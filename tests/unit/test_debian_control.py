"""
The Plasma editor package. v1.4.1 shipped one package for it,
network-manager-gpclient-plasma, built with the Qt of the Plasma of the Ubuntu
release; 1.5.0 keeps that: one package of that name on every release, Qt5 on
22.04 and 24.04, Qt6 on 24.10 and 26.04. No -plasma-5 / -plasma-6 packages and
no transitional packages are built.

Test packages of pull requests (versions like 1.5.0-1~noble1+pr31.62) did have
network-manager-gpclient-plasma-5 (Qt5 releases) and -plasma-6 (Qt6 releases)
with the same files as the new package, so every control file replaces and breaks
the ones that were built for its release below 1.5.1~, and no other: -plasma-5
on Ubuntu 22.04 and 24.04, -plasma-6 on 26.04, both on 24.10 (its test builds
produced both).

debian/rules builds the plugin against the Qt that debian/control lists in
Build-Depends (qtbase5-dev or qt6-base-dev, exactly one), and the Dockerfile of
the release installs the same Qt and KDE Frameworks packages.

Ubuntu 24.04 has a second build, for KDE neon (Ubuntu 24.04 with Plasma 6 from
archive.neon.kde.org): debian/control.ubuntu24.04-neon with Dockerfile.ubuntu24.04-neon
builds network-manager-gpclient-plasma-6 (Qt6) and nothing else, and the noble
network-manager-gpclient-plasma (Qt5) conflicts with it. debian/rules recognises
such a control file by its lack of the core package and builds the Plasma plugin only.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import glob
import os
import pwd
import re
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEBIAN = os.path.join(ROOT, "debian")
CONTROLS = sorted(glob.glob(os.path.join(DEBIAN, "control*")))

CORE = "network-manager-gpclient"
PLASMA = CORE + "-plasma"
PLASMA6 = PLASMA + "-6"
FORMER = {"qt5": PLASMA + "-5", "qt6": PLASMA6}
NEON = "control.ubuntu24.04-neon"
BOUND = "1.5.1~"
VERSIONED = " (= ${binary:Version})"
# The Qt of every control file; debian/control is a copy of the one of Ubuntu 24.04
QT = {
    "control": "qt5",
    "control.ubuntu22.04": "qt5",
    "control.ubuntu24.04": "qt5",
    "control.ubuntu24.10": "qt6",
    "control.ubuntu26.04": "qt6",
}
# The former test packages every control file replaces and breaks
FORMERS = {
    "control": [PLASMA + "-5"],
    "control.ubuntu22.04": [PLASMA + "-5"],
    "control.ubuntu24.04": [PLASMA + "-5"],
    "control.ubuntu24.10": [PLASMA + "-5", PLASMA + "-6"],
    "control.ubuntu26.04": [PLASMA + "-6"],
}
# What the Plasma package of every control file conflicts with: on Ubuntu 24.04 the
# package for KDE neon, which has the same files in the Qt6 directory
CONFLICTS = {
    "control": [PLASMA6],
    "control.ubuntu22.04": [],
    "control.ubuntu24.04": [PLASMA6],
    "control.ubuntu24.10": [],
    "control.ubuntu26.04": [],
}
# The plasma-nm the Plasma package of every control file depends on: the plugin is built with
# one Plasma generation (Qt5: Plasma 5, Qt6: Plasma 6), so apt must refuse it on the other. KDE neon
# is Ubuntu 24.04 with Plasma 6, where the Qt5 package (plasma-nm 4:5.x in the archive) must not install
PLASMA_NM = {
    "control": "plasma-nm (<< 4:6)",
    "control.ubuntu22.04": "plasma-nm (<< 4:6)",
    "control.ubuntu24.04": "plasma-nm (<< 4:6)",
    "control.ubuntu24.10": "plasma-nm (>= 4:6)",
    "control.ubuntu26.04": "plasma-nm (>= 4:6)",
}
# What the core package recommends: on Ubuntu 24.04 the package for KDE neon is one of the alternatives, or
# apt would pull the GNOME package onto KDE neon, where -plasma-6 is installed
RECOMMENDS = {
    "control": "%s-gnome | %s | %s" % (CORE, PLASMA, PLASMA6),
    "control.ubuntu22.04": "%s-gnome | %s" % (CORE, PLASMA),
    "control.ubuntu24.04": "%s-gnome | %s | %s" % (CORE, PLASMA, PLASMA6),
    "control.ubuntu24.10": "%s-gnome | %s" % (CORE, PLASMA),
    "control.ubuntu26.04": "%s-gnome | %s" % (CORE, PLASMA),
}
DOCKERFILES = {
    "control.ubuntu22.04": "Dockerfile.ubuntu22.04",
    "control.ubuntu24.04": "Dockerfile.ubuntu24.04",
    "control.ubuntu24.10": "Dockerfile.ubuntu24.10",
    "control.ubuntu26.04": "Dockerfile.ubuntu26.04",
}
# What the Plasma plugin needs to build, by Qt (the plugin's CMakeLists.txt)
BUILD_DEPENDS = {
    "qt5": {"qtbase5-dev", "libkf5networkmanagerqt-dev", "libkf5i18n-dev", "libkf5service-dev",
            "libkf5widgetsaddons-dev"},
    "qt6": {"qt6-base-dev", "qt6-base-dev-tools", "libkf6networkmanagerqt-dev", "libkf6i18n-dev",
            "libkf6service-dev", "libkf6widgetsaddons-dev", "libkf6coreaddons-dev"},
}
# Versions that exist as test builds of pull requests (and the last release)
EXISTING_VERSIONS = ["1.4.1-1~noble1", "1.4.2-1~noble1+pr24.57", "1.5.0-1~noble1+pr31.62", "1.5.0-1~resolute1+pr31.62"]


def parse_control(text):
    """The stanzas of a control file as {package: {lowercase field: value}}; the source stanza is
    under the key '' (it has no Package field)"""
    packages = {}
    # A blank line may hold spaces or tabs and end in CRLF
    for stanza in re.split(r"\n[ \t]*\r?\n", text):
        fields = {}
        last = None
        for line in stanza.splitlines():
            if line[:1] in (" ", "\t"):
                if last:
                    fields[last] += "\n" + line.strip()
            elif ":" in line:
                last, _, value = line.partition(":")
                last = last.strip().lower()
                fields[last] = value.strip()
        if "package" in fields:
            packages[fields["package"]] = fields
        elif "source" in fields:
            packages[""] = fields
    return packages


def relations(value):
    """['a (<< 1)', 'b'] of 'a (<< 1),\\n b'"""
    return [" ".join(item.split()) for item in value.replace("\n", " ").split(",") if item.strip()]


def relation(former):
    return "%s (<< %s)" % (former, BOUND)


def upstream(version):
    """(1, 5, 0) of 1.5.0-1~noble1+pr31.62"""
    return tuple(int(part) for part in re.match(r"[0-9]+(?:\.[0-9]+)*", version).group(0).split("."))


def covers(bound, version):
    """True when `version` is below `bound`, a version that ends in '~' (1.5.1~ is below every 1.5.1-*)"""
    assert bound.endswith("~"), bound
    return upstream(version) < upstream(bound[:-1])


def expected(name, **other):
    """What check_control expects of the real control file `name`, with `other` expectations instead"""
    return {"formers": FORMERS[name], "conflicts": CONFLICTS[name], "plasma_nm": PLASMA_NM[name],
            "recommends": RECOMMENDS[name], **other}


def check_control(text, qt, formers=None, conflicts=(), plasma_nm="plasma-nm", recommends=None):
    """Problems with the Plasma package of a control file that builds with `qt` (empty list when fine);
    `formers` are the former test packages it replaces and breaks (default: the one of that Qt),
    `conflicts` the packages it conflicts with (default: none), `plasma_nm` the plasma-nm relation it
    depends on, `recommends` what the core package recommends (default: gnome | plasma)"""
    formers = formers or [FORMER[qt]]
    recommends = recommends or "%s-gnome | %s" % (CORE, PLASMA)
    packages = parse_control(text)
    problems = []
    source = packages.get("", {})

    for name, fields in packages.items():
        if name.startswith(PLASMA) and name != PLASMA:
            problems.append("unexpected package %s: the Plasma package is %s only" % (name, PLASMA))
        if fields.get("section") == "oldlibs" or "transitional" in fields.get("description", "").lower():
            problems.append("%s is a transitional package" % (name or "source"))

    for name in (CORE, CORE + "-gnome"):
        if name not in packages:
            problems.append("no package %s" % name)

    build = {item.split("(")[0].split("|")[0].strip() for item in relations(source.get("build-depends", ""))}
    wanted = BUILD_DEPENDS[qt]
    other = BUILD_DEPENDS["qt6" if qt == "qt5" else "qt5"]
    for name in sorted(wanted - build):
        problems.append("Build-Depends lacks %s" % name)
    for name in sorted(other & build):
        problems.append("Build-Depends has %s, but the plugin is built with %s" % (name, qt))

    plasma = packages.get(PLASMA)
    if plasma is None:
        problems.append("no package %s" % PLASMA)
        return problems
    if plasma.get("architecture") != "any":
        problems.append("%s has Architecture %r, not 'any'" % (PLASMA, plasma.get("architecture")))
    depends = relations(plasma.get("depends", ""))
    for needed in (CORE + VERSIONED, plasma_nm):
        if needed not in depends:
            problems.append("%s does not depend on %s" % (PLASMA, needed))
    for field in ("replaces", "breaks"):
        values = relations(plasma.get(field, ""))
        for former in formers:
            if relation(former) not in values:
                problems.append("%s lacks %s: %s" % (PLASMA, field.capitalize(), relation(former)))
        for value in values:
            if value not in [relation(former) for former in formers]:
                problems.append("%s has %s: %s" % (PLASMA, field.capitalize(), value))
    if relations(plasma.get("conflicts", "")) != list(conflicts):
        problems.append("%s conflicts with %r, expected %r" % (PLASMA, plasma.get("conflicts"), list(conflicts)))

    core = packages.get(CORE, {})
    if core.get("recommends") != recommends:
        problems.append("%s recommends %r, not %r" % (CORE, core.get("recommends"), recommends))
    return problems


def depends_of(text, package):
    """The names (without version) the Depends field of `package` lists; empty when it has none"""
    depends = parse_control(text).get(package, {}).get("depends", "")
    return {item.split("(")[0].split("|")[0].strip() for item in relations(depends)}


def read(name):
    with open(os.path.join(DEBIAN, name), encoding="utf-8") as handle:
        return handle.read()


def good(qt, formers=None, conflicts=(), plasma_nm="plasma-nm", recommends=None):
    """A correct control file"""
    formers = formers or [FORMER[qt]]
    recommends = recommends or "%s-gnome | %s" % (CORE, PLASMA)
    conflict_line = "Conflicts: %s\n" % ", ".join(conflicts) if conflicts else ""
    versioned = ",\n         ".join(relation(f) for f in formers)
    qt_dev = "qtbase5-dev" if qt == "qt5" else "qt6-base-dev"
    deps = sorted(BUILD_DEPENDS[qt] - {qt_dev})
    build = ",\n               ".join(["debhelper-compat (= 13)", qt_dev] + deps)
    return """\
