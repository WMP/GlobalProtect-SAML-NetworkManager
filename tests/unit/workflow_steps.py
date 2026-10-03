"""
Helper for the tests of the GitHub workflows: the shell script of one step, as
it is written in the workflow file, so a test can run it with fake tools
instead of trusting a copy of it. No YAML parser is needed (CI installs only
pytest): steps are "      - name: ..." and their script is the "        run: |"
block below.
"""

import os

WORKFLOWS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows"))


def read_workflow(workflow):
    """The text of .github/workflows/<workflow>"""
    with open(os.path.join(WORKFLOWS, workflow), encoding="utf-8") as handle:
        return handle.read()


def step_names(workflow, text=None):
    """The names of the steps of a workflow, in order. `text` replaces the file (a modified copy)"""
    lines = (read_workflow(workflow) if text is None else text).splitlines()
    return [line[len("      - name: "):] for line in lines if line.startswith("      - name: ")]


def step_lines(workflow, name, text=None):
    """All the lines of the step `name` of .github/workflows/<workflow>; `text` replaces the file"""
    lines = (read_workflow(workflow) if text is None else text).splitlines()
    start = lines.index(f"      - name: {name}")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("      - name:") or (line.strip() and not line.startswith("      ")):
            break
        block.append(line)
    return block


def step_script(workflow, name, substitutions=None):
    """The shell script of the step `name`, with ${{ ... }} expressions replaced as given"""
    script = None
    for line in step_lines(workflow, name)[1:]:
        if script is None:
            if line == "        run: |":
                script = []
            continue
        if line.strip() == "":
            script.append("")
        elif line.startswith(" " * 10):
            script.append(line[10:])
        else:
            break
    assert script, f"no run script in step {name}"
    text = "\n".join(script) + "\n"
    for old, new in (substitutions or {}).items():
        text = text.replace(old, new)
    assert "${{" not in text, "an expression was left in the script"
    return text
