#!/usr/bin/env python3
"""
Check the contract between this repository and the pinned upstream
(external/GlobalProtect-openconnect).

nm-gpclient-service.py reads the text that gpclient prints. This tool keeps a
list of the upstream code and tests that produce or guard that text
(upstream/contract.json), so a change upstream is noticed. See docs/UPSTREAM.md.

Subcommands:
  check     every item of the contract is still true for the source tree
  update    record the current source in the contract (after a review)
  features  every changelog entry since the baseline is classified
  interfaces  every function that reads upstream output or runs upstream has tests
  gate      CI: a change that the changelog calls breaking needs a human verification
"""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "external" / "GlobalProtect-openconnect"
DEFAULT_CONTRACT = ROOT / "upstream" / "contract.json"
DEFAULT_FEATURES = ROOT / "upstream" / "features.json"
DEFAULT_VERIFICATION = ROOT / "upstream" / "verification.json"
DEFAULT_INTERFACES = ROOT / "upstream" / "interfaces.json"
SERVICE = "service/nm-gpclient-service.py"
DOCS = "docs/UPSTREAM.md"
LABEL = "upstream-verified"

# The pin of main before the rule "pin to a release tag" (v2.6.5+14, not a tag).
# Only this commit may be accepted without a tag, with "legacy_untagged": true
# in the contract. The next bump names a tag and drops the flag.
LEGACY_UNTAGGED_COMMIT = "9e4f45997ebee452c58d7d3066dcfc175d65ef82"

OK = "OK"
MOVED = "MOVED"
CHANGED = "CHANGED"
MISSING = "MISSING"

SEARCH_SUFFIXES = (".rs", ".toml", ".desktop")
SEARCH_NAMES = ("Cargo.lock",)
SKIP_DIRS = {"target", "deps", ".git", "node_modules"}

FEATURE_STATUSES = ("tested", "no-action", "not-applicable", "follow-up")
REQUIRED_SCENARIOS = ("install", "saml-login", "disconnect", "reconnect")
OPTIONAL_SCENARIOS = ("gateway-selection", "credentials-prompt", "otp-prompt", "fix-openssl", "hip")


class ContractError(Exception):
    """The input is not usable (bad file, bad item): exit code 2"""


# ---------------------------------------------------------------- files


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ContractError(f"{path} does not exist")
    except ValueError as exc:
        raise ContractError(f"{path} is not valid JSON: {exc}")


def dump_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def source_files(source):
    """Files of the source tree that can hold an anchor (no target/ and deps/)"""
    for directory, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(SEARCH_SUFFIXES) or name in SEARCH_NAMES:
                yield Path(directory) / name


def source_commit(source):
    """The commit checked out in `source`, or None"""
    try:
        out = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def source_version(source):
    """Version from [workspace.package] (or [package]) of the root Cargo.toml"""
    text = read_text(Path(source) / "Cargo.toml")
    if text is None:
        return None
    found = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        header = re.match(r"^\[([^\]]+)\]\s*(#.*)?$", line)
        if header:
            section = header.group(1).strip()
            continue
        match = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if match and section in ("workspace.package", "package"):
            found.setdefault(section, match.group(1))
    return found.get("workspace.package") or found.get("package")


def parse_cargo_lock(text):
    """{package name: [versions]} from the text of a Cargo.lock"""
    packages = {}
    name = None
    for line in text.splitlines():
        if line.startswith("[[package]]"):
            name = None
        elif line.startswith("name = "):
            name = line.split('"')[1]
        elif line.startswith("version = ") and name is not None:
            packages.setdefault(name, []).append(line.split('"')[1])
            name = None
    return packages


def version_tuple(version):
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        raise ContractError(f"not a version: {version!r}")


# ---------------------------------------------------------------- Rust blocks


def _skip_raw_string(text, i):
    """If a raw string starts at i (r"..", r#".."#, br".."), the index after it, else None"""
    j = i
    if text[j] == "b":
        j += 1
    if j >= len(text) or text[j] != "r":
        return None
    j += 1
    hashes = 0
    while j < len(text) and text[j] == "#":
        hashes += 1
        j += 1
    if j >= len(text) or text[j] != '"':
        return None
    end = text.find('"' + "#" * hashes, j + 1)
    return len(text) if end < 0 else end + 1 + hashes


