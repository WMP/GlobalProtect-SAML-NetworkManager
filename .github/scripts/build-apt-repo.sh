#!/bin/bash
# Build a (signed) apt repository from a directory of .deb files.
#
#   build-apt-repo.sh <incoming-dir> <output-dir> [gpg-key-id]
#
# The repository is always rebuilt from scratch - GitHub Releases are the source
# of truth, this tree is a derived artifact. Without a key id the repository is
# left unsigned, which is only useful for local testing.
#
# Layout produced:
#   dists/<suite>/{Release,Release.gpg,InRelease}
#   dists/<suite>/main/binary-amd64/{Packages,Packages.gz}
#   pool/<suite>/main/n/network-manager-gpclient/*.deb
#   gpclient-archive-keyring.gpg, index.html, .nojekyll
#
# Requires: dpkg-dev (dpkg-scanpackages), apt-utils (apt-ftparchive), gnupg.

set -euo pipefail

INCOMING="${1:?usage: build-apt-repo.sh <incoming-dir> <output-dir> [gpg-key-id]}"
OUTDIR="${2:?usage: build-apt-repo.sh <incoming-dir> <output-dir> [gpg-key-id]}"
GPG_KEY="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../pages/index.html"

ORIGIN="GlobalProtect-SAML-NetworkManager"
LABEL="GlobalProtect NetworkManager plugin"
DESCRIPTION="NetworkManager VPN plugin for GlobalProtect (SAML/SSO)"
ARCH="amd64"
COMPONENT="main"
SOURCE_PACKAGE="network-manager-gpclient"
# Ubuntu releases we build for; the suite name is the release codename
SUITES=(jammy noble resolute)

log() { echo "[build-apt-repo] $*"; }
die() { echo "[build-apt-repo] ERROR: $*" >&2; exit 1; }

# Which suite does this .deb belong to?
#
# Current builds carry the codename in the version (1.4.0-1~noble1), so that is
# read from the package itself: GitHub replaces the tilde with a dot when it
# stores a release asset, so the file name is not trustworthy. Releases
# v1.0.0/v1.2.0 predate the versioned builds and only carry the release in their
# file name (_ubuntu24.04.deb).
suite_for_deb() {
    local file="$1" version codename name

    version="$(dpkg-deb -f "$file" Version 2>/dev/null || true)"
    codename="$(printf '%s' "$version" | sed -n 's/.*~\([a-z][a-z]*\)[0-9]*$/\1/p')"

    if [ -n "$codename" ]; then
        for suite in "${SUITES[@]}"; do
            if [ "$suite" = "$codename" ]; then
                echo "$codename"
                return 0
            fi
        done
        log "WARNING: $(basename "$file") is built for unknown release '$codename'"
        return 1
    fi

    name="$(basename "$file")"
    case "$name" in
        *_ubuntu22.04.deb) echo jammy ;;
        *_ubuntu24.04.deb) echo noble ;;
        *_ubuntu26.04.deb) echo resolute ;;
        *) return 1 ;;
    esac
}

[ -d "$INCOMING" ] || die "incoming directory does not exist: $INCOMING"

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

# --- Fill the pool ----------------------------------------------------------

shopt -s nullglob
debs=("$INCOMING"/*.deb)
shopt -u nullglob
[ ${#debs[@]} -gt 0 ] || die "no .deb files found in $INCOMING"

for deb in "${debs[@]}"; do
    suite="$(suite_for_deb "$deb")" || die \
        "cannot tell which Ubuntu release $(basename "$deb") is for - expected a
       ~<codename>1 suffix in its version or a _ubuntu<version>.deb file name"

    pool="$OUTDIR/pool/$suite/$COMPONENT/${SOURCE_PACKAGE:0:1}/$SOURCE_PACKAGE"
    mkdir -p "$pool"
    cp "$deb" "$pool/"
done

# --- Index each suite -------------------------------------------------------

cd "$OUTDIR"

for suite in "${SUITES[@]}"; do
    binary_dir="dists/$suite/$COMPONENT/binary-$ARCH"
    mkdir -p "$binary_dir"

    if [ -d "pool/$suite" ]; then
        count=$(find "pool/$suite" -name '*.deb' | wc -l)
    else
        count=0
        # Still publish an empty suite: a user on this release then gets "no
        # packages" from apt instead of a 404 on every update
        mkdir -p "pool/$suite/$COMPONENT"
    fi
    log "$suite: $count package(s)"

    # --multiversion keeps every release in the index, not just the newest.
    # No --arch: that option filters by *filename pattern* (*_amd64.deb), which
    # silently drops the legacy _ubuntu24.04.deb names. We only build amd64 and
    # dpkg-scanpackages reads the real architecture from each package anyway.
    dpkg-scanpackages --multiversion "pool/$suite" > "$binary_dir/Packages"
    gzip -9nc "$binary_dir/Packages" > "$binary_dir/Packages.gz"

    entries=$(grep -c '^Package:' "$binary_dir/Packages" || true)
    [ "$entries" -eq "$count" ] || die \
        "$suite: indexed $entries of $count packages - check dpkg-scanpackages output"

    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin="$ORIGIN" \
        -o APT::FTPArchive::Release::Label="$LABEL" \
        -o APT::FTPArchive::Release::Suite="$suite" \
        -o APT::FTPArchive::Release::Codename="$suite" \
        -o APT::FTPArchive::Release::Architectures="$ARCH" \
        -o APT::FTPArchive::Release::Components="$COMPONENT" \
        -o APT::FTPArchive::Release::Description="$DESCRIPTION" \
        release "dists/$suite" > "dists/$suite/Release"

    if [ -n "$GPG_KEY" ]; then
        gpg --batch --yes --local-user "$GPG_KEY" \
            --clearsign -o "dists/$suite/InRelease" "dists/$suite/Release"
        gpg --batch --yes --local-user "$GPG_KEY" \
            -abs -o "dists/$suite/Release.gpg" "dists/$suite/Release"
    fi
done

# --- Key, landing page, Jekyll opt-out --------------------------------------

fingerprint="(unsigned test build)"
if [ -n "$GPG_KEY" ]; then
    gpg --export "$GPG_KEY" > gpclient-archive-keyring.gpg
    fingerprint="$(gpg --batch --with-colons --fingerprint "$GPG_KEY" \
        | awk -F: '/^fpr:/ {print $10; exit}')"
fi

touch .nojekyll

# One table row per suite: the newest version of the core package. The index
# lists every version we ever published, in pool order, so they have to be
# sorted - "the first one" is the oldest.
version_rows=""
for suite in "${SUITES[@]}"; do
    packages="dists/$suite/$COMPONENT/binary-$ARCH/Packages"
    version="$(awk -v pkg="Package: $SOURCE_PACKAGE" '
        $0 == pkg { found = 1; next }
        found && /^Version:/ { print $2; found = 0 }
    ' "$packages" 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$version" ] || version="&mdash;"
    version_rows="$version_rows<tr><td><code>$suite</code></td><td><code>$version</code></td></tr>"
done

if [ -f "$TEMPLATE" ]; then
    sed -e "s|__FINGERPRINT__|$fingerprint|g" \
        -e "s|__UPDATED__|$(date -u '+%Y-%m-%d %H:%M UTC')|g" \
        -e "s|__VERSION_ROWS__|$version_rows|g" \
        "$TEMPLATE" > index.html
else
    log "WARNING: $TEMPLATE not found, no landing page generated"
fi

log "repository built in $OUTDIR"
if [ -z "$GPG_KEY" ]; then
    log "WARNING: unsigned repository - apt will need [trusted=yes]"
fi
