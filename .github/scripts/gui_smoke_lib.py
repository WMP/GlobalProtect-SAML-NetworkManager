"""Pure logic of the GUI smoke test (.github/scripts/gui-smoke.sh).

The probes gui_smoke_gtk.py and gui_smoke_plasma.py do the work that needs GTK,
libnm or Qt; everything they decide lives here, so tests/unit/test_gui_smoke.py
can run it anywhere. Standard library only.
"""

import configparser
import re

PLASMA_PLUGIN_ID = "plasmanetworkmanagement_gpclientui"
PLASMA_SERVICE_TYPE = "PlasmaNetworkManagement/VpnUiPlugin"

# What the editor must show for the connection built by vpn_data(). The texts
# are the ones of plugins/gnome/nm-gpclient-editor*.c.
GATEWAY = "vpn.example.com"
GATEWAY_LIST = "gw-a (a.example.com);gw-b (b.example.com)"
GATEWAYS = ["gw-a (a.example.com)", "gw-b (b.example.com)"]
PREFERRED = "gw-b (b.example.com)"
USERNAME = "test-user"
AUTH_ITEMS = ["Browser (SAML, passkey, 2FA)", "Username and password (RSA token)"]
AUTH_ACTIVE = "Username and password (RSA token)"  # for auth-mode=credentials
AS_GATEWAY_LABEL = "Address is a gateway (skip the portal)"
# vpn.timeout of the test connection: a value "the user set" (not 0, not 360)
VPN_TIMEOUT = 240
# What browser authentication starts with (issue #46)
SAML_TIMEOUT = 360

# Save scenarios of the connect timeout. Each case opens the editor and applies
# the steps; the editor is shown in a window first, like in the real hosts.
#   new      a new profile has only the service type, an existing one has vpn.data
#   timeout  the stored vpn.timeout (also of a new profile)
#   vpn_data the vpn.data of an existing profile (default: vpn_data())
#   steps    ("auth", combo index) selects an authentication mode, ("spin", int)
#            sets the value like the +/- buttons, ("type", "digits") types text
#            into the spin button without committing it (no Enter, no focus-out)
#   shown    the number in the spin button after the steps (what the user sees)
#   saved    the vpn.timeout that update_connection() writes
STEP_KINDS = ("auth", "spin", "type")
TIMEOUT_CASES = [
    {"name": "new profile", "new": True, "timeout": 0, "steps": [],
     "shown": SAML_TIMEOUT, "saved": SAML_TIMEOUT},
    {"name": "new profile, credentials", "new": True, "timeout": 0, "steps": [("auth", 1)],
     "shown": 0, "saved": 0},
    {"name": "new profile, credentials, browser", "new": True, "timeout": 0,
     "steps": [("auth", 1), ("auth", 0)], "shown": SAML_TIMEOUT, "saved": SAML_TIMEOUT},
    {"name": "new profile, own value, credentials", "new": True, "timeout": 0,
     "steps": [("spin", 300), ("auth", 1)], "shown": 300, "saved": 300},
    {"name": "new profile, typed value, credentials", "new": True, "timeout": 0,
     "steps": [("type", "500"), ("auth", 1)], "shown": 500, "saved": 500},
    {"name": "new profile with a timeout, credentials", "new": True, "timeout": 90,
     "steps": [("auth", 1)], "shown": 90, "saved": 90},
    {"name": "existing SAML profile without timeout", "new": False, "timeout": 0, "steps": [],
     "vpn_data": {"gateway": GATEWAY}, "shown": 0, "saved": 0},
    {"name": "existing credentials profile without timeout, browser", "new": False, "timeout": 0,
     "steps": [("auth", 0)], "shown": SAML_TIMEOUT, "saved": SAML_TIMEOUT},
    {"name": "existing profile with timeout, browser", "new": False, "timeout": VPN_TIMEOUT,
     "steps": [("auth", 0)], "shown": VPN_TIMEOUT, "saved": VPN_TIMEOUT},
    {"name": "existing profile, typed value", "new": False, "timeout": VPN_TIMEOUT,
     "steps": [("type", "300")], "shown": 300, "saved": 300},
    # a numeric spin button accepts a sign; GTK parses with g_strtod and clamps
    {"name": "existing profile, typed sign", "new": False, "timeout": VPN_TIMEOUT,
     "steps": [("type", "+90")], "shown": 90, "saved": 90},
    {"name": "existing profile, typed negative value", "new": False, "timeout": VPN_TIMEOUT,
     "steps": [("type", "-5")], "shown": -5, "saved": 0},
    {"name": "existing profile, typed value above the range", "new": False, "timeout": VPN_TIMEOUT,
     "steps": [("type", "99999999999999999999")], "shown": 99999999999999999999, "saved": 4294967295},
    # vpn.timeout is a guint32: values above 2^31 must survive a save
    {"name": "existing profile with a timeout above 2^31", "new": False, "timeout": 3000000000,
     "steps": [], "shown": 3000000000, "saved": 3000000000},
    {"name": "existing profile with the largest timeout", "new": False, "timeout": 4294967295,
     "steps": [], "shown": 4294967295, "saved": 4294967295},
]


