#!/bin/bash
# Build a (signed) apt repository from a directory of .deb files.
#
#   [APT_REPO_URL=<url>] build-apt-repo.sh <incoming-dir> <output-dir> [gpg-key-id]
#
# APT_REPO_URL is the public address of the repository, without a trailing
# slash (default: https://wmp.github.io/GlobalProtect-SAML-NetworkManager); it
# must be an http(s) URL without spaces. It ends up in the "Changelogs:" field of
# the Release file of every suite that has a changelog, and on the landing page,
# so set it when the repository is served from anywhere else (a local test
# server, say).
#
# The repository is always rebuilt from scratch - GitHub Releases are the source
# of truth, this tree is a derived artifact. Without a key id the repository is
# left unsigned, which is only useful for local testing.
#
# Layout produced:
#   dists/<suite>/{Release,Release.gpg,InRelease}
#   dists/<suite>/main/binary-<arch>/{Packages,Packages.gz}   (amd64 and arm64)
#   pool/<suite>/main/n/network-manager-gpclient/*.deb        (all architectures)
#   changelogs/main/<prefix>/<source>/<source>_<version>_changelog   (for `apt changelog`)
#       taken from changelog.Debian.gz, or changelog.gz in a native package
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
# Architectures we build for; each gets its own binary-<arch> index per suite
ARCHES=(amd64 arm64)
COMPONENT="main"
# Where the repository is served; `apt changelog` downloads from here
APT_REPO_URL="${APT_REPO_URL:-https://wmp.github.io/GlobalProtect-SAML-NetworkManager}"
while [[ "$APT_REPO_URL" == */ ]]; do APT_REPO_URL="${APT_REPO_URL%/}"; done
[[ "$APT_REPO_URL" =~ ^https?://[^[:space:]]+$ ]] \
    || { echo "[build-apt-repo] ERROR: APT_REPO_URL must be an http(s) URL without spaces, got '$APT_REPO_URL'" >&2; exit 1; }
SOURCE_PACKAGE="network-manager-gpclient"
# Ubuntu releases we build for; the suite name is the release codename
SUITES=(jammy noble oracular resolute)

log() { echo "[build-apt-repo] $*"; }
err() { echo "[build-apt-repo] ERROR: $*" >&2; }
die() { err "$*"; exit 1; }

# Scratch space for unpacking packages; removed on exit (also when die() exits)
tmpdir="$(mktemp -d)"
all_packages=""
trap 'rm -f "$all_packages"; rm -rf "$tmpdir"' EXIT

# Escape a value for the replacement part of a sed "s|...|...|" command:
# backslash, & (the matched text) and the | delimiter are special there
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

# Escape a value for HTML text and attributes
html_escape() {
    printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
        -e 's/"/\&quot;/g' -e "s/'/\&#39;/g"
}

# Fields of one .deb, read with a single dpkg-deb call: sets pkg, version,
# source (only the name), source_version (the version of the source package;
# dpkg falls back to the package's own) and arch. dpkg-deb --show refuses a
# malformed Source field; the package is then read without it and gets an
# empty source, which changelog_path rejects (no changelog, the build goes on).
read_control() {
    local deb="$1"
    pkg="" version="" source="" source_version="" arch=""
    if ! IFS=$'\t' read -r pkg version source source_version arch < <(
            dpkg-deb --show --showformat='${Package}\t${Version}\t${source:Package}\t${source:Version}\t${Architecture}\n' \
                "$deb" 2> /dev/null); then
        source="" source_version=""
        IFS=$'\t' read -r pkg version arch < <(
            dpkg-deb --show --showformat='${Package}\t${Version}\t${Architecture}\n' "$deb") || true
    fi
    [ -n "$pkg" ] && [ -n "$version" ] && [ -n "$arch" ]
}

# Path of the changelog of the package read_control has just read, relative to
# the "Changelogs:" base URL, in the layout apt expects:
# <component>/<prefix>/<source>/<source>_<version> (the version without epoch;
# the prefix is 4 letters for lib* sources). Source and version come from the
# package and end up in a path, so anything but a plain name is refused.
changelog_path() {
    local v="${source_version#*:}"
    if ! [[ "$source" =~ ^[a-z0-9][a-z0-9.+-]*$ && "$v" =~ ^[0-9A-Za-z.+~-]+$ ]] \
        || [[ "$source$v" == *..* ]]; then
        return 1
    fi
    case "$source" in
        lib*) echo "$COMPONENT/${source:0:4}/$source/${source}_$v" ;;
        *) echo "$COMPONENT/${source:0:1}/$source/${source}_$v" ;;
    esac
}

