# Arch Linux packages and repository

Every release has Arch Linux packages for `x86_64`. Official Arch Linux has no
other architecture. **Arch Linux ARM** is another distribution and is not
supported. Derivatives such as Manjaro and EndeavourOS are not tested.

The packages are the same three as on the other distributions, with the same
files:

- `network-manager-gpclient`: the service, the NetworkManager plugin, `gpclient`,
  `gpauth` (enough for `nmcli`)
- `network-manager-gpclient-gnome`: GTK3/GTK4 connection editors and the
  authentication dialog (GNOME, MATE, Cinnamon, XFCE)
- `network-manager-gpclient-plasma`: KDE Plasma 6 connection editor

`network-manager-gpclient` conflicts with the AUR packages
`globalprotect-openconnect` and `globalprotect-openconnect-git`, which install
the same `/usr/bin/gpclient` and `/usr/bin/gpauth`. Remove them first.

`python-sdbus` is not in the official repositories (the module `sdbus` is the
D-Bus library of the service), so `network-manager-gpclient` contains it
(version in `rpm/sources`, shared with the RPM build). Do not install it with
pip: the service runs as root and uses the bundled copy.

## Using the repository

The repository `gpclient` and the signing key are at
<https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/>. The packages and
the database are signed with the key of the apt repository, so you tell pacman
once to trust it. The fingerprint is on the
[landing page](https://wmp.github.io/GlobalProtect-SAML-NetworkManager/); compare it
with the one printed below:

```bash
curl -fsSLo /tmp/gpclient.gpg https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/GPG-KEY-gpclient
gpg --show-keys --with-colons /tmp/gpclient.gpg | awk -F: '/^fpr:/ {print $10; exit}'   # compare with the landing page
sudo pacman-key --add /tmp/gpclient.gpg
sudo pacman-key --lsign-key "$(gpg --show-keys --with-colons /tmp/gpclient.gpg | awk -F: '/^fpr:/ {print $10; exit}')"
```

Then add the repository to `/etc/pacman.conf`, above or below the official ones:

```ini
[gpclient]
SigLevel = Required
Server = https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/$arch
```

```bash
sudo pacman -Syu network-manager-gpclient-gnome    # GNOME, MATE, Cinnamon, XFCE
# or
sudo pacman -Syu network-manager-gpclient-plasma   # KDE Plasma 6
```

`SigLevel = Required` makes pacman refuse a package or a database without a
valid signature of a key you trust. Do not weaken it. `-Syu` is a full upgrade
with the install: on Arch Linux do not refresh the databases (`-Sy`) without
upgrading.

### Build from source with makepkg

The pacman repository above is the signed, prebuilt way. To build the packages yourself, as
with any package of the Arch ecosystem, clone the tag and run `makepkg` in `arch/`:

```bash
git clone --branch v2.0.0 https://github.com/WMP/GlobalProtect-SAML-NetworkManager
cd GlobalProtect-SAML-NetworkManager/arch
makepkg -si                  # builds the three packages and installs them (asks for the dependencies)
```

`arch/PKGBUILD` is the same file the CI uses; without the environment variables of
`arch/build.sh` it is in user mode. That mode needs the network: it fetches the project
and its three submodules from GitHub (pinned by tag and commit), `python-sdbus` from PyPI
and the Rust crates from crates.io (`cargo fetch --locked` in `prepare()`). The build
takes a while (Rust, Qt). The same two files are attached to every release (`PKGBUILD`
and `network-manager-gpclient.install`, listed in `SHA256SUMS`): download both into one
directory and run `makepkg -si` there; use the file of the release you want, because it
builds that version. The packages from `makepkg` are not signed by this project.

### Single files

The release assets are the same packages, but unsigned: only the repository
signs them (`network-manager-gpclient-1.5.0-1-x86_64.pkg.tar.zst`). Prefer the
repository. Install the core package and one GUI package together:

```bash
sudo pacman -U network-manager-gpclient-1.5.0-1-x86_64.pkg.tar.zst \
               network-manager-gpclient-gnome-1.5.0-1-x86_64.pkg.tar.zst
```

### Removal

```bash
sudo pacman -Rs network-manager-gpclient-gnome network-manager-gpclient   # or the plasma package
# then remove the [gpclient] section from /etc/pacman.conf and, if you want, the key:
sudo pacman-key --delete <fingerprint>
```

`pacman -R network-manager-gpclient` alone fails while a GUI package is
installed: they depend on it.

## Versions

A release has the version of `debian/changelog` (`1.5.0-1`: upstream version
`pkgver`, Debian revision `pkgrel`). The repository publishes only the newest
version of every package: a pacman database lists one version per package. Older
versions stay in the GitHub releases (`pacman -U` takes them).

The packages of a pull request have `pkgver` `1.5.0.pr<PR>.<run>`
(`makepkg` allows neither `-` nor `:` in `pkgver`, and only `int[.int]` in
`pkgrel`, so the marker goes into `pkgver`). `vercmp` orders them: such a build
is newer than the release `1.5.0-1` and every earlier one (`vercmp 1.5.0.pr1.2-1 1.5.0-1`
prints `1`), as the `.deb` and RPM builds of a pull request are, so pacman installs it
over the release; builds of one pull request follow the run number. To go back to the
release, `sudo pacman -Syyuu` (downgrades to the repository's version).
`scripts/install-pr-build.sh <PR>` installs them on Arch Linux.

## Differences from the Ubuntu packages

- No SELinux policy (Arch has none by default) and no change of
  `/etc/nsswitch.conf`. pacman reloads systemd itself; the install script
  reloads D-Bus and stops an old running service.
- The Plasma plugin links plasma-nm's private libraries. Arch is rolling: after a
  Plasma update it can fail to load until the next release of the plugin is built
  against the new Plasma.
- `network-manager-gpclient` depends on the exact minor version of Python that
  built it (`python>=3.14 python<3.15`), because the bundled `sdbus` is a C extension in
  `/usr/lib/python3.X/site-packages`. After a Python update on Arch, pacman reports a
  dependency conflict until the next release of the packages is built with the new
  Python. Wait for it, or remove the packages; do not force the update past the
  conflict, the service would fail with an `ImportError`.
- `gpclient` and `gpauth` link the libraries of the system (`webkit2gtk-4.1`,
  `gnutls`, `libxml2`, ...), so after a soname change of one of them
  (`error while loading shared libraries`) wait for the next release or build the
  package yourself.

## Building

```bash
git submodule update --init --recursive
bash scripts/vendor-crates.sh                # the crates, once
arch/build.sh                                # -> output/arch-x86_64/*.pkg.tar.zst
arch/build.sh --install-test                 # install them in a fresh container and check them
```

`arch/build.sh` needs Docker and runs `makepkg` as a normal user in a fresh
`archlinux:base-devel` container (x86_64: `--platform linux/amd64`, so the build
on another machine needs QEMU binfmt). The recipe is `arch/PKGBUILD`, the hooks are
`arch/network-manager-gpclient.install`, and the package version comes from
`debian/changelog` (`rpm/debian_changelog.py`).

`arch/standalone-test.sh` tests the user mode: it makes local git mirrors of the checkout
and its submodules, points a copy of the PKGBUILD at them (only the URLs change), runs
plain `makepkg -s` in a fresh container and installs the result. CI runs it as the job
`build-arch-standalone` (pull requests and manual runs with `arch_linux`; nothing is
published). `tests/unit/test_arch_build.py` keeps the values in the PKGBUILD (version,
submodule commits, URLs, `python-sdbus`) equal to `debian/changelog`, the submodules,
`.gitmodules` and `rpm/sources`: change them together at a release or a submodule bump.

CI (`build-release.yml`, job `build-arch`) builds the packages on every pull
request, checks the file names, installs the packages in a fresh container
(`arch/install-test.sh`) and attests them. A manual run builds them with the input
`arch_linux`.

## Releasing

Nothing to do besides the tag (see [APT_REPO.md](APT_REPO.md)): the release job
uploads the packages with the `.deb` and `.rpm` files, and `publish-apt.yml`
rebuilds the apt, RPM and pacman repositories from all published releases
(`.github/scripts/build-arch-repo.sh`). It runs `repo-add` in an `archlinux`
container, without the key, and signs on the host with the apt key
(`APT_GPG_PRIVATE_KEY`): every package, `gpclient.db` and `gpclient.files` (detached
`.sig`). The two database files are plain copies, not symlinks, because GitHub Pages
does not keep symlinks reliably. Before it signs, it checks every package against the
release's `SHA256SUMS` and its build provenance (see "Verifying packages" in
[APT_REPO.md](APT_REPO.md)). A key rotation is the one of [APT_REPO.md](APT_REPO.md);
users then add the new key with `pacman-key` as above.

The release job waits for every build: a release has all its packages or is
not made. Releases before Arch Linux support have no packages; the repository
is built from the releases that have some.
