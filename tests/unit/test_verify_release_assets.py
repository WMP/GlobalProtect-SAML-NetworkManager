"""
Tests for .github/scripts/verify-release-assets.sh, which publish-apt.yml runs on
the assets of every release before they are signed into the apt and RPM
repositories: `gh attestation verify` accepts the release's SHA256SUMS (one call per
release, none per package) and the SHA256 of each package is the one in it. Releases
of the legacy list are not verified; any other release without SHA256SUMS fails.

`gh` is a fake found through PATH (it records its arguments); sha256sum is real.
Every positive test has negative counterparts.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import hashlib
import os
import subprocess

import pytest
import workflow_steps

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "verify-release-assets.sh")
LEGACY = os.path.join(ROOT, ".github", "legacy-unattested-releases.txt")
REPO = "WMP/GlobalProtect-SAML-NetworkManager"

# Fails for the files listed in $FAKE_DIR/bad (one name per line) and, like the real one, when the
# attestation is not of the repository, of its release workflow and of the tag $FAKE_TAG
FAKE_GH = r"""#!/bin/bash
echo "$*" >> "$FAKE_DIR/gh_calls"
[ "$1 $2" = "attestation verify" ] || exit 2
file="$3"; repo=""; signer=""; ref=""
while [ $# -gt 0 ]; do
    case "$1" in --repo) repo="$2" ;; --signer-workflow) signer="$2" ;; --source-ref) ref="$2" ;; esac
    shift
done
[ "$repo" = "$GITHUB_REPOSITORY" ] && [ "$signer" = "$repo/.github/workflows/build-release.yml" ] \
    && [ "$ref" = "refs/tags/$FAKE_TAG" ] || { echo "wrong repository, workflow or tag" >&2; exit 1; }
