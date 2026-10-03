"""
Tests for .github/workflows/publish-apt.yml: the public address of the site is known before the
repository is built (configure-pages runs first) and reaches the build script through the
environment, never through an expression pasted into a shell script.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

from workflow_steps import WORKFLOWS, step_lines

import os

WORKFLOW = "publish-apt.yml"


def step_names():
    with open(os.path.join(WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
        return [line[len("      - name: "):] for line in handle.read().splitlines()
                if line.startswith("      - name: ")]


def test_pages_are_configured_before_the_repository_is_built():
    names = step_names()
    assert names.index("Configure Pages") < names.index("Build the repository")


def test_the_configure_pages_step_has_the_id_the_build_reads():
    assert "        id: pages" in step_lines(WORKFLOW, "Configure Pages")


def test_the_build_gets_the_base_url_through_its_environment():
    lines = step_lines(WORKFLOW, "Build the repository")
    assert "          APT_REPO_URL: ${{ steps.pages.outputs.base_url }}" in lines
    assert "        env:" in lines


def test_no_expression_is_pasted_into_the_build_script():
    lines = step_lines(WORKFLOW, "Build the repository")
    script = lines[lines.index("        run: bash .github/scripts/build-apt-repo.sh incoming site \"$APT_GPG_KEY\"")]
    assert "${{" not in script
