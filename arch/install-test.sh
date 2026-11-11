#!/bin/bash
# Installs the packages of a build in a fresh archlinux container and checks them.
# Exits non-zero with a message on the first failed check.
#
# Run it with arch/build.sh --install-test [<dir>] (after a build), or by hand from
# the repository root, with the packages in <dir>:
#
#   docker run --rm --platform linux/amd64 \
#     -v "$PWD/output/arch-x86_64:/pkgs:ro" -v "$PWD/arch/install-test.sh:/install-test.sh:ro" \
#     -v "$PWD/rpm/install-test.sh:/rpm-install-test.sh:ro" \
#     archlinux:base bash /install-test.sh /pkgs
#
# Usage inside the container: install-test.sh <dir with the packages>
# /rpm-install-test.sh (rpm/install-test.sh) has the checks that do not depend on the
# package format: OpenSSL, stripped programs, the vpnc-script and its hooks.
set -euo pipefail

# shellcheck source=rpm/install-test.sh
. /rpm-install-test.sh

pkgs=${1:-}
[ -n "$pkgs" ] && [ -d "$pkgs" ] || { echo "usage: install-test.sh <dir with the packages>" >&2; exit 2; }

shopt -s nullglob
files=("$pkgs"/*.pkg.tar.zst)
[ "${#files[@]}" -eq 3 ] || fail "expected 3 packages in $pkgs, found ${#files[@]}"

# The container image leaves out documentation and translations (NoExtract): extract everything,
# or pacman -Qkk below would report them as missing
sed -i 's/^NoExtract/#NoExtract/' /etc/pacman.conf

echo "== Installing ${files[*]##*/}"
# The packages are not signed here (only the repository they are published in is).
# A full upgrade first: no partial upgrades, the image can be older than the repositories.
pacman -Syu --noconfirm
pacman -U --noconfirm "${files[@]}"

echo "== Checks"
for pkg in network-manager-gpclient network-manager-gpclient-gnome network-manager-gpclient-plasma; do
    pacman -Q "$pkg" >/dev/null || fail "$pkg is not installed"
    # Files changed after the install: none expected (pacman -Qkk compares size, mode,
    # time and checksum; the config files of the package are unchanged as well)
    if ! out=$(pacman -Qkk "$pkg" 2>&1); then
        fail "pacman -Qkk $pkg: $out"
    fi
done

for path in \
    /usr/bin/gpclient /usr/bin/gpauth \
    /usr/lib/NetworkManager/nm-gpclient-service \
    /usr/lib/NetworkManager/gpclient-nm-script \
    /usr/lib/NetworkManager/VPN/nm-gpclient-service.name \
    /usr/libexec/gpclient/browser-wrapper /usr/libexec/gpclient/edge-wrapper \
    /usr/libexec/nm-gpclient-auth-dialog \
    /usr/share/applications/gpgui.desktop \
    /usr/lib/systemd/system/nm-gpclient.service \
    /etc/apparmor.d/usr.lib.NetworkManager.nm-gpclient-service \
    /usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service \
    /usr/share/dbus-1/system.d/nm-gpclient.conf \
    /etc/vpnc/connect.d/90-gpclient-routing \
    /etc/vpnc/disconnect.d/90-gpclient-routing \
    /etc/vpnc/post-connect.d/90-gpclient-notify \
    /etc/vpnc/reconnect.d/90-gpclient-notify \
    /usr/lib/NetworkManager/libnm-vpn-plugin-gpclient.so \
    /usr/lib/NetworkManager/libnm-vpn-plugin-gpclient-editor.so \
    /usr/lib/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so \
    /usr/lib/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so \
    /usr/lib/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.json \
    /usr/share/licenses/network-manager-gpclient/LICENSE \
    /usr/share/licenses/network-manager-gpclient/LICENSE.GPL-3.0 \
    /usr/share/licenses/network-manager-gpclient/COPYING.LESSER.python-sdbus \
    /usr/share/licenses/network-manager-gpclient/third-party-licenses.txt; do
    [ -e "$path" ] || fail "missing file $path"
