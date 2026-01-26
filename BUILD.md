# Build Instructions

## Prerequisites

### Ubuntu 22.04 - 26.04

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

### Rust Installation

This project uses GlobalProtect-openconnect v2.5.1 which requires **Rust 1.85+**.
The Makefile will automatically install Rust via rustup if not present.

To install manually:
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.85
source $HOME/.cargo/env
```

## Building

### Build all components:
```bash
make
```

### Build individual components:
```bash
make gpclient  # Build gpclient binary
make gpauth    # Build gpauth binary
```

### Build Debian packages:
```bash
dpkg-buildpackage -us -uc -b
```

### Build with Docker:
```bash
docker build -t gpclient-builder .
docker run --rm -v $(pwd):/build gpclient-builder
```

## Installing

```bash
sudo make install
```

Or install the generated .deb packages:
```bash
sudo dpkg -i ../network-manager-gpclient_*.deb
sudo dpkg -i ../network-manager-gpclient-gnome_*.deb  # For GNOME
# OR
sudo dpkg -i ../network-manager-gpclient-plasma_*.deb  # For KDE Plasma
```
