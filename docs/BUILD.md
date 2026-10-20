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

This project uses GlobalProtect-openconnect which requires **Rust 1.89+** (the version in its rust-toolchain.toml).
The Makefile will automatically install Rust via rustup if not present.

To install manually:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.89
source $HOME/.cargo/env
```

## Building

### Build Debian packages (recommended):

```bash
# Build for all supported Ubuntu versions (uses Docker)
./build-all.sh

# Build for specific version
./build-all.sh 26.04
./build-all.sh 24.04
./build-all.sh 22.04
```

Packages will be in `output/ubuntu26.04/`, `output/ubuntu24.04/` or `output/ubuntu22.04/`.

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
docker build -t gpclient-builder:ubuntu24.04 -f Dockerfile.ubuntu24.04 .

# Run build
docker run --rm -v $(pwd)/output:/output gpclient-builder:ubuntu24.04
```
