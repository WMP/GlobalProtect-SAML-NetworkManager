"""
Tests for the functions that read output from gpclient or run it, and that the
other test files do not cover on their own (upstream/interfaces.json):
_start_gpclient, Disconnect, _cleanup_stale_gpd0, _close_pty, ScreenBuffer._write
and tokens_text.

Every test fakes the external tools: a stand-in gpclient, and a fake
asyncio.create_subprocess_exec that records the command and never starts a
process. No test uses the real gpclient, pgrep, pkill or ip.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import json
import os
import subprocess
import sys
import threading

import pytest

# ---------------------------------------------------------------------------
# GpclientVPNPlugin._start_gpclient
# ---------------------------------------------------------------------------

FAKE_GPCLIENT = f"""#!{sys.executable}
import json, os, sys
with open("__DUMP__", "w") as out:
    json.dump({{"argv": sys.argv[1:], "env": dict(os.environ)}}, out)
"""


def open_fds():
    return set(os.listdir("/proc/self/fd"))


@pytest.fixture
def start_env(service_module, monkeypatch, tmp_path):
    """Plugin that starts a fake gpclient. Nothing touches the real user, the
    real session or the real processes."""
    dump = tmp_path / "dump.json"
    binary = tmp_path / "gpclient"
    binary.write_text(FAKE_GPCLIENT.replace("__DUMP__", str(dump)))
    binary.chmod(0o755)
    monkeypatch.setattr(service_module, "GPCLIENT_BINARY", str(binary))
    for name in ("SUDO_UID", "SUDO_USER", "GP_BROWSER", "DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    # The pkill of old gpauth processes must not run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    plugin = service_module.GpclientVPNPlugin()
    plugin.gateway = "portal.example.com"
    plugin.browser = "default"
    plugin._get_real_user = lambda: (4321, "alice", "/home/alice")
    plugin._get_session_env = lambda uid, home: {
        "DISPLAY": ":7",
        "XDG_RUNTIME_DIR": "/run/user/4321",
    }

    async def no_monitor():
        pass

    plugin._monitor_gpclient_output = no_monitor  # the PTY stays open

    class Env:
        pass

    result = Env()
    result.plugin = plugin
    result.dump = dump
    result.tmp_path = tmp_path

    def start():
        async def run():
            started = await plugin._start_gpclient()
            if started:
                await plugin.stdout_monitor_task
                await asyncio.wait_for(plugin.gpclient_process.wait(), 10)
            return started

        try:
            return asyncio.run(run())
        finally:
            plugin._close_pty()

    result.start = start
    return result


def test_start_gpclient_passes_flags_and_environment(start_env):
    plugin = start_env.plugin
    plugin.fix_openssl = True
    plugin.as_gateway = True
    plugin.vpn_username = "alice"
    plugin.browser_target = "/usr/bin/firefox"
    pty_seen = []
    real_close = plugin._close_pty
    plugin._close_pty = lambda: (pty_seen.append(plugin._pty_master), real_close())

    assert start_env.start() is True  # _start_gpclient

    dumped = json.loads(start_env.dump.read_text())
    assert dumped["argv"] == [
        "--fix-openssl",
        "connect",
        "--hip",
        "--as-gateway",
        "--user=alice",
        "--browser=default",
        "--",
        "portal.example.com",
    ]
    env = dumped["env"]
    assert env["SUDO_UID"] == "4321"
    assert env["SUDO_USER"] == "alice"
    assert env["DISPLAY"] == ":7"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/4321"
    assert env["GP_BROWSER"] == "/usr/bin/firefox"
    assert pty_seen and isinstance(pty_seen[0], int)  # the PTY master was set


def test_start_gpclient_leaves_out_optional_flags_by_default(start_env):
    assert start_env.start() is True  # _start_gpclient

    dumped = json.loads(start_env.dump.read_text())
    for flag in ("--fix-openssl", "--as-gateway", "--user"):
        assert flag not in dumped["argv"]
    assert "GP_BROWSER" not in dumped["env"]


def test_start_gpclient_passes_the_mtu_before_the_browser(start_env):
    start_env.plugin.mtu = 1300

    assert start_env.start() is True  # _start_gpclient

    argv = json.loads(start_env.dump.read_text())["argv"]
    assert argv[argv.index("--mtu") + 1] == "1300"
    browser = next(a for a in argv if a.startswith("--browser="))
    assert argv.index("connect") < argv.index("--mtu") < argv.index(browser)


@pytest.mark.parametrize("mtu", [None, 0])
def test_start_gpclient_leaves_out_the_mtu_when_automatic(start_env, mtu):
    start_env.plugin.mtu = mtu

    assert start_env.start() is True  # _start_gpclient

    assert "--mtu" not in json.loads(start_env.dump.read_text())["argv"]


def test_start_gpclient_without_a_real_user_sets_no_session_env(start_env):
    # root with no desktop user: no SUDO_UID and no session variables
    start_env.plugin._get_real_user = lambda: (0, "root", "/root")

    assert start_env.start() is True  # _start_gpclient

    env = json.loads(start_env.dump.read_text())["env"]
    for name in ("SUDO_UID", "SUDO_USER", "DISPLAY"):
        assert name not in env


def test_start_gpclient_looks_up_the_user_off_the_event_loop(start_env):
    # _get_real_user runs blocking loginctl calls: they must not stall the loop
    threads = []

    def real_user():
        threads.append(threading.current_thread())
        return (4321, "alice", "/home/alice")

    start_env.plugin._get_real_user = real_user

    assert start_env.start() is True  # _start_gpclient

    assert len(threads) == 1
    assert threads[0] is not threading.main_thread()


def test_start_gpclient_fails_cleanly_when_the_binary_is_missing(
    service_module, start_env, monkeypatch
):
    monkeypatch.setattr(service_module, "GPCLIENT_BINARY", "/no/such/gpclient")
    before = open_fds()

    started = start_env.start()  # _start_gpclient

    assert started is False
    assert start_env.plugin.gpclient_process is None
    assert start_env.plugin._pty_master is None
    assert open_fds() == before  # no PTY fd is left open


# ---------------------------------------------------------------------------
# Fake create_subprocess_exec for Disconnect and _cleanup_stale_gpd0
# ---------------------------------------------------------------------------

SCALE = 20  # one second of the service is 1/20 s here


class FakeProc:
    """A process that exits with `code`, or never (code None)"""

    pid = 4242

    def __init__(self, code):
        self.code = code
        self.killed = False

    async def wait(self):
        if self.code is None and not self.killed:
            await asyncio.Event().wait()  # hangs until wait_for cancels it
        return 0 if self.code is None else self.code

    def kill(self):
        self.killed = True

    def terminate(self):
        pass


@pytest.fixture
def fake_exec(monkeypatch):
    """Records every command. `exec.codes` maps the first argv element that
    identifies the command (for example "disconnect") to its exit code, and
    `exec.raises` to an exception to raise instead of starting it."""

    class Exec:
        def __init__(self):
            self.commands = []
            self.codes = {}
            self.raises = {}

        async def __call__(self, *argv, **_kwargs):
            self.commands.append(list(argv))
            key = argv[1] if len(argv) > 1 else argv[0]
            if key in self.raises:
                raise self.raises[key]
            return FakeProc(self.codes.get(key, 0))

    fake = Exec()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    real_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout / SCALE)

    monkeypatch.setattr(asyncio, "wait_for", fast_wait_for)
    return fake


# ---------------------------------------------------------------------------
# GpclientVPNPlugin.Disconnect
# ---------------------------------------------------------------------------


class RecordingGpclient(FakeProc):
    def __init__(self):
        super().__init__(0)
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _dirty_plugin(service_module):
    plugin = service_module.GpclientVPNPlugin()
    plugin.gpclient_process = RecordingGpclient()
    plugin.hip_enabled = False
    plugin.never_default = True
    plugin.custom_routes = ["10.0.0.0/8"]
    plugin.dns_servers = ["10.0.0.53"]
    plugin.browser_target = "/usr/bin/firefox"
    return plugin


def _assert_cleaned(plugin):
    assert plugin.gpclient_process is None
    assert plugin.hip_enabled is True
    assert plugin.never_default is False
    assert plugin.custom_routes == []
    assert plugin.dns_servers == []
    assert plugin.browser_target is None


def test_disconnect_terminates_gpclient_and_runs_gpclient_disconnect(
    service_module, fake_exec, dbus_signals
):
    plugin = _dirty_plugin(service_module)
    process = plugin.gpclient_process

    asyncio.run(plugin.Disconnect())  # Disconnect

    assert process.terminated
    assert fake_exec.commands == [["/usr/bin/gpclient", "disconnect"]]
    _assert_cleaned(plugin)


@pytest.mark.parametrize(
    "failure",
    ["exit-code", "cannot-start", "hangs"],
)
def test_disconnect_survives_a_failing_gpclient_disconnect(
    service_module, fake_exec, dbus_signals, failure
):
    if failure == "exit-code":
        fake_exec.codes["disconnect"] = 1
    elif failure == "cannot-start":
        fake_exec.raises["disconnect"] = FileNotFoundError("gpclient")
    else:
        fake_exec.codes["disconnect"] = None  # never exits: the timeout hits
    plugin = _dirty_plugin(service_module)

    asyncio.run(plugin.Disconnect())  # Disconnect: must not raise

    assert fake_exec.commands == [["/usr/bin/gpclient", "disconnect"]]
    _assert_cleaned(plugin)


# ---------------------------------------------------------------------------
# GpclientVPNPlugin._cleanup_stale_gpd0
# ---------------------------------------------------------------------------


@pytest.fixture
def net_sysfs(service_module, monkeypatch, tmp_path):
    """A fake /sys/class/net; gpd0 is added by the test"""
    net = tmp_path / "net"
    net.mkdir()
    (net / "lo").mkdir()
    monkeypatch.setattr(service_module, "NET_SYSFS_PATH", str(net))
    return net


def test_cleanup_stale_gpd0_runs_the_cleanup_without_gpclient(
    service_module, fake_exec, net_sysfs
):
    (net_sysfs / "gpd0").mkdir()
    fake_exec.codes["-x"] = 1  # pgrep -x gpclient: no process

    plugin = service_module.GpclientVPNPlugin()
    asyncio.run(plugin._cleanup_stale_gpd0())

    assert fake_exec.commands == [
        ["pgrep", "-x", "gpclient"],
        ["/usr/bin/gpclient", "disconnect"],
        ["ip", "link", "del", "gpd0"],  # the fake does not remove gpd0
    ]


def test_cleanup_stale_gpd0_does_nothing_while_gpclient_runs(
    service_module, fake_exec, net_sysfs
):
    (net_sysfs / "gpd0").mkdir()
    fake_exec.codes["-x"] = 0  # pgrep -x gpclient: a process runs

    plugin = service_module.GpclientVPNPlugin()
    asyncio.run(plugin._cleanup_stale_gpd0())

    assert fake_exec.commands == [["pgrep", "-x", "gpclient"]]


def test_cleanup_stale_gpd0_does_nothing_without_gpd0(
    service_module, fake_exec, net_sysfs
):
    plugin = service_module.GpclientVPNPlugin()
    asyncio.run(plugin._cleanup_stale_gpd0())

    assert fake_exec.commands == []


def test_cleanup_stale_gpd0_does_nothing_when_pgrep_cannot_run(
    service_module, fake_exec, net_sysfs
):
    (net_sysfs / "gpd0").mkdir()
    fake_exec.raises["-x"] = FileNotFoundError("pgrep")

    plugin = service_module.GpclientVPNPlugin()
    asyncio.run(plugin._cleanup_stale_gpd0())

    assert fake_exec.commands == [["pgrep", "-x", "gpclient"]]


# ---------------------------------------------------------------------------
# GpclientVPNPlugin._close_pty
# ---------------------------------------------------------------------------


def _fd_is_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def test_close_pty_closes_the_fd_and_resets_it(service_module):
    plugin = service_module.GpclientVPNPlugin()
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    plugin._pty_master = read_fd

    plugin._close_pty()  # _close_pty

    assert plugin._pty_master is None
    assert not _fd_is_open(read_fd)


def test_close_pty_closes_the_transport_and_resets_it(service_module):
    plugin = service_module.GpclientVPNPlugin()
    closed = []

    class Transport:
        def close(self):
            closed.append(True)

    plugin._pty_transport = Transport()
    plugin._pty_master = 999999

    plugin._close_pty()  # _close_pty

    assert closed == [True]
    assert plugin._pty_transport is None
    assert plugin._pty_master is None


def test_close_pty_without_a_pty_does_nothing_twice(service_module):
    plugin = service_module.GpclientVPNPlugin()
    before = open_fds()

    plugin._close_pty()  # _close_pty
    plugin._close_pty()

    assert plugin._pty_master is None
    assert plugin._pty_transport is None
    assert open_fds() == before


def test_close_pty_called_twice_does_not_close_the_fd_again(service_module):
    plugin = service_module.GpclientVPNPlugin()
    first_fd, second_fd = os.pipe()
    plugin._pty_master = first_fd

    plugin._close_pty()  # _close_pty
    # The number is free now: a second call must not close what reuses it
    reused = os.dup2(second_fd, first_fd)
    try:
        plugin._close_pty()
        assert _fd_is_open(reused)
    finally:
        os.close(reused)
        os.close(second_fd)


# ---------------------------------------------------------------------------
# ScreenBuffer._write and tokens_text
# ---------------------------------------------------------------------------


def test_screen_buffer_write_puts_text_at_the_cursor(service_module):
    screen = service_module.ScreenBuffer()

    screen._write("abc")  # ScreenBuffer._write
    screen._col = 1
    screen._write("X")

    assert screen.lines() == ("aXc",)
    assert screen._col == 2


def test_screen_buffer_write_gives_wide_characters_two_cells(service_module):
    screen = service_module.ScreenBuffer()

    screen._write("漢x")  # ScreenBuffer._write

    assert screen.lines() == ("漢x",)
    assert screen._col == 3  # two cells for the wide one, one for "x"


@pytest.mark.parametrize("char", ["\x00", "\x01", "\x07", "\x0e", "\x1b", "\x1f", "\x7f"])
def test_screen_buffer_write_drops_control_characters(service_module, char):
    assert service_module.CONTROL_CHARS_RE.match(char)
    screen = service_module.ScreenBuffer()

    screen._write(f"a{char}b")  # ScreenBuffer._write

    assert screen.lines() == ("ab",)
    assert screen._col == 2  # the control character took no cell


def test_tokens_text_joins_the_text_tokens(service_module):
    tokens = [("text", "one "), ("text", "two")]

    assert service_module.tokens_text(tokens) == "one two"  # tokens_text


def test_tokens_text_removes_control_characters_and_escape_tokens(service_module):
    tokens = [
        ("text", "a\x01b\x7f"),
        ("csi", "31", "", "m"),
        ("text", "c\x1bd\n"),
    ]

    assert service_module.tokens_text(tokens) == "abcd\n"  # tokens_text
