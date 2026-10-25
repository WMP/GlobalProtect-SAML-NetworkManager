"""
Tests for the tunnel detection loop and its helpers (issue #55).

Covers `_check_tunnel_loop` (owned-interface skip, DNS state wait, gateway
lookup, the Ip4Config payload), `_cleanup_stale_gpd0`, `_build_dns_config`
and two small gaps in `list_tunnel_candidates` / `tunnel_ifaces_held_by`.

No real `ip`, `pgrep` or `gpclient` is ever run: `asyncio.create_subprocess_exec`
is replaced by a fake, and /sys/class/net, /proc and the DNS state file live in
tmp_path.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import logging

import pytest

DEBUG = logging.DEBUG


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeProc:
    """Just enough of asyncio.subprocess.Process."""

    def __init__(self, returncode=0, stdout=b"", hang=False, on_wait=None):
        self.returncode = returncode
        self._stdout = stdout
        self._hang = hang
        self._on_wait = on_wait
        self._released = asyncio.Event() if hang else None
        self.killed = False

    async def communicate(self):
        return self._stdout, b""

    async def wait(self):
        if self._hang and not self.killed:
            await self._released.wait()
        if self._on_wait:
            self._on_wait()
        return self.returncode

    def kill(self):
        self.killed = True
        if self._released:
            self._released.set()


class _Exec:
    """Replacement for asyncio.create_subprocess_exec.

    `handlers` maps the first argument (the program) to a callable returning a
    process (or raising); every call is recorded in `calls`.
    """

    def __init__(self, handlers):
        self.handlers = handlers
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        handler = self.handlers.get(args[0])
        if handler is None:
            raise AssertionError(f"unexpected command: {args}")
        return handler(args)


def _install_exec(service_module, monkeypatch, handlers):
    fake = _Exec(handlers)
    monkeypatch.setattr(service_module.asyncio, "create_subprocess_exec", fake)
    return fake


def _fast_wait_for(monkeypatch, service_module):
    """Make wait_for time out after 10 ms whatever timeout the code asks for."""
    real = asyncio.wait_for

    async def fast(awaitable, timeout=None):
        return await real(awaitable, 0.01)

    monkeypatch.setattr(service_module.asyncio, "wait_for", fast)


def _record_sleep(monkeypatch, service_module):
    sleeps = []
    real = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        sleeps.append(delay)
        await real(0)

    monkeypatch.setattr(service_module.asyncio, "sleep", fake_sleep)
    return sleeps


def _ip4_config(signals):
    for name, payload in signals:
        if name == "Ip4Config":
            return payload
    return None


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


class TestListTunnelCandidates:
    def test_unreadable_net_dir_gives_empty_list(
        self, service_module, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.setattr(service_module, "NET_SYSFS_PATH", str(tmp_path / "gone"))
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            assert service_module.list_tunnel_candidates() == []
        assert "Cannot list" in caplog.text

    def test_lists_tunnels_in_order(self, service_module, monkeypatch, tmp_path):
        for name in ("tun10", "eth0", "tun2", "gpd0"):
            (tmp_path / name).mkdir()
        monkeypatch.setattr(service_module, "NET_SYSFS_PATH", str(tmp_path))
        assert service_module.list_tunnel_candidates() == ["gpd0", "tun2", "tun10"]


class TestTunnelIfacesHeldBy:
    def test_pid_without_fdinfo_is_skipped(
        self, service_module, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(service_module, "PROC_PATH", str(tmp_path))
        assert service_module.tunnel_ifaces_held_by([4242]) == set()

    def test_pid_with_tun_fd_is_found(self, service_module, monkeypatch, tmp_path):
        fdinfo = tmp_path / "4242" / "fdinfo"
        fdinfo.mkdir(parents=True)
        (fdinfo / "3").write_text("pos:\t0\niff:\ttun3\n")
        monkeypatch.setattr(service_module, "PROC_PATH", str(tmp_path))
        # the vanished pid does not hide the live one
        assert service_module.tunnel_ifaces_held_by([1, 4242]) == {"tun3"}


# ---------------------------------------------------------------------------
# _build_dns_config
# ---------------------------------------------------------------------------


def _nm(service_module, text):
    return service_module.ipv4_to_nm_uint32(text)


class TestBuildDnsConfig:
    def _plugin(self, service_module, dns=(), domains=()):
        plugin = service_module.GpclientVPNPlugin()
        plugin.dns_servers = list(dns)
        plugin.dns_domains = list(domains)
        return plugin

    def test_profile_override_replaces_gateway_servers(
        self, service_module, caplog
    ):
        plugin = self._plugin(service_module, dns=["10.9.9.9"])
        config = {}
        with caplog.at_level("INFO", logger=service_module.logger.name):
            plugin._build_dns_config(config, (["10.1.1.1"], [], []))
        assert config["dns"] == ("au", [_nm(service_module, "10.9.9.9")])
        assert "overridden by the profile" in caplog.text

    def test_override_without_gateway_servers_logs_nothing_about_override(
        self, service_module, caplog
    ):
        plugin = self._plugin(service_module, dns=["10.9.9.9"])
        config = {}
        with caplog.at_level("INFO", logger=service_module.logger.name):
            plugin._build_dns_config(config, None)
        assert config["dns"] == ("au", [_nm(service_module, "10.9.9.9")])
        assert "overridden by the profile" not in caplog.text

    def test_ipv6_only_override_leaves_no_dns_servers(self, service_module, caplog):
        plugin = self._plugin(service_module, dns=["2001:db8::1"])
        config = {}
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            plugin._build_dns_config(config, (["10.1.1.1"], [], []))
        assert "dns" not in config
        assert "IPv6 only" in caplog.text

    def test_mixed_override_is_not_ipv6_only(self, service_module, caplog):
        plugin = self._plugin(service_module, dns=["2001:db8::1", "10.9.9.9"])
        config = {}
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            plugin._build_dns_config(config, None)
        assert config["dns"] == ("au", [_nm(service_module, "10.9.9.9")])
        assert "IPv6 only" not in caplog.text

    @pytest.mark.parametrize(
        "learned, expected_text",
        [
            ((["10.1.1.1"], [], []), "using the ones learned from the gateway"),
            (None, "no DNS servers from the gateway are available"),
        ],
    )
    def test_invalid_override_falls_back_to_gateway(
        self, service_module, caplog, learned, expected_text
    ):
        plugin = self._plugin(service_module, dns=["not-an-ip"])
        config = {}
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            plugin._build_dns_config(config, learned)
        assert expected_text in caplog.text
        if learned:
            assert config["dns"] == ("au", [_nm(service_module, "10.1.1.1")])
        else:
            assert "dns" not in config

    def test_no_override_uses_gateway_without_warning(self, service_module, caplog):
        plugin = self._plugin(service_module)
        config = {}
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            plugin._build_dns_config(config, (["10.1.1.1"], [], []))
        assert config["dns"] == ("au", [_nm(service_module, "10.1.1.1")])
        assert "None of the DNS servers" not in caplog.text

    def test_domains_are_merged_and_deduplicated(self, service_module):
        plugin = self._plugin(service_module, domains=["b.example", "a.example"])
        config = {}
        plugin._build_dns_config(config, ([], ["a.example", "c.example"], []))
        assert config["domains"] == ("as", ["a.example", "c.example", "b.example"])

    def test_nothing_known_adds_nothing(self, service_module):
        plugin = self._plugin(service_module)
        config = {}
        plugin._build_dns_config(config, None)
        assert config == {}

    def test_gateway_ipv6_servers_are_only_logged(self, service_module, caplog):
        plugin = self._plugin(service_module)
        config = {}
        with caplog.at_level("INFO", logger=service_module.logger.name):
            plugin._build_dns_config(config, ([], [], ["2001:db8::53"]))
        assert config == {}
        assert "Gateway pushed IPv6 DNS servers" in caplog.text


# ---------------------------------------------------------------------------
# _cleanup_stale_gpd0
# ---------------------------------------------------------------------------


class TestCleanupStaleGpd0:
    @pytest.fixture
    def net(self, service_module, monkeypatch, tmp_path):
        net = tmp_path / "net"
        net.mkdir()
        monkeypatch.setattr(service_module, "NET_SYSFS_PATH", str(net))
        return net

    def _run(self, service_module):
        asyncio.run(service_module.GpclientVPNPlugin()._cleanup_stale_gpd0())

    def test_no_gpd0_runs_nothing(self, service_module, monkeypatch, net):
        fake = _install_exec(service_module, monkeypatch, {})
        self._run(service_module)
        assert fake.calls == []

    def test_running_gpclient_keeps_gpd0(
        self, service_module, monkeypatch, net, caplog
    ):
        (net / "gpd0").mkdir()
        fake = _install_exec(
            service_module, monkeypatch, {"pgrep": lambda a: _FakeProc(0)}
        )
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            self._run(service_module)
        assert [c[0] for c in fake.calls] == ["pgrep"]
        assert "not cleaning up" in caplog.text

    def test_pgrep_failure_aborts_cleanup(
        self, service_module, monkeypatch, net, caplog
    ):
        (net / "gpd0").mkdir()

        def boom(_args):
            raise OSError("no pgrep")

        fake = _install_exec(service_module, monkeypatch, {"pgrep": boom})
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            self._run(service_module)
        assert [c[0] for c in fake.calls] == ["pgrep"]
        assert "Could not check for a running gpclient" in caplog.text
        assert (net / "gpd0").exists()

    def test_running_gpservice_keeps_gpd0(
        self, service_module, monkeypatch, net, caplog
    ):
        # The GUI client's gpservice owns gpd0; pgrep -x gpclient does not see it
        (net / "gpd0").mkdir()
        fake = _install_exec(
            service_module,
            monkeypatch,
            {"pgrep": lambda a: _FakeProc(0 if a[2] == "gpservice" else 1)},
        )
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            self._run(service_module)
        assert fake.calls == [("pgrep", "-x", "gpclient"), ("pgrep", "-x", "gpservice")]
        assert "a gpservice process is running" in caplog.text
        assert (net / "gpd0").exists()

    def test_gpservice_check_failure_keeps_gpd0(
        self, service_module, monkeypatch, net, caplog
    ):
        (net / "gpd0").mkdir()

        def pgrep(args):
            if args[2] == "gpservice":
                raise OSError("no pgrep")
            return _FakeProc(1)

        fake = _install_exec(service_module, monkeypatch, {"pgrep": pgrep})
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            self._run(service_module)
        assert [c[0] for c in fake.calls] == ["pgrep", "pgrep"]
        assert "Could not check for a running gpservice" in caplog.text

    def test_stale_gpd0_is_deleted_without_gpclient_disconnect(
        self, service_module, monkeypatch, net, caplog
    ):
        gpd0 = net / "gpd0"
        gpd0.mkdir()
        fake = _install_exec(
            service_module,
            monkeypatch,
            {
                "pgrep": lambda a: _FakeProc(1),
                "ip": lambda a: _FakeProc(0, on_wait=gpd0.rmdir),
            },
        )
        with caplog.at_level("INFO", logger=service_module.logger.name):
            self._run(service_module)
        assert fake.calls == [
            ("pgrep", "-x", "gpclient"),
            ("pgrep", "-x", "gpservice"),
            ("ip", "link", "del", "gpd0"),
        ]
        assert "Stale gpd0 interface removed" in caplog.text

    def test_ip_link_del_removes_gpd0(
        self, service_module, monkeypatch, net, caplog
    ):
        gpd0 = net / "gpd0"
        gpd0.mkdir()
        fake = _install_exec(
            service_module,
            monkeypatch,
            {
                "pgrep": lambda a: _FakeProc(1),
                "ip": lambda a: _FakeProc(0, on_wait=gpd0.rmdir),
            },
        )
        with caplog.at_level("INFO", logger=service_module.logger.name):
            self._run(service_module)
        assert ("ip", "link", "del", "gpd0") in fake.calls
        assert "Stale gpd0 interface removed" in caplog.text

    def test_ip_link_del_timeout_kills_it_and_gpd0_stays(
        self, service_module, monkeypatch, net, caplog
    ):
        (net / "gpd0").mkdir()
        hung = _FakeProc(0, hang=True)
        _install_exec(
            service_module,
            monkeypatch,
            {
                "pgrep": lambda a: _FakeProc(1),
                "ip": lambda a: hung,
            },
        )
        _fast_wait_for(monkeypatch, service_module)
        with caplog.at_level("INFO", logger=service_module.logger.name):
            self._run(service_module)
        assert hung.killed
        assert "'ip link del gpd0' timed out" in caplog.text
        assert "Stale gpd0 interface removed" not in caplog.text

    def test_ip_link_del_error_is_logged_and_gpd0_stays(
        self, service_module, monkeypatch, net, caplog
    ):
        (net / "gpd0").mkdir()

        def boom(_args):
            raise OSError("no ip")

        _install_exec(
            service_module,
            monkeypatch,
            {
                "pgrep": lambda a: _FakeProc(1),
                "ip": boom,
            },
        )
        with caplog.at_level("INFO", logger=service_module.logger.name):
            self._run(service_module)
        assert "Failed to delete stale gpd0" in caplog.text
        assert "Stale gpd0 interface removed" not in caplog.text


# ---------------------------------------------------------------------------
# _check_tunnel_loop
# ---------------------------------------------------------------------------


class _Loop:
    """A plugin wired for the loop, plus the things the tests look at."""

    def __init__(self, service_module, monkeypatch, ifaces, route_out=b"", owned=()):
        self.module = service_module
        self.persisted = []
        self.plugin = service_module.GpclientVPNPlugin()
        plugin = self.plugin
        plugin._preexisting_ifaces = {}
        self.candidate_rounds = [list(ifaces)]

        def candidates():
            rounds = self.candidate_rounds
            return rounds.pop(0) if len(rounds) > 1 else list(rounds[0])

        monkeypatch.setattr(service_module, "list_tunnel_candidates", candidates)

        self.ips = {name: ("10.8.0.5", 24) for name in ifaces}

        async def get_ipv4(iface):
            return self.ips.get(iface, (None, 0))

        monkeypatch.setattr(plugin, "_get_iface_ipv4", get_ipv4)
        monkeypatch.setattr(plugin, "_tunnel_ifaces_owned_by_gpclient", lambda: set(owned))

        async def persist_gateways():
            self.persisted.append("gateways")

        async def persist_openssl():
            self.persisted.append("openssl")

        monkeypatch.setattr(plugin, "_persist_gateway_list", persist_gateways)
        monkeypatch.setattr(plugin, "_persist_fix_openssl", persist_openssl)

        self.route_out = route_out
        self.exec = _install_exec(
            service_module, monkeypatch, {"ip": self._ip}
        )
        self.sleeps = _record_sleep(monkeypatch, service_module)

    def _ip(self, args):
        if isinstance(self.route_out, Exception):
            raise self.route_out
        return _FakeProc(0, stdout=self.route_out)

    def run(self, timeout=2):
        async def scenario():
            task = asyncio.create_task(self.plugin._check_tunnel_loop())
            try:
                await asyncio.wait_for(task, timeout=timeout)
                return True
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return False

        return asyncio.run(scenario())


@pytest.fixture
def make_loop(service_module, monkeypatch):
    def factory(ifaces=("tun0",), **kwargs):
        return _Loop(service_module, monkeypatch, list(ifaces), **kwargs)

    return factory


class TestCheckTunnelLoopPayload:
    def test_accepts_tunnel_and_reports_everything(
        self, make_loop, service_module, dbus_signals
    ):
        loop = make_loop(route_out=b"10.0.0.0/8 via 10.8.0.1\n")
        assert loop.run() is True

        config = _ip4_config(dbus_signals)
        nm = service_module.ipv4_to_nm_uint32
        assert config["tundev"] == ("s", "tun0")
        assert config["address"] == ("u", nm("10.8.0.5"))
        assert config["prefix"] == ("u", 24)
        assert config["gateway"] == ("u", nm("10.8.0.1"))
        assert "routes" not in config
        assert loop.exec.calls == [("ip", "route", "show", "dev", "tun0")]

        names = [name for name, _ in dbus_signals]
        assert names == ["Config", "Ip4Config", "StateChanged"]
        assert dbus_signals[2][1] == service_module.NM_VPN_SERVICE_STATE_STARTED
        assert loop.plugin._tunnel_up is True
        assert loop.persisted == ["gateways", "openssl"]

    def test_gateway_taken_from_first_via_line(
        self, make_loop, service_module, dbus_signals
    ):
        out = (
            b"\n"
            b"10.8.0.0/24 proto kernel scope link src 10.8.0.5\n"
            b"10.1.0.0/16 via 10.8.0.9 metric 50\n"
            b"10.2.0.0/16 via 10.8.0.10\n"
        )
        loop = make_loop(route_out=out)
        assert loop.run() is True
        assert _ip4_config(dbus_signals)["gateway"] == (
            "u",
            service_module.ipv4_to_nm_uint32("10.8.0.9"),
        )

    @pytest.mark.parametrize(
        "route_out",
        [
            b"",
            b"10.8.0.0/24 proto kernel scope link src 10.8.0.5\n",
            b"10.1.0.0/16 via\n",  # "via" with nothing behind it
            OSError("ip is not there"),
        ],
    )
    def test_point_to_point_falls_back_to_tunnel_ip(
        self, make_loop, service_module, dbus_signals, route_out
    ):
        loop = make_loop(route_out=route_out)
        assert loop.run() is True
        assert _ip4_config(dbus_signals)["gateway"] == (
            "u",
            service_module.ipv4_to_nm_uint32("10.8.0.5"),
        )

    def test_invalid_gateway_is_left_out(
        self, make_loop, dbus_signals, caplog, service_module
    ):
        loop = make_loop(route_out=b"10.1.0.0/16 via not-an-ip\n")
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            assert loop.run() is True
        config = _ip4_config(dbus_signals)
        assert "gateway" not in config
        assert "Not adding gateway not-an-ip" in caplog.text
        # the tunnel is still reported
        assert config["tundev"] == ("s", "tun0")
        assert loop.plugin._tunnel_up is True

    def test_invalid_address_is_left_out(
        self, make_loop, dbus_signals, caplog, service_module
    ):
        loop = make_loop()
        loop.ips["tun0"] = ("not-an-ip", 24)
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            assert loop.run() is True
        config = _ip4_config(dbus_signals)
        assert "address" not in config
        assert "prefix" not in config
        assert "Not adding address not-an-ip" in caplog.text
        # the same text is not a usable gateway either
        assert "gateway" not in config

    @pytest.mark.parametrize("never_default", [True, False])
    def test_never_default_only_changes_the_log(
        self, make_loop, dbus_signals, caplog, service_module, never_default
    ):
        loop = make_loop(route_out=b"10.1.0.0/16 via 10.8.0.1\n")
        loop.plugin.never_default = never_default
        with caplog.at_level("INFO", logger=service_module.logger.name):
            assert loop.run() is True
        assert ("gateway" in _ip4_config(dbus_signals)) is True
        assert ("never-default is set" in caplog.text) is never_default
        assert ("Added gateway: 10.8.0.1" in caplog.text) is not never_default

    def test_custom_routes_skip_invalid_entries(
        self, make_loop, dbus_signals, caplog, service_module
    ):
        loop = make_loop()
        loop.plugin.custom_routes = [("10.50.0.0", 16), ("bogus", 8), ("192.168.7.0", 24)]
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            assert loop.run() is True
        nm = service_module.ipv4_to_nm_uint32
        assert _ip4_config(dbus_signals)["routes"] == (
            "a(uuuu)",
            [(nm("10.50.0.0"), 16, 0, 0), (nm("192.168.7.0"), 24, 0, 0)],
        )
        assert "Failed to add route bogus/8" in caplog.text

    def test_only_invalid_custom_routes_leave_routes_out(
        self, make_loop, dbus_signals
    ):
        loop = make_loop()
        loop.plugin.custom_routes = [("bogus", 8)]
        assert loop.run() is True
        assert "routes" not in _ip4_config(dbus_signals)

    def test_dns_config_is_built(self, make_loop, dbus_signals, service_module):
        calls = []
        loop = make_loop()
        loop.plugin.dns_servers = ["10.9.9.9"]
        real = loop.plugin._build_dns_config

        def spy(config, learned):
            calls.append(learned)
            real(config, learned)

        loop.plugin._build_dns_config = spy
        assert loop.run() is True
        assert calls == [None]
        assert _ip4_config(dbus_signals)["dns"] == (
            "au",
            [service_module.ipv4_to_nm_uint32("10.9.9.9")],
        )


def _config(signals):
    for name, payload in signals:
        if name == "Config":
            return payload
    return None


class TestCheckTunnelLoopGenericConfig:
    """The Config signal (new-style plugin), sent right before Ip4Config"""

    def test_config_is_sent_once_before_ip4config(
        self, make_loop, service_module, dbus_signals
    ):
        loop = make_loop(route_out=b"10.0.0.0/8 via 10.8.0.1\n")
        assert loop.run() is True

        names = [name for name, _ in dbus_signals]
        assert names.count("Config") == 1
        assert names.index("Config") + 1 == names.index("Ip4Config")
        config = _config(dbus_signals)
        assert config == {
            "tundev": ("s", "tun0"),
            "gateway": _ip4_config(dbus_signals)["gateway"],
            "has-ip4": ("b", True),
            "has-ip6": ("b", False),
        }
        assert config["gateway"] == ("u", service_module.ipv4_to_nm_uint32("10.8.0.1"))

    def test_mtu_is_sent_when_the_profile_sets_it(self, make_loop, dbus_signals):
        loop = make_loop()
        loop.plugin.mtu = 1400
        assert loop.run() is True
        assert _config(dbus_signals)["mtu"] == ("u", 1400)

    @pytest.mark.parametrize("mtu", [None, 0])
    def test_no_mtu_when_the_profile_leaves_it_unset(
        self, make_loop, dbus_signals, mtu
    ):
        loop = make_loop()
        loop.plugin.mtu = mtu
        assert loop.run() is True
        assert "mtu" not in _config(dbus_signals)

    @pytest.mark.parametrize("text", ["abc", "9999", "-5", "12x", "1.5"])
    def test_no_mtu_when_the_profile_value_is_invalid(
        self, service_module, text
    ):
        # parse_mtu rejects it, so the service stores None
        assert service_module.parse_mtu(text) is None
        plugin = service_module.GpclientVPNPlugin()
        plugin.mtu = service_module.parse_mtu(text) or None
        assert "mtu" not in plugin._build_generic_config("tun0", 1)

    def test_gateway_that_cannot_be_converted_is_left_out(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(route_out=b"10.1.0.0/16 via not-an-ip\n")
        assert loop.run() is True
        assert "gateway" not in _ip4_config(dbus_signals)
        config = _config(dbus_signals)
        assert "gateway" not in config
        assert config["tundev"] == ("s", "tun0")

    def test_zero_gateway_is_kept(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        assert plugin._build_generic_config("tun0", 0)["gateway"] == ("u", 0)
        assert "gateway" not in plugin._build_generic_config("tun0", None)

    @pytest.mark.parametrize("ifaces", [[], ["tun0"]])
    def test_no_config_while_the_tunnel_is_not_up(
        self, make_loop, dbus_signals, ifaces
    ):
        loop = make_loop(ifaces=ifaces)
        if ifaces:
            loop.ips["tun0"] = (None, 0)
        assert loop.run(timeout=0.2) is False
        assert _config(dbus_signals) is None
        assert dbus_signals == []


class TestCheckTunnelLoopSelection:
    def test_no_candidates_keeps_polling_every_half_second(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=[])
        assert loop.run(timeout=0.2) is False
        assert loop.sleeps and set(loop.sleeps) == {0.5}
        assert dbus_signals == []
        assert loop.plugin._tunnel_up is False
        assert loop.persisted == []

    def test_second_round_finds_tunnel_after_sleep(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=["tun0"])
        loop.candidate_rounds = [[], ["tun0"]]
        assert loop.run() is True
        assert loop.sleeps == [0.5]
        assert _ip4_config(dbus_signals) is not None

    def test_interface_without_ip_is_skipped(self, make_loop, dbus_signals):
        loop = make_loop(ifaces=["tun0"])
        loop.ips["tun0"] = (None, 0)
        assert loop.run(timeout=0.2) is False
        assert dbus_signals == []

    def test_unchanged_preexisting_interface_is_skipped(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=["tun0"])
        loop.plugin._preexisting_ifaces = {"tun0": "10.8.0.5"}
        assert loop.run(timeout=0.2) is False
        assert dbus_signals == []

    def test_preexisting_interface_with_new_ip_is_accepted(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=["tun0"])
        loop.plugin._preexisting_ifaces = {"tun0": "10.7.7.7"}
        assert loop.run() is True
        assert _ip4_config(dbus_signals)["tundev"] == ("s", "tun0")

    def test_interface_held_by_another_process_is_skipped(
        self, make_loop, dbus_signals, caplog, service_module
    ):
        loop = make_loop(ifaces=["tun0", "tun1"], owned={"tun1"})
        with caplog.at_level("DEBUG", logger=service_module.logger.name):
            assert loop.run() is True
        assert _ip4_config(dbus_signals)["tundev"] == ("s", "tun1")
        assert "not held by gpclient (it holds ['tun1'])" in caplog.text

    def test_only_foreign_interfaces_are_never_accepted(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=["tun0"], owned={"tun5"})
        assert loop.run(timeout=0.2) is False
        assert dbus_signals == []

    def test_unknown_ownership_accepts_first_new_interface(
        self, make_loop, dbus_signals
    ):
        loop = make_loop(ifaces=["tun0", "tun1"], owned=())
        assert loop.run() is True
        assert _ip4_config(dbus_signals)["tundev"] == ("s", "tun0")

    def test_ownership_is_looked_up_once_per_round(
        self, make_loop, service_module, monkeypatch
    ):
        loop = make_loop(ifaces=["tun0", "tun1", "tun2"], owned={"tun2"})
        lookups = []

        def owned():
            lookups.append(1)
            return {"tun2"}

        monkeypatch.setattr(loop.plugin, "_tunnel_ifaces_owned_by_gpclient", owned)
        assert loop.run() is True
        assert lookups == [1]

    def test_cancellation_propagates(self, make_loop, caplog, service_module):
        loop = make_loop(ifaces=[])

        async def scenario():
            task = asyncio.create_task(loop.plugin._check_tunnel_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        with caplog.at_level("DEBUG", logger=service_module.logger.name):
            asyncio.run(scenario())
        assert "Tunnel monitoring cancelled" in caplog.text


class TestCheckTunnelLoopDnsWait:
    def _learned(self, service_module, tmp_path):
        """Write a DNS state file for tun0 (what the vpnc hook records)."""
        service_module_state = service_module.DNS_STATE_FILE
        with open(service_module_state, "w", encoding="utf-8") as f:
            f.write("TUNDEV=tun0\nINTERNAL_IP4_DNS=10.1.1.1\n")

    def test_waits_for_the_state_file_then_gives_up(
        self, make_loop, service_module, monkeypatch, dbus_signals, caplog
    ):
        monkeypatch.setattr(service_module, "DNS_STATE_WAIT_ROUNDS", 2)
        loop = make_loop()
        with caplog.at_level("DEBUG", logger=service_module.logger.name):
            assert loop.run() is True
        # two rounds of waiting, each followed by the 0.5 s sleep
        assert loop.sleeps == [0.5, 0.5]
        assert loop.plugin._dns_state_rounds == 2
        assert "waiting (1/2)" in caplog.text
        assert "waiting (2/2)" in caplog.text
        assert "No DNS state from the vpnc hook" in caplog.text
        assert "dns" not in _ip4_config(dbus_signals)

    def test_state_file_appearing_during_the_wait_is_used(
        self, make_loop, service_module, monkeypatch, dbus_signals, tmp_path
    ):
        monkeypatch.setattr(service_module, "DNS_STATE_WAIT_ROUNDS", 5)
        loop = make_loop()
        real_read = loop.plugin._read_learned_dns
        seen = []

        def read(iface):
            if seen:
                self._learned(service_module, tmp_path)
            seen.append(iface)
            return real_read(iface)

        loop.plugin._read_learned_dns = read
        assert loop.run() is True
        assert loop.sleeps == [0.5]
        assert loop.plugin._dns_state_rounds == 1
        assert _ip4_config(dbus_signals)["dns"] == (
            "au",
            [service_module.ipv4_to_nm_uint32("10.1.1.1")],
        )

    def test_present_state_file_means_no_wait(
        self, make_loop, service_module, monkeypatch, dbus_signals, tmp_path, caplog
    ):
        monkeypatch.setattr(service_module, "DNS_STATE_WAIT_ROUNDS", 5)
        self._learned(service_module, tmp_path)
        loop = make_loop()
        with caplog.at_level("WARNING", logger=service_module.logger.name):
            assert loop.run() is True
        assert loop.sleeps == []
        assert loop.plugin._dns_state_rounds == 0
        assert "No DNS state from the vpnc hook" not in caplog.text

    def test_state_file_for_another_tunnel_is_waited_for(
        self, make_loop, service_module, monkeypatch, dbus_signals, tmp_path
    ):
        monkeypatch.setattr(service_module, "DNS_STATE_WAIT_ROUNDS", 1)
        with open(service_module.DNS_STATE_FILE, "w", encoding="utf-8") as f:
            f.write("TUNDEV=tun9\nINTERNAL_IP4_DNS=10.1.1.1\n")
        loop = make_loop()
        assert loop.run() is True
        assert loop.sleeps == [0.5]
        assert "dns" not in _ip4_config(dbus_signals)


# ---------------------------------------------------------------------------
# _get_iface_ipv4 and the remaining branches of the /proc and /sys helpers
# ---------------------------------------------------------------------------


IP_ADDR_TUN0 = b"""\
5: tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1400 qdisc fq_codel
    inet 10.8.0.5/24 scope global tun0
       valid_lft forever preferred_lft forever
    inet 10.8.0.6/32 scope global secondary tun0
