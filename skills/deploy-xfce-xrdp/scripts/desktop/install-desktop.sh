#!/usr/bin/env bash
# First-install workflow. Run only on the reviewed target after SSH preflight.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
umask 077
rd_user=''
rd_label='Remote Desktop'
rd_rdp_port=13389
rd_ssh_port=22
rd_bind=127.0.0.1
usage() {
    cat <<'USAGE'
Usage: sudo bash install-desktop.sh --user USER [options]
  --label LABEL         Desktop label (default: Remote Desktop)
  --rdp-port PORT       RDP TCP port (default: 13389)
  --ssh-port PORT       Actual SSH port for Fail2ban (default: 22)
  --bind-address IPV4   RDP bind address (default: 127.0.0.1)
                       Pass 0.0.0.0 explicitly for direct public RDP.
  -h, --help            Show help

Supported first-install targets: Debian 12 / Ubuntu 26.04, amd64.
An existing Linux account and Firefox ESR APT candidate are required.
The workflow does not create credentials or change SSH authentication.
Audio is disabled. Existing graphical configuration/session is refused.
Failure after masking leaves xrdp masked; inspect the printed backup directory
and fix the failed phase before deliberately completing or rolling back.
USAGE
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --user|--label|--rdp-port|--ssh-port|--bind-address)
            (($# >= 2)) || die "Missing value for $1"
            case "$1" in
                --user) rd_user=$2 ;;
                --label) rd_label=$2 ;;
                --rdp-port) rd_rdp_port=$2 ;;
                --ssh-port) rd_ssh_port=$2 ;;
                --bind-address) rd_bind=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[[ -n $rd_user ]] || die '--user is required.'
for rd_port in "$rd_rdp_port" "$rd_ssh_port"; do
    [[ $rd_port =~ ^[0-9]{1,5}$ ]] || die "Invalid port: $rd_port"
    ((10#$rd_port >= 1 && 10#$rd_port <= 65535)) || die "Invalid port: $rd_port"
done
rd_rdp_port=$((10#$rd_rdp_port))
rd_ssh_port=$((10#$rd_ssh_port))
[[ $rd_rdp_port != "$rd_ssh_port" ]] || die 'RDP and SSH ports must differ.'
[[ $EUID -eq 0 ]] || die 'Run with sudo.'
rd_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f $rd_dir/configure-desktop.py && -f $rd_dir/../install-fail2ban.sh ]] || die 'Keep the complete scripts directory together.'
[[ -d /run/systemd/system ]] || die 'A running systemd system is required.'
command -v python3 >/dev/null || die 'Install python3 first.'
rd_config_args=(--user "$rd_user" --label "$rd_label" --rdp-port "$rd_rdp_port" --bind-address "$rd_bind")
python3 "$rd_dir/configure-desktop.py" "${rd_config_args[@]}" --preflight
. /etc/os-release
for rd_unit in xrdp.service xrdp-sesman.service; do
    rd_state=$(systemctl is-enabled "$rd_unit" 2>/dev/null || true)
    case "$rd_state" in
        masked*) die "$rd_unit already masked; review the previous deployment before continuing." ;;
    esac
done
if ss -lntH "( sport = :$rd_rdp_port )" | awk 'END {exit NR == 0 ? 1 : 0}'; then
    die "TCP port $rd_rdp_port is already in use."
fi
rd_backup=$(mktemp -d /var/backups/xfce-xrdp-desktop.XXXXXXXX)
printf 'Backup directory: %s\n' "$rd_backup"
dpkg-query -W > "$rd_backup/packages-before.txt"
systemctl list-unit-files > "$rd_backup/services-before.txt"
printf 'user=%s\nlabel=%s\nrdp_port=%s\nssh_port=%s\nbind_address=%s\n' \
    "$rd_user" "$rd_label" "$rd_rdp_port" "$rd_ssh_port" "$rd_bind" > "$rd_backup/deployment-settings.txt"
# Resolve all packages before changing service masks. Ubuntu needs Mozilla's
# separately verified APT repository; Debian 12 supplies firefox-esr itself.
apt-get update
rd_firefox_candidate=$(apt-cache policy firefox-esr | awk '/Candidate:/ { candidate=$2 } END { print candidate }')
[[ -n $rd_firefox_candidate && $rd_firefox_candidate != '(none)' ]] || die 'No firefox-esr candidate; configure its trusted APT source first.'
rd_packages=(
    xfce4 xfce4-terminal xfce4-whiskermenu-plugin xfce4-power-manager xfce4-notifyd
    mousepad thunar-archive-plugin file-roller
    arc-theme papirus-icon-theme fonts-noto-cjk fonts-noto-color-emoji fonts-jetbrains-mono
    fcitx5 fcitx5-chinese-addons fcitx5-config-qt fcitx5-frontend-gtk3
    fcitx5-frontend-gtk2 fcitx5-frontend-qt5 im-config
    xrdp xorgxrdp dbus-x11 librsvg2-bin librsvg2-common firefox-esr
    x11-xserver-utils xauth locales ssl-cert fail2ban python3-systemd nftables
)
if [[ $ID == ubuntu ]]; then rd_packages+=(fcitx5-frontend-qt6); fi
apt-get --simulate install --no-install-recommends "${rd_packages[@]}" > "$rd_backup/apt-simulation.txt"
rd_masks_started=0
rd_finished=0
on_exit() {
    rd_rc=$?
    if ((rd_rc != 0 && rd_masks_started && ! rd_finished)); then
        # Never expose a partial setup. Best effort stop+mask also covers errors
        # during the final enable --now after some units have started.
        systemctl stop xrdp.service xrdp-sesman.service || true
        if systemctl mask xrdp.service xrdp-sesman.service; then
            printf 'Installation incomplete. xrdp is masked; backup: %s\n' "$rd_backup" >&2
        else
            printf 'Installation incomplete; could not mask xrdp. Inspect service state now. Backup: %s\n' "$rd_backup" >&2
        fi
        printf 'Review references/desktop-compatibility.md before resuming or restoring.\n' >&2
    fi
}
trap on_exit EXIT
rd_masks_started=1
systemctl mask xrdp.service xrdp-sesman.service
# Fail2ban is deliberately not masked: existing login protection stays active.
apt-get install -y --no-install-recommends "${rd_packages[@]}"
python3 "$rd_dir/configure-desktop.py" "${rd_config_args[@]}" --backup-dir "$rd_backup"
if [[ ! -f /var/log/xrdp-sesman.log ]]; then
    install -o root -g adm -m 0640 /dev/null /var/log/xrdp-sesman.log
fi
bash "$rd_dir/../install-fail2ban.sh" --ssh-port "$rd_ssh_port" --xrdp-port "$rd_rdp_port"
systemctl is-active --quiet fail2ban.service
fail2ban-client status sshd
fail2ban-client status remote-xrdp
systemctl unmask xrdp.service xrdp-sesman.service
systemctl enable --now xrdp.service
systemctl is-active --quiet xrdp.service
systemctl is-active --quiet xrdp-sesman.service
ss -lnt "( sport = :$rd_rdp_port or sport = :3389 )"
openssl x509 -in /etc/xrdp/cert.pem -noout -fingerprint -sha256
rd_finished=1
printf 'Installation complete. Backups: %s\n' "$rd_backup"
printf 'Continue with a real RDP login, dynamic resolution and bidirectional clipboard checks.\n'
