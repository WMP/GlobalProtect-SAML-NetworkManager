#!/bin/bash
# Container entrypoint of the end-to-end tests (runs as root).
# Usage: start.sh <gnome|kde>
#
# Starts udev, the system D-Bus, systemd-resolved, NetworkManager with a wired
# connection to a "home router" (a network namespace), the fictional sign-in
# site and an X11 desktop session, then records the screen while the scenario
# runs and checks the result.
#
# Environment: E2E_SCENARIO (name of /opt/gp-demo/scenarios/scenario-<name>.sh,
# default: the desktop), E2E_SCENARIO_FILE (a file to run instead),
# GP_DEMO_AUTH_TIMEOUT (seconds the fake gpauth waits for the sign-in).
# Output goes to /out: raw.mp4, logs, screenshots, state-*.txt, results.json.
# The exit code is not zero if the scenario or a check fails.
set -u

DE="${1:?usage: $0 <gnome|kde>}"
OUT=/out
DEMO_UID=1000
mkdir -p "$OUT" /run/gp-demo
chmod a+rwx "$OUT"
[ -n "${GP_DEMO_AUTH_TIMEOUT:-}" ] && echo "$GP_DEMO_AUTH_TIMEOUT" > /run/gp-demo/auth-timeout
LOG="$OUT/start.log"
log() { echo "$(date +%T) $*" | tee -a "$LOG"; }

# Docker writes /etc/hosts at run time, so the fictional hosts are added here
echo "127.0.0.1 vpn.contoso.example login.contoso.example" >> /etc/hosts

# gnome-shell takes /run/systemd/seats for a sign of logind (there is none);
# systemd-resolved needs /run/systemd/resolve
rm -rf /run/systemd/seats /run/systemd/sessions /run/systemd/users
mkdir -p /run/systemd/resolve
chown systemd-resolve:systemd-resolve /run/systemd/resolve

log "udev"
/usr/lib/systemd/systemd-udevd --daemon
udevadm trigger --action=add
udevadm settle

log "system D-Bus"
mkdir -p /run/dbus
dbus-daemon --system --fork

log "systemd-resolved"
# Docker bind-mounts /etc/resolv.conf; use the stub of systemd-resolved as Ubuntu does
umount /etc/resolv.conf 2> /dev/null
ln -sf ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
/usr/lib/systemd/systemd-resolved > "$OUT/systemd-resolved.log" 2>&1 &
for _ in $(seq 40); do resolvectl status > /dev/null 2>&1 && break; sleep 0.25; done

log "network"
ip link add enp0s31f6 type veth peer name ethpeer
ip link set ethpeer up
udevadm settle
NetworkManager --no-daemon > "$OUT/NetworkManager.log" 2>&1 &
for _ in $(seq 60); do nmcli -t -f RUNNING general 2>/dev/null | grep -q running && break; sleep 0.5; done
nmcli connection add type ethernet ifname enp0s31f6 con-name "Wired connection 1" \
    connection.autoconnect yes \
    ipv4.method manual ipv4.addresses 192.168.1.20/24 ipv4.gateway 192.168.1.1 ipv4.dns 192.168.1.1 \
    ipv6.method disabled > /dev/null
nmcli connection up "Wired connection 1" > /dev/null
# The other end of the cable is the home router, in its own network namespace
# (NetworkManager keeps managing the card after the peer has moved)
ip netns add router
ip link set ethpeer netns router
ip -n router addr add 192.168.1.1/24 dev ethpeer
ip -n router link set ethpeer up
ip -n router link set lo up
for _ in $(seq 40); do
    nmcli -t -f NAME,STATE connection show --active | grep -qx "Wired connection 1:activated" && break
    sleep 0.5
done

log "sign-in site"
python3 /opt/gp-demo/sso-server.py --cert /opt/gp-demo/certs/server.crt \
    --key /opt/gp-demo/certs/server.key --www /opt/gp-demo/www > "$OUT/sso-server.log" 2>&1 &

log "display and desktop ($DE)"
install -d -o demo -g demo -m 700 /run/user/$DEMO_UID
as_demo() { runuser -u demo -- env HOME=/home/demo USER=demo LOGNAME=demo DISPLAY=:1 \
    XDG_RUNTIME_DIR=/run/user/$DEMO_UID PATH="$PATH" "$@"; }
as_demo Xvfb :1 -screen 0 1920x1080x24 +extension GLX > "$OUT/xvfb.log" 2>&1 &
for _ in $(seq 60); do [ -S /tmp/.X11-unix/X1 ] && as_demo xdpyinfo > /dev/null 2>&1 && break; sleep 0.5; done
as_demo /opt/gp-demo/session/desktop.sh "$DE" > "$OUT/desktop.log" 2>&1 &

case "$DE" in
gnome) shell=gnome-shell ;;
kde) shell=plasmashell ;;
esac
for _ in $(seq 240); do pgrep -u $DEMO_UID -x "$shell" > /dev/null && break; sleep 0.5; done
if [ "$DE" = kde ]; then
    # The panel is a window of plasmashell
    for _ in $(seq 120); do [ -n "$(as_demo xdotool search --class plasmashell 2>/dev/null)" ] && break; sleep 0.5; done
fi
sleep 25
log "desktop ready"

SCENARIO="${E2E_SCENARIO_FILE:-/opt/gp-demo/scenarios/scenario-${E2E_SCENARIO:-$DE}.sh}"
log "scenario: $SCENARIO"
if [ "$DE" = gnome ]; then
    # Close the "Screen Lock disabled" banner gnome-shell shows (no GDM here)
    as_demo xdotool mousemove 1216 57 click 1
    sleep 1
fi
as_demo bash -c "xdotool mousemove 960 540"
as_demo ffmpeg -nostdin -y -f x11grab -framerate 25 -video_size 1920x1080 -i :1 \
    -c:v libx264 -preset ultrafast -crf 18 -pix_fmt yuv420p /tmp/raw.mp4 > "$OUT/ffmpeg.log" 2>&1 &
FFMPEG_PID=$!
sleep 1
date +%s.%N > /tmp/recording-start
log "scenario start"
as_demo env DEMO_OUT="$OUT" DEMO_DE="$DE" bash "$SCENARIO" > "$OUT/scenario.log" 2>&1
STATUS=$?
echo "$STATUS" > "$OUT/scenario.exit"
log "scenario end (exit $STATUS)"
sleep 1
pkill -INT -u $DEMO_UID -x ffmpeg
for _ in $(seq 60); do kill -0 $FFMPEG_PID 2>/dev/null || break; sleep 0.5; done
cp /tmp/raw.mp4 "$OUT/raw.mp4"

# Logs: the service logs to stderr, which NetworkManager forwards to its log
cp /tmp/edge-wrapper-$DEMO_UID.log "$OUT/edge-wrapper.log" 2>/dev/null || true
nmcli -t -f NAME,TYPE,STATE connection show --active > "$OUT/nmcli-active.txt" 2>&1
{ ip route; resolvectl status; } > "$OUT/network-state.txt" 2>&1
ip -4 addr show gpd0 > "$OUT/gpd0.txt" 2>&1

# The checks read what the scenario saved (state-*.txt) and the logs
log "checks"
python3 /opt/gp-demo/checks.py "${E2E_SCENARIO:-$DE}" "$OUT" 2>&1 | tee "$OUT/checks.log"
CHECKS=${PIPESTATUS[0]}
chmod -R a+rwX "$OUT" 2>/dev/null || true
log "done (scenario exit $STATUS, checks exit $CHECKS)"
[ "$STATUS" -ne 0 ] && exit "$STATUS"
exit "$CHECKS"