done

# The licenses of the Rust crates are listed
[ -s /usr/share/licenses/network-manager-gpclient/third-party-licenses.txt ] \
    || fail "third-party-licenses.txt is empty"

# Each translation domain is in its own package
pacman -Qlq network-manager-gpclient | grep -qx /usr/share/locale/pl/LC_MESSAGES/NetworkManager-gpclient.mo \
    || fail "network-manager-gpclient has no Polish catalog"
pacman -Qlq network-manager-gpclient-plasma \
    | grep -qx /usr/share/locale/pl/LC_MESSAGES/plasmanetworkmanagement_gpclientui.mo \
    || fail "network-manager-gpclient-plasma has no Polish catalog"

# gpgui is not shipped: the entry is hidden, the callback handler stays
grep -qx 'NoDisplay=true' /usr/share/applications/gpgui.desktop \
    || fail "gpgui.desktop has no NoDisplay=true"
grep -qx 'MimeType=x-scheme-handler/globalprotectcallback;' /usr/share/applications/gpgui.desktop \
    || fail "gpgui.desktop does not handle globalprotectcallback"

[ -L /usr/lib/libnm-gpclient-properties ] \
    || fail "/usr/lib/libnm-gpclient-properties is not a symlink"
[ -e /usr/lib/libnm-gpclient-properties ] \
    || fail "/usr/lib/libnm-gpclient-properties is a dangling symlink"
pacman -Qqo /usr/lib/libnm-gpclient-properties | grep -qx network-manager-gpclient-gnome \
    || fail "libnm-gpclient-properties is not in network-manager-gpclient-gnome"

# Every program and plugin finds its shared libraries
for exe in /usr/bin/gpclient /usr/bin/gpauth \
    /usr/lib/NetworkManager/libnm-*gpclient*.so \
    /usr/lib/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so; do
    if missing=$(ldd "$exe" 2>&1 | grep 'not found'); then
        fail "$exe: missing libraries: $missing"
    fi
done

check_openssl /usr/bin/gpclient /usr/bin/gpauth
check_stripped /usr/bin/gpclient /usr/bin/gpauth
check_vpnc_script

/usr/bin/gpclient --version >/dev/null || fail "gpclient --version"
/usr/bin/gpauth --version >/dev/null || fail "gpauth --version"

# The Python programs run with the interpreter of their shebang (#!/usr/bin/env python3)
shebang=$(head -n1 /usr/lib/NetworkManager/nm-gpclient-service)
[ "$(head -n1 /usr/libexec/nm-gpclient-auth-dialog)" = "$shebang" ] \
    || fail "the service and the auth dialog have different shebangs"
case "$shebang" in
    '#!'*) read -r -a python <<<"${shebang#\#!}" ;;
    *) fail "the service has no shebang" ;;
esac
"${python[@]}" -c 'import sdbus; print("sdbus", sdbus.__file__)' || fail "${python[*]} cannot import sdbus"

# The service needs a D-Bus to run: import it with the real sdbus
"${python[@]}" - <<'PY' || fail "the service does not import"
import importlib.machinery
import importlib.util

path = "/usr/lib/NetworkManager/nm-gpclient-service"
loader = importlib.machinery.SourceFileLoader("nm_gpclient_service", path)
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
PY

# The auth dialog is a GTK program: its Python module imports must resolve
"${python[@]}" -c 'import gi; gi.require_version("Gtk", "3.0")' || fail "${python[*]} gi / GTK 3 typelib missing"

# Removing the core package takes the desktop packages with it (-Rs would not: -R of
# the core package alone must refuse while they depend on it)
if pacman -R --noconfirm network-manager-gpclient >/dev/null 2>&1; then
    fail "pacman removed network-manager-gpclient although the desktop packages need it"
fi
pacman -R --noconfirm network-manager-gpclient-gnome network-manager-gpclient-plasma network-manager-gpclient \
    || fail "the packages cannot be removed"
[ ! -e /usr/bin/gpclient ] || fail "gpclient is still there after the removal"

echo "OK: all checks passed"
