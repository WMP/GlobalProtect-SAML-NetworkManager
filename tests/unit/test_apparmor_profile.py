"""
Tests for the AppArmor profile of the VPN service (config/apparmor/, #183): the profile
itself, and the Debian and Arch packaging that installs and loads it. The profile is in
complain mode: it logs and blocks nothing.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import glob
import os
import re

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NAME = "usr.lib.NetworkManager.nm-gpclient-service"
SERVICE = "/usr/lib/NetworkManager/nm-gpclient-service"
CORE = "network-manager-gpclient"


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def code(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def profile_problems(text):
    """The problems with an AppArmor profile text (an empty list: none)"""
    text = code(text)
    problems = []
    head = re.search(r"^profile\s+(\S+)\s+(\S+)\s+flags=\(([^)]*)\)\s*\{", text, re.M)
    if not head:
        return ["no profile line with flags"]
    if head.group(2) != SERVICE:
        problems.append(f"attached to {head.group(2)}, not {SERVICE}")
    if head.group(3).split(",") != ["complain"]:
        problems.append(f"flags {head.group(3)}, not complain")
    if re.search(r"^\s*(audit\s+)?deny\b", text, re.M):
        problems.append("a deny rule")
    if "enforce" in text:
        problems.append("enforce")
    if not re.search(r"^\s*/usr/bin/gpauth ux,$", text, re.M):
        problems.append("gpauth does not run unconfined (ux)")
    return problems


def test_the_profile_is_in_complain_mode_and_attached_to_the_service():
    assert profile_problems(read("config/apparmor/" + NAME)) == []


def test_the_profile_path_is_where_every_package_installs_the_service():
    # debian/rules and the PKGBUILD name it in full; the spec as %{_prefix}/lib/...
    for path in ("debian/rules", "arch/PKGBUILD", "rpm/network-manager-gpclient.spec"):
        assert "lib/NetworkManager/nm-gpclient-service\n" in read(path) or "lib/NetworkManager/nm-gpclient-service \\" in read(path), path
    assert SERVICE in read("config/apparmor/" + NAME)


@pytest.mark.parametrize(
    "old, new",
    [
        ("flags=(complain)", "flags=(enforce)"),
        ("flags=(complain)", "flags=(complain,enforce)"),
        ("flags=(complain)", "flags=(attach_disconnected)"),
        ("profile nm-gpclient-service /usr/lib/NetworkManager/nm-gpclient-service", "profile nm-gpclient-service /usr/libexec/gpclient/nm-gpclient-service"),
        ("  /usr/bin/gpauth ux,\n", "  /usr/bin/gpauth Ux,\n"),
        ("  /usr/bin/gpauth ux,\n", "  /usr/bin/gpauth ix,\n"),
        ("  /usr/bin/gpauth ux,\n", "  /usr/bin/gpauth ux,\n  deny /etc/shadow r,\n"),
        ("  /usr/bin/gpauth ux,\n", "  /usr/bin/gpauth ux,\n  audit deny /etc/shadow r,\n"),
        ("  /usr/bin/gpauth ux,\n", "  /usr/bin/gpauth ux,\n  # enforce\n  enforce,\n"),
    ],
)
def test_the_profile_check_rejects_a_changed_profile(old, new):
    text = read("config/apparmor/" + NAME)
    assert old in text
    assert profile_problems(text.replace(old, new))


def test_the_profile_check_rejects_a_text_that_is_no_profile():
    assert profile_problems("# profile x /usr/lib/NetworkManager/nm-gpclient-service flags=(complain) {\n")


# --- Debian ---


def rules_problems(rules):
    """The problems with the AppArmor lines of debian/rules"""
    rules = code(rules)
    problems = []
    install = re.search(
        r"install -D -m 644 config/apparmor/(\S+) \\\n\s+\$\(CURDIR\)/debian/network-manager-gpclient/etc/apparmor\.d/(\S+)\n",
        rules,
    )
    if not install or install.group(1) != NAME or install.group(2) != NAME:
        problems.append("the profile is not installed to etc/apparmor.d")
    elif not re.search(r"^ifneq \(\$\(HAS_CORE\),\)\n(?:(?!^endif).*\n)*?.*" + re.escape(install.group(0)), rules, re.M):
        problems.append("the install is not in the HAS_CORE block")
    override = re.search(r"^override_dh_installdeb:\n((?:\t.*\n|ifneq.*\n|endif\n)*)", rules, re.M)
    if not override:
        return problems + ["no override_dh_installdeb"]
    lines = override.group(1).splitlines()
    call = f"\tdh_apparmor --profile-name={NAME} -p{CORE}"
    if call not in lines:
        problems.append("dh_apparmor has no profile name of the installed file or no -p" + CORE)
    elif lines != ["ifneq ($(HAS_CORE),)", call, "endif", "\tdh_installdeb"]:
        problems.append("dh_apparmor is not under HAS_CORE and before dh_installdeb")
    return problems


def core_controls():
    names = [os.path.basename(p) for p in sorted(glob.glob(os.path.join(ROOT, "debian", "control*")))]
    return [n for n in names if re.search(r"^Package:\s+%s\s*$" % CORE, read("debian/" + n), re.M)]


def control_problems(text):
    """The problems of a control file that has the core package: dh-apparmor in Build-Depends"""
    fields = re.search(r"^Build-Depends:(.*?)^\S", text, re.M | re.S)
    names = {item.split("(")[0].split("|")[0].strip() for item in (fields.group(1) if fields else "").split(",")}
    return [] if "dh-apparmor" in names else ["no dh-apparmor in Build-Depends"]


def test_debian_rules_installs_the_profile_and_loads_it_with_dh_apparmor():
    assert rules_problems(read("debian/rules")) == []


@pytest.mark.parametrize(
    "old, new",
    [
        ("etc/apparmor.d/" + NAME, "etc/apparmor.d/other"),
        ("install -D -m 644 config/apparmor/", "# install -D -m 644 config/apparmor/"),
        ("dh_apparmor --profile-name=" + NAME, "dh_apparmor --profile-name=other"),
        ("-pnetwork-manager-gpclient\n", "\n"),
        ("override_dh_installdeb:", "override_dh_other:"),
        ("\tdh_installdeb\n", "\tdh_installdeb\n\tdh_apparmor --profile-name=" + NAME + " -p" + CORE + "\n"),
    ],
)
def test_the_rules_check_rejects_a_change(old, new):
    rules = read("debian/rules")
    assert old in rules
    assert rules_problems(rules.replace(old, new, 1))


def test_the_rules_check_rejects_an_install_outside_the_core_block():
    rules = read("debian/rules")
    block = re.search(r"\t# AppArmor profile of the service.*?\n\n", rules, re.S).group(0)
    assert rules_problems(rules.replace(block, "").replace("override_dh_clean:", block + "override_dh_clean:", 1))


def test_every_control_file_with_the_core_package_needs_dh_apparmor():
    names = core_controls()
    # the neon control file builds the Plasma package only
    assert set(names) == {"control", "control.ubuntu22.04", "control.ubuntu24.04", "control.ubuntu26.04"}
    for name in names:
        assert control_problems(read("debian/" + name)) == [], name


@pytest.mark.parametrize("name", ["control", "control.ubuntu22.04", "control.ubuntu24.04", "control.ubuntu26.04"])
def test_the_control_check_rejects_a_file_without_dh_apparmor(name):
    text = read("debian/" + name)
    assert "               dh-apparmor,\n" in text
    assert control_problems(text.replace("               dh-apparmor,\n", ""))
    assert control_problems(text.replace("dh-apparmor", "dh-apparmor-x"))


def test_the_neon_control_file_has_no_core_package_and_no_dh_apparmor():
    assert "dh-apparmor" not in read("debian/control.ubuntu24.04-neon")
    assert "control.ubuntu24.04-neon" not in core_controls()


def test_the_profile_is_no_conffile_of_the_maintscript():
    # the conffile is new: the maintscript removes the old D-Bus policy only
    assert "apparmor" not in read("debian/network-manager-gpclient.maintscript")
