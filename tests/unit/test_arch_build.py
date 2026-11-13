"""
Tests for arch/PKGBUILD, arch/network-manager-gpclient.install, arch/build.sh and
arch/install-test.sh (the parts that run without a container).

arch/build.sh runs in a copy of the arch/ and rpm/ directories with a fake docker and a
fake curl in PATH: the tests check which container it starts and with which options, and
what it accepts in the output. The real build is done by CI (and by hand) in the
archlinux container. Every positive test has a negative counterpart: a half-set or
non-numeric PR number, a download with another sha256, a build that makes the wrong
packages, a hook that would stop another process.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import hashlib
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ARCH_DIR = os.path.join(ROOT, "arch")
PACKAGES = ["network-manager-gpclient", "network-manager-gpclient-gnome", "network-manager-gpclient-plasma"]


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


# --- arch/PKGBUILD ---


def pkgbuild_function(name):
    """The body of a function of the PKGBUILD"""
    match = re.search(r"^%s\(\) \{\n(.*?)^\}\n" % re.escape(name), read("arch/PKGBUILD"), re.M | re.S)
    assert match, name
    return match.group(1)


def test_pkgbuild_is_a_split_package_of_the_same_three_packages():
    text = read("arch/PKGBUILD")
    assert "pkgbase=network-manager-gpclient\n" in text
    assert "pkgname=(network-manager-gpclient network-manager-gpclient-gnome network-manager-gpclient-plasma)\n" in text
    for name in PACKAGES:
        assert re.search(r"^package_%s\(\) \{$" % re.escape(name), text, re.M)
    assert len(re.findall(r"^package_\S+\(\) \{$", text, re.M)) == 3


def test_pkgbuild_is_x86_64_only_without_a_debug_package():
    text = read("arch/PKGBUILD")
    assert "arch=(x86_64)\n" in text
    assert "options=(!debug !lto)\n" in text


def test_pkgbuild_takes_the_version_from_the_build_script_in_ci_mode():
    text = read("arch/PKGBUILD")
    assert "pkgver=${PROJECT_VERSION}${PR_SUFFIX:-}\n" in text
    assert "pkgrel=$DEBIAN_RELEASE\n" in text
    # the source archive is named after the version without the pull request marker
    assert '_dir=network-manager-gpclient-$PROJECT_VERSION\n' in text and '"$_dir.tar.gz"' in text
    # and stops when the pieces of the CI mode are not all there
    assert ': "${DEBIAN_RELEASE:?' in text and '"${SDBUS_SHA256:?}"' in text


def test_pkgbuild_bundles_python_sdbus_checked_against_the_pinned_sha256():
    text = read("arch/PKGBUILD")
    assert '"sdbus-$_sdbus_version.tar.gz"' in text
    assert 'sha256sums=(SKIP "$_sdbus_sha256")' in text  # CI mode
    assert 'sha256sums=(SKIP SKIP SKIP SKIP "$_sdbus_sha256")' in text  # user mode
    assert "python-sdbus" not in re.search(r"^makedepends=\(.*?^\)", text, re.M | re.S).group(0)
    assert "--no-index" in text


# --- user mode: the values written in the PKGBUILD do not drift from the rest of the repository ---


def pkgbuild_value(text, name):
    """The value of `name=value` in the user mode branch (after `else`) of the PKGBUILD"""
    user = text.split("\nelse\n", 1)[1]
    match = re.search(r"^  %s=(\S+)$" % re.escape(name), user, re.M)
    assert match, name
    return match.group(1)


def user_mode_sources(text):
    return re.findall(r'^    "(\S+?)::git\+(\S+?)\.git#(tag|commit)=(\S+?)"$', text.split("\nelse\n", 1)[1], re.M)


def changelog_upstream_version(text):
    return re.match(r"^\S+ \(([^)]+)\) ", text).group(1).rsplit("-", 1)[0]


def rpm_source(name, text=None):
    return re.search(r"^%s=(\S+)$" % name, text or read("rpm/sources"), re.M).group(1)


def all_gitmodules():
    """.gitmodules of the project and of the submodule (which has the two nested ones)"""
    inner = os.path.join(ROOT, "external", "GlobalProtect-openconnect", ".gitmodules")
    if not os.path.isfile(inner):
        pytest.skip("the submodules are not checked out")
    with open(inner, encoding="utf-8") as handle:
        return read(".gitmodules") + "\n" + handle.read()


def gitlink(repo, path):
    out = subprocess.run(["git", "ls-tree", "HEAD", path], cwd=repo, capture_output=True, text=True, check=True).stdout
    return out.split()[2]


def gitmodules_urls(text):
    return dict(re.findall(r'\[submodule "([^"]+)"\]\n\s*path = \S+\n\s*url = (\S+)', text))


def user_mode_problems(pkgbuild, changelog, sources, gitmodules, links):
    """What in the user mode of `pkgbuild` differs from the changelog, rpm/sources, .gitmodules and
    the gitlinks (`links`: {path: commit})"""
    problems = []
    if pkgbuild_value(pkgbuild, "pkgver") != changelog_upstream_version(changelog):
        problems.append("pkgver differs from debian/changelog")
    if pkgbuild_value(pkgbuild, "_sdbus_version") != rpm_source("SDBUS_VERSION", sources):
        problems.append("the sdbus version differs from rpm/sources")
    if pkgbuild_value(pkgbuild, "_sdbus_sha256") != rpm_source("SDBUS_SHA256", sources):
        problems.append("the sdbus sha256 differs from rpm/sources")
    urls = gitmodules_urls(gitmodules)
    expected = {  # source name -> (url of .gitmodules, path of the gitlink)
        "GlobalProtect-openconnect": ("external/GlobalProtect-openconnect", "external/GlobalProtect-openconnect"),
        "openconnect": ("crates/openconnect/deps/openconnect", "crates/openconnect/deps/openconnect"),
        "libxml2": ("crates/openconnect/deps/libxml2", "crates/openconnect/deps/libxml2"),
    }
    found = {name: (url, kind, ref) for name, url, kind, ref in user_mode_sources(pkgbuild)}
    if set(found) != {"network-manager-gpclient", *expected}:
        problems.append("the git sources are not the project and its three submodules")
    for name, (module, path) in expected.items():
        if name not in found:
            continue
        url, kind, ref = found[name]
        if kind != "commit" or ref != links.get(path):
            problems.append(f"{name} is not pinned to the commit of the submodule")
        if url + ".git" != urls.get(module):
            problems.append(f"the URL of {name} differs from .gitmodules")
    project = found.get("network-manager-gpclient")
    if not project or project[1:] != ("tag", "v$pkgver"):
        problems.append("the project is not taken from the tag v$pkgver")
    return problems


def real_links():
    sub = os.path.join(ROOT, "external", "GlobalProtect-openconnect")
    if not os.path.isdir(os.path.join(sub, ".git")) and not os.path.isfile(os.path.join(sub, ".git")):
        pytest.skip("the submodules are not checked out")
    top = "external/GlobalProtect-openconnect"
    return {
        top: gitlink(ROOT, top),
        "crates/openconnect/deps/openconnect": gitlink(sub, "crates/openconnect/deps/openconnect"),
        "crates/openconnect/deps/libxml2": gitlink(sub, "crates/openconnect/deps/libxml2"),
    }


def gitlink_in_the_tree():
    """The gitlink of the top submodule in the index even without a checkout (a copy without .git)"""
    return subprocess.run(["git", "ls-tree", "HEAD", "external/GlobalProtect-openconnect"], cwd=ROOT,
                          capture_output=True, text=True).returncode == 0


def test_user_mode_values_equal_the_changelog_rpm_sources_gitmodules_and_gitlinks():
    if not gitlink_in_the_tree():
        pytest.skip("not a git checkout")
    problems = user_mode_problems(read("arch/PKGBUILD"), read("debian/changelog"), read("rpm/sources"),
                                  all_gitmodules(), real_links())
    assert problems == []


# the version of the top debian/changelog entry, so the drift test needs no change per release
TOP_VERSION = re.match(r"\S+ \(([^)]+)\)", read("debian/changelog")).group(1)


@pytest.mark.parametrize("old, new, expected", [
    ("pkgver=" + TOP_VERSION + "\n", "pkgver=1.9.9\n", "pkgver"),
    ("_sdbus_version=0.14.3", "_sdbus_version=0.14.2", "sdbus version"),
    ("_sdbus_sha256=4ec4", "_sdbus_sha256=0000", "sdbus sha256"),
    ("#commit=915c130c", "#commit=015c130c", "GlobalProtect-openconnect is not pinned"),
    ("#commit=33b3d7a0", "#commit=03b3d7a0", "openconnect is not pinned"),
    ("#commit=f8a8c1f5", "#commit=08a8c1f5", "libxml2 is not pinned"),
    ("github.com/yuezk/openconnect.git", "github.com/yuezk/openconnect2.git", "URL of openconnect"),
    ("github.com/GNOME/libxml2.git", "github.com/GNOME/libxml3.git", "URL of libxml2"),
    ("#tag=v$pkgver", "#commit=abc", "tag"),
])
def test_user_mode_drift_is_found(old, new, expected):
    pkgbuild = read("arch/PKGBUILD")
    assert old in pkgbuild
    links = {"external/GlobalProtect-openconnect": "915c130c708582a61bbd5ccf747f94347d732364",
             "crates/openconnect/deps/openconnect": "33b3d7a01d2fdcbc68652d63d69387ed599c244b",
             "crates/openconnect/deps/libxml2": "f8a8c1f59db355b46962577e7b74f1a1e8149dc6"}
    gitmodules = all_gitmodules()
    # with the right values the check is clean ...
    assert user_mode_problems(pkgbuild, read("debian/changelog"), read("rpm/sources"), gitmodules, links) == []
    # ... and it finds the change
    problems = user_mode_problems(pkgbuild.replace(old, new, 1), read("debian/changelog"), read("rpm/sources"),
                                  gitmodules, links)
    assert any(expected in problem for problem in problems), problems


def test_user_mode_needs_no_environment_and_ci_mode_ignores_nothing():
    text = read("arch/PKGBUILD")
    user = text.split("\nelse\n", 1)[1].split("\nfi\n", 1)[0]
    assert "$PROJECT_VERSION" not in user and "SDBUS" not in user.replace("_sdbus", "")
    # cargo fetch in prepare() and an offline build
    assert "cargo fetch --locked" in text and "vendor_flags=(--offline)" in text


def test_pkgbuild_core_package_conflicts_with_the_aur_packages_of_upstream():
    body = pkgbuild_function("package_network-manager-gpclient")
    assert "conflicts=(globalprotect-openconnect globalprotect-openconnect-git)" in body
    assert "install=network-manager-gpclient.install" in body
    # the desktop packages need the core package of the same version
    for suffix in ("gnome", "plasma"):
        assert '"network-manager-gpclient=$pkgver-$pkgrel"' in pkgbuild_function(f"package_network-manager-gpclient-{suffix}")


def test_pkgbuild_depends_on_what_the_binaries_link():
    core = pkgbuild_function("package_network-manager-gpclient")
    for dependency in ("networkmanager", "openconnect", "vpnc", "webkit2gtk-4.1", "libsoup3", "gtk3", "openssl", "xdg-utils"):
        assert re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(dependency), core), dependency
    assert "plasma-nm" in pkgbuild_function("package_network-manager-gpclient-plasma")
    assert "libnma-gtk4" in pkgbuild_function("package_network-manager-gpclient-gnome")


def python_pin_problems(body):
    """What is wrong with the Python pin in the core package function"""
    problems = []
    if "sys.version_info[:2]" not in body:
        problems.append("the minor version is not taken from the Python that built the package")
    if 'depends+=("python>=$pyver" "python<${pyver%.*}.$((${pyver#*.} + 1))")' not in body:
        problems.append("python is not pinned to the minor version")
    return problems


def test_pkgbuild_pins_the_python_minor_version_of_the_bundled_sdbus():
    assert python_pin_problems(pkgbuild_function("package_network-manager-gpclient")) == []


@pytest.mark.parametrize("old, new", [
    ('depends+=("python>=$pyver" "python<${pyver%.*}.$((${pyver#*.} + 1))")', ""),  # plain python only
    ('"python<${pyver%.*}.$((${pyver#*.} + 1))"', ""),  # no upper bound
    ('"python>=$pyver" ', ""),  # no lower bound
    ("sys.version_info[:2]", "sys.version_info[:1]"),
])
def test_pkgbuild_with_an_unpinned_python_is_found(old, new):
    body = pkgbuild_function("package_network-manager-gpclient")
    assert old in body
    assert python_pin_problems(body.replace(old, new, 1)) != []


def test_pkgbuild_pin_gives_a_valid_range():
    # the shell arithmetic of the pin, run for a few versions: 3.14 -> python>=3.14 python<3.15
    for pyver, upper in (("3.14", "3.15"), ("3.9", "3.10"), ("3.19", "3.20")):
        out = subprocess.run(["bash", "-c", 'pyver=%s; echo "${pyver%%.*}.$((${pyver#*.} + 1))"' % pyver],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == upper


APPARMOR_PROFILE = "/etc/apparmor.d/usr.lib.NetworkManager.nm-gpclient-service"


def spec_config_files():
    return sorted(re.findall(r"^%config\(noreplace\) (\S+)$", read("rpm/network-manager-gpclient.spec"), re.M))


def test_pkgbuild_backs_up_the_config_files_of_the_spec():
    text = read("arch/PKGBUILD")
    backup = re.search(r"^  backup=\((.*?)\)$", pkgbuild_function("package_network-manager-gpclient"), re.M | re.S)
    assert backup
    spec = [path.replace("%{_sysconfdir}", "etc") for path in spec_config_files()]
    assert len(spec) == 4
    # and the AppArmor profile, which only the Debian and Arch packages ship
    assert sorted(backup.group(1).split()) == sorted(spec + [APPARMOR_PROFILE.lstrip("/")])
    assert "backup=" not in pkgbuild_function("package_network-manager-gpclient-gnome") and text


def test_pkgbuild_with_another_backup_list_is_found():
    spec = [path.replace("%{_sysconfdir}", "etc") for path in spec_config_files()]
    body = pkgbuild_function("package_network-manager-gpclient")
    changed = body.replace(" etc/vpnc/reconnect.d/90-gpclient-notify", "", 1)
    backup = re.search(r"^  backup=\((.*?)\)$", changed, re.M | re.S).group(1).split()
    assert sorted(backup) != sorted(spec)


def installs_the_apparmor_profile(body):
    return ("install -D -m 0644 config/apparmor/usr.lib.NetworkManager.nm-gpclient-service \\\n"
            '    "$pkgdir%s"' % APPARMOR_PROFILE) in body


def test_pkgbuild_installs_the_apparmor_profile_as_a_config_file_and_suggests_apparmor():
    body = pkgbuild_function("package_network-manager-gpclient")
    assert installs_the_apparmor_profile(body)
    assert "optdepends=('apparmor: AppArmor profile of the VPN service (complain mode)')" in body
    assert "apparmor" not in re.search(r"^  depends=\((.*?)\)$", body, re.M | re.S).group(1)
    assert APPARMOR_PROFILE in read("arch/install-test.sh")


def test_pkgbuild_without_the_apparmor_profile_is_found():
    body = pkgbuild_function("package_network-manager-gpclient")
    assert not installs_the_apparmor_profile(body.replace("$pkgdir/etc/apparmor.d/", "$pkgdir/etc/other/"))
    assert not installs_the_apparmor_profile(body.replace("install -D -m 0644 config/apparmor/", "# "))


def test_pkgbuild_builds_what_the_spec_builds():
    text = read("arch/PKGBUILD")
    spec = read("rpm/network-manager-gpclient.spec")
    for command in ("make -C plugins/gnome all", "-DQT_MAJOR_VERSION=6", "env -u RUSTFLAGS cargo auditable build --locked",
                    "--bin gpclient --bin gpauth", "ctest --output-on-failure --no-tests=error"):
        assert command in text and command in spec, command


def installed_files(text):
    """(mode, source, destination as a path under the root) of every `install -D` of the text
    (`%{buildroot}%{_prefix}/lib/...` of the spec and `"$pkgdir/usr/lib/..."` of the PKGBUILD alike)"""
    text = text.replace("\\\n", " ")
    found = []
    for mode, source, dest in re.findall(r"install -D -m (\d+) (\S+)\s+(\S+)", text):
        dest = dest.strip('"').replace("%{buildroot}", "").replace("$pkgdir", "")
        dest = dest.replace("%{_prefix}", "/usr").replace("%{_sysconfdir}", "/etc").replace("%{_bindir}", "/usr/bin")
        dest = dest.replace("%{_unitdir}", "/usr/lib/systemd/system").replace("%{_datadir}", "/usr/share")
        dest = dest.replace("%{_libdir}", "/usr/lib")
        found.append((mode, source.strip('"'), dest))
    return found


def test_pkgbuild_installs_what_the_spec_installs_to_the_same_places():
    spec = installed_files(read("rpm/network-manager-gpclient.spec"))
    arch = installed_files(read("arch/PKGBUILD"))
    # the spec's SELinux policy has no counterpart on Arch (checked below); the sdbus wheel is
    # installed by pip in both
    spec = [item for item in spec if "selinux" not in item[2]]
    # the licenses and the README are %license and %doc in the spec, installed by hand here
    arch = [item for item in arch if not item[2].startswith(("/usr/share/licenses/", "/usr/share/doc/"))]
    # the AppArmor profile has no counterpart in the spec (SELinux there); see the test below
    arch = [item for item in arch if item[2] != APPARMOR_PROFILE]
    assert len(spec) >= 20
    assert sorted(spec) == sorted(arch)


def test_pkgbuild_has_no_selinux_and_no_nsswitch_change():
    text = read("arch/PKGBUILD") + read("arch/network-manager-gpclient.install")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "selinux" not in code.lower()
    assert "nsswitch" not in code


def test_pkgbuild_has_the_symlinks_of_the_spec():
    text = read("arch/PKGBUILD")
    assert "ln -s /usr/lib/NetworkManager/nm-gpclient-service \\\n    \"$pkgdir/usr/libexec/NetworkManager/gpclient-nm-service\"" in text
    assert "ln -s /usr/lib/NetworkManager/libnm-vpn-plugin-gpclient-editor.so \\\n    \"$pkgdir/usr/lib/libnm-gpclient-properties\"" in text
    assert "libnm-gpclient-properties" in pkgbuild_function("package_network-manager-gpclient-gnome")
    assert "libnm-gpclient-properties" not in pkgbuild_function("package_network-manager-gpclient")


def test_pkgbuild_puts_each_translation_domain_in_its_own_package():
    assert "install-mo MO_DOMAIN=NetworkManager-gpclient" in pkgbuild_function("package_network-manager-gpclient")
    assert "install-mo MO_DOMAIN=plasmanetworkmanagement_gpclientui" in pkgbuild_function("package_network-manager-gpclient-plasma")
    assert "plasmanetworkmanagement_gpclientui" not in pkgbuild_function("package_network-manager-gpclient")


def test_pkgbuild_ships_the_license_texts_and_the_gpl_text_of_gpclient():
    body = pkgbuild_function("package_network-manager-gpclient")
    assert "usr/share/licenses/$pkgname/LICENSE" in body and "LICENSE.GPL-3.0" in body and "COPYING.LESSER" in body
    assert "cp -p external/GlobalProtect-openconnect/LICENSE LICENSE.GPL-3.0" in pkgbuild_function("prepare")


def test_pkgbuild_strips_the_rust_binaries_like_the_spec():
    # makepkg.conf can export RUSTFLAGS, which overrides upstream's release profile (strip = true)
    assert "env -u RUSTFLAGS cargo auditable build --locked" in read("arch/PKGBUILD")
    assert "CARGO_PROFILE_RELEASE_STRIP" not in read("arch/PKGBUILD")


# --- arch/network-manager-gpclient.install ---


def run_hook(tmp_path, call, present=True, apparmor=True):
    """Run a hook function of the .install file with a fake dbus-send, pkill and systemctl"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls"
    for name in ("dbus-send", "pkill", "systemctl", "apparmor_parser"):
        (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n')
        (bin_dir / name).chmod(0o755)
    # the hooks test for /usr/bin/dbus-send and /usr/bin/pkill: use stand-ins of the same name
    script = (os.path.join(ARCH_DIR, "network-manager-gpclient.install"))
    where = str(bin_dir) if present else str(tmp_path / "nothing-here")
    # the AppArmor file system: a directory when AppArmor is active
    (tmp_path / "securityfs").mkdir(exist_ok=True)
    kernel = str(tmp_path / "securityfs") if apparmor else str(tmp_path / "no-apparmor")
    text = open(script, encoding="utf-8").read().replace("/usr/bin/dbus-send", where + "/dbus-send") \
        .replace("/usr/bin/apparmor_parser", where + "/apparmor_parser").replace("/sys/kernel/security/apparmor", kernel) \
        .replace("/usr/bin/pkill", where + "/pkill").replace("[ -d /run/systemd/system ]", "true" if present else "false")
    (tmp_path / "hook.sh").write_text(text)
    result = subprocess.run(["sh", "-c", f'. "{tmp_path}/hook.sh"; {call}'], capture_output=True, text=True,
                            env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, check=False)
    return result, (log.read_text().splitlines() if log.exists() else [])


@pytest.mark.parametrize("hook", ["post_install", "post_upgrade"])
def test_the_hooks_reload_dbus_and_stop_the_old_service_and_never_fail(tmp_path, hook):
    result, calls = run_hook(tmp_path, hook)

    assert result.returncode == 0, result.stderr
    assert any(c.startswith("dbus-send --system") and "org.freedesktop.DBus.ReloadConfig" in c for c in calls)
    assert len([c for c in calls if c.startswith("pkill -f")]) == 2


def test_the_removal_stops_the_service_and_reloads_dbus_and_never_fails(tmp_path):
    result, calls = run_hook(tmp_path, "pre_remove; post_remove")

    assert result.returncode == 0, result.stderr
    assert "systemctl stop nm-gpclient.service" in calls
    assert any("ReloadConfig" in c for c in calls)
    assert not any(c.startswith("pkill") for c in calls)


@pytest.mark.parametrize("hook", ["post_install", "post_upgrade"])
def test_the_install_hooks_load_the_apparmor_profile_when_apparmor_is_active(tmp_path, hook):
    result, calls = run_hook(tmp_path, hook)

    assert result.returncode == 0, result.stderr
    assert f"apparmor_parser -r -W {APPARMOR_PROFILE}" in calls


def test_the_removal_unloads_the_apparmor_profile_when_apparmor_is_active(tmp_path):
    result, calls = run_hook(tmp_path, "pre_remove")

    assert result.returncode == 0, result.stderr
    assert f"apparmor_parser -R {APPARMOR_PROFILE}" in calls


@pytest.mark.parametrize("call", ["post_install", "post_upgrade", "pre_remove"])
def test_the_hooks_do_not_call_apparmor_parser_without_apparmor(tmp_path, call):
    # AppArmor is not active (no /sys/kernel/security/apparmor): the hooks succeed and do not call it
    result, calls = run_hook(tmp_path, call, apparmor=False)

    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("apparmor_parser") for c in calls)
    assert calls


