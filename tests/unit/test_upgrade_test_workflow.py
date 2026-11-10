"""
Tests for the "Upgrade test from the last release" step of
.github/workflows/build-release.yml and for .github/scripts/upgrade-test.sh,
which it starts in a fresh ubuntu container. The step is taken out of the
workflow file and run as it is, with a fake docker found through PATH: nothing
starts a container and nothing touches the network.

Every positive test has negative counterparts: when one scenario fails the
others still run and the step fails at the end naming the scenario; when all
succeed the step passes; a bad scenario or a missing debs directory is refused
by the script.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import base64
import os
import re
import shutil
import subprocess
import sys

import pytest
import workflow_steps

WORKFLOW = "build-release.yml"
STEP = "Upgrade test from the last release"
UPLOAD_STEP = "Upload GUI screenshots"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "upgrade-test.sh")
GUI_SCRIPT = os.path.join(ROOT, ".github", "scripts", "gui-smoke.sh")
SCENARIOS = ["gnome", "plasma", "neon"]

FAKE_DOCKER = r"""#!/bin/bash
echo "$*" >> "$FAKE_DIR/docker_calls"
# the scenario is the argument after the script path
while [ $# -gt 0 ]; do
    if [ "$1" = "/scripts/upgrade-test.sh" ]; then scenario="$2"; fi
    shift
done
echo "fake docker output for $scenario"
case " $FAKE_FAIL " in *" $scenario "*) echo "fake docker: $scenario failed" >&2; exit 1 ;; esac
"""


def lines_of_step():
    return workflow_steps.step_lines(WORKFLOW, STEP)


def run_step(tmp_path, fail=""):
    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FAKE_DIR": str(fake),
        "FAKE_FAIL": fail,
        "UBUNTU": "24.04",
        "ARCH": "arm64",
    }
    result = subprocess.run(
        ["bash", "-e", "-c", workflow_steps.step_script(WORKFLOW, STEP)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    calls = (fake / "docker_calls").read_text().splitlines() if (fake / "docker_calls").exists() else []
    return result, calls


class TestStepInTheWorkflow:
    def names(self):
        with open(os.path.join(workflow_steps.WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
            return [l.strip()[len("- name: "):] for l in handle if l.startswith("      - name:")]

    def test_runs_after_the_packages_are_verified_and_uploaded(self):
        # After the upload: a failed upgrade test must not take the packages
        # away from a manual test of the pull request
        names = self.names()

        assert names.index(STEP) == names.index("Upload artifacts") + 1
        assert names.index(STEP) > names.index("Verify .deb files exist")

    def test_matrix_values_come_through_env(self):
        text = "\n".join(lines_of_step())

        assert "          UBUNTU: ${{ matrix.ubuntu }}" in text
        assert "          ARCH: ${{ matrix.arch }}" in text

    def test_no_expression_inside_the_script(self):
        # step_script() asserts that no ${{ is left
        assert "${{" not in workflow_steps.step_script(WORKFLOW, STEP)

    def test_all_scenarios_are_listed(self):
        script = workflow_steps.step_script(WORKFLOW, STEP)

        assert "for scenario in gnome plasma neon; do" in script
        assert "plasma-new" not in script

    def test_mounts_and_image(self):
        script = workflow_steps.step_script(WORKFLOW, STEP)

        assert '-v "$PWD/output/ubuntu${UBUNTU}-${ARCH}:/debs:ro"' in script
        assert '-v "$PWD/.github/scripts:/scripts:ro"' in script
        assert '"ubuntu:${UBUNTU}" bash /scripts/upgrade-test.sh "$scenario" /debs /out' in script

    def test_the_screenshot_directory_is_mounted_writable(self):
        script = workflow_steps.step_script(WORKFLOW, STEP)

        assert '-v "$PWD/gui-smoke:/out" \\' in script
        assert '-v "$PWD/gui-smoke:/out:ro"' not in script
        # created by the runner, not by docker (as root)
        assert script.index('mkdir -p "$PWD/gui-smoke"') < script.index("docker run")

    def test_each_scenario_has_its_own_group(self):
        script = workflow_steps.step_script(WORKFLOW, STEP)

        assert '::group::Upgrade test: $scenario' in script
        assert script.count("::endgroup::") == 1

    def test_screenshots_are_uploaded_even_after_a_failure(self):
        names = self.names()
        step = "\n".join(workflow_steps.step_lines(WORKFLOW, UPLOAD_STEP))

        assert names.index(UPLOAD_STEP) == names.index(STEP) + 1
        assert "        if: always()" in step
        assert "uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2" in step
        assert "name: gui-smoke-ubuntu-${{ matrix.ubuntu }}-${{ matrix.arch }}" in step
        assert "path: gui-smoke/*.png" in step
        assert "if-no-files-found: ignore" in step

    def test_push_is_only_the_tag_trigger(self):
        # the steps below skip "push" events: that must be tags only
        with open(os.path.join(workflow_steps.WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
            text = handle.read()

        triggers = text[text.index("\non:\n"):text.index("\npermissions:")]
        assert "  push:\n    tags:\n      - \"v*\"\n" in triggers
        assert "branches" not in triggers

    def test_the_test_and_the_upload_do_not_run_for_tags(self):
        # a network flake must not block a release
        for name in (STEP, UPLOAD_STEP):
            step = workflow_steps.step_lines(WORKFLOW, name)
            conditions = [l.strip() for l in step if l.startswith("        if:")]
            assert len(conditions) == 1, name
            assert "github.event_name != 'push'" in conditions[0], name

    def test_the_upload_still_runs_after_a_failed_test(self):
        step = "\n".join(workflow_steps.step_lines(WORKFLOW, UPLOAD_STEP))

        assert "        if: always() && github.event_name != 'push'\n" in step

    def test_the_build_jobs_get_no_write_permission_but_the_attestations(self):
        with open(os.path.join(workflow_steps.WORKFLOWS, WORKFLOW), encoding="utf-8") as handle:
            text = handle.read()

        build = text.split("\n  build:\n")[1].split("\n  release:\n")[0]
        # build, build-rpm and build-arch: the contents stay read-only, the attestations of the
        # packages (tests/unit/test_release_provenance_workflow.py) are all they may write
        assert build.count("      contents: read\n      id-token: write\n      attestations: write\n") == 3
        assert build.count("permissions:") == 3
        assert "contents: write" not in build


class TestRunningTheStep:
    def test_all_scenarios_pass(self, tmp_path):
        result, calls = run_step(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert [c.split()[-3] for c in calls] == SCENARIOS
        assert "::error::" not in result.stdout

    def test_every_call_gets_the_matrix_values(self, tmp_path):
        _, calls = run_step(tmp_path)

        for call in calls:
            assert call.startswith("run --rm -v ")
            assert f"{tmp_path}/output/ubuntu24.04-arm64:/debs:ro" in call
            assert f"{tmp_path}/.github/scripts:/scripts:ro" in call
            assert " ubuntu:24.04 bash /scripts/upgrade-test.sh " in call
            assert call.endswith(" /debs /out")
            assert f"{tmp_path}/gui-smoke:/out" in call

    @pytest.mark.parametrize("failing", SCENARIOS)
    def test_one_failure_runs_the_others_and_fails_at_the_end(self, tmp_path, failing):
        result, calls = run_step(tmp_path, fail=failing)

        assert result.returncode == 1
        assert [c.split()[-3] for c in calls] == SCENARIOS
        assert f"::error::Upgrade test failed for: {failing}" in result.stdout

    def test_all_failures_are_listed(self, tmp_path):
        result, calls = run_step(tmp_path, fail="gnome plasma neon")

        assert result.returncode == 1
        assert len(calls) == 3
        assert "::error::Upgrade test failed for: gnome plasma neon" in result.stdout

    def test_output_of_each_scenario_is_in_its_own_group(self, tmp_path):
        result, _ = run_step(tmp_path, fail="plasma")

        out = result.stdout
        for scenario in SCENARIOS:
            start = out.index(f"::group::Upgrade test: {scenario}\n")
            end = out.index("::endgroup::", start)
            assert f"fake docker output for {scenario}" in out[start:end]


class TestScript:
    def test_syntax(self):
        result = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)

        assert result.returncode == 0, result.stderr

    def test_strict_mode(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            assert "set -euo pipefail" in handle.read()

    def read(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            return handle.read()

    def test_gui_smoke_test_follows_the_pass_checks(self):
        text = self.read()
        call = 'bash "$HERE/gui-smoke.sh" "$SCENARIO" "$GUI_OUT"'

        assert text.count(call) == 1
        # the only scenario that skips itself does so at the start, never after the checks
        assert "SKIP" not in text[text.index('python3 "$HERE/check_upgrade.py"'):]
        assert text.index("check_upgrade.py") < text.index(call)
        assert text.index('echo "PASS: ') < text.index(call)
        # nothing but the end of the script follows the call
        assert text.rstrip().endswith("fi")
        assert text[text.index(call):].count("\n") <= 2

    def test_one_plasma_package_per_scenario_and_no_transitional_one(self):
        text = self.read()

        assert "plasma) PACKAGE=network-manager-gpclient-plasma ;;" in text
        assert "neon) PACKAGE=network-manager-gpclient-plasma-6 ;;" in text
        assert "plasma-5" not in text and "transitional" not in text

    def test_the_qt_directory_is_checked_for_the_plasma_and_neon_scenarios_only(self):
        text = self.read()

        assert text.count("--plasma-files") == 1
        assert ('if [ "$SCENARIO" = plasma ] || [ "$SCENARIO" = neon ]; then\n    dpkg -L "$PACKAGE"') in text

    def test_the_plugin_is_checked_in_one_place_only(self):
        # check_upgrade.py checks it (listed, on disk, Qt directory); no copy in bash
        text = self.read()

        assert "check_plugin" not in text and "plugin_of" not in text
        assert "plasmanetworkmanagement_gpclientui" not in text

    def test_gui_smoke_test_runs_only_with_an_output_directory(self):
        text = self.read()

        assert 'GUI_OUT="${3:-}"' in text
        assert 'if [ -n "$GUI_OUT" ]; then\n    bash "$HERE/gui-smoke.sh"' in text

    @pytest.mark.parametrize("scenario", ["", "kde", "GNOME", "plasma-6", "plasma-5", "plasma-new", "NEON", "neon2", "kde-neon"])
    def test_unknown_scenario_is_refused(self, scenario, tmp_path):
        result = subprocess.run(["bash", SCRIPT, scenario, str(tmp_path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "unknown scenario" in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for non-root users")
    def test_refuses_to_run_as_non_root(self, tmp_path):
        result = subprocess.run(["bash", SCRIPT, "gnome", str(tmp_path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "run as root" in result.stderr


def function_of(text, name):
    """The shell function `name` of a script"""
    start = text.index(name + "() {")
    return text[start:text.index("\n}\n", start) + 3]


def script_text():
    with open(SCRIPT, encoding="utf-8") as handle:
        return handle.read()


def make_bin(tmp_path, tools):
    """A bin directory with fake tools {name: script}"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in tools.items():
        tool = bin_dir / name
        tool.write_text("#!/bin/bash\n" + body)
        tool.chmod(0o755)
    return bin_dir


