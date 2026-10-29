"""
Tests for rpm/debian_changelog.py, which derives the RPM version, release and %changelog
from debian/changelog.

Every positive test has a negative counterpart: input that is not valid is rejected, not
turned into a version or an entry.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import importlib.util
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "rpm", "debian_changelog.py")
CHANGELOG = os.path.join(ROOT, "debian", "changelog")

_spec = importlib.util.spec_from_file_location("debian_changelog", SCRIPT)
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)

SAMPLE = """\
network-manager-gpclient (1.6.0~rc1-2) unstable; urgency=medium

  * Fix 100% of the first thing, which has a long text that continues
    on a second line
  * Second thing

 -- Jane Doe <jane@example.org>  Fri, 02 Oct 2026 12:00:00 +0200

network-manager-gpclient (1.5.0-1) unstable; urgency=medium

  * Older thing

 -- WMP <example@example.org>  Sat, 18 Jul 2026 12:00:00 +0200
"""


def run(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    "debian, expected",
    [
        ("1.5.0-1", ("1.5.0", "1")),
        ("1.6.0~rc1-2", ("1.6.0~rc1", "2")),
        ("1.5.0-1ubuntu2", ("1.5.0", "1ubuntu2")),
        ("1.5.0", ("1.5.0", "1")),
        ("2.0.0", ("2.0.0", "1")),
        ("2.0.0~noble1", ("2.0.0~noble1", "1")),
        ("2.0.0~noble1+pr5.1", ("2.0.0~noble1+pr5.1", "1")),
        ("1.5-0-3", ("1.5_0", "3")),
    ],
)
def test_split_version(debian, expected):
    assert dc.split_version(debian) == expected


@pytest.mark.parametrize(
    "debian",
    ["1:1.5.0-1", "-1", "1.5.0-", "1.5 0-1", "1.5.0-1:2", "v1/5-1", "", "1.5.0-1 2"],
)
def test_split_version_rejects(debian):
    with pytest.raises(dc.ChangelogError):
        dc.split_version(debian)


def test_pr_suffix():
    assert dc.pr_suffix("36", "7") == ".pr36.7"
    assert dc.pr_suffix("", "") == ""


@pytest.mark.parametrize(
    "pr, run_number",
    [("36", ""), ("", "7"), ("3x", "7"), ("36", "7.1"), ("-1", "7"), ("36", "$(id)"), ("3 6", "7"), ("٣", "7")],
)
def test_pr_suffix_rejects(pr, run_number):
    with pytest.raises(dc.ChangelogError):
        dc.pr_suffix(pr, run_number)


def test_rpm_changelog_format():
    out = dc.rpm_changelog(SAMPLE).splitlines()
    # The weekday is computed from the date, not copied
    assert out[0] == "* Fri Oct 02 2026 Jane Doe <jane@example.org> - 1.6.0~rc1-2"
    # A continued item is one line; "%" is escaped
    assert out[1] == (
        "- Fix 100%% of the first thing, which has a long text that continues on a second line"
    )
    assert out[2] == "- Second thing"
    assert out[4] == "* Sat Jul 18 2026 WMP <example@example.org> - 1.5.0-1"
    assert out[5] == "- Older thing"


def test_rpm_changelog_computes_weekday():
    text = SAMPLE.replace("Fri, 02 Oct 2026", "Mon, 02 Oct 2026")
    assert dc.rpm_changelog(text).startswith("* Fri Oct 02 2026 ")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\n\n",
        "garbage before\n" + SAMPLE,
        # No trailer line
        "pkg (1.0-1) unstable; urgency=low\n\n  * Thing\n",
        # Item text without a bullet
        "pkg (1.0-1) unstable; urgency=low\n\nThing\n\n -- A <a@b>  Fri, 02 Oct 2026 12:00:00 +0200\n",
        # Impossible date
        SAMPLE.replace("02 Oct 2026", "31 Feb 2026"),
        SAMPLE.replace("02 Oct 2026", "02 Foo 2026"),
        # Epoch
        SAMPLE.replace("(1.6.0~rc1-2)", "(1:1.6.0-2)"),
    ],
)
def test_rpm_changelog_rejects(text):
    with pytest.raises(dc.ChangelogError):
        dc.rpm_changelog(text)


def test_real_changelog_converts():
    with open(CHANGELOG, encoding="utf-8") as handle:
        text = handle.read()
    headers = re.findall(r"^\S+ \(\S+\) ", text, re.M)
    entries = [l for l in dc.rpm_changelog(text).splitlines() if l.startswith("* ")]
    assert len(entries) == len(headers) > 0
    # No macro is left in the output
    assert not re.search(r"(?<!%)%(?!%)", dc.rpm_changelog(text))


def test_cli_version_and_release():
    top = re.match(r"^\S+ \((\S+)\) ", open(CHANGELOG, encoding="utf-8").read()).group(1)
    # A native version (2.0.0) has no revision: the RPM release is 1
    version, _, revision = top.rpartition("-")
    if not version:
        version, revision = top, "1"
    assert run("version").stdout.strip() == version
    assert run("release").stdout.strip() == revision


def test_cli_uses_the_top_entry(tmp_path):
    path = tmp_path / "changelog"
    path.write_text(SAMPLE, encoding="utf-8")
    assert run("version", "--changelog", str(path)).stdout.strip() == "1.6.0~rc1"
    assert run("release", "--changelog", str(path)).stdout.strip() == "2"
    assert run("changelog", "--changelog", str(path)).stdout.startswith("* Fri Oct 02 2026 Jane")


def test_cli_fails_on_a_bad_changelog(tmp_path):
    path = tmp_path / "changelog"
    path.write_text("not a changelog\n", encoding="utf-8")
    for command in ("version", "release", "changelog"):
        result = run(command, "--changelog", str(path))
        assert result.returncode == 1 and result.stdout == ""
        assert "error:" in result.stderr
    result = run("version", "--changelog", str(tmp_path / "missing"))
    assert result.returncode == 1 and "error:" in result.stderr


def test_cli_pr_suffix():
    assert run("pr-suffix", "36", "7").stdout.strip() == ".pr36.7"
    assert run("pr-suffix", "", "").stdout == "\n"
    for args in (("36", ""), ("a", "7"), ("36", "7; id")):
        result = run("pr-suffix", *args)
        assert result.returncode == 1 and result.stdout == ""
