"""
Tests for .github/scripts/check_tag_version.py, which stops a release build when
the tag (v2.0.0) differs from the upstream version in the top line of
debian/changelog (2.0.0, native; older entries have a
revision: 1.5.0-1). Every positive test has negative counterparts.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import subprocess
import sys

import pytest
import workflow_steps

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "check_tag_version.py")


def run(tag, text, tmp_path):
    changelog = tmp_path / "changelog"
    changelog.write_text(text)
    return subprocess.run([sys.executable, SCRIPT, tag, str(changelog)], capture_output=True, text=True)


def entry(version):
    return f"network-manager-gpclient ({version}) unstable; urgency=medium\n\n  * x\n\n -- A <a@b.c>  Mon, 01 Jan 2024 00:00:00 +0000\n"


@pytest.mark.parametrize("tag, version", [
    ("v1.5.0", "1.5.0-1"),
    ("v2.0.0", "2.0.0"),
    ("v2.1", "2.1"),
])
def test_matching_tag_passes(tag, version, tmp_path):
    result = run(tag, entry(version), tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tag, version", [
    ("v2.0.1", "2.0.0-1"),
    ("v2.0", "2.0.0-1"),
    ("v2.0.0", "2.0.0.1-1"),
    ("v1.5.0", "2.0.0-1"),
    ("v2.0.0", "2.0.0~rc1-1"),
    ("v2.0.1", "2.0.0"),
    ("v1.5.0", "2.0.0"),
    ("v2.0.0", "2.0.0~rc1"),
    ("v2.0.0", "2.0.0.1"),
])
def test_mismatching_tag_fails(tag, version, tmp_path):
    result = run(tag, entry(version), tmp_path)
    assert result.returncode == 1
    assert "does not match" in result.stderr


@pytest.mark.parametrize("tag, version", [
    ("v2.0.0", "2.0.0-1"),
    ("v2.0.0", "2.0.0-3"),
    ("v2.0.0", "1:2.0.0-1"),
    ("v2.1", "2.1-1"),
    ("v2.1.3", "2.1.3-2"),
    ("v10.0.0", "10.0.0-1"),
    ("v2.0", "2.0-1"),
    ("v2", "2-1"),
])
def test_a_revision_from_2_0_0_on_fails(tag, version, tmp_path):
    result = run(tag, entry(version), tmp_path)
    assert result.returncode == 1
    assert "from 2.0.0 on the version is native" in result.stderr


@pytest.mark.parametrize("tag", ["2.0.0", "v", "v2.0.0-1", "v2.0.0\n", "vx", "v2..0", "refs/tags/v2.0.0", ""])
def test_malformed_tag_fails(tag, tmp_path):
    result = run(tag, entry("2.0.0-1"), tmp_path)
    assert result.returncode == 1
    assert "not a release tag" in result.stderr


@pytest.mark.parametrize("text", ["", "\n", "garbage\n", " package (1.0-1)\n", "package 1.0-1\n"])
def test_malformed_changelog_fails(text, tmp_path):
    result = run("v1.0", text, tmp_path)
    assert result.returncode == 1
    assert "changelog" in result.stderr


def test_missing_changelog_fails(tmp_path):
    result = subprocess.run([sys.executable, SCRIPT, "v1.0", str(tmp_path / "none")], capture_output=True, text=True)
    assert result.returncode == 1


def test_the_repository_changelog_gives_a_valid_tag():
    sys.path.insert(0, os.path.join(ROOT, ".github", "scripts"))
    import check_tag_version
    with open(os.path.join(ROOT, "debian", "changelog"), encoding="utf-8") as handle:
        text = handle.read()
    check_tag_version.check(f"v{check_tag_version.changelog_version(text)}", text)


def test_the_workflow_checks_the_tag_before_building():
    text = workflow_steps.read_workflow("build-release.yml")
    assert "python3 .github/scripts/check_tag_version.py" in text
    assert text.count("needs: check-tag") == 3


def run_step(event, ref, tag):
    script = workflow_steps.step_script("build-release.yml", "Check the tag against debian/changelog")
    env = {"PATH": os.environ["PATH"], "EVENT_NAME": event, "REF": ref, "TAG": tag}
    return subprocess.run(["bash", "-e", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("event, ref, tag, ok", [
    ("push", "refs/tags/v99.0.0", "v99.0.0", False),
    ("push", "refs/tags/v1", "v1", False),
    ("pull_request", "refs/pull/1/merge", "1/merge", True),
    ("push", "refs/heads/main", "main", True),
    ("workflow_dispatch", "refs/heads/x", "x", True),
])
def test_the_step_checks_tag_pushes_only(event, ref, tag, ok):
    assert (run_step(event, ref, tag).returncode == 0) is ok


def test_the_step_accepts_the_tag_of_the_changelog():
    with open(os.path.join(ROOT, "debian", "changelog"), encoding="utf-8") as handle:
        version = handle.readline().split("(")[1].split(")")[0].rsplit("-", 1)[0]
    assert run_step("push", f"refs/tags/v{version}", f"v{version}").returncode == 0
