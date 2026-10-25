"""
Tests for branches of the nm-gpclient service that no other unit test reaches
(issue #55): gpclient start-up and output monitor, the OpenSSL retry, stopping
gpclient, closing the PTY, the debounced prompt checks, `nmcli` writes to the
profile, the interactive secret request and a few small helpers.

External tools are faked: `nmcli` through PATH, gpclient with a stand-in
script, and the PTY, the processes and the clock with small fakes. Nothing here
depends on the uid of the test process.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import gc
import json
import logging
import os
import stat
import sys

import pytest

SELECT_FRAME = [
    "? Which gateway do you want to connect to?",
    "> gw-warsaw (gw1.example.com)",
    "  gw-frankfurt (gw2.example.com)",
    "[↑↓ to move, enter to select, type to filter]",
]
HELP = "[↑↓ to move, enter to select, type to filter]"
QUESTION = "? Which gateway do you want to connect to?"


def _plugin(service_module):
    return service_module.GpclientVPNPlugin()


def _log_text(caplog, service_module, level=logging.INFO):
    return "\n".join(r.getMessage() for r in caplog.records if r.levelno >= level)


class FakeProcess:
    """Stand-in for the asyncio process of gpclient"""

    pid = 4242

    def __init__(self, returncode=0, wait_error=None, terminate_error=None):
        self.returncode = returncode
        self.wait_error = wait_error
        self.terminate_error = terminate_error
        self.terminated = 0

    async def wait(self):
        if self.wait_error is not None:
            raise self.wait_error
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if self.terminate_error is not None:
            raise self.terminate_error


# --- detect_select_prompt and gateway_matches --------------------------------


class TestDetectSelectPromptNoise:
    NOISE = ["x", "Xfoo (host)", ">nospace (host)", "plain log line", "*  odd"]

    @pytest.mark.parametrize("noise", NOISE)
    def test_lines_without_a_marker_are_not_options(self, service_module, noise):
        lines = [QUESTION, "> gw-a (a.example.com)", noise, HELP]
        frame = service_module.detect_select_prompt(lines)
        assert frame["options"] == ["gw-a (a.example.com)"]

    @pytest.mark.parametrize("noise", NOISE)
    def test_frame_made_of_noise_only_is_not_a_frame(self, service_module, noise):
        assert service_module.detect_select_prompt([QUESTION, noise, HELP]) is None


class TestGatewayMatchesEdges:
    @pytest.mark.parametrize("preferred", ["", "   ", None])
    def test_nothing_asked_matches_nothing(self, service_module, preferred):
        assert not service_module.gateway_matches(preferred, "gw (gw.example.com)")

    @pytest.mark.parametrize("substring", [True, False])
    def test_the_whole_entry_matches_without_substring(self, service_module, substring):
        option = "gw-a (a.example.com)"
        assert service_module.gateway_matches(
            "GW-A (a.example.com)", option, substring=substring
        )

    def test_partial_text_needs_substring(self, service_module):
        option = "gw-a (a.example.com)"
        assert not service_module.gateway_matches("gw-a (a", option, substring=False)
        assert service_module.gateway_matches("gw-a (a", option, substring=True)
        assert not service_module.gateway_matches("gw-b", option, substring=True)


# --- _reset_phase_state ------------------------------------------------------


class TestResetPhaseState:
    @pytest.mark.parametrize("prefilled", [True, False])
    def test_username_is_answered_only_when_prefilled(self, service_module, prefilled):
        plugin = _plugin(service_module)
        plugin._username_prefilled = prefilled
        plugin._answered_username = not prefilled
        plugin._answered_password = True

        plugin._reset_phase_state()

        assert plugin._answered_username is prefilled
        assert plugin._answered_password is False


# --- _close_pty ---------------------------------------------------------------


class TestClosePty:
    def test_master_without_reader_is_closed(self, service_module):
        plugin = _plugin(service_module)
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        plugin._pty_master = read_fd

        plugin._close_pty()

        assert plugin._pty_master is None
        with pytest.raises(OSError):
            os.fstat(read_fd)

    def test_master_that_is_already_closed_is_not_an_error(self, service_module):
        plugin = _plugin(service_module)
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        os.close(read_fd)
        plugin._pty_master = read_fd

        plugin._close_pty()  # EBADF is swallowed

        assert plugin._pty_master is None

    def test_nothing_to_close_changes_nothing(self, service_module):
        plugin = _plugin(service_module)
        plugin._pty_master = None
        plugin._pty_transport = None

        plugin._close_pty()

        assert plugin._pty_master is None and plugin._pty_transport is None

    @pytest.mark.parametrize("fails", [True, False])
    def test_transport_close_error_is_swallowed(self, service_module, fails):
        closed = []

        class Transport:
            def close(self):
                closed.append(True)
                if fails:
                    raise RuntimeError("already closing")

        plugin = _plugin(service_module)
        plugin._pty_transport = Transport()
        plugin._pty_master = 99999

        plugin._close_pty()

        assert closed == [True]
        assert plugin._pty_transport is None
        assert plugin._pty_master is None


# --- _write_keys, _fail, _fail_internal_network -------------------------------


class TestWriteKeys:
    def test_keys_reach_the_pty(self, service_module):
        plugin = _plugin(service_module)
        read_fd, write_fd = os.pipe()
        plugin._pty_master = write_fd
        try:
            plugin._write_keys(b"abc\r", "answer")
            assert os.read(read_fd, 16) == b"abc\r"
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_no_pty_is_logged_not_raised(self, service_module, caplog):
        plugin = _plugin(service_module)
        plugin._pty_master = None
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            plugin._write_keys(b"abc", "answer")
        assert "Cannot send answer: PTY is gone" in _log_text(caplog, service_module)

    def test_write_error_is_logged_not_raised(self, service_module, caplog):
        plugin = _plugin(service_module)
        read_fd, write_fd = os.pipe()
        os.close(read_fd)  # writing to the pipe now fails (EPIPE)
        plugin._pty_master = write_fd
        try:
            with caplog.at_level(logging.INFO, logger=service_module.logger.name):
                plugin._write_keys(b"abc", "answer")
        finally:
            os.close(write_fd)
        assert "Failed to send answer to PTY" in _log_text(caplog, service_module)


class TestFail:
    def test_a_process_that_is_gone_is_not_an_error(self, service_module, dbus_signals):
        plugin = _plugin(service_module)
        plugin.gpclient_process = FakeProcess(terminate_error=ProcessLookupError())

        plugin._fail(service_module.NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED, "boom")

        assert plugin.gpclient_process.terminated == 1
        assert plugin._login_failed is True
        assert ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED) in (
            dbus_signals
        )

    def test_without_a_process_the_failure_is_still_reported(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin.gpclient_process = None

        plugin._fail(service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED, "boom")

        assert [name for name, _ in dbus_signals] == ["Failure", "StateChanged"]


class TestFailInternalNetwork:
    def test_reports_connect_failed_once(self, service_module, dbus_signals):
        plugin = _plugin(service_module)
        plugin.gpclient_process = FakeProcess()

        plugin._fail_internal_network()
        plugin._fail_internal_network()  # one line per gateway

        failures = [p for name, p in dbus_signals if name == "Failure"]
        assert failures == [service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED]
        assert plugin.gpclient_process.terminated == 1

    def test_nothing_is_reported_after_an_earlier_failure(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin.gpclient_process = FakeProcess()
        plugin._login_failed = True

        plugin._fail_internal_network()

        assert dbus_signals == []
        assert plugin.gpclient_process.terminated == 0


# --- _stop_gpclient -------------------------------------------------------------


class StoppableProcess:
    """Process whose wait() never ends unless `exits_on` happens"""

    pid = 777

    def __init__(self, exits_on="terminate", kill_error=None, terminate_error=None):
        self.exits_on = exits_on
        self.kill_error = kill_error
        self.terminate_error = terminate_error
        self.calls = []
        self._exited = None

    def _signal(self, name):
        self.calls.append(name)
        if self.exits_on == name and self._exited is not None:
            self._exited.set_result(None)

    def terminate(self):
        if self.terminate_error:
            raise self.terminate_error
        self._signal("terminate")

    def kill(self):
        if self.kill_error:
            raise self.kill_error
        self._signal("kill")

    async def wait(self):
        if self._exited is None:
            self._exited = asyncio.get_running_loop().create_future()
            if self.exits_on in self.calls:
                self._exited.set_result(None)
        await asyncio.shield(self._exited)  # a timeout must not cancel it
        return 0


@pytest.fixture
def fast_wait_for(monkeypatch):
    real = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        return await real(awaitable, min(timeout, 0.05))

    monkeypatch.setattr(asyncio, "wait_for", wait_for)


class TestStopGpclient:
    def _stop(self, service_module, process):
        plugin = _plugin(service_module)
        plugin.gpclient_process = process
        asyncio.run(plugin._stop_gpclient())

    def test_terminate_is_enough_when_gpclient_exits(self, service_module, fast_wait_for):
        process = StoppableProcess(exits_on="terminate")
        self._stop(service_module, process)
        assert process.calls == ["terminate"]

    def test_gpclient_that_ignores_sigterm_is_killed(self, service_module, fast_wait_for):
        process = StoppableProcess(exits_on="kill")
        self._stop(service_module, process)
        assert process.calls == ["terminate", "kill"]

    def test_process_that_vanishes_before_the_kill_is_not_an_error(
        self, service_module, fast_wait_for
    ):
        process = StoppableProcess(exits_on="never", kill_error=ProcessLookupError())
        self._stop(service_module, process)
        assert process.calls == ["terminate"]

    def test_process_that_survives_sigkill_is_reported(
        self, service_module, fast_wait_for, caplog
    ):
        process = StoppableProcess(exits_on="never")
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            self._stop(service_module, process)
        assert process.calls == ["terminate", "kill"]
        assert "refused to die after SIGKILL" in _log_text(caplog, service_module)

    def test_process_that_is_gone_at_terminate_is_not_an_error(
        self, service_module, fast_wait_for, caplog
    ):
        process = StoppableProcess(terminate_error=ProcessLookupError())
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            self._stop(service_module, process)
        assert process.calls == []
        assert "Error terminating" not in _log_text(caplog, service_module)

    def test_unexpected_error_is_logged_not_raised(self, service_module, caplog):
        process = StoppableProcess(terminate_error=PermissionError("not allowed"))
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            self._stop(service_module, process)
        assert "Error terminating gpclient: not allowed" in _log_text(
            caplog, service_module
        )

    def test_no_process_does_nothing(self, service_module):
        self._stop(service_module, None)


# --- _start_gpclient -----------------------------------------------------------

FAKE_GPCLIENT_ENV = r"""
import json, os, sys
keys = ["SUDO_UID", "SUDO_USER", "DISPLAY", "GP_BROWSER",
        "GPCLIENT_CUSTOM_DNS_DOMAINS", "TERM"]
