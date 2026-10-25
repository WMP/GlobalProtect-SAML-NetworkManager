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

The service does not pass the session environment to gpclient. gpclient starts
gpauth as the desktop user (it finds the user by `SUDO_UID`) and rebuilds the
session environment itself from the user's own processes, with defaults for
`XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` (upstream
`crates/gpapi/src/process/desktop_session_env.rs`). Passing only `DISPLAY=:0`
was why `browser=firefox` opened no window at all on Wayland
([#7](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/7)); gpauth now
gets the full environment from upstream.

### 2. Sandbox Workaround (ProtectHome)

NetworkManager uses systemd's `ProtectHome=read-only` for security, making the user's home directory read-only. Edge needs writable directories for:
- User profile
- Cache
- Crash reports

The wrapper creates temporary directories in a private base directory:
```
<base>/
├── profile/     # Edge user data (profile-firefox/ for Firefox)
├── home/        # Fake HOME
├── cache/       # XDG_CACHE_HOME
├── data/        # XDG_DATA_HOME
└── wrapper.log  # log file (mode 600)
```

`<base>` is `/tmp/edge-wrapper-$UID`. The wrapper creates it with mode 700 and
uses it only if it is a real directory, owned by the user, with mode 700. A
directory from an older wrapper that is owned by the user (mode 755) gets
`chmod 700` and is then used: only its owner could have written in it. The older
wrapper ran `mkdir` as root, so on many systems the leftover directory is owned
by root. The wrapper cannot fix it: it logs "left by an older version of the
wrapper (owned by root)", uses a new directory, and you can remove the old one
with `sudo rm -rf /tmp/edge-wrapper-$UID`. If someone else created it first (a
symlink, a directory with other owner, a file), the wrapper does not use it: it
logs a "planted by another user?" warning and takes a new directory from
`mktemp` (`/tmp/edge-wrapper-$UID.XXXXXXXX`, mode 700). After the run, the
wrapper empties such a `mktemp` directory but keeps `wrapper.log`, so you can
read the log of a failed login. If a file cannot be removed (the browser may
still write for a moment), it tries 3 times, 1 second apart. When the wrapper
runs as root, it creates the directories and checks that the home is writable as
the desktop user (through `sudo -u "#$UID"`), so they never belong to root and
root never follows a symlink.
The Chromium policy file and the `Crash Reports` directories are also written
after the privilege drop, as the desktop user.

#### Security Considerations

**Why temporary profile?**
- NetworkManager's `ProtectHome=read-only` is a security feature that prevents VPN services from reading/writing user's home directory
- Edge cannot function without a writable profile directory
- The wrapper uses a separate profile in a private directory (see above), not your normal one. It is reused until you delete it

**Implications:**
- Your main Edge profile (`~/.config/microsoft-edge`) with saved passwords, cookies, and browsing history is **not used**
- The browser profile does not contain your normal browsing data
- Intune/Entra authentication still works because it uses device certificates (stored in system keychain, not browser profile)

**Security tradeoffs:**
- `/tmp` is shared between all users, so the wrapper never trusts a directory there that it did not create with mode 700 for the right user
- All directories have user-only permissions (mode 700)
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

Debug logs are written to the private base directory (see above):
```
<base>/wrapper.log        # /tmp/edge-wrapper-$UID/wrapper.log (or the mktemp directory)
```
The file has mode 600 and is created by the desktop user, never by root. The
messages from before the base directory exists (user lookup, browser and session
detection) go to stderr and syslog (`logger -t gpclient-browser-wrapper`). As
root, the wrapper itself never opens the log file: all its own messages go to
syslog, and only the part that runs as the desktop user (the browser start and
stop) writes `wrapper.log`. A log file in
the shared `/tmp` is not used, because another user could replace it with a
symlink or read it.

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
# Messages of the part that runs as the desktop user (browser start and stop)
cat /tmp/edge-wrapper-$(id -u)/wrapper.log   # or <base>/wrapper.log
# Messages from before the base directory exists, and all messages written as
# root (the normal case with NetworkManager): in the journal, not in wrapper.log
journalctl -t gpclient-browser-wrapper

# Test manually (any browser, without a VPN connection involved)
GP_BROWSER=firefox /usr/libexec/gpclient/browser-wrapper "https://example.com"
```
With no gpauth running the window must open and stay open - nothing should
close it.

### The window closes before I finish logging in

Check the reason in the log:
```bash
grep "done:" /tmp/edge-wrapper-$(id -u)/wrapper.log
# if wrapper.log has no "done:" line, look in the journal too
journalctl -t gpclient-browser-wrapper | grep "done:"
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
# <base> is /tmp/edge-wrapper-$(id -u)
cat <base>/home/.config/microsoft-edge/policies/managed/globalprotect.json
```

### Wrong display / window doesn't appear

Check detected display:
```bash
# The wrapper writes this message before the base directory exists (and as root),
# so it is in the journal, not in wrapper.log
journalctl -t gpclient-browser-wrapper | grep "detected DISPLAY"
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
the session-environment fixup that used to be Edge-only. Your own wrapper
script can be given as an absolute path (for example `/opt/me/my-wrapper`) or as
a plain command name (for example `brave-browser` or `opera`: letters, digits,
`.`, `_`, `+` and `-`, not starting with `-`). Both are passed to gpclient
untouched. Any other unknown value (a relative path, a value that starts with
`-`, a value with spaces) is logged and falls back to the desktop's default
browser.

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
