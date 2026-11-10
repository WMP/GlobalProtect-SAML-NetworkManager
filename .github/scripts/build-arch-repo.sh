#!/bin/bash
# Build a (signed) pacman repository from a directory of Arch Linux packages.
#
#   build-arch-repo.sh [--index FILE] [--allow-empty] <incoming-dir> <output-dir> [gpg-key-id]
#
# The repository is always rebuilt from scratch - GitHub Releases are the source
# of truth, this tree is a derived artifact. Without a key id the repository is
# left unsigned, which is only useful for local testing. The secret key is never
# an argument: gpg finds it in the keyring of the GNUPGHOME of the environment
# (the workflow imports it there).
#
# repo-add is part of pacman and not available on Ubuntu runners: it runs in an
# archlinux container (docker) on the packages, without the key. The signatures
# are made on the host.
#
# Layout produced (repository name "gpclient", x86_64 only: official Arch Linux
# has no other architecture):
#   x86_64/*.pkg.tar.zst + x86_64/*.pkg.tar.zst.sig   (detached, binary)
#   x86_64/gpclient.db + .sig, x86_64/gpclient.files + .sig
#   GPG-KEY-gpclient                                  (armored public key)
# gpclient.db and gpclient.files are plain copies of the archives repo-add makes:
# GitHub Pages does not keep symlinks reliably.
#
# A pacman database lists one version of a package, so only the newest version of
# each package is published (repo-add keeps the newest: the order of pacman's
# vercmp); the older ones stay assets of their releases. Names that are not one
# of our three packages are skipped with a warning.
#
# --index FILE: the landing page the apt repository script wrote. The Arch
#   version goes into it, and the Arch sections are removed from it when there
#   is no Arch package at all, so no placeholder is left on the page.
# --allow-empty: no packages is not an error: nothing is built and only the
#   index is cleaned up (the releases before Arch support had none).
#
# Environment: ARCH_REPO_IMAGE (the container with repo-add, default archlinux:base).
#
# Requires: docker, gnupg (for a signed repository), python3 (reads the database).

set -euo pipefail

USAGE="usage: build-arch-repo.sh [--index FILE] [--allow-empty] <incoming-dir> <output-dir> [gpg-key-id]"
IMAGE="${ARCH_REPO_IMAGE:-archlinux:base}"
REPO_NAME="gpclient"
KEY_FILE="GPG-KEY-gpclient"
ARCH_DIR="x86_64"
BEGIN_MARK="<!-- ARCH-BEGIN -->"
END_MARK="<!-- ARCH-END -->"

log() { echo "[build-arch-repo] $*"; }
die() { echo "[build-arch-repo] ERROR: $*" >&2; exit 1; }

INDEX=""
ALLOW_EMPTY=0
positional=()
while [ $# -gt 0 ]; do
    case "$1" in
        --index) [ $# -ge 2 ] || die "--index needs a file. $USAGE"; INDEX="$2"; shift ;;
        --allow-empty) ALLOW_EMPTY=1 ;;
        --*) die "unknown option: $1. $USAGE" ;;
        *) positional+=("$1") ;;
    esac
    shift
done
[ "${#positional[@]}" -ge 2 ] && [ "${#positional[@]}" -le 3 ] || die "$USAGE"
INCOMING="${positional[0]}"
OUTDIR="${positional[1]}"
GPG_KEY="${positional[2]:-}"

# A key id is a fingerprint or key id: hex digits (it ends up in gpg arguments)
if [ -n "$GPG_KEY" ] && ! [[ "$GPG_KEY" =~ ^[0-9A-Fa-f]{8,64}$ ]]; then
    die "the key id must be a fingerprint or key id (hex digits), not '$GPG_KEY'"
fi

[ -d "$INCOMING" ] || die "incoming directory does not exist: $INCOMING"
if [ -n "$INDEX" ] && [ ! -f "$INDEX" ]; then
    die "the index file does not exist: $INDEX"
fi

# --- Which package is ours -------------------------------------------------------