def test_the_hooks_do_nothing_without_the_tools(tmp_path):
    # no dbus-send and no pkill (a minimal system): the hooks still succeed and call nothing
    result, calls = run_hook(tmp_path, "post_install; post_upgrade; post_remove", present=False)

    assert result.returncode == 0, result.stderr
    assert calls == []


def test_the_pkill_pattern_matches_the_service_only():
    hook = read("arch/network-manager-gpclient.install")
    # Process name is cut to 15 characters: pkill -x can never match
    assert "pkill -x" not in hook
    pattern = re.search(r"pkill -f '(\^[^']*nm-gpclient-service)'", hook).group(1)
    assert pattern == re.search(r"pkill -f '(\^[^']*nm-gpclient-service)'", read("rpm/network-manager-gpclient.spec")).group(1)
    regex = re.compile(pattern)
    for line in ("/usr/bin/python3 /usr/lib/NetworkManager/nm-gpclient-service",
                 "python3 /usr/lib/NetworkManager/nm-gpclient-service",
                 "/usr/bin/python3.14 /usr/lib/NetworkManager/nm-gpclient-service"):
        assert regex.match(line), line
    for line in ("pkill -f nm-gpclient-service", "vim /usr/lib/NetworkManager/nm-gpclient-service",
                 "/usr/bin/perl /usr/lib/NetworkManager/nm-gpclient-service",
                 "/bin/sh /var/tmp/hook.sh"):
        assert not regex.match(line), line


