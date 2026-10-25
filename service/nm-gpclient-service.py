#!/usr/bin/env python3
"""
NetworkManager VPN Service for GlobalProtect (gpclient)

This service handles VPN connections via gpclient command.
It reads configuration from NetworkManager and spawns gpclient process.

Rewritten using python-sdbus for proper D-Bus interface implementation.
"""

import asyncio
import codecs
import dataclasses
import fcntl
import functools
import ipaddress
import logging
import os
import pty
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

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

# --- Tunnel interface detection ---------------------------------------------
#
# The tunnel our gpclient brings up is either gpd0 (created exclusively by
# gpclient) or a kernel-assigned tunN device - openconnect falls back to the
# latter, and N is simply the first free number. A fixed candidate list used
# to be enough, but it caps how many *foreign* tunN VPNs may be up at once:
# with two other tun-based VPNs already holding tun0 and tun1, our own tunnel
# lands on tun2, which no fixed list predicted, so detection polled forever
# until NetworkManager's vpn.timeout killed the connection (issue #13).
#
# So the candidates are discovered at runtime instead. The safety work is done
# by the pre-existing-interface snapshot (issue #7) and by matching the tunnel
# against the file descriptors our own gpclient process holds - not by the
# length of a hardcoded list.
NET_SYSFS_PATH = "/sys/class/net"
PROC_PATH = "/proc"

# gpdN / tunN, and nothing else: "tunl0" (the always-present IPIP tunnel
# device) must not be mistaken for a VPN tunnel.
TUNNEL_INTERFACE_RE = re.compile(r"^(?P<kind>gpd|tun)(?P<index>\d+)$")

# gpd first (only gpclient creates those), then tunN by number
TUNNEL_INTERFACE_KIND_ORDER = ("gpd", "tun")

GPCLIENT_BINARY = "/usr/bin/gpclient"

# --- VPN DNS handed back from vpnc-script -----------------------------------
#
# The DNS servers and search domains the gateway pushes are applied by
# vpnc-script directly to systemd-resolved, and NetworkManager never learns
# about them: it sees an assumed tunnel device with an empty DNS configuration.
# The next time NetworkManager recomputes DNS for an unrelated reason (an IPv6
# router advertisement on the physical interface is enough) it pushes that
# empty configuration to systemd-resolved, wiping the VPN resolver while the
# tunnel keeps running (issue #15).
#
# Our /etc/vpnc/connect.d hook therefore records INTERNAL_IP4_DNS,
# CISCO_DEF_DOMAIN and CISCO_SPLIT_DNS in this file (the path is handed to
# gpclient in the environment), and tunnel detection reports them to
# NetworkManager in Ip4Config - so NetworkManager owns the resolver state and
# reapplies the same servers on every recalculation.
DNS_STATE_DIR = "/run/nm-gpclient"
DNS_STATE_FILE = f"{DNS_STATE_DIR}/dns-state"
DNS_STATE_ENV = "GPCLIENT_NM_DNS_STATE"

# The hook runs before vpnc-script assigns the tunnel address, so the file is
# normally there by the time the interface has an IP. Should it lag (or be
# missing because the hook is not installed) detection waits at most this many
# 500 ms rounds and then reports the tunnel without learned DNS.
DNS_STATE_WAIT_ROUNDS = 4

# Secret name used for one-time codes in SecretsRequired/NewSecrets
OTP_SECRET_KEY = "otp"
# Tries per connection to mark the code as not-saved in the profile
OTP_FLAG_MAX_ATTEMPTS = 2

# --- Legacy TLS renegotiation ------------------------------------------------
#
# Portals with an old TLS stack need renegotiation that OpenSSL 3.x refuses by
# default, so the prelogin request fails before any browser can open:
#
#   error:0A000152:SSL routines:final_renegotiate:unsafe legacy renegotiation disabled
#   Re-run it with the `--fix-openssl` option to work around this issue
#
# gpclient 3.x words the final error differently, and still prints the hint:
#
#   The server requires unsafe legacy TLS renegotiation: https://vpn.example.com
#
# gpclient has the workaround built in (a temporary OPENSSL_CONF enabling
# UnsafeLegacyServerConnect, also passed on to gpauth), but it has to be asked
# for with a global flag placed before the subcommand. We watch for the error
# and retry once with the flag, so nobody has to know the option exists
# (issue #2).
OPENSSL_LEGACY_ERROR_RE = re.compile(
    r"unsafe legacy renegotiation disabled"
    r"|requires unsafe legacy TLS renegotiation"
    r"|--fix-openssl"
)

# --- Internal network ---------------------------------------------------------
#
# With internal host detection configured on the portal, a gateway can answer
# in non-tunnel mode: gpclient 3.x stays in the foreground, creates no tun
# device and logs one line per gateway status change
# (apps/gpclient/src/connect/internal.rs):
#
#   Internal network — authenticated: <gateway> (IPv4: ..., IPv6: ...)
#   Internal gateway <gateway>: Authenticated | Rejected
#
# The first form is for MaintenanceState::Healthy (upstream writes an em dash;
# a plain hyphen or en dash is accepted too, in case a future release or a
# terminal swaps it), the second for the other states
# (crates/gpapi/src/session/connection.rs). In all of them gpclient is in
# internal mode with no tunnel. The second form has the gateway as upstream
# prints it ("name (address)") and the state word at the end of the line, so an
# unknown state does not match.
#
# "Internal gateway <gateway> authentication failed: ..." is a warning for one
# of the further gateways: gpclient goes on with the gateways that
# authenticated (the first one already did), so it says nothing about the
# tunnel, and it does not match.
INTERNAL_NETWORK_RE = re.compile(
    r"Internal network\s+[\u2014\u2013-]\s+authenticated:"
    r"|Internal gateway\s+.+\):\s+(?:Authenticated|Healthy|Rejected)\s*$"
)

# gpclient logs out of every gateway session on SIGTERM, each with its own 5 s
# timeout, so it needs more than 5 s before we may SIGKILL it (a cut logout
# leaves a stale lock on the gateway)
GPCLIENT_TERMINATE_TIMEOUT = 10
# How long our own gpauth processes get to exit after SIGTERM before SIGKILL
GPAUTH_TERMINATE_TIMEOUT = 2

# How long we wait for the user to answer an interactive secrets request
# (NewSecrets from NetworkManager) before giving up.
SECRETS_REQUEST_TIMEOUT = 300

# How long a prompt candidate must stay unchanged before we act on it.
# gpclient (inquire) renders prompts incrementally; the debounce avoids
# reacting to half-rendered lines.
PROMPT_DEBOUNCE_SECONDS = 0.5

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

# Browser modes of gpclient itself (apps/gpclient/src/connect/args.rs,
# crates/auth/src/browser/browser_auth.rs). `manual` and `remote` wait for the
# callback to be pasted on the terminal, which the service never does, and
# `auto` bypasses the wrapper: all of them go through the wrapper with the
# desktop's default browser instead.
GPCLIENT_BROWSER_MODES = ("auto", "manual", "remote")

# Concrete binaries the connection editors used to offer - wrapping them keeps
# existing profiles working and fixes them at the same time
WRAPPED_BROWSER_PATH_RE = re.compile(
    r"^/usr/bin/(?:microsoft-edge\S*|google-chrome\S*|chromium\S*|firefox\S*)$"
)

# A user's own wrapper: an absolute path without whitespace or control chars
CUSTOM_BROWSER_PATH_RE = re.compile(r"^/[^\s\x00-\x1f\x7f]*$")

# A browser command on PATH (brave-browser, opera): one plain word, no slash
# and no leading dash, so it can never become a gpclient option
BARE_BROWSER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

# Profile values that must not reach the gpclient command line
UNSAFE_GATEWAY_RE = re.compile(r"[\s\x00-\x1f\x7f]")
UNSAFE_USERNAME_RE = re.compile(r"[\x00-\x1f\x7f]")


def is_unsafe_username(username: str) -> bool:
    """A username must not look like a gpclient option or hold control chars"""
    return username.startswith("-") or bool(UNSAFE_USERNAME_RE.search(username))

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
#
# Only the FIRST frame is a stream of complete lines. inquire redraws
# incrementally (FrameRenderer): it moves the cursor back to the top of the
# frame with relative moves and rewrites only the rows that changed, so after a
# Down key the help row is not sent again and the new frame cannot be read from
# the line stream (issue #25: the walk never saw the redraw and confirmed the
# wrong gateway). The frame is therefore read from ScreenBuffer, a small model
# of the terminal screen.
SELECT_HELP_RE = re.compile(r"^\[.*(?:to move|to select|to filter|↑↓).*\]$")
SELECT_OPTION_MARKERS = ">^v "
SELECT_FRAME_MAX_LINES = 24

# Down wraps around in inquire (move_cursor_down(1, wrap=true)), so walking the
# list with Down alone always terminates and reaches every entry - including
# ones outside the visible page.
KEY_DOWN = b"\x1b[B"
# PageDown moves the cursor down by a page without wrapping and stops at the
# last entry (no redraw when it is already there); Home goes to the first entry
KEY_PAGE_DOWN = b"\x1b[6~"
KEY_HOME = b"\x1b[H"
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

# gpclient logs how many gateways the portal config holds (gpapi), before it
# shows the list; a paged list is only complete in the profile once that many
# were seen. The bound keeps a garbled number from meaning anything.
GATEWAY_COUNT_RE = re.compile(r"\bFound ([0-9]+) gateways in portal config")
GATEWAY_COUNT_MAX = 10000


def _gateway_count_value(digits: str) -> Optional[int]:
    """A gateway count from its digits: ASCII only, 1..GATEWAY_COUNT_MAX"""
    if not (digits.isascii() and digits.isdigit()) or len(digits) > 5:
        return None
    count = int(digits)
    return count if 1 <= count <= GATEWAY_COUNT_MAX else None


def parse_gateway_count(line: str) -> Optional[int]:
    """The N of gpclient's "Found N gateways in portal config", else None"""
    match = GATEWAY_COUNT_RE.search(line)
    return _gateway_count_value(match.group(1)) if match else None


def parse_stored_gateway_count(value: str) -> Optional[int]:
    """vpn.data gateway-list-count as a number; None if absent or garbled"""
    return _gateway_count_value(value.strip())


# MTU of the tunnel (vpn.data mtu). gpclient --mtu reaches openconnect as the
# requested MTU. openconnect uses it ONLY when the gateway sends no MTU of its
# own (it then logs "No MTU received. Calculated N ..."); a gateway MTU wins.
# openconnect raises values below 576 to 576, and so does this code.
MTU_MIN = 576
MTU_MAX = 1500
MTU_SPACE = " \t\n\r\f\v"  # the only whitespace that is stripped


def parse_mtu(text: str) -> Optional[int]:
    """vpn.data mtu as a number: 0 = automatic (empty text or only zeros),
    MTU_MIN..MTU_MAX otherwise (smaller values are raised to MTU_MIN), None
    when the text is invalid (sign, dot, inner space, non-ASCII digits, more
    than MTU_MAX). The same rule is in the editors (nm_gpclient_mtu_parse in
    plugins/gnome/nm-gpclient-mtu.h, -1 there is None here)."""
    value = text.strip(MTU_SPACE)
    if not value:
        return 0
    if not (value.isascii() and value.isdigit()):
        return None
    digits = value.lstrip("0")
    if not digits:
        return 0
    if len(digits) > 4 or int(digits) > MTU_MAX:
        return None
    return max(int(digits), MTU_MIN)


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

# Size of the PTY gpclient runs on (TIOCSWINSZ). Wide, so prompts do not wrap
# mid-line; ScreenBuffer models the same grid.
PTY_ROWS = 24
PTY_COLUMNS = 200

# Control characters except \n, \r and \t
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# An escape sequence cut in half by a read boundary. Without holding the head
# back, ESC is dropped as a control character and the rest leaks into the text -
# which is how a prompt label ended up as "[39m Password" in the #2 report.
# Covers a CSI head (with or without intermediate bytes), a lone ESC (or one
# with intermediate bytes only, like the ESC ( of ESC ( B) and an unterminated
# OSC. The OSC part is bounded so that one that never ends cannot hold the
# output back forever.
INCOMPLETE_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*\Z" r"|\x1b\][^\x07\x1b]{0,256}\x1b?\Z" r"|\x1b[ -/]*\Z"
)

# "Please enter RSA token (Portal: vpn.example.com)" banner printed by
# gpclient before a standard (non-SAML) authentication round.
AUTH_BANNER_RE = re.compile(
    r"^(?P<message>.+?)\s*\((?P<kind>Portal|Gateway):\s*(?P<server>[^)]+)\)\s*$"
)