def block_end(text, pos):
    """
    Index after the closing brace of the first `{` at or after `pos`, or None.
    Braces in strings, raw strings, char literals and comments do not count;
    a lifetime ('a) is not a char literal.
    """
    depth = 0
    i = pos
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            nesting = 1
            i += 2
            while i < n and nesting:
                if text.startswith("/*", i):
                    nesting += 1
                    i += 2
                elif text.startswith("*/", i):
                    nesting -= 1
                    i += 2
                else:
                    i += 1
        elif c in "rb" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")) \
                and _skip_raw_string(text, i) is not None:
            i = _skip_raw_string(text, i)
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif c == "'":
            if text.startswith("\\", i + 1):
                end = text.find("'", i + 3)
                i = n if end < 0 else end + 1
            elif i + 2 < n and text[i + 2] == "'":
                i += 3
            else:
                i += 1  # a lifetime
        elif c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return None


def normalize(block):
    return " ".join(block.split())


def block_hash(block):
    return hashlib.sha256(normalize(block).encode("utf-8")).hexdigest()


def extract_block(text, start):
    """(block text, None) or (None, why not)"""
    count = text.count(start)
    if count == 0:
        return None, f"start {start!r} not found"
    if count > 1:
        return None, f"start {start!r} is ambiguous ({count} matches)"
    at = text.index(start)
    line_start = text.rfind("\n", 0, at) + 1
    end = block_end(text, at)
    if end is None:
        return None, f"no closed block after {start!r}"
    return text[line_start:end], None


# ---------------------------------------------------------------- items


class Result:
    def __init__(self, item, status, detail=""):
        self.item = item
        self.status = status
        self.detail = detail

    def line(self):
        text = f"{self.status} {self.item.get('id', '?')} ({self.item.get('path', self.item.get('package', '-'))})"
        text += f" used by: {self.item.get('used_by', '-')}"
        if self.detail:
            text += f" [{self.detail}]"
        return text


def _need(item, *keys):
    for key in keys:
        if not isinstance(item.get(key), str) or not item[key]:
            raise ContractError(f"item {item.get('id', '?')!r}: missing {key!r}")


def eval_anchor(item, source):
    _need(item, "id", "path", "text")
    text = read_text(Path(source) / item["path"])
    if text is not None and item["text"] in text:
        return Result(item, OK)
    for path in source_files(source):
        if path == Path(source) / item["path"]:
            continue
        other = read_text(path)
        if other is not None and item["text"] in other:
            return Result(item, MOVED, f"now in {path.relative_to(source)}")
    return Result(item, MISSING, "text not found" if text is not None else "file not found")


def eval_block(item, source):
    _need(item, "id", "path", "start")
    if item.get("role") not in ("code", "test"):
        raise ContractError(f"item {item['id']!r}: role must be 'code' or 'test'")
    text = read_text(Path(source) / item["path"])
    if text is None:
        return Result(item, MISSING, "file not found")
    block, why = extract_block(text, item["start"])
    if block is None:
        return Result(item, MISSING, why)
    if block_hash(block) != item.get("sha256"):
        return Result(item, CHANGED, "block differs from the accepted one")
    return Result(item, OK)


def eval_cargo_lock(item, source):
    _need(item, "id", "package", "version")
    text = read_text(Path(source) / "Cargo.lock")
    if text is None:
        return Result(item, MISSING, "Cargo.lock not found")
    versions = parse_cargo_lock(text).get(item["package"])
    if not versions:
        return Result(item, MISSING, f"package {item['package']} not in Cargo.lock")
    if item["version"] in versions:
        return Result(item, OK)
    return Result(item, CHANGED, f"version is {', '.join(versions)}, accepted {item['version']}")


EVALUATORS = {"anchor": eval_anchor, "block": eval_block, "cargo-lock": eval_cargo_lock}


def evaluate(items, source):
    results = []
    for item in items:
        evaluator = EVALUATORS.get(item.get("kind"))
        if evaluator is None:
            raise ContractError(f"item {item.get('id', '?')!r}: unknown kind {item.get('kind')!r}")
        results.append(evaluator(item, source))
    return results


def check_contract(contract, source):
    """All results, with the version check first"""
    results = evaluate(contract.get("items", []), source)
    version = source_version(source)
    item = {"id": "version", "path": "Cargo.toml", "used_by": "upstream/contract.json: version"}
    if version is None:
        results.insert(0, Result(item, MISSING, "no version in Cargo.toml"))
    elif version != contract.get("version"):
        results.insert(0, Result(item, CHANGED, f"source is {version}, contract says {contract.get('version')}"))
    else:
        results.insert(0, Result(item, OK))
    return results


# ---------------------------------------------------------------- features