with open("__OUT__", "w") as out:
    json.dump({"argv": sys.argv[1:],
               "env": {k: os.environ.get(k) for k in keys}}, out)
sys.exit(0)
"""


@pytest.fixture
def start_env(service_module, monkeypatch, tmp_path):
    """Run _start_gpclient against a stand-in script; returns a runner that
    gives what the script saw. The user lookup is faked, and so is subprocess.run."""
    out = tmp_path / "seen.json"
    script = tmp_path / "gpclient.py"
    script.write_text(FAKE_GPCLIENT_ENV.replace("__OUT__", str(out)))
    launcher = tmp_path / "gpclient.sh"
    launcher.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{script}" "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setattr(service_module, "GPCLIENT_BINARY", str(launcher))
    for name in ("SUDO_UID", "SUDO_USER", "DISPLAY", "GP_BROWSER",
                 "GPCLIENT_CUSTOM_DNS_DOMAINS"):
        monkeypatch.delenv(name, raising=False)

    subprocess_calls = []

    def fake_run(cmd, **kwargs):
        subprocess_calls.append(cmd)

    monkeypatch.setattr(service_module.subprocess, "run", fake_run)

    def run(uid=1000, **settings):
        plugin = _plugin(service_module)
        plugin.gateway = "portal.example.com"
        plugin.browser = "/bin/true"
        for name, value in settings.items():
            setattr(plugin, name, value)
        monkeypatch.setattr(
            plugin, "_get_real_user", lambda: (uid, "alice", str(tmp_path))
        )

        async def go():
            assert await plugin._start_gpclient()
            await asyncio.wait_for(plugin.stdout_monitor_task, 10)

        asyncio.run(go())
        return json.loads(out.read_text())

    run.subprocess_calls = subprocess_calls
    return run


class TestStartGpclient:
    def test_no_pkill_of_gpauth(self, start_env, gpauth_signals):
        # pkill -9 -u <uid> gpauth also killed the gpauth of a CLI or GUI
        # session of the same user (issue #52)
        seen = start_env(uid=1234)
        assert seen["argv"][0] == "connect"
        assert not [c for c in start_env.subprocess_calls if "pkill" in c]
        assert gpauth_signals == []

    def test_root_session_gets_no_user_environment(self, start_env):
        seen = start_env(uid=0)
        assert seen["env"]["SUDO_UID"] is None
        assert seen["env"]["SUDO_USER"] is None

    def test_custom_dns_domains_are_exported(self, start_env):
        seen = start_env(dns_domains=["corp.example.com", "lan.example.com"])
        assert (
            seen["env"]["GPCLIENT_CUSTOM_DNS_DOMAINS"]
            == "corp.example.com lan.example.com"
        )

    @pytest.mark.parametrize("domains", [[], None])
    def test_no_dns_domains_exports_nothing(self, start_env, domains):
        seen = start_env(dns_domains=domains or [])
        assert seen["env"]["GPCLIENT_CUSTOM_DNS_DOMAINS"] is None

    def test_browser_target_and_terminal_are_set(self, start_env):
        seen = start_env(browser_target="firefox")
        assert seen["env"]["GP_BROWSER"] == "firefox"
        assert seen["env"]["TERM"] == "xterm-256color"

    def test_no_browser_target_leaves_gp_browser_unset(self, start_env):
        seen = start_env(browser_target=None)
        assert seen["env"]["GP_BROWSER"] is None

    def test_missing_binary_fails_and_leaks_no_fd(
        self, service_module, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            service_module, "GPCLIENT_BINARY", str(tmp_path / "does-not-exist")
        )
        monkeypatch.setattr(service_module.subprocess, "run", lambda *a, **k: None)
        plugin = _plugin(service_module)
        plugin.gateway = "portal.example.com"
        plugin.browser = "/bin/true"
        monkeypatch.setattr(plugin, "_get_real_user", lambda: (0, "root", "/root"))

        before = set(os.listdir("/proc/self/fd"))
        assert asyncio.run(plugin._start_gpclient()) is False
        after = set(os.listdir("/proc/self/fd"))

        assert plugin._pty_master is None
        assert plugin.gpclient_process is None
        assert len(after) <= len(before)


# --- _monitor_gpclient_output ---------------------------------------------------


async def _monitor(plugin, chunks, process):
    """Run the monitor on a pipe that carries `chunks` and then ends"""
    # An object an earlier test leaked (a PTY transport, a file) closes its fd
    # when it is collected; collect now, before this test takes fd numbers,
    # so that it cannot close one of ours later (EBADF)
    gc.collect()
    read_fd, write_fd = os.pipe()
    for chunk in chunks:
        os.write(write_fd, chunk)
    os.close(write_fd)
    plugin._pty_master = read_fd
    plugin.gpclient_process = process
    try:
        await asyncio.wait_for(plugin._monitor_gpclient_output(), 10)
    finally:
        if plugin._prompt_task:
            plugin._prompt_task.cancel()
        # Close the read end now, while this loop runs. Left to the garbage
        # collector, the transport closes its fd number later, when a later
        # test may already have got the same number from os.pipe() (EBADF).
        plugin._close_pty()


class TestMonitorGpclientOutput:
    @pytest.mark.parametrize("missing", ["process", "master"])
    def test_nothing_to_monitor_returns_at_once(self, service_module, missing):
        plugin = _plugin(service_module)
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        plugin.gpclient_process = None if missing == "process" else FakeProcess()
        plugin._pty_master = read_fd if missing == "process" else None
        try:
            asyncio.run(plugin._monitor_gpclient_output())
            # Nothing was attached, so the descriptor is still ours
            if missing == "process":
                os.fstat(read_fd)
        finally:
            os.close(read_fd)
        assert plugin._recent_lines == type(plugin._recent_lines)()

    def test_reader_that_cannot_attach_fails_the_login(
        self, service_module, tmp_path, dbus_signals
    ):
        plugin = _plugin(service_module)
        process = FakeProcess()
        plugin.gpclient_process = process
        # A regular file cannot be watched as a pipe
        plugin._pty_master = os.open(tmp_path / "plain", os.O_RDWR | os.O_CREAT)

        asyncio.run(plugin._monitor_gpclient_output())

        assert plugin._pty_master is None
        assert plugin._login_failed is True
        assert process.terminated == 1
        assert (
            "Failure",
            service_module.NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED,
        ) in dbus_signals

    def test_unrequested_exit_zero_reports_connect_failed(
        self, service_module, dbus_signals, caplog
    ):
        plugin = _plugin(service_module)
        with caplog.at_level("ERROR", logger=service_module.logger.name):
            asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess(returncode=0)))
        assert list(plugin._recent_lines) == ["hello"]
        assert dbus_signals == [
            ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED),
            ("StateChanged", service_module.NM_VPN_SERVICE_STATE_STOPPED),
        ]
        assert plugin.State() == service_module.NM_VPN_SERVICE_STATE_STOPPED
        assert "gpclient exited (status 0) without being asked to" in caplog.text

    def test_exit_zero_after_the_tunnel_is_up_reports_connect_failed(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin._set_state(service_module.NM_VPN_SERVICE_STATE_STARTED)
        plugin._tunnel_up = True
        dbus_signals.clear()
        asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess(returncode=0)))
        assert dbus_signals == [
            ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED),
            ("StateChanged", service_module.NM_VPN_SERVICE_STATE_STOPPED),
        ]

    def test_exit_zero_after_a_reported_failure_is_not_reported_again(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin._login_failed = True
        asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess(returncode=0)))
        assert dbus_signals == []

    def test_exit_requested_by_disconnect_is_not_reported(
        self, service_module, dbus_signals
    ):
        # Disconnect cancels the monitor before it stops gpclient, so the
        # monitor never gets to see the exit
        plugin = _plugin(service_module)

        async def go():
            read_fd, write_fd = os.pipe()
            plugin._pty_master = read_fd
            plugin.gpclient_process = FakeProcess(returncode=0)
            task = asyncio.create_task(plugin._monitor_gpclient_output())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            os.close(write_fd)
            if plugin._pty_master is not None:
                try:
                    os.close(plugin._pty_master)
                except OSError:
                    pass

        asyncio.run(go())
        assert dbus_signals == []

    def test_non_zero_exit_reports_connect_failed(self, service_module, dbus_signals):
        plugin = _plugin(service_module)
        asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess(returncode=3)))
        assert (
            "Failure",
            service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED,
        ) in dbus_signals

    def test_gpauth_of_gpclient_is_noted_before_the_exit_is_handled(
        self, service_module, dbus_signals, monkeypatch
    ):
        # Once gpclient is reaped its children belong to init: look first
        plugin = _plugin(service_module)
        events = []
        process = FakeProcess(returncode=3)
        real_wait = process.wait

        async def wait():
            events.append("wait")
            return await real_wait()

        async def remember():
            events.append("remember")

        process.wait = wait
        monkeypatch.setattr(plugin, "_remember_gpauth", remember)

        asyncio.run(_monitor(plugin, [b"hello\r\n"], process))

        assert events == ["remember", "wait"]

    def test_monitor_that_is_cancelled_notes_no_gpauth(
        self, service_module, dbus_signals, monkeypatch
    ):
        # Disconnect cancels the monitor and notes the gpauth itself
        plugin = _plugin(service_module)
        noted = []

        async def remember():
            noted.append(True)

        monkeypatch.setattr(plugin, "_remember_gpauth", remember)

        async def go():
            read_fd, write_fd = os.pipe()
            plugin._pty_master = read_fd
            plugin.gpclient_process = FakeProcess(returncode=None)
            task = asyncio.create_task(plugin._monitor_gpclient_output())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            os.close(write_fd)
            if plugin._pty_master is not None:
                try:
                    os.close(plugin._pty_master)
                except OSError:
                    pass

        asyncio.run(go())
        assert noted == []

    def test_non_zero_exit_after_a_reported_failure_is_not_reported_again(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin._login_failed = True
        asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess(returncode=143)))
        assert dbus_signals == []

    def test_repeated_line_is_logged_once_but_kept(self, service_module, caplog):
        plugin = _plugin(service_module)
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            asyncio.run(
                _monitor(plugin, [b"same\r\nsame\r\nother\r\n"], FakeProcess())
            )
        text = _log_text(caplog, service_module)
        assert text.count("gpclient output: same") == 1
        assert text.count("gpclient output: other") == 1
        assert list(plugin._recent_lines) == ["same", "same", "other"]

    def test_different_lines_are_all_logged(self, service_module, caplog):
        plugin = _plugin(service_module)
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            asyncio.run(_monitor(plugin, [b"one\r\ntwo\r\none\r\n"], FakeProcess()))
        assert _log_text(caplog, service_module).count("gpclient output: one") == 2

    @pytest.mark.parametrize(
        "line, warns",
        [
            ("Error: try again with --as-gateway", True),
            ("Connecting to the gateway", False),
        ],
    )
    def test_as_gateway_hint_is_reported(self, service_module, caplog, line, warns):
        plugin = _plugin(service_module)
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            asyncio.run(_monitor(plugin, [line.encode() + b"\r\n"], FakeProcess()))
        text = _log_text(caplog, service_module, logging.WARNING)
        assert ("Address is a gateway" in text) is warns

    def test_unexpected_error_is_logged_not_raised(self, service_module, caplog):
        plugin = _plugin(service_module)
        process = FakeProcess(wait_error=RuntimeError("wait broke"))
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            asyncio.run(_monitor(plugin, [b"hello\r\n"], process))
        assert "Error monitoring gpclient output: wait broke" in _log_text(
            caplog, service_module
        )

    def test_clean_run_logs_no_monitor_error(self, service_module, caplog):
        plugin = _plugin(service_module)
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            asyncio.run(_monitor(plugin, [b"hello\r\n"], FakeProcess()))
        assert "Error monitoring" not in _log_text(caplog, service_module)


# --- _retry_with_openssl_fix ----------------------------------------------------


class TestRetryWithOpensslFix:
    def _ready(self, service_module, start_result):
        plugin = _plugin(service_module)
        plugin._openssl_error_seen = True
        starts = []

        async def noop():
            return {}

        async def start():
            starts.append(True)
            return start_result

        plugin._cleanup_stale_gpd0 = noop
        plugin._snapshot_tunnel_interfaces = noop
        plugin._start_gpclient = start
        return plugin, starts

    def test_pending_prompt_task_is_cancelled(self, service_module):
        plugin, starts = self._ready(service_module, True)

        async def go():
            pending = asyncio.ensure_future(asyncio.sleep(60))
            plugin._prompt_task = pending
            result = await plugin._retry_with_openssl_fix()
            await asyncio.sleep(0)
            return result, pending

        result, pending = asyncio.run(go())

        assert result is True
        assert pending.cancelled()
        assert plugin._prompt_task is None
        assert starts == [True]
        assert plugin.fix_openssl is True and plugin._openssl_retried is True

    def test_finished_prompt_task_is_left_alone(self, service_module):
        plugin, _ = self._ready(service_module, True)

        async def go():
            done = asyncio.ensure_future(asyncio.sleep(0))
            await done
            plugin._prompt_task = done
            await plugin._retry_with_openssl_fix()
            return done

        assert not asyncio.run(go()).cancelled()

    def test_restart_that_fails_reports_false(self, service_module, caplog):
        plugin, starts = self._ready(service_module, False)
        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            assert asyncio.run(plugin._retry_with_openssl_fix()) is False
        assert starts == [True]
        assert "Failed to restart gpclient with --fix-openssl" in _log_text(
            caplog, service_module
        )

    @pytest.mark.parametrize("seen, retried", [(False, False), (True, True)])
    def test_no_restart_without_a_fresh_legacy_error(
        self, service_module, seen, retried
    ):
        plugin, starts = self._ready(service_module, True)
        plugin._openssl_error_seen = seen
        plugin._openssl_retried = retried
        assert asyncio.run(plugin._retry_with_openssl_fix()) is False
        assert starts == []


# --- _schedule_prompt_check: debounced checks that find new output -------------


@pytest.fixture
def quick_debounce(service_module, monkeypatch):
    monkeypatch.setattr(service_module, "PROMPT_DEBOUNCE_SECONDS", 0.05)


async def _settle():
    await asyncio.sleep(0.2)


class TestDebouncedPromptChecks:
    def _watched(self, service_module, handled):
        plugin = _plugin(service_module)

        async def select(frame):
            handled.append(("select", frame["message"]))

        async def prompt(label):
            handled.append(("prompt", label))

        plugin._handle_select_prompt = select
        plugin._handle_prompt = prompt
        return plugin

    @pytest.mark.parametrize("more_output", [False, True])
    def test_select_frame_waits_for_the_screen_to_settle(
        self, service_module, quick_debounce, more_output
    ):
        handled = []
        plugin = self._watched(service_module, handled)

        async def go():
            plugin._screen.feed("\r\n".join(SELECT_FRAME) + "\r\n")
            plugin._schedule_prompt_check()
            if more_output:
                plugin._screen.feed("unrelated line\r\n")  # screen moved on
            await _settle()

        asyncio.run(go())

        if more_output:
            assert handled == []
        else:
            assert handled == [("select", "Which gateway do you want to connect to?")]

    @pytest.mark.parametrize("more_output", [False, True])
    def test_text_prompt_in_the_tail_waits_for_stable_output(
        self, service_module, quick_debounce, more_output
    ):
        handled = []
        plugin = self._watched(service_module, handled)

        async def go():
            plugin._consume_output("? Password: ")
            plugin._schedule_prompt_check()
            if more_output:
                plugin._consume_output("x")  # the tail changed
            await _settle()

        asyncio.run(go())

        assert handled == ([] if more_output else [("prompt", "Password")])

    @pytest.mark.parametrize("more_output", [False, True])
    def test_prompt_on_a_terminated_row_waits_for_the_screen_to_settle(
        self, service_module, quick_debounce, more_output
    ):
        handled = []
        plugin = self._watched(service_module, handled)

        async def go():
            # inquire ends the prompt line: the cursor is on the next row
            plugin._consume_output("? Password:\r\n")
            plugin._schedule_prompt_check()
            if more_output:
                plugin._consume_output("more\r\n")
            await _settle()

        asyncio.run(go())

        assert handled == ([] if more_output else [("prompt", "Password")])


class TestPendingPromptCheck:
    @pytest.mark.parametrize("answering", [False, True])
    def test_new_output_cancels_the_pending_check_unless_answering(
        self, service_module, answering
    ):
        plugin = _plugin(service_module)
        plugin._answering = answering

        async def go():
            pending = asyncio.ensure_future(asyncio.sleep(3600))
            plugin._prompt_task = pending
            plugin._schedule_prompt_check()  # no prompt on the screen
            await asyncio.sleep(0)
            cancelled = pending.cancelled()
            kept = plugin._prompt_task is pending
            pending.cancel()
            return cancelled, kept

        cancelled, kept = asyncio.run(go())

        # A prompt being answered keeps its task; otherwise the stale check is
        # cancelled and, with no prompt in sight, none replaces it
        assert cancelled is not answering
        assert kept is answering
        if not answering:
            assert plugin._prompt_task is None


# --- _nmcli_modify and the writes built on it ----------------------------------

FAKE_NMCLI = """#!/bin/sh
printf '%s\\n' "$*" >> "{log}"
if [ -n "$NMCLI_FAIL" ]; then
    echo "$NMCLI_FAIL" >&2
    exit 4
