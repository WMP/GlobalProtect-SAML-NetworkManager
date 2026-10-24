# Debian Package Structure

This project builds **3 separate Debian packages** to support different desktop environments.

## Package Overview

```
┌─────────────────────────────────────────┐
│  network-manager-gpclient (BASE)        │
│  ├── Python VPN service                 │
│  ├── gpclient, gpauth                   │
│  ├── Configuration files                │
│  └── Helper scripts (edge-wrapper)      │
└─────────────────────────────────────────┘
                  ▲
                  │ depends on
    ┌─────────────┴──────────────┐
    │                            │
┌───┴────────────────┐  ┌────────┴─────────────┐
│ ...-gnome          │  │ ...-plasma           │
│ ├── GTK4 plugin    │  │ └── Qt5 plugin       │
│ ├── GTK3 plugin    │  │                      │
│ └── Simple plugin  │  │                      │
└────────────────────┘  └──────────────────────┘
```

## Packages

### 1. network-manager-gpclient (core)

**Required by both GUI packages.**

Contents:
- `/usr/lib/NetworkManager/nm-gpclient-service` - Python VPN service
- `/usr/lib/NetworkManager/VPN/nm-gpclient-service.name` - Service descriptor
- `/usr/libexec/gpclient/edge-wrapper` - Browser wrapper script
- `/usr/bin/gpclient`, `/usr/bin/gpauth` - VPN client binaries
- D-Bus and systemd configuration files

Dependencies:
- `network-manager`
- `openconnect`
- `python3`, `python3-sdbus`

### 2. network-manager-gpclient-gnome

**For GNOME, MATE, Cinnamon, XFCE, etc.**

Contents:
- `/usr/lib/<multiarch>/NetworkManager/libnm-vpn-plugin-gpclient.so`
- `/usr/lib/<multiarch>/NetworkManager/libnm-vpn-plugin-gpclient-editor.so` (GTK3)
- `/usr/lib/<multiarch>/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so` (GTK4, Ubuntu 24.04+)

Supports:
- GNOME Settings (GTK4)
- nm-connection-editor (GTK3)
- Other GTK-based network managers

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `network-manager-gnome`

### 3. network-manager-gpclient-plasma

**For KDE Plasma desktop.**

Contents (Qt5 on Ubuntu 22.04 and 24.04, Qt6 on 24.10 and 26.04):
- `/usr/lib/<multiarch>/qt5/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so`
  (`qt6/` on 24.10 and 26.04)
- `/usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop` (Qt5 only)

Supports:
- KDE Plasma NetworkManager applet

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm` (on Ubuntu 22.04 and 24.04 `plasma-nm (<< 4:6)`, on 24.10 and 26.04
  `plasma-nm (>= 4:6)`: the plugin fits the Plasma generation of its Qt)

### 4. network-manager-gpclient-plasma-6

**For KDE neon: Ubuntu 24.04 with Plasma 6.** `amd64` only.

Ubuntu 24.04 ships Plasma 5, KDE neon ships Plasma 6 / KF6 from
`https://archive.neon.kde.org/user`, so `network-manager-gpclient-plasma` (Qt5) does not
work there. This package has the same plugin built for Qt6 / KF6 against the neon
repository, in `/usr/lib/<multiarch>/qt6/plugins/plasma/network/vpn/`. It is built by
`debian/control.ubuntu24.04-neon` with `Dockerfile.ubuntu24.04-neon`: that control file
has this package only, and `debian/rules` then builds the Plasma plugin and nothing else.
Version `<version>~noble1` like the other 24.04 packages; it goes to the `noble` suite.

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm (>= 4:6)`

Conflicts with `network-manager-gpclient-plasma`: only one Plasma generation can use the
plugin (Qt5 or Qt6), so only one of the two packages makes sense on a system. They share no
file (the Qt5 plugin is in the `qt5` directory, this one in `qt6`). The Qt5 package depends
on `plasma-nm (<< 4:6)`, so apt refuses it on Plasma 6.

## Building Packages

### Recommended method (Docker-based):

```bash
# Build for all Ubuntu versions
./build-all.sh

# Build for specific version
./build-all.sh 24.04
./build-all.sh 22.04
```

Packages will be in:
- `output/ubuntu24.04/*.deb`
- `output/ubuntu22.04/*.deb`

RPM packages (Fedora, EL 10, openSUSE; the same three packages, Plasma 6 only)
are built with `rpm/build.sh <target> <amd64|arm64>`: see [RPM_REPO.md](RPM_REPO.md).

### Local build:

```bash
dpkg-buildpackage -us -uc -b
```

## Installation

Download packages from [GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases) for your Ubuntu version.

Install two packages:
1. **network-manager-gpclient** - core package (required)
2. **network-manager-gpclient-gnome** - for GNOME/GTK desktops, or
   **network-manager-gpclient-plasma** - for KDE Plasma, or
   **network-manager-gpclient-plasma-6** - for KDE neon (Ubuntu 24.04 with Plasma 6)

```bash
sudo dpkg -i <downloaded-packages>.deb
sudo apt-get install -f  # install dependencies
```

## Supported Platforms

| Ubuntu Version | GTK3 | GTK4 | Plasma  |
|----------------|------|------|---------|
| 22.04 LTS      | ✅   | ❌   | ✅ (Qt5) |
| 24.04 LTS      | ✅   | ✅   | ✅ (Qt5; Qt6 on KDE neon, `amd64`) |
| 24.10          | ✅   | ✅   | ✅ (Qt6) |
| 26.04 LTS      | ✅   | ✅   | ✅ (Qt6) |

Notes:
- Packages are built for `amd64` and `arm64`; `<multiarch>` in the paths above is
  `x86_64-linux-gnu` or `aarch64-linux-gnu` respectively.
- GTK4 editor requires `libnma-gtk4` which is not available on Ubuntu 22.04.
- The Plasma plugin is built against Qt5/KF5 on 22.04/24.04 and Qt6/KF6 on 24.10/26.04, the one the
  `Build-Depends` of `debian/control.ubuntu<version>` list (`debian/rules` reads it);
  install paths differ (`/usr/lib/<multiarch>/qt5/plugins/...` vs `qt6/`).

## Why Separate Packages?

1. **Smaller footprint** - GNOME users don't need Qt5 libraries, Plasma users don't need GTK4
2. **Cleaner dependencies** - No conflicting desktop environment packages
3. **Flexibility** - Install/remove GUI packages independently
4. **Best practices** - Follows pattern of other NetworkManager VPN plugins

## Verification

```bash
# Check installed packages
dpkg -l | grep network-manager-gpclient

# List package contents
dpkg -L network-manager-gpclient
dpkg -L network-manager-gpclient-gnome
dpkg -L network-manager-gpclient-plasma
```
