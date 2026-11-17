#!/usr/bin/env bash
# Restore the current distribution-owned binary, including any intervening updates.
# Run as root after logging out of the affected remote desktop.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 027
if [[ $# == 1 && ( $1 == --help || $1 == -h ) ]]; then
    printf 'Usage: %s\n' "${0##*/}"
    printf 'Run as root after all RDP users log out. Restore the currently diverted distribution binary.\n'
    exit 0
fi
readonly TARGET=/usr/sbin/xrdp-chansrv
readonly DISTRIB=/usr/sbin/xrdp-chansrv.distrib
readonly STATE=/var/lib/xrdp-clipboard-fix
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $(id -u) == 0 && $# == 0 ]] || die 'Run rollback-chansrv.sh as root without arguments.'
[[ -f "$STATE/installed.json" ]] || die 'No installation state from this package was found.'
command -v pgrep >/dev/null || die 'Install procps for the active-session check.'
if pgrep -x xrdp-chansrv >/dev/null; then
    die 'Active xrdp-chansrv processes remain; log out all RDP sessions before rollback.'
fi
[[ "$(dpkg-divert --listpackage "$TARGET")" == LOCAL ]] || die 'Expected local diversion is missing.'
[[ "$(dpkg-divert --truename "$TARGET")" == "$DISTRIB" ]] || die 'Unexpected diversion target.'
[[ -f "$TARGET" && ! -L "$TARGET" && -f "$DISTRIB" && ! -L "$DISTRIB" ]] || die 'Target or distribution binary is missing or unexpected.'
python3 - "$STATE/installed.json" <<'PY'
import hashlib, json, pathlib, sys
state = json.loads(pathlib.Path(sys.argv[1]).read_text())
target = pathlib.Path('/usr/sbin/xrdp-chansrv')
if state['target'] != str(target) or state['diverted_packaged_binary'] != str(target) + '.distrib':
    raise SystemExit('ERROR: Installation metadata names a different binary.')
if hashlib.sha256(target.read_bytes()).hexdigest() != state['custom_sha256']:
    raise SystemExit('ERROR: Active binary changed since installation; review before rollback.')
PY
backup_dir="$(mktemp -d /var/backups/xrdp-clipboard-rollback.XXXXXXXX)"
exec > >(tee -a "$backup_dir/rollback.log") 2>&1
cp -a -- "$STATE/installed.json" "$backup_dir/installed.json"
cp -a -- "$DISTRIB" "$backup_dir/xrdp-chansrv.distrib"
mv -- "$TARGET" "$backup_dir/xrdp-chansrv.custom"
if ! dpkg-divert --local --remove --rename --divert "$DISTRIB" "$TARGET"; then
    if [[ ! -e "$TARGET" ]]; then cp -a -- "$backup_dir/xrdp-chansrv.custom" "$TARGET"; fi
    die 'Dpkg diversion rollback failed; inspect the log and diversion state.'
fi
mv -- "$STATE/installed.json" "$backup_dir/removed-state.json"
sha256sum "$TARGET" | tee "$backup_dir/restored.sha256"
printf 'Restored distribution binary. Audit: %s\nStart a fresh desktop login.\n' "$backup_dir"