# Substrings that make a label word (or sub-word, see split_label) a username
# word: "Username", "UserID", "Login", "Email", "E-mail" (sub-word "mail")
USERNAME_LABEL_WORDS = ("user", "login", "mail")
# Whole words that name the password itself. Not "Passport" or "Passkey": a
# word that merely starts with "pass" is something else.
PASSWORD_LABEL_WORDS = (
    "password",
    "passwort",
    "passwd",
    "passphrase",
    "pass",
    "kennwort",
)
# A hint in brackets is not what the prompt asks for: "Username (not your
# password)" is a username, "Secret (login)" a secret
LABEL_HINT_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
# Words that make a following (or preceding, inside one word) "code" something
# else: "Postcode", "ZIP-Code", "Country code", "Unicode", "Promo code"
NOT_ONE_TIME_CODE_PREFIXES = (
    "bar",
    "post",
    "zip",
    "uni",
    "country",
    "area",
    "postal",
    "promo",
)
# One-time keywords that may end a word: "mytoken", "TOTP", "authcode",
# "Sicherheitscode", "PINcode" (ends with "code"). "code" is not taken from
# "Barcode", "Postcode", "Zipcode" or "Unicode" (see NOT_ONE_TIME_CODE_PREFIXES).
ONE_TIME_SECRET_SUFFIXES = (
    "token",
    "otp",
    "".join(f"(?<!{prefix})" for prefix in NOT_ONE_TIME_CODE_PREFIXES) + "code",
    "challenge",
)
# Short words that would match too much at the end of a word ("Pakistan",
# "Kingpin", "Ursa") count as whole words only; TAN also as the known
# compounds "mTAN", "pushTAN", "chipTAN", "smsTAN", "photoTAN", "qrTAN"
ONE_TIME_SECRET_WHOLE_WORDS = ("pin", "rsa", "(?:m|push|chip|sms|photo|qr)?tan")
# Optionally plural. "tan" must not match "instance" or "Stand-alone", "pin"
# not "Pinnacle", "code" not "encoded". Matched against a whole word or one
# sub-word of it (see split_label) with fullmatch().
ONE_TIME_SECRET_RE = re.compile(
    r"\w*(?:"
    + "|".join(ONE_TIME_SECRET_SUFFIXES)
    + r")s?|(?:"
    + "|".join(ONE_TIME_SECRET_WHOLE_WORDS)
    + r")s?",
    re.IGNORECASE,
)
# A bare "code" (a word or sub-word of its own): not one-time after one of
# NOT_ONE_TIME_CODE_PREFIXES ("Zip code", "ZIP-Code", "PostCode")
BARE_CODE_RE = re.compile(r"codes?", re.IGNORECASE)
# A label is looked at word by word (runs of letters: digits, underscores and
# punctuation separate), and each word again by its sub-words, so that
# "OTPPassword" is OTP + Password, "pushTAN" push + TAN, "PIN1" the word PIN.
# Sub-words end at lower->Upper and at ACRONYM->Word.
LABEL_WORD_RE = re.compile(r"[^\W\d_]+")
LABEL_SUB_WORD_RE = re.compile(r"[A-Z]+(?![^\W\d_A-Z])|[A-Z]?[^\W\d_A-Z]+")


class EscapeTokenizer:
    """Split terminal output into text runs and CSI sequences.

    The one definition of the escape syntax: the line stream (plain text for
    OutputScanner) and ScreenBuffer both work from its tokens. Tokens are
    ("text", str) and ("csi", params, intermediates, final); every other escape
    sequence (OSC, nF like ESC ( B, Fp like ESC 7, Fe, Fs, a stray ESC) is
    recognised and dropped.

    The input must not end in the middle of an escape sequence (_consume_output
    holds those back), with one exception: an overlong OSC without terminator
    is skipped up to the end of the text and, on the next feed, up to its
    terminator. That state lives here. An OSC body ends at BEL or ESC \\, or at
    any other ESC, which then starts the next sequence.
    """

    _TOKEN_RE = re.compile(
        # Parameter bytes are 0x30-0x3F, intermediate bytes 0x20-0x2F
        r"\x1b\[(?P<params>[0-?]*)(?P<intermediates>[ -/]*)(?P<final>[@-~])"
        r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|(?=\x1b[^\\]))"
        r"|(?P<osc_open>\x1b\][^\x07\x1b]*(?P<osc_esc>\x1b)?\Z)"
        r"|\x1b[ -/]*[0-~]"
        r"|(?P<text>[^\x1b]+)"
        r"|\x1b"  # stray ESC
    )
    _OSC_END_RE = re.compile(r"\x07|\x1b\\|(?=\x1b[^\\])")

    def __init__(self):
        self._in_osc = False  # inside an OSC whose terminator is still to come
        self._osc_esc = False  # ... and the text ended with an ESC (ESC \\?)

    def feed(self, text: str) -> List[Tuple[str, ...]]:
        """Tokenize a chunk (the state carries over to the next one)"""
        if self._in_osc:
            if self._osc_esc:
                text = "\x1b" + text
            end = self._OSC_END_RE.search(text)
            if end is None:
                self._osc_esc = text.endswith("\x1b")
                return []
            self._in_osc = self._osc_esc = False
            text = text[end.end() :]
        tokens: List[Tuple[str, ...]] = []
        for match in self._TOKEN_RE.finditer(text):
            if match.group("text") is not None:
                tokens.append(("text", match.group("text")))
            elif match.group("final") is not None:
                tokens.append(
                    (
                        "csi",
                        match.group("params"),
                        match.group("intermediates"),
                        match.group("final"),
                    )
                )
            elif match.group("osc_open") is not None:
                self._in_osc = True
                self._osc_esc = match.group("osc_esc") is not None
        return tokens


