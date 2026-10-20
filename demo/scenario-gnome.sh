#!/bin/bash
# README scenario (GNOME): add the VPN in Settings, connect from the panel.
# Runs as user demo inside the container; see demo/lib.sh for the helpers.
source /opt/gp-demo/lib.sh
EDGE_DX=${EDGE_DX:-0}
EDGE_DY=${EDGE_DY:-0}
EDGE_POS="545 91"

pause 1.5
shot step-01-desktop

# 1. Settings from the dock; it opens on the Network panel
click 33 196
win=$(wait_for_window "^Settings$" 20) || fail "Settings did not open"
pause 1.5
win_box "$win" || fail "cannot read the Settings geometry"
SX=$((WX + 61)); SY=$((WY + 55))
shot step-02-settings

# 2. "+" next to VPN, then GlobalProtect in the list
click $((SX + 942)) $((SY + 111))
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
animate_window "$dlg" 900 740 510 170
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
click $((SX + 881)) $((SY + 160))
edge_signin
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active"
pause 1.5
shot step-08-connected

# 5. Close Settings, show the VPN in the quick settings
click $((SX + 1057)) $((SY + 23))
wait_window_gone "^Settings$" 10 || fail "Settings did not close"
pause 1.0
click 1867 15
pause 1.0
shot step-09-quick-settings
pause 2.5
key Escape
pause 1.0
