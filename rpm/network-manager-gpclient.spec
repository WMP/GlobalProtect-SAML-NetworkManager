# RPM packaging of the GlobalProtect NetworkManager VPN plugin. It mirrors
# debian/rules: the same three packages with the same files.
#
# Do not run rpmbuild on this file directly: rpm/build.sh passes the values of
#   project_version  upstream version, from debian/changelog
#   debian_release   Debian revision, from debian/changelog
#   dist             distribution tag (.fc44, .el10, .lp160, .lp161, .tw), from rpm/targets
#   pr_suffix        ".pr<PR>.<run>" for pull request builds, else empty
#   sdbus_version    version of the bundled python-sdbus, from rpm/sources
# and appends the %changelog that rpm/debian_changelog.py generates.

%if "%{?project_version}" == "" || "%{?debian_release}" == "" || "%{?sdbus_version}" == ""
%{error:project_version, debian_release and sdbus_version must be defined: build with rpm/build.sh}
%endif

# No debuginfo/debugsource package: the output is exactly three binary RPMs
# (and so no /usr/lib/.build-id links of the binaries either).
%global debug_package %{nil}
%global _build_id_links none
# The bundled sdbus is ours only: provide bundled(python3dist(sdbus)), not the
# python3dist(sdbus) a distribution package would provide
%global __provides_exclude ^python[0-9.]*dist\\(sdbus\\)

Name:           network-manager-gpclient
Version:        %{project_version}
Release:        %{debian_release}%{?dist}%{?pr_suffix}
Summary:        NetworkManager VPN plugin for GlobalProtect
# Per package, as debian/copyright says for the same files (checked by
# tests/unit/test_rpm_license.py): the service and the editors are
# LGPL-2.1-only; gpclient and gpauth are GPL-3.0-only (upstream Cargo.toml
# "GPL-3.0") with the LGPL-2.1-only openconnect fork linked in (some of its
# files are LGPL-2.1-or-later (lzo.c) and BSD-2-Clause (json/json.c, jsondump.c))
# and the Rust crates (the union of their SPDX IDs; the list with the texts is
# third-party-licenses.txt, from scripts/third_party_licenses.py);
# nm-vpn-plugin-utils (in libnm-vpn-plugin-gpclient.so, this package) is
# LGPL-2.0-or-later. RPM only: the bundled python-sdbus is LGPL-2.1-or-later
# (also in openconnect, so debian/copyright has it too).
License:        GPL-3.0-only AND LGPL-2.1-only AND LGPL-2.0-or-later AND LGPL-2.1-or-later AND BSD-2-Clause AND BSD-3-Clause AND MIT AND Apache-2.0 AND MPL-2.0 AND ISC AND Zlib AND Unicode-3.0 AND Unlicense AND CC0-1.0 AND BSL-1.0 AND MIT-0 AND 0BSD
URL:            https://github.com/WMP/GlobalProtect-SAML-NetworkManager
Source0:        %{name}-%{version}.tar.gz
Source1:        sdbus-%{sdbus_version}.tar.gz

