#!/bin/bash
# Browser wrapper for gpauth's SAML authentication.
#
# gpauth launches "<browser> <url>" with almost no environment: NetworkManager
# starts the VPN service with a bare env and gpauth only overrides HOME/USER
# (see crates/gpapi/src/process/command_traits.rs). A browser started that way
# has no usable DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS
# and silently fails to open a window (issue #7). This wrapper reconstructs the
# session environment, works around NetworkManager's ProtectHome=read-only
# sandbox, and closes the browser once the authentication is done.
#
# Which browser to launch comes from $GP_BROWSER (set by nm-gpclient-service);
# without it we autodetect. $GP_AUTH_TIMEOUT overrides the 300 s safety timeout.

MAX_WAIT="${GP_AUTH_TIMEOUT:-300}"

# Note: LOG_FILE is set after REAL_UID is known (security: per-user log file)
log() {
    if [ -n "$LOG_FILE" ]; then
        echo "[$(date '+%F %T')] $*" >> "$LOG_FILE"
    fi
}

REAL_USER="${SUDO_USER:-$USER}"

# Validate username to prevent command injection (security)
if ! [[ "$REAL_USER" =~ ^[a-z_][a-z0-9_-]*\$?$ ]]; then
    log "ERROR: Invalid username: $REAL_USER"
    exit 1
fi

REAL_UID=$(id -u "$REAL_USER" 2>/dev/null)
if [ -z "$REAL_UID" ]; then
    log "ERROR: Cannot get UID for user: $REAL_USER"
    exit 1
fi

REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

# Per-user log file (security: prevent symlink attack). The name is kept from
# the old edge-only wrapper so existing troubleshooting docs stay valid.
LOG_FILE="/tmp/edge-wrapper-$REAL_UID.log"
log "called with args: $*"
log "EUID=$EUID USER=$USER SUDO_USER=${SUDO_USER:-} GP_BROWSER=${GP_BROWSER:-unset} GP_AUTH_TIMEOUT=$MAX_WAIT"
log "resolved real user: $REAL_USER (uid=$REAL_UID) home=$REAL_HOME"

# --- Pick the browser binary ------------------------------------------------

resolve_browser() {
    local candidate="${GP_BROWSER:-}"

    # Friendly names may arrive instead of a path
    case "$candidate" in
        edge|msedge|microsoft-edge) candidate=/usr/bin/microsoft-edge ;;
        chrome|google-chrome)       candidate=$(command -v google-chrome-stable || command -v google-chrome) ;;
        chromium)                   candidate=$(command -v chromium || command -v chromium-browser) ;;
        firefox)                    candidate=$(command -v firefox) ;;
        default|xdg-open)           candidate=$(command -v xdg-open) ;;
    esac

    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        echo "$candidate"
        return 0
    fi

    local fallback
    for fallback in /usr/bin/microsoft-edge google-chrome-stable google-chrome chromium chromium-browser firefox xdg-open; do
        local path
        path=$(command -v "$fallback" 2>/dev/null)
        if [ -n "$path" ]; then
            echo "$path"
            return 0
        fi
    done

    return 1
}

BROWSER_BIN=$(resolve_browser)
if [ -z "$BROWSER_BIN" ]; then
    log "ERROR: no usable browser found (GP_BROWSER=${GP_BROWSER:-unset})"
    exit 1
fi

case "$(basename "$BROWSER_BIN")" in
    microsoft-edge*)                     FAMILY=edge ;;
    google-chrome*|chrome|chromium*)     FAMILY=chromium ;;
    firefox*)                            FAMILY=firefox ;;
    *)                                   FAMILY=other ;;
esac
log "browser: $BROWSER_BIN (family=$FAMILY)"

# --- Reconstruct the user's session environment -----------------------------

# Detect Wayland socket (fallback to wayland-0)
WAYLAND_SOCK=$(find /run/user/"$REAL_UID" -maxdepth 1 -name "wayland-*" -type s 2>/dev/null | head -1)
if [ -n "$WAYLAND_SOCK" ]; then
    WAYLAND_DISPLAY=$(basename "$WAYLAND_SOCK")
    log "using wayland socket: $WAYLAND_SOCK"
else
    WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}
    log "no wayland socket found for uid $REAL_UID"
fi

# Read a variable from the environment of the user's session processes.
# NetworkManager's own environment is useless here (sandboxed, no session).
session_env_var() {
    local var="$1" proc pid value
    for proc in plasmashell gnome-shell gnome-session-binary kwin_wayland xfce4-session cinnamon-session mate-session sway; do
        pid=$(pgrep -u "$REAL_UID" -x "$proc" 2>/dev/null | head -1)
        if [ -n "$pid" ] && [ -r "/proc/$pid/environ" ]; then
            value=$(grep -z "^$var=" "/proc/$pid/environ" 2>/dev/null | tr -d '\0' | cut -d= -f2-)
            if [ -n "$value" ]; then
                echo "$value"
                return 0
            fi
        fi
    done
    return 1
}

