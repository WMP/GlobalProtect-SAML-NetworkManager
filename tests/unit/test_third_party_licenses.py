"""
Issue #148: scripts/third_party_licenses.py writes the licenses of the Rust crates
that gpclient and gpauth link. Fake crate directories and a fake `cargo metadata`
JSON; cargo is never run.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import importlib.util
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("tpl", os.path.join(ROOT, "scripts", "third_party_licenses.py"))
tpl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpl)

REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"


def package(tmp_path, name, license_="MIT", files=None, source=REGISTRY, version="1.0.0", license_file=None):
    """A package of the metadata, with its crate directory (files: {name: text})"""
    directory = tmp_path / f"{name}-{version}"
    directory.mkdir()
    (directory / "Cargo.toml").write_text("")
    for fname, text in (files if files is not None else {"LICENSE-MIT": f"MIT text of {name}"}).items():
        (directory / fname).write_text(text)
    return {"id": f"{name} {version}", "name": name, "version": version, "license": license_,
            "license_file": license_file, "source": source, "manifest_path": str(directory / "Cargo.toml")}


def metadata(packages, edges, kinds=None):
    """edges: {id: [dependency ids]}; kinds: {(id, dependency id): kind} (default: normal, None)"""
    kinds = kinds or {}
    nodes = [{"id": p["id"], "deps": [
        {"pkg": d, "dep_kinds": [{"kind": kinds.get((p["id"], d)), "target": None}]}
        for d in edges.get(p["id"], [])]} for p in packages]
    members = [p["id"] for p in packages if not p["source"]]
    return {"packages": packages, "workspace_members": members, "resolve": {"nodes": nodes}}


@pytest.fixture
def tree(tmp_path):
    """gpclient and gpauth (workspace) with a workspace library, a crate and a dual license crate"""
    gpclient = package(tmp_path, "gpclient", "GPL-3.0", {}, source=None)
    gpauth = package(tmp_path, "gpauth", "GPL-3.0", {}, source=None)
    gpapi = package(tmp_path, "gpapi", "MIT", {}, source=None)
    serde = package(tmp_path, "serde", "MIT OR Apache-2.0",
                    {"LICENSE-MIT": "MIT serde", "LICENSE-APACHE": "Apache serde"})
    libc = package(tmp_path, "libc", "MIT")
    return {"packages": [gpclient, gpauth, gpapi, serde, libc], "tmp": tmp_path,
            "edges": {gpclient["id"]: [gpapi["id"], libc["id"]], gpauth["id"]: [gpapi["id"]],
                      gpapi["id"]: [serde["id"]]}}


def names(crates):
    return [p["name"] for p, _, _ in crates]


def test_the_crates_of_gpclient_and_gpauth_are_listed_without_workspace_crates(tree):
    crates = tpl.collect(metadata(tree["packages"], tree["edges"]))

    assert names(crates) == ["libc", "serde"]


def test_the_text_has_name_version_expression_and_every_license_file(tree):
    text = tpl.render(tpl.collect(metadata(tree["packages"], tree["edges"])))

    assert "serde 1.0.0: MIT OR Apache-2.0" in text
    assert "MIT serde" in text and "Apache serde" in text
    assert "libc 1.0.0: MIT" in text
    assert "gpapi" not in text


def test_the_same_text_is_written_once(tmp_path, tree):
    other = package(tmp_path, "other", "MIT", {"LICENSE": "MIT text of libc"}, version="2.0.0")
    tree["packages"].append(other)
    tree["edges"][tree["packages"][0]["id"]].append(other["id"])
    for p in tree["packages"]:
        if p["name"] == "libc":
            (tmp_path / "libc-1.0.0" / "LICENSE-MIT").write_text("MIT text of libc")

    text = tpl.render(tpl.collect(metadata(tree["packages"], tree["edges"])))

    assert text.count("MIT text of libc") == 1
    assert "Used by: libc 1.0.0, other 2.0.0" in text


@pytest.mark.parametrize("kind", ["dev", "build"])
def test_a_dev_or_build_dependency_is_not_listed(tree, kind):
    gpclient, libc = tree["packages"][0], tree["packages"][4]

    crates = tpl.collect(metadata(tree["packages"], tree["edges"], {(gpclient["id"], libc["id"]): kind}))

    assert names(crates) == ["serde"]


def test_a_crate_that_no_package_needs_is_not_listed(tree, tmp_path):
    tree["packages"].append(package(tmp_path, "windows-sys", "MIT"))  # e.g. cargo dropped it for Linux

    assert "windows-sys" not in names(tpl.collect(metadata(tree["packages"], tree["edges"])))


def test_a_crate_of_gpgui_only_is_not_listed(tree, tmp_path):
    gpgui = package(tmp_path, "gpgui-helper", "GPL-3.0", {}, source=None)
    tauri = package(tmp_path, "tauri", "MIT")
    tree["packages"] += [gpgui, tauri]
    tree["edges"][gpgui["id"]] = [tauri["id"]]

    assert "tauri" not in names(tpl.collect(metadata(tree["packages"], tree["edges"])))


def test_a_git_crate_is_listed(tree, tmp_path):
    tao = package(tmp_path, "tao", "Apache-2.0", {"LICENSE": "Apache tao"}, source="git+https://example.invalid/tao")
    tree["packages"].append(tao)
    tree["edges"][tree["packages"][0]["id"]].append(tao["id"])

    assert "tao" in names(tpl.collect(metadata(tree["packages"], tree["edges"])))


def test_the_roots_must_be_in_the_workspace(tree):
    packages = [p for p in tree["packages"] if p["name"] != "gpauth"]

    with pytest.raises(tpl.LicenseError, match="not all in the workspace"):
        tpl.collect(metadata(packages, tree["edges"]))


@pytest.mark.parametrize("license_", [None, ""])
def test_a_crate_without_a_license_expression_stops_the_script(tree, license_):
    tree["packages"][4]["license"] = license_

    with pytest.raises(tpl.LicenseError, match=r"libc 1.0.0: no license expression"):
        tpl.collect(metadata(tree["packages"], tree["edges"]))


def test_a_license_file_instead_of_an_expression_is_enough(tree):
    tree["packages"][4]["license"] = None
    tree["packages"][4]["license_file"] = "LICENSE-MIT"

    crates = tpl.collect(metadata(tree["packages"], tree["edges"]))

    assert [e for p, e, _ in crates if p["name"] == "libc"] == ["see LICENSE-MIT"]


def test_a_crate_without_a_license_file_stops_the_script_and_names_every_crate(tree, tmp_path):
    for name in ("libc", "serde"):
        directory = tmp_path / f"{name}-1.0.0"
        for f in directory.glob("LICENSE*"):
            f.unlink()

    with pytest.raises(tpl.LicenseError) as err:
        tpl.collect(metadata(tree["packages"], tree["edges"]))

    assert "libc 1.0.0" in str(err.value) and "serde 1.0.0" in str(err.value)
    assert "needs a notice" in str(err.value)


@pytest.mark.parametrize("name", ["COPYING", "licence.txt", "NOTICE", "UNLICENSE", "License-Apache"])
def test_every_name_of_a_license_file_is_found(tree, tmp_path, name):
    (tmp_path / "libc-1.0.0" / "LICENSE-MIT").unlink()
    (tmp_path / "libc-1.0.0" / name).write_text(f"text in {name}")

    assert f"text in {name}" in tpl.render(tpl.collect(metadata(tree["packages"], tree["edges"])))


def test_a_file_that_is_not_a_license_file_is_not_a_license_file(tree, tmp_path):
    (tmp_path / "libc-1.0.0" / "LICENSE-MIT").unlink()
    (tmp_path / "libc-1.0.0" / "README.md").write_text("readme")
    (tmp_path / "libc-1.0.0" / "src").mkdir()

    with pytest.raises(tpl.LicenseError, match="libc 1.0.0"):
        tpl.collect(metadata(tree["packages"], tree["edges"]))


def test_a_crate_whose_license_asks_no_notice_needs_no_file(tree, tmp_path):
    tree["packages"][4]["license"] = "Unlicense OR CC0-1.0"
    (tmp_path / "libc-1.0.0" / "LICENSE-MIT").unlink()

    assert "libc" in names(tpl.collect(metadata(tree["packages"], tree["edges"])))


@pytest.mark.parametrize("expression", ["Unlicense AND MIT", "MIT", "0BSD AND Apache-2.0", "MIT OR Apache-2.0"])
def test_a_crate_with_a_choice_that_asks_a_notice_needs_a_file(expression):
    assert tpl.needs_notice(expression)


@pytest.mark.parametrize("expression", ["Unlicense", "CC0-1.0 OR Unlicense", "Unlicense OR MIT OR 0BSD"])
def test_a_license_that_asks_no_notice_needs_no_file(expression):
    assert not tpl.needs_notice(expression)


def test_a_crate_of_the_allowlist_is_listed_without_a_text(tree, tmp_path, monkeypatch):
    (tmp_path / "libc-1.0.0" / "LICENSE-MIT").unlink()
    monkeypatch.setitem(tpl.NOTICE_ALLOWLIST, "libc 1.0.0", "test")

    text = tpl.render(tpl.collect(metadata(tree["packages"], tree["edges"])))

    assert "libc 1.0.0: MIT (the crate has no license file)" in text


def test_the_allowlist_is_for_one_version(tree, tmp_path, monkeypatch):
    (tmp_path / "libc-1.0.0" / "LICENSE-MIT").unlink()
    monkeypatch.setitem(tpl.NOTICE_ALLOWLIST, "libc 0.9.0", "test")

    with pytest.raises(tpl.LicenseError, match="libc 1.0.0"):
        tpl.collect(metadata(tree["packages"], tree["edges"]))


def test_cargo_is_asked_for_linux_only_offline_and_locked(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"packages": []}), stderr="")

    monkeypatch.setattr(tpl.subprocess, "run", fake_run)

    tpl.run_metadata("Cargo.toml", "vendor/config.toml", "x86_64-unknown-linux-gnu")

    cmd = calls[0]
    assert cmd[:2] == ["cargo", "metadata"]
    assert {"--offline", "--locked"} <= set(cmd)
    assert cmd[cmd.index("--filter-platform") + 1] == "x86_64-unknown-linux-gnu"
    assert cmd[cmd.index("--config") + 1] == "vendor/config.toml"


def test_main_writes_the_file_and_fails_with_a_message(monkeypatch, tree, tmp_path, capsys):
    monkeypatch.setattr(tpl, "run_metadata", lambda *a: metadata(tree["packages"], tree["edges"]))
    monkeypatch.setattr(tpl, "host_triple", lambda: "x86_64-unknown-linux-gnu")
    out = tmp_path / "out.txt"

    assert tpl.main(["-o", str(out)]) == 0
    assert "2 crates" in out.read_text()

    tree["packages"][4]["license"] = None
    out.unlink()
    assert tpl.main(["-o", str(out)]) == 1
    assert "no license expression" in capsys.readouterr().err
    assert not out.exists()
