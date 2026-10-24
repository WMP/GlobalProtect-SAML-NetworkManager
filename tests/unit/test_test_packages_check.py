"""
Tests for .github/scripts/test_packages_check.py, which renders the "Test
packages" check run of a pull request: the install command as the title (shown
next to the check in the merge box) and what it does in the summary.

Every positive test has negative counterparts: a failed build, a failed
publish or an unpublished release yields a neutral check without an install
command, and bad input is refused instead of ending up in a check.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts", "test_packages_check.py")
)
spec = importlib.util.spec_from_file_location("test_packages_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

REPO = "WMP/GlobalProtect-SAML-NetworkManager"
SHA = "0123456789abcdef0123456789abcdef01234567"
PR = "24"
RUN_URL = f"https://github.com/{REPO}/actions/runs/123456"
# Release builds and the builds of a pull request (+pr<PR>.<run>) are both named
# like this by dpkg
DEBS = [
    "network-manager-gpclient_1.4.2-1~noble1_amd64.deb",
    "network-manager-gpclient-gnome_1.4.2-1~noble1_amd64.deb",
    "network-manager-gpclient-plasma_1.4.2-1~noble1_amd64.deb",
    "network-manager-gpclient_1.4.2-1~resolute1_arm64.deb",
    "network-manager-gpclient-plasma_1.4.2-1~resolute1_arm64.deb",
]
PR_DEBS = [name.replace("1_", "1+pr24.57_") for name in DEBS]
COMMAND = (
    f"bash <(curl -fsSL https://raw.githubusercontent.com/{REPO}/main/scripts/install-pr-build.sh) {PR}"
)


def on_github(name):
    """How GitHub names the release asset"""
    return name.replace("~", ".")


def published(**overrides):
    args = dict(state="published", pr=PR, sha=SHA, repo=REPO, debs=DEBS)
    args.update(overrides)
    return check.render(**args)


class TestPublished:
    def test_the_check_is_completed_and_successful_on_the_head_commit(self):
        result = published()

        assert result["name"] == "Test packages"
        assert result["head_sha"] == SHA
        assert result["status"] == "completed"
        assert result["conclusion"] == "success"
        assert result["details_url"] == f"https://github.com/{REPO}/releases/tag/pr-{PR}"

    def test_the_title_is_the_install_command_with_the_pr_number(self):
        assert published()["output"]["title"] == f"Install: {COMMAND}"

    def test_the_command_fetches_the_script_from_the_default_branch_not_the_pr(self):
        title = published(script_ref="trunk")["output"]["title"]

        assert f"/{REPO}/trunk/scripts/install-pr-build.sh) {PR}" in title
        assert SHA not in title

    def test_the_command_is_not_a_pipe_into_bash(self):
        summary = published()["output"]["summary"]

        assert "curl -fsSL" in summary
        assert "| bash" not in summary.replace("`curl ... | bash`", "")

    def test_the_summary_has_the_command_in_a_code_block(self):
        summary = published()["output"]["summary"]

        assert f"```bash\n{COMMAND}\n```" in summary

    def test_the_summary_says_what_the_script_detects(self):
        summary = published()["output"]["summary"]

        for text in ("/etc/os-release", "amd64 or arm64", "XDG_CURRENT_DESKTOP", "--desktop gnome", "sudo pip3 install sdbus"):
            assert text in summary

    def test_the_summary_lists_the_published_files_sorted(self):
        summary = published(debs=list(reversed(DEBS)))["output"]["summary"]

        positions = [summary.index(f"- `{on_github(name)}`") for name in sorted(DEBS, key=on_github)]
        assert positions == sorted(positions)
        assert f"Published packages ({len(DEBS)} files" in summary

    @pytest.mark.parametrize("debs", [DEBS, PR_DEBS])
    def test_the_listed_names_are_the_ones_of_the_release_assets(self, debs):
        summary = published(debs=debs)["output"]["summary"]
        listed = [line[3:-1] for line in summary.splitlines() if line.startswith("- `")]

        assert sorted(listed) == sorted(on_github(name) for name in debs)
        assert all("~" not in name for name in listed)
        assert not any(f"`{name}`" in summary for name in debs)

    def test_a_pr_build_is_listed_and_accepted_like_a_release_build(self):
        result = published(debs=PR_DEBS)

        assert result["conclusion"] == "success"
        assert "- `network-manager-gpclient-plasma_1.4.2-1.resolute1+pr24.57_arm64.deb`" in result["output"]["summary"]

    def test_the_summary_says_the_pr_version_is_above_the_release(self):
        summary = published()["output"]["summary"]

        assert "+pr24.<run>" in summary
        assert "installs it over the release" in summary

    def test_the_summary_names_the_plasma_package_and_the_one_for_kde_neon(self):
        summary = published()["output"]["summary"]

        assert "# or -plasma (-plasma-6 on KDE neon), as installed" in summary
        assert "On KDE neon (Ubuntu 24.04 with Plasma 6) the Plasma package is `network-manager-gpclient-plasma-6`." in summary
        assert "-plasma-5" not in summary
        assert "plasma-nm" not in summary

    def test_the_package_for_kde_neon_is_listed_by_name(self):
        debs = DEBS + ["network-manager-gpclient-plasma-6_1.4.2-1~noble1_amd64.deb"]

        summary = published(debs=debs)["output"]["summary"]

        assert ("Published packages (6 files, network-manager-gpclient, network-manager-gpclient-gnome, "
                "network-manager-gpclient-plasma, network-manager-gpclient-plasma-6)") in summary
        assert "- `network-manager-gpclient-plasma-6_1.4.2-1.noble1_amd64.deb`" in summary
        assert check.PACKAGE_RE.match(debs[-1]).group(1) == "network-manager-gpclient-plasma-6"

    def test_the_package_for_kde_neon_is_accepted_for_upload(self):
        result = published(debs=["network-manager-gpclient-plasma-6_1.4.2-1~noble1+pr24.57_amd64.deb"])

        assert result["conclusion"] == "success"

    @pytest.mark.parametrize("package", [
        "network-manager-gpclient-plasma-5", "network-manager-gpclient-plasma-7", "network-manager-gpclient-plasma-66",
        "network-manager-gpclient-plasma-6-extra", "network-manager-gpclient-plasma6",
    ])
    def test_another_plasma_package_is_not_listed_by_name(self, package):
        assert check.PACKAGE_RE.match(package + "_1.4.2-1~noble1_amd64.deb") is None

    def test_the_default_repository_adds_no_repo_option(self):
        result = published()

        assert "--repo" not in result["output"]["title"]
        assert "--repo" not in result["output"]["summary"]

    @pytest.mark.parametrize("repo", ["someone/GlobalProtect-fork", "WMP/other", "wmp/GlobalProtect-SAML-NetworkManager"])
    def test_another_repository_is_named_in_the_command(self, repo):
        result = published(repo=repo)
        command = (
            f"bash <(curl -fsSL https://raw.githubusercontent.com/{repo}/main/scripts/install-pr-build.sh)"
            f" {PR} --repo {repo}"
        )

        assert result["output"]["title"] == f"Install: {command}"
        assert f"```bash\n{command}\n```" in result["output"]["summary"]
        assert result["details_url"] == f"https://github.com/{repo}/releases/tag/pr-{PR}"

    def test_the_summary_says_how_to_go_back(self):
        summary = published()["output"]["summary"]

        assert (
            "sudo apt update && sudo apt install --allow-downgrades "
            "network-manager-gpclient/$(lsb_release -cs) network-manager-gpclient-gnome/$(lsb_release -cs)"
        ) in summary
        assert "sudo apt remove network-manager-gpclient" in summary
        assert "docs/APT_REPO.md" in summary

    def test_the_way_back_does_not_reinstall_and_names_the_suite(self):
        summary = published()["output"]["summary"]
        commands = [line for line in summary.splitlines() if line.startswith("sudo apt update && sudo apt install")]

        assert len(commands) == 1
        assert "--reinstall" not in summary
        words = commands[0].split("#")[0].replace("$(lsb_release -cs)", "SUITE").split()
        names = [word for word in words if word.startswith("network-manager-gpclient")]
        assert names
        assert all(name.endswith("/SUITE") for name in names), commands[0]

    def test_the_summary_links_the_release_and_names_the_commit(self):
        summary = published()["output"]["summary"]

        assert f"[`pr-{PR}`](https://github.com/{REPO}/releases/tag/pr-{PR})" in summary
        assert f"commit `{SHA[:7]}`" in summary
        assert "Unreviewed test build" in summary


class TestNoPackages:
    def test_a_failed_build_is_neutral_and_has_no_install_command(self):
        result = check.render("failed", PR, SHA, REPO, run_url=RUN_URL)

        assert result["conclusion"] == "neutral"
        assert result["output"]["title"] == "No test packages: the build failed"
        assert RUN_URL in result["output"]["summary"]
        assert "install-pr-build.sh" not in json.dumps(result)
        assert "details_url" not in result

    def test_a_failed_build_without_a_run_link_still_works(self):
        result = check.render("failed", PR, SHA, REPO)

        assert result["conclusion"] == "neutral"
        assert "actions/runs" not in result["output"]["summary"]

    def test_a_failed_build_ignores_the_packages_it_is_given(self):
        result = check.render("failed", PR, SHA, REPO, debs=DEBS)

        assert ".deb" not in json.dumps(result)

    def test_a_failed_publish_is_neutral_and_links_the_workflow_run(self):
        result = check.render("publish-failed", PR, SHA, REPO, run_url=RUN_URL)

        assert result["name"] == "Test packages"
        assert result["head_sha"] == SHA
        assert result["status"] == "completed"
        assert result["conclusion"] == "neutral"
        assert result["output"]["title"] == "No test packages: publishing failed"
        assert f"[workflow run]({RUN_URL})" in result["output"]["summary"]
        assert f"commit `{SHA[:7]}`" in result["output"]["summary"]
        assert "install-pr-build.sh" not in json.dumps(result)
        assert "details_url" not in result

    def test_a_failed_publish_without_a_run_link_still_works(self):
        result = check.render("publish-failed", PR, SHA, REPO)

        assert result["conclusion"] == "neutral"
        assert "actions/runs" not in result["output"]["summary"]

    def test_a_failed_publish_ignores_the_packages_it_is_given(self):
        result = check.render("publish-failed", PR, SHA, REPO, debs=DEBS, run_url=RUN_URL)

        assert ".deb" not in json.dumps(result)

    def test_a_failed_publish_is_not_the_failed_build(self):
        build = check.render("failed", PR, SHA, REPO, run_url=RUN_URL)
        publish = check.render("publish-failed", PR, SHA, REPO, run_url=RUN_URL)

        assert build["output"]["title"] != publish["output"]["title"]
        assert "build" not in publish["output"]["title"]

    def test_not_published_says_why(self):
        reason = "pull requests from forks are not published"

        result = check.render("not-published", PR, SHA, REPO, reason=reason)

        assert result["conclusion"] == "neutral"
        assert result["output"]["title"] == f"No test packages: {reason}"
        assert "install-pr-build.sh" not in json.dumps(result)

    @pytest.mark.parametrize("run_url", ["http://github.com/a/b/actions/runs/1", "https://evil.example/actions/runs/1",
                                          f"{RUN_URL})[x](https://evil.example", "javascript:alert(1)"])
    def test_a_run_link_that_is_not_a_github_run_is_refused(self, run_url):
        for state in ("failed", "publish-failed"):
            with pytest.raises(check.CheckError):
                check.render(state, PR, SHA, REPO, run_url=run_url)

    @pytest.mark.parametrize("reason", ["", "x" * 201, "line\nbreak", "`code`", "<b>html</b>", "[a](b)"])
    def test_a_reason_must_be_plain(self, reason):
        with pytest.raises(check.CheckError):
            check.render("not-published", PR, SHA, REPO, reason=reason)


class TestRefused:
    @pytest.mark.parametrize("pr", ["abc", "0", "007", "12345678", "", "-1", "1;rm", "24 ", "24\n"])
    def test_bad_pr_numbers(self, pr):
        for state in ("published", "failed", "publish-failed", "not-published"):
            with pytest.raises(check.CheckError):
                check.render(state, pr, SHA, REPO, debs=DEBS, reason="because")

    @pytest.mark.parametrize("sha", ["", "abc", SHA[:-1], SHA.upper(), SHA + "0", "g" * 40, SHA + "\n"])
    def test_bad_commit_hashes(self, sha):
        with pytest.raises(check.CheckError):
            published(sha=sha)

    @pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "a b/c", "a/b;x", "a/b\n"])
    def test_bad_repositories(self, repo):
        with pytest.raises(check.CheckError):
            published(repo=repo)

    @pytest.mark.parametrize("ref", ["", "a b", "main;x", "$(x)", "main)", "main\n"])
    def test_bad_script_refs(self, ref):
        with pytest.raises(check.CheckError):
            published(script_ref=ref)

    def test_an_empty_release_is_not_announced(self):
        with pytest.raises(check.CheckError, match="no packages"):
            published(debs=[])

    @pytest.mark.parametrize(
        "name",
        ["../evil.deb", "a/b.deb", ".hidden.deb", "a b.deb", "x.deb;rm", "x.ddeb", "x.deb.sig", "`x`.deb", "x|y.deb", "",
         "x.deb", "pkg_1.0.deb", "pkg_1.0_amd64", "_1.0_amd64.deb", "pkg__amd64.deb", "pkg_1.0_amd64.deb\n",
         "pkg_1.0_amd64.deb ", "Pkg_1.0_amd64.deb", "pkg_v1_amd64.deb", "pkg_1.0_amd64_x.deb", "pkg_1.0;x_amd64.deb",
         "pkg_1.0_AMD64.deb", "pkg_1.0~_amd64.deb\x00"],
    )
    def test_package_names_that_are_not_plain_are_refused(self, name):
        with pytest.raises(check.CheckError, match="unexpected package file name"):
            published(debs=DEBS + [name])

    @pytest.mark.parametrize(
        "name",
        ["network-manager-gpclient_1.4.2-1~noble1_amd64.deb", "network-manager-gpclient_1.4.2-1~noble1+pr24.57_arm64.deb",
         "network-manager-gpclient-plasma_1.4.2-1~resolute1+pr1.1234567_amd64.deb", "pkg_1.0_all.deb"],
    )
    def test_release_and_pr_build_names_are_plain_names(self, name):
        assert check.DEB_RE.fullmatch(name)

    def test_an_unknown_state(self):
        with pytest.raises(check.CheckError, match="unknown state"):
            check.render("done", PR, SHA, REPO)


class TestCommandLine:
    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_published_prints_the_check_as_json(self, tmp_path):
        for name in DEBS + ["README.txt"]:
            (tmp_path / name).write_text("x")

        result = self.run("--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO, "--debs-dir", str(tmp_path))

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["conclusion"] == "success"
        assert data["output"]["title"] == f"Install: {COMMAND}"
        assert "README.txt" not in data["output"]["summary"]
        assert f"Published packages ({len(DEBS)} files" in data["output"]["summary"]
        assert f"- `{on_github(DEBS[0])}`" in data["output"]["summary"]

    def test_published_names_the_repository_when_it_is_not_the_default(self, tmp_path):
        for name in PR_DEBS:
            (tmp_path / name).write_text("x")

        result = self.run("--state", "published", "--pr", PR, "--sha", SHA, "--repo", "someone/fork",
                          "--debs-dir", str(tmp_path))

        assert result.returncode == 0, result.stderr
        title = json.loads(result.stdout)["output"]["title"]
        assert title.endswith(f"install-pr-build.sh) {PR} --repo someone/fork")

    def test_publish_failed_prints_a_neutral_check(self):
        result = self.run("--state", "publish-failed", "--pr", PR, "--sha", SHA, "--repo", REPO, "--run-url", RUN_URL)

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["conclusion"] == "neutral"
        assert data["output"]["title"] == "No test packages: publishing failed"
        assert RUN_URL in data["output"]["summary"]

    def test_publish_failed_refuses_a_run_link_that_is_not_a_run(self):
        result = self.run("--state", "publish-failed", "--pr", PR, "--sha", SHA, "--repo", REPO,
                          "--run-url", "https://evil.example/x")

        assert result.returncode == 1 and result.stdout == ""
        assert "not a workflow run url" in result.stderr

    def test_failed_prints_a_neutral_check(self):
        result = self.run("--state", "failed", "--pr", PR, "--sha", SHA, "--repo", REPO, "--run-url", RUN_URL)

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["conclusion"] == "neutral"
        assert data["output"]["title"] == "No test packages: the build failed"

    def test_an_empty_packages_directory_is_an_error_and_prints_nothing(self, tmp_path):
        result = self.run("--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO, "--debs-dir", str(tmp_path))

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no packages" in result.stderr

    def test_a_directory_that_does_not_exist_is_an_error(self, tmp_path):
        result = self.run(
            "--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO, "--debs-dir", str(tmp_path / "nope")
        )

        assert result.returncode == 1 and result.stdout == ""
        assert "Traceback" not in result.stderr

    def test_a_package_with_an_odd_name_is_an_error(self, tmp_path):
        (tmp_path / "a b.deb").write_text("x")

        result = self.run("--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO, "--debs-dir", str(tmp_path))

        assert result.returncode == 1 and result.stdout == ""
        assert "unexpected package file name" in result.stderr

    @pytest.mark.parametrize(
        "args",
        [
            [],
            ["--state", "published"],
            ["--state", "bogus", "--pr", PR, "--sha", SHA, "--repo", REPO],
            ["--state", "failed", "--pr", "abc", "--sha", SHA, "--repo", REPO],
            ["--state", "failed", "--pr", PR, "--sha", "abc", "--repo", REPO],
            ["--state", "failed", "--pr", PR, "--sha", SHA],
            ["--pr", PR, "--sha", SHA, "--repo", REPO],
            ["--copy-to", "somewhere"],
        ],
    )
    def test_bad_arguments_are_rejected(self, args):
        result = self.run(*args)

        assert result.returncode != 0
        assert result.stdout == ""


class TestFilter:
    """--filter DIR: the one definition of an acceptable package name, used by the workflow"""

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def artifacts(self, tmp_path, names_by_dir):
        root = tmp_path / "artifacts"
        for directory, names in names_by_dir.items():
            (root / directory).mkdir(parents=True, exist_ok=True)
            for name in names:
                (root / directory / name).write_text(f"content of {name}")
        return root

    def test_the_acceptable_names_below_the_directory_are_printed_sorted(self, tmp_path):
        root = self.artifacts(tmp_path, {
            "debs-ubuntu-24.04-amd64": PR_DEBS[:3],
            "debs-ubuntu-26.04-arm64": PR_DEBS[3:],
        })

        result = self.run("--filter", str(root))

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == sorted(PR_DEBS)
        assert result.stderr == ""

    def test_release_style_names_are_accepted_too(self, tmp_path):
        root = self.artifacts(tmp_path, {"a": DEBS})

        result = self.run("--filter", str(root))

        assert result.stdout.splitlines() == sorted(DEBS)

    def test_the_files_are_copied_flat_with_their_content(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": PR_DEBS[:2], "two": PR_DEBS[2:]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 0, result.stderr
        assert sorted(os.listdir(tmp_path / "upload")) == sorted(PR_DEBS)
        for name in PR_DEBS:
            assert (tmp_path / "upload" / name).read_text() == f"content of {name}"

    def test_nothing_is_copied_without_the_option(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": DEBS})

        self.run("--filter", str(root))

        assert not (tmp_path / "upload").exists()

    @pytest.mark.parametrize(
        "bad",
        ["a b.deb", "x.deb", "pkg_1.0.deb", "Pkg_1.0_amd64.deb", "a`x`_1.0_amd64.deb", "$(x)_1.0_amd64.deb",
         "x\n::error::injected_1.0_amd64.deb"],
    )
    def test_other_names_are_neither_printed_nor_copied_and_reported_safely(self, tmp_path, bad):
        root = self.artifacts(tmp_path, {"one": [DEBS[0], bad]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [DEBS[0]]
        assert os.listdir(tmp_path / "upload") == [DEBS[0]]
        assert "skipping a package with an unexpected file name" in result.stderr
        # Escaped: a hostile name cannot start a workflow command in the log
        assert not any(line.startswith("::") for line in result.stderr.splitlines())
        assert result.stderr.count("\n") == 1, "the name is on one line"

    @pytest.mark.parametrize("other", ["pkg_1.0_amd64.ddeb", "pkg_1.0_amd64.deb.sig", "README.txt", "checksums", "x.deb;rm", "pkg_1.0_amd64.deb\n"])
    def test_files_that_are_no_debs_are_ignored_silently(self, tmp_path, other):
        root = self.artifacts(tmp_path, {"one": [DEBS[0], other]})

        result = self.run("--filter", str(root))

        assert result.stdout.splitlines() == [DEBS[0]]
        assert result.stderr == ""

    def test_a_link_to_a_package_is_not_followed(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": [DEBS[0]]})
        secret = tmp_path / "secret"
        secret.write_text("not a package")
        (root / "one" / DEBS[1]).symlink_to(secret)

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.stdout.splitlines() == [DEBS[0]]
        assert os.listdir(tmp_path / "upload") == [DEBS[0]]
        assert "skipping" in result.stderr

    def test_a_directory_named_like_a_package_is_not_one(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": [DEBS[0]]})
        (root / "one" / DEBS[1]).mkdir()

        result = self.run("--filter", str(root))

        assert result.stdout.splitlines() == [DEBS[0]]

    @pytest.mark.parametrize("names_by_dir", [{}, {"one": []}, {"one": ["README.txt", "a b.deb"]}])
    def test_no_acceptable_package_is_an_error_and_copies_nothing(self, tmp_path, names_by_dir):
        root = self.artifacts(tmp_path, names_by_dir)
        root.mkdir(exist_ok=True)

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no packages found" in result.stderr
        assert not (tmp_path / "upload").exists()

    def test_a_directory_that_does_not_exist_is_an_error(self, tmp_path):
        result = self.run("--filter", str(tmp_path / "nope"))

        assert result.returncode == 1 and result.stdout == ""
        assert "Traceback" not in result.stderr

    def test_the_same_package_twice_is_an_error_and_copies_nothing(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": [DEBS[0]], "two": [DEBS[0]]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 1 and result.stdout == ""
        assert "in the build twice" in result.stderr
        assert not (tmp_path / "upload").exists()

    @pytest.mark.parametrize(
        "name",
        DEBS + PR_DEBS + ["a b.deb", "x.deb", "x.deb;rm", "pkg_1.0_amd64.deb", "../x_1_amd64.deb", "pkg_1.0_amd64.deb\n",
                          "network-manager-gpclient_1.4.2-1~noble1+pr24.57_amd64.deb ", "pkg_1_2_3.deb"],
    )
    def test_filter_and_check_agree_on_what_a_plain_name_is(self, tmp_path, name):
        """One regex: what --filter lets through, the check accepts, and the other way round"""
        try:
            check.render("published", PR, SHA, REPO, debs=[name])
            rendered = True
        except check.CheckError:
            rendered = False
        filtered = bool(check.DEB_RE.fullmatch(name)) and "/" not in name

        assert rendered == filtered

    def test_the_workflow_uses_the_script_and_has_no_name_pattern_of_its_own(self):
        workflow = os.path.join(os.path.dirname(SCRIPT), "..", "workflows", "pr-test-packages.yml")
        with open(workflow, encoding="utf-8") as handle:
            text = handle.read()

        assert "test_packages_check.py --filter artifacts --copy-to upload" in text
        assert ".deb$" not in text
        assert "A-Za-z0-9._+~-" not in text


# --- RPM packages --------------------------------------------------------------------------------

RPMS = [
    "network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-gnome-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-plasma-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-1.4.2-1.el10.aarch64.rpm",
    "network-manager-gpclient-gnome-1.4.2-1.lp160.aarch64.rpm",
    "network-manager-gpclient-plasma-1.4.2-1.tw.aarch64.rpm",
]
PR_RPMS = [name.replace(".x86_64", ".pr24.57.x86_64").replace(".aarch64", ".pr24.57.aarch64") for name in RPMS]
BAD_RPMS = [
    "network-manager-gpclient-1.4.2-1.fc44.src.rpm",
    "network-manager-gpclient-debuginfo-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-debugsource-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-plasma-6-1.4.2-1.fc44.x86_64.rpm",
    "network-manager-gpclient-1.4.2-1.fc44.i686.rpm",
    "network-manager-gpclient-1.4.2-1.fc44.noarch.rpm",
    "network-manager-gpclient-1.4.2-1.xx44.x86_64.rpm",
    "network-manager-gpclient-1.4.2-1.x86_64.rpm",
    "network-manager-gpclient-1.4.2-1.fc44.pr24.x86_64.rpm",
    "network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm ",
    "network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm\n",
    "network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm;rm",
    "Network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm",
    "../network-manager-gpclient-1.4.2-1.fc44.x86_64.rpm",
    "other-1.4.2-1.fc44.x86_64.rpm",
    "$(x)-1.4.2-1.fc44.x86_64.rpm",
]


class TestRpm:
    def test_an_rpm_only_release_is_announced(self):
        result = published(debs=[], rpms=PR_RPMS)

        assert result["conclusion"] == "success"
        assert result["output"]["title"] == f"Install: {COMMAND}"

    def test_the_summary_counts_and_names_the_rpms_too(self):
        summary = published(debs=PR_DEBS, rpms=PR_RPMS)["output"]["summary"]

        assert f"Published packages ({len(PR_DEBS) + len(PR_RPMS)} files" in summary
        assert "network-manager-gpclient-gnome, network-manager-gpclient-plasma" in summary
        listed = [line[3:-1] for line in summary.splitlines() if line.startswith("- `")]
        assert sorted(listed) == sorted([on_github(n) for n in PR_DEBS] + PR_RPMS)

    def test_the_packages_shown_come_from_the_rpms_when_there_are_no_debs(self):
        summary = published(debs=[], rpms=PR_RPMS[:1])["output"]["summary"]

        assert "(1 files, network-manager-gpclient)" in summary

    def test_the_way_back_has_the_dnf_and_the_zypper_command(self):
        summary = published(debs=[], rpms=PR_RPMS)["output"]["summary"]

        assert "sudo dnf downgrade network-manager-gpclient" in summary
        assert "sudo zypper install --oldpackage network-manager-gpclient=<released version>" in summary

    def test_a_deb_only_summary_has_no_zypper_command(self):
        assert "oldpackage" not in published(debs=PR_DEBS)["output"]["summary"]

    def test_the_summary_says_how_the_script_treats_rpm_systems(self):
        summary = published(debs=[], rpms=PR_RPMS)["output"]["summary"]

        for text in ("Fedora 43 to 45", "EL 10", "uname -m", "dnf", "zypper", ".pr24.<run>", "docs/RPM_REPO.md",
                     "EL 9 is not supported"):
            assert text in summary

    def test_an_rpm_only_summary_is_not_about_apt(self):
        summary = published(debs=[], rpms=PR_RPMS)["output"]["summary"]

        for text in ("apt", "dpkg", "Ubuntu", "APT_REPO"):
            assert text not in summary

    def test_a_deb_only_summary_has_nothing_about_rpm(self):
        summary = published(debs=PR_DEBS)["output"]["summary"]

        for text in ("rpm", "dnf", "zypper", "RPM_REPO", "Fedora"):
            assert text not in summary

    def test_the_summary_of_both_formats_has_both_instructions(self):
        summary = published(debs=PR_DEBS, rpms=PR_RPMS)["output"]["summary"]

        assert "apt installs" not in summary and "apt, dnf and zypper install it over the release" in summary
        assert "docs/APT_REPO.md" in summary and "docs/RPM_REPO.md" in summary

    @pytest.mark.parametrize("name", RPMS + PR_RPMS)
    def test_the_rpms_of_the_build_are_plain_names(self, name):
        assert check.RPM_RE.fullmatch(name)
        assert published(debs=[], rpms=[name])["conclusion"] == "success"

    @pytest.mark.parametrize("name", BAD_RPMS)
    def test_other_rpm_names_are_refused(self, name):
        assert not check.RPM_RE.fullmatch(name)
        with pytest.raises(check.CheckError, match="unexpected package file name"):
            published(debs=DEBS, rpms=RPMS[:1] + [name])

    def test_a_deb_name_is_no_rpm_name_and_the_other_way_round(self):
        with pytest.raises(check.CheckError, match="unexpected package file name"):
            published(debs=[], rpms=DEBS[:1])
        with pytest.raises(check.CheckError, match="unexpected package file name"):
            published(debs=RPMS[:1])

    def test_nothing_at_all_is_still_not_announced(self):
        with pytest.raises(check.CheckError, match="no packages"):
            published(debs=[], rpms=[])

    def test_the_command_line_reads_the_rpms_directory_and_ignores_source_rpms(self, tmp_path):
        for name in RPMS + ["network-manager-gpclient-1.4.2-1.fc44.src.rpm", "README.txt"]:
            (tmp_path / name).write_text("x")

        result = subprocess.run(
            [sys.executable, SCRIPT, "--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO,
             "--rpms-dir", str(tmp_path)], capture_output=True, text=True)

        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)["output"]["summary"]
        assert f"Published packages ({len(RPMS)} files" in summary
        assert "src.rpm" not in summary

    def test_the_command_line_refuses_an_rpms_directory_that_does_not_exist(self, tmp_path):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--state", "published", "--pr", PR, "--sha", SHA, "--repo", REPO,
             "--rpms-dir", str(tmp_path / "nope")], capture_output=True, text=True)

        assert result.returncode == 1 and result.stdout == ""
        assert "Traceback" not in result.stderr


class TestFilterRpm:
    def artifacts(self, tmp_path, names_by_dir):
        return TestFilter().artifacts(tmp_path, names_by_dir)

    def run(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_debs_and_rpms_of_the_artifacts_are_printed_and_copied_together(self, tmp_path):
        root = self.artifacts(tmp_path, {"debs-a": PR_DEBS[:2], "rpms-fedora44-x86_64": PR_RPMS[:3],
                                         "rpms-el10-aarch64": PR_RPMS[3:4]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 0, result.stderr
        expected = sorted(PR_DEBS[:2] + PR_RPMS[:4])
        assert result.stdout.splitlines() == expected
        assert sorted(os.listdir(tmp_path / "upload")) == expected
        assert result.stderr == ""

    @pytest.mark.parametrize("bad", [n for n in BAD_RPMS if n.endswith(".rpm") and not n.endswith(".src.rpm")
                                     and "/" not in n and "\n" not in n])
    def test_other_rpm_names_are_neither_printed_nor_copied(self, tmp_path, bad):
        root = self.artifacts(tmp_path, {"rpms-x": [RPMS[0], bad]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [RPMS[0]]
        assert os.listdir(tmp_path / "upload") == [RPMS[0]]
        assert "skipping a package with an unexpected file name" in result.stderr

    def test_source_rpms_are_ignored_silently(self, tmp_path):
        root = self.artifacts(tmp_path, {"rpms-x": [RPMS[0], "network-manager-gpclient-1.4.2-1.fc44.src.rpm"]})

        result = self.run("--filter", str(root))

        assert result.stdout.splitlines() == [RPMS[0]]
        assert result.stderr == ""

    def test_a_link_to_an_rpm_is_not_followed(self, tmp_path):
        root = self.artifacts(tmp_path, {"rpms-x": [RPMS[0]]})
        secret = tmp_path / "secret"
        secret.write_text("not a package")
        (root / "rpms-x" / RPMS[1]).symlink_to(secret)

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.stdout.splitlines() == [RPMS[0]]
        assert os.listdir(tmp_path / "upload") == [RPMS[0]]

    def test_the_same_rpm_twice_is_an_error_and_copies_nothing(self, tmp_path):
        root = self.artifacts(tmp_path, {"one": [RPMS[0]], "two": [RPMS[0]]})

        result = self.run("--filter", str(root), "--copy-to", str(tmp_path / "upload"))

        assert result.returncode == 1 and result.stdout == ""
        assert not (tmp_path / "upload").exists()

    @pytest.mark.parametrize("name", RPMS + PR_RPMS + BAD_RPMS)
    def test_filter_and_check_agree_on_what_a_plain_rpm_name_is(self, name):
        try:
            check.render("published", PR, SHA, REPO, rpms=[name])
            rendered = True
        except check.CheckError:
            rendered = False

        assert rendered == bool(check.RPM_RE.fullmatch(name))
