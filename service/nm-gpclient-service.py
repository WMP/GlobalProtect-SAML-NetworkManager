#!/usr/bin/env python3
"""
NetworkManager VPN Service for GlobalProtect (gpclient)

This service handles VPN connections via gpclient command.
It reads configuration from NetworkManager and spawns gpclient process.

Rewritten using python-sdbus for proper D-Bus interface implementation.
"""

import asyncio
import fcntl
import logging
import os
import pty
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sdbus import (
    DbusInterfaceCommonAsync,
    dbus_method_async,
    dbus_property_async,
    dbus_signal_async,
    request_default_bus_name_async,
    sd_bus_open_system,
    set_default_bus,
)

# Configure logging to use systemd journal
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(name)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# D-Bus service constants
NM_DBUS_SERVICE_GPCLIENT = "org.freedesktop.NetworkManager.gpclient"
NM_DBUS_INTERFACE_VPN = "org.freedesktop.NetworkManager.VPN.Plugin"
NM_DBUS_PATH_GPCLIENT = "/org/freedesktop/NetworkManager/VPN/Plugin"

# VPN Plugin states
NM_VPN_SERVICE_STATE_UNKNOWN = 0
NM_VPN_SERVICE_STATE_INIT = 1
NM_VPN_SERVICE_STATE_SHUTDOWN = 2
NM_VPN_SERVICE_STATE_STARTING = 3
NM_VPN_SERVICE_STATE_STARTED = 4
NM_VPN_SERVICE_STATE_STOPPING = 5
NM_VPN_SERVICE_STATE_STOPPED = 6

# VPN failure reasons
NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED = 0
NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED = 1
NM_VPN_PLUGIN_FAILURE_BAD_IP_CONFIG = 2

# Interface names the tunnel detection looks for. gpd0 is created
# exclusively by gpclient; tun0/tun1 may also belong to other VPN clients.
TUNNEL_INTERFACES = ["gpd0", "tun0", "tun1"]

GPCLIENT_BINARY = "/usr/bin/gpclient"

# Secret name used for one-time codes in SecretsRequired/NewSecrets
OTP_SECRET_KEY = "otp"

# --- Legacy TLS renegotiation ------------------------------------------------
#
# Portals with an old TLS stack need renegotiation that OpenSSL 3.x refuses by
# default, so the prelogin request fails before any browser can open:
#
#   error:0A000152:SSL routines:final_renegotiate:unsafe legacy renegotiation disabled
#   Re-run it with the `--fix-openssl` option to work around this issue
#
# gpclient has the workaround built in (a temporary OPENSSL_CONF enabling
# UnsafeLegacyServerConnect, also passed on to gpauth), but it has to be asked
# for with a global flag placed before the subcommand. We watch for the error
# and retry once with the flag, so nobody has to know the option exists
# (issue #2).
OPENSSL_LEGACY_ERROR_RE = re.compile(
    r"unsafe legacy renegotiation disabled|--fix-openssl"
)

# How long we wait for the user to answer an interactive secrets request
# (NewSecrets from NetworkManager) before giving up.
SECRETS_REQUEST_TIMEOUT = 300

# How long a prompt candidate must stay unchanged before we act on it.
# gpclient (inquire) renders prompts incrementally; the debounce avoids
# reacting to half-rendered lines.
PROMPT_DEBOUNCE_SECONDS = 0.5

# --- Session environment ----------------------------------------------------
#
# NetworkManager starts this service with a bare environment, so gpauth - and
# through it the SAML browser - has no way to reach the display server unless
# we import these from the running session. Passing only DISPLAY=:0 (what we
# used to do) is wrong on Wayland: the browser silently failed to open a window
# for every browser except the wrapped Edge, which reconstructs the session
# environment itself (issue #7).
SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
)

# Processes whose environment describes the graphical session, most specific
# first ("systemd" is the per-user manager and carries only a subset).
SESSION_LEADER_PROCESSES = (
    "gnome-shell",
    "plasmashell",
    "gnome-session-binary",
    "kwin_wayland",
    "xfce4-session",
    "cinnamon-session",
    "mate-session",
    "sway",
    "systemd",
)

# --- Browser resolution -----------------------------------------------------
#
# The wrapper fixes up the environment, the profile directory and the window
# lifetime for the SAML browser (scripts/browser-wrapper.sh).
BROWSER_WRAPPER = "/usr/libexec/gpclient/browser-wrapper"
LEGACY_EDGE_WRAPPER = "/usr/libexec/gpclient/edge-wrapper"

# Friendly browser names accepted in vpn.data, normalised for the wrapper
BROWSER_ALIASES = {
    "edge": "edge",
    "msedge": "edge",
    "microsoft-edge": "edge",
    "chrome": "chrome",
    "google-chrome": "chrome",
    "chromium": "chromium",
    "firefox": "firefox",
    "default": "default",
}

# Concrete binaries the connection editors used to offer - wrapping them keeps
# existing profiles working and fixes them at the same time
WRAPPED_BROWSER_PATH_RE = re.compile(
    r"^/usr/bin/(?:microsoft-edge\S*|google-chrome\S*|chromium\S*|firefox\S*)$"
)

# Fallback when the wrapper is missing (service upgraded, wrapper not yet
# installed): let gpclient launch the browser directly
BROWSER_BINARIES = {
    "edge": ("/usr/bin/microsoft-edge",),
    "chrome": ("google-chrome-stable", "google-chrome"),
    "chromium": ("chromium", "chromium-browser"),
    "firefox": ("firefox",),
}

# --- Gateway selection ------------------------------------------------------
#
# For a portal with more than one gateway, gpclient renders an inquire Select
# frame of complete lines (every one terminated with \r\n, so - unlike a Text
# prompt - nothing is left in the output tail):
#
#   ? Which gateway do you want to connect to?
#   > gw-warsaw (gw1.example.com)
#     gw-frankfurt (gw2.example.com)
#   [↑↓ to move, enter to select, type to filter]
#
# We answer it ourselves: the gateway from vpn.data preferred-gateway, or the
# first proposal when there is none. The list is cached in the connection
# profile afterwards so the connection editor can offer it (issue #7).
SELECT_HELP_RE = re.compile(r"^\[.*(?:to move|to select|to filter|↑↓).*\]$")
SELECT_OPTION_MARKERS = ">^v "
SELECT_FRAME_MAX_LINES = 24

# Down wraps around in inquire (move_cursor_down(1, wrap=true)), so walking the
# list with Down alone always terminates and reaches every entry - including
# ones outside the visible page.
KEY_DOWN = b"\x1b[B"
KEY_ENTER = b"\r"
SELECT_MAX_STEPS = 200
SELECT_REDRAW_TIMEOUT = 1.5
SELECT_POLL_INTERVAL = 0.05

# Separator for the cached gateway list in vpn.data. Commas cannot be used:
# `nmcli connection modify ... +vpn.data` splits key=value pairs on them.
GATEWAY_LIST_SEPARATOR = ";"

# gpclient logs the gateway it picked when it did not have to ask
GATEWAY_CHOSEN_RE = re.compile(
    r"Connecting to (?:the only available|the selected) gateway: (?P<gateway>.+?)\s*$"
)

# --- Interactive prompt detection -------------------------------------------
#
# For portals that do NOT use SAML (Prelogin::Standard in gpclient, e.g. RSA
# SecurID token challenges - see issue #6), gpclient prompts interactively on
# its terminal via the `inquire` crate:
#
#   Please enter RSA token (Portal: vpn.example.com)   <- plain println banner
#   ? Username:                                        <- inquire Text prompt
#   ? Password:                                        <- inquire Password prompt
#
# and for gateway MFA challenges:
#
#   ? <server-provided message>                        <- inquire Text prompt
#
# We run gpclient under a PTY so those prompts actually render, detect them in
# the output stream and answer them either from secrets stored in the NM
# connection or interactively via the SecretsRequired/NewSecrets D-Bus flow.

# CSI / OSC / other escape sequences emitted by inquire (crossterm)
ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI sequences (colors, cursor, clear)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|\x1b[@-Z\\-_]"  # other Fe escape sequences
)

# Control characters except \n, \r and \t
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# An escape sequence cut in half by a read boundary. Without holding the head
# back, ESC is dropped as a control character and the rest leaks into the text -
# which is how a prompt label ended up as "[39m Password" in the #2 report.
INCOMPLETE_ANSI_RE = re.compile(r"\x1b\[?[0-9;?]*$")

# "Please enter RSA token (Portal: vpn.example.com)" banner printed by
# gpclient before a standard (non-SAML) authentication round.
AUTH_BANNER_RE = re.compile(
    r"^(?P<message>.+?)\s*\((?P<kind>Portal|Gateway):\s*(?P<server>[^)]+)\)\s*$"
)

