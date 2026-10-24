#!/bin/bash
# Install the test packages of a pull request on this machine.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/WMP/GlobalProtect-SAML-NetworkManager/main/scripts/install-pr-build.sh) <PR number>
#   install-pr-build.sh <PR number> [--desktop gnome|plasma|plasma-6] [--repo OWNER/REPO] [--yes]
#
# CI publishes every open pull request as the prerelease "pr-<N>" (see
# .github/workflows/pr-test-packages.yml) and removes it when the PR closes. This
# picks the package files for your distribution, architecture and desktop from
# that release and hands them to apt (.deb), dnf or zypper (.rpm). The package
# manager asks for confirmation unless --yes is given.
#
# On Ubuntu 24.04 the Plasma editor is network-manager-gpclient-plasma-6 (built
# against the KDE neon repository) on KDE neon, and network-manager-gpclient-plasma
# (Plasma 5) on Ubuntu. Ubuntu 24.04 with plasma-nm 6 or newer from elsewhere is not
# supported: the script stops, and --desktop plasma-6 installs -plasma-6 anyway.
#
# The packages of a pull request carry a version above the release's one
# (1.4.2-1~noble1+pr24.57 sorts after 1.4.2-1~noble1), so apt installs them over
# the released version.
#
# The RPM packages of a pull request have the release <revision><dist>.pr<N>.<run>
# (1.fc44.pr24.57 sorts after 1.fc44). Supported: Fedora 43, 44, 45, EL 10
# (AlmaLinux, Rocky, RHEL, CentOS Stream; needs EPEL and CRB), openSUSE Leap 16.0
# and Tumbleweed, on x86_64 and aarch64. EL 9 is not supported (no webkit2gtk-4.1).
#
# Run it as `bash <(curl ...)`, not `curl ... | bash`: apt asks its question on
# the terminal, and a pipe would take stdin away.
#
# Test hooks: OS_RELEASE_FILE (default /etc/os-release) and GP_RELEASE_API
# (default https://api.github.com/repos/<the repository>).

set -euo pipefail

DEFAULT_REPO="WMP/GlobalProtect-SAML-NetworkManager"
OS_RELEASE="${OS_RELEASE_FILE:-/etc/os-release}"

die() { echo "install-pr-build: $*" >&2; exit 1; }

usage() {
    cat >&2 <<'EOF'
usage: install-pr-build.sh <PR number> [--desktop gnome|plasma|plasma-6] [--repo OWNER/REPO] [--yes]

  <PR number>          the pull request whose test packages to install
  --desktop gnome|plasma|plasma-6
                       which editor plugin to install (default: plasma when
                       XDG_CURRENT_DESKTOP mentions KDE, otherwise gnome). For
                       plasma on Ubuntu 24.04, KDE neon means -plasma-6 and Ubuntu
                       means -plasma, but not with plasma-nm 6 or newer installed
                       (stops). plasma-6 (Ubuntu 24.04 only) installs -plasma-6
                       whatever the system is. On RPM systems the only
                       plasma package is network-manager-gpclient-plasma
  --repo OWNER/REPO    the GitHub repository that published the packages
                       (default: WMP/GlobalProtect-SAML-NetworkManager)
  --yes                do not ask apt, dnf or zypper for confirmation
EOF
}

# --- Arguments ---------------------------------------------------------------

pr=""
desktop=""
repo="$DEFAULT_REPO"
assume_yes=0
positional=0

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --yes) assume_yes=1 ;;
        --desktop)
            [ $# -ge 2 ] || { usage; die "--desktop needs a value: gnome, plasma or plasma-6"; }
            desktop="$2"; shift ;;
        --desktop=*) desktop="${1#--desktop=}" ;;
        --repo)
            [ $# -ge 2 ] || { usage; die "--repo needs a value: OWNER/REPO"; }
            repo="$2"; shift ;;
        --repo=*) repo="${1#--repo=}" ;;
        --*) usage; die "unknown option: $1" ;;
        *)
            positional=$((positional + 1))
            pr="$1" ;;
    esac
    shift
done

[ "$positional" -ge 1 ] || { usage; die "missing the pull request number"; }
[ "$positional" -eq 1 ] || { usage; die "expected one pull request number, got $positional arguments"; }

# Digits only, no leading zero: the number ends up in a URL and a release tag
[[ $pr =~ ^[1-9][0-9]{0,6}$ ]] \
    || die "'$pr' is not a pull request number (1 to 7 digits, no leading zero)"

case "$desktop" in
    ""|gnome|plasma|plasma-6) ;;
    *) die "--desktop must be gnome, plasma or plasma-6, not '$desktop'" ;;
esac