BuildRequires:  autoconf
BuildRequires:  automake
BuildRequires:  cmake
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  gettext
BuildRequires:  libtool
BuildRequires:  make
BuildRequires:  patch
BuildRequires:  pkgconfig(glib-2.0)
BuildRequires:  pkgconfig(gtk+-3.0)
BuildRequires:  pkgconfig(gtk4)
BuildRequires:  pkgconfig(javascriptcoregtk-4.1)
BuildRequires:  pkgconfig(libnm)
BuildRequires:  pkgconfig(libnma)
BuildRequires:  pkgconfig(libnma-gtk4)
BuildRequires:  pkgconfig(libsoup-3.0)
BuildRequires:  pkgconfig(libsystemd)
BuildRequires:  pkgconfig(libxml-2.0)
BuildRequires:  pkgconfig(webkit2gtk-4.1)
BuildRequires:  cmake(Qt6Widgets)
# The Plasma widget test (plugins/plasma/tests, BUILD_TESTING) needs QtTest at configure time
BuildRequires:  cmake(Qt6Test)
BuildRequires:  cmake(KF6I18n)
BuildRequires:  cmake(KF6NetworkManagerQt)
BuildRequires:  cmake(KF6Service)
BuildRequires:  cmake(KF6WidgetsAddons)
BuildRequires:  cargo
BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  systemd-rpm-macros
%if 0%{?fedora} || 0%{?rhel}
BuildRequires:  selinux-policy-devel
%endif
%if 0%{?suse_version}
# pkgconfig(openssl) resolves to libressl-devel on openSUSE; gpclient needs OpenSSL (3 or later)
BuildRequires:  libopenssl-devel
BuildConflicts: libressl-devel
BuildRequires:  kf6-extra-cmake-modules
BuildRequires:  plasma6-nm
BuildRequires:  openconnect-devel
BuildRequires:  python-rpm-macros
%else
BuildRequires:  pkgconfig(openssl)
BuildRequires:  extra-cmake-modules
BuildRequires:  plasma-nm
BuildRequires:  openconnect-devel
%endif

Requires:       NetworkManager
Requires:       openconnect
Requires:       python3
Requires:       xdg-utils
# openconnect needs a vpnc-script, which also runs /etc/vpnc/connect.d/90-gpclient-routing
# and the 90-gpclient-notify hooks (HOOKS_DIR=/etc/vpnc); openSUSE installs none with
# openconnect
%if 0%{?suse_version}
Requires:       vpnc
%else
Requires:       vpnc-script
%endif
Conflicts:      globalprotect-openconnect
# python-sdbus: no target ships it as a package (Debian/Ubuntu: python3-sdbus)
Provides:       bundled(python3dist(sdbus)) = %{sdbus_version}
%if 0%{?fedora} || 0%{?rhel}
Requires:       selinux-policy-targeted
%endif

%description
NetworkManager VPN service and command-line tools for GlobalProtect VPN
connections with SAML/SSO authentication support. The package contains the
service, the NetworkManager plugin, gpclient and gpauth.

The GUI packages add the connection editors.

%package gnome
Summary:        GNOME editor plugins for the GlobalProtect NetworkManager VPN
License:        LGPL-2.1-only
Requires:       %{name} = %{version}-%{release}
%if 0%{?suse_version}
Requires:       python3-gobject-Gdk
Requires:       typelib(Gtk) = 3.0
Requires:       libnma-gtk4-0
%else
Requires:       python3-gobject
Requires:       gtk3
Requires:       libnma
Requires:       libnma-gtk4
%endif

%description gnome
GTK3 and GTK4 connection editor plugins and the authentication dialog for
GlobalProtect VPN connections in GNOME, MATE, Cinnamon, XFCE and other
GTK-based desktops.

%package plasma
Summary:        KDE Plasma 6 editor plugin for the GlobalProtect NetworkManager VPN
License:        LGPL-2.1-only
Requires:       %{name} = %{version}-%{release}
%if 0%{?suse_version}
Requires:       plasma6-nm
%else
Requires:       plasma-nm
%endif

%description plasma
KDE Plasma 6 NetworkManager connection editor plugin for GlobalProtect VPN
connections.

%prep
%setup -q -n %{name}-%{version} -a 1
# gpclient and gpauth are GPL-3.0-only: ship upstream's text (license files go
# by basename, and LICENSE is this project's LGPL-2.1)
cp -p external/GlobalProtect-openconnect/LICENSE LICENSE.GPL-3.0

%build
%if 0%{?fedora} || 0%{?rhel}
make -f /usr/share/selinux/devel/Makefile network-manager-gpclient.pp
%endif
# The openconnect crate builds the bundled openconnect and libxml2 (build.rs)
test -d external/GlobalProtect-openconnect/crates/openconnect/deps/openconnect/.
test -d external/GlobalProtect-openconnect/crates/openconnect/deps/libxml2/.

# NM_PLUGIN_DIR and MULTIARCH are used by "make install" only; this spec installs by hand
make -C plugins/gnome all