def tokens_text(tokens: Sequence[Tuple[str, ...]]) -> str:
    """The plain text of tokens: control chars (except \\n \\r \\t) removed"""
    return CONTROL_CHARS_RE.sub("", "".join(t[1] for t in tokens if t[0] == "text"))


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and control chars (except \\n \\r \\t)"""
    return tokens_text(EscapeTokenizer().feed(text))


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


def detect_screen_prompt(
    lines: Sequence[str], cursor_row: int, last_answer: str = ""
) -> Optional[Tuple[int, str]]:
    """Detect a pending inquire text prompt on the screen rows.

    inquire parks the cursor on the prompt row, so the prompt is the row at the
    cursor. Rows below it (an error message, the help line) are never looked at.
    When the cursor row is empty - the prompt line was terminated with a newline
    and the cursor moved on - the nearest non-empty row above it is the
    candidate. Rows are looked at one by one: a label above the cursor row is
    not joined to it.

    Returns (row index, label) or None. The label is classified by
    detect_prompt().
    """
    if not 0 <= cursor_row < len(lines):
        return None
    for index in range(cursor_row, -1, -1):
        if lines[index].strip():
            label = detect_prompt(lines[index], last_answer)
            return None if label is None else (index, label)
    return None


def split_label(text: str) -> List[Tuple[str, List[Tuple[str, int, int]]]]:
    """Split a label into words and each word into sub-words.

    Returns (word, [(sub-word, start, end), ...]) per word; start/end are
    offsets into `text`, so the gap between two sub-words can be looked at.
    """
    words = []
    for run in LABEL_WORD_RE.finditer(text):
        subs = [
            (sub.group(), run.start() + sub.start(), run.start() + sub.end())
            for sub in LABEL_SUB_WORD_RE.finditer(run.group())
        ]
        words.append((run.group(), subs))
    return words


HIDDEN = "<hidden>"


def password_not_required(data: Dict[str, Any]) -> bool:
    """True when the NM_SETTING_SECRET_FLAG_NOT_REQUIRED bit (4) is set in
    password-flags (a bitmask); a junk value counts as 0"""
    try:
        return bool(int(data.get("password-flags", 0)) & 4)
    except (TypeError, ValueError):
        return False


def redact_settings(obj: Any) -> Any:
    """Copy of NM settings that is safe to log: every value under a "secrets"
    key (at any depth, also inside sdbus (signature, value) tuples) becomes
    "<hidden>". The keys stay, so the log still shows what was sent."""
    if isinstance(obj, dict):
        return {
            key: HIDDEN if key == "secrets" else redact_settings(value)
            for key, value in obj.items()
        }
    if isinstance(obj, tuple):
        return tuple(redact_settings(item) for item in obj)
    if isinstance(obj, list):
        return [redact_settings(item) for item in obj]
    return obj


@functools.lru_cache(maxsize=64)
def _secrets_pattern(secrets: frozenset) -> Optional["re.Pattern[str]"]:
    """One compiled pattern for a set of secrets, or None when there is none"""
    parts = []
    # Longest first: a secret that contains another one is masked whole. Only
    # whole occurrences count, so a short answer ("1", "y") does not garble
    # every word that happens to contain it.
    # A guard applies only on a side where the secret has a word character: a
    # secret that starts or ends with a symbol ("#Hunter2", "abc!") is also
    # masked when a word character touches it ("x#Hunter2", "abc!def").
    for secret in sorted(secrets, key=lambda x: (-len(x), x)):
        if secret:
            before = r"(?<!\w)" if re.match(r"\w", secret[0]) else ""
            after = r"(?!\w)" if re.match(r"\w", secret[-1]) else ""
            parts.append(f"{before}{re.escape(secret)}{after}")
    return re.compile("|".join(parts)) if parts else None


def mask_secrets(line: str, secrets: Sequence[str]) -> str:
    """Replace every occurrence of a secret value in a line of output"""
    pattern = _secrets_pattern(frozenset(secrets))
    return pattern.sub(lambda _: HIDDEN, line) if pattern else line


def mask_secret_prompts(line: str, labels: Sequence[str]) -> str:
    """Hide whatever follows the label of a prompt that was answered with a secret

    inquire redraws a text prompt after every keystroke, so the output holds
    partial echoes ("? Code: h", "? Code: hu", ...) that mask_secrets cannot
    know. A colon right after the label is kept; a line that ends there stays.
    """
    # Longest first: a label that contains another one wins
    for label in sorted(labels, key=len, reverse=True):
        if not label:
            continue
        start = line.find(label)
        if start < 0:
            continue
        end = start + len(label)
        if line.startswith(":", end):
            end += 1
        if line[end:].strip():
            line = f"{line[:end]} {HIDDEN}"
    return line


def classify_prompt(label: str) -> str:
    """Classify a prompt label as 'username' or 'password' (any secret).

    Text in parentheses or brackets is a hint and is not looked at. The first
    sub-word (see split_label) that is a password word or contains a username
    word decides. A password word is a password ("Password for user jdoe"). A
    username word is a username, unless a password word follows right after it
    (only blanks or nothing in between): then it describes the password
    ("Login password", "UserPassword"). "Username/Password" and "Login or
    password" ask for both, the username first. A label with neither kind of
    word is a secret.
    """
    text = LABEL_HINT_RE.sub(" ", label)
    subs = [sub for _, word_subs in split_label(text) for sub in word_subs]
    lowered = [sub[0].lower() for sub in subs]
    for index, word in enumerate(lowered):
        if word in PASSWORD_LABEL_WORDS:
            return "password"
        if any(keyword in word for keyword in USERNAME_LABEL_WORDS):
            following = index + 1
            if following < len(subs) and lowered[following] in PASSWORD_LABEL_WORDS:
                if not text[subs[index][2] : subs[following][1]].strip():
                    return "password"
            return "username"
    return "password"


def is_one_time_secret(text: str) -> bool:
    """True when the label/banner suggests a one-time secret (RSA token, OTP).

    One-time secrets must never be answered from a stored password - the user
    has to be asked every time.
    """
    previous = ""  # the sub-word before, even in the word before
    for word, subs in split_label(text):
        pieces = [word] + [sub[0] for sub in subs]
        befores = [previous, previous] + [sub[0] for sub in subs[:-1]]
        for piece, before in zip(pieces, befores):
            if not ONE_TIME_SECRET_RE.fullmatch(piece):
                continue
            # "Zip code", "ZIP-Code", "PostCode": a bare "code" after a word
            # that makes it something else
            if (
                BARE_CODE_RE.fullmatch(piece)
                and before.lower() in NOT_ONE_TIME_CODE_PREFIXES
            ):
                continue
            return True
        previous = subs[-1][0]
    return False


def detect_select_prompt(lines: Sequence[str]) -> Optional[Dict[str, Any]]:
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
    cursor = None
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

    # A Select frame always has a highlighted entry; without one the frame is
    # incomplete (or not a Select at all)
    if not message or not options or cursor is None:
        return None

    return {"message": message, "options": options, "cursor": cursor, "more": more}


def pick_gateway(options: List[str], preferred: str) -> Optional[str]:
    """Pick the option matching `preferred`, most specific match first.

    Options look like "name (host.example.com)". Three tiers: the whole entry
    (case-insensitive), then the name or the host alone (gateway_matches()
    without substring), and only then a substring (gateway_matches()). Returns
    None when nothing matches.
    """
    wanted = (preferred or "").strip().lower()
    if not wanted:
        return options[0] if options else None

    for option in options:
        if option.strip().lower() == wanted:
            return option

    for substring in (False, True):
        for option in options:
            if gateway_matches(preferred, option, substring=substring):
                return option

    return None


def gateway_matches(preferred: str, option: str, substring: bool = True) -> bool:
    """True when `option` ("name (host)") is what `preferred` asks for.

    With substring=False only the whole entry, the name or the host may match.
    """
    wanted = (preferred or "").strip().lower()
    if not wanted:
        return False

    candidate = option.strip().lower()
    if wanted == candidate:
        return True

    name, _, host = option.partition("(")
    if wanted == name.strip().lower() or wanted == host.strip(") ").lower():
        return True

    return substring and wanted in candidate


def resolve_browser(value: str) -> Tuple[str, Optional[str]]:
    """Map the connection's `browser` setting to what gpclient should launch.

    Returns (argument for gpclient --browser, GP_BROWSER for the wrapper).
    Friendly names and the browser binaries our editors used to offer go
    through the wrapper. A user's own wrapper script is passed through when it
    is an absolute path or a bare command name; any other value falls back to
    "default", so a profile value can never become an extra gpclient option.
    """
    value = (value or "").strip()

    if value == LEGACY_EDGE_WRAPPER:
        # Compatibility shim, it knows which browser to launch
        return value, None

    if not value:
        target = "edge"  # historical default
    elif value.lower() in BROWSER_ALIASES:
        target = BROWSER_ALIASES[value.lower()]
    elif value.lower() in GPCLIENT_BROWSER_MODES:
        logger.warning(
            f"browser={value} is a gpclient mode the service cannot drive - "
            "using the desktop's default browser"
        )
        target = "default"
    elif WRAPPED_BROWSER_PATH_RE.match(value):
        target = value
    elif CUSTOM_BROWSER_PATH_RE.match(value) or BARE_BROWSER_NAME_RE.fullmatch(value):
        return value, None
    else:
        logger.warning(
            f"browser={value!r} is not a known name, a command name or an absolute path - "
            "using the desktop's default browser"
        )
        target = "default"

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


def tunnel_candidate_order(iface: str) -> Tuple[int, int]:
    """Sort key putting gpd0 first and tunN in numeric (not lexical) order."""
    match = TUNNEL_INTERFACE_RE.match(iface)
    if not match:
        return len(TUNNEL_INTERFACE_KIND_ORDER), 0
    kind = match.group("kind")
    return TUNNEL_INTERFACE_KIND_ORDER.index(kind), int(match.group("index"))


def list_tunnel_candidates() -> List[str]:
    """Tunnel interfaces present right now, in the order detection tries them.

    Replaces the old fixed ["gpd0", "tun0", "tun1"] list, which could not see
    a tunnel that landed on tun2 or higher because other VPNs held the lower
    numbers (issue #13).
    """
    try:
        names = os.listdir(NET_SYSFS_PATH)
    except OSError as e:
        logger.warning(f"Cannot list {NET_SYSFS_PATH}: {e}")
        return []

    candidates = [name for name in names if TUNNEL_INTERFACE_RE.match(name)]
    return sorted(candidates, key=tunnel_candidate_order)


def tunnel_ifaces_held_by(pids: List[int]) -> set:
    r"""Tunnel interfaces whose file descriptor is held by one of `pids`.

    The kernel names the interface behind a tun file descriptor in
    /proc/PID/fdinfo/N ("iff:\ttun2"), which identifies our own tunnel
    directly instead of inferring it from what appeared since Connect()
    started. Returns an empty set when the information is unavailable
    (process already gone, /proc unreadable); the caller then falls back to
    the pre-existing-interface snapshot.
    """
    ifaces = set()
    for pid in pids:
        fdinfo_dir = f"{PROC_PATH}/{pid}/fdinfo"
        try:
            fds = os.listdir(fdinfo_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                with open(f"{fdinfo_dir}/{fd}", "r") as handle:
                    content = handle.read()
            except OSError:
                continue
            for line in content.splitlines():
                if line.startswith("iff:"):
                    name = line.split(":", 1)[1].strip()
                    if name:
                        ifaces.add(name)
    return ifaces


def process_tree(root_pid: int) -> List[int]:
    """A PID plus every descendant, so a tunnel opened by a forked helper of
    gpclient is still recognised as ours."""
    try:
        pids = [int(entry) for entry in os.listdir(PROC_PATH) if entry.isdigit()]
    except OSError:
        return [root_pid]

    children: Dict[int, List[int]] = {}
    for pid in pids:
        try:
            with open(f"{PROC_PATH}/{pid}/stat", "r") as handle:
                stat = handle.read()
        except OSError:
            continue
        # Field 2 is the command name in parentheses and may itself contain
        # spaces and parentheses, so the parent PID is read after the last ')'
        close = stat.rfind(")")
        if close < 0:
            continue
        fields = stat[close + 1 :].split()
        if len(fields) < 2:
            continue
        try:
            children.setdefault(int(fields[1]), []).append(pid)
        except ValueError:
            continue

    tree: List[int] = []
    seen = set()
    queue = [root_pid]
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        tree.append(pid)
        queue.extend(children.get(pid, []))
    return tree


def parse_proc_stat(text: str) -> Optional[Tuple[str, str, int, int]]:
    """(comm, state, ppid, starttime) from the text of /proc/PID/stat, or None.

    Field 2 (comm) is in parentheses and may itself contain spaces and
    parentheses, so everything after the LAST ')' is split into fields:
    state is field 3, ppid field 4, starttime field 22.
    """
    close = text.rfind(")")
    open_ = text.find("(")
    if open_ < 0 or close < open_:
        return None
    fields = text[close + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return text[open_ + 1 : close], fields[0], int(fields[1]), int(fields[19])
    except ValueError:
        return None


def read_proc_stat(pid: int) -> Optional[Tuple[str, str, int, int]]:
    """parse_proc_stat() of one process; None when it is gone or unreadable"""
    try:
        with open(f"{PROC_PATH}/{pid}/stat", "r") as handle:
            return parse_proc_stat(handle.read())
    except OSError:
        return None


def gpauth_descendants(root_pid: int) -> List[Tuple[int, int]]:
    """(pid, starttime) of every gpauth below `root_pid` in the process tree.

    These are the gpauth processes that belong to our own gpclient. The
    starttime tells a later signal sender whether the PID is still the same
    process. Blocking /proc I/O: call it off the event loop.
    """
    try:
        pids = [int(entry) for entry in os.listdir(PROC_PATH) if entry.isdigit()]
    except OSError:
        return []

    children: Dict[int, List[int]] = {}
    info: Dict[int, Tuple[str, int]] = {}
    for pid in pids:
        stat_fields = read_proc_stat(pid)
        if stat_fields is None:
            continue
        comm, _state, ppid, starttime = stat_fields
        children.setdefault(ppid, []).append(pid)
        info[pid] = (comm, starttime)

    found: List[Tuple[int, int]] = []
    seen = {root_pid}
    queue = [root_pid]
    while queue:
        for child in children.get(queue.pop(), []):
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
            comm, starttime = info[child]
            if comm == "gpauth":
                found.append((child, starttime))
    return found


def is_same_process(pid: int, starttime: int) -> bool:
    """True while `pid` is a live process that started at `starttime`.

    A zombie counts as gone. A PID that was reused has another starttime.
    """
    stat_fields = read_proc_stat(pid)
    return (
        stat_fields is not None
        and stat_fields[1] not in ("Z", "X")
        and stat_fields[3] == starttime
    )


def send_signal(pid: int, sig: int) -> None:
    """The only place that signals a process we did not start ourselves"""
    os.kill(pid, sig)


def signal_if_same(pid: int, starttime: int, sig: int) -> bool:
    """Send `sig` only when `pid` still is the process that started at
    `starttime`. Returns True when a signal was sent."""
    if not is_same_process(pid, starttime):
        return False
    try:
        send_signal(pid, sig)
    except OSError:
        return False
    return True


def parse_dns_state(text: str) -> Dict[str, str]:
    """Parse the KEY=value lines the vpnc hook writes to DNS_STATE_FILE."""
    state: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            state[key] = value.strip()
    return state


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def learned_dns_from_state(
    state: Dict[str, str], iface: str
) -> Optional[Tuple[List[str], List[str], List[str]]]:
    """DNS the gateway pushed for `iface`: (ipv4 servers, search domains,
    ipv6 servers), or None when the state does not describe this tunnel.

    CISCO_DEF_DOMAIN is space-separated (vpnc-script convention, and our hook
    appends the profile's dns-domains to it); CISCO_SPLIT_DNS is the
    comma-separated split-DNS list openconnect builds from the gateway's
    response.
    """
    tundev = state.get("TUNDEV", "")
    if tundev and tundev != iface:
        return None
    servers = [s for s in state.get("INTERNAL_IP4_DNS", "").split() if s]
    servers6 = [s for s in state.get("INTERNAL_IP6_DNS", "").split() if s]
    domains = state.get("CISCO_DEF_DOMAIN", "").split()
    domains += re.split(r"[,\s]+", state.get("CISCO_SPLIT_DNS", ""))
    return _dedupe(servers), _dedupe(domains), _dedupe(servers6)


def vpn_gateway_from_state(
    state: Dict[str, str], iface: str
) -> Optional[ipaddress.IPv4Address]:
    """The VPN server's IPv4 address the hook recorded for `iface`, or None
    when the state is for another tunnel or VPNGATEWAY is no IPv4 address."""
    tundev = state.get("TUNDEV", "")
    if tundev and tundev != iface:
        return None
    address = parse_ip_address(state.get("VPNGATEWAY", ""))
    return address if isinstance(address, ipaddress.IPv4Address) else None


def parse_dns_servers(text: str) -> List[str]:
    """The profile's `dns` entries: separated by commas, semicolons or blanks"""
    return [entry for entry in re.split(r"[;,\s]+", text) if entry]


# gpclient (3.1.0+) logs the tunnel settings it negotiated, one INFO line each,
# in every foreground mode (gptunnel log_configuration). They are what the
# gateway pushed, not proof that the system was configured, so they are only
# a cross-check and a fallback for the vpnc hook's DNS state. The line starts
# with the log prefix ("[2026-01-01T00:00:00Z INFO  target] ").
TUNNEL_SETTINGS_RE = re.compile(
    r"^\[(?:\S+\s+)?INFO\s+[A-Za-z0-9_:]+\]\s+VPN "
    r"(?P<key>interface|IPv4|DNS|search domains|split DNS|MTU): (?P<value>.*?)\s*$"
)
# values() ends a cut list with " (N of M shown; truncated)"
TUNNEL_LIST_TRUNCATED_RE = re.compile(r"\s\([0-9]+ of [0-9]+ shown; truncated\)$")
TUNNEL_DOMAIN_RE = re.compile(
    r"(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*"
)
TUNNEL_LOGGED_INTERFACE_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,15}")


@dataclasses.dataclass(frozen=True)
class TunnelSettings:
    """The tunnel settings gpclient logged. None: not seen, "none"/"unavailable"
    where the value does not exist, or not usable (malformed, escaped,
    truncated). A list that gpclient reported as empty is ()."""

    interface: Optional[str] = None
    ipv4: Optional[str] = None  # the address only
    prefix: Optional[int] = None
    dns: Optional[Tuple[str, ...]] = None  # IPv4 and IPv6 servers
    search_domains: Optional[Tuple[str, ...]] = None
    split_dns: Optional[Tuple[str, ...]] = None
    mtu: Optional[int] = None


def _tunnel_list(value: str, valid: Any) -> Optional[Tuple[str, ...]]:
    """The items of a list value of gpclient's values(); None when the list is
    not complete or an item cannot be trusted.

    Items are told apart by ", ", so each one is validated: an item that
    holds ", " fails there, and so does one that upstream escaped
    (escape_debug adds a backslash, which no address or domain holds): the
    escaping is not reversed.
    """
    if TUNNEL_LIST_TRUNCATED_RE.search(value):
        return None
    if value == "none":
        return ()
    items = value.split(", ")
    if not all(item and valid(item) for item in items):
        return None
    return tuple(_dedupe(items))


def _dns_key(server: str) -> Any:
    """A DNS server as it compares: the same address in any spelling is equal"""
    return parse_ip_address(server) or server


def parse_tunnel_settings_line(line: str, settings: TunnelSettings) -> TunnelSettings:
    """`settings` updated with one gpclient output line; the same object when
    the line is no tunnel settings line.

    "VPN interface" is the first line of a block, so it starts a new record
    (gpclient logs the block again when the settings change).
    """
    match = TUNNEL_SETTINGS_RE.match(line)
    if not match:
        return settings
    key, value = match.group("key"), match.group("value")
    if key == "interface":
        usable = TUNNEL_LOGGED_INTERFACE_RE.fullmatch(value) and value != "none"
        return TunnelSettings(interface=value if usable else None)
    if key == "IPv4":
        address, _, rest = value.partition("/")
        if "(" in address:  # "10.0.0.2 (mask 255.0.255.0)": no prefix
            address, rest = address.split(" (", 1)[0], ""
        parsed = parse_ip_address(address)
        if not isinstance(parsed, ipaddress.IPv4Address) or str(parsed) != address:
            return dataclasses.replace(settings, ipv4=None, prefix=None)
        prefix = int(rest) if rest.isascii() and rest.isdigit() else None
        if prefix is not None and prefix > 32:
            prefix = None
        return dataclasses.replace(settings, ipv4=address, prefix=prefix)
    if key == "MTU":
        mtu = int(value) if value.isascii() and value.isdigit() else None
        if mtu is not None and not 0 < mtu <= 65535:
            mtu = None
        return dataclasses.replace(settings, mtu=mtu)
    if key == "DNS":
        items = _tunnel_list(
            value, lambda item: parse_ip_address(item) is not None and "%" not in item
        )
        return dataclasses.replace(settings, dns=items)
    items = _tunnel_list(value, TUNNEL_DOMAIN_RE.fullmatch)
    field = "search_domains" if key == "search domains" else "split_dns"
    return dataclasses.replace(settings, **{field: items})


def validate_hip_script(path: str, trusted_uids: Sequence[int] = (0,)) -> str:
    """The resolved path of the custom HIP executable `path`, safe to run.

    Raises ValueError with the reason when it is not safe.
    """
    # The service runs gpclient, and so the script, as root. Without these
    # checks "can edit the NetworkManager profile" would become "can run code
    # as root": the script and every directory above it must be changeable by
    # a trusted user only. A sticky directory (like /tmp) does not count as
    # safe: the script must not be in a group or world-writable directory.
    if not os.path.isabs(path):
        raise ValueError("the path is not absolute")
    resolved = os.path.realpath(path)
    try:
        info = os.stat(resolved)
    except OSError as e:
        raise ValueError(f"cannot read the file: {e.strerror or e}")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{resolved} is not a regular file")
    if info.st_uid not in trusted_uids:
        raise ValueError(f"{resolved} has an untrusted owner (uid {info.st_uid})")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError(f"{resolved} is writable by group or others")
    if not info.st_mode & stat.S_IXUSR:
        raise ValueError(f"{resolved} is not executable by its owner")

    directory = os.path.dirname(resolved)
    while True:
        dir_info = os.stat(directory)
        if dir_info.st_uid not in trusted_uids:
            raise ValueError(
                f"directory {directory} has an untrusted owner (uid {dir_info.st_uid})"
            )
        if dir_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"directory {directory} is writable by group or others")
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return resolved


