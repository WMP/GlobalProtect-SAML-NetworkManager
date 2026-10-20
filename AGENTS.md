# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Copilot and others)
working in this repository. People are welcome to read it too.

This file must stay under 32 KB: `tests/unit/test_agents_md.py` fails when it
grows past that limit. Keep it short and move details into `docs/`.

## What this project is

A NetworkManager VPN plugin for GlobalProtect with SAML/SSO authentication,
packaged as `.deb` files for Ubuntu 22.04, 24.04, 24.10 and 26.04 on `amd64`
and `arm64`, and published as a signed apt repository on GitHub Pages.

| Path | Contents |
|---|---|
| `service/nm-gpclient-service.py` | D-Bus VPN service that NetworkManager starts as root; runs `gpclient` |
| `scripts/browser-wrapper.sh` | Starts the SAML browser for `gpauth` in the desktop user's session |
| `auth-dialog/` | GTK dialog for credentials and one-time passwords |
| `plugins/gnome/`, `plugins/plasma/` | Connection editor plugins (GTK3/GTK4, Plasma 5/6) |
| `debian/` | Packaging; `debian/control.ubuntu<version>` is copied to `debian/control` per build |
| `.github/workflows/` | `tests.yml` (unit tests), `build-release.yml` (matrix Ubuntu × arch), `publish-apt.yml` |
| `.github/scripts/build-apt-repo.sh` | Builds the apt repository from release `.deb` files |
| `external/GlobalProtect-openconnect` | Git submodule (upstream `gpclient`/`gpauth`) |
| `upstream/` | Contract with upstream: `contract.json`, `features.json`, `interfaces.json`, `verification.json` |
| `scripts/upstream_contract.py` | `check`, `update`, `features`, `interfaces`, `gate` for `upstream/` |
| `tests/unit/` | Unit tests (pytest), run anywhere |
| `tests/upstream/` | Tests of the pinned submodule (need `external/`) |
| `tests/test_*.py` | GUI tests (dogtail, needs an X11 session): see `CLAUDE_INSTRUCTIONS.md` |

## Ground rules

- Do not modify `external/`. Fix upstream behaviour in this repo (service or
  wrapper) or report it upstream.
- Never hard-code `x86_64-linux-gnu`. Use `$(DEB_HOST_MULTIARCH)` in
  `debian/rules` (from `/usr/share/dpkg/architecture.mk`), `$(MULTIARCH)` in the
  Makefiles, and `CMAKE_LIBRARY_ARCHITECTURE` in CMake.
- Upstream (`external/`) is pinned to the release tag named in `README.md`, never
  to upstream `main` (main's legacy untagged pin is the only exception, see
  `docs/UPSTREAM.md`). Bump it as in `docs/UPSTREAM.md` and run
  `python3 -m pytest tests/upstream -q`. AI agents never add a record to
  `upstream/verification.json` and never add the label `upstream-verified`:
  only a person who installed and ran the package does. The step-by-step
  playbook for agents is `docs/UPSTREAM_AGENTS.md`. Every function that reads
  upstream output or runs upstream tools is listed in `upstream/interfaces.json`
  with at least two tests (`scripts/upstream_contract.py interfaces` enforces it).
- `scripts/browser-wrapper.sh` identifies the real user by **UID only**. Never
  pass a user name taken from the environment into a command, and never start
  the browser as root.
- Code, comments, commit messages and docs are in English.
- Keep changes minimal and match the style of the surrounding code.

## Build

```bash
make gnome-plugins                    # GTK editor plugins
make all                              # plugins + gpclient + gpauth (Rust, needs the submodule)
dpkg-buildpackage -us -uc -b          # packages, after: cp debian/control.ubuntu24.04 debian/control
```

CI builds every Ubuntu version for `amd64` (`ubuntu-latest`) and `arm64`
(`ubuntu-24.04-arm`) in Docker (`Dockerfile.ubuntu<version>`) on every pull
request, except for changes to docs only (`**.md`, `docs/**`). A release can also
have variants (`Dockerfile.ubuntu<version>-<variant>` with
`debian/control.ubuntu<version>-<variant>`): `24.04-neon` builds
`network-manager-gpclient-plasma-6` for KDE neon, `amd64` only. To build a branch
that has no PR, run the workflow by hand:

```bash
# GitHub Actions → Build and Release → Run workflow (workflow_dispatch)
# Release and apt publishing run only for tags (refs/tags/v*).
```

## Tests

```bash
make test-unit                        # or: python3 -m pytest tests/unit -q
```

Some tests exercise root-only code paths and some non-root paths; each run
skips the other half. CI (`tests.yml`) runs both on every pull request, on
Python 3.10 (Ubuntu 22.04), 3.12 (24.04) and the newest 3.x. Run both yourself
before you push a change to `scripts/browser-wrapper.sh`:

```bash
python3 -m pytest tests/unit -q       # as root (e.g. in a container)
T=$(mktemp -d /var/tmp/t.XXXX)
tar --exclude=./.git --exclude=./external --exclude=./.claude -cf - . | tar -xf - -C "$T"; chmod -R a+rwX "$T"
(cd "$T" && runuser -u nobody -- env HOME="$T" python3 -m pytest tests/unit -q -p no:cacheprovider)
rm -rf "$T"
```

Rules for tests:

- **Every positive test has a negative counterpart**: for each "this works"
  test there is a test that shows the bad input is rejected, the fallback is
  taken, or the thing does not happen. Parametrize over bad inputs.
- A new negative test must fail on the code before the fix. Check that it
  does.
- Tests never touch real user state: fake external tools (`id`, `sudo`,
  `getent`, `pgrep`, `logger`, the browser) through `PATH`, and clean up
  everything they create in `/tmp`.
- Never skip, disable or loosen a test to get green.

## Pull requests

- **Three rounds of code review before every merge.** Run a full code review of
  the PR diff (`origin/main...HEAD`) three times. After each round, fix every
  finding or record in the PR why it is not fixed (pre-existing, out of scope,
  wrong). Push the fixes, then start the next round on the new head. If the
  same kind of finding comes back, fix its root cause.
- Merge only when all three rounds are done and all PR checks are green on the
  current head: the unit tests (root and non-root) and the build of all Ubuntu
  versions × {amd64, arm64}. A docs-only PR skips the build; if the PR changes
  anything else, the build must have run.
- Before merging, merge `main` into the branch if `main` moved since the checks
  ran, and wait for the checks on that head: `pull_request` checks run against
  the merge ref as it was when they ran.
- Do not make the build jobs required checks in branch protection: docs-only PRs
  (`**.md`, `docs/**`) skip the build, so a required build check would never
  report.
- The results are the PR's checks: each `Unit tests` job has a summary with the
  counts, and once the build succeeds a `Test packages` check carries the
  command that installs the PR's packages (`scripts/install-pr-build.sh <PR>`).
  They live in the prerelease `pr-<N>` (never part of the apt repository) and
  are deleted when the PR closes. `pr-test-packages.yml` is a `workflow_run`
  workflow: it runs only from `main`, so changes to it take effect after the
  merge. It must never check out or run PR code.
- Record out-of-scope bugs that reviews find as GitHub issues and link them
  from the PR.
- Merge with a merge commit; do not squash or rebase contributors' commits.
- One topic per PR where possible; say in the description what was tested and
  what was not.