def step_problems(step):
    """The problems of one step of a TIMEOUT_CASES entry (empty list = fine)"""
    if not isinstance(step, tuple) or len(step) != 2:
        return [f"step {step!r} is not a (kind, value) pair"]
    kind, value = step
    if kind not in STEP_KINDS:
        return [f"unknown step kind {kind!r}"]
    if kind == "auth":
        if type(value) is not int or value not in (0, 1):
            return [f"auth index {value!r} is not the int 0 or 1"]
    elif kind == "spin":
        if type(value) is not int or not 0 <= value <= 4294967295:
            return [f"spin value {value!r} is not an int in 0..4294967295"]
    elif not isinstance(value, str) or not value.isascii() or not re.fullmatch(r"[+-]?[0-9]+", value):
        return [f"typed text {value!r} is not an ASCII number with an optional sign"]
    return []


def parse_name_file(text):
    """{"service", "plugin", "name"} of a NetworkManager VPN .name file.

    Raises ValueError when the service or the libnm plugin is missing: the
    editor cannot be found without them."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ValueError(f"not a keyfile: {error}") from error
    result = {
        "name": parser.get("VPN Connection", "name", fallback=""),
        "service": parser.get("VPN Connection", "service", fallback=""),
        "plugin": parser.get("libnm", "plugin", fallback=""),
    }
    for key in ("service", "plugin"):
        if not result[key].strip():
            raise ValueError(f"the .name file has no {key}")
    return result


def vpn_data():
    """The vpn.data of the connection the editor is opened with"""
    return {
        "gateway": GATEWAY,
        "as-gateway": "true",
        "preferred-gateway": PREFERRED,
        "gateway-list": GATEWAY_LIST,
        "gateway-list-count": "2",
        "username": USERNAME,
        "auth-mode": "credentials",
    }


def flatten(node):
    """The node and all its descendants, depth first. A node is a dict:
    {"kind": "entry"|"combo"|"check"|"label"|"spin"|"other", "text": str,
     "items": [str], "active": bool, "value": int, "children": [node]}; all keys but kind are optional."""
    nodes = [node]
    for child in node.get("children", []):
        nodes.extend(flatten(child))
    return nodes


def check_editor(root, width, height):
    """The problems of the editor widget (empty list = fine): `root` is the
    widget tree as nodes (see flatten()), width and height its allocation."""
    problems = []
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        problems.append(f"the editor widget has no size ({width}x{height})")
    nodes = flatten(root)

    def entries(text):
        return [n for n in nodes if n.get("kind") == "entry" and n.get("text") == text]

    def combos_with(items):
        return [n for n in nodes if n.get("kind") == "combo" and all(i in n.get("items", []) for i in items)]

    if not entries(GATEWAY):
        problems.append(f"no entry with the gateway {GATEWAY!r}")
    if not entries(USERNAME):
        problems.append(f"no entry with the username {USERNAME!r}")

    gateway_combos = combos_with(GATEWAYS)
    if not gateway_combos:
        problems.append(f"no combo box offers the gateways {GATEWAYS}")
    elif not any(c.get("text") == PREFERRED for c in gateway_combos):
        shown = [c.get("text") for c in gateway_combos]
        problems.append(f"the preferred gateway is not {PREFERRED!r} (shown: {shown})")

    auth_combos = combos_with(AUTH_ITEMS)
    if not auth_combos:
        problems.append(f"no combo box offers the authentication modes {AUTH_ITEMS}")
    elif not any(c.get("text") == AUTH_ACTIVE for c in auth_combos):
        shown = [c.get("text") for c in auth_combos]
        problems.append(f"the authentication is not {AUTH_ACTIVE!r} (shown: {shown})")

    spins = [n for n in nodes if n.get("kind") == "spin"]
    if not spins:
        problems.append("no spin button for the connect timeout")
    elif not any(n.get("value") == VPN_TIMEOUT for n in spins):
        shown = [n.get("value") for n in spins]
        problems.append(f"the connect timeout is not {VPN_TIMEOUT} (shown: {shown})")

    checks = [n for n in nodes if n.get("kind") == "check" and n.get("text") == AS_GATEWAY_LABEL]
    if not checks:
        problems.append(f"no check button {AS_GATEWAY_LABEL!r}")
    elif not any(c.get("active") for c in checks):
        problems.append(f"the check button {AS_GATEWAY_LABEL!r} is not ticked")
    return problems


def check_timeout_results(results):
    """The problems of the connect timeout scenarios (empty list = fine):
    `results` maps the name of a TIMEOUT_CASES entry to a dict with
    "shown" (number in the spin button), "saved" (vpn.timeout after
    update_connection()), "silent_steps" (steps after which the editor did not
    emit "changed") and "changed_on_save" ("changed" during update_connection())."""
    problems = []
    for case in TIMEOUT_CASES:
        name = case["name"]
        if name not in results:
            problems.append(f"connect timeout case {name!r} did not run")
            continue
        result = results[name]
        if result["shown"] != case["shown"]:
            problems.append(f"{name}: the connect timeout shown is {result['shown']}, expected {case['shown']}")
        if result["saved"] != case["saved"]:
            problems.append(f"{name}: the connect timeout saved is {result['saved']}, expected {case['saved']}")
        if result["silent_steps"] != 0:
            problems.append(f"{name}: a user action did not emit changed ({result['silent_steps']} steps)")
        if result["changed_on_save"] != 0:
            problems.append(f"{name}: update_connection emitted changed ({result['changed_on_save']} times)")
    return problems


def parse_ldd(output):
    """The libraries `ldd` could not find (names), from its output"""
    missing = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\S+)\s+=>\s+not found\s*$", line)
        if match:
            missing.append(match.group(1))
    return missing


def ldd_problems(returncode, output):
    """The problems `ldd` reports for a plugin (empty list = it can be loaded)"""
    problems = [f"library not found: {name}" for name in parse_ldd(output)]
    if returncode != 0 and not problems:
        problems.append(f"ldd failed with status {returncode}: {output.strip()[:200]}")
    return problems


def plasma_metadata(meta):
    """The plugin's own JSON from what QPluginLoader.metaData() returns (it wraps
    the file in "MetaData") or from the bare JSON file; {} when there is none"""
    if not isinstance(meta, dict):
        return {}
    inner = meta.get("MetaData", meta if "IID" not in meta else {})
    return inner if isinstance(inner, dict) else {}


def check_plasma_metadata(meta, service):
    """The problems of the metadata of the Plasma editor plugin (empty = fine)"""
    data = plasma_metadata(meta)
    if not data:
        return ["the plugin has no metadata"]
    problems = []
    kplugin = data.get("KPlugin")
    kplugin = kplugin if isinstance(kplugin, dict) else {}
    if kplugin.get("Id") != PLASMA_PLUGIN_ID:
        problems.append(f"KPlugin.Id is {kplugin.get('Id')!r}, expected {PLASMA_PLUGIN_ID!r}")
    if PLASMA_SERVICE_TYPE not in (kplugin.get("ServiceTypes") or []):
        problems.append(f"KPlugin.ServiceTypes lacks {PLASMA_SERVICE_TYPE!r}")
    services = data.get("X-NetworkManager-Services")
    listed = [s.strip() for s in services.split(",")] if isinstance(services, str) else []
    if service not in listed:
        problems.append(f"X-NetworkManager-Services is {services!r}, expected {service!r}")
    return problems


def qt_major(path):
    """5 or 6 from the qt5/qt6 directory of a plugin path, None if there is none"""
    match = re.search(r"/qt([56])/", path)
    return int(match.group(1)) if match else None


IMPORT_TOOLS = ("import", "import-im7.q16", "import-im6.q16")


def find_import_tool(which):
    """The ImageMagick screenshot tool: `which` maps a command name to its path
    or None. None when ImageMagick is not installed."""
    for name in IMPORT_TOOLS:
        path = which(name)
        if path:
            return path
    return None


def screenshot_command(tool, display, out):
    """Command line that saves the whole X screen as the image `out`"""
    if not out.endswith(".png"):
        raise ValueError(f"the screenshot must be a .png file: {out}")
    return [tool, "-display", display, "-window", "root", out]