def parse_changelog(text):
    """[(version, section, bullet text)]; a bullet may wrap onto indented lines"""
    bullets = []
    version = section = None
    current = None
    for line in text.splitlines():
        heading = re.match(r"^## (\d+\.\d+\.\d+)\s*$", line)
        if heading:
            version, section, current = heading.group(1), "", None
            continue
        if line.startswith("## "):
            version, section, current = None, "", None
            continue
        if line.startswith("### "):
            section, current = line[4:].strip(), None
            continue
        if version is None:
            continue
        if line.startswith("- "):
            current = [version, section, line[2:].strip()]
            bullets.append(current)
        elif current is not None and line.strip() and line[0] in " \t":
            current[2] += " " + line.strip()
        else:
            current = None
    return [tuple(b) for b in bullets]


def test_reference_problem(ref, root):
    """Why a test reference 'path::name' does not exist, or None"""
    parts = ref.split("::")
    if len(parts) < 2 or not all(parts):
        return f"{ref}: not in the form path::test_name"
    path = Path(root) / parts[0]
    text = read_text(path)
    if text is None:
        return f"{ref}: {parts[0]} does not exist"
    for cls in parts[1:-1]:
        if not re.search(rf"^\s*class\s+{re.escape(cls)}\b", text, re.M):
            return f"{ref}: no class {cls} in {parts[0]}"
    if not re.search(rf"\bdef\s+{re.escape(parts[-1])}\(", text):
        return f"{ref}: no function {parts[-1]} in {parts[0]}"
    return None


def check_features(registry, changelog_text, contract_version, root):
    """List of problems (empty when every changelog entry is classified)"""
    problems = []
    baseline = registry.get("baseline")
    if not isinstance(baseline, str):
        return ["registry has no baseline"]
    low, high = version_tuple(baseline), version_tuple(contract_version)
    wanted = [b for b in parse_changelog(changelog_text) if low < version_tuple(b[0]) <= high]
    entries = registry.get("entries", [])
    keys = [(e.get("version"), e.get("section"), e.get("text")) for e in entries]
    for key in wanted:
        count = keys.count(key)
        label = f"{key[0]} {key[1]}: {key[2]}"
        if count == 0:
            problems.append(f"not classified: {label}")
        elif count > 1:
            problems.append(f"classified {count} times: {label}")
    for entry, key in zip(entries, keys):
        label = f"{key[0]} {key[1]}: {key[2]}"
        if key not in wanted:
            problems.append(f"stale entry (not in the changelog for {baseline} < v <= {contract_version}): {label}")
            continue
        status = entry.get("status")
        if status not in FEATURE_STATUSES:
            problems.append(f"bad status {status!r}: {label}")
            continue
        reason = entry.get("reason")
        has_reason = isinstance(reason, str) and reason.strip()
        if status == "tested":
            tests = entry.get("tests")
            if not isinstance(tests, list) or not tests:
                problems.append(f"tested without a test: {label}")
            else:
                for ref in tests:
                    problem = test_reference_problem(ref, root)
                    if problem:
                        problems.append(f"missing test, {problem} (for {label})")
        elif status in ("no-action", "not-applicable") and not has_reason:
            problems.append(f"{status} without a reason: {label}")
        elif status == "follow-up":
            if not has_reason:
                problems.append(f"follow-up without a reason: {label}")
            if not isinstance(entry.get("issue"), int) or isinstance(entry.get("issue"), bool):
                problems.append(f"follow-up without an issue number: {label}")
    return problems


# ---------------------------------------------------------------- verification


def validate_record(record):
    """Problems of one verification record (empty list when valid)"""
    if not isinstance(record, dict):
        return ["record is not an object"]
    problems = []
    commit = record.get("commit")
    if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)):
        problems.append("commit must be 40 hex digits")
    for key in ("tag", "tester"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            problems.append(f"{key} is missing")
    if not isinstance(record.get("pr"), int) or isinstance(record.get("pr"), bool):
        problems.append("pr must be a number")
    date = record.get("date")
    if not (isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)):
        problems.append("date must be YYYY-MM-DD")
    systems = record.get("systems")
    if not isinstance(systems, list) or not systems:
        problems.append("at least one system is required")
    else:
        for system in systems:
            if not isinstance(system, dict) or not all(
                isinstance(system.get(k), str) and system[k].strip() for k in ("ubuntu", "arch", "desktop")
            ):
                problems.append("every system needs ubuntu, arch and desktop")
                break
    scenarios = record.get("scenarios")
    if not isinstance(scenarios, dict):
        problems.append("scenarios are missing")
    else:
        for name in REQUIRED_SCENARIOS:
            if scenarios.get(name) != "pass":
                problems.append(f"scenario {name} must be pass (is {scenarios.get(name)!r})")
        for name in OPTIONAL_SCENARIOS:
            if scenarios.get(name) not in ("pass", "n/a"):
                problems.append(f"scenario {name} must be pass or n/a (is {scenarios.get(name)!r})")
        for name in scenarios:
            if name not in REQUIRED_SCENARIOS + OPTIONAL_SCENARIOS:
                problems.append(f"unknown scenario {name}")
    if record.get("result") != "pass":
        problems.append(f"result must be pass (is {record.get('result')!r})")
    return problems