mkdir -p plugins/plasma/build
(cd plugins/plasma/build && cmake .. -DQT_MAJOR_VERSION=6 -DCMAKE_BUILD_TYPE=Release && %make_build)

# python-sdbus: built here from the source archive, offline
/usr/bin/python3 -m pip wheel --no-deps --no-build-isolation --no-index \
  --wheel-dir sdbus-wheel ./sdbus-%{sdbus_version}

# The longest step comes last. cargo-auditable (rpm/container-build.sh) embeds the
# dependency list in the binaries; the build fails when it is missing
# With upstream's release profile (strip, opt-level "z"), as the Debian build: Fedora's
# rpmbuild exports RUSTFLAGS with -Cstrip=none -Cdebuginfo=2, which would override it
# With vendor/config.toml (scripts/vendor-crates.sh, in the source archive) the crates come
# from vendor/ and cargo does not use the network; without it cargo downloads them
vendor_flags=
if [ -f "$PWD/vendor/config.toml" ]; then
  vendor_flags="--offline --config $PWD/vendor/config.toml"
fi
(cd external/GlobalProtect-openconnect && \
  env -u RUSTFLAGS cargo auditable build --locked $vendor_flags --release --bin gpclient --bin gpauth)

# The licenses of the Rust crates linked into gpclient and gpauth (offline; the build
# stops when a crate has no license)
/usr/bin/python3 scripts/third_party_licenses.py -o third-party-licenses.txt

%install
# The translations (po/LINGUAS): each domain goes to its own package
make install-mo MO_DOMAIN=NetworkManager-gpclient DESTDIR=%{buildroot}
make install-mo MO_DOMAIN=plasmanetworkmanagement_gpclientui DESTDIR=%{buildroot}
%find_lang NetworkManager-gpclient
%find_lang plasmanetworkmanagement_gpclientui
%if 0%{?fedora} || 0%{?rhel}
install -D -m 0644 network-manager-gpclient.pp \
  %{buildroot}%{_datadir}/selinux/packages/network-manager-gpclient.pp
%endif
install -D -m 0644 plugins/gnome/libnm-vpn-plugin-gpclient.so \
  %{buildroot}%{_libdir}/NetworkManager/libnm-vpn-plugin-gpclient.so
install -D -m 0644 plugins/gnome/libnm-vpn-plugin-gpclient-editor.so \
  %{buildroot}%{_libdir}/NetworkManager/libnm-vpn-plugin-gpclient-editor.so
install -D -m 0644 plugins/gnome/libnm-gtk4-vpn-plugin-gpclient-editor.so \
  %{buildroot}%{_libdir}/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so
mkdir -p %{buildroot}%{_prefix}/lib
ln -s %{_libdir}/NetworkManager/libnm-vpn-plugin-gpclient-editor.so \
  %{buildroot}%{_prefix}/lib/libnm-gpclient-properties
install -D -m 0755 auth-dialog/nm-gpclient-auth-dialog.py \
  %{buildroot}%{_prefix}/libexec/nm-gpclient-auth-dialog

install -D -m 0644 plugins/plasma/build/plasmanetworkmanagement_gpclientui.so \
  %{buildroot}%{_libdir}/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so
install -D -m 0644 plugins/plasma/plasmanetworkmanagement_gpclientui.json \
  %{buildroot}%{_libdir}/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.json

install -D -m 0644 plugins/gnome/nm-gpclient-service.name \
  %{buildroot}%{_prefix}/lib/NetworkManager/VPN/nm-gpclient-service.name
install -D -m 0755 service/nm-gpclient-service.py \
  %{buildroot}%{_prefix}/lib/NetworkManager/nm-gpclient-service
install -D -m 0755 scripts/browser-wrapper.sh \
  %{buildroot}%{_prefix}/libexec/gpclient/browser-wrapper
install -D -m 0755 scripts/edge-wrapper.sh \
  %{buildroot}%{_prefix}/libexec/gpclient/edge-wrapper
