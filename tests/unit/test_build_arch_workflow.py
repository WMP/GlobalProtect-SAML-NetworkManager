"""
Tests for the Arch Linux part of the workflows: the job "build-arch" of
.github/workflows/build-release.yml (its steps are taken out of the workflow file
and run as they are with fake tools), the manual run's choice, the release job,
which takes the packages along with the debs and RPMs, and publish-apt.yml,
which builds the pacman repository.

Every positive test has negative counterparts: an expression inside a script, a
wrong number, name, architecture, package or pull request suffix of the built
packages, a bad pull request number or a missing file stop the step.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re
import subprocess

import pytest
import workflow_steps

WORKFLOW = "build-release.yml"
PUBLISH = "publish-apt.yml"
BUILD = "Build Arch Linux packages"
VERIFY = "Verify .pkg.tar.zst files"
INSTALL = "Install test of the Arch Linux packages"
UPLOAD = "Upload Arch Linux artifacts"
ATTEST = "Attest build provenance of the .pkg.tar.zst files"
PACKAGES = ["network-manager-gpclient", "network-manager-gpclient-gnome", "network-manager-gpclient-plasma"]


def workflow_text(name=WORKFLOW):
    return workflow_steps.read_workflow(name)


def job_text(name, text=None):
    text = workflow_text() if text is None else text
    start = text.index("\n  %s:\n" % name) + 1
    match = re.search(r"^  (#|[a-z][a-z-]*:\n)", text[start + 1:], re.M)
    return text[start:start + 1 + match.start()] if match else text[start:]


class TestJob:
    def test_the_steps_order_and_the_artifact(self):
        names = re.findall(r"^      - name: (.*)$", job_text("build-arch"), re.M)

        assert names == ["Checkout code", "Vendor the crates", BUILD, VERIFY, ATTEST, UPLOAD, "Upload the PKGBUILD", INSTALL]
        lines = "\n".join(workflow_steps.step_lines(WORKFLOW, UPLOAD)[:10])
        assert "name: arch-x86_64" in lines
        assert "path: output/arch-x86_64/*.pkg.tar.zst" in lines

    def test_it_runs_on_x86_64_only_after_the_tag_check(self):
        job = job_text("build-arch")

        assert "needs: check-tag" in job
        assert "runs-on: ubuntu-latest" in job
        assert "matrix" not in job and "arm64" not in job
        assert "timeout-minutes: 120" in job
        assert "submodules: recursive" in job

    def test_it_writes_attestations_like_the_other_builds(self):
        job = job_text("build-arch")

        assert "id-token: write" in job and "attestations: write" in job
        condition = "if: github.event_name == 'push' || (github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository && github.actor != 'dependabot[bot]')"
        assert condition in "\n".join(workflow_steps.step_lines(WORKFLOW, ATTEST))
        # the same condition as the RPM and the Ubuntu builds
        assert condition in "\n".join(workflow_steps.step_lines(WORKFLOW, "Attest build provenance of the .rpm files"))

    def test_the_install_test_is_not_run_for_tags_and_the_build_is(self):
        assert "if: github.event_name != 'push'" in "\n".join(workflow_steps.step_lines(WORKFLOW, INSTALL))
        for name in (BUILD, VERIFY, UPLOAD):
            assert "if:" not in "\n".join(workflow_steps.step_lines(WORKFLOW, name))

    @pytest.mark.parametrize("name", [BUILD, VERIFY, INSTALL])
    def test_no_expression_inside_a_run_script(self, name):
        assert "${{" not in workflow_steps.step_script(WORKFLOW, name)

    def test_every_run_script_of_the_job_is_free_of_expressions(self):
        for block in re.findall(r"^        run: \|\n((?:          .*\n|\n)+)", job_text("build-arch"), re.M):
            assert "${{" not in block

    def test_a_manual_run_builds_arch_only_when_asked(self):
        text = workflow_text()
        block = text[text.index("      arch_linux:\n"):].split("\n\n")[0]

        assert "type: boolean" in block and "default: false" in block
        assert "if: github.event_name != 'workflow_dispatch' || github.event.inputs.arch_linux == 'true'" in job_text("build-arch")

    def test_the_other_jobs_keep_their_manual_choices(self):
        text = workflow_text()

        assert "      ubuntu_version:\n" in text and "      rpm_target:\n" in text
        assert "if: github.event_name != 'workflow_dispatch' || github.event.inputs.rpm_target != 'none'" in job_text("build-rpm")


FAKE_BUILD = """#!/bin/bash
# arch/build.sh of the test: what it was called with
{
    echo "args: $*"
    echo "PR_NUMBER=${PR_NUMBER-unset}"
    echo "RUN_NUMBER=${RUN_NUMBER-unset}"
} > "$FAKE_DIR/build_call"
mkdir -p "$1"
"""


class Box:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.work = tmp_path / "work"
        self.fake = tmp_path / "fake"
        for directory in (self.work / "arch", self.fake):
            directory.mkdir(parents=True)
        (self.work / "arch" / "build.sh").write_text(FAKE_BUILD)

    def run(self, step, **env):
        values = {"PATH": "/usr/bin:/bin", "FAKE_DIR": str(self.fake), "HOME": str(self.root),
                  "EVENT_NAME": "pull_request", "PR_NUMBER": "24", "RUN_NUMBER": "57"}
        values.update(env)
        return subprocess.run(["bash", "-e", "-c", workflow_steps.step_script(WORKFLOW, step)], cwd=self.work,
                              env=values, capture_output=True, text=True, timeout=60)

    def output(self, names):
        out = self.work / "output" / "arch-x86_64"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.iterdir():
            old.unlink()
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
        assert box.read("build_call").splitlines() == ["args: output/arch-x86_64", "PR_NUMBER=24", "RUN_NUMBER=57"]

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

    def test_a_failing_build_fails_the_step(self, box):
        (box.work / "arch" / "build.sh").write_text("#!/bin/bash\nexit 3\n")

        assert box.run(BUILD).returncode == 3


def good_names(event="pull_request", version="1.5.0", pr="24", run="57", revision="1"):
    suffix = f".pr{pr}.{run}" if event == "pull_request" else ""
    return [f"{p}-{version}{suffix}-{revision}-x86_64.pkg.tar.zst" for p in PACKAGES]


class TestVerifyStep:
    @pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
    def test_the_packages_a_build_is_supposed_to_make_pass(self, box, event):
        box.output(good_names(event))

        result = box.run(VERIFY, EVENT_NAME=event)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "Built packages" in result.stdout

    def test_a_higher_pkgrel_passes(self, box):
        box.output(good_names("push", revision="12"))

        assert box.run(VERIFY, EVENT_NAME="push").returncode == 0

    @pytest.mark.parametrize("drop", range(3))
    def test_a_missing_package_fails(self, box, drop):
        names = good_names()
        box.output(names[:drop] + names[drop + 1:])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected exactly 3 files" in result.stdout

    @pytest.mark.parametrize("extra", [
        "network-manager-gpclient-debug-1.5.0.pr24.57-1-x86_64.pkg.tar.zst",
        "network-manager-gpclient-1.5.0.pr24.57-1-x86_64.pkg.tar.zst.sig",
        "README.txt",
    ])
    def test_an_extra_file_fails(self, box, extra):
        box.output(good_names() + [extra])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected exactly 3 files" in result.stdout

    def test_no_file_fails(self, box):
        box.output([])

        assert box.run(VERIFY).returncode != 0

    def test_a_package_twice_and_one_missing_fails(self, box):
        names = good_names()
        box.output([names[0], names[0].replace("1.5.0", "1.5.1"), names[1]])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "expected the packages" in result.stdout

    @pytest.mark.parametrize("bad", ["aarch64", "any"])
    def test_a_wrong_architecture_fails(self, box, bad):
        box.output([n.replace("x86_64", bad) for n in good_names()])

        result = box.run(VERIFY)

        assert result.returncode != 0
        assert "does not match the expected name" in result.stdout

    def test_a_pull_request_build_without_the_suffix_fails(self, box):
        box.output(good_names("push"))

        result = box.run(VERIFY, EVENT_NAME="pull_request")

        assert result.returncode != 0
        assert "does not match" in result.stdout

    @pytest.mark.parametrize("pr, run", [("25", "57"), ("24", "58"), ("124", "57"), ("2", "57")])
    def test_a_pull_request_build_of_another_pull_request_or_run_fails(self, box, pr, run):
        box.output(good_names(pr=pr, run=run))

        assert box.run(VERIFY, EVENT_NAME="pull_request").returncode != 0

    @pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
    def test_a_release_build_with_a_pull_request_suffix_fails(self, box, event):
        box.output(good_names("pull_request"))

        result = box.run(VERIFY, EVENT_NAME=event, PR_NUMBER="", RUN_NUMBER="57")

        assert result.returncode != 0
        assert "pull request marker" in result.stdout

    def test_a_package_of_another_name_fails(self, box):
        names = good_names()
        names[2] = names[2].replace("-plasma", "-kde")
        box.output(names)

        assert box.run(VERIFY).returncode != 0

    def test_a_version_without_a_pkgrel_fails(self, box):
        box.output([n.replace("-1-x86_64", "-x86_64") for n in good_names()])

        assert box.run(VERIFY).returncode != 0


class TestInstallStep:
    def test_the_packages_are_installed_in_a_fresh_container_by_the_build_script(self, box):
        (box.work / "arch" / "build.sh").write_text('#!/bin/bash\necho "$*" > "$FAKE_DIR/install_call"\n')

        result = box.run(INSTALL)

        assert result.returncode == 0, result.stderr
        assert box.read("install_call") == "--install-test output/arch-x86_64\n"

    def test_a_failing_install_test_fails_the_step(self, box):
        (box.work / "arch" / "build.sh").write_text("#!/bin/bash\nexit 1\n")

        assert box.run(INSTALL).returncode != 0


class TestRelease:
    def test_the_release_waits_for_every_build(self):
        assert "needs: [build, build-rpm, build-arch, sbom]" in job_text("release")

    def test_the_release_job_collects_lists_and_uploads_the_arch_packages(self):
        job = job_text("release")

        assert "-name '*.pkg.tar.zst'" in job
        assert "release-files/*.pkg.tar.zst" in job
        assert "fail_on_unmatched_files: true" in job

    def test_sha256sums_has_the_arch_packages_and_keeps_the_tilde_rule(self):
        script = "\n".join(workflow_steps.step_lines(WORKFLOW, "Write SHA256SUMS"))

        assert "sha256sum -- *.deb *.rpm *.pkg.tar.zst PKGBUILD network-manager-gpclient.install > SHA256SUMS" in script
        assert "sed -i 's/~/./g' SHA256SUMS" in script

    def test_the_release_is_only_for_tags(self):
        assert "if: startsWith(github.ref, 'refs/tags/') && github.event_name == 'push'" in job_text("release")


class TestPublish:
    def test_the_arch_assets_are_downloaded_verified_and_collected(self):
        text = workflow_text(PUBLISH)

        assert "--pattern '*.pkg.tar.zst'" in text
        assert "has no .pkg.tar.zst assets" in text
        assert "-o -name '*.pkg.tar.zst'" in text
        # verified with the other packages, before they are collected
        assert text.index("verify-release-assets.sh") < text.index("-o -name '*.pkg.tar.zst'")

    def test_the_pacman_repository_is_built_after_the_rpm_repositories_with_the_imported_key(self):
        text = workflow_text(PUBLISH)

        run = "bash .github/scripts/build-arch-repo.sh --index site/index.html --allow-empty incoming site/arch \"$APT_GPG_KEY\""
        assert run in text
        assert text.index("build-rpm-repo.sh") < text.index(run) < text.index("Upload Pages artifact")

    def test_the_secret_key_is_never_an_argument(self):
        text = workflow_text(PUBLISH)
        line = next(l for l in text.splitlines() if "build-arch-repo.sh" in l and "run:" in l)

        assert "APT_GPG_PRIVATE_KEY" not in line and "$APT_GPG_KEY" in line

    def test_the_script_is_in_the_shell_check_list(self):
        with open(os.path.join(workflow_steps.WORKFLOWS, "..", "..", "Makefile"), encoding="utf-8") as handle:
            makefile = handle.read()

        for script in (".github/scripts/build-arch-repo.sh", "arch/build.sh", "arch/container-build.sh",
                       "arch/install-test.sh"):
            assert f"\t{script} " in makefile, script


class TestStandalone:
    def test_the_job_builds_the_user_mode_for_pull_requests_and_the_manual_input(self):
        job = job_text("build-arch-standalone")

        assert "if: github.event_name == 'pull_request' || (github.event_name == 'workflow_dispatch' && github.event.inputs.arch_linux == 'true')" in job
        assert "runs-on: ubuntu-latest" in job
        assert "bash arch/standalone-test.sh output/arch-standalone" in job
        # the script pushes the checkout to a mirror: a full clone with the submodules
        assert "fetch-depth: 0" in job and "submodules: recursive" in job and "persist-credentials: false" in job
        # nothing is published or attested from it, and it has no write permissions
        assert "permissions:" not in job and "upload-artifact" not in job and "attest" not in job

    def test_the_release_does_not_wait_for_it_and_it_is_not_for_tags(self):
        assert "build-arch-standalone" not in job_text("release")
        job = job_text("build-arch-standalone")
        assert "push" not in job.split("steps:")[0]

    @pytest.mark.parametrize("old, new", [
        ("needs: [build, build-rpm, build-arch, sbom]", "needs: [build, build-rpm, build-arch, build-arch-standalone, sbom]"),
    ])
    def test_a_release_that_waits_for_it_is_found(self, old, new):
        text = workflow_text().replace(old, new, 1)
        assert "build-arch-standalone" in job_text("release", text)

    def test_the_release_attaches_the_pkgbuild_and_the_hooks_and_lists_them_in_sha256sums(self):
        job = job_text("release")

        assert "-name PKGBUILD -o -name 'network-manager-gpclient.install'" in job
        assert "release-files/PKGBUILD" in job and "release-files/network-manager-gpclient.install" in job
        assert "PKGBUILD network-manager-gpclient.install > SHA256SUMS" in job
        build = job_text("build-arch")
        assert "name: pkgbuild-arch" in build and "arch/PKGBUILD" in build and "arch/network-manager-gpclient.install" in build
        # not matched by the pattern of pr-test-packages.yml for the packages
        assert not re.fullmatch(r"arch-.*", "pkgbuild-arch")

    def test_every_git_source_of_the_pkgbuild_has_a_mirror_rule_in_the_test_script(self):
        pkgbuild = open(os.path.join(workflow_steps.WORKFLOWS, "..", "..", "arch", "PKGBUILD"), encoding="utf-8").read()
        script = open(os.path.join(workflow_steps.WORKFLOWS, "..", "..", "arch", "standalone-test.sh"), encoding="utf-8").read()
        urls = re.findall(r"git\+(https://\S+?\.git)#", pkgbuild)

        assert len(urls) == 4
        for url in urls:
            assert "git+" + url[:-len(".git")] + r"\.git" in script, url

    def test_a_pkgbuild_url_without_a_rule_is_found(self):
        script = open(os.path.join(workflow_steps.WORKFLOWS, "..", "..", "arch", "standalone-test.sh"), encoding="utf-8").read()

        assert r"git+https://github.com/other/repo\.git" not in script