# Write the changelog of a .deb (uncompressed) to $3. A package without one is
# normal and only logged; a package that cannot be unpacked is an ERROR, but
# the build goes on - the changelog is not worth losing the repository for.
# debhelper installs changelog.Debian.gz, or changelog.gz when the package is
# native (no Debian revision in the version). The data archive is streamed, not
# written to disk: first listed (member names may or may not start with "./"),
# then the one member is extracted.
extract_changelog() {
    local deb="$1" package="$2" dest="$3" name="$(basename "$1")" member found="" entry
    local list="$tmpdir/list" errors="$tmpdir/errors" out="$tmpdir/changelog"

    if ! { dpkg-deb --fsys-tarfile "$deb" 2> "$errors.dpkg" | tar -tf - > "$list" 2> "$errors"; }; then
        err "cannot read the files of $name, no changelog: $(cat "$errors.dpkg" "$errors" | tr '\n' ' ')"
        return 1
    fi
    for member in changelog.Debian.gz changelog.gz; do
        while IFS= read -r entry; do
            if [ "${entry#./}" = "usr/share/doc/$package/$member" ]; then
                found="$entry"
                break 2
            fi
        done < "$list"
    done
    if [ -z "$found" ]; then
        log "no changelog.Debian.gz or changelog.gz in $name"
        return 1
    fi
    if ! { dpkg-deb --fsys-tarfile "$deb" 2> "$errors.dpkg" \
            | tar -xOf - --no-wildcards -- "$found" 2> "$errors" \
            | gzip -dc > "$out" 2> "$errors.gz"; }; then
        err "cannot extract $found from $name: $(cat "$errors.dpkg" "$errors" "$errors.gz" | tr '\n' ' ')"
        return 1
    fi
    if [ ! -s "$out" ]; then
        log "${found##*/} in $name is empty"
        return 1
    fi
    mkdir -p "$(dirname "$dest")"
    mv "$out" "$dest"
}

# Which suite does this .deb belong to?
#
# Current builds carry the codename in the version (1.4.0-1~noble1), so that is
# read from the package itself: GitHub replaces the tilde with a dot when it
# stores a release asset, so the file name is not trustworthy. Releases
# v1.0.0/v1.2.0 predate the versioned builds and only carry the release in their
# file name (_ubuntu24.04.deb).
suite_for_deb() {
    local file="$1" version="$2" codename name

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
        *_ubuntu24.10.deb) echo oracular ;;
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

# Number of packages per "<suite>/<arch>" ("all" packages belong to every arch)
declare -A pkg_count=()
# Suites with at least one published changelog (they get the Changelogs field)
declare -A suite_has_changelog=()

for deb in "${debs[@]}"; do
    read_control "$deb" || die "cannot read the control data of $(basename "$deb")"
    suite="$(suite_for_deb "$deb" "$version")" || die \
        "cannot tell which Ubuntu release $(basename "$deb") is for - expected a
       ~<codename>1 suffix in its version or a _ubuntu<version>.deb file name"

    # The real architecture comes from the package itself: legacy file names
    # (_ubuntu24.04.deb) do not carry it
    case " ${ARCHES[*]} all " in
        *" $arch "*) ;;
        *) die "$(basename "$deb") has unsupported architecture '$arch' (expected: ${ARCHES[*]})" ;;
    esac
    pkg_count["$suite/$arch"]=$(( ${pkg_count["$suite/$arch"]:-0} + 1 ))

    pool="$OUTDIR/pool/$suite/$COMPONENT/${SOURCE_PACKAGE:0:1}/$SOURCE_PACKAGE"
    mkdir -p "$pool"
    cp "$deb" "$pool/"

    # Changelog for `apt changelog`: all binaries of a source share one file
    if rel="$(changelog_path)"; then
        changelog="$OUTDIR/changelogs/${rel}_changelog"
        [ -e "$changelog" ] || extract_changelog "$deb" "$pkg" "$changelog" || true
        [ ! -e "$changelog" ] || suite_has_changelog["$suite"]=1
    else
        log "WARNING: not publishing a changelog for $(basename "$deb"):" \
            "invalid source '$source' or version '$source_version'"
    fi
done

# --- Index each suite -------------------------------------------------------

