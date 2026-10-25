"""
Tests for finding the desktop user (issue #51).

The service used to take the first non-root user of `loginctl list-users`, so
with fast user switching, a lingering user or a gdm greeter user the browser
opened in another user's session. Now: SUDO_UID, the single user of the
connection's permissions, the active seat0 session, the only user with an
active session (xrdp, remote desktop, VM), else the current user.

`loginctl` is faked through PATH. System users from the container stand in
for desktop users: daemon (1), bin (2) and nobody (65534).
"""

import os
import pwd
import stat

import pytest

SEAT_USER = "daemon"
OTHER_USER = "bin"


@pytest.fixture
def plugin(service_module, monkeypatch, tmp_path):
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setenv("USER", "current-user")
    monkeypatch.setenv("HOME", "/home/current-user")
    return service_module.GpclientVPNPlugin()


@pytest.fixture
def fake_loginctl(monkeypatch, tmp_path):
    """Install a fake `loginctl`; returns a function that configures it"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def install(active="c1", session=None, fail=False, sessions=None, vanished=()):
        """sessions: {id: {"User": uid, "Class": ..., "State": ..., "Type": ...}} for
        `list-sessions` (the seat0 session c1 is only listed when given);
        vanished: ids that are listed but make `show-session` fail"""
        session = session or {}
        sessions = sessions or {}
        props = {
            "User": str(pwd.getpwnam(SEAT_USER).pw_uid),
            "Name": SEAT_USER,
            "Class": "user",
            "Remote": "no",
            "State": "active",
            "Type": "x11",
        }
        props.update(session)
        lines = "\n".join(f"{k}={v}" for k, v in props.items())
        exit_code = 1 if fail else 0
        listing = "\n".join(f"{sid} {p['User']} name seat9" for sid, p in sessions.items())
        script = bindir / "loginctl"
        script.write_text(
            "#!/bin/sh\n"
            f'echo "$*" >> {tmp_path}/loginctl_calls\n'
            f"[ {exit_code} -ne 0 ] && exit 1\n"
            'case "$1" in\n'
            f"  show-seat) echo '{active}' ;;\n"
            f"  list-sessions) cat <<'EOT'\n{listing}\nEOT\n ;;\n"
            # show-session <id>... -p ...: one block per id, blank line between
            "  show-session) shift; first=1\n"
            + "".join(
                f'  for id in "$@"; do [ "$id" = {sid} ] && exit 1; done\n'
                for sid in vanished
            )
            + '  for id in "$@"; do\n'
            '    case "$id" in -*) break ;; esac\n'
            '    [ "$first" = 1 ] || echo; first=0\n'
            '    case "$id" in\n'
            f"  c1) cat <<'EOT'\n{lines}\nEOT\n ;;\n"
            + "".join(
                f"  {sid}) cat <<'EOT'\n"
                + "\n".join(
                    f"{k}={v}"
                    for k, v in {"Class": "user", "State": "active", "Type": "x11", **p}.items()
                )
                + "\nEOT\n ;;\n"
                for sid, p in sessions.items()
            )
            + "    esac\n  done ;;\n"
            f"  list-users) echo '{pwd.getpwnam(OTHER_USER).pw_uid} {OTHER_USER}' ;;\n"
            "esac\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    return install


def seat_user():
    pw = pwd.getpwnam(SEAT_USER)
    return pw.pw_uid, pw.pw_name, pw.pw_dir


def current_user():
    return os.getuid(), "current-user", "/home/current-user"


class TestSeat0Session:
    def test_active_seat0_user_wins_over_list_users(self, plugin, fake_loginctl):
        fake_loginctl()

        assert plugin._get_real_user() == seat_user()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"session": {"Class": "greeter"}},
            {"session": {"Remote": "yes"}},
            {"session": {"State": "online"}},
            {"session": {"State": "closing"}},
            {"session": {"User": "0", "Name": "root"}},
            {"session": {"User": "not-a-number"}},
            {"active": ""},
            {"fail": True},
        ],
        ids=[
            "greeter",
            "remote",
            "online",
            "closing",
            "root",
            "bad-uid",
            "no-active-session",
            "loginctl-fails",
        ],
    )
    def test_unusable_session_gives_current_user(self, plugin, fake_loginctl, kwargs):
        fake_loginctl(**kwargs)

        # Never another logged-in user, such as the one list-users shows
        assert plugin._get_real_user() == current_user()

    def test_missing_loginctl_gives_current_user(self, plugin, monkeypatch, tmp_path):
        monkeypatch.setenv("PATH", str(tmp_path))

        assert plugin._get_real_user() == current_user()


class TestPermissionUser:
    @pytest.mark.parametrize(
        "permissions",
        [
            ["user:bin:"],
            ["user:bin"],
            ("as", ["user:bin:"]),
        ],
    )
    def test_single_permission_user_wins_over_seat0(
        self, plugin, fake_loginctl, permissions
    ):
        fake_loginctl()
        plugin._permission_user = plugin._parse_permission_user(permissions)

        pw = pwd.getpwnam(OTHER_USER)
        assert plugin._get_real_user() == (pw.pw_uid, pw.pw_name, pw.pw_dir)

    @pytest.mark.parametrize(
        "permissions",
        [
            ["user:bin:", "user:daemon:"],
            [],
            ["group:wheel:"],
            ["user:no-such-user-xyz:"],
            ["user:root:"],
            "user:bin:",
            None,
        ],
        ids=["two-users", "none", "no-user-entry", "nonexistent", "root", "str", "none-type"],
    )
    def test_other_permissions_fall_through_to_seat0(
        self, plugin, fake_loginctl, permissions
    ):
        fake_loginctl()
        plugin._permission_user = plugin._parse_permission_user(permissions)

        assert plugin._get_real_user() == seat_user()

    @pytest.mark.parametrize(
        "permissions, expected",
        [
            (["user:bin:"], "bin"),
            (["user:bin", "user:bin:"], "bin"),
            (["user:bin:", "user:daemon:"], ""),
            ([], ""),
            ("user:bin:", ""),
        ],
    )
    def test_parse_permission_user(self, plugin, permissions, expected):
        assert plugin._parse_permission_user(permissions) == expected


class TestSudoUser:
    def test_sudo_uid_still_wins(self, plugin, fake_loginctl, monkeypatch):
        fake_loginctl()
        pw = pwd.getpwnam(OTHER_USER)
        plugin._permission_user = SEAT_USER
        monkeypatch.setenv("SUDO_UID", str(pw.pw_uid))
        monkeypatch.setenv("SUDO_USER", OTHER_USER)

        assert plugin._get_real_user() == (pw.pw_uid, OTHER_USER, pw.pw_dir)

    def test_sudo_uid_needs_both_variables(self, plugin, fake_loginctl, monkeypatch):
        fake_loginctl()
        monkeypatch.setenv("SUDO_UID", "2")

        assert plugin._get_real_user() == seat_user()


OTHER_UID = pwd.getpwnam(OTHER_USER).pw_uid
SEAT_UID = pwd.getpwnam(SEAT_USER).pw_uid
NOBODY_UID = pwd.getpwnam("nobody").pw_uid


def other_user():
    pw = pwd.getpwnam(OTHER_USER)
    return pw.pw_uid, pw.pw_name, pw.pw_dir


class TestOtherActiveSession:
    """seat0 has no usable session (xrdp, remote desktop, VM)"""

    def test_single_active_remote_user_is_chosen(self, plugin, fake_loginctl):
        fake_loginctl(
            active="", sessions={"c5": {"User": OTHER_UID, "Remote": "yes"}}
        )

        assert plugin._get_real_user() == other_user()

    def test_all_sessions_are_read_with_one_loginctl_call(
        self, plugin, fake_loginctl, tmp_path
    ):
        fake_loginctl(
            active="",
            sessions={
                "c5": {"User": OTHER_UID},
                "c6": {"User": OTHER_UID},
                "c7": {"User": 0},
            },
        )

        assert plugin._get_real_user() == other_user()
        calls = (tmp_path / "loginctl_calls").read_text().splitlines()
        shows = [c for c in calls if c.startswith("show-session c5")]
        assert shows == ["show-session c5 c6 c7 -p User -p Class -p State -p Type"]
        assert not any(c.startswith(("show-session c6", "show-session c7")) for c in calls)

    def test_a_session_that_closed_in_between_does_not_hide_the_user(
        self, plugin, fake_loginctl, tmp_path
    ):
        """The batched call fails when one id is gone: ask for each id instead"""
        fake_loginctl(
            active="",
            sessions={"c5": {"User": OTHER_UID}, "c6": {"User": 0}, "c7": {"User": 0}},
            vanished=["c6"],
        )

        assert plugin._get_real_user() == other_user()
        calls = (tmp_path / "loginctl_calls").read_text().splitlines()
        assert "show-session c5 c6 c7 -p User -p Class -p State -p Type" in calls
        assert "show-session c5 -p User -p Class -p State -p Type" in calls
        assert "show-session c6 -p User -p Class -p State -p Type" in calls

    def test_two_users_stay_ambiguous_after_a_session_closed(
        self, plugin, fake_loginctl
    ):
        fake_loginctl(
            active="",
            sessions={
                "c5": {"User": OTHER_UID},
                "c6": {"User": OTHER_UID + 1},
                "c7": {"User": 0},
            },
            vanished=["c7"],
        )

        assert plugin._get_real_user() == current_user()

    def test_the_same_user_in_two_sessions_is_one_user(self, plugin, fake_loginctl):
        fake_loginctl(
            active="",
            sessions={"c5": {"User": OTHER_UID}, "c6": {"User": OTHER_UID}},
        )

        assert plugin._get_real_user() == other_user()

    def test_seat0_wins_over_other_sessions(self, plugin, fake_loginctl):
        fake_loginctl(sessions={"c5": {"User": OTHER_UID}})

        assert plugin._get_real_user() == seat_user()

    def test_remote_seat0_session_alone_is_chosen_too(self, plugin, fake_loginctl):
        fake_loginctl(
            session={"Remote": "yes"},
            sessions={"c1": {"User": SEAT_UID, "Remote": "yes"}},
        )

        assert plugin._get_real_user() == seat_user()

    @pytest.mark.parametrize(
        "sessions",
        [
            {"c5": {"User": OTHER_UID}, "c6": {"User": NOBODY_UID}},
            {"c5": {"User": OTHER_UID, "Remote": "yes"}, "c6": {"User": SEAT_UID}},
        ],
        ids=["two-local-users", "remote-and-local-users"],
    )
    def test_two_different_users_give_current_user(
        self, plugin, fake_loginctl, sessions
    ):
        fake_loginctl(active="", sessions=sessions)

        # Never one of them: we cannot tell which session is meant
        assert plugin._get_real_user() == current_user()

    @pytest.mark.parametrize(
        "sessions",
        [
            {"c5": {"User": OTHER_UID, "Class": "greeter"}},
            {"c5": {"User": OTHER_UID, "State": "closing"}},
            {"c5": {"User": OTHER_UID, "State": "online"}},
            {"c5": {"User": OTHER_UID, "Type": "tty"}},
            {"c5": {"User": OTHER_UID, "Type": "unspecified"}},
            {"c5": {"User": 0}},
            {"c5": {"User": "not-a-number"}},
            {},
        ],
        ids=["greeter", "closing", "online", "ssh", "no-type", "root", "bad-uid", "no-sessions"],
    )
    def test_no_usable_session_gives_current_user(
        self, plugin, fake_loginctl, sessions
    ):
        fake_loginctl(active="", sessions=sessions)

        assert plugin._get_real_user() == current_user()

    def test_remote_desktop_user_wins_over_an_ssh_admin(self, plugin, fake_loginctl):
        # An xrdp-like x11 session of one user and an SSH login of an admin:
        # the admin has no display, so there is nothing to confuse
        fake_loginctl(
            active="",
            sessions={
                "c5": {"User": OTHER_UID, "Remote": "yes", "Type": "x11"},
                "c6": {"User": NOBODY_UID, "Remote": "yes", "Type": "tty"},
            },
        )

        assert plugin._get_real_user() == other_user()

    def test_wayland_and_mir_sessions_count(self, plugin, fake_loginctl):
        fake_loginctl(
            active="",
            sessions={"c5": {"User": OTHER_UID, "Type": "wayland"}},
        )
        assert plugin._get_real_user() == other_user()

        fake_loginctl(
            active="",
            sessions={"c5": {"User": OTHER_UID, "Type": "mir"}},
        )
        assert plugin._get_real_user() == other_user()

    def test_ssh_only_user_is_not_chosen_in_the_per_session_fallback(
        self, plugin, fake_loginctl
    ):
        fake_loginctl(
            active="",
            sessions={
                "c5": {"User": OTHER_UID, "Type": "tty"},
                "c6": {"User": 0},
            },
            vanished=["c6"],
        )

        assert plugin._get_real_user() == current_user()

    def test_unusable_sessions_do_not_hide_the_one_good_user(
        self, plugin, fake_loginctl
    ):
        fake_loginctl(
            active="",
            sessions={
                "c4": {"User": SEAT_UID, "Class": "greeter"},
                "c5": {"User": NOBODY_UID, "State": "closing"},
                "c6": {"User": 0},
                "c7": {"User": OTHER_UID, "Remote": "yes"},
            },
        )

        assert plugin._get_real_user() == other_user()