USERNAME_LABEL_WORDS = ("user", "login", "email", "e-mail")
ONE_TIME_SECRET_WORDS = (
    "token",
    "otp",
    "passcode",
    "pin",
    "code",
    "challenge",
    "tan",
    "rsa",
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and control chars (except \\n \\r \\t)"""
    text = ANSI_ESCAPE_RE.sub("", text)
    return CONTROL_CHARS_RE.sub("", text)


def parse_auth_banner(line: str) -> Optional[Dict[str, str]]:
    """Parse gpclient's 'message (Portal|Gateway: server)' auth banner line"""
    match = AUTH_BANNER_RE.match(line.strip())
    if not match:
        return None
    return match.groupdict()


def detect_prompt(tail: str, last_answer: str = "") -> Optional[str]:
    """Detect a pending inquire prompt in the unterminated output tail.

    Returns the prompt label (e.g. "Username", "Password", "Enter the next
    tokencode") or None when the tail is not a prompt waiting for input.
    """
    candidate = tail.strip()
    if not candidate.startswith("?"):
        return None
    body = candidate[1:].strip()
    if not body:
        return None
    # Skip echo of an answer we already typed (visible for Text prompts)
    if last_answer and last_answer in body:
        return None
    if ":" in body:
        label, _, after_colon = body.rpartition(":")
        # "? Password: ******" (masked echo) or a finalized "? User: john"
        # line is not a prompt waiting for input
        if after_colon.strip():
            return None
        label = label.strip()
    else:
        # MFA / OTP prompts use a server-provided message with no colon
        label = body
    return label or None


def classify_prompt(label: str) -> str:
    """Classify a prompt label as 'username' or 'password' (any secret)"""
    lowered = label.lower()
    if any(word in lowered for word in USERNAME_LABEL_WORDS):
        return "username"
    return "password"


def is_one_time_secret(text: str) -> bool:
    """True when the label/banner suggests a one-time secret (RSA token, OTP).

    One-time secrets must never be answered from a stored password - the user
    has to be asked every time.
    """
    lowered = text.lower()
    return any(word in lowered for word in ONE_TIME_SECRET_WORDS)


def detect_select_prompt(lines: List[str]) -> Optional[Dict[str, Any]]:
    """Detect an inquire Select frame (the gateway list) in the output lines.

    Returns a dict with the question, the visible options in render order, the
    index of the highlighted one and whether the list is longer than the page,
    or None when the tail of the output is not a Select frame.

    Works on raw (unstripped) lines: the one-character option marker is only
    distinguishable from an option whose name starts with the same letter by
    its position (marker, space, value).
    """
    frame = [line.rstrip() for line in lines if line.strip()]
    if not frame or not SELECT_HELP_RE.match(frame[-1].strip()):
        return None

    window = frame[-SELECT_FRAME_MAX_LINES:-1]

    prompt_index = None
    for index in range(len(window) - 1, -1, -1):
        if window[index].lstrip().startswith("?"):
            prompt_index = index
            break
    if prompt_index is None:
        return None

    message = window[prompt_index].lstrip()[1:].strip()
    options: List[str] = []
    cursor = 0
    more = False

    for line in window[prompt_index + 1 :]:
        if len(line) < 2 or line[0] not in SELECT_OPTION_MARKERS or line[1] != " ":
            continue
        marker, text = line[0], line[2:].strip()
        if not text:
            continue
        if marker == ">":
            cursor = len(options)
        elif marker in "^v":
            # Scroll marker: the list continues above/below the visible page
            more = True
        options.append(text)

    if not message or not options:
        return None

    return {"message": message, "options": options, "cursor": cursor, "more": more}


def pick_gateway(options: List[str], preferred: str) -> Optional[str]:
    """Pick the option matching `preferred`, most specific match first.

    Options look like "name (host.example.com)", so an exact match is tried
    against the whole entry, then against the name and host parts separately,
    and only then as a substring. Returns None when nothing matches.
    """
    wanted = (preferred or "").strip().lower()
    if not wanted:
        return options[0] if options else None

    for option in options:
        if option.strip().lower() == wanted:
            return option

    for option in options:
        name, _, host = option.partition("(")
        if name.strip().lower() == wanted or host.strip(") ").lower() == wanted:
            return option

    for option in options:
        if wanted in option.strip().lower():
            return option

    return None


def gateway_matches(preferred: str, option: str) -> bool:
    """True when `option` ("name (host)") is what `preferred` asks for"""
    wanted = (preferred or "").strip().lower()
    if not wanted:
        return False

    candidate = option.strip().lower()
    if wanted == candidate:
        return True

    name, _, host = option.partition("(")
    if wanted == name.strip().lower() or wanted == host.strip(") ").lower():
        return True

    return wanted in candidate


def resolve_browser(value: str) -> Tuple[str, Optional[str]]:
    """Map the connection's `browser` setting to what gpclient should launch.

    Returns (argument for gpclient --browser, GP_BROWSER for the wrapper).
    Friendly names and the browser binaries our editors used to offer go
    through the wrapper; anything else (a user's own wrapper script) is passed
    through untouched.
    """
    value = (value or "").strip()

    if value == LEGACY_EDGE_WRAPPER:
        # Compatibility shim, it knows which browser to launch
        return value, None

    if not value:
        target = "edge"  # historical default
    elif value.lower() in BROWSER_ALIASES:
        target = BROWSER_ALIASES[value.lower()]
    elif WRAPPED_BROWSER_PATH_RE.match(value):
        target = value
    else:
        return value, None

    if os.path.exists(BROWSER_WRAPPER):
        return BROWSER_WRAPPER, target

    logger.warning(
        f"{BROWSER_WRAPPER} is missing - letting gpclient launch the browser "
        "directly (no session environment fixup, the auth window may not open)"
    )
    if target.startswith("/"):
        return target, None
    for candidate in BROWSER_BINARIES.get(target, ()):
        path = candidate if candidate.startswith("/") else shutil.which(candidate)
        if path and os.path.exists(path):
            return path, None
    return target, None


class OutputScanner:
    """Split a raw PTY output stream into complete lines and a pending tail.

    inquire redraws prompt lines using \\r, so both \\r and \\n are treated as
    line separators; the last unterminated segment is the tail (a potential
    prompt waiting for input).
    """

    def __init__(self):
        self._tail = ""

    @property
    def tail(self) -> str:
        return self._tail

    def feed(self, text: str) -> List[str]:
        """Feed decoded output, return newly completed lines"""
        pending = self._tail + text
        segments = re.split(r"[\r\n]", pending)
        self._tail = segments[-1]
        return [seg for seg in segments[:-1] if seg.strip()]


class GpclientVPNPlugin(DbusInterfaceCommonAsync, interface_name=NM_DBUS_INTERFACE_VPN):
    """NetworkManager VPN Plugin for gpclient using python-sdbus"""

    def __init__(self):
        super().__init__()
        self.gpclient_process = None
        self.tunnel_check_task = None
        self.stdout_monitor_task = None
        self.dns_servers = []
        self.dns_domains = []
        self.gateway = None
        self.browser = None
        self.browser_target = None  # GP_BROWSER passed to the wrapper
        self.hip_enabled = True  # HIP enabled by default
        self._state = NM_VPN_SERVICE_STATE_INIT

        # Legacy TLS renegotiation workaround (issue #2)
        self.fix_openssl_mode = "auto"  # auto | true | false
        self.fix_openssl = False  # pass --fix-openssl to gpclient
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False  # profile told not to save the passcode

        # Portal / gateway selection (issue #7)
        self.as_gateway = False
        self.preferred_gateway = ""
        self._connection_uuid = ""
        self._gateway_list: List[str] = []  # discovered during this attempt
        self._stored_gateway_list = ""  # what the profile already has cached
        self._answered_select = None  # message of the Select we answered

        # Routing configuration
        self.never_default = False
        self.ignore_auto_routes = False
        self.custom_routes = []

        # Tunnel-candidate interfaces (with their IPs) that already existed
        # when Connect() started - these must never be picked up by tunnel
        # detection (stale gpd0 from a crashed session, another VPN's tun0;
        # issue #7)
        self._preexisting_ifaces = {}

        # Interactive authentication state (issue #6: RSA token / standard
        # login portals where gpclient prompts on its terminal)
        self.vpn_username = ""
        self.vpn_password = ""
        self._interactive = False
        self._pty_master = None
        self._pty_transport = None
        self._output_scanner = OutputScanner()
        self._ansi_carry = ""  # incomplete escape sequence from the last read
        # Recent complete output lines, used to recognise multi-line prompts
        # (the inquire Select frame with the gateway list) and prompts that
        # inquire has already terminated with a newline
        self._recent_lines: deque = deque(maxlen=96)
        self._line_counter = 0  # monotonic count of complete lines seen
        self._answered_at_line = -1  # line count when we last answered a prompt
        self._auth_banner = None  # last "message (Portal: server)" banner
        self._prompt_task = None  # debounce task for prompt handling
        self._answering = False  # a prompt is currently being answered
        self._secret_future = None  # pending SecretsRequired -> NewSecrets
        self._last_answer = ""  # last answer written to the PTY
        # Per-phase prompt state (a "phase" is one Portal/Gateway auth round,
        # delimited by the auth banner). gpclient asks username then password;
        # any prompt after the password is a follow-up challenge (MFA).
        self._phase_key = None
        self._username_prefilled = False
        self._answered_username = False
        self._answered_password = False
        self._login_failed = False

        logger.info("GpclientVPNPlugin initialized with python-sdbus")

    @dbus_method_async("a{sa{sv}}", "s")
    async def NeedSecrets(self, settings: Dict[str, Dict[str, Tuple[str, Any]]]) -> str:
        """Check if additional secrets are needed for connection

        Args:
            settings: Dictionary with VPN connection settings

        Returns:
            String with setting name that needs secrets, or empty string if none needed
        """
        logger.debug("=== NeedSecrets() called ===")
        logger.debug(f"Settings data: {settings}")

        # SAML (default): authentication happens in the browser via gpauth,
        # no secrets needed upfront.
        #
        # Standard login portals (auth-mode=credentials): ask NM to collect
        # the password upfront so a fully stored username/password connection
        # works non-interactively. One-time challenges (RSA token, OTP) are
        # requested mid-connection via SecretsRequired instead.
        data, secrets = self._parse_vpn_section(settings)

        if data.get("auth-mode", "saml") != "credentials":
            logger.info("NeedSecrets(): SAML mode, no secrets needed")
            return ""

        # password-flags: 4 = NOT_REQUIRED
        if data.get("password-flags", "0") == "4":
            logger.info("NeedSecrets(): password not required")
            return ""

        if secrets.get("password"):
            logger.info("NeedSecrets(): password already present")
            return ""

        logger.info("NeedSecrets(): credentials mode, requesting 'vpn' secrets")
        return "vpn"

    @staticmethod
    def _parse_vpn_section(
        connection: Dict[str, Dict[str, Tuple[str, Any]]],
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Extract (data, secrets) dicts from a connection's vpn section"""
        vpn_section = connection.get("vpn", {})
        vpn_data = {}
        for key, value in vpn_section.items():
            if isinstance(value, tuple) and len(value) == 2:
                vpn_data[key] = value[1]
            else:
                vpn_data[key] = value
        data = vpn_data.get("data", {}) or {}
        secrets = vpn_data.get("secrets", {}) or {}
        return data, secrets

    @dbus_method_async("a{sa{sv}}")
    async def Connect(self, connection: Dict[str, Dict[str, Tuple[str, Any]]]) -> None:
        """Connect to VPN

        Args:
            connection: Dictionary with VPN connection settings
        """
        logger.info("=== Connect() called ===")
        await self._do_connect(connection, interactive=False)

    async def _do_connect(
        self,
        connection: Dict[str, Dict[str, Tuple[str, Any]]],
        interactive: bool,
    ) -> None:
        """Shared implementation for Connect() and ConnectInteractive()"""
        logger.debug(f"Full connection data: {connection}")
        self._interactive = interactive
        self._auth_banner = None
        self._answering = False
        self._secret_future = None
        self._last_answer = ""
        self._phase_key = None
        self._username_prefilled = False
        self._answered_username = False
        self._answered_password = False
        self._login_failed = False
        self._output_scanner = OutputScanner()
        self._ansi_carry = ""
        self._recent_lines.clear()
        self._line_counter = 0
        self._answered_at_line = -1
        self._answered_select = None
        self._gateway_list = []
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False

        try:
            # Extract VPN data
            if "vpn" not in connection:
                raise Exception("No VPN data in connection")

            vpn_section = connection["vpn"]

            # Parse data from vpn section
            vpn_data = {}
            for key, value in vpn_section.items():
                if isinstance(value, tuple) and len(value) == 2:
                    vpn_data[key] = value[1]
                else:
                    vpn_data[key] = value

            logger.debug(f"Parsed VPN data: {vpn_data}")

            # Parse IPv4 routing configuration
            ipv4_section = connection.get("ipv4", {})
            ipv4_config = {}
            for key, value in ipv4_section.items():
                if isinstance(value, tuple) and len(value) == 2:
                    ipv4_config[key] = value[1]
                else:
                    ipv4_config[key] = value

            # Get never-default option
            self.never_default = ipv4_config.get("never-default", False)
            logger.info(f"never-default: {self.never_default}")
            self.ignore_auto_routes = ipv4_config.get("ignore-auto-routes", False)
            logger.info(f"ignore-auto-routes: {self.ignore_auto_routes}")

            self.custom_routes = []
            # Prefer route-data if present
            route_data = ipv4_config.get("route-data")
            if route_data:
                for route in route_data:
                    dest_raw = route.get("dest", "")
                    prefix_raw = route.get("prefix", 0)
                    dest = dest_raw[1] if isinstance(dest_raw, tuple) else dest_raw
                    prefix = (
                        prefix_raw[1] if isinstance(prefix_raw, tuple) else prefix_raw
                    )
                    if dest:
                        self.custom_routes.append((dest, int(prefix)))
            else:
                # Fallback: parse "routes" (aau): [dest_u32, prefix, next_hop_u32, metric]
                routes = ipv4_config.get("routes", [])
                for r in routes:
                    if not (isinstance(r, (list, tuple)) and len(r) >= 2):
                        continue
                    dest_u32 = int(r[0])
                    prefix = int(r[1])
                    try:
                        prefix = int(prefix)
                    except Exception:
                        prefix = 32

                    # IMPORTANT: NM uses host order uint32 here on little-endian machines
                    dest_ip = socket.inet_ntoa(struct.pack("<I", dest_u32))

                    self.custom_routes.append((dest_ip, prefix))
                    logger.info(f"Custom route: {dest_ip}/{prefix}")

            # Get the actual data dictionary
            data_dict = vpn_data.get("data", {})
            logger.debug(f"Data dict: {data_dict}")

            # Stored credentials for standard (non-SAML) login portals.
            # Username lives in vpn.data, password in vpn.secrets.
            secrets_dict = vpn_data.get("secrets", {}) or {}
            self.vpn_username = data_dict.get("username", "")
            self.vpn_password = secrets_dict.get("password", "")
            # When a username is stored we pass it as gpclient --user, so
            # gpclient does not prompt for it - treat username as already
            # answered in every auth phase.
            self._username_prefilled = bool(self.vpn_username)
            self._reset_phase_state()
            if self.vpn_username:
                logger.info(f"Stored username: {self.vpn_username}")
            if self.vpn_password:
                logger.info("Stored password: <present>")

            # Connection UUID, needed to cache the gateway list in the profile
            connection_section = connection.get("connection", {})
            uuid_raw = connection_section.get("uuid", "")
            self._connection_uuid = (
                uuid_raw[1] if isinstance(uuid_raw, tuple) else uuid_raw
            ) or ""

            # Get the server address (required). This is the portal address, or
            # a gateway address when as-gateway is set.
            self.gateway = data_dict.get("gateway", "")
            if not self.gateway:
                raise Exception("No gateway specified")
            logger.info(f"Server: {self.gateway}")

            # Legacy TLS renegotiation workaround (issue #2): auto retries once
            # after gpclient reports the error, true passes the flag from the
            # start, false disables the workaround entirely
            self.fix_openssl_mode = data_dict.get("fix-openssl", "auto").lower()
            if self.fix_openssl_mode not in ("auto", "true", "false"):
                logger.warning(
                    f"Unknown fix-openssl value {self.fix_openssl_mode!r}, "
                    "falling back to 'auto'"
                )
                self.fix_openssl_mode = "auto"
            self.fix_openssl = self.fix_openssl_mode == "true"
            logger.info(f"fix-openssl: {self.fix_openssl_mode}")

            # Portal / gateway handling (issue #7)
            self.as_gateway = data_dict.get("as-gateway", "false").lower() == "true"
            self.preferred_gateway = data_dict.get("preferred-gateway", "").strip()
            self._stored_gateway_list = data_dict.get("gateway-list", "").strip()
            logger.info(f"Treat server as gateway: {self.as_gateway}")
            logger.info(
                "Preferred gateway: "
                + (self.preferred_gateway or "<first proposed by the portal>")
            )

            # Get browser (optional). Friendly names and the known browser
            # binaries go through our wrapper, which fixes up the session
            # environment and the auth window lifetime.
            self.browser, self.browser_target = resolve_browser(
                data_dict.get("browser", "")
            )
            logger.info(f"Browser: {self.browser} (target: {self.browser_target})")

            # Get DNS servers (optional)
            dns_str = data_dict.get("dns", "")
            if dns_str:
                self.dns_servers = [s.strip() for s in dns_str.split(";") if s.strip()]
                logger.info(f"DNS servers configured: {self.dns_servers}")

            # Get custom DNS domains (optional)
            dns_domains_str = data_dict.get("dns-domains", "")
            if dns_domains_str:
                self.dns_domains = [
                    d.strip() for d in dns_domains_str.split() if d.strip()
                ]
                logger.info(f"Custom DNS domains configured: {self.dns_domains}")

            # Get HIP setting (default: enabled)
            hip_str = data_dict.get("hip", "true")
            self.hip_enabled = hip_str.lower() == "true"
            logger.info(f"HIP enabled: {self.hip_enabled}")

            # Emit state change: preparing
            self.StateChanged.emit(NM_VPN_SERVICE_STATE_STARTING)

            # Clean up a stale gpd0 left by a crashed previous session and
            # snapshot the tunnel-candidate interfaces that exist BEFORE
            # gpclient starts, so tunnel detection cannot pick up a stale
            # or foreign interface (issue #7)
            await self._cleanup_stale_gpd0()
            self._preexisting_ifaces = await self._snapshot_tunnel_interfaces()

            # Start gpclient process
            success = await self._start_gpclient()

            if not success:
                raise Exception("Failed to start gpclient process")

            # Start monitoring for tunnel interface
            self.tunnel_check_task = asyncio.create_task(self._check_tunnel_loop())

            logger.info("Connect() completed successfully")

        except Exception as e:
            logger.error(f"Connect() failed: {e}")
            self._emit_failure(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)
            raise

    @dbus_method_async()
    async def Disconnect(self) -> None:
        """Disconnect from VPN"""
        logger.info("Disconnect() called")

        # Stop tunnel monitoring
        if self.tunnel_check_task:
            self.tunnel_check_task.cancel()
            try:
                await self.tunnel_check_task
            except asyncio.CancelledError:
                pass
            self.tunnel_check_task = None

        # Stop pending prompt handling
        if self._prompt_task:
            self._prompt_task.cancel()
            self._prompt_task = None

        # Cancel any pending interactive secrets request
        if self._secret_future and not self._secret_future.done():
            self._secret_future.cancel()
        self._secret_future = None

        # Stop stdout monitoring
        if self.stdout_monitor_task:
            self.stdout_monitor_task.cancel()
            try:
                await self.stdout_monitor_task
            except asyncio.CancelledError:
                pass
            self.stdout_monitor_task = None

        # Close the PTY
        self._close_pty()

        # Kill gpclient process
        if self.gpclient_process:
            try:
                logger.info(
                    f"Terminating gpclient process (PID: {self.gpclient_process.pid})"
                )
                self.gpclient_process.terminate()
                try:
                    await asyncio.wait_for(self.gpclient_process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("gpclient didn't terminate, killing it")
                    self.gpclient_process.kill()
                    try:
                        await asyncio.wait_for(self.gpclient_process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        logger.error("gpclient process refused to die after SIGKILL")
            except Exception as e:
                logger.error(f"Error terminating gpclient: {e}")

        # Also run gpclient disconnect command
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/gpclient", "disconnect"
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception as e:
            logger.error(f"Error running 'gpclient disconnect': {e}")

        # Clean up
        self.gpclient_process = None
        self.dns_servers = []
        self.hip_enabled = True
        self.never_default = False
        self.custom_routes = []
        self.browser_target = None
        self.fix_openssl_mode = "auto"
        self.fix_openssl = False
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False
        self.as_gateway = False
        self.preferred_gateway = ""
        self._connection_uuid = ""
        self._gateway_list = []
        self._stored_gateway_list = ""
        self._answered_select = None
        self._recent_lines.clear()
        self._line_counter = 0
        self._answered_at_line = -1
        self.vpn_username = ""
        self.vpn_password = ""
        self._interactive = False
        self._auth_banner = None
        self._answering = False
        self._last_answer = ""
        self._phase_key = None
        self._username_prefilled = False
        self._answered_username = False
        self._answered_password = False
        self._login_failed = False
        self._preexisting_ifaces = {}

        # Emit state change
        self.StateChanged.emit(NM_VPN_SERVICE_STATE_STOPPED)

        logger.info("Disconnected from VPN")

    @dbus_method_async("a{sv}")
    async def SetConfig(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Set configuration (optional, for compatibility)"""
        logger.info(f"SetConfig() called with: {config}")

    @dbus_method_async("a{sv}")
    async def SetIp4Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Set IPv4 configuration (optional, for compatibility)"""
        logger.info(f"SetIp4Config() called")

    @dbus_method_async("a{sv}")
    async def SetIp6Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Set IPv6 configuration (optional, for compatibility)"""
        logger.info(f"SetIp6Config() called")

    @dbus_method_async("s")
    async def SetFailure(self, reason: str) -> None:
        """Set failure (optional, for compatibility)"""
        logger.error(f"SetFailure() called with: {reason}")

    @dbus_method_async("a{sa{sv}}a{sv}")
    async def ConnectInteractive(
        self,
        connection: Dict[str, Dict[str, Tuple[str, Any]]],
        details: Dict[str, Tuple[str, Any]],
    ) -> None:
        """Connect with support for interactive secrets requests.

        When gpclient hits an interactive challenge (RSA token, OTP, standard
        login) we emit SecretsRequired and receive the answer via NewSecrets.
        """
        logger.info("=== ConnectInteractive() called ===")
        await self._do_connect(connection, interactive=True)

    @dbus_method_async("a{sa{sv}}")
    async def NewSecrets(
        self, connection: Dict[str, Dict[str, Tuple[str, Any]]]
    ) -> None:
        """Secrets provided by NetworkManager after a SecretsRequired signal"""
        logger.info("NewSecrets() called")
        data, secrets = self._parse_vpn_section(connection)
        logger.debug(f"NewSecrets keys: {list(secrets.keys())}")

        # Refresh stored credentials in case the user (re)entered them
        if data.get("username"):
            self.vpn_username = data["username"]

        if self._secret_future and not self._secret_future.done():
            self._secret_future.set_result(secrets)
        else:
            logger.warning("NewSecrets() received but no secret request pending")

    # Signals
    @dbus_signal_async("u")
    def StateChanged(self, state: int) -> None:
        """Signal: VPN state changed"""
        logger.info(f"StateChanged signal: {state}")
        self._state = state

    @dbus_signal_async("sas")
    def SecretsRequired(self, message: str, secrets: List[str]) -> None:
        """Signal: Secrets required"""
        logger.info(f"SecretsRequired signal: {message}, {secrets}")

    @dbus_signal_async("a{sv}")
    def Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: Configuration ready"""
        logger.info(f"Config signal: {config}")

    @dbus_signal_async("a{sv}")
    def Ip4Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: IPv4 configuration ready"""
        logger.info(f"Ip4Config signal: {config}")

    @dbus_signal_async("a{sv}")
    def Ip6Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: IPv6 configuration ready"""
        logger.info(f"Ip6Config signal: {config}")

    @dbus_signal_async("s")
    def LoginBanner(self, banner: str) -> None:
        """Signal: Login banner"""
        logger.info(f"LoginBanner signal: {banner}")

    @dbus_signal_async("u")
    def Failure(self, reason: int) -> None:
        """Signal: VPN connection failed"""
        logger.error(f"Failure signal: {reason}")

    # Properties
    @dbus_property_async("u")
    def State(self) -> int:
        """Property: Current VPN state"""
        return self._state

    def _get_real_user(self) -> Tuple[int, str, str]:
        """Get real user info when running as root"""
        import pwd

        # First try SUDO_UID/SUDO_USER
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_user = os.environ.get("SUDO_USER")

        if sudo_uid and sudo_user:
            uid = int(sudo_uid)
            username = sudo_user
            try:
                pw = pwd.getpwuid(uid)
                home = pw.pw_dir
            except:
                home = f"/home/{username}"
            return uid, username, home

        # Try to find logged-in user from loginctl
        try:
            result = subprocess.run(
                ["loginctl", "list-users", "--no-legend"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Parse first non-root user
                for line in result.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 2:
                        uid_str = parts[0]
                        username = parts[1]
                        if username != "root":
                            uid = int(uid_str)
                            try:
                                pw = pwd.getpwuid(uid)
                                home = pw.pw_dir
                                logger.debug(
                                    f"Found user via loginctl: {username} (UID: {uid})"
                                )
                                return uid, username, home
                            except:
                                pass
        except Exception as e:
            logger.debug(f"loginctl failed: {e}")

        # Fallback to current user
        uid = os.getuid()
        username = os.environ.get("USER", "root")
        home = os.environ.get("HOME", f"/home/{username}")
        return uid, username, home

    @staticmethod
    def _read_proc_environ(pid: str) -> Dict[str, str]:
        """Parse /proc/<pid>/environ into a dict (empty when unreadable)"""
        try:
            with open(f"/proc/{pid}/environ", "rb") as handle:
                raw = handle.read()
        except OSError:
            return {}

        environ = {}
        for entry in raw.split(b"\0"):
            if not entry or b"=" not in entry:
                continue
            key, _, value = entry.partition(b"=")
            environ[key.decode("utf-8", errors="replace")] = value.decode(
                "utf-8", errors="replace"
            )
        return environ

    def _scan_session_env(
        self, real_uid: int, already_found: Dict[str, str]
    ) -> Dict[str, str]:
        """Find session variables in any process the user owns.

        Fallback for desktops whose session leader is not in
        SESSION_LEADER_PROCESSES: the first process that exposes a display wins,
        and the remaining missing keys are taken from it as well.
        """
        found: Dict[str, str] = {}

        try:
            pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
        except OSError as e:
            logger.debug(f"Cannot list /proc: {e}")
            return found

        for pid in pids:
            try:
                if os.stat(f"/proc/{pid}").st_uid != real_uid:
                    continue
            except OSError:
                continue

            proc_environ = self._read_proc_environ(pid)
            if not (proc_environ.get("DISPLAY") or proc_environ.get("WAYLAND_DISPLAY")):
                continue

            for key in SESSION_ENV_KEYS:
                if key not in already_found and proc_environ.get(key):
                    found[key] = proc_environ[key]

            try:
                with open(f"/proc/{pid}/comm") as handle:
                    name = handle.read().strip()
            except OSError:
                name = "?"
            logger.info(
                f"Session display taken from a running process: {name} ({pid})"
            )
            break

        return found

    def _get_session_env(self, real_uid: int, real_home: str) -> Dict[str, str]:
        """Collect the user's graphical session environment.

        NetworkManager starts us without any session context, so the values are
        read from the processes that own the session (SESSION_LEADER_PROCESSES,
        most specific first). Only SESSION_ENV_KEYS are taken and the first
        process that provides a key wins.
        """
        session_env: Dict[str, str] = {}

        for process in SESSION_LEADER_PROCESSES:
            if len(session_env) == len(SESSION_ENV_KEYS):
                break
            try:
                result = subprocess.run(
                    ["pgrep", "-u", str(real_uid), "-x", process],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except Exception as e:
                logger.debug(f"pgrep for {process} failed: {e}")
                continue
            if result.returncode != 0:
                continue

            for pid in result.stdout.split():
                proc_environ = self._read_proc_environ(pid)
                for key in SESSION_ENV_KEYS:
                    if key not in session_env and proc_environ.get(key):
                        session_env[key] = proc_environ[key]
                        logger.debug(f"Session {key} taken from {process} ({pid})")

        # No display from the known session leaders - the user may run a desktop
        # we don't have on the list, so look at everything they have running
        # (issue #2: only XDG_* keys were found, and the browser had no display)
        if "DISPLAY" not in session_env and "WAYLAND_DISPLAY" not in session_env:
            session_env.update(self._scan_session_env(real_uid, session_env))

        # Fallbacks that don't need a session process
        runtime_dir = session_env.get("XDG_RUNTIME_DIR") or f"/run/user/{real_uid}"
        if os.path.isdir(runtime_dir):
            session_env["XDG_RUNTIME_DIR"] = runtime_dir
            bus_path = f"{runtime_dir}/bus"
            if "DBUS_SESSION_BUS_ADDRESS" not in session_env and os.path.exists(
                bus_path
            ):
                session_env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

        if "XAUTHORITY" not in session_env:
            xauthority = f"{real_home}/.Xauthority"
            if os.path.exists(xauthority):
                session_env["XAUTHORITY"] = xauthority

        return session_env

    async def _start_gpclient(self) -> bool:
        """Start gpclient process"""
        try:
            # Get real user first (needed for pkill filter)
            real_uid, real_user, real_home = self._get_real_user()
            logger.info(f"Will run gpclient as user: {real_user}")

            # Kill any hanging gpauth processes first (filtered by user for security)
            try:
                subprocess.run(
                    ["pkill", "-9", "-u", str(real_uid), "gpauth"], timeout=2
                )
                logger.debug(
                    f"Killed any hanging gpauth processes for user {real_user}"
                )
            except Exception as e:
                logger.debug(f"No gpauth processes to kill: {e}")

            # Build command.
            #
            # NOTE: --gateway is deliberately NOT passed. It used to be set to
            # the server address, which makes gpclient look that address up in
            # the portal's gateway list and abort with "Cannot find gateway
            # specified" for every real portal (issue #7). Instead we answer
            # gpclient's gateway prompt ourselves, which also lets us fall back
            # to the first proposal when the configured gateway is gone.
            cmd = [GPCLIENT_BINARY]

            # Global flag, must come before the subcommand (issue #2)
            if self.fix_openssl:
                cmd.append("--fix-openssl")

            cmd.append("connect")

            # Add --hip flag if enabled
            if self.hip_enabled:
                cmd.append("--hip")

            # The server is a gateway, not a portal: skip the portal workflow
            # so the user is not authenticated twice
            if self.as_gateway:
                cmd.append("--as-gateway")

            # Pass stored username so standard-login portals don't prompt for it
            if self.vpn_username:
                cmd.extend(["--user", self.vpn_username])

            cmd.extend(["--browser", self.browser, self.gateway])

            logger.info(f"Spawning: {' '.join(cmd)}")

            # Set up environment
            env = os.environ.copy()

            # Set SUDO_UID for gpclient to detect real user
            if real_uid > 0:
                env["SUDO_UID"] = str(real_uid)
                env["SUDO_USER"] = real_user

                # Import the user's graphical session environment so gpauth can
                # actually open the SAML browser (issue #7)
                session_env = self._get_session_env(real_uid, real_home)
                env.update(session_env)
                logger.info(
                    f"Environment: SUDO_UID={real_uid}, session keys: "
                    f"{sorted(session_env)}"
                )
                if "DISPLAY" not in session_env and "WAYLAND_DISPLAY" not in session_env:
                    logger.warning(
                        "No DISPLAY/WAYLAND_DISPLAY found in the user's session - "
                        "the authentication browser may fail to open a window"
                    )

            # Tell the wrapper which browser to launch
            if self.browser_target:
                env["GP_BROWSER"] = self.browser_target

            env["GPCLIENT_NM_IGNORE_AUTO_ROUTES"] = (
                "1" if self.ignore_auto_routes else "0"
            )
            env["GPCLIENT_NM_NEVER_DEFAULT"] = "1" if self.never_default else "0"

            # Export custom DNS domains for vpnc hook
            if self.dns_domains:
                env["GPCLIENT_CUSTOM_DNS_DOMAINS"] = " ".join(self.dns_domains)
                logger.info(
                    f"Exporting custom DNS domains: {env['GPCLIENT_CUSTOM_DNS_DOMAINS']}"
                )

            # Spawn gpclient under a PTY. Standard-login portals (RSA token
            # challenges, issue #6) make gpclient prompt interactively via the
            # `inquire` crate, which needs a real terminal. With a plain pipe
            # those prompts fail/hang; with a PTY we can detect them in the
            # output and answer via NM's secrets flow.
            env["TERM"] = "xterm-256color"

            master_fd, slave_fd = pty.openpty()
            # Wide window so prompts don't wrap mid-line
            fcntl.ioctl(
                master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 200, 0, 0)
            )

            def _child_setup():
                # New session + make the PTY slave (fd 0) the controlling
                # terminal so /dev/tty works inside gpclient
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            try:
                self.gpclient_process = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    preexec_fn=_child_setup,
                )
            finally:
                os.close(slave_fd)

            self._pty_master = master_fd

            logger.info(f"Started gpclient with PID {self.gpclient_process.pid}")

            # Start monitoring PTY output
            self.stdout_monitor_task = asyncio.create_task(
                self._monitor_gpclient_output()
            )

            return True

        except Exception as e:
            logger.error(f"Failed to start gpclient: {e}")
            return False

    async def _monitor_gpclient_output(self) -> None:
        """Monitor gpclient PTY output for messages and interactive prompts"""
        if not self.gpclient_process or self._pty_master is None:
            return

        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        pty_file = os.fdopen(self._pty_master, "rb", buffering=0)

        try:
            self._pty_transport, _ = await loop.connect_read_pipe(
                lambda: protocol, pty_file
            )
        except Exception as e:
            logger.error(f"Failed to attach PTY reader: {e}")
            pty_file.close()
            self._pty_master = None
            # Without the reader we can neither detect prompts nor see gpclient
            # finish, so fail the activation and stop gpclient instead of
            # leaving NM stuck in STARTING with an orphaned process (review #9).
            self._fail_login(f"Failed to attach PTY reader: {e}")
            return

        last_logged_line = None

        try:
            while True:
                try:
                    chunk = await reader.read(4096)
                except OSError:
                    # PTY master raises EIO when the child exits
                    break
                if not chunk:
                    break

                lines = self._consume_output(
                    chunk.decode("utf-8", errors="replace")
                )

                # Once the answered prompt is committed as a full line (its
                # echo flushed), stop suppressing on the old answer - otherwise
                # a later prompt that merely contains it as a substring (e.g.
                # answer "code" vs "? Enter passcode:") is suppressed forever.
                if self._last_answer and any(
                    self._last_answer in ln for ln in lines
                ):
                    self._last_answer = ""

                for raw_line in lines:
                    # Keep the raw line: the Select frame's option marker is
                    # only recognisable by its position (marker, space, value)
                    self._recent_lines.append(raw_line)
                    self._line_counter += 1

                    line = raw_line.strip()
                    # inquire redraws lines on every keystroke; skip repeats
                    if line == last_logged_line:
                        continue
                    last_logged_line = line
                    logger.info(f"gpclient output: {line}")

                    chosen = GATEWAY_CHOSEN_RE.search(line)
                    if chosen:
                        self._record_gateways([chosen.group("gateway")])

                    if "--as-gateway" in line:
                        logger.warning(
                            "gpclient reports the server may be a gateway rather "
                            "than a portal - enable 'Address is a gateway' "
                            "(vpn.data as-gateway=true) in the connection "
                            "settings to authenticate only once"
                        )

                    if not self.fix_openssl and OPENSSL_LEGACY_ERROR_RE.search(line):
                        # The portal needs legacy TLS renegotiation (issue #2)
                        self._openssl_error_seen = True

                    banner = parse_auth_banner(line)
                    if banner:
                        logger.info(
                            f"Detected auth banner: {banner['message']} "
                            f"({banner['kind']}: {banner['server']})"
                        )
                        self._auth_banner = banner
                        # A new auth banner means a new Portal/Gateway round;
                        # reset per-phase username/password tracking so the
                        # gateway round can reuse the stored password once.
                        phase_key = (banner["kind"], banner["server"])
                        if phase_key != self._phase_key:
                            self._phase_key = phase_key
                            self._reset_phase_state()

                    # Check for connection success indicators
                    if any(
                        msg in line
                        for msg in [
                            "ESP tunnel connected",
                            "Connected to",
                            "Tunnel is up",
                            "VPN connected",
                        ]
                    ):
                        logger.info(
                            "Detected VPN connection message - checking for interface"
                        )

                self._schedule_prompt_check()

            # Process ended
            returncode = await self.gpclient_process.wait()
            logger.info(f"gpclient process exited with status {returncode}")

            if returncode != 0 and not self._login_failed:
                if await self._retry_with_openssl_fix():
                    # A new monitor task took over the retried process
                    return
                logger.error(f"gpclient failed with exit code {returncode}")
                self._emit_failure(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error monitoring gpclient output: {e}")

    def _consume_output(self, text: str) -> List[str]:
        """Clean a chunk of PTY output and return the lines it completed.

        An escape sequence cut in half by the read boundary is held back until
        the rest arrives; otherwise ESC is stripped as a control character and
        the remainder leaks into the text (issue #2: a prompt label that read
        "[39m Password").
        """
        text = self._ansi_carry + text
        self._ansi_carry = ""

        split = INCOMPLETE_ANSI_RE.search(text)
        if split:
            self._ansi_carry = split.group(0)
            text = text[: split.start()]

        return self._output_scanner.feed(strip_ansi(text))

    async def _retry_with_openssl_fix(self) -> bool:
        """Restart gpclient once with --fix-openssl after a legacy TLS error.

        The portal needs renegotiation that OpenSSL 3.x refuses, gpclient has
        the workaround built in and even prints the command to re-run - doing it
        here means nobody has to know the option exists (issue #2).
        """
        if not self._openssl_error_seen or self._openssl_retried:
            return False

        if self.fix_openssl_mode == "false":
            logger.warning(
                "The portal needs legacy TLS renegotiation, but fix-openssl is "
                "disabled for this connection"
            )
            return False

        logger.warning(
            "The portal needs legacy TLS renegotiation - retrying with "
            "--fix-openssl. If the connection comes up, this is remembered in "
            "the profile (fix-openssl=true), so the failed first attempt does "
            "not repeat"
        )

        self._openssl_retried = True
        self.fix_openssl = True

        # Drop the finished process and its PTY before starting over
        if self._prompt_task and not self._prompt_task.done():
            self._prompt_task.cancel()
        self._prompt_task = None
        self._close_pty()
        self.gpclient_process = None

        # Fresh output state for the new attempt
        self._output_scanner = OutputScanner()
        self._ansi_carry = ""
        self._recent_lines.clear()
        self._line_counter = 0
        self._answered_at_line = -1
        self._auth_banner = None
        self._answering = False
        self._last_answer = ""
        self._answered_select = None
        self._phase_key = None
        self._reset_phase_state()

        # Same groundwork as before the first attempt: whatever the failed run
        # left behind must not be mistaken for the new tunnel (issue #7)
        await self._cleanup_stale_gpd0()
        self._preexisting_ifaces = await self._snapshot_tunnel_interfaces()

        if await self._start_gpclient():
            return True

        logger.error("Failed to restart gpclient with --fix-openssl")
        return False

    def _close_pty(self) -> None:
        """Close the PTY master (and its transport, if a reader was attached)"""
        if self._pty_transport:
            try:
                self._pty_transport.close()
            except Exception as e:
                logger.debug(f"Error closing PTY transport: {e}")
            self._pty_transport = None
            self._pty_master = None
        elif self._pty_master is not None:
            try:
                os.close(self._pty_master)
            except OSError:
                pass
            self._pty_master = None

    def _emit_failure(self, reason: int) -> None:
        """Report a failed activation to NetworkManager.

        The STOPPED state matters: NetworkManager waits for the transition after
        a failure, and without it the activation sits there until its own
        connect timeout expires, which reads as a hang (issue #2).
        """
        self.Failure.emit(reason)
        self.StateChanged.emit(NM_VPN_SERVICE_STATE_STOPPED)

    def _schedule_prompt_check(self) -> None:
        """(Re)schedule the debounced check for a pending interactive prompt.

        Called after every output chunk: new output cancels the previous
        check, so we only act on a prompt once the output has been stable for
        PROMPT_DEBOUNCE_SECONDS.
        """
        # A prompt is already being answered (possibly waiting minutes for
        # the user via SecretsRequired) - don't touch the task that answers it
        if self._answering:
            return

        if self._prompt_task and not self._prompt_task.done():
            self._prompt_task.cancel()

        # A list prompt (the gateway list) is a whole frame of complete lines,
        # so it has to be checked before the tail-based text prompt detection -
        # otherwise "? Which gateway do you want to connect to?" would be
        # answered with the username.
        select_frame = detect_select_prompt(list(self._recent_lines))
        if select_frame is not None:
            if select_frame["message"] == self._answered_select:
                self._prompt_task = None
                return

            async def _debounced_select(line_count: int):
                await asyncio.sleep(PROMPT_DEBOUNCE_SECONDS)
                # Only act if no further output arrived (frame fully rendered)
                if len(self._recent_lines) != line_count:
                    return
                await self._handle_select_prompt(select_frame)

            self._prompt_task = asyncio.create_task(
                _debounced_select(len(self._recent_lines))
            )
            return

        label = detect_prompt(self._output_scanner.tail, self._last_answer)
        from_tail = label is not None

        if label is None:
            # inquire terminates the line it renders a prompt on (every backend
            # ends with new_line()), so with a real gpclient the pending prompt
            # is the last COMPLETE line and the tail is empty. Only act on a
            # line that appeared after our last answer, otherwise the redraw of
            # an answered prompt would be answered again.
            if self._line_counter > self._answered_at_line:
                label = detect_prompt(self._last_output_line(), self._last_answer)

        if label is None:
            self._prompt_task = None
            return

        async def _debounced(tail_snapshot: str, line_snapshot: int):
            await asyncio.sleep(PROMPT_DEBOUNCE_SECONDS)
            # Only act if the output has not moved on since we saw the prompt
            if self._output_scanner.tail != tail_snapshot:
                return
            if not from_tail and self._line_counter != line_snapshot:
                return
            await self._handle_prompt(label)

        self._prompt_task = asyncio.create_task(
            _debounced(self._output_scanner.tail, self._line_counter)
        )

    def _last_output_line(self) -> str:
        """Last non-empty line gpclient printed (stripped), or an empty string"""
        for raw_line in reversed(self._recent_lines):
            stripped = raw_line.strip()
            if stripped:
                return stripped
        return ""

    def _reset_phase_state(self) -> None:
        """Reset per-phase prompt tracking at the start of an auth round.

        Username counts as already answered when it was pre-filled via
        gpclient --user (a stored username), because gpclient then does not
        prompt for it.
        """
        self._answered_username = self._username_prefilled
        self._answered_password = False

    def _classify_prompt_kind(self, label: str, banner_msg: str) -> str:
        """Decide how to answer a prompt: 'otp', 'username' or 'password'.

        Combines label keywords with the prompt ORDER, which is a
        language-independent protocol invariant: gpclient asks username then
        password for a standard login, and any prompt after the password is a
        follow-up challenge (MFA / OTP). Order lets us do the right thing even
        for localized labels the English keyword lists don't match.
        """
        # One-time challenge, by keyword (label or banner) OR by position
        # (anything after we've already sent the password this phase).
        if (
            is_one_time_secret(label)
            or is_one_time_secret(banner_msg)
            or self._answered_password
        ):
            return "otp"

        # Username: by keyword, or positionally the first credential prompt
        # (we have not answered a username yet this phase).
        if classify_prompt(label) == "username" or not self._answered_username:
            return "username"

        return "password"

    async def _handle_prompt(self, label: str) -> None:
        """Answer an interactive gpclient prompt (username/password/OTP)"""
        logger.info(f"Detected interactive prompt: {label!r}")

        banner_msg = self._auth_banner["message"] if self._auth_banner else ""
        kind = self._classify_prompt_kind(label, banner_msg)

        # Anything already printed must not be taken for a new prompt again
        self._answered_at_line = self._line_counter
        self._answering = True
        try:
            if kind == "username":
                if self.vpn_username and not self._answered_username:
                    logger.info("Answering username prompt from stored username")
                    answer = self.vpn_username
                else:
                    answer = await self._request_secret_interactive(
                        "username", label, banner_msg
                    )
                self._answered_username = True
            elif kind == "otp":
                logger.info(
                    "Prompt looks like a one-time secret (token/OTP/challenge), "
                    "asking the user"
                )
                # Do this first: a passcode left in the profile would be handed
                # back by the agent without asking anyone (issue #2)
                await self._forget_one_time_secret()
                answer = await self._request_secret_interactive(
                    OTP_SECRET_KEY, label, banner_msg
                )
            else:
                if self.vpn_password and not self._answered_password:
                    logger.info("Answering password prompt from stored password")
                    answer = self.vpn_password
                else:
                    answer = await self._request_secret_interactive(
                        "password", label, banner_msg
                    )
                self._answered_password = True
        except Exception as e:
            logger.error(f"Cannot answer prompt {label!r}: {e}")
            self._fail_login(str(e))
            return
        finally:
            self._answering = False

        self._write_answer(answer)

    def _record_gateways(self, options: List[str]) -> None:
        """Remember gateways seen during this attempt, for the profile cache"""
        for option in options:
            # ';' separates cached entries and nmcli splits +vpn.data values on
            # commas, so neither may survive inside an entry
            entry = option.replace(",", " ").replace(GATEWAY_LIST_SEPARATOR, " ")
            entry = " ".join(entry.split())
            if entry and entry not in self._gateway_list:
                self._gateway_list.append(entry)

    async def _handle_select_prompt(self, frame: Dict[str, Any]) -> None:
        """Answer gpclient's gateway list without interrupting the user.

        The gateway comes from vpn.data preferred-gateway; with none configured
        (the default) the portal's first proposal wins. A configured gateway
        that the portal no longer offers falls back to the first proposal - the
        connection setting itself is left alone (issue #7).
        """
        options = frame["options"]
        self._answered_select = frame["message"]
        self._answered_at_line = self._line_counter
        self._answering = True
        try:
            self._record_gateways(options)
            logger.info(f"gpclient asks to choose: {frame['message']}")
            logger.info(
                f"Gateways offered: {options}"
                + (" (list continues past the visible page)" if frame["more"] else "")
            )

            preferred = self.preferred_gateway
            if not preferred:
                wanted = options[0]
                matches = lambda option: option == wanted  # noqa: E731
                logger.info(
                    f"No preferred gateway configured - taking the first "
                    f"proposal: {wanted!r}"
                )
            else:
                exact = None if frame["more"] else pick_gateway(options, preferred)
                if exact is not None:
                    wanted = exact
                    matches = lambda option: option == wanted  # noqa: E731
                    logger.info(f"Preferred gateway {preferred!r} matches {wanted!r}")
                elif frame["more"]:
                    # Cannot see the whole list yet - walk it looking for a match
                    wanted = preferred
                    matches = lambda option: gateway_matches(  # noqa: E731
                        preferred, option
                    )
                    logger.info(
                        f"Preferred gateway {preferred!r} is not on the visible "
                        "page - walking the list"
                    )
                else:
                    wanted = options[0]
                    matches = lambda option: option == wanted  # noqa: E731
                    logger.warning(
                        f"Preferred gateway {preferred!r} is not offered by the "
                        f"portal - falling back to the first proposal {wanted!r} "
                        "(the connection setting is left unchanged)"
                    )

            start_option = options[frame["cursor"]]
            current_frame = frame
            steps = 0

            while True:
                self._record_gateways(current_frame["options"])
                current = current_frame["options"][current_frame["cursor"]]

                if matches(current):
                    logger.info(f"Selecting gateway: {current!r}")
                    self._write_keys(KEY_ENTER, f"select {current!r}")
                    return

                if steps and current == start_option:
                    logger.warning(
                        f"Walked the whole list without finding {wanted!r} - "
                        f"selecting the first proposal {current!r}"
                    )
                    self._write_keys(KEY_ENTER, "select the first proposal")
                    return

                if steps >= SELECT_MAX_STEPS:
                    logger.warning(
                        f"Gave up after {steps} steps through the gateway list - "
                        f"selecting {current!r}"
                    )
                    self._write_keys(KEY_ENTER, f"select {current!r}")
                    return

                next_frame = await self._press_list_down()
                if next_frame is None:
                    logger.warning(
                        "gpclient stopped redrawing the gateway list - selecting "
                        f"the highlighted entry {current!r}"
                    )
                    self._write_keys(KEY_ENTER, f"select {current!r}")
                    return

                current_frame = next_frame
                steps += 1
        finally:
            self._answering = False

    async def _press_list_down(self) -> Optional[Dict[str, Any]]:
        """Move the list cursor one entry down, return the redrawn frame.

        Down wraps around in inquire, so this reaches every entry, including
        ones outside the visible page. None means gpclient did not redraw.
        """
        previous = detect_select_prompt(list(self._recent_lines))
        previous_option = (
            previous["options"][previous["cursor"]] if previous else None
        )

        self._write_keys(KEY_DOWN, "move down the gateway list")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SELECT_REDRAW_TIMEOUT
        while loop.time() < deadline:
            await asyncio.sleep(SELECT_POLL_INTERVAL)
            frame = detect_select_prompt(list(self._recent_lines))
            if frame is None:
                continue
            if frame["options"][frame["cursor"]] != previous_option:
                return frame
        return None

    async def _nmcli_modify(self, *arguments: str) -> bool:
        """Run `nmcli connection modify <uuid> ...` (best effort).

        `+vpn.data` sets a single key and leaves the rest of the dictionary
        alone, and NetworkManager writes the profile back wherever it lives
        (including netplan on Ubuntu). Nothing in the connect path depends on
        this succeeding, so failures are logged and swallowed.
        """
        if not self._connection_uuid:
            logger.debug(f"No connection UUID, skipping nmcli {arguments}")
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli",
                "connection",
                "modify",
                self._connection_uuid,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception as e:
            logger.warning(f"nmcli modify {arguments} failed: {e}")
            return False

        if proc.returncode != 0:
            logger.warning(
                f"nmcli modify {arguments} failed with {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )
            return False

        return True

    async def _write_vpn_data(self, key: str, value: str) -> bool:
        """Store one vpn.data key in the connection profile (best effort)"""
        return await self._nmcli_modify("+vpn.data", f"{key}={value}")

    async def _forget_one_time_secret(self) -> None:
        """Keep one-time codes out of the connection profile.

        A saved passcode is worse than none: the desktop agent answers the next
        connection from the stale value without asking, and the gateway rejects
        the whole login ("Invalid username or password" in the #2 report).
        Flag 2 is NM_SETTING_SECRET_FLAG_NOT_SAVED, which tells NetworkManager
        and the agents to ask every time and store nothing.
        """
        if self._otp_flags_written or not self._connection_uuid:
            return

        self._otp_flags_written = True
        logger.info(
            "Marking the one-time code as not-saved in the profile and dropping "
            "any stored value"
        )
        await self._write_vpn_data(f"{OTP_SECRET_KEY}-flags", "2")
        await self._nmcli_modify("-vpn.secrets", OTP_SECRET_KEY)

    async def _persist_gateway_list(self) -> None:
        """Cache the discovered gateway list in the connection profile.

        The connection editors read vpn.data gateway-list to offer a gateway
        drop-down; nothing in the connect path depends on it.
        """
        if not self._gateway_list:
            return

        value = GATEWAY_LIST_SEPARATOR.join(self._gateway_list)
        if value == self._stored_gateway_list:
            logger.debug("Gateway list unchanged, leaving the profile alone")
            return

        logger.info(f"Caching gateway list in the connection profile: {value}")
        if await self._write_vpn_data("gateway-list", value):
            self._stored_gateway_list = value

    async def _persist_fix_openssl(self) -> None:
        """Remember that this portal needs the legacy TLS workaround.

        We found out by retrying, so storing it means the next connection skips
        the failed first attempt - and the checkbox in the connection editor
        shows why (issue #2).
        """
        if not self._openssl_retried or not self.fix_openssl:
            return
        if self.fix_openssl_mode == "true":
            return  # already stored in the profile

        logger.info(
            "Storing fix-openssl=true in the connection profile - this portal "
            "needs legacy TLS renegotiation"
        )
        if await self._write_vpn_data("fix-openssl", "true"):
            self.fix_openssl_mode = "true"

    async def _request_secret_interactive(
        self, secret_key: str, label: str, banner_msg: str
    ) -> str:
        """Ask the user for a secret via SecretsRequired/NewSecrets.

        Emits the SecretsRequired signal with the requested secret name as a
        hint (plus an x-vpn-message: hint carrying the human-readable prompt)
        and waits for NetworkManager to deliver the answer via NewSecrets().
        """
        if not self._interactive:
            raise Exception(
                f"gpclient asked for {label!r} but the connection was not "
                "started interactively - cannot prompt the user. "
                "Store the credentials in the connection or activate it "
                "from a GUI applet."
            )

        server_part = ""
        if self._auth_banner:
            server_part = (
                f" ({self._auth_banner['kind']}: {self._auth_banner['server']})"
            )
        if banner_msg:
            message = f"{banner_msg}{server_part} - {label}"
        else:
            message = f"{label}{server_part}"

        logger.info(f"Requesting secret {secret_key!r} from user: {message}")

        loop = asyncio.get_running_loop()
        self._secret_future = loop.create_future()

        hints = [f"x-vpn-message:{message}", secret_key]
        self.SecretsRequired.emit((message, hints))

        try:
            secrets = await asyncio.wait_for(
                self._secret_future, timeout=SECRETS_REQUEST_TIMEOUT
            )
        except asyncio.TimeoutError:
            raise Exception(
                f"Timed out waiting for the user to provide {secret_key!r}"
            )
        finally:
            self._secret_future = None

        value = secrets.get(secret_key, "")
        if not value and len(secrets) == 1:
            # Some agents return the secret under a different key
            value = next(iter(secrets.values()))
        if not value:
            raise Exception(f"User did not provide {secret_key!r}")

        return value

    def _write_answer(self, answer: str) -> None:
        """Type an answer into gpclient's PTY"""
        self._last_answer = answer
        # inquire (crossterm raw mode) treats \r as Enter
        self._write_keys(answer.encode("utf-8") + KEY_ENTER, "answer")

    def _write_keys(self, data: bytes, description: str) -> None:
        """Send raw key bytes to gpclient's PTY"""
        if self._pty_master is None:
            logger.error(f"Cannot send {description}: PTY is gone")
            return
        try:
            os.write(self._pty_master, data)
            logger.debug(f"Sent {description} to gpclient")
        except OSError as e:
            logger.error(f"Failed to send {description} to PTY: {e}")

    def _fail_login(self, reason: str) -> None:
        """Emit a login failure and terminate gpclient"""
        logger.error(f"Login failed: {reason}")
        self._login_failed = True
        self._emit_failure(NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED)
        if self.gpclient_process:
            try:
                self.gpclient_process.terminate()
            except ProcessLookupError:
                pass

    async def _get_iface_ipv4(self, iface: str) -> Tuple[Any, int]:
        """Get the first IPv4 address of an interface.

        Returns:
            (ip_address, prefix) tuple; ip_address is None when the
            interface has no IPv4 address or the lookup failed.
        """
        try:
            result = await asyncio.create_subprocess_exec(
                "ip",
                "-4",
                "addr",
                "show",
                iface,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
        except Exception as e:
            logger.warning(f"Failed to get tunnel IP for {iface}: {e}")
            return None, 32

        ip_addr = None
        prefix = 32
        for line in stdout.decode("utf-8", errors="replace").split("\n"):
            if "inet " in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    addr_with_prefix = parts[1]
                    if "/" in addr_with_prefix:
                        try:
                            ip_addr, prefix_str = addr_with_prefix.split("/", 1)
                            prefix = int(prefix_str)
                        except ValueError:
                            ip_addr = addr_with_prefix
                            prefix = 32
                    else:
                        ip_addr = addr_with_prefix
                break
        return ip_addr, prefix

    async def _snapshot_tunnel_interfaces(self) -> Dict[str, Any]:
        """Record tunnel-candidate interfaces existing before gpclient starts.

        An interface recorded here (with an unchanged IP) is never accepted
        by _check_tunnel_loop: it is either a stale gpd0 from a crashed
        session or another VPN client's tunnel (issue #7).
        """
        snapshot = {}
        for iface in TUNNEL_INTERFACES:
            if os.path.exists(f"/sys/class/net/{iface}"):
                ip_addr, _ = await self._get_iface_ipv4(iface)
                snapshot[iface] = ip_addr
                logger.info(
                    f"Interface {iface} (IP: {ip_addr}) already exists before "
                    "gpclient start - it will be ignored by tunnel detection "
                    "unless its address changes"
                )
        return snapshot

    async def _cleanup_stale_gpd0(self) -> None:
        """Remove a leftover gpd0 interface from a previous session.

        gpd0 is created exclusively by gpclient and gpclient enforces a
        single session via its lock file, so a gpd0 with no running gpclient
        process is always stale. A stale gpd0 blackholes routing (the portal
        becomes unreachable) and used to be picked up by tunnel detection as
        a live connection (issue #7). tun0/tun1 may belong to other VPN
        clients and are never touched.
        """
        if not os.path.exists("/sys/class/net/gpd0"):
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep",
                "-x",
                "gpclient",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await proc.wait() == 0:
                logger.warning(
                    "gpd0 exists and a gpclient process is running - "
                    "not cleaning up"
                )
                return
        except Exception as e:
            logger.warning(f"Could not check for a running gpclient: {e}")
            return

        logger.warning(
            "Found stale gpd0 interface with no gpclient process - cleaning up"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/gpclient", "disconnect"
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("'gpclient disconnect' timed out, killing it")
                proc.kill()
                await proc.wait()
        except Exception as e:
            logger.debug(f"'gpclient disconnect' during cleanup failed: {e}")

        if os.path.exists("/sys/class/net/gpd0"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ip", "link", "del", "gpd0"
                )
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("'ip link del gpd0' timed out, killing it")
                    proc.kill()
                    await proc.wait()
            except Exception as e:
                logger.error(f"Failed to delete stale gpd0: {e}")

        if not os.path.exists("/sys/class/net/gpd0"):
            logger.info("Stale gpd0 interface removed")

    async def _check_tunnel_loop(self) -> None:
        """Periodically check for tunnel interface"""
        try:
            while True:
                for iface in TUNNEL_INTERFACES:
                    iface_path = f"/sys/class/net/{iface}"
                    if not os.path.exists(iface_path):
                        continue

                    # Check if interface has an IP address (not just exists)
                    ip_addr, prefix = await self._get_iface_ipv4(iface)

                    # Only consider interface valid if it has an IP
                    if not ip_addr:
                        logger.debug(
                            f"Interface {iface} exists but has no IP, skipping"
                        )
                        continue

                    # Never accept an interface that already existed with the
                    # same IP before gpclient started - it is a stale gpd0 or
                    # another VPN's tunnel (issue #7)
                    if (
                        iface in self._preexisting_ifaces
                        and self._preexisting_ifaces[iface] == ip_addr
                    ):
                        logger.debug(
                            f"Interface {iface} pre-existed with unchanged "
                            f"IP {ip_addr}, skipping"
                        )
                        continue

                    logger.info(
                        f"VPN connected - tunnel interface {iface} detected with IP {ip_addr}!"
                    )
                    logger.debug(f"Tunnel IP: {ip_addr}/{prefix}")

                    # Get gateway - for point-to-point VPN without explicit gateway,
                    # use the tunnel IP address itself (NetworkManager requirement)
                    gateway = None
                    try:
                        result = await asyncio.create_subprocess_exec(
                            "ip",
                            "route",
                            "show",
                            "dev",
                            iface,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout_route, _ = await result.communicate()

                        # Look for gateway (via X.X.X.X)
                        for line in stdout_route.decode().split("\n"):
                            if line.strip() and "via" in line:
                                parts = line.strip().split()
                                via_idx = parts.index("via")
                                if via_idx + 1 < len(parts):
                                    gateway = parts[via_idx + 1]
                                    break

                        logger.debug(f"Gateway from routes: {gateway}")
                    except Exception as e:
                        logger.debug(f"Failed to get gateway from routes: {e}")

                    # For point-to-point VPN (no explicit gateway), use tunnel IP as gateway
                    # This is required by NetworkManager
                    if not gateway and ip_addr:
                        gateway = ip_addr
                        logger.debug(
                            f"Using tunnel IP as gateway (point-to-point): {gateway}"
                        )

                    # Build IP4 config
                    config: Dict[str, Tuple[str, Any]] = {
                        "tundev": ("s", iface),
                    }

                    # Add IP address if found
                    if ip_addr:
                        # Convert IP to 32-bit integer (network byte order)
                        ip_int = struct.unpack("!I", socket.inet_aton(ip_addr))[0]
                        config["address"] = ("u", ip_int)
                        config["prefix"] = ("u", prefix)
                        logger.info(f"Added address: {ip_addr}/{prefix}")

                    # Add gateway (required by NetworkManager, even with never-default)
                    # NetworkManager uses never-default to control routing, not plugin
                    if gateway:
                        gateway_int = struct.unpack("!I", socket.inet_aton(gateway))[0]
                        config["gateway"] = ("u", gateway_int)
                        if self.never_default:
                            logger.info(
                                f"Added gateway (but never-default is set): {gateway}"
                            )
                        else:
                            logger.info(f"Added gateway: {gateway}")

                    # Add custom routes if specified
                    if self.custom_routes:
                        routes = []
                        for dest, dest_prefix in self.custom_routes:
                            try:
                                dest_int = struct.unpack("!I", socket.inet_aton(dest))[
                                    0
                                ]
                                # Route format: (dest_ip, prefix, next_hop, metric)
                                # For VPN, next_hop is usually 0 (direct route)
                                routes.append((dest_int, dest_prefix, 0, 0))
                                logger.info(f"Added custom route: {dest}/{dest_prefix}")
                            except Exception as e:
                                logger.warning(
                                    f"Failed to add route {dest}/{dest_prefix}: {e}"
                                )

                        if routes:
                            config["routes"] = ("a(uuuu)", routes)

                    # Add DNS servers if configured
                    if self.dns_servers:
                        # Convert DNS servers to integer format
                        dns_list = []
                        for dns in self.dns_servers:
                            try:
                                # Convert IP string to 32-bit integer
                                dns_int = struct.unpack("<I", socket.inet_aton(dns))[0]
                                dns_list.append(dns_int)
                                logger.info(f"Added DNS server: {dns}")
                            except Exception as e:
                                logger.warning(f"Failed to convert DNS {dns}: {e}")

                        if dns_list:
                            config["dns"] = ("au", dns_list)

                    # Emit Ip4Config signal
                    self.Ip4Config.emit(config)

                    # Emit state change: activated
                    self.StateChanged.emit(NM_VPN_SERVICE_STATE_STARTED)

                    # The login succeeded, so what we learned along the way is
                    # worth keeping in the profile: the gateway list for the
                    # editor's drop-down, and whether this portal needs the
                    # legacy TLS workaround
                    await self._persist_gateway_list()
                    await self._persist_fix_openssl()

                    # Stop checking
                    return

                # Wait 500ms before next check
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.debug("Tunnel monitoring cancelled")
            raise


async def main_async():
    """Async main entry point"""
    import argparse
    import hashlib

    parser = argparse.ArgumentParser(description="NetworkManager gpclient VPN service")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Don't quit when VPN connection terminates",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Enable debug mode: from --debug flag or GPCLIENT_DEBUG env var (default: disabled for security)
    debug_mode = args.debug or os.environ.get("GPCLIENT_DEBUG", "0") == "1"

    if debug_mode:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting gpclient VPN service (python-sdbus)")

    # Log version information in debug mode
    if debug_mode:
        try:
            import datetime

            script_path = os.path.abspath(__file__)
            with open(script_path, "rb") as f:
                script_hash = hashlib.md5(f.read()).hexdigest()
            mtime = os.path.getmtime(script_path)
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            logger.debug(f"Script path: {script_path}")
            logger.debug(f"Script modified: {mtime_str}")
            logger.debug(f"Script MD5: {script_hash}")
            logger.debug(f"Python version: {sys.version}")
            logger.debug("Using python-sdbus (not python-sdbus-networkmanager)")
        except Exception as e:
            logger.debug(f"Failed to compute script hash: {e}")

    # Set system bus as default BEFORE creating any D-Bus objects
    bus = sd_bus_open_system()
    set_default_bus(bus)
    logger.debug("Set system bus as default")

    # Request service name FIRST. If another instance already owns the name
    # (typical: systemd/D-Bus auto-activated us when NetworkManager first
    # touched the VPN) we exit with a clear message instead of dumping a
    # raw sd-bus traceback that users tend to read as "VPN broken".
    try:
        await request_default_bus_name_async(NM_DBUS_SERVICE_GPCLIENT)
    except Exception as e:
        if type(e).__name__ == "SdBusRequestNameExistsError":
            print(
                f"ERROR: D-Bus name {NM_DBUS_SERVICE_GPCLIENT} is already owned\n"
                "by another nm-gpclient-service instance (likely auto-started\n"
                "by systemd/D-Bus). To run this binary manually for debugging,\n"
                "stop the auto-started instance first:\n"
                "    sudo systemctl stop nm-gpclient\n"
                "and to watch its live logs without stopping it, use:\n"
                "    sudo journalctl -u nm-gpclient -f",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
    logger.info(f"Acquired D-Bus service name: {NM_DBUS_SERVICE_GPCLIENT}")

    # Create and export our VPN plugin object
    plugin = GpclientVPNPlugin()
    plugin.export_to_dbus(NM_DBUS_PATH_GPCLIENT)
    logger.debug(f"Exported object to path: {NM_DBUS_PATH_GPCLIENT}")
    logger.debug("D-Bus interfaces fully registered and ready")

    # Setup signal handlers using asyncio Event
    shutdown_event = asyncio.Event()

    def signal_handler(signum):
        logger.info(f"Received signal {signum}, exiting...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_event_loop().add_signal_handler(
            sig, lambda s=sig: signal_handler(s)
        )

    # Run forever
    try:
        logger.info("Entering main loop")
        await shutdown_event.wait()  # Wait for shutdown signal
        logger.info("Shutting down gracefully")
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
        return 1
    finally:
        # Cleanup
        if plugin.gpclient_process:
            try:
                plugin.gpclient_process.terminate()
                await asyncio.wait_for(plugin.gpclient_process.wait(), timeout=5)
            except:
                pass

    logger.info("gpclient VPN service stopped")
    return 0


def main():
    """Main entry point"""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