# --- arch/build.sh ---

FAKE_DOCKER = """\
#!/bin/bash
echo "$@" >> "$FAKE_LOG"
out=
for arg in "$@"; do
    case "$arg" in *:/out) out=${arg%:/out} ;; esac
done
if [ -n "$out" ]; then
    for name in $FAKE_PKGS; do : > "$out/$name"; done
fi
exit "${FAKE_DOCKER_RC:-0}"
"""

FAKE_CURL = """\
#!/bin/bash
# curl -fsSL --retry 3 -o FILE URL
while [ $# -gt 0 ]; do
    case "$1" in -o) shift; printf '%s' "$FAKE_SDBUS" > "$1" ;; esac
    shift
done
"""

SDBUS_CONTENT = "fake sdbus archive"


@pytest.fixture
def sandbox(tmp_path):
    """A copy of the arch/ and rpm/ directories with a fake docker, a fake curl and a changelog."""
    root = tmp_path / "repo"
    for name in ("arch", "rpm"):
        shutil.copytree(os.path.join(ROOT, name), root / name, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "debian").mkdir()
    shutil.copy(os.path.join(ROOT, "debian", "changelog"), root / "debian" / "changelog")
    deps = root / "external" / "GlobalProtect-openconnect" / "crates" / "openconnect" / "deps"
    for name in ("openconnect", "libxml2"):
        (deps / name).mkdir(parents=True)
        (deps / name / "configure.ac").write_text("")
    # The pinned sha256 of the real archive is replaced by the one of the fake
    digest = hashlib.sha256(SDBUS_CONTENT.encode()).hexdigest()
    sources = (root / "rpm" / "sources").read_text()
    (root / "rpm" / "sources").write_text(re.sub(r"SDBUS_SHA256=\w+", f"SDBUS_SHA256={digest}", sources))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "docker.log"
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FAKE_LOG": str(log),
           "FAKE_SDBUS": SDBUS_CONTENT}
    return root, env, log, tmp_path