Source: network-manager-gpclient
Section: net
Build-Depends: %s,
               curl
Standards-Version: 4.6.2

Package: network-manager-gpclient
Architecture: any
Depends: ${shlibs:Depends}
Recommends: %s
Description: core

Package: network-manager-gpclient-gnome
Architecture: any
Depends: ${shlibs:Depends},
         network-manager-gpclient (= ${binary:Version})
Description: GNOME GUI

Package: network-manager-gpclient-plasma
Architecture: any
Depends: ${shlibs:Depends},
         ${misc:Depends},
         network-manager-gpclient (= ${binary:Version}),
         %s
Replaces: %s
Breaks: %s
%sDescription: Plasma GUI
 text
""" % (build, recommends, plasma_nm, versioned, versioned, conflict_line)


GOOD = {"qt5": good("qt5"), "qt6": good("qt6")}
BOTH = [PLASMA + "-5", PLASMA + "-6"]


class TestParseControl:
    def test_stanzas_are_split_at_a_blank_line(self):
        assert sorted(parse_control(GOOD["qt5"])) == ["", CORE, CORE + "-gnome", PLASMA]

    @pytest.mark.parametrize("blank", ["\n \n", "\n\t\n", "\n  \t \n", "\n\r\n", "\n \r\n", "\n\n"])
    def test_a_blank_line_with_spaces_or_a_carriage_return_still_ends_a_stanza(self, blank):
        text = "Package: a\nDepends: x\nDescription: A\n text" + blank + "Package: b\nDepends: y\nDescription: B\n"
        packages = parse_control(text)
        assert sorted(packages) == ["a", "b"]
        assert packages["a"]["depends"] == "x"
        assert packages["b"]["depends"] == "y"

    def test_a_crlf_file_is_parsed_like_an_lf_file(self):
        assert parse_control(GOOD["qt5"].replace("\n", "\r\n")) == parse_control(GOOD["qt5"])

    @pytest.mark.parametrize("line", [" .", " text", "\t."])
    def test_a_line_that_is_not_blank_does_not_end_a_stanza(self, line):
        packages = parse_control("Package: a\nDescription: A\n" + line + "\nDepends: x\n")
        assert sorted(packages) == ["a"]
        assert packages["a"]["depends"] == "x"

    def test_the_fields_of_one_stanza_do_not_leak_into_the_next(self):
        packages = parse_control(GOOD["qt5"])
        assert "replaces" not in packages[CORE]
        assert "recommends" not in packages[PLASMA]

    def test_without_a_blank_line_two_stanzas_are_one(self):
        merged = GOOD["qt5"].replace("\n\nPackage: " + PLASMA + "\n", "\nPackage: " + PLASMA + "\n")
        assert CORE + "-gnome" not in parse_control(merged)
        assert check_control(merged, "qt5") != []


class TestCheckControl:
    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_a_correct_control_file_has_no_problems(self, qt):
        assert check_control(GOOD[qt], qt) == []

    @pytest.mark.parametrize("name, qt", [("control.ubuntu24.04", "qt5"), ("control.ubuntu26.04", "qt6")])
    def test_the_fixtures_have_the_stanzas_and_relations_of_the_real_files(self, name, qt):
        real = parse_control(read(name))
        fixture = parse_control(good(qt, FORMERS[name], CONFLICTS[name], PLASMA_NM[name], RECOMMENDS[name]))
        assert sorted(real) == sorted(fixture)
        for field in ("replaces", "breaks", "depends"):
            assert relations(real[PLASMA][field]) == relations(fixture[PLASMA][field]) or field == "depends"
        assert real[CORE]["recommends"] == fixture[CORE]["recommends"]
        assert PLASMA_NM[name] in relations(real[PLASMA]["depends"])
        assert PLASMA_NM[name] in relations(fixture[PLASMA]["depends"])

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    @pytest.mark.parametrize("name, old, new", [
        ("no Replaces", "Replaces: {f} (<< 1.5.1~)\n", ""),
        ("no Breaks", "Breaks: {f} (<< 1.5.1~)\n", ""),
        ("Replaces on a lower bound", "Replaces: {f} (<< 1.5.1~)", "Replaces: {f} (<< 1.5.0~)"),
        ("Breaks on a lower bound", "Breaks: {f} (<< 1.5.1~)", "Breaks: {f} (<< 1.5.0)"),
        ("Breaks on the core package", "Breaks: {f} (<< 1.5.1~)", "Breaks: network-manager-gpclient (<< 1.5.1~)"),
        ("Replaces on the old name of 1.4.1", "Replaces: {f} (<< 1.5.1~)",
         "Replaces: network-manager-gpclient-plasma (<< 1.5.1~)"),
        ("Conflicts", "Description: Plasma GUI", "Conflicts: network-manager-gpclient-plasma-7\nDescription: Plasma GUI"),
        ("Architecture all", "Package: network-manager-gpclient-plasma\nArchitecture: any",
         "Package: network-manager-gpclient-plasma\nArchitecture: all"),
        ("no plasma-nm", "         plasma-nm\n", "         plasma-nm-extra\n"),
        ("no versioned core package", "network-manager-gpclient (= ${{binary:Version}}),\n         plasma-nm",
         "network-manager-gpclient,\n         plasma-nm"),
        ("Recommends without the Plasma package",
         "Recommends: network-manager-gpclient-gnome | network-manager-gpclient-plasma\n",
         "Recommends: network-manager-gpclient-gnome\n"),
        ("Recommends of a former package",
         "Recommends: network-manager-gpclient-gnome | network-manager-gpclient-plasma\n",
         "Recommends: network-manager-gpclient-gnome | network-manager-gpclient-plasma-5\n"),
    ])
    def test_a_broken_plasma_package_is_reported(self, name, old, new, qt):
        text = GOOD[qt]
        old, new = old.format(f=FORMER[qt]), new.format(f=FORMER[qt])
        assert old in text, name
        assert check_control(text.replace(old, new), qt) != [], name

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_a_file_without_the_plasma_package_is_reported(self, qt):
        text = GOOD[qt]
        assert any("no package" in p for p in check_control(text[:text.index("Package: " + PLASMA + "\n")], qt))

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_the_replaced_package_is_the_one_with_the_same_qt(self, qt):
        wrong = "qt6" if qt == "qt5" else "qt5"
        text = GOOD[qt].replace(FORMER[qt], FORMER[wrong])
        problems = check_control(text, qt)
        assert any("lacks Replaces" in p for p in problems) and any("lacks Breaks" in p for p in problems)

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    @pytest.mark.parametrize("extra", ["network-manager-gpclient-plasma-5", "network-manager-gpclient-plasma-6",
                                       "network-manager-gpclient-plasma-7"])
    def test_a_second_plasma_package_is_reported(self, qt, extra):
        text = GOOD[qt] + "\nPackage: %s\nArchitecture: any\nDescription: Plasma GUI\n text\n" % extra
        assert any("unexpected package " + extra in p for p in check_control(text, qt))

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    @pytest.mark.parametrize("stanza", [
        "Package: network-manager-gpclient-other\nArchitecture: any\nSection: oldlibs\nDescription: other\n text\n",
        "Package: network-manager-gpclient-other\nArchitecture: any\n"
        "Description: transitional package for something\n This is a transitional package.\n",
    ])
    def test_a_transitional_package_is_reported(self, qt, stanza):
        assert any("transitional" in p for p in check_control(GOOD[qt] + "\n" + stanza, qt))

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_the_build_depends_of_the_other_qt_are_reported(self, qt):
        other = "qt6" if qt == "qt5" else "qt5"
        extra = sorted(BUILD_DEPENDS[other])[0]
        text = GOOD[qt].replace("               curl\n", "               %s,\n               curl\n" % extra)
        assert any("Build-Depends has " + extra in p for p in check_control(text, qt))

    @pytest.mark.parametrize("qt, missing", [(q, m) for q in ("qt5", "qt6") for m in sorted(BUILD_DEPENDS[q])])
    def test_a_missing_build_depends_is_reported(self, qt, missing):
        text = GOOD[qt].replace("               %s,\n" % missing, "")
        assert text != GOOD[qt]
        assert any("Build-Depends lacks " + missing in p for p in check_control(text, qt))

    def test_the_other_qt_is_not_accepted_for_the_control_file(self):
        assert check_control(GOOD["qt5"], "qt6") != []
        assert check_control(GOOD["qt6"], "qt5") != []

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_the_expected_conflict_is_accepted_and_a_missing_or_other_one_is_reported(self, qt):
        with_conflict = good(qt, conflicts=[PLASMA6])
        assert check_control(with_conflict, qt, conflicts=[PLASMA6]) == []
        assert any("conflicts with" in p for p in check_control(GOOD[qt], qt, conflicts=[PLASMA6]))
        assert any("conflicts with" in p for p in check_control(with_conflict, qt))
        assert any("conflicts with" in p for p in check_control(with_conflict, qt, conflicts=[PLASMA + "-7"]))
        assert any("conflicts with" in p for p in check_control(with_conflict, qt, conflicts=[PLASMA6, PLASMA + "-7"]))


class TestUpperBound:
    @pytest.mark.parametrize("version", EXISTING_VERSIONS)
    def test_the_bound_covers_every_version_that_exists_as_a_build(self, version):
        assert covers(BOUND, version)

    @pytest.mark.parametrize("version", ["1.5.1-1~noble1", "1.5.1-1", "1.6.0-1~noble1", "2.0-1"])
    def test_the_bound_does_not_cover_later_releases(self, version):
        assert not covers(BOUND, version)

    @pytest.mark.parametrize("bound", ["1.5.0~", "1.4.2~"])
    def test_a_lower_bound_would_miss_the_test_builds_of_1_5_0(self, bound):
        assert not covers(bound, "1.5.0-1~noble1+pr31.62")

    def test_every_control_file_uses_this_bound(self):
        for name in QT:
            assert "(<< %s)" % BOUND in read(name), name


class TestControlFiles:
    def test_every_control_file_is_covered(self):
        assert sorted(os.path.basename(p) for p in CONTROLS) == sorted([*QT, NEON])

    @pytest.mark.parametrize("name, qt", sorted(QT.items()))
    def test_the_plasma_package_is_declared(self, name, qt):
        assert check_control(read(name), qt, **expected(name)) == []

    @pytest.mark.parametrize("name, qt", sorted(QT.items()))
    def test_the_files_are_not_accepted_for_the_other_qt(self, name, qt):
        assert check_control(read(name), "qt6" if qt == "qt5" else "qt5", **expected(name)) != []

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_conflicts_of_the_plasma_package_are_those_of_the_file_only(self, name):
        assert relations(parse_control(read(name))[PLASMA].get("conflicts", "")) == CONFLICTS[name]
        other = [c for c in (PLASMA6, PLASMA + "-7") if c not in CONFLICTS[name]]
        assert check_control(read(name), QT[name], **expected(name, conflicts=CONFLICTS[name] + other[:1])) != []
        assert (CONFLICTS[name] == []) or check_control(read(name), QT[name], **expected(name, conflicts=())) != []

    def test_only_ubuntu_24_04_conflicts_with_the_plasma_6_package_of_kde_neon(self):
        assert sorted(n for n, c in CONFLICTS.items() if c) == ["control", "control.ubuntu24.04"]

    @pytest.mark.parametrize("name", sorted(QT))
    def test_a_file_that_replaces_other_former_packages_than_its_own_is_reported(self, name):
        qt = QT[name]
        assert check_control(read(name), qt, **expected(name, formers=["network-manager-gpclient-plasma-7"])) != []
        if name != "control.ubuntu24.10":
            # only 24.10 had test builds of both
            assert check_control(read(name), qt, **expected(name, formers=BOTH)) != []

    def test_debian_control_is_the_copy_for_ubuntu_24_04(self):
        assert read("control") == read("control.ubuntu24.04")

    @pytest.mark.parametrize("name, qt", [("control.ubuntu22.04", "qt5"), ("control.ubuntu24.04", "qt5"),
                                          ("control.ubuntu24.10", "qt6"), ("control.ubuntu26.04", "qt6")])
    def test_qt5_on_22_04_and_24_04_and_qt6_on_24_10_and_26_04(self, name, qt):
        package = parse_control(read(name))[PLASMA]
        expected = [relation(f) for f in FORMERS[name]]
        assert relations(package["replaces"]) == expected
        assert relations(package["breaks"]) == expected
        build = parse_control(read(name))[""]["build-depends"]
        assert ("qtbase5-dev" in build) == (qt == "qt5")
        assert ("qt6-base-dev" in build) == (qt == "qt6")

    def test_24_10_replaces_and_breaks_both_former_packages(self):
        package = parse_control(read("control.ubuntu24.10"))[PLASMA]
        for field in ("replaces", "breaks"):
            assert relations(package[field]) == [relation(PLASMA + "-5"), relation(PLASMA + "-6")]

    @pytest.mark.parametrize("missing", BOTH)
    def test_a_24_10_file_without_one_of_the_former_packages_is_reported(self, missing):
        text = good("qt6", BOTH)
        assert check_control(text, "qt6", BOTH) == []
        broken = text.replace(",\n         %s (<< 1.5.1~)" % missing, "").replace(
            "%s (<< 1.5.1~),\n         " % missing, "")
        assert broken != text
        assert any("lacks" in p and missing in p for p in check_control(broken, "qt6", BOTH))

    def test_the_file_of_24_04_does_not_pass_as_the_one_of_24_10(self):
        assert check_control(read("control.ubuntu24.04"), "qt5", **expected("control.ubuntu24.04", formers=BOTH)) != []

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_packages_are_the_core_gnome_and_plasma_ones(self, name):
        packages = parse_control(read(name))
        assert sorted(p for p in packages if p) == [CORE, CORE + "-gnome", PLASMA]

    @pytest.mark.parametrize("name", sorted(QT))
    def test_core_package_depends_on_xdg_utils(self, name):
        # gpauth 3.x runs xdg-mime to register the callback URL handler and
        # fails without it (issue #34)
        assert "xdg-utils" in depends_of(read(name), CORE), name

    def test_missing_xdg_utils_dependency_is_reported(self):
        # The same control text without xdg-utils: the helper must not find it
        text = read("control.ubuntu24.04")
        assert "xdg-utils" in depends_of(text, CORE)
        broken = text.replace("openconnect,\n         xdg-utils\n", "openconnect\n")
        assert broken != text
        assert "xdg-utils" not in depends_of(broken, CORE)
        assert "xdg-utils" not in depends_of(GOOD["qt5"], CORE)

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_core_package_recommends_gnome_or_plasma(self, name):
        assert parse_control(read(name))[CORE]["recommends"] == RECOMMENDS[name]

    def test_only_ubuntu_24_04_recommends_the_plasma_6_package_so_that_kde_neon_does_not_get_gnome(self):
        assert sorted(n for n, r in RECOMMENDS.items() if PLASMA6 in r) == ["control", "control.ubuntu24.04"]
        for name in sorted(QT):
            alternatives = [a.strip() for a in parse_control(read(name))[CORE]["recommends"].split("|")]
            assert (PLASMA6 in alternatives) == (name in ("control", "control.ubuntu24.04")), name
            assert alternatives[:2] == [CORE + "-gnome", PLASMA], name

    @pytest.mark.parametrize("name", sorted(QT))
    def test_a_core_package_with_other_recommends_is_reported(self, name):
        text = read(name)
        old = "Recommends: " + RECOMMENDS[name]
        assert old in text
        for new in ("Recommends: %s-gnome" % CORE, "Recommends: %s | %s" % (PLASMA, CORE + "-gnome"),
                    "Recommends: %s-gnome | %s | %s" % (CORE, PLASMA, PLASMA + "-7"),
                    "Recommends: %s-gnome | %s | %s" % (CORE, PLASMA, PLASMA6) if PLASMA6 not in old
                    else "Recommends: %s-gnome | %s" % (CORE, PLASMA)):
            assert any("recommends" in p for p in
                       check_control(text.replace(old, new), QT[name], **expected(name))), new

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_plasma_package_depends_on_the_plasma_generation_of_its_qt(self, name):
        depends = relations(parse_control(read(name))[PLASMA]["depends"])
        assert PLASMA_NM[name] in depends
        assert "plasma-nm" not in depends
        # Qt5 is Plasma 5 and Qt6 is Plasma 6: an unversioned or the other bound is reported
        text = read(name)
        for new in ("plasma-nm", "plasma-nm (>= 4:6)" if QT[name] == "qt5" else "plasma-nm (<< 4:6)"):
            broken = text.replace("         " + PLASMA_NM[name] + "\n", "         " + new + "\n")
            assert broken != text
            assert any("does not depend on" in p for p in check_control(broken, QT[name], **expected(name))), new

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_core_package_description_names_the_plasma_package(self, name):
        description = parse_control(read(name))[CORE]["description"]
        assert "- %s for KDE Plasma" % PLASMA in description
        assert "plasma-5" not in description and "plasma-6" not in description

    CORE_TEXT = (" The core package alone is enough to use GlobalProtect VPN from the command\n"
                 " line (nmcli): it ships the NetworkManager plugin libnm-vpn-plugin-gpclient.so.\n"
                 " The GUI packages add the connection editors:")

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_core_package_description_says_that_it_alone_supports_nmcli(self, name):
        text = read(name)
        stanza = text[text.index("Package: %s\n" % CORE):text.index("Package: %s-gnome\n" % CORE)]
        assert self.CORE_TEXT in stanza
        assert stanza.index(self.CORE_TEXT) < stanza.index("- %s-gnome for" % CORE)

    def test_the_core_package_description_is_the_same_in_every_file(self):
        found = {parse_control(read(name))[CORE]["description"] for name in QT}
        assert len(found) == 1

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_core_package_really_ships_the_nmcli_plugin(self, name):
        # the description names the file; the rules install it into the core package only
        assert re.search(r"libnm-vpn-plugin-gpclient\.so\n?\s*\\?\s*\$\(CURDIR\)/debian/%s/usr/lib/"
                         % CORE, RULES)
        assert "libnm-vpn-plugin-gpclient.so" in parse_control(read(name))[CORE]["description"]
        assert "libnm-vpn-plugin-gpclient.so" not in parse_control(read(name))[CORE + "-gnome"]["description"]

    @pytest.mark.parametrize("former", sorted(FORMER.values()))
    def test_the_former_packages_have_no_install_file(self, former):
        assert not os.path.exists(os.path.join(DEBIAN, former + ".install"))

    def test_the_plasma_package_has_an_install_file(self):
        assert os.path.exists(os.path.join(DEBIAN, PLASMA + ".install"))

    @pytest.mark.parametrize("name", sorted(QT))
    def test_no_package_provides_and_only_the_known_ones_conflict(self, name):
        lines = [l for l in read(name).splitlines() if l.startswith(("Provides:", "Conflicts:"))]
        # the core package conflicts with globalprotect-openconnect, the Plasma package as in CONFLICTS
        expected = ["Conflicts: globalprotect-openconnect"] + ["Conflicts: " + c for c in CONFLICTS[name]]
        assert lines == expected


def build_depends_of(text):
    return {item.split("(")[0].split("|")[0].strip() for item in relations(parse_control(text)[""]["build-depends"])}


def dockerfile_packages(text):
    """The packages of the apt-get install line of a Dockerfile"""
    install = text[text.index("apt-get install -y"):]
    install = install[:install.index("&&", 1) if "&&" in install[1:] else len(install)]
    return {w for w in re.findall(r"^\s+([a-z0-9][a-z0-9+.-]*)\s*\\?$", install, re.M)}


class TestDockerfiles:
    @staticmethod
    def qt_packages(names):
        return {n for n in names if re.match(r"(qt|libkf)", n)}

    @pytest.mark.parametrize("control, dockerfile", sorted(DOCKERFILES.items()))
    def test_the_dockerfile_installs_the_qt_packages_the_control_file_needs(self, control, dockerfile):
        with open(os.path.join(ROOT, dockerfile), encoding="utf-8") as handle:
            docker = self.qt_packages(dockerfile_packages(handle.read()))
        build = self.qt_packages(build_depends_of(read(control)))
        assert build >= BUILD_DEPENDS[QT[control]]
        assert docker >= BUILD_DEPENDS[QT[control]]
        assert docker == build

    @pytest.mark.parametrize("control, dockerfile", sorted(DOCKERFILES.items()))
    def test_the_dockerfile_installs_no_packages_of_the_other_qt(self, control, dockerfile):
        other = BUILD_DEPENDS["qt6" if QT[control] == "qt5" else "qt5"]
        with open(os.path.join(ROOT, dockerfile), encoding="utf-8") as handle:
            assert not dockerfile_packages(handle.read()) & other

    def test_the_dockerfile_parser_finds_the_packages(self):
        text = "RUN apt-get update && apt-get install -y \\\n    build-essential \\\n    qt6-base-dev \\\n    curl \\\n    && rm -rf x\n"
        assert dockerfile_packages(text) == {"build-essential", "qt6-base-dev", "curl"}


# The Build-Depends of the neon control file: what the Plasma 6 plugin needs and nothing of the rest
NEON_BUILD_DEPENDS = BUILD_DEPENDS["qt6"] | {"debhelper-compat", "cmake", "extra-cmake-modules", "plasma-nm"}
NEON_DEPENDS = ["${shlibs:Depends}", "${misc:Depends}", CORE + VERSIONED, "plasma-nm (>= 4:6)"]


def check_neon(text):
    """Problems with the control file for KDE neon (empty list when fine): one binary package,
    network-manager-gpclient-plasma-6, that needs Plasma 6 and conflicts with the Qt5 one"""
    packages = parse_control(text)
    problems = []
    names = sorted(p for p in packages if p)
    if names != [PLASMA6]:
        problems.append("the packages are %s, expected only %s" % (names, PLASMA6))
    source = packages.get("", {})
    if source.get("source") != CORE:
        problems.append("the source package is %r, not %s" % (source.get("source"), CORE))
    build = {item.split("(")[0].split("|")[0].strip() for item in relations(source.get("build-depends", ""))}
    if build != NEON_BUILD_DEPENDS:
        problems.append("Build-Depends lacks %s and has %s" % (sorted(NEON_BUILD_DEPENDS - build), sorted(build - NEON_BUILD_DEPENDS)))
    plasma = packages.get(PLASMA6)
    if plasma is None:
        return problems
    if plasma.get("architecture") != "amd64":
        problems.append("%s has Architecture %r, not 'amd64'" % (PLASMA6, plasma.get("architecture")))
    if relations(plasma.get("depends", "")) != NEON_DEPENDS:
        problems.append("%s depends on %s, expected %s" % (PLASMA6, relations(plasma.get("depends", "")), NEON_DEPENDS))
    if relations(plasma.get("conflicts", "")) != [PLASMA]:
        problems.append("%s conflicts with %r, expected %s" % (PLASMA6, plasma.get("conflicts"), PLASMA))
    for field in ("replaces", "breaks", "provides"):
        if field in plasma:
            problems.append("%s has %s: %s" % (PLASMA6, field.capitalize(), plasma[field]))
    if "KDE neon" not in plasma.get("description", ""):
        problems.append("the description of %s does not say that it is for KDE neon" % PLASMA6)
    return problems


class TestNeonControl:
    def test_the_control_file_for_kde_neon_is_correct(self):
        assert check_neon(read(NEON)) == []

    def test_it_builds_the_plasma_6_package_only_with_qt6(self):
        packages = parse_control(read(NEON))
        assert sorted(p for p in packages if p) == [PLASMA6]
        build = build_depends_of(read(NEON))
        assert "qt6-base-dev" in build and not build & BUILD_DEPENDS["qt5"]
        assert not [b for b in build if re.match(r"lib(gtk|nm|nma|ssl|dbus|openconnect|webkit)", b)]

    def test_the_neon_package_is_built_for_amd64_only_and_the_other_files_for_any(self):
        # the one place that says it: the workflow reads it from the control file of the variant
        assert parse_control(read(NEON))[PLASMA6]["architecture"] == "amd64"
        for name in QT:
            assert {f["architecture"] for p, f in parse_control(read(name)).items() if p} == {"any"}, name

    def test_the_source_package_is_the_one_of_the_other_control_files(self):
        assert parse_control(read(NEON))[""]["source"] == parse_control(read("control.ubuntu24.04"))[""]["source"]

    @pytest.mark.parametrize("name, old, new", [
        ("another source", "Source: network-manager-gpclient\n", "Source: other\n"),
        ("the core package", "\nPackage: network-manager-gpclient-plasma-6\n",
         "\nPackage: network-manager-gpclient\nArchitecture: any\nDescription: core\n x\n\nPackage: network-manager-gpclient-plasma-6\n"),
        ("the package of the other name", "Package: network-manager-gpclient-plasma-6\n",
         "Package: network-manager-gpclient-plasma\n"),
        ("Architecture all", "Architecture: amd64", "Architecture: all"),
        ("Architecture any", "Architecture: amd64", "Architecture: any"),
        ("Architecture arm64", "Architecture: amd64", "Architecture: arm64"),
        ("no Architecture", "Architecture: amd64\n", ""),
        ("no Conflicts", "Conflicts: network-manager-gpclient-plasma\n", ""),
        ("Conflicts with the wrong package", "Conflicts: network-manager-gpclient-plasma\n",
         "Conflicts: network-manager-gpclient-gnome\n"),
        ("Conflicts with a version", "Conflicts: network-manager-gpclient-plasma\n",
         "Conflicts: network-manager-gpclient-plasma (<< 1.5.1~)\n"),
        ("no Plasma 6 in the Depends", "plasma-nm (>= 4:6)", "plasma-nm"),
        ("Plasma 5 in the Depends", "plasma-nm (>= 4:6)", "plasma-nm (>= 4:5)"),
        ("no versioned core package", " (= ${binary:Version})", ""),
        ("Replaces", "Conflicts: network-manager-gpclient-plasma\n",
         "Conflicts: network-manager-gpclient-plasma\nReplaces: network-manager-gpclient-plasma-5 (<< 1.5.1~)\n"),
        ("Breaks", "Conflicts: network-manager-gpclient-plasma\n",
         "Conflicts: network-manager-gpclient-plasma\nBreaks: network-manager-gpclient-plasma (<< 1.5.1~)\n"),
        ("Qt5 Build-Depends", "               qt6-base-dev,\n", "               qt6-base-dev,\n               qtbase5-dev,\n"),
        ("GNOME Build-Depends", "               qt6-base-dev,\n", "               qt6-base-dev,\n               libgtk-3-dev,\n"),
        ("no KF6 Build-Depends", "               libkf6coreaddons-dev,\n", ""),
        ("no plasma-nm Build-Depends", "libkf6coreaddons-dev,\n               plasma-nm\n", "libkf6coreaddons-dev\n"),
        ("the description without KDE neon", "KDE neon", "KDE"),
    ])
    def test_a_broken_neon_control_file_is_reported(self, name, old, new):
        text = read(NEON)
        assert old in text, name
        assert check_neon(text.replace(old, new)) != [], name

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_other_control_files_are_not_neon_control_files(self, name):
        assert check_neon(read(name)) != []

    @pytest.mark.parametrize("name", sorted(QT))
    def test_the_neon_control_file_is_not_a_control_file_of_a_release(self, name):
        for former in (FORMERS[name], BOTH):
            assert check_control(read(NEON), "qt6", **expected(name, formers=former)) != []

    def test_the_package_conflicts_with_the_one_that_conflicts_with_it(self):
        neon = parse_control(read(NEON))[PLASMA6]
        noble = parse_control(read("control.ubuntu24.04"))[PLASMA]
        assert relations(neon["conflicts"]) == [PLASMA]
        assert relations(noble["conflicts"]) == [PLASMA6]

    def test_the_noble_package_still_breaks_and_replaces_only_the_former_test_package(self):
        noble = parse_control(read("control.ubuntu24.04"))[PLASMA]
        assert relations(noble["breaks"]) == [relation(PLASMA + "-5")]
        assert relations(noble["replaces"]) == [relation(PLASMA + "-5")]

    def test_the_package_is_not_a_former_test_package_that_a_control_file_replaces(self):
        # 1.5.0 builds network-manager-gpclient-plasma-6 for neon; the 24.10 and 26.04 files still
        # replace and break the -plasma-6 of the pull request test builds below 1.5.1~, which have
        # the same files in the same Qt6 directory
        for name in ("control.ubuntu24.10", "control.ubuntu26.04"):
            assert relation(PLASMA6) in relations(parse_control(read(name))[PLASMA]["breaks"])


NEON_DOCKERFILE = "Dockerfile.ubuntu24.04-neon"


class TestNeonDockerfile:
    @staticmethod
    def text():
        with open(os.path.join(ROOT, NEON_DOCKERFILE), encoding="utf-8") as handle:
            return handle.read()

    def packages(self):
        """The packages of the build dependencies, installed after the repository is set up"""
        text = self.text()
        return dockerfile_packages(text[text.index("bash /usr/local/sbin/neon-repo.sh"):])

    def test_it_installs_the_build_depends_of_the_neon_control_file_and_the_build_tools(self):
        tools = {"build-essential", "debhelper", "fakeroot"}
        assert self.packages() - tools == build_depends_of(read(NEON)) - {"debhelper-compat"}
        assert tools <= self.packages()

    def test_it_installs_the_qt6_packages_of_the_control_file_and_none_of_qt5(self):
        names = TestDockerfiles.qt_packages(self.packages())
        assert names == TestDockerfiles.qt_packages(build_depends_of(read(NEON)))
        assert names >= BUILD_DEPENDS["qt6"] and not names & BUILD_DEPENDS["qt5"]

    def test_it_is_not_the_dockerfile_of_a_release(self):
        with open(os.path.join(ROOT, "Dockerfile.ubuntu26.04"), encoding="utf-8") as handle:
            assert self.packages() != dockerfile_packages(handle.read())

    def test_it_installs_no_gnome_packages_and_no_rust(self):
        assert not [p for p in self.packages() if re.match(r"lib(gtk|nm|nma|ssl|dbus|openconnect|webkit)", p)]
        assert "rustup" not in self.text() and "cargo" not in self.text()

    def test_it_is_based_on_ubuntu_24_04(self):
        assert self.text().startswith("FROM ubuntu:24.04\n")

    def test_it_sets_up_the_neon_repository_with_the_script_before_it_installs_the_packages(self):
        text = self.text()
        assert "COPY .github/scripts/neon-repo.sh /usr/local/sbin/neon-repo.sh" in text
        assert text.index("COPY .github/scripts/neon-repo.sh") < text.index("bash /usr/local/sbin/neon-repo.sh")
        assert text.index("bash /usr/local/sbin/neon-repo.sh") < text.index("qt6-base-dev")
        # the script needs these to download and check the key
        before = text[:text.index("bash /usr/local/sbin/neon-repo.sh")]
        assert "ca-certificates" in before and "curl" in before and "gnupg" in before

    def test_the_address_and_the_key_of_the_repository_are_only_in_the_script(self):
        text = self.text()
        assert "neon.kde.org" not in text and "444DABCF" not in text.upper()

    def test_the_script_is_in_the_build_context(self):
        assert os.path.isfile(os.path.join(ROOT, ".github", "scripts", "neon-repo.sh"))
        with open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8") as handle:
            ignored = [l.strip() for l in handle if l.strip() and not l.startswith("#")]
        assert not [l for l in ignored if l.startswith(".github") or l in ("*.sh", "*")]

    def test_it_builds_as_the_builder_user_in_build_source(self):
        text = self.text()
        assert "WORKDIR /build/source\n" in text
        assert text.index("USER builder") < text.index("COPY --chown=builder:builder")
        assert "USER root" not in text.split("USER builder")[1]

    def test_the_parser_finds_the_packages_after_the_script(self):
        text = "COPY a b\nRUN apt-get install -y curl && bash /usr/local/sbin/neon-repo.sh\nRUN apt-get install -y \\\n    qt6-base-dev \\\n    cmake \\\n    && x\n"
        assert dockerfile_packages(text[text.index("bash /usr/local/sbin/neon-repo.sh"):]) == {"qt6-base-dev", "cmake"}


RULES = read("rules")


def make_variables(tmp_path, control_text):
    """{PLASMA_QT_MAJOR, PLASMA_PACKAGE, HAS_CORE} of debian/rules for a debian/control with this content"""
    lines = RULES.splitlines()
    first = next(i for i, l in enumerate(lines) if l.startswith("qt_listed"))
    last = next(i for i, l in enumerate(lines) if l.startswith("HAS_CORE"))
    (tmp_path / "debian").mkdir(exist_ok=True)
    (tmp_path / "debian" / "control").write_text(control_text, encoding="utf-8")
    names = ["PLASMA_QT_MAJOR", "PLASMA_PACKAGE", "HAS_CORE"]
    echo = "".join("\t@echo '%s=[$(%s)]'\n" % (n, n) for n in names)
    (tmp_path / "mini.mk").write_text("\n".join(lines[first:last + 1]) + "\nprint:\n" + echo, encoding="utf-8")
    result = subprocess.run(["make", "-s", "-f", "mini.mk", "print"], cwd=tmp_path, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return {l.split("=", 1)[0]: l.split("=", 1)[1][1:-1] for l in result.stdout.splitlines()}


def make_variable(tmp_path, control_text):
    """PLASMA_QT_MAJOR of debian/rules for a debian/control with this content"""
    return make_variables(tmp_path, control_text)["PLASMA_QT_MAJOR"]


def run_rules(tmp_path, control_text, target, dry_run=False):
    """Run a target of debian/rules for a debian/control with this content, with fakes for what the
    target runs: `make` (MAKE is a script that logs its arguments), cmake and cargo (they log theirs).
    Returns (CompletedProcess, the log lines)"""
    (tmp_path / "debian").mkdir(exist_ok=True)
    (tmp_path / "debian" / "control").write_text(control_text, encoding="utf-8")
    (tmp_path / "debian" / "rules").write_text(RULES, encoding="utf-8")
    (tmp_path / "external" / "GlobalProtect-openconnect").mkdir(parents=True, exist_ok=True)
    fakes = tmp_path / "fakes"
    fakes.mkdir(exist_ok=True)
    for name in ("fakemake", "cmake", "cargo"):
        (fakes / name).write_text('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, tmp_path / "log"))
        (fakes / name).chmod(0o755)
    command = ["make", "-f", "debian/rules", target, "MAKE=%s" % (fakes / "fakemake")]
    result = subprocess.run(command + (["-n"] if dry_run else []), cwd=tmp_path, capture_output=True, text=True,
                            env={"PATH": "%s:/usr/bin:/bin" % fakes}, timeout=30)
    log = (tmp_path / "log").read_text().splitlines() if (tmp_path / "log").exists() else []
    return result, log


# The Qt package in the syntaxes of Build-Depends: (old, new) replaces the entry of GOOD
SYNTAXES = {
    "qt5": [
        ("qtbase5-dev,", "qtbase5-dev (>= 5.15),"),
        ("qtbase5-dev,", "qtbase5-dev:native,"),
        ("qtbase5-dev,", "qtbase5-dev:native (>= 5.15),"),
        ("qtbase5-dev,", "qtbase5-dev | qt5-default,"),
        ("qtbase5-dev,", "qtbase5-dev ,"),
        ("qtbase5-dev,", "qtbase5-dev\t,"),
        ("               qtbase5-dev,\n", "\tqtbase5-dev,\n"),
        ("               curl\n", "               curl,\n               qtbase5-dev\n"),
        ("Build-Depends: debhelper-compat (= 13),\n               qtbase5-dev,\n",
         "Build-Depends: qtbase5-dev, debhelper-compat (= 13),\n"),
        ("Build-Depends: debhelper-compat (= 13),\n               qtbase5-dev,\n",
         "Build-Depends: qtbase5-dev (>= 5.15),\n               debhelper-compat (= 13),\n"),
        ("qtbase5-dev,", "debhelper | qtbase5-dev,"),
    ],
    "qt6": [
        ("qt6-base-dev,", "qt6-base-dev (>= 6.4),"),
        ("qt6-base-dev,", "qt6-base-dev:native,"),
        ("qt6-base-dev,", "qt6-base-dev:native (>= 6.4),"),
        ("qt6-base-dev,", "qt6-base-dev | qt6-base-dev-x,"),
        ("               curl\n", "               curl,\n               qt6-base-dev\n"),
        ("Build-Depends: debhelper-compat (= 13),\n               qt6-base-dev,\n",
         "Build-Depends: qt6-base-dev, debhelper-compat (= 13),\n"),
    ],
}


class TestRules:
    @pytest.mark.parametrize("name, qt", sorted(QT.items()))
    def test_the_qt_of_the_plugin_is_the_one_of_the_control_file(self, tmp_path, name, qt):
        assert make_variable(tmp_path, read(name)) == qt[-1]

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    def test_a_control_file_with_both_qt_or_none_gives_no_qt(self, tmp_path, qt):
        other = "qtbase5-dev" if qt == "qt6" else "qt6-base-dev"
        both = GOOD[qt].replace("               curl\n", "               %s,\n               curl\n" % other)
        assert make_variable(tmp_path, both) == ""
        neither = re.sub(r"^ +(qtbase5-dev|qt6-base-dev),\n", "", GOOD[qt], flags=re.M)
        assert make_variable(tmp_path, neither) == ""

    def test_a_package_name_that_only_contains_the_qt_package_is_not_taken_for_it(self, tmp_path):
        text = GOOD["qt6"].replace("qt6-base-dev,", "qt6-base-dev-tools-x,", 1)
        assert make_variable(tmp_path, text) == ""

    @pytest.mark.parametrize("qt, old, new", [(q, o, n) for q in ("qt5", "qt6") for o, n in SYNTAXES[q]])
    def test_the_qt_package_is_found_in_every_syntax_of_build_depends(self, tmp_path, qt, old, new):
        text = GOOD[qt]
        assert old in text, old
        assert make_variable(tmp_path, text.replace(old, new, 1)) == qt[-1]

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    @pytest.mark.parametrize("name", ["qtbase5-dev-tools", "qt6-base-dev-tools", "xqtbase5-dev", "xqt6-base-dev",
                                      "libqtbase5-dev", "qtbase5-dev2", "qt6-base-dev-x"])
    def test_a_longer_package_name_only_is_not_taken_for_the_qt_package(self, tmp_path, qt, name):
        text = re.sub(r"^( +)(qtbase5-dev|qt6-base-dev),$", r"\g<1>%s," % name, GOOD[qt], flags=re.M)
        assert name in text
        assert make_variable(tmp_path, text) == ""

    @pytest.mark.parametrize("qt", ["qt5", "qt6"])
    @pytest.mark.parametrize("entry, old, new", [
        ("{0} (>= 1),", "               curl\n", "               {entry}\n               curl\n"),
        ("{0}:native,", "               curl\n", "               {entry}\n               curl\n"),
        ("{0}", "               curl\n", "               curl,\n               {entry}\n"),
    ])
    def test_both_qt_packages_in_any_syntax_give_no_qt(self, tmp_path, qt, entry, old, new):
        other = "qtbase5-dev" if qt == "qt6" else "qt6-base-dev"
        both = GOOD[qt].replace(old, new.format(entry=entry.format(other)))
        assert other in both
        assert make_variable(tmp_path, both) == ""

    @pytest.mark.parametrize("field", ["Build-Depends-Indep", "Build-Conflicts", "Depends", "Recommends", "X-Build-Depends"])
    def test_a_qt_package_in_another_field_does_not_count(self, tmp_path, field):
        text = re.sub(r"^ +(qtbase5-dev|qt6-base-dev),\n", "", GOOD["qt5"], flags=re.M)
        text = text.replace("Standards-Version", "%s: qtbase5-dev (>= 5.15)\nStandards-Version" % field)
        assert make_variable(tmp_path, text) == ""

    def test_a_qt_package_in_the_continuation_of_the_next_field_does_not_count(self, tmp_path):
        text = re.sub(r"^ +(qtbase5-dev|qt6-base-dev),\n", "", GOOD["qt5"], flags=re.M)
        text = text.replace("Standards-Version: 4.6.2", "Standards-Version: 4.6.2\nX-Notes: none\n qtbase5-dev (>= 5.15),")
        assert make_variable(tmp_path, text) == ""

    def test_a_commented_out_qt_package_does_not_count(self, tmp_path):
        text = GOOD["qt5"].replace("               qtbase5-dev,\n", "#              qtbase5-dev,\n")
        assert text != GOOD["qt5"]
        assert make_variable(tmp_path, text) == ""

    def test_a_missing_qt_stops_the_build(self):
        assert 'test -n "$(PLASMA_QT_MAJOR)"' in RULES

    def test_a_missing_plasma_package_stops_the_build_and_the_install(self):
        assert RULES.count('test -n "$(PLASMA_PACKAGE)"') == 2

    def test_the_plugin_is_built_for_that_qt(self):
        assert "-DQT_MAJOR_VERSION=$(PLASMA_QT_MAJOR)" in RULES
        assert "pkg-config --exists Qt" not in RULES

    def test_the_plugin_goes_into_the_one_plasma_package_in_the_qt_directory(self):
        install = [l for l in RULES.splitlines() if "plasmanetworkmanagement_gpclientui" in l]
        assert install
        for line in install:
            if "$(CURDIR)" in line:
                assert "debian/$(PLASMA_PACKAGE)/usr/" in line, line
        assert "/qt$(PLASMA_QT_MAJOR)/plugins/plasma/network/vpn/" in RULES

    def test_the_multiarch_directory_is_never_hard_coded(self):
        assert "x86_64-linux-gnu" not in RULES.replace("x86_64-linux-gnu, aarch64", "")
        assert "$(DEB_HOST_MULTIARCH)" in RULES

    def test_only_qt5_installs_the_kservices_file(self):
        assert '[ "$(PLASMA_QT_MAJOR)" = 5 ]' in RULES
        assert RULES.count("kservices5") == 2  # the comment and the install line

    @pytest.mark.parametrize("name", sorted(QT))
    def test_every_package_directory_of_the_rules_is_a_package_of_the_control_file(self, name):
        packages = set(parse_control(read(name)))
        for directory in set(re.findall(r"debian/(network-manager-gpclient[a-z-]*)/", RULES)):
            assert directory in packages, directory

    def test_no_build_directory_per_qt(self):
        assert "build-5" not in RULES and "build-6" not in RULES



class TestRulesPlasmaOnly:
    """debian/control.ubuntu24.04-neon has no core package: the build makes the Plasma plugin only,
    into network-manager-gpclient-plasma-6. Every other control file builds everything."""

    @pytest.mark.parametrize("name, package", [
        (NEON, PLASMA6), ("control.ubuntu22.04", PLASMA), ("control.ubuntu24.04", PLASMA),
        ("control.ubuntu24.10", PLASMA), ("control.ubuntu26.04", PLASMA), ("control", PLASMA),
    ])
    def test_the_variables_follow_the_packages_of_the_control_file(self, tmp_path, name, package):
        variables = make_variables(tmp_path, read(name))

        assert variables["PLASMA_PACKAGE"] == package
        assert (variables["HAS_CORE"] == CORE) == (name != NEON)

    def test_the_neon_control_file_builds_qt6(self, tmp_path):
        assert make_variables(tmp_path, read(NEON))["PLASMA_QT_MAJOR"] == "6"

    @pytest.mark.parametrize("extra", [PLASMA, PLASMA + "-5", PLASMA + "-7"])
    def test_a_control_file_with_two_plasma_packages_has_no_plasma_package(self, tmp_path, extra):
        # the second of the two allowed names, or none of them
        text = read(NEON) + "\nPackage: %s\nArchitecture: any\nDescription: x\n y\n" % extra
        expected = "" if extra == PLASMA else PLASMA6
        assert make_variables(tmp_path, text)["PLASMA_PACKAGE"] == expected

    def test_a_package_name_that_only_starts_like_a_plasma_package_is_not_one(self, tmp_path):
        text = read(NEON).replace("Package: " + PLASMA6, "Package: " + PLASMA6 + "-extra")
        assert make_variables(tmp_path, text)["PLASMA_PACKAGE"] == ""

    def test_a_package_in_a_comment_or_a_description_is_not_a_package(self, tmp_path):
        text = "# Package: %s\n" % PLASMA + read(NEON).replace(" This package provides", " Package: %s\n This package provides" % PLASMA)
        assert make_variables(tmp_path, text)["PLASMA_PACKAGE"] == PLASMA6

    @pytest.mark.parametrize("name", [NEON])
    def test_the_build_makes_the_plasma_plugin_only(self, tmp_path, name):
        result, log = run_rules(tmp_path, read(name), "override_dh_auto_build")

        assert result.returncode == 0, result.stderr
        # cmake builds the plugin for Qt6, then make runs; no GNOME plugins, no submodules, no cargo
        assert log == ["cmake .. -DQT_MAJOR_VERSION=6", "fakemake "]

    @pytest.mark.parametrize("name", [n for n in sorted(QT) if n != "control"])
    def test_the_other_control_files_build_everything(self, tmp_path, name):
        result, log = run_rules(tmp_path, read(name), "override_dh_auto_build")

        assert result.returncode == 0, result.stderr
        assert log == ["fakemake init-submodules", "fakemake gnome-plugins",
                       "cmake .. -DQT_MAJOR_VERSION=%s" % QT[name][-1], "fakemake ",
                       "cargo build --release --bin gpclient --bin gpauth --bin gpservice"]

    @pytest.mark.parametrize("name", [NEON, "control.ubuntu24.04", "control.ubuntu26.04"])
    @pytest.mark.parametrize("extra", [PLASMA, PLASMA6])
    def test_two_plasma_packages_stop_the_build_before_the_plugin_is_built(self, tmp_path, name, extra):
        base = read(name)
        if "Package: " + extra in base:
            extra = PLASMA if extra == PLASMA6 else PLASMA6
        text = base + "\nPackage: %s\nArchitecture: any\nDescription: x\n y\n" % extra

        result, log = run_rules(tmp_path, text, "override_dh_auto_build")

        assert result.returncode != 0
        assert "exactly one of network-manager-gpclient-plasma and network-manager-gpclient-plasma-6" in result.stdout
        assert "cmake" not in " ".join(log) and "cargo" not in " ".join(log)

    @pytest.mark.parametrize("name", [NEON, "control.ubuntu24.04"])
    def test_no_plasma_package_stops_the_build_and_the_install(self, tmp_path, name):
        text = re.sub(r"Package: %s\n.*?(\n\n|\Z)" % PLASMA6 if name == NEON else r"Package: %s\n.*?(\n\n|\Z)" % PLASMA,
                      "", read(name), flags=re.S)
        assert ("Package: " + PLASMA) not in text.replace("Package: " + CORE, "")
        for target in ("override_dh_auto_build", "override_dh_auto_install"):
            result, log = run_rules(tmp_path, text, target)

            assert result.returncode != 0, target
            assert "exactly one of network-manager-gpclient-plasma and network-manager-gpclient-plasma-6" in result.stdout
            assert "cmake" not in " ".join(log)

    @staticmethod
    def commands(output):
        """The install, ln and mkdir commands of a dry run, continued lines joined"""
        joined = output.replace("\\\n", " ")
        return [l.strip() for l in joined.splitlines() if l.strip().startswith(("install ", "ln ", "mkdir "))]

    @classmethod
    def installed_directories(cls, output):
        return sorted(set(re.findall(r"/debian/([a-z0-9-]+)/", " ".join(cls.commands(output)))))

    def test_the_install_puts_the_plugin_into_the_plasma_6_package_only(self, tmp_path):
        result, _ = run_rules(tmp_path, read(NEON), "override_dh_auto_install", dry_run=True)

        assert result.returncode == 0, result.stderr
        assert self.installed_directories(result.stdout) == [PLASMA6]
        installs = [c for c in self.commands(result.stdout) if c.startswith("install ")]
        assert len(installs) == 2
        for command in installs:
            assert "/debian/%s/usr/lib/" % PLASMA6 in command
            assert "/qt6/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui." in command

    @pytest.mark.parametrize("name, package", [("control.ubuntu24.04", PLASMA), ("control.ubuntu26.04", PLASMA)])
    def test_the_install_of_the_other_control_files_fills_every_package(self, tmp_path, name, package):
        result, _ = run_rules(tmp_path, read(name), "override_dh_auto_install", dry_run=True)

        assert result.returncode == 0, result.stderr
        assert self.installed_directories(result.stdout) == sorted([CORE, CORE + "-gnome", package])

    def test_the_install_for_neon_has_no_core_files(self, tmp_path):
        result, _ = run_rules(tmp_path, read(NEON), "override_dh_auto_install", dry_run=True)

        text = " ".join(self.commands(result.stdout)).replace("plasmanetworkmanagement_gpclientui", "")
        text = text.replace(PLASMA6, "")
        for core_file in ("nm-gpclient-service", "gpclient", "gpauth", "browser-wrapper", "libnm-vpn-plugin",
                          "nm-gpclient.service", "90-gpclient-routing", "gpgui.desktop", "-gnome/"):
            assert core_file not in text, core_file

    def test_the_install_for_other_files_has_the_core_files(self, tmp_path):
        result, _ = run_rules(tmp_path, read("control.ubuntu26.04"), "override_dh_auto_install", dry_run=True)

        text = " ".join(self.commands(result.stdout))
        for core_file in ("nm-gpclient-service", "usr/bin/gpclient", "usr/bin/gpauth", "browser-wrapper",
                          "libnm-vpn-plugin-gpclient.so", "90-gpclient-routing"):
            assert core_file in text, core_file


MAINTAINER_SCRIPTS = ("preinst", "postinst", "prerm", "postrm", "config")


def generic_scripts(debian):
    """The maintainer scripts in `debian` that have no package name in front (debian/postinst): debhelper
    gives them to the first binary package of debian/control, which is the core package in every
    control file but the one for KDE neon, where it is network-manager-gpclient-plasma-6"""
    return [name for name in MAINTAINER_SCRIPTS if os.path.exists(os.path.join(debian, name))]


class TestMaintainerScripts:
    def test_debian_has_no_maintainer_script_without_a_package_name(self):
        assert generic_scripts(DEBIAN) == []

    @pytest.mark.parametrize("script", MAINTAINER_SCRIPTS)
    def test_a_generic_maintainer_script_is_found(self, tmp_path, script):
        (tmp_path / script).write_text("#!/bin/sh\n#DEBHELPER#\n")
        assert generic_scripts(str(tmp_path)) == [script]

    @pytest.mark.parametrize("script", MAINTAINER_SCRIPTS)
    def test_a_maintainer_script_of_a_package_is_not_a_generic_one(self, tmp_path, script):
        (tmp_path / (CORE + "." + script)).write_text("#!/bin/sh\n#DEBHELPER#\n")
        assert generic_scripts(str(tmp_path)) == []

    def test_the_maintainer_scripts_belong_to_the_core_package(self):
        found = sorted(n for n in os.listdir(DEBIAN) if n.endswith(tuple("." + s for s in MAINTAINER_SCRIPTS)))
        assert found == [CORE + ".postinst", CORE + ".postrm"]

    @pytest.mark.parametrize("script, kept", [
        ("postinst", ["org.freedesktop.DBus.ReloadConfig", "pkill -f \"$service_re\"", "/etc/nsswitch.conf",
                      "#DEBHELPER#"]),
        ("postrm", ["org.freedesktop.DBus.ReloadConfig", "pkill -f \"$service_re\"", "#DEBHELPER#"]),
    ])
    def test_the_core_package_keeps_its_maintainer_scripts(self, script, kept):
        text = read(CORE + "." + script)
        for part in kept:
            assert part in text, part

    @pytest.mark.parametrize("name", sorted([*QT, NEON]))
    def test_the_first_package_of_a_control_file_gets_no_maintainer_script(self, name):
        # the neon file has no core package: its first package, -plasma-6, must not inherit the core scripts
        first = next(p for p in re.findall(r"^Package:\s*(\S+)", read(name), re.M))
        scripts = [s for s in MAINTAINER_SCRIPTS if os.path.exists(os.path.join(DEBIAN, first + "." + s))]
        assert scripts == ([] if first != CORE else ["postinst", "postrm"])
        assert (first == CORE) == (name != NEON)


class TestPostinstLegacyWrapperFiles:
    """The postinst removes root-owned /tmp/edge-wrapper-<uid> leftovers of old wrappers"""

    def run_cleanup(self, tmp_path, *args):
        text = read(CORE + ".postinst")
        start = text.index("remove_legacy_edge_wrapper() {")
        func = text[start:text.index("\n}\n", start) + 3]
        subprocess.run(["sh", "-c", func + '\nremove_legacy_edge_wrapper "$@"', "sh", str(tmp_path), *args],
                       check=True)

    def make(self, tmp_path, names):
        for name in names:
            if name.endswith("/"):
                (tmp_path / name).mkdir()
                (tmp_path / name / "wrapper.log").write_text("x")
            else:
                (tmp_path / name).write_text("x")

    LEGACY = ["edge-wrapper-1000/", "edge-wrapper-1000.log", "edge-wrapper-0/"]
    OTHER = ["edge-wrapper-1000.XXXX/", "edge-wrapper-abc/", "edge-wrapper-10x.log", "edge-wrapper-/",
             "edge-wrapper-1000.log.old", "edge-wrapper-1000.txt", "other-1000/", "edge-wrapper-.log"]

    def test_entries_of_the_owner_are_removed(self, tmp_path):
        # The owner is the current user here, so that this runs without root
        self.make(tmp_path, self.LEGACY)
        self.run_cleanup(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
        assert os.listdir(tmp_path) == []

    def test_entries_of_another_owner_are_kept(self, tmp_path):
        # Without an argument the owner is root: as root, a file of "nobody" stays; as a user, none is root's
        self.make(tmp_path, self.LEGACY)
        if os.getuid() == 0:
            for path in [*tmp_path.iterdir(), *tmp_path.glob("*/*")]:
                os.chown(path, 65534, 65534)
        self.run_cleanup(tmp_path)
        assert sorted(os.listdir(tmp_path)) == sorted(n.rstrip("/") for n in self.LEGACY)

    @pytest.mark.skipif(os.getuid() != 0, reason="needs root to create root-owned entries")
    def test_root_owned_entries_are_removed_by_default(self, tmp_path):
        self.make(tmp_path, self.LEGACY)
        self.run_cleanup(tmp_path)
        assert os.listdir(tmp_path) == []

    @pytest.mark.parametrize("name", OTHER)
    def test_other_names_are_kept(self, tmp_path, name):
        self.make(tmp_path, [name])
        self.run_cleanup(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
        assert os.listdir(tmp_path) == [name.rstrip("/")]

    def test_the_wrong_type_is_kept(self, tmp_path):
        self.make(tmp_path, ["edge-wrapper-1000", "edge-wrapper-1001.log/"])
        self.run_cleanup(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
        assert sorted(os.listdir(tmp_path)) == ["edge-wrapper-1000", "edge-wrapper-1001.log"]

    def test_a_symlink_is_kept_and_its_target_too(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        (target / "precious").write_text("x")
        os.symlink(target, tmp_path / "edge-wrapper-1000")
        os.symlink(target / "precious", tmp_path / "edge-wrapper-1000.log")
        self.run_cleanup(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
        assert (target / "precious").exists()
        assert (tmp_path / "edge-wrapper-1000").is_symlink()
        assert (tmp_path / "edge-wrapper-1000.log").is_symlink()

    def test_an_empty_directory_is_fine(self, tmp_path):
        self.run_cleanup(tmp_path)
        assert os.listdir(tmp_path) == []

    def test_the_postinst_runs_the_cleanup_when_configuring(self):
        text = read(CORE + ".postinst")
        assert text.index("configure|reconfigure)") < text.index("\n        remove_legacy_edge_wrapper /tmp\n")


class TestChangelog:
    def top(self):
        text = read("changelog")
        return text[:text.index("\n -- ")]

    def test_the_plasma_package_and_its_qt_are_described(self):
        top = self.top()
        assert "network-manager-gpclient-plasma," in top
        assert "Qt5 on Ubuntu 22.04 and 24.04" in top and "Qt6" in top

    def test_the_former_test_packages_are_removed_not_replaced(self):
        top = " ".join(self.top().split())
        assert "network-manager-gpclient-plasma-5 and -plasma-6, were never released" in top
        assert "Breaks them, so apt full-upgrade removes them" in top
        assert "apt install network-manager-gpclient-plasma installs the editor" in top
        assert "are replaced by it" not in top

    def test_the_plasma_6_package_for_kde_neon_is_announced_once(self):
        top = " ".join(self.top().split())
        assert top.count("New network-manager-gpclient-plasma-6 for KDE neon (Ubuntu 24.04 with Plasma 6") == 1
        assert "built against the neon repository for amd64 only" in top
        assert "conflicts with the Qt5 network-manager-gpclient-plasma of Ubuntu 24.04" in top
        assert "PR #16" in top

    def test_the_plasma_6_package_is_not_announced_as_transitional(self):
        top = " ".join(self.top().split())
        assert "transitional" not in top and "replaces network-manager-gpclient-plasma-6" not in top

    def test_no_transitional_package_is_announced(self):
        top = self.top()
        assert "transitional" not in top
        assert "Ubuntu 26.04 has a" not in top
