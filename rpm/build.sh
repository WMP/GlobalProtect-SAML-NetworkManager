#!/bin/bash
# Builds the binary RPMs of one target for one architecture in a fresh container.
#
# Usage:
#   rpm/build.sh <target> <amd64|arm64> [<output dir>]
#   rpm/build.sh --install-test <target> <amd64|arm64> [<dir with the RPMs>]
#
# <target> is a name from rpm/targets (fedora43, fedora44, fedora45, el10, leap16.0,
# leap16.1, tumbleweed). The output directory defaults to output/<target>-<arch>. It receives
# the three binary RPMs network-manager-gpclient, -gnome and -plasma, named
# <name>-<version>-<release>.<x86_64|aarch64>.rpm; no source RPM and no debuginfo.
#
# Environment (both or none, digits only): PR_NUMBER and RUN_NUMBER make the
# Release "<Debian revision><dist>.pr<PR_NUMBER>.<RUN_NUMBER>", for pull request builds.
# Other arch than the host needs QEMU binfmt (docker run --privileged tonistiigi/binfmt --install all).
#
# --install-test installs the RPMs of the directory in a fresh container of the target
# and checks them (rpm/install-test.sh). Run it after a build; it does not build.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RPM_DIR=$ROOT/rpm

die() {
    echo "error: $*" >&2
    exit 1
}

install_test=
if [ "${1:-}" = "--install-test" ]; then
    install_test=1
    shift
fi

[ $# -ge 2 ] && [ $# -le 3 ] || die "usage: rpm/build.sh [--install-test] <target> <amd64|arm64> [<dir>]"
target=$1
arch=$2

case "$arch" in
    amd64) rpm_arch=x86_64 ;;
    arm64) rpm_arch=aarch64 ;;
    *) die "unknown architecture '$arch' (use amd64 or arm64)" ;;
esac

# Column 1 target, 2 image, 3 dist tag, 4 family
image=
dist=
family=
while read -r name img dst fam _rest; do
    case "$name" in '' | '#'*) continue ;; esac
    if [ "$name" = "$target" ]; then
        image=$img
        dist=$dst
        family=$fam
    fi
done <"$RPM_DIR/targets"
[ -n "$image" ] || die "unknown target '$target' (see rpm/targets)"

outdir=${3:-$ROOT/output/$target-$arch}
mkdir -p "$outdir"
outdir=$(cd "$outdir" && pwd)

if [ -n "$install_test" ]; then
    ls "$outdir"/*.rpm >/dev/null 2>&1 || die "no RPMs in $outdir"
    exec docker run --rm --platform "linux/$arch" \
        -v "$outdir:/rpms:ro" -v "$RPM_DIR/install-test.sh:/install-test.sh:ro" \
        "$image" bash /install-test.sh /rpms
fi

version=$(python3 "$RPM_DIR/debian_changelog.py" version)
revision=$(python3 "$RPM_DIR/debian_changelog.py" release)
pr_suffix=$(python3 "$RPM_DIR/debian_changelog.py" pr-suffix "${PR_NUMBER:-}" "${RUN_NUMBER:-}")

# SDBUS_VERSION, SDBUS_SHA256, SDBUS_URL
# shellcheck source=rpm/sources
. "$RPM_DIR/sources"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/in/SOURCES" "$work/in/SPECS"

# Source archive: the repository with the submodules, without git data and build products
deps=$ROOT/external/GlobalProtect-openconnect/crates/openconnect/deps
[ -e "$deps/openconnect/configure.ac" ] && [ -e "$deps/libxml2/configure.ac" ] \
    || die "submodules are missing: git submodule update --init --recursive"
src_name=network-manager-gpclient-$version
tar -C "$ROOT" -czf "$work/in/SOURCES/$src_name.tar.gz" \
    --transform "s,^\./,$src_name/," \
    --exclude=.git --exclude=./output --exclude=./.claude --exclude=./external/GlobalProtect-openconnect/target \
    --exclude=./plugins/plasma/build --exclude='*.o' --exclude='*.so' --exclude='*.rpm' \
    --exclude='*.deb' --exclude=__pycache__ \
    .

# Bundled python-sdbus, checked against the pinned sha256
sdbus_file=$work/in/SOURCES/sdbus-$SDBUS_VERSION.tar.gz
curl -fsSL --retry 3 -o "$sdbus_file" "$SDBUS_URL"
echo "$SDBUS_SHA256  $sdbus_file" | sha256sum -c - >/dev/null \
    || die "sha256 of $SDBUS_URL does not match rpm/sources"

# The spec and the %changelog generated from debian/changelog
{
    cat "$RPM_DIR/network-manager-gpclient.spec"
    python3 "$RPM_DIR/debian_changelog.py" changelog
} >"$work/in/SPECS/network-manager-gpclient.spec"

# rpm refuses a macro with an empty body: define pr_suffix for pull request builds only
pr_define=()
[ -z "$pr_suffix" ] || pr_define=(--define "pr_suffix $pr_suffix")

mkdir -p "$work/out"
echo "Building $target $arch: version $version, release $revision$dist$pr_suffix"
docker run --rm --platform "linux/$arch" \
    -v "$work/in:/in:ro" -v "$work/out:/out" \
    -v "$RPM_DIR/container-build.sh:/container-build.sh:ro" \
    "$image" bash /container-build.sh "$family" /in /out \
    --define "project_version $version" \
    --define "debian_release $revision" \
    --define "dist $dist" \
    --define "sdbus_version $SDBUS_VERSION" \
    "${pr_define[@]}"

# Binary RPMs only
rm -f "$outdir"/*.rpm
count=0
for rpm_file in "$work"/out/*.rpm; do
    case "$rpm_file" in
        *.src.rpm | *-debuginfo-* | *-debugsource-*) continue ;;
    esac
    cp "$rpm_file" "$outdir/"
    count=$((count + 1))
done
[ "$count" -eq 3 ] || die "expected 3 binary RPMs, got $count"
for rpm_file in "$outdir"/*.rpm; do
    case "$rpm_file" in *."$rpm_arch".rpm) ;; *) die "unexpected architecture: $rpm_file" ;; esac
done
ls -l "$outdir"
