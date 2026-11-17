#!/usr/bin/env python3
"""Initialize one Xfce account and xrdp configuration on supported fresh hosts.

This does not start services. The companion installer keeps xrdp masked until
configuration and Fail2ban are ready. Existing graphical setups are refused.
"""
from pathlib import Path
import argparse
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def validate_options(args):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", args.user):
        raise ValueError("Use an existing POSIX user name: lowercase letters, digits, _ or -.")
    if (not 1 <= len(args.label) <= 48 or args.label != args.label.strip()
            or not all(c.isalnum() or c in " ._-&()" for c in args.label)):
        raise ValueError("Label must be 1-48 letters, digits, spaces, or ._-&().")
    if not 1 <= args.rdp_port <= 65535:
        raise ValueError("RDP port must be between 1 and 65535.")
    address = ipaddress.ip_address(args.bind_address)
    if address.version != 4 or address.is_multicast or str(address) == "255.255.255.255":
        raise ValueError("Bind address must be a unicast IPv4 address or 0.0.0.0.")
    args.bind_address = str(address)
    if args.backup_dir is not None:
        path = Path(args.backup_dir)
        if (not path.is_absolute() or path.parent != Path("/var/backups")
                or not re.fullmatch(r"xfce-xrdp-desktop\.[A-Za-z0-9_-]+", path.name)):
            raise ValueError("Backup directory must be /var/backups/xfce-xrdp-desktop.<id>.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="Existing desktop account; never root")
    parser.add_argument("--label", default="Remote Desktop")
    parser.add_argument("--rdp-port", type=int, default=13389)
    parser.add_argument("--bind-address", default="127.0.0.1",
                        help="IPv4; pass 0.0.0.0 explicitly for direct public RDP")
    parser.add_argument("--backup-dir")
    parser.add_argument("--preflight", action="store_true", help="Read-only OS, account and existing-desktop checks")
    args = parser.parse_args(argv)
    try:
        validate_options(args)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def checked_result(argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True).stdout.strip()


def preflight(args):
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo; --help and pure rendering functions need no privilege.")
    if not Path("/run/systemd/system").is_dir():
        raise RuntimeError("A running systemd system is required.")
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            release[key] = shlex.split(value)[0] if value else ""
    platform = (release.get("ID"), release.get("VERSION_ID"))
    if platform not in (("debian", "12"), ("ubuntu", "26.04")):
        raise RuntimeError("Supported first-install targets: Debian 12 and Ubuntu 26.04.")
    if checked_result(["dpkg", "--print-architecture"]) != "amd64":
        raise RuntimeError("This workflow was verified for amd64; review other architectures separately.")
    account = pwd.getpwnam(args.user)
    user_home = Path(account.pw_dir)
    if (account.pw_uid == 0 or not user_home.is_absolute() or user_home == Path("/")
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(user_home))
            or user_home.resolve() != user_home or not user_home.is_dir()
            or user_home.stat().st_uid != account.pw_uid):
        raise RuntimeError("Account must have a normal, owned, absolute home with no symlink or shell metacharacters.")
    marker = Path("/var/lib/xfce-xrdp-desktop") / (args.user + ".json")
    if marker.exists() or marker.is_symlink():
        raise RuntimeError("This account has already been initialized; review existing configuration instead of rerunning.")
    for relative in (".xsession", ".xinputrc", ".config/xfce4", ".config/fcitx5/profile",
                     ".local/bin/remote-desktop-session", ".config/autostart/remote-desktop-session.desktop"):
        path = user_home / relative
        if path.exists() or path.is_symlink():
            raise RuntimeError(f"Existing desktop configuration requires review: {path}")
    for unit in ("xrdp.service", "xrdp-sesman.service"):
        state = subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode
        if state == 0:
            raise RuntimeError(f"{unit} is active; this initializer only handles a fresh inactive desktop.")
    result = subprocess.run(["pgrep", "-x", "xfce4-session"], stdout=subprocess.DEVNULL)
    if result.returncode == 0:
        raise RuntimeError("An Xfce session is active; do not initialize over a running desktop.")
    if result.returncode != 1:
        raise RuntimeError("Could not check for existing Xfce sessions.")
    return platform[0], account, marker