class TestReleasedSuite:
    """check_released_suite: returns 1 when the released repository has no suite for the release"""

    FAKE_CURL = r"""echo "$*" >> "$FAKE_DIR/curl_calls"
[ "$FAKE_EXIT" = 0 ] || exit "$FAKE_EXIT"
printf '%s' "$FAKE_CODE"
"""

    def run_function(self, tmp_path, code, curl_exit=0):
        bin_dir = make_bin(tmp_path, {"curl": self.FAKE_CURL})
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\nREPO_URL=https://example.org/repo\nCODENAME=oracular\n'
                  + function_of(script_text(), "check_released_suite")
                  + 'check_released_suite\necho "RETURNED $?"\n')
        return subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path), "FAKE_CODE": code,
                 "FAKE_EXIT": str(curl_exit)},
            capture_output=True, text=True, timeout=30,
        )

    def test_a_suite_that_exists_returns_zero(self, tmp_path):
        result = self.run_function(tmp_path, "200")

        assert result.returncode == 0, result.stderr
        assert result.stdout == "RETURNED 0\n"
        assert "https://example.org/repo/dists/oracular/Release" in (tmp_path / "curl_calls").read_text()

    def test_a_missing_suite_returns_one_and_does_not_exit(self, tmp_path):
        result = self.run_function(tmp_path, "404")

        assert result.returncode == 0, result.stderr
        assert result.stdout == "RETURNED 1\n"
        assert "SKIP" not in result.stdout

    @pytest.mark.parametrize("code, curl_exit", [("500", 0), ("503", 0), ("403", 0), ("301", 0), ("000", 6), ("", 28), ("000", 0)])
    def test_any_other_answer_or_a_network_error_fails(self, tmp_path, code, curl_exit):
        result = self.run_function(tmp_path, code, curl_exit)

        assert result.returncode == 1
        assert "FAIL: cannot read" in result.stderr
        assert "RETURNED" not in result.stdout

    def test_the_check_comes_before_the_released_repository_is_added(self):
        text = script_text()
        function = function_of(text, "upgrade_possible")

        assert function.index("check_released_suite || return 1") < function.index("$RELEASED_LIST")
        assert function.index("check_released_suite || return 1") < function.index("gpclient-archive-keyring.gpg")


