#!/usr/bin/env bash
# Build the text-clipboard compatibility fix against Ubuntu's vendor-patched xrdp source.
# Run as an ordinary user on Ubuntu 26.04 after installing build dependencies and
# enabling deb-src. This script neither installs packages nor changes services.
# Usage: ./build-xrdp-chansrv.sh [build-parent-directory] [verified-source-archive-directory]
set -Eeuo pipefail
umask 022
if [[ $# == 1 && ( $1 == --help || $1 == -h ) ]]; then
    printf 'Usage: %s [build-parent-directory] [verified-source-archive-directory]\n' "${0##*/}"
    printf 'Run as an ordinary user on the exact supported OS/package version.\n'
    printf 'Uses signed APT source metadata unless a pinned archive directory is supplied.\n'
    exit 0
fi

readonly EXPECTED_VERSION='0.10.1-4.1ubuntu0.1'
readonly SOURCE_DIRECTORY='xrdp-0.10.1'
readonly BUILD_JOBS=2
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
readonly PATCH_SCRIPT="$SCRIPT_DIR/patch-xrdp-clipboard.py"
source_archives="${2:-}"
if [[ -n "$source_archives" ]]; then
    source_archives="$(cd -- "$source_archives" && pwd -P)"
fi

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ $# -le 2 ]] || die 'Usage: build-xrdp-chansrv.sh [build-parent-directory] [verified-source-archive-directory]'
[[ "$(id -u)" -ne 0 ]] || die 'Run this script as an ordinary user, not root.'
[[ -r /etc/os-release ]] || die '/etc/os-release is unavailable.'
# shellcheck source=/dev/null
. /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 26.04 ]] || die 'Ubuntu 26.04 is required.'
[[ -r "$PATCH_SCRIPT" ]] || die "Missing patch script: $PATCH_SCRIPT"

for tool in apt-get dpkg-query dpkg-architecture dpkg-parsechangelog \
    dpkg-buildflags dpkg-source python3 autoconf automake libtoolize \
    pkg-config gcc g++ make patch xz sha256sum ldd readelf strip tee; do
    command -v "$tool" >/dev/null || die "Missing build prerequisite: $tool"
done
[[ "$(dpkg-architecture -qDEB_HOST_ARCH)" == amd64 ]] || die 'This build was prepared for amd64.'
installed_version="$(dpkg-query -W -f='${Version}' xrdp)"
[[ "$installed_version" == "$EXPECTED_VERSION" ]] || \
    die "Installed xrdp is $installed_version; expected $EXPECTED_VERSION. Re-review before building."
pkg-config --exists openssl opus x11 xfixes xrandr || \
    die 'Missing development packages for OpenSSL, Opus, or X11.'
[[ -r /usr/include/security/pam_appl.h ]] || die 'Install libpam0g-dev before building.'

# Use a writable persistent directory with enough space for the source/build.
build_parent="${1:-$HOME}"
[[ -d "$build_parent" && -w "$build_parent" ]] || die 'Build parent must be an existing writable directory.'
build_dir="$(mktemp -d "$build_parent/xrdp-chansrv-build.XXXXXXXX")"
build_dir="$(cd -- "$build_dir" && pwd -P)"
readonly build_dir
exec > >(tee -a "$build_dir/build.log") 2>&1
trap 'rc=$?; printf "Build failed at line %s (exit %s). Work directory: %s\n" "$LINENO" "$rc" "$build_dir" >&2; exit "$rc"' ERR

printf 'Build directory: %s\nUbuntu xrdp version: %s\n' "$build_dir" "$EXPECTED_VERSION"
df -h "$build_dir"
cd -- "$build_dir"
# apt-get authenticates the source through the signed APT repository metadata.
# dpkg-source then applies the complete Debian quilt series during extraction.
if [[ -n "$source_archives" ]]; then
    # Fallback for a host without deb-src: the three files are pinned to the
    # official Ubuntu archive downloaded over HTTPS. This is checksum
    # validation against that reference, not a fresh APT-signature validation.
    (cd -- "$source_archives" && sha256sum --check "$SCRIPT_DIR/source-archives.sha256")
    cp "$source_archives"/xrdp_0.10.1-4.1ubuntu0.1.dsc .
    cp "$source_archives"/xrdp_0.10.1.orig.tar.gz .
    cp "$source_archives"/xrdp_0.10.1-4.1ubuntu0.1.debian.tar.xz .
    dpkg-source -x xrdp_0.10.1-4.1ubuntu0.1.dsc
    source_auth='official HTTPS reference plus pinned DSC/archive SHA256'
else
    apt-get --only-source source "xrdp=$EXPECTED_VERSION"
    source_auth='signed APT repository metadata'
fi
cd -- "$SOURCE_DIRECTORY"
[[ "$(dpkg-parsechangelog -S Version)" == "$EXPECTED_VERSION" ]] || die 'Extracted source version mismatch.'

python3 - "$SCRIPT_DIR/debian-patches-series.txt" <<'PY'
from pathlib import Path
import hashlib
import sys

def entries(path):
    return [line.split('#', 1)[0].strip() for line in Path(path).read_text().splitlines()
            if line.split('#', 1)[0].strip()]

series = entries('debian/patches/series')
applied = entries('.pc/applied-patches')
expected = entries(sys.argv[1])
if len(series) != 5 or series != applied or series != expected:
    raise SystemExit('ERROR: Expected 5 Ubuntu quilt patches are not fully applied in order.')
base_hash = hashlib.sha256(Path('sesman/chansrv/clipboard.c').read_bytes()).hexdigest()
if base_hash != 'ffaa2ee7999984ed0ccb2f22ee3236a32addb84261088abf9140e7e310e52936':
    raise SystemExit('ERROR: clipboard.c does not match the reviewed Ubuntu source.')