def top_version():
    top = re.match(r"^\S+ \((\S+)\) ", read("debian/changelog")).group(1)
    # A native version (2.0.0) has no revision: the pkgrel is 1
    version, _, revision = top.rpartition("-")
    return (version, revision) if version else (top, "1")


def good_pkgs(pkgver, pkgrel, arch="x86_64"):
    return " ".join(f"{name}-{pkgver}-{pkgrel}-{arch}.pkg.tar.zst" for name in PACKAGES)


def build(sandbox, *args, env_extra=None):
    root, env, _log, tmp = sandbox
    env = {**env, **(env_extra or {})}
    return subprocess.run([str(root / "arch" / "build.sh"), *args], capture_output=True, text=True, env=env,
                          cwd=tmp, check=False)


def test_build_runs_the_archlinux_image_and_collects_three_packages(sandbox):
    version, revision = top_version()
    out = sandbox[3] / "out"

    result = build(sandbox, str(out), env_extra={"FAKE_PKGS": good_pkgs(version, revision)})

    assert result.returncode == 0, result.stderr
    assert sorted(os.listdir(out)) == sorted(good_pkgs(version, revision).split())
    call = sandbox[2].read_text().strip().splitlines()
    assert len(call) == 1
    assert "--platform linux/amd64" in call[0]
    assert "archlinux:base-devel bash /container-build.sh /in /out" in call[0]
    assert f"-e PROJECT_VERSION={version}" in call[0] and f"-e DEBIAN_RELEASE={revision}" in call[0]
    assert "-e PR_SUFFIX= " in call[0]
    assert "-e SDBUS_VERSION=0.14.3" in call[0]


