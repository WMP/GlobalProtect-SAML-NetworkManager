#!/bin/bash
# Starts the desktop session as user demo (run by start.sh, never as root).
# Usage: desktop.sh <gnome|kde>
# Runs in the foreground; the session ends when this script is killed.
set -u

export DISPLAY=:1
export XDG_RUNTIME_DIR=/run/user/1000
export XDG_SESSION_TYPE=x11
export XDG_SESSION_CLASS=user
export LANG=C.UTF-8

case "${1:-}" in
gnome)
    export XDG_CURRENT_DESKTOP=ubuntu:GNOME GNOME_SHELL_SESSION_MODE=ubuntu XDG_SESSION_DESKTOP=ubuntu
    # gnome-session needs logind, so start the pieces by hand
    exec dbus-run-session -- bash -c '
        gsettings set org.gnome.desktop.lockdown disable-lock-screen true
        gsettings set org.gnome.desktop.screensaver lock-enabled false
        gsettings set org.gnome.desktop.session idle-delay 0
        gsettings set org.gnome.shell welcome-dialog-last-shown-version "999"
        gsettings set org.gnome.mutter center-new-windows true
        gsettings set org.gnome.Settings last-panel network
        gsettings set org.gnome.Settings window-state "(1080, 760, false)"
        gsettings set org.gnome.Settings show-development-warning false
        gsettings set org.gnome.shell favorite-apps \
            "[\"microsoft-edge.desktop\", \"org.gnome.Nautilus.desktop\", \"org.gnome.Settings.desktop\", \"org.gnome.Terminal.desktop\"]"
        # Terminal: large readable font, 110x30
        uuid=$(gsettings get org.gnome.Terminal.ProfilesList default | tr -d "\047")
        profile="org.gnome.Terminal.Legacy.Profile:/org/gnome/terminal/legacy/profiles:/:$uuid/"
        gsettings set "$profile" use-system-font false
        gsettings set "$profile" font "Ubuntu Mono 16"
        gsettings set "$profile" default-size-columns 110
        gsettings set "$profile" default-size-rows 30
        gsettings set "$profile" audible-bell false
        /usr/libexec/gsd-xsettings &
        exec gnome-shell --x11'
    ;;
kde)
    export XDG_CURRENT_DESKTOP=KDE KDE_FULL_SESSION=true XDG_SESSION_DESKTOP=KDE
    # New windows are centered; the task manager pins only apps that exist
    kwriteconfig6 --file kwinrc --group Windows --key Placement Centered
    exec dbus-run-session -- bash -c '
        (
            script="for (const p of panels()) { for (const id of p.widgetIds) { const w = p.widgetById(id);
                if (w.type == \"org.kde.plasma.icontasks\") { w.currentConfigGroup = [\"General\"];
                w.writeConfig(\"launchers\", [\"applications:org.kde.dolphin.desktop\",
                \"applications:systemsettings.desktop\", \"applications:microsoft-edge.desktop\"]); } } }"
            # evaluateScript returns nothing, and the panel appears late: repeat
            for _ in $(seq 25); do
                dbus-send --session --dest=org.kde.plasmashell /PlasmaShell \
                    org.kde.PlasmaShell.evaluateScript "string:$script" > /dev/null 2>&1
                sleep 2
            done
        ) &
        exec startplasma-x11'
    ;;
*)
    echo "usage: $0 <gnome|kde>" >&2
    exit 2
    ;;
esac
