# Testing GlobalProtect VPN Plugin

## Manual Testing

### 1. Verify installation

```bash
# Check plugin files exist
ls -la /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so

# Check service file
ls -la /usr/lib/NetworkManager/nm-gpclient-service

# Check exported symbols
objdump -T /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient-editor.so | grep factory
```

### 2. Test nm-connection-editor

1. Run: `nm-connection-editor`
2. Click "+" to add new connection
3. Select "VPN" → "GlobalProtect"
4. Click "Create..."

**Expected:** Dialog opens with VPN tab containing:
- Gateway (text entry)
- Browser (text entry)
- DNS Servers (text entry)

### 3. Test GNOME Settings (Ubuntu 24.04)

1. Open Settings → Network
2. Click "+" next to VPN
3. Select "GlobalProtect"

### 4. Test command line

```bash
# Create connection
nmcli connection add type vpn vpn-type org.freedesktop.NetworkManager.gpclient \
    con-name "Test VPN" \
    vpn.data "gateway=vpn.example.com"

# Verify
nmcli connection show "Test VPN" | grep vpn

# Delete test connection
nmcli connection delete "Test VPN"
```

## Troubleshooting

### Common errors

**"Could not load editor VPN plugin"**
- Check if plugin library exists
- Check NetworkManager logs: `journalctl -u NetworkManager | grep gpclient`

**"nm_vpn_plugin_utils_load_editor: assertion failed"**
- Factory function issue - check exported symbols with `objdump`

### Diagnostic commands

```bash
# System info
lsb_release -a
nmcli --version

# Library versions
pkg-config --modversion libnm libnma gtk+-3.0

# Check if plugin is recognized
nmcli connection add type vpn vpn-type gpclient con-name test-gp 2>&1

# NetworkManager logs
journalctl -u NetworkManager --since "5 minutes ago" | grep -i gpclient
```

## Architecture

```
nm-connection-editor / GNOME Settings
    │
    ▼ loads via nm-gpclient-service.name
    │
libnm-vpn-plugin-gpclient.so
    │ nm_vpn_editor_plugin_factory() → creates plugin
    │
    ▼ loads editor library
    │
libnm-vpn-plugin-gpclient-editor.so (GTK3)
libnm-gtk4-vpn-plugin-gpclient-editor.so (GTK4)
    │
    ▼
VPN Editor Dialog with Gateway/Browser/DNS fields
```

## Ubuntu Version Differences

| Component | Ubuntu 22.04 | Ubuntu 24.04 |
|-----------|--------------|--------------|
| libnm | 1.36.x | 1.46.x |
| libnma | 1.8.x | 1.10.x |
| GTK3 | 3.24.33 | 3.24.41 |
| GTK4 | 4.6.x | 4.14.x |
| libnma-gtk4 | ❌ | ✅ |

Note: GTK4 editor (for GNOME Settings) is only built on Ubuntu 24.04+ where `libnma-gtk4` is available.
