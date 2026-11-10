"""
Tests for the provenance and checksum parts of .github/workflows/build-release.yml:
the jobs that build the .deb and .rpm files attest them (only for tags and pull
requests of this repository, and only these two jobs may write attestations), and the
release job uploads a SHA256SUMS file for every package asset.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import hashlib
import re
import subprocess

import pytest
from workflow_steps import read_workflow, step_lines, step_names, step_script

WORKFLOW = "build-release.yml"
ATTEST_ACTION = re.compile(r"uses: actions/attest-build-provenance@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def job_block(job, text=None):
    text = read_workflow(WORKFLOW) if text is None else text
    start = text.index(f"\n  {job}:\n") + 1
    nxt = re.search(r"^  [a-z-]+:$", text[start + 1:], re.M)
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


@pytest.mark.parametrize("job, step, glob", [
    ("build", "Attest build provenance of the .deb files", "*.deb"),
    ("build-rpm", "Attest build provenance of the .rpm files", "*.rpm"),
])
def test_package_jobs_attest_their_files_before_the_upload(job, step, glob):
    lines = step_lines(WORKFLOW, step)
    assert any(ATTEST_ACTION.search(line) for line in lines)
    assert any(line.strip().startswith("subject-path:") and line.endswith(glob) for line in lines)
    names = step_names(WORKFLOW)
    assert names.index(step) < names.index("Upload artifacts" if job == "build" else "Upload RPM artifacts")


@pytest.mark.parametrize("step", ["Attest build provenance of the .deb files", "Attest build provenance of the .rpm files"])
def test_attestation_is_skipped_where_the_token_cannot_write(step):
    condition = [line for line in step_lines(WORKFLOW, step) if line.strip().startswith("if:")]
    assert len(condition) == 1
    condition = condition[0]
    # tags (push), and pull requests of this repository except Dependabot's: never forks
    assert "github.event_name == 'push'" in condition
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "dependabot" in condition
    assert "workflow_dispatch" not in condition


def test_only_the_package_jobs_and_the_release_job_get_attestation_rights():
    text = read_workflow(WORKFLOW)
    assert text.count("attestations: write") == 4 and text.count("id-token: write") == 5
    assert "      contents: write\n      id-token: write\n      attestations: write\n" in job_block("release")
    assert "contents: read\n\n# A new push" in text or "permissions:\n  contents: read\n" in text
    for job in ("build", "build-rpm", "build-arch"):
        block = job_block(job)
        assert "      contents: read\n      id-token: write\n      attestations: write\n" in block
        assert "contents: write" not in block
    for job in ("check-tag", "release-notes"):
        assert "attestations: write" not in job_block(job)
    # the job that calls publish-apt only reads attestations (gh attestation verify)
    assert "attestations: read" in job_block("publish-apt")


def test_sha256sums_is_uploaded_with_the_packages():
    lines = step_lines(WORKFLOW, "Create Release")
    assert "            release-files/SHA256SUMS" in lines
    names = step_names(WORKFLOW)
    assert names.index("Write SHA256SUMS") < names.index("Create Release")
    assert "fail_on_unmatched_files: true" in "\n".join(lines)


ATTEST_SUMS = "Attest build provenance of SHA256SUMS"
SUMS_SUBJECT = "          subject-path: release-files/SHA256SUMS"
RELEASE_RIGHTS = "      contents: write\n      id-token: write\n      attestations: write\n"


def sums_attestation_problems(text):
    """What is wrong with the way the release job attests SHA256SUMS in the workflow text"""
    found = []
    names = step_names(WORKFLOW, text)
    if ATTEST_SUMS not in names:
        return ["the SHA256SUMS attest step is missing"]
    lines = step_lines(WORKFLOW, ATTEST_SUMS, text)
    if not any(ATTEST_ACTION.search(line) for line in lines):
        found.append("the step does not use the pinned attest action")
    if SUMS_SUBJECT not in lines:
        found.append("the step does not attest release-files/SHA256SUMS")
    if [line for line in lines if line.strip().startswith("if:")]:
        found.append("the step has a condition")
    # after the file is final, before it is uploaded
    if not names.index("Write SHA256SUMS") < names.index(ATTEST_SUMS) < names.index("Create Release"):
        found.append("the step is not between Write SHA256SUMS and Create Release")
    if ATTEST_SUMS not in job_block("release", text) or RELEASE_RIGHTS not in job_block("release", text):
        found.append("the release job does not have the rights to attest")
    return found


def test_release_job_attests_the_sha256sums_it_uploads():
    assert sums_attestation_problems(read_workflow(WORKFLOW)) == []


def _attest_after_release(text):
    attest = "\n".join(step_lines(WORKFLOW, ATTEST_SUMS, text)) + "\n"
    create = "\n".join(step_lines(WORKFLOW, "Create Release", text)) + "\n"
    assert attest + create in text
    return text.replace(attest + create, create + attest, 1)


def _attest_pinned_to_a_tag(text):
    attest = "\n".join(step_lines(WORKFLOW, ATTEST_SUMS, text)) + "\n"
    return text.replace(attest, re.sub(r"(attest-build-provenance@)\S+", r"\1v4", attest), 1)


@pytest.mark.parametrize("name, mutate, expected", [
    ("step after Create Release", _attest_after_release, "not between"),
    ("subject removed", lambda t: t.replace(SUMS_SUBJECT + "\n", ""), "does not attest release-files/SHA256SUMS"),
    ("subject elsewhere", lambda t: t.replace(SUMS_SUBJECT, SUMS_SUBJECT.replace("SHA256SUMS", "other")),
     "does not attest release-files/SHA256SUMS"),
    ("permission removed", lambda t: t.replace(RELEASE_RIGHTS, "      contents: write\n      id-token: write\n"),
     "does not have the rights"),
    ("step removed", lambda t: t.replace("      - name: " + ATTEST_SUMS, "      - name: Other"), "missing"),
    ("condition added", lambda t: t.replace("      - name: " + ATTEST_SUMS + "\n",
                                            "      - name: " + ATTEST_SUMS + "\n        if: false\n"),
     "has a condition"),
    ("tag pin", _attest_pinned_to_a_tag, "pinned attest action"),
])
def test_a_modified_sha256sums_attestation_is_rejected(name, mutate, expected):
    text = read_workflow(WORKFLOW)
    modified = mutate(text)
    assert modified != text, name
    found = sums_attestation_problems(modified)
    assert any(expected in problem for problem in found), (name, found)


def attest_pins_are_one_commit(text):
    pins = set(re.findall(r"actions/attest-build-provenance@(\S+)", text))
    return len(pins) == 1 and re.fullmatch(r"[0-9a-f]{40}", pins.pop()) is not None


def test_the_attest_action_is_pinned_to_one_commit_everywhere():
    assert attest_pins_are_one_commit(read_workflow(WORKFLOW))


@pytest.mark.parametrize("pin", ["v4", "0" * 40])
def test_a_second_or_tag_pin_of_the_attest_action_is_found(pin):
    text = read_workflow(WORKFLOW)
    modified = re.sub(r"(attest-build-provenance@)\S+", r"\g<1>" + pin, text, count=1)
    assert modified != text
    assert not attest_pins_are_one_commit(modified)


def run_sums(tmp_path, files):
    out = tmp_path / "release-files"
    out.mkdir()
    for name, data in files.items():
        (out / name).write_bytes(data)
    script = step_script(WORKFLOW, "Write SHA256SUMS")
    return out, subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True)


def test_sha256sums_covers_every_package_under_the_name_of_the_asset(tmp_path):
    files = {
        "network-manager-gpclient_2.0.0-1~noble1_amd64.deb": b"deb",
        "network-manager-gpclient-gnome_2.0.0-1~jammy1_arm64.deb": b"deb2",
        "network-manager-gpclient-2.0.0-1.fc44.x86_64.rpm": b"rpm",
        "network-manager-gpclient-plasma-2.0.0-1-x86_64.pkg.tar.zst": b"arch",
        "PKGBUILD": b"pkgbuild",
        "network-manager-gpclient.install": b"hooks",
    }
    out, result = run_sums(tmp_path, files)
    assert result.returncode == 0, result.stderr
    lines = (out / "SHA256SUMS").read_text().splitlines()
    got = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines}
    # GitHub stores "~" as "."
    assert got == {name.replace("~", "."): hashlib.sha256(data).hexdigest() for name, data in files.items()}


def test_sha256sums_fails_without_packages(tmp_path):
    out, result = run_sums(tmp_path, {"notes.txt": b"x"})
    assert result.returncode != 0


def test_sha256sums_fails_when_a_format_has_no_package(tmp_path):
    # a release has all its packages or is not made: no Arch Linux package is no release
    files = {
        "network-manager-gpclient_2.0.0-1~noble1_amd64.deb": b"deb",
        "network-manager-gpclient-2.0.0-1.fc44.x86_64.rpm": b"rpm",
    }
    _out, result = run_sums(tmp_path, files)
    assert result.returncode != 0
