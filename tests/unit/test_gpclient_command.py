"""
Tests for the gpclient command line the service builds (issue #34).

gpclient 3.x has modes the service cannot handle (it reads the output of a
foreground `connect` under a PTY): background and dashboard mode, other log
formats, cookie-only output, and the `manual`/`remote` browsers that wait for
input the service never sends. The command must never ask for them.

The real command is captured: a stand-in for gpclient writes its argv to a file.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import json
import sys

import pytest

FAKE_GPCLIENT = r'''
import json, sys
with open("__ARGV_FILE__", "w") as out:
    json.dump(sys.argv[1:], out)
sys.exit(0)
'''

UNSUPPORTED_FLAGS = [
    "--background",
    "--dashboard",
    "--log-format",
    "--log-file",
    "--cookie-only",
]

WRAPPER = "/usr/libexec/gpclient/browser-wrapper"


def _command(service_module, monkeypatch, tmp_path, browser_setting="", **settings):
    """The argv gpclient gets for a plugin with `settings`, after the global
    flags and subcommand the service puts in front"""
    argv_file = tmp_path / "argv.json"
    script = tmp_path / "gpclient.py"
    script.write_text(FAKE_GPCLIENT.replace("__ARGV_FILE__", str(argv_file)))
    launcher = tmp_path / "gpclient.sh"
    launcher.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{script}" "$@"\n')
    launcher.chmod(0o755)

    wrapper = tmp_path / "browser-wrapper"
    wrapper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(service_module, "GPCLIENT_BINARY", str(launcher))
    monkeypatch.setattr(service_module, "BROWSER_WRAPPER", str(wrapper))

    plugin = service_module.GpclientVPNPlugin()
    plugin.gateway = "portal.example.com"
    plugin.browser, plugin.browser_target = service_module.resolve_browser(
        browser_setting
    )
    for name, value in settings.items():
        setattr(plugin, name, value)

    async def run():
        assert await plugin._start_gpclient()
        await asyncio.wait_for(plugin.stdout_monitor_task, 10)

    asyncio.run(run())
    return json.loads(argv_file.read_text()), str(wrapper)


TYPICAL = dict(
    fix_openssl=True,
    hip_enabled=True,
    as_gateway=True,
    vpn_username="alice",
)


def test_connect_command_passes_expected_flags(service_module, monkeypatch, tmp_path):
    argv, wrapper = _command(
        service_module, monkeypatch, tmp_path, browser_setting="firefox", **TYPICAL
    )

    assert argv.index("--fix-openssl") < argv.index("connect")
    for flag in ("--hip", "--as-gateway"):
        assert argv.index("connect") < argv.index(flag)
    assert "--user=alice" in argv
    assert f"--browser={wrapper}" in argv
    assert argv[-2:] == ["--", "portal.example.com"]


def test_connect_command_omits_optional_flags_by_default(
    service_module, monkeypatch, tmp_path
):
    argv, wrapper = _command(
        service_module,
        monkeypatch,
        tmp_path,
        fix_openssl=False,
        hip_enabled=False,
        as_gateway=False,
        vpn_username="",
    )

    for flag in ("--fix-openssl", "--hip", "--as-gateway", "--user"):
        assert flag not in argv
    assert argv == ["connect", f"--browser={wrapper}", "--", "portal.example.com"]


BROWSER_SETTINGS = [
    "",
    "edge",
    "firefox",
    "default",
    "/usr/bin/google-chrome",
    "manual",
    "remote",
    "auto",
]


@pytest.mark.parametrize("flag", UNSUPPORTED_FLAGS)
@pytest.mark.parametrize("browser_setting", BROWSER_SETTINGS)
@pytest.mark.parametrize(
    "settings",
    [
        TYPICAL,
        dict(fix_openssl=False, hip_enabled=False, as_gateway=False, vpn_username=""),
        dict(fix_openssl=True, hip_enabled=True, as_gateway=False, vpn_username="bob"),
    ],
)
def test_connect_command_never_uses_unsupported_modes(
    service_module, monkeypatch, tmp_path, flag, browser_setting, settings
):
    argv, wrapper = _command(
        service_module, monkeypatch, tmp_path, browser_setting, **settings
    )

    assert not any(arg == flag or arg.startswith(flag + "=") for arg in argv)
    # The wrapper starts the browser; gpclient never waits for a manual or a
    # remote callback
    assert f"--browser={wrapper}" in argv
    assert "manual" not in argv
    assert "remote" not in argv


def test_custom_absolute_wrapper_is_passed_as_single_token(
    service_module, monkeypatch, tmp_path
):
    argv, _ = _command(
        service_module, monkeypatch, tmp_path, "/opt/corp/my-wrapper", **TYPICAL
    )

    assert "--browser=/opt/corp/my-wrapper" in argv
    assert argv[-2:] == ["--", "portal.example.com"]


HOSTILE_BROWSERS = [
    "--script=/tmp/x.sh",
    "-S",
    "--hip=/tmp/x",
    "relative/wrapper",
    "x --script=/tmp/x",
    "/opt/wrapper --script=/tmp/x",
    "--browser",
]


@pytest.mark.parametrize("browser_setting", HOSTILE_BROWSERS)
def test_hostile_browser_never_becomes_an_option(
    service_module, monkeypatch, tmp_path, browser_setting
):
    argv, wrapper = _command(
        service_module, monkeypatch, tmp_path, browser_setting, **TYPICAL
    )

    assert f"--browser={wrapper}" in argv
    assert not any(a.startswith(("--script", "-S", "--hip=")) for a in argv)
    assert argv.count("--hip") == 1
    assert argv[-2:] == ["--", "portal.example.com"]
    assert not any(a == browser_setting for a in argv)


@pytest.mark.parametrize("browser_setting", HOSTILE_BROWSERS)
def test_hostile_browser_uses_default_target(service_module, tmp_path, monkeypatch, browser_setting):
    wrapper = tmp_path / "browser-wrapper"
    wrapper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(service_module, "BROWSER_WRAPPER", str(wrapper))

    assert service_module.resolve_browser(browser_setting) == (str(wrapper), "default")


@pytest.mark.parametrize("browser_setting", HOSTILE_BROWSERS)
def test_hostile_browser_never_starts_with_dash_without_wrapper(
    service_module, tmp_path, monkeypatch, browser_setting
):
    monkeypatch.setattr(service_module, "BROWSER_WRAPPER", str(tmp_path / "missing"))

    browser, target = service_module.resolve_browser(browser_setting)

    assert not browser.startswith("-")
    assert target is None


@pytest.mark.parametrize("value", ["/opt/corp/my-wrapper", "/usr/local/bin/gp-browser.sh"])
def test_resolve_browser_keeps_custom_absolute_path(service_module, value):
    assert service_module.resolve_browser(value) == (value, None)


@pytest.mark.parametrize("value", ["brave-browser", "opera", "safari"])
def test_bare_browser_name_is_passed_as_single_token(
    service_module, monkeypatch, tmp_path, value
):
    argv, _ = _command(service_module, monkeypatch, tmp_path, value, **TYPICAL)

    assert f"--browser={value}" in argv
    assert value not in argv
    assert argv[-2:] == ["--", "portal.example.com"]


@pytest.mark.parametrize("username", ["--script=/tmp/x.sh", "-x", "--hip=/tmp/x"])
def test_username_is_never_a_standalone_option(
    service_module, monkeypatch, tmp_path, username
):
    argv, _ = _command(
        service_module, monkeypatch, tmp_path, vpn_username=username
    )

    assert f"--user={username}" in argv
    assert username not in argv
    assert argv[-2:] == ["--", "portal.example.com"]


def _drive_connect(service_module, monkeypatch, data):
    """Run _do_connect with `data` as vpn.data; return (error, gpclient starts)"""
    plugin = service_module.GpclientVPNPlugin()
    starts = []

    async def fake_start():
        starts.append(plugin.gateway)
        return True

    async def nothing(*args, **kwargs):
        return None
    async def idle():
        return None

    monkeypatch.setattr(plugin, "_start_gpclient", fake_start)
    monkeypatch.setattr(plugin, "_cleanup_stale_gpd0", nothing)
    monkeypatch.setattr(plugin, "_snapshot_tunnel_interfaces", nothing)
    monkeypatch.setattr(plugin, "_check_tunnel_loop", idle)

    async def run():
        try:
            await plugin._do_connect({"vpn": {"data": data}}, interactive=False)
        except Exception as e:
            return e
        return None

    return asyncio.run(run()), starts


def test_do_connect_accepts_plain_gateway_and_username(service_module, monkeypatch):
    error, starts = _drive_connect(
        service_module,
        monkeypatch,
        {"gateway": "portal.example.com", "username": "alice"},
    )

    assert error is None
    assert starts == ["portal.example.com"]


@pytest.mark.parametrize(
    "gateway", [" vpn.example.com\n", "\tvpn.example.com", "vpn.example.com  "]
)
def test_do_connect_strips_spaces_around_the_gateway(
    service_module, monkeypatch, gateway
):
    error, starts = _drive_connect(service_module, monkeypatch, {"gateway": gateway})

    assert error is None
    assert starts == ["vpn.example.com"]


@pytest.mark.parametrize(
    "data",
    [
        {"gateway": "--script=/tmp/x"},
        {"gateway": "-x"},
        {"gateway": "portal.example.com --script=/tmp/x"},
        {"gateway": "portal.example\ncom"},
        {"gateway": "portal.example.com\tx"},
        {"gateway": " \n"},
        {"gateway": " -x"},
        {"gateway": "portal.example.com", "username": "-x"},
        {"gateway": "portal.example.com", "username": "--script=/tmp/x"},
        {"gateway": "portal.example.com", "username": "al\nice"},
    ],
)
def test_do_connect_rejects_option_like_values(service_module, monkeypatch, data):
    error, starts = _drive_connect(service_module, monkeypatch, data)

    assert error is not None
    assert starts == []


# --- MTU (vpn.data mtu -> gpclient connect --mtu) ---------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("576", 576),
        ("1300", 1300),
        ("1500", 1500),
        (" \t1400\n", 1400),
        ("01300", 1300),
        ("\v1300\v", 1300),
        ("\f1300\r", 1300),
        ("0" * 50 + "1300", 1300),
        ("500", 576),  # raised, like openconnect does
        ("1", 576),
        ("575", 576),
        # automatic
        ("", 0),
        ("   ", 0),
        ("0", 0),
        ("00", 0),
        ("0000", 0),
        ("\v", 0),
        (" 0 ", 0),
        ("0" * 50, 0),
    ],
)
def test_parse_mtu_accepts_the_rule(service_module, text, expected):
    assert service_module.parse_mtu(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1501",
        "99999999999999999999",
        "9" * 5000,
        "-1400",
        "+1300",
        "1400.0",
        "13 00",
        "0 0",
        "0\t0",
        "abc",
        "1400mtu",
        "0x578",
        "\u0661\u0663\u0660\u0660",  # Arabic-Indic digits
        "\u00a01300",  # no-break space: not stripped
        "\u20031300",  # em space: not stripped
    ],
)
def test_parse_mtu_gives_none_for_invalid(service_module, text):
    assert service_module.parse_mtu(text) is None


def test_connect_command_passes_mtu_before_browser(
    service_module, monkeypatch, tmp_path
):
    argv, wrapper = _command(
        service_module, monkeypatch, tmp_path, mtu=1300, hip_enabled=False
    )

    assert argv[argv.index("--mtu") + 1] == "1300"
    assert argv.index("connect") < argv.index("--mtu")
    assert argv.index("--mtu") < argv.index(f"--browser={wrapper}")


@pytest.mark.parametrize("mtu", [None, 0])
def test_connect_command_has_no_mtu_when_automatic(
    service_module, monkeypatch, tmp_path, mtu
):
    argv, wrapper = _command(
        service_module, monkeypatch, tmp_path, mtu=mtu, hip_enabled=False
    )

    assert "--mtu" not in argv


def _do_connect(service_module, monkeypatch, vpn_data):
    """Run the connect path with `vpn_data`; gpclient is not started"""
    plugin = service_module.GpclientVPNPlugin()

    async def nothing(*args, **kwargs):
        return None

    async def started():
        return True

    async def idle():
        pass

    plugin._cleanup_stale_gpd0 = nothing
    plugin._snapshot_tunnel_interfaces = nothing
    plugin._start_gpclient = started
    plugin._check_tunnel_loop = idle

    async def run():
        data = {"gateway": "portal.example.com", **vpn_data}
        await plugin._do_connect({"vpn": {"data": ("a{ss}", data)}}, False)

    asyncio.run(run())
    return plugin


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({"mtu": "1300"}, 1300),
        ({"mtu": " 576 "}, 576),
        ({"mtu": "01300"}, 1300),
        ({"mtu": "500"}, 576),
    ],
)
def test_connect_reads_a_valid_mtu(service_module, monkeypatch, caplog, stored, expected):
    caplog.set_level("INFO")

    plugin = _do_connect(service_module, monkeypatch, stored)

    assert plugin.mtu == expected
    assert f"MTU: {expected}" in caplog.text
    assert "Invalid mtu" not in caplog.text


@pytest.mark.parametrize(
    "stored",
    [{}, {"mtu": ""}, {"mtu": "0"}, {"mtu": " 0 "}, {"mtu": "00"}, {"mtu": "0000"}],
)
def test_connect_mtu_automatic_logs_no_warning(
    service_module, monkeypatch, caplog, stored
):
    caplog.set_level("INFO")

    plugin = _do_connect(service_module, monkeypatch, stored)

    assert plugin.mtu is None
    assert "MTU: automatic" in caplog.text
    assert "Invalid mtu" not in caplog.text


@pytest.mark.parametrize(
    "bad",
    ["1501", "abc", "-1400", "+1300", "1400.0", "13 00", "0 0", "99999999999", "\u0661\u0663\u0660\u0660"],
)
def test_connect_ignores_an_invalid_mtu_with_a_warning(
    service_module, monkeypatch, caplog, bad
):
    caplog.set_level("INFO")

    plugin = _do_connect(service_module, monkeypatch, {"mtu": bad})

    assert plugin.mtu is None
    assert "Invalid mtu" in caplog.text
    assert "MTU: automatic" in caplog.text
