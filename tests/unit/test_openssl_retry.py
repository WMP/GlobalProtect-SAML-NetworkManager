"""
Tests for the legacy TLS renegotiation workaround (issue #2).

Portals with an old TLS stack make gpclient fail the prelogin request with
"unsafe legacy renegotiation disabled" and print the `--fix-openssl` option it
wants to be re-run with. The service retries once with that flag, so the user
never sees the option - and reports the failure properly when it still fails.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import os
import sys

# Stand-in for gpclient: fails like a portal that needs legacy renegotiation,
# unless --fix-openssl is passed before the subcommand.
FAKE_GPCLIENT = r'''
import sys

args = sys.argv[1:]
sys.stdout.write("[INFO  gpclient::cli] gpclient started: fake\r\n")
sys.stdout.write("argv: %s\r\n" % " ".join(args))

if "--fix-openssl" in args:
    # The flag has to come before the subcommand
    assert args.index("--fix-openssl") < args.index("connect"), "flag misplaced"
    sys.stdout.write(
        "[INFO  gpclient::connect] Connecting to the only available gateway: "
        "gw-a (a.example.com)\r\n"
    )
    sys.stdout.flush()
    sys.exit(0)

sys.stdout.write(
    "[WARN  gpapi::portal::prelogin] Network error: error:0A000152:SSL routines:"
    "final_renegotiate:unsafe legacy renegotiation disabled\r\n"
)
sys.stdout.write(
    "Error: error:0A000152:SSL routines:final_renegotiate:unsafe legacy "
    "renegotiation disabled\r\n"
)
sys.stdout.write("Re-run it with the `--fix-openssl` option to work around this issue, e.g.:\r\n")
sys.stdout.flush()
sys.exit(1)
'''

# Stand-in that fails for an unrelated reason - no retry may happen
FAKE_GPCLIENT_OTHER_ERROR = r'''
import sys
sys.stdout.write("[INFO  gpclient::cli] gpclient started: fake\r\n")
sys.stdout.write("Error: portal is on fire\r\n")
sys.stdout.flush()
sys.exit(1)
'''


def _write_fake(tmp_path, name, body):
    script = tmp_path / name
    script.write_text(body)
    launcher = tmp_path / (name + ".sh")
    launcher.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{script}" "$@"\n')
    launcher.chmod(0o755)
    return str(launcher)


async def _connect_and_wait(plugin, timeout=30):
    """Start gpclient and wait for the monitor task(s) to finish"""
    assert await plugin._start_gpclient()
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = plugin.stdout_monitor_task
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        # A retry replaces the monitor task; keep waiting for the new one
        if plugin.stdout_monitor_task is task:
            return
    raise AssertionError("monitor tasks did not settle")


def _plugin(service_module, monkeypatch, fake, mode="auto"):
    monkeypatch.setattr(service_module, "GPCLIENT_BINARY", fake)
    plugin = service_module.GpclientVPNPlugin()
    plugin.gateway = "portal.example.com"
    plugin.browser = "/bin/true"
    plugin.hip_enabled = True
    plugin.fix_openssl_mode = mode
    plugin.fix_openssl = mode == "true"
    return plugin


class TestOpensslRetry:
    def test_retries_once_with_the_flag(self, service_module, monkeypatch, tmp_path):
        fake = _write_fake(tmp_path, "gpclient.py", FAKE_GPCLIENT)
        plugin = _plugin(service_module, monkeypatch, fake)

        asyncio.run(_connect_and_wait(plugin))

        assert plugin._openssl_error_seen is True
        assert plugin._openssl_retried is True
        assert plugin.fix_openssl is True
        # The retry got through, so the gateway line from the second run is here
        assert any(
            "Connecting to the only available gateway" in line
            for line in plugin._recent_lines
        )
        assert plugin._gateway_list == ["gw-a (a.example.com)"]

    def test_no_retry_for_an_unrelated_failure(
        self, service_module, monkeypatch, tmp_path, dbus_signals
    ):
        fake = _write_fake(tmp_path, "gpclient-other.py", FAKE_GPCLIENT_OTHER_ERROR)
        plugin = _plugin(service_module, monkeypatch, fake)

        asyncio.run(_connect_and_wait(plugin))

        assert plugin._openssl_error_seen is False
        assert plugin._openssl_retried is False
        assert plugin.fix_openssl is False
        # ...and NetworkManager is told the activation failed, with the state
        # transition that keeps it from waiting out its own timeout
        assert dbus_signals == [
            ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED),
            ("StateChanged", service_module.NM_VPN_SERVICE_STATE_STOPPED),
        ]

    def test_disabled_by_configuration(
        self, service_module, monkeypatch, tmp_path, dbus_signals
    ):
        fake = _write_fake(tmp_path, "gpclient-off.py", FAKE_GPCLIENT)
        plugin = _plugin(service_module, monkeypatch, fake, mode="false")

        asyncio.run(_connect_and_wait(plugin))

        assert plugin._openssl_error_seen is True
        assert plugin._openssl_retried is False
        assert ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED) in (
            dbus_signals
        )

    def test_flag_passed_from_the_start(
        self, service_module, monkeypatch, tmp_path, dbus_signals
    ):
        fake = _write_fake(tmp_path, "gpclient-forced.py", FAKE_GPCLIENT)
        plugin = _plugin(service_module, monkeypatch, fake, mode="true")

        asyncio.run(_connect_and_wait(plugin))

        # No failed first attempt at all
        assert plugin._openssl_retried is False
        assert plugin._openssl_error_seen is False
        assert any("--fix-openssl" in line for line in plugin._recent_lines)
        # The stand-in asserts the flag comes before the subcommand and exits 0,
        # so a clean run (no Failure) also proves the flag position
        assert dbus_signals == []


class TestPersistingTheWorkaround:
    """After the first occurrence the profile remembers it, so the connection
    editor shows the checkbox ticked and the next connect skips the failure."""

    def _plugin_with_recorder(self, service_module, retried=True, mode="auto"):
        plugin = service_module.GpclientVPNPlugin()
        plugin._connection_uuid = "e5b3e5b3-0000-0000-0000-000000000000"
        plugin._openssl_retried = retried
        plugin.fix_openssl = retried
        plugin.fix_openssl_mode = mode

        written = []

        async def record(key, value):
            written.append((key, value))
            return True

        plugin._write_vpn_data = record
        return plugin, written

    def test_stored_after_a_successful_retry(self, service_module):
        plugin, written = self._plugin_with_recorder(service_module)

        asyncio.run(plugin._persist_fix_openssl())

        assert written == [("fix-openssl", "true")]
        assert plugin.fix_openssl_mode == "true"

    def test_not_stored_without_a_retry(self, service_module):
        plugin, written = self._plugin_with_recorder(service_module, retried=False)

        asyncio.run(plugin._persist_fix_openssl())

        assert written == []

    def test_not_stored_twice(self, service_module):
        plugin, written = self._plugin_with_recorder(service_module, mode="true")

        asyncio.run(plugin._persist_fix_openssl())

        assert written == []

    def test_nothing_written_without_a_uuid(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        plugin._openssl_retried = True
        plugin.fix_openssl = True

        async def fail(*_args, **_kwargs):
            raise AssertionError("nmcli must not be called")

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fail
        try:
            asyncio.run(plugin._persist_fix_openssl())
        finally:
            asyncio.create_subprocess_exec = original


class TestFailureStateTransition:
    def test_login_failure_also_stops(self, service_module, dbus_signals):
        plugin = service_module.GpclientVPNPlugin()
        plugin._fail_login("no way to ask for the token")

        assert dbus_signals == [
            ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_LOGIN_FAILED),
            ("StateChanged", service_module.NM_VPN_SERVICE_STATE_STOPPED),
        ]


class TestOpensslErrorDetection:
    def test_matches_the_openssl_error(self, service_module):
        line = (
            "[2026-07-27T15:07:51Z WARN  gpapi::portal::prelogin] Network error: "
            "error:0A000152:SSL routines:final_renegotiate:unsafe legacy "
            "renegotiation disabled"
        )
        assert service_module.OPENSSL_LEGACY_ERROR_RE.search(line)

    def test_matches_the_hint_line(self, service_module):
        assert service_module.OPENSSL_LEGACY_ERROR_RE.search(
            "Re-run it with the `--fix-openssl` option to work around this issue"
        )

    def test_ignores_ordinary_output(self, service_module):
        for line in (
            "[INFO  gpclient::cli] gpclient started: 2.5.1",
            "Error: error sending request for url (https://portal/prelogin.esp)",
            "SSL negotiation with vpn.example.com",
        ):
            assert not service_module.OPENSSL_LEGACY_ERROR_RE.search(line)
