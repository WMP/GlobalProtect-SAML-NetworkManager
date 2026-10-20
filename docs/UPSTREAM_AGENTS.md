# Upstream playbook for AI agents

For AI agents (Claude Code, Codex, Copilot and others). People read
[UPSTREAM.md](UPSTREAM.md); it explains the design. This page only says what to do.

## 1. Rules

1. Never modify `external/`. Fix upstream behaviour in `service/` or `scripts/`.
2. Pin the submodule only to a release tag (`vX.Y.Z`) that `README.md` names in the
   line `Upstream version:`. Never pin to upstream `main` or to a branch tip.
   The only exception is the legacy untagged pin of `main` (`"legacy_untagged":
   true` in the contract): do not add it for any other commit. A bump moves to a tag.
3. Never add or edit a record in `upstream/verification.json`.
4. Never add or remove the label `upstream-verified`.
5. Never run `update` to make a failing contract green before you read the
   upstream diff of every `CHANGED` and `MISSING` item and adapt our code.
6. Never skip, disable or loosen a test. Every positive test has a negative one.

## 2. Bump to a new tag

1. Check out the tag:

   ```bash
   cd external/GlobalProtect-openconnect
   git fetch --tags origin
   git checkout vX.Y.Z
   cd ../..
   git submodule sync --recursive && git submodule update --init --recursive
   ```

2. Run `python3 scripts/upstream_contract.py check`. It prints one line per item:
   `STATUS id (path) used by: ...`.
3. Act on each status:
   - `OK`: nothing.
   - `MOVED` (anchors only): the text exists in another file. Set the new `path`
     in `upstream/contract.json`. This is not a breaking change.
   - `CHANGED`: a block hash or a crate version changed. Read the change, adapt
     the code named in `used_by`, and add or fix tests.
   - `MISSING`: the text, the function or the crate is gone (or `start` is
     ambiguous). Read the change, adapt our code and the contract by hand.
     `update` never rewrites anchors.

   Read the change with:

   ```bash
   git -C external/GlobalProtect-openconnect diff <old commit> <new commit> -- <path>
   ```

   The old commit is `commit` in `upstream/contract.json` before you change it.
4. Run `python3 scripts/upstream_contract.py update --tag vX.Y.Z`. It records the
   block hashes, the crate versions, the commit and the version. It exits 1 when
   an anchor is still `MISSING`.
5. Change the line `Upstream version:` in `README.md`: the text and the link
   must both name the new tag.
6. Compare `rust-toolchain.toml` of upstream with `--default-toolchain` in every
   `Dockerfile.ubuntu*` that installs rustup and with `RUST_VERSION` in the
   `Makefile`. They must not be older. Check the build dependencies of
   `debian/control.ubuntu*` against the upstream packaging.
   `tests/upstream` checks the Rust version.

## 3. Changelog features

1. Run `python3 scripts/upstream_contract.py features`. It lists each changelog
   bullet after `baseline` (up to the pinned version) that has no entry in
   `upstream/features.json`, and each stale entry. Match is on version, section
   and exact text.
2. Add one entry for each bullet. Choose the status:
   - `tested`: the entry changes something that the service reads or runs
     (text output, flags, process lifecycle, packaging that we use). Write a
     positive and a negative test in this repository and list them in `tests`
     (`path::test_name`).
   - `no-action`: an upstream-internal change that does not change the
     interface we use. Write the reason.
   - `not-applicable`: GUI only, macOS or BSD only, a flow that we do not use.
     Write the reason.
   - `follow-up`: it affects us but is not done. Create a GitHub issue, put its
     number in `issue` and write the reason.
3. Do not mark an entry `tested` before the tests exist: the command checks them.

## 4. Interfaces

1. Run `python3 scripts/upstream_contract.py interfaces`.
2. When you add or change a function in `service/nm-gpclient-service.py` that
   reads `gpclient` or `gpauth` output or runs their binaries, add it to
   `upstream/interfaces.json`: `function` (`path::func` or `path::Class.method`),
   `kind` (`reads-output` or `runs-upstream`), `upstream` (ids of the contract
   items it depends on) and at least two `tests` (positive and negative). At
   least one test must name the function in its body.