def record_problems(records, commit):
    """Why no record accepts `commit` (empty list when one does)"""
    if not commit:
        return ["the commit of the source is unknown (is the submodule checked out?)"]
    mine = [r for r in records if isinstance(r, dict) and r.get("commit") == commit]
    if not mine:
        return [f"no record in upstream/verification.json for commit {commit}"]
    attempts = [validate_record(r) for r in mine]
    if any(not p for p in attempts):
        return []
    return [f"record for commit {commit} is not valid: {problem}" for problem in attempts[0]]


# A change is breaking when the upstream changelog says so, not when the contract
# drifts (drift fails `check` and needs a code review and `update`, no manual
# verification). The names and words come from the maintainer's decision and
# from yuezk's changelog style: there is no "Breaking" section so far, but
# 3.1.0 has "Command-line notes" (a rename and a removal). A section name
# matches when it contains one of BREAKING_SECTIONS (case-insensitive); a word
# matches as a whole word only ("removal" and "mustard" do not match).
BREAKING_SECTIONS = ("breaking", "command-line notes", "removed", "deprecated")
BREAKING_WORDS = ("breaking", "incompatible", "removed", "renamed", "deprecated", "must")
BREAKING_WORDS_RE = re.compile(r"\b(?:" + "|".join(BREAKING_WORDS) + r")\b", re.I)


def changelog_breaking_reasons(changelog_text, base_version, version):
    """['<version> / <section>: <bullet>'] for the entries in base < v <= version that break"""
    if changelog_text is None:
        return ["changelog not found: cannot tell what changed"]
    low, high = version_tuple(base_version), version_tuple(version)
    bullets = [b for b in parse_changelog(changelog_text) if low < version_tuple(b[0]) <= high]
    if not bullets:
        return [f"changelog not found: no entries for {base_version} < v <= {version}"]
    reasons = []
    for bullet_version, section, text in bullets:
        by_section = any(name in section.lower() for name in BREAKING_SECTIONS)
        if by_section or BREAKING_WORDS_RE.search(text):
            reasons.append(f"{bullet_version} / {section}: {text}")
    return reasons


def gate(source, base_contract, verification, labels, commit=None, changelog_path="changelog.md",
         base_commit=None):
    """
    Decide if the source is a breaking change relative to the accepted base
    contract (None: there is none). Returns a dict with breaking, verified,
    reasons (why breaking), problems (what is missing to accept it) and
    unchanged (the upstream commit is the one of the base branch).

    `base_commit` is the commit that the base branch pins. When it is the commit
    of the source, upstream did not change: the change is not breaking, also
    when the base branch has no contract.
    """
    if commit is None:
        commit = source_commit(source)
    if base_commit and base_commit == commit:
        return {"breaking": False, "verified": False, "reasons": [], "problems": [], "unchanged": True}
    reasons = []
    if base_contract is None:
        reasons.append("no accepted baseline: the base branch has no upstream/contract.json")
    else:
        base_version = base_contract["version"]
        version = source_version(source)
        if version is None:
            reasons.append("version of the source is unknown")
        elif version_tuple(version) < version_tuple(base_version):
            # No changelog scan: the entries of a downgrade are not "new"
            reasons.append(f"version went down: {base_version} -> {version}")
        elif version_tuple(version) > version_tuple(base_version):
            if version_tuple(version)[0] != version_tuple(base_version)[0]:
                reasons.append(f"major version changed: {base_version} -> {version}")
            reasons += changelog_breaking_reasons(read_text(Path(source) / changelog_path), base_version, version)
    outcome = {"breaking": bool(reasons), "verified": False, "reasons": reasons, "problems": [],
               "unchanged": False}
    if not reasons:
        return outcome
    problems = record_problems(verification.get("records", []), commit)
    if LABEL not in labels:
        problems.append(f"the pull request has no label {LABEL}")
    outcome["problems"] = problems
    outcome["verified"] = not problems
    return outcome


