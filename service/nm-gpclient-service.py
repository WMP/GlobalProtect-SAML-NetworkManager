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
import signal
import socket
import struct
import subprocess
import sys
import termios
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

# How long we wait for the user to answer an interactive secrets request
# (NewSecrets from NetworkManager) before giving up.
SECRETS_REQUEST_TIMEOUT = 300

# How long a prompt candidate must stay unchanged before we act on it.
# gpclient (inquire) renders prompts incrementally; the debounce avoids
# reacting to half-rendered lines.
PROMPT_DEBOUNCE_SECONDS = 0.5

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
        self.hip_enabled = True  # HIP enabled by default
        self._state = NM_VPN_SERVICE_STATE_INIT

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

            # Get gateway (required)
            self.gateway = data_dict.get("gateway", "")
            if not self.gateway:
                raise Exception("No gateway specified")
            logger.info(f"Gateway: {self.gateway}")

            # Get browser (optional, default to edge-wrapper)
            self.browser = data_dict.get(
                "browser", "/usr/libexec/gpclient/edge-wrapper"
            )
            logger.info(f"Browser: {self.browser}")

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
            self.Failure.emit(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)
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

            # Build command - IMPORTANT: include --gateway to avoid TTY prompt
            cmd = [
                "/usr/bin/gpclient",
                "connect",
            ]

            # Add --hip flag if enabled
            if self.hip_enabled:
                cmd.append("--hip")

            # Pass stored username so standard-login portals don't prompt for it
            if self.vpn_username:
                cmd.extend(["--user", self.vpn_username])

            cmd.extend(
                [
                    "--browser",
                    self.browser,
                    "--gateway",
                    self.gateway,
                    self.gateway,
                ]
            )

            logger.info(f"Spawning: {' '.join(cmd)}")

            # Set up environment
            env = os.environ.copy()

            # Set DISPLAY for browser
            display = env.get("DISPLAY", ":0")
            env["DISPLAY"] = display

            # Set SUDO_UID for gpclient to detect real user
            if real_uid > 0:
                env["SUDO_UID"] = str(real_uid)
                env["SUDO_USER"] = real_user

                # Try to find XAUTHORITY
                xauthority = env.get("XAUTHORITY")
                if not xauthority:
                    xauth_path = f"{real_home}/.Xauthority"
                    if os.path.exists(xauth_path):
                        env["XAUTHORITY"] = xauth_path
                        logger.info(f"Set XAUTHORITY={xauth_path}")

                logger.info(f"Environment: SUDO_UID={real_uid}, DISPLAY={display}")

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

                text = strip_ansi(chunk.decode("utf-8", errors="replace"))
                lines = self._output_scanner.feed(text)

                # Once the answered prompt is committed as a full line (its
                # echo flushed), stop suppressing on the old answer - otherwise
                # a later prompt that merely contains it as a substring (e.g.
                # answer "code" vs "? Enter passcode:") is suppressed forever.
                if self._last_answer and any(
                    self._last_answer in ln for ln in lines
                ):
                    self._last_answer = ""

                for line in lines:
                    line = line.strip()
                    # inquire redraws lines on every keystroke; skip repeats
                    if line == last_logged_line:
                        continue
                    last_logged_line = line
                    logger.info(f"gpclient output: {line}")

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
                logger.error(f"gpclient failed with exit code {returncode}")
                self.Failure.emit(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error monitoring gpclient output: {e}")

    def _schedule_prompt_check(self) -> None:
        """(Re)schedule the debounced check for a pending interactive prompt.

        Called after every output chunk: new output cancels the previous
        check, so we only act on a prompt once the output has been stable for
        PROMPT_DEBOUNCE_SECONDS.
        """
        if self._prompt_task and not self._prompt_task.done():
            self._prompt_task.cancel()

        # A prompt is already being answered (possibly waiting minutes for
        # the user via SecretsRequired) - don't detect it again
        if self._answering:
            return

        label = detect_prompt(self._output_scanner.tail, self._last_answer)
        if label is None:
            self._prompt_task = None
            return

        async def _debounced(tail_snapshot: str):
            await asyncio.sleep(PROMPT_DEBOUNCE_SECONDS)
            # Only act if the output is still exactly this prompt
            if self._output_scanner.tail != tail_snapshot:
                return
            await self._handle_prompt(label)

        self._prompt_task = asyncio.create_task(
            _debounced(self._output_scanner.tail)
        )

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
                answer = await self._request_secret_interactive(
                    "otp", label, banner_msg
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
        if self._pty_master is None:
            logger.error("Cannot write answer: PTY is gone")
            return
        self._last_answer = answer
        try:
            # inquire (crossterm raw mode) treats \r as Enter
            os.write(self._pty_master, answer.encode("utf-8") + b"\r")
            logger.info("Answer written to gpclient")
        except OSError as e:
            logger.error(f"Failed to write answer to PTY: {e}")

    def _fail_login(self, reason: str) -> None:
        """Emit a login failure and terminate gpclient"""
        logger.error(f"Login failed: {reason}")
        self._login_failed = True
        self.Failure.emit(NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED)
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
