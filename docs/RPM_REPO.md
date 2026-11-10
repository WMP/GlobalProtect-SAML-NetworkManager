# RPM packages and repositories

Every release has RPM packages for these distributions, for `x86_64` and
`aarch64`:

| Target | Distribution | Notes |
|---|---|---|
| `fedora43` | Fedora 43 | |
| `fedora44` | Fedora 44 | |
| `fedora45` | Fedora 45 | |
| `el10` | RHEL 10, AlmaLinux 10, Rocky Linux 10 | needs EPEL and CRB (Plasma 6, `libnma-gtk4`) |
| `leap16.0` | openSUSE Leap 16.0 | |
| `leap16.1` | openSUSE Leap 16.1 | |
| `tumbleweed` | openSUSE Tumbleweed | rolling: built against the Tumbleweed of the release day |

`rpm/targets` is the list the build, the CI matrix and the repository use.
Not supported:

- **EL 9** (RHEL/AlmaLinux/Rocky 9): it has no `webkit2gtk-4.1` and no `libsoup3`,
  which `gpauth` needs, and only Plasma 5.
- **openSUSE Leap 15.6**: end of life.

The packages are the same three as on Ubuntu:

- `network-manager-gpclient`: the service, the NetworkManager plugin, `gpclient`,
  `gpauth` (enough for `nmcli`)
- `network-manager-gpclient-gnome`: GTK3/GTK4 connection editors and the
  authentication dialog (GNOME, MATE, Cinnamon, XFCE)
- `network-manager-gpclient-plasma`: KDE Plasma 6 connection editor

No RPM distribution packages the Python module `sdbus` (Debian/Ubuntu:
`python3-sdbus`), so `network-manager-gpclient` contains it (version in
`rpm/sources`, `Provides: bundled(python3dist(sdbus))`). Do not install it with
pip: the service runs as root and uses the bundled copy.

## Using the repository

The repositories and the signing key are at
<https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/>. The packages and
the repository metadata are signed with the key of the apt repository
(`RPM-GPG-KEY-gpclient`); dnf and zypper ask you to accept it on first use.

### Fedora 43, 44, 45

```bash
sudo dnf config-manager addrepo --from-repofile=https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/fedora$(rpm -E %fedora)/network-manager-gpclient.repo
sudo dnf install network-manager-gpclient-gnome    # GNOME, MATE, Cinnamon, XFCE
# or
sudo dnf install network-manager-gpclient-plasma   # KDE Plasma 6
```

### RHEL 10, AlmaLinux 10, Rocky Linux 10

EPEL and CRB first (on RHEL, enable `codeready-builder-for-rhel-10-$(arch)-rpms`
with `subscription-manager` instead of `crb`, and install `epel-release` as the
[EPEL documentation](https://docs.fedoraproject.org/en-US/epel/) says):

```bash
sudo dnf install epel-release
sudo dnf config-manager --set-enabled crb
sudo dnf config-manager --add-repo https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/el10/network-manager-gpclient.repo
sudo dnf install network-manager-gpclient-gnome    # or network-manager-gpclient-plasma
```

### openSUSE Leap 16.0, 16.1 and Tumbleweed

```bash
sudo zypper addrepo https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/tumbleweed/network-manager-gpclient.repo
# Leap 16.0: .../rpm/leap16.0/network-manager-gpclient.repo
# Leap 16.1: .../rpm/leap16.1/network-manager-gpclient.repo
sudo zypper refresh                                 # accept the key
sudo zypper install network-manager-gpclient-gnome  # or network-manager-gpclient-plasma
```

### Single files

The release assets are the same RPMs, but unsigned: only the repository signs
them (`network-manager-gpclient-1.5.0-1.fc44.x86_64.rpm`; the dist tag names the
target). Prefer the repository. Install the core package and one GUI package
together:

```bash
sudo dnf install ./network-manager-gpclient-1.5.0-1.fc44.x86_64.rpm \
                 ./network-manager-gpclient-gnome-1.5.0-1.fc44.x86_64.rpm
# openSUSE: sudo zypper install --allow-unsigned-rpm <the same two files>
```

### Removal

```bash
sudo dnf remove network-manager-gpclient            # removes the GUI packages too
sudo rm /etc/yum.repos.d/network-manager-gpclient.repo
# openSUSE
sudo zypper remove network-manager-gpclient
sudo zypper removerepo network-manager-gpclient
```

## Differences from the Ubuntu packages

- Neither the RPMs nor the Debian package change `/etc/nsswitch.conf`
  (Debian packages before 2.0.0 added `resolve` to its `hosts:` line). Fedora
  and EL manage that file with `authselect`, and Fedora uses systemd-resolved
  by default.
- openSUSE does not use systemd-resolved by default; the VPN DNS then follows
  what vpnc-script does without it. This is not tested with a real VPN yet.
- On openSUSE the service and the auth dialog start with the versioned Python
  of the build (`/usr/bin/python3.13`), which has the bundled `sdbus`. On
  Tumbleweed, install the next release of the plugin after a Python change.
- The Plasma plugin links plasma-nm's private libraries. After a Plasma update
  within a release (Fedora, Tumbleweed) it can fail to load until the next
  release of the plugin is built against the new Plasma.

## Distribution upgrades

The Fedora and Leap `.repo` files use `$releasever` (`.../rpm/fedora$releasever/`),
so after `dnf system-upgrade` or a Leap upgrade they serve the build for the new
release. They have `skip_if_unavailable=1`: when a release leaves the supported
list (`rpm/targets`), its directory goes away and dnf and zypper skip the
repository with a warning (`zypper refresh` then exits with 4).

The repository keeps the newest two versions of every package (for
`dnf downgrade`); older ones stay in the GitHub releases.

## Building

```bash
git submodule update --init --recursive
rpm/build.sh fedora44 amd64                 # -> output/fedora44-amd64/*.rpm
rpm/build.sh --install-test fedora44 amd64  # install them in a fresh container and check them
```

`rpm/build.sh` needs Docker and runs the build in a fresh container of the
target's image (`rpm/targets`). Another architecture needs QEMU binfmt. The
version comes from `debian/changelog`, and so does the RPM `%changelog`
(`rpm/debian_changelog.py`).

CI (`build-release.yml`, job `build-rpm`) builds every target for both
architectures on every pull request, checks the file names and installs the
packages in a fresh container (`rpm/install-test.sh`). A pull request's
packages have the release `1.fc44.pr<PR>.<run>`, above the released `1.fc44`:
`scripts/install-pr-build.sh <PR>` installs them on Fedora, EL 10 and openSUSE.

## Releasing

Nothing to do besides the tag (see [APT_REPO.md](APT_REPO.md)): the release
job uploads the RPMs with the `.deb` files, and `publish-apt.yml` rebuilds the
apt repository and the RPM repositories from all published releases
(`.github/scripts/build-rpm-repo.sh`). It signs every RPM and every
`repodata/repomd.xml` with the apt key (`APT_GPG_PRIVATE_KEY`, RSA), so a key
rotation is the one described in [APT_REPO.md](APT_REPO.md); the repository
signs the RPMs again on every publish. Before it signs, it checks every RPM
against the release's `SHA256SUMS` and its build provenance (see
"Verifying packages" in [APT_REPO.md](APT_REPO.md)).

The release job waits for every Ubuntu and RPM build: a release has all its
packages or is not made. When one build fails on the tag (Tumbleweed changes
every day), fix it or re-run the failed jobs; the release then continues.
