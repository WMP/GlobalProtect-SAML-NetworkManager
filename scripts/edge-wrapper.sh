#!/bin/bash
# Wrapper for Microsoft Edge to add required flags for gpauth (Wayland/KDE)
# Handles NetworkManager's ProtectHome=read-only sandbox

# Note: LOG_FILE will be set after REAL_UID is determined (security: per-user log file)
log() {
    if [ -n "$LOG_FILE" ]; then
        echo "[$(date '+%F %T')] $*" >> "$LOG_FILE"
    fi
}

log "called with args: $*"
log "EUID=$EUID USER=$USER SUDO_USER=${SUDO_USER:-} PWD=$PWD DISPLAY=${DISPLAY:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}"

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

# Set per-user log file (security: prevent symlink attack)
LOG_FILE="/tmp/edge-wrapper-$REAL_UID.log"
log "resolved real user: $REAL_USER (uid=$REAL_UID) home=$REAL_HOME"

# Detect Wayland socket (fallback to wayland-0)
WAYLAND_SOCK=$(find /run/user/"$REAL_UID" -maxdepth 1 -name "wayland-*" -type s 2>/dev/null | head -1)
if [ -n "$WAYLAND_SOCK" ]; then
    WAYLAND_DISPLAY=$(basename "$WAYLAND_SOCK")
    log "using wayland socket: $WAYLAND_SOCK"
else
    WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
    log "wayland socket not found, fallback WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
fi

# Detect DISPLAY from user's session (NM may have wrong value due to sandboxing)
detect_display() {
    # Try to get DISPLAY from user's running desktop session
    local session_display pid

    # Check KDE/Plasma
    pid=$(pgrep -u "$REAL_UID" -x plasmashell 2>/dev/null | head -1)
    if [ -n "$pid" ] && [ -f "/proc/$pid/environ" ]; then
        session_display=$(grep -z "^DISPLAY=" "/proc/$pid/environ" 2>/dev/null | tr -d '\0' | cut -d= -f2)
        [ -n "$session_display" ] && echo "$session_display" && return
    fi

    # Check GNOME
    pid=$(pgrep -u "$REAL_UID" -x gnome-shell 2>/dev/null | head -1)
    if [ -n "$pid" ] && [ -f "/proc/$pid/environ" ]; then
        session_display=$(grep -z "^DISPLAY=" "/proc/$pid/environ" 2>/dev/null | tr -d '\0' | cut -d= -f2)
        [ -n "$session_display" ] && echo "$session_display" && return
    fi

    # Check any X client
    pid=$(pgrep -u "$REAL_UID" -x dbus-daemon 2>/dev/null | head -1)
    if [ -n "$pid" ] && [ -f "/proc/$pid/environ" ]; then
        session_display=$(grep -z "^DISPLAY=" "/proc/$pid/environ" 2>/dev/null | tr -d '\0' | cut -d= -f2)
        [ -n "$session_display" ] && echo "$session_display" && return
    fi

    # Fallback
    echo "${DISPLAY:-:0}"
}

DETECTED_DISPLAY=$(detect_display)
if [ "$DETECTED_DISPLAY" != "${DISPLAY:-:0}" ]; then
    log "detected DISPLAY=$DETECTED_DISPLAY (was ${DISPLAY:-:0})"
    DISPLAY="$DETECTED_DISPLAY"
else
    DISPLAY=${DISPLAY:-:0}
fi

# Temp directory for Edge when home is read-only (NM's ProtectHome=read-only)
EDGE_TEMP_BASE="/tmp/edge-wrapper-$REAL_UID"
ALT_PROFILE_DIR="$EDGE_TEMP_BASE/profile"
ALT_HOME_DIR="$EDGE_TEMP_BASE/home"

# Check if real home is writable
home_is_writable() {
    touch "$REAL_HOME/.edge-wrapper-writecheck" 2>/dev/null && \
        rm -f "$REAL_HOME/.edge-wrapper-writecheck" 2>/dev/null
}

# Prepare directories
mkdir -p "$ALT_PROFILE_DIR" "$ALT_HOME_DIR/.config" 2>/dev/null

# Create Edge policy to auto-launch globalprotectcallback:// protocol
POLICY_DIR="$ALT_HOME_DIR/.config/microsoft-edge/policies/managed"
mkdir -p "$POLICY_DIR" 2>/dev/null
cat > "$POLICY_DIR/globalprotect.json" 2>/dev/null << 'EOF'
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

