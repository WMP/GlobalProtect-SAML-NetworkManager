"""
Tests for .github/scripts/build-arch-repo.sh, which builds the signed pacman
repository (x86_64) from the Arch Linux package assets of the releases, and
finishes the landing page.

docker (repo-add runs in an archlinux container) and gpg are fakes found
through PATH: the fake docker makes the database archives the way the script
in the container does (one entry per package, the newest version only, as
chosen by vercmp there, and the symlinks gpclient.db and gpclient.files; the
script itself runs only in a real container, see the e2e check of the pull
request) and records the script it was given on stdin, the fake gpg writes the signature files. The packages are
small files with real names. The secret key is not part of any call: the fakes
show that only the key id, a fingerprint, reaches the arguments, that the
container gets no key and no network, and that the keyring is found through
GNUPGHOME.

Every positive test has negative counterparts: names that are not our
packages, bad key ids, failing tools and a missing incoming directory stop the
script or are skipped, an old version is left out, and no placeholder is left
on the page whether there are packages or not.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import re
import subprocess
import tarfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "build-arch-repo.sh")
TEMPLATE = os.path.join(ROOT, ".github", "pages", "index.html")
KEY = "0123456789ABCDEF0123456789ABCDEF01234567"
CORE = "network-manager-gpclient"
PACKAGES = (CORE, CORE + "-gnome", CORE + "-plasma")

# Like repo-add: gpclient.db.tar.gz and gpclient.files.tar.gz with one entry per package name
# (the newest version of it) and the symlinks gpclient.db and gpclient.files
FAKE_DOCKER = r"""#!/bin/bash
echo "docker $*" >> "$FAKE_DIR/calls"
cat > "$FAKE_DIR/container_script"
[ -z "$FAKE_DOCKER_FAILS" ] || exit 1
dir=""
prev=""
for arg in "$@"; do
    [ "$prev" = "-v" ] && dir="${arg%:/repo}"
    prev="$arg"
done
cd "$dir" || exit 1
[ -z "$FAKE_NO_DB" ] || exit 0
python3 - <<'PY'
import glob, io, re, tarfile

newest = {}
for path in sorted(glob.glob("*.pkg.tar.zst")):
    m = re.fullmatch(r"(.+)-([^-]+-[0-9]+)-x86_64\.pkg\.tar\.zst", path)
    key = lambda v: [int(x) if x.isdigit() else x for x in re.split(r"[.-]", v)]
    if m.group(1) not in newest or key(m.group(2)) > key(newest[m.group(1)][0]):
        newest[m.group(1)] = (m.group(2), path)
for archive in ("gpclient.db.tar.gz", "gpclient.files.tar.gz"):
    with tarfile.open(archive, "w:gz") as tar:
        for name, (version, path) in sorted(newest.items()):
            desc = ("%%FILENAME%%\n%s\n\n%%NAME%%\n%s\n\n%%VERSION%%\n%s\n" % (path, name, version)).encode()
            info = tarfile.TarInfo("%s-%s/desc" % (name, version))
            info.size = len(desc)
            tar.addfile(info, io.BytesIO(desc))
import os
for link in ("gpclient.db", "gpclient.files"):
    os.symlink(link + ".tar.gz", link)
PY
"""

FAKE_GPG = r"""#!/bin/bash
echo "gpg $*" >> "$FAKE_DIR/calls"
echo "GNUPGHOME=$GNUPGHOME" >> "$FAKE_DIR/gpg_env"
[ -z "$FAKE_GPG_FAILS" ] || exit 1
case " $* " in
    *" --detach-sign "*)
        out=""; prev=""
        for arg in "$@"; do [ "$prev" = "--output" ] && out="$arg"; prev="$arg"; done
        echo "signature of ${@: -1}" > "$out" ;;
    *" --export "*) [ -n "$FAKE_NO_KEY" ] || echo "-----BEGIN PGP PUBLIC KEY BLOCK-----" ;;