shopt -s nullglob
packages=("$INCOMING"/*.pkg.tar.zst)
shopt -u nullglob

name_re='^(network-manager-gpclient(-gnome|-plasma)?)-([0-9][A-Za-z0-9._+]*)-([0-9]+)-x86_64\.pkg\.tar\.zst$'
selected=()
for pkg in "${packages[@]}"; do
    file="$(basename "$pkg")"
    # A name that is not one of ours is skipped, not fatal: one odd asset in any
    # release must not stop the publication (the apt repository is built in the
    # same job). Nothing of it is copied or passed to a tool.
    if ! [[ "$file" =~ $name_re ]]; then
        log "WARNING: unexpected name of a package, not published: ${file//[^A-Za-z0-9._+~-]/?}"
        continue
    fi
    selected+=("$pkg")
done

# --- The landing page ----------------------------------------------------------

# Replace the placeholder of the Arch sections, or take the sections out
update_index() {
    [ -n "$INDEX" ] || return 0
    if [ "${#selected[@]}" -gt 0 ]; then
        sed -e "/$BEGIN_MARK/d" -e "/$END_MARK/d" -e "s|__ARCH_VERSION__|$1|g" "$INDEX" > "$INDEX.tmp"
    else
        sed -e "/$BEGIN_MARK/,/$END_MARK/d" "$INDEX" > "$INDEX.tmp"
    fi
    mv "$INDEX.tmp" "$INDEX"
}

if [ "${#selected[@]}" -eq 0 ]; then
    if [ "$ALLOW_EMPTY" -eq 1 ]; then
        update_index
        log "no Arch Linux packages: no pacman repository built"
        exit 0
    fi
    die "no Arch Linux packages found in $INCOMING"
fi

command -v docker >/dev/null || die "docker is required (repo-add runs in an archlinux container)"
if [ -n "$GPG_KEY" ]; then
    command -v gpg >/dev/null || die "gpg is required for a signed repository"
fi

# --- Copy, index, sign -----------------------------------------------------------

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR/$ARCH_DIR"
# Absolute: docker run -v takes a relative host path for a volume name
repo="$(cd "$OUTDIR/$ARCH_DIR" && pwd)"
for pkg in "${selected[@]}"; do
    cp "$pkg" "$repo/"
done

# As the user of the host: the files stay ours. No network: repo-add needs none.
# repo-add does not keep the newest version (it only warns about a newer one and replaces
# it with the package it adds last), so the script in the container picks the newest
# of every package with vercmp and adds only that.
docker run --rm -i --network none -e "REPO_NAME=$REPO_NAME" --user "$(id -u):$(id -g)" -v "$repo:/repo" -w /repo "$IMAGE" bash -s <<'CONTAINER' \
    || die "repo-add failed"
set -euo pipefail
declare -A best=()
for file in *.pkg.tar.zst; do
    [[ $file =~ ^(.+)-([^-]+-[0-9]+)-x86_64\.pkg\.tar\.zst$ ]] || continue
    name=${BASH_REMATCH[1]}
    version=${BASH_REMATCH[2]}
    if [ -z "${best[$name]:-}" ] || [ "$(vercmp "$version" "${best[$name]#*|}")" -gt 0 ]; then
        best[$name]="$file|$version"
    fi
done
add=()
for name in "${!best[@]}"; do add+=("${best[$name]%%|*}"); done
repo-add --quiet "$REPO_NAME.db.tar.gz" "${add[@]}"
CONTAINER
for archive in "$REPO_NAME.db.tar.gz" "$REPO_NAME.files.tar.gz"; do
    [ -s "$repo/$archive" ] || die "repo-add did not create $ARCH_DIR/$archive"
done

# The files the database lists: the newest version of every package. The others
# are not in it, pacman would never fetch them.
mapfile -t listed < <(python3 - "$repo/$REPO_NAME.db.tar.gz" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1]) as db:
    for member in db.getmembers():
        if member.isfile() and member.name.endswith("/desc"):
            lines = db.extractfile(member).read().decode().split("\n")
            print(lines[lines.index("%FILENAME%") + 1])
PY
)
[ "${#listed[@]}" -gt 0 ] || die "the database lists no package"
core_version=""
for pkg in "${selected[@]}"; do
    file="$(basename "$pkg")"
    kept=0
    for name in "${listed[@]}"; do
        [ "$name" = "$file" ] && kept=1
    done
    if [ "$kept" -eq 0 ]; then
        rm -f "$repo/$file"
        log "left out $file: not the newest version"
    elif [[ "$file" =~ $name_re ]] && [ "${BASH_REMATCH[1]}" = "network-manager-gpclient" ]; then
        core_version="${BASH_REMATCH[3]}-${BASH_REMATCH[4]}"
    fi
done

# pacman asks for gpclient.db and gpclient.files, not the archives; repo-add made
# symlinks of these names: plain files instead
rm -f "$repo/$REPO_NAME.db" "$repo/$REPO_NAME.files"
mv "$repo/$REPO_NAME.db.tar.gz" "$repo/$REPO_NAME.db"
mv "$repo/$REPO_NAME.files.tar.gz" "$repo/$REPO_NAME.files"
rm -f "$repo"/*.old "$repo"/*.tar.gz*

count=0
for file in "$repo"/*.pkg.tar.zst; do
    count=$((count + 1))
    [ -z "$GPG_KEY" ] || gpg --batch --yes --local-user "$GPG_KEY" --detach-sign --output "$file.sig" "$file"
done
if [ -n "$GPG_KEY" ]; then
    for file in "$repo/$REPO_NAME.db" "$repo/$REPO_NAME.files"; do
        gpg --batch --yes --local-user "$GPG_KEY" --detach-sign --output "$file.sig" "$file"
    done
    for file in "$repo"/*; do
        case "$file" in *.sig) continue ;; esac
        [ -s "$file.sig" ] || die "no signature of $(basename "$file")"
    done
    gpg --batch --yes --export --armor "$GPG_KEY" > "$OUTDIR/$KEY_FILE"
    [ -s "$OUTDIR/$KEY_FILE" ] || die "gpg exported no public key for $GPG_KEY"
fi
log "$ARCH_DIR: $count package(s)"

update_index "$core_version"

log "repository built in $OUTDIR"
if [ -z "$GPG_KEY" ]; then
    log "WARNING: unsigned repository - pacman needs SigLevel = Never for it"
fi
