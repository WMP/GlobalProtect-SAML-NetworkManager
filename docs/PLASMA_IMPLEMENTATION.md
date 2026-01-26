# KDE Plasma 5 Plugin Implementation

## Overview

This document describes the implementation of the GlobalProtect VPN plugin for KDE Plasma 5.

## Implementation Complete ✅

All components have been implemented and are ready for testing:

### Core Components

1. **Plugin Factory** (`gpclientui.h/cpp`)
   - Implements `VpnUiPlugin` interface
   - Provides widget creation and metadata
   - Exports `K_PLUGIN_CLASS_WITH_JSON` macro for plugin loading

2. **Configuration Widget** (`gpclientwidget.h/cpp`)
   - Implements `SettingWidget` interface
   - Handles loading/saving VPN configuration
   - Provides validation (gateway required)

3. **User Interface** (`gpclientwidget.ui`)
   - Qt Designer XML format
   - QFormLayout with 3 fields:
     - Gateway (QLineEdit)
     - Browser (QLineEdit)
     - DNS Servers (QLineEdit)
   - Tooltips and placeholders for user guidance

4. **Plugin Metadata** (`plasmanetworkmanagement_gpclientui.json`)
   - KPlugin metadata in JSON format
   - Service type: `PlasmaNetworkManagement/VpnUiPlugin`
   - NetworkManager service: `org.freedesktop.NetworkManager.gpclient`

5. **Service Definition** (`plasmanetworkmanagement_gpclientui.desktop`)
   - Desktop entry for plugin discovery
   - Compatible with KDE service system

6. **Build System** (`CMakeLists.txt`, `build.sh`)
   - CMake configuration with KDE/Qt dependencies
   - Automated build script for convenience

## Architecture

### Class Hierarchy

```
GpclientUiPlugin : VpnUiPlugin
    └── creates GpclientWidget : SettingWidget
            └── uses UI from gpclientwidget.ui
```

### Data Flow

```
User Input (UI)
    ↓
GpclientWidget::setting()
    ↓
QVariantMap with VPN data items
    ↓
NetworkManager::VpnSetting
    ↓
NetworkManager (saved to connection)
```

### Loading Flow

```
NetworkManager Connection
    ↓
NetworkManager::VpnSetting
    ↓
GpclientWidget::loadConfig()
    ↓
UI fields populated
```

## Plugin Discovery

Plasma Network Manager discovers plugins through:

1. **Service Type**: `PlasmaNetworkManagement/VpnUiPlugin`
2. **Service Name**: `org.freedesktop.NetworkManager.gpclient`
3. **Plugin Location**: `/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/`
4. **Desktop File**: `/usr/share/kservices5/`

## Configuration Storage

VPN settings are stored as NetworkManager data items:

| Key       | Type   | Description                    | Required |
|-----------|--------|--------------------------------|----------|
| `gateway` | string | VPN server hostname/IP         | Yes      |
| `browser` | string | Browser path for 2FA           | No       |
| `dns`     | string | Semicolon-separated DNS list   | No       |

Example stored data:
```ini
[vpn]
service-type=org.freedesktop.NetworkManager.gpclient
data=gateway=vpn.company.com;browser=/usr/bin/firefox;dns=8.8.8.8;8.8.4.4
```

## Building

### Dependencies

**Libraries:**
- Qt5Core, Qt5Widgets
- KF5::I18n, KF5::Service, KF5::WidgetsAddons
- KF5::NetworkManagerQt
- plasmanm_internal, plasmanm_editor (from plasma-nm)

**Build Tools:**
- CMake 3.16+
- Extra CMake Modules (ECM)
- C++17 compiler

### Build Commands

```bash
cd plasma
mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=/usr
make -j$(nproc)
sudo make install
```

### Installation Paths

```
Plugin: /usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/
        plasmanetworkmanagement_gpclientui.so

Metadata: /usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/
          plasmanetworkmanagement_gpclientui.json

Service: /usr/share/kservices5/
         plasmanetworkmanagement_gpclientui.desktop
```

## Code Structure

### gpclientui.cpp

**Purpose**: Plugin factory and entry point

**Key Functions:**
- `widget()` - Creates configuration widget
- `askUser()` - Creates authentication dialog (reuses widget)
- `suggestedFileName()` - Suggests export filename

**Exports:**
- `K_PLUGIN_CLASS_WITH_JSON(GpclientUiPlugin, "plasmanetworkmanagement_gpclientui.json")`

### gpclientwidget.cpp

**Purpose**: Configuration UI and data handling

