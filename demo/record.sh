#!/bin/bash
# Builds the demo image(s), runs the scenario in a container and makes the
# README GIF. Usage: demo/record.sh [gnome|kde|all]   (default: all)
#
# Environment:
#   HTTPS_PROXY      passed to the image build when set
#   EXTRA_CA_CERT    CA file for a TLS-inspecting proxy, used as a BuildKit
#                    secret while the image is built (not stored in the image)
#   DEMO_SCENARIO    scenario file bind-mounted over the one in the image
#   DEMO_OUT_DIR     output directory (default: demo/out)
#   GIF_TRIM_START   seconds to cut from the start of the recording (default 0)
#   GIF_FPS          GIF frame rate (default 12)
#   SKIP_BUILD=1     reuse the image from the last build
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
out_base="${DEMO_OUT_DIR:-$here/out}"
target="${1:-all}"
case "$target" in
gnome | kde) targets=("$target") ;;
all) targets=(gnome kde) ;;
*) echo "usage: $0 [gnome|kde|all]" >&2; exit 2 ;;
esac

export DOCKER_BUILDKIT=1

build_image() {
    local de="$1" args=(--network host)
    if [ -n "${HTTPS_PROXY:-}" ]; then
        args+=(--build-arg "https_proxy=$HTTPS_PROXY" --build-arg "HTTPS_PROXY=$HTTPS_PROXY")
    fi
    if [ -n "${EXTRA_CA_CERT:-}" ]; then
        args+=(--secret "id=extra_ca,src=$EXTRA_CA_CERT")
    fi
    docker build "${args[@]}" -f "$here/Dockerfile.$de" -t "gpclient-demo-$de" "$repo"
}

make_gif() {
    local de="$1" outdir="$2" gif="$repo/docs/images/demo-$de.gif"
    local fps="${GIF_FPS:-12}" trim="${GIF_TRIM_START:-0}"
    local filters="fps=$fps,scale=1280:-1:flags=lanczos" pal
    pal="$(mktemp --suffix=.png)"
    mkdir -p "$(dirname "$gif")"
    ffmpeg -nostdin -y -loglevel error -ss "$trim" -i "$outdir/raw.mp4" \
        -vf "$filters,palettegen=stats_mode=diff:max_colors=256" "$pal"
    ffmpeg -nostdin -y -loglevel error -ss "$trim" -i "$outdir/raw.mp4" -i "$pal" \
        -lavfi "$filters [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
        -loop 0 "$gif"
    rm -f "$pal"
    local size
    size=$(stat -c %s "$gif")
    echo "GIF: $gif ($((size / 1024)) KiB)"
    if [ "$size" -gt $((10 * 1024 * 1024)) ]; then
        echo "WARNING: $gif is larger than 10 MB (lower GIF_FPS, trim, or shorten the scenario)" >&2
    fi
}

for de in "${targets[@]}"; do
    outdir="$out_base/$de"
    rm -rf "$outdir"
    mkdir -p "$outdir"
    [ "${SKIP_BUILD:-0}" = 1 ] || build_image "$de"
    run=(docker run --rm --privileged --network none -v "$outdir:/out")
    if [ -n "${DEMO_SCENARIO:-}" ]; then
        run+=(-v "$(readlink -f "$DEMO_SCENARIO"):/opt/gp-demo/scenario.sh:ro")
    fi
    "${run[@]}" "gpclient-demo-$de"
    if [ ! -s "$outdir/raw.mp4" ]; then
        echo "no recording in $outdir (see start.log, desktop.log, scenario.log)" >&2
        exit 1
    fi
    make_gif "$de" "$outdir"
done
