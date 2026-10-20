#!/bin/bash
# README scenario (GNOME): the console slide. Shows the routes and the DNS
# servers before connecting, after connecting with the default settings and
# after enabling "Use this connection only for resources on its network".
# Runs as user demo inside the container; see demo/lib.sh for the helpers.
source /opt/gp-demo/lib.sh
EDGE_DX=0
EDGE_DY=0
EDGE_POS="545 91"

say() { type_slow "# $1"; key Return; pause 0.5; }
run() { type_slow "$1"; pause 0.4; key Return; pause 0.6; }

# Connect or disconnect with the VPN tile of the quick settings
quick_vpn() {
    click 1867 15
    pause 1.0
    click 1600 130
    pause 0.8
}

# --- Setup, not in the GIF --------------------------------------------------
# The profile is made as in the main demo (the editor is shown there). One
# complete sign-in fills the sign-in memory of the page, so that the sign-in
# in the GIF is the short one.
nmcli connection add type vpn vpn-type gpclient con-name "Contoso VPN" \
    vpn.data "gateway=vpn.contoso.example,browser=edge" > /dev/null || fail "cannot add the VPN profile"
nmcli connection up "Contoso VPN" > "$DEMO_OUT/nmcli-warmup.log" 2>&1 &
edge_signin
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active (setup)"
nmcli connection down "Contoso VPN" > /dev/null 2>&1
for _ in $(seq 40); do
    nmcli -t -f NAME connection show --active | grep -qx "Contoso VPN" || break
    sleep 0.5
done
pause 1.0
xdotool mousemove 1100 600
mark_start

# --- 1. Before connecting ---------------------------------------------------
pause 1.5
click 33 260
term=$(wait_for_window "^demo@" 20) || fail "the terminal did not open"
pause 1.2
win_box "$term" || fail "cannot read the terminal geometry"
TX=$((WX + WW / 2)); TY=$((WY + WH / 2))
shot step-01-terminal
snapshot before
say "Before connecting"
run "ip route"
pause 2.5
run "resolvectl dns"
pause 3.0

# --- 2. Connect with the default settings ----------------------------------
quick_vpn
edge_sso_auto
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active"
pause 1.0
click "$TX" "$TY" 0.6
key ctrl+l
say "VPN connected, default settings"
run "ip route"
pause 3.0
run "resolvectl dns"
run "resolvectl domain"
pause 3.0
shot step-02-connected-default
snapshot connected-default

# --- 3. Disconnect, set "only for resources on its network" ----------------
quick_vpn
for _ in $(seq 40); do
    nmcli -t -f NAME connection show --active | grep -qx "Contoso VPN" || break
    sleep 0.5
done
pause 1.5
snapshot disconnected
key Escape
pause 0.8
click 33 196
win=$(wait_for_window "^Settings$" 20) || fail "Settings did not open"
pause 1.5
win_box "$win" || fail "cannot read the Settings geometry"
SX=$((WX + 61)); SY=$((WY + 55))
shot step-03-settings
click $((SX + 929)) $((SY + 160))
dlg=$(wait_for_window "^Contoso VPN$" 20) || fail "the VPN dialog did not open"
pause 1.0
win_box "$dlg" || fail "cannot read the dialog geometry"
DX=$((WX + 61)); DY=$((WY + 55))
click $((DX + 197)) $((DY + 64))
pause 1.2
shot step-04-ipv4
click $((DX + 37)) $((DY + 456))
pause 1.8
shot step-05-checkbox
click $((DX + 711)) $((DY + 23))
wait_window_gone "^Contoso VPN$" 20 || fail "the VPN dialog did not close"
pause 1.0
click $((SX + 1057)) $((SY + 23))
wait_window_gone "^Settings$" 10 || fail "Settings did not close"
pause 1.0

# --- 4. Connect again --------------------------------------------------------
quick_vpn
edge_sso_auto
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active (second time)"
pause 1.0
click "$TX" "$TY" 0.6
key ctrl+l
say "Routing setting enabled: only company networks use the VPN"
run "ip route"
pause 3.0
run "resolvectl dns"
run "resolvectl domain"
pause 3.5
shot step-06-connected-never-default
snapshot connected-never-default
