# APT repository (GitHub Pages)

Packages are published as a signed apt repository at
<https://wmp.github.io/GlobalProtect-SAML-NetworkManager/>, so installing and
updating works like with any PPA - and `apt` resolves the dependency between the
core service and the GUI packages, which downloading individual `.deb` files
never could ([#5](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/5)).

## For users

```bash
# old one-line setup from earlier instructions, if present
sudo rm -f /etc/apt/sources.list.d/gpclient.list /usr/share/keyrings/gpclient-archive-keyring.gpg
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSLo /etc/apt/keyrings/gpclient-archive-keyring.gpg \
  https://wmp.github.io/GlobalProtect-SAML-NetworkManager/gpclient-archive-keyring.gpg

sudo tee /etc/apt/sources.list.d/gpclient.sources > /dev/null <<EOF
Types: deb
URIs: https://wmp.github.io/GlobalProtect-SAML-NetworkManager
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/gpclient-archive-keyring.gpg
EOF

sudo apt update
sudo apt install network-manager-gpclient-gnome    # GNOME, MATE, Cinnamon, XFCE
sudo apt install network-manager-gpclient-plasma   # KDE Plasma
sudo apt install network-manager-gpclient-plasma-6 # KDE neon (Ubuntu 24.04 with Plasma 6), amd64 only
```

On KDE neon use `network-manager-gpclient-plasma-6`: it is in the `noble` suite
next to the others, built against the neon repository, and conflicts with
`network-manager-gpclient-plasma` (Plasma 5): only one Plasma generation can use the
plugin.

The `Architectures` line makes apt skip foreign-architecture indexes such as `i386`,
which the repository does not publish. The key file is binary (not armored), so
`Signed-By` takes it as it is on Ubuntu 22.04 and later without extra packages.

Migration: earlier versions of these instructions created a one-line
`/etc/apt/sources.list.d/gpclient.list` and the key
`/usr/share/keyrings/gpclient-archive-keyring.gpg`. The `sudo rm -f` line of the commands
above deletes them (and does nothing on a new system). If you keep the old `.list`
file next to the new `.sources` file, `apt update` stops with
`E: Conflicting values set for option Signed-By`. The earlier instructions also offered
a deb822 form with a free file name and `Signed-By: /usr/share/keyrings/gpclient-archive-keyring.gpg`:
if you used it under a name other than `gpclient.sources`, delete that file too (the same
conflict).

`apt changelog network-manager-gpclient` shows the changelog: it is published under
`/changelogs/<component>/<prefix>/<source>/<source>_<version>_changelog` (not next to
the packages) and announced by the `Changelogs` field of the `Release` file of every
suite that has at least one changelog (a suite without any gets no field). The field is
per suite, not per package version: `apt changelog` for a version whose changelog could
not be extracted (a package without one, or one that cannot be unpacked) gets a 404.

Supported suites are Ubuntu release codenames: `jammy` (22.04), `noble` (24.04),
`resolute` (26.04). Both `amd64` and `arm64` are built; apt picks the one matching
your system. `oracular` (24.10, end of life) keeps the packages of earlier releases
and gets no new ones.

Removal:

```bash
# the desktop packages depend on the core package and are removed with it
sudo apt remove network-manager-gpclient
sudo rm -f /etc/apt/sources.list.d/gpclient.sources /etc/apt/keyrings/gpclient-archive-keyring.gpg /etc/apt/sources.list.d/gpclient.list /usr/share/keyrings/gpclient-archive-keyring.gpg
sudo apt update
```

### Verifying packages

Every release after v1.5.0 has a `SHA256SUMS` asset and a build provenance
attestation (SLSA, signed through Sigstore) for `SHA256SUMS` and for each `.deb`
`.rpm` and `.pkg.tar.zst` file. The attestation names the workflow run of this repository that
built the file. To check a file you downloaded from a GitHub release (replace
`vX.Y.Z` with the tag of that release):

```bash
gh attestation verify SHA256SUMS --repo WMP/GlobalProtect-SAML-NetworkManager \
    --signer-workflow WMP/GlobalProtect-SAML-NetworkManager/.github/workflows/build-release.yml \
    --source-ref refs/tags/vX.Y.Z
sha256sum --ignore-missing -c SHA256SUMS            # in the download directory
gh attestation verify network-manager-gpclient_*.deb --repo WMP/GlobalProtect-SAML-NetworkManager \
    --signer-workflow WMP/GlobalProtect-SAML-NetworkManager/.github/workflows/build-release.yml \
    --source-ref refs/tags/vX.Y.Z
```

`--source-ref` is needed: without it, the `SHA256SUMS` of an older release, or a
file of a pull request build, would also pass.

`publish-apt.yml` verifies the attestation of `SHA256SUMS` of every release (one
API call per release) and the SHA256 of every package against it before it signs
the package into the apt, RPM and pacman repositories: a failure, or a release without
`SHA256SUMS`, stops the run. Only the releases listed in
`.github/legacy-unattested-releases.txt` (v1.5.0 and older) are published without
the checks. The attestation must come from `build-release.yml` and the tag of the
release (`--signer-workflow`, `--source-ref`). `scripts/install-pr-build.sh`
checks the build provenance of the test packages of a pull request with
`gh attestation verify` when `gh` is installed, logged in and 2.51 or newer (it has no
`SHA256SUMS`; the source ref of a pull request build is its merge ref).

## For the maintainer

### Cutting a release

1. Add an entry to `debian/changelog` with the new version, native from 2.0.0 on
   (`network-manager-gpclient (2.0.0) unstable; urgency=medium`, no `-1`; see
   [PACKAGING.md](PACKAGING.md#version-in-debianchangelog)).
2. Commit, then tag and push. The tag is `v` and the version of the top changelog
   entry (`2.0.0` gives `v2.0.0`); `build-release.yml` stops at once when they
   differ, or when a version from 2.0.0 on has a Debian revision (`2.0.0-1`):
   ```bash
   git tag v2.0.0
   git push origin v2.0.0
   ```
3. `build-release.yml` builds every Ubuntu release for `amd64` and `arm64`, creates the GitHub
   release with the `.deb` and `.rpm` files and `SHA256SUMS` attached (the build jobs
   attest each file), and then calls `publish-apt.yml`,
   which republishes the repository.

The build appends the release codename to the version, so the same upstream
version never means different binaries:
`network-manager-gpclient_2.0.0~noble1_amd64.deb`.

### How publishing works

**GitHub Releases are the source of truth.** `publish-apt.yml` downloads the
`.deb` assets of *every* published release (drafts and prereleases are skipped),
rebuilds the entire repository with `.github/scripts/build-apt-repo.sh`, and
uploads the result as a GitHub Pages artifact. Nothing is committed to the
repository and there is no `gh-pages` branch, so:

- every run is idempotent - re-running fixes a broken deploy,
- the repository can be recreated from scratch (after a key rotation, say),
- package binaries never enter the git history.

**The repository keeps only the newest versions.** GitHub Pages limits a site to 1 GB,
so the script publishes, for each suite, architecture and binary package (`Package`
field; `Architecture: all` is a group of its own), only the newest `APT_KEEP_VERSIONS`
versions. The default is 2: the current version and the previous one, to go back to.
Versions are ordered like dpkg orders them (epoch, `~`, revision), not by file name.
The build logs every `.deb` it leaves out (`dropping ...`). The dropped files are not in
the pool, the `Packages` indexes or the changelogs, but stay as assets of their GitHub
release, so a user can still install an old version by hand. `APT_KEEP_VERSIONS` must
be a whole number from 1 to 9999 (no sign, no leading zero, not empty), otherwise the
script stops:

```bash
APT_KEEP_VERSIONS=3 bash .github/scripts/build-apt-repo.sh incoming site "$KEY"
```

Republish by hand at any time:

```bash
gh workflow run publish-apt.yml
```

Suite assignment comes from the `~<codename>1` version suffix, with a fallback
for the `_ubuntu24.04.deb` filenames used by v1.0.0 and v1.2.0. An unrecognised
filename fails the build rather than being quietly skipped.

### Testing the repository locally

No GitHub involved - build it, serve it, install from it:

```bash
gh release download v1.2.0 -D /tmp/apt-test/incoming --pattern '*.deb'

docker run --rm -it -v "$PWD/.github":/repo/.github:ro \
    -v /tmp/apt-test/incoming:/incoming:ro ubuntu:24.04 bash
# in the container:
apt-get update && apt-get install -y dpkg-dev apt-utils gnupg python3
export GNUPGHOME=/tmp/gnupg && mkdir -p -m 700 $GNUPGHOME
gpg --batch --passphrase '' --pinentry-mode loopback \
    --quick-generate-key 'test <test@example.invalid>' rsa3072 sign never
KEY=$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/ {print $10; exit}')
# the repository URL goes into the Release files (Changelogs:) and the landing page
APT_REPO_URL=http://127.0.0.1:8000 bash /repo/.github/scripts/build-apt-repo.sh /incoming /site "$KEY"

cd /site && python3 -m http.server 8000 &
install -m 644 /site/gpclient-archive-keyring.gpg /usr/share/keyrings/gpclient.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gpclient.gpg] http://127.0.0.1:8000 noble main" \
    > /etc/apt/sources.list.d/gpclient.list
apt-get update
apt-get install -s network-manager-gpclient-gnome | grep network-manager-gpclient
```

The last command must show `Inst network-manager-gpclient` **and**
`Inst network-manager-gpclient-gnome`: apt pulling the core package in as a
dependency is the whole point. `apt-get update` must not print any warning about
signatures or an unauthenticated repository.

`APT_REPO_URL` (default `https://wmp.github.io/GlobalProtect-SAML-NetworkManager`) is the
address the repository is served from. Without it `apt changelog` and the install commands
on the landing page point at the public site instead of your test server. The changelog of a
package is taken from `changelog.Debian.gz`, or from `changelog.gz` in a native package;
a package without one, or that cannot be unpacked (reported as `ERROR`), does not stop the build.
When several packages of one source share the file, the one named like the source (the core
package) provides it, otherwise the first in file name order. `APT_REPO_URL` must be an
`http(s)` URL of the characters `A-Z a-z 0-9 . _ ~ : / % -` only; anything else stops the
build, as it is unsafe on the landing page.

Leaving out the key id builds an unsigned repository, which apt only accepts
with `[trusted=yes]` - useful for a quick structural check, never for publishing.

### The signing key

The repository is signed with a GPG key whose only purpose is signing this
archive. The private key lives in the repository secret `APT_GPG_PRIVATE_KEY`
(ASCII armor, no passphrase - CI has to sign unattended; the secret store is the
security boundary). The public key is published as
`gpclient-archive-keyring.gpg` at the root of the site.

Rotating it:

```bash
gpg --batch --passphrase '' --pinentry-mode loopback \
    --quick-generate-key 'WMP GlobalProtect apt repository <wmp@users.noreply.github.com>' \
    rsa4096 sign never
FPR=$(gpg --batch --with-colons --list-secret-keys --list-options show-only-fpr-mbox \
    | head -1 | awk '{print $1}')
gpg --armor --export-secret-keys "$FPR" | gh secret set APT_GPG_PRIVATE_KEY
gh workflow run publish-apt.yml
```

Users have to fetch the new public key (the install command above does that);
until they do, `apt update` will complain about a signature it cannot verify.
Keep a backup of the private key outside the repository - in a password manager,
never in git.

### GitHub Pages configuration

Pages is served from the GitHub Actions deployment (Settings → Pages → Source:
GitHub Actions), configured once with:

```bash
gh api -X POST repos/WMP/GlobalProtect-SAML-NetworkManager/pages -f build_type=workflow
```

A public repository means a public site - which is what an apt repository is
anyway.

### Repository settings only the owner can set

These are in the GitHub UI and no workflow can set them. Until they are set, a
person with write access to the repository can still reach the signing key or
move a release tag.

- **Signing key in an environment.** Move `APT_GPG_PRIVATE_KEY` from the
  repository secrets to a secret of the `github-pages` environment (Settings →
  Environments → github-pages), and restrict that environment to the tags `v*`
  (Deployment branches and tags → Selected branches and tags → Add tag rule
  `v*`). `publish-apt.yml` already runs in that environment. Delete the
  repository-level secret afterwards.
- **Tag ruleset.** Settings → Rules → Rulesets → New tag ruleset, target `v*`:
  restrict creations, updates and deletions to the owner, and block force pushes.
- **Immutable releases.** Settings → General → Releases → "Enable release
  immutability". A published release then keeps its assets and its tag, so
  `SHA256SUMS` and the attested files cannot be replaced afterwards.
- **Code scanning and Scorecard.** `codeql.yml`, `zizmor.yml` and `scorecard.yml`
  need Settings → Advanced Security → Code scanning to be enabled for the results
  to show in the Security tab.

### Not implemented

There is no testing channel for prereleases in the apt repository. Test builds of
pull requests are published as the prerelease `pr-<N>` (removed when the PR
closes) by `.github/workflows/pr-test-packages.yml`, which adds a `Test packages`
check to the PR with the install command
(`bash <(curl -fsSL https://raw.githubusercontent.com/WMP/GlobalProtect-SAML-NetworkManager/main/scripts/install-pr-build.sh) <N>`).
`publish-apt.yml` skips prereleases, so these packages never reach the repository.
Their version carries the PR and run number after the codename
(`1.4.2-1~noble1+pr24.57`), which sorts above the release's `1.4.2-1~noble1`, so
installing one over the released version is an upgrade for apt. Going back to the
release is a downgrade:
`sudo apt install --allow-downgrades <package>/$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") ...`.
Name the suite after each package (`network-manager-gpclient/noble`): the
installed test build is newer than the release, so apt's own candidate is that
build, which no repository has, and `--reinstall` fails with "it cannot be
downloaded". The suite of our repository is the codename, and Ubuntu's archive
has no such package, so the suite selects the newest release.
If testing builds for users become a habit, the natural shape is a second suite
(`<codename>-testing`) fed by prereleases rather than a separate repository.