def render_ini(text, changes):
    """Edit selected keys while preserving repeated xrdp param= lines.

    For keys we own, discard duplicate active definitions so an older later
    value cannot override the requested value. Unmanaged keys stay untouched.
    """
    remaining = {section: dict(values) for section, values in changes.items()}
    owned = {section: set(values) for section, values in changes.items()}
    output = []
    section = None
    seen_sections = set()
    def append_missing():
        if section in remaining:
            output.extend(f"{key}={value}\n" for key, value in remaining[section].items())
            remaining[section].clear()
    for line in text.splitlines(keepends=True):
        header = re.match(r"\s*\[([^]]+)\]", line)
        if header:
            append_missing()
            section = header.group(1)
            if section in owned and section in seen_sections:
                raise ValueError(f"Duplicate managed INI section: {section}")
            seen_sections.add(section)
        setting = re.match(r"\s*([^;#=\s]+)\s*=", line)
        if setting and setting.group(1) in owned.get(section, set()):
            key = setting.group(1)
            if key not in remaining.get(section, {}):
                continue
            line = f"{key}={remaining[section].pop(key)}\n"
        output.append(line if line.endswith("\n") else line + "\n")
    append_missing()
    for missing_section, settings in remaining.items():
        if settings:
            output.append(f"\n[{missing_section}]\n")
            output.extend(f"{key}={value}\n" for key, value in settings.items())
    return "".join(output)


