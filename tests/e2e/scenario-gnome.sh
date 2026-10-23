#!/bin/bash
# README scenario (GNOME): add the VPN in Settings, connect from the panel.
# Runs as user demo inside the container; see demo/lib.sh for the helpers.
source /opt/gp-demo/lib.sh
EDGE_DX=${EDGE_DX:-0}
EDGE_DY=${EDGE_DY:-0}
EDGE_LEFT=66 # the dock
EDGE_TOP=40  # the top bar

pause 1.5
shot step-01-desktop
snapshot start

# 1. Settings from the dock; it opens on the Network panel
click 33 196
win=$(wait_for_window "^Settings$" 20) || fail "Settings did not open"
pause 1.5
win_box "$win" || fail "cannot read the Settings geometry"
SX=$((WX + 61)); SY=$((WY + 55))
shot step-02-settings

# 2. "+" next to VPN, then GlobalProtect in the list
click $((SX + 889)) $((SY + 111))
dlg=$(wait_for_window "^Add VPN$" 20) || fail "the Add VPN dialog did not open"
pause 1.0
win_box "$dlg" || fail "cannot read the dialog geometry"
DX=$((WX + 61)); DY=$((WY + 55))
shot step-03-add-vpn
click $((DX + 182)) $((DY + 83))
pause 1.0

# 3. Our editor: Identity tab, in a dialog made big enough for the form
click $((DX + 123)) $((DY + 64))
pause 0.5
animate_window "$dlg" 880 700 $(((SCREEN_W + 66 - 880) / 2)) $((32 + (SCREEN_H - 32 - 700) / 2))
pause 0.8
win_box "$dlg" || fail "cannot read the dialog geometry"
DX=$((WX + 61)); DY=$((WY + 55)); DW=$((WW - 122))
shot step-04-editor
click $((DX + 450)) $((DY + 112))
key ctrl+a
type_slow "Contoso VPN"
pause 0.8
click $((DX + 450)) $((DY + 168))
type_slow "vpn.contoso.example"
pause 0.8
click $((DX + DW - 81)) $((DY + 362))
pause 1.0
shot step-05-browser-list
smooth_move $((DX + 300)) $((DY + 362 + 109)) 0.5
pause 0.7
click $((DX + 300)) $((DY + 362 + 45))
key End
pause 1.8
shot step-06-filled
click $((DX + DW - 38)) $((DY + 23))
wait_window_gone "^Add VPN$" 20 || fail "the Add VPN dialog did not close"
pause 1.2
shot step-07-vpn-added

# 4. Connect with the switch of the new row; Edge opens through the wrapper
click_until_window $((SX + 828)) $((SY + 160)) "Sign in|GlobalProtect Portal" || fail "the switch did not start the sign-in"
edge_signin
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active"
pause 1.5
shot step-08-connected
snapshot connected

# 5. Close Settings, show the VPN in the quick settings
click $((SX + 977)) $((SY + 23))
wait_window_gone "^Settings$" 10 || fail "Settings did not close"
pause 1.0
click $((SCREEN_W - 53)) 15
pause 1.0
shot step-09-quick-settings
pause 2.5
key Escape
pause 1.0
mark_end
disconnect_vpn "Contoso VPN" || fail "the VPN did not disconnect"
pause 1.5
snapshot disconnected
