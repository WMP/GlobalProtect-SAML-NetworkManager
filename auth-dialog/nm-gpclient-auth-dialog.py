#!/usr/bin/env python3
"""
NetworkManager auth dialog for the GlobalProtect (gpclient) VPN plugin.

Speaks the standard NetworkManager VPN auth-dialog protocol used by
nm-applet / GNOME Shell:

  - invoked with:  -u UUID -n NAME -s SERVICE [-i] [-r] [-t HINT]...
  - reads connection data on stdin as DATA_KEY=/DATA_VAL= and
    SECRET_KEY=/SECRET_VAL= line pairs, terminated by a line "DONE"
  - writes requested secrets to stdout as "key\nvalue\n" pairs followed
    by an empty line, then waits for "QUIT" on stdin before exiting

Two modes:
  - no hints: upfront password request (auth-mode=credentials connections)
  - with hints: dynamic challenge from the VPN service (SecretsRequired),
    e.g. "otp" for an RSA token / one-time code, "password", "username".
    A hint prefixed with "x-vpn-message:" carries the human-readable
    prompt message from the VPN service.
"""

import argparse
import sys

SECRET_LABELS = {
    "username": "Username",
    "password": "Password",
    "otp": "Token / one-time code",
}

# Hints that should be shown as plain-text entries instead of masked ones
PLAIN_TEXT_HINTS = ("username", "otp")

# One-time secrets: a stored value is worse than none, because the gateway
# rejects a reused passcode and the whole login fails. Never answer these from
# what the connection happens to have saved, and never pre-fill them.
ONE_TIME_HINTS = ("otp",)


def read_stdin_data():
    """Read DATA_KEY/DATA_VAL and SECRET_KEY/SECRET_VAL pairs until DONE"""
    data = {}
    secrets = {}
    key = None
    is_secret = False

    for line in sys.stdin:
        line = line.rstrip("\n")
        if line == "DONE":
            break
        if line.startswith("DATA_KEY="):
            key = line[len("DATA_KEY=") :]
            is_secret = False
        elif line.startswith("DATA_VAL=") and key is not None and not is_secret:
            data[key] = line[len("DATA_VAL=") :]
            key = None
        elif line.startswith("SECRET_KEY="):
            key = line[len("SECRET_KEY=") :]
            is_secret = True
        elif line.startswith("SECRET_VAL=") and key is not None and is_secret:
            secrets[key] = line[len("SECRET_VAL=") :]
            key = None

    return data, secrets


def wait_for_quit():
    """Block until the agent sends QUIT (or closes stdin)"""
    for line in sys.stdin:
        if line.strip() == "QUIT":
            break


def output_secrets(secrets):
    """Write secrets to stdout in the auth-dialog protocol format"""
    for key, value in secrets.items():
        sys.stdout.write(f"{key}\n{value}\n")
    sys.stdout.write("\n\n")
    sys.stdout.flush()


def parse_hints(raw_hints):
    """Split hints into requested secret keys and an optional message"""
    keys = []
    message = None
    for hint in raw_hints:
        if hint.startswith("x-vpn-message:"):
            message = hint[len("x-vpn-message:") :]
        else:
            keys.append(hint)
    return keys, message


