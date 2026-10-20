"""
Tests for .github/workflows/upstream.yml: the properties that make the gate
work and keep it safe. The file is read as text (CI installs only pytest, no
YAML parser).

Every positive test has a negative counterpart: the label events re-run the
check, and no step puts an expression into the script text.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import json
import os
import re
import subprocess
import sys

import workflow_steps

WORKFLOW = "upstream.yml"


def text():
    with open(os.path.join(workflow_steps.WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
        return handle.read()


def test_pull_request_trigger_includes_the_label_events():
    assert "types: [opened, synchronize, reopened, labeled, unlabeled]" in text()


def test_only_the_safe_triggers_are_used():
    triggers = re.search(r"^on:\n(.*?)^permissions:", text(), re.M | re.S).group(1)
    assert "pull_request_target" not in triggers
    assert "workflow_run" not in triggers
    assert "push:" in triggers and "workflow_dispatch:" in triggers


def test_token_is_read_only_and_no_secrets_are_used():
    assert "permissions:\n  contents: read\n" in text()
    assert "secrets." not in text()


def test_checkout_gets_the_top_level_submodule_only():
    step = "\n".join(workflow_steps.step_lines(WORKFLOW, "Checkout code"))
    assert "submodules: true" in step
    assert "recursive" not in step


def test_labels_and_base_ref_reach_the_gate_through_the_environment():
    step = "\n".join(workflow_steps.step_lines(WORKFLOW, "Gate breaking upstream changes"))
    assert "LABELS: ${{ toJSON(github.event.pull_request.labels.*.name) }}" in step
    assert 'BASE_REF: ${{ github.base_ref }}' in step
    assert '--labels "$LABELS"' in step
    assert "if: always() && github.event_name == 'pull_request'" in step


def test_no_expression_is_inside_a_script():
    for line in text().splitlines():
        stripped = line.strip()
        if "${{" in stripped:
            assert not stripped.startswith(("git ", "python", "TAG=", "BASE=", "echo", "if ")), line


def test_the_gate_reads_the_contract_of_the_base_branch():
    script = workflow_steps.step_script(WORKFLOW, "Gate breaking upstream changes", {
        "${{ github.base_ref }}": "main",
        "${{ toJSON(github.event.pull_request.labels.*.name) }}": "[]",
    })
    assert 'git show FETCH_HEAD:upstream/contract.json' in script
    assert '--base-contract "$BASE"' in script


def test_the_gate_step_is_not_run_outside_pull_requests():
    # A push to main has no base branch to compare with.
    assert "github.event_name == 'pull_request'" in "\n".join(
        workflow_steps.step_lines(WORKFLOW, "Gate breaking upstream changes"))
    assert "github.event_name == 'pull_request'" not in "\n".join(
        workflow_steps.step_lines(WORKFLOW, "Check the pinned upstream"))


# --- the gate logic comes from the base branch --------------------------------

STUB = """import sys
print("%s", "isolated=%%d" %% sys.flags.isolated, " ".join(sys.argv[1:]))
"""


def run_gate_step(tmp_path, base_has_script, base_gitlink=None):
    """Run the gate step in scratch repositories. The scripts are stubs that
    print where they come from, how they were started and their arguments.
    `base_gitlink` is the commit that the base branch pins for the submodule."""
    def git(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=cwd, check=True, capture_output=True)

    origin, work = tmp_path / "origin", tmp_path / "work"
    for repo in (origin, work):
        (repo / "scripts").mkdir(parents=True)
        (repo / "upstream").mkdir()
    (origin / "upstream" / "contract.json").write_text("{}")
    if base_has_script:
        (origin / "scripts" / "upstream_contract.py").write_text(STUB % "BASE")
    git(origin, "init", "-q", "-b", "main")
    git(origin, "add", "-A")
    if base_gitlink:
        git(origin, "update-index", "--add", "--cacheinfo",
            f"160000,{base_gitlink},external/GlobalProtect-openconnect")
    git(origin, "commit", "-q", "-m", "base")
    git(work, "init", "-q", "-b", "pr")
    git(work, "remote", "add", "origin", str(origin))
    (work / "scripts" / "upstream_contract.py").write_text(STUB % "PR")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    shim.chmod(0o755)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()

    script = workflow_steps.step_script(WORKFLOW, "Gate breaking upstream changes", {
        "${{ github.base_ref }}": "main",
        "${{ toJSON(github.event.pull_request.labels.*.name) }}": "[]",
    })
    return subprocess.run(
        ["bash", "-e", "-c", script], cwd=work, capture_output=True, text=True,
        env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", RUNNER_TEMP=str(runner_temp),
                 BASE_REF="main", LABELS='["upstream-verified"]'),
    )


def test_the_gate_runs_the_script_of_the_base_branch(tmp_path):
    result = run_gate_step(tmp_path, base_has_script=True)

    assert result.returncode == 0, result.stderr
    assert "BASE isolated=1" in result.stdout
    assert "PR isolated" not in result.stdout


def test_the_gate_uses_explicit_paths_and_the_labels(tmp_path):
    result = run_gate_step(tmp_path, base_has_script=True)

    assert "gate --source external/GlobalProtect-openconnect" in result.stdout
    assert "--verification upstream/verification.json" in result.stdout
    assert f"--base-contract {tmp_path / 'runner' / 'base-contract.json'}" in result.stdout
    assert '--labels ["upstream-verified"]' in result.stdout


def test_the_gate_falls_back_to_the_script_of_the_pull_request(tmp_path):
    # The first pull request that adds the script: the base has none
    result = run_gate_step(tmp_path, base_has_script=False)

    assert result.returncode == 0, result.stderr
    assert "PR isolated=1" in result.stdout
    assert "BASE isolated" not in result.stdout
    assert "using the one of the pull request" in result.stdout


COMMIT = "9e4f45997ebee452c58d7d3066dcfc175d65ef82"


def test_the_gate_passes_the_commit_that_the_base_branch_pins(tmp_path):
    result = run_gate_step(tmp_path, base_has_script=True, base_gitlink=COMMIT)

    assert result.returncode == 0, result.stderr
    assert f"--base-commit {COMMIT} --labels" in result.stdout


def test_the_gate_passes_an_empty_base_commit_when_the_base_has_no_submodule(tmp_path):
    result = run_gate_step(tmp_path, base_has_script=True)

    assert result.returncode == 0, result.stderr
    assert "--base-commit  --labels" in result.stdout
    assert COMMIT not in result.stdout


# --- the tag fetch step -------------------------------------------------------


def run_fetch_step(tmp_path, tag):
    """Run the tag fetch step in a scratch repository. `origin` has the tag v1.0.0."""
    def git(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=cwd, check=True, capture_output=True)

    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "commit", "-q", "--allow-empty", "-m", "release")
    git(origin, "tag", "v1.0.0")
    (work / "upstream").mkdir(parents=True)
    (work / "upstream" / "contract.json").write_text(json.dumps({"tag": tag}))
    external = work / "external" / "GlobalProtect-openconnect"
    external.mkdir(parents=True)
    git(external, "init", "-q", "-b", "main")
    git(external, "remote", "add", "origin", str(origin))
    script = workflow_steps.step_script(WORKFLOW, "Fetch the pinned upstream tag", {})
    result = subprocess.run(["bash", "-e", "-c", script], cwd=work, capture_output=True, text=True)
    tags = subprocess.run(["git", "-C", str(external), "tag"], capture_output=True, text=True).stdout.split()
    return result, tags


def test_the_pinned_tag_is_fetched(tmp_path):
    result, tags = run_fetch_step(tmp_path, "v1.0.0")

    assert result.returncode == 0, result.stderr
    assert tags == ["v1.0.0"]


def test_nothing_is_fetched_for_an_untagged_pin(tmp_path):
    result, tags = run_fetch_step(tmp_path, None)

    assert result.returncode == 0, result.stderr
    assert tags == []
    assert "untagged commit" in result.stdout