cd "$OUTDIR"

for suite in "${SUITES[@]}"; do
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
    # silently drops the legacy _ubuntu24.04.deb names. Scan everything once
    # and split by the real architecture that dpkg-scanpackages reads from each
    # package.
    all_packages="$(mktemp)"
    dpkg-scanpackages --multiversion "pool/$suite" > "$all_packages"

    entries=$(grep -c '^Package:' "$all_packages" || true)
    [ "$entries" -eq "$count" ] || die \
        "$suite: indexed $entries of $count packages - check dpkg-scanpackages output"

    for arch in "${ARCHES[@]}"; do
        binary_dir="dists/$suite/$COMPONENT/binary-$arch"
        mkdir -p "$binary_dir"

        # Keep the stanzas built for this arch plus Architecture: all ones
        awk -v arch="$arch" -v RS= -v ORS='\n\n' '
            {
                n = split($0, lines, "\n")
                for (i = 1; i <= n; i++) {
                    if (lines[i] ~ /^Architecture: /) {
                        a = substr(lines[i], 15)
                        if (a == arch || a == "all") print
                        break
                    }
                }
            }
        ' "$all_packages" > "$binary_dir/Packages"
        gzip -9nc "$binary_dir/Packages" > "$binary_dir/Packages.gz"

        expected=$(( ${pkg_count["$suite/$arch"]:-0} + ${pkg_count["$suite/all"]:-0} ))
        entries=$(grep -c '^Package:' "$binary_dir/Packages" || true)
        [ "$entries" -eq "$expected" ] || die \
            "$suite/$arch: indexed $entries of $expected packages - check the Architecture split"
        log "$suite/$arch: $entries package(s)"
    done
    rm -f "$all_packages"

    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin="$ORIGIN" \
        -o APT::FTPArchive::Release::Label="$LABEL" \
        -o APT::FTPArchive::Release::Suite="$suite" \
        -o APT::FTPArchive::Release::Codename="$suite" \
        -o APT::FTPArchive::Release::Architectures="${ARCHES[*]}" \
        -o APT::FTPArchive::Release::Components="$COMPONENT" \
        -o APT::FTPArchive::Release::Description="$DESCRIPTION" \
        release "dists/$suite" > "dists/$suite/Release.new"

    # Not every apt-ftparchive can write this field itself: add it ahead of the
    # checksum sections, and only when this suite has a changelog to point to.
    # The line goes in through the environment: awk -v would process backslashes.
    if [ -n "${suite_has_changelog["$suite"]:-}" ]; then
        CHANGELOGS_LINE="Changelogs: $APT_REPO_URL/changelogs/@CHANGEPATH@_changelog" awk '
            !done && /^(MD5Sum|SHA1|SHA256|SHA512):/ { print ENVIRON["CHANGELOGS_LINE"]; done = 1 }
            { print }
            END { if (!done) print ENVIRON["CHANGELOGS_LINE"] }
        ' "dists/$suite/Release.new" > "dists/$suite/Release"
        rm -f "dists/$suite/Release.new"
    else
        mv "dists/$suite/Release.new" "dists/$suite/Release"
    fi

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
    # Same version on every architecture, so just read all the indexes
    packages=("dists/$suite/$COMPONENT"/binary-*/Packages)
    version="$(awk -v pkg="Package: $SOURCE_PACKAGE" '
        $0 == pkg { found = 1; next }
        found && /^Version:/ { print $2; found = 0 }
    ' "${packages[@]}" 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$version" ] || version="&mdash;"
    version_rows="$version_rows<tr><td><code>$suite</code></td><td><code>$version</code></td></tr>"
done

if [ -f "$TEMPLATE" ]; then
    sed -e "s|__FINGERPRINT__|$(sed_escape "$fingerprint")|g" \
        -e "s|__REPO_URL__|$(sed_escape "$(html_escape "$APT_REPO_URL")")|g" \
        -e "s|__UPDATED__|$(sed_escape "$(date -u '+%Y-%m-%d %H:%M UTC')")|g" \
        -e "s|__VERSION_ROWS__|$(sed_escape "$version_rows")|g" \
        "$TEMPLATE" > index.html
else
    log "WARNING: $TEMPLATE not found, no landing page generated"
fi

log "repository built in $OUTDIR"
if [ -z "$GPG_KEY" ]; then
    log "WARNING: unsigned repository - apt will need [trusted=yes]"
fi
