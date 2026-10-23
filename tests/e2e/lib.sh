#!/bin/bash
# Helpers for the demo scenarios (source this file). Everything runs as the
# desktop user on DISPLAY :1. No `set -e` here: a failed helper returns 1 and
# the scenario decides what to do.
#
#   wait_for_window <name-regex> [timeout]   print the window id, 1 on timeout
#   window_geometry <id>                     print "X Y WIDTH HEIGHT"
#   smooth_move <x> <y> [duration]           human-like mouse movement
#   click <x> <y>                            move smoothly, then left click
#   click_until_window <x> <y> <regex> [s]   click, retry when no such window opens
#   type_slow <text> [delay-ms]              type with a human delay (default 70)
#   key <xdotool key names...>               press keys
#   screenshot <file>                        one frame of the screen as PNG
#   pause <seconds>
#   fail <message>                           stop the scenario with exit 1
#   win_box <id>                             set WX WY WW WH (frame with shadow)
#   wait_window_gone <name-regex> [timeout]  1 on timeout
#   wait_vpn <name> <state> [timeout]        wait for an nmcli state, 1 on timeout
#   shot <name>                              screenshot "$DEMO_OUT/<name>.png"
#   snapshot <name>                          save the network state to "$DEMO_OUT/state-<name>.txt"
#
# A scenario fails loudly: use `|| fail "message"` after every wait.

export DISPLAY="${DISPLAY:-:1}"
DEMO_OUT="${DEMO_OUT:-/out}"
# Size of the screen: set by run.sh and passed in by start.sh
: "${SCREEN_W:?}" "${SCREEN_H:?}"

pause() { sleep "${1:-1}"; }

fail() {
    echo "SCENARIO FAILED: $*" >&2
    screenshot "$DEMO_OUT/failure.png" 2> /dev/null
    exit 1
}

shot() { screenshot "$DEMO_OUT/$1.png"; }

# The state a check can read: connections, addresses, routes and DNS
snapshot() {
    {
        echo "## nmcli connection show --active"
        nmcli -t -f NAME,TYPE,STATE,DEVICE connection show --active
        echo "## ip -4 addr show gpd0"
        ip -4 addr show gpd0 2>&1
        echo "## ip route"
        ip route
        echo "## ip route show default"
        ip route show default
        echo "## ip route get 9.9.9.9"
        ip route get 9.9.9.9 2>&1
        echo "## ip route get 10.20.0.10"
        ip route get 10.20.0.10 2>&1
        echo "## resolvectl dns"
        resolvectl dns 2>&1
        echo "## resolvectl domain"
        resolvectl domain 2>&1
        echo "## resolvectl status gpd0"
        resolvectl status gpd0 2>&1
        echo "## processes"
        ps -eo user:12,pid,comm,args --no-headers | awk '$3 ~ /^(msedge|microsoft-edge.*|gpclient|gpauth)$/' | cut -c1-200
        echo "## windows"
        xdotool search --onlyvisible --name "." 2> /dev/null | while read -r w; do xdotool getwindowname "$w"; done
    } > "$DEMO_OUT/state-$1.txt" 2>&1
}

win_box() {
    local out
    out=$(xwininfo -id "$1" 2> /dev/null) || return 1
    WX=$(awk '/Absolute upper-left X/ { print $4 }' <<< "$out")
    WY=$(awk '/Absolute upper-left Y/ { print $4 }' <<< "$out")
    WW=$(awk '/Width:/ { print $2 }' <<< "$out")
    WH=$(awk '/Height:/ { print $2 }' <<< "$out")
    echo "window $1: $WX $WY $WW $WH" >&2
}

wait_window_gone() {
    local regex="$1" timeout="${2:-30}" end
    end=$((SECONDS + $(scaled "$timeout")))
    while [ "$SECONDS" -lt "$end" ]; do
        [ -z "$(xdotool search --onlyvisible --name "$regex" 2> /dev/null)" ] && return 0
        sleep 0.5
    done
    return 1
}

