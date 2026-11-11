"""
Tests for the custom HIP executable (issue #65): the `hip-script` setting of the
profile, passed to gpclient as `--hip=<path>`.

The service runs gpclient, and so the script, as root. The service checks that
only a trusted user (root) can change the script or the directories above it.
The tests use the current user as a trusted user too, so that they run as root
and as a normal user.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import os
import stat

import pytest

from test_dbus_methods import FakeProc, _conn, _plugin, connect
from test_gpclient_command import _command

TRUSTED = (0, os.getuid())


@pytest.fixture
def script(tmp_path, monkeypatch, service_module):
    """A HIP script that passes every check, in a directory that does too

    The directories above tmp_path (like /tmp) belong to the host and are often
    world-writable, so they are shown as not writable by group or others.
    """
    real_stat = os.stat
    above = set()
    parent = os.path.dirname(os.path.realpath(tmp_path))
    while parent not in above:
        above.add(parent)
        parent = os.path.dirname(parent)

    class Closed:
        def __init__(self, info):
            self.st_mode = info.st_mode & ~(stat.S_IWGRP | stat.S_IWOTH)
            self.st_uid = info.st_uid

    def fake_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if isinstance(path, str) and path in above:
            return Closed(info)
        return info

    monkeypatch.setattr(service_module.os, "stat", fake_stat)
    directory = tmp_path / "bin"
    directory.mkdir()
    directory.chmod(0o755)
    path = directory / "hip.sh"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


# --- validate_hip_script -----------------------------------------------------


def test_valid_script_is_accepted(service_module, script):
    result = service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)

    assert result == os.path.realpath(script)


def test_symlink_resolves_to_the_target(service_module, script, tmp_path):
    link = tmp_path / "link.sh"
    link.symlink_to(script)

    result = service_module.validate_hip_script(str(link), trusted_uids=TRUSTED)

    assert result == os.path.realpath(script)
    assert result != str(link)


@pytest.mark.parametrize("mode", [0o1777, 0o1775, 0o1757])
def test_sticky_writable_directory_is_rejected(service_module, script, mode):
    # The sticky bit does not make a group or world-writable directory safe
    script.parent.chmod(mode)

    with pytest.raises(ValueError, match="directory .* writable by group or others"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


def test_default_trusted_user_is_root_only(service_module, script):
    if os.getuid() == 0:
        pytest.skip("the owner is root, a trusted user")

    with pytest.raises(ValueError, match="untrusted owner"):
        service_module.validate_hip_script(str(script))


@pytest.mark.parametrize(
    "path", ["hip.sh", "./hip.sh", "../bin/hip.sh", "~/hip.sh", ""]
)
def test_relative_path_is_rejected(service_module, path):
    with pytest.raises(ValueError, match="not absolute"):
        service_module.validate_hip_script(path, trusted_uids=TRUSTED)


def test_missing_file_is_rejected(service_module, script):
    with pytest.raises(ValueError, match="cannot read"):
        service_module.validate_hip_script(
            str(script.parent / "missing.sh"), trusted_uids=TRUSTED
        )


def test_directory_is_rejected(service_module, script):
    with pytest.raises(ValueError, match="not a regular file"):
        service_module.validate_hip_script(str(script.parent), trusted_uids=TRUSTED)


def test_not_executable_is_rejected(service_module, script):
    script.chmod(0o644)

    with pytest.raises(ValueError, match="not executable"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777, 0o770])
def test_file_writable_by_group_or_others_is_rejected(service_module, script, mode):
    script.chmod(mode)

    with pytest.raises(ValueError, match="writable by group or others"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


def test_untrusted_file_owner_is_rejected(service_module, script):
    with pytest.raises(ValueError, match="untrusted owner"):
        service_module.validate_hip_script(
            str(script), trusted_uids=(os.getuid() + 12345,)
        )


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777, 0o770])
def test_directory_writable_by_group_or_others_is_rejected(
    service_module, script, mode
):
    script.parent.chmod(mode)

    with pytest.raises(ValueError, match="directory .* writable by group or others"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


def test_ancestor_directory_writable_is_rejected(service_module, script, tmp_path):
    # Not only the parent: every directory up to "/" counts
    tmp_path.chmod(0o777)

    with pytest.raises(ValueError, match="directory .* writable by group or others"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


def test_untrusted_directory_owner_is_rejected(service_module, script, monkeypatch):
    # The file is owned by a trusted user, the directory above it is not
    real_stat = os.stat
    parent = str(script.parent)

    class Owned:
        def __init__(self, info):
            self.st_mode = info.st_mode
            self.st_uid = os.getuid() + 12345

    def fake_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        return Owned(info) if path == parent else info

    monkeypatch.setattr(service_module.os, "stat", fake_stat)

    with pytest.raises(ValueError, match="directory .* untrusted owner"):
        service_module.validate_hip_script(str(script), trusted_uids=TRUSTED)


@pytest.mark.parametrize("mode", [0o777, 0o666])
def test_symlink_to_a_bad_target_is_rejected(service_module, script, tmp_path, mode):
    link = tmp_path / "link.sh"
    link.symlink_to(script)
    script.chmod(mode | 0o111)

    with pytest.raises(ValueError, match="writable by group or others"):
        service_module.validate_hip_script(str(link), trusted_uids=TRUSTED)


def test_symlink_in_a_bad_directory_is_checked_at_the_target(
    service_module, script, tmp_path
):
    # The link sits in a safe place, the target's directory is not
    link = tmp_path / "link.sh"
    link.symlink_to(script)
    script.parent.chmod(0o777)

    with pytest.raises(ValueError, match="directory .* writable by group or others"):
        service_module.validate_hip_script(str(link), trusted_uids=TRUSTED)


# --- the gpclient command line -----------------------------------------------


def test_hip_script_is_one_argv_item(service_module, monkeypatch, tmp_path):
    argv, _ = _command(
        service_module,
        monkeypatch,
        tmp_path,
        hip_enabled=True,
        hip_script="/opt/gp/hip.sh",
    )

    assert "--hip=/opt/gp/hip.sh" in argv
    assert "--hip" not in argv
    assert "/opt/gp/hip.sh" not in argv
    assert argv.index("connect") < argv.index("--hip=/opt/gp/hip.sh")
    assert argv[-1] == "portal.example.com"


def test_no_hip_script_gives_bare_hip(service_module, monkeypatch, tmp_path):
    argv, _ = _command(
        service_module, monkeypatch, tmp_path, hip_enabled=True, hip_script=None
    )

    assert "--hip" in argv
    assert not [arg for arg in argv if arg.startswith("--hip=")]


def test_hip_script_is_not_passed_when_hip_is_off(service_module, monkeypatch, tmp_path):
    argv, _ = _command(
        service_module,
        monkeypatch,
        tmp_path,
        hip_enabled=False,
        hip_script="/opt/gp/hip.sh",
    )

    assert not [arg for arg in argv if arg.startswith("--hip")]
    assert "/opt/gp/hip.sh" not in argv


# --- Connect -----------------------------------------------------------------


def test_connect_passes_the_resolved_script(service_module, monkeypatch, script, tmp_path):
    link = tmp_path / "link.sh"
    link.symlink_to(script)
    real = service_module.validate_hip_script
    monkeypatch.setattr(
        service_module,
        "validate_hip_script",
        lambda path: real(path, trusted_uids=TRUSTED),
    )
    plugin = _plugin(service_module, monkeypatch)

    connect(plugin, _conn(data={"gateway": "g", "hip-script": f"  {link}  "}))

    assert plugin.hip_script == os.path.realpath(script)
    assert "start_gpclient" in plugin.calls


@pytest.mark.parametrize("value", ["", "   "])
def test_connect_without_hip_script_uses_bare_hip(service_module, monkeypatch, value):
    plugin = _plugin(service_module, monkeypatch)
    plugin.hip_script = "/stale"

    connect(plugin, _conn(data={"gateway": "g", "hip-script": value}))

    assert plugin.hip_script is None


@pytest.mark.parametrize(
    "value", ["relative.sh", "/nonexistent/hip.sh", "/tmp", "/etc/passwd"]
)
def test_connect_with_an_invalid_hip_script_fails(
    service_module, monkeypatch, dbus_signals, value
):
    plugin = _plugin(service_module, monkeypatch)

    with pytest.raises(Exception, match="Invalid hip-script"):
        connect(plugin, _conn(data={"gateway": "g", "hip-script": value}))

    assert "start_gpclient" not in plugin.calls
    assert plugin.hip_script is None
    assert ("Failure", service_module.NM_VPN_PLUGIN_FAILURE_CONNECT_FAILED) in dbus_signals


def test_connect_with_an_unsafe_script_names_path_and_reason(
    service_module, monkeypatch, script, caplog
):
    script.chmod(0o777)
    real = service_module.validate_hip_script
    monkeypatch.setattr(
        service_module,
        "validate_hip_script",
        lambda path: real(path, trusted_uids=TRUSTED),
    )
    plugin = _plugin(service_module, monkeypatch)

    with caplog.at_level("ERROR"):
        with pytest.raises(Exception, match="writable by group or others"):
            connect(plugin, _conn(data={"gateway": "g", "hip-script": str(script)}))

    assert str(script) in caplog.text
    assert "start_gpclient" not in plugin.calls


@pytest.mark.parametrize("value", ["false", "FALSE", "0"])
def test_connect_with_hip_off_ignores_the_script(service_module, monkeypatch, value):
    plugin = _plugin(service_module, monkeypatch)

    # The path does not exist: nothing may check it
    connect(
        plugin,
        _conn(data={"gateway": "g", "hip": value, "hip-script": "/nonexistent/x.sh"}),
    )

    assert plugin.hip_enabled is False
    assert plugin.hip_script is None
    assert "start_gpclient" in plugin.calls


def test_new_connect_forgets_the_previous_script(service_module, monkeypatch):
    plugin = _plugin(service_module, monkeypatch)
    plugin.hip_script = "/old/hip.sh"

    connect(plugin, _conn())

    assert plugin.hip_script is None


def test_init_has_no_hip_script(service_module):
    assert service_module.GpclientVPNPlugin().hip_script is None


def test_disconnect_resets_the_script(service_module, monkeypatch):
    plugin = _plugin(service_module, monkeypatch)
    plugin.hip_script = "/opt/gp/hip.sh"

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(service_module.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(plugin.Disconnect())

    assert plugin.hip_script is None


# --- private connections ------------------------------------------------------

PRIVATE = [
    ["user:alice:"],
    ["user:alice"],
    ("as", ["user:alice:"]),
    ["user:alice:", "user:bob:"],
    ["group:wheel"],  # unknown, but not empty: fail closed
    ("user:alice:", "user:bob:"),  # a pair of entries, not (signature, value)
    "user:alice:",  # not a list: fail closed
]
SHARED = [[], ("as", []), [""]]


def _no_validation(service_module, monkeypatch):
    def fail(path):
        raise AssertionError("validate_hip_script must not be called")

    monkeypatch.setattr(service_module, "validate_hip_script", fail)


@pytest.mark.parametrize("permissions", PRIVATE)
def test_private_connection_rejects_hip_script(
    service_module, monkeypatch, dbus_signals, caplog, permissions
):
    _no_validation(service_module, monkeypatch)
    plugin = _plugin(service_module, monkeypatch)

    with caplog.at_level("ERROR"):
        with pytest.raises(
            service_module.BadArgumentsError, match="not allowed in a private connection"
        ):
            connect(
                plugin,
                _conn(
                    data={"gateway": "g", "hip-script": "/opt/gp/hip.sh"},
                    connection={"permissions": permissions},
                ),
            )

    assert "not allowed in a private connection" in caplog.text
    assert "start_gpclient" not in plugin.calls
    assert plugin.hip_script is None


@pytest.mark.parametrize("permissions", SHARED)
def test_shared_connection_still_uses_hip_script(
    service_module, monkeypatch, script, permissions
):
    real = service_module.validate_hip_script
    monkeypatch.setattr(
        service_module, "validate_hip_script", lambda p: real(p, trusted_uids=TRUSTED)
    )
    plugin = _plugin(service_module, monkeypatch)

    connect(
        plugin,
        _conn(
            data={"gateway": "g", "hip-script": str(script)},
            connection={"permissions": permissions},
        ),
    )

    assert plugin.hip_script == os.path.realpath(script)
    assert "start_gpclient" in plugin.calls


@pytest.mark.parametrize("permissions", PRIVATE)
def test_private_connection_with_hip_off_ignores_hip_script(
    service_module, monkeypatch, permissions
):
    _no_validation(service_module, monkeypatch)
    plugin = _plugin(service_module, monkeypatch)

    connect(
        plugin,
        _conn(
            data={"gateway": "g", "hip": "false", "hip-script": "/opt/gp/hip.sh"},
            connection={"permissions": permissions},
        ),
    )

    assert plugin.hip_script is None
    assert "start_gpclient" in plugin.calls


@pytest.mark.parametrize("permissions", PRIVATE)
def test_private_connection_without_hip_script_uses_bare_hip(
    service_module, monkeypatch, permissions
):
    _no_validation(service_module, monkeypatch)
    plugin = _plugin(service_module, monkeypatch)

    connect(
        plugin,
        _conn(data={"gateway": "g"}, connection={"permissions": permissions}),
    )

    assert plugin.hip_enabled is True
    assert plugin.hip_script is None
    assert plugin._private_connection is True
    assert "start_gpclient" in plugin.calls


@pytest.mark.parametrize("permissions", SHARED)
def test_shared_connection_is_not_private(service_module, monkeypatch, permissions):
    plugin = _plugin(service_module, monkeypatch)
    plugin._private_connection = True

    connect(plugin, _conn(connection={"permissions": permissions}))

    assert plugin._private_connection is False
