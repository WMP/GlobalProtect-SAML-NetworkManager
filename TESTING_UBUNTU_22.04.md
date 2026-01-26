# Testing GlobalProtect VPN Plugin on Ubuntu 22.04

## Context - What Was Fixed

On Ubuntu 24.04, the VPN editor plugin now works correctly. The main bug was:

**Problem**: The main plugin (`libnm-vpn-plugin-gpclient.so`) called `nm_vpn_plugin_utils_load_editor()` with symbol `nm_vpn_editor_plugin_factory` which returns `NMVpnEditorPlugin*`, but it needs a factory function returning `NMVpnEditor*`.

**Solution**: Added `nm_vpn_editor_factory_gpclient()` function to both GTK3 and GTK4 editors, and changed the symbol lookup in the main plugin.

**Files changed**:
- `plugins/gnome/nm-gpclient-plugin-simple.c` - changed symbol to `nm_vpn_editor_factory_gpclient`
- `plugins/gnome/nm-gpclient-editor-gtk3.c` - added `nm_vpn_editor_factory_gpclient()` function
- `plugins/gnome/nm-gpclient-editor.c` - added `nm_vpn_editor_factory_gpclient()` function (GTK4)

## Potential Issues on Ubuntu 22.04

Ubuntu 22.04 has older library versions that may cause issues:

| Library | Ubuntu 22.04 | Ubuntu 24.04 |
|---------|--------------|--------------|
| libnm | 1.36.x | 1.46.x |
| libnma | 1.8.x | 1.10.x |
| GTK3 | 3.24.33 | 3.24.41 |
| GTK4 | 4.6.x | 4.14.x |

### Things to watch for:

1. **libnma-gtk4 may not be available** - Ubuntu 22.04 might not have `libnma-gtk4` package. The Makefile handles this - it only builds GTK4 editor if `pkg-config --exists libnma-gtk4` succeeds.

2. **Different API in older libnm** - Some functions may have different signatures or not exist. Check compilation warnings/errors carefully.

3. **nm_vpn_plugin_utils_load_editor location** - This function is copied into our code (`nm-vpn-plugin-utils.c`), but if it behaves differently with older libnm, there could be issues.

4. **GTK widget behavior** - The lazy widget initialization should work, but older GTK3 might have subtle differences.

5. **AT-SPI/Dogtail for testing** - Accessibility framework might behave differently on older GNOME.

## Setup Instructions

### 1. Clone and switch branch:
```bash
git clone git@github.com:WMP/GlobalProtect-SAML-NetworkManager.git
cd GlobalProtect-SAML-NetworkManager
git checkout gui-tests
```

### 2. Install build dependencies:
```bash
sudo apt update
sudo apt install -y build-essential pkg-config libnm-dev libnma-dev \
    libgtk-3-dev network-manager-gnome
```

### 3. Build plugins:
```bash
make -C plugins/gnome clean all 2>&1 | tee build.log
```

**Check for errors!** If compilation fails, the build.log will contain details.

### 4. Install:
```bash
sudo make install-dev
sudo systemctl restart NetworkManager
```

### 5. Verify installation:
```bash
# Check plugin files exist
ls -la /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so

# Check exported symbols
objdump -T /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient-editor.so | grep factory

# Expected output should include:
# nm_vpn_editor_plugin_factory
# nm_vpn_editor_factory_gpclient  <-- THIS IS CRITICAL
```

## Manual Testing

1. Run: `nm-connection-editor`
2. Click "Add" (or "+")
3. In dropdown select "GlobalProtect" (under VPN section)
4. Click "Create..."

**Expected**: Dialog "Editing VPN connection" opens with VPN tab containing:
- Gateway (text entry)
- Browser (dropdown with detected browsers like `/usr/bin/firefox`)
- DNS Servers (text entry)

**If error appears**: Note the exact error message. Common errors:
- `nm_vpn_plugin_utils_load_editor: assertion 'NM_IS_VPN_EDITOR (editor)' failed` - factory function issue
- `Could not load editor VPN plugin` - library not found or symbol missing

## Automated Testing with Claude

### Install test dependencies:
```bash
# For Ubuntu 22.04 (no PEP 668 restriction)
sudo apt install -y python3-pytest python3-gi gir1.2-atspi-2.0 at-spi2-core
pip3 install dogtail

# Enable accessibility
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

### Run automated tests:
```bash
# Must be run in X11 session (not Wayland), with display available
export DISPLAY=:0
make test-ui-only 2>&1 | tee test.log
```

### Expected test results:
```
tests/test_nm_connection_editor.py::TestNMConnectionEditor::test_editor_opens PASSED
tests/test_nm_connection_editor.py::TestNMConnectionEditor::test_add_button_exists PASSED
tests/test_nm_connection_editor.py::TestNMConnectionEditor::test_open_add_connection_dialog PASSED
tests/test_nm_connection_editor.py::TestNMConnectionEditor::test_vpn_option_available PASSED
tests/test_nm_connection_editor.py::TestNMConnectionEditor::test_select_vpn_and_create PASSED
```

The critical test is `test_select_vpn_and_create` - it verifies the full flow of creating a GlobalProtect VPN connection and checks that the editor dialog opens with VPN fields.

### If tests fail:

1. Check if running in X11: `echo $XDG_SESSION_TYPE` (should be `x11`, not `wayland`)
2. Check accessibility: `gsettings get org.gnome.desktop.interface toolkit-accessibility`
3. Screenshots are saved to `artifacts/` directory on failure
4. Review `test.log` for error details

## Diagnostic Information to Collect

If something doesn't work, run these commands and provide output:

```bash
# System info
lsb_release -a
uname -a

# Library versions
pkg-config --modversion libnm libnma gtk+-3.0
dpkg -l | grep -E "libnm|network-manager"

# NetworkManager version
nmcli --version

# Check if plugin is recognized
nmcli connection add type vpn vpn-type gpclient con-name test-gp 2>&1

# Check for errors in journal
journalctl -u NetworkManager --since "5 minutes ago" | grep -i gpclient

# Exported symbols
objdump -T /usr/lib/x86_64-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so | grep -E "factory|editor"
```

## Architecture Reference

```
nm-connection-editor
    │
    ▼ loads via nm-gpclient-service.name [plugin=libnm-vpn-plugin-gpclient.so]
    │
libnm-vpn-plugin-gpclient.so
    │ nm_vpn_editor_plugin_factory() → creates GpclientPlugin
    │ get_editor() → calls nm_vpn_plugin_utils_load_editor()
    │
    ▼ loads "libnm-vpn-plugin-gpclient-editor.so", symbol "nm_vpn_editor_factory_gpclient"
    │
libnm-vpn-plugin-gpclient-editor.so
    │ nm_vpn_editor_factory_gpclient() → creates NMGpclientEditor
    │ get_widget() → builds GTK UI with Gateway/Browser/DNS fields
    │
    ▼
VPN Editor Dialog with fields
```
