# Upstream (GlobalProtect-openconnect)

`external/GlobalProtect-openconnect` is the upstream `gpclient` and `gpauth`.
`service/nm-gpclient-service.py` runs `gpclient` under a PTY and reads the
text that it prints: gateway lists, username, password and OTP prompts, banners
and errors. A change of one of these texts breaks the service without any
build error. This page explains how we find such a change and how we accept it.

## How the pin works

- The submodule is pinned to a **release tag**, never to upstream `main`. The
  tag is named in the line `Upstream version:` of `README.md`.
- One exception: the pin that `main` had before this rule, commit
  `9e4f45997ebee452c58d7d3066dcfc175d65ef82` (v2.6.5+14), is not a tag. The
  contract has `"tag": null` and `"legacy_untagged": true` for it, and the
  README line names the commit. The flag is valid for this one commit only
  (`LEGACY_UNTAGGED_COMMIT` in `scripts/upstream_contract.py`), and never
  together with a tag. The next bump must name a tag: `update --tag vX.Y.Z`
  removes the flag.
- `upstream/contract.json` lists the upstream code, texts and versions that we
  depend on (the contract). It records the tag, the commit and the version.
  Item kinds:
  - `anchor`: an exact text that must exist in a file. If it is in another
    file, the status is `MOVED`. If it is nowhere, the status is `MISSING`.
  - `block`: a function, an `impl` or a test. It has a hash of its text
    (whitespace is ignored). `role` is `code` (code that prints a text we read)
    or `test` (an upstream test that guards such code). A change gives `CHANGED`.
  - `cargo-lock`: the version of a crate in `Cargo.lock` (`inquire`,
    `crossterm`, `unicode-width`; our `ScreenBuffer` models their output).
  - `used_by` names the code of this repository that depends on the item.
- `upstream/features.json` classifies every entry of the upstream
  `changelog.md` after the baseline version. Status: `tested` (needs tests that
  exist in this repository), `no-action` or `not-applicable` (need a reason),
  `follow-up` (needs a reason and an issue number).
- `upstream/verification.json` holds the records of manual verifications.
- `upstream/interfaces.json` lists every function of our code that reads the
  output of the upstream tools or runs them (and the scripts they run), with at
  least two tests each (a positive and a negative one). `interfaces` finds such
  functions in `service/nm-gpclient-service.py` by scanning its code, and fails
  when one is not listed, when a listed one is gone, or when its tests are
  missing or do not name it.
- `scripts/upstream_contract.py` has the commands `check`, `update`,
  `features`, `interfaces` and `gate`. `tests/upstream/` runs them against the real
  submodule. The workflow `.github/workflows/upstream.yml` runs it on every
  pull request.

## Bump procedure

1. Check out the new tag in the submodule:

   ```bash
   cd external/GlobalProtect-openconnect
   git fetch --tags origin && git checkout vX.Y.Z
   cd ../..
   git submodule sync --recursive && git submodule update --init --recursive
   ```

2. Run `python3 scripts/upstream_contract.py check`. Items that are not `OK`
   need a review.
3. For each `CHANGED` or `MISSING` item, read the upstream change and adapt our
   code (`used_by` says which code):

   ```bash
   git -C external/GlobalProtect-openconnect diff <old commit> <new commit> -- <path>
   ```

   A `MOVED` anchor needs a new `path` in `upstream/contract.json`.
4. Run `python3 scripts/upstream_contract.py update --tag vX.Y.Z`. It records the
   new hashes, versions, commit and tag. It never changes an anchor: a
   `MISSING` anchor must be fixed in our code or in the contract by hand.
   Add new items if the new version prints new text that we read.
5. Run `python3 scripts/upstream_contract.py interfaces`. A new function that
   reads upstream output or runs upstream needs an entry and two tests.
   Then run `python3 scripts/upstream_contract.py features`. Classify each new
   changelog entry in `upstream/features.json`. When an entry affects the
   service, write an automated test in this repository and name it in `tests`.
6. Change the `Upstream version:` line of `README.md` (both the text and the link).
7. Build the packages (the Rust version of the Dockerfiles and the Makefile must
   not be older than `rust-toolchain.toml` of upstream) and run:

   ```bash
   python3 -m pytest tests/unit -q
   python3 -m pytest tests/upstream -q
   ```

8. Open a pull request. If the gate says the change is breaking, do the
   manual verification below.

## What "breaking" means and what the gate does

