#!/bin/bash
# Runs INSIDE the build container (started by arch/build.sh): installs the build
# dependencies of the PKGBUILD and builds the packages with makepkg as a normal user.
#
# Usage: container-build.sh <input dir> <output dir>
#   input dir   has PKGBUILD, network-manager-gpclient.install and the two source archives
#   output dir  receives the *.pkg.tar.zst
# The environment has PROJECT_VERSION, DEBIAN_RELEASE, PR_SUFFIX, SDBUS_VERSION and
# SDBUS_SHA256 (see the PKGBUILD).
set -euo pipefail

indir=$1
outdir=$2

# A full upgrade first: the image can be older than the repositories (no partial upgrades)
pacman -Syu --noconfirm --needed base-devel sudo

# makepkg refuses to run as root; it installs the build dependencies through sudo
useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: ALL' >/etc/sudoers.d/builder
build=/home/builder/build
mkdir -p "$build"
cp -r "$indir"/. "$build/"
chown -R builder: "$build"

runuser -u builder -- env -C "$build" \
    PROJECT_VERSION="$PROJECT_VERSION" DEBIAN_RELEASE="$DEBIAN_RELEASE" PR_SUFFIX="${PR_SUFFIX:-}" \
    SDBUS_VERSION="$SDBUS_VERSION" SDBUS_SHA256="$SDBUS_SHA256" PKGDEST="$build/out" \
    makepkg --syncdeps --noconfirm --noprogressbar

cp -v "$build"/out/*.pkg.tar.zst "$outdir/"