def test_build_pull_request_version(sandbox):
    version, revision = top_version()

    result = build(sandbox, str(sandbox[3] / "out"), env_extra={
        "PR_NUMBER": "36", "RUN_NUMBER": "7", "FAKE_PKGS": good_pkgs(f"{version}.pr36.7", revision)})

    assert result.returncode == 0, result.stderr
    assert "-e PR_SUFFIX=.pr36.7" in sandbox[2].read_text()
    assert f"version {version}.pr36.7" in result.stdout


def test_a_release_build_has_no_pull_request_marker(sandbox):
    version, revision = top_version()

    # a build that names its packages with a marker although none was asked for
    result = build(sandbox, str(sandbox[3] / "out"), env_extra={"FAKE_PKGS": good_pkgs(f"{version}.pr36.7", revision)})

    assert result.returncode != 0
    assert "unexpected file name" in result.stderr


def test_a_pull_request_build_must_have_its_marker(sandbox):
    version, revision = top_version()

    result = build(sandbox, str(sandbox[3] / "out"), env_extra={
        "PR_NUMBER": "36", "RUN_NUMBER": "7", "FAKE_PKGS": good_pkgs(version, revision)})

    assert result.returncode != 0
    assert "unexpected file name" in result.stderr


@pytest.mark.parametrize("pr, run", [("36", ""), ("", "7"), ("36x", "7"), ("36", "7 8"), ("$(id)", "7"), ("36", "-1")])
def test_build_rejects_a_bad_pr_or_run_number(sandbox, pr, run):
    result = build(sandbox, str(sandbox[3] / "out"), env_extra={"PR_NUMBER": pr, "RUN_NUMBER": run})

    assert result.returncode != 0
    assert "PR_NUMBER and RUN_NUMBER" in result.stderr
    assert not sandbox[2].exists(), "docker must not run"


