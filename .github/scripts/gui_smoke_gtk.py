#!/usr/bin/env python3
"""GUI smoke test of the installed GNOME connection editor (GTK3 or GTK4).

    gui_smoke_gtk.py --gtk 3|4 --name-file <nm-gpclient-service.name> --screenshot <png>

Run under X (xvfb-run) by .github/scripts/gui-smoke.sh. It finds the editor the
way NetworkManager does (the .name file of the core package), opens it for a
test connection, shows it in a window, saves a screenshot and checks what the
editor displays. Exit status: 0 = fine, 1 = a check failed, 2 = bad arguments.
The decisions are in gui_smoke_lib.py.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gui_smoke_lib as lib  # noqa: E402

TIMEOUT = 15  # seconds to wait for the window to appear


def fail(message):
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def build_connection(NM, service):
    """The connection the editor is opened with for the screenshot and
    lib.check_editor(): an existing profile with lib.vpn_data() and lib.VPN_TIMEOUT"""
    case = {"new": False, "timeout": lib.VPN_TIMEOUT}
    return build_case_connection(NM, service, case, connection_id="gui-smoke")


def build_case_connection(NM, service, case, connection_id="gui-smoke-timeout"):
    """The connection of one lib.TIMEOUT_CASES entry: a new profile has only the
    service type and the stored vpn.timeout, an existing one also has vpn.data
    (default: lib.vpn_data())"""
    connection = NM.SimpleConnection.new()
    s_con = NM.SettingConnection.new()
    s_con.set_property("id", connection_id)
    s_con.set_property("uuid", NM.utils_uuid_generate())
    s_con.set_property("type", "vpn")
    connection.add_setting(s_con)
    s_vpn = NM.SettingVpn.new()
    s_vpn.set_property("service-type", service)
    s_vpn.set_property("timeout", case["timeout"])
    if not case["new"]:
        for key, value in case.get("vpn_data", lib.vpn_data()).items():
            s_vpn.add_data_item(key, value)
    connection.add_setting(s_vpn)
    return connection


def children(widget, Gtk, major):
    if major == 3:
        return widget.get_children() if isinstance(widget, Gtk.Container) else []
    result = []
    child = widget.get_first_child()
    while child is not None:
        result.append(child)
        child = child.get_next_sibling()
    return result


def to_node(widget, Gtk, major):
    """The widget tree as the simple nodes of gui_smoke_lib"""
    if isinstance(widget, Gtk.ComboBoxText):
        items = [row[0] for row in widget.get_model()]
        return {"kind": "combo", "items": items, "text": widget.get_active_text() or ""}
    node = {"kind": "other", "children": []}
    if isinstance(widget, Gtk.SpinButton):  # before Entry: a GTK3 SpinButton is an Entry
        node.update(kind="spin", value=int(widget.get_value()))
    elif isinstance(widget, Gtk.Entry):
        node.update(kind="entry", text=widget.get_text())
    elif isinstance(widget, Gtk.CheckButton):
        node.update(kind="check", text=widget.get_label() or "", active=bool(widget.get_active()))
    elif isinstance(widget, Gtk.Label):
        node.update(kind="label", text=widget.get_text())
    node["children"] = [to_node(c, Gtk, major) for c in children(widget, Gtk, major)]
    return node


def find_widgets(widget, Gtk, major, wanted):
    """All widgets in the tree of `widget` for which wanted(widget) is true"""
    found = [widget] if wanted(widget) else []
    for child in children(widget, Gtk, major):
        found.extend(find_widgets(child, Gtk, major, wanted))
    return found


def show_in_window(widget, GLib, Gtk, major):
    """Put `widget` in its own window and wait until it is mapped: the real
    hosts realise the editor, and a side effect of that must not hide a bug"""
    window = Gtk.Window()
    window.set_default_size(760, 460)
    if major == 3:
        window.add(widget)
        window.show_all()
    else:
        window.set_child(widget)
        window.present()
    if not pump(GLib, until=lambda: window.get_mapped() and widget.get_mapped()):
        fail(f"the editor window did not appear within {TIMEOUT} s")
    return window


def run_timeout_case(NM, GLib, Gtk, major, plugin, service, case):
    """Open the editor for one lib.TIMEOUT_CASES entry in a window, apply its
    steps and save; returns {"shown", "saved", "silent_steps", "changed_on_save"}
    (see gui_smoke_lib.check_timeout_results)"""
    for step in case["steps"]:
        problems = lib.step_problems(step)
        if problems:
            fail(f"case {case['name']!r}: " + "; ".join(problems))
    connection = build_case_connection(NM, service, case)
    editor = plugin.get_editor(connection)
    grid = editor.get_widget()
    changed = [0]
    editor.connect("changed", lambda *args: changed.__setitem__(0, changed[0] + 1))

    def is_auth_combo(w):
        return isinstance(w, Gtk.ComboBoxText) and [r[0] for r in w.get_model()] == lib.AUTH_ITEMS

    combos = find_widgets(grid, Gtk, major, is_auth_combo)
    spins = find_widgets(grid, Gtk, major, lambda w: isinstance(w, Gtk.SpinButton))
    if len(combos) != 1 or len(spins) != 1:
        fail(f"GTK{major} editor: {len(combos)} authentication combos and {len(spins)} spin buttons, expected 1 each")
    spin = spins[0]
    window = show_in_window(grid, GLib, Gtk, major)
    try:
        silent_steps = 0
        for kind, value in case["steps"]:
            before = changed[0]
            if kind == "auth":
                combos[0].set_active(value)
            elif kind == "spin":
                spin.set_value(value)
            else:  # typed text, not committed: no Enter, no focus-out
                if major == 3:
                    Gtk.Entry.set_text(spin, value)
                else:
                    Gtk.Editable.set_text(spin, value)
            if changed[0] == before:
                silent_steps += 1
        text = Gtk.Entry.get_text(spin) if major == 3 else Gtk.Editable.get_text(spin)
        shown = int(text) if re.fullmatch(r"[+-]?[0-9]+", text) else -1
        if case["new"]:
            # the GTK3 editor refuses to save without a gateway
            grid.get_child_at(1, 0).set_text(lib.GATEWAY)
        changed[0] = 0
        editor.update_connection(connection)
        return {
            "shown": shown,
            "saved": connection.get_setting_vpn().get_timeout(),
            "silent_steps": silent_steps,
            "changed_on_save": changed[0],
        }
    finally:
        window.destroy()


def without_tooltips(widget, Gtk, major):
    """Switch off the tooltips of `widget` and everything in it: Xvfb's pointer
    sits in the middle of the screen, over the editor, and a tooltip would
    cover fields in the screenshot"""
    widget.set_has_tooltip(False)
    for child in children(widget, Gtk, major):
        without_tooltips(child, Gtk, major)


def pump(GLib, seconds=0.0, until=None):
    """Run the main loop for `seconds`, or until `until()` is true (at most TIMEOUT)"""
    context = GLib.MainContext.default()
    end = time.monotonic() + (seconds or TIMEOUT)
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until is None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gtk", choices=("3", "4"), required=True)
    parser.add_argument("--name-file", required=True)
    parser.add_argument("--screenshot", required=True)
    args = parser.parse_args(argv)
    major = int(args.gtk)

    try:
        with open(args.name_file, encoding="utf-8") as handle:
            name = lib.parse_name_file(handle.read())
    except (OSError, ValueError) as error:
        fail(f"{args.name_file}: {error}")
    if not os.environ.get("DISPLAY"):
        fail("DISPLAY is not set (run under xvfb-run)")

    import gi

    # Gdk too: left unpinned, gi may load Gdk 4 next to Gtk 3 and fail
    gi.require_version("Gtk", f"{major}.0")
    gi.require_version("Gdk", f"{major}.0")
    gi.require_version("NM", "1.0")
    from gi.repository import Gdk, GLib, Gtk, NM

    if Gdk.Display.get_default() is None:
        fail(f"GTK{major} cannot open the display {os.environ['DISPLAY']}")

    try:
        info = NM.VpnPluginInfo.new_from_file(args.name_file)
        plugin = info.load_editor_plugin()
        editor = plugin.get_editor(build_connection(NM, name["service"]))
    except GLib.Error as error:
        fail(f"cannot load the GTK{major} editor through {args.name_file}: {error.message}")
    if plugin.get_property("service") != name["service"]:
        fail(f"the plugin serves {plugin.get_property('service')!r}, the .name file {name['service']!r}")
    widget = editor.get_widget()
    if widget is None:
        fail("the editor has no widget")

    without_tooltips(widget, Gtk, major)
    show_in_window(widget, GLib, Gtk, major)

    def width_of():
        return widget.get_allocated_width() if major == 3 else widget.get_width()

    def height_of():
        return widget.get_allocated_height() if major == 3 else widget.get_height()

    if not pump(GLib, until=lambda: width_of() > 0):
        fail(f"the editor window did not appear within {TIMEOUT} s")
    pump(GLib, seconds=1.0)  # let it draw

    # The screenshot first: it is most useful when a check below fails
    tool = lib.find_import_tool(shutil.which)
    if tool is None:
        fail("no ImageMagick 'import' for the screenshot (install imagemagick)")
    command = lib.screenshot_command(tool, os.environ["DISPLAY"], args.screenshot)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.isfile(args.screenshot) or not os.path.getsize(args.screenshot):
        fail(f"the screenshot failed: {result.stderr.strip()}")

    problems = lib.check_editor(to_node(widget, Gtk, major), width_of(), height_of())
    if problems:
        fail(f"GTK{major} editor: " + "; ".join(problems))
    print(f"OK: GTK{major} editor {width_of()}x{height_of()}, screenshot {args.screenshot}")

    results = {}
    for case in lib.TIMEOUT_CASES:
        try:
            results[case["name"]] = run_timeout_case(NM, GLib, Gtk, major, plugin, name["service"], case)
        except GLib.Error as error:
            fail(f"GTK{major} editor, case {case['name']!r}: {error.message}")
    problems = lib.check_timeout_results(results)
    if problems:
        fail(f"GTK{major} editor: " + "; ".join(problems))
    print(f"OK: GTK{major} editor connect timeout, {len(results)} save scenarios")


if __name__ == "__main__":
    main()
