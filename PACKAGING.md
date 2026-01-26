# Debian Package Structure

This project builds **3 separate Debian packages** to support different desktop environments efficiently.

## 📦 Package Overview

```
┌─────────────────────────────────────────┐
│  network-manager-gpclient (BASE)       │
│  ├── Python VPN service                │
│  ├── Configuration files               │
│  └── Helper scripts                    │
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

## 1️⃣ network-manager-gpclient

**Core package** - Required by both GUI packages.

### Contents:
- `/usr/lib/NetworkManager/nm-gpclient-service` - Python VPN service
- `/usr/lib/NetworkManager/VPN/nm-gpclient-service.name` - Service descriptor
- `/usr/bin/edge-wrapper` - Browser wrapper script

### Dependencies:
- `network-manager`
- `gpclient` (GlobalProtect client binary)
- `python3`, `python3-dbus`, `python3-gi`

### Size: ~50 KB

---

## 2️⃣ network-manager-gpclient-gnome

**GNOME/GTK GUI package** - For GNOME, MATE, Cinnamon, XFCE, etc.

### Contents:
- `/usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-vpn-plugin-gpclient.so`
- `/usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-vpn-plugin-gpclient-editor.so` (GTK3)
- `/usr/lib/x86_64-linux-gnu/NetworkManager/VPN/libnm-gtk4-vpn-plugin-gpclient-editor.so` (GTK4)
- `/usr/lib/libnm-gpclient-properties` → symlink

### Supports:
- ✅ GNOME Settings (GTK4)
- ✅ nm-connection-editor (GTK3)
- ✅ Other GTK-based network managers

### Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `network-manager-gnome`
- GTK libraries (libgtk-3, libgtk-4, libnma)

### Size: ~100 KB

---

## 3️⃣ network-manager-gpclient-plasma

**KDE Plasma GUI package** - For KDE Plasma desktop.

### Contents:
- `/usr/lib/x86_64-linux-gnu/qt5/plugins/plasmanetworkmanagement_gpclientui.so`
- `/usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop`

### Supports:
- ✅ KDE Plasma NetworkManager applet

### Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm`
- Qt5 libraries (libqt5core, libkf5networkmanagerqt)

### Size: ~80 KB

---

## 🔧 Building Packages

### Build all packages:
```bash
./build-deb.sh
```

This will create in `output/`:
```
network-manager-gpclient_1.0.0_amd64.deb
network-manager-gpclient-gnome_1.0.0_amd64.deb
network-manager-gpclient-plasma_1.0.0_amd64.deb
```

### Build GNOME-only:
```bash
dpkg-buildpackage -b -us -uc -Pgnome
```

### Build Plasma-only:
```bash
dpkg-buildpackage -b -us -uc -Pplasma
```

---

## 📥 Installation

### For GNOME users:
```bash
sudo dpkg -i network-manager-gpclient_*_amd64.deb \
              network-manager-gpclient-gnome_*_amd64.deb
sudo apt-get install -f
```

### For KDE Plasma users:
```bash
sudo dpkg -i network-manager-gpclient_*_amd64.deb \
              network-manager-gpclient-plasma_*_amd64.deb
sudo apt-get install -f
```

### Install both (for multi-DE systems):
```bash
sudo dpkg -i network-manager-gpclient*.deb
sudo apt-get install -f
```

---

## 🎯 Why Separate Packages?

### Benefits:

1. **Smaller footprint**
   - GNOME users don't need Qt5/Plasma libraries
   - Plasma users don't need GTK4 libraries
   - Saves ~100 MB of disk space per installation

2. **Cleaner dependencies**
   - No conflicting desktop environment packages
   - Automatic dependency resolution for each DE

3. **Flexibility**
   - Users choose their desktop environment
   - Easy to support multiple DEs on same system
   - Can install/remove GUI packages independently

4. **Best practices**
   - Follows Debian Policy Manual guidelines
   - Similar to other NetworkManager VPN plugins:
     - `network-manager-openvpn-gnome`
     - `plasma-nm-openvpn`

---

## 📊 Package Comparison

| Feature | Base | GNOME | Plasma |
|---------|------|-------|--------|
| VPN Service | ✅ | ➖ | ➖ |
| Config files | ✅ | ➖ | ➖ |
| Scripts | ✅ | ➖ | ➖ |
| GTK3 GUI | ➖ | ✅ | ➖ |
| GTK4 GUI | ➖ | ✅ | ➖ |
| Qt5 GUI | ➖ | ➖ | ✅ |
| Size | 50 KB | 100 KB | 80 KB |
| Required | Always | If GNOME | If Plasma |

---

## 🔍 Verification

### Check installed packages:
```bash
dpkg -l | grep network-manager-gpclient
```

### List package contents:
```bash
dpkg -L network-manager-gpclient
dpkg -L network-manager-gpclient-gnome
dpkg -L network-manager-gpclient-plasma
```

### Check dependencies:
```bash
apt-cache depends network-manager-gpclient-gnome
apt-cache depends network-manager-gpclient-plasma
```

---

## 🐛 Troubleshooting

### Wrong package installed?

**Symptom:** VPN works but no GUI in network settings

**Solution:**
```bash
# For GNOME
sudo apt install network-manager-gpclient-gnome

# For Plasma
sudo apt install network-manager-gpclient-plasma
```

### Both packages installed?

This is fine! System will use appropriate package for active desktop environment.

---

## 📝 For Package Maintainers

### debian/control structure:
```
Source: network-manager-gpclient
  - Build-Depends: (all libraries for both GNOME and Plasma)

Package: network-manager-gpclient
  - Architecture: amd64
  - Depends: core dependencies only

Package: network-manager-gpclient-gnome
  - Depends: network-manager-gpclient (= ${binary:Version})
  - Depends: network-manager-gnome

Package: network-manager-gpclient-plasma
  - Depends: network-manager-gpclient (= ${binary:Version})
  - Depends: plasma-nm
```

### Build process (debian/rules):
1. Build GNOME plugins with Make
2. Build Plasma plugin with CMake
3. Install each to separate package directories