print(f'Ubuntu quilt verification passed: all {len(series)} patches applied.')
PY

mkdir -p "$build_dir/output"
cp debian/patches/series "$build_dir/output/debian-patches-series.txt"
cp .pc/applied-patches "$build_dir/output/debian-patches-applied.txt"
cp sesman/chansrv/clipboard.c "$build_dir/clipboard.c.before"
python3 "$PATCH_SCRIPT" sesman/chansrv/clipboard.c
python3 - "$build_dir" <<'PY'
import difflib
from pathlib import Path
import sys

build = Path(sys.argv[1])
before = (build / 'clipboard.c.before').read_text().splitlines(keepends=True)
after = Path('sesman/chansrv/clipboard.c').read_text().splitlines(keepends=True)
patch = ''.join(difflib.unified_diff(
    before, after,
    fromfile='a/sesman/chansrv/clipboard.c',
    tofile='b/sesman/chansrv/clipboard.c',
))
if not patch:
    raise SystemExit('ERROR: Fresh Ubuntu source unexpectedly needed no clipboard changes.')
(build / 'output' / 'clipboard.patch').write_text(patch)
PY

export DEB_BUILD_MAINT_OPTIONS='hardening=+all'
export DEB_LDFLAGS_MAINT_APPEND='-Wl,--as-needed'
eval "$(dpkg-buildflags --export=sh)"
export PKG_CONFIG_PATH="$PWD/pkgconfig"

./bootstrap
./configure \
    --no-recursion \
    --build="$(dpkg-architecture -qDEB_BUILD_GNU_TYPE)" \
    --prefix=/usr \
    --includedir=/usr/include --mandir=/usr/share/man --infodir=/usr/share/info \
    --libexecdir="/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)" \
    --disable-silent-rules --disable-maintainer-mode --disable-dependency-tracking \
    --libdir="/usr/lib/$(dpkg-architecture -qDEB_HOST_MULTIARCH)" \
    --sysconfdir=/etc \
    --localstatedir=/var \
    --with-socketdir=/run/xrdp/sockdir \
    --disable-shared --enable-static \
    --disable-painter --disable-rfxcodec \
    --enable-ipv6 --enable-vsock \
    --disable-fuse --enable-opus \
    --enable-pam --enable-pam-config=debian \
    --without-imlib2 --without-freetype2

# Reproduce Debian's generated Makefile PID-directory adjustment.
python3 - <<'PY'
from pathlib import Path
import re

for directory in ('common', 'libipm', 'sesman/libsesman', 'sesman/chansrv'):
    path = Path(directory) / 'Makefile'
    text = path.read_text()
    updated = re.sub(r'(XRDP_PID_PATH[^\n]*?/run)(?!/xrdp)', r'\1/xrdp', text)
    if text != updated:
        path.write_text(updated)
PY

# Build the three internal libraries and the channel server.
# Internal libraries are static; system libraries remain dynamically linked.
make -C common -j"$BUILD_JOBS"
make -C libipm -j"$BUILD_JOBS"
make -C sesman/libsesman -j"$BUILD_JOBS"
make -C sesman/chansrv -j"$BUILD_JOBS"

[[ "$(dpkg-query -W -f='${Version}' xrdp)" == "$EXPECTED_VERSION" ]] || \
    die 'Installed xrdp changed during the build; do not deploy this output.'
binary="$PWD/sesman/chansrv/xrdp-chansrv"
[[ -x "$binary" ]] || die 'The channel server binary was not produced.'
readelf -h "$binary" > "$build_dir/output/elf-header.txt"
ldd "$binary" | tee "$build_dir/output/ldd.txt"
if grep -Eq 'not found|lib(common|ipm|sesman)\.so' "$build_dir/output/ldd.txt"; then
    die 'Binary has missing dependencies or unexpectedly links xrdp internal shared libraries.'
fi
readelf -d "$binary" | tee "$build_dir/output/elf-dynamic.txt"

install -m 0755 "$binary" "$build_dir/output/xrdp-chansrv"
strip --strip-unneeded "$build_dir/output/xrdp-chansrv"
cp "$PATCH_SCRIPT" "$build_dir/output/patch-xrdp-clipboard.py"
cp "$SCRIPT_DIR/build-xrdp-chansrv.sh" "$build_dir/output/build-xrdp-chansrv.sh"
cp "$SCRIPT_DIR/source-archives.sha256" "$build_dir/output/source-archives.sha256"
sha256sum "$build_dir"/*.dsc "$build_dir"/*.tar.* > "$build_dir/output/source-archives-actual.sha256"
{
    printf 'xrdp_source_version=%s\n' "$EXPECTED_VERSION"
    printf 'source_validation=%s\n' "$source_auth"
    printf 'architecture=%s\n' "$(dpkg-architecture -qDEB_HOST_ARCH)"
    printf 'build_time_utc=%s\n' "$(date -u +%FT%TZ)"
    printf 'internal_libraries=static; system_libraries=dynamic\n'
    printf 'socket_directory=/run/xrdp/sockdir\n'
    printf 'build_directory=%s\n' "$build_dir"
} > "$build_dir/output/build-info.txt"
cd -- "$build_dir/output"
sha256sum xrdp-chansrv clipboard.patch patch-xrdp-clipboard.py \
    build-xrdp-chansrv.sh debian-patches-series.txt debian-patches-applied.txt source-archives.sha256 \
    | tee SHA256SUMS
printf '\nBuild complete. Candidate binary: %s/output/xrdp-chansrv\n' "$build_dir"
printf 'No packages, installed binaries, sessions, or services were modified.\n'