class TestUpgradeOrFresh:
    """upgrade_possible decides between the upgrade test and the fresh install of this build"""

    FAKE_CURL = r"""echo "$*" >> "$FAKE_DIR/curl_calls"
case "$*" in
    *keyring*) [ -n "$FAKE_KEY_FAIL" ] && exit 22; printf 'KEY' ;;
    *) printf '%s' "$FAKE_CODE" ;;
esac
"""
    FAKE_APT_CACHE = r"""printf 'network-manager-gpclient-gnome:\n  Installed: (none)\n%s\n' "$FAKE_CANDIDATE"
"""
    FAKE_APT_GET = r"""echo "$*" >> "$FAKE_DIR/apt_calls"
[ -z "$FAKE_APT_FAIL" ] || exit 100
"""

    def decide(self, tmp_path, code, candidate, **fake_env):
        """(result, files) of upgrade_possible with a released repository that answers `code` for its
        Release file and shows `candidate` (a policy line, e.g. '  Candidate: 1.4.1-1~noble1')"""
        bin_dir = make_bin(tmp_path, {"curl": self.FAKE_CURL, "apt-cache": self.FAKE_APT_CACHE,
                                      "apt-get": self.FAKE_APT_GET})
        text = script_text()
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\n'
                  f'REPO_URL=https://example.org/repo\nCODENAME=noble\nARCH=arm64\nKEYRING={tmp_path}/keyring\n'
                  f'RELEASED_LIST={tmp_path}/gpclient.list\nPACKAGE=network-manager-gpclient-gnome\n'
                  + function_of(text, "check_released_suite") + function_of(text, "candidate")
                  + function_of(text, "upgrade_possible")
                  + 'if upgrade_possible; then echo UPGRADE; else echo FRESH; fi\n')
        result = subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path), "FAKE_CODE": code,
                 "FAKE_CANDIDATE": candidate, **fake_env},
            capture_output=True, text=True, timeout=30,
        )
        return result

    def test_a_released_package_means_an_upgrade(self, tmp_path):
        result = self.decide(tmp_path, "200", "  Candidate: 1.4.1-1~noble1")

        assert result.returncode == 0, result.stderr
        assert result.stdout == "UPGRADE\n"
        assert "deb [arch=arm64 signed-by=%s/keyring] https://example.org/repo noble main" % tmp_path in (
            tmp_path / "gpclient.list").read_text()
        assert (tmp_path / "keyring").read_text() == "KEY"
        assert (tmp_path / "apt_calls").read_text() == "update -qq\n"

    def test_a_suite_that_does_not_exist_means_a_fresh_install(self, tmp_path):
        result = self.decide(tmp_path, "404", "  Candidate: 1.4.1-1~noble1")

        assert result.returncode == 0, result.stderr
        assert result.stdout == "FRESH\n"
        assert not (tmp_path / "gpclient.list").exists() and not (tmp_path / "keyring").exists()

    @pytest.mark.parametrize("candidate", ["  Candidate: (none)", ""])
    def test_a_suite_without_the_package_means_a_fresh_install(self, tmp_path, candidate):
        result = self.decide(tmp_path, "200", candidate)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "FRESH\n"

    @pytest.mark.parametrize("code", ["500", "403", "000", ""])
    def test_a_broken_network_fails_instead_of_installing_fresh(self, tmp_path, code):
        result = self.decide(tmp_path, code, "  Candidate: 1.4.1-1~noble1")

        assert result.returncode == 1
        assert "FRESH" not in result.stdout and "UPGRADE" not in result.stdout
        assert "FAIL: cannot read" in result.stderr

    @pytest.mark.parametrize("fake_env, message", [
        ({"FAKE_KEY_FAIL": "1"}, "FAIL: cannot download"),
        ({"FAKE_APT_FAIL": "1"}, "FAIL: apt-get update failed"),
    ])
    def test_a_failing_keyring_download_or_apt_update_fails_instead_of_installing_fresh(
            self, tmp_path, fake_env, message):
        # upgrade_possible runs as an `if` condition (set -e is off): without the explicit
        # `|| fail` a failure here would silently turn the upgrade test into a fresh install
        result = self.decide(tmp_path, "200", "  Candidate: 1.4.1-1~noble1", **fake_env)

        assert result.returncode == 1
        assert "FRESH" not in result.stdout and "UPGRADE" not in result.stdout
        assert message in result.stderr