if home_is_writable; then
    # Normal case - use real home and profile
    PROFILE_DIR="$REAL_HOME/.config/microsoft-edge"
    EFFECTIVE_HOME="$REAL_HOME"
    log "home is writable; using main profile: $PROFILE_DIR"
else
    # Sandboxed (NM with ProtectHome=read-only) - use temp dirs
    PROFILE_DIR="$ALT_PROFILE_DIR"
    EFFECTIVE_HOME="$ALT_HOME_DIR"
    log "home is read-only (sandboxed); using temp HOME=$EFFECTIVE_HOME profile=$PROFILE_DIR"

    # Create minimal .config structure for Edge/Chromium
    mkdir -p "$EFFECTIVE_HOME/.config/microsoft-edge/Crash Reports" 2>/dev/null
    mkdir -p "$PROFILE_DIR/Crash Reports" 2>/dev/null
fi

ENV_VARS=(
    "DISPLAY=$DISPLAY"
    "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
    "XDG_RUNTIME_DIR=/run/user/$REAL_UID"
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$REAL_UID/bus"
    "HOME=$EFFECTIVE_HOME"
    "XDG_CONFIG_HOME=$EFFECTIVE_HOME/.config"
    "XDG_CACHE_HOME=$EDGE_TEMP_BASE/cache"
    "XDG_DATA_HOME=$EDGE_TEMP_BASE/data"
    "QT_QPA_PLATFORM=wayland"
)

EDGE_CMD="/usr/bin/microsoft-edge"
URL="$1"
EDGE_FLAGS=(
    "--ozone-platform=wayland"
    "--enable-features=UseOzonePlatform"
#    "--no-sandbox"
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
    # "--inprivate"
    "--app=$URL"
    "--window-size=896,964"
    "--user-data-dir=$PROFILE_DIR"
)

log "edge env: ${ENV_VARS[*]}"
log "edge flags: ${EDGE_FLAGS[*]}"

# Function to check if VPN authentication is complete
# Returns 0 if still waiting, 1 if complete
vpn_auth_pending() {
    # If tunnel interface exists, VPN is connected
    ip link show gpd0 &>/dev/null && return 1
    ip link show tun0 &>/dev/null && return 1
    # If gpauth process is gone, auth finished
    pgrep -x gpauth &>/dev/null || return 1
    return 0
}

# Function to run Edge and monitor for auth completion
run_edge_with_monitor() {
    # Start Edge in background
    env "${ENV_VARS[@]}" "$EDGE_CMD" "${EDGE_FLAGS[@]}" 2>>"$LOG_FILE" &
    EDGE_PID=$!
    log "started edge with PID=$EDGE_PID"

    # Wait for Edge to fully start
    sleep 5

    # Monitor loop - check every 3 seconds if auth is complete (max 5 minutes)
    MAX_WAIT=300  # 5 minutes timeout
    elapsed=0
    while kill -0 "$EDGE_PID" 2>/dev/null && [ $elapsed -lt $MAX_WAIT ]; do
        sleep 3
        elapsed=$((elapsed + 3))

        # Check if VPN tunnel is up or gpauth finished
        if ! vpn_auth_pending; then
            log "VPN auth complete (tunnel up or gpauth finished) - closing Edge"
            sleep 2
            kill "$EDGE_PID" 2>/dev/null
            wait "$EDGE_PID" 2>/dev/null
            log "edge terminated"
            exit 0
        fi
    done

    # Check if timeout reached
    if [ $elapsed -ge $MAX_WAIT ]; then
        log "WARNING: Auth timeout after ${MAX_WAIT}s, killing Edge"
        kill -9 "$EDGE_PID" 2>/dev/null
        wait "$EDGE_PID" 2>/dev/null
        exit 1
    fi

    log "edge exited on its own"
}

if [ "$EUID" -eq 0 ] && [ "$REAL_USER" != "root" ]; then
    log "running as root; dropping privileges to $REAL_USER via sudo"
    exec sudo -u "$REAL_USER" bash -c "$(declare -f vpn_auth_pending run_edge_with_monitor log); ENV_VARS=(${ENV_VARS[*]@Q}); EDGE_CMD='$EDGE_CMD'; EDGE_FLAGS=(${EDGE_FLAGS[*]@Q}); LOG_FILE='$LOG_FILE'; run_edge_with_monitor"
else
    log "running as EUID=$EUID (no sudo drop)"
    run_edge_with_monitor
fi
