"""
Tests for .github/scripts/build-apt-repo.sh: which suite and architecture index a package goes
to, the landing page, and the changelogs published for `apt changelog`.

Suites: the package for KDE neon, network-manager-gpclient-plasma-6, is built for Ubuntu 24.04
(version 1.5.0-1~noble1), so it belongs to the suite noble and to amd64 only, like the codename of
any other package says; nothing is decided by the name of the package.

Landing page: the version table, the fingerprint (or the unsigned fallback) and the repository
URL (APT_REPO_URL) are filled in literally.

Changelogs: taken from changelog.Debian.gz or, in a native package, changelog.gz; published under
changelogs/<component>/<prefix>/<source>/; announced once by the Changelogs field of every Release
file, ahead of the checksum sections; a source or version that is not a plain name never reaches
a path, and a package that cannot be unpacked is reported without stopping the build.

The .deb files are real (dpkg-deb --build of an empty package); apt-ftparchive, which only writes
the Release file, is a fake, and the repository is not signed. Every positive test has a negative
counterpart: a package that names no known release, or has an architecture the repository does
not serve, stops the script and builds no repository.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import gzip
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "build-apt-repo.sh")
CORE = "network-manager-gpclient"
PLASMA6 = CORE + "-plasma-6"

FAKE_FTPARCHIVE = """#!/bin/sh
# apt-ftparchive ... release <dir>: prints the canned Release file next to this script
cat "$(dirname "$0")/release.txt"
"""

# What apt-ftparchive prints: the checksum fields are multi-line, one indented line per file
RELEASE = """Origin: GlobalProtect-SAML-NetworkManager
Suite: noble
Codename: noble
Date: Sat, 03 Oct 2026 10:00:00 UTC
Architectures: amd64 arm64
Components: main
Description: NetworkManager VPN plugin for GlobalProtect (SAML/SSO)
MD5Sum:
 d41d8cd98f00b204e9800998ecf8427e                0 main/binary-amd64/Packages
 7029066c27ac6f5ef18d660d5741979a               20 main/binary-amd64/Packages.gz
SHA256:
 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855                0 main/binary-amd64/Packages
 f3d9c6a1e0bd2e5c9c3e5a7b1d9e0f2a4b6c8d0e1f3a5b7c9d1e3f5a7b9c1d3e               20 main/binary-amd64/Packages.gz
