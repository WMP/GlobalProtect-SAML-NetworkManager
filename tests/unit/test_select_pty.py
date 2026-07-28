"""
PTY-level tests for answering gpclient's gateway list (issue #7).

These drive the real output pipeline - OutputScanner, the raw line buffer, the
debounced prompt check and the keystrokes written back - against a stand-in for
gpclient that renders an inquire Select frame and reacts to arrow keys.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import os
import pty
import sys

FAKE_GPCLIENT = r'''
import os, sys, tty

OPTIONS = [
    "gw-warsaw (gw1.example.com)",
    "gw-frankfurt (gw2.example.com)",
    "gw-london (gw3.example.com)",
]


def render(cursor):
    lines = ["? Which gateway do you want to connect to?"]
    for index, option in enumerate(OPTIONS):
        lines.append(("> " if index == cursor else "  ") + option)
    lines.append("[↑↓ to move, enter to select, type to filter]")
    sys.stdout.write("\r\n".join(lines) + "\r\n")
    sys.stdout.flush()


tty.setraw(0)
sys.stdout.write("[INFO  gpclient::cli] gpclient started: fake\r\n")
sys.stdout.flush()

cursor = 0
render(cursor)

pending = b""
while True:
    chunk = os.read(0, 16)
    if not chunk:
        break
    pending += chunk
    while pending:
        if pending.startswith(b"\x1b[B"):
            pending = pending[3:]
            cursor = (cursor + 1) % len(OPTIONS)
            render(cursor)
        elif pending[:1] in (b"\r", b"\n"):
            pending = pending[1:]
            sys.stdout.write(
                "[INFO  gpclient::connect] Connecting to the selected gateway: %s\r\n"
                % OPTIONS[cursor]
            )
            sys.stdout.flush()
            sys.exit(0)
        else:
            pending = pending[1:]
'''


                                                                    # noqa: E501
# Stand-in that renders prompts the way inquire really does: every backend ends
# its rendered line with new_line(), so the prompt arrives as a COMPLETE line
# and nothing is left in the output tail. The service used to look only at the
# tail, so it never saw a prompt from a real gpclient (issue #2 log).
FAKE_TERMINATED_PROMPTS_GPCLIENT = r'''
import os, sys, tty


def read_answer():
    value = b""
    while True:
        chunk = os.read(0, 16)
        if not chunk:
            break
        for byte in chunk:
            if byte in (13, 10):
                return value.decode()
            value += bytes([byte])
    return value.decode()


tty.setraw(0)
sys.stdout.write("[INFO  gpclient::cli] gpclient started: fake\r\n")
sys.stdout.write("Enter login credentials (Portal: portal.example.com)\r\n")
# The prompt line is terminated, exactly like inquire renders it
sys.stdout.write("? Username: \r\n")
sys.stdout.flush()
user = read_answer()
sys.stdout.write("? Username: %s\r\n" % user)
sys.stdout.write("? Password: \r\n")
sys.stdout.flush()
password = read_answer()
sys.stdout.write("? Password: %s\r\n" % ("*" * len(password)))
sys.stdout.flush()

if user == "jdoe" and password == "s3cret":
    sys.stdout.write(
        "[INFO  gpclient::connect] Connecting to the only available gateway: "
        "gw-a (a.example.com)\r\n"
    )
else:
    sys.stdout.write("Authentication failure: got %r / %r\r\n" % (user, password))
sys.stdout.flush()
sys.exit(0)
'''

FAKE_CREDENTIALS_GPCLIENT = r'''
import os, sys, tty


def read_answer():
    value = b""
    while True:
        chunk = os.read(0, 16)
        if not chunk:
            break
        for byte in chunk:
            if byte in (13, 10):
                return value.decode()
            value += bytes([byte])
    return value.decode()


def ask(label, secret):
    sys.stdout.write("? %s: " % label)
    sys.stdout.flush()
    value = read_answer()
    # inquire finalises the line after the answer is confirmed
    shown = "*" * len(value) if secret else value
    sys.stdout.write("\r? %s: %s\r\n" % (label, shown))
    sys.stdout.flush()
    return value


tty.setraw(0)
sys.stdout.write("[INFO  gpclient::cli] gpclient started: fake\r\n")
sys.stdout.write("Please enter the login credentials (Portal: vpn.example.com)\r\n")
sys.stdout.flush()

user = ask("Username", False)
password = ask("Password", True)

if user == "jdoe" and password == "s3cret":
    sys.stdout.write(
        "[INFO  gpclient::connect] Connecting to the only available gateway: "
        "gw-a (a.example.com)\r\n"
    )
else:
    sys.stdout.write("Authentication failure: got %r / %r\r\n" % (user, password))
sys.stdout.flush()
sys.exit(0)
'''


async def _run_against_fake(service_module, fake_path, preferred):
    plugin = service_module.GpclientVPNPlugin()
    plugin.preferred_gateway = preferred

    master, slave = pty.openpty()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(fake_path),
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    plugin._pty_master = master
    plugin.gpclient_process = process

    monitor = asyncio.create_task(plugin._monitor_gpclient_output())
    try:
        await asyncio.wait_for(process.wait(), timeout=20)
        await asyncio.wait_for(monitor, timeout=10)
    finally:
        if not monitor.done():
            monitor.cancel()
        if plugin._prompt_task and not plugin._prompt_task.done():
            plugin._prompt_task.cancel()

    return plugin


def _write_fake(tmp_path):
    fake = tmp_path / "fake-gpclient.py"
    fake.write_text(FAKE_GPCLIENT)
    return fake


class TestGatewaySelectionOverPty:
    def test_preferred_gateway_is_selected(self, service_module, tmp_path):
        plugin = asyncio.run(
            _run_against_fake(service_module, _write_fake(tmp_path), "gw-london")
        )

        # The fake echoes what it was told to connect to
        assert any(
            "Connecting to the selected gateway: gw-london (gw3.example.com)" in line
            for line in plugin._recent_lines
        )
        # ...and the whole list ends up in the cache for the profile
        assert plugin._gateway_list[:3] == [
            "gw-warsaw (gw1.example.com)",
            "gw-frankfurt (gw2.example.com)",
            "gw-london (gw3.example.com)",
        ]

    def test_no_preference_takes_the_first_proposal(self, service_module, tmp_path):
        plugin = asyncio.run(
            _run_against_fake(service_module, _write_fake(tmp_path), "")
        )

        assert any(
            "Connecting to the selected gateway: gw-warsaw (gw1.example.com)" in line
            for line in plugin._recent_lines
        )

    def test_unknown_preference_falls_back_to_the_first(self, service_module, tmp_path):
        plugin = asyncio.run(
            _run_against_fake(service_module, _write_fake(tmp_path), "gw-tokyo")
        )

        assert any(
            "Connecting to the selected gateway: gw-warsaw (gw1.example.com)" in line
            for line in plugin._recent_lines
        )
        assert plugin.preferred_gateway == "gw-tokyo"


class TestStoredCredentialsOverPty:
    """Regression for issue #6: the text-prompt flow must still work now that
    the list-prompt check runs first in _schedule_prompt_check()."""

    def _run(self, service_module, fake_path):
        async def scenario():
            plugin = service_module.GpclientVPNPlugin()
            plugin.vpn_username = "jdoe"
            plugin.vpn_password = "s3cret"
            plugin._reset_phase_state()

            master, slave = pty.openpty()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(fake_path),
                stdin=slave,
                stdout=slave,
                stderr=slave,
            )
            os.close(slave)
            plugin._pty_master = master
            plugin.gpclient_process = process

            monitor = asyncio.create_task(plugin._monitor_gpclient_output())
            try:
                await asyncio.wait_for(process.wait(), timeout=20)
                await asyncio.wait_for(monitor, timeout=10)
            finally:
                if not monitor.done():
                    monitor.cancel()
                if plugin._prompt_task and not plugin._prompt_task.done():
                    plugin._prompt_task.cancel()
            return plugin

        return asyncio.run(scenario())

    def test_prompts_terminated_by_inquire_are_answered(
        self, service_module, tmp_path
    ):
        """The real case from the #2 log: inquire ends the prompt line, so the
        prompt is a complete line and the tail is empty."""
        fake = tmp_path / "fake-gpclient-terminated.py"
        fake.write_text(FAKE_TERMINATED_PROMPTS_GPCLIENT)

        plugin = self._run(service_module, fake)

        lines = list(plugin._recent_lines)
        assert not any("Authentication failure" in line for line in lines), (
            "the credentials never reached gpclient - the prompt was not detected"
        )
        assert any("Connecting to the only available gateway" in line for line in lines)

    def test_username_and_password_are_typed_from_the_profile(
        self, service_module, tmp_path
    ):
        fake = tmp_path / "fake-gpclient-credentials.py"
        fake.write_text(FAKE_CREDENTIALS_GPCLIENT)

        async def scenario():
            plugin = service_module.GpclientVPNPlugin()
            plugin.vpn_username = "jdoe"
            plugin.vpn_password = "s3cret"
            plugin._reset_phase_state()

            master, slave = pty.openpty()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(fake),
                stdin=slave,
                stdout=slave,
                stderr=slave,
            )
            os.close(slave)
            plugin._pty_master = master
            plugin.gpclient_process = process

            monitor = asyncio.create_task(plugin._monitor_gpclient_output())
            try:
                await asyncio.wait_for(process.wait(), timeout=20)
                await asyncio.wait_for(monitor, timeout=10)
            finally:
                if not monitor.done():
                    monitor.cancel()
                if plugin._prompt_task and not plugin._prompt_task.done():
                    plugin._prompt_task.cancel()
            return plugin

        plugin = asyncio.run(scenario())

        lines = list(plugin._recent_lines)
        assert not any("Authentication failure" in line for line in lines)
        assert any("Connecting to the only available gateway" in line for line in lines)
        # The gateway from the log line is cached too, even without a list prompt
        assert plugin._gateway_list == ["gw-a (a.example.com)"]


class TestPressListDown:
    def test_down_key_is_sent_and_the_redraw_is_awaited(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        sent = []
        plugin._write_keys = lambda data, description: sent.append(data)

        first = [
            "? Which gateway do you want to connect to?",
            "> gw-a (a.example.com)",
            "  gw-b (b.example.com)",
            "[to move, to select]",
        ]
        second = [
            "? Which gateway do you want to connect to?",
            "  gw-a (a.example.com)",
            "> gw-b (b.example.com)",
            "[to move, to select]",
        ]
        plugin._recent_lines.extend(first)

        async def scenario():
            async def redraw_later():
                await asyncio.sleep(0.1)
                plugin._recent_lines.extend(second)

            asyncio.create_task(redraw_later())
            return await plugin._press_list_down()

        frame = asyncio.run(scenario())

        assert sent == [service_module.KEY_DOWN]
        assert frame["cursor"] == 1

    def test_no_redraw_gives_up(self, service_module, monkeypatch):
        monkeypatch.setattr(service_module, "SELECT_REDRAW_TIMEOUT", 0.2)
        plugin = service_module.GpclientVPNPlugin()
        plugin._write_keys = lambda data, description: None
        plugin._recent_lines.extend(
            [
                "? Which gateway do you want to connect to?",
                "> gw-a (a.example.com)",
                "[to move, to select]",
            ]
        )

        assert asyncio.run(plugin._press_list_down()) is None
