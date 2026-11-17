#!/usr/bin/env bash
# Install one locally built channel server. Run as root after GUI logout.
# Usage: install-chansrv.sh /absolute/path/to/build/output
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 027
if [[ $# == 1 && ( $1 == --help || $1 == -h ) ]]; then
    printf 'Usage: %s BUILD_OUTPUT_DIRECTORY\n' "${0##*/}"
    printf 'Run as root after all RDP users log out. Validates and diverts one locally built chansrv.\n'
    exit 0
fi
readonly EXPECTED_VERSION='0.10.1-4.1ubuntu0.1'
readonly TARGET=/usr/sbin/xrdp-chansrv
readonly DISTRIB=/usr/sbin/xrdp-chansrv.distrib
readonly STATE=/var/lib/xrdp-clipboard-fix
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $(id -u) == 0 && $# == 1 ]] || die 'Run as root: install-chansrv.sh BUILD_OUTPUT_DIRECTORY'
candidate_dir="$(cd -- "$1" && pwd -P)"
[[ "$(dpkg-query -W -f='${Version}' xrdp)" == "$EXPECTED_VERSION" ]] || die 'Installed xrdp version does not match this build.'
[[ "$(dpkg --print-architecture)" == amd64 ]] || die 'This candidate requires amd64.'
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 26.04 ]] || die 'Ubuntu 26.04 is required.'
[[ -f "$candidate_dir/xrdp-chansrv" && ! -L "$candidate_dir/xrdp-chansrv" ]] || die 'Candidate must be a regular file.'
(cd -- "$candidate_dir" && sha256sum --check SHA256SUMS)
grep -Fxq "xrdp_source_version=$EXPECTED_VERSION" "$candidate_dir/build-info.txt" || die 'Source version metadata mismatch.'
grep -Fxq 'architecture=amd64' "$candidate_dir/build-info.txt" || die 'Build architecture metadata mismatch.'
readelf -h "$candidate_dir/xrdp-chansrv" | grep -Eq 'Machine:.*Advanced Micro Devices X86-64' || die 'ELF machine is not x86-64.'
readelf -h "$candidate_dir/xrdp-chansrv" | grep -Eq 'Type:.*DYN' || die 'Expected Debian PIE executable.'
readelf -d "$candidate_dir/xrdp-chansrv" | grep -q BIND_NOW || die 'Expected Debian immediate binding hardening.'
dependency_output="$(ldd "$candidate_dir/xrdp-chansrv")"
printf '%s\n' "$dependency_output"
if grep -Eq 'not found|lib(common|ipm|sesman)\.so' <<< "$dependency_output"; then
    die 'Missing target-host libraries or unexpected internal shared library.'
fi
[[ -z "$(dpkg-divert --listpackage "$TARGET")" ]] || die 'An existing diversion requires review before this installer runs.'
[[ -f "$TARGET" && ! -L "$TARGET" && ! -e "$DISTRIB" ]] || die 'Installed target or backup path is unexpected.'
[[ ! -e "$STATE/installed.json" ]] || die 'An existing local installation state requires review.'
command -v pgrep >/dev/null || die 'Install procps for the active-session check.'
if pgrep -x xrdp-chansrv >/dev/null; then
    die 'Active xrdp-chansrv processes remain; log out all RDP sessions before installing.'
fi
backup_dir="$(mktemp -d /var/backups/xrdp-clipboard.XXXXXXXX)"
exec > >(tee -a "$backup_dir/install.log") 2>&1
cp -a -- "$TARGET" "$backup_dir/xrdp-chansrv.before"
cp -a -- "$candidate_dir" "$backup_dir/build-output"
printf '%s\n' "$dependency_output" > "$backup_dir/target-ldd.txt"
sha256sum "$TARGET" "$candidate_dir/xrdp-chansrv" > "$backup_dir/before-install.sha256"
install -d -m 0700 "$STATE"
staging="$(mktemp /usr/sbin/.xrdp-chansrv.clipboard.XXXXXXXX)"
cleanup() { [[ -z "${staging:-}" ]] || rm -f -- "$staging"; }
trap cleanup EXIT
install -o root -g root -m 0755 -- "$candidate_dir/xrdp-chansrv" "$staging"
# Dpkg updates the .distrib file while this local diversion is active.
dpkg-divert --local --add --rename --divert "$DISTRIB" "$TARGET"
if ! mv -f -- "$staging" "$TARGET"; then
    dpkg-divert --local --remove --rename --divert "$DISTRIB" "$TARGET"
    die 'Candidate install failed; the packaged binary was restored.'
fi
staging=''
python3 - "$STATE/installed.json" "$backup_dir" "$EXPECTED_VERSION" <<'PY'
import datetime, hashlib, json, pathlib, sys
target = pathlib.Path('/usr/sbin/xrdp-chansrv')
state = {
    'version': sys.argv[3],
    'installed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'target': str(target),
    'diverted_packaged_binary': '/usr/sbin/xrdp-chansrv.distrib',
    'backup_directory': sys.argv[2],
    'custom_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(state, indent=2) + '\n')
PY
sha256sum "$TARGET" "$DISTRIB" | tee "$backup_dir/after-install.sha256"
printf 'Installed. Audit and backups: %s\n' "$backup_dir"
printf 'Start a fresh desktop login, then verify clipboard and /proc/PID/exe hash. No service was restarted.\n'
