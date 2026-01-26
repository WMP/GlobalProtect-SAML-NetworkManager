# GUI Tests for NetworkManager GlobalProtect Plugin

Automated GUI tests using Dogtail/AT-SPI for testing VPN plugin integration.

## Quick Start

```bash
# Clone repository
git clone https://github.com/WMP/GlobalProtect-SAML-NetworkManager.git
cd GlobalProtect-SAML-NetworkManager
git checkout gui-tests

# Install build and test dependencies
sudo apt install -y \
    build-essential pkg-config \
    libnm-dev libgtk-3-dev libgtk-4-dev libnma-dev libnma-gtk4-dev \
    at-spi2-core python3-pyatspi libatk-adaptor \
    accerciser gnome-screenshot imagemagick \
    network-manager-gnome \
    python3-dogtail python3-pytest

# Enable accessibility
gsettings set org.gnome.desktop.interface toolkit-accessibility true

# Run full test (build + install + restart NM + test)
make test-ui
```

## Requirements

### System (Ubuntu 24.04)

```bash
# Build dependencies
sudo apt install -y \
    build-essential \
    pkg-config \
    libnm-dev \
    libgtk-3-dev \
    libgtk-4-dev \
    libnma-dev \
    libnma-gtk4-dev

# AT-SPI accessibility framework
sudo apt install -y \
    at-spi2-core \
    python3-pyatspi \
    libatk-adaptor

# Debugging tool
sudo apt install -y accerciser

# Screenshot tools
sudo apt install -y gnome-screenshot imagemagick

# NetworkManager editor
sudo apt install -y network-manager-gnome
```

### Python

```bash
# Ubuntu 24.04+ (PEP 668 - system packages)
sudo apt install -y python3-dogtail python3-pytest

# Lub przez pipx/venv jeśli wolisz izolację
# pipx install dogtail pytest
```

## Environment Setup

### 1. X11 Session Required

GUI tests require X11 (not Wayland):

```bash
# Check current session
echo $XDG_SESSION_TYPE  # Should output: x11

# If Wayland, logout and select "GNOME on Xorg" at login screen
```

### 2. Enable Accessibility

```bash
# Check if enabled
gsettings get org.gnome.desktop.interface toolkit-accessibility

# Enable if false
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

### 3. Verify D-Bus

```bash
echo $DBUS_SESSION_BUS_ADDRESS  # Must be set
```

## Running Tests

### Full Test (Build + Install + Test)

```bash
make test-ui
```

This will:
1. Build GNOME plugins (`make gnome-plugins`)
2. Install plugin files to system (`make install-dev`)
3. Restart NetworkManager (`make restart-nm`)
4. Run GUI tests with pytest

### Test Without Rebuilding

```bash
make test-ui-only
```

Use this when plugin is already installed and you just want to re-run tests.

### Only nm-connection-editor Tests (ETAP 1 - Stable)

```bash
make test-ui-editor
```

### Only GNOME Settings Tests (ETAP 2 - GTK4)

```bash
make test-ui-gnome
```

### Manual Screenshot

```bash
make screenshot
make screenshot SCREEN_NAME=my_screenshot
```

### Collect Logs

```bash
make logs
# Logs saved to artifacts/
```

## Development Workflow

### Install Plugin for Development

```bash
make install-dev    # Build and install plugin (requires sudo)
make restart-nm     # Restart NetworkManager to load plugin
```

### Uninstall Plugin

```bash
make uninstall-dev  # Remove all installed files
make restart-nm     # Restart NetworkManager
```

## Test Structure

```
tests/
├── conftest.py                    # Pytest fixtures and hooks
├── test_nm_connection_editor.py   # ETAP 1: nm-connection-editor tests
├── test_gnome_settings.py         # ETAP 2: GNOME Settings tests
├── helpers/
│   ├── __init__.py
│   ├── dogtail_utils.py           # AT-SPI helper functions
│   └── screenshot.py              # Screenshot utilities
└── README.md                      # This file
```

## Test Stages

### ETAP 1: nm-connection-editor (Stable)

Tests the GTK3-based NetworkManager connection editor:
- Opens nm-connection-editor
- Clicks "Add" button
- Selects "VPN" connection type
- Verifies VPN type selection dialog appears

**Works on:** All desktop environments (GNOME, KDE, MATE, Cinnamon, etc.)

### ETAP 2: GNOME Settings (GTK4)

Tests integration with GNOME Control Center:
- Opens gnome-control-center network panel
- Navigates to VPN section
- Verifies "Add VPN" functionality

**Requires:** GNOME desktop environment

## Debugging

### Using Accerciser

Accerciser is a GUI tool to explore AT-SPI tree:

```bash
# Run both tools
accerciser &
nm-connection-editor &

# In Accerciser:
# 1. Click on nm-connection-editor window
# 2. Navigate the tree to find elements
# 3. Note: roleName, name, states
# 4. Use these values in tests
```

### Common AT-SPI Roles

| Role | Examples |
|------|----------|
| `frame` | Main application window |
| `dialog` | Modal dialogs |
| `push button` | Buttons (Add, Create, Cancel) |
| `table cell` | List/tree items |
| `text` | Text input fields |
| `combo box` | Dropdown menus |

### Test Failure Debugging

On failure, tests automatically:
1. Take screenshot → `artifacts/fail_<testname>_<time>.png`
2. Save test log → `artifacts/test_ui.log`

Additional debugging:
```bash
make logs   # Collect system logs
ls artifacts/  # View all artifacts
```

## Troubleshooting

### "Element not found" Errors

1. Run Accerciser to find correct element names
2. Element names may vary by language/theme
3. Use `name_contains` for partial matching

### "X11 session required" Error

```bash
# Check session type
echo $XDG_SESSION_TYPE

# If 'wayland', log out and select "GNOME on Xorg"
```

### "Accessibility not enabled" Warning

```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
# May need to restart session
```

### Dogtail Import Error

```bash
pip3 install --user dogtail
# Or system-wide:
sudo apt install python3-dogtail
```

## CI/CD Integration

For headless testing (CI), use Xvfb:

```bash
# Install
sudo apt install xvfb

# Run tests with virtual display
xvfb-run -a make test-ui
```

Note: Some tests may behave differently in Xvfb due to timing differences.