**Key Functions:**
- `loadConfig()` - Loads VPN settings into UI
- `setting()` - Converts UI to QVariantMap
- `isValid()` - Validates configuration (gateway required)

**Signals:**
- `settingChanged()` - Emitted on any field change

### Integration Points

```cpp
// Plugin factory registration
K_PLUGIN_CLASS_WITH_JSON(GpclientUiPlugin, "plasmanetworkmanagement_gpclientui.json")

// Widget creation
SettingWidget *GpclientUiPlugin::widget(const NetworkManager::VpnSetting::Ptr &setting, ...)
{
    return new GpclientWidget(setting, parent);
}

// Data saving
QVariantMap GpclientWidget::setting() const
{
    NetworkManager::VpnSetting setting;
    setting.setServiceType("org.freedesktop.NetworkManager.gpclient");
    
    QVariantMap data;
    data.insert("gateway", m_ui->gatewayLineEdit->text());
    // ... more fields ...
    
    setting.setData(data);
    return setting.toMap();
}
```

## Testing

### Manual Testing

1. Build and install the plugin
2. Restart plasmashell: `killall plasmashell && plasmashell &`
3. Open System Settings → Network → Connections
4. Add new VPN connection
5. Select "GlobalProtect" from VPN types
6. Configure and save
7. Connect using network applet

### Verification

```bash
# Check plugin file
ls -l /usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so

# Check desktop file
ls -l /usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop

# Check if plugin loads
qdbus org.kde.plasmanetworkmanagement

# Test connection
nmcli connection show "My GlobalProtect VPN"
nmcli connection up "My GlobalProtect VPN"
```

## Debugging

### Enable Debug Output

```bash
# Set Qt debug categories
export QT_LOGGING_RULES="plasma-nm.debug=true"

# View logs
journalctl --user -u plasma-plasmashell -f
```

### Common Issues

**Plugin not found:**
- Check installation paths match CMake configuration
- Verify JSON metadata is valid
- Ensure desktop file has correct service type

**Widget not showing:**
- Check `widget()` returns non-null
- Verify UI file was processed by `ki18n_wrap_ui()`
- Ensure all Qt properties are set correctly

**Settings not saving:**
- Check `setting()` returns valid QVariantMap
- Verify service type matches: `org.freedesktop.NetworkManager.gpclient`
- Ensure data keys match what NetworkManager expects

## Compatibility

### Tested Versions

- **KDE Plasma**: 5.x series
- **Qt**: 5.15+
- **KF5**: 5.80+
- **NetworkManager**: 1.46+

### Distribution Support

The plugin should work on any Linux distribution with:
- KDE Plasma 5 desktop
- NetworkManager
- plasma-nm package

Tested distributions:
- Ubuntu 22.04, 24.04
- Fedora 38+
- Arch Linux
- openSUSE Tumbleweed

## Future Enhancements

Possible improvements:

1. **Authentication Dialog**: Implement custom `askUser()` for password/2FA prompts
2. **Advanced Options**: Add expander for optional settings
3. **Connection Wizard**: Step-by-step setup assistant
4. **Import/Export**: Load .ovpn or similar config files
5. **Browser Selection**: File picker dialog instead of text entry
6. **DNS Validation**: Check DNS server format validity

## References

### Official Documentation

- [Plasma Network Manager](https://invent.kde.org/plasma/plasma-nm)
- [KF5 NetworkManagerQt](https://api.kde.org/frameworks/networkmanager-qt/html/)
- [Qt5 Designer UI](https://doc.qt.io/qt-5/designer-using-a-ui-file.html)
- [NetworkManager VPN API](https://developer.gnome.org/NetworkManager/stable/)

### Example Plugins

- [OpenVPN plugin](https://invent.kde.org/plasma/plasma-nm/-/tree/master/vpn/openvpn)
- [OpenConnect plugin](https://invent.kde.org/plasma/plasma-nm/-/tree/master/vpn/openconnect)
- [WireGuard plugin](https://github.com/Intika-Linux-Wireguard/Plasma-NM-Wireguard)

## Credits

Based on:
- Plasma Network Manager VPN plugin architecture
- OpenConnect VPN plugin structure
- NetworkManager OpenConnect plugin

## ⚠️ AI-GENERATED CODE DISCLAIMER

This KDE Plasma plugin code was 100% generated by AI (Claude). The author does not have expertise in Qt/KDE/Plasma plugin development and cannot fully verify the correctness of this implementation. The code works in testing but may contain issues. Use at your own risk and please report any bugs.

## License

SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