@pytest.mark.parametrize("args", [["a", "b"], ["--install-test", "a", "b"]])
def test_build_rejects_too_many_arguments(sandbox, args):
    result = build(sandbox, *args)

    assert result.returncode != 0 and "usage" in result.stderr
    assert not sandbox[2].exists(), "docker must not run"


def test_build_rejects_a_debian_revision_that_is_not_an_integer(sandbox):
    changelog = sandbox[0] / "debian" / "changelog"
    text = changelog.read_text()
    # The top entry with a revision that is not an integer, native (2.0.0) or not (1.5.0-1)
    changed = re.sub(r"^(\S+ \([^)\s-]+)(?:-\d+)?\)", r"\1-1ubuntu1)", text, count=1, flags=re.M)
    assert changed != text
    changelog.write_text(changed)

    result = build(sandbox, str(sandbox[3] / "out"))

    assert result.returncode != 0
    assert "pkgrel" in result.stderr
    assert not sandbox[2].exists()


def test_build_rejects_an_sdbus_archive_with_another_sha256(sandbox):
    result = build(sandbox, str(sandbox[3] / "out"), env_extra={"FAKE_SDBUS": "tampered archive"})

    assert result.returncode != 0
    assert "sha256" in result.stderr
    assert not sandbox[2].exists(), "docker must not run"


