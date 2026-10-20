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
        gsettings set org.gnome.shell favorite-apps \
            "[\"microsoft-edge.desktop\", \"org.gnome.Nautilus.desktop\", \"org.gnome.Settings.desktop\", \"org.gnome.Terminal.desktop\"]"
        /usr/libexec/gsd-xsettings &
        exec gnome-shell --x11'
    ;;
kde)
    export XDG_CURRENT_DESKTOP=KDE KDE_FULL_SESSION=true XDG_SESSION_DESKTOP=KDE
    exec dbus-run-session startplasma-x11
    ;;
*)
    echo "usage: $0 <gnome|kde>" >&2
    exit 2
    ;;
esac