wait_vpn() {
    local name="$1" state="$2" timeout="${3:-60}" end
    end=$((SECONDS + $(scaled "$timeout")))
    while [ "$SECONDS" -lt "$end" ]; do
        nmcli -t -f NAME,STATE connection show --active 2> /dev/null | grep -qx "$name:$state" && return 0
        sleep 0.5
    done
    return 1
}

# All waits multiply their timeout by DEMO_TIMEOUT_SCALE (default 1)
scaled() { echo $(($1 * ${DEMO_TIMEOUT_SCALE:-1})); }

wait_for_window() {
    local regex="$1" timeout="${2:-30}" end id
    end=$((SECONDS + $(scaled "$timeout")))
    while [ "$SECONDS" -lt "$end" ]; do
        id=$(xdotool search --onlyvisible --name "$regex" 2>/dev/null | tail -n 1)
        if [ -n "$id" ]; then
            echo "$id"
            return 0
        fi
        sleep 0.5
    done
    return 1
}

window_geometry() {
    local X Y WIDTH HEIGHT
    eval "$(xdotool getwindowgeometry --shell "$1" 2>/dev/null)" || return 1
    echo "$X $Y $WIDTH $HEIGHT"
}

# Moves along a smoothstep curve in steps of about 20 ms
smooth_move() {
    local x="$1" y="$2" duration="${3:-0.55}" steps i cx cy
    local X Y
    eval "$(xdotool getmouselocation --shell 2>/dev/null)"
    cx="${X:-0}"
    cy="${Y:-0}"
    steps=$(awk -v d="$duration" 'BEGIN { n = int(d * 50); print (n < 1 ? 1 : n) }')
    for i in $(seq 1 "$steps"); do
        awk -v i="$i" -v n="$steps" -v x0="$cx" -v y0="$cy" -v x1="$x" -v y1="$y" 'BEGIN {
            t = i / n; s = t * t * (3 - 2 * t)
            printf "%d %d\n", x0 + (x1 - x0) * s, y0 + (y1 - y0) * s }' \
            | { read -r px py; xdotool mousemove "$px" "$py"; }
        sleep 0.02
    done
}

# Clicks and waits for a window; clicks again (up to 3 times) when none opens,
# because a click on a popup that is still being drawn can be lost:
# click_until_window <x> <y> <name-regex> [seconds per try]
click_until_window() {
    local try
    for try in 1 2 3; do
        click "$1" "$2"
        wait_for_window "$3" "${4:-8}" > /dev/null && return 0
    done
    return 1
}

click() {
    smooth_move "$1" "$2" "${3:-0.55}"
    sleep 0.15
    xdotool click 1
}

type_slow() { xdotool type --delay "${2:-70}" -- "$1"; }

key() { xdotool key --delay 80 "$@"; }

screenshot() {
    ffmpeg -nostdin -y -loglevel error -f x11grab -video_size "${SCREEN_W}x${SCREEN_H}" -i "$DISPLAY" \
        -frames:v 1 "$1"
}

# Grows and moves a window in small steps: animate_window <id> <w> <h> <x> <y>
animate_window() {
    local id="$1" w="$2" h="$3" x="$4" y="$5" i n=8
    win_box "$id" || return 1
    local w0=$WW h0=$WH x0=$WX y0=$WY
    for i in $(seq 1 "$n"); do
        xdotool windowsize "$id" $((w0 + (w - w0) * i / n)) $((h0 + (h - h0) * i / n))
        xdotool windowmove "$id" $((x0 + (x - x0) * i / n)) $((y0 + (y - y0) * i / n))
        sleep 0.04
    done
}

EDGE_COUNT=0

