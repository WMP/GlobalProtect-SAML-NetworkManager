#!/bin/bash
# Build script for GlobalProtect Plasma VPN Plugin

set -e

echo "Building GlobalProtect Plasma VPN Plugin..."

# Create build directory
mkdir -p build
cd build

# Run CMake
cmake .. \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DKDE_INSTALL_LIBDIR=lib/x86_64-linux-gnu \
    -DCMAKE_BUILD_TYPE=Release

# Build
make -j$(nproc)

echo ""
echo "Build complete!"
echo ""
echo "To install, run:"
echo "  cd plasma/build && sudo make install"
echo ""
echo "Then restart plasma-nm:"
echo "  killall plasmashell && plasmashell &"