fi
exit 0
"""


@pytest.fixture
def fake_nmcli(monkeypatch, tmp_path):
    """A fake `nmcli` first (and only) on PATH; returns the file of its calls"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "nmcli.log"
    script = bin_dir / "nmcli"
    script.write_text(FAKE_NMCLI.format(log=log))
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("NMCLI_FAIL", raising=False)

    def calls():
        return log.read_text().splitlines() if log.exists() else []

    calls.fail = lambda message: monkeypatch.setenv("NMCLI_FAIL", message)
    calls.bin_dir = bin_dir
    return calls


class TestNmcliModify:
    def test_runs_nmcli_with_the_connection_uuid(self, service_module, fake_nmcli):
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"

        assert asyncio.run(plugin._nmcli_modify("+vpn.data", "k=v")) is True

        assert fake_nmcli() == ["connection modify uuid-1234 +vpn.data k=v"]

    def test_no_uuid_runs_nothing(self, service_module, fake_nmcli):
        plugin = _plugin(service_module)
        plugin._connection_uuid = ""

        assert asyncio.run(plugin._nmcli_modify("+vpn.data", "k=v")) is False

        assert fake_nmcli() == []

    def test_failing_nmcli_is_logged_and_reported_false(
        self, service_module, fake_nmcli, caplog
    ):
        fake_nmcli.fail("Error: unknown connection")
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"

        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            assert asyncio.run(plugin._nmcli_modify("+vpn.data", "k=v")) is False

        text = _log_text(caplog, service_module, logging.WARNING)
        assert "failed with 4: Error: unknown connection" in text

    def test_missing_nmcli_is_swallowed(self, service_module, fake_nmcli, caplog):
        (fake_nmcli.bin_dir / "nmcli").unlink()
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"

        with caplog.at_level(logging.INFO, logger=service_module.logger.name):
            assert asyncio.run(plugin._nmcli_modify("+vpn.data", "k=v")) is False

        assert "nmcli modify" in _log_text(caplog, service_module, logging.WARNING)

    def test_write_vpn_data_builds_one_key_assignment(self, service_module, fake_nmcli):
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"

        assert asyncio.run(plugin._write_vpn_data("fix-openssl", "true")) is True

        assert fake_nmcli() == ["connection modify uuid-1234 +vpn.data fix-openssl=true"]