# ---------------------------------------------------------------- interfaces
#
# Every function of the service that reads the output of the upstream tools or
# runs them must be in upstream/interfaces.json with at least two tests. A
# function of SERVICE is upstream-facing when its body (nested functions
# included):
#   (a) uses a module-level name that a contract item lists in `used_by` as
#       "service/nm-gpclient-service.py: NAME" (a regex or a class that models
#       upstream output), or
#   (b) has a command element in a list, a tuple or the arguments of a call: a string that is exactly
#       gpclient, gpauth or gpservice (or a path ending in one of them), or a
#       module-level constant holding such a string, or
#   (c) reads or writes the gpclient PTY or process: an attribute in
#       OUTPUT_ATTRS, a call of a method in OUTPUT_CALLS, or an attribute
#       access in OUTPUT_CHAINS (object attribute, then attribute).
# Constructors (`__init__`) are not scanned: they only create attributes.
# Scripts that gpclient or gpauth run (scripts/browser-wrapper.sh) are not
# scanned: they are listed in the registry by hand.

UPSTREAM_COMMAND_RE = re.compile(r"^(?:/[\w./-]*/)?(?:gpclient|gpauth|gpservice|browser-wrapper)$")
OUTPUT_ATTRS = ("_pty_master",)
OUTPUT_CALLS = ("_consume_output", "_decode_output")
OUTPUT_CHAINS = (("gpclient_process", "stdout"),)
INTERFACE_KINDS = ("reads-output", "runs-upstream")


def contract_service_names(contract):
    """Names that the `used_by` of contract items give as service code"""
    names = set()
    prefix = SERVICE + ":"
    for item in contract.get("items", []):
        used = item.get("used_by", "")
        if used.startswith(prefix):
            names.update(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", used[len(prefix):]))
    return names


def _module_names(tree):
    names, commands = set(), set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) \
                            and UPSTREAM_COMMAND_RE.match(value.value):
                        commands.add(target.id)
    return names, commands


def service_functions(tree):
    """{function id without the path: node}: `func` or `Class.method`"""
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{node.name}.{sub.name}"] = sub
    return found


def scan_service(source_text, contract):
    """{function id without the path: [why it is upstream-facing]}"""
    tree = ast.parse(source_text)
    module_names, command_names = _module_names(tree)
    used_names = contract_service_names(contract) & module_names
    detected = {}
    for fid, func in service_functions(tree).items():
        if fid.endswith(".__init__"):
            continue
        why = []
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id in used_names:
                why.append(f"uses {node.id}")
            elif isinstance(node, ast.Name) and node.id in command_names:
                why.append(f"command {node.id}")
            elif isinstance(node, (ast.List, ast.Tuple, ast.Call)):
                for element in (node.args if isinstance(node, ast.Call) else node.elts):
                    if isinstance(element, ast.Constant) and isinstance(element.value, str) \
                            and UPSTREAM_COMMAND_RE.match(element.value):
                        why.append(f"command {element.value}")
            elif isinstance(node, ast.Attribute):
                if node.attr in OUTPUT_ATTRS:
                    why.append(f"accesses {node.attr}")
                elif node.attr in OUTPUT_CALLS:
                    why.append(f"calls {node.attr}")
                elif isinstance(node.value, ast.Attribute) and (node.value.attr, node.attr) in OUTPUT_CHAINS:
                    why.append(f"reads {node.value.attr}.{node.attr}")
        if why:
            detected[fid] = sorted(set(why))
    return detected


def _find_function(tree, parts):
    if len(parts) == 1:  # no class given: a test in a class is found too
        return next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == parts[0]), None)
    nodes = tree.body
    node = None
    for part in parts:
        node = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None
        nodes = node.body
    return node


def test_body(ref, root):
    """Source text of the test `path::[Class::]name`, or None"""
    parts = ref.split("::")
    text = read_text(Path(root) / parts[0])
    if text is None:
        return None
    try:
        node = _find_function(ast.parse(text), parts[1:])
    except SyntaxError:
        return None
    return None if node is None else ast.get_source_segment(text, node)