def test_build_stops_without_submodules(sandbox):
    deps = sandbox[0] / "external" / "GlobalProtect-openconnect" / "crates" / "openconnect" / "deps"
    os.remove(deps / "libxml2" / "configure.ac")

    result = build(sandbox, str(sandbox[3] / "out"))

    assert result.returncode != 0 and "submodules" in result.stderr


def test_build_stops_when_the_container_fails(sandbox):
    assert build(sandbox, str(sandbox[3] / "out"), env_extra={"FAKE_DOCKER_RC": "3"}).returncode != 0


@pytest.mark.parametrize(
    "names",
    [
        lambda v, r: good_pkgs(v, r).rsplit(" ", 1)[0],  # too few
        lambda v, r: good_pkgs(v, r, "aarch64"),  # wrong architecture
        lambda v, r: good_pkgs(v, r, "any"),
        lambda v, r: "",
    ],
)
def test_build_rejects_a_wrong_set_of_packages(sandbox, names):
    version, revision = top_version()

    result = build(sandbox, str(sandbox[3] / "out"), env_extra={"FAKE_PKGS": names(version, revision)})

    assert result.returncode != 0


def test_build_leaves_the_debug_package_out(sandbox):
    version, revision = top_version()
    out = sandbox[3] / "out"
    stray = f"network-manager-gpclient-debug-{version}-{revision}-x86_64.pkg.tar.zst"

    result = build(sandbox, str(out), env_extra={"FAKE_PKGS": good_pkgs(version, revision) + " " + stray})

    assert result.returncode == 0, result.stderr
    assert stray not in os.listdir(out) and len(os.listdir(out)) == 3