def ask_user(vpn_name, requested, message, existing_secrets, data, reprompt):
    """Show a GTK dialog asking for the requested secrets.

    Returns a dict {key: value} or None when the user cancelled.
    """
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    dialog = Gtk.Dialog(title=f"Authenticate VPN: {vpn_name}")
    dialog.set_modal(True)
    dialog.set_keep_above(True)
    dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    ok_button = dialog.add_button("_OK", Gtk.ResponseType.OK)
    ok_button.get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.OK)

    content = dialog.get_content_area()
    content.set_border_width(12)
    content.set_spacing(8)

    header = Gtk.Label()
    if message:
        header.set_text(message)
    elif reprompt:
        header.set_text(
            f"Authentication failed. Enter the credentials for “{vpn_name}” again."
        )
    else:
        header.set_text(f"Credentials are required to connect to “{vpn_name}”.")
    header.set_line_wrap(True)
    header.set_max_width_chars(60)
    header.set_xalign(0)
    content.pack_start(header, False, False, 0)

    if data.get("username"):
        user_label = Gtk.Label(label=f"User: {data['username']}")
        user_label.set_xalign(0)
        content.pack_start(user_label, False, False, 0)

    grid = Gtk.Grid()
    grid.set_row_spacing(6)
    grid.set_column_spacing(8)
    content.pack_start(grid, False, False, 0)

    entries = {}
    for row, key in enumerate(requested):
        label_text = SECRET_LABELS.get(key, key.replace("-", " ").capitalize())
        label = Gtk.Label(label=f"{label_text}:")
        label.set_xalign(1)
        grid.attach(label, 0, row, 1, 1)

        entry = Gtk.Entry()
        entry.set_width_chars(24)
        entry.set_activates_default(True)
        if key not in PLAIN_TEXT_HINTS:
            entry.set_visibility(False)
        if not reprompt and key not in ONE_TIME_HINTS and existing_secrets.get(key):
            entry.set_text(existing_secrets[key])
        grid.attach(entry, 1, row, 1, 1)
        entries[key] = entry

    dialog.show_all()
    if entries:
        first_empty = next(
            (e for e in entries.values() if not e.get_text()),
            next(iter(entries.values())),
        )
        first_empty.grab_focus()

    response = dialog.run()
    result = None
    if response == Gtk.ResponseType.OK:
        result = {key: entry.get_text() for key, entry in entries.items()}
    dialog.destroy()

    # Drain pending GTK events so the dialog disappears immediately
    while Gtk.events_pending():
        Gtk.main_iteration()

    return result


def main():
    parser = argparse.ArgumentParser(description="GlobalProtect VPN auth dialog")
    parser.add_argument("-u", "--uuid", required=True)
    parser.add_argument("-n", "--name", required=True)
    parser.add_argument("-s", "--service", required=True)
    parser.add_argument("-i", "--allow-interaction", action="store_true")
    parser.add_argument("-r", "--reprompt", action="store_true")
    parser.add_argument("-t", "--hint", action="append", default=[])
    parser.add_argument(
        "--external-ui-mode", action="store_true", help="Ignored (not supported)"
    )
    args = parser.parse_args()

    if args.service != "org.freedesktop.NetworkManager.gpclient":
        print(f"Unsupported VPN service: {args.service}", file=sys.stderr)
        return 1

    data, existing_secrets = read_stdin_data()
    requested, message = parse_hints(args.hint)

    if not requested:
        # No hints: NetworkManager wants the connection's stored secrets, not
        # an answer to a challenge from the VPN service.
        #
        # SAML connections (the default) have no stored secrets at all - the
        # browser does the authentication. Exiting non-zero here is reported
        # to NetworkManager as "user canceled the secrets request", which is
        # what kept the connection editor spinning until the 25 s secrets
        # timeout expired (issue #8). Answer with an empty set instead.
        if data.get("auth-mode", "saml") != "credentials":
            output_secrets({})
            wait_for_quit()
            return 0

        # Standard login portal, but the password is explicitly not required
        # (password-flags: 4 = NOT_REQUIRED).
        if data.get("password-flags", "0") == "4":
            output_secrets({})
            wait_for_quit()
            return 0

        # Standard login portal: only the password is stored as a secret.
        requested = ["password"]

    # If we already have every requested secret and don't need to re-ask,
    # return them without any UI. One-time codes are always asked for.
    reusable = not args.reprompt and not any(
        key in ONE_TIME_HINTS for key in requested
    )
    if reusable and all(existing_secrets.get(key) for key in requested):
        output_secrets({key: existing_secrets[key] for key in requested})
        wait_for_quit()
        return 0

    if not args.allow_interaction:
        print("No secrets available and interaction is not allowed", file=sys.stderr)
        return 1

    try:
        result = ask_user(
            args.name, requested, message, existing_secrets, data, args.reprompt
        )
    except Exception as e:
        print(f"Failed to show auth dialog: {e}", file=sys.stderr)
        return 1

    if result is None:
        # User cancelled
        return 1

    output_secrets(result)
    wait_for_quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
