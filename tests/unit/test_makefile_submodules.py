"""
Tests for `make init-submodules`.

After a bump of the upstream pin, an existing clone must get the nested
submodules of the new release (3.x moved openconnect to another URL and commit),
so in a git checkout the target syncs and updates them every time. Sources that
were copied into the Docker build container have no usable git; there the
target keeps them when the nested sources are present.

`git` is a fake that records its calls: no test touches a real repository.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GPOC = "gpoc"
NESTED = os.path.join(GPOC, "crates", "openconnect", "deps", "openconnect")

FAKE_GIT = """#!/bin/sh
echo "$*" >> "$GIT_LOG"
if [ "$1" = "-C" ] && [ "$3" = "rev-parse" ]; then
    [ "$GIT_WORKS" = "1" ]
    exit $?
fi
exit 0
"""


def run_target(tmp_path, git_works, has_dot_git, nested_present):
    """Run init-submodules in a scratch tree; return the lines that fake git got"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(FAKE_GIT)
    git.chmod(0o755)
    work = tmp_path / "work"
    (work / GPOC).mkdir(parents=True)
    if has_dot_git:
        (work / GPOC / ".git").write_text("gitdir: ../../.git/modules/gpoc\n")
    if nested_present:
        os.makedirs(work / NESTED)
        (work / NESTED / ".git").write_text("gitdir: elsewhere\n")
    log = tmp_path / "git.log"
    log.write_text("")
    env = dict(
        os.environ,
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        GIT_LOG=str(log),
        GIT_WORKS="1" if git_works else "0",
    )
    subprocess.run(
        ["make", "-f", os.path.join(ROOT, "Makefile"), f"GPOC_DIR={GPOC}", "init-submodules"],
        cwd=work,
        env=env,
        check=True,
        capture_output=True,
    )
    return log.read_text().splitlines()


@pytest.mark.parametrize("nested_present", [True, False])
def test_a_git_checkout_syncs_and_updates_the_nested_submodules(tmp_path, nested_present):
    calls = run_target(tmp_path, git_works=True, has_dot_git=True, nested_present=nested_present)

    assert "submodule sync --recursive" in calls
    assert "submodule update --init --recursive" in calls
    assert calls.index("submodule sync --recursive") < calls.index(
        "submodule update --init --recursive"
    )


def test_copied_sources_with_the_nested_sources_are_left_alone(tmp_path):
    calls = run_target(tmp_path, git_works=False, has_dot_git=True, nested_present=True)

    assert not any(call.startswith("submodule") for call in calls)


def test_a_directory_without_its_own_git_is_not_updated_through_a_parent(tmp_path):
    # git finds a parent repository from here; it is not the checkout of GPOC_DIR
    calls = run_target(tmp_path, git_works=True, has_dot_git=False, nested_present=True)

    assert not any(call.startswith("submodule") for call in calls)


def test_copied_sources_without_the_nested_sources_try_the_update(tmp_path):
    calls = run_target(tmp_path, git_works=False, has_dot_git=True, nested_present=False)

    assert "submodule update --init --recursive" in calls
    assert "submodule sync --recursive" not in calls