# OWNER/REPO ends up in URLs: GitHub's own character sets, and no "." or ".." as
# the repository name
[[ $repo =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$ && ${repo#*/} != . && ${repo#*/} != .. ]] \
    || die "'$repo' is not a repository name like OWNER/REPO"
REPO="$repo"
API_BASE="${GP_RELEASE_API:-https://api.github.com/repos/$REPO}"

# --- This machine ------------------------------------------------------------

# Read the keys one by one: os-release is not sourced, so nothing in it runs
os_value() {
    local value
    value="$(sed -n "s/^$1=//p" "$OS_RELEASE" | tail -n 1)"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    printf '%s' "$value"
}

[ -r "$OS_RELEASE" ] || die "cannot read $OS_RELEASE: this script supports Ubuntu only, plus KDE neon, Fedora, EL 10 and openSUSE"
os_id="$(os_value ID)"
version_id="$(os_value VERSION_ID)"
# KDE neon is Ubuntu 24.04 with Plasma 6. The RPM targets are the ones of rpm/targets.
rpm_target=""
rpm_dist=""
case "$os_id" in
    ubuntu|neon) package_format=deb ;;
    fedora)
        package_format=rpm
        case "$version_id" in
            43|44|45) rpm_target="fedora$version_id"; rpm_dist=".fc$version_id" ;;
            *) die "Fedora ${version_id:-?} is not supported: the test packages are built for Fedora 43, 44 and 45" ;;
        esac ;;
    almalinux|rocky|rhel|centos)
        package_format=rpm
        case "$version_id" in
            10|10.*) rpm_target=el10; rpm_dist=.el10 ;;
            9|9.*) die "EL 9 ($os_id $version_id) is not supported: gpauth needs webkit2gtk-4.1 and libsoup 3, which EL 9 does not have. Use EL 10" ;;
            *) die "$os_id ${version_id:-?} is not supported: the test packages are built for EL 10 only" ;;
        esac ;;
    opensuse-leap)
        package_format=rpm
        [ "$version_id" = "16.0" ] || die "openSUSE Leap ${version_id:-?} is not supported: the test packages are built for Leap 16.0"
        rpm_target=leap16.0; rpm_dist=.lp160 ;;
    opensuse-tumbleweed)
        package_format=rpm
        rpm_target=tumbleweed; rpm_dist=.tw ;;
    *) die "this is '$os_id', not Ubuntu or KDE neon, and not Fedora, EL 10 or openSUSE: the test packages are built for Ubuntu 22.04, 24.04, 24.10 and 26.04 (and KDE neon, which is Ubuntu 24.04 with Plasma 6), Fedora 43 to 45, EL 10, openSUSE Leap 16.0 and Tumbleweed only" ;;
esac

if [ "$package_format" = deb ]; then
    codename="$(os_value VERSION_CODENAME)"
    if [ -z "$codename" ]; then
        case "$version_id" in
            22.04) codename=jammy ;;
            24.04) codename=noble ;;
            24.10) codename=oracular ;;
            26.04) codename=resolute ;;
        esac
    fi
    # Same suites as .github/scripts/build-apt-repo.sh
    case "$codename" in
        jammy|noble|oracular|resolute) ;;
        *) die "Ubuntu ${version_id:-?} (${codename:-unknown}) is not supported: the test packages are built for jammy (22.04), noble (24.04), oracular (24.10) and resolute (26.04)" ;;
    esac

    [ "$os_id" != "neon" ] || [ "$codename" = "noble" ] \
        || die "KDE neon is supported on its Ubuntu 24.04 (noble) base only, not on ${codename:-unknown}"

    arch="$(dpkg --print-architecture)"
    case "$arch" in
        amd64|arm64) ;;
        *) die "architecture '$arch' is not supported: the test packages are built for amd64 and arm64" ;;
    esac

    [ "$desktop" != "plasma-6" ] || [ "$codename" = "noble" ] \
        || die "--desktop plasma-6 is for Ubuntu 24.04 (noble) only, not ${codename:-unknown}: the other releases have one Plasma package, network-manager-gpclient-plasma"
    release_id="$codename"
else
    machine="$(uname -m)"
    case "$machine" in
        x86_64|aarch64) arch="$machine" ;;
        *) die "architecture '$machine' is not supported: the test packages are built for x86_64 and aarch64" ;;
    esac
    [ "$desktop" != "plasma-6" ] \
        || die "--desktop plasma-6 is for Ubuntu 24.04 (noble) only: on $rpm_target the plasma package is network-manager-gpclient-plasma"
    release_id="$rpm_target"
fi

if [ -z "$desktop" ]; then
    case "${XDG_CURRENT_DESKTOP:-}" in
        *[Kk][Dd][Ee]*) desktop=plasma ;;
        *) desktop=gnome ;;
    esac
