# Browser Wrapper - Browser Integration for SAML Authentication

## Overview

`browser-wrapper` handles the awkward requirements of running a browser for
SAML/2FA authentication from inside NetworkManager's sandboxed environment: a
session environment that has to be reconstructed, a read-only home directory,
privilege dropping, and knowing when the authentication window may be closed.

It works with Edge, Chrome, Chromium, Firefox and your desktop's default
browser; `edge-wrapper` remains as a thin shim (`GP_BROWSER=edge`) so profiles
created before the generalisation keep working. Which browser is used comes from
`vpn.data browser` in the connection - see [Alternative Browsers](#alternative-browsers).

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

## What browser-wrapper Does

### 1. Display Detection

NetworkManager runs VPN services in a sandboxed environment with no session
context, so display variables are missing or wrong. The wrapper reads them from
the processes that own the session:

```bash
# DISPLAY, XAUTHORITY, WAYLAND_DISPLAY, XDG_SESSION_TYPE from the session leader
pid=$(pgrep -u "$REAL_UID" -x plasmashell)  # KDE
pid=$(pgrep -u "$REAL_UID" -x gnome-shell)  # GNOME
# Read from /proc/$pid/environ
```

Supports both X11 (`DISPLAY=:0`) and Wayland (`WAYLAND_DISPLAY=wayland-0`); the
Chromium `--ozone-platform=wayland` flags are only added on an actual Wayland
session, so X11 sessions are no longer broken by them.

The service does the same thing on its side (`_get_session_env()`), so gpauth and
browsers launched without this wrapper get a usable environment too. Passing only
`DISPLAY=:0` was why `browser=firefox` opened no window at all on Wayland
([#7](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/7)).

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

The wrapper watches the `gpauth` process it was launched from. gpauth exits the
moment it receives the callback data, so its death is the one reliable signal
that the user is done authenticating:

```bash
GPAUTH_PID=$(pgrep -n -x -u "$REAL_UID" gpauth)   # taken once, at startup
... kill the browser when that PID is gone
```

The PID cannot be taken from `$PPID`: gpauth launches the browser through
`open::with_detached()`, which double-forks and calls `setsid()`, so the
wrapper's parent is init.

Earlier versions guessed instead - they closed the browser as soon as a
`gpd0`/`tun0` interface existed (any interface, including another VPN's) or as
soon as `pgrep gpauth` came up empty, first checked ~8 s after launch. That is
far too early for a login with a password and a push 2FA step, and it is why
authentication only ever worked for people already signed in to their browser
([#7](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/7)).

If no gpauth process can be found, the wrapper never closes the window - it
waits for the browser to exit or for the safety timeout (`GP_AUTH_TIMEOUT`,
300 s by default).

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
[2025-01-26 10:30:15] tracking gpauth PID 12344
[2025-01-26 10:30:16] started browser with PID=12345
[2025-01-26 10:31:22] done: gpauth (12344) exited - authentication finished, closing browser
```

The last line always starts with `done:` and says why the wrapper finished:
`gpauth ... exited`, `timeout after ...`, `browser exited on its own`, or
`browser exited (no gpauth to watch, nothing was killed)`.

## Troubleshooting

### Edge doesn't start

```bash
# Check log file
cat /tmp/edge-wrapper-$(id -u).log

# Test manually (any browser, without a VPN connection involved)
GP_BROWSER=firefox /usr/libexec/gpclient/browser-wrapper "https://example.com"
```
With no gpauth running the window must open and stay open - nothing should
close it.

### The window closes before I finish logging in

Check the reason in the log:
```bash
grep "done:" /tmp/edge-wrapper-$(id -u).log
```
`timeout after 300s` means the login took longer than the safety timeout. Raise
it for the whole service:
```bash
sudo systemctl edit nm-gpclient      # [Service] Environment=GP_AUTH_TIMEOUT=900
sudo systemctl restart nm-gpclient
```

### Authentication works but the window stays open

Expected when the browser cannot be tracked - `no gpauth to watch` or
`browser exited on its own` in the log. It happens when the URL is handed to an
already running browser instance (common with `firefox` and `default`), because
the process the wrapper started exits immediately. Close the window yourself;
the VPN is unaffected.

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

The same wrapper handles every browser - pick one in the connection editor, or:

```bash
nmcli connection modify "My VPN" +vpn.data "browser=firefox"    # or chrome, chromium, edge, default
```

The service maps these names (and the old `/usr/bin/firefox`-style paths) to
`browser-wrapper` and passes the binary in `GP_BROWSER`, so every browser gets
the session-environment fixup that used to be Edge-only. A value that is not a
known browser - your own wrapper script - is passed to gpclient untouched.

| Value | Launched as |
|-------|-------------|
| `edge` (default) | `/usr/bin/microsoft-edge --app=<url>` in app mode |
| `chrome`, `chromium` | the Chrome/Chromium binary in app mode |
| `firefox` | `firefox --new-window <url>` |
| `default` | `xdg-open <url>`, i.e. your desktop's default browser |
| `/path/to/script` | executed as `script <url>`, no flags added |

Two caveats for non-Edge browsers:

- Firefox has no equivalent of Edge's `AutoLaunchProtocolsFromOrigins` policy,
  so the first authentication shows a "open GlobalProtect callback?" prompt that
  you have to confirm.
- Browsers that hand the URL to an already running instance exit immediately, so
  the wrapper cannot close the window afterwards.

## Installation Paths

```
/usr/libexec/gpclient/browser-wrapper   # the wrapper for every browser
/usr/libexec/gpclient/edge-wrapper      # shim that calls it with GP_BROWSER=edge
```

The shim exists because profiles created before this change store the
`edge-wrapper` path in `vpn.data browser`; they keep working unchanged.

## Environment Variables

| Variable | Meaning |
|----------|---------|
| `GP_BROWSER` | Browser to launch (friendly name or absolute path). Set by the service; autodetected when unset. |
| `GP_AUTH_TIMEOUT` | Seconds to wait for authentication before killing the window (default 300). |
