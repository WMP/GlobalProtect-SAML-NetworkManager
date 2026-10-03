"""
Tests for .github/workflows/publish-apt.yml: the public address of the site is known before the
repository is built (configure-pages runs first, with the id the build reads) and reaches the build
script through the environment, never through an expression pasted into a shell script.

`problems` is the checker; the workflow as written must have none, and every modified copy below
(steps swapped, id missing or different, the expression moved into the script ...) must be rejected.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import pytest

from workflow_steps import read_workflow, step_lines, step_names

WORKFLOW = "publish-apt.yml"
PAGES = "Configure Pages"
BUILD = "Build the repository"
ENV_LINE = "          APT_REPO_URL: ${{ steps.pages.outputs.base_url }}"
ID_LINE = "        id: pages"


def problems(text):
    """What is wrong with the way the workflow text passes the Pages base URL to the build"""
    names = step_names(WORKFLOW, text)
    if PAGES not in names or BUILD not in names:
        return [f"missing step: {PAGES!r} or {BUILD!r}"]
    found = []
    if names.index(PAGES) > names.index(BUILD):
        found.append("the Pages step runs after the build")
    if ID_LINE not in step_lines(WORKFLOW, PAGES, text):
        found.append("the Pages step does not have the id pages")
    build = step_lines(WORKFLOW, BUILD, text)
    if "        env:" not in build or ENV_LINE not in build:
        found.append("the build does not get APT_REPO_URL from steps.pages.outputs.base_url in its env")
    # the only expression in the whole step is that one env line: none in the script, nor in another key
    others = [line for line in build if "${{" in line and line != ENV_LINE]
    if others:
        found.append(f"an expression outside the env line: {others}")
    return found


def swap_steps(text, first, second):
    """The workflow text with the steps `first` and `second` (first listed before second) exchanged"""
    lines = text.splitlines()
    one, two = step_lines(WORKFLOW, first, text), step_lines(WORKFLOW, second, text)
    i, j = lines.index(one[0]), lines.index(two[0])
    return "\n".join(lines[:i] + two + lines[i + len(one):j] + one + lines[j + len(two):]) + "\n"


def test_the_workflow_passes_the_base_url_to_the_build_through_its_environment():
    assert problems(read_workflow(WORKFLOW)) == []


def test_the_pages_step_comes_before_the_build_step():
    names = step_names(WORKFLOW)
    assert names.index(PAGES) < names.index(BUILD)


def test_the_build_script_line_holds_no_expression():
    script = [line for line in step_lines(WORKFLOW, BUILD) if line.strip().startswith("run:")]
    assert script and all("${{" not in line for line in script)


@pytest.mark.parametrize("name, mutate, expected", [
    ("steps swapped", lambda t: swap_steps(t, PAGES, BUILD), "runs after the build"),
    ("id removed", lambda t: t.replace(ID_LINE + "\n", ""), "does not have the id pages"),
    ("id different", lambda t: t.replace(ID_LINE, "        id: site"), "does not have the id pages"),
    ("id moved to another step", lambda t: t.replace(ID_LINE + "\n", "").replace(
        "      - name: Upload Pages artifact\n", "      - name: Upload Pages artifact\n" + ID_LINE + "\n"),
     "does not have the id pages"),
    ("env reads another output", lambda t: t.replace(ENV_LINE, ENV_LINE.replace("base_url", "base_path")),
     "does not get APT_REPO_URL"),
    ("env reads another step", lambda t: t.replace(ENV_LINE, ENV_LINE.replace("steps.pages", "steps.deploy")),
     "does not get APT_REPO_URL"),
    ("env line removed", lambda t: t.replace(ENV_LINE + "\n", ""), "does not get APT_REPO_URL"),
    ("expression pasted into the script", lambda t: t.replace(
        'build-apt-repo.sh incoming site "$APT_GPG_KEY"',
        'build-apt-repo.sh incoming site "$APT_GPG_KEY" "${{ steps.pages.outputs.base_url }}"'),
     "an expression outside the env line"),
    ("expression in a second env entry", lambda t: t.replace(
        ENV_LINE, ENV_LINE + "\n          OTHER: ${{ github.head_ref }}"), "an expression outside the env line"),
    ("build step renamed", lambda t: t.replace("- name: " + BUILD, "- name: Build"), "missing step"),
])
def test_a_modified_workflow_is_rejected(name, mutate, expected):
    text = read_workflow(WORKFLOW)
    modified = mutate(text)
    assert modified != text, name
    found = problems(modified)
    assert any(expected in problem for problem in found), (name, found)
