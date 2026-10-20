#!/bin/bash
# README scenario (KDE Plasma): add the VPN in System Settings, connect from
# the system tray. Runs as user demo inside the container; see demo/lib.sh.
source /opt/gp-demo/lib.sh
EDGE_DX=${EDGE_DX:-0}
EDGE_DY=${EDGE_DY:-0}
EDGE_POS="512 48"

pause 1.5
shot step-01-desktop
snapshot start

# 1. Network icon in the tray, then the configure button of the popup
click 1742 1048
pause 1.2
shot step-02-tray-popup
click 1857 597
win=$(wait_for_window "Wi-Fi & Networking" 30) || fail "System Settings did not open"
pause 1.5
shot step-03-settings

# 2. "+" below the connection list, then the GlobalProtect entry
win_box "$win" || fail "cannot read the System Settings geometry"
BX=$WX; BY=$WY
click 974 763
dlg=$(wait_for_window "Choose a Connection Type" 20) || fail "the connection type dialog did not open"
pause 1.0
shot step-04-types
smooth_move 950 600 0.5
for _ in 1 2 3 4 5 6 7 8 9 10; do xdotool click 5; sleep 0.12; done
pause 1.0
shot step-05-types-scrolled
click 790 432
dlg=$(wait_for_window "^New Connection" 20) || fail "the connection editor did not open"
pause 1.0
win_box "$dlg" || fail "cannot read the editor geometry"
EX=$WX; EY=$WY
shot step-06-editor

# 3. Our tab: name, address, browser
click $((EX + 142)) $((EY + 63))
pause 0.8
click $((EX + 316)) $((EY + 26))
key ctrl+a
type_slow "Contoso VPN"
pause 0.8
click $((EX + 341)) $((EY + 102))
type_slow "vpn.contoso.example"
pause 0.8
click $((EX + 238)) $((EY + 282))
pause 1.0
shot step-07-browser-list
smooth_move $((EX + 238)) $((EY + 282 + 62)) 0.5
pause 0.7
click $((EX + 238)) $((EY + 282 + 30))
pause 1.8
shot step-08-filled
click $((EX + 381)) $((EY + 609))
wait_window_gone "^New Connection" 20 || fail "the connection editor did not close"
pause 1.2
shot step-09-saved

# 4. Close System Settings, connect from the tray popup
click $((BX + 916)) $((BY - 14))
wait_window_gone "Wi-Fi & Networking" 10 || fail "System Settings did not close"
pause 1.0
click 1742 1048
pause 1.5
shot step-10-tray-vpn
smooth_move 1600 777 0.5
pause 0.6
click 1822 777
edge_signin
wait_vpn "Contoso VPN" activated 60 || fail "the VPN did not become active"
pause 2.0
shot step-11-notification
snapshot connected
click 1742 1048
pause 1.5
shot step-12-tray-connected
pause 2.5
key Escape
pause 1.0
snapshot end
