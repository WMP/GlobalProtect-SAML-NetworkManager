# Testing GlobalProtect VPN Plugin

## Manual Testing

### 1. Verify installation

```bash
# Check plugin files exist
ls -la /usr/lib/*-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so

# Check service file
ls -la /usr/lib/NetworkManager/nm-gpclient-service

# Check exported symbols
objdump -T /usr/lib/*-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient-editor.so | grep factory
```

### 2. Test nm-connection-editor

1. Run: `nm-connection-editor`
2. Click "+" to add new connection
3. Select "VPN" → "GlobalProtect"
4. Click "Create..."

**Expected:** Dialog opens with VPN tab containing:
- Gateway (text entry)
- Browser (text entry)
- Connect timeout (spin button)
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

## Upgrade test

CI proves that users of the last release upgrade cleanly to the packages of a
build. For every Ubuntu version and architecture the step "Upgrade test from the
last release" of `.github/workflows/build-release.yml` starts a fresh
`ubuntu:<version>` container, installs the newest release from the public apt
repository (scenarios `gnome` and `plasma`; `plasma` also checks that the editor
plugin is in the Qt directory of the release: `qt5` on 22.04 and 24.04, `qt6` on
24.10 and 26.04; the scenario `neon` does the same for
`network-manager-gpclient-plasma-6` on KDE neon, see below), then runs
`apt upgrade` to the new `.deb` files (in the `neon` scenario only the installed
`network-manager-gpclient*` packages are upgraded, not the base system of the neon
repository) and checks that nothing is kept back,
removed or half-configured (`.github/scripts/upgrade-test.sh`, judged by
`.github/scripts/check_upgrade.py`). Before the upgrade it checks that the build
is newer than the release (`dpkg --compare-versions`). When the release has no
such package for that Ubuntu version or architecture (no suite for 24.10, no
arm64 builds), there is nothing to upgrade from: the script says so, installs the
package from the new `.deb` files instead and checks the result the same way
(`check_upgrade.py --fresh`). The step does not run for tags, so a network
failure cannot block a release. To run it by hand
against built packages (it needs network access and changes only the container):

```bash
mkdir -p gui-smoke
docker run --rm -v "$PWD/output/ubuntu24.04-amd64:/debs:ro" -v "$PWD/.github/scripts:/scripts:ro" \
  -v "$PWD/gui-smoke:/out" \
  ubuntu:24.04 bash /scripts/upgrade-test.sh gnome /debs /out   # or: plasma, neon
```

### GUI smoke test

After a successful upgrade, `upgrade-test.sh` calls `.github/scripts/gui-smoke.sh`
(only when a third argument names an output directory). It runs in the same container and checks that the installed
connection editor starts:

- **GNOME** (`gnome`): under Xvfb, `gui_smoke_gtk.py` finds the editor the way
  NetworkManager does (the `.name` file of `network-manager-gpclient`, loaded
  with `NM.VpnPluginInfo`), opens it for a test connection and shows it in a
  window. It then saves a screenshot and checks that the widget has a size and
  shows the gateway, the preferred gateway and the two gateways of the list, the
  username, the authentication mode, the "Address is a gateway" check button
  and the connect timeout (a spin button showing the `vpn.timeout` of the test
  connection). It then runs the save scenarios of `TIMEOUT_CASES` in
  `gui_smoke_lib.py`: a new profile starts with 180 s for browser login and 0
  for username and password (until the user sets a value), an existing profile
  keeps its value, and `update_connection()` writes the shown value to
  `vpn.timeout`.
  It runs for GTK3 and, where the package ships the GTK4 editor (not on 22.04),
  for GTK4 in a second process.
- **KDE neon** (`neon`): installs `network-manager-gpclient-plasma-6` in an
  `ubuntu:24.04` container to which `.github/scripts/neon-repo.sh` has added the KDE
  neon repository (the script the build image uses too; it checks the key's
  fingerprint), expects the plugin in the `qt6` directory and runs the Plasma load
  check below with PyQt6. The scenario runs only on Ubuntu 24.04 where this build has
  the package for the architecture (`amd64`); elsewhere it prints `SKIP` and
  succeeds.
- **Plasma** (`plasma`, and `neon`): a load check only. `ldd` finds every library
  of `plasmanetworkmanagement_gpclientui.so`, its metadata names our service,
  and `QPluginLoader` (PyQt5 for a `qt5` plugin, PyQt6 for a `qt6` one) loads it
  with `QT_QPA_PLATFORM=offscreen`; a PyQt package that cannot be installed fails
  the test. PyQt5 cannot read the metadata of a loaded plugin, so the JSON file
  next to the plugin is checked then. There is **no screenshot**: showing the
  Plasma editor needs a small C++ harness against plasma-nm, a possible follow-up.

The screenshots (`gnome-gtk3-<codename>-<arch>.png`, `gnome-gtk4-...png`) are in
the artifact `gui-smoke-ubuntu-<ubuntu>-<arch>` of the workflow run (step "Upload
GUI screenshots", also after a failed run). The pure logic of the probes is in
`.github/scripts/gui_smoke_lib.py`, tested by `tests/unit/test_gui_smoke.py`; the
GTK and Qt parts themselves run only in CI.

## End-to-end tests

`tests/e2e/` runs the plugin on a real desktop in Docker: GNOME 46 (Ubuntu
24.04) or KDE Plasma 6.6 (Ubuntu 26.04), real NetworkManager and
systemd-resolved, the editor plugins, the VPN service and Microsoft Edge started
by the browser wrapper. Only `gpclient`/`gpauth` (they run the real
`vpnc-script`) and the sign-in site are fakes. The scenarios click through the
real UI (add the VPN, connect, sign in), and a check step in the container
fails the run when something is wrong: VPN not active, wrong tunnel address,
Edge not started as the desktop user or not closed, wrong routes or DNS (full
tunnel against "only for resources on its network"), errors in the service
log, or leftovers after the disconnect. A negative scenario closes the sign-in
window and expects a clean failure.

```bash
tests/e2e/run.sh all            # gnome, kde, routing, auth-cancel (about 25 min with the builds)
tests/e2e/run.sh gnome --gif    # also makes docs/images/demo-gnome.gif
```

Docker with privileged containers and an amd64 host are needed. The results are
in `tests/e2e/out/<scenario>/` (`results.json`, logs, screenshots, `raw.mp4`).
The workflow `e2e.yml` runs the same by hand (Actions tab) or when a pull
request gets the label `e2e`. See `tests/e2e/README.md`.

## Upstream tests

`tests/upstream/` checks the pinned `external/GlobalProtect-openconnect`
(the contract, the changelog classification, the README tag and the Rust
version). Run `python3 -m pytest tests/upstream -q` with the submodule checked
out. A breaking upstream change needs a manual verification: see
[UPSTREAM.md](UPSTREAM.md).

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
