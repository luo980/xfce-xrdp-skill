#!/usr/bin/env bash
# Debian/Ubuntu with Fail2ban >= 1.0.2; xrdp 0.9/0.10 AUTHFAIL logs.
# Default: TCP 22 / 13389, 5 failures in 10 minutes => a 1-hour ban.
# This script does not restart SSH/xrdp or flush existing firewall rules.
# References:
# https://github.com/neutrinolabs/xrdp/blob/v0.10.1/sesman/scp_process.c
# https://github.com/neutrinolabs/xrdp/blob/v0.10.1/common/os_calls.c
# https://github.com/fail2ban/fail2ban/blob/1.1.0/config/action.d/nftables.conf
set -Eeuo pipefail
export LC_ALL=C
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
umask 077

ssh_port=22
xrdp_port=13389
allow_ips=()
allow_current_ssh=0
usage() {
    cat <<'EOF'
Usage: sudo bash install-fail2ban.sh [options]
  --ssh-port PORT       SSH port (default: 22)
  --xrdp-port PORT      xrdp port (default: 13389)
  --allow-ip IP[/CIDR]  Add an administrator address to BOTH jails' allowlist;
                       repeat for more addresses. No hostnames accepted.
  --allow-current-ssh   Explicitly allowlist the source in SSH_CONNECTION.
                       Use: sudo SSH_CONNECTION="$SSH_CONNECTION" bash SCRIPT ...
  -h, --help           Show this help.

No administrator address is allowlisted automatically. Loopback is excluded.
Run from an existing SSH session and keep it open while verifying deployment.
EOF
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --ssh-port|--xrdp-port|--allow-ip)
            (($# >= 2)) || die "Missing value for $1"
            case "$1" in
                --ssh-port) ssh_port=$2 ;;
                --xrdp-port) xrdp_port=$2 ;;
                --allow-ip) allow_ips+=("$2") ;;
            esac
            shift 2 ;;
        --allow-current-ssh) allow_current_ssh=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1 (see --help)" ;;
    esac
done
[[ $EUID -eq 0 ]] || die 'Run this script with sudo.'
[[ -f /etc/os-release ]] || die '/etc/os-release is missing.'
. /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) die 'Supported systems: Ubuntu and Debian.' ;; esac
[[ -d /run/systemd/system ]] || die 'A running systemd system is required.'
command -v python3 >/dev/null || die 'Install python3 first.'
[[ -f /var/log/xrdp-sesman.log ]] || die 'Missing /var/log/xrdp-sesman.log; create the root-owned sesman log before activating the jail.'
python3 - <<'PY'
import configparser, sys
c = configparser.ConfigParser(interpolation=None, strict=False)
if not c.read('/etc/xrdp/sesman.ini'):
    sys.exit('Cannot read /etc/xrdp/sesman.ini')
if c.get('Logging', 'LogLevel', fallback='INFO').upper() not in ('INFO', 'DEBUG', 'TRACE'):
    sys.exit('xrdp-sesman must log at INFO or DEBUG level to emit AUTHFAIL records.')
