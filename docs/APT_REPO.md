# APT repository (GitHub Pages)

Packages are published as a signed apt repository at
<https://wmp.github.io/GlobalProtect-SAML-NetworkManager/>, so installing and
updating works like with any PPA - and `apt` resolves the dependency between the
core service and the GUI packages, which downloading individual `.deb` files
never could ([#5](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/5)).

## For users

```bash
curl -fsSL https://wmp.github.io/GlobalProtect-SAML-NetworkManager/gpclient-archive-keyring.gpg \
  | sudo tee /usr/share/keyrings/gpclient-archive-keyring.gpg > /dev/null

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/gpclient-archive-keyring.gpg] https://wmp.github.io/GlobalProtect-SAML-NetworkManager $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/gpclient.list

sudo apt update
sudo apt install network-manager-gpclient-gnome    # GNOME, MATE, Cinnamon, XFCE
sudo apt install network-manager-gpclient-plasma   # KDE Plasma
```

Same thing in deb822 format, if you prefer `/etc/apt/sources.list.d/*.sources`:

```
Types: deb
URIs: https://wmp.github.io/GlobalProtect-SAML-NetworkManager
Suites: noble
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/gpclient-archive-keyring.gpg
```

Supported suites are Ubuntu release codenames: `jammy` (22.04), `noble` (24.04),
`resolute` (26.04). Only `amd64` is built - `arch=amd64` in the source line keeps
apt from looking for indexes that do not exist.

Removal:

```bash
sudo apt remove network-manager-gpclient network-manager-gpclient-gnome network-manager-gpclient-plasma
sudo rm /etc/apt/sources.list.d/gpclient.list /usr/share/keyrings/gpclient-archive-keyring.gpg
sudo apt update
```

## For the maintainer

### Cutting a release

1. Add an entry to `debian/changelog` with the new version (`1.4.0-1`).
2. Commit, then tag and push:
   ```bash
   git tag v1.4.0
   git push origin v1.4.0
   ```
3. `build-release.yml` builds all three Ubuntu releases, creates the GitHub
   release with the `.deb` files attached, and then calls `publish-apt.yml`,
   which republishes the repository.

The build appends the release codename to the version, so the same upstream
version never means different binaries:
`network-manager-gpclient_1.4.0-1~noble1_amd64.deb`.

### How publishing works

**GitHub Releases are the source of truth.** `publish-apt.yml` downloads the
`.deb` assets of *every* published release (drafts and prereleases are skipped),
rebuilds the entire repository with `.github/scripts/build-apt-repo.sh`, and
uploads the result as a GitHub Pages artifact. Nothing is committed to the
repository and there is no `gh-pages` branch, so:

- every run is idempotent - re-running fixes a broken deploy,
- the repository can be recreated from scratch (after a key rotation, say),
- package binaries never enter the git history.

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
bash /repo/.github/scripts/build-apt-repo.sh /incoming /site "$KEY"

cd /site && python3 -m http.server 8000 &
install -m 644 /site/gpclient-archive-keyring.gpg /usr/share/keyrings/gpclient.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/gpclient.gpg] http://127.0.0.1:8000 noble main' \
    > /etc/apt/sources.list.d/gpclient.list
apt-get update
apt-get install -s network-manager-gpclient-gnome | grep network-manager-gpclient
```

The last command must show `Inst network-manager-gpclient` **and**
`Inst network-manager-gpclient-gnome`: apt pulling the core package in as a
dependency is the whole point. `apt-get update` must not print any warning about
signatures or an unauthenticated repository.

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

### Not implemented

There is no testing channel for prereleases. Test builds for issue reporters
still come from workflow artifacts (`gh run list`, or the Actions UI). If that
becomes a habit, the natural shape is a second suite (`<codename>-testing`) fed
by prereleases rather than a separate repository.
