"""
Tests for the process patterns in debian/network-manager-gpclient.postinst and
.postrm, which stop the old VPN service on upgrade and removal.

The service is a Python script with the shebang "#!/usr/bin/env python3", so
the process name is python3 and /proc/PID/cmdline is "python3 /path/to/script".
The scripts match the whole command line with an anchored pattern. Every
positive test has negative counterparts: an editor, a pager, a shell, pkill
itself, a file with a suffix, an unrelated gpclient command.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
POSTINST = os.path.join(ROOT, "debian", "network-manager-gpclient.postinst")
POSTRM = os.path.join(ROOT, "debian", "network-manager-gpclient.postrm")

NEW = "/usr/lib/NetworkManager/nm-gpclient-service"
OLD = "/usr/libexec/NetworkManager/gpclient-nm-service"


def _patterns(path):
    """Return {variable: pattern} for every *service_re='...' line of a script."""
    with open(path) as f:
        text = f.read()
    return dict(re.findall(r"^\s*(\w*service_re)='([^']*)'$", text, re.M))


def _matches(pattern, cmdline):
    # pgrep -f / pkill -f use POSIX extended regular expressions
    return (
        subprocess.run(
            ["grep", "-E", "-q", "--", pattern],
            input=cmdline,
            text=True,
        ).returncode
        == 0
    )


PATTERNS = {
    "postinst new": _patterns(POSTINST)["service_re"],
    "postinst old": _patterns(POSTINST)["old_service_re"],
    "postrm": _patterns(POSTRM)["service_re"],
}

# (pattern name, service path) pairs that each pattern must match
CASES = [
    ("postinst new", NEW),
    ("postinst old", OLD),
    ("postrm", NEW),
    ("postrm", OLD),
]

INTERPRETERS = ["python3", "/usr/bin/python3", "python3.12", "/usr/bin/python3.10"]


@pytest.mark.parametrize("name,path", CASES)
@pytest.mark.parametrize("interp", INTERPRETERS)
@pytest.mark.parametrize("args", ["", " --debug", " -v  x"])
def test_pattern_matches_running_service(name, path, interp, args):
    assert _matches(PATTERNS[name], f"{interp} {path}{args}")


def _negatives(path):
    return [
        f"vim {path}",
        f"less {path}",
        f"python3 {path}.bak",
        f"python3 {path}-old",
        f"python3 {path}x",
        f"python3 /home/u/{path}",
        f"python3 -m pytest {path}",
        f"sudo python3 {path}",
        f"sh -c python3 {path}",
        f"sh -c pkill -f {path}",
        f"pkill -f python3 {path}",
        f"pgrep -f {path}",
        f"python2 {path}",
        f"python3x {path}",
        f"python {path}",
        f"/usr/local/bin/python3 {path}",
        f"/usr/bin/python3{path}",
        "nm-gpclient-service",
        f"gpclient connect vpn.example.com --service",
        f"gpclient --service",
        f"gpservice",
        "",
    ]


@pytest.mark.parametrize("name,path", CASES)
def test_pattern_rejects_other_command_lines(name, path):
    for cmdline in _negatives(path):
        assert not _matches(PATTERNS[name], cmdline), cmdline


def test_patterns_do_not_cross_match():
    """The old-name pattern never matches the new path and the reverse."""
    assert not _matches(PATTERNS["postinst new"], f"python3 {OLD}")
    assert not _matches(PATTERNS["postinst old"], f"python3 {NEW}")


def test_patterns_own_command_line_is_not_matched():
    """pkill -f gets the pattern as an argument: its command line must not match."""
    for name, pattern in PATTERNS.items():
        assert not _matches(pattern, f"pkill -f {pattern}"), name
        assert not _matches(pattern, f"pgrep -f {pattern}"), name


@pytest.mark.parametrize("path", [POSTINST, POSTRM])
def test_scripts_have_no_bare_substring_patterns(path):
    with open(path) as f:
        text = f.read()
    assert 'pkill -f "gpclient-nm-service"' not in text
    assert 'pgrep -f "gpclient-nm-service"' not in text
    assert "gpclient.*service" not in text
    assert "pkill -x" not in text
    assert "pidof" not in text


@pytest.mark.parametrize("path", [POSTINST, POSTRM])
def test_pkill_never_fails_the_script(path):
    with open(path) as f:
        lines = [x for x in f if re.match(r"\s*pkill\b", x)]
    assert lines
    for line in lines:
        assert "|| true" in line, line


@pytest.mark.parametrize("path", [POSTINST, POSTRM])
def test_scripts_are_valid_posix_sh(path):
    dash = shutil.which("dash") or shutil.which("sh")
    assert subprocess.run([dash, "-n", path]).returncode == 0


# --- real processes -------------------------------------------------------

pgrep = shutil.which("pgrep")
pkill = shutil.which("pkill")
needs_procps = pytest.mark.skipif(not (pgrep and pkill), reason="procps missing")


def _substitute(pattern, real_path):
    return pattern.replace(NEW, real_path)


@pytest.fixture
def workdir():
    d = tempfile.mkdtemp(prefix="gp-maint-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _spawn(argv):
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    return p


def _script(workdir, name="nm-gpclient-service"):
    path = os.path.join(workdir, name)
    with open(path, "w") as f:
        f.write("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
    os.chmod(path, 0o755)
    return path


def _pgrep(pattern):
    r = subprocess.run([pgrep, "-f", pattern], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


@needs_procps
def test_real_cmdline_of_env_shebang_script_is_matched_and_killed(workdir):
    path = _script(workdir)
    pattern = _substitute(PATTERNS["postinst new"], path)
    p = _spawn([path])
    try:
        with open(f"/proc/{p.pid}/cmdline", "rb") as f:
            cmdline = f.read().split(b"\0")
        # the kernel runs "env python3 path", env execs python3
        assert cmdline[0].endswith(b"python3")
        assert path.encode() in cmdline
        assert _pgrep(pattern) == [p.pid]
        subprocess.run([pkill, "-f", pattern], check=True)
        assert p.wait(timeout=10) != 0
    finally:
        p.kill()
        p.wait()


@needs_procps
def test_real_processes_that_only_name_the_file_are_not_matched(workdir):
    path = _script(workdir)
    pattern = _substitute(PATTERNS["postinst new"], path)
    sleeper = "import time; time.sleep(60)"
    others = [
        # a shell and a program that have the path in their arguments
        _spawn(["sh", "-c", f"sleep 60 # {path}"]),
        _spawn([sys.executable, "-c", sleeper, path]),
        _spawn(["sleep", "60", path]),
        # a copy with a suffix
        _spawn([path + ".bak"]) if shutil.copy(path, path + ".bak") else None,
    ]
    try:
        assert _pgrep(pattern) == []
    finally:
        for p in others:
            p.kill()
            p.wait()