def check_interfaces(registry, contract, root):
    """List of problems (empty when every upstream-facing function is registered and tested)"""
    problems = []
    service_text = read_text(Path(root) / SERVICE)
    if service_text is None:
        return [f"{SERVICE} does not exist"]
    tree = ast.parse(service_text)
    functions = service_functions(tree)
    detected = scan_service(service_text, contract)
    entries = registry.get("functions", [])
    ids = {item.get("id") for item in contract.get("items", [])}
    registered = {}
    for entry in entries:
        name = entry.get("function")
        if not isinstance(name, str) or not name:
            problems.append(f"bad function name {name!r}: use path::func, path::Class.method or the path of a script")
            continue
        if name in registered:
            problems.append(f"{name}: listed twice")
        registered[name] = entry
        path, _, short = name.partition("::")
        if short and path != SERVICE:
            problems.append(f"{name}: only {SERVICE} is scanned; list a script by its path")
            continue
        if path == SERVICE:
            if short not in functions:
                problems.append(f"{name}: no such function (stale entry)")
                continue
            needle = short.split(".")[-1]
        else:
            if not (Path(root) / path).is_file():
                problems.append(f"{name}: no such file (stale entry)")
                continue
            needle = Path(path).name.rsplit(".", 1)[0]
        if entry.get("kind") not in INTERFACE_KINDS:
            problems.append(f"{name}: kind must be one of {', '.join(INTERFACE_KINDS)}")
        upstream = entry.get("upstream")
        if not isinstance(upstream, list):
            problems.append(f"{name}: upstream must be a list of contract item ids")
        else:
            for item_id in upstream:
                if item_id not in ids:
                    problems.append(f"{name}: unknown upstream id {item_id!r}")
        tests = entry.get("tests")
        if not isinstance(tests, list) or len(set(tests)) < 2:
            problems.append(f"{name}: needs at least 2 tests (a positive and a negative one)")
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(needle).replace(r"\-", "[-_]") + r"(?![A-Za-z0-9_])")
        named = False
        for ref in tests:
            problem = test_reference_problem(ref, root)
            if problem:
                problems.append(f"{name}: missing test, {problem}")
                continue
            body = test_body(ref, root)
            if path != SERVICE:
                # a script is usually named by a constant at the top of the test file
                body = read_text(Path(root) / ref.split("::")[0])
            named = named or bool(body and pattern.search(body))
        if not named and not any(p.startswith(f"{name}: missing test") for p in problems):
            problems.append(f"{name}: no listed test mentions {needle!r}")
    for fid, why in sorted(detected.items()):
        if f"{SERVICE}::{fid}" not in registered:
            problems.append(f"{SERVICE}::{fid}: upstream-facing ({'; '.join(why[:3])}) but not in the registry")
    return problems


# ---------------------------------------------------------------- pin and toolchain

README_PIN_RE = re.compile(
    r"^Upstream version: \[`(v[^`\s]+)`\]"
    r"\(https://github\.com/yuezk/GlobalProtect-openconnect/releases/tag/(v[^)\s]+)\)\s*$",
    re.M,
)


RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


README_LEGACY_RE = re.compile(
    r"^Upstream version: untagged commit \[`([0-9a-f]{7})`\]"
    r"\(https://github\.com/yuezk/GlobalProtect-openconnect/commit/([0-9a-f]{40})\)"
    r" \(v[0-9][^)\s]*\)\s*$",
    re.M,
)


def is_legacy(contract):
    return contract.get("legacy_untagged") is True


def pin_problem(contract):
    """Why the pin of the contract is not allowed, or None.

    A release tag, or the one legacy exception: "tag": null with
    "legacy_untagged": true and the commit LEGACY_UNTAGGED_COMMIT. A tag
    together with the flag is not allowed: the flag says "no tag".
    """
    tag = contract.get("tag")
    if "legacy_untagged" in contract and not isinstance(contract["legacy_untagged"], bool):
        return "legacy_untagged must be true or absent"
    if not is_legacy(contract):
        return tag_problem(tag)
    if tag is not None:
        return f"legacy_untagged is set and the tag is {tag!r}: a tag and the legacy flag exclude each other"
    if contract.get("commit") != LEGACY_UNTAGGED_COMMIT:
        return (f"legacy_untagged is only for the commit {LEGACY_UNTAGGED_COMMIT}, "
                f"the contract pins {contract.get('commit')}: pin a release tag")
    return None


def tag_problem(tag):
    """Why `tag` is not a release tag (never a branch such as main), or None"""
    if not isinstance(tag, str) or not RELEASE_TAG_RE.match(tag):
        return f"{tag!r} is not a release tag like v3.1.0"
    return None


