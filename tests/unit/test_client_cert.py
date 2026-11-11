"""
Tests for the client certificate (issue #134): the `usercert` and `userkey` file
paths of the profile and the `cert-pass` secret.

The passphrase never goes on the command line (argv is world-readable). The
service answers the passphrase prompt of gpclient ("Enter the PEM passphrase:",
"Enter the PKCS#12 passphrase:") from the secret, and never types the VPN
password there.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio
import os
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_gpclient_command import _command, _drive_connect
from test_log_redaction import _monitor

PASSPHRASE_PROMPTS = ["Enter the PEM passphrase", "Enter the PKCS#12 passphrase"]
VPN_PASSWORD = "Vp#Password1"
CERT_PASS = "Cert#Pass2"


# A desktop user other than root, when the tests run as root: the file is read
# by a helper that runs as this user, so the user must exist
DESKTOP_UID = pwd.getpwnam("nobody").pw_uid if os.getuid() == 0 else os.getuid()
# Another user that exists (the file is "not owned" by the desktop user)
OTHER_UID = (
    next(p.pw_uid for p in pwd.getpwall() if p.pw_uid not in (0, DESKTOP_UID))
    if os.getuid() == 0
    else DESKTOP_UID + 1
)


def _desktop_user(service_module, monkeypatch, tmp_path, uid):
    """The desktop user is `uid`; the private copies go below tmp_path"""
    monkeypatch.setattr(
        service_module.GpclientVPNPlugin,
        "_get_real_user",
        lambda self: (uid, "me", "/home/me"),
    )
    monkeypatch.setattr(service_module, "CERT_COPY_ROOT", str(tmp_path / "run"))


@pytest.fixture
def me(service_module, monkeypatch, tmp_path):
    """The desktop user: the current user, or DESKTOP_UID when root"""
    _desktop_user(service_module, monkeypatch, tmp_path, DESKTOP_UID)
    return DESKTOP_UID


@pytest.fixture
def udir():
    """A directory of the desktop user's files that the user can enter (tmp_path
    is below a root-only directory when the tests run as root)"""
    path = tempfile.mkdtemp()
    os.chmod(path, 0o711)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def cert_files(udir, me):
    """A certificate and a key owned by the desktop user"""
    cert = Path(udir) / "me.pem"
    key = Path(udir) / "me.key"
    for f, text in ((cert, "cert"), (key, "key")):
        f.write_text(text)
        os.chown(f, me, -1) if os.getuid() == 0 else None
    return str(cert), str(key)


def _copy_of(argv, option):
    (value,) = [a.split("=", 1)[1] for a in argv if a.startswith(option + "=")]
    return value


# --- command line ------------------------------------------------------------


def test_gpclient_gets_private_copies_not_the_paths_of_the_user(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    cert, key = cert_files
    # the fake gpclient exits at once: keep the copies for the checks
    monkeypatch.setattr(service_module.GpclientVPNPlugin, "_emit_failure", lambda *a: None)
    argv, _ = _command(
        service_module, monkeypatch, tmp_path, usercert=cert, userkey=key
    )

    assert cert not in " ".join(argv) and key not in " ".join(argv)
    for option, original, ext in (("--certificate", cert, ".pem"), ("--sslkey", key, ".key")):
        copy = _copy_of(argv, option)
        info = os.stat(copy)
        assert copy.startswith(str(tmp_path / "run") + os.sep) and copy.endswith(ext)
        assert open(copy).read() == open(original).read()
        assert stat.S_IMODE(info.st_mode) == 0o400
        assert info.st_uid == me
        assert argv.index(f"{option}={copy}") < argv.index("--")
    # the directory of the copies: the user cannot write, but can enter it
    mode = stat.S_IMODE(os.stat(os.path.dirname(copy)).st_mode)
    assert mode == 0o711


def test_no_key_option_without_a_key(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    argv, _ = _command(
        service_module, monkeypatch, tmp_path, usercert=cert_files[0]
    )

    assert _copy_of(argv, "--certificate").endswith("usercert.pem")
    assert not any(a.startswith(("--sslkey", "-k")) for a in argv)


def test_no_certificate_options_when_not_set(service_module, monkeypatch, tmp_path):
    argv, _ = _command(service_module, monkeypatch, tmp_path)

    assert not any(a.startswith(("--certificate", "--sslkey")) for a in argv)


@pytest.mark.parametrize("pass_attr", ["cert_pass", "vpn_password"])
def test_neither_the_passphrase_nor_the_vpn_password_is_on_the_command_line(
    service_module, monkeypatch, tmp_path, me, cert_files, pass_attr
):
    argv, _ = _command(
        service_module,
        monkeypatch,
        tmp_path,
        usercert=cert_files[0],
        **{pass_attr: CERT_PASS},
    )

    assert not any("key-password" in a or a == "-p" for a in argv)
    assert CERT_PASS not in " ".join(argv)


# --- the profile -------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        {"usercert": "me.pem"},
        {"usercert": "~/me.pem"},
        {"usercert": "/a/me\n.pem"},
        {"usercert": "/a/me\x00.pem"},
        {"usercert": "/a/me.pem", "userkey": "key.pem"},
        {"usercert": "/a/me.pem", "userkey": "/a/k\r.pem"},
    ],
)
def test_bad_paths_are_rejected(service_module, monkeypatch, data):
    error, starts = _drive_connect(
        service_module, monkeypatch, {"gateway": "portal.example.com", **data}
    )

    assert isinstance(error, service_module.BadArgumentsError)
    assert starts == []


def test_good_paths_are_accepted(service_module, monkeypatch):
    error, starts = _drive_connect(
        service_module,
        monkeypatch,
        {
            "gateway": "portal.example.com",
            "usercert": " /a/me.pem ",
            "userkey": "/a/my key.pem",
        },
    )

    assert error is None
    assert starts == ["portal.example.com"]


@pytest.mark.parametrize(
    "path", ["/a/me.pem", "/a/with space/me.p12", "/a/ü.pem"]
)
def test_validate_cert_path_accepts(service_module, path):
    assert service_module.validate_cert_path("usercert", path) == path


@pytest.mark.parametrize("path", ["", "me.pem", "./me.pem", "/a/\x1b.pem", "/a/\x7f"])
def test_validate_cert_path_rejects(service_module, path):
    with pytest.raises(ValueError, match="usercert must be an absolute path"):
        service_module.validate_cert_path("usercert", path)


def test_key_without_certificate_is_ignored(service_module):
    plugin = service_module.GpclientVPNPlugin()
    connection = {
        "vpn": {
            "data": {"gateway": "g.example.com", "userkey": "/a/k.pem"},
            "secrets": {"cert-pass": CERT_PASS},
        }
    }

    async def run():
        async def nothing(*args, **kwargs):
            return None

        plugin._start_gpclient = lambda: asyncio.sleep(0, True)
        plugin._cleanup_stale_gpd0 = nothing
        await plugin._do_connect(connection, interactive=False)

    asyncio.run(run())

    assert plugin.userkey == ""
    assert plugin.cert_pass == CERT_PASS
    assert CERT_PASS in plugin._written_secrets


# --- prompts -----------------------------------------------------------------


def _answers(service_module, plugin, label):
    """The keys written for the prompt `label`, and the secrets requested"""
    written, asked = [], []

    async def ask(key, label, banner):
        asked.append(key)
        return "from-user"

    plugin._interactive = True
    plugin._answered_username = True
    plugin._request_secret_interactive = ask
    plugin._write_keys = lambda data, description: written.append(data)
    asyncio.run(plugin._handle_prompt(label))
    return written, asked


@pytest.mark.parametrize("label", PASSPHRASE_PROMPTS)
def test_passphrase_prompt_is_the_certificate_passphrase(service_module, label):
    plugin = service_module.GpclientVPNPlugin()
    plugin._answered_username = True

    assert plugin._classify_prompt_kind(label, "") == "cert-pass"
    # also after the password: not an MFA prompt
    plugin._answered_password = True
    assert plugin._classify_prompt_kind(label, "") == "cert-pass"


@pytest.mark.parametrize(
    "label",
    [
        "Password",
        "Enter the passphrase",
        "Enter the PEM passphrase for the VPN",
        "Passphrase",
        "Enter the PEM password",
    ],
)
def test_other_prompts_are_not_the_certificate_passphrase(service_module, label):
    plugin = service_module.GpclientVPNPlugin()
    plugin._answered_username = True

    assert plugin._classify_prompt_kind(label, "") != "cert-pass"


@pytest.mark.parametrize("label", PASSPHRASE_PROMPTS)
def test_stored_vpn_password_is_never_typed_at_the_passphrase_prompt(
    service_module, label
):
    plugin = service_module.GpclientVPNPlugin()
    plugin.vpn_password = VPN_PASSWORD

    written, asked = _answers(service_module, plugin, label)

    assert asked == ["cert-pass"]
    assert written == [b"from-user\r"]
    assert VPN_PASSWORD.encode() not in b"".join(written)
    assert plugin._answered_password is False


def test_password_prompt_is_still_answered_from_the_password(service_module):
    plugin = service_module.GpclientVPNPlugin()
    plugin.vpn_password = VPN_PASSWORD
    plugin.cert_pass = CERT_PASS

    written, asked = _answers(service_module, plugin, "Password")

    assert asked == []
    assert written == [VPN_PASSWORD.encode() + b"\r"]


@pytest.mark.parametrize("label", PASSPHRASE_PROMPTS)
def test_stored_passphrase_answers_once_then_the_user_is_asked(service_module, label):
    plugin = service_module.GpclientVPNPlugin()
    plugin.cert_pass = CERT_PASS

    written, asked = _answers(service_module, plugin, label)
    assert written == [CERT_PASS.encode() + b"\r"]
    assert asked == []

    # gpclient asks again: the stored one was wrong
    written, asked = _answers(service_module, plugin, label)
    assert asked == ["cert-pass"]
    assert written == [b"from-user\r"]


def test_the_passphrase_is_masked_in_the_output(service_module, caplog):
    import os

    plugin = service_module.GpclientVPNPlugin()
    plugin._pty_master = os.open(os.devnull, os.O_WRONLY)
    plugin.cert_pass = CERT_PASS
    plugin._written_secrets.add(CERT_PASS)
    asyncio.run(plugin._handle_prompt("Enter the PEM passphrase"))

    with caplog.at_level("DEBUG", logger=service_module.logger.name):
        asyncio.run(_monitor(plugin, f"? Enter the PEM passphrase: {CERT_PASS}\r\n"))

    assert CERT_PASS not in caplog.text
    assert "? Enter the PEM passphrase: <hidden>" in caplog.text


# --- files read by root (gpclient) ------------------------------------------


def _started(service_module, monkeypatch, tmp_path, **settings):
    """Whether _start_gpclient spawned gpclient (the fake writes argv.json)"""
    try:
        _command(service_module, monkeypatch, tmp_path, **settings)
    except (AssertionError, FileNotFoundError):
        return False
    return True


def _copy(service_module, tmp_path, path, uid, name="usercert"):
    directory = tmp_path / "copies"
    directory.mkdir(exist_ok=True)
    return service_module.copy_cert_file(name, path, uid, str(directory))


def _own(path, uid):
    if os.getuid() == 0:
        os.chown(path, uid, -1)
    return str(path)


def test_a_file_of_the_desktop_user_is_copied(service_module, tmp_path, me, cert_files):
    copy = _copy(service_module, tmp_path, cert_files[0], me)

    assert open(copy).read() == "cert"
    assert stat.S_IMODE(os.stat(copy).st_mode) == 0o400
    assert os.stat(copy).st_uid == me


@pytest.mark.parametrize(
    "name, ext",
    [
        ("a.p12", ".p12"),
        ("a.PFX", ".PFX"),
        ("a.pem", ".pem"),
        ("a", ""),
        ("a.p12;touch x", ""),  # an odd extension is not kept
    ],
)
def test_the_copy_keeps_a_plain_extension(service_module, tmp_path, udir, me, name, ext):
    path = _own(_touch(Path(udir) / name), me)

    copy = _copy(service_module, tmp_path, path, me)

    assert os.path.basename(copy) == "usercert" + ext


def test_a_file_changed_after_the_copy_does_not_change_the_copy(
    service_module, tmp_path, me, cert_files
):
    copy = _copy(service_module, tmp_path, cert_files[0], me)

    os.unlink(cert_files[0])
    os.symlink("/dev/zero", cert_files[0])

    assert open(copy).read() == "cert"


def test_a_fifo_does_not_block(service_module, tmp_path, udir, me):
    fifo = Path(udir) / "fifo"
    os.mkfifo(fifo)
    _own(fifo, me)

    def hung(*_):
        raise AssertionError("opening the FIFO blocked")

    old = signal.signal(signal.SIGALRM, hung)
    signal.alarm(5)
    try:
        with pytest.raises(ValueError, match="not a regular file"):
            _copy(service_module, tmp_path, str(fifo), me)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_the_largest_file_is_copied(service_module, tmp_path, udir, me):
    path = Path(udir) / "max.pem"
    path.write_bytes(b"x" * service_module.CERT_FILE_MAX_BYTES)
    _own(path, me)

    copy = _copy(service_module, tmp_path, str(path), me)

    assert os.path.getsize(copy) == service_module.CERT_FILE_MAX_BYTES


def _big(tmp_path, uid):
    path = tmp_path / "big.pem"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    return _own(path, uid)


@pytest.mark.parametrize(
    "make, uid_shift, word",
    [
        (lambda t, u: "/usr", 0, "not a regular file"),  # a directory
        (lambda t, u: "/dev/zero", 0, "not a regular file"),
        (_big, 0, "larger than"),
        (lambda t, u: _own(_touch(t / "o.pem"), u), OTHER_UID - DESKTOP_UID, "not owned"),  # another owner
        (lambda t, u: str(t / "missing"), 0, "cannot read"),
    ],
)
def test_copy_cert_file_rejects(
    service_module, tmp_path, udir, me, make, uid_shift, word
):
    path = make(Path(udir), me)

    with pytest.raises(ValueError, match=word):
        _copy(service_module, tmp_path, path, me + uid_shift)
    assert not os.listdir(tmp_path / "copies")  # nothing was written


def _touch(path):
    path.write_text("x")
    return path


def test_a_symlink_is_followed_and_the_target_is_checked(
    service_module, tmp_path, udir, me
):
    target = _touch(Path(udir) / "t.pem")
    _own(target, me)
    link = Path(udir) / "l.pem"
    link.symlink_to(target)

    assert open(_copy(service_module, tmp_path, str(link), me)).read() == "x"
    with pytest.raises(ValueError, match="not owned"):
        _copy(service_module, tmp_path, str(link), OTHER_UID)


def _root_only(f):
    return pytest.mark.skipif(os.getuid() != 0, reason="needs root to switch users")(f)


@_root_only
def test_a_file_in_a_directory_the_user_cannot_enter_is_rejected(
    service_module, tmp_path, udir, me
):
    # Root could read it, the user cannot: root must not read it for the user
    hidden = Path(udir) / "hidden"
    hidden.mkdir()
    hidden.chmod(0o700)  # owned by root
    _own(_touch(hidden / "me.pem"), me)

    with pytest.raises(ValueError, match="cannot read"):
        _copy(service_module, tmp_path, str(hidden / "me.pem"), me)
    assert not os.listdir(tmp_path / "copies")


@_root_only
def test_the_helper_runs_as_the_user_not_as_root(service_module, tmp_path, udir, me):
    path = _own(_touch(Path(udir) / "secret.pem"), me)
    os.chmod(path, 0o000)  # root would read it, the owner cannot

    with pytest.raises(ValueError, match="cannot read"):
        _copy(service_module, tmp_path, path, me)


@_root_only
def test_a_user_that_does_not_exist_means_no_file_is_read(service_module, tmp_path, udir):
    uid = max(p.pw_uid for p in pwd.getpwall()) + 1

    with pytest.raises(ValueError, match="no user with uid"):
        _copy(service_module, tmp_path, str(_touch(Path(udir) / "x.pem")), uid)


def test_the_parent_does_not_trust_the_size_the_helper_reports(
    service_module, monkeypatch, tmp_path, udir, me
):
    path = _own(_touch(Path(udir) / "x.pem"), me)
    big = b"x" * (service_module.CERT_FILE_MAX_BYTES + 1)
    monkeypatch.setattr(
        service_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, big, b""),
    )

    with pytest.raises(ValueError, match="larger than"):
        _copy(service_module, tmp_path, path, me)
    assert not os.listdir(tmp_path / "copies")


def test_a_helper_that_hangs_is_a_read_failure(
    service_module, monkeypatch, tmp_path, udir, me
):
    path = _own(_touch(Path(udir) / "x.pem"), me)

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(a, 5)

    monkeypatch.setattr(service_module.subprocess, "run", hang)

    with pytest.raises(ValueError, match="cannot read the file"):
        _copy(service_module, tmp_path, path, me)


def test_no_desktop_user_means_no_file_is_read(service_module, tmp_path, cert_files):
    # uid 0 is the fallback of _get_real_user when no session user is found
    with pytest.raises(ValueError, match="no desktop user"):
        _copy(service_module, tmp_path, cert_files[0], 0)


def test_the_directory_of_the_copies_is_checked(service_module, monkeypatch, tmp_path):
    root = tmp_path / "run"
    monkeypatch.setattr(service_module, "CERT_COPY_ROOT", str(root))
    assert service_module._cert_copy_root() == str(root)  # created
    assert service_module._cert_copy_root() == str(root)  # and used again

    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    plain = _touch(tmp_path / "file")
    for bad in (link, shared, plain):
        monkeypatch.setattr(service_module, "CERT_COPY_ROOT", str(bad))
        with pytest.raises(ValueError, match="not a directory"):
            service_module._cert_copy_root()


def _spawn_attempt(service_module, monkeypatch, tmp_path, uid, **settings):
    _desktop_user(service_module, monkeypatch, tmp_path, uid)
    return _started(service_module, monkeypatch, tmp_path, **settings)


def test_a_bad_file_is_not_passed_to_gpclient(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    # Positive twin: the same file of the desktop user starts gpclient
    assert _started(service_module, monkeypatch, tmp_path, usercert=cert_files[0])
    other = tmp_path / "other"
    other.mkdir()
    assert not _started(service_module, monkeypatch, other, usercert=str(other))
    assert os.listdir(tmp_path / "run") == []  # no directory is left behind


def test_the_desktop_uid_is_used_not_the_uid_of_the_service(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    assert _started(service_module, monkeypatch, tmp_path, usercert=cert_files[0])
    other = tmp_path / "other"
    other.mkdir()
    # same file, but the desktop user is someone else than its owner
    assert not _spawn_attempt(
        service_module, monkeypatch, other, OTHER_UID, usercert=cert_files[0]
    )


def test_without_a_desktop_user_gpclient_is_not_started(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    other = tmp_path / "other"
    other.mkdir()

    assert not _spawn_attempt(
        service_module, monkeypatch, other, 0, usercert=cert_files[0]
    )


def _teardown_plugin(service_module, tmp_path):
    plugin = service_module.GpclientVPNPlugin()
    plugin._cert_dir = str(tmp_path / "copies")
    (tmp_path / "copies").mkdir()
    (tmp_path / "copies" / "usercert.pem").write_text("x")
    return plugin


def test_teardown_deletes_the_copies(service_module, tmp_path):
    plugin = _teardown_plugin(service_module, tmp_path)

    asyncio.run(plugin._teardown())

    assert not (tmp_path / "copies").exists()
    assert plugin._cert_dir == ""


def test_a_failure_deletes_the_copies(service_module, tmp_path):
    plugin = _teardown_plugin(service_module, tmp_path)

    plugin._emit_failure(1)

    assert not (tmp_path / "copies").exists()


def test_a_new_attempt_deletes_the_copies_of_the_old_one(
    service_module, monkeypatch, tmp_path, me, cert_files
):
    old = tmp_path / "run" / "old"
    old.mkdir(parents=True)
    (old / "usercert.pem").write_text("x")
    plugin_dir = str(old)

    def keep(self):  # the plugin that _command makes knows this directory
        self._cert_dir = plugin_dir

    orig = service_module.GpclientVPNPlugin.__init__

    def init(self):
        orig(self)
        keep(self)

    monkeypatch.setattr(service_module.GpclientVPNPlugin, "__init__", init)
    _command(service_module, monkeypatch, tmp_path, usercert=cert_files[0])

    assert not old.exists()


# --- a wrong passphrase must not loop ---------------------------------------


def test_an_agent_that_returns_the_same_passphrase_fails_the_login(service_module):
    plugin = service_module.GpclientVPNPlugin()
    plugin.cert_pass = CERT_PASS
    plugin._interactive = True
    plugin._answered_username = True
    asked, written, failures = [], [], []

    async def agent(key, label, banner):
        asked.append(key)
        return CERT_PASS  # the agent hands back the stored, wrong value

    plugin._request_secret_interactive = agent
    plugin._write_keys = lambda data, description: written.append(data)
    plugin._fail_login = failures.append

    async def run():
        for _ in range(10):  # gpclient asks again after every wrong passphrase
            before = len(written)
            await plugin._handle_prompt("Enter the PEM passphrase")
            if len(written) == before:
                break

    asyncio.run(run())

    assert failures == ["wrong certificate passphrase"]
    assert len(written) <= 1  # the stored value once, never again


def test_different_passphrases_are_asked_at_most_three_times(service_module):
    plugin = service_module.GpclientVPNPlugin()
    plugin._interactive = True
    plugin._answered_username = True
    answers = iter(["a1", "a2", "a3", "a4", "a5"])
    written, failures = [], []

    async def agent(key, label, banner):
        return next(answers)

    plugin._request_secret_interactive = agent
    plugin._write_keys = lambda data, description: written.append(data)
    plugin._fail_login = failures.append

    async def run():
        for _ in range(5):
            await plugin._handle_prompt("Enter the PEM passphrase")

    asyncio.run(run())

    assert len(written) == 3
    assert failures and set(failures) == {"wrong certificate passphrase"}


# --- the lone value of an agent ----------------------------------------------


def _ask(service_module, secret_key, returned):
    plugin = service_module.GpclientVPNPlugin()
    plugin._interactive = True

    async def run():
        async def deliver():
            await asyncio.sleep(0)
            plugin._secret_future.set_result(returned)

        asyncio.get_running_loop().create_task(deliver())
        return await plugin._request_secret_interactive(secret_key, "label", "")

    return asyncio.run(run())


@pytest.mark.parametrize(
    "asked, returned_key",
    [
        ("cert-pass", "password"),
        ("password", "cert-pass"),
        ("otp", "password"),
        ("otp", "cert-pass"),
        ("cert-pass", "otp"),
        # the SAML result of the auth dialog (saml-auth=dialog) is no passphrase
        ("cert-pass", "gpauth-result"),
        ("gpauth-result", "cert-pass"),
    ],
)
def test_another_known_secret_is_never_taken_for_the_one_asked(
    service_module, asked, returned_key
):
    with pytest.raises(Exception, match="did not provide"):
        _ask(service_module, asked, {returned_key: VPN_PASSWORD})


@pytest.mark.parametrize("asked", ["cert-pass", "password", "otp"])
def test_a_lone_value_under_an_unknown_key_is_still_taken(service_module, asked):
    assert _ask(service_module, asked, {"legacy": "v"}) == "v"
    assert _ask(service_module, asked, {asked: "v"}) == "v"


# --- the screen text of the prompt -------------------------------------------


@pytest.mark.parametrize("label", PASSPHRASE_PROMPTS)
def test_the_screen_text_of_the_prompt_is_the_certificate_passphrase(
    service_module, label
):
    row = f"? {label}: "

    assert service_module.detect_prompt(row) == label
    assert service_module.detect_screen_prompt([row], 0) == (0, label)
    plugin = service_module.GpclientVPNPlugin()
    plugin._answered_username = True
    assert plugin._classify_prompt_kind(label, "") == "cert-pass"


# --- a restarted gpclient asks for the passphrase again ------------------------


def test_a_restarted_gpclient_gets_the_stored_passphrase_again(
    service_module, monkeypatch, tmp_path
):
    # --fix-openssl starts a new process, which asks again: the same (right)
    # passphrase must be typed again, it is no "wrong passphrase"
    _command(service_module, monkeypatch, tmp_path)  # makes the fake gpclient
    plugin = service_module.GpclientVPNPlugin()
    plugin.gateway = "portal.example.com"
    plugin.cert_pass = CERT_PASS
    plugin._interactive = True
    plugin._answered_username = True
    written, failures = [], []
    plugin._write_keys = lambda data, description: written.append(data)
    plugin._fail_login = failures.append

    async def agent(key, label, banner):
        return CERT_PASS  # the agent has the same (right) passphrase

    plugin._request_secret_interactive = agent

    async def run():
        await plugin._handle_prompt("Enter the PEM passphrase")
        await plugin._start_gpclient()  # the restart
        await asyncio.wait_for(plugin.stdout_monitor_task, 10)
        await plugin._handle_prompt("Enter the PEM passphrase")

    asyncio.run(run())

    assert len(written) == 2
    assert failures == []
