#!/usr/bin/env python3
"""
Write the third-party license file of the Rust crates linked into gpclient and
gpauth (issue #148): per crate the name, the version, the SPDX license
expression and the full text of its license and notice files.

Usage: scripts/third_party_licenses.py [-o FILE] [--manifest-path Cargo.toml]
       [--vendor-config vendor/config.toml] [--target TRIPLE]

The crates are the normal (not build, not dev) dependencies of the packages
gpclient and gpauth in `cargo metadata --offline --locked --filter-platform
<host triple>`, so crates for other platforms are not listed. The upstream
workspace crates (no registry or git source) are not listed: debian/copyright
names them. cargo resolves the features of the whole workspace, so the list
can have a crate that only a feature of another binary needs: too many is safe.

The rule (the script stops with exit status 1 and says which crate):
  - A crate must have a license expression in its Cargo.toml ("license"), or
    "license-file" with that file in the crate directory.
  - A crate must have at least one license file (LICENSE*, LICENCE*, COPYING*,
    NOTICE*, UNLICENSE*, top level of the crate directory), because every
    license but the ones in NO_NOTICE_LICENSES asks that the notice is shipped
    with the binary. A crate whose license expression has an OR choice of only
    such licenses (for example "Unlicense OR MIT": Unlicense) does not need a file.
  - NOTICE_ALLOWLIST names crates ("name version", so a new version is checked
    again) that are known to have no file in the crates.io package. Such a
    crate is listed with its license expression and a note, never with an
    invented text. Each entry is to be checked by a person; remove it when the
    crate gets a file.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOTS = ("gpclient", "gpauth")
LICENSE_FILE = re.compile(r"^(licen[sc]e|copying|notice|unlicense)", re.I)
# Licenses that do not ask to ship a notice with the binary
NO_NOTICE_LICENSES = {"Unlicense", "CC0-1.0", "0BSD"}
# "name version" -> reason why it may have no license file
_NO_FILE = "the crates.io package has no license file"
NOTICE_ALLOWLIST = {
    "Inflector 0.11.4": _NO_FILE,
    "alloc-stdlib 0.3.0": _NO_FILE,
    "compile-time 0.4.0": _NO_FILE,
    "compile-time-macros 0.4.0": _NO_FILE,
    "defmt-parser 1.0.0": _NO_FILE,
    "dlopen2 0.8.2": _NO_FILE,
    "dlopen2_derive 0.4.3": _NO_FILE,
    "inquire 0.9.4": _NO_FILE,
    "libappindicator-sys 0.9.0": _NO_FILE,
    "log-reload 0.1.3": _NO_FILE,
    "selectors 0.38.0": _NO_FILE,
    "specta 2.0.0-rc.20": _NO_FILE,
    "specta-macros 2.0.0-rc.17": _NO_FILE,
}


class LicenseError(Exception):
    pass


def needs_notice(expression):
    """False when one OR choice has only licenses of NO_NOTICE_LICENSES"""
    for choice in re.split(r"\s+OR\s+|/", expression.strip("() ")):
        ids = re.split(r"\s+(?:AND|WITH)\s+", choice.strip("() "))
        if all(i.strip("() ") in NO_NOTICE_LICENSES for i in ids):
            return False
    return True


def run_metadata(manifest, vendor_config, target):
    cmd = ["cargo", "metadata", "--format-version", "1", "--offline", "--locked",
           "--filter-platform", target, "--manifest-path", str(manifest)]
    if vendor_config:
        cmd += ["--config", str(vendor_config)]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def host_triple():
    out = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True).stdout
    return re.search(r"^host: (\S+)", out, re.M).group(1)


def linked_crates(metadata):
    """Packages (dicts) of the third-party crates reachable from gpclient and gpauth"""
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    todo = [i for i in metadata["workspace_members"] if packages[i]["name"] in ROOTS]
    if len(todo) != len(ROOTS):
        raise LicenseError(f"the packages {ROOTS} are not all in the workspace")
    seen = set(todo)
    while todo:
        for dep in nodes[todo.pop()]["deps"]:
            if dep["pkg"] not in seen and any(k["kind"] is None for k in dep["dep_kinds"]):
                seen.add(dep["pkg"])
                todo.append(dep["pkg"])
    third_party = [packages[i] for i in seen if packages[i].get("source")]
    return sorted(third_party, key=lambda p: (p["name"], p["version"]))


def license_texts(package):
    """[(file name, text)] of the license files in the crate directory"""
    directory = Path(package["manifest_path"]).parent
    files = [f for f in directory.iterdir() if f.is_file() and LICENSE_FILE.match(f.name)]
    extra = package.get("license_file")
    if extra and (directory / extra).is_file() and (directory / extra) not in files:
        files.append(directory / extra)
    return [(f.name, f.read_text(encoding="utf-8", errors="replace").strip()) for f in sorted(files)]


def collect(metadata):
    """[(package, expression, [(file name, text)])]; LicenseError lists every crate that breaks the rule"""
    result, errors = [], []
    for package in linked_crates(metadata):
        name = f"{package['name']} {package['version']}"
        expression = package.get("license")
        if not expression and not package.get("license_file"):
            errors.append(f"{name}: no license expression and no license-file in Cargo.toml")
            continue
        texts = license_texts(package)
        if not texts and name not in NOTICE_ALLOWLIST and (
                not expression or needs_notice(expression)):
            errors.append(f"{name}: license {expression!r} needs a notice, but the crate "
                          "has no LICENSE/COPYING/NOTICE file (see NOTICE_ALLOWLIST)")
        result.append((package, expression or f"see {package['license_file']}", texts))
    if errors:
        raise LicenseError("\n".join(errors))
    return result


def render(crates):
    """The text of the file: a crate list, then every distinct license text once"""
    blocks = {}
    lines = []
    for package, expression, texts in crates:
        lines.append(f"{package['name']} {package['version']}: {expression}"
                     + ("" if texts else " (the crate has no license file)"))
        for _, text in texts:
            key = hashlib.sha256(text.encode()).hexdigest()
            blocks.setdefault(key, (text, []))[1].append(f"{package['name']} {package['version']}")
    out = ["Third-party Rust crates linked into gpclient and gpauth",
           "(generated by scripts/third_party_licenses.py; the upstream workspace crates",
           "are not listed, see /usr/share/doc/network-manager-gpclient/copyright)",
           "", f"{len(crates)} crates", "", *lines, ""]
    for text, users in blocks.values():
        out += ["=" * 78, "Used by: " + ", ".join(users), "=" * 78, text, ""]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", default="-")
    parser.add_argument("--manifest-path", default=str(ROOT / "external/GlobalProtect-openconnect/Cargo.toml"))
    parser.add_argument("--vendor-config", default=str(ROOT / "vendor/config.toml"))
    parser.add_argument("--target", help="Rust target triple (default: host of rustc)")
    args = parser.parse_args(argv)
    config = args.vendor_config if Path(args.vendor_config).is_file() else None
    try:
        text = render(collect(run_metadata(args.manifest_path, config, args.target or host_triple())))
    except (LicenseError, subprocess.CalledProcessError) as err:
        print(f"error: {getattr(err, 'stderr', None) or err}", file=sys.stderr)
        return 1
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