DETECTED_DISPLAY=$(session_env_var DISPLAY)
if [ -n "$DETECTED_DISPLAY" ]; then
    if [ "$DETECTED_DISPLAY" != "${DISPLAY:-}" ]; then
        log "detected DISPLAY=$DETECTED_DISPLAY (was ${DISPLAY:-unset})"
    fi
    DISPLAY="$DETECTED_DISPLAY"
fi

DETECTED_XAUTH=$(session_env_var XAUTHORITY)
if [ -n "$DETECTED_XAUTH" ]; then
    XAUTHORITY="$DETECTED_XAUTH"
elif [ -z "${XAUTHORITY:-}" ] && [ -f "$REAL_HOME/.Xauthority" ]; then
    XAUTHORITY="$REAL_HOME/.Xauthority"
fi

if [ -z "$WAYLAND_DISPLAY" ]; then
    WAYLAND_DISPLAY=$(session_env_var WAYLAND_DISPLAY)
fi

SESSION_TYPE=$(session_env_var XDG_SESSION_TYPE)
if [ -z "$SESSION_TYPE" ]; then
    if [ -n "$WAYLAND_DISPLAY" ]; then SESSION_TYPE=wayland; else SESSION_TYPE=x11; fi
fi
log "session type: $SESSION_TYPE (DISPLAY=${DISPLAY:-unset} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset})"

# --- Profile / HOME workaround for ProtectHome=read-only --------------------

TEMP_BASE="/tmp/edge-wrapper-$REAL_UID"
ALT_HOME_DIR="$TEMP_BASE/home"
case "$FAMILY" in
    firefox) ALT_PROFILE_DIR="$TEMP_BASE/profile-firefox" ;;
    *)       ALT_PROFILE_DIR="$TEMP_BASE/profile" ;;
esac

home_is_writable() {
    touch "$REAL_HOME/.gp-browser-writecheck" 2>/dev/null && \
        rm -f "$REAL_HOME/.gp-browser-writecheck" 2>/dev/null
}

mkdir -p "$ALT_PROFILE_DIR" "$ALT_HOME_DIR/.config" 2>/dev/null

if home_is_writable; then
    HOME_IS_RO=0
    EFFECTIVE_HOME="$REAL_HOME"
    log "home is writable; using the user's own browser profile"
else
    HOME_IS_RO=1
    EFFECTIVE_HOME="$ALT_HOME_DIR"
    log "home is read-only (sandboxed); using temp HOME=$EFFECTIVE_HOME profile=$ALT_PROFILE_DIR"
fi

# Chromium-family policy that auto-launches the globalprotectcallback://
# handler. Note that the actual suppression of the "open external app?" dialog
# comes from the --disable-features=ExternalProtocolDialog flag below; this
# policy file is kept for browsers/setups that honour a per-HOME policy dir.
write_chromium_policy() {
    local policy_name policy_dir
    case "$FAMILY" in
        edge)     policy_name=microsoft-edge ;;
        chromium) policy_name=$(basename "$BROWSER_BIN" | grep -q chromium && echo chromium || echo google-chrome) ;;
        *)        return 0 ;;
    esac

    policy_dir="$EFFECTIVE_HOME/.config/$policy_name/policies/managed"
    mkdir -p "$policy_dir" 2>/dev/null || return 0
    cat > "$policy_dir/globalprotect.json" 2>/dev/null << 'EOF'
{
    "AutoLaunchProtocolsFromOrigins": [
        {
            "allowed_origins": ["*"],
            "protocol": "globalprotectcallback"
        }
    ],
    "ExternalProtocolDialogShowAlwaysOpenCheckbox": true
}
EOF
}

# --- Environment and flags for the browser ----------------------------------

ENV_VARS=(
    "XDG_RUNTIME_DIR=/run/user/$REAL_UID"
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$REAL_UID/bus"
    "HOME=$EFFECTIVE_HOME"
    "XDG_CONFIG_HOME=$EFFECTIVE_HOME/.config"
    "XDG_CACHE_HOME=$TEMP_BASE/cache"
    "XDG_DATA_HOME=$TEMP_BASE/data"
    "XDG_SESSION_TYPE=$SESSION_TYPE"
)
[ -n "${DISPLAY:-}" ] && ENV_VARS+=("DISPLAY=$DISPLAY")
[ -n "${XAUTHORITY:-}" ] && ENV_VARS+=("XAUTHORITY=$XAUTHORITY")
[ -n "$WAYLAND_DISPLAY" ] && ENV_VARS+=("WAYLAND_DISPLAY=$WAYLAND_DISPLAY")
[ "$SESSION_TYPE" = "wayland" ] && ENV_VARS+=("QT_QPA_PLATFORM=wayland")

URL="$1"
if [ -z "$URL" ]; then
    log "ERROR: no URL given"
    exit 1
fi