def readme_problem(text, tag):
    """Why the README does not name `tag` as the upstream version, or None"""
    if README_LEGACY_RE.search(text):
        return "README names an untagged commit, the contract pins a tag"
    pins = README_PIN_RE.findall(text)
    if len(pins) != 1:
        return f"README must have exactly one 'Upstream version: [`{tag}`](...)' line, found {len(pins)}"
    if pins[0] != (tag, tag):
        return f"README names {pins[0][0]} (link {pins[0][1]}), the contract pins {tag}"
    return None


def readme_legacy_problem(text, commit):
    """Why the README does not name the untagged `commit` as the upstream version, or None"""
    if README_PIN_RE.search(text):
        return "README names a release tag, the contract pins an untagged commit"
    pins = README_LEGACY_RE.findall(text)
    if len(pins) != 1:
        return f"README must have exactly one 'Upstream version: untagged commit [`...`](...)' line, found {len(pins)}"
    short, full = pins[0]
    if full != commit or short != commit[:7]:
        return f"README names {full} (short {short}), the contract pins {commit}"
    return None


def readme_pin_problem(text, contract):
    """Why the README does not match the pin of the contract (tag or legacy), or None"""
    if is_legacy(contract):
        return readme_legacy_problem(text, contract.get("commit") or "")
    return readme_problem(text, contract.get("tag"))


