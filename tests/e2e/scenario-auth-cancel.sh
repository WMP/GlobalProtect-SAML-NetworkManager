#!/bin/bash
# Negative end-to-end scenario (GNOME): the user closes the sign-in window.
# The connection must fail cleanly: gpclient and gpauth end, no tunnel device,
# the VPN is not active, routes and DNS stay as they were. The fake gpauth
# gives up after GP_DEMO_AUTH_TIMEOUT seconds (run.sh sets 15).
# Not recorded for a GIF. Runs as user demo; see lib.sh.
source /opt/gp-demo/lib.sh
EDGE_LEFT=66 # the dock
EDGE_TOP=40  # the top bar

nmcli connection add type vpn vpn-type gpclient con-name "Contoso VPN" \
    vpn.data "gateway=vpn.contoso.example,browser=edge" > /dev/null || fail "cannot add the VPN profile"
pause 3
snapshot before

nmcli connection up "Contoso VPN" > "$DEMO_OUT/nmcli-up.log" 2>&1 &
UP_PID=$!
win=$(wait_for_window "Sign in|GlobalProtect Portal" 60) || fail "the Edge sign-in window did not open"
edge_fit "$win"
wait_for_window "Sign in" 30 > /dev/null || fail "the sign-in page did not load"
pause 1.5
shot step-1-signin
snapshot edge-open-1

# The user gives up: close the window with the keyboard, as a user would
xdotool windowfocus "$win"
pause 0.3
key alt+F4
wait_window_gone "Sign in|GlobalProtect Portal" 15 || fail "the sign-in window did not close"
shot step-2-closed

# The connection attempt ends by itself (fake gpauth timeout)
for _ in $(seq $(scaled 60)); do
    kill -0 "$UP_PID" 2> /dev/null || break
    sleep 1
done
kill -0 "$UP_PID" 2> /dev/null && fail "the connection attempt did not end"
wait "$UP_PID"
echo "nmcli connection up exit code: $?" >> "$DEMO_OUT/nmcli-up.log"
pause 2
snapshot failed
