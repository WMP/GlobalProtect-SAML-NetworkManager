#!/bin/bash
# Helpers for the demo scenarios (source this file). Everything runs as the
# desktop user on DISPLAY :1. No `set -e` here: a failed helper returns 1 and
# the scenario decides what to do.
#
#   wait_for_window <name-regex> [timeout]   print the window id, 1 on timeout
#   window_geometry <id>                     print "X Y WIDTH HEIGHT"
#   smooth_move <x> <y> [duration]           human-like mouse movement
#   click <x> <y>                            move smoothly, then left click
#   type_slow <text>                         type with a human delay
#   key <xdotool key names...>               press keys
#   screenshot <file>                        one frame of the screen as PNG
#   pause <seconds>

export DISPLAY="${DISPLAY:-:1}"
DEMO_OUT="${DEMO_OUT:-/out}"

pause() { sleep "${1:-1}"; }

wait_for_window() {
    local regex="$1" timeout="${2:-30}" end id
    end=$((SECONDS + timeout))
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
    local x="$1" y="$2" duration="${3:-0.8}" steps i cx cy
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

click() {
    smooth_move "$1" "$2" "${3:-0.8}"
    sleep 0.15
    xdotool click 1
}

type_slow() { xdotool type --delay $((60 + RANDOM % 31)) -- "$1"; }

key() { xdotool key --delay 80 "$@"; }

screenshot() {
    ffmpeg -nostdin -y -loglevel error -f x11grab -video_size 1920x1080 -i "$DISPLAY" \
        -frames:v 1 "$1"
}
