# Build Instructions

## Prerequisites

### Ubuntu 22.04 / 24.04 (Plasma 5 / Qt5 / KF5)

Install build dependencies:
```bash
sudo apt-get install -y \
    build-essential debhelper pkg-config \
    libglib2.0-dev libnm-dev \
    libgtk-3-dev libgtk-4-dev libnma-dev libnma-gtk4-dev \
    cmake extra-cmake-modules \
    qtbase5-dev libkf5networkmanagerqt-dev libkf5i18n-dev \
    libkf5service-dev libkf5widgetsaddons-dev \
    curl libssl-dev libdbus-1-dev \
    libopenconnect-dev libwebkit2gtk-4.1-dev
```

Note: `libnma-gtk4-dev` may not be available on Ubuntu 22.04 - the build will skip GTK4 editor in that case.

### Ubuntu 26.04 (Plasma 6 / Qt6 / KF6)

```bash
sudo apt-get install -y \
    build-essential debhelper pkg-config \
    libglib2.0-dev libnm-dev \
    libgtk-3-dev libgtk-4-dev libnma-dev libnma-gtk4-dev \
    cmake extra-cmake-modules \
    qt6-base-dev qt6-base-dev-tools \
    libkf6networkmanagerqt-dev libkf6i18n-dev \
    libkf6service-dev libkf6widgetsaddons-dev libkf6coreaddons-dev \
    plasma-nm \
    curl libssl-dev libdbus-1-dev \
    libopenconnect-dev libwebkit2gtk-4.1-dev python3-sdbus
```

The Plasma plugin's CMakeLists auto-detects Qt6/KF6 if both Qt5 and Qt6 are
installed. To force a specific major version pass `-DQT_MAJOR_VERSION=6` (or 5)
to cmake.

### Ubuntu 24.04 with Plasma 6 (KDE neon)

KDE neon is Ubuntu 24.04 with Plasma 6 / KF6 from `https://archive.neon.kde.org/user`
(suite `noble`). The noble archive has no KF6, so `network-manager-gpclient-plasma-6`
is a second build for 24.04, only of the Plasma plugin (no GNOME plugins, no Rust):
`debian/control.ubuntu24.04-neon` (one binary package) with `Dockerfile.ubuntu24.04-neon`,
which sets the repository up with `.github/scripts/neon-repo.sh` (it checks the
fingerprint of the repository key). `debian/rules` recognises such a control file by
its lack of the core package. CI builds it after the main 24.04 build, with the same
version (`~noble1`), for `amd64` only: neon has no `plasma-nm` for `arm64`. The control
file says so (`Architecture: amd64`) and the workflow reads it from there: a variant is built
for the architectures its binary packages list (`any` is all of them). Locally:

```bash
docker build -t gpclient-builder:ubuntu24.04-neon -f Dockerfile.ubuntu24.04-neon .
docker run --rm -v $(pwd)/output:/output gpclient-builder:ubuntu24.04-neon bash -c \
  'cp debian/control.ubuntu24.04-neon debian/control && dpkg-buildpackage -us -uc -b && cp /build/*.deb /output/'
```

### Rust Installation

This project uses GlobalProtect-openconnect which requires **Rust 1.91+** (the version in its rust-toolchain.toml).
The Makefile will automatically install Rust via rustup if not present.

To install manually:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.91
source $HOME/.cargo/env
```

The packages are built with `cargo auditable build` ([cargo-auditable](https://github.com/rust-secure-code/cargo-auditable)),
so `gpclient` and `gpauth` carry their dependency list (`cargo audit bin`, syft and osv-scanner read it). The images
and `rpm/container-build.sh` install it; `make` uses it when `cargo install cargo-auditable --locked` was run, and builds without it otherwise.

## Building

### Build Debian packages (recommended):

```bash
# Build for all supported Ubuntu versions (uses Docker)
./build-all.sh

# Build for specific version
./build-all.sh 26.04
./build-all.sh 24.04
./build-all.sh 22.04