def configure(args, distro, account, marker):
    user_home = Path(account.pw_dir)
    for executable in ("rsvg-convert", "startxfce4", "xfce4-terminal", "thunar",
                       "firefox-esr", "fcitx5", "usermod"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"Missing required program: {executable}")
    for ini_path in ("/etc/xrdp/xrdp.ini", "/etc/xrdp/sesman.ini"):
        if not Path(ini_path).is_file():
            raise RuntimeError(f"Missing xrdp configuration: {ini_path}")
    version = checked_result(["dpkg-query", "-W", "-f=${Version}", "xrdp"])
    expected = r"(?:\d+:)?0\.9\.21\.1-" if distro == "debian" else r"(?:\d+:)?0\.10\.1-"
    if not re.match(expected, version):
        raise RuntimeError(f"Unreviewed xrdp version {version}; inspect its schemas before deployment.")
    backup = Path(args.backup_dir) if args.backup_dir else Path(tempfile.mkdtemp(
        prefix="xfce-xrdp-desktop.", dir="/var/backups"))
    if backup.exists() and (backup.stat().st_uid != 0 or backup.stat().st_mode & 0o077):
        raise RuntimeError("Backup directory must be root-owned and mode 0700.")

    def check_regular_destination(path):
        """Refuse symlinks instead of writing outside the managed configuration."""
        path = Path(path)
        for candidate in (path, *path.parents):
            if candidate.is_symlink():
                raise RuntimeError(f"Refusing symlink in managed path: {candidate}")
        if path.exists() and not path.is_file():
            raise RuntimeError(f"Expected a regular file: {path}")


    def make_parents(path, user=False):
        """Set ownership only on directories created by this script."""
        missing = []
        parent = Path(path).parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            if user:
                os.chown(directory, account.pw_uid, account.pw_gid)


    check_regular_destination(backup / "manifest.txt")
    backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup.chmod(0o700)


    def save_original(path):
        path = Path(path)
        check_regular_destination(path)
        dest = backup / path.relative_to("/")
        if path.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        elif not path.exists():
            # New managed paths can be removed explicitly when rolling back.
            manifest = backup / "created-files.txt"
            recorded = manifest.read_text().splitlines() if manifest.exists() else []
            if str(path) not in recorded:
                with manifest.open("a", encoding="utf-8") as f:
                    f.write(str(path) + "\n")


    def write(path, content, mode=0o644, user=False):
        path = Path(path)
        save_original(path)
        make_parents(path, user=user)
        fd, tmp_name = tempfile.mkstemp(prefix=".remote-desktop-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(content)
            os.chmod(tmp_name, mode)
            if user:
                os.chown(tmp_name, account.pw_uid, account.pw_gid)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


    def edit_ini(path, changes):
        write(path, render_ini(Path(path).read_text(), changes),
              mode=Path(path).stat().st_mode & 0o777)

    edit_ini("/etc/xrdp/xrdp.ini", {
        "Globals": {
            "port": f"tcp://{args.bind_address}:{args.rdp_port}", "security_layer": "tls",
            "crypt_level": "high", "tcp_nodelay": "true", "tcp_keepalive": "true",
            "max_bpp": "32", "allow_channels": "true", "ls_title": args.label,
            "ssl_protocols": "TLSv1.2, TLSv1.3",
        },
        "Channels": {"cliprdr": "true", "drdynvc": "true", "rdpdr": "true", "rdpsnd": "false"},
        "Logging": {"LogLevel": "INFO"},
    })
    sesman_changes = {
        "Sessions": {
            "MaxSessions": "2", "KillDisconnected": "false",
            "DisconnectedTimeLimit": "0", "IdleTimeLimit": "0", "Policy": "Default",
        },
        "Security": {"AllowRootLogin": "false", "MaxLoginRetry": "4"},
        "Chansrv": {"EnableFuseMount": "false"},
        "Logging": {"LogLevel": "INFO"},
    }
    if distro == "debian":
        sesman_changes["Globals"] = {"ListenAddress": "127.0.0.1", "ListenPort": "3350"}
    else:
        sesman_changes["ChansrvLogging"] = {"LogLevel": "INFO"}
    edit_ini("/etc/xrdp/sesman.ini", sesman_changes)
    subprocess.run(["usermod", "-a", "-G", "ssl-cert", "xrdp"], check=True)

    write(user_home / ".xsession", """#!/bin/sh
unset SESSION_MANAGER
# Follow the machine timezone even when a shell profile exports another TZ.
unset TZ
exec startxfce4
""", 0o755, user=True)
    write(user_home / ".xinputrc", "# Managed for the remote desktop.\nrun_im fcitx5\n", user=True)
    write(user_home / ".config/fcitx5/profile", """[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[GroupOrder]
0=Default
""", user=True)

    def property_node(parent, name, kind, value=None):
        node = ET.SubElement(parent, "property", name=name, type=kind)
        if value is not None:
            node.set("value", str(value).lower() if isinstance(value, bool) else str(value))
        return node

    def channel(name, settings):
        root = ET.Element("channel", name=name, version="1.0")
        for key, kind, value in settings:
            parts = key.strip("/").split("/")
            parent = root
            for part in parts[:-1]:
                found = next((p for p in parent if p.get("name") == part), None)
                parent = found if found is not None else property_node(parent, part, "empty")
            property_node(parent, parts[-1], kind, value)
        return root

    def save_channel(root):
        ET.indent(root)
        write(user_home / ".config/xfce4/xfconf/xfce-perchannel-xml" / (root.get("name") + ".xml"),
              '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n", user=True)

    save_channel(channel("xsettings", [
        ("Net/ThemeName", "string", "Arc-Dark"),
        ("Net/IconThemeName", "string", "Papirus-Dark"),
        ("Gtk/FontName", "string", "Noto Sans CJK SC 10"),
        ("Gtk/MonospaceFontName", "string", "JetBrains Mono 10"),
        ("Gtk/CursorThemeName", "string", "Adwaita"),
        ("Gtk/CursorThemeSize", "int", 24),
        ("Gtk/EnableAnimations", "bool", False),
        ("Gtk/DialogsUseHeader", "bool", False),
        ("Xft/Antialias", "int", 1),
        ("Xft/Hinting", "int", 1),
        ("Xft/HintStyle", "string", "hintslight"),
        ("Xft/RGBA", "string", "none"),
        ("Xft/DPI", "int", 96),
    ]))
    save_channel(channel("xfwm4", [
        ("general/theme", "string", "Arc-Dark"),
        ("general/title_font", "string", "Noto Sans CJK SC Bold 10"),
        ("general/use_compositing", "bool", False),
        ("general/box_move", "bool", False),
        ("general/box_resize", "bool", False),
        ("general/workspace_count", "int", 2),
        ("general/button_layout", "string", "O|HMC"),
    ]))
    save_channel(channel("xfce4-session", [
        ("general/SaveOnExit", "bool", False),
        ("general/PromptOnLogout", "bool", True),
    ]))
    save_channel(channel("xfce4-power-manager", [
        ("xfce4-power-manager/dpms-enabled", "bool", False),
        ("xfce4-power-manager/blank-on-ac", "int", 0),
        ("xfce4-power-manager/inactivity-on-ac", "uint", 0),
        ("xfce4-power-manager/presentation-mode", "bool", True),
    ]))
    save_channel(channel("thunar", [
        ("last-view", "string", "ThunarIconView"),
        ("last-icon-view-zoom-level", "string", "THUNAR_ZOOM_LEVEL_100_PERCENT"),
        ("misc-single-click", "bool", False),
    ]))

    panel = ET.Element("channel", name="xfce4-panel", version="1.0")
    property_node(panel, "configver", "int", 2)
    panels = property_node(panel, "panels", "array")
    ET.SubElement(panels, "value", type="int", value="1")
    p = property_node(panels, "panel-1", "empty")
    for key, kind, value in [
        ("position", "string", "p=10;x=0;y=0"),
        ("position-locked", "bool", True), ("size", "uint", 44),
        ("length", "uint", 100), ("length-adjust", "bool", False),
        ("autohide-behavior", "uint", 0), ("span-monitors", "bool", False),
        ("background-style", "uint", 0),
    ]:
        property_node(p, key, kind, value)
    ids = property_node(p, "plugin-ids", "array")
    for number in range(1, 10):
        ET.SubElement(ids, "value", type="int", value=str(number))
    plugins = property_node(panel, "plugins", "empty")
    definitions = [
        (1, "whiskermenu"), (2, "launcher"), (3, "launcher"),
        (4, "launcher"), (5, "separator"), (6, "tasklist"),
        (7, "separator"), (8, "systray"), (9, "clock"),
    ]
    for number, name in definitions:
        plugin = property_node(plugins, f"plugin-{number}", "string", name)
        if name == "launcher":
            items = property_node(plugin, "items", "array")
            ET.SubElement(items, "value", type="string", value=f"remote-desktop-{number}.desktop")
        elif name == "separator":
            property_node(plugin, "style", "uint", 0)
            property_node(plugin, "expand", "bool", number == 7)
        elif name == "tasklist":
            property_node(plugin, "show-handle", "bool", False)
            property_node(plugin, "show-labels", "bool", True)
            property_node(plugin, "grouping", "uint", 1)
        elif name == "clock":
            property_node(plugin, "digital-time-format", "string", "%a  %d %b   %H:%M %Z")
            property_node(plugin, "digital-layout", "uint", 3)
            property_node(plugin, "tooltip-format", "string", "%Y-%m-%d %A (%Z)")
            property_node(plugin, "timezone", "string", "")  # Follow the server timezone.
    save_channel(panel)

    write(user_home / ".config/xfce4/panel/whiskermenu-1.rc", f"""button-title={args.label}
button-icon=org.xfce.panel.whiskermenu
show-button-title=true
show-button-icon=true
show-button-arrow=false
view-mode=0
launcher-show-name=true
launcher-show-description=false
favorites=exo-terminal-emulator.desktop,exo-file-manager.desktop,firefox-esr.desktop,org.xfce.mousepad.desktop
""", user=True)
    for number, name, icon, command in [
        (2, "Terminal", "utilities-terminal", "xfce4-terminal"),
        (3, "Files", "system-file-manager", "thunar"),
        (4, "Firefox ESR", "firefox-esr", "firefox-esr"),
    ]:
        write(user_home / f".config/xfce4/panel/launcher-{number}/remote-desktop-{number}.desktop",
              f"[Desktop Entry]\nType=Application\nName={name}\nIcon={icon}\nExec={command}\nTerminal=false\n", user=True)

    write(user_home / ".config/xfce4/terminal/terminalrc", """[Configuration]
FontName=JetBrains Mono 11
ColorForeground=#d8dee9
ColorBackground=#111827
ColorCursor=#7dd3fc
ColorPalette=#1e293b;#fb7185;#86efac;#fde68a;#93c5fd;#c4b5fd;#67e8f9;#e2e8f0;#64748b;#fda4af;#bbf7d0;#fef3c7;#bfdbfe;#ddd6fe;#a5f3fc;#f8fafc
BackgroundMode=TERMINAL_BACKGROUND_SOLID
MiscMenubarDefault=TRUE
MiscBordersDefault=TRUE
MiscToolbarDefault=FALSE
ScrollingLines=10000
""", user=True)

    save_channel(channel("xfce4-terminal", [
        ("font-name", "string", "JetBrains Mono 11"),
        ("color-foreground", "string", "#d8dee9"),
        ("color-background", "string", "#111827"),
        ("color-cursor", "string", "#7dd3fc"),
        ("misc-borders-default", "bool", True),
        ("misc-menubar-default", "bool", True),
        ("misc-toolbar-default", "bool", False),
        ("scrolling-lines", "uint", 10000),
    ]))

    wallpaper = """<svg xmlns="http://www.w3.org/2000/svg" width="2560" height="1440" viewBox="0 0 2560 1440">
<defs>
 <linearGradient id="bg" x2="1" y2="1"><stop stop-color="#0a1220"/><stop offset="1" stop-color="#182b42"/></linearGradient>
 <linearGradient id="ridge" x2="1" y2="0"><stop stop-color="#1d4960" stop-opacity=".2"/><stop offset=".65" stop-color="#2a6477" stop-opacity=".55"/><stop offset="1" stop-color="#57a5ac" stop-opacity=".12"/></linearGradient>
 <radialGradient id="glow"><stop stop-color="#276c86" stop-opacity=".28"/><stop offset="1" stop-color="#276c86" stop-opacity="0"/></radialGradient>
</defs>
<rect width="2560" height="1440" fill="url(#bg)"/>
<ellipse cx="1960" cy="370" rx="950" ry="800" fill="url(#glow)"/>
<path d="M-200 1300 Q650 1140 1280 640 T2750 430 L2750 1600 H-200Z" fill="url(#ridge)"/>
<path d="M-100 1400 Q750 1190 1420 790 T2700 610" fill="none" stroke="#65bac3" stroke-opacity=".13" stroke-width="2"/>
<path d="M-100 1450 Q900 1260 1560 940 T2750 800" fill="none" stroke="#78a9cb" stroke-opacity=".1" stroke-width="2"/>
<circle cx="2110" cy="270" r="128" fill="none" stroke="#7cbbcf" stroke-opacity=".12"/>
<circle cx="2110" cy="270" r="134" fill="none" stroke="#7cbbcf" stroke-opacity=".04"/>
</svg>"""
    write("/usr/local/share/backgrounds/xfce-rdp-workspace.svg", wallpaper)
    wallpaper_png = Path("/usr/local/share/backgrounds/xfce-rdp-workspace.png")
    save_original(wallpaper_png)
    subprocess.run(["rsvg-convert", "/usr/local/share/backgrounds/xfce-rdp-workspace.svg", "-o", str(wallpaper_png)], check=True)
    wallpaper_png.chmod(0o644)
    write(user_home / ".local/bin/remote-desktop-session", """#!/bin/sh
# Apply the background to the monitor names created by xorgxrdp.
sleep 2
xfconf-query -c xfce4-desktop -p /backdrop/single-workspace-mode -n -t bool -s true 2>/dev/null || true
xfconf-query -c xfce4-desktop -p /desktop-icons/style -n -t int -s 0 2>/dev/null || true
for monitor in $(xrandr --query | awk '$2 == "connected" {print $1}'); do
    xfconf-query -c xfce4-desktop -p "/backdrop/screen0/monitor$monitor/workspace0/last-image" -n -t string -s /usr/local/share/backgrounds/xfce-rdp-workspace.png
    xfconf-query -c xfce4-desktop -p "/backdrop/screen0/monitor$monitor/workspace0/image-style" -n -t int -s 5
done
xfconf-query -c xfce4-desktop -l | while IFS= read -r prop; do
    case "$prop" in
        */last-image) xfconf-query -c xfce4-desktop -p "$prop" -s /usr/local/share/backgrounds/xfce-rdp-workspace.png ;;
        */image-style) xfconf-query -c xfce4-desktop -p "$prop" -s 5 ;;
    esac
done
xset s off
xset -dpms 2>/dev/null || true
""", 0o755, user=True)
    write(user_home / ".config/autostart/remote-desktop-session.desktop", f"""[Desktop Entry]
Type=Application
Name=Remote desktop preferences
Exec={user_home}/.local/bin/remote-desktop-session
OnlyShowIn=XFCE;
Terminal=false
""", user=True)

    write(marker, json.dumps({"user": args.user, "label": args.label,
          "bind_address": args.bind_address, "rdp_port": args.rdp_port,
          "backup_directory": str(backup)}, indent=2) + "\n", mode=0o600)
    print(f"Configured {args.label} for TLS at {args.bind_address}:{args.rdp_port}.")
    print(f"Existing files backed up under {backup}.")
    print("Services were not started; enable Fail2ban before starting xrdp.")



def main():
    args = parse_args()
    try:
        distro, account, marker = preflight(args)
        if args.preflight:
            print(f"Preflight passed: {distro}, user={args.user}, bind={args.bind_address}:{args.rdp_port}")
            return
        configure(args, distro, account, marker)
    except (RuntimeError, KeyError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
