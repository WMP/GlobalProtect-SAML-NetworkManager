#!/bin/bash
# Installs the RPMs of a build in a fresh container of the target image and checks them.
# Exits non-zero with a message on the first failed check.
#
# Run it from the repository root, with the RPMs in <dir> and the image of the target
# (column 2 of rpm/targets), for example fedora44 on amd64:
#
#   docker run --rm --platform linux/amd64 \
#     -v "$PWD/output/fedora44-amd64:/rpms:ro" -v "$PWD/rpm/install-test.sh:/install-test.sh:ro" \
#     registry.fedoraproject.org/fedora:44 bash /install-test.sh /rpms
#
# or: rpm/build.sh --install-test fedora44 amd64 [<dir>]
#
# Usage inside the container: install-test.sh <dir with the RPMs>
set -euo pipefail

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

# check_openssl <executable>...: none links another libssl than OpenSSL 3 or later
# (libssl.so.3, libssl.so.4 on Fedora 45; on openSUSE pkgconfig(openssl) can resolve
# to LibreSSL, libssl.so.5x/6x, which gpclient does not work with), and the first one
# (gpclient) links such a libssl.
check_openssl() {
    local exe out first=1
    for exe in "$@"; do
        out=$(ldd "$exe" 2>&1) || fail "ldd $exe: $out"
        if grep -qi 'libressl' <<<"$out" || grep -Eq 'libssl\.so\.([0-2]|[0-9][0-9]+)([^0-9]|$)' <<<"$out"; then
            fail "$exe does not link OpenSSL 3 or later: $(grep -Ei 'libressl|libssl|libcrypto' <<<"$out" | tr '\n' ' ')"
        fi
        if [ "$first" = 1 ] && ! grep -Eq 'libssl\.so\.[3-9]([^0-9]|$)' <<<"$out"; then
            fail "$exe does not link libssl.so.3 or later"
        fi
        first=0
    done
}

# check_stripped <executable>...: no symbol table (SHT_SYMTAB section) in the ELF
# file. Fedora's rpmbuild exports RUSTFLAGS with -Cstrip=none, which overrides
# upstream's strip = true unless the spec builds without them.
check_stripped() {
    local exe
    for exe in "$@"; do
        python3 - "$exe" <<'PY' || fail "$exe is not stripped (it has a symbol table)"
import struct, sys
with open(sys.argv[1], "rb") as f:
    data = f.read()
if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
    sys.exit("%s: not a 64-bit little-endian ELF file" % sys.argv[1])
shoff, = struct.unpack_from("<Q", data, 0x28)
shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
for i in range(shnum):
    sh_type, = struct.unpack_from("<I", data, shoff + i * shentsize + 4)
    if sh_type == 2:  # SHT_SYMTAB
        sys.exit(1)
PY
    done
}

# check_vpnc_script [root]: a vpnc-script that gpclient finds (the list of
# crates/openconnect/src/vpn_utils.rs) is executable and runs the hooks of /etc/vpnc,
# where 90-gpclient-routing and 90-gpclient-notify are.
# shellcheck disable=SC2120 # the argument is optional
check_vpnc_script() {
    local root=${1:-} path script="" hooks
    for path in /usr/local/libexec/gpclient/vpnc-script /usr/libexec/gpclient/vpnc-script \
        /usr/lib/gpclient/vpnc-script /usr/local/share/vpnc-scripts/vpnc-script \
        /usr/local/sbin/vpnc-script /usr/share/vpnc-scripts/vpnc-script /usr/sbin/vpnc-script \
        /etc/vpnc/vpnc-script /etc/openconnect/vpnc-script /usr/libexec/vpnc-scripts/vpnc-script; do
        if [ -f "$root$path" ] && [ -x "$root$path" ]; then
            script=$root$path
            break
        fi
    done
    [ -n "$script" ] || fail "no executable vpnc-script in a path gpclient searches"
    hooks=$(sed -n 's/^HOOKS_DIR=//p' "$script" | head -n1)
    [ -n "$hooks" ] || fail "$script has no HOOKS_DIR"
    # Without post-connect hooks the service is never told that the tunnel is up
    grep -q 'run_hooks post-connect' "$script" \
        || fail "$script does not run post-connect hooks: the connection would never be reported as up"
    for hook in connect.d/90-gpclient-routing disconnect.d/90-gpclient-routing post-connect.d/90-gpclient-notify reconnect.d/90-gpclient-notify; do
        [ -x "$root$hooks/$hook" ] \
            || fail "$script runs the hooks of $hooks, but $hooks/$hook is not there"
    done
}

# Sourced by the unit tests: only the functions above are needed
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

rpms=${1:-}
[ -n "$rpms" ] && [ -d "$rpms" ] || { echo "usage: install-test.sh <dir with the RPMs>" >&2; exit 2; }

