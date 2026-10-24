#!/usr/bin/env python3
"""The "Test packages" check run of a pull request, as the JSON the GitHub API
takes (POST/PATCH /repos/{repo}/check-runs).

    test_packages_check.py --state published --pr 24 --sha <head sha> \\
        --repo OWNER/REPO --debs-dir upload/ --rpms-dir upload/
    test_packages_check.py --state failed --pr 24 --sha ... --repo ... --run-url URL
    test_packages_check.py --state not-published --reason "..." --pr 24 ...
    test_packages_check.py --state publish-failed --run-url URL --pr 24 ...
    test_packages_check.py --filter artifacts/ [--copy-to upload/]

Used by .github/workflows/pr-test-packages.yml. The check's title is shown next
to it in the merge box of the pull request, so it carries the install command;
the summary says what the command does and how to go back. Standard library only.
"""

import argparse
import json
import os
import re
import shutil
import sys

NAME = "Test packages"
SCRIPT_PATH = "scripts/install-pr-build.sh"
DEFAULT_REPO = "WMP/GlobalProtect-SAML-NetworkManager"
# File names that came out of a build artifact are data: only names of the form
# <package>_<version>_<arch>.deb made of plain characters are listed and
# uploaded. The version is the release's one (1.4.2-1~noble1) or a pull
# request's (1.4.2-1~noble1+pr24.57). The workflow filters with --filter, so this
# is the only definition.
DEB_RE = re.compile(r"[a-z0-9][a-z0-9+.-]*_[0-9][A-Za-z0-9.+~-]*_[a-z0-9]+\.deb")
# Binary RPMs: <package>-<version>-<revision><dist>[.pr<PR>.<run>].<rpm arch>.rpm
# with the three packages we build and the dist tags of rpm/targets (.fc<N>,
# .el<N>, .lp<N>, .tw). No source and no debuginfo packages.
RPM_RE = re.compile(
    r"network-manager-gpclient(?:-gnome|-plasma)?-[0-9][A-Za-z0-9.+~_]*-[0-9]+"
    r"\.(?:fc[0-9]+|el[0-9]+|lp[0-9]+|tw)(?:\.pr[0-9]+\.[0-9]+)?\.(?:x86_64|aarch64)\.rpm"
)
RUN_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
REF_RE = re.compile(r"^[\w./-]+$")
PR_RE = re.compile(r"^[1-9][0-9]{0,6}$")
REASON_RE = re.compile(r"^[A-Za-z0-9 ,.;:()'#/_-]{1,200}$")
PACKAGE_RE = re.compile(r"^(network-manager-gpclient(?:-gnome|-plasma(?:-6)?)?)_")
RPM_PACKAGE_RE = re.compile(r"^(network-manager-gpclient(?:-gnome|-plasma)?)-[0-9]")


class CheckError(Exception):
    """Input that must not end up in a check run."""


def install_command(repo, pr, script_ref):
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, script_ref, SCRIPT_PATH)
    # bash <(...) and not `curl | bash`: apt asks for confirmation on the
    # terminal, which a pipe would take away
    command = "bash <(curl -fsSL %s) %s" % (url, pr)
    if repo != DEFAULT_REPO:
        # The script's own default is the main repository
        command += " --repo %s" % repo
    return command


def github_name(name):
    """The name GitHub gives a release asset: the "~" of the version becomes "."."""
    return name.replace("~", ".")