"""


class TestGetIfaceIpv4:
    def _run(self, service_module, monkeypatch, handler):
        fake = _install_exec(service_module, monkeypatch, {"ip": handler})
        plugin = service_module.GpclientVPNPlugin()
        return asyncio.run(plugin._get_iface_ipv4("tun0")), fake

    def test_first_address_and_prefix(self, service_module, monkeypatch):
        result, fake = self._run(
            service_module, monkeypatch, lambda a: _FakeProc(0, stdout=IP_ADDR_TUN0)
        )
        assert result == ("10.8.0.5", 24)
        assert fake.calls == [("ip", "-4", "addr", "show", "tun0")]

    @pytest.mark.parametrize(
        "stdout, expected",
        [
            (b"    inet 10.8.0.5 scope global tun0\n", ("10.8.0.5", 32)),
            (b"    inet 10.8.0.5/xx scope global tun0\n", ("10.8.0.5/xx", 32)),
        ],
    )
    def test_address_without_usable_prefix_gets_32(
        self, service_module, monkeypatch, stdout, expected
    ):
        result, _ = self._run(
            service_module, monkeypatch, lambda a: _FakeProc(0, stdout=stdout)
        )
        assert result == expected

    @pytest.mark.parametrize(
        "stdout",
        [
            b"",
            b"Device \"tun0\" does not exist.\n",
            b"5: tun0: <POINTOPOINT> mtu 1400\n    inet6 fe80::1/64 scope link\n",
            b"    inet\n",
            b"\xff\xfe garbage\n",
        ],
    )
    def test_no_ipv4_address_gives_none(self, service_module, monkeypatch, stdout):
        result, _ = self._run(
            service_module, monkeypatch, lambda a: _FakeProc(0, stdout=stdout)
        )
        assert result == (None, 32)

    def test_ip_that_cannot_run_gives_none(self, service_module, monkeypatch, caplog):
        def boom(_args):
            raise FileNotFoundError("ip")

        with caplog.at_level("WARNING", logger=service_module.logger.name):
            result, _ = self._run(service_module, monkeypatch, boom)
        assert result == (None, 32)
        assert "Failed to get tunnel IP for tun0" in caplog.text


class TestHelperBranches:
    @pytest.mark.parametrize("name", ["eth0", "wg0", "tunnel", ""])
    def test_non_tunnel_names_sort_last(self, service_module, name):
        order = service_module.tunnel_candidate_order
        assert order(name) > order("tun99")
        assert order(name) > order("gpd0")

    def test_unreadable_fdinfo_entry_is_skipped(
        self, service_module, monkeypatch, tmp_path
    ):
        fdinfo = tmp_path / "4242" / "fdinfo"
        fdinfo.mkdir(parents=True)
        (fdinfo / "3").mkdir()  # open() fails with IsADirectoryError
        (fdinfo / "4").write_text("pos:\t0\niff:\ttun4\n")
        monkeypatch.setattr(service_module, "PROC_PATH", str(tmp_path))
        assert service_module.tunnel_ifaces_held_by([4242]) == {"tun4"}

    def test_process_tree_skips_unreadable_stat(
        self, service_module, monkeypatch, tmp_path
    ):
        (tmp_path / "100").mkdir()
        (tmp_path / "100" / "stat").write_text("100 (gpclient) S 1 0\n")
        (tmp_path / "101").mkdir()
        (tmp_path / "101" / "stat").mkdir()  # open() fails
        monkeypatch.setattr(service_module, "PROC_PATH", str(tmp_path))
        assert service_module.process_tree(100) == [100]

    def test_process_tree_survives_a_parent_cycle(
        self, service_module, monkeypatch, tmp_path
    ):
        # Two entries naming each other as parent (a pid reused while /proc
        # was read): each pid is listed once and the walk ends
        for pid, ppid in ((100, 101), (101, 100)):
            (tmp_path / str(pid)).mkdir()
            (tmp_path / str(pid) / "stat").write_text(f"{pid} (x) S {ppid} 0\n")
        monkeypatch.setattr(service_module, "PROC_PATH", str(tmp_path))
        assert sorted(service_module.process_tree(100)) == [100, 101]
