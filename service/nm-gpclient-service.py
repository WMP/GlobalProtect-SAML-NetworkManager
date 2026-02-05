#!/usr/bin/env python3
"""
NetworkManager VPN Service for GlobalProtect (gpclient)

This service handles VPN connections via gpclient command.
It reads configuration from NetworkManager and spawns gpclient process.

Rewritten using python-sdbus for proper D-Bus interface implementation.
"""

import asyncio
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
        logger.info("NeedSecrets() returning empty string (no secrets needed)")
        # GlobalProtect uses browser-based SAML auth, no additional secrets needed here
        # All authentication is handled by gpclient with gpauth
        return ""

    @dbus_method_async("a{sa{sv}}")
    async def Connect(self, connection: Dict[str, Dict[str, Tuple[str, Any]]]) -> None:
        """Connect to VPN

        Args:
            connection: Dictionary with VPN connection settings
        """
        logger.info("=== Connect() called ===")
        logger.debug(f"Full connection data: {connection}")

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

        # Stop stdout monitoring
        if self.stdout_monitor_task:
            self.stdout_monitor_task.cancel()
            try:
                await self.stdout_monitor_task
            except asyncio.CancelledError:
                pass
            self.stdout_monitor_task = None

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
        """Connect with interactive secrets (not used for gpclient)"""
        logger.info("ConnectInteractive() called, delegating to Connect()")
        await self.Connect(connection)

    @dbus_method_async("a{sa{sv}}")
    async def NewSecrets(
        self, connection: Dict[str, Dict[str, Tuple[str, Any]]]
    ) -> None:
        """New secrets provided (not used for gpclient)"""
        logger.info("NewSecrets() called")

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

            # Spawn gpclient
            self.gpclient_process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            logger.info(f"Started gpclient with PID {self.gpclient_process.pid}")

            # Start monitoring stdout
            self.stdout_monitor_task = asyncio.create_task(
                self._monitor_gpclient_output()
            )

            return True

        except Exception as e:
            logger.error(f"Failed to start gpclient: {e}")
            return False

    async def _monitor_gpclient_output(self) -> None:
        """Monitor gpclient stdout for connection messages"""
        if not self.gpclient_process or not self.gpclient_process.stdout:
            return

        try:
            async for line_bytes in self.gpclient_process.stdout:
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    logger.info(f"gpclient output: {line}")

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

            # Process ended
            returncode = await self.gpclient_process.wait()
            logger.info(f"gpclient process exited with status {returncode}")

            if returncode != 0:
                logger.error(f"gpclient failed with exit code {returncode}")
                self.Failure.emit(NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED)

        except Exception as e:
            logger.error(f"Error monitoring gpclient output: {e}")

    async def _check_tunnel_loop(self) -> None:
        """Periodically check for tunnel interface"""
        tunnel_interfaces = ["gpd0", "tun0", "tun1"]

        try:
            while True:
                for iface in tunnel_interfaces:
                    iface_path = f"/sys/class/net/{iface}"
                    if not os.path.exists(iface_path):
                        continue

                    # Check if interface has an IP address (not just exists)
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

                        # Parse IP address
                        ip_addr = None
                        prefix = 32
                        for line in stdout.decode().split("\n"):
                            if "inet " in line:
                                parts = line.strip().split()
                                if len(parts) >= 2:
                                    addr_with_prefix = parts[1]
                                    if "/" in addr_with_prefix:
                                        ip_addr, prefix_str = addr_with_prefix.split(
                                            "/"
                                        )
                                        prefix = int(prefix_str)
                                    else:
                                        ip_addr = addr_with_prefix
                                break

                        # Only consider interface valid if it has an IP
                        if not ip_addr:
                            logger.debug(
                                f"Interface {iface} exists but has no IP, skipping"
                            )
                            continue

                        logger.info(
                            f"VPN connected - tunnel interface {iface} detected with IP {ip_addr}!"
                        )
                        logger.debug(f"Tunnel IP: {ip_addr}/{prefix}")
                    except Exception as e:
                        logger.warning(f"Failed to get tunnel IP for {iface}: {e}")
                        continue

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

    # Request service name FIRST
    await request_default_bus_name_async(NM_DBUS_SERVICE_GPCLIENT)
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
