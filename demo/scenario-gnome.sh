#!/bin/bash
# Placeholder scenario (GNOME): proves the whole chain. The real click-by-click
# scenario replaces this file. Runs as user demo inside the container.
source /opt/gp-demo/lib.sh

pause 3
screenshot "$DEMO_OUT/shot-1-desktop.png"

nmcli connection add type vpn vpn-type gpclient con-name "Contoso VPN" \
    vpn.data "gateway=vpn.contoso.example,browser=edge" > /dev/null
nmcli connection up "Contoso VPN" > "$DEMO_OUT/nmcli-up.log" 2>&1 &

win=$(wait_for_window "Sign in" 60) || { echo "no Edge window"; exit 1; }
echo "Edge window $win: $(window_geometry "$win")"
pause 3
screenshot "$DEMO_OUT/shot-2-signin.png"

type_slow "alex.morgan@contoso.example"
key Return
pause 2
type_slow "demo-password"
key Return
pause 6
screenshot "$DEMO_OUT/shot-3-stay-signed-in.png"
key Return

for _ in $(seq 60); do
    nmcli -t -f NAME,STATE connection show --active | grep -q "^Contoso VPN:activated" && break
    sleep 1
done
pause 3
screenshot "$DEMO_OUT/shot-4-connected.png"
nmcli -t -f NAME,TYPE,STATE connection show --active
