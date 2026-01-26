# GlobalProtect NetworkManager Plugin - Full Documentation

A NetworkManager VPN plugin for GlobalProtect with GUI configuration support for GNOME, KDE Plasma, MATE, and XFCE.

## What Works

- **VPN Connection** - Connects to GlobalProtect servers with SAML/2FA
- **GUI Configuration** - Configure Gateway, Browser, and DNS in network settings
- **Browser-based 2FA** - Opens browser for SAML authentication
- **Custom DNS** - Set your own DNS servers or use system defaults
- **Multi-Desktop Support** - GTK3, GTK4, and Qt5/Plasma editors
- **Routing Control** - Configure which traffic goes through VPN

## Installation

### From Release (Recommended)

Download packages from [GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases) for your Ubuntu version.

```bash
# Install core package + GUI package for your desktop
sudo dpkg -i network-manager-gpclient_*.deb network-manager-gpclient-gnome_*.deb
sudo apt-get install -f

# For KDE Plasma use network-manager-gpclient-plasma_*.deb instead
```

### Building from Source

```bash
./build-all.sh          # Build for all Ubuntu versions
./build-all.sh 24.04    # Build for Ubuntu 24.04 only
```

## Configuring Your VPN

### Method 1: Using GUI

**For GNOME/MATE/XFCE:**
1. Open **Settings → Network** or `nm-connection-editor`
2. Click **+** to add a new connection
3. Select **VPN** → **GlobalProtect**
4. Configure:
   - **Gateway**: Your VPN server (e.g., `vpn.company.com`)
   - **Browser**: Path to browser for 2FA (default: `/usr/bin/edge-wrapper`)
   - **DNS Servers**: Optional custom DNS (e.g., `8.8.8.8;8.8.4.4`)
5. Click **Save**

**For KDE Plasma:**
1. Open **System Settings** → **Network** → **Connections**
2. Click **+** to add a new connection
3. Select **VPN** → **GlobalProtect**
4. Configure the same fields as above
5. Click **OK**

### Method 2: Using nmcli (Command Line)

```bash
# Create new connection with all settings
nmcli connection add type vpn vpn-type org.freedesktop.NetworkManager.gpclient \
  con-name "My VPN" \
  vpn.data "gateway=vpn.company.com,browser=/usr/bin/edge-wrapper"

# Connect
nmcli connection up "My VPN"

# Disconnect
nmcli connection down "My VPN"
```

**Modify existing connection:**
```bash
# Set gateway
nmcli connection modify "My VPN" +vpn.data "gateway=vpn.company.com"

# Set browser
nmcli connection modify "My VPN" +vpn.data "browser=/usr/bin/firefox"

# Set custom DNS
nmcli connection modify "My VPN" +vpn.data "dns=8.8.8.8;8.8.4.4"

# View settings
nmcli connection show "My VPN" | grep vpn.data
```

## Browser Setup for 2FA/SAML

The `browser` field specifies which browser opens for authentication.

| Browser | Path |
|---------|------|
| Edge (recommended) | `/usr/bin/edge-wrapper` or `/usr/libexec/gpclient/edge-wrapper` |
| Firefox | `/usr/bin/firefox` |
| Chrome | `/usr/bin/google-chrome` |
| Chromium | `/usr/bin/chromium-browser` |

Verify browser exists:
```bash
which firefox  # Should return path
```

## Troubleshooting

### Browser doesn't open for 2FA

```bash
# Check browser path
nmcli connection show "My VPN" | grep browser

# Fix browser path
nmcli connection modify "My VPN" +vpn.data "browser=/usr/bin/firefox"
```

### VPN won't connect

```bash
# Check logs
sudo journalctl -u NetworkManager -f | grep gpclient

# Test service manually
sudo /usr/lib/NetworkManager/nm-gpclient-service --debug

# Check if gpclient is running
ps aux | grep gpclient
```

### GUI editor doesn't appear

Use nm-connection-editor as alternative:
```bash
nm-connection-editor
```

## Architecture

### Components

| Component | Description |
|-----------|-------------|
| `libnm-vpn-plugin-gpclient.so` | Main NetworkManager plugin |
| `libnm-vpn-plugin-gpclient-editor.so` | GTK3 editor for nm-connection-editor |
| `libnm-gtk4-vpn-plugin-gpclient-editor.so` | GTK4 editor for GNOME Settings |
| `plasmanetworkmanagement_gpclientui.so` | Qt5 editor for KDE Plasma |
| `nm-gpclient-service` | Python VPN service daemon |

### Installed Files

```
/usr/lib/NetworkManager/
├── nm-gpclient-service              # VPN service
└── VPN/
    └── nm-gpclient-service.name     # Service descriptor

/usr/lib/x86_64-linux-gnu/NetworkManager/
├── libnm-vpn-plugin-gpclient.so           # Main plugin
├── libnm-vpn-plugin-gpclient-editor.so    # GTK3 editor
└── libnm-gtk4-vpn-plugin-gpclient-editor.so  # GTK4 editor

/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/
└── plasmanetworkmanagement_gpclientui.so  # Plasma editor

/usr/bin/
├── gpclient    # VPN client
├── gpauth      # SAML auth handler
└── gpservice   # Background service
```

### Configuration Storage

VPN settings are stored as NetworkManager data items:
- `gateway` - VPN server hostname/IP
- `browser` - Full path to browser executable
- `dns` - Custom DNS servers (semicolon-separated)

## System Requirements

- Linux with NetworkManager 1.46+
- Python 3, python3-sdbus
- openconnect

**Desktop-specific:**
- GNOME: GTK3/GTK4, libnma
- KDE Plasma 5: Qt5, KF5, plasma-nm

## Additional Documentation

- [EDGE_WRAPPER.md](EDGE_WRAPPER.md) - Edge wrapper and browser integration for SAML
- [PYTHON_SERVICE.md](PYTHON_SERVICE.md) - VPN service implementation details
- [GNOME_SETTINGS_INTEGRATION.md](GNOME_SETTINGS_INTEGRATION.md) - GNOME/GTK plugin details
- [PLASMA_IMPLEMENTATION.md](PLASMA_IMPLEMENTATION.md) - KDE Plasma plugin details

## License

Based on NetworkManager OpenConnect plugin architecture. See [debian/copyright](../debian/copyright).
