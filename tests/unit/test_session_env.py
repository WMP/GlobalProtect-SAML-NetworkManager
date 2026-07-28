"""
Tests for collecting the user's session environment (issue #7, hardened in #2).

NetworkManager gives the service no session context, so DISPLAY and friends are
read from the user's running processes. A reporter on a desktop whose session
leader is not on our list ended up with no display at all - hence the fallback
that scans every process the user owns.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os

import pytest

HAS_SESSION = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
needs_session = pytest.mark.skipif(
    not HAS_SESSION, reason="no graphical session in this environment"
)


class TestSessionEnv:
    @needs_session
    def test_finds_a_display_on_this_machine(self, service_module):
        plugin = service_module.GpclientVPNPlugin()

        env = plugin._get_session_env(os.getuid(), os.path.expanduser("~"))

        assert env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
        # These two have fallbacks that do not need a session process at all
        assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"
        assert "DBUS_SESSION_BUS_ADDRESS" in env

    @needs_session
    def test_falls_back_to_scanning_all_processes(self, service_module, monkeypatch):
        # Pretend we know no session leaders at all: the display must still be
        # found by scanning the user's processes
        monkeypatch.setattr(
            service_module, "SESSION_LEADER_PROCESSES", ("no-such-process",)
        )
        plugin = service_module.GpclientVPNPlugin()

        env = plugin._get_session_env(os.getuid(), os.path.expanduser("~"))

        assert env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")

    def test_scan_ignores_processes_without_a_display(self, service_module):
        plugin = service_module.GpclientVPNPlugin()

        # A process of another user, and one of ours without any display
        found = plugin._scan_session_env(-1, {})

        assert found == {}

    def test_survives_an_unreadable_proc(self, service_module, monkeypatch):
        def boom(_path):
            raise OSError("nope")

        monkeypatch.setattr(service_module.os, "listdir", boom)
        plugin = service_module.GpclientVPNPlugin()

        assert plugin._scan_session_env(os.getuid(), {}) == {}

    def test_runtime_dir_fallback_without_any_session(
        self, service_module, monkeypatch
    ):
        monkeypatch.setattr(
            service_module, "SESSION_LEADER_PROCESSES", ("no-such-process",)
        )
        monkeypatch.setattr(
            service_module.GpclientVPNPlugin,
            "_scan_session_env",
            lambda self, uid, found: {},
        )
        plugin = service_module.GpclientVPNPlugin()

        env = plugin._get_session_env(os.getuid(), os.path.expanduser("~"))

        # No display, but the runtime dir and bus address are still derivable
        assert "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env
        assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"