def published_summary(repo, pr, sha, script_ref, debs, rpms=()):
    command = install_command(repo, pr, script_ref)
    release = "https://github.com/%s/releases/tag/pr-%s" % (repo, pr)
    matches = [PACKAGE_RE.match(d) for d in debs] + [RPM_PACKAGE_RE.match(r) for r in rpms]
    shown = sorted({m.group(1) for m in matches if m})
    if debs and not rpms:
        version_text = ("Its version ends in `+pr%s.<run>`, above the released one, so apt installs it "
                        "over the release. " % pr)
    elif debs:
        version_text = ("Its version ends in `+pr%s.<run>` (RPM release: `.pr%s.<run>`), above the "
                        "released one, so apt, dnf and zypper install it over the release. " % (pr, pr))
    else:
        version_text = ("Its release ends in `.pr%s.<run>`, above the released one, so dnf and zypper "
                        "install it over the release. " % pr)
    lines = [
        "### Install this PR's test packages",
        "",
        "Unreviewed test build of #%s (commit `%s`), published as the prerelease "
        "[`pr-%s`](%s) and removed when the PR closes. %s"
        "It installs as root: read the diff first." % (pr, sha[:7], pr, release, version_text),
        "",
        "```bash",
        command,
        "```",
        "",
    ]
    if debs:
        lines += [
            "The script picks what fits the machine it runs on:",
            "",
            "- the Ubuntu release (22.04, 24.04, 24.10 or 26.04) from `/etc/os-release`,",
            "- the architecture (amd64 or arm64) from `dpkg`,",
            "- the desktop: Plasma when `XDG_CURRENT_DESKTOP` mentions KDE, otherwise GNOME. "
            "Force one with `--desktop gnome` or `--desktop plasma`. On KDE neon (Ubuntu 24.04 "
            "with Plasma 6) the Plasma package is `network-manager-gpclient-plasma-6`.",
            "",
            "It prints what it is going to install and lets apt ask for confirmation "
            "(`--yes` skips the question). On Ubuntu 22.04 `python3-sdbus` is not in apt: "
            "run `sudo apt install python3-pip && sudo pip3 install sdbus` first (as root: "
            "the service runs as root and does not see a user's `~/.local`).",
            "",
            "Run it as shown (`bash <(...)`), not as `curl ... | bash`: apt needs the terminal.",
            "",
            "#### Back to the released version",
            "",
            "With the apt repository set up as in `docs/APT_REPO.md`:",
            "",
            "```bash",
            "sudo apt update && sudo apt install --allow-downgrades "
            "network-manager-gpclient/$(lsb_release -cs) network-manager-gpclient-gnome/$(lsb_release -cs)"
            "   # or -plasma (-plasma-6 on KDE neon), as installed",
            "```",
            "",
            "Without it: `sudo apt remove network-manager-gpclient network-manager-gpclient-gnome` "
            "(or the plasma package) and install the released `.deb` files again.",
            "",
        ]
    if rpms:
        lines += [
            "On an RPM system the script picks the packages for the target "
            "(Fedora 43 to 45, EL 10, openSUSE Leap 16.0 or Tumbleweed) from `/etc/os-release`, "
            "the architecture (x86_64 or aarch64) from `uname -m` and the desktop as above "
            "(`-gnome` or `-plasma`). It hands them to `dnf` (`zypper` on openSUSE), which asks "
            "for confirmation (`--yes` skips the question). The test packages are not signed. "
            "On EL 10 enable EPEL and CRB first. EL 9 is not supported.",
            "",
            "#### Back to the released RPM version",
            "",
            "With the RPM repository set up as in `docs/RPM_REPO.md`:",
            "",
            "```bash",
            "sudo dnf downgrade network-manager-gpclient network-manager-gpclient-gnome   # or -plasma, as installed",
            "# openSUSE: the released version is older, so name it (zypper search -s --match-exact "
            "network-manager-gpclient)",
            "sudo zypper install --oldpackage network-manager-gpclient=<released version> "
            "network-manager-gpclient-gnome=<released version>   # or -plasma",
            "```",
            "",
            "Without it: `sudo dnf remove network-manager-gpclient network-manager-gpclient-gnome` "
            "(`zypper remove` on openSUSE, or the plasma package) and install the released `.rpm` "
            "files again.",
            "",
        ]
    lines += [
        "<details><summary>Published packages (%d files, %s)</summary>" %
        (len(debs) + len(rpms), ", ".join(shown) or "none"),
        "",
    ]
    # As the release lists them, not as the build named them
    lines += ["- `%s`" % name for name in sorted(github_name(d) for d in list(debs) + list(rpms))]
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def render(state, pr, sha, repo, script_ref="main", debs=(), run_url="", reason="", rpms=()):
    """The check run as a dict."""
    if not PR_RE.fullmatch(str(pr)):
        raise CheckError("not a pull request number: %r" % (pr,))
    if not SHA_RE.fullmatch(sha):
        raise CheckError("not a full commit sha: %r" % (sha,))
    if not REPO_RE.fullmatch(repo):
        raise CheckError("repo must look like OWNER/REPO: %r" % (repo,))
    if not REF_RE.fullmatch(script_ref):
        raise CheckError("not a branch name: %r" % (script_ref,))

    check = {"name": NAME, "head_sha": sha, "status": "completed"}
    if state == "published":
        debs, rpms = list(debs), list(rpms)
        if not debs and not rpms:
            raise CheckError("no packages to publish: refusing to announce an empty release")
        for name in debs:
            if not DEB_RE.fullmatch(name):
                raise CheckError("unexpected package file name: %r" % (name,))
        for name in rpms:
            if not RPM_RE.fullmatch(name):
                raise CheckError("unexpected package file name: %r" % (name,))
        check["conclusion"] = "success"
        check["details_url"] = "https://github.com/%s/releases/tag/pr-%s" % (repo, pr)
        check["output"] = {
            "title": "Install: " + install_command(repo, pr, script_ref),
            "summary": published_summary(repo, pr, sha, script_ref, sorted(debs), sorted(rpms)),
        }
    elif state == "failed":
        if run_url and not RUN_URL_RE.fullmatch(run_url):
            raise CheckError("not a workflow run url: %r" % (run_url,))
        summary = "The build of commit `%s` failed, so no test packages were published." % sha[:7]
        if run_url:
            summary += " See the [Build and Release run](%s)." % run_url
        check["conclusion"] = "neutral"
        check["output"] = {"title": "No test packages: the build failed", "summary": summary}
    elif state == "publish-failed":
        if run_url and not RUN_URL_RE.fullmatch(run_url):
            raise CheckError("not a workflow run url: %r" % (run_url,))
        summary = "Publishing the test packages of commit `%s` failed (the build itself succeeded)." % sha[:7]
        if run_url:
            summary += " See the [workflow run](%s)." % run_url
        check["conclusion"] = "neutral"
        check["output"] = {"title": "No test packages: publishing failed", "summary": summary}
    elif state == "not-published":
        if not REASON_RE.fullmatch(reason):
            raise CheckError("a plain reason is required (letters, digits and punctuation): %r" % (reason,))
        check["conclusion"] = "neutral"
        check["output"] = {
            "title": "No test packages: " + reason,
            "summary": "No test packages were published for commit `%s`: %s." % (sha[:7], reason),
        }
    else:
        raise CheckError("unknown state: %r" % (state,))
    return check