def toolchain_channel(text):
    """`channel` of a rust-toolchain.toml, or None"""
    match = re.search(r'^\s*channel\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else None


def _rust_tuple(version):
    parts = version.split(".")
    if not 2 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ContractError(f"not a Rust version: {version!r}")
    return tuple(int(part) for part in parts) + (0,) * (3 - len(parts))


def toolchain_problems(channel, name, versions):
    """Problems when the Rust `versions` used by the build `name` are older than `channel`"""
    if channel is None:
        return ["upstream rust-toolchain.toml has no channel"]
    try:
        need = _rust_tuple(channel)
    except ContractError:
        return [f"upstream rust-toolchain.toml channel {channel!r} is not a version number"]
    if not versions:
        return [f"{name}: no Rust version found"]
    problems = []
    for version in versions:
        try:
            old = _rust_tuple(version) < need
        except ContractError:
            problems.append(f"{name}: {version!r} is not a Rust version")
            continue
        if old:
            problems.append(f"{name}: Rust {version} is older than {channel} of the upstream rust-toolchain.toml")
    return problems


def dockerfile_rust_versions(text):
    """Versions given to `--default-toolchain` (as written, e.g. 1.91)"""
    return re.findall(r"--default-toolchain\s+([0-9][0-9.]*)", text)


def makefile_rust_version(text):
    match = re.search(r"^RUST_VERSION\s*[:?]?=\s*([0-9][0-9.]*)\s*$", text, re.M)
    return [match.group(1)] if match else []


# ---------------------------------------------------------------- commands


def cmd_check(args):
    contract = load_json(args.contract)
    results = check_contract(contract, args.source)
    for result in results:
        print(result.line())
    bad = [r for r in results if r.status != OK]
    print(f"{len(results) - len(bad)} of {len(results)} items OK")
    if bad:
        print(f"Upstream changed. Review it and adapt the code: see {DOCS}", file=sys.stderr)
    return 1 if bad else 0


def cmd_update(args):
    contract = load_json(args.contract)
    source = args.source
    changes = []

    def set_key(target, key, value, label):
        if target.get(key) != value:
            changes.append(f"{label}: {target.get(key)} -> {value}")
            target[key] = value

    for item in contract.get("items", []):
        if item.get("kind") == "block":
            text = read_text(Path(source) / item.get("path", ""))
            block, _ = extract_block(text, item.get("start", "")) if text is not None else (None, None)
            if block is not None:
                set_key(item, "sha256", block_hash(block), f"{item['id']} sha256")
        elif item.get("kind") == "cargo-lock":
            text = read_text(Path(source) / "Cargo.lock")
            versions = parse_cargo_lock(text).get(item.get("package"), []) if text else []
            if versions and item.get("version") not in versions:
                set_key(item, "version", versions[0], f"{item['id']} version")
    commit = source_commit(source)
    if commit is None:
        raise ContractError(f"cannot read the commit of {source}")
    version = source_version(source)
    if version is None:
        raise ContractError(f"no version in {source}/Cargo.toml")
    set_key(contract, "tag", args.tag, "tag")
    if "legacy_untagged" in contract:
        changes.append(f"legacy_untagged: {contract.pop('legacy_untagged')} -> removed")
    set_key(contract, "commit", commit, "commit")
    set_key(contract, "version", version, "version")
    dump_json(args.contract, contract)
    print("\n".join(changes) if changes else "no change")
    bad = [r for r in check_contract(contract, source) if r.status != OK]
    for result in bad:
        print(result.line())
    if bad:
        print("These items need a change in our code or in the contract by hand.", file=sys.stderr)
    return 1 if bad else 0


def cmd_features(args):
    contract = load_json(args.contract)
    registry = load_json(args.registry)
    changelog = read_text(Path(args.source) / args.changelog_path)
    if changelog is None:
        raise ContractError(f"{Path(args.source) / args.changelog_path} does not exist")
    problems = check_features(registry, changelog, contract["version"], args.root)
    for problem in problems:
        print(f"ERROR {problem}")
    if not problems:
        print(f"every changelog entry up to {contract['version']} is classified")
    return 1 if problems else 0


def cmd_interfaces(args):
    contract = load_json(args.contract)
    registry = load_json(args.registry)
    problems = check_interfaces(registry, contract, args.root)
    for problem in problems:
        print(f"ERROR {problem}")
    if not problems:
        print(f"{len(registry.get('functions', []))} upstream-facing functions are registered and tested")
    return 1 if problems else 0


def cmd_gate(args):
    base = None
    if args.base_contract and Path(args.base_contract).is_file() and Path(args.base_contract).stat().st_size:
        base = load_json(args.base_contract)
    verification = load_json(args.verification) if Path(args.verification).is_file() else {"records": []}
    try:
        labels = json.loads(args.labels) if args.labels.strip() else []
    except ValueError as exc:
        raise ContractError(f"--labels is not a JSON array: {exc}")
    if not isinstance(labels, list):
        raise ContractError("--labels is not a JSON array")
    outcome = gate(args.source, base, verification, labels, base_commit=args.base_commit or None)
    lines = []
    if outcome["unchanged"]:
        lines.append("Upstream commit unchanged: no manual verification is needed.")
    elif not outcome["breaking"]:
        lines.append("Upstream change is not breaking: no manual verification is needed.")
    else:
        lines.append("Upstream change is BREAKING relative to the accepted contract:")
        lines += [f"  - {reason}" for reason in outcome["reasons"]]
        if outcome["verified"]:
            lines.append(f"A valid verification record and the label {LABEL} are present.")
        else:
            lines.append("Manual verification is required and is not complete:")
            lines += [f"  - {problem}" for problem in outcome["problems"]]
            lines.append("A person installs and runs the package, adds a record to upstream/verification.json,")
            lines.append(f"and a maintainer adds the label {LABEL}. See {DOCS}.")
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"breaking={str(outcome['breaking']).lower()}\n")
            handle.write(f"verified={str(outcome['verified']).lower()}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("### Upstream contract gate\n\n```text\n" + text + "\n```\n")
    return 1 if outcome["breaking"] and not outcome["verified"] else 0


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", default=str(DEFAULT_SOURCE), help="upstream source tree")
    common.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common], help="check the contract").set_defaults(func=cmd_check)
    update = sub.add_parser("update", parents=[common], help="record the current source in the contract")
    update.add_argument("--tag", required=True, help="the release tag of the source, e.g. v3.1.0")
    update.set_defaults(func=cmd_update)
    features = sub.add_parser("features", parents=[common], help="check the classification of changelog entries")
    features.add_argument("--registry", default=str(DEFAULT_FEATURES))
    features.add_argument("--changelog-path", default="changelog.md")
    features.add_argument("--root", default=str(ROOT), help="repository root that test paths are relative to")
    features.set_defaults(func=cmd_features)
    interfaces = sub.add_parser("interfaces", parents=[common],
                                help="check that every function that reads upstream output or runs upstream has tests")
    interfaces.add_argument("--registry", default=str(DEFAULT_INTERFACES))
    interfaces.add_argument("--root", default=str(ROOT), help="repository root of the service and the tests")
    interfaces.set_defaults(func=cmd_interfaces)
    gate_parser = sub.add_parser("gate", parents=[common], help="CI gate for breaking upstream changes")
    gate_parser.add_argument("--base-contract", default="", help="upstream/contract.json of the base branch")
    gate_parser.add_argument("--base-commit", default="",
                             help="commit of the submodule on the base branch (empty: unknown)")
    gate_parser.add_argument("--verification", default=str(DEFAULT_VERIFICATION))
    gate_parser.add_argument("--labels", default="[]", help="JSON array of the labels of the pull request")
    gate_parser.set_defaults(func=cmd_gate)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