def parse_ip_address(
    text: str,
) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """The IPv4/IPv6 address `text` spells, or None when it is no address"""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def ipv4_to_nm_uint32(address: Union[str, ipaddress.IPv4Address]) -> int:
    """An IPv4 address as the uint32 NetworkManager's Ip4Config expects.

    NetworkManager stores it as in_addr_t: the network-byte-order bytes read
    as a host integer, i.e. the raw address bytes in native order.

    Raises ValueError (AddressValueError) for anything that is not a dotted
    quad.
    """
    # ipaddress, not inet_aton: inet_aton() also accepts "192.168.1", "1" and
    # hex parts, silently turning a typo in the profile into a different
    # (wrong) DNS server
    if not isinstance(address, ipaddress.IPv4Address):
        address = ipaddress.IPv4Address(address)
    return struct.unpack("=I", address.packed)[0]


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


class ScreenBuffer:
    """Apply raw PTY output (with escape sequences) to a grid of text rows.

    Models just enough of a terminal for inquire's incremental redraws: text
    overwrites at the cursor, \\r and \\n, relative cursor moves and the erase
    sequences. Colours and everything else are ignored. The escape syntax is
    EscapeTokenizer's; feed() takes text, apply() takes its tokens.
    `version` changes whenever the screen may have changed.

    Characters take as many columns as inquire (unicode-width) gives them: wide
    (East Asian W/F) ones two - the character in the first cell and an empty
    string in the second - and zero-width ones (combining marks, format
    characters) none (they join the previous cell).
    """

    # Relative cursor moves cannot reach above the visible screen (PTY_ROWS),
    # so twice that is margin enough; every lines() and detect_select_prompt
    # pass walks all rows
    MAX_ROWS = 2 * PTY_ROWS
    TAB_WIDTH = 8

    def __init__(self):
        self._rows: List[List[str]] = [[]]
        self._row = 0
        self._col = 0
        self._tokenizer = EscapeTokenizer()  # for feed(); apply() takes tokens
        self.version = 0
        self.dropped = 0  # rows trimmed from the top: row_id() stays stable
        self._lines_cache: Tuple[str, ...] = ()
        self._lines_version = -1

    @property
    def cursor_row(self) -> int:
        """Index (in lines()) of the row the cursor is on"""
        return self._row

    def row_id(self, index: int) -> int:
        """A number for the row at `index` that stays the same when the buffer
        is trimmed at the top"""
        return self.dropped + index

    def lines(self) -> Tuple[str, ...]:
        """The screen rows (a tuple, cached until the next feed)"""
        if self._lines_version != self.version:
            self._lines_cache = tuple("".join(row).rstrip() for row in self._rows)
            self._lines_version = self.version
        return self._lines_cache

    def feed(self, text: str) -> None:
        self.apply(self._tokenizer.feed(text))

    def apply(self, tokens: Sequence[Tuple[str, ...]]) -> None:
        """Apply EscapeTokenizer tokens"""
        if not tokens:
            return
        self.version += 1
        for token in tokens:
            if token[0] == "text":
                self._write(token[1])
            else:
                self._csi(*token[1:])

    def _write(self, text: str) -> None:
        for char in text:
            if char == "\r":
                self._col = 0
            elif char in "\n\x0b\x0c":  # VT and FF act as line feed
                self._line_feed()
            elif char == "\b":
                self._col = max(0, self._col - 1)
            elif char == "\t":
                next_stop = (self._col // self.TAB_WIDTH + 1) * self.TAB_WIDTH
                self._col = min(next_stop, PTY_COLUMNS - 1)
            elif CONTROL_CHARS_RE.match(char):
                continue
            elif self._zero_width(char):
                self._attach_combining(char)
            else:
                width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
                if self._col + width > PTY_COLUMNS:  # autowrap, like a terminal
                    self._col = 0
                    self._line_feed()
                self._put(char, width)

    @staticmethod
    def _zero_width(char: str) -> bool:
        """Combining marks and format characters (ZWSP, ZWJ, VS16) take no cell.

        combining() alone misses e.g. Thai vowel signs, which are Mn.
        """
        return bool(unicodedata.combining(char)) or (
            unicodedata.category(char) in ("Mn", "Me", "Cf")
        )

    def _put(self, char: str, width: int) -> None:
        """Write `char` into `width` cells at the cursor and advance"""
        row = self._rows[self._row]
        end = self._col + width
        if len(row) < end:
            row.extend(" " * (end - len(row)))
        # Overwriting one half of a wide character blanks the other half
        for index in range(self._col, end):
            if row[index] == "" and index > 0:
                row[index - 1] = " "
        if end < len(row) and row[end] == "":
            row[end] = " "
        row[self._col] = char
        for index in range(self._col + 1, end):
            row[index] = ""
        self._col = end

    def _attach_combining(self, char: str) -> None:
        """Add a zero-width combining mark to the cell left of the cursor"""
        row = self._rows[self._row]
        index = self._col - 1
        if 0 <= index < len(row) and row[index] == "":  # second half of a wide one
            index -= 1
        if 0 <= index < len(row):
            row[index] += char

    def _line_feed(self) -> None:
        self._move_to_row(self._row + 1)

    def _move_to_row(self, row: int) -> None:
        """Move to `row`, growing the buffer once and trimming it to MAX_ROWS"""
        self._row = row
        missing = row + 1 - len(self._rows)
        if missing > 0:
            self._rows.extend([] for _ in range(missing))
        excess = len(self._rows) - self.MAX_ROWS
        if excess > 0:
            del self._rows[:excess]
            self.dropped += excess
            self._row = max(0, self._row - excess)

    def _csi(self, params: str, intermediates: str, final: str) -> None:
        if intermediates:  # e.g. "ESC [ 2 SP A" (scroll right): not a cursor move
            return
        if params[:1] in ("<", "=", ">", "?"):  # private (cursor visibility, ...)
            return
        numbers = []
        for part in params.split(";"):
            if not part:
                numbers.append(None)
            elif part.isdigit():
                # Longer values are far beyond the screen anyway; the cut also
                # keeps int() away from absurdly long digit strings
                numbers.append(int(part[:9]))
            else:  # e.g. "38:5:1" (SGR) or "1?2": nothing we model
                return
        first = numbers[0]
        count = first if first else 1  # a move by 0 is a move by 1

        if final == "A":
            self._row = max(0, self._row - count)
        elif final == "B":
            self._move_to_row(self._row + min(count, self.MAX_ROWS))
        elif final == "C":
            self._col = min(self._col + count, PTY_COLUMNS - 1)
        elif final == "D":
            self._col = max(0, self._col - count)
        elif final == "G":
            self._col = min(max(0, count - 1), PTY_COLUMNS - 1)
        elif final == "K":
            row = self._rows[self._row]
            mode = first or 0
            if mode == 0:
                del row[self._col :]
            elif mode == 1:
                row[: self._col + 1] = [" "] * min(len(row), self._col + 1)
            elif mode == 2:
                row.clear()
        elif final == "J":
            mode = first or 0
            if mode == 0:
                del self._rows[self._row][self._col :]
                del self._rows[self._row + 1 :]
            elif mode == 2:
                # Clears the content but not the cursor, so relative moves
                # keep lining up. Mode 3 (scrollback) does not touch the screen.
                self._rows = [[] for _ in self._rows]


class GpclientVPNPlugin(DbusInterfaceCommonAsync, interface_name=NM_DBUS_INTERFACE_VPN):
    """NetworkManager VPN Plugin for gpclient using python-sdbus"""

    def __init__(self):
        super().__init__()
        self.gpclient_process = None
        # (pid, starttime) of the gpauth processes of our own gpclient that
        # may still run; see _remember_gpauth()
        self._gpauth_leftovers: List[Tuple[int, int]] = []
        self.tunnel_check_task = None
        self.stdout_monitor_task = None
        self.dns_servers = []
        self.dns_domains = []
        self.gateway = None
        self.browser = None
        self.browser_target = None  # GP_BROWSER passed to the wrapper
        self.hip_enabled = True  # HIP enabled by default
        self.hip_script = None  # validated path of a custom HIP executable
        self._state = NM_VPN_SERVICE_STATE_INIT

        # Legacy TLS renegotiation workaround (issue #2)
        self.fix_openssl_mode = "auto"  # auto | true | false
        self.fix_openssl = False  # pass --fix-openssl to gpclient
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False  # profile told not to save the passcode
        self._otp_flag_attempts = 0  # tries so far to do that (bounded)

        # Portal / gateway selection (issue #7)
        self.as_gateway = False
        self.mtu: Optional[int] = None  # pass --mtu to gpclient when set
        self.preferred_gateway = ""
        self._connection_uuid = ""
        self._permission_user = ""  # sole user of connection.permissions
        self._gateway_list: List[str] = []  # discovered during this attempt
        self._stored_gateway_list = ""  # what the profile already has cached
        self._stored_gateway_count: Optional[int] = None  # gateway-list-count

        # Routing configuration
        self.never_default = False
        self.ignore_auto_routes = False
        self.custom_routes = []

        # Tunnel-candidate interfaces (with their IPs) that already existed
        # when Connect() started - these must never be picked up by tunnel
        # detection (stale gpd0 from a crashed session, another VPN's tun0;
        # issue #7)
        self._preexisting_ifaces = {}

        # Rounds tunnel detection has waited for the vpnc hook's DNS state
        # file (issue #15)
        self._dns_state_rounds = 0
        self._vpn_gateway: Optional[ipaddress.IPv4Address] = None

        # Interactive authentication state (issue #6: RSA token / standard
        # login portals where gpclient prompts on its terminal)
        self.vpn_username = ""
        self.vpn_password = ""
        self._interactive = False
        self._pty_master = None
        self._pty_transport = None
        # Recent complete output lines, used for logging only
        self._recent_lines: deque = deque(maxlen=96)
        self._reset_output_state()
        self._auth_banner = None  # last "message (Portal: server)" banner
        self._prompt_task = None  # debounce task for prompt handling
        self._answering = False  # a prompt is currently being answered
        self._secret_future = None  # pending SecretsRequired -> NewSecrets
        self._last_answer = ""  # last answer written to the PTY
        # Secret answers written to the PTY in this attempt: masked in the
        # logged output (inquire echoes a typed answer). Never logged.
        self._written_secrets: set = set()
        self._secret_prompt_labels: set = set()
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
        logger.debug(f"Settings data: {redact_settings(settings)}")

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

        if password_not_required(data):
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
        logger.debug(f"Full connection data: {redact_settings(connection)}")
        self._interactive = interactive
        self._auth_banner = None
        self._answering = False
        self._secret_future = None
        self._last_answer = ""
        self._written_secrets = set()
        self._secret_prompt_labels = set()
        self._phase_key = None
        self._username_prefilled = False
        self._answered_username = False
        self._answered_password = False
        self._login_failed = False
        self._reset_output_state()
        self._gateway_list = []
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False
        self._otp_flag_attempts = 0

        try:
            # Extract VPN data
            if "vpn" not in connection:
                raise Exception("No VPN data in connection")

            data_dict, secrets_dict = self._parse_vpn_section(connection)
            logger.debug(
                "Parsed VPN data: "
                f"{redact_settings({'data': data_dict, 'secrets': secrets_dict})}"
            )

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
                    try:
                        dest_u32 = int(r[0])
                    except (TypeError, ValueError):
                        continue
                    try:
                        prefix = int(r[1])
                    except (TypeError, ValueError):
                        prefix = 32

                    # IMPORTANT: NM uses host order uint32 here on little-endian machines
                    dest_ip = socket.inet_ntoa(struct.pack("<I", dest_u32))

                    self.custom_routes.append((dest_ip, prefix))
                    logger.info(f"Custom route: {dest_ip}/{prefix}")

            logger.debug(f"Data dict: {data_dict}")

            # Stored credentials for standard (non-SAML) login portals.
            # Username lives in vpn.data, password in vpn.secrets.
            self.vpn_username = data_dict.get("username", "")
            if is_unsafe_username(self.vpn_username):
                self.vpn_username = ""
                raise Exception("Invalid username specified")
            self.vpn_password = secrets_dict.get("password", "")
            if self.vpn_password:
                # Masked in the output even if no prompt is ever answered with it
                self._written_secrets.add(self.vpn_password)
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
            # A connection limited to one user tells us the desktop user (#51)
            self._permission_user = self._parse_permission_user(
                connection_section.get("permissions", [])
            )

            # Get the server address (required). This is the portal address, or
            # a gateway address when as-gateway is set.
            self.gateway = data_dict.get("gateway", "").strip()
            if not self.gateway:
                raise Exception("No gateway specified")
            # The server goes to gpclient as an argument, never as an option
            if self.gateway.startswith("-") or UNSAFE_GATEWAY_RE.search(self.gateway):
                raise Exception("Invalid gateway specified")
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

            # Tunnel MTU: empty or 0 means automatic. An invalid value is
            # ignored so a typo cannot break the connection. openconnect uses
            # the value only when the gateway sends no MTU.
            mtu_text = data_dict.get("mtu", "")
            mtu = parse_mtu(mtu_text)
            if mtu is None:
                logger.warning(
                    f"Invalid mtu value {mtu_text!r} (use 576-1500), ignoring it"
                )
            self.mtu = mtu or None
            logger.info(f"MTU: {self.mtu if self.mtu else 'automatic'}")

            # Portal / gateway handling (issue #7)
            self.as_gateway = data_dict.get("as-gateway", "false").lower() == "true"
            self.preferred_gateway = data_dict.get("preferred-gateway", "").strip()
            self._stored_gateway_list = data_dict.get("gateway-list", "").strip()
            self._stored_gateway_count = parse_stored_gateway_count(
                data_dict.get("gateway-list-count", "")
            )
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
            self.dns_servers = []
            self.dns_domains = []
            dns_str = data_dict.get("dns", "")
            if dns_str:
                self.dns_servers = parse_dns_servers(dns_str)
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

            # Custom HIP executable (optional, issue #65)
            self.hip_script = None
            hip_script_str = data_dict.get("hip-script", "").strip()
            if hip_script_str and not self.hip_enabled:
                logger.info("hip-script is ignored because hip=false")
            elif hip_script_str:
                try:
                    self.hip_script = validate_hip_script(hip_script_str)
                except ValueError as e:
                    logger.error(f"Rejected hip-script {hip_script_str!r}: {e}")
                    raise Exception(f"Invalid hip-script {hip_script_str!r}: {e}")
                logger.info(f"HIP script: {self.hip_script}")

            # Emit state change: preparing
            self._set_state(NM_VPN_SERVICE_STATE_STARTING)

            # Clean up a stale gpd0 left by a crashed previous session and
            # snapshot the tunnel-candidate interfaces that exist BEFORE
            # gpclient starts, so tunnel detection cannot pick up a stale
            # or foreign interface (issue #7)
            await self._cleanup_stale_gpd0()
            self._preexisting_ifaces = await self._snapshot_tunnel_interfaces()

            # A DNS state file from a previous session must not be mistaken
            # for this connection's (issue #15)
            self._clear_dns_state()
            self._dns_state_rounds = 0

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
            except Exception:
                logger.exception("Tunnel monitor task had failed")
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
            except Exception:
                logger.exception("gpclient output monitor task had failed")
            self.stdout_monitor_task = None

        # Stop gpclient first, close the PTY after. gpclient is the session
        # leader of the PTY: closing the master sends it SIGHUP, which gpclient
        # does not handle (only SIGINT and SIGTERM), so it would die at once and
        # skip the gateway logout. It does not block on the PTY while it waits:
        # it writes a few log lines at most, and the PTY buffer holds them.
        #
        # There is no `gpclient disconnect` after it: that command signals the
        # PID in the shared lock file, which is our gpclient (already stopped
        # here) or, if someone else holds the lock, a gpclient or gpservice
        # that is not ours (issue #52). The gpauth of our gpclient is noted
        # first (it is a child of gpclient) and stopped after it.
        await self._remember_gpauth()
        await self._stop_gpclient()

        # Close the PTY
        self._close_pty()

        await self._stop_remembered_gpauth()

        # Clean up
        self._clear_dns_state()
        self._dns_state_rounds = 0
        self.gpclient_process = None
        self.dns_servers = []
        self.dns_domains = []
        self.hip_enabled = True
        self.hip_script = None
        self.never_default = False
        self.custom_routes = []
        self.browser_target = None
        self.fix_openssl_mode = "auto"
        self.fix_openssl = False
        self._openssl_error_seen = False
        self._openssl_retried = False
        self._otp_flags_written = False
        self._otp_flag_attempts = 0
        self.as_gateway = False
        self.mtu = None
        self.preferred_gateway = ""
        self._connection_uuid = ""
        self._permission_user = ""
        self._gateway_list = []
        self._stored_gateway_list = ""
        self._stored_gateway_count = None
        self._reset_output_state()
        self.vpn_username = ""
        self.vpn_password = ""
        self._interactive = False
        self._auth_banner = None
        self._answering = False
        self._last_answer = ""
        self._written_secrets = set()
        self._secret_prompt_labels = set()
        self._phase_key = None
        self._username_prefilled = False
        self._answered_username = False
        self._answered_password = False
        self._login_failed = False
        self._preexisting_ifaces = {}

        # Emit state change
        self._set_state(NM_VPN_SERVICE_STATE_STOPPED)

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
            if is_unsafe_username(data["username"]):
                # Same rule as in _do_connect; never log the value
                logger.warning("NewSecrets(): ignoring an invalid username")
            else:
                self.vpn_username = data["username"]

        if self._secret_future and not self._secret_future.done():
            self._secret_future.set_result(secrets)
        else:
            logger.warning("NewSecrets() received but no secret request pending")

    # Signals
    @dbus_signal_async("u")
    def StateChanged(self, state: int) -> None:
        """Signal: VPN state changed (emit it through _set_state)"""

    @dbus_signal_async("sas")
    def SecretsRequired(self, message: str, secrets: List[str]) -> None:
        """Signal: Secrets required"""

    @dbus_signal_async("a{sv}")
    def Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: Configuration ready"""

    @dbus_signal_async("a{sv}")
    def Ip4Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: IPv4 configuration ready"""

    @dbus_signal_async("a{sv}")
    def Ip6Config(self, config: Dict[str, Tuple[str, Any]]) -> None:
        """Signal: IPv6 configuration ready"""

    @dbus_signal_async("s")
    def LoginBanner(self, banner: str) -> None:
        """Signal: Login banner"""

    @dbus_signal_async("u")
    def Failure(self, reason: int) -> None:
        """Signal: VPN connection failed"""

    # Properties
    @dbus_property_async("u")
    def State(self) -> int:
        """Property: Current VPN state"""
        return self._state

    @staticmethod
    def _parse_permission_user(permissions: Any) -> str:
        """Return the user name when `connection.permissions` names exactly one

        NetworkManager stores entries as "user:<name>:" (the trailing colon is
        optional here). Values arrive as an `as` array, possibly wrapped in an
        sdbus (signature, value) tuple. Returns "" for none or several users.
        """
        if isinstance(permissions, tuple) and len(permissions) == 2:
            permissions = permissions[1]
        if not isinstance(permissions, (list, tuple)):
            return ""
        users = set()
        for entry in permissions:
            if not isinstance(entry, str):
                continue
            parts = entry.split(":")
            if len(parts) >= 2 and parts[0] == "user" and parts[1]:
                users.add(parts[1])
        return users.pop() if len(users) == 1 else ""

    @staticmethod
    def _loginctl(*args: str) -> str:
        """Output of `loginctl <args>` ("" when it fails)"""
        result = subprocess.run(
            ["loginctl", *args],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout if result.returncode == 0 else ""

    @staticmethod
    def _session_props(output: str) -> Dict[str, str]:
        """Parse the Key=Value lines of `loginctl show-session`"""
        props = {}
        for line in output.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key.strip()] = value.strip()
        return props

    @staticmethod
    def _user_entry(pw_lookup: Any, key: Any) -> Optional[Tuple[int, str, str]]:
        """(uid, name, home) from pwd.getpwuid/getpwnam, None for root or unknown"""
        try:
            pw = pw_lookup(key)
        except KeyError:
            return None
        if pw.pw_uid == 0:
            return None
        return pw.pw_uid, pw.pw_name, pw.pw_dir

    @staticmethod
    def _seat0_active_user() -> Optional[int]:
        """UID of the active local graphical user session on seat0, or None"""
        loginctl = GpclientVPNPlugin._loginctl
        try:
            session_id = loginctl(
                "show-seat", "seat0", "-p", "ActiveSession", "--value"
            ).strip()
            if not session_id:
                return None
            props = GpclientVPNPlugin._session_props(
                loginctl(
                    "show-session",
                    session_id,
                    "-p",
                    "User",
                    "-p",
                    "Class",
                    "-p",
                    "Remote",
                    "-p",
                    "State",
                )
            )
            if (
                props.get("Class") != "user"
                or props.get("Remote") != "no"
                or props.get("State") != "active"
            ):
                return None
            uid = int(props.get("User", ""))
            return uid if uid != 0 else None
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            logger.debug(f"loginctl failed: {e}")
            return None

    @staticmethod
    def _single_active_session_user() -> Optional[int]:
        """UID of the one non-root user with an active session, or None

        For xrdp, remote desktops and VMs, where seat0 has no session. Remote
        sessions count. With no such user or with several, None: we do not
        guess between users. Only sessions with a display (x11, wayland, mir)
        count: a user on SSH or on a text console has no desktop for the
        browser, and must not make the choice ambiguous.
        """
        loginctl = GpclientVPNPlugin._loginctl
        uids = set()
        try:
            session_ids = [
                line.split()[0]
                for line in loginctl("list-sessions", "--no-legend").splitlines()
                if line.split()
            ]
            if not session_ids:
                return None
            # One call for all sessions: the output has one block per session,
            # separated by a blank line
            props_args = ("-p", "User", "-p", "Class", "-p", "State", "-p", "Type")
            output = loginctl("show-session", *session_ids, *props_args)
            if not output.strip():
                # A session that closed in between fails the whole call: ask
                # for each one, and skip the ones that are gone
                output = "\n\n".join(
                    loginctl("show-session", session_id, *props_args)
                    for session_id in session_ids
                )
            for block in re.split(r"\n\s*\n", output):
                props = GpclientVPNPlugin._session_props(block)
                if props.get("Class") != "user" or props.get("State") != "active":
                    continue
                if props.get("Type") not in ("x11", "wayland", "mir"):
                    continue
                try:
                    uid = int(props.get("User", ""))
                except ValueError:
                    continue
                if uid != 0:
                    uids.add(uid)
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug(f"loginctl failed: {e}")
            return None
        return uids.pop() if len(uids) == 1 else None

    def _get_real_user(self) -> Tuple[int, str, str]:
        """Find the desktop user whose session gets the authentication browser

        Order:
        1. SUDO_UID/SUDO_USER (manual runs).
        2. The single user in the connection's `connection.permissions`.
        3. The active local session on seat0, from logind.
        4. The only non-root user with an active session of any kind (also a
           remote one: xrdp, remote desktop, VM), when seat0 has none.
        5. The current user, with a warning.

        We never pick "some other logged-in user" (issue #51): with fast user
        switching, a lingering user or a gdm greeter user, the browser would
        open in the wrong session. Step 4 is used only when exactly one user
        has an active session, so there is nothing to confuse.
        """
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
            except KeyError:
                home = f"/home/{username}"
            return uid, username, home

        # The connection is limited to one user: that is the desktop user
        if self._permission_user:
            entry = self._user_entry(pwd.getpwnam, self._permission_user)
            if entry:
                logger.debug(f"Using the user of the connection: {entry[1]}")
                return entry
            logger.debug(
                f"Connection user {self._permission_user} does not exist or is root"
            )

        # The active local session on seat0
        uid = self._seat0_active_user()
        if uid is not None:
            entry = self._user_entry(pwd.getpwuid, uid)
            if entry:
                logger.debug(f"Found active seat0 user: {entry[1]} (UID: {uid})")
                return entry
            logger.debug(f"Seat0 user with UID {uid} does not exist")

        # No seat0 session (xrdp, remote desktop, VM): one active user is clear
        uid = self._single_active_session_user()
        if uid is not None:
            entry = self._user_entry(pwd.getpwuid, uid)
            if entry:
                logger.info(
                    f"Using the active non-seat0 session of {entry[1]} (UID: {uid})"
                )
                return entry
            logger.debug(f"Session user with UID {uid} does not exist")

        logger.warning(
            "no active local session on seat0 and not exactly one other user "
            "with an active session - the authentication browser cannot be opened"
        )

        # Fallback to current user
        uid = os.getuid()
        username = os.environ.get("USER", "root")
        home = os.environ.get("HOME", f"/home/{username}")
        return uid, username, home

    async def _start_gpclient(self) -> bool:
        """Start gpclient process"""
        try:
            # Get real user first
            # (blocking loginctl calls: off the event loop)
            real_uid, real_user, _ = await asyncio.to_thread(
                self._get_real_user
            )
            logger.info(f"Will run gpclient as user: {real_user}")

            # gpauth left over from an earlier run of OUR gpclient. Never
            # `pkill gpauth`: it would also kill the gpauth of a gpclient the
            # user started from a terminal or of the GUI (issue #52).
            await self._stop_remembered_gpauth()

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
                # One argv item: gpclient's --hip takes an optional value, and
                # a separate path could be read as the positional server
                cmd.append(f"--hip={self.hip_script}" if self.hip_script else "--hip")

            # The server is a gateway, not a portal: skip the portal workflow
            # so the user is not authenticated twice
            if self.as_gateway:
                cmd.append("--as-gateway")

            # Tunnel MTU, set when the automatic value is too big for the
            # network (ESP falls back to HTTPS with "Message too long")
            if self.mtu:
                cmd.extend(["--mtu", str(self.mtu)])

            # Pass stored username so standard-login portals don't prompt for it
            if self.vpn_username:
                cmd.append(f"--user={self.vpn_username}")

            # Single-token options and "--" keep profile values from being
            # parsed as options of their own (--browser takes an optional value)
            cmd.extend([f"--browser={self.browser}", "--", self.gateway])

            logger.info(f"Spawning: {' '.join(cmd)}")

            # Set up environment
            env = os.environ.copy()

            # Set SUDO_UID for gpclient to detect real user
            if real_uid > 0:
                env["SUDO_UID"] = str(real_uid)
                env["SUDO_USER"] = real_user

                # No session environment here: gpclient starts gpauth as this
                # user (SUDO_UID) and builds DISPLAY, XDG_RUNTIME_DIR, the
                # D-Bus address and the rest itself (gpapi desktop_session_env)
                logger.info(
                    f"Environment: SUDO_UID={real_uid}; the session environment "
                    "comes from gpclient/gpauth"
                )

            # Tell the wrapper which browser to launch
            if self.browser_target:
                env["GP_BROWSER"] = self.browser_target

            env["GPCLIENT_NM_IGNORE_AUTO_ROUTES"] = (
                "1" if self.ignore_auto_routes else "0"
            )
            env["GPCLIENT_NM_NEVER_DEFAULT"] = "1" if self.never_default else "0"

            # Where the vpnc hook records the DNS the gateway pushes, so it
            # can be reported to NetworkManager (issue #15)
            env[DNS_STATE_ENV] = DNS_STATE_FILE

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

            def _child_setup():
                # New session + make the PTY slave (fd 0) the controlling
                # terminal so /dev/tty works inside gpclient
                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            try:
                # Wide window so prompts don't wrap mid-line
                fcntl.ioctl(
                    master_fd,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", PTY_ROWS, PTY_COLUMNS, 0, 0),
                )
                self.gpclient_process = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    preexec_fn=_child_setup,
                )
            except BaseException:
                # Nobody owns the master yet (_pty_master is set below)
                os.close(master_fd)
                raise
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

                lines = self._consume_output(self._decode_output(chunk))

                # Once the answered prompt is committed as a full line (its
                # echo flushed), stop suppressing on the old answer - otherwise
                # a later prompt that merely contains it as a substring (e.g.
                # answer "code" vs "? Enter passcode:") is suppressed forever.
                if self._last_answer and any(
                    self._last_answer in ln for ln in lines
                ):
                    self._last_answer = ""

                for raw_line in lines:
                    # Raw, not stripped: _recent_lines only feeds logging (the
                    # prompts are read from _screen)
                    self._recent_lines.append(raw_line)

                    line = raw_line.strip()
                    # inquire redraws lines on every keystroke; skip repeats
                    if line == last_logged_line:
                        continue
                    last_logged_line = line
                    shown = mask_secret_prompts(line, self._secret_prompt_labels)
                    logger.info(
                        f"gpclient output: {mask_secrets(shown, self._written_secrets)}"
                    )

                    found = parse_gateway_count(line)
                    if found is not None:
                        self._gateway_count = found

                    chosen = GATEWAY_CHOSEN_RE.search(line)
                    if chosen:
                        self._record_gateways([chosen.group("gateway")])
                        if (
                            "the only available" in line
                            and self._gateway_count == 1
                        ):
                            # The portal config holds just this one gateway:
                            # the whole list, so it replaces the stored one.
                            # Without "Found 1" gpclient may have fallen back
                            # to the portal address - that says nothing
                            self._lap_entries = []
                            self._append_entries(
                                self._lap_entries, [chosen.group("gateway")]
                            )

                    self._tunnel_settings = parse_tunnel_settings_line(
                        line, self._tunnel_settings
                    )

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

                    if INTERNAL_NETWORK_RE.search(line):
                        self._fail_internal_network()

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

            # Process ended. Note its gpauth children now: once gpclient is
            # reaped they belong to init and cannot be told from other gpauth
            await self._remember_gpauth()
            returncode = await self.gpclient_process.wait()
            logger.info(f"gpclient process exited with status {returncode}")

            if returncode != 0 and not self._login_failed:
                if await self._retry_with_openssl_fix():
                    # A new monitor task took over the retried process
                    return
                logger.error(f"gpclient failed with exit code {returncode}")
                self._emit_failure(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)
            elif returncode == 0 and not self._login_failed:
                # Disconnect() cancels this task before it stops gpclient, so
                # an exit seen here was not asked for. Upstream returns Ok(())
                # on some failures; without a Failure NetworkManager waits for
                # vpn.timeout.
                logger.error("gpclient exited (status 0) without being asked to")
                self._emit_failure(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error monitoring gpclient output: {e}")

    def _reset_output_state(self) -> None:
        """Forget everything read from gpclient's PTY (a new attempt starts)"""
        self._output_scanner = OutputScanner()
        self._ansi_carry = ""  # incomplete escape sequence from the last read
        self._tokenizer = EscapeTokenizer()  # keeps running after the tunnel is up
        # Multi-byte characters are cut by read boundaries
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._recent_lines.clear()
        # What the terminal would show right now; the only way to see an
        # incrementally redrawn Select frame (issue #25)
        self._screen = ScreenBuffer()
        self._tunnel_up = False  # STARTED emitted: stop watching for a Select
        # (screen row id, label) of the text prompt we answered last
        self._answered_prompt: Optional[Tuple[int, str]] = None
        self._answered_select = None  # message of the Select we answered
        # "Found N gateways in portal config" of this attempt, and the entries
        # of the whole list when this attempt saw all of it (read page by
        # page, or a list that is not paged); empty otherwise
        self._gateway_count: Optional[int] = None
        self._lap_entries: List[str] = []
        # The tunnel settings gpclient logged in this attempt (issue #37)
        self._tunnel_settings = TunnelSettings()

    def _decode_output(self, chunk: bytes) -> str:
        """Decode a PTY read; a character split across reads stays whole.

        Without this the halves become U+FFFD, and inquire - which redraws only
        the rows that changed - would never repair them on the screen.
        """
        return self._decoder.decode(chunk)

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

        # One parse for both consumers, so they cannot disagree about the syntax
        tokens = self._tokenizer.feed(text)
        if not self._tunnel_up:
            self._screen.apply(tokens)
        return self._output_scanner.feed(tokens_text(tokens))

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
        self._reset_output_state()
        self._auth_banner = None
        self._answering = False
        self._last_answer = ""
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

    async def _remember_gpauth(self) -> None:
        """Note the gpauth processes below our gpclient, for later cleanup.

        Only descendants of the gpclient this service started are noted, each
        with its start time. Never raises.
        """
        process = self.gpclient_process
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int) or getattr(process, "returncode", None) is not None:
            return
        try:
            found = await asyncio.to_thread(gpauth_descendants, pid)
        except Exception as e:
            logger.debug(f"Could not look for the gpauth of gpclient: {e}")
            return
        for entry in found:
            if entry not in self._gpauth_leftovers:
                self._gpauth_leftovers.append(entry)

    async def _stop_remembered_gpauth(self) -> None:
        """SIGTERM, then SIGKILL, the gpauth processes noted by
        _remember_gpauth() that still run.

        A process is signalled only while its PID still has the start time we
        noted, so a reused PID is never hit. Nothing else is signalled. Never
        raises.
        """
        leftovers, self._gpauth_leftovers = self._gpauth_leftovers, []
        if not leftovers:
            return
        try:
            async def signal_all(sig: int) -> List[Tuple[int, int]]:
                sent = []
                for pid, starttime in leftovers:
                    if await asyncio.to_thread(signal_if_same, pid, starttime, sig):
                        sent.append((pid, starttime))
                return sent

            if not await signal_all(signal.SIGTERM):
                return
            logger.info("Stopped gpauth of our gpclient (SIGTERM)")
            deadline = asyncio.get_running_loop().time() + GPAUTH_TERMINATE_TIMEOUT
            while asyncio.get_running_loop().time() < deadline:
                alive = [
                    entry
                    for entry in leftovers
                    if await asyncio.to_thread(is_same_process, *entry)
                ]
                if not alive:
                    return
                await asyncio.sleep(0.1)
            if await signal_all(signal.SIGKILL):
                logger.warning("gpauth of our gpclient did not exit, killed it")
        except Exception as e:
            logger.error(f"Error stopping gpauth: {e}")

    async def _stop_gpclient(self) -> None:
        """Stop gpclient with SIGTERM, so it logs out of the gateways.

        Waits GPCLIENT_TERMINATE_TIMEOUT, then sends SIGKILL. Does nothing when
        there is no process. Never raises: a failure here must not stop the
        rest of the shutdown.
        """
        process = self.gpclient_process
        if not process:
            return
        try:
            logger.info(f"Terminating gpclient process (PID: {process.pid})")
            try:
                process.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=GPCLIENT_TERMINATE_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("gpclient didn't terminate, killing it")
                try:
                    process.kill()
                except ProcessLookupError:
                    return
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    logger.error("gpclient process refused to die after SIGKILL")
        except Exception as e:
            logger.error(f"Error terminating gpclient: {e}")

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
        logger.error(f"Reporting failure to NetworkManager: reason {reason}")
        self.Failure.emit(reason)
        self._set_state(NM_VPN_SERVICE_STATE_STOPPED)

    def _set_state(self, state: int) -> None:
        """Record the VPN state, log it and tell NetworkManager.

        python-sdbus does not run the body of a signal method on emit(), so the
        State property must be set here.
        """
        self._state = state
        logger.info(f"StateChanged: {state}")
        self.StateChanged.emit(state)

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

        # A list prompt (the gateway list) is read from the screen model, and
        # has to be checked before the tail-based text prompt detection -
        # otherwise "? Which gateway do you want to connect to?" would be
        # answered with the username.
        select_frame = (
            None if self._tunnel_up else detect_select_prompt(self._screen.lines())
        )
        if select_frame is not None:
            if select_frame["message"] == self._answered_select:
                self._prompt_task = None
                return

            async def _debounced_select(version: int):
                await asyncio.sleep(PROMPT_DEBOUNCE_SECONDS)
                # Only act if no further output arrived (frame fully rendered)
                if self._screen.version != version:
                    return
                await self._handle_select_prompt(select_frame)

            self._prompt_task = asyncio.create_task(
                _debounced_select(self._screen.version)
            )
            return

        found = self._screen_prompt()
        if found is None:
            self._prompt_task = None
            return
        label = found[1]

        async def _debounced(version: int):
            await asyncio.sleep(PROMPT_DEBOUNCE_SECONDS)
            # Only act if the screen has not changed since we saw the prompt
            if self._screen.version != version:
                return
            self._answered_prompt = found
            await self._handle_prompt(label)

        self._prompt_task = asyncio.create_task(_debounced(self._screen.version))

    def _screen_prompt(self) -> Optional[Tuple[int, str]]:
        """The text prompt gpclient waits at, as (row id, label), or None.

        Read from the screen model: inquire redraws incrementally, so the line
        stream holds stale rows and only some of the new ones (issue #27). The
        line stream is used for logging only. Once the tunnel is up the screen
        is no longer fed, and no prompt is expected.

        A prompt that was already answered is skipped for as long as its row
        and label have not changed. They change when inquire finalises the
        answer or draws another prompt there; a prompt that comes back later is
        a new one.
        """
        if self._tunnel_up:
            return None
        found = detect_screen_prompt(
            self._screen.lines(), self._screen.cursor_row, self._last_answer
        )
        prompt = None if found is None else (self._screen.row_id(found[0]), found[1])
        if prompt is None or prompt != self._answered_prompt:
            self._answered_prompt = None  # the answered row has moved on
        if prompt is None or prompt == self._answered_prompt:
            return None
        # The final line of an answered list ("? Which gateway ... gw-a") is
        # not a prompt either
        if self._answered_select and prompt[1].startswith(self._answered_select):
            return None
        return prompt

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
        # Text in brackets is a hint and may be a negation ("Password (not
        # your PIN)"), so it is never looked at
        main = LABEL_HINT_RE.sub(" ", label)

        # One-time challenge, by keyword in the label OR by position (anything
        # after we've already sent the password this phase).
        if is_one_time_secret(main) or self._answered_password:
            return "otp"

        # Username by keyword. This comes before the banner check: an RSA
        # banner is printed before the Username prompt too, and the token is
        # only wanted at the password prompt (issue #6).
        if classify_prompt(main) == "username":
            return "username"

        # A prompt under a one-time banner ("Please enter RSA token"). Before
        # the positional username rule: a Gateway phase without a stored
        # username starts with nothing answered, yet gpclient asks only for
        # the token there, as a bare "Password".
        if is_one_time_secret(banner_msg):
            return "otp"

        # Username by position: the first credential prompt of the phase when
        # no username was answered yet (a localized label the keywords miss)
        if not self._answered_username:
            return "username"

        return "password"

    async def _handle_prompt(self, label: str) -> None:
        """Answer an interactive gpclient prompt (username/password/OTP)"""
        logger.info(f"Detected interactive prompt: {label!r}")

        banner_msg = self._auth_banner["message"] if self._auth_banner else ""
        kind = self._classify_prompt_kind(label, banner_msg)

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

        # Only the username stored in the profile may show up in the log. A
        # prompt that was classified as "username" (also by position, for a
        # label we do not know) can still ask for a secret; a password that
        # equals the stored username is still a password.
        secret = not (
            kind == "username" and self.vpn_username and answer == self.vpn_username
        )
        self._write_answer(answer, secret=secret, label=label)

    @staticmethod
    def _gateway_entry(option: str) -> str:
        """A gateway as it is cached in the profile ("" if nothing is left)"""
        # ';' separates cached entries and nmcli splits +vpn.data values on
        # commas, so neither may survive inside an entry
        entry = option.replace(",", " ").replace(GATEWAY_LIST_SEPARATOR, " ")
        return " ".join(entry.split())

    def _stored_list_is_complete(self, options: List[str]) -> bool:
        """Does the profile already hold the whole list this portal offers?

        Only reading a paged list to its end tells, and that takes a few
        PageDown presses, so it is done once: the stored list counts as complete
        when gateway-list-count equals the portal's current count (gpclient's
        "Found N"; without one nothing is trusted) and every gateway on the
        visible page is in it.
        """
        if self._stored_gateway_count is None:
            return False
        if self._gateway_count != self._stored_gateway_count:  # also if None
            return False
        stored = set(self._stored_gateway_list.split(GATEWAY_LIST_SEPARATOR))
        return all(
            self._gateway_entry(option) in stored
            for option in options
            if self._gateway_entry(option)
        )

    def _append_entries(self, entries: List[str], options: List[str]) -> None:
        """Add the options to `entries` as cached entries, each only once"""
        for option in options:
            entry = self._gateway_entry(option)
            if entry and entry not in entries:
                entries.append(entry)

    def _record_gateways(self, options: List[str]) -> None:
        """Remember gateways seen during this attempt, for the profile cache"""
        self._append_entries(self._gateway_list, options)

    async def _handle_select_prompt(self, frame: Dict[str, Any]) -> None:
        """Answer gpclient's gateway list without interrupting the user.

        The gateway comes from vpn.data preferred-gateway; with none configured
        (the default) the portal's first proposal wins. A configured gateway
        that the portal no longer offers falls back to the first proposal - the
        connection setting itself is left alone (issue #7).
        """
        options = frame["options"]
        self._answered_select = frame["message"]
        self._answering = True
        try:
            self._record_gateways(options)
            logger.info(f"gpclient asks to choose: {frame['message']}")
            logger.info(
                f"Gateways offered: {options}"
                + (" (list continues past the visible page)" if frame["more"] else "")
            )

            # The whole list, when this attempt gets to see all of it
            self._lap_entries = []
            shown = frame  # the frame the selection starts from
            full = None  # every option of the list, once all of it was read
            if frame["more"]:
                if not self._stored_list_is_complete(options):
                    shown, full = await self._collect_gateway_pages(frame)
            elif self._gateway_count == len(options):
                # Not paged: what is shown is the whole list - if the portal
                # reports no other number of gateways
                self._append_entries(self._lap_entries, options)

            preferred = self.preferred_gateway
            walking = False  # looking through a paged list for `preferred`
            step_limit = SELECT_MAX_STEPS
            # Walking on to a gateway known by its name: no lap, no search
            homing = False
            if full is not None:
                # The whole list is known: decide here, then just walk to it
                target = pick_gateway(full, preferred) if preferred else None
                if target is None:
                    target = options[0]
                    if preferred:
                        logger.warning(
                            f"Preferred gateway {preferred!r} is not offered by "
                            f"the portal - falling back to the first proposal "
                            f"{target!r} (the connection setting is left "
                            "unchanged)"
                        )
                else:
                    logger.info(f"Preferred gateway {preferred!r} matches {target!r}")
                wanted = target
                matches = lambda option: option == target  # noqa: E731
                homing = True
                step_limit = max(SELECT_MAX_STEPS, self._gateway_count + 1)
            elif not preferred:
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
                    # Cannot see the whole list yet - walk it looking for a
                    # match. Only an exact name/host counts on the way: "gw-1"
                    # must not stop at "gw-10" while a real "gw-1" is further
                    # down. The first substring hit is remembered as the
                    # fallback once the walk wrapped around without an exact one.
                    wanted = preferred
                    matches = lambda option: gateway_matches(  # noqa: E731
                        preferred, option, substring=False
                    )
                    walking = True
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

            start_option = shown["options"][shown["cursor"]]
            current_frame = shown
            steps = 0  # Down presses so far
            substring_hit = None  # first entry that merely contains the name

            while True:
                self._record_gateways(current_frame["options"])
                current = current_frame["options"][current_frame["cursor"]]

                if walking and substring_hit is None and gateway_matches(
                    preferred, current
                ):
                    substring_hit = current

                lapped = steps > 0 and current == start_option
                if (
                    walking
                    and substring_hit is not None
                    and (lapped or steps >= step_limit)
                    and not matches(current)
                ):
                    # No exact match on the lap (or none within the step
                    # limit): go on to the first entry that contains the name
                    # (as on a short list), looking for it by name, with a
                    # step budget of its own
                    reason = "No exact match" if lapped else "Step limit reached"
                    logger.info(
                        f"{reason} for {preferred!r} after {steps} steps - "
                        f"going to {substring_hit!r}"
                    )
                    walking = False
                    homing = True
                    hit = substring_hit
                    # An exact match seen on the way back still wins
                    matches = lambda option: (  # noqa: E731
                        option == hit
                        or gateway_matches(preferred, option, substring=False)
                    )
                    step_limit = steps + SELECT_MAX_STEPS
                elif lapped and not homing:
                    first_proposal = options[0]
                    logger.warning(
                        f"Walked the whole list without finding {wanted!r} - "
                        f"selecting the first proposal {first_proposal!r}"
                    )
                    # The walk began on the first proposal, unless gpclient did
                    # not redraw after Home: then go on to it by its name
                    homing = True
                    matches = lambda option: option == first_proposal  # noqa: E731
                    step_limit = steps + SELECT_MAX_STEPS

                if matches(current):
                    logger.info(f"Selecting gateway: {current!r}")
                    self._write_keys(KEY_ENTER, f"select {current!r}")
                    return

                if steps >= step_limit:
                    logger.warning(
                        f"Gave up after {steps} steps through the gateway list - "
                        f"selecting {current!r}"
                    )
                    self._write_keys(KEY_ENTER, f"select {current!r}")
                    return

                next_frame = await self._press_list_down(current_frame)
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

    async def _collect_gateway_pages(
        self, frame: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[List[str]]]:
        """Read a paged gateway list page by page.

        Returns the frame to select from and, when the whole list was seen, all
        its options in list order (else None).

        PageDown moves the cursor down by a page without wrapping and stops at
        the last entry, so a few key presses show the whole list (issue #25).
        Only gpclient's "Found N" tells the end of the list from a gpclient that
        stopped redrawing, so without it nothing is read. From the first entry
        the cursor is on the last one after ceil((N - 1) / page) presses; a
        PageDown without a redraw ends the reading, and it was the end of the
        list only if that many presses did redraw. The list then goes to
        `_lap_entries`, which replaces the cached list. Home puts the cursor
        back on the first proposal; without a redraw the last frame is returned
        and the selection goes on from there.
        """
        count = self._gateway_count  # gpclient's "Found N"
        self._lap_entries = []
        if count is None:
            logger.info(
                "gpclient did not log the gateway count - not reading the "
                "gateway list page by page"
            )
            return frame, None

        logger.info("Reading the whole gateway list page by page")
        full: List[str] = []

        def see(shown: Dict[str, Any]) -> None:
            self._record_gateways(shown["options"])
            self._append_entries(self._lap_entries, shown["options"])
            full.extend(o for o in shown["options"] if o not in full)

        see(frame)
        last = frame
        reached_end = False
        # Redraws that put the cursor on the last entry (page = rows shown)
        needed = -(-(count - 1) // len(frame["options"]))
        presses = redraws = 0
        while True:
            if len(self._lap_entries) >= count:
                reached_end = True
                break
            if presses >= needed + 2:
                break
            next_frame = await self._press_list_key(
                last, KEY_PAGE_DOWN, "page down the gateway list"
            )
            presses += 1
            if next_frame is None:
                # No redraw: the cursor is on the last entry - or gpclient
                # stalled half way
                reached_end = redraws >= needed
                break
            redraws += 1
            last = next_frame
            see(last)

        complete = reached_end and len(self._lap_entries) <= count
        if complete:
            logger.info(
                f"Read the whole gateway list: {len(self._lap_entries)} entries"
            )
        else:
            logger.warning(
                f"Could not read the whole gateway list ({len(self._lap_entries)} "
                "entries seen) - keeping what the profile has"
            )
            self._lap_entries = []

        result = full if complete else None
        if last is frame:
            return frame, result
        home = await self._press_list_key(last, KEY_HOME, "go to the first gateway")
        if home is None:
            logger.warning(
                "gpclient did not redraw the list after Home - selecting from "
                f"the highlighted entry {last['options'][last['cursor']]!r}"
            )
            return last, result
        return home, result

    async def _press_list_key(
        self, previous: Dict[str, Any], key: bytes, what: str
    ) -> Optional[Dict[str, Any]]:
        """Press a list key, return the redrawn frame.

        `previous` is the frame the cursor is on. Down wraps around in inquire,
        so it reaches every entry, including ones outside the visible page;
        PageDown and Home do not wrap. None means the highlighted entry did not
        change: gpclient did not redraw, or the cursor is where the key leads.

        inquire redraws only the rows that changed (issue #25), so the frame is
        read from the screen model, and only once the same frame shows on two
        consecutive polls: a frame read in the middle of a redraw mixes old and
        new rows, and such a frame does not stay the same. Unrelated output
        does not hold the walk up.
        """
        previous_option = previous["options"][previous["cursor"]]

        self._write_keys(key, what)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SELECT_REDRAW_TIMEOUT
        last_seen = None
        while loop.time() < deadline:
            await asyncio.sleep(SELECT_POLL_INTERVAL)
            frame = detect_select_prompt(self._screen.lines())
            seen = (
                None
                if frame is None
                else (tuple(frame["options"]), frame["cursor"], frame["more"])
            )
            if seen is not None and seen == last_seen:
                if frame["options"][frame["cursor"]] != previous_option:
                    return frame
            last_seen = seen
        return None

    async def _press_list_down(
        self, previous: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Move the list cursor one entry down, return the redrawn frame"""
        return await self._press_list_key(
            previous, KEY_DOWN, "move down the gateway list"
        )

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
        if self._otp_flag_attempts >= OTP_FLAG_MAX_ATTEMPTS:
            return

        self._otp_flag_attempts += 1
        logger.info(
            "Marking the one-time code as not-saved in the profile and dropping "
            "any stored value"
        )
        flagged = await self._write_vpn_data(f"{OTP_SECRET_KEY}-flags", "2")
        # Without the flag dropping the value would not hold: skip it and try
        # both again at a later prompt
        dropped = flagged and await self._nmcli_modify("-vpn.secrets", OTP_SECRET_KEY)
        # Only after both succeeded: on a failure the code may still be in the
        # profile, so the next one-time prompt has to try again - a few times,
        # not at every prompt (each nmcli call may wait 10 s)
        self._otp_flags_written = flagged and dropped
        if not self._otp_flags_written and (
            self._otp_flag_attempts >= OTP_FLAG_MAX_ATTEMPTS
        ):
            logger.warning(
                f"Giving up on marking the one-time code as not-saved after "
                f"{self._otp_flag_attempts} attempts - a stored code may stay "
                "in the profile"
            )

    async def _persist_gateway_list(self) -> None:
        """Cache the discovered gateway list in the connection profile.

        The connection editors read vpn.data gateway-list to offer a gateway
        drop-down; nothing in the connect path depends on it.
        """
        if not self._gateway_list:
            return

        count = None
        if self._lap_entries:
            # The whole list was seen (read page by page, or not paged): it
            # replaces what was stored, so gateways the portal dropped
            # disappear
            entries = self._lap_entries
            count = self._gateway_count
        else:
            # Only part of the list was seen: never shrink what is stored.
            # What was seen comes first (the portal's order), then the stored
            # entries that were not seen this time
            entries = list(self._gateway_list)
            entries += [
                e
                for e in self._stored_gateway_list.split(GATEWAY_LIST_SEPARATOR)
                if e and e not in entries
            ]

        value = GATEWAY_LIST_SEPARATOR.join(entries)
        changed = False
        if value != self._stored_gateway_list:
            logger.info(f"Caching gateway list in the connection profile: {value}")
            if not await self._write_vpn_data("gateway-list", value):
                return
            self._stored_gateway_list = value
            changed = True
        # The count marks the list as complete, so it follows the list
        if count is not None and count != self._stored_gateway_count:
            if await self._write_vpn_data("gateway-list-count", str(count)):
                self._stored_gateway_count = count
            else:
                logger.warning(
                    "Could not store the gateway count - the next connection "
                    "reads the gateway list again"
                )
            changed = True
        if not changed:
            logger.debug("Gateway list unchanged, leaving the profile alone")

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
        logger.info(f"SecretsRequired: {message}, {hints}")
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

    def _write_answer(
        self, answer: str, secret: bool = False, label: str = ""
    ) -> None:
        """Type an answer into gpclient's PTY (never logs the answer)

        `label` is the prompt that is answered: the echo of a secret after that
        label is hidden in the log, also while it is still being typed.
        """
        self._last_answer = answer
        if secret and answer:
            self._written_secrets.add(answer)
            if label.strip():
                self._secret_prompt_labels.add(label.strip())
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

    def _fail(self, reason_code: int, message: str) -> None:
        """Report one failure to NetworkManager and terminate gpclient.

        _login_failed means "failure already reported": it keeps the monitor
        from reporting the exit of the terminated gpclient a second time.
        """
        logger.error(message)
        self._login_failed = True
        self._emit_failure(reason_code)
        if self.gpclient_process:
            try:
                self.gpclient_process.terminate()
            except ProcessLookupError:
                pass

    def _fail_login(self, reason: str) -> None:
        """Emit a login failure and terminate gpclient"""
        self._fail(NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED, f"Login failed: {reason}")

    def _fail_internal_network(self) -> None:
        """The gateway authenticated us without a tunnel: nothing to connect.

        Not a login failure (the credentials were fine), so this reports
        CONNECT_FAILED. The line can come more than once (one per gateway), and
        a failure may be reported already: then there is nothing to do.
        """
        if self._login_failed:
            return
        self._fail(
            NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED,
            "The device is on the internal network; the gateway authenticated "
            "it without a VPN tunnel, so there is nothing to connect",
        )

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
        session or another VPN client's tunnel (issue #7). Any number of
        foreign tunnels may be up - the snapshot grows with them (issue #13).
        """
        snapshot = {}
        for iface in list_tunnel_candidates():
            ip_addr, _ = await self._get_iface_ipv4(iface)
            snapshot[iface] = ip_addr
            logger.info(
                f"Interface {iface} (IP: {ip_addr}) already exists before "
                "gpclient start - it will be ignored by tunnel detection "
                "unless its address changes"
            )
        return snapshot

    def _tunnel_ifaces_owned_by_gpclient(self) -> set:
        """Tunnel interfaces held open by our own gpclient process tree.

        Empty when the answer is unknown (gpclient already exited, /proc
        unreadable) - the caller then relies on the snapshot alone.
        """
        process = self.gpclient_process
        if process is None or process.returncode is not None:
            return set()
        try:
            return tunnel_ifaces_held_by(process_tree(process.pid))
        except Exception as e:
            logger.debug(f"Could not determine gpclient's tunnel interfaces: {e}")
            return set()

    def _clear_dns_state(self) -> None:
        """Remove the vpnc hook's DNS state file, if any."""
        try:
            os.unlink(DNS_STATE_FILE)
            logger.debug(f"Removed DNS state file {DNS_STATE_FILE}")
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Could not remove DNS state file {DNS_STATE_FILE}: {e}")

    def _read_learned_dns(
        self, iface: str
    ) -> Optional[Tuple[List[str], List[str], List[str]]]:
        """DNS the gateway pushed for `iface`, as recorded by the vpnc hook.

        None when the file is not there (yet) or describes another tunnel.
        Also sets self._vpn_gateway (the state's VPNGATEWAY, or None).
        """
        self._vpn_gateway = None
        try:
            with open(DNS_STATE_FILE, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning(f"Could not read DNS state file {DNS_STATE_FILE}: {e}")
            return None

        state = parse_dns_state(text)
        learned = learned_dns_from_state(state, iface)
        self._vpn_gateway = vpn_gateway_from_state(state, iface)
        if learned is None:
            logger.debug(
                f"DNS state file is for {state.get('TUNDEV')!r}, not {iface} - ignoring"
            )
        return learned

    def _cross_check_logged_dns(
        self,
        iface: str,
        learned: Optional[Tuple[List[str], List[str], List[str]]],
    ) -> Optional[Tuple[List[str], List[str], List[str]]]:
        """Compare the vpnc hook's DNS with the settings gpclient logged (#37).

        The hook stays the source: its values are never replaced. What the
        hook lacks (no servers, or no search domains) is taken from the log
        when gpclient logged a complete list for this very interface. When
        both exist and differ, one warning shows both. The interface and the
        address used for routing are never taken from the log.
        """
        settings = self._tunnel_settings
        if settings.interface != iface:
            return learned
        servers, domains, servers6 = learned or ([], [], [])

        logged_servers: Optional[List[str]] = None
        if settings.dns is not None:
            logged_servers = list(settings.dns)
        logged_domains: Optional[List[str]] = None
        if settings.search_domains is not None and settings.split_dns is not None:
            logged_domains = _dedupe(
                list(settings.search_domains) + list(settings.split_dns)
            )

        hook_servers = servers + servers6
        mismatches = []
        if logged_servers:
            if not hook_servers:
                servers = [s for s in logged_servers if ":" not in s]
                servers6 = [s for s in logged_servers if ":" in s]
                logger.info(
                    f"No DNS servers from the vpnc hook - using the ones "
                    f"gpclient logged for {iface}: {logged_servers}"
                )
            elif {_dns_key(s) for s in hook_servers} != {
                _dns_key(s) for s in logged_servers
            }:
                mismatches.append(
                    f"DNS servers: hook {hook_servers}, gpclient {logged_servers}"
                )
        if logged_domains:
            if not domains:
                domains = logged_domains
                logger.info(
                    f"No search domains from the vpnc hook - using the ones "
                    f"gpclient logged for {iface}: {logged_domains}"
                )
            elif {d.lower() for d in domains} != {d.lower() for d in logged_domains}:
                mismatches.append(
                    f"search domains: hook {domains}, gpclient {logged_domains}"
                )
        if mismatches:
            logger.warning(
                f"The vpnc hook and gpclient's log disagree for {iface} "
                f"(keeping the hook's values): " + "; ".join(mismatches)
            )
        if learned is None and not (servers or domains or servers6):
            return None
        return servers, domains, servers6

    @staticmethod
    def _dns_to_nm(
        entries: List[Tuple[str, Optional[Any]]],
    ) -> List[int]:
        """IPv4 servers as NetworkManager uint32s; anything else is skipped.

        `entries` are (text, parse_ip_address(text)) pairs.
        """
        dns_list = []
        for dns, address in entries:
            if address is None:
                logger.warning(f"Failed to convert DNS {dns!r}: not an IP address")
            elif address.version == 6:
                # Ip4Config has no room for it
                logger.info(
                    f"DNS server {dns} is IPv6 - not reported to NetworkManager"
                )
            else:
                dns_list.append(ipv4_to_nm_uint32(address))
                logger.info(f"Added DNS server: {dns}")
        return dns_list

    def _build_dns_config(
        self,
        config: Dict[str, Tuple[str, Any]],
        learned: Optional[Tuple[List[str], List[str], List[str]]],
    ) -> None:
        """Fill in Ip4Config's dns/domains (issue #15).

        The profile's `dns` override wins over the servers the gateway pushed,
        unless none of its entries is a valid IP address of either family
        (then the gateway's servers are used); an IPv6-only override is kept
        as it is, which leaves Ip4Config without DNS servers. Search domains
        are the gateway's plus the profile's `dns-domains`.
        Handing them to NetworkManager is what keeps them alive: it reapplies
        its own VPN DNS configuration every time it recomputes DNS, whereas
        what vpnc-script wrote to systemd-resolved is overwritten with nothing.
        """
        learned_servers, learned_domains, learned_servers6 = learned or ([], [], [])

        profile = [(dns, parse_ip_address(dns)) for dns in self.dns_servers]
        addresses = [address for _, address in profile if address is not None]
        if addresses:
            dns_list = self._dns_to_nm(profile)
            if learned_servers:
                logger.info(
                    f"DNS servers overridden by the profile: {self.dns_servers} "
                    f"(gateway pushed {learned_servers})"
                )
            if all(address.version == 6 for address in addresses):
                logger.warning(
                    f"The DNS servers in the profile are IPv6 only "
                    f"({self.dns_servers}): IPv6 DNS servers are not applied "
                    "and no IPv4 DNS servers will be configured"
                )
        else:
            if self.dns_servers:
                # A typo in the only override must not leave the tunnel
                # without DNS while the gateway pushed working servers
                logger.warning(
                    f"None of the DNS servers in the profile is a valid IP "
                    f"address ({self.dns_servers}) - "
                    + (
                        f"using the ones learned from the gateway: {learned_servers}"
                        if learned_servers
                        else "no DNS servers from the gateway are available "
                        "either, none will be configured"
                    )
                )
            dns_list = self._dns_to_nm(
                [(dns, parse_ip_address(dns)) for dns in learned_servers]
            )
        if dns_list:
            config["dns"] = ("au", dns_list)

        domains = _dedupe(learned_domains + list(self.dns_domains))
        if domains:
            config["domains"] = ("as", domains)
            logger.info(f"Added DNS search domains: {domains}")

        if learned_servers6:
            # No Ip6Config is emitted for the tunnel, so these stay with
            # vpnc-script alone
            logger.info(
                f"Gateway pushed IPv6 DNS servers {learned_servers6} - "
                "not reported to NetworkManager (no IPv6 config)"
            )

    async def _cleanup_stale_gpd0(self) -> None:
        """Remove a leftover gpd0 interface from a previous session.

        gpd0 is created by gpclient, and by gpservice for the GUI client. A
        gpd0 while neither runs is stale. A stale gpd0 blackholes routing (the
        portal becomes unreachable) and used to be picked up by tunnel
        detection as a live connection (issue #7). When one of them runs, gpd0
        may belong to a session of the user that is not ours (CLI or GUI), so
        it is kept (issue #52). tunN devices may belong to other VPN clients
        and are never touched. No `gpclient disconnect` runs here: it would
        signal that other session.
        """
        if not os.path.exists(f"{NET_SYSFS_PATH}/gpd0"):
            return

        for name in ("gpclient", "gpservice"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pgrep",
                    "-x",
                    name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                if await proc.wait() == 0:
                    logger.warning(
                        f"gpd0 exists and a {name} process is running - "
                        "not cleaning up"
                    )
                    return
            except Exception as e:
                logger.warning(f"Could not check for a running {name}: {e}")
                return

        logger.warning(
            "Found stale gpd0 interface with no gpclient or gpservice process "
            "- cleaning up"
        )

        if os.path.exists(f"{NET_SYSFS_PATH}/gpd0"):
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

        if not os.path.exists(f"{NET_SYSFS_PATH}/gpd0"):
            logger.info("Stale gpd0 interface removed")

    def _build_generic_config(
        self, iface: str, gateway_int: Optional[int]
    ) -> Dict[str, Tuple[str, Any]]:
        """The a{sv} dict of the Config signal (new-style plugin).

        gateway_int is the external gateway as the uint32 that Ip4Config
        carries, or None when it could not be converted."""
        generic: Dict[str, Tuple[str, Any]] = {"tundev": ("s", iface)}
        if gateway_int is not None:
            generic["gateway"] = ("u", gateway_int)
        generic["has-ip4"] = ("b", True)
        generic["has-ip6"] = ("b", False)
        if self.mtu:
            generic["mtu"] = ("u", self.mtu)
        return generic

    async def _check_tunnel_loop(self) -> None:
        """Periodically check for tunnel interface"""
        try:
            while True:
                # Which interfaces gpclient itself holds open. Looked up at
                # most once per round, and only once a new candidate actually
                # turned up, so the /proc walk stays off the idle path.
                owned_ifaces = None

                for iface in list_tunnel_candidates():
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

                    # A tunnel that appeared after Connect() is normally ours,
                    # but another VPN may well have been started at the same
                    # moment. When gpclient's own file descriptors tell us
                    # which interface is ours, trust that over the timing
                    # (issue #13).
                    if owned_ifaces is None:
                        owned_ifaces = self._tunnel_ifaces_owned_by_gpclient()
                    if owned_ifaces and iface not in owned_ifaces:
                        logger.debug(
                            f"Interface {iface} is new but not held by "
                            f"gpclient (it holds {sorted(owned_ifaces)}), "
                            "skipping"
                        )
                        continue

                    # The gateway's DNS, as recorded by the vpnc hook. The hook
                    # runs before the address is assigned, so the file is
                    # normally already there; give it a moment otherwise
                    # rather than reporting the tunnel without DNS (issue #15)
                    learned_dns = self._read_learned_dns(iface)
                    if (
                        learned_dns is None
                        and self._dns_state_rounds < DNS_STATE_WAIT_ROUNDS
                    ):
                        self._dns_state_rounds += 1
                        logger.debug(
                            f"Tunnel {iface} is up but the DNS state file is "
                            f"not there yet, waiting "
                            f"({self._dns_state_rounds}/{DNS_STATE_WAIT_ROUNDS})"
                        )
                        break
                    if learned_dns is None:
                        logger.warning(
                            "No DNS state from the vpnc hook - NetworkManager "
                            "will not know the VPN DNS servers (is "
                            "/etc/vpnc/connect.d/90-gpclient-routing installed?)"
                        )
                    learned_dns = self._cross_check_logged_dns(iface, learned_dns)

                    logger.info(
                        f"VPN connected - tunnel interface {iface} detected with IP {ip_addr}!"
                    )
                    logger.debug(f"Tunnel IP: {ip_addr}/{prefix}")

                    # Get gateway - for point-to-point VPN without explicit gateway,
                    # use the tunnel IP address itself (NetworkManager requirement)
                    # NetworkManager reads "gateway" as the VPN server's
                    # address and routes it via the physical interface, so
                    # prefer the one the vpnc hook recorded (VPNGATEWAY)
                    gateway = None
                    if self._vpn_gateway is not None:
                        gateway = str(self._vpn_gateway)
                        logger.debug(f"Gateway from vpnc hook state: {gateway}")
                    if not gateway:
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
                        # Convert IP to the uint32 NetworkManager expects
                        try:
                            ip_int = ipv4_to_nm_uint32(ip_addr)
                        except ValueError as e:
                            logger.warning(f"Not adding address {ip_addr}: {e}")
                        else:
                            config["address"] = ("u", ip_int)
                            config["prefix"] = ("u", prefix)
                            logger.info(f"Added address: {ip_addr}/{prefix}")

                    # Add gateway (required by NetworkManager, even with never-default)
                    # NetworkManager uses never-default to control routing, not plugin
                    gateway_int = None
                    if gateway:
                        try:
                            gateway_int = ipv4_to_nm_uint32(gateway)
                        except ValueError as e:
                            logger.warning(f"Not adding gateway {gateway}: {e}")
                        else:
                            config["gateway"] = ("u", gateway_int)
                            if self.never_default:
                                logger.info(
                                    f"Added gateway (but never-default is set): "
                                    f"{gateway}"
                                )
                            else:
                                logger.info(f"Added gateway: {gateway}")

                    # Add custom routes if specified
                    if self.custom_routes:
                        routes = []
                        for dest, dest_prefix in self.custom_routes:
                            try:
                                dest_int = ipv4_to_nm_uint32(dest)
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

                    # DNS servers and search domains: the profile's override
                    # and/or what the gateway pushed (issue #15)
                    self._build_dns_config(config, learned_dns)

                    # Emit the generic Config signal first: a new-style
                    # plugin sends it before Ip4Config
                    generic_config = self._build_generic_config(iface, gateway_int)
                    logger.info(f"Config: {generic_config}")
                    self.Config.emit(generic_config)

                    # Emit Ip4Config signal
                    logger.info(f"Ip4Config: {config}")
                    self.Ip4Config.emit(config)

                    # Emit state change: activated
                    self._set_state(NM_VPN_SERVICE_STATE_STARTED)
                    self._tunnel_up = True

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
        # Kept so that a unit override that passes it still starts the service
        help="Accepted for compatibility; the service never quits on its own",
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
        # Cleanup: stop gpclient before the PTY goes away (see Disconnect)
        await plugin._stop_gpclient()

    logger.info("gpclient VPN service stopped")
    return 0


def main():
    """Main entry point"""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
