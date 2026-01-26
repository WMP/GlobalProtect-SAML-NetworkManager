# NetworkManager GlobalProtect VPN Plugin

NetworkManager VPN plugin for GlobalProtect (Palo Alto Networks) with SAML/SSO authentication support.

## Features

- **NetworkManager integration** - manage VPN like any other connection
- **SAML/2FA authentication** via browser (Edge, Firefox, Chrome)
- **Desktop support** - GNOME Settings (GTK3/GTK4) and KDE Plasma
- **Routing control** - configure which traffic goes through VPN
- **Systemd service** - automatic VPN service management via D-Bus

## Installation

Download `.deb` packages from [GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases).

```bash
# Ubuntu 24.04 - GNOME
sudo dpkg -i network-manager-gpclient_*_ubuntu24.04.deb \
             network-manager-gpclient-gnome_*_ubuntu24.04.deb
sudo apt-get install -f

# Ubuntu 24.04 - KDE Plasma
sudo dpkg -i network-manager-gpclient_*_ubuntu24.04.deb \
             network-manager-gpclient-plasma_*_ubuntu24.04.deb
sudo apt-get install -f
```

Packages for Ubuntu 22.04 are also available.

## Usage

1. Open GNOME Settings → Network or KDE Network Settings
2. Add VPN → GlobalProtect
3. Enter gateway URL (e.g. `vpn.example.com`)
4. Connect - browser will open for SAML authentication

```bash
# Or via command line
nmcli connection up "GlobalProtect VPN"
```

## Packages

| Package | Description |
|---------|-------------|
| `network-manager-gpclient` | Core VPN service (required) |
| `network-manager-gpclient-gnome` | GNOME/GTK integration |
| `network-manager-gpclient-plasma` | KDE Plasma integration |

## Architecture

```
┌─────────────────────────┐
│   GNOME Settings        │
│   KDE Plasma NM         │
│   nm-connection-editor  │
└───────────┬─────────────┘
            │ Configuration
            ▼
┌─────────────────────────┐
│   NetworkManager        │
└───────────┬─────────────┘
            │ D-Bus
            ▼
┌─────────────────────────┐
│ nm-gpclient-service     │  ← Python VPN Service (systemd)
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   gpclient / gpauth     │  ← VPN connection + SAML auth
└─────────────────────────┘
```

## Project Structure

```
├── service/                    # Python VPN service backend
│   └── nm-gpclient-service.py
├── plugins/
│   ├── gnome/                  # GNOME/GTK plugins (C)
│   └── plasma/                 # KDE Plasma plugin (C++/Qt)
├── config/                     # NetworkManager & systemd configuration
├── scripts/                    # Helper scripts (edge-wrapper)
├── external/
│   └── GlobalProtect-openconnect/  # VPN client (submodule)
└── debian/                     # Debian packaging
```

## Building from Source

### Requirements

- GNOME plugins: `libglib2.0-dev`, `libnm-dev`, `libgtk-3-dev`, `libgtk-4-dev`, `libnma-dev`
- Plasma plugin: `cmake`, `extra-cmake-modules`, `plasma-nm-dev`, Qt5 libraries
- VPN client: `cargo` (Rust), `libssl-dev`, `libopenconnect-dev`

### Build

```bash
./build-all.sh          # Build for all Ubuntu versions
./build-all.sh 24.04    # Build for Ubuntu 24.04 only
```

### Build Individual Components

```bash
make gnome-plugins      # Build only GNOME plugins
cd plugins/plasma && ./build.sh  # Build only Plasma plugin
```

## Why Microsoft Edge?

Microsoft Edge is the recommended browser for SAML authentication because:

- **Microsoft Intune compatibility** - Edge integrates with Microsoft Entra ID (Azure AD) and Intune MDM, enabling seamless SSO authentication without additional password prompts
- **Keyless authentication** - When enrolled in Intune, Edge can use device certificates and Windows Hello credentials stored in the system, eliminating manual credential entry
- **GlobalProtect callback handling** - Edge properly handles the `globalprotectcallback://` protocol used to pass authentication tokens back to the VPN client

The included `edge-wrapper` script handles:
- Running Edge with correct Wayland/X11 display settings
- Working around NetworkManager's sandbox (ProtectHome=read-only)
- Auto-closing Edge window after successful authentication
- Setting up Edge policies for automatic protocol handling

**Security note:** NetworkManager runs VPN services with `ProtectHome=read-only`, which prevents Edge from accessing its profile in `~/.config/microsoft-edge`. The edge-wrapper creates a temporary profile in `/tmp/edge-wrapper-$UID/` to work around this. This means your main Edge profile (with saved passwords, cookies) is **not** used for VPN authentication - each session starts fresh. While `/tmp` is world-readable, the wrapper creates per-user directories with restricted permissions.

Firefox and Chrome also work but may require manual credential entry for Intune-protected portals.

## Troubleshooting

```bash
# Check service logs
sudo journalctl -u NetworkManager -f | grep gpclient

# Test service manually
sudo /usr/lib/NetworkManager/nm-gpclient-service --debug

# Verify installation
ls -l /usr/lib/NetworkManager/nm-gpclient-service
ls -l /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so
```

### Debug vpnc-script

The repository includes a modified `vpnc-script` (from Ubuntu 24.04 `vpnc-scripts` package) with added debug logging. This script is **not installed** by the package - you need to download it manually from the repository:

```bash
# Download and install debug vpnc-script
curl -o /tmp/vpnc-script https://raw.githubusercontent.com/WMP/GlobalProtect-SAML-NetworkManager/main/scripts/vpnc-script-debug
sudo cp /tmp/vpnc-script /usr/share/vpnc-scripts/vpnc-script
```

Debug logs are written to `/tmp/vpnc-script2.log`.

## Documentation

- [docs/README.md](docs/README.md) - Full documentation
- [docs/EDGE_WRAPPER.md](docs/EDGE_WRAPPER.md) - Edge wrapper and browser integration
- [docs/PYTHON_SERVICE.md](docs/PYTHON_SERVICE.md) - Service implementation details
- [docs/GNOME_SETTINGS_INTEGRATION.md](docs/GNOME_SETTINGS_INTEGRATION.md) - GNOME integration
- [docs/PLASMA_IMPLEMENTATION.md](docs/PLASMA_IMPLEMENTATION.md) - Plasma plugin details

## License

See [debian/copyright](debian/copyright).

## Credits

This project uses [GlobalProtect-openconnect](https://github.com/yuezk/GlobalProtect-openconnect) by yuezk as a submodule. From that project we build and include:
- `gpclient` - VPN client binary that handles the actual VPN connection
- `gpauth` - SAML authentication handler
- `gpservice` - Background service for VPN management

The NetworkManager integration (plugins for GNOME/Plasma, Python service, D-Bus configuration) is original work in this repository.

## Related Projects

- [GlobalProtect-openconnect](https://github.com/yuezk/GlobalProtect-openconnect) - VPN client backend by yuezk
- [NetworkManager](https://networkmanager.dev/) - Linux network management
