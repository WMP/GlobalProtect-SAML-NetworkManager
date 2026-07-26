#!/bin/bash
# Compatibility shim: existing VPN profiles store
# browser=/usr/libexec/gpclient/edge-wrapper in vpn.data, so this path has to
# keep working. All the logic now lives in browser-wrapper, which handles any
# browser (see scripts/browser-wrapper.sh and docs/EDGE_WRAPPER.md).

WRAPPER_DIR="$(dirname "$(readlink -f "$0")")"

for candidate in "$WRAPPER_DIR/browser-wrapper" /usr/libexec/gpclient/browser-wrapper; do
    if [ -x "$candidate" ]; then
        export GP_BROWSER="${GP_BROWSER:-/usr/bin/microsoft-edge}"
        exec "$candidate" "$@"
    fi
done

echo "ERROR: browser-wrapper not found next to $0 nor in /usr/libexec/gpclient" >&2
exit 1