class TestFlow:
    """The order of the script, as text"""

    def branches(self):
        text = script_text()
        start = text.index("\nif upgrade_possible; then\n")
        middle = text.index("\nelse\n", start)
        end = text.index("\nfi\n", middle)
        return text[start:middle], text[middle:end], text[end:]

    def test_the_fresh_branch_says_why_and_installs_this_build(self):
        _, fresh, _ = self.branches()

        message = 'echo "No released $PACKAGE for $CODENAME/$ARCH: fresh install of this build instead"'
        install = 'apt-get install -y --no-install-recommends "$PACKAGE"'
        assert message in fresh
        assert fresh.index(message) < fresh.index("prepare_local_repo") < fresh.index(install)
        assert "MODE=fresh" in fresh and "MODE_ARGS=(--fresh)" in fresh
        assert 'WHEN="after the fresh install"' in fresh and 'WHEN="after the upgrade"' not in fresh
        # only this build, and no upgrade
        assert 'rm -f "$RELEASED_LIST"' in fresh and fresh.index('rm -f "$RELEASED_LIST"') < fresh.index("prepare_local_repo")
        assert "apt upgrade" not in fresh and "upgrade_packages" not in fresh and "--before" not in fresh

    def test_the_upgrade_branch_installs_the_release_first_and_upgrades(self):
        upgrade, _, _ = self.branches()

        order = ['apt-get install -y --no-install-recommends "$PACKAGE"', "before.txt", "prepare_local_repo",
                 "check_newer", 'upgrade_packages "$WORK/before.txt"']
        positions = [upgrade.index(item) for item in order]
        assert positions == sorted(positions)
        assert "MODE=upgrade" in upgrade and "MODE_ARGS=(--before" in upgrade
        assert 'WHEN="after the upgrade"' in upgrade and "after the fresh install" not in upgrade
        assert "--fresh" not in upgrade

    def test_both_paths_are_checked_by_the_same_call_and_followed_by_the_gui_smoke_test(self):
        _, _, tail = self.branches()

        call = 'python3 "$HERE/check_upgrade.py" "${MODE_ARGS[@]}"'
        assert tail.count(call) == 1
        assert "--plasma-files" in tail
        assert tail.index(call) < tail.index('echo "PASS: ') < tail.index("gui-smoke.sh")

    def test_the_local_repository_is_prepared_by_one_function(self):
        text = script_text()

        assert text.count("dpkg-scanpackages") == 1
        assert text.count("prepare_local_repo\n") == 2  # one call in each branch
        assert text.count("prepare_local_repo() {") == 1

    def test_nothing_exits_with_success_before_the_end_but_the_skip_of_the_neon_scenario(self):
        text = script_text()

        assert text.count("exit 0") == 1
        # at the start: before anything is installed or changed
        assert text.index("exit 0") < text.index("policy-rc.d") < text.index("apt-get update")
        assert 'echo "SKIP: scenario neon: $SKIP_REASON"\n        exit 0' in text


class TestVersionGuard:
    """check_newer: apt installs the build over the release only when it is greater"""

    def run_guard(self, built, released):
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\n' + function_of(script_text(), "check_newer")
                  + f'check_newer "{built}" "{released}"\necho NEWER\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)

    @pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs dpkg")
    @pytest.mark.parametrize("built, released", [
        ("1.5.0-1~noble1", "1.4.1-1~noble1"),
        ("1.5.0-1~noble1+pr31.62", "1.4.1-1~noble1"),
        ("1.5.0-1~noble1+pr31.62", "1.4.2-1~noble1+pr24.57"),
        ("1.5.1-1~noble1", "1.5.0-1~noble1"),
        ("1.5.0-1~noble1+pr31.63", "1.5.0-1~noble1+pr31.62"),
    ])
    def test_a_newer_build_passes(self, built, released):
        result = self.run_guard(built, released)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "NEWER\n"

    @pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs dpkg")
    @pytest.mark.parametrize("built, released", [
        ("1.4.1-1~noble1", "1.4.1-1~noble1"),
        ("1.4.1-1~noble1", "1.5.0-1~noble1"),
        ("1.5.0-1~noble1+pr31.62", "1.5.0-1~noble1+pr31.63"),
        ("1.5.0-1~noble1", "1.5.0-1~noble1+pr31.62"),
    ])
    def test_a_build_that_is_not_newer_fails_with_a_clear_message(self, built, released):
        result = self.run_guard(built, released)

        assert result.returncode == 1
        assert result.stderr == (f"FAIL: the build {built} is not newer than the released {released}: "
                                 "the upgrade would not install it\n")
        assert "NEWER" not in result.stdout

    def test_the_guard_runs_before_the_upgrade_and_compares_with_the_installed_release(self):
        text = script_text()

        assert "dpkg --compare-versions \"$1\" gt \"$2\"" in function_of(text, "check_newer")
        assert 'RELEASED="$(dpkg-query -W -f=\'${Version}\' network-manager-gpclient)"' in text
        assert 'check_newer "$EXPECTED" "$RELEASED"' in text
        assert text.index('RELEASED="$(dpkg-query') < text.index('check_newer "$EXPECTED"') < text.index(
            '    upgrade_packages "$WORK/before.txt"')


class TestPrepareLocalRepo:
    FAKES = {
        "dpkg-deb": r"""name="$(basename "$2")"; v="${name#*_}"; echo "${v%_*}" """,
        "dpkg-scanpackages": "echo Packages-of-the-fake\n",
        "apt-get": 'echo "$*" >> "$FAKE_DIR/apt_calls"\n',
    }

    def run_function(self, tmp_path, debs, codename="noble", arch="arm64", scenario="plasma"):
        bin_dir = make_bin(tmp_path, self.FAKES)
        debs_dir = tmp_path / "debs"
        debs_dir.mkdir()
        for name in debs:
            (debs_dir / name).write_text("")
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\n'
                  f'DEBS_DIR={debs_dir}\nLOCAL_REPO={tmp_path}/repo\nLOCAL_LIST={tmp_path}/local.list\n'
                  f'CODENAME={codename}\nARCH={arch}\nSCENARIO={scenario}\n'
                  + function_of(script_text(), "release_debs") + function_of(script_text(), "prepare_local_repo")
                  + 'prepare_local_repo\necho "EXPECTED=$EXPECTED"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                              env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)})

    def test_only_the_packages_of_this_release_and_architecture_are_used(self, tmp_path):
        result = self.run_local(tmp_path)

        assert result.returncode == 0, result.stderr
        assert "EXPECTED=1.5.0-1~noble1+pr31.62\n" in result.stdout
        assert sorted(os.listdir(tmp_path / "repo")) == sorted(
            ["network-manager-gpclient_1.5.0-1~noble1+pr31.62_arm64.deb",
             "network-manager-gpclient-gnome_1.5.0-1~noble1+pr31.62_arm64.deb", "Packages"])
        assert (tmp_path / "local.list").read_text() == "deb [trusted=yes] file:%s/repo ./\n" % tmp_path
        assert (tmp_path / "apt_calls").read_text() == "update -qq\n"

    def run_local(self, tmp_path):
        return self.run_function(tmp_path, [
            "network-manager-gpclient_1.5.0-1~noble1+pr31.62_arm64.deb",
            "network-manager-gpclient-gnome_1.5.0-1~noble1+pr31.62_arm64.deb",
            "network-manager-gpclient_1.5.0-1~noble1+pr31.62_amd64.deb",
            "network-manager-gpclient_1.5.0-1~jammy1+pr31.62_arm64.deb",
        ])

    @pytest.mark.parametrize("debs", [[], ["network-manager-gpclient_1.5.0-1~jammy1_arm64.deb"],
                                      ["network-manager-gpclient_1.5.0-1~noble1_amd64.deb"]])
    def test_no_package_for_this_release_and_architecture_fails(self, tmp_path, debs):
        result = self.run_function(tmp_path, debs)

        assert result.returncode == 1
        assert "FAIL: no .deb for noble/arm64" in result.stderr

    def test_two_versions_fail(self, tmp_path):
        result = self.run_function(tmp_path, ["network-manager-gpclient_1.5.0-1~noble1_arm64.deb",
                                              "network-manager-gpclient-gnome_1.4.1-1~noble1_arm64.deb"])

        assert result.returncode == 1
        assert "more than one version" in result.stderr