# Compatibility symlink for the old path
mkdir -p %{buildroot}%{_prefix}/libexec/NetworkManager
ln -s %{_prefix}/lib/NetworkManager/nm-gpclient-service \
  %{buildroot}%{_prefix}/libexec/NetworkManager/gpclient-nm-service

install -D -m 0644 config/nm-gpclient.service \
  %{buildroot}%{_unitdir}/nm-gpclient.service
install -D -m 0644 config/org.freedesktop.NetworkManager.gpclient.service \
  %{buildroot}%{_datadir}/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service
install -D -m 0644 config/nm-gpclient.conf \
  %{buildroot}%{_datadir}/dbus-1/system.d/nm-gpclient.conf
install -D -m 0755 config/90-gpclient-routing \
  %{buildroot}%{_sysconfdir}/vpnc/connect.d/90-gpclient-routing
# The same hook for disconnect: vpnc-script must see the edited route lists there too
install -D -m 0755 config/90-gpclient-routing \
  %{buildroot}%{_sysconfdir}/vpnc/disconnect.d/90-gpclient-routing
# The hook that tells the service the tunnel is up: after vpnc-script configured
# it, and again on a reconnect
install -D -m 0755 config/gpclient-nm-notify \
  %{buildroot}%{_sysconfdir}/vpnc/post-connect.d/90-gpclient-notify
install -D -m 0755 config/gpclient-nm-notify \
  %{buildroot}%{_sysconfdir}/vpnc/reconnect.d/90-gpclient-notify
# What gpclient runs with --script when a profile has nm-routing=yes
install -D -m 0755 config/gpclient-nm-script \
  %{buildroot}%{_prefix}/lib/NetworkManager/gpclient-nm-script

%if 0%{?suse_version}
# sdbus and PyGObject are in the site-packages of this python3.X: do not follow a
# python3 that moves to a newer version
sed -i '1s|^#!/usr/bin/env python3$|#!/usr/bin/python%{python3_version}|' \
  %{buildroot}%{_prefix}/lib/NetworkManager/nm-gpclient-service \
  %{buildroot}%{_prefix}/libexec/nm-gpclient-auth-dialog
head -n1 %{buildroot}%{_prefix}/lib/NetworkManager/nm-gpclient-service | grep -qx '#!/usr/bin/python%{python3_version}'
head -n1 %{buildroot}%{_prefix}/libexec/nm-gpclient-auth-dialog | grep -qx '#!/usr/bin/python%{python3_version}'
%endif

install -D -m 0755 external/GlobalProtect-openconnect/target/release/gpclient \
  %{buildroot}%{_bindir}/gpclient
install -D -m 0755 external/GlobalProtect-openconnect/target/release/gpauth \
  %{buildroot}%{_bindir}/gpauth
# URL handler for globalprotectcallback://
install -D -m 0644 external/GlobalProtect-openconnect/packaging/files/usr/share/applications/gpgui.desktop \
  %{buildroot}%{_datadir}/applications/gpgui.desktop
# gpgui is not shipped: keep the MIME handler, hide the menu entry
sh scripts/hide-desktop-entry.sh %{buildroot}%{_datadir}/applications/gpgui.desktop

/usr/bin/python3 -m pip install --no-deps --no-index --no-user --ignore-installed \
  --root %{buildroot} --prefix %{_prefix} sdbus-wheel/sdbus-%{sdbus_version}-*.whl
# pip leaves files that name the build directory
rm -f %{buildroot}%{python3_sitearch}/sdbus-%{sdbus_version}.dist-info/{direct_url.json,INSTALLER,REQUESTED}

%check
test -x %{buildroot}%{_bindir}/gpclient
test -f %{buildroot}%{_prefix}/lib/NetworkManager/VPN/nm-gpclient-service.name
test -d %{buildroot}%{python3_sitearch}/sdbus
PYTHONPATH=%{buildroot}%{python3_sitearch} /usr/bin/python3 -c 'import sdbus'
# The Plasma widget tests, as debian/rules runs them
(cd plugins/plasma/build && ctest --output-on-failure --no-tests=error)

