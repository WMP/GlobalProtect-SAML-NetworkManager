"""
Tests for one-time codes and for escape sequences split across reads.

Both come from the #2 report, where a stored passcode was handed back without
asking anyone (so the gateway rejected the login) and a prompt label arrived as
"[39m Password" because a colour sequence straddled a read boundary.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import os
import subprocess
import sys

DIALOG = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "auth-dialog", "nm-gpclient-auth-dialog.py"
    )
)
SERVICE_NAME = "org.freedesktop.NetworkManager.gpclient"


def run_dialog(hints, secrets, interaction=False):
    args = [
        sys.executable,
        DIALOG,
        "-u",
        "e5b3e5b3-0000-0000-0000-000000000000",
        "-n",
        "Work VPN",
        "-s",
        SERVICE_NAME,
    ]
    if interaction:
        args.append("-i")
    for hint in hints:
        args.extend(["-t", hint])

    lines = ["DATA_KEY=gateway", "DATA_VAL=vpn.example.com"]
    for key, value in secrets.items():
        lines.append(f"SECRET_KEY={key}")
        lines.append(f"SECRET_VAL={value}")
    lines += ["DONE", "QUIT"]

    return subprocess.run(
        args, input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=20
    )


class TestStoredOneTimeCode:
    def test_stored_passcode_is_never_handed_back(self):
        """The bug behind "worked once, then every connection failed": the
        dialog answered from the saved code, so the gateway saw a reused one."""
        result = run_dialog(
            ["x-vpn-message:Enter Your 6 Digit Passcode", "otp"],
            {"otp": "498874"},
        )

        assert result.returncode == 1
        assert "498874" not in result.stdout

    def test_stored_password_is_still_reused(self):
        result = run_dialog(["password"], {"password": "s3cret"})

        assert result.returncode == 0
        assert result.stdout == "password\ns3cret\n\n\n"


class TestForgetOneTimeSecret:
    def _plugin(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        plugin._connection_uuid = "e5b3e5b3-0000-0000-0000-000000000000"

        calls = []

        async def record(*arguments):
            calls.append(arguments)
            return True

        plugin._nmcli_modify = record
        return plugin, calls

    def test_marks_not_saved_and_drops_the_value(self, service_module):
        plugin, calls = self._plugin(service_module)

        asyncio.run(plugin._forget_one_time_secret())

        assert calls == [
            ("+vpn.data", "otp-flags=2"),
            ("-vpn.secrets", "otp"),
        ]

    def test_only_done_once_per_connection(self, service_module):
        plugin, calls = self._plugin(service_module)

        asyncio.run(plugin._forget_one_time_secret())
        asyncio.run(plugin._forget_one_time_secret())

        assert len(calls) == 2  # from the first call only

    def test_nothing_without_a_uuid(self, service_module):
        plugin, calls = self._plugin(service_module)
        plugin._connection_uuid = ""

        asyncio.run(plugin._forget_one_time_secret())

        assert calls == []


class TestSplitEscapeSequences:
    def test_sequence_split_across_reads_does_not_leak(self, service_module):
        plugin = service_module.GpclientVPNPlugin()

        # "\x1b[39m? Password: " arriving in two reads, cut inside the sequence
        assert plugin._consume_output("\x1b[3") == []
        lines = plugin._consume_output("9m? Password: \r\n")

        assert lines == ["? Password: "]
        assert service_module.detect_prompt(lines[0]) == "Password"

    def test_label_is_clean_for_the_real_sequence_from_the_report(
        self, service_module
    ):
        plugin = service_module.GpclientVPNPlugin()

        # The report shows "?[39m Password" - the prefix and the colour code
        # arriving separately
        plugin._consume_output("? \x1b[")
        lines = plugin._consume_output("39mPassword: \r\n")

        assert lines == ["? Password: "]
        assert service_module.detect_prompt(lines[0]) == "Password"

    def test_complete_sequence_is_not_held_back(self, service_module):
        text = "\x1b[39m? Password: "
        assert service_module.INCOMPLETE_ANSI_RE.search(text) is None

    def test_lone_escape_is_held_back(self, service_module):
        match = service_module.INCOMPLETE_ANSI_RE.search("hello\x1b")
        assert match and match.group(0) == "\x1b"
