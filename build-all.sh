#!/bin/bash
# Build script for creating .deb packages for multiple Ubuntu versions
# Usage: ./build-all.sh [VERSION]
#   VERSION: Optional Ubuntu version (22.04 or 24.04). If not specified, builds for all versions.
# Examples:
#   ./build-all.sh          # Build for all versions
#   ./build-all.sh 22.04    # Build only for Ubuntu 22.04
#   ./build-all.sh 24.04    # Build only for Ubuntu 24.04

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if specific version was requested
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $0 [VERSION]"
    echo ""
    echo "Build .deb packages for Ubuntu versions."
    echo ""
    echo "Arguments:"
    echo "  VERSION    Optional Ubuntu version (22.04 or 24.04)"
    echo "             If not specified, builds for all supported versions"
    echo ""
    echo "Examples:"
    echo "  $0          # Build for all versions (22.04 and 24.04)"
    echo "  $0 22.04    # Build only for Ubuntu 22.04"
    echo "  $0 24.04    # Build only for Ubuntu 24.04"
    exit 0
elif [ -n "$1" ]; then
    # Validate version
    if [ "$1" != "22.04" ] && [ "$1" != "24.04" ]; then
        echo "ERROR: Invalid Ubuntu version '$1'. Supported versions: 22.04, 24.04"
        echo "Run '$0 --help' for usage information"
        exit 1
    fi
    UBUNTU_VERSIONS=("$1")
    echo "=== Building package for Ubuntu $1 only ==="
else
    # Build for all versions
    UBUNTU_VERSIONS=("22.04" "24.04")
    echo "=== Building packages for Ubuntu ${UBUNTU_VERSIONS[*]} ==="
fi

# Enable Docker BuildKit for better caching
export DOCKER_BUILDKIT=1

for VERSION in "${UBUNTU_VERSIONS[@]}"; do
    echo ""
    echo "======================================"
    echo "=== Building for Ubuntu $VERSION ==="
    echo "======================================"

    IMAGE_NAME="gpclient-builder:ubuntu${VERSION}"
    DOCKERFILE="Dockerfile.ubuntu${VERSION}"
    OUTPUT_DIR="$SCRIPT_DIR/output/ubuntu${VERSION}"

    # Check if Dockerfile exists
    if [ ! -f "$SCRIPT_DIR/$DOCKERFILE" ]; then
        echo "ERROR: $DOCKERFILE not found"
        exit 1
    fi

    echo "=== Building Docker image from $DOCKERFILE ==="
    docker build -f "$DOCKERFILE" -t "$IMAGE_NAME" "$SCRIPT_DIR"

    echo "=== Building .deb package for Ubuntu $VERSION ==="
    mkdir -p "$OUTPUT_DIR"
    chmod 777 "$OUTPUT_DIR"

    # Run with cache mounts for Cargo registry and Rust target directory
    # Copy version-specific control file before building
    docker run --rm \
        --name "gpclient-build-ubuntu${VERSION}-$$" \
        -v "$OUTPUT_DIR:/output" \
        -v "gpclient-cargo-cache-${VERSION}:/cargo-cache" \
        -v "gpclient-target-cache-${VERSION}:/build/external/GlobalProtect-openconnect/target" \
        "$IMAGE_NAME" \
        bash -c "cp debian/control.ubuntu${VERSION} debian/control && fakeroot dpkg-buildpackage -us -uc -b; cp -v debs/*.deb debs/*.ddeb /output/ 2>/dev/null; true"

    echo "=== Build complete for Ubuntu $VERSION ==="
    echo "Packages available in: $OUTPUT_DIR/"
    ls -lh "$OUTPUT_DIR/"*.deb 2>/dev/null || echo "No .deb files found"
done

echo ""
echo "======================================"
echo "=== All builds complete ==="
echo "======================================"
echo ""
echo "Packages summary:"
for VERSION in "${UBUNTU_VERSIONS[@]}"; do
    OUTPUT_DIR="$SCRIPT_DIR/output/ubuntu${VERSION}"
    echo ""
    echo "Ubuntu $VERSION:"
    ls -lh "$OUTPUT_DIR/"*.deb 2>/dev/null || echo "  No packages found"
done