class TestPersistThroughNmcli:
    def _plugin(self, service_module):
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"
        plugin._gateway_list = ["gw-a", "gw-b"]
        plugin._lap_entries = ["gw-a", "gw-b"]
        plugin._gateway_count = 2
        return plugin

    def test_whole_list_and_count_are_written(self, service_module, fake_nmcli):
        plugin = self._plugin(service_module)

        asyncio.run(plugin._persist_gateway_list())

        assert fake_nmcli() == [
            "connection modify uuid-1234 +vpn.data gateway-list=gw-a;gw-b",
            "connection modify uuid-1234 +vpn.data gateway-list-count=2",
        ]
        assert plugin._stored_gateway_list == "gw-a;gw-b"
        assert plugin._stored_gateway_count == 2

    def test_failed_write_keeps_the_stored_list(self, service_module, fake_nmcli):
        fake_nmcli.fail("Error: denied")
        plugin = self._plugin(service_module)
        plugin._stored_gateway_list = "gw-old"

        asyncio.run(plugin._persist_gateway_list())

        assert fake_nmcli() == [
            "connection modify uuid-1234 +vpn.data gateway-list=gw-a;gw-b"
        ]
        assert plugin._stored_gateway_list == "gw-old"
        assert plugin._stored_gateway_count is None

    def test_unchanged_list_writes_nothing(self, service_module, fake_nmcli):
        plugin = self._plugin(service_module)
        plugin._stored_gateway_list = "gw-a;gw-b"
        plugin._stored_gateway_count = 2

        asyncio.run(plugin._persist_gateway_list())

        assert fake_nmcli() == []

    def test_fix_openssl_is_stored_after_a_retry(self, service_module, fake_nmcli):
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"
        plugin._openssl_retried = True
        plugin.fix_openssl = True
        plugin.fix_openssl_mode = "auto"

        asyncio.run(plugin._persist_fix_openssl())

        assert fake_nmcli() == [
            "connection modify uuid-1234 +vpn.data fix-openssl=true"
        ]
        assert plugin.fix_openssl_mode == "true"

    @pytest.mark.parametrize(
        "retried, fix, mode",
        [
            (False, True, "auto"),  # no retry: nothing was learned
            (True, False, "auto"),  # flag not in use
            (True, True, "true"),  # already in the profile
        ],
    )
    def test_fix_openssl_is_not_stored_otherwise(
        self, service_module, fake_nmcli, retried, fix, mode
    ):
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"
        plugin._openssl_retried = retried
        plugin.fix_openssl = fix
        plugin.fix_openssl_mode = mode

        asyncio.run(plugin._persist_fix_openssl())

        assert fake_nmcli() == []

    def test_failed_fix_openssl_write_keeps_the_mode(self, service_module, fake_nmcli):
        fake_nmcli.fail("Error: denied")
        plugin = _plugin(service_module)
        plugin._connection_uuid = "uuid-1234"
        plugin._openssl_retried = True
        plugin.fix_openssl = True
        plugin.fix_openssl_mode = "auto"

        asyncio.run(plugin._persist_fix_openssl())

        assert plugin.fix_openssl_mode == "auto"


