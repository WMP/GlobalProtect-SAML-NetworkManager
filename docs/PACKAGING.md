# Debian Package Structure

This project builds **3 separate Debian packages** to support different desktop environments.

## Package Overview

```
┌─────────────────────────────────────────┐
│  network-manager-gpclient (BASE)        │
│  ├── Python VPN service                 │
│  ├── gpclient, gpauth, gpservice        │
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
- `/usr/bin/gpclient`, `/usr/bin/gpauth`, `/usr/bin/gpservice` - VPN client binaries
- D-Bus and systemd configuration files

Dependencies:
- `network-manager`
- `openconnect`
- `python3`, `python3-sdbus`

### 2. network-manager-gpclient-gnome

**For GNOME, MATE, Cinnamon, XFCE, etc.**

Contents:
- `/usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient.so`
- `/usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient-editor.so` (GTK3)
- `/usr/lib/x86_64-linux-gnu/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so` (GTK4, Ubuntu 24.04+)

Supports:
- GNOME Settings (GTK4)
- nm-connection-editor (GTK3)
- Other GTK-based network managers

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `network-manager-gnome`

### 3. network-manager-gpclient-plasma

**For KDE Plasma desktop.**

Contents:
- `/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so`
- `/usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop`

Supports:
- KDE Plasma NetworkManager applet

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm`

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

### Local build:

```bash
dpkg-buildpackage -us -uc -b
```

## Installation

Download packages from [GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases) for your Ubuntu version.

Install two packages:
1. **network-manager-gpclient** - core package (required)
2. **network-manager-gpclient-gnome** - for GNOME/GTK desktops, or
   **network-manager-gpclient-plasma** - for KDE Plasma

```bash
sudo dpkg -i <downloaded-packages>.deb
sudo apt-get install -f  # install dependencies
```

## Supported Platforms

| Ubuntu Version | GTK3 | GTK4 | Plasma |
|----------------|------|------|--------|
| 22.04 LTS      | ✅   | ❌   | ✅     |
| 24.04 LTS      | ✅   | ✅   | ✅     |

Note: GTK4 editor requires `libnma-gtk4` which is not available on Ubuntu 22.04.

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