3. The scan finds a function when it uses a service name that a contract item
   lists in `used_by`, has a command element `gpclient`, `gpauth`, `gpservice` or
   `browser-wrapper` in a list, a tuple or call arguments, or touches
   `_pty_master`, `gpclient_process.stdout`, `_consume_output` or
   `_decode_output`. `__init__` is not scanned.
4. When you add a new text match against upstream output, add an anchor (and a
   block for the upstream function that prints it) to `upstream/contract.json` in
   the same change. Put the name of your regex in `used_by` as
   `service/nm-gpclient-service.py: NAME`, so the scan finds the functions that use it.
5. Scripts that gpclient or gpauth run (`scripts/browser-wrapper.sh`) are listed
   by their path, by hand.

## 5. Breaking changes and the gate

1. When the upstream commit is the same as on the base branch (`--base-commit`),
   the gate says "upstream commit unchanged" and the change is not breaking.
   Otherwise: "Breaking" means what the upstream changelog says. The gate compares the
   version in `upstream/contract.json` of the base branch (not the one in your
   pull request) with the version of the checked-out upstream. It says "breaking"
   when there is no base contract, when the major version changed, when a
   changelog section of a new version is named like `BREAKING_SECTIONS`
   (`breaking`, `command-line notes`, `removed`, `deprecated`), when a bullet has
   one of the whole words in `BREAKING_WORDS` (`breaking`, `incompatible`,
   `removed`, `renamed`, `deprecated`, `must`), when the version went down, or when
   the changelog is missing. CI runs the gate script of the base branch, so a change
   to `scripts/upstream_contract.py` in your pull request does not change the gate
   of that pull request.
   Contract drift (`CHANGED`, `MISSING`) is not breaking: review it and run
   `update` (section 2). It needs no manual verification.
2. A breaking pull request stays red until both exist: a valid record in
   `upstream/verification.json` for the commit of the submodule, and the label
   `upstream-verified`. A person must install and run the packages. You cannot do it.
3. When the gate says breaking: finish all other work, make every other check
   green, and then stop. Tell the user:
   - which scenarios to run (`install`, `saml-login`, `disconnect`, `reconnect`
     must pass; `gateway-selection`, `credentials-prompt`, `otp-prompt`,
     `fix-openssl`, `hip` pass or `n/a`; details in [UPSTREAM.md](UPSTREAM.md));
   - the command `scripts/install-pr-build.sh <PR number>`;
   - the record format (see UPSTREAM.md) and the commit to put in it:
     `git -C external/GlobalProtect-openconnect rev-parse HEAD`;
   - that a maintainer must add the label `upstream-verified`.
4. If the change is not breaking, no manual verification is needed.

## 6. Validate before you push

```bash
python3 -m pytest tests/unit -q          # as root, and as non-root (recipe in AGENTS.md)
python3 -m pytest tests/upstream -q
python3 scripts/upstream_contract.py check
python3 scripts/upstream_contract.py features
python3 scripts/upstream_contract.py interfaces
```

## 7. Troubleshooting

- `the tag vX.Y.Z is not in external/GlobalProtect-openconnect`: the submodule is
  shallow. Run
  `git -C external/GlobalProtect-openconnect fetch --depth=1 origin refs/tags/vX.Y.Z:refs/tags/vX.Y.Z`.
- The nested submodule fails to update after a bump: upstream can change its URL.
  Run `git submodule sync --recursive` and then
  `git submodule update --init --recursive`.
- The build downloads another Rust version: `rust-toolchain.toml` of upstream asks
  for a newer one than the Dockerfiles install. Raise `--default-toolchain` in
  the Dockerfiles and `RUST_VERSION` in the `Makefile`.
- `check` says `MISSING` for a block with "ambiguous": `start` occurs more than
  once in the file. Use a longer `start`, it can span lines.
