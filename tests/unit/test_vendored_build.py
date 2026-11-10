"""
Tests for the vendored crates and the offline build of issue #160: debian/rules and the
RPM spec use vendor/config.toml and --offline only when the config exists, CI vendors
the crates before the package builds and runs the Debian build without network, and the
reproducibility workflow exists and is not run for pull requests.

Every check is a function on the text of a file, so a negative test runs it on a
modified copy that must be rejected. Nothing is built.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as handle:
        return handle.read()


def rules_problems(text):
    """What is wrong with the conditional offline flags of debian/rules"""
    problems = []
    flags = re.search(r"^CARGO_VENDOR_FLAGS\s*:=\s*\$\(if \$\(wildcard \$\(VENDOR_CONFIG\)\),(.*),\)\s*$", text, re.M)
    if not re.search(r"^VENDOR_CONFIG\s*:=\s*\$\(CURDIR\)/vendor/config\.toml$", text, re.M):
        problems.append("VENDOR_CONFIG is not $(CURDIR)/vendor/config.toml")
    if not flags:
        problems.append("CARGO_VENDOR_FLAGS is not conditional on the config file")
    elif "--offline" not in flags.group(1) or "--config $(VENDOR_CONFIG)" not in flags.group(1):
        problems.append("the conditional flags lack --offline or --config")
    build = [l for l in text.splitlines() if "cargo auditable build" in l]
    if len(build) != 1 or "--locked $(CARGO_VENDOR_FLAGS)" not in build[0]:
        problems.append("the cargo build does not use --locked and $(CARGO_VENDOR_FLAGS)")
    return problems


def spec_problems(text):
    """What is wrong with the conditional offline flags of the RPM spec"""
    problems = []
    if not re.search(r'if \[ -f "\$PWD/vendor/config\.toml" \]; then\n\s*vendor_flags="--offline --config \$PWD/vendor/config\.toml"\n\s*fi', text):
        problems.append("vendor_flags is not set only when vendor/config.toml exists")
    build = [l for l in text.splitlines() if "cargo auditable build" in l]
    if len(build) != 1 or "--locked $vendor_flags" not in build[0]:
        problems.append("the cargo build does not use --locked and $vendor_flags")
    return problems


def release_problems(text):
    """What is wrong with the vendoring in build-release.yml"""
    problems = []
    vendor = [m.start() for m in re.finditer(r"bash scripts/vendor-crates\.sh", text)]
    if len(vendor) != 3:
        problems.append("expected 3 vendor steps (Debian, RPM and Arch Linux), found %d" % len(vendor))
    else:
        if not (vendor[0] < text.find("docker build ") < vendor[1] < text.find("bash rpm/build.sh")
                < vendor[2] < text.find("bash arch/build.sh")):
            problems.append("the crates are not vendored before the Debian image, the RPM build and the Arch Linux build")
    if not re.search(r"docker run --rm --network none -e PR_SUFFIX=", text):
        problems.append("the Debian build container does not run with --network none")
    return problems


def test_rules_use_vendored_crates_when_present():
    assert rules_problems(read("debian", "rules")) == []


def test_spec_uses_vendored_crates_when_present():
    assert spec_problems(read("rpm", "network-manager-gpclient.spec")) == []


def test_release_workflow_vendors_and_builds_without_network():
    assert release_problems(read(".github", "workflows", "build-release.yml")) == []


@pytest.mark.parametrize(
    "old, new",
    [
        ("--offline --config $(VENDOR_CONFIG)", "--config $(VENDOR_CONFIG)"),
        ("$(if $(wildcard $(VENDOR_CONFIG)),", "$(if 1,"),
        ("--locked $(CARGO_VENDOR_FLAGS)", "--locked"),
        ("$(CURDIR)/vendor/config.toml", "$(CURDIR)/config.toml"),
    ],
)
def test_rules_mutations_are_rejected(old, new):
    text = read("debian", "rules")
    assert old in text
    assert rules_problems(text.replace(old, new)) != []


@pytest.mark.parametrize(
    "old, new",
    [
        ('if [ -f "$PWD/vendor/config.toml" ]; then', "if true; then"),
        ("--offline --config", "--config"),
        ("--locked $vendor_flags", "--locked"),
    ],
)
def test_spec_mutations_are_rejected(old, new):
    text = read("rpm", "network-manager-gpclient.spec")
    assert old in text
    assert spec_problems(text.replace(old, new)) != []


@pytest.mark.parametrize(
    "old, new",
    [
        ("docker run --rm --network none -e", "docker run --rm -e"),
        ("bash rpm/build.sh", "bash rpm/build2.sh"),
        ("bash arch/build.sh", "bash arch/build2.sh"),
    ],
)
def test_release_mutations_are_rejected(old, new):
    text = read(".github", "workflows", "build-release.yml")
    assert old in text
    assert release_problems(text.replace(old, new)) != []


def test_release_without_vendor_step_is_rejected():
    text = read(".github", "workflows", "build-release.yml")
    assert release_problems(text.replace("bash scripts/vendor-crates.sh", "true")) != []


def test_vendor_dir_is_ignored_by_git_and_docker():
    assert "vendor/" in read(".gitignore").splitlines()
    assert "vendor/" in read(".dockerignore").splitlines()
    # The RPM source archive must keep vendor/
    assert "--exclude=./vendor" not in read("rpm", "build.sh")


def test_vendor_script_is_in_shell_scripts_and_strict():
    assert "\tscripts/vendor-crates.sh \\\n" in read("Makefile")
    assert "set -euo pipefail" in read("scripts", "vendor-crates.sh")


def test_vendor_script_needs_cargo(tmp_path):
    """Without cargo the script fails with a clear message"""
    empty = tmp_path / "bin"
    empty.mkdir()
    result = subprocess.run(
        ["/bin/bash", os.path.join(ROOT, "scripts", "vendor-crates.sh")],
        env={"PATH": str(empty)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "cargo not found" in result.stderr


def reproducible_problems(text):
    """What is wrong with the trigger and the build of reproducible.yml"""
    on = text.split("\npermissions:")[0]
    problems = []
    if "workflow_dispatch:" not in on or "schedule:" not in on:
        problems.append("not run by hand and weekly")
    if "pull_request" in on or "push:" in on:
        problems.append("run on every pull request or push")
    for needle in ("persist-credentials: false", "submodules: recursive", "--network none", "diffoscope"):
        if needle not in text:
            problems.append("missing: " + needle)
    return problems


def test_reproducible_workflow_runs_by_hand_and_weekly_only():
    assert reproducible_problems(read(".github", "workflows", "reproducible.yml")) == []


@pytest.mark.parametrize(
    "old, new",
    [
        ("  schedule:", "  pull_request:\n  schedule:"),
        ("  workflow_dispatch:\n", ""),
        ("persist-credentials: false", ""),
        ("--network none", ""),
    ],
)
def test_reproducible_mutations_are_rejected(old, new):
    text = read(".github", "workflows", "reproducible.yml")
    assert old in text
    assert reproducible_problems(text.replace(old, new)) != []


MOUNT = ":/build/source/vendor:ro"


def mount_problems(text):
    """The docker run commands with --network none that do not mount vendor/ read-only"""
    chunks = [c[:500] for c in text.split("docker run")[1:] if "--network none" in c[:200]]
    return [] if chunks and all(MOUNT in c for c in chunks) else ["a --network none container lacks " + MOUNT]


@pytest.mark.parametrize("path", [(".github", "workflows", "build-release.yml"), (".github", "workflows", "reproducible.yml")])
def test_network_none_containers_mount_vendor_read_only(path):
    text = read(*path)
    assert mount_problems(text) == []
    assert mount_problems(text.replace(MOUNT, ":/build/source/vendor")) != []
    assert mount_problems(text.replace(MOUNT, "")) != []


def test_build_all_mounts_vendor_with_network_none_and_revendors_a_stale_copy():
    text = read("build-all.sh")
    line = re.search(r"^    NETWORK_ARGS=\((.*)\)$", text, re.M).group(1)
    assert "--network none" in line and MOUNT in line
    assert '"$LOCK_FILE" -nt "$SCRIPT_DIR/vendor/config.toml"' in text
    assert '[ ! -f "$SCRIPT_DIR/vendor/config.toml" ]' in text
    assert '"${NETWORK_ARGS[@]}"' in text
    # negative twins
    assert MOUNT not in line.replace(MOUNT, "")
    assert '"$LOCK_FILE" -nt' not in text.replace('"$LOCK_FILE" -nt', "")


# --- scripts/vendor-crates.sh with a fake cargo ---

SYSTEM_PATH = "/usr/bin:/bin"


def vendor_root(tmp_path, with_lock=True):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "scripts", "vendor-crates.sh"), root / "scripts")
    if with_lock:
        (root / "external" / "GlobalProtect-openconnect").mkdir(parents=True)
        (root / "external" / "GlobalProtect-openconnect" / "Cargo.lock").write_text("")
    return root


def fake_cargo(tmp_path, output):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cargo = bindir / "cargo"
    cargo.write_text("#!/bin/sh\nmkdir -p \"$5\"\ncat <<'EOF'\n%s\nEOF\n" % output)
    cargo.chmod(0o755)
    return str(bindir)


def run_vendor(root, bindir):
    return subprocess.run(
        ["/bin/bash", str(root / "scripts" / "vendor-crates.sh")],
        env={"PATH": bindir + ":" + SYSTEM_PATH},
        capture_output=True,
        text=True,
    )


def test_vendor_script_writes_a_config_with_a_relative_directory(tmp_path):
    root = vendor_root(tmp_path)
    bindir = fake_cargo(tmp_path, '[source.vendored-sources]\ndirectory = "/x/vendor/crates"')
    result = run_vendor(root, bindir)
    assert result.returncode == 0, result.stderr
    config = (root / "vendor" / "config.toml").read_text()
    assert 'directory = "vendor/crates"' in config
    assert "/x/" not in config
    assert not (root / "vendor" / "config.tmp").exists()


@pytest.mark.parametrize("mode", [0o640, 0o600])
def test_vendored_files_are_readable_by_everyone(tmp_path, mode):
    # the container's build user may have another uid than the user that vendored
    root = vendor_root(tmp_path)
    bindir = fake_cargo(tmp_path, 'directory = "/x/vendor/crates"')
    cargo = os.path.join(bindir, "cargo")
    with open(cargo, "a", encoding="utf-8") as handle:
        handle.write(f'mkdir -p "$5/fnv" && echo x > "$5/fnv/lib.rs" && chmod {mode:o} "$5/fnv/lib.rs"\n')
    result = run_vendor(root, bindir)
    assert result.returncode == 0, result.stderr

    assert os.stat(root / "vendor" / "crates" / "fnv" / "lib.rs").st_mode & 0o004
    assert os.stat(root / "vendor" / "crates" / "fnv").st_mode & 0o005 == 0o005


def test_vendor_script_without_a_directory_line_leaves_no_config(tmp_path):
    root = vendor_root(tmp_path)
    (root / "vendor").mkdir()
    (root / "vendor" / "config.toml").write_text("stale")
    result = run_vendor(root, fake_cargo(tmp_path, "nothing useful"))
    assert result.returncode == 1
    assert "unexpected output of cargo vendor" in result.stderr
    assert not (root / "vendor" / "config.toml").exists()


def test_vendor_script_needs_the_submodule(tmp_path):
    root = vendor_root(tmp_path, with_lock=False)
    result = run_vendor(root, fake_cargo(tmp_path, 'directory = "/x"'))
    assert result.returncode == 1
    assert "git submodule update --init --recursive" in result.stderr
    assert not (root / "vendor").exists()


DH_CLEAN_RE = re.compile(r"^override_dh_clean:\n\tdh_clean -Xvendor/crates/\n", re.M)


def test_dh_clean_keeps_the_vendored_crates():
    # every crate has a Cargo.toml.orig (in its .cargo-checksum.json) and dh_clean deletes
    # *.orig; in CI vendor/ is mounted read-only, so the delete fails the build
    assert DH_CLEAN_RE.search(read("debian/rules"))


@pytest.mark.parametrize("old, new", [
    ("\tdh_clean -Xvendor/crates/\n", "\tdh_clean\n"),
    ("override_dh_clean:\n", "override_dh_clean_x:\n"),
])
def test_a_dh_clean_without_the_exclusion_is_found(old, new):
    rules = read("debian/rules")
    assert old in rules

    assert not DH_CLEAN_RE.search(rules.replace(old, new, 1))


def pkgbuild_problems(text):
    """What is wrong with the conditional offline flags of arch/PKGBUILD"""
    problems = []
    if not re.search(r'if \[ -f "\$PWD/vendor/config\.toml" \]; then\n\s*vendor_flags=\(--offline --config "\$PWD/vendor/config\.toml"\)\n\s*fi', text):
        problems.append("vendor_flags is not set only when vendor/config.toml exists")
    build = [l for l in text.splitlines() if "cargo auditable build" in l]
    if len(build) != 1 or '--locked "${vendor_flags[@]}"' not in build[0]:
        problems.append("the cargo build does not use --locked and the vendor flags")
    return problems


def test_pkgbuild_uses_vendored_crates_when_present():
    assert pkgbuild_problems(read("arch", "PKGBUILD")) == []


@pytest.mark.parametrize(
    "old, new",
    [
        ('if [ -f "$PWD/vendor/config.toml" ]; then', "if true; then"),
        ("--offline --config", "--config"),
        ('--locked "${vendor_flags[@]}"', "--locked"),
    ],
)
def test_pkgbuild_mutations_are_rejected(old, new):
    text = read("arch", "PKGBUILD")
    assert old in text
    assert pkgbuild_problems(text.replace(old, new)) != []
