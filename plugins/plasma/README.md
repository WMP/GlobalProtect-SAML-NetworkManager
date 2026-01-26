# GlobalProtect VPN Plugin for KDE Plasma 5

Qt5-based VPN configuration plugin for KDE Plasma's Network Manager GUI.

## Features

- Native Qt5/KF5 integration with Plasma Network Manager
- Same configuration options as GTK versions:
  - Gateway (VPN server)
  - Browser path (for 2FA/SAML)
  - DNS servers (optional)
- Seamless integration with KDE System Settings

## Requirements

### Build Dependencies

**Debian/Ubuntu:**
```bash
sudo apt install \
    cmake \
    extra-cmake-modules \
    qtbase5-dev \
    libkf5i18n-dev \
    libkf5service-dev \
    libkf5widgetsaddons-dev \
    libkf5networkmanagerqt-dev \
    plasma-nm-dev
```

**Fedora/RHEL:**
```bash
sudo dnf install \
    cmake \
    extra-cmake-modules \
    qt5-qtbase-devel \
    kf5-ki18n-devel \
    kf5-kservice-devel \
    kf5-kwidgetsaddons-devel \
    kf5-networkmanager-qt-devel \
    plasma-nm-devel
```

**Arch Linux:**
```bash
sudo pacman -S \
    cmake \
    extra-cmake-modules \
    qt5-base \
    ki18n5 \
    kservice5 \
    kwidgetsaddons5 \
    networkmanager-qt5 \
    plasma-nm
```

**openSUSE:**
```bash
sudo zypper install \
    cmake \
    extra-cmake-modules \
    libqt5-qtbase-devel \
    ki18n-devel \
    kservice-devel \
    kwidgetsaddons-devel \
    networkmanager-qt-devel \
    plasma5-nm-devel
```

### Runtime Dependencies

- KDE Plasma 5
- NetworkManager
- plasma-nm (Plasma Network Manager applet)

## Building and Installation

```bash
# From the plasma directory
./build.sh

# Install
cd build
sudo make install

# Restart Plasma Shell to load the plugin
killall plasmashell && plasmashell &
```

## Manual Build

If the build script doesn't work, you can build manually:

```bash
mkdir build
cd build

cmake .. \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DKDE_INSTALL_LIBDIR=lib/x86_64-linux-gnu \
    -DCMAKE_BUILD_TYPE=Release

make -j$(nproc)
sudo make install
```

## Configuration

### Using System Settings GUI

1. Open **System Settings** → **Network** → **Connections**
2. Click **+** to add a new connection
3. Select **VPN** → **GlobalProtect**
4. Fill in the configuration:
   - **Gateway**: Your VPN server (e.g., `vpn.company.com`)
   - **Browser**: Browser path for 2FA (e.g., `/usr/bin/firefox`)
   - **DNS Servers**: Optional DNS servers (e.g., `8.8.8.8;8.8.4.4`)
5. Click **OK** to save

### Using nmcli

See the main README for nmcli configuration examples.

## Troubleshooting

### Plugin not appearing in System Settings

1. Check if the plugin is installed:
   ```bash
   ls -l /usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so
   ```

2. Check if the service file is installed:
   ```bash
   ls -l /usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop
   ```

3. Verify plugin metadata:
   ```bash
   qdbus org.kde.plasmanetworkmanagement
   ```

4. Check plasma-nm logs:
   ```bash
   journalctl --user -u plasma-plasmashell -f
   ```

### Build fails with "plasma-nm libraries not found"

The build requires plasma-nm internal libraries. If your distribution doesn't provide `plasma-nm-dev`:

1. Download plasma-nm source:
   ```bash
   git clone https://invent.kde.org/plasma/plasma-nm.git
   cd plasma-nm
   ```

2. Build and install plasma-nm:
   ```bash
   mkdir build && cd build
   cmake .. -DCMAKE_INSTALL_PREFIX=/usr
   make -j$(nproc)
   sudo make install
   ```

3. Then try building this plugin again

### Changes not appearing after rebuild

Make sure to restart plasmashell:
```bash
killall plasmashell && plasmashell &
```

Or log out and log back in to KDE.

## Architecture

### Files

- `gpclientui.h/cpp` - Main plugin class implementing `VpnUiPlugin`
- `gpclientwidget.h/cpp` - Configuration widget implementing `SettingWidget`
- `gpclientwidget.ui` - Qt Designer UI definition
- `plasmanetworkmanagement_gpclientui.json` - KPlugin metadata
- `plasmanetworkmanagement_gpclientui.desktop` - Service definition
- `CMakeLists.txt` - Build configuration
- `build.sh` - Convenience build script

### Integration Points

The plugin implements the Plasma Network Manager VPN plugin interface:

```cpp
class GpclientUiPlugin : public VpnUiPlugin
{
    SettingWidget *widget(const NetworkManager::VpnSetting::Ptr &setting, ...);
    SettingWidget *askUser(const NetworkManager::VpnSetting::Ptr &setting, ...);
    QString suggestedFileName(...) const;
};
```

The widget saves configuration as NetworkManager VPN data items:
- `gateway` - VPN server address
- `browser` - Browser executable path
- `dns` - Semicolon-separated DNS servers

## Contributing

This plugin follows the same architecture as other plasma-nm VPN plugins (OpenVPN, OpenConnect, etc.). For examples, see:
- [plasma-nm on KDE Invent](https://invent.kde.org/plasma/plasma-nm)
- [OpenConnect plugin](https://invent.kde.org/plasma/plasma-nm/-/tree/master/vpn/openconnect)

## License

LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL

## References

- [KDE Plasma NetworkManager VPN Plugin Documentation](https://github.com/KDE/plasma-nm)
- [NetworkManager VPN API](https://developer.gnome.org/NetworkManager/stable/)
- [Qt5 Documentation](https://doc.qt.io/qt-5/)