BROWSER_FLAGS=()
case "$FAMILY" in
    edge|chromium)
        write_chromium_policy
        if [ "$HOME_IS_RO" -eq 1 ]; then
            PROFILE_DIR="$ALT_PROFILE_DIR"
            mkdir -p "$PROFILE_DIR/Crash Reports" "$EFFECTIVE_HOME/.config/Crash Reports" 2>/dev/null
        else
            # Reuse the user's real profile so existing SSO cookies apply
            case "$(basename "$BROWSER_BIN")" in
                microsoft-edge*) PROFILE_DIR="$REAL_HOME/.config/microsoft-edge" ;;
                chromium*)       PROFILE_DIR="$REAL_HOME/.config/chromium" ;;
                *)               PROFILE_DIR="$REAL_HOME/.config/google-chrome" ;;
            esac
        fi
        BROWSER_FLAGS=(
            "--no-first-run"
            "--no-default-browser-check"
            "--disable-crash-reporter"
            "--disable-breakpad"
            "--disable-sync"
            "--disable-extensions"
            "--disable-plugins"
            "--disable-background-networking"
            "--disable-component-update"
            "--disable-features=msEdgeSyncService,TranslateUI,EdgeCollections,msEdgeSweeperMode,ExternalProtocolDialog"
            "--app=$URL"
            "--window-size=896,964"
            "--user-data-dir=$PROFILE_DIR"
        )
        if [ "$SESSION_TYPE" = "wayland" ]; then
            BROWSER_FLAGS=("--ozone-platform=wayland" "--enable-features=UseOzonePlatform" "${BROWSER_FLAGS[@]}")
        fi
        ;;
    firefox)
        if [ "$HOME_IS_RO" -eq 1 ]; then
            BROWSER_FLAGS=("--profile" "$ALT_PROFILE_DIR" "--new-window" "$URL")
        else
            BROWSER_FLAGS=("--new-window" "$URL")
        fi
        ;;
    *)
        # Unknown binary (xdg-open, a user-supplied script): pass the URL only
        BROWSER_FLAGS=("$URL")
        ;;
esac

# --- gpauth is the authoritative "authentication finished" signal -----------
#
# gpauth exits as soon as it receives the callback data, so its death means the
# user is done. It cannot be found via $PPID: open::with_detached() double-forks
# and calls setsid(), so this wrapper's parent is init, not gpauth.
GPAUTH_PID=$(pgrep -n -x -u "$REAL_UID" gpauth 2>/dev/null | head -1)
if [ -n "$GPAUTH_PID" ]; then
    log "tracking gpauth PID $GPAUTH_PID"
else
    log "WARNING: no gpauth process found - the browser will not be closed automatically"
fi

log "env: ${ENV_VARS[*]}"
log "cmd: $BROWSER_BIN ${BROWSER_FLAGS[*]}"

run_browser_with_monitor() {
    env "${ENV_VARS[@]}" "$BROWSER_BIN" "${BROWSER_FLAGS[@]}" 2>>"$LOG_FILE" &
    BROWSER_PID=$!
    log "started browser with PID=$BROWSER_PID"

    # Give the process a moment to fail loudly (bad flags, no display)
    sleep 1

    if [ -z "$GPAUTH_PID" ]; then
        wait "$BROWSER_PID" 2>/dev/null
        log "done: browser exited (no gpauth to watch, nothing was killed)"
        return 0
    fi

    # Stay alive for as long as gpauth does: that is how long the
    # authentication window can appear, so it is also how long the window rule
    # has to stay loaded - even when the process we started handed the URL to an
    # already running browser and exited straight away.
    local elapsed=0 handed_over=0
    while kill -0 "$GPAUTH_PID" 2>/dev/null; do
        if [ "$handed_over" -eq 0 ] && ! kill -0 "$BROWSER_PID" 2>/dev/null; then
            handed_over=1
            log "the browser we started exited - the URL went to a running instance"
        fi

        if [ "$elapsed" -ge "$MAX_WAIT" ]; then
            log "done: timeout after ${MAX_WAIT}s with gpauth still running"
            if [ "$handed_over" -eq 0 ]; then
                log "killing the browser we started"
                kill -9 "$BROWSER_PID" 2>/dev/null
                wait "$BROWSER_PID" 2>/dev/null
            fi
            return 1
        fi

        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ "$handed_over" -eq 1 ]; then
        log "done: gpauth ($GPAUTH_PID) exited - authentication finished (window belongs to a running browser, not closing it)"
        return 0
    fi

    log "done: gpauth ($GPAUTH_PID) exited - authentication finished, closing browser"
    sleep 2
    kill "$BROWSER_PID" 2>/dev/null
    wait "$BROWSER_PID" 2>/dev/null
    return 0
}

if [ "$EUID" -eq 0 ] && [ "$REAL_USER" != "root" ]; then
    log "running as root; dropping privileges to $REAL_USER via sudo"
    exec sudo -u "$REAL_USER" bash -c "$(declare -f log run_browser_with_monitor); \
ENV_VARS=(${ENV_VARS[*]@Q}); BROWSER_BIN=${BROWSER_BIN@Q}; \
BROWSER_FLAGS=(${BROWSER_FLAGS[*]@Q}); LOG_FILE=${LOG_FILE@Q}; \
GPAUTH_PID=${GPAUTH_PID@Q}; MAX_WAIT=${MAX_WAIT@Q}; run_browser_with_monitor"
else
    log "running as EUID=$EUID (no privilege drop needed)"
    run_browser_with_monitor
fi