PY
for port in "$ssh_port" "$xrdp_port"; do
    [[ $port =~ ^[0-9]{1,5}$ ]] || die "Invalid port: $port"
    ((10#$port >= 1 && 10#$port <= 65535)) || die "Invalid port: $port"
done
ssh_port=$((10#$ssh_port))
xrdp_port=$((10#$xrdp_port))
[[ $ssh_port != "$xrdp_port" ]] || die "SSH and RDP must use different ports."
if ((allow_current_ssh)); then
    [[ -n ${SSH_CONNECTION:-} ]] || die 'SSH_CONNECTION is absent; use --allow-ip or pass it through sudo.'
    allow_ips+=("${SSH_CONNECTION%% *}")
fi
normalized_ips=$(python3 - "${allow_ips[@]}" <<'PY'
import ipaddress, sys
try:
    print(' '.join(dict.fromkeys(str(ipaddress.ip_network(s, strict=False)) for s in sys.argv[1:])))
except ValueError as exc:
    sys.exit('Invalid allowlist IP/CIDR: ' + str(exc))
PY
)
ignore_ips="127.0.0.0/8 ::1${normalized_ips:+ $normalized_ips}"
printf 'Ports: SSH=%s, xrdp=%s; allowlist: %s\n' "$ssh_port" "$xrdp_port" "$ignore_ips"

apt-get update
NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban python3-systemd nftables
command -v fail2ban-client >/dev/null || die 'Fail2ban installation failed.'
f2b_version=$(dpkg-query -W -f='${Version}' fail2ban)
dpkg --compare-versions "$f2b_version" ge 1.0.2 || die 'This script requires Fail2ban >= 1.0.2.'
nft list tables >/dev/null || die 'The kernel/container does not permit nftables.'

backup=$(mktemp -d /var/backups/remote-desktop-fail2ban.XXXXXXXX)
work=$(mktemp -d /tmp/remote-desktop-fail2ban.XXXXXXXX)
files=(/etc/fail2ban/filter.d/remote-xrdp.conf /etc/fail2ban/jail.d/99-remote-desktop.local)
for path in "${files[@]}"; do
    if [[ -e $path || -L $path ]]; then cp -a -- "$path" "$backup/$(basename "$path")"; fi
done
was_active=0
systemctl is-active --quiet fail2ban && was_active=1
runtime_changed=0
committed=0
was_enabled=$(systemctl is-enabled fail2ban 2>/dev/null || true)
cleanup() {
    rc=$?
    trap - EXIT
    if ((rc != 0 && committed == 0)); then
        printf 'Deployment failed; restoring the two managed configuration files. Backup: %s\n' "$backup" >&2
        for path in "${files[@]}"; do
            saved="$backup/$(basename "$path")"
            rm -f -- "$path"
            if [[ -e $saved || -L $saved ]]; then cp -a -- "$saved" "$path"; fi
        done
        if ((runtime_changed)); then
            if ((was_active)); then systemctl restart fail2ban || true
            else systemctl stop fail2ban || true
            fi
            if [[ $was_enabled != enabled ]]; then systemctl disable fail2ban || true; fi
        fi
    fi
    rm -rf -- "$work"
    exit "$rc"
}
trap cleanup EXIT

# The authoritative timestamp is the final Unix epoch supplied by xrdp.
# Fail2ban removes the matched date before applying failregex.
# Greedily consume the username, and anchor the real trailing IP field.
# xrdp 0.9 can emit ip=<unknown> when peer parsing fails. Missing/invalid
# addresses are intentionally ignored: they cannot identify a ban target.
cat > "$work/remote-xrdp.conf" <<'EOF'
[Definition]
failregex = ^\[[0-9T:.,+Z-]+\]\s+\[INFO \]\s+AUTHFAIL: user=.* ip=<ADDR>\s*$
ignoreregex =
datepattern = time={EPOCH}$
maxlines = 1
EOF

cat > "$work/99-remote-desktop.local" <<EOF
# Managed by install-fail2ban.sh. Settings apply only to these two jails.
[sshd]
enabled = true
backend = systemd
filter = sshd[mode=normal, _daemon=sshd(?:-session|-auth)?]
journalmatch = _SYSTEMD_UNIT=ssh.service + _SYSTEMD_UNIT=sshd.service + _COMM=sshd + _COMM=sshd-session + _COMM=sshd-auth
port = $ssh_port
usedns = no
ignoreip = $ignore_ips
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = false
action = nftables[type=multiport, name=remote-sshd, port=$ssh_port, protocol=tcp]

[remote-xrdp]
enabled = true
backend = polling
filter = remote-xrdp
logpath = /var/log/xrdp-sesman.log
port = $xrdp_port
usedns = no
ignoreip = $ignore_ips
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = false
action = nftables[type=multiport, name=remote-xrdp, port=$xrdp_port, protocol=tcp]
EOF

# Test the xrdp 0.9 timestamp plus ISO timestamps, valid address families,
# usernames containing misleading IP fields, and missing/unknown peer addresses.
python3 - "$work/xrdp-test.log" <<'PYTEST'
import datetime, pathlib, sys, time
now = datetime.datetime.now(datetime.timezone.utc)
legacy = now.strftime('%Y%m%d-%H:%M:%S')
iso = now.strftime('%Y-%m-%dT%H:%M:%S.000+0000')
epoch = int(time.time())
prefix = f'[{legacy}] [INFO ] '
records = [
    prefix + f'AUTHFAIL: user=desktopuser ip=192.0.2.10 time={epoch}',
    prefix + f'AUTHFAIL: user=test ip=2001:db8::10 time={epoch}',
    prefix + f'AUTHFAIL: user=test ip=::ffff:192.0.2.11 time={epoch}',
    prefix + f'AUTHFAIL: user= ip=192.0.2.12 time={epoch}',
    prefix + 'Access permitted for user: desktopuser',
    prefix + f'AUTHFAIL: user=bad ip=192.0.2.99 time=123 ip=192.0.2.13 time={epoch}',
    prefix + f'AUTHFAIL: user=test ip=not-an-ip time={epoch}',
    prefix + f'AUTHFAIL: user=test ip= time={epoch}',
    prefix + f'AUTHFAIL: user=test ip=<unknown> time={epoch}',
    prefix + f'AUTHFAIL: user=bad ip=192.0.2.99 time=123 ip=<unknown> time={epoch}',
    f'[{iso}] [INFO ] AUTHFAIL: user=test ip=192.0.2.14 time={epoch}',
    f'[{legacy}] [WARN ] AUTHFAIL: user=test ip=192.0.2.15 time={epoch}',
]
pathlib.Path(sys.argv[1]).write_text('\n'.join(records) + '\n')
PYTEST
fail2ban-regex --usedns=no "$work/xrdp-test.log" "$work/remote-xrdp.conf" > "$work/regex-test.txt"
if ! grep -Eq 'Lines: 12 lines, 0 ignored, 6 matched, 6 missed' "$work/regex-test.txt"; then
    cat "$work/regex-test.txt"
    die 'xrdp filter self-test failed; no configuration was activated.'
fi
fail2ban-regex --usedns=no -o ip "$work/xrdp-test.log" "$work/remote-xrdp.conf" > "$work/matched-ips.txt"
grep -Fxq '192.0.2.13' "$work/matched-ips.txt" || die 'Failed to extract the actual source IP.'
if grep -Fxq '192.0.2.99' "$work/matched-ips.txt"; then die 'Rejected unsafe IP extraction from username.'; fi
install -m 644 "$work/remote-xrdp.conf" "${files[0]}"
install -m 644 "$work/99-remote-desktop.local" "${files[1]}"
fail2ban-client -t
runtime_changed=1
# Package installation may already have started the distribution's default
# sshd jail. Restart Fail2ban so replacing its action also rebuilds nft rules.
systemctl enable fail2ban
systemctl restart fail2ban
for attempt in {1..10}; do
    if fail2ban-client ping >/dev/null 2>&1; then break; fi
    sleep 1
done
fail2ban-client ping
fail2ban-client status sshd
fail2ban-client status remote-xrdp
committed=1
printf '\nDeployed: 5 failures in 10 minutes => 1-hour ban, separately per service.\n'
printf 'Backup: %s\n' "$backup"
printf 'Inspect: sudo fail2ban-client status sshd; sudo fail2ban-client status remote-xrdp\n'
printf 'Unban:   sudo fail2ban-client set JAIL unbanip IP\n'
printf 'nftables rules are created on the first ban; nftables.service need not be enabled.\n'
printf 'AUTHFAIL records with missing/invalid IPs are ignored; verify one actual failed login has a source IP.\n'
