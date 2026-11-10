"""
Issue #106: the License tags of the RPM spec name, per package, the licenses
of the files the package ships, as debian/copyright gives them for the .deb
packages, plus what only the RPM bundles (python-sdbus).

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPEC = os.path.join(ROOT, "rpm", "network-manager-gpclient.spec")
COPYRIGHT = os.path.join(ROOT, "debian", "copyright")
# DEP-5 short names (without "+": only that version) -> SPDX
SPDX = {"LGPL-2.1": "LGPL-2.1-only", "LGPL-2+": "LGPL-2.0-or-later", "LGPL-2.1+": "LGPL-2.1-or-later",
        "GPL-3": "GPL-3.0-only"}
# The crates (issue #148): the SPDX IDs of the Rust crates that gpclient and gpauth link
CRATES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "ISC", "MIT-0",
          "MPL-2.0", "Unicode-3.0", "Unlicense", "Zlib", "0BSD"}
# What each package ships (its %files): the main package has the service,
# gpclient/gpauth (GPL-3.0-only, the openconnect fork: LGPL-2.1-only, lzo.c
# LGPL-2.1-or-later, json BSD-2-Clause; the crates) and libnm-vpn-plugin-gpclient.so
# (with nm-vpn-plugin-utils, LGPL-2.0-or-later). The RPM also bundles python-sdbus
# (LGPL-2.1-or-later, already there for lzo.c)
EXPECTED = {
    "": {"GPL-3.0-only", "LGPL-2.1-only", "LGPL-2.0-or-later", "LGPL-2.1-or-later"} | CRATES,
    "gnome": {"LGPL-2.1-only"},
    "plasma": {"LGPL-2.1-only"},
}


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def package_licenses(text):
    """{package suffix ("" = main): set of SPDX IDs of its License tag}"""
    found, package = {}, ""
    for line in text.splitlines():
        sub = re.match(r"%package\s+(\S+)", line)
        if sub:
            package = sub.group(1)
        tag = re.match(r"License:\s*(.+)$", line)
        if tag:
            found[package] = {part.strip() for part in tag.group(1).split(" AND ")}
    return found


def copyright_licenses(text):
    """Licenses of the Files: paragraphs (not the stand-alone license texts),
    as SPDX (the ID of "A and B" too); libxml2 is not in the packages (system libxml2)"""
    found = set()
    for paragraph in text.split("\n\n"):
        files = re.search(r"^Files:(.*?)^\S", paragraph + "\nX", re.M | re.S)
        license_ = re.search(r"^License: (.+)$", paragraph, re.M)
        if files and license_ and "deps/libxml2" not in files.group(1):
            for name in license_.group(1).split(" and "):
                found.add(SPDX.get(name, name))
    return found


def test_each_package_names_the_licenses_of_its_files():
    assert package_licenses(read(SPEC)) == EXPECTED


def test_the_packages_name_every_license_of_debian_copyright():
    assert set().union(*EXPECTED.values()) == copyright_licenses(read(COPYRIGHT))


def test_every_license_of_debian_copyright_has_a_text():
    text = read(COPYRIGHT)
    for name in set(re.findall(r"^License: (.+)$", text, re.M)):
        for one in name.split(" and "):
            assert re.search(rf"^License: {re.escape(one)}\n ", text, re.M), f"no License paragraph for {one}"


@pytest.mark.parametrize("old, new", [
    (" AND MIT AND", " AND"),  # a license of the crates left out
    (" AND BSD-2-Clause", ""),  # json/json.c of the openconnect fork
    ("LGPL-2.0-or-later AND LGPL-2.1-or-later", "LGPL-2.0-or-later"),  # lzo.c of the openconnect fork
    ("License:        GPL-3.0-only", "License:        GPL-3.0-or-later"),  # an "or later" upstream does not give
    (" AND LGPL-2.0-or-later", ""),  # nm-vpn-plugin-utils in the main package
])
def test_a_missing_or_wrong_license_is_found(old, new):
    spec = read(SPEC)
    assert old in spec

    assert package_licenses(spec.replace(old, new, 1)) != EXPECTED


def test_a_license_on_the_wrong_package_is_found():
    # the #106 draft: nm-vpn-plugin-utils on -gnome, not on the main package
    spec = read(SPEC).replace(" AND LGPL-2.0-or-later", "", 1)
    spec = spec.replace("License:        LGPL-2.1-only\n",
                        "License:        LGPL-2.1-only AND LGPL-2.0-or-later\n", 1)

    assert package_licenses(spec) != EXPECTED


def test_the_old_tag_is_found():
    assert package_licenses("License:        GPL-3.0-or-later AND LGPL-2.1-or-later\n") != EXPECTED