"Breaking" means what the upstream changelog says. The gate takes the version
of the contract on the **base branch** of the pull request (not the one in the
pull request, so a pull request cannot hide a change by editing it) and the
version in `Cargo.toml` of the checked-out upstream. The change is breaking when:

(Before all rules below: when the commit of the submodule on the base branch is
the commit of the pull request, the upstream commit is unchanged and the change is
not breaking, also when the base branch has no contract. The workflow passes the
base commit as `--base-commit`.)

- the base branch has no `upstream/contract.json` ("no accepted baseline": this
  is true once, for the pull request that adds the contract), or
- the version went down (`version went down: <base> -> <current>`), or
- the major version changed (for example 3.x to 4.0.0), or
- the upstream `changelog.md` has, in a version V with base < V <= current,
  a `###` section whose name contains `breaking`, `command-line notes`,
  `removed` or `deprecated` (every bullet is a reason), or a bullet in any
  section that has one of the whole words `breaking`, `incompatible`,
  `removed`, `renamed`, `deprecated` or `must` (case-insensitive; `removal`
  and `mustard` do not count). The lists are `BREAKING_SECTIONS` and
  `BREAKING_WORDS` in `scripts/upstream_contract.py`, or
- the changelog is missing or has no entries for the new versions (fail safe).

If the version did not change, the change is not breaking. The workflow runs
`scripts/upstream_contract.py` from the base branch too (the pull request cannot
weaken the gate by editing the script; only the pull request that adds the
script uses its own copy). The workflow file itself comes from the pull request,
so review every change to it. A change of the
contract items alone (`CHANGED`, `MISSING`, `MOVED`) is **not** breaking: it
fails `check` and `tests/upstream`, you review the upstream diff, adapt our code
and run `update`, but no manual verification is needed.

If the change is not breaking, the gate passes and no manual verification is
needed. If it is breaking, the check fails until both are present:

1. a valid record in `upstream/verification.json` for the commit of the
   submodule, and
2. the label `upstream-verified` on the pull request.

The check runs again when a label is added or removed.

## Manual verification (a person does this)

1. Wait for the build of the pull request. Install its packages on a test
   machine (the command is in the check `Test packages`):

   ```bash
   scripts/install-pr-build.sh <PR number> [--desktop gnome|plasma|plasma-6]
   ```

2. Run each scenario and look at the result:

   | Scenario | What to check |
   |---|---|
   | `install` | The packages install and `systemctl status NetworkManager` is active. The editor plugin opens. |
   | `saml-login` | A connection with SAML opens the browser, the login ends and the VPN is up. |
   | `disconnect` | Disconnect ends the session. No `gpclient` or `gpauth` process stays. |
   | `reconnect` | Connect again after a disconnect. It works. |
   | `gateway-selection` | A portal with more than one gateway: the gateway list and the choice work (or `n/a`). |
   | `credentials-prompt` | A portal with user name and password: the dialog asks and the login works (or `n/a`). |
   | `otp-prompt` | A gateway with MFA or an OTP: the dialog asks for the code (or `n/a`). |
   | `fix-openssl` | A server with legacy TLS renegotiation: the retry with `--fix-openssl` works (or `n/a`). |
   | `hip` | A connection with HIP enabled works (or `n/a`). |

   `install`, `saml-login`, `disconnect` and `reconnect` must be `pass`. The
   others are `pass` or `n/a`.

3. Add a record to `records` in `upstream/verification.json` in the pull request:

   ```json
   {
     "commit": "<40 hex digits: git -C external/GlobalProtect-openconnect rev-parse HEAD>",
     "tag": "vX.Y.Z",
     "pr": 0,
     "tester": "<your GitHub login>",
     "date": "YYYY-MM-DD",
     "systems": [{"ubuntu": "24.04", "arch": "amd64", "desktop": "GNOME"}],
     "scenarios": {
       "install": "pass", "saml-login": "pass", "disconnect": "pass", "reconnect": "pass",
       "gateway-selection": "pass", "credentials-prompt": "n/a", "otp-prompt": "n/a",
       "fix-openssl": "n/a", "hip": "n/a"
     },
     "result": "pass",
     "notes": ""
   }
   ```

4. A maintainer adds the label `upstream-verified` to the pull request. Create
   the label once in the repository if it does not exist.

If a scenario fails, do not add a record. Fix the problem and test again.

**AI agents must never add a record to `upstream/verification.json` and must
never add the label `upstream-verified`.** Only a person who installed the
package and ran the scenarios can do this.
