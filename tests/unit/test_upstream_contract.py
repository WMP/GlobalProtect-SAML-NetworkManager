"""
Tests for scripts/upstream_contract.py, run against small synthetic source
trees (fake .rs files, Cargo.toml, Cargo.lock, changelog.md) in tmp_path: they
do not need the real submodule.

Every positive test has a negative counterpart: an item that is OK next to one
that is MOVED, CHANGED or MISSING, a valid verification record next to records
that are refused, a classified changelog entry next to unclassified or stale
ones.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "scripts", "upstream_contract.py")

_spec = importlib.util.spec_from_file_location("upstream_contract", SCRIPT)
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)

LIB_RS = """\
use something;

fn prompt_credential(name: &str) -> String {
    println!("Enter {}:", name);
    let x = 1;
    x.to_string()
}

fn other() {}
"""

TEST_RS = """\
#[cfg(test)]
mod tests {
    #[test]
    fn lock_file_defaults() {
        assert_eq!(1, 1);
    }
}
"""

CARGO_TOML = """\
[workspace]
members = ["a"]

[workspace.package]
version = "3.1.0"
edition = "2021"
"""

CARGO_LOCK = """\
version = 4

[[package]]
name = "inquire"
version = "0.9.4"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "crossterm"
version = "0.29.0"
"""

CHANGELOG = """\
# Changelog

## 3.1.0

### Added

- Added a dashboard
  that wraps onto a second line.

### Fixed

- Fixed one thing.

## 3.0.0

### Added

- Added the old thing.

## 2.6.5

### Fixed

- Fixed the baseline thing.

## 3.2.0

### Added