shopt -s nullglob
files=("$rpms"/*.rpm)
[ "${#files[@]}" -eq 3 ] || fail "expected 3 RPMs in $rpms, found ${#files[@]}"

. /etc/os-release
case "${ID:-}" in
    opensuse-* | sles)
        # The RPMs are not signed here (only the repository they are published in is)
        install_cmd=(zypper --non-interactive install --no-recommends --allow-unsigned-rpm)
        # download.opensuse.org sends each file to a mirror: one that has not synced the
        # newest metadata yet fails the refresh, the next try usually gets another one
        for attempt in 1 2 3 4 5; do
            zypper --non-interactive refresh && break
            [ "$attempt" -lt 5 ] || fail "zypper refresh failed $attempt times"
            sleep 30
        done
        ;;
    *)
        install_cmd=(dnf -y --setopt=install_weak_deps=False install)
        case "${ID:-}" in
            fedora) ;;
            *)
                # Plasma and libnma-gtk4 dependencies of EL are in EPEL and CRB
                dnf -y install dnf-plugins-core epel-release
                dnf config-manager --set-enabled crb
                ;;
        esac
        # The Cisco openh264 repository is http only and not needed
        [ "${ID:-}" != fedora ] || install_cmd+=(--disablerepo=fedora-cisco-openh264)
        ;;
esac

echo "== Installing ${files[*]##*/}"
"${install_cmd[@]}" "${files[@]}"

echo "== Checks"
for pkg in network-manager-gpclient network-manager-gpclient-gnome network-manager-gpclient-plasma; do
    rpm -q "$pkg" >/dev/null || fail "$pkg is not installed"
    # Files changed after the install: none expected (config files are not verified)
    if ! out=$(rpm -V "$pkg" 2>&1); then
        fail "rpm -V $pkg: $out"
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
    /usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service \
    /usr/share/dbus-1/system.d/nm-gpclient.conf \
    /etc/vpnc/connect.d/90-gpclient-routing \
    /etc/vpnc/disconnect.d/90-gpclient-routing \
    /etc/vpnc/post-connect.d/90-gpclient-notify \
    /etc/vpnc/reconnect.d/90-gpclient-notify \
    "$(rpm --eval '%{_libdir}')/NetworkManager/libnm-vpn-plugin-gpclient.so" \
    "$(rpm --eval '%{_libdir}')/NetworkManager/libnm-vpn-plugin-gpclient-editor.so" \
    "$(rpm --eval '%{_libdir}')/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so" \
    "$(rpm --eval '%{_libdir}')/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so" \
    "$(rpm --eval '%{_libdir}')/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.json"; do
    [ -e "$path" ] || fail "missing file $path"
done

# SELinux (#183): the installed policy gives the unit file its own type, so that
# NetworkManager may start it. Without a policy (or matchpathcon) there is nothing to check.
if [ -e /etc/selinux/config ] && command -v matchpathcon >/dev/null 2>&1; then
    context=$(matchpathcon /usr/lib/systemd/system/nm-gpclient.service) \
        || fail "matchpathcon /usr/lib/systemd/system/nm-gpclient.service"
    grep -q ':nm_gpclient_unit_file_t:' <<<"$context" \
        || fail "the unit file does not get nm_gpclient_unit_file_t: $context"
else
    echo "Note: no SELinux policy or no matchpathcon in this image: the unit file label is not checked"
fi

# The catalogs are %lang(pl): rpm skips them on images that install only some
# languages (%_install_langs, e.g. Fedora and EL containers), so check the
# package contents, not the disk
rpm -ql network-manager-gpclient | grep -qx /usr/share/locale/pl/LC_MESSAGES/NetworkManager-gpclient.mo \
    || fail "network-manager-gpclient has no Polish catalog"
rpm -ql network-manager-gpclient-plasma \
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
rpm -qf --qf '%{NAME}\n' /usr/lib/libnm-gpclient-properties | grep -qx network-manager-gpclient-gnome \
    || fail "libnm-gpclient-properties is not in network-manager-gpclient-gnome"

# Every program and plugin finds its shared libraries
for exe in /usr/bin/gpclient /usr/bin/gpauth \
    "$(rpm --eval '%{_libdir}')"/NetworkManager/libnm-*gpclient*.so \
    "$(rpm --eval '%{_libdir}')"/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so; do
    if missing=$(ldd "$exe" 2>&1 | grep 'not found'); then
        fail "$exe: missing libraries: $missing"
    fi
done

check_openssl /usr/bin/gpclient /usr/bin/gpauth
check_stripped /usr/bin/gpclient /usr/bin/gpauth
check_vpnc_script

/usr/bin/gpclient --version >/dev/null || fail "gpclient --version"
/usr/bin/gpauth --version >/dev/null || fail "gpauth --version"

# The Python programs run with the interpreter of their shebang (on openSUSE the
# versioned one, which has sdbus and PyGObject in its site-packages)
shebang=$(head -n1 /usr/lib/NetworkManager/nm-gpclient-service)
[ "$(head -n1 /usr/libexec/nm-gpclient-auth-dialog)" = "$shebang" ] \
    || fail "the service and the auth dialog have different shebangs"
case "$shebang" in
    '#!'*) read -r -a python <<<"${shebang#\#!}" ;;
    *) fail "the service has no shebang" ;;
esac
case "${ID:-}" in
    opensuse-* | sles)
        [[ "$shebang" =~ ^#!/usr/bin/python3\.[0-9]+$ ]] \
            || fail "the shebang '$shebang' is not a versioned interpreter"
        ;;
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

echo "OK: all checks passed"