"""


def make_deb(directory, package, version, arch="amd64", source=None, changelog=None, native_changelog=None):
    """A real, empty .deb; returns its path. changelog is installed as changelog.Debian.gz,
    native_changelog as changelog.gz (what debhelper does for a version without a Debian revision)"""
    root = directory / f"{package}_{version}_{arch}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\nMaintainer: x <x@example.org>\n"
        + (f"Source: {source}\n" if source else "")
        + "Description: x\n x\n"
    )
    for name, text in (("changelog.Debian.gz", changelog), ("changelog.gz", native_changelog)):
        if text is not None:
            doc = root / "usr" / "share" / "doc" / package
            doc.mkdir(parents=True, exist_ok=True)
            (doc / name).write_bytes(gzip.compress(text.encode()))
    deb = directory / f"{package}_{version}_{arch}.deb"
    subprocess.run(["dpkg-deb", "--build", str(root), str(deb)], check=True, capture_output=True)
    return deb


class Repo:
    def __init__(self, tmp_path):
        self.incoming = tmp_path / "incoming"
        self.incoming.mkdir()
        self.out = tmp_path / "repo"
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "apt-ftparchive").write_text(FAKE_FTPARCHIVE)
        (self.bin / "apt-ftparchive").chmod(0o755)
        self.release(RELEASE)
        self.work = tmp_path / "work"
        self.work.mkdir()

    def add(self, package, version, arch="amd64", **kwargs):
        deb = make_deb(self.work, package, version, arch, **kwargs)
        return deb.rename(self.incoming / f"{package}_{version}_{arch}.deb")

    def release(self, text):
        """What the fake apt-ftparchive prints"""
        (self.bin / "release.txt").write_text(text)

    def build(self, key=None, env=None):
        """Run the script, optionally signing with key; env adds variables to the environment"""
        full_env = {"PATH": f"{self.bin}:/usr/bin:/bin", **(env or {})}
        args = ["bash", SCRIPT, str(self.incoming), str(self.out)] + ([key] if key else [])
        return subprocess.run(args, env=full_env, capture_output=True, text=True, timeout=120)

    def index(self, suite, arch):
        path = self.out / "dists" / suite / "main" / f"binary-{arch}" / "Packages"
        return path.read_text() if path.exists() else ""

    def pool(self, suite):
        path = self.out / "pool" / suite / "main" / "n" / CORE
        return sorted(os.listdir(path)) if path.exists() else []


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


class TestPlasma6ForKdeNeon:
    def test_it_goes_to_the_suite_noble_and_the_amd64_index(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")
        repo.add(PLASMA6, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert f"Package: {PLASMA6}\n" in repo.index("noble", "amd64")
        assert f"{PLASMA6}_1.5.0-1~noble1_amd64.deb" in repo.pool("noble")

    def test_it_is_in_no_other_suite_and_not_in_the_arm64_index(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")
        repo.add(CORE, "1.5.0-1~noble1", "arm64")
        repo.add(CORE, "1.5.0-1~resolute1")
        repo.add(PLASMA6, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert PLASMA6 not in repo.index("noble", "arm64")
        for suite in ("jammy", "oracular", "resolute"):
            assert PLASMA6 not in repo.index(suite, "amd64"), suite
            assert PLASMA6 not in repo.index(suite, "arm64"), suite
            assert not [n for n in repo.pool(suite) if PLASMA6 in n], suite

    def test_the_suite_comes_from_the_version_and_not_from_the_name_of_the_package(self, repo):
        repo.add(PLASMA6, "1.5.0-1~resolute1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert f"Package: {PLASMA6}\n" in repo.index("resolute", "amd64")
        assert PLASMA6 not in repo.index("noble", "amd64")

    def test_a_pull_request_build_of_it_is_not_part_of_the_apt_repository(self, repo):
        # its version ends in +pr<PR>.<run>: the test packages of a pull request have their own release
        repo.add(PLASMA6, "1.5.0-1~noble1+pr31.57")

        result = repo.build()

        assert result.returncode != 0
        assert "cannot tell which Ubuntu release" in result.stderr
        assert not (repo.out / "dists" / "noble" / "Release").exists()

    @pytest.mark.parametrize("version", ["1.5.0-1", "1.5.0-1~focal1", "1.5.0-1+noble"])
    def test_a_version_without_a_known_codename_stops_the_script(self, repo, version):
        repo.add(PLASMA6, version)

        result = repo.build()

        assert result.returncode != 0
        assert "cannot tell which Ubuntu release" in result.stderr
        assert not (repo.out / "dists" / "noble" / "Release").exists()

    def test_an_architecture_the_repository_does_not_serve_stops_the_script(self, repo):
        repo.add(PLASMA6, "1.5.0-1~noble1", "i386")

        result = repo.build()

        assert result.returncode != 0
        assert "unsupported architecture 'i386'" in result.stderr


DEFAULT_URL = "https://wmp.github.io/GlobalProtect-SAML-NetworkManager"

FAKE_GPG = """#!/bin/sh
# --fingerprint prints a colon-separated record; every other call (export, sign) succeeds silently
case "$*" in
*--fingerprint*) echo 'fpr:::::::::AB&CD|EF\\GH:' ;;
esac
"""


class TestLandingPage:
    def index_html(self, repo):
        return (repo.out / "index.html").read_text()

    def test_a_suite_without_packages_shows_a_dash_and_no_placeholder(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "<tr><td><code>oracular</code></td><td><code>&mdash;</code></td></tr>" in html
        assert "__VERSION_ROWS__" not in html

    def test_a_suite_with_a_package_shows_its_version_and_no_dash(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "<tr><td><code>noble</code></td><td><code>1.5.0-1~noble1</code></td></tr>" in html
        assert "<tr><td><code>noble</code></td><td><code>&mdash;</code>" not in html

    def test_the_fingerprint_is_written_literally(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")
        (repo.bin / "gpg").write_text(FAKE_GPG)
        (repo.bin / "gpg").chmod(0o755)

        result = repo.build(key="KEY")

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "<pre><code>AB&CD|EF\\GH</code></pre>" in html
        assert "__FINGERPRINT__" not in html
        assert "(unsigned test build)" not in html

    def test_without_a_key_the_fingerprint_says_the_build_is_unsigned(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "<pre><code>(unsigned test build)</code></pre>" in html
        assert "__FINGERPRINT__" not in html
        assert not (repo.out / "gpclient-archive-keyring.gpg").exists()

    def test_the_default_repository_url_is_written_to_the_page(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert f"curl -fsSL {DEFAULT_URL}/gpclient-archive-keyring.gpg" in html
        assert "__REPO_URL__" not in html

    def test_apt_repo_url_replaces_the_url_on_the_page_literally(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build(env={"APT_REPO_URL": "http://127.0.0.1:8000/a&b|c"})

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "curl -fsSL http://127.0.0.1:8000/a&b|c/gpclient-archive-keyring.gpg" in html
        assert "wmp.github.io" not in html
        assert "__REPO_URL__" not in html


def corrupt_deb(path, member):
    """Replace the data member of a .deb with garbage: the control data stays readable"""
    work = path.parent / (path.name + ".ar")
    work.mkdir()
    subprocess.run(["ar", "x", str(path)], cwd=work, check=True, capture_output=True)
    for old in work.glob("data.tar*"):
        old.unlink()
    (work / member).write_bytes(b"this is not an archive\n" * 50)
    path.unlink()
    control = next(work.glob("control.tar*")).name
    subprocess.run(["ar", "rc", str(path), "debian-binary", control, member], cwd=work, check=True,
                   capture_output=True)


class TestChangelogs:
    URL_LINE = f"Changelogs: {DEFAULT_URL}/changelogs/@CHANGEPATH@_changelog\n"
    TEXT = "network-manager-gpclient (1.5.0-1~noble1) noble; urgency=medium\n\n  * Test.\n"

    def changelogs(self, repo):
        found = []
        for dirpath, _, names in os.walk(repo.out / "changelogs"):
            found += [os.path.relpath(os.path.join(dirpath, n), repo.out / "changelogs") for n in names]
        return sorted(found)

    def directories(self, repo):
        found = []
        for dirpath, names, _ in os.walk(repo.out / "changelogs"):
            found += [os.path.join(dirpath, n) for n in names]
        return found

    def release(self, repo, suite="noble"):
        return (repo.out / "dists" / suite / "Release").read_text()

    def test_every_release_file_names_the_changelog_location(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        for suite in ("jammy", "noble", "oracular", "resolute"):
            assert self.URL_LINE in self.release(repo, suite), suite

    def test_the_line_is_there_once_ahead_of_the_checksums_which_stay_intact(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        lines = self.release(repo).splitlines()
        assert lines.count(self.URL_LINE.rstrip()) == 1
        assert sum(1 for line in lines if line.startswith("Changelogs:")) == 1
        assert lines.index(self.URL_LINE.rstrip()) < lines.index("MD5Sum:") < lines.index("SHA256:")
        lines.remove(self.URL_LINE.rstrip())
        assert lines == RELEASE.splitlines()

    def test_without_checksum_sections_the_line_is_added_at_the_end(self, repo):
        repo.release("Suite: noble\nCodename: noble\n")
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert self.release(repo) == "Suite: noble\nCodename: noble\n" + self.URL_LINE

    def test_apt_repo_url_sets_the_line_and_a_trailing_slash_does_not_matter(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        for url in ("http://127.0.0.1:8000", "http://127.0.0.1:8000/"):
            result = repo.build(env={"APT_REPO_URL": url})

            assert result.returncode == 0, result.stderr + result.stdout
            lines = self.release(repo).splitlines()
            assert "Changelogs: http://127.0.0.1:8000/changelogs/@CHANGEPATH@_changelog" in lines
            assert not [line for line in lines if "wmp.github.io" in line]

    def test_a_stray_base_url_in_the_environment_changes_nothing(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build(env={"BASE_URL": "https://evil.example"})

        assert result.returncode == 0, result.stderr + result.stdout
        assert self.URL_LINE in self.release(repo)
        assert "evil.example" not in self.release(repo)
        assert "evil.example" not in (repo.out / "index.html").read_text()

    def test_the_changelog_is_published_uncompressed_under_the_apt_path(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        path = repo.out / "changelogs" / "main" / "n" / CORE / f"{CORE}_1.5.0-1~noble1_changelog"
        assert path.read_text() == self.TEXT

    def test_a_native_package_has_changelog_gz_instead_of_changelog_debian_gz(self, repo):
        repo.add(CORE, "1.5.0~noble1", native_changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        path = repo.out / "changelogs" / "main" / "n" / CORE / f"{CORE}_1.5.0~noble1_changelog"
        assert path.read_text() == self.TEXT

    def test_changelog_debian_gz_wins_over_changelog_gz(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog="debian\n", native_changelog="native\n")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        path = repo.out / "changelogs" / "main" / "n" / CORE / f"{CORE}_1.5.0-1~noble1_changelog"
        assert path.read_text() == "debian\n"

    def test_binaries_of_one_source_share_one_file(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)
        repo.add(CORE, "1.5.0-1~noble1", "arm64", changelog=self.TEXT)
        repo.add(PLASMA6, "1.5.0-1~noble1", source=f"{CORE} (1.5.0-1~noble1)", changelog="other\n")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert self.changelogs(repo) == [f"main/n/{CORE}/{CORE}_1.5.0-1~noble1_changelog"]

    def test_the_epoch_is_dropped_and_a_lib_source_has_a_four_letter_prefix(self, repo):
        repo.add("libfoo1", "2:1.0-1~noble1", source="libfoo (2:1.0-1~noble1)", changelog="foo\n")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert self.changelogs(repo) == ["main/libf/libfoo/libfoo_1.0-1~noble1_changelog"]

    def test_the_source_version_wins_over_the_binary_version(self, repo):
        repo.add("libfoo-data", "1.0-2~noble1", source="libfoo (1.0-1~noble1)", changelog="foo\n")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert self.changelogs(repo) == ["main/libf/libfoo/libfoo_1.0-1~noble1_changelog"]

    def test_a_package_without_a_changelog_does_not_break_the_build(self, repo):
        repo.add(CORE, "1.5.0-1~noble1")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert "no changelog.Debian.gz or changelog.gz" in result.stdout
        assert "ERROR" not in result.stderr
        assert self.changelogs(repo) == []
        assert self.directories(repo) == []
        assert f"Package: {CORE}\n" in repo.index("noble", "amd64")

    @pytest.mark.parametrize("source", [
        "../../dists/x",
        "../../../escaped",
        "a/b",
        "Upper",
        "-dash",
        "x..y",
        "libfoo (../../dists/x)",
        "libfoo (1.0/../../x)",
        "libfoo (1..0)",
        "libfoo ()",
    ])
    def test_a_source_or_version_that_is_not_a_plain_name_is_not_used_in_a_path(self, repo, tmp_path, source):
        repo.add("libfoo1", "1.0-1~noble1", source=source, changelog="foo\n")

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert "not publishing a changelog" in result.stdout
        assert [p for p in tmp_path.rglob("*_changelog")] == []
        assert not (repo.out / "dists" / "x").exists()
        assert self.directories(repo) == []
        assert "Package: libfoo1\n" in repo.index("noble", "amd64")

    @pytest.mark.parametrize("member", ["data.tar.gz", "data.tar"])
    def test_a_package_that_cannot_be_unpacked_is_reported_and_the_build_goes_on(self, repo, member):
        deb = repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)
        corrupt_deb(deb, member)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        assert "ERROR" in result.stderr
        assert deb.name in result.stderr
        assert self.changelogs(repo) == []
        assert self.directories(repo) == []
        assert f"Package: {CORE}\n" in repo.index("noble", "amd64")
