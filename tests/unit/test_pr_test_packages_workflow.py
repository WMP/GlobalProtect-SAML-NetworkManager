"""
Tests for the steps of .github/workflows/pr-test-packages.yml that decide what
the "Test packages" check says and what happens to the release pr-<N>. The
steps are taken out of the workflow file and run as they are; gh is a fake
found through PATH, the helper script is the real one. Nothing touches GitHub.

Every positive test has a negative counterpart: a PR that is closed or has a
newer head keeps its release alone, packages with odd names are not uploaded,
and a failed publish never announces an install command.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import json
import os
import re
import subprocess
import sys

import pytest
import workflow_steps

WORKFLOW = "pr-test-packages.yml"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO = "WMP/GlobalProtect-SAML-NetworkManager"
SHA = "0123456789abcdef0123456789abcdef01234567"
NEWER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
BUILD_RUN_URL = f"https://github.com/{REPO}/actions/runs/111"
THIS_RUN_URL = f"https://github.com/{REPO}/actions/runs/222"
DEBS = [
    "network-manager-gpclient_1.4.2-1~noble1+pr24.57_amd64.deb",
    "network-manager-gpclient-gnome_1.4.2-1~noble1+pr24.57_amd64.deb",
]

RPMS = [
    "network-manager-gpclient-1.4.2-1.fc44.pr24.57.x86_64.rpm",
    "network-manager-gpclient-gnome-1.4.2-1.fc44.pr24.57.x86_64.rpm",
]

ARCHS = [
    "network-manager-gpclient-1.4.2.pr24.57-1-x86_64.pkg.tar.zst",
    "network-manager-gpclient-gnome-1.4.2.pr24.57-1-x86_64.pkg.tar.zst",
]

FAKE_GH = r"""#!/bin/bash
echo "$*" >> "$FAKE_DIR/gh_calls"
case "$1 $2" in
    "pr view") cat "$FAKE_DIR/pr_view" ;;
    "pr list") cat "$FAKE_DIR/pr_list" ;;
    "release view") [ -e "$FAKE_DIR/release_exists" ] ;;
    "release delete") echo "$*" >> "$FAKE_DIR/gh_deleted" ;;
    "run download")
        # --dir <dir>: one directory per artifact, like gh does
        while [ $# -gt 0 ]; do [ "$1" = "--dir" ] && dir="$2"; shift; done
        [ ! -e "$FAKE_DIR/download_fails" ] || { echo "gh: download failed" >&2; exit 1; }
        cp -r "$FAKE_DIR/artifacts" "$dir"
        ;;
esac
"""


class Box:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        self.work = tmp_path / "work"
        for directory in (self.fake, self.bin, self.work, self.work / ".github", self.fake / "artifacts"):
            directory.mkdir()
        # The step runs .github/scripts/test_packages_check.py from its working directory
        os.symlink(os.path.join(ROOT, ".github", "scripts"), self.work / ".github" / "scripts")
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.set_pr(f"OPEN {SHA}")

    def set_pr(self, text):
        (self.fake / "pr_view").write_text(text + "\n")

    def set_release(self, exists):
        path = self.fake / "release_exists"
        path.touch() if exists else path.unlink(missing_ok=True)

    def run(self, name, **env):
        script = workflow_steps.step_script(WORKFLOW, name)
        full = {
            "PATH": f"{self.bin}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            "HOME": str(self.root),
            "FAKE_DIR": str(self.fake),
            "REPO": REPO,
            "SHA": SHA,
            "PR": "24",
            "DEFAULT_BRANCH": "main",
            "RUN_ID": "111",
            "RUN_URL": BUILD_RUN_URL,
            "WORKFLOW_RUN_URL": THIS_RUN_URL,
            "GITHUB_OUTPUT": str(self.root / "output"),
        }
        full.update(env)
        return subprocess.run(
            ["bash", "-e", "-c", script], cwd=self.work, env=full, capture_output=True, text=True, timeout=60
        )

    def deleted(self):
        path = self.fake / "gh_deleted"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def outputs(box):
    path = box.root / "output"
    return dict(line.split("=", 1) for line in path.read_text().splitlines()) if path.exists() else {}


class TestDecideWhatToDo:
    PLAN = "Decide what to do"
    ENV = {"CONCLUSION": "success", "HEAD_REPO": REPO, "ACTOR": "alice", "PR_AUTHOR": "alice"}

    def plan(self, box, **env):
        result = box.run(self.PLAN, **{**self.ENV, **env})
        assert result.returncode == 0, result.stderr
        return outputs(box)

    def test_a_successful_build_of_the_own_repository_is_published(self, box):
        assert self.plan(box) == {"state": "publish"}

    @pytest.mark.parametrize("env", [
        {"ACTOR": "dependabot[bot]"},
        {"PR_AUTHOR": "dependabot[bot]"},
        {"PR_AUTHOR": "app/dependabot"},
        {"ACTOR": "dependabot[bot]", "PR_AUTHOR": "dependabot[bot]"},
    ])
    def test_a_dependabot_pull_request_is_not_published(self, box, env):
        result = self.plan(box, **env)

        assert result["state"] == "not-published"
        assert "Dependabot" in result["reason"]

    def test_a_fork_is_not_published(self, box):
        assert self.plan(box, HEAD_REPO="someone/fork")["state"] == "not-published"

    def test_a_failed_build_is_reported_as_failed_also_for_dependabot(self, box):
        assert self.plan(box, CONCLUSION="failure", ACTOR="dependabot[bot]") == {"state": "failed"}

    @pytest.mark.parametrize("actor, author", [("alice", "alice"), ("bob", "alice"), ("renovate[bot]", "bot")])
    def test_other_users_are_published(self, box, actor, author):
        assert self.plan(box, ACTOR=actor, PR_AUTHOR=author) == {"state": "publish"}

    def test_the_plan_does_not_check_out_or_run_pull_request_code(self):
        text = workflow_steps.read_workflow(WORKFLOW)
        assert text.count("actions/checkout@") == 1
        assert "ref: ${{ github.event.repository.default_branch }}" in text


class TestFindThePullRequest:
    FIND = "Find the pull request"

    @pytest.mark.parametrize("listing, number, author", [
        ("24 alice", "24", "alice"),
        ("24 dependabot[bot]", "24", "dependabot[bot]"),
        ("", "", ""),
    ])
    def test_number_and_author_are_read_from_the_listing(self, box, listing, number, author):
        (box.fake / "pr_list").write_text(listing + "\n")

        result = box.run(self.FIND)

        assert result.returncode == 0, result.stderr
        assert outputs(box) == {"number": number, "author": author}


class TestRenderTheCheck:
    def render(self, box, state, release_outcome="", published="", reason="", debs=DEBS):
        (box.work / "upload").mkdir()
        for name in debs:
            (box.work / "upload" / name).write_text("x")
        result = box.run(
            "Render the check", STATE=state, RELEASE_OUTCOME=release_outcome, PUBLISHED=published, REASON=reason
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads((box.work / "check.json").read_text())

    def test_a_published_release_gets_the_install_command(self, box):
        check = self.render(box, "publish", "success", "true")

        assert check["conclusion"] == "success"
        assert check["output"]["title"].startswith("Install: bash <(curl -fsSL ")
        assert "--repo" not in check["output"]["title"]

    def test_the_rpms_of_the_upload_directory_are_part_of_the_check(self, box):
        check = self.render(box, "publish", "success", "true", debs=DEBS + RPMS)

        assert check["conclusion"] == "success"
        assert f"Published packages ({len(DEBS) + len(RPMS)} files" in check["output"]["summary"]
        assert f"- `{RPMS[0]}`" in check["output"]["summary"]

    def test_an_upload_directory_of_rpms_only_is_published_too(self, box):
        check = self.render(box, "publish", "success", "true", debs=RPMS)

        assert check["conclusion"] == "success"
        assert f"Published packages ({len(RPMS)} files, network-manager-gpclient" in check["output"]["summary"]

    def test_the_arch_packages_of_the_upload_directory_are_part_of_the_check(self, box):
        check = self.render(box, "publish", "success", "true", debs=DEBS + ARCHS)

        assert check["conclusion"] == "success"
        assert f"Published packages ({len(DEBS) + len(ARCHS)} files" in check["output"]["summary"]
        assert f"- `{ARCHS[0]}`" in check["output"]["summary"]
        assert "pacman -U" in check["output"]["summary"]

    def test_the_check_of_a_release_without_arch_packages_has_nothing_about_pacman(self, box):
        check = self.render(box, "publish", "success", "true", debs=DEBS + RPMS)

        assert "pacman" not in check["output"]["summary"]

    def test_another_repository_is_named_in_the_command(self, box):
        (box.work / "upload").mkdir()
        (box.work / "upload" / DEBS[0]).write_text("x")

        result = box.run("Render the check", STATE="publish", RELEASE_OUTCOME="success", PUBLISHED="true",
                         REPO="someone/fork")

        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads((box.work / "check.json").read_text())["output"]["title"].endswith(" 24 --repo someone/fork")

    def test_a_failed_build_is_reported_with_the_build_run(self, box):
        check = self.render(box, "failed")

        assert check["output"]["title"] == "No test packages: the build failed"
        assert BUILD_RUN_URL in check["output"]["summary"]

    def test_a_pr_that_changed_while_publishing_is_not_published(self, box):
        check = self.render(box, "publish", "success", "false")

        assert check["conclusion"] == "neutral"
        assert check["output"]["title"] == "No test packages: the pull request is closed or has a newer commit"

    def test_a_fork_is_not_published(self, box):
        check = self.render(box, "not-published", reason="pull requests from forks are not published")

        assert check["output"]["title"] == "No test packages: pull requests from forks are not published"

    @pytest.mark.parametrize(
        "release_outcome, published",
        [
            ("failure", ""),       # gh release create or an upload failed
            ("skipped", ""),       # the download failed, so the release step never ran
            ("cancelled", ""),
            ("failure", "true"),   # the outcome counts, not a leftover output
            ("failure", "false"),
            ("", ""),
        ],
    )
    def test_a_publish_that_did_not_finish_says_publishing_failed(self, box, release_outcome, published):
        check = self.render(box, "publish", release_outcome, published)

        assert check["conclusion"] == "neutral"
        assert check["output"]["title"] == "No test packages: publishing failed"
        assert THIS_RUN_URL in check["output"]["summary"]
        assert BUILD_RUN_URL not in check["output"]["summary"]
        assert "install-pr-build.sh" not in json.dumps(check)

    def test_no_plan_at_all_says_publishing_failed_and_not_an_install_command(self, box):
        check = self.render(box, "")

        assert check["output"]["title"] == "No test packages: publishing failed"


class TestRemoveOutdatedPackages:
    STEP = "Remove outdated packages"

    def test_the_release_of_the_current_head_of_an_open_pr_is_deleted(self, box):
        box.set_release(True)

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert box.deleted() == [f"release delete pr-24 --repo {REPO} --cleanup-tag --yes"]

    def test_the_pr_is_looked_at_before_anything_is_deleted(self, box):
        box.set_release(True)

        box.run(self.STEP)

        calls = (box.fake / "gh_calls").read_text().splitlines()
        assert calls[0].startswith("pr view 24 ")
        assert calls.index(f"release delete pr-24 --repo {REPO} --cleanup-tag --yes") > 0

    @pytest.mark.parametrize(
        "state",
        [f"OPEN {NEWER_SHA}", f"CLOSED {SHA}", f"MERGED {SHA}", f"CLOSED {NEWER_SHA}", f"MERGED {NEWER_SHA}", "OPEN ", ""],
    )
    def test_a_closed_pr_or_a_newer_head_leaves_the_release_alone(self, box, state):
        box.set_release(True)
        box.set_pr(state)

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "leaving release pr-24 alone" in result.stdout
        assert box.deleted() == []
        assert "release view" not in (box.fake / "gh_calls").read_text()

    def test_without_a_release_there_is_nothing_to_delete(self, box):
        box.set_release(False)

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert box.deleted() == []


class TestDownload:
    STEP = "Download the built packages"

    def artifacts(self, box, names_by_dir):
        for directory, names in names_by_dir.items():
            (box.fake / "artifacts" / directory).mkdir(parents=True, exist_ok=True)
            for name in names:
                (box.fake / "artifacts" / directory / name).write_text(name)

    def test_only_the_plain_package_names_are_uploaded(self, box):
        self.artifacts(box, {"debs-ubuntu-24.04-amd64": DEBS, "debs-x": ["a b.deb", "x.deb", "evil;rm_1.0_amd64.deb", "notes.txt"]})

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert sorted(os.listdir(box.work / "upload")) == sorted(DEBS)
        assert result.stderr.count("skipping a package with an unexpected file name") == 3

    def test_the_rpm_artifacts_are_downloaded_and_only_plain_rpm_names_are_uploaded(self, box):
        self.artifacts(box, {
            "debs-ubuntu-24.04-amd64": DEBS,
            "rpms-fedora44-x86_64": RPMS + ["network-manager-gpclient-1.4.2-1.fc44.src.rpm"],
            "rpms-x": ["a b.rpm", "x.rpm", "network-manager-gpclient-debuginfo-1.4.2-1.fc44.x86_64.rpm", "evil;rm.rpm"],
        })

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert sorted(os.listdir(box.work / "upload")) == sorted(DEBS + RPMS)
        assert result.stderr.count("skipping a package with an unexpected file name") == 4
        gh_calls = (box.fake / "gh_calls").read_text()
        assert "--pattern debs-*" in gh_calls and "--pattern rpms-*" in gh_calls

    def test_the_arch_artifact_is_downloaded_and_only_plain_arch_names_are_uploaded(self, box):
        self.artifacts(box, {
            "debs-ubuntu-24.04-amd64": DEBS,
            "arch-x86_64": ARCHS + [ARCHS[0] + ".sig", "network-manager-gpclient-debug-1.4.2-1-x86_64.pkg.tar.zst",
                                    "evil;rm.pkg.tar.zst"],
        })

        result = box.run(self.STEP)

        assert result.returncode == 0, result.stdout + result.stderr
        assert sorted(os.listdir(box.work / "upload")) == sorted(DEBS + ARCHS)
        assert result.stderr.count("skipping a package with an unexpected file name") == 2
        assert "--pattern arch-*" in (box.fake / "gh_calls").read_text()

    def test_arch_files_that_are_not_plain_are_not_enough(self, box):
        self.artifacts(box, {"arch-x86_64": ["a b.pkg.tar.zst", "x.pkg.tar.zst"]})

        result = box.run(self.STEP)

        assert result.returncode != 0
        assert "no packages found" in result.stderr
        assert not (box.work / "upload").exists()

    def test_rpms_that_are_not_plain_are_not_enough(self, box):
        self.artifacts(box, {"rpms-x": ["a b.rpm", "x.rpm", "network-manager-gpclient-1.4.2-1.fc44.src.rpm"]})

        result = box.run(self.STEP)

        assert result.returncode != 0
        assert "no packages found" in result.stderr
        assert not (box.work / "upload").exists()

    def test_no_acceptable_package_fails_the_step_and_uploads_nothing(self, box):
        self.artifacts(box, {"debs-x": ["a b.deb", "x.deb"]})

        result = box.run(self.STEP)

        assert result.returncode != 0
        assert "no packages found" in result.stderr
        assert not (box.work / "upload").exists()

    def test_a_failed_download_fails_the_step(self, box):
        self.artifacts(box, {"debs-x": DEBS})
        (box.fake / "download_fails").touch()

        result = box.run(self.STEP)

        assert result.returncode != 0
        assert not (box.work / "upload").exists()


class TestWorkflowWiring:
    @pytest.mark.parametrize("name", ["Render the check", "Create or update the check run", "Remove outdated packages"])
    def test_these_steps_run_after_a_failed_publish_too(self, name):
        block = "\n".join(workflow_steps.step_lines(WORKFLOW, name))

        assert re.search(r"\n        if: (>-\s+)?always\(\) && steps\.pr\.outputs\.number != ''", block), block

    @pytest.mark.parametrize("name", ["Download the built packages", "Publish the prerelease"])
    def test_publishing_steps_stop_at_the_first_failure(self, name):
        block = "\n".join(workflow_steps.step_lines(WORKFLOW, name))

        assert "always()" not in block
        assert "if: steps.plan.outputs.state == 'publish'" in block

    def test_the_check_is_only_posted_when_it_was_rendered(self):
        block = "\n".join(workflow_steps.step_lines(WORKFLOW, "Create or update the check run"))

        assert "steps.render.outcome == 'success'" in block

    def test_the_outdated_release_is_removed_after_a_failed_build_or_a_failed_publish(self):
        block = "\n".join(workflow_steps.step_lines(WORKFLOW, "Remove outdated packages"))

        assert "steps.plan.outputs.state == 'failed'" in block
        assert "steps.release.outputs.published == ''" in block
