"""
Tests for .github/scripts/build-apt-repo.sh with the package for KDE neon,
network-manager-gpclient-plasma-6: it is built for Ubuntu 24.04 (version 1.5.0-1~noble1), so
it belongs to the suite noble and to amd64 only, like the codename of any other package says;
nothing is decided by the name of the package.

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
# apt-ftparchive ... release <dir>: only the Release file's existence matters here
echo "Suite: fake"
"""


def make_deb(directory, package, version, arch="amd64", source=None, changelog=None):
    """A real, empty .deb; returns its path"""
    root = directory / f"{package}_{version}_{arch}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\nMaintainer: x <x@example.org>\n"
        + (f"Source: {source}\n" if source else "")
        + "Description: x\n x\n"
    )
    if changelog is not None:
        doc = root / "usr" / "share" / "doc" / package
        doc.mkdir(parents=True)
        (doc / "changelog.Debian.gz").write_bytes(gzip.compress(changelog.encode()))
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
        self.work = tmp_path / "work"
        self.work.mkdir()

    def add(self, package, version, arch="amd64", **kwargs):
        deb = make_deb(self.work, package, version, arch, **kwargs)
        return deb.rename(self.incoming / f"{package}_{version}_{arch}.deb")

    def build(self):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin"}
        return subprocess.run(["bash", SCRIPT, str(self.incoming), str(self.out)], env=env, capture_output=True,
                              text=True, timeout=120)

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
        env = {"PATH": f"{repo.bin}:/usr/bin:/bin"}

        result = subprocess.run(["bash", SCRIPT, str(repo.incoming), str(repo.out), "KEY"], env=env,
                                capture_output=True, text=True, timeout=120)

        assert result.returncode == 0, result.stderr + result.stdout
        html = self.index_html(repo)
        assert "<pre><code>AB&CD|EF\\GH</code></pre>" in html
        assert "__FINGERPRINT__" not in html


class TestChangelogs:
    URL_LINE = "Changelogs: https://wmp.github.io/GlobalProtect-SAML-NetworkManager/changelogs/@CHANGEPATH@_changelog\n"
    TEXT = "network-manager-gpclient (1.5.0-1~noble1) noble; urgency=medium\n\n  * Test.\n"

    def changelogs(self, repo):
        found = []
        for dirpath, _, names in os.walk(repo.out / "changelogs"):
            found += [os.path.relpath(os.path.join(dirpath, n), repo.out / "changelogs") for n in names]
        return sorted(found)

    def test_every_release_file_names_the_changelog_location(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        for suite in ("jammy", "noble", "oracular", "resolute"):
            assert self.URL_LINE in (repo.out / "dists" / suite / "Release").read_text(), suite

    def test_the_changelog_is_published_uncompressed_under_the_apt_path(self, repo):
        repo.add(CORE, "1.5.0-1~noble1", changelog=self.TEXT)

        result = repo.build()

        assert result.returncode == 0, result.stderr + result.stdout
        path = repo.out / "changelogs" / "main" / "n" / CORE / f"{CORE}_1.5.0-1~noble1_changelog"
        assert path.read_text() == self.TEXT

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
        assert "no changelog.Debian.gz" in result.stdout
        assert self.changelogs(repo) == []
        assert f"Package: {CORE}\n" in repo.index("noble", "amd64")
