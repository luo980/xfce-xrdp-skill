#!/usr/bin/env python3
"""Create a password-free RDP profile and a macOS Windows App launcher."""
import argparse
import ipaddress
from pathlib import Path
import re


def endpoint(value):
    try:
        address = ipaddress.ip_address(value)
        return f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        if len(value) > 253 or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value
        ):
            raise argparse.ArgumentTypeError("Use an IP address or DNS hostname")
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=endpoint)
    parser.add_argument("--port", type=int, default=13389)
    parser.add_argument("--name", required=True, help="Filename slug: letters, digits, dot, dash, underscore")
    parser.add_argument("--user", help="Optional Linux username; no password is stored")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--audio", action="store_true", help="Play server audio locally; requires server audio support")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.name):
        parser.error("Invalid filename slug")
    if args.user and not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", args.user):
        parser.error("Invalid Linux username")
    rdp = args.output_dir / f"{args.name}.rdp"
    launcher = args.output_dir / f"Connect-{args.name}.command"
    for path in (rdp, launcher):
        if path.exists() or path.is_symlink():
            parser.error(f"Destination already exists: {path}")
    address = f"{args.host}:{args.port}"
    lines = [f"full address:s:{address}", "domain:s:"]
    if args.user:
        lines.append(f"username:s:{args.user}")
    lines.extend([
        "screen mode id:i:1", "desktopwidth:i:1920", "desktopheight:i:1080",
        "session bpp:i:32", "use multimon:i:0", "dynamic resolution:i:1",
        "smart sizing:i:0", "redirectclipboard:i:1", "drivestoredirect:s:",
        "redirectdrives:i:0", "redirectprinters:i:0", "camerastoredirect:s:",
        "devicestoredirect:s:", "usbdevicestoredirect:s:", "redirectcomports:i:0",
        "redirectsmartcards:i:0", "redirectwebauthn:i:0",
        f"audiomode:i:{0 if args.audio else 2}", "audiocapturemode:i:0",
        "authentication level:i:2", "prompt for credentials:i:1",
        "autoreconnection enabled:i:1", "networkautodetect:i:1",
        "bandwidthautodetect:i:1", "compression:i:1",
    ])
    script = f'''#!/bin/bash
set -euo pipefail
readonly rd_script_dir="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd -P)"
readonly rd_rdp_file="$rd_script_dir/{args.name}.rdp"
[[ -f "$rd_rdp_file" ]] || {{ printf 'Missing RDP profile: %s\\n' "$rd_rdp_file" >&2; exit 1; }}
exec /usr/bin/open -a 'Windows App' "$rd_rdp_file"
'''
    args.output_dir.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, content, mode in ((rdp, "\n".join(lines) + "\n", 0o600), (launcher, script, 0o700)):
            with path.open("x", encoding="utf-8") as output:
                created.append(path)
                path.chmod(mode)
                output.write(content)
    except Exception:
        for path in created:
            path.unlink()
        raise
    print(rdp)
    print(launcher)


if __name__ == "__main__":
    main()
