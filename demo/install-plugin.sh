#!/bin/bash
# Builds the plugins of this repository and installs them into a root
# directory, with the paths of `override_dh_auto_install` in debian/rules.
# Both demo Dockerfiles use it, so the paths are in one place.
#
# Usage: demo/install-plugin.sh <gnome|kde> <source-dir> <dest-root>
#   gnome: libnm plugin, GTK3/GTK4 editor plugins, auth dialog, service files
#   kde:   libnm plugin, Qt6 Plasma plugin, service files
set -euo pipefail

flavor="${1:?usage: $0 <gnome|kde> <source-dir> <dest-root>}"
src="$(cd "${2:?usage: $0 <gnome|kde> <source-dir> <dest-root>}" && pwd)"
dest="${3:?usage: $0 <gnome|kde> <source-dir> <dest-root>}"
mkdir -p "$dest"
dest="$(cd "$dest" && pwd)"

multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"
nm_dir="usr/lib/$multiarch/NetworkManager"
cd "$src"

case "$flavor" in
gnome)
    make -C plugins/gnome all
    ;;
kde)
    make -C plugins/gnome libnm-vpn-plugin-gpclient.so
    mkdir -p plugins/plasma/build
    (cd plugins/plasma/build && cmake .. -DQT_MAJOR_VERSION=6 && make -j"$(nproc)")
    ;;
*)
    echo "unknown flavor: $flavor" >&2
    exit 1
    ;;
esac

# Core package (network-manager-gpclient)
install -D -m 644 plugins/gnome/libnm-vpn-plugin-gpclient.so \
    "$dest/$nm_dir/libnm-vpn-plugin-gpclient.so"
install -D -m 644 plugins/gnome/nm-gpclient-service.name \
    "$dest/usr/lib/NetworkManager/VPN/nm-gpclient-service.name"
install -D -m 755 service/nm-gpclient-service.py \
    "$dest/usr/lib/NetworkManager/nm-gpclient-service"
install -D -m 755 scripts/browser-wrapper.sh \
    "$dest/usr/libexec/gpclient/browser-wrapper"
install -D -m 755 scripts/edge-wrapper.sh \
    "$dest/usr/libexec/gpclient/edge-wrapper"
install -D -m 644 config/org.freedesktop.NetworkManager.gpclient.service \
    "$dest/usr/share/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service"
install -D -m 644 config/nm-gpclient.conf \
    "$dest/etc/dbus-1/system.d/nm-gpclient.conf"
mkdir -p "$dest/usr/libexec/NetworkManager"
ln -sf /usr/lib/NetworkManager/nm-gpclient-service \
    "$dest/usr/libexec/NetworkManager/gpclient-nm-service"
install -D -m 755 config/90-gpclient-routing \
    "$dest/etc/vpnc/connect.d/90-gpclient-routing"

if [ "$flavor" = gnome ]; then
    # GNOME package (network-manager-gpclient-gnome)
    install -D -m 644 plugins/gnome/libnm-vpn-plugin-gpclient-editor.so \
        "$dest/$nm_dir/libnm-vpn-plugin-gpclient-editor.so"
    if [ -f plugins/gnome/libnm-gtk4-vpn-plugin-gpclient-editor.so ]; then
        install -D -m 644 plugins/gnome/libnm-gtk4-vpn-plugin-gpclient-editor.so \
            "$dest/$nm_dir/libnm-gtk4-vpn-plugin-gpclient-editor.so"
    fi
    mkdir -p "$dest/usr/lib"
    ln -sf "/usr/lib/$multiarch/NetworkManager/libnm-vpn-plugin-gpclient-editor.so" \
        "$dest/usr/lib/libnm-gpclient-properties"
    install -D -m 755 auth-dialog/nm-gpclient-auth-dialog.py \
        "$dest/usr/libexec/nm-gpclient-auth-dialog"
else
    # Plasma package (Qt6: no .desktop service file)
    plugin_dir="usr/lib/$multiarch/qt6/plugins/plasma/network/vpn"
    install -D -m 644 plugins/plasma/build/plasmanetworkmanagement_gpclientui.so \
        "$dest/$plugin_dir/plasmanetworkmanagement_gpclientui.so"
    install -D -m 644 plugins/plasma/plasmanetworkmanagement_gpclientui.json \
        "$dest/$plugin_dir/plasmanetworkmanagement_gpclientui.json"
fi
echo "Installed $flavor plugins into $dest"
