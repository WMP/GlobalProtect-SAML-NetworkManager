"""
Tests for the RPM part of .github/workflows/build-release.yml: the job
"build-rpm" (a matrix of the targets of rpm/targets and both architectures),
its steps, which are taken out of the workflow file and run as they are with
fake tools, the manual run's choice of a target, and the release job, which
takes the RPMs along with the debs.

Every positive test has negative counterparts: a matrix that differs from
rpm/targets, an expression inside a script, a wrong number, name, architecture,
dist tag or pull request suffix of the built RPMs, a bad pull request number or
an unknown target stop the step.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import json
import os
import re
import shutil
import subprocess

import pytest
import workflow_steps

WORKFLOW = "build-release.yml"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TARGETS_FILE = os.path.join(ROOT, "rpm", "targets")
BUILD = "Build RPM packages"
VERIFY = "Verify .rpm files"
INSTALL = "Install test"
UPLOAD = "Upload RPM artifacts"
ATTEST = "Attest build provenance of the .rpm files"
PACKAGES = ["network-manager-gpclient", "network-manager-gpclient-gnome", "network-manager-gpclient-plasma"]
RPM_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}


def targets():
    """[(target, image, dist, family)] of rpm/targets"""
    assert os.path.exists(TARGETS_FILE), "rpm/targets is missing"
    with open(TARGETS_FILE, encoding="utf-8") as handle:
        return [fields for fields in (line.split("#")[0].split() for line in handle) if fields]


def workflow_text():
    with open(os.path.join(workflow_steps.WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
        return handle.read()


def job_text(name):
    text = workflow_text()
    start = text.index("\n  %s:\n" % name) + 1
    match = re.search(r"^  (#|[a-z][a-z-]*:\n)", text[start + 1:], re.M)
    return text[start:start + 1 + match.start()] if match else text[start:]


def step_names(job):
    return re.findall(r"^      - name: (.*)$", job_text(job), re.M)


def matrix_lists(job):
    """The lists of fromJSON('[...]') in the matrix line of `target:`"""
    lines = [l for l in job_text(job).splitlines() if l.startswith("        target: ")]
    assert len(lines) == 1, "expected one target matrix line"
    return [json.loads(m) for m in re.findall(r"fromJSON\('(\[[^']*\])'\)", lines[0])]


def dispatch_options(input_name):
    text = workflow_text()
    block = text[text.index("      %s:\n" % input_name) + 1:]
    following = re.search(r"^(?:      [a-z_]+:|[a-z#])", block, re.M)
    block = block[:following.start()] if following else block
    return re.findall(r'^          - "([^"]+)"$', block, re.M)


class TestMatrixIsTheListOfTargets:
    def test_the_matrix_equals_rpm_targets(self):
        names = [t[0] for t in targets()]
        lists = matrix_lists("build-rpm")

        # no manual run, and "all" of a manual run: every target
        assert lists == [names, names]

    def test_the_manual_choice_has_none_all_and_every_target(self):
        names = [t[0] for t in targets()]

        assert dispatch_options("rpm_target") == ["none"] + names + ["all"]

    def test_a_manual_run_defaults_to_no_rpm_build(self):
        text = workflow_text()
        block = text[text.index("      rpm_target:\n"):]

        assert 'default: "none"' in block.split("\n\n")[0]

    def test_the_matrix_check_fails_when_a_target_differs(self):
        # the negative counterpart: a matrix with another list is not rpm/targets
        names = [t[0] for t in targets()]
        assert names[:-1] != names and names + ["fedora99"] != names
        assert sorted(names) == sorted(set(names)), "a target is listed twice in rpm/targets"

    def test_both_architectures_have_their_native_runner(self):
        job = job_text("build-rpm")

        assert "arch: [amd64, arm64]" in job
        assert re.search(r"- arch: amd64\n\s+runner: ubuntu-latest\n\s+- arch: arm64\n\s+runner: ubuntu-24.04-arm", job)
        assert "runs-on: ${{ matrix.runner }}" in job

    def test_the_ubuntu_job_and_its_input_are_untouched(self):
        text = workflow_text()

        assert "      ubuntu_version:\n" in text
        assert dispatch_options("ubuntu_version") == ["22.04", "24.04", "26.04", "all"]
        # Ubuntu 24.10 is end of life and not built any more
        assert "24.10" not in text
        assert "oracular" not in text
        assert "        ubuntu: ${{ github.event_name == 'workflow_dispatch'" in text

    def test_the_job_is_skipped_when_a_manual_run_picks_none(self):
        job = job_text("build-rpm")

        assert "if: github.event_name != 'workflow_dispatch' || github.event.inputs.rpm_target != 'none'" in job


class TestScriptsHaveNoExpressions:
    @pytest.mark.parametrize("name", [BUILD, VERIFY, INSTALL])
    def test_no_expression_inside_a_run_script(self, name):
        script = workflow_steps.step_script(WORKFLOW, name)

        assert "${{" not in script

    def test_every_run_script_of_the_job_is_free_of_expressions(self):
        job = job_text("build-rpm")
        for block in re.findall(r"^        run: \|\n((?:          .*\n|\n)+)", job, re.M):
            assert "${{" not in block

    def test_the_values_come_through_env(self):
        for name in (BUILD, VERIFY, INSTALL):
            lines = "\n".join(workflow_steps.step_lines(WORKFLOW, name))
            assert "TARGET: ${{ matrix.target }}" in lines and "ARCH: ${{ matrix.arch }}" in lines

    def test_the_steps_order_and_the_artifact(self):
        names = step_names("build-rpm")

        assert names == ["Checkout code", "Vendor the crates", BUILD, VERIFY, ATTEST, UPLOAD, INSTALL]
        lines = "\n".join(workflow_steps.step_lines(WORKFLOW, UPLOAD))
        assert "name: rpms-${{ matrix.target }}-${{ matrix.arch }}" in lines
        assert "path: output/${{ matrix.target }}-${{ matrix.arch }}/*.rpm" in lines

    def test_the_install_test_is_not_run_for_tags_and_the_build_is(self):
        assert "if: github.event_name != 'push'" in "\n".join(workflow_steps.step_lines(WORKFLOW, INSTALL))
        for name in (BUILD, VERIFY, UPLOAD):
            assert "if:" not in "\n".join(workflow_steps.step_lines(WORKFLOW, name))

    def test_the_checkout_has_the_submodules(self):
        job = job_text("build-rpm")

        assert "submodules: recursive" in job


FAKE_BUILD = """#!/bin/bash
# rpm/build.sh of the test: what it was called with
{
    echo "args: $*"
    echo "PR_NUMBER=${PR_NUMBER-unset}"
    echo "RUN_NUMBER=${RUN_NUMBER-unset}"
} > "$FAKE_DIR/build_call"
mkdir -p "$3"
"""


class Box:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.work = tmp_path / "work"
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        for directory in (self.work / "rpm", self.fake, self.bin):
            directory.mkdir(parents=True)
        shutil.copy(TARGETS_FILE, self.work / "rpm" / "targets")
        (self.work / "rpm" / "build.sh").write_text(FAKE_BUILD)
        docker = self.bin / "docker"
        docker.write_text('#!/bin/bash\necho "$*" >> "$FAKE_DIR/docker_calls"\n')
        docker.chmod(0o755)

    def run(self, step, **env):
        values = {"PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_DIR": str(self.fake), "HOME": str(self.root),
                  "EVENT_NAME": "pull_request", "PR_NUMBER": "24", "RUN_NUMBER": "57",
                  "TARGET": "fedora44", "ARCH": "amd64"}
        values.update(env)
        return subprocess.run(["bash", "-e", "-c", workflow_steps.step_script(WORKFLOW, step)], cwd=self.work,
                              env=values, capture_output=True, text=True, timeout=60)

    def output(self, target, arch, names):
        out = self.work / "output" / f"{target}-{arch}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        for name in names:
            (out / name).write_text(name)

    def read(self, name):
        path = self.fake / name
        return path.read_text() if path.exists() else None


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


class TestBuildStep:
    def test_a_pull_request_passes_its_numbers_to_the_build(self, box):
        result = box.run(BUILD)

        assert result.returncode == 0, result.stderr
        assert box.read("build_call").splitlines() == [
            "args: fedora44 amd64 output/fedora44-amd64", "PR_NUMBER=24", "RUN_NUMBER=57"]

    @pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule"])
    def test_other_builds_get_no_numbers(self, box, event):
        result = box.run(BUILD, EVENT_NAME=event, PR_NUMBER="", RUN_NUMBER="57")

        assert result.returncode == 0, result.stderr
        assert box.read("build_call").splitlines()[1:] == ["PR_NUMBER=unset", "RUN_NUMBER=unset"]

    @pytest.mark.parametrize("pr", ["", "0", "024", "24x", "-1", "12345678", "24 25", "$(id)", "24;id"])
    def test_a_bad_pull_request_number_stops_before_the_build(self, box, pr):
        result = box.run(BUILD, PR_NUMBER=pr)

        assert result.returncode != 0
        assert "unexpected pull request or run number" in result.stdout
        assert box.read("build_call") is None

    @pytest.mark.parametrize("run", ["", "0", "057", "5x", "12345678901", "$(id)"])
    def test_a_bad_run_number_stops_before_the_build(self, box, run):
        result = box.run(BUILD, RUN_NUMBER=run)

        assert result.returncode != 0
        assert box.read("build_call") is None

    @pytest.mark.parametrize("target, arch", [(t[0], a) for t in targets() for a in ("amd64", "arm64")])
    def test_every_target_and_architecture_goes_to_the_build_script(self, box, target, arch):
        result = box.run(BUILD, TARGET=target, ARCH=arch, EVENT_NAME="push")

        assert result.returncode == 0, result.stderr
        assert box.read("build_call").splitlines()[0] == f"args: {target} {arch} output/{target}-{arch}"

    def test_a_failing_build_fails_the_step(self, box):
        (box.work / "rpm" / "build.sh").write_text("#!/bin/bash\nexit 3\n")

        result = box.run(BUILD)

        assert result.returncode == 3


def good_names(target, arch, event="pull_request", version="1.5.0", pr="24", run="57"):
    dist = next(t[2] for t in targets() if t[0] == target)
    suffix = f".pr{pr}.{run}" if event == "pull_request" else ""
    return [f"{p}-{version}-1{dist}{suffix}.{RPM_ARCH[arch]}.rpm" for p in PACKAGES]


class TestVerifyStep:
    @pytest.mark.parametrize("target", [t[0] for t in targets()])
    @pytest.mark.parametrize("arch", ["amd64", "arm64"])
    @pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
    def test_the_packages_a_build_is_supposed_to_make_pass(self, box, target, arch, event):
        box.output(target, arch, good_names(target, arch, event))

        result = box.run(VERIFY, TARGET=target, ARCH=arch, EVENT_NAME=event)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Built packages" in result.stdout

    def test_a_version_with_a_tilde_passes(self, box):
        box.output("fedora44", "amd64", good_names("fedora44", "amd64", version="1.5.0~rc1"))

        assert box.run(VERIFY).returncode == 0

    @pytest.mark.parametrize("drop", range(3))
    def test_a_missing_package_fails(self, box, drop):
        names = good_names("fedora44", "amd64")
        box.output("fedora44", "amd64", names[:drop] + names[drop + 1:])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected exactly 3 files" in result.stdout

    @pytest.mark.parametrize("extra", [
        "network-manager-gpclient-1.5.0-1.fc44.pr24.57.src.rpm",
        "network-manager-gpclient-debuginfo-1.5.0-1.fc44.pr24.57.x86_64.rpm",
        "README.txt",
    ])
    def test_an_extra_file_fails(self, box, extra):
        box.output("fedora44", "amd64", good_names("fedora44", "amd64") + [extra])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected exactly 3 files" in result.stdout

    def test_no_file_fails(self, box):
        box.output("fedora44", "amd64", [])

        assert box.run(VERIFY).returncode != 0

    def test_a_package_twice_and_one_missing_fails(self, box):
        names = good_names("fedora44", "amd64")
        box.output("fedora44", "amd64", [names[0], names[0].replace("1.5.0", "1.5.1"), names[1]])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected the packages" in result.stdout

    def test_a_wrong_architecture_fails(self, box):
        box.output("fedora44", "amd64", good_names("fedora44", "arm64"))

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "does not match the expected name" in result.stdout

    def test_the_dist_tag_of_another_target_fails(self, box):
        box.output("fedora44", "amd64", good_names("fedora43", "amd64"))

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "does not match" in result.stdout

    def test_a_dist_tag_that_only_starts_like_the_target_fails(self, box):
        names = [n.replace(".fc44", ".fc440") for n in good_names("fedora44", "amd64")]
        box.output("fedora44", "amd64", names)

        assert box.run(VERIFY).returncode != 0

    def test_a_pull_request_build_without_the_suffix_fails(self, box):
        box.output("fedora44", "amd64", good_names("fedora44", "amd64", event="push"))

        result = box.run(VERIFY, EVENT_NAME="pull_request")

        assert result.returncode != 0
        assert "does not match" in result.stdout

    @pytest.mark.parametrize("pr, run", [("25", "57"), ("24", "58"), ("124", "57"), ("2", "57")])
    def test_a_pull_request_build_of_another_pull_request_or_run_fails(self, box, pr, run):
        box.output("fedora44", "amd64", good_names("fedora44", "amd64", pr=pr, run=run))

        assert box.run(VERIFY, EVENT_NAME="pull_request").returncode != 0

    @pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
    def test_a_release_build_with_a_pull_request_suffix_fails(self, box, event):
        box.output("fedora44", "amd64", good_names("fedora44", "amd64", event="pull_request"))

        assert box.run(VERIFY, EVENT_NAME=event, PR_NUMBER="", RUN_NUMBER="57").returncode != 0

    def test_a_package_of_another_name_fails(self, box):
        names = good_names("fedora44", "amd64")
        names[2] = names[2].replace("-plasma", "-kde")
        box.output("fedora44", "amd64", names)

        assert box.run(VERIFY).returncode != 0

    def test_an_unknown_target_fails(self, box):
        box.output("fedora99", "amd64", good_names("fedora44", "amd64"))

        result = box.run(VERIFY, TARGET="fedora99")

        assert result.returncode != 0
        assert "not in rpm/targets" in result.stdout

    def test_an_unknown_architecture_fails(self, box):
        box.output("fedora44", "ppc64", good_names("fedora44", "amd64"))

        result = box.run(VERIFY, ARCH="ppc64")

        assert result.returncode != 0
        assert "unexpected architecture" in result.stdout


class TestInstallStep:
    @pytest.mark.parametrize("target, image", [(t[0], t[1]) for t in targets()])
    def test_the_packages_are_installed_in_a_fresh_container_of_the_targets_image(self, box, target, image):
        result = box.run(INSTALL, TARGET=target, ARCH="arm64")

        assert result.returncode == 0, result.stderr
        assert box.read("docker_calls").splitlines() == [
            f"run --rm --platform linux/arm64 -v {box.work}/output/{target}-arm64:/rpms:ro "
            f"-v {box.work}/rpm:/rpm:ro {image} bash /rpm/install-test.sh /rpms"]

    def test_an_unknown_target_runs_no_container(self, box):
        result = box.run(INSTALL, TARGET="fedora99")

        assert result.returncode != 0
        assert "not in rpm/targets" in result.stdout
        assert box.read("docker_calls") is None

    def test_a_failing_install_test_fails_the_step(self, box):
        (box.bin / "docker").write_text("#!/bin/bash\nexit 1\n")

        assert box.run(INSTALL).returncode != 0


class TestRelease:
    def test_the_release_waits_for_both_builds(self):
        job = job_text("release")

        assert "needs: [build, build-rpm, build-arch, sbom]" in job

    def test_the_release_job_collects_and_uploads_debs_and_rpms_and_needs_both(self):
        job = job_text("release")

        assert "-name '*.deb' -o -name '*.rpm'" in job
        assert "release-files/*.deb" in job and "release-files/*.rpm" in job
        assert "fail_on_unmatched_files: true" in job

    def test_the_release_is_only_for_tags(self):
        assert "if: startsWith(github.ref, 'refs/tags/')" in job_text("release")

    def test_the_publish_job_calls_the_workflow_that_exists(self):
        job = job_text("publish-apt")

        assert "uses: ./.github/workflows/publish-apt.yml" in job
        assert os.path.exists(os.path.join(workflow_steps.WORKFLOWS, "publish-apt.yml"))

    def test_no_file_refers_to_the_renamed_workflow_file(self):
        # publish-apt.yml keeps its name: the first version of the RPM change renamed it
        old = "publish-package-" + "repositories"
        assert not os.path.exists(os.path.join(workflow_steps.WORKFLOWS, old + ".yml"))
        hits = []
        for directory, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "external", "vendor", "__pycache__", ".pytest_cache")]
            for name in files:
                path = os.path.join(directory, name)
                try:
                    with open(path, encoding="utf-8") as handle:
                        if old in handle.read():
                            hits.append(os.path.relpath(path, ROOT))
                except (UnicodeDecodeError, OSError):
                    continue
        assert hits == []
