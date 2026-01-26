# Build Instructions

## Prerequisites

### Ubuntu 22.04 / 24.04

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

### Rust Installation

This project uses GlobalProtect-openconnect which requires **Rust 1.85+**.
The Makefile will automatically install Rust via rustup if not present.

To install manually:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.85
source $HOME/.cargo/env
```

## Building

### Build Debian packages (recommended):

```bash
# Build for all supported Ubuntu versions (uses Docker)
./build-all.sh

# Build for specific version
./build-all.sh 24.04
./build-all.sh 22.04
```

Packages will be in `output/ubuntu24.04/` or `output/ubuntu22.04/`.

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
   **network-manager-gpclient-plasma** - for KDE Plasma

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