# Build a release variant (Dockerfile.ubuntu<version>-<variant>), for example KDE neon (amd64 only; skipped on arm64 when building all)
./build-all.sh 24.04-neon

# Add the version suffix that CI adds to pull request builds.
# The ~<codename>1 tag is added only with a suffix: a plain local build stays e.g. 1.5.0-1, above the released 1.5.0-1~noble1.
VERSION_SUFFIX=+pr54.1 ./build-all.sh 24.04
```

Packages will be in `output/ubuntu26.04/`, `output/ubuntu24.04/`, `output/ubuntu24.04-neon/` or `output/ubuntu22.04/`.

The script keeps Cargo's downloads and the Rust build in the Docker volumes
`gpclient-cargo-cache-<version>` and `gpclient-target-cache-<version>`, so a
second build is faster. If a build fails with `failed to create directory
.../target/release ... Permission denied`, the target volume belongs to root
(it was made by a version of the script before issue #139): remove it with
`docker volume rm gpclient-target-cache-<version>` and build again.

### Build RPM packages:

```bash
rpm/build.sh fedora44 amd64     # targets: rpm/targets; output: output/fedora44-amd64/
```

Details: [RPM_REPO.md](RPM_REPO.md).

### Build Arch Linux packages:

```bash
arch/build.sh                  # output: output/arch-x86_64/ (x86_64 only)
```

Details: [ARCH_REPO.md](ARCH_REPO.md).

### Vendored crates and offline build:

A package build needs no network for the Rust crates. `scripts/vendor-crates.sh` runs
`cargo vendor --locked` for `external/GlobalProtect-openconnect` into `vendor/crates` and
writes the source replacement to `vendor/config.toml` (`vendor/` is not in git).
When `vendor/config.toml` exists, `debian/rules` and the RPM spec build with
`cargo auditable build --locked --offline --config vendor/config.toml`; without it they
download the crates, so a plain `dpkg-buildpackage` still works.
CI makes `vendor/` before every package build and runs the Debian build container with
`--network none`. `build-all.sh` makes it when `cargo` is on the PATH, and then also
uses `--network none`.

```bash
scripts/vendor-crates.sh
```

The workflow `reproducible.yml` (weekly and by hand) builds the Ubuntu 24.04 amd64
packages twice in fresh containers and compares the `.deb` files. On a difference it
fails and uploads a diffoscope report as the artifact `diffoscope-report`.
It cannot find a difference that depends on the build environment: both builds use the
same image, build path, user and kernel.

### Build individual components (for development):

```bash
make gnome-plugins  # Build GNOME/GTK plugins
make gpclient       # Build gpclient binary
make gpauth         # Build gpauth binary
```

### Build Plasma plugin:

```bash
cd plugins/plasma
./build.sh
```

### Build with dpkg-buildpackage (local build):

```bash
dpkg-buildpackage -us -uc -b
```

## Installing

### From built packages:

Install two packages from `output/ubuntu24.04/` (or `ubuntu22.04`):
1. **network-manager-gpclient** - core package (required)
2. **network-manager-gpclient-gnome** - for GNOME/GTK desktops, or
   **network-manager-gpclient-plasma** - for KDE Plasma, or
   **network-manager-gpclient-plasma-6** - for KDE neon (Ubuntu 24.04 with Plasma 6, `amd64`)

```bash
sudo dpkg -i <packages>.deb
sudo apt-get install -f  # install dependencies
```

### For development (without packaging):

```bash
sudo make install
sudo systemctl restart NetworkManager
```

## Docker Build

The project uses Docker for reproducible builds:

```bash
# Build Docker image for Ubuntu 24.04
docker build -t gpclient-builder:ubuntu24.04 --build-arg UBUNTU=24.04 -f Dockerfile.ubuntu .

# Run build
docker run --rm -v $(pwd)/output:/output gpclient-builder:ubuntu24.04
```