grep -qxF "$(basename "$file")" "$FAKE_DIR/bad" 2>/dev/null && { echo "no attestation" >&2; exit 1; }
exit 0
"""

DEB = "network-manager-gpclient_2.0.0-1~noble1_amd64.deb"
RPM = "network-manager-gpclient-2.0.0-1.fc44.x86_64.rpm"
PKG = "network-manager-gpclient-2.0.0-1-x86_64.pkg.tar.zst"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Box:
    def __init__(self, tmp_path):
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        self.dir = tmp_path / "release"
        for directory in (self.fake, self.bin, self.dir):
            directory.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.legacy = tmp_path / "legacy.txt"
        self.legacy.write_text("# legacy\nv1.5.0\nv1.4.1  # comment\n")

    def add(self, name, data=None, listed=True, listed_as=None):
        data = data if data is not None else name.encode()
        (self.dir / name).write_bytes(data)
        if listed:
            with open(self.dir / "SHA256SUMS", "a", encoding="utf-8") as handle:
                handle.write(f"{sha(data)}  {listed_as or name}\n")

    def run(self, tag="v2.0.0", attested_tag=None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_DIR": str(self.fake), "GITHUB_REPOSITORY": REPO,
               "FAKE_TAG": attested_tag or tag}
        return subprocess.run(["bash", SCRIPT, tag, str(self.dir), str(self.legacy)], env=env,
                              capture_output=True, text=True, timeout=60)

    def gh_calls(self):
        path = self.fake / "gh_calls"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_good_release_passes_and_sha256sums_is_verified_once_against_the_repository(box):
    box.add(DEB)
    box.add(RPM)
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert box.gh_calls() == [
        f"attestation verify {box.dir}/SHA256SUMS --repo {REPO}"
        f" --signer-workflow {REPO}/.github/workflows/build-release.yml --source-ref refs/tags/v2.0.0"]


def test_packages_are_not_attested_one_by_one(box):
    # a bad attestation of a package is not looked at: SHA256SUMS binds its hash
    box.add(DEB)
    box.add(RPM)
    (box.fake / "bad").write_text(RPM + "\n")
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert len(box.gh_calls()) == 1


def test_an_attestation_of_another_tag_fails(box):
    box.add(DEB)
    result = box.run("v2.0.0", attested_tag="v2.0.1")
    assert result.returncode != 0
    assert "gh attestation verify failed" in result.stderr


def test_an_attestation_of_another_workflow_fails(box):
    box.add(DEB)
    (box.bin / "gh").write_text(FAKE_GH.replace("build-release.yml", "other.yml"))
    result = box.run()
    assert result.returncode != 0
    assert "gh attestation verify failed" in result.stderr


def test_github_renames_the_tilde_of_an_asset_to_a_dot(box):
    box.add(DEB.replace("~", "."), listed_as=DEB)
    assert box.run().returncode == 0
    box.add("network-manager-gpclient-gnome_2.0.0-1.noble1_amd64.deb",
            listed_as="network-manager-gpclient-gnome_2.0.0-1~noble1_amd64.deb")
    assert box.run().returncode == 0


def test_checksum_mismatch_fails_after_the_attestation_of_sha256sums(box):
    box.add(DEB)
    (box.dir / DEB).write_bytes(b"tampered")
    result = box.run()
    assert result.returncode != 0
    assert "SHA256 of" in result.stderr
    assert len(box.gh_calls()) == 1


def test_file_missing_from_sha256sums_fails(box):
    box.add(DEB)
    box.add(RPM, listed=False)
    result = box.run()
    assert result.returncode != 0
    assert f"{RPM} is not in SHA256SUMS" in result.stderr


def test_an_arch_package_is_checked_against_sha256sums(box):
    box.add(DEB)
    box.add(PKG)
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert f"verified {PKG}" in result.stdout


def test_a_release_with_only_an_arch_package_passes(box):
    box.add(PKG)
    assert box.run().returncode == 0


def test_a_tampered_arch_package_fails(box):
    box.add(DEB)
    box.add(PKG)
    (box.dir / PKG).write_bytes(b"tampered")
    result = box.run()
    assert result.returncode != 0
    assert f"SHA256 of {PKG}" in result.stderr


def test_an_arch_package_missing_from_sha256sums_fails(box):
    box.add(DEB)
    box.add(PKG, listed=False)
    result = box.run()
    assert result.returncode != 0
    assert f"{PKG} is not in SHA256SUMS" in result.stderr


def test_name_must_match_whole_not_as_a_suffix(box):
    box.add(DEB, listed_as="x" + DEB)
    result = box.run()
    assert result.returncode != 0
    assert "is not in SHA256SUMS" in result.stderr


def test_duplicate_line_in_sha256sums_fails(box):
    box.add(DEB)
    box.add(DEB)
    result = box.run()
    assert result.returncode != 0
    assert "more than once" in result.stderr


def test_sha256sums_attestation_failure_fails_before_any_package_is_checked(box):
    box.add(DEB)
    box.add(RPM)
    (box.dir / DEB).write_bytes(b"tampered")
    (box.fake / "bad").write_text("SHA256SUMS\n")
    result = box.run()
    assert result.returncode != 0
    assert "no valid build provenance for SHA256SUMS" in result.stderr
    assert "SHA256 of" not in result.stderr


@pytest.mark.parametrize("tag", ["v2.0.0", "v1.5.1", "v1.5.0-1", "1.5.0", "v1.5"])
def test_release_without_sha256sums_fails_unless_legacy(box, tag):
    box.add(DEB, listed=False)
    result = box.run(tag)
    assert result.returncode != 0
    assert "no SHA256SUMS" in result.stderr
    assert box.gh_calls() == []


@pytest.mark.parametrize("tag", ["v1.5.0", "v1.4.1"])
def test_legacy_release_is_not_verified(box, tag):
    box.add(DEB, listed=False)
    result = box.run(tag)
    assert result.returncode == 0, result.stderr
    assert "legacy" in result.stdout
    assert box.gh_calls() == []


def test_empty_release_fails(box):
    (box.dir / "SHA256SUMS").write_text("")
    result = box.run()
    assert result.returncode != 0
    assert "no .deb, .rpm or .pkg.tar.zst" in result.stderr


def test_the_real_legacy_list_has_the_old_tags_only():
    with open(LEGACY, encoding="utf-8") as handle:
        tags = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    assert tags == ["v1.0.0", "v1.2.0", "v1.4.0", "v1.4.1", "v1.5.0"]


def test_the_publish_workflow_verifies_every_release_before_the_repositories_are_built():
    names = workflow_steps.step_names("publish-apt.yml")
    download = "\n".join(workflow_steps.step_lines("publish-apt.yml", "Download packages from every published release"))
    assert 'bash .github/scripts/verify-release-assets.sh "$tag"' in download
    assert names.index("Download packages from every published release") < names.index("Build the repository")


def test_the_standalone_pkgbuild_assets_are_listed_in_sha256sums_and_ignored(box):
    box.add(DEB)
    box.add(PKG)
    box.add("PKGBUILD")
    box.add("network-manager-gpclient.install")

    result = box.run()

    assert result.returncode == 0, result.stderr
    assert "verified PKGBUILD" not in result.stdout


def test_the_standalone_pkgbuild_assets_do_not_hide_a_bad_package(box):
    box.add("PKGBUILD")
    box.add(PKG)
    (box.dir / PKG).write_bytes(b"tampered")

    result = box.run()

    assert result.returncode != 0
    assert f"SHA256 of {PKG}" in result.stderr


def test_a_release_with_only_the_standalone_pkgbuild_assets_has_no_packages(box):
    box.add("PKGBUILD")
    box.add("network-manager-gpclient.install")

    result = box.run()

    assert result.returncode != 0
    assert "no .deb, .rpm or .pkg.tar.zst" in result.stderr