class TestGuiSmokeScript:
    def test_syntax(self):
        result = subprocess.run(["bash", "-n", GUI_SCRIPT], capture_output=True, text=True)

        assert result.returncode == 0, result.stderr

    def test_strict_mode_and_installs_without_recommends(self):
        with open(GUI_SCRIPT, encoding="utf-8") as handle:
            text = handle.read()

        assert "set -euo pipefail" in text
        assert "--no-install-recommends" in text
        for needed in ("xvfb", "xauth", "imagemagick", "gir1.2-gtk-3.0", "gir1.2-nm-1.0", "gir1.2-gtk-4.0", "python3-pyqt5", "python3-pyqt6"):
            assert needed in text

    @pytest.mark.parametrize("scenario", ["", "kde", "GNOME", "plasma-6", "plasma-5", "plasma-new"])
    def test_unknown_scenario_is_refused(self, scenario, tmp_path):
        result = subprocess.run(["bash", GUI_SCRIPT, scenario, str(tmp_path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "unknown scenario" in result.stderr

    def test_output_directory_is_required(self):
        result = subprocess.run(["bash", GUI_SCRIPT, "gnome"], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "usage: gui-smoke.sh" in result.stderr

    def test_the_plasma_probe_takes_the_plasma_package_and_for_neon_the_plasma_6_one(self):
        with open(GUI_SCRIPT, encoding="utf-8") as handle:
            text = handle.read()

        assert "PLASMA=network-manager-gpclient-plasma\n" in text
        assert '    [ "$SCENARIO" != neon ] || PLASMA=network-manager-gpclient-plasma-6\n' in text
        assert "plasma-5" not in text and "plasma-new" not in text
        assert text.count("plasma-6") == 2  # the header and the assignment

    def test_the_neon_scenario_is_the_plasma_probe_with_pyqt_of_the_plugin_directory(self):
        with open(GUI_SCRIPT, encoding="utf-8") as handle:
            text = handle.read()

        assert "    gnome | plasma | neon) ;;\n" in text
        assert "*/qt6/*) PYQT=python3-pyqt6 ;;" in text
        # neon is not a branch of its own: the one that is not gnome is the Plasma probe
        assert text.count('"$SCENARIO" = gnome') == 1
        assert '"$SCENARIO" = neon' not in text.replace('[ "$SCENARIO" != neon ]', "")

    @pytest.mark.parametrize("scenario", ["NEON", "neon2", "kde-neon", "plasma-6"])
    def test_a_scenario_that_is_not_neon_is_refused(self, scenario, tmp_path):
        result = subprocess.run(["bash", GUI_SCRIPT, scenario, str(tmp_path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "unknown scenario" in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for non-root users")
    def test_refuses_to_run_as_non_root(self, tmp_path):
        result = subprocess.run(["bash", GUI_SCRIPT, "gnome", str(tmp_path)], capture_output=True, text=True, timeout=30)

        assert result.returncode == 1
        assert "run as root" in result.stderr


class TestEndOfLifeReleases:
    """Ubuntu releases past their end of life are served by old-releases.ubuntu.com"""

    CODENAMES = {"22.04": "jammy", "24.04": "noble", "26.04": "resolute"}

    def script_text(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            return handle.read()

    def eol_in_script(self):
        match = re.search(r'^EOL_CODENAMES="([^"]*)"$', self.script_text(), re.M)
        return set(match.group(1).split())

    def eol_in_dockerfiles(self):
        # One Dockerfile.ubuntu for every release: an EOL release's sources blob
        # names its codename (Suites:)
        found = set()
        with open(os.path.join(ROOT, "Dockerfile.ubuntu"), encoding="utf-8") as handle:
            text = handle.read()
        pattern = r'echo "([A-Za-z0-9+/=]+)" \| base64 -d > /etc/apt/sources\.list\.d/ubuntu\.sources'
        for blob in re.findall(pattern, text):
            sources = base64.b64decode(blob).decode()
            if re.search(r"^URIs:\s*https?://old-releases\.ubuntu\.com/", sources, re.M):
                found.update(c for c in self.CODENAMES.values() if re.search(rf"\b{c}\b", sources))
        return found

    def test_the_set_matches_the_dockerfiles(self):
        assert self.eol_in_script() == self.eol_in_dockerfiles()

    def test_ubuntu_24_10_is_not_built_any_more(self):
        assert not os.path.exists(os.path.join(ROOT, "Dockerfile.ubuntu24.10"))

    def test_a_current_release_is_not_in_the_set(self):
        assert not self.eol_in_script() & {"jammy", "noble", "resolute"}

    def test_sources_are_replaced_before_the_first_apt_get_update(self):
        text = self.script_text()

        assert text.index("old-releases.ubuntu.com/ubuntu/") < text.index("apt-get update")

    def run_block(self, tmp_path, codename, eol="oracular"):
        """Run the EOL block with /etc/apt redirected into tmp_path"""
        text = self.script_text()
        block = text[text.index("EOL_CODENAMES="):text.index("# neon_skip_reason:")]
        # no release that is built is end of life now: pretend that one is, to test the block
        block = block.replace('EOL_CODENAMES=""', f'EOL_CODENAMES="{eol}"')
        block = block.replace('$(. /etc/os-release && echo "${VERSION_CODENAME:-}")', codename)
        block = block.replace("/etc/apt/", f"{tmp_path}/")
        (tmp_path / "sources.list.d").mkdir()
        (tmp_path / "sources.list").write_text("deb http://archive.ubuntu.com/ubuntu x main\n")
        subprocess.run(["bash", "-c", block], check=True, capture_output=True)

    def test_oracular_gets_the_old_releases_archive(self, tmp_path):
        self.run_block(tmp_path, "oracular")

        sources = (tmp_path / "sources.list.d" / "ubuntu.sources").read_text()
        assert "URIs: http://old-releases.ubuntu.com/ubuntu/\n" in sources
        assert "Suites: oracular oracular-updates oracular-backports oracular-security\n" in sources
        assert "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n" in sources
        assert (tmp_path / "sources.list").read_text() == ""

    @pytest.mark.parametrize("codename", ["jammy", "noble", "resolute", "oracular2", "x", ""])
    @pytest.mark.parametrize("eol", ["oracular", ""])
    def test_other_releases_keep_their_sources(self, tmp_path, codename, eol):
        self.run_block(tmp_path, codename, eol)

        assert not (tmp_path / "sources.list.d" / "ubuntu.sources").exists()
        assert "archive.ubuntu.com" in (tmp_path / "sources.list").read_text()


NEON_PLASMA6 = "network-manager-gpclient-plasma-6_1.5.0-1~noble1_amd64.deb"


class TestNeonScenario:
    """The scenario neon: KDE neon (Ubuntu 24.04 with the neon repository), network-manager-gpclient-plasma-6"""

    FAKE_DPKG = 'echo "$FAKE_ARCH"\n'
    FAKE_ID = 'echo 0\n'
    FAKE_APT_GET = 'echo "$*" >> "$FAKE_DIR/apt_calls"\nexit 1\n'

    def run_function(self, tmp_path, debs, codename="noble", arch="amd64"):
        """(result) of neon_skip_reason with a debs directory holding `debs`"""
        bin_dir = make_bin(tmp_path, {"dpkg": self.FAKE_DPKG})
        debs_dir = tmp_path / "debs"
        debs_dir.mkdir()
        for name in debs:
            (debs_dir / name).write_text("")
        script = (f'DEBS_DIR={debs_dir}\nOS_CODENAME={codename}\n' + function_of(script_text(), "release_debs")
                  + function_of(script_text(), "neon_skip_reason")
                  + 'neon_skip_reason\necho "RETURNED $?"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                              env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_ARCH": arch})

    def test_a_noble_build_with_the_plasma_6_package_for_the_architecture_is_tested(self, tmp_path):
        result = self.run_function(tmp_path, [NEON_PLASMA6, "network-manager-gpclient_1.5.0-1~noble1_amd64.deb"])

        assert result.returncode == 0, result.stderr
        assert result.stdout == "RETURNED 0\n"

    def test_a_pull_request_build_is_tested_too(self, tmp_path):
        result = self.run_function(tmp_path, ["network-manager-gpclient-plasma-6_1.5.0-1~noble1+pr31.62_amd64.deb"])

        assert result.stdout == "RETURNED 0\n"

    @pytest.mark.parametrize("name", [
        "network-manager-gpclient-plasma-6_1.5.0-1.noble1_amd64.deb",
        "network-manager-gpclient-plasma-6_1.5.0-1.noble1.pr31.62_amd64.deb",
        "network-manager-gpclient-plasma-6_1.5.0-1.noble1+pr31.62_amd64.deb",
    ])
    def test_the_spelling_with_a_dot_of_github_release_assets_is_tested_too(self, tmp_path, name):
        result = self.run_function(tmp_path, [name])

        assert result.stdout == "RETURNED 0\n"

    @pytest.mark.parametrize("name", [
        "network-manager-gpclient-plasma-6_1.5.0-1.jammy1_amd64.deb",
        "network-manager-gpclient-plasma-6_1.5.0-1.noble1_arm64.deb",
        "network-manager-gpclient-plasma-66_1.5.0-1.noble1_amd64.deb",
        "network-manager-gpclient-plasma_1.5.0-1.noble1_amd64.deb",
        "network-manager-gpclient-plasma-6_1.5.0-1noble1_amd64.deb",
        "network-manager-gpclient-plasma-6_1.5.0-1.noble_amd64.deb",
    ])
    def test_the_dot_spelling_of_another_package_release_or_architecture_is_skipped(self, tmp_path, name):
        result = self.run_function(tmp_path, [name])

        assert "no network-manager-gpclient-plasma-6 for noble/amd64 in" in result.stdout
        assert result.stdout.endswith("RETURNED 0\n")

    def test_the_skip_and_the_local_repository_match_the_file_names_with_the_same_function(self):
        text = script_text()

        assert text.count("release_debs() {") == 1
        assert "release_debs noble" in function_of(text, "neon_skip_reason")
        assert "release_debs " in function_of(text, "prepare_local_repo")
        # no pattern of file names outside of it
        assert text.count("_*[~.]") == 1
        assert "~noble1" not in text and "~${CODENAME}" not in text

    @pytest.mark.parametrize("codename", ["jammy", "oracular", "resolute", "", "Noble"])
    def test_another_release_is_skipped_even_with_the_package(self, tmp_path, codename):
        result = self.run_function(tmp_path, [NEON_PLASMA6], codename=codename)

        assert "KDE neon is Ubuntu 24.04 (noble), this is " in result.stdout
        assert result.stdout.endswith("RETURNED 0\n")

    @pytest.mark.parametrize("arch, debs", [
        ("arm64", [NEON_PLASMA6]),
        ("amd64", []),
        ("amd64", ["network-manager-gpclient_1.5.0-1~noble1_amd64.deb", "network-manager-gpclient-plasma_1.5.0-1~noble1_amd64.deb"]),
        ("amd64", ["network-manager-gpclient-plasma-6_1.5.0-1~jammy1_amd64.deb"]),
        ("amd64", ["network-manager-gpclient-plasma-6_1.5.0-1~noble1_arm64.deb"]),
        ("amd64", ["network-manager-gpclient-plasma-66_1.5.0-1~noble1_amd64.deb"]),
    ])
    def test_a_build_without_the_package_for_this_architecture_and_release_is_skipped(self, tmp_path, arch, debs):
        result = self.run_function(tmp_path, debs, arch=arch)

        assert f"no network-manager-gpclient-plasma-6 for noble/{arch} in" in result.stdout
        assert result.stdout.endswith("RETURNED 0\n")

    def run_script(self, tmp_path, scenario, debs, codename="noble", arch="amd64"):
        """The script as root with fakes (id, dpkg, apt-get that fails), its system paths in tmp_path.
        Returns (result, whether apt-get was called, whether policy-rc.d was written)"""
        bin_dir = make_bin(tmp_path, {"dpkg": self.FAKE_DPKG, "id": self.FAKE_ID, "apt-get": self.FAKE_APT_GET})
        debs_dir = tmp_path / "debs"
        debs_dir.mkdir()
        for name in debs:
            (debs_dir / name).write_text("")
        (tmp_path / "os-release").write_text(f"VERSION_CODENAME={codename}\n")
        (tmp_path / "apt").mkdir()
        text = script_text().replace("/usr/sbin/policy-rc.d", f"{tmp_path}/policy-rc.d").replace(
            "/etc/os-release", f"{tmp_path}/os-release").replace("/etc/apt/", f"{tmp_path}/apt/")
        (tmp_path / "upgrade-test.sh").write_text(text)
        result = subprocess.run(
            ["bash", str(tmp_path / "upgrade-test.sh"), scenario, str(debs_dir)], capture_output=True, text=True, timeout=30,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_ARCH": arch, "FAKE_DIR": str(tmp_path), "TMPDIR": str(tmp_path)},
        )
        return result, (tmp_path / "apt_calls").exists(), (tmp_path / "policy-rc.d").exists()

    def test_the_scenario_skips_itself_before_it_changes_anything(self, tmp_path):
        result, apt_called, policy_written = self.run_script(tmp_path, "neon", [])

        assert result.returncode == 0, result.stderr
        assert "SKIP: scenario neon: no network-manager-gpclient-plasma-6 for noble/amd64" in result.stdout
        assert not apt_called and not policy_written

    def test_the_scenario_skips_itself_on_another_release(self, tmp_path):
        result, apt_called, policy_written = self.run_script(tmp_path, "neon", [NEON_PLASMA6], codename="resolute")

        assert result.returncode == 0, result.stderr
        assert "SKIP: scenario neon: KDE neon is Ubuntu 24.04 (noble), this is resolute" in result.stdout
        assert not apt_called and not policy_written

    def test_the_scenario_goes_on_where_the_package_was_built(self, tmp_path):
        result, apt_called, policy_written = self.run_script(tmp_path, "neon", [NEON_PLASMA6])

        # the fake apt-get fails: the script got as far as installing
        assert result.returncode != 0
        assert "SKIP" not in result.stdout
        assert apt_called and policy_written

    @pytest.mark.parametrize("scenario", ["gnome", "plasma"])
    @pytest.mark.parametrize("debs, codename", [([], "noble"), ([NEON_PLASMA6], "noble"), ([], "resolute")])
    def test_the_other_scenarios_never_skip(self, tmp_path, scenario, debs, codename):
        result, apt_called, policy_written = self.run_script(tmp_path, scenario, debs, codename=codename)

        assert result.returncode != 0
        assert "SKIP" not in result.stdout
        assert apt_called and policy_written

    def test_the_skip_comes_before_the_policy_file_and_the_first_apt_call(self):
        text = script_text()

        assert text.index('SKIP_REASON="$(neon_skip_reason)"') < text.index("SKIP:") < text.index("policy-rc.d")
        assert text.index("policy-rc.d") < text.index("apt-get update")
        assert text.index('if [ "$SCENARIO" = neon ]; then\n    SKIP_REASON=') < text.index("policy-rc.d")

    def test_the_neon_repository_is_added_by_the_script_the_dockerfile_uses_and_only_for_neon(self):
        text = script_text()
        prepare = text[text.index('echo "::group::Prepare'):text.index('echo "::endgroup::"')]

        assert 'if [ "$SCENARIO" = neon ]; then' in prepare
        assert prepare.index("gnupg") < prepare.index('bash "$HERE/neon-repo.sh"')
        assert text.count('bash "$HERE/neon-repo.sh"') == 1
        with open(os.path.join(ROOT, "Dockerfile.ubuntu24.04-neon"), encoding="utf-8") as handle:
            assert "neon-repo.sh" in handle.read()
        # the address and the key are in the script only
        assert "neon.kde.org" not in text and "444DABCF" not in text.upper()
        assert os.path.isfile(os.path.join(ROOT, ".github", "scripts", "neon-repo.sh"))

    def test_the_repository_is_added_before_the_first_use_of_apt(self):
        text = script_text()

        # prepare, then the package is looked up in the released repository or installed
        assert text.index('bash "$HERE/neon-repo.sh"') < text.index("upgrade_possible; then") < text.index("apt-get install -y --no-install-recommends \"$PACKAGE\"")

    def test_the_scenario_checks_the_plugin_of_the_plasma_6_package(self):
        text = script_text()

        assert 'dpkg -L "$PACKAGE"' in text
        assert "neon) PACKAGE=network-manager-gpclient-plasma-6 ;;" in text


class TestNeonPackageInTheLocalRepository:
    """Only the neon scenario takes network-manager-gpclient-plasma-6 from the build: it conflicts with
    the Plasma 5 package of the other scenarios and its dependencies are from KDE neon"""

    DEBS = ["network-manager-gpclient_1.5.0-1~noble1_amd64.deb", "network-manager-gpclient-plasma_1.5.0-1~noble1_amd64.deb",
            NEON_PLASMA6, "network-manager-gpclient-gnome_1.5.0-1~noble1_amd64.deb"]

    def repo(self, tmp_path, scenario):
        result = TestPrepareLocalRepo().run_function(tmp_path, self.DEBS, codename="noble", arch="amd64", scenario=scenario)
        assert result.returncode == 0, result.stderr
        return sorted(n for n in os.listdir(tmp_path / "repo") if n.endswith(".deb"))

    def test_the_neon_scenario_takes_the_plasma_6_package(self, tmp_path):
        assert NEON_PLASMA6 in self.repo(tmp_path, "neon")

    @pytest.mark.parametrize("scenario", ["gnome", "plasma", ""])
    def test_the_other_scenarios_do_not(self, tmp_path, scenario):
        names = self.repo(tmp_path, scenario)

        assert NEON_PLASMA6 not in names
        assert sorted(names) == sorted(d for d in self.DEBS if d != NEON_PLASMA6)

    def test_every_other_package_goes_to_the_neon_scenario_too(self, tmp_path):
        assert sorted(self.repo(tmp_path, "neon")) == sorted(self.DEBS)


class TestUpgradePackages:
    """upgrade_packages: apt upgrade for gnome and plasma; in the neon scenario, with the neon repository
    enabled, only the installed network-manager-gpclient packages (--only-upgrade), not the base system"""

    BEFORE = ("network-manager-gpclient 1.4.1-1~noble1 ii \n"
              "network-manager-gpclient-plasma-6 1.4.1-1~noble1 ii \n"
              "network-manager-gpclient-gnome 1.4.1-1~noble1 rc \n"
              "network-manager-gpclient-plasma-5  un \n"
              "network-manager-gpclient-other 1.0 pn \n")
    FAKE = 'echo "$(basename "$0") $*" >> "$FAKE_DIR/calls"\n'

    def run_function(self, tmp_path, scenario, before=None):
        bin_dir = make_bin(tmp_path, {"apt": self.FAKE, "apt-get": self.FAKE})
        (tmp_path / "before.txt").write_text(self.BEFORE if before is None else before)
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\n' f'SCENARIO={scenario}\n'
                  + function_of(script_text(), "upgrade_packages")
                  + f'upgrade_packages {tmp_path}/before.txt\n')
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)})
        calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
        return result, calls

    def test_the_neon_scenario_upgrades_only_the_installed_packages_of_the_project(self, tmp_path):
        result, calls = self.run_function(tmp_path, "neon")

        assert result.returncode == 0, result.stderr
        assert calls == ["apt-get install -y --no-install-recommends --only-upgrade "
                         "network-manager-gpclient network-manager-gpclient-plasma-6"]

    @pytest.mark.parametrize("scenario", ["gnome", "plasma"])
    def test_the_other_scenarios_run_apt_upgrade(self, tmp_path, scenario):
        result, calls = self.run_function(tmp_path, scenario)

        assert result.returncode == 0, result.stderr
        assert calls == ["apt upgrade -y --no-install-recommends"]

    def test_the_neon_scenario_never_runs_apt_upgrade(self, tmp_path):
        _, calls = self.run_function(tmp_path, "neon")

        assert not [c for c in calls if c.startswith("apt ")]

    @pytest.mark.parametrize("before", ["network-manager-gpclient-plasma-5  un \n",
                                        "network-manager-gpclient 1.4.1-1~noble1 rc \n"])
    def test_the_neon_scenario_without_an_installed_package_fails_and_upgrades_nothing(self, tmp_path, before):
        result, calls = self.run_function(tmp_path, "neon", before)

        assert result.returncode == 1
        assert "FAIL: no network-manager-gpclient package is installed to upgrade" in result.stderr
        assert calls == []

    def test_the_upgrade_is_the_function_in_the_upgrade_branch_only(self):
        text = script_text()

        assert text.count("apt upgrade -y --no-install-recommends") == 1
        assert text.count('upgrade_packages "$WORK/before.txt"') == 1
        assert "apt upgrade" in function_of(text, "upgrade_packages")


class TestWording:
    """The messages say "after the upgrade" or "after the fresh install", whichever the script did"""

    def test_the_messages_use_the_wording_of_the_mode_not_the_mode_name(self):
        text = script_text()

        assert 'echo "Installed $WHEN:"' in text
        assert '|| fail "$PACKAGE is not installed $WHEN"' in text
        assert "after the $MODE" not in text
        assert text.count('WHEN="after the upgrade"') == 1 and text.count('WHEN="after the fresh install"') == 1

    def test_each_branch_sets_the_wording_of_its_own_mode(self):
        text = script_text()
        start = text.index("\nif upgrade_possible; then\n")
        middle = text.index("\nelse\n", start)

        assert text.index("MODE=upgrade", start) < text.index('WHEN="after the upgrade"') < middle
        assert middle < text.index("MODE=fresh") < text.index('WHEN="after the fresh install"')

    @pytest.mark.parametrize("mode, when", [("upgrade", "after the upgrade"), ("fresh", "after the fresh install")])
    def test_the_installed_list_and_the_failure_of_a_missing_package_name_the_mode(self, tmp_path, mode, when):
        text = script_text()
        block = text[text.index('status > "$WORK/after.txt"'):text.index('python3 "$HERE/check_upgrade.py"')]
        bin_dir = make_bin(tmp_path, {"dpkg": "exit 1\n"})
        script = ('fail() { echo "FAIL: $*" >&2; exit 1; }\nstatus() { echo "pkg 1 ii"; }\n'
                  f'WORK={tmp_path}\nSCENARIO=neon\nPACKAGE=network-manager-gpclient-plasma-6\n'
                  f'MODE={mode}\nWHEN="{when}"\n' + block)
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                                env={"PATH": f"{bin_dir}:/usr/bin:/bin"})

        assert f"Installed {when}:\n" in result.stdout
        assert result.returncode == 1
        assert f"FAIL: network-manager-gpclient-plasma-6 is not installed {when}\n" == result.stderr
        assert "after the fresh\n" not in result.stdout + result.stderr
        assert "install install" not in result.stdout + result.stderr