def filter_packages(source, copy_to=""):
    """The plain .deb and binary .rpm files below `source` as {name: path}; with `copy_to`, they
    are also copied there. Anything else (odd names, links, other than regular files) is
    skipped and reported on stderr, never passed on."""
    found = {}
    for directory, _subdirs, names in os.walk(source):
        for name in sorted(names):
            path = os.path.join(directory, name)
            if not (name.endswith(".deb") or (name.endswith(".rpm") and not name.endswith(".src.rpm"))):
                continue
            if os.path.islink(path) or not os.path.isfile(path) or not (DEB_RE.fullmatch(name) or RPM_RE.fullmatch(name)):
                # repr: a hostile name must not reach the log as it is
                print("skipping a package with an unexpected file name: %r" % (name,), file=sys.stderr)
                continue
            if name in found:
                raise CheckError("package %s is in the build twice" % name)
            found[name] = path
    if not found:
        raise CheckError("no packages found in %s" % source)
    if copy_to:
        os.makedirs(copy_to, exist_ok=True)
        for name, path in found.items():
            shutil.copyfile(path, os.path.join(copy_to, name))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state", choices=["published", "failed", "publish-failed", "not-published"])
    parser.add_argument("--pr")
    parser.add_argument("--sha")
    parser.add_argument("--repo")
    parser.add_argument("--script-ref", default="main", help="branch to fetch install-pr-build.sh from")
    parser.add_argument("--debs-dir", default="", help="directory with the published .deb files")
    parser.add_argument("--rpms-dir", default="", help="directory with the published binary .rpm files")
    parser.add_argument("--run-url", default="", help="the workflow run, shown when nothing was published")
    parser.add_argument("--reason", default="", help="why nothing was published (state not-published)")
    parser.add_argument("--filter", default="", metavar="DIR",
                        help="print the names of acceptable .deb and binary .rpm files below DIR, one per line")
    parser.add_argument("--copy-to", default="", metavar="DIR", help="with --filter: copy them to DIR")
    args = parser.parse_args(argv)

    if args.filter:
        try:
            found = filter_packages(args.filter, args.copy_to)
        except (CheckError, OSError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        for name in sorted(found):
            print(name)
        return 0
    if args.copy_to:
        parser.error("--copy-to needs --filter")
    for option in ("state", "pr", "sha", "repo"):
        if getattr(args, option) is None:
            parser.error("--%s is required" % option)

    debs = []
    if args.debs_dir:
        try:
            debs = sorted(n for n in os.listdir(args.debs_dir) if n.endswith(".deb"))
        except OSError as exc:
            print("error: cannot read %s: %s" % (args.debs_dir, exc), file=sys.stderr)
            return 1
    rpms = []
    if args.rpms_dir:
        try:
            rpms = sorted(n for n in os.listdir(args.rpms_dir) if n.endswith(".rpm") and not n.endswith(".src.rpm"))
        except OSError as exc:
            print("error: cannot read %s: %s" % (args.rpms_dir, exc), file=sys.stderr)
            return 1
    try:
        check = render(args.state, args.pr, args.sha, args.repo, args.script_ref, debs, args.run_url, args.reason, rpms)
    except CheckError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    json.dump(check, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