fi

# The Plasma package. Ubuntu 24.04 has Plasma 5, and KDE neon, its Plasma 6
# variant, has network-manager-gpclient-plasma-6 (built against the KDE neon
# repository). Ubuntu 24.04 with plasma-nm 6 or newer from elsewhere is not
# supported: the script does not pick a package for it, --desktop plasma-6 does.
# Every other release has one Plasma package, network-manager-gpclient-plasma.
plasma_package="network-manager-gpclient-plasma"
if [ "$desktop" = plasma-6 ]; then
    plasma_package="network-manager-gpclient-plasma-6"
    desktop=plasma
elif [ "$package_format" = deb ] && [ "$desktop" = plasma ] && [ "$codename" = noble ]; then
    if [ "$os_id" = neon ]; then
        plasma_package="network-manager-gpclient-plasma-6"
    else
        plasma_nm="$(dpkg-query -W -f='${Version}' plasma-nm 2>/dev/null || true)"
        plasma_nm="${plasma_nm#*:}" # the epoch: 4:6.1.5-0ubuntu1
        if [[ $plasma_nm =~ ^([0-9]+)\. ]] && [ "${BASH_REMATCH[1]}" -ge 6 ]; then
            die "this is Ubuntu 24.04 with plasma-nm $plasma_nm (Plasma 6): only KDE neon is supported for Plasma 6 on Ubuntu 24.04. Use --desktop plasma-6 to install network-manager-gpclient-plasma-6 anyway (not tested), or --desktop gnome"
        fi
    fi
fi

# --- The release -------------------------------------------------------------

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# apt downloads as the unprivileged _apt user and complains about a private dir
chmod 755 "$tmp"

tag="pr-$pr"
url="$API_BASE/releases/tags/$tag"
echo "Looking for the test packages of PR #$pr ($url)"
status="$(curl -sSL -H 'Accept: application/vnd.github+json' \
    --connect-timeout 20 --max-time 120 \
    -o "$tmp/release.json" -w '%{http_code}' "$url")" \
    || die "cannot reach GitHub ($url)"

case "$status" in
    200) ;;
    404) die "there is no release '$tag': the build of PR #$pr may still be running or have failed, or the PR is closed (its packages are removed then). The \"Test packages\" check of the PR shows the state." ;;
    *) die "GitHub answered HTTP $status for $url (rate limit? try again later)" ;;
esac

# Package names: <package>_<version>~<codename>1[+pr<N>.<run>]_<arch>.deb or
# <package>-<version>-<revision><dist>[.pr<N>.<run>].<arch>.rpm, the suffix
# being the one of a pull request's build (the release's own builds have
# none). GitHub stores the "~" of a release asset as "." (1.4.2-1.noble1), so
# both are accepted. The names come from the release listing and are matched
# strictly before they are used anywhere: other targets, other architectures,
# other pull requests, source and debuginfo packages never match.
selection="$(python3 - "$tmp/release.json" "$package_format" "$release_id" "$arch" "$desktop" "$pr" "$plasma_package" "$rpm_dist" <<'PY'
import json
import re
import sys
import urllib.parse

path, package_format, release_id, arch, desktop, pr, plasma_package, rpm_dist = sys.argv[1:9]
try:
    with open(path, encoding="utf-8") as handle:
        assets = json.load(handle).get("assets", [])
except (OSError, ValueError, AttributeError):
    sys.exit("the release answer is not valid JSON")
if not isinstance(assets, list):
    sys.exit("the release answer has no asset list")


def find(package):
    if package_format == "deb":
        pattern = re.compile(
            re.escape(package) + r"_[0-9][A-Za-z0-9.+-]*[~.]" + re.escape(release_id)
            + r"[0-9]+(?:[+.]pr" + re.escape(pr) + r"\.[0-9]+)?_" + re.escape(arch) + r"\.deb"
        )
    else:
        pattern = re.compile(
            re.escape(package) + r"-[0-9][A-Za-z0-9.+~_]*-[0-9]+" + re.escape(rpm_dist)
            + r"(?:\.pr" + re.escape(pr) + r"\.[0-9]+)?\." + re.escape(arch) + r"\.rpm"
        )
    found = []
    for asset in assets:
        name = asset.get("name") if isinstance(asset, dict) else None
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if isinstance(name, str) and isinstance(url, str) and pattern.fullmatch(name):
            found.append((name, url))
    if len(found) > 1:
        sys.exit("more than one %s package for %s/%s in the release" % (package, release_id, arch))
    return found[0] if found else None