# The browser wrapper starts Edge with --window-size=896,964, which is taller
# than the screen. Resize it to fit between the top bar and the bottom edge
# (the panel of KDE) and center it: edge_fit <window id>. KWin keeps the window
# inside the work area whatever the size. The scenario sets EDGE_LEFT (width of the dock, 0 without) and EDGE_TOP (top bar height).
EDGE_W=896
EDGE_H=$((SCREEN_H - 100))
edge_fit() {
    local x y
    x=$(((SCREEN_W + ${EDGE_LEFT:-0} - EDGE_W) / 2))
    y=${EDGE_TOP:-0}
    xdotool windowsize "$1" "$EDGE_W" "$EDGE_H"
    xdotool windowmove "$1" "$x" "$y"
}

# The Edge sign-in of the demo (the same in both desktops). Set EDGE_DX and
# EDGE_DY first: the offset of the page content from the window box (win_box).
# Types the email and password, waits for the number-match page, clicks "Yes"
# on "Stay signed in?" and waits until the browser closes itself.
edge_signin() {
    local win
    # The window opens on the portal page ("GlobalProtect Portal"); Edge places
    # it where it likes, so fit it to the screen right away
    win=$(wait_for_window "Sign in|GlobalProtect Portal" 60) || fail "the Edge sign-in window did not open"
    edge_fit "$win"
    wait_for_window "Sign in" 30 > /dev/null || fail "the sign-in page did not load"
    pause 1.0
    win_box "$win" || fail "cannot read the Edge window geometry"
    echo "Edge window box: $WX $WY $WW $WH"
    shot step-edge-1-signin
    snapshot "edge-open-$((++EDGE_COUNT))"
    type_slow "alex.morgan@contoso.example"
    pause 0.6
    key Return
    pause 0.8
    shot step-edge-2-password
    type_slow "correct-horse-battery"
    pause 0.6
    key Return
    pause 0.8
    shot step-edge-3-approve
    # The approve page moves on by itself after 4.5 s
    pause 4.5
    pause 0.8
    shot step-edge-4-stay
    click $((WX + EDGE_DX + 569)) $((WY + EDGE_DY + 415)) 0.6
    pause 1.0
    shot step-edge-5-done
    wait_window_gone "Sign in|GlobalProtect Portal" 30 || fail "the Edge window did not close by itself"
}

# Marks the start of the part that goes into the GIF: record.sh cuts what
# comes before (setup that must not be shown). Writes "$DEMO_OUT/trim-start".
mark_start() {
    awk -v now="$(date +%s.%N)" -v start="$(cat /tmp/recording-start 2> /dev/null || echo 0)" \
        'BEGIN { d = now - start; print (start == 0 || d < 0 ? 0 : d) }' > "$DEMO_OUT/trim-start"
}

# The sign-in when the sign-in cookie exists: the page picks the account and
# finishes by itself. Waits until the window is gone.
edge_sso_auto() {
    local win
    win=$(wait_for_window "Sign in|GlobalProtect Portal" 60) || fail "the Edge sign-in window did not open"
    edge_fit "$win"
    shot step-edge-auto
    snapshot "edge-open-$((++EDGE_COUNT))"
    wait_window_gone "Sign in|GlobalProtect Portal" 30 || fail "the Edge window did not close by itself"
}

# Marks the end of the part that goes into the GIF; what the scenario does after
# this (disconnecting for the checks) is cut. Writes "$DEMO_OUT/trim-end".
mark_end() {
    awk -v now="$(date +%s.%N)" -v start="$(cat /tmp/recording-start 2> /dev/null || echo 0)" \
        'BEGIN { print now - start }' > "$DEMO_OUT/trim-end"
}

# Disconnects the VPN and waits until it is gone: disconnect_vpn <name>
disconnect_vpn() {
    nmcli connection down "$1" > /dev/null 2>&1
    for _ in $(seq $(scaled 40)); do
        nmcli -t -f NAME connection show --active | grep -qx "$1" || return 0
        sleep 0.5
    done
    return 1
}
