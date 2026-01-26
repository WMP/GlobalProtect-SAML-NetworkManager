# Edge Wrapper - Browser Integration for SAML Authentication

## Overview

The `edge-wrapper` script is a wrapper for Microsoft Edge that handles the complex requirements of running a browser for SAML/2FA authentication from within NetworkManager's sandboxed environment.

## Why Microsoft Edge?

### Microsoft Intune Integration

Microsoft Edge is the recommended browser for organizations using Microsoft Intune MDM because:

1. **Single Sign-On (SSO)** - Edge integrates natively with Microsoft Entra ID (formerly Azure AD). When your device is enrolled in Intune, Edge can authenticate automatically using your device identity.

2. **Keyless Authentication** - Edge supports:
   - Device certificates provisioned by Intune
   - Windows Hello for Business credentials
   - Primary Refresh Tokens (PRT) from Microsoft Entra
   
   This means users don't need to enter passwords manually - authentication happens seamlessly using device credentials.

3. **Conditional Access Policies** - Edge properly evaluates Intune compliance policies, ensuring VPN access is granted only to compliant devices.

### GlobalProtect Callback Protocol

After successful SAML authentication, GlobalProtect servers redirect to a special URL:
```
globalprotectcallback://TOKEN_DATA_HERE
```

Edge (with the wrapper's policy configuration) automatically handles this protocol and passes the token back to `gpauth`, completing the authentication flow.

## What edge-wrapper Does

### 1. Display Detection

NetworkManager runs VPN services in a sandboxed environment where display variables may be incorrect. The wrapper:

```bash
# Detects DISPLAY from user's running desktop session
pid=$(pgrep -u "$REAL_UID" -x plasmashell)  # KDE
pid=$(pgrep -u "$REAL_UID" -x gnome-shell)  # GNOME
# Reads DISPLAY from /proc/$pid/environ
```

Supports both X11 (`DISPLAY=:0`) and Wayland (`WAYLAND_DISPLAY=wayland-0`).

### 2. Sandbox Workaround (ProtectHome)

NetworkManager uses systemd's `ProtectHome=read-only` for security, making the user's home directory read-only. Edge needs writable directories for:
- User profile
- Cache
- Crash reports

The wrapper creates temporary directories:
```
/tmp/edge-wrapper-$UID/
├── profile/     # Edge user data
├── home/        # Fake HOME
├── cache/       # XDG_CACHE_HOME
└── data/        # XDG_DATA_HOME
```

#### Security Considerations

**Why temporary profile?**
- NetworkManager's `ProtectHome=read-only` is a security feature that prevents VPN services from reading/writing user's home directory
- Edge cannot function without a writable profile directory
- The wrapper creates a fresh profile in `/tmp` for each authentication session

**Implications:**
- Your main Edge profile (`~/.config/microsoft-edge`) with saved passwords, cookies, and browsing history is **not used**
- Each VPN authentication starts with a clean browser profile
- Intune/Entra authentication still works because it uses device certificates (stored in system keychain, not browser profile)

**Security tradeoffs:**
- `/tmp` is typically world-readable on Linux systems
- The wrapper creates directories with user-only permissions (`/tmp/edge-wrapper-$UID/`)
- Temporary profile may contain session cookies during authentication
- Profile is not automatically cleaned up after use

**Recommendations:**
- The temporary profile does not persist sensitive data long-term
- For high-security environments, consider cleaning `/tmp/edge-wrapper-$UID/` after VPN connection
- Device certificate authentication (Intune) is more secure than password-based auth

### 3. Edge Policy Configuration

Creates a policy file to auto-approve the GlobalProtect callback protocol:

```json
{
    "AutoLaunchProtocolsFromOrigins": [
        {
            "allowed_origins": ["*"],
            "protocol": "globalprotectcallback"
        }
    ]
}
```

This prevents the "Open this link in external application?" dialog.

### 4. Privilege Dropping

When called by NetworkManager (running as root), the wrapper drops privileges to the actual user:

```bash
exec sudo -u "$REAL_USER" ... microsoft-edge ...
```

This ensures Edge runs with correct user permissions and access to user's D-Bus session.

### 5. Auto-Close After Authentication

The wrapper monitors for VPN connection completion:

```bash
# Check every 3 seconds if tunnel is up
ip link show gpd0 &>/dev/null && return 1  # VPN connected
pgrep -x gpauth &>/dev/null || return 1     # gpauth finished
```

Once authentication completes, Edge window is automatically closed.

## Edge Flags Explained

```bash
--ozone-platform=wayland          # Use Wayland backend
--enable-features=UseOzonePlatform
--no-first-run                    # Skip first-run wizard
--no-default-browser-check        # Don't ask to be default browser
--disable-crash-reporter          # Don't send crash reports
--disable-sync                    # Don't sync with Microsoft account
--disable-extensions              # Disable extensions for security
--disable-background-networking   # Don't fetch data in background
--app=$URL                        # Open in app mode (minimal UI)
--window-size=896,964             # Fixed window size
--user-data-dir=$PROFILE_DIR      # Custom profile directory
```

## Log Files

Debug logs are written to:
```
/tmp/edge-wrapper-$UID.log
```

Example log:
```
[2025-01-26 10:30:15] called with args: https://vpn.company.com/SAML
[2025-01-26 10:30:15] resolved real user: john (uid=1000) home=/home/john
[2025-01-26 10:30:15] using wayland socket: /run/user/1000/wayland-0
[2025-01-26 10:30:15] home is read-only (sandboxed); using temp HOME
[2025-01-26 10:30:16] started edge with PID=12345
[2025-01-26 10:30:45] VPN auth complete (tunnel up) - closing Edge
```

## Troubleshooting

### Edge doesn't start

```bash
# Check log file
cat /tmp/edge-wrapper-$(id -u).log

# Test manually
/usr/libexec/gpclient/edge-wrapper "https://example.com"
```

### Authentication works but Edge stays open

Check if gpauth process is running:
```bash
pgrep -x gpauth
```

Check if tunnel interface exists:
```bash
ip link show gpd0
```

### "Protocol not supported" error

Edge policy may not be applied. Check:
```bash
cat /tmp/edge-wrapper-$(id -u)/home/.config/microsoft-edge/policies/managed/globalprotect.json
```

### Wrong display / window doesn't appear

Check detected display:
```bash
grep "detected DISPLAY" /tmp/edge-wrapper-$(id -u).log
```

For Wayland, ensure `WAYLAND_DISPLAY` is correct:
```bash
ls /run/user/$(id -u)/wayland-*
```

## Alternative Browsers

If Edge is not available, you can use other browsers:

### Firefox
```bash
nmcli connection modify "My VPN" +vpn.data "browser=/usr/bin/firefox"
```
Note: Firefox may require manual login for Intune-protected portals.

### Chrome/Chromium
```bash
nmcli connection modify "My VPN" +vpn.data "browser=/usr/bin/google-chrome"
```

### Custom wrapper
You can create your own wrapper script and set it as browser path.

## Installation Path

The edge-wrapper is installed to:
```
/usr/libexec/gpclient/edge-wrapper
```

Default browser setting in VPN connections should use this path.