def test_build_removes_old_packages_from_the_output_dir(sandbox):
    version, revision = top_version()
    out = sandbox[3] / "out"
    out.mkdir()
    (out / "old-1.0-1-x86_64.pkg.tar.zst").write_text("")

    result = build(sandbox, str(out), env_extra={"FAKE_PKGS": good_pkgs(version, revision)})

    assert result.returncode == 0, result.stderr
    assert "old-1.0-1-x86_64.pkg.tar.zst" not in os.listdir(out)


def test_the_source_archive_has_the_repository_without_git_data_and_build_products(sandbox):
    root = sandbox[0]
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("")
    (root / "output").mkdir()
    (root / "output" / "x.pkg.tar.zst").write_text("")
    (root / "vendor").mkdir()
    (root / "vendor" / "config.toml").write_text("")
    (root / "keep.txt").write_text("")
    # the fake docker copies the source archive out of its input directory
    docker = sandbox[3] / "bin" / "docker"
    docker.write_text(FAKE_DOCKER.replace('exit "', 'cp "${in}"/*.tar.gz "$FAKE_KEEP/"\nexit "', 1).replace(
        "out=\n", "out=\nin=\n").replace("*:/out) out=${arg%:/out} ;;", "*:/out) out=${arg%:/out} ;;\n    *:/in:ro) in=${arg%:/in:ro} ;;"))
    keep = sandbox[3] / "keep"
    keep.mkdir()
    version, revision = top_version()

    result = build(sandbox, str(sandbox[3] / "out"), env_extra={"FAKE_PKGS": good_pkgs(version, revision), "FAKE_KEEP": str(keep)})

    assert result.returncode == 0, result.stderr
    (archive,) = list(keep.glob("network-manager-gpclient-*.tar.gz"))
    assert archive.name == f"network-manager-gpclient-{version}.tar.gz"
    names = subprocess.run(["tar", "tzf", str(archive)], capture_output=True, text=True, check=True).stdout.split()
    prefix = f"network-manager-gpclient-{version}/"
    assert prefix + "keep.txt" in names and prefix + "vendor/config.toml" in names and prefix + "arch/PKGBUILD" in names
    assert not any(n.startswith(prefix + ".git/") or n.startswith(prefix + "output/") for n in names)


def test_install_test_mode_starts_a_fresh_container_with_the_packages(sandbox):
    out = sandbox[3] / "pkgs"
    out.mkdir()
    (out / "a.pkg.tar.zst").write_text("")

    result = build(sandbox, "--install-test", str(out))

    assert result.returncode == 0, result.stderr
    call = sandbox[2].read_text()
    assert "--rm --platform linux/amd64" in call
    assert f"{out}:/pkgs:ro" in call
    assert "/rpm-install-test.sh:ro" in call
    assert "archlinux:base bash /install-test.sh /pkgs" in call


def test_install_test_mode_needs_packages(sandbox):
    empty = sandbox[3] / "empty"
    empty.mkdir()

    result = build(sandbox, "--install-test", str(empty))

    assert result.returncode != 0 and "no packages" in result.stderr
    assert not sandbox[2].exists()


# --- arch/install-test.sh ---


def run_install_test(*args):
    return subprocess.run(["bash", os.path.join(ARCH_DIR, "install-test.sh"), *args], capture_output=True, text=True,
                          check=False)


def test_install_test_needs_the_shared_checks_and_a_directory(tmp_path):
    # outside the container /rpm-install-test.sh is not there: it must say so, not run half
    result = run_install_test(str(tmp_path))
    assert result.returncode != 0
    assert "rpm-install-test.sh" in result.stderr


def test_install_test_checks_run_the_shared_functions(tmp_path):
    shutil.copy(os.path.join(ROOT, "rpm", "install-test.sh"), tmp_path / "rpm-install-test.sh")
    script = (tmp_path / "install-test.sh")
    script.write_text(read("arch/install-test.sh").replace("/rpm-install-test.sh", str(tmp_path / "rpm-install-test.sh")))
    for args in ([], [""], [str(tmp_path / "missing")]):
        result = subprocess.run(["bash", str(script), *args], capture_output=True, text=True, check=False)
        assert result.returncode == 2 and "usage" in result.stderr
    for count in (0, 2, 4):
        directory = tmp_path / f"d{count}"
        directory.mkdir()
        for i in range(count):
            (directory / f"p{i}.pkg.tar.zst").write_text("")
        result = subprocess.run(["bash", str(script), str(directory)], capture_output=True, text=True, check=False)
        assert result.returncode == 1 and "expected 3 packages" in result.stderr


def test_install_test_lists_the_files_of_the_packages():
    text = read("arch/install-test.sh")
    # every file the PKGBUILD installs outside the translations and the licenses is checked
    for _mode, _source, dest in installed_files(read("arch/PKGBUILD")):
        if dest.startswith(("/usr/share/licenses", "/usr/share/doc")):
            continue
        assert dest in text, dest
