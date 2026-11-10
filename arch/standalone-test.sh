#!/bin/bash
# Tests the user mode of arch/PKGBUILD: plain `makepkg -s` with no helper script and no
# environment variables, as a user does after `git clone --branch v<version>`.
#
# Usage: arch/standalone-test.sh [<output dir>]      (default: output/arch-standalone)
#
# The sources of the PKGBUILD are git repositories on GitHub. To test without them, this makes
# local bare mirrors of the checked-out project (tagged v<pkgver> at HEAD) and of its three
# submodules, copies arch/PKGBUILD and the .install file to a temporary directory with the
# `git+https://github.com/...` URLs changed to `git+file:///...` of the mirrors (the #tag and
# #commit pins stay), and runs makepkg in a fresh archlinux:base-devel container as a normal
# user. The network is still needed (pacman, crates.io, PyPI), GitHub is not.
# Then it checks that there are exactly the three packages of the version in the PKGBUILD and
# installs them in a fresh container (arch/build.sh --install-test).
#
# Needs Docker, git (a full clone: not shallow) and the submodules (git submodule update --init --recursive). The project
# comes from HEAD of the working tree (uncommitted changes of the sources are not in it; the
# PKGBUILD is the working copy).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PLATFORM=linux/amd64

die() {
    echo "error: $*" >&2
    exit 1
}

[ $# -le 1 ] || die "usage: arch/standalone-test.sh [<output dir>]"
outdir=${1:-$ROOT/output/arch-standalone}
mkdir -p "$outdir"
outdir=$(cd "$outdir" && pwd)

# the values of the user mode (the branch after `else`)
pkgver=$(sed -n '/^else$/,$ s/^  pkgver=//p' "$ROOT/arch/PKGBUILD")
pkgrel=$(sed -n '/^else$/,$ s/^  pkgrel=//p' "$ROOT/arch/PKGBUILD")
[[ $pkgver =~ ^[0-9][0-9A-Za-z.]*$ && $pkgrel =~ ^[0-9]+$ ]] || die "no user mode pkgver/pkgrel in arch/PKGBUILD"

gp=$ROOT/external/GlobalProtect-openconnect
deps=$gp/crates/openconnect/deps
[ -e "$deps/openconnect/configure.ac" ] && [ -e "$deps/libxml2/configure.ac" ] \
    || die "submodules are missing: git submodule update --init --recursive"

[ "$(git -C "$ROOT" rev-parse --is-shallow-repository)" = false ] \
    || die "the repository is shallow: git fetch --unshallow (a shallow repository cannot be pushed to a mirror)"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/mirrors" "$work/pkg" "$work/out"

# mirror <name> <working tree>: a bare repository with HEAD of the working tree as branch main.
# A submodule is checked out detached, so a plain mirror clone would not have its commit.
mirror() {
    git init -q --bare "$work/mirrors/$1.git"
    git -C "$2" -c push.negotiate=false push -q "$work/mirrors/$1.git" HEAD:refs/heads/main
}
mirror project "$ROOT"
git -C "$work/mirrors/project.git" tag "v$pkgver" "$(git -C "$ROOT" rev-parse HEAD)"
mirror GlobalProtect-openconnect "$gp"
mirror openconnect "$deps/openconnect"
mirror libxml2 "$deps/libxml2"

# Only the URLs of the git sources change
cp "$ROOT/arch/network-manager-gpclient.install" "$work/pkg/"
sed -e 's|git+https://github.com/WMP/GlobalProtect-SAML-NetworkManager\.git|git+file:///mirrors/project.git|' \
    -e 's|git+https://github.com/yuezk/GlobalProtect-openconnect\.git|git+file:///mirrors/GlobalProtect-openconnect.git|' \
    -e 's|git+https://github.com/yuezk/openconnect\.git|git+file:///mirrors/openconnect.git|' \
    -e 's|git+https://github.com/GNOME/libxml2\.git|git+file:///mirrors/libxml2.git|' \
    "$ROOT/arch/PKGBUILD" >"$work/pkg/PKGBUILD"
! grep -q 'git+https://' "$work/pkg/PKGBUILD" || die "a git source of the PKGBUILD has an URL that is not mirrored"
[ "$(grep -c 'git+file:///mirrors/' "$work/pkg/PKGBUILD")" -eq 4 ] || die "expected 4 mirrored git sources"

echo "Building $pkgver-$pkgrel from the mirrors with plain makepkg"
docker run --rm -i --platform "$PLATFORM" \
    -v "$work/mirrors:/mirrors:ro" -v "$work/pkg:/in:ro" -v "$work/out:/out" \
    archlinux:base-devel bash -s <<'CONTAINER'
set -euo pipefail
pacman -Syu --noconfirm --needed base-devel sudo git
useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: ALL' >/etc/sudoers.d/builder
# the mirrors belong to another user
runuser -u builder -- git config --global safe.directory '*'
build=/home/builder/build
mkdir -p "$build"
cp /in/PKGBUILD /in/network-manager-gpclient.install "$build/"
chown -R builder: "$build"
# plain makepkg: no environment variables of the project
runuser -u builder -- env -C "$build" PKGDEST="$build/out" makepkg -s --noconfirm --noprogressbar
cp -v "$build"/out/*.pkg.tar.zst /out/
CONTAINER

rm -f "$outdir"/*.pkg.tar.zst
count=0
for pkg_file in "$work"/out/*.pkg.tar.zst; do
    case "$pkg_file" in *-debug-*) continue ;; esac
    cp "$pkg_file" "$outdir/"
    count=$((count + 1))
done
[ "$count" -eq 3 ] || die "expected 3 packages, got $count"
for name in network-manager-gpclient network-manager-gpclient-gnome network-manager-gpclient-plasma; do
    [ -f "$outdir/$name-$pkgver-$pkgrel-x86_64.pkg.tar.zst" ] || die "missing $name-$pkgver-$pkgrel-x86_64.pkg.tar.zst"
done
ls -l "$outdir"

bash "$ROOT/arch/build.sh" --install-test "$outdir"
