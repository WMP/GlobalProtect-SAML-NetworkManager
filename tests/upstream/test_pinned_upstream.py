"""
Tests of the pinned upstream: the checkout of external/GlobalProtect-openconnect
against upstream/contract.json, upstream/features.json, the README and the
Rust toolchain of the builds. See docs/UPSTREAM.md.

They need the submodule. When it is missing they FAIL (they never skip: a
skipped check would look like a passed one). The logic of the checks, with
their negative cases, is tested in tests/unit/test_upstream_contract.py.

Run with: python3 -m pytest tests/upstream -q
"""

import glob
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "upstream_contract.py")
SOURCE = os.path.join(ROOT, "external", "GlobalProtect-openconnect")

_spec = importlib.util.spec_from_file_location("upstream_contract", SCRIPT)
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)


def git(*args):
    return subprocess.run(["git", "-C", SOURCE, *args], capture_output=True, text=True)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="session")
def contract():
    with open(os.path.join(ROOT, "upstream", "contract.json"), encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="session", autouse=True)
def submodule(contract):
    """Fail with instructions when the submodule or the pinned tag is missing"""
    if not os.path.isfile(os.path.join(SOURCE, "Cargo.toml")):
        pytest.fail(
            "external/GlobalProtect-openconnect is not checked out. Run:\n"
            "  git submodule update --init external/GlobalProtect-openconnect",
            pytrace=False,
        )
    tag = contract["tag"]
    if tag is None:
        return  # the legacy untagged pin: there is no tag to look for
    if git("rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}").returncode != 0:
        pytest.fail(
            f"the tag {tag} is not in external/GlobalProtect-openconnect. Run:\n"
            f"  git -C external/GlobalProtect-openconnect fetch --depth=1 origin refs/tags/{tag}:refs/tags/{tag}",
            pytrace=False,
        )


def run_script(*args):
    result = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=ROOT)
    return result.returncode, result.stdout + result.stderr


def test_contract_matches_the_checked_out_upstream():
    code, output = run_script("check")
    assert code == 0, output


def test_every_changelog_entry_is_classified_and_tested():
    code, output = run_script("features")
    assert code == 0, output


def test_every_function_that_reads_or_runs_upstream_has_tests():
    code, output = run_script("interfaces")
    assert code == 0, output


def test_pin_is_a_release_tag_or_the_legacy_untagged_commit(contract):
    assert uc.pin_problem(contract) is None


def test_contract_commit_is_the_checked_out_commit(contract):
    head = git("rev-parse", "HEAD").stdout.strip()
    assert contract["commit"] == head, (
        f"the submodule is at {head}, the contract accepts {contract['commit']}: "
        "run the bump procedure of docs/UPSTREAM.md"
    )


def test_pinned_tag_points_at_the_contract_commit(contract):
    if uc.is_legacy(contract):
        assert contract["tag"] is None and contract["commit"] == uc.LEGACY_UNTAGGED_COMMIT
        return
    tagged = git("rev-parse", f"refs/tags/{contract['tag']}^{{commit}}").stdout.strip()
    assert tagged == contract["commit"], f"{contract['tag']} is {tagged}, not the pinned commit {contract['commit']}"


def test_readme_names_the_pinned_tag_or_the_legacy_commit(contract):
    assert uc.readme_pin_problem(read("README.md"), contract) is None


def test_builds_use_a_rust_toolchain_that_upstream_accepts():
    channel = uc.toolchain_channel(read("external", "GlobalProtect-openconnect", "rust-toolchain.toml"))
    problems = []
    checked = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "Dockerfile.ubuntu*"))):
        text = open(path, encoding="utf-8").read()
        if "rustup" not in text:
            continue
        checked += 1
        problems += uc.toolchain_problems(channel, os.path.basename(path), uc.dockerfile_rust_versions(text))
    assert checked, "no Dockerfile installs rustup: this check does not look at anything"
    problems += uc.toolchain_problems(channel, "Makefile", uc.makefile_rust_version(read("Makefile")))
    assert not problems, "\n".join(problems)
