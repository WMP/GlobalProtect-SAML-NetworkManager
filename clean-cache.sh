#!/bin/bash
# Clean Docker build cache for gpclient-gui

set -e

echo "=== Cleaning Docker build cache ==="

# Remove named volumes
echo "Removing Cargo cache volume..."
docker volume rm gpclient-cargo-cache 2>/dev/null || echo "  (volume doesn't exist)"

echo "Removing Rust target cache volume..."
docker volume rm gpclient-target-cache 2>/dev/null || echo "  (volume doesn't exist)"

# Remove Docker image
echo "Removing Docker image..."
docker rmi gpclient-gui-builder 2>/dev/null || echo "  (image doesn't exist)"

echo "=== Cache cleaned ==="
echo "Next build will compile everything from scratch."
