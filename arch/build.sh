#!/bin/bash
# Builds the three Arch Linux packages (x86_64) in a fresh archlinux container.
#
# Usage:
#   arch/build.sh [<output dir>]
#   arch/build.sh --install-test [<dir with the packages>]
#
# The output directory defaults to output/arch-x86_64. It receives the packages
# network-manager-gpclient, -gnome and -plasma, named
# <name>-<version>-<release>-x86_64.pkg.tar.zst; no debug package.
#
# Environment (both or none, digits only): PR_NUMBER and RUN_NUMBER make the version
# "<upstream version>.pr<PR_NUMBER>.<RUN_NUMBER>", for pull request builds.
#
# --install-test installs the packages of the directory in a fresh container
# (arch/install-test.sh). Run it after a build; it does not build.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ARCH_DIR=$ROOT/arch
RPM_DIR=$ROOT/rpm
# Official Arch Linux has x86_64 only (Arch Linux ARM is another distribution)
PLATFORM=linux/amd64

die() {
    echo "error: $*" >&2
    exit 1
}

install_test=
if [ "${1:-}" = "--install-test" ]; then
    install_test=1
    shift
fi
[ $# -le 1 ] || die "usage: arch/build.sh [--install-test] [<dir>]"

outdir=${1:-$ROOT/output/arch-x86_64}
mkdir -p "$outdir"
outdir=$(cd "$outdir" && pwd)

if [ -n "$install_test" ]; then
    ls "$outdir"/*.pkg.tar.zst >/dev/null 2>&1 || die "no packages in $outdir"
    # rpm/install-test.sh has the checks that do not depend on the package format
    exec docker run --rm --platform "$PLATFORM" \
        -v "$outdir:/pkgs:ro" -v "$ARCH_DIR/install-test.sh:/install-test.sh:ro" \
        -v "$RPM_DIR/install-test.sh:/rpm-install-test.sh:ro" \
        archlinux:base bash /install-test.sh /pkgs
fi

version=$(python3 "$RPM_DIR/debian_changelog.py" version)
revision=$(python3 "$RPM_DIR/debian_changelog.py" release)
# Digits only, both or none: the same validation as for the RPMs
pr_suffix=$(python3 "$RPM_DIR/debian_changelog.py" pr-suffix "${PR_NUMBER:-}" "${RUN_NUMBER:-}")
# makepkg: pkgrel is "int[.int]"; pkgver has no "-", ":" or whitespace
[[ $revision =~ ^[0-9]+$ ]] || die "Debian revision '$revision' is not an integer: pkgrel needs one"
[[ $version =~ ^[A-Za-z0-9._+]+$ ]] || die "version '$version' is not valid for pkgver"

# SDBUS_VERSION, SDBUS_SHA256, SDBUS_URL
# shellcheck source=rpm/sources
. "$RPM_DIR/sources"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/in" "$work/out"

# Source archive: the repository with the submodules and vendor/, without git data and
# build products
deps=$ROOT/external/GlobalProtect-openconnect/crates/openconnect/deps
[ -e "$deps/openconnect/configure.ac" ] && [ -e "$deps/libxml2/configure.ac" ] \
    || die "submodules are missing: git submodule update --init --recursive"
src_name=network-manager-gpclient-$version
tar -C "$ROOT" -czf "$work/in/$src_name.tar.gz" \
    --transform "s,^\./,$src_name/," \
    --exclude=.git --exclude=./output --exclude=./.claude --exclude=./external/GlobalProtect-openconnect/target \
    --exclude=./plugins/plasma/build --exclude='*.o' --exclude='*.so' --exclude='*.rpm' \
    --exclude='*.deb' --exclude='*.pkg.tar.zst' --exclude=__pycache__ \
    .

# Bundled python-sdbus (not in the Arch repositories), checked against the pinned sha256
sdbus_file=$work/in/sdbus-$SDBUS_VERSION.tar.gz
curl -fsSL --retry 3 -o "$sdbus_file" "$SDBUS_URL"
echo "$SDBUS_SHA256  $sdbus_file" | sha256sum -c - >/dev/null \
    || die "sha256 of $SDBUS_URL does not match rpm/sources"

cp "$ARCH_DIR/PKGBUILD" "$ARCH_DIR/network-manager-gpclient.install" "$work/in/"

echo "Building Arch x86_64: version $version$pr_suffix, release $revision"
docker run --rm --platform "$PLATFORM" \
    -v "$work/in:/in:ro" -v "$work/out:/out" \
    -v "$ARCH_DIR/container-build.sh:/container-build.sh:ro" \
    -e PROJECT_VERSION="$version" -e DEBIAN_RELEASE="$revision" -e PR_SUFFIX="$pr_suffix" \
    -e SDBUS_VERSION="$SDBUS_VERSION" -e SDBUS_SHA256="$SDBUS_SHA256" \
    archlinux:base-devel bash /container-build.sh /in /out

# Exactly the three packages
rm -f "$outdir"/*.pkg.tar.zst
count=0
for pkg_file in "$work"/out/*.pkg.tar.zst; do
    case "$pkg_file" in *-debug-*) continue ;; esac
    cp "$pkg_file" "$outdir/"
    count=$((count + 1))
done
[ "$count" -eq 3 ] || die "expected 3 packages, got $count"
for pkg_file in "$outdir"/*.pkg.tar.zst; do
    case "$pkg_file" in *-"$version$pr_suffix-$revision"-x86_64.pkg.tar.zst) ;; *) die "unexpected file name: $pkg_file" ;; esac
done
ls -l "$outdir"