wanted = ["network-manager-gpclient"]
if desktop == "gnome":
    wanted.append("network-manager-gpclient-gnome")
else:
    wanted.append(plasma_package)

for package in wanted:
    asset = find(package)
    if asset is None:
        why = " (it is built for amd64 only)" if package.endswith("-plasma-6") and arch != "amd64" else ""
        sys.exit("the release has no %s package for %s/%s%s (the build of this PR may have failed or is not finished)"
                 % (package, release_id, arch, why))
    # The name as GitHub writes it in a download address ("+" is "%2B")
    print("%s\t%s\t%s\t%s" % (package, asset[0], asset[1], urllib.parse.quote(asset[0], safe="")))
PY
)" || die "cannot pick the packages for $release_id/$arch from release $tag (see the message above)"

[ -n "$selection" ] || die "no packages selected from release $tag"

echo
if [ "$package_format" = deb ]; then
    echo "Ubuntu ${version_id:-?} ($codename), $arch, desktop: $desktop"
else
    echo "$os_id ${version_id:-?} ($rpm_target), $arch, desktop: $desktop"
fi
echo "Unreviewed test build of PR #$pr, installed as root. Packages:"
package_files=()
packages=()
while IFS=$'\t' read -r package name asset_url encoded_name; do
    # With GP_RELEASE_API set (tests, mirrors) any URL is fine; by default only
    # GitHub's own download address is trusted: GitHub writes "+" as "%2B" there
    # (the name is matched strictly above, so the plain "+" is the same file)
    if [ -z "${GP_RELEASE_API:-}" ]; then
        case "$asset_url" in
            "https://github.com/$REPO/releases/download/$tag/$encoded_name") ;;
            "https://github.com/$REPO/releases/download/$tag/$name") ;;
            *) die "unexpected download address for $name: $asset_url" ;;
        esac
    fi
    echo "  $name"
    curl -fsSL --connect-timeout 20 --max-time 600 -o "$tmp/$name" "$asset_url" \
        || die "cannot download $name"
    chmod 644 "$tmp/$name"
    package_files+=("$tmp/$name")
    packages+=("$package")
done <<< "$selection"

# --- Install -----------------------------------------------------------------

runner=()
[ "$(id -u)" -eq 0 ] || runner=(sudo)

if [ "$package_format" = rpm ]; then
    if [ "$rpm_target" = el10 ]; then
        cat <<'EOF'

EL 10: the dependencies come from EPEL and CRB. If the installation reports
missing packages, enable them first (if you have not yet):
    sudo dnf install epel-release && sudo /usr/bin/crb enable
EOF
    fi
    case "$rpm_target" in
        leap16.0|tumbleweed)
            # The test packages are not signed
            manager=(zypper)
            [ "$assume_yes" -eq 1 ] && manager+=(--non-interactive)
            manager+=(install --allow-unsigned-rpm)
            remove_command="zypper remove" ;;
        *)
            manager=(dnf install)
            [ "$assume_yes" -eq 1 ] && manager+=(-y)
            remove_command="dnf remove" ;;
    esac
    echo
    echo "Running: ${runner[*]:+${runner[*]} }${manager[*]} <the packages above>"
    "${runner[@]}" "${manager[@]}" "${package_files[@]}" || die "${manager[0]} failed, nothing more was done"
    cat <<EOF

Installed.

Without the test packages: sudo $remove_command ${packages[*]}
and install the released packages again (see docs/RPM_REPO.md or
https://github.com/$REPO/releases).
EOF
    exit 0
fi

if [ "$codename" = "jammy" ]; then
    cat <<'EOF'

Ubuntu 22.04 only: python3-sdbus is not in apt, install it with pip first
(if you have not yet). The service runs as root, so install it system-wide:
    sudo apt install python3-pip
    sudo pip3 install sdbus
EOF
fi

apt_args=(install)
[ "$assume_yes" -eq 1 ] && apt_args+=(-y)

echo
echo "Running: ${runner[*]:+${runner[*]} }apt ${apt_args[*]} <the packages above>"
"${runner[@]}" apt "${apt_args[@]}" "${package_files[@]}" || die "apt failed, nothing more was done"

# The test build is newer than the release, so apt's candidate is the installed
# build, which no repository has: --reinstall cannot download it. Pick the
# release by the suite of our apt repository (its name is the codename).
released=()
for package in "${packages[@]}"; do released+=("$package/$codename"); done

cat <<EOF

Installed.

Back to the released version, with the apt repository set up as described in
docs/APT_REPO.md:
    sudo apt update && sudo apt install --allow-downgrades ${released[*]}
Without the apt repository: sudo apt remove ${packages[*]}
and install the released packages again (https://github.com/$REPO/releases).
EOF