# --- _request_secret_interactive and the prompts that use it --------------------


class TestRequestSecretInteractive:
    def _ask(self, service_module, plugin, answer, key="password", label="Password",
             banner_msg="", timeout=None):
        """Run the request; `answer` is delivered through _secret_future"""

        async def go():
            task = asyncio.ensure_future(
                plugin._request_secret_interactive(key, label, banner_msg)
            )
            await asyncio.sleep(0)
            assert plugin._secret_future is not None
            if answer is not None:
                plugin._secret_future.set_result(answer)
            return await asyncio.wait_for(task, timeout or 5)

        return asyncio.run(go())

    def test_not_interactive_raises_without_a_signal(
        self, service_module, dbus_signals
    ):
        plugin = _plugin(service_module)
        plugin._interactive = False

        with pytest.raises(Exception, match="not started interactively"):
            asyncio.run(
                plugin._request_secret_interactive("password", "Password", "")
            )

        assert dbus_signals == []
        assert plugin._secret_future is None

    def test_signal_carries_message_and_hints(self, service_module, dbus_signals):
        plugin = _plugin(service_module)
        plugin._interactive = True

        value = self._ask(service_module, plugin, {"password": "s3cret"})

        assert value == "s3cret"
        assert dbus_signals == [
            (
                "SecretsRequired",
                ("Password", ["x-vpn-message:Password", "password"]),
            )
        ]
        assert plugin._secret_future is None

    @pytest.mark.parametrize(
        "banner, banner_msg, expected",
        [
            (None, "", "Password"),
            (None, "Enter RSA token", "Enter RSA token - Password"),
            (
                {"kind": "Gateway", "server": "gw.example.com", "message": "m"},
                "",
                "Password (Gateway: gw.example.com)",
            ),
            (
                {"kind": "Portal", "server": "p.example.com", "message": "m"},
                "Enter RSA token",
                "Enter RSA token (Portal: p.example.com) - Password",
            ),
        ],
    )
    def test_message_names_the_banner_and_the_server(
        self, service_module, dbus_signals, banner, banner_msg, expected
    ):
        plugin = _plugin(service_module)
        plugin._interactive = True
        plugin._auth_banner = banner

        self._ask(service_module, plugin, {"password": "x"}, banner_msg=banner_msg)

        message, hints = dbus_signals[0][1]
        assert message == expected
        assert hints[0] == f"x-vpn-message:{expected}"

    def test_secret_under_another_key_is_accepted_when_alone(self, service_module):
        plugin = _plugin(service_module)
        plugin._interactive = True
        assert self._ask(service_module, plugin, {"other": "tok"}) == "tok"

    @pytest.mark.parametrize(
        "answer",
        [
            {},
            {"password": ""},
            {"other": ""},
            {"other": "a", "third": "b"},  # ambiguous: no guess
        ],
    )
    def test_missing_secret_raises(self, service_module, answer):
        plugin = _plugin(service_module)
        plugin._interactive = True
        with pytest.raises(Exception, match="User did not provide 'password'"):
            self._ask(service_module, plugin, answer)
        assert plugin._secret_future is None

    def test_timeout_raises_and_clears_the_future(self, service_module, monkeypatch):
        monkeypatch.setattr(service_module, "SECRETS_REQUEST_TIMEOUT", 0.05)
        plugin = _plugin(service_module)
        plugin._interactive = True

        with pytest.raises(Exception, match="Timed out waiting.*'password'"):
            self._ask(service_module, plugin, None)

        assert plugin._secret_future is None

    def test_cancellation_propagates_and_clears_the_future(self, service_module):
        plugin = _plugin(service_module)
        plugin._interactive = True

        async def go():
            task = asyncio.ensure_future(
                plugin._request_secret_interactive("password", "Password", "")
            )
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(go())

        assert plugin._secret_future is None


