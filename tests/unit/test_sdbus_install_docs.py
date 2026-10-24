"""
The Ubuntu 22.04 sdbus instructions install it where root's Python finds it.

NetworkManager runs the service as root. A plain `pip3 install sdbus` as the
desktop user goes to ~/.local, which root's Python does not search, so the
service fails with "No module named 'sdbus'" (issue #30, checked on a clean
22.04 container). Every place that tells users to install sdbus with pip must
use sudo.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FILES = [
    "README.md",
    "scripts/install-pr-build.sh",
    ".github/scripts/test_packages_check.py",
]


def unsafe_pip_lines(text):
    """The `pip install sdbus` instructions in `text` that do not use sudo"""
    bad = []
    for line in text.splitlines():
        for match in re.finditer(r"pip3? install(?: --user)? sdbus\b", line):
            before = line[: match.start()].rstrip()
            if not before.endswith("sudo"):
                bad.append(line.strip())
    return bad


@pytest.mark.parametrize("path", FILES)
def test_sdbus_is_installed_with_sudo(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        text = handle.read()

    assert "sudo pip3 install sdbus" in text
    assert unsafe_pip_lines(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "pip3 install sdbus",
        "    pip3 install sdbus",
        "run `pip3 install sdbus` first",
        "pip install sdbus",
        "pip3 install --user sdbus",
        "sudo apt install python3-pip && pip3 install sdbus",
    ],
)
def test_pip_without_sudo_is_found(text):
    assert unsafe_pip_lines(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "sudo pip3 install sdbus",
        "    sudo pip3 install sdbus",
        "run `sudo apt install python3-pip && sudo pip3 install sdbus` first",
    ],
)
def test_pip_with_sudo_is_accepted(text):
    assert unsafe_pip_lines(text) == []