- Added the newer thing.
"""

GOOD_RECORD = {
    "commit": "a" * 40,
    "tag": "v3.1.0",
    "pr": 12,
    "tester": "someone",
    "date": "2026-01-31",
    "systems": [{"ubuntu": "24.04", "arch": "amd64", "desktop": "GNOME"}],
    "scenarios": {
        "install": "pass",
        "saml-login": "pass",
        "disconnect": "pass",
        "reconnect": "pass",
        "gateway-selection": "pass",
        "credentials-prompt": "n/a",
        "otp-prompt": "n/a",
        "fix-openssl": "n/a",
        "hip": "n/a",
    },
    "result": "pass",
    "notes": "",
}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(directory, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-C", str(directory), *args],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def commit_all(source):
    if not (source / ".git").exists():
        git(source, "init", "-q")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "x", "--allow-empty")
    return git(source, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src"
    write(src / "crates" / "a" / "lib.rs", LIB_RS)
    write(src / "crates" / "a" / "tests.rs", TEST_RS)
    write(src / "Cargo.toml", CARGO_TOML)
    write(src / "Cargo.lock", CARGO_LOCK)
    write(src / "changelog.md", CHANGELOG)
    return src


def block_item(source, start="fn prompt_credential(", path="crates/a/lib.rs", role="code"):
    text = (source / path).read_text(encoding="utf-8")
    block, why = uc.extract_block(text, start)
    assert block is not None, why
    return {"id": "b", "kind": "block", "role": role, "path": path, "start": start,
            "sha256": uc.block_hash(block), "used_by": "service: X"}


def anchor_item(text="Enter {}:", path="crates/a/lib.rs"):
    return {"id": "a", "kind": "anchor", "path": path, "text": text, "used_by": "service: Y"}


def lock_item(package="inquire", version="0.9.4"):
    return {"id": "c", "kind": "cargo-lock", "package": package, "version": version, "used_by": "service: Z"}


def status_of(item, source):
    return uc.evaluate([item], source)[0].status


# ---------------------------------------------------------------- anchors


def test_anchor_found_in_its_file_is_ok(source):
    assert status_of(anchor_item(), source) == uc.OK


def test_anchor_found_in_another_file_is_moved(source):
    write(source / "crates" / "b" / "moved.rs", 'fn f() { println!("Moved text here"); }\n')
    result = uc.evaluate([anchor_item("Moved text here")], source)[0]
    assert result.status == uc.MOVED
    assert "moved.rs" in result.detail


def test_anchor_found_in_other_file_types(source):
    write(source / "packaging" / "x.desktop", "Exec=/usr/bin/gpclient launch-gui %u\n")
    assert status_of(anchor_item("Exec=/usr/bin/gpclient launch-gui %u"), source) == uc.MOVED


@pytest.mark.parametrize("skipped", ["target", "deps"])
def test_anchor_only_in_a_skipped_directory_is_missing(source, skipped):
    write(source / skipped / "gen.rs", "Only in generated code\n")
    assert status_of(anchor_item("Only in generated code"), source) == uc.MISSING


def test_anchor_nowhere_is_missing(source):
    assert status_of(anchor_item("No such text anywhere"), source) == uc.MISSING


def test_anchor_in_a_missing_file_is_missing(source):
    assert status_of(anchor_item("No such text", path="crates/none.rs"), source) == uc.MISSING


# ---------------------------------------------------------------- blocks


def test_block_unchanged_is_ok(source):
    assert status_of(block_item(source), source) == uc.OK


def test_block_changed_is_changed(source):
    item = block_item(source)
    write(source / "crates" / "a" / "lib.rs", LIB_RS.replace("Enter {}:", "Type {}:"))
    assert status_of(item, source) == uc.CHANGED


def test_block_whitespace_only_change_keeps_the_hash(source):
    item = block_item(source)
    reformatted = LIB_RS.replace("    println!", "\n\n        println!").replace("let x = 1;", "let  x\t=   1;")
    write(source / "crates" / "a" / "lib.rs", reformatted)
    assert status_of(item, source) == uc.OK


def test_block_change_outside_the_block_keeps_the_hash(source):
    item = block_item(source)
    write(source / "crates" / "a" / "lib.rs", LIB_RS.replace("fn other() {}", "fn other() { 1; }"))
    assert status_of(item, source) == uc.OK


def test_block_start_not_found_is_missing(source):
    item = block_item(source)
    item["start"] = "fn no_such_function("
    result = uc.evaluate([item], source)[0]
    assert result.status == uc.MISSING
    assert "not found" in result.detail


def test_block_ambiguous_start_is_missing(source):
    item = block_item(source)
    write(source / "crates" / "a" / "lib.rs", LIB_RS + "\n" + LIB_RS.replace("fn other() {}", ""))
    result = uc.evaluate([item], source)[0]
    assert result.status == uc.MISSING
    assert "ambiguous" in result.detail


def test_block_file_missing_is_missing(source):
    item = block_item(source)
    item["path"] = "crates/none.rs"
    assert status_of(item, source) == uc.MISSING


def test_block_start_can_span_lines(source):
    write(source / "crates" / "a" / "two.rs",
          "#[cfg(unix)]\npub fn same() {\n  1;\n}\n#[cfg(windows)]\npub fn same() {\n  2;\n}\n")
    item = block_item(source, start="#[cfg(unix)]\npub fn same()", path="crates/a/two.rs")
    block, _ = uc.extract_block((source / "crates/a/two.rs").read_text(), item["start"])
    assert "1;" in block and "2;" not in block


def test_test_role_block_is_checked_like_code(source):
    item = block_item(source, start="fn lock_file_defaults(", path="crates/a/tests.rs", role="test")
    assert status_of(item, source) == uc.OK
    write(source / "crates" / "a" / "tests.rs", TEST_RS.replace("assert_eq!(1, 1)", "assert_eq!(1, 2)"))
    assert status_of(item, source) == uc.CHANGED


def test_block_with_an_unknown_role_is_refused(source):
    item = block_item(source)
    item["role"] = "doc"
    with pytest.raises(uc.ContractError):
        uc.evaluate([item], source)


# ---------------------------------------------------------------- brace matching


@pytest.mark.parametrize("body", [
    'let s = "a } b";',
    'let s = "a \\" } b";',
    'let s = r"a } b";',
    'let s = r#"a " } b"#;',
    'let s = r##"a "# } b"##;',
    "let c = '}';",
    "let c = '\\'';  let d = '}';",
    "let c = '\\u{7d}';",
    "// a } in a comment",
    "/* a } in a comment */",
    "/* nested /* } */ } */",
    'let b = b"}";',
])
def test_braces_in_strings_chars_and_comments_do_not_count(body):
    text = "fn f() {\n  " + body + "\n}\nfn after() { }\n"
    end = uc.block_end(text, 0)
    assert text[:end].endswith("}\n".rstrip("\n")) and text[:end].count("fn") == 1
    assert text[end:].strip().startswith("fn after")


def test_opening_brace_in_a_string_does_not_count():
    text = 'fn f() {\n  let s = "{";\n}\nfn after() { }\n'
    assert text[uc.block_end(text, 0):].strip().startswith("fn after")


def test_lifetime_is_not_a_char_literal():
    text = "fn f<'a>(x: &'a str) -> &'a str {\n  x\n}\nfn after() { }\n"
    assert text[uc.block_end(text, 0):].strip().startswith("fn after")


def test_lifetime_followed_by_brace_char_literal():
    text = "fn f<'a>(x: &'a str) {\n  let c = '{';\n}\nfn after() { }\n"
    assert text[uc.block_end(text, 0):].strip().startswith("fn after")


def test_nested_braces_are_matched():
    text = "fn f() {\n  if x { y } else { z }\n}\nfn after() { }\n"
    assert text[uc.block_end(text, 0):].strip().startswith("fn after")


def test_wrong_brace_counting_is_visible():
    # A naive counter would stop at the brace in the string; this one must not.
    text = 'fn f() {\n  let s = "}";\n  let t = 1;\n}\n'
    assert "let t" in text[:uc.block_end(text, 0)]


def test_unclosed_block_has_no_end():
    assert uc.block_end("fn f() {\n  let s = 1;\n", 0) is None


def test_unclosed_block_is_missing(source):
    item = block_item(source)
    write(source / "crates" / "a" / "lib.rs", "fn prompt_credential() {\n  let x = 1;\n")
    assert status_of(item, source) == uc.MISSING


def test_hash_is_sha256_of_the_collapsed_block():
    assert uc.block_hash("fn f() {\n   1;\n}") == hashlib.sha256(b"fn f() { 1; }").hexdigest()


# ---------------------------------------------------------------- cargo-lock


def test_cargo_lock_same_version_is_ok(source):
    assert status_of(lock_item(), source) == uc.OK


def test_cargo_lock_other_version_is_changed(source):
    assert status_of(lock_item(version="0.9.5"), source) == uc.CHANGED


def test_cargo_lock_absent_package_is_missing(source):
    assert status_of(lock_item(package="unicode-width", version="0.2.2"), source) == uc.MISSING


def test_cargo_lock_without_a_lock_file_is_missing(source):
    (source / "Cargo.lock").unlink()
    assert status_of(lock_item(), source) == uc.MISSING


def test_cargo_lock_accepts_any_of_several_versions(source):
    write(source / "Cargo.lock", CARGO_LOCK + '\n[[package]]\nname = "inquire"\nversion = "0.8.0"\n')
    assert status_of(lock_item(version="0.8.0"), source) == uc.OK


# ---------------------------------------------------------------- check and version


def make_contract(source, items=None, version="3.1.0"):
    return {"tag": "v3.1.0", "commit": "b" * 40, "version": version,
            "items": items if items is not None else [anchor_item(), block_item(source), lock_item()]}


def test_check_passes_when_everything_is_ok(source, tmp_path, capsys):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(make_contract(source)))
    assert uc.main(["check", "--source", str(source), "--contract", str(contract)]) == 0
    out = capsys.readouterr().out
    assert "OK a (crates/a/lib.rs) used by: service: Y" in out


def test_check_fails_and_names_the_item_when_one_is_not_ok(source, tmp_path, capsys):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(make_contract(source, [anchor_item("No such text")])))
    assert uc.main(["check", "--source", str(source), "--contract", str(contract)]) == 1
    assert "MISSING a (crates/a/lib.rs) used by: service: Y" in capsys.readouterr().out


def test_version_matching_the_source_is_ok(source):
    assert uc.check_contract(make_contract(source), source)[0].status == uc.OK


def test_version_not_matching_the_source_is_changed(source):
    result = uc.check_contract(make_contract(source, version="3.0.0"), source)[0]
    assert result.status == uc.CHANGED and "3.1.0" in result.detail


def test_version_without_a_cargo_toml_is_missing(source):
    (source / "Cargo.toml").unlink()
    assert uc.check_contract(make_contract(source), source)[0].status == uc.MISSING


def test_source_version_prefers_workspace_package_then_package(source):
    assert uc.source_version(source) == "3.1.0"
    write(source / "Cargo.toml", '[package]\nname = "x"\nversion = "1.2.3"\n[dependencies]\nfoo = { version = "9" }\n')
    assert uc.source_version(source) == "1.2.3"
    write(source / "Cargo.toml", '[dependencies]\nversion = "9.9.9"\n')
    assert uc.source_version(source) is None


def test_bad_contract_file_is_an_error(tmp_path, source, capsys):
    bad = tmp_path / "contract.json"
    bad.write_text("{not json")
    assert uc.main(["check", "--source", str(source), "--contract", str(bad)]) == 2
    assert uc.main(["check", "--source", str(source), "--contract", str(tmp_path / "none.json")]) == 2


def test_unknown_item_kind_is_an_error(source):
    with pytest.raises(uc.ContractError):
        uc.evaluate([{"id": "x", "kind": "magic"}], source)


# ---------------------------------------------------------------- update


def test_update_rewrites_hashes_versions_commit_and_tag_but_not_anchors(source, tmp_path, capsys):
    contract = make_contract(source)
    anchor = contract["items"][0]
    write(source / "crates" / "a" / "lib.rs", LIB_RS.replace("let x = 1;", "let x = 2;"))
    write(source / "Cargo.lock", CARGO_LOCK.replace("0.9.4", "0.9.9"))
    write(source / "Cargo.toml", CARGO_TOML.replace("3.1.0", "3.2.0"))
    commit = commit_all(source)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract, indent=2) + "\n")
    old_hash = contract["items"][1]["sha256"]

    assert uc.main(["update", "--source", str(source), "--contract", str(path), "--tag", "v3.2.0"]) == 0
    new = json.loads(path.read_text())
    assert new["tag"] == "v3.2.0" and new["commit"] == commit and new["version"] == "3.2.0"
    assert new["items"][1]["sha256"] != old_hash
    assert new["items"][2]["version"] == "0.9.9"
    assert new["items"][0] == anchor
    assert list(new) == ["tag", "commit", "version", "items"]
    assert path.read_text().endswith("}\n")
    assert uc.main(["check", "--source", str(source), "--contract", str(path)]) == 0


def test_update_with_a_tag_removes_the_legacy_flag(source, tmp_path, capsys):
    commit = commit_all(source)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(make_contract(source) | {"tag": None, "legacy_untagged": True}))

    assert uc.main(["update", "--source", str(source), "--contract", str(path), "--tag", "v3.1.0"]) == 0
    new = json.loads(path.read_text())
    assert new["tag"] == "v3.1.0" and new["commit"] == commit
    assert "legacy_untagged" not in new
    assert "legacy_untagged" in capsys.readouterr().out


def test_update_without_the_flag_does_not_add_one(source, tmp_path):
    commit_all(source)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(make_contract(source)))
    assert uc.main(["update", "--source", str(source), "--contract", str(path), "--tag", "v3.1.0"]) == 0
    assert "legacy_untagged" not in json.loads(path.read_text())


def test_update_does_not_hide_a_missing_anchor(source, tmp_path, capsys):
    commit_all(source)
    contract = make_contract(source, [anchor_item("No such text")])
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    assert uc.main(["update", "--source", str(source), "--contract", str(path), "--tag", "v3.1.0"]) == 1
    assert json.loads(path.read_text())["items"][0]["text"] == "No such text"
    assert "MISSING a" in capsys.readouterr().out


def test_update_needs_a_tag(source, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(make_contract(source)))
    with pytest.raises(SystemExit):
        uc.main(["update", "--source", str(source), "--contract", str(path)])


def test_update_needs_a_git_checkout(source, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(make_contract(source)))
    assert uc.main(["update", "--source", str(source), "--contract", str(path), "--tag", "v3.1.0"]) == 2


# ---------------------------------------------------------------- changelog


def test_parse_changelog_joins_wrapped_bullets_and_tracks_sections():
    bullets = uc.parse_changelog(CHANGELOG)
    assert ("3.1.0", "Added", "Added a dashboard that wraps onto a second line.") in bullets
    assert ("3.1.0", "Fixed", "Fixed one thing.") in bullets
    assert ("3.0.0", "Added", "Added the old thing.") in bullets
    assert len(bullets) == 5


def test_parse_changelog_ignores_text_outside_versions():
    assert uc.parse_changelog("# Changelog\n\n- loose\n\n## Unreleased\n\n### Added\n\n- not counted\n") == []


def entry(version, section, text, status="no-action", reason="because", **extra):
    return {"version": version, "section": section, "text": text, "status": status, "reason": reason, **extra}


def full_registry():
    return {"baseline": "2.6.5", "entries": [
        entry("3.1.0", "Added", "Added a dashboard that wraps onto a second line."),
        entry("3.1.0", "Fixed", "Fixed one thing."),
        entry("3.0.0", "Added", "Added the old thing."),
    ]}


def problems_of(registry, root, version="3.1.0"):
    return uc.check_features(registry, CHANGELOG, version, root)


def test_features_all_classified_passes(tmp_path):
    assert problems_of(full_registry(), tmp_path) == []


def test_features_empty_range_needs_no_entries(tmp_path):
    # the pin is at the baseline: nothing is new, an empty registry is complete
    assert uc.check_features({"baseline": "2.6.5", "entries": []}, CHANGELOG, "2.6.5", tmp_path) == []


def test_features_empty_range_refuses_an_entry(tmp_path):
    registry = {"baseline": "2.6.5", "entries": [entry("2.6.5", "Fixed", "Fixed the baseline thing.")]}
    assert uc.check_features(registry, CHANGELOG, "2.6.5", tmp_path)


def test_features_version_range_excludes_baseline_and_newer(tmp_path):
    # 2.6.5 (baseline) and 3.2.0 (newer than the pin) are not required...
    assert problems_of(full_registry(), tmp_path) == []
    # ...but when the pin moves up, 3.2.0 becomes required.
    problems = problems_of(full_registry(), tmp_path, version="3.2.0")
    assert problems == ["not classified: 3.2.0 Added: Added the newer thing."]


def test_features_entry_for_the_baseline_or_a_newer_version_is_stale(tmp_path):
    registry = full_registry()
    registry["entries"].append(entry("2.6.5", "Fixed", "Fixed the baseline thing."))
    registry["entries"].append(entry("3.2.0", "Added", "Added the newer thing."))
    problems = problems_of(registry, tmp_path)
    assert len([p for p in problems if p.startswith("stale entry")]) == 2


def test_features_unclassified_bullet_is_reported(tmp_path):
    registry = full_registry()
    del registry["entries"][1]
    assert problems_of(registry, tmp_path) == ["not classified: 3.1.0 Fixed: Fixed one thing."]


def test_features_stale_entry_is_reported(tmp_path):
    registry = full_registry()
    registry["entries"].append(entry("3.1.0", "Fixed", "Fixed a thing that is gone."))
    assert problems_of(registry, tmp_path)[0].startswith("stale entry")


def test_features_text_must_match_exactly(tmp_path):
    registry = full_registry()
    registry["entries"][1]["text"] = "Fixed one thing"
    problems = problems_of(registry, tmp_path)
    assert any(p.startswith("not classified") for p in problems) and any(p.startswith("stale") for p in problems)


def test_features_duplicate_entry_is_reported(tmp_path):
    registry = full_registry()
    registry["entries"].append(copy.deepcopy(registry["entries"][1]))
    assert any("classified 2 times" in p for p in problems_of(registry, tmp_path))


def test_features_unknown_status_is_reported(tmp_path):
    registry = full_registry()
    registry["entries"][0]["status"] = "done"
    assert any("bad status" in p for p in problems_of(registry, tmp_path))


@pytest.mark.parametrize("status", ["no-action", "not-applicable"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_features_status_without_a_reason_is_reported(tmp_path, status, reason):
    registry = full_registry()
    registry["entries"][0].update(status=status, reason=reason)
    assert any("without a reason" in p for p in problems_of(registry, tmp_path))


def test_features_tested_with_an_existing_test_passes(tmp_path):
    write(tmp_path / "tests" / "unit" / "test_x.py", "class TestA:\n    def test_it(self):\n        pass\n\ndef test_name():\n    pass\n")
    registry = full_registry()
    registry["entries"][0].update(status="tested", tests=["tests/unit/test_x.py::test_name", "tests/unit/test_x.py::TestA::test_it"])
    assert problems_of(registry, tmp_path) == []


@pytest.mark.parametrize("tests", [
    None,
    [],
    ["tests/unit/test_x.py::test_missing"],
    ["tests/unit/test_none.py::test_name"],
    ["tests/unit/test_x.py"],
    ["tests/unit/test_x.py::NoClass::test_name"],
])
def test_features_tested_with_a_bad_test_reference_is_reported(tmp_path, tests):
    write(tmp_path / "tests" / "unit" / "test_x.py", "def test_name():\n    pass\n")
    registry = full_registry()
    registry["entries"][0].update(status="tested")
    if tests is not None:
        registry["entries"][0]["tests"] = tests
    assert problems_of(registry, tmp_path)


def test_features_test_name_must_be_a_definition_not_a_prefix(tmp_path):
    write(tmp_path / "t.py", "def test_name_longer():\n    pass\n")
    assert uc.test_reference_problem("t.py::test_name", tmp_path)
    assert uc.test_reference_problem("t.py::test_name_longer", tmp_path) is None


def test_features_follow_up_needs_an_issue_and_a_reason(tmp_path):
    registry = full_registry()
    registry["entries"][0].update(status="follow-up", issue=38)
    assert problems_of(registry, tmp_path) == []
    for broken in ({"issue": None}, {"issue": "38"}, {"issue": True}, {"reason": ""}):
        registry = full_registry()
        registry["entries"][0].update({"status": "follow-up", "issue": 38, **broken})
        assert problems_of(registry, tmp_path), broken


def test_features_command_reports_problems_with_exit_1(source, tmp_path, capsys):
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(make_contract(source)))
    registry = tmp_path / "features.json"
    registry.write_text(json.dumps(full_registry()))
    args = ["features", "--source", str(source), "--contract", str(contract), "--registry", str(registry), "--root", str(tmp_path)]
    assert uc.main(args) == 0
    broken = full_registry()
    del broken["entries"][0]
    registry.write_text(json.dumps(broken))
    assert uc.main(args) == 1
    assert "ERROR not classified" in capsys.readouterr().out


# ---------------------------------------------------------------- verification records


def test_valid_record_has_no_problems():
    assert uc.validate_record(GOOD_RECORD) == []


def mutate(path, value):
    record = copy.deepcopy(GOOD_RECORD)
    target = record
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return record


@pytest.mark.parametrize("path,value", [
    (["commit"], "abc"),
    (["commit"], "A" * 40),
    (["commit"], KeyError),
    (["tag"], ""),
    (["pr"], "12"),
    (["pr"], KeyError),
    (["tester"], ""),
    (["date"], "31.01.2026"),
    (["systems"], []),
    (["systems"], [{"ubuntu": "24.04", "arch": "amd64"}]),
    (["scenarios", "install"], "fail"),
    (["scenarios", "install"], "n/a"),
    (["scenarios", "saml-login"], KeyError),
    (["scenarios", "disconnect"], "fail"),
    (["scenarios", "reconnect"], "n/a"),
    (["scenarios", "hip"], "fail"),
    (["scenarios", "hip"], KeyError),
    (["scenarios", "made-up"], "pass"),
    (["result"], "fail"),
    (["result"], KeyError),
])
def test_invalid_record_is_refused(path, value):
    assert uc.validate_record(mutate(path, value))


def test_record_that_is_not_an_object_is_refused():
    assert uc.validate_record("pass")


# ---------------------------------------------------------------- gate


@pytest.fixture
def gate_env(source, tmp_path):
    """A git source, a base contract that matches it, and the commit"""
    commit = commit_all(source)
    contract = make_contract(source)
    base = tmp_path / "base.json"
    base.write_text(json.dumps(contract))
    verification = tmp_path / "verification.json"
    verification.write_text(json.dumps({"records": []}))
    return {"source": source, "commit": commit, "contract": contract, "base": base,
            "verification": verification, "tmp": tmp_path}


def run_gate(env, labels=(), base=None, base_commit=None):
    extra = [] if base_commit is None else ["--base-commit", base_commit]
    return uc.main([
        "gate", "--source", str(env["source"]),
        "--base-contract", str(env["base"] if base is None else base),
        "--verification", str(env["verification"]),
        "--labels", json.dumps(list(labels)),
        *extra,
    ])


def add_record(env, **changes):
    record = {**copy.deepcopy(GOOD_RECORD), "commit": env["commit"], **changes}
    env["verification"].write_text(json.dumps({"records": [record]}))


def set_base_version(env, version):
    env["contract"]["version"] = version
    env["base"].write_text(json.dumps(env["contract"]))


def set_changelog(env, text, version=None):
    write(env["source"] / "changelog.md", text)
    if version:
        write(env["source"] / "Cargo.toml", CARGO_TOML.replace("3.1.0", version))
    env["commit"] = commit_all(env["source"])


def make_breaking(env):
    """The base is 3.0.0 and 3.1.0 has a "Command-line notes" section"""
    set_base_version(env, "3.0.0")
    set_changelog(env, CHANGELOG.replace("### Fixed\n\n- Fixed one thing.", "### Command-line notes\n\n- Changed a flag."))


def changelog_with(version, section, bullet):
    return f"# Changelog\n\n## {version}\n\n### {section}\n\n- {bullet}\n\n## 3.0.0\n\n### Added\n\n- Old.\n"


def test_gate_not_breaking_passes_without_record_or_label(gate_env, capsys):
    assert run_gate(gate_env) == 0
    assert "not breaking" in capsys.readouterr().out


def test_gate_not_breaking_when_only_unrelated_code_changes(gate_env):
    write(gate_env["source"] / "crates" / "a" / "other.rs", "fn unrelated() {}\n")
    commit_all(gate_env["source"])
    assert run_gate(gate_env) == 0


def drift(env, how):
    src = env["source"]
    if how == "changed-block":
        write(src / "crates" / "a" / "lib.rs", LIB_RS.replace("let x = 1;", "let x = 2;"))
    elif how == "missing-anchor":
        write(src / "crates" / "a" / "lib.rs", LIB_RS.replace("Enter", "Type"))
    elif how == "moved-anchor":
        write(src / "crates" / "a" / "lib.rs", LIB_RS.replace('"Enter {}:"', '"Other"'))
        write(src / "crates" / "a" / "elsewhere.rs", 'fn g() { "Enter {}:"; }\n')
    elif how == "cargo-lock":
        write(src / "Cargo.lock", CARGO_LOCK.replace("0.9.4", "0.9.5"))
    elif how == "missing-block":
        write(src / "crates" / "a" / "lib.rs", LIB_RS.replace("prompt_credential", "renamed"))
    commit_all(src)


@pytest.mark.parametrize("how", ["changed-block", "missing-anchor", "moved-anchor", "cargo-lock", "missing-block"])
def test_gate_contract_drift_alone_is_not_breaking(gate_env, how, capsys):
    drift(gate_env, how)
    # the contract really does not match any more (check would fail)...
    if how != "moved-anchor":
        assert any(r.status != uc.OK for r in uc.check_contract(gate_env["contract"], gate_env["source"]))
    # ...but the gate needs neither a record nor a label
    assert run_gate(gate_env) == 0
    assert "not breaking" in capsys.readouterr().out


@pytest.mark.parametrize("how", ["no-baseline", "empty-path"])
def test_gate_without_a_base_contract_is_breaking(gate_env, how, capsys):
    base = gate_env["tmp"] / "does-not-exist.json" if how == "no-baseline" else ""
    assert run_gate(gate_env, base=base) == 1
    out = capsys.readouterr().out
    assert "no accepted baseline" in out and "BREAKING" in out and "docs/UPSTREAM.md" in out


# The upstream commit is the one of the base branch: nothing changed upstream

OTHER_COMMIT = "c" * 40


def test_gate_unchanged_commit_is_not_breaking_without_a_base_contract(gate_env, capsys):
    base = gate_env["tmp"] / "does-not-exist.json"
    assert run_gate(gate_env, base=base, base_commit=gate_env["commit"]) == 0
    out = capsys.readouterr().out
    assert "Upstream commit unchanged" in out and "BREAKING" not in out


def test_gate_unchanged_commit_is_not_breaking_even_with_a_breaking_changelog(gate_env, capsys):
    make_breaking(gate_env)
    assert run_gate(gate_env, base_commit=gate_env["commit"]) == 0
    assert "Upstream commit unchanged" in capsys.readouterr().out


def test_gate_a_different_commit_without_a_base_contract_is_breaking(gate_env, capsys):
    base = gate_env["tmp"] / "does-not-exist.json"
    assert run_gate(gate_env, base=base, base_commit=OTHER_COMMIT) == 1
    assert "no accepted baseline" in capsys.readouterr().out


def test_gate_a_different_commit_with_a_breaking_changelog_is_still_breaking(gate_env, capsys):
    make_breaking(gate_env)
    assert run_gate(gate_env, base_commit=OTHER_COMMIT) == 1
    assert "Command-line notes" in capsys.readouterr().out


@pytest.mark.parametrize("base_commit", ["", None])
def test_gate_an_empty_base_commit_keeps_the_old_behaviour(gate_env, base_commit, capsys):
    base = gate_env["tmp"] / "does-not-exist.json"
    assert run_gate(gate_env, base=base, base_commit=base_commit) == 1
    out = capsys.readouterr().out
    assert "no accepted baseline" in out and "unchanged" not in out


def test_gate_function_does_not_call_the_source_unchanged_when_its_commit_is_unknown(tmp_path):
    outcome = uc.gate(tmp_path, None, {"records": []}, [], base_commit="a" * 40)
    assert outcome["breaking"] and not outcome["unchanged"]


def test_gate_breaking_message_lists_reason_and_docs(gate_env, capsys):
    make_breaking(gate_env)
    assert run_gate(gate_env) == 1
    out = capsys.readouterr().out
    assert "3.1.0 / Command-line notes: Changed a flag." in out and "docs/UPSTREAM.md" in out


@pytest.mark.parametrize("base_version", ["3.1.1", "3.2.0", "4.0.0"])
def test_gate_a_downgrade_is_breaking_and_says_so(gate_env, base_version, capsys):
    set_base_version(gate_env, base_version)
    assert run_gate(gate_env) == 1
    out = capsys.readouterr().out
    assert f"version went down: {base_version} -> 3.1.0" in out
    assert "changelog not found" not in out


@pytest.mark.parametrize("base_version", ["3.1.0", "3.0.5"])
def test_gate_the_same_version_or_an_upgrade_is_not_reported_as_a_downgrade(gate_env, base_version, capsys):
    set_base_version(gate_env, base_version)
    run_gate(gate_env)
    assert "version went down" not in capsys.readouterr().out


@pytest.mark.parametrize("section", ["Breaking", "Breaking changes", "Command-line notes", "Removed", "Deprecated", "REMOVED FEATURES"])
def test_gate_breaking_section_name_is_breaking(gate_env, section, capsys):
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, changelog_with("3.1.0", section, "A harmless bullet."))
    assert run_gate(gate_env) == 1
    assert f"3.1.0 / {section}: A harmless bullet." in capsys.readouterr().out


@pytest.mark.parametrize("section", ["Added", "Fixed", "Improved", "Changed"])
def test_gate_ordinary_sections_with_harmless_bullets_are_not_breaking(gate_env, section):
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, changelog_with("3.1.0", section, "Added a nicer dialog."))
    assert run_gate(gate_env) == 0


@pytest.mark.parametrize("word", list(uc.BREAKING_WORDS) + ["Breaking", "MUST", "Removed"])
def test_gate_breaking_word_in_a_bullet_is_breaking(gate_env, word):
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, changelog_with("3.1.0", "Improved", f"The old flag {word} now, use the new one."))
    assert run_gate(gate_env) == 1


@pytest.mark.parametrize("bullet", [
    "The tool no longer reports an error.",
    "Added mustard to the menu.",
    "A renamedly odd word.",
    "Removal of the old cache is planned.",
    "Undeprecated nothing.",
    "The breakingly fast start.",
    "Improved the incompatibility message.",
])
def test_gate_near_misses_are_not_breaking(gate_env, bullet):
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, changelog_with("3.1.0", "Improved", bullet))
    assert run_gate(gate_env) == 0


def test_gate_a_word_before_a_hyphen_is_a_whole_word(gate_env):
    # \b sees "must" in "must-have" as a whole word: it counts, by design
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, changelog_with("3.1.0", "Improved", "Added a must-have panel."))
    assert run_gate(gate_env) == 1


def test_gate_breaking_bullet_in_the_base_version_or_a_newer_one_is_ignored(gate_env):
    text = (changelog_with("3.1.0", "Added", "Harmless.")
            + "\n## 3.2.0\n\n### Removed\n\n- Removed a thing.\n"
            + "\n## 3.0.0\n\n### Removed\n\n- Removed an old thing.\n")
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, text)
    assert run_gate(gate_env) == 0


def test_gate_breaking_bullet_between_base_and_current_counts(gate_env):
    text = changelog_with("3.1.0", "Added", "Harmless.") + "\n## 3.0.5\n\n### Fixed\n\n- Removed a thing.\n"
    set_base_version(gate_env, "3.0.0")
    set_changelog(gate_env, text)
    assert run_gate(gate_env) == 1


def test_gate_major_bump_with_a_harmless_changelog_is_breaking(gate_env, capsys):
    set_changelog(gate_env, changelog_with("4.0.0", "Added", "Harmless."), version="4.0.0")
    assert run_gate(gate_env) == 1
    assert "major version changed: 3.1.0 -> 4.0.0" in capsys.readouterr().out


def test_gate_minor_bump_with_a_harmless_changelog_is_not_breaking(gate_env):
    set_changelog(gate_env, changelog_with("3.2.0", "Added", "Harmless."), version="3.2.0")
    assert run_gate(gate_env) == 0


def test_gate_same_version_is_not_breaking_even_with_breaking_text(gate_env):
    set_changelog(gate_env, changelog_with("3.1.0", "Removed", "Removed a thing."))
    assert run_gate(gate_env) == 0


@pytest.mark.parametrize("changelog", ["missing", "no-entries"])
def test_gate_missing_or_empty_changelog_with_a_version_change_is_breaking(gate_env, changelog, capsys):
    set_base_version(gate_env, "3.0.0")
    if changelog == "missing":
        (gate_env["source"] / "changelog.md").unlink()
        gate_env["commit"] = commit_all(gate_env["source"])
    else:
        set_changelog(gate_env, "# Changelog\n")
    assert run_gate(gate_env) == 1
    assert "changelog not found" in capsys.readouterr().out


def test_gate_missing_changelog_with_the_same_version_is_not_breaking(gate_env):
    (gate_env["source"] / "changelog.md").unlink()
    commit_all(gate_env["source"])
    assert run_gate(gate_env) == 0


def test_gate_breaking_fails_with_record_but_no_label(gate_env, capsys):
    make_breaking(gate_env)
    add_record(gate_env)
    assert run_gate(gate_env, labels=["other"]) == 1
    assert "upstream-verified" in capsys.readouterr().out


def test_gate_breaking_fails_with_label_but_no_record(gate_env, capsys):
    make_breaking(gate_env)
    assert run_gate(gate_env, labels=["upstream-verified"]) == 1
    assert "no record" in capsys.readouterr().out


def test_gate_breaking_fails_with_label_and_a_record_for_another_commit(gate_env):
    make_breaking(gate_env)
    add_record(gate_env, commit="c" * 40)
    assert run_gate(gate_env, labels=["upstream-verified"]) == 1


@pytest.mark.parametrize("changes", [
    {"result": "fail"},
    {"scenarios": {**GOOD_RECORD["scenarios"], "install": "fail"}},
    {"scenarios": {k: v for k, v in GOOD_RECORD["scenarios"].items() if k != "reconnect"}},
    {"systems": []},
])
def test_gate_breaking_fails_with_label_and_an_invalid_record(gate_env, changes, capsys):
    make_breaking(gate_env)
    add_record(gate_env, **changes)
    assert run_gate(gate_env, labels=["upstream-verified"]) == 1
    assert "not valid" in capsys.readouterr().out


def test_gate_breaking_passes_with_valid_record_and_label(gate_env, capsys):
    make_breaking(gate_env)
    add_record(gate_env)
    assert run_gate(gate_env, labels=["bug", "upstream-verified"]) == 0
    assert "BREAKING" in capsys.readouterr().out


def test_gate_compares_with_the_base_not_with_the_contract_of_the_pr(gate_env):
    # The PR updates upstream/contract.json to accept the change: that must not help.
    make_breaking(gate_env)
    pr_contract = gate_env["tmp"] / "pr-contract.json"
    new_item = block_item(gate_env["source"])
    pr_contract.write_text(json.dumps(make_contract(gate_env["source"], [new_item])))
    assert uc.evaluate([new_item], gate_env["source"])[0].status == uc.OK
    assert uc.main(["gate", "--source", str(gate_env["source"]), "--base-contract", str(gate_env["base"]),
                    "--contract", str(pr_contract), "--verification", str(gate_env["verification"]),
                    "--labels", "[]"]) == 1


def test_gate_writes_github_output_and_summary(gate_env, monkeypatch):
    out = gate_env["tmp"] / "output"
    summary = gate_env["tmp"] / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert run_gate(gate_env) == 0
    assert out.read_text().splitlines() == ["breaking=false", "verified=false"]
    assert "not breaking" in summary.read_text()
    make_breaking(gate_env)
    add_record(gate_env)
    assert run_gate(gate_env, labels=["upstream-verified"]) == 0
    assert out.read_text().splitlines()[2:] == ["breaking=true", "verified=true"]
    assert run_gate(gate_env, labels=[]) == 1
    assert out.read_text().splitlines()[4:] == ["breaking=true", "verified=false"]


def test_gate_writes_nothing_when_the_variables_are_not_set(gate_env, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert run_gate(gate_env) == 0
    assert not (gate_env["tmp"] / "output").exists()


def test_gate_without_a_git_checkout_cannot_match_a_record(gate_env, tmp_path):
    make_breaking(gate_env)
    add_record(gate_env)
    outcome = uc.gate(gate_env["source"], gate_env["contract"], {"records": [dict(GOOD_RECORD, commit=gate_env["commit"])]},
                      ["upstream-verified"], commit="")
    assert outcome["breaking"] and not outcome["verified"]


@pytest.mark.parametrize("labels", ["not json", '{"a": 1}'])
def test_gate_bad_labels_is_an_error(gate_env, labels):
    assert uc.main(["gate", "--source", str(gate_env["source"]), "--base-contract", str(gate_env["base"]),
                    "--verification", str(gate_env["verification"]), "--labels", labels]) == 2


def test_gate_empty_labels_means_no_labels(gate_env):
    make_breaking(gate_env)
    add_record(gate_env)
    assert uc.main(["gate", "--source", str(gate_env["source"]), "--base-contract", str(gate_env["base"]),
                    "--verification", str(gate_env["verification"]), "--labels", ""]) == 1


# ---------------------------------------------------------------- README pin and Rust toolchain

PIN_LINE = "Upstream version: [`v3.1.0`](https://github.com/yuezk/GlobalProtect-openconnect/releases/tag/v3.1.0)"


def test_readme_naming_the_pinned_tag_is_accepted():
    assert uc.readme_problem("# X\n\n" + PIN_LINE + "\n\ntext\n", "v3.1.0") is None


@pytest.mark.parametrize("text", [
    "no pin here\n",
    PIN_LINE + "\n" + PIN_LINE + "\n",
    PIN_LINE.replace("`v3.1.0`", "`v3.0.5`") + "\n",
    PIN_LINE.replace("tag/v3.1.0", "tag/v3.0.5") + "\n",
    PIN_LINE.replace("`v3.1.0`", "`main`") + "\n",
    "Upstream version: v3.1.0\n",
    "see Upstream version: [`v3.1.0`](https://github.com/yuezk/GlobalProtect-openconnect/releases/tag/v3.1.0)\n",
])
def test_readme_not_naming_the_pinned_tag_is_refused(text):
    assert uc.readme_problem(text, "v3.1.0")


LEGACY = uc.LEGACY_UNTAGGED_COMMIT
LEGACY_LINE = (f"Upstream version: untagged commit [`{LEGACY[:7]}`]"
               f"(https://github.com/yuezk/GlobalProtect-openconnect/commit/{LEGACY}) (v2.6.5+14)")


def legacy_contract(**changes):
    return {"tag": None, "legacy_untagged": True, "commit": LEGACY, "version": "2.6.5", "items": [], **changes}


def test_legacy_pin_is_accepted_for_the_exact_commit():
    assert uc.pin_problem(legacy_contract()) is None


@pytest.mark.parametrize("changes", [
    {"commit": "d" * 40},                      # another commit
    {"commit": LEGACY[:7]},                    # not the full commit
    {"legacy_untagged": False},                # no flag, and no tag
    {"tag": "v3.1.0"},                         # flag and tag exclude each other
    {"tag": "v3.1.0", "commit": "d" * 40},
    {"legacy_untagged": "true"},               # not a boolean
    {"legacy_untagged": 1},
])
def test_legacy_pin_is_refused_otherwise(changes):
    assert uc.pin_problem(legacy_contract(**changes))


def test_untagged_pin_without_the_flag_is_refused():
    contract = legacy_contract()
    del contract["legacy_untagged"]
    assert uc.pin_problem(contract)


def test_a_tagged_pin_is_accepted_and_a_bad_tag_is_refused():
    assert uc.pin_problem({"tag": "v3.1.0", "commit": "d" * 40}) is None
    assert uc.pin_problem({"tag": "main", "commit": "d" * 40})
    assert uc.pin_problem({"tag": "v3.1.0", "legacy_untagged": False, "commit": "d" * 40}) is None


def test_readme_legacy_line_is_accepted_for_the_pinned_commit():
    assert uc.readme_legacy_problem("# X\n\n" + LEGACY_LINE + "\n\ntext\n", LEGACY) is None
    assert uc.readme_pin_problem(LEGACY_LINE + "\n", legacy_contract()) is None


@pytest.mark.parametrize("text", [
    "no pin here\n",
    LEGACY_LINE + "\n" + LEGACY_LINE + "\n",
    LEGACY_LINE.replace(LEGACY, "d" * 40) + "\n",                          # another commit
    LEGACY_LINE.replace(f"`{LEGACY[:7]}`", "`ddddddd`") + "\n",             # short name differs
    LEGACY_LINE.replace("untagged commit", "commit") + "\n",
    "see " + LEGACY_LINE + "\n",
    PIN_LINE + "\n",                                                        # a tag line does not name the commit
    LEGACY_LINE + "\n" + PIN_LINE + "\n",
])
def test_readme_legacy_line_that_does_not_match_is_refused(text):
    assert uc.readme_legacy_problem(text, LEGACY)
    assert uc.readme_pin_problem(text, legacy_contract())


def test_readme_legacy_line_does_not_name_a_tag():
    assert uc.readme_problem(LEGACY_LINE + "\n", "v3.1.0")
    assert uc.readme_problem(LEGACY_LINE + "\n" + PIN_LINE + "\n", "v3.1.0")
    assert uc.readme_pin_problem(PIN_LINE + "\n", {"tag": "v3.1.0"}) is None


def test_toolchain_channel_is_read():
    assert uc.toolchain_channel('[toolchain]\nchannel = "1.91"\n') == "1.91"
    assert uc.toolchain_channel("[toolchain]\n") is None


def test_build_toolchain_not_older_than_upstream_is_accepted():
    assert uc.toolchain_problems("1.91", "Dockerfile", ["1.91"]) == []
    assert uc.toolchain_problems("1.91", "Dockerfile", ["1.91.1", "1.92"]) == []


@pytest.mark.parametrize("versions", [["1.90"], ["1.91", "1.85.2"], [], ["stable"]])
def test_build_toolchain_older_than_upstream_is_refused(versions):
    assert uc.toolchain_problems("1.91", "Dockerfile", versions)


@pytest.mark.parametrize("channel", [None, "stable", "nightly-2025-01-01"])
def test_upstream_channel_that_is_not_a_version_is_refused(channel):
    assert uc.toolchain_problems(channel, "Dockerfile", ["1.91"])


def test_rust_versions_are_read_from_dockerfile_and_makefile():
    docker = "RUN curl x | sh -s -- -y --default-toolchain 1.91\nRUN other\n"
    assert uc.dockerfile_rust_versions(docker) == ["1.91"]
    assert uc.dockerfile_rust_versions("RUN apt-get install rustc\n") == []
    assert uc.makefile_rust_version("RUST_VERSION = 1.91\nall:\n") == ["1.91"]
    assert uc.makefile_rust_version("all:\n\techo RUST_VERSION = 1.91\n") == []


def test_release_tag_is_accepted():
    assert uc.tag_problem("v3.1.0") is None


@pytest.mark.parametrize("tag", ["main", "master", "v3.1", "3.1.0", "v3.1.0-rc1", "v3.1.0+14", "", None, "origin/main"])
def test_tag_that_is_not_a_release_is_refused(tag):
    assert uc.tag_problem(tag)


# ---------------------------------------------------------------- interfaces (functions that read or run upstream)

FAKE_SERVICE = '''\
import re

GATEWAY_COUNT_RE = re.compile(r"Found ([0-9]+) gateways")
GPCLIENT_BINARY = "/usr/bin/gpclient"
OTHER = "text"


def parse_count(line):
    return GATEWAY_COUNT_RE.search(line)


def unrelated(x):
    return x + 1


def mentions_text_only():
    return "gpclient start - ignored"


def run_by_constant():
    return [GPCLIENT_BINARY, "connect"]


def run_by_literal():
    return ["gpauth", "--gateway"]


def run_by_call_argument(spawn):
    return spawn("/usr/bin/gpclient", "disconnect")


class Plugin:
    def __init__(self):
        self._pty_master = None

    def reads_pty(self):
        return self._pty_master

    def reads_stdout(self):
        return self.gpclient_process.stdout

    def decodes(self, chunk):
        return self._decode_output(chunk)

    def plain(self):
        return 1
'''

FAKE_TESTS = '''\
def test_parse_count_positive():
    assert parse_count("Found 2 gateways")


def test_parse_count_negative():
    assert not parse_count("nothing")


def test_other_thing():
    assert 1


class TestPlugin:
    def test_reads_pty_positive(self):
        assert Plugin().reads_pty() is None

    def test_reads_pty_negative(self):
        assert Plugin().other() is None
'''

FAKE_CONTRACT = {"items": [
    {"id": "gateway-count", "kind": "anchor", "path": "x", "text": "x",
     "used_by": "service/nm-gpclient-service.py: GATEWAY_COUNT_RE"},
    {"id": "other", "kind": "anchor", "path": "x", "text": "x",
     "used_by": "service/nm-gpclient-service.py: NOT_A_NAME, GPCLIENT_BINARY_MISSING (words)"},
]}


def detect(text, contract=FAKE_CONTRACT):
    return uc.scan_service(text, contract)


@pytest.mark.parametrize("function,why", [
    ("parse_count", "uses GATEWAY_COUNT_RE"),
    ("run_by_constant", "command GPCLIENT_BINARY"),
    ("run_by_literal", "command gpauth"),
    ("run_by_call_argument", "command /usr/bin/gpclient"),
    ("Plugin.reads_pty", "accesses _pty_master"),
    ("Plugin.reads_stdout", "reads gpclient_process.stdout"),
    ("Plugin.decodes", "calls _decode_output"),
])
def test_scanner_detects_each_kind_of_upstream_facing_function(function, why):
    assert why in detect(FAKE_SERVICE)[function]


@pytest.mark.parametrize("function", ["unrelated", "mentions_text_only", "Plugin.plain", "Plugin.__init__"])
def test_scanner_does_not_detect_unrelated_functions(function):
    assert function not in detect(FAKE_SERVICE)


def test_scanner_names_come_from_used_by_and_must_exist_in_the_service():
    assert uc.contract_service_names(FAKE_CONTRACT) >= {"GATEWAY_COUNT_RE", "NOT_A_NAME"}
    # a name in the contract that the service does not define detects nothing
    assert "parse_count" not in detect(FAKE_SERVICE, {"items": []})


def test_scanner_follows_the_contract_when_it_changes():
    contract = {"items": [{"id": "z", "used_by": "service/nm-gpclient-service.py: OTHER"}]}
    assert "parse_count" not in detect(FAKE_SERVICE, contract)
    assert "uses OTHER" not in sum(detect(FAKE_SERVICE, contract).values(), [])


@pytest.fixture
def repo(tmp_path):
    write(tmp_path / "service" / "nm-gpclient-service.py", FAKE_SERVICE)
    write(tmp_path / "tests" / "unit" / "test_fake.py", FAKE_TESTS)
    write(tmp_path / "scripts" / "wrapper-script.sh", "#!/bin/sh\n")
    return tmp_path


T = "tests/unit/test_fake.py::"
S = "service/nm-gpclient-service.py::"


def fake_entry(function, tests, upstream=("gateway-count",), kind="reads-output"):
    return {"function": S + function, "kind": kind, "upstream": list(upstream), "tests": list(tests)}


def good_registry(skip=()):
    entries = {
        "parse_count": fake_entry("parse_count", [T + "test_parse_count_positive", T + "test_parse_count_negative"]),
        "run_by_constant": fake_entry("run_by_constant", [T + "test_parse_count_positive", T + "test_parse_count_negative"]),
    }
    return {"functions": [e for k, e in entries.items() if k not in skip]}


def interface_problems(repo, registry, service=None):
    contract = {"items": [{"id": "gateway-count", "used_by": "service/nm-gpclient-service.py: GATEWAY_COUNT_RE"}]}
    # only the two functions of the registry are upstream-facing in this service
    write(repo / "service" / "nm-gpclient-service.py", service or LIMITED_SERVICE)
    return uc.check_interfaces(registry, contract, repo)


LIMITED_SERVICE = FAKE_SERVICE.split("def run_by_literal")[0]


def test_interfaces_all_registered_and_tested_passes(repo):
    problems = interface_problems(repo, good_registry())
    # run_by_constant does not name its test: the mention check is the one problem
    assert problems == ["service/nm-gpclient-service.py::run_by_constant: no listed test mentions 'run_by_constant'"]


def test_interfaces_clean_registry_has_no_problems(repo):
    registry = good_registry(skip=["run_by_constant"])
    service = LIMITED_SERVICE.replace("def run_by_constant", "def _ignored").replace("GPCLIENT_BINARY, ", "")
    assert interface_problems(repo, registry, service) == []


def test_interfaces_detected_function_missing_from_the_registry_is_reported(repo):
    registry = good_registry(skip=["parse_count", "run_by_constant"])
    problems = interface_problems(repo, registry)
    assert any(p.startswith(S + "parse_count: upstream-facing") for p in problems)


def test_interfaces_stale_entry_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"].append(fake_entry("gone", [T + "test_parse_count_positive", T + "test_parse_count_negative"]))
    assert any(p == S + "gone: no such function (stale entry)" for p in interface_problems(repo, registry))


def test_interfaces_entry_with_fewer_than_two_tests_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["tests"] = [T + "test_parse_count_positive"]
    registry["functions"][0]["tests"].append(T + "test_parse_count_positive")  # twice is still one test
    assert any("at least 2 tests" in p for p in interface_problems(repo, registry))


def test_interfaces_nonexistent_test_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["tests"][1] = T + "test_not_there"
    assert any("missing test" in p and "test_not_there" in p for p in interface_problems(repo, registry))


def test_interfaces_tests_that_do_not_name_the_function_are_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["tests"] = [T + "test_other_thing", T + "test_parse_count_negative"]
    registry["functions"][0]["tests"][0] = T + "test_other_thing"
    registry["functions"][0]["tests"][1] = T + "test_other_thing2"
    write(repo / "tests" / "unit" / "test_fake.py", FAKE_TESTS + "\n\ndef test_other_thing2():\n    assert 2\n")
    problems = interface_problems(repo, registry)
    assert any("no listed test mentions 'parse_count'" in p for p in problems)


def test_interfaces_one_naming_test_is_enough_for_indirect_tests(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["tests"] = [T + "test_parse_count_positive", T + "test_other_thing"]
    service = LIMITED_SERVICE.replace("def run_by_constant", "def _ignored").replace("GPCLIENT_BINARY, ", "")
    assert interface_problems(repo, registry, service) == []


def test_interfaces_unknown_upstream_id_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["upstream"] = ["no-such-item"]
    assert any("unknown upstream id 'no-such-item'" in p for p in interface_problems(repo, registry))


def test_interfaces_bad_kind_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][0]["kind"] = "other"
    assert any("kind must be" in p for p in interface_problems(repo, registry))


def test_interfaces_function_listed_twice_is_reported(repo):
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"].append(copy.deepcopy(registry["functions"][0]))
    assert any("listed twice" in p for p in interface_problems(repo, registry))


def test_interfaces_method_name_is_matched_in_a_class_test(repo):
    service = LIMITED_SERVICE + "\n\nclass Plugin:\n    def reads_pty(self):\n        return self._pty_master\n"
    registry = good_registry(skip=["run_by_constant"])
    registry["functions"][1:] = []
    registry["functions"].append(fake_entry("Plugin.reads_pty", [T + "TestPlugin::test_reads_pty_positive", T + "TestPlugin::test_reads_pty_negative"]))
    service = service.replace("def run_by_constant", "def _ignored").replace("GPCLIENT_BINARY, ", "")
    assert interface_problems(repo, registry, service) == []


def test_interfaces_script_entry_is_checked_for_existence_and_tests(repo):
    registry = good_registry(skip=["run_by_constant"])
    service = LIMITED_SERVICE.replace("def run_by_constant", "def _ignored").replace("GPCLIENT_BINARY, ", "")
    write(repo / "tests" / "unit" / "test_fake.py", FAKE_TESTS + '\nSCRIPT = "scripts/wrapper-script.sh"\n')
    registry["functions"].append({"function": "scripts/wrapper-script.sh", "kind": "runs-upstream", "upstream": [],
                                  "tests": [T + "test_parse_count_positive", T + "test_parse_count_negative"]})
    write(repo / "tests" / "unit" / "test_fake.py", FAKE_TESTS + "\n# wrapper-script\n")
    assert interface_problems(repo, registry, service) == []
    registry["functions"][-1]["function"] = "scripts/gone.sh"
    assert any("stale entry" in p for p in interface_problems(repo, registry, service))