esac
"""


def pkg_name(package, version="1.5.0", revision="1", arch="x86_64"):
    return f"{package}-{version}-{revision}-{arch}.pkg.tar.zst"


class Box:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.incoming = tmp_path / "incoming"
        self.out = tmp_path / "site" / "arch"
        self.index = tmp_path / "site" / "index.html"
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        self.gnupg = tmp_path / "gnupg"
        for directory in (self.incoming, self.out.parent, self.fake, self.bin, self.gnupg):
            directory.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("gpg", FAKE_GPG)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.write_index()

    def write_index(self):
        """The page as build-rpm-repo.sh leaves it: only the Arch Linux part is still to do"""
        with open(TEMPLATE, encoding="utf-8") as handle:
            text = handle.read()
        text = (text.replace("__VERSION_ROWS__", "<tr><td>noble</td><td>1.5.0-1~noble1</td></tr>")
                .replace("__RPM_VERSION_ROWS__", "<tr><td>Fedora 44</td></tr>")
                .replace("__FINGERPRINT__", KEY).replace("__UPDATED__", "now")
                .replace("__REPO_URL__", "https://wmp.github.io/GlobalProtect-SAML-NetworkManager"))
        self.index.write_text(text)

    def add(self, *names):
        for name in names:
            (self.incoming / name).write_text(name)

    def add_set(self, version="1.5.0", revision="1"):
        self.add(*[pkg_name(p, version, revision) for p in PACKAGES])

    def run(self, *args, key=KEY, **extra_env):
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "FAKE_DIR": str(self.fake),
            "FAKE_DOCKER_FAILS": "",
            "FAKE_GPG_FAILS": "",
            "FAKE_NO_KEY": "",
            "FAKE_NO_DB": "",
            "GNUPGHOME": str(self.gnupg),
            "HOME": str(self.root),
        }
        env.update(extra_env)
        command = ["bash", SCRIPT, *args, str(self.incoming), str(self.out)]
        if key:
            command.append(key)
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def page(self):
        return self.index.read_text()

    def tree(self):
        return sorted(
            os.path.relpath(os.path.join(d, f), self.out)
            for d, _dirs, files in os.walk(self.out) for f in files
        ) if self.out.exists() else []


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


class TestLayout:
    def test_packages_database_and_signatures(self, box):
        box.add_set()

        result = box.run("--index", str(box.index))

        assert result.returncode == 0, result.stderr + result.stdout
        expected = sorted(
            [pkg_name(p) for p in PACKAGES] + [pkg_name(p) + ".sig" for p in PACKAGES]
            + ["x86_64/gpclient.db", "x86_64/gpclient.db.sig", "x86_64/gpclient.files", "x86_64/gpclient.files.sig"]
        )
        expected = [name if "/" in name else "x86_64/" + name for name in expected] + ["GPG-KEY-gpclient"]
        assert box.tree() == sorted(expected)

    def test_the_database_files_are_plain_files_not_symlinks(self, box):
        box.add_set()

        assert box.run().returncode == 0
        for name in ("gpclient.db", "gpclient.files"):
            path = box.out / "x86_64" / name
            assert path.is_file() and not path.is_symlink()
            with tarfile.open(path) as db:
                assert sorted(m.name.rsplit("-", 2)[0] for m in db.getmembers()) == sorted(PACKAGES)

    def test_the_public_key_is_published_armored(self, box):
        box.add_set()

        assert box.run().returncode == 0
        assert (box.out / "GPG-KEY-gpclient").read_text().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")

    def test_a_rebuild_starts_from_scratch(self, box):
        box.add_set()
        (box.out / "x86_64").mkdir(parents=True)
        (box.out / "x86_64" / "stale-1-1-x86_64.pkg.tar.zst").write_text("old")

        assert box.run().returncode == 0
        assert "x86_64/stale-1-1-x86_64.pkg.tar.zst" not in box.tree()

    def test_only_the_newest_version_of_a_package_is_published(self, box):
        box.add_set("1.9.0")
        box.add_set("1.10.0")

        result = box.run()

        assert result.returncode == 0, result.stderr
        tree = box.tree()
        assert all(f"x86_64/{pkg_name(p, '1.10.0')}" in tree for p in PACKAGES)
        assert not any("1.9.0" in name for name in tree)
        assert "left out" in result.stdout

    def test_every_package_and_the_database_have_a_signature_and_no_other_file_has_one(self, box):
        box.add_set()

        assert box.run().returncode == 0
        files = {name for name in box.tree() if not name.endswith(".sig") and name.startswith("x86_64/")}
        sigs = {name[:-4] for name in box.tree() if name.endswith(".sig")}
        assert files == sigs


class TestTools:
    def test_repo_add_runs_in_a_container_without_network_and_without_the_key(self, box):
        box.add_set()

        assert box.run().returncode == 0
        [call] = [c for c in box.calls() if c.startswith("docker ")]
        assert "--network none" in call and "archlinux:base" in call
        assert f"-v {box.out}/x86_64:/repo" in call
        assert str(box.gnupg) not in call and KEY not in call
        script = (box.fake / "container_script").read_text()
        assert "repo-add" in script and "vercmp" in script and KEY not in script

    def test_only_the_key_id_reaches_gpg_and_the_keyring_is_the_one_of_gnupghome(self, box):
        box.add_set()

        assert box.run().returncode == 0
        gpg = [c for c in box.calls() if c.startswith("gpg ")]
        assert gpg and all(f"--local-user {KEY}" in c or "--export" in c for c in gpg)
        assert not any("BEGIN" in c or "PRIVATE" in c for c in box.calls())
        assert set((box.fake / "gpg_env").read_text().splitlines()) == {f"GNUPGHOME={box.gnupg}"}

    def test_without_a_key_nothing_is_signed_and_the_script_warns(self, box):
        box.add_set()

        result = box.run(key="")

        assert result.returncode == 0, result.stderr
        assert not [c for c in box.calls() if c.startswith("gpg ")]
        assert not any(name.endswith(".sig") for name in box.tree()) and "GPG-KEY-gpclient" not in box.tree()
        assert "unsigned repository" in result.stdout

    @pytest.mark.parametrize("key", ["xyz", "0123456", KEY + "0" * 30, "01234567; rm -rf /", "$(id)"])
    def test_a_key_that_is_not_hex_is_refused(self, box, key):
        box.add_set()

        result = box.run(key=key)

        assert result.returncode != 0
        assert "key id" in result.stderr
        assert box.calls() == [] and box.tree() == []

    def test_a_failing_repo_add_stops_the_script(self, box):
        box.add_set()

        result = box.run(FAKE_DOCKER_FAILS="1")

        assert result.returncode != 0
        assert "repo-add failed" in result.stderr

    def test_a_missing_database_stops_the_script(self, box):
        box.add_set()

        result = box.run(FAKE_NO_DB="1")

        assert result.returncode != 0
        assert "did not create" in result.stderr

    def test_a_failing_gpg_stops_the_script(self, box):
        box.add_set()

        assert box.run(FAKE_GPG_FAILS="1").returncode != 0

    def test_a_key_that_cannot_be_exported_stops_the_script(self, box):
        box.add_set()

        result = box.run(FAKE_NO_KEY="1")

        assert result.returncode != 0
        assert "exported no public key" in result.stderr


class TestInput:
    @pytest.mark.parametrize(
        "name",
        [
            pkg_name(CORE, arch="aarch64"),
            pkg_name(CORE, arch="any"),
            pkg_name(CORE + "-debug"),
            pkg_name(CORE + "-plasma-6"),
            pkg_name("other-package"),
            pkg_name(CORE, version="1.5.0-rc1"),
            pkg_name(CORE, revision="1.2"),
            f"{CORE}-1.5.0-1-x86_64.pkg.tar.xz",
            f"{CORE}-1.4.0-1-x86_64.pkg.tar.zst.sig",
            "$(touch-pwned)-1.5.0-1-x86_64.pkg.tar.zst",
        ],
    )
    def test_a_name_that_is_not_ours_is_skipped_with_a_warning(self, box, name):
        box.add_set()
        box.add(name)

        result = box.run()

        assert result.returncode == 0, result.stderr
        assert "WARNING" in result.stdout or name.endswith(".sig") or name.endswith(".xz")
        assert not any(name in tree_name for tree_name in box.tree())
        assert len([n for n in box.tree() if n.endswith(".pkg.tar.zst")]) == 3

    def test_without_packages_it_is_an_error(self, box):
        result = box.run()

        assert result.returncode != 0
        assert "no Arch Linux packages" in result.stderr
        assert not box.out.exists() and box.calls() == []

    def test_only_foreign_names_are_no_packages(self, box):
        box.add(pkg_name(CORE, arch="aarch64"))

        result = box.run()

        assert result.returncode != 0 and not box.out.exists()

    def test_a_missing_incoming_directory_is_an_error(self, box):
        box.incoming.rmdir()

        result = box.run()

        assert result.returncode != 0
        assert "incoming directory does not exist" in result.stderr

    @pytest.mark.parametrize("args", [[], ["--unknown"]])
    def test_usage_errors(self, box, args):
        result = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, timeout=60)

        assert result.returncode != 0 and "usage" in result.stderr


class TestLandingPage:
    def placeholders(self, text):
        return re.findall(r"__[A-Z_]+__", text)

    def test_the_version_is_the_newest_core_version(self, box):
        box.add_set("1.9.0")
        box.add_set("1.10.0", "2")

        result = box.run("--index", str(box.index))

        assert result.returncode == 0, result.stderr
        assert "<code>1.10.0-2</code>" in box.page()
        assert "1.9.0" not in box.page()

    def test_no_placeholder_and_no_marker_is_left(self, box):
        box.add_set()

        assert box.run("--index", str(box.index)).returncode == 0
        page = box.page()
        assert self.placeholders(page) == []
        assert "ARCH-BEGIN" not in page and "ARCH-END" not in page
        assert "pacman-key --add" in page and "SigLevel = Required" in page and "arch/GPG-KEY-gpclient" in page
        assert "Server = https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/$arch" in page
        assert f"pacman-key --lsign-key {KEY}" in page

    def test_without_packages_the_page_has_no_arch_part_and_no_placeholder(self, box):
        result = box.run("--index", str(box.index), "--allow-empty")

        assert result.returncode == 0, result.stderr
        page = box.page()
        assert self.placeholders(page) == []
        assert "ARCH-BEGIN" not in page and "ARCH-END" not in page
        for text in ("pacman", "Arch&nbsp;Linux", "Arch Linux"):
            assert text not in page, text
        assert "apt install" in page and "dnf install" in page
        assert not box.out.exists() and box.calls() == []

    def test_without_packages_and_without_allow_empty_the_page_is_not_touched(self, box):
        before = box.page()

        assert box.run("--index", str(box.index)).returncode != 0
        assert box.page() == before

    def test_foreign_names_only_leave_no_arch_part_with_allow_empty(self, box):
        box.add(pkg_name(CORE, arch="aarch64"))

        result = box.run("--index", str(box.index), "--allow-empty")

        assert result.returncode == 0, result.stderr
        assert "pacman" not in box.page() and self.placeholders(box.page()) == []

    def test_the_other_parts_of_the_page_are_not_changed(self, box):
        box.add_set()
        before = box.page()
        before_other = re.sub(r"<!-- ARCH-BEGIN -->.*?<!-- ARCH-END -->\n?", "", before, flags=re.S)

        assert box.run("--index", str(box.index)).returncode == 0
        page = box.page()
        for line in before_other.splitlines():
            assert line in page

    def test_the_index_file_must_exist(self, box):
        box.add_set()
        box.index.unlink()

        result = box.run("--index", str(box.index))

        assert result.returncode != 0
        assert "index file does not exist" in result.stderr
        assert box.calls() == []

    def test_without_an_index_option_the_page_is_not_looked_at(self, box):
        box.add_set()
        before = box.page()

        assert box.run().returncode == 0
        assert box.page() == before

    def test_the_template_has_balanced_arch_markers_and_one_placeholder(self):
        with open(TEMPLATE, encoding="utf-8") as handle:
            text = handle.read()

        assert text.count("<!-- ARCH-BEGIN -->") == text.count("<!-- ARCH-END -->") >= 1
        assert text.count("__ARCH_VERSION__") == 1
        marks = re.findall(r"<!-- ARCH-(BEGIN|END) -->", text)
        assert marks == ["BEGIN", "END"] * (len(marks) // 2)
        # the placeholder is inside a block, so that the block can be removed whole
        placeholder = text.index("__ARCH_VERSION__")
        begin = text.rindex("<!-- ARCH-BEGIN -->", 0, placeholder)
        assert "<!-- ARCH-END -->" not in text[begin:placeholder]