%post
%if 0%{?fedora} || 0%{?rhel}
%selinux_modules_install %{_datadir}/selinux/packages/network-manager-gpclient.pp
%endif
%systemd_post nm-gpclient.service
# Reload the D-Bus configuration to pick up the new service files
if [ -x /usr/bin/dbus-send ]; then
    dbus-send --system --type=method_call --dest=org.freedesktop.DBus \
        /org/freedesktop/DBus org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || :
fi
# Stop a running (old) service, so that NetworkManager starts the new one.
# /etc/nsswitch.conf is not changed: Fedora and EL manage it with authselect.
# (the service runs as "python3[.X] /usr/lib/NetworkManager/nm-gpclient-service"; its
# process name is cut to 15 characters, so match the whole command line)
if [ -x /usr/bin/pkill ]; then
    pkill -f '^(/usr/bin/)?python3[.0-9]* /usr/lib/NetworkManager/nm-gpclient-service' >/dev/null 2>&1 || :
    pkill -f '^(/usr/bin/)?python3[.0-9]* /usr/libexec/NetworkManager/gpclient-nm-service' >/dev/null 2>&1 || :
fi

%preun
%systemd_preun nm-gpclient.service

%postun
%systemd_postun nm-gpclient.service
%if 0%{?fedora} || 0%{?rhel}
if [ "$1" -eq 0 ]; then
    %selinux_modules_uninstall network-manager-gpclient
fi
%endif
if [ "$1" -eq 0 ]; then
    if [ -x /usr/bin/dbus-send ]; then
        dbus-send --system --type=method_call --dest=org.freedesktop.DBus \
            /org/freedesktop/DBus org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || :
    fi
fi

%files -f NetworkManager-gpclient.lang
%license LICENSE
%license LICENSE.GPL-3.0
%license third-party-licenses.txt
%license sdbus-%{sdbus_version}/COPYING.LESSER
%doc README.md
%{_bindir}/gpclient
%{_bindir}/gpauth
%{_libdir}/NetworkManager/libnm-vpn-plugin-gpclient.so
%{_prefix}/lib/NetworkManager/nm-gpclient-service
%{_prefix}/lib/NetworkManager/gpclient-nm-script
%{_prefix}/lib/NetworkManager/VPN/nm-gpclient-service.name
%{_prefix}/libexec/gpclient
%dir %{_prefix}/libexec/NetworkManager
%{_prefix}/libexec/NetworkManager/gpclient-nm-service
%{_unitdir}/nm-gpclient.service
%{_datadir}/dbus-1/system-services/org.freedesktop.NetworkManager.gpclient.service
%{_datadir}/applications/gpgui.desktop
%if 0%{?fedora} || 0%{?rhel}
%{_datadir}/selinux/packages/network-manager-gpclient.pp
%endif
%{_datadir}/dbus-1/system.d/nm-gpclient.conf
%dir %{_sysconfdir}/vpnc
%dir %{_sysconfdir}/vpnc/connect.d
%config(noreplace) %{_sysconfdir}/vpnc/connect.d/90-gpclient-routing
%dir %{_sysconfdir}/vpnc/disconnect.d
%config(noreplace) %{_sysconfdir}/vpnc/disconnect.d/90-gpclient-routing
%dir %{_sysconfdir}/vpnc/post-connect.d
%config(noreplace) %{_sysconfdir}/vpnc/post-connect.d/90-gpclient-notify
%dir %{_sysconfdir}/vpnc/reconnect.d
%config(noreplace) %{_sysconfdir}/vpnc/reconnect.d/90-gpclient-notify
%{python3_sitearch}/sdbus*

%files gnome
%{_libdir}/NetworkManager/libnm-vpn-plugin-gpclient-editor.so
%{_libdir}/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so
%{_prefix}/lib/libnm-gpclient-properties
%{_prefix}/libexec/nm-gpclient-auth-dialog

%files plasma -f plasmanetworkmanagement_gpclientui.lang
%{_libdir}/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so
%{_libdir}/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.json

# rpm/build.sh appends the entries generated from debian/changelog
%changelog