class TestHandlePromptAsksTheUser:
    def _plugin(self, service_module, answer="typed"):
        plugin = _plugin(service_module)
        asked = []
        written = []

        async def request(key, label, banner_msg):
            asked.append((key, label))
            if isinstance(answer, Exception):
                raise answer
            return answer

        plugin._request_secret_interactive = request
        plugin._write_keys = lambda data, description: written.append(data)
        plugin.gpclient_process = FakeProcess()
        return plugin, asked, written

    def test_username_without_a_stored_one_is_asked(self, service_module):
        plugin, asked, written = self._plugin(service_module)
        plugin.vpn_username = ""

        asyncio.run(plugin._handle_prompt("Username"))

        assert asked == [("username", "Username")]
        assert written == [b"typed\r"]
        assert plugin._answered_username is True

    def test_stored_username_is_used_without_asking(self, service_module):
        plugin, asked, written = self._plugin(service_module)
        plugin.vpn_username = "alice"

        asyncio.run(plugin._handle_prompt("Username"))

        assert asked == []
        assert written == [b"alice\r"]

    def test_one_time_prompt_clears_the_profile_first_then_asks(self, service_module):
        plugin, asked, written = self._plugin(service_module, answer="123456")
        order = []

        async def forget():
            order.append("forget")

        plugin._forget_one_time_secret = forget
        plugin.vpn_password = "stored"

        original = plugin._request_secret_interactive

        async def request(key, label, banner_msg):
            order.append("ask")
            return await original(key, label, banner_msg)

        plugin._request_secret_interactive = request

        asyncio.run(plugin._handle_prompt("Enter the RSA token"))

        assert order == ["forget", "ask"]
        assert asked == [(service_module.OTP_SECRET_KEY, "Enter the RSA token")]
        assert written == [b"123456\r"]

    def test_failed_request_fails_the_login_and_types_nothing(
        self, service_module, dbus_signals
    ):
        plugin, asked, written = self._plugin(
            service_module, answer=Exception("User did not provide 'username'")
        )

        asyncio.run(plugin._handle_prompt("Username"))

        assert written == []
        assert plugin._answering is False
        assert plugin._login_failed is True
        assert (
            "Failure",
            service_module.NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED,
        ) in dbus_signals
