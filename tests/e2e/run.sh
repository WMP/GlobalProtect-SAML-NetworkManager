#!/bin/bash
# End-to-end tests of the plugin on a real desktop in Docker.
#
# Usage: tests/e2e/run.sh <gnome|kde|routing|auth-cancel|all> [--gif]
#
#   gnome        GNOME 46 (Ubuntu 24.04): add the VPN in Settings, connect
#   kde          KDE Plasma 6.6 (Ubuntu 26.04): add the VPN, connect from the tray
#   routing      GNOME: routes and DNS before, with the default settings and with
#                "only for resources on its network" (the console slide)
#   auth-cancel  GNOME, negative: the user closes the sign-in window
#   all          all of the above
#
# Builds the image, runs the scenario in a privileged container without
# network access and checks the result there (results.json). Without --gif that
# is all; with --gif the recording of gnome, kde and routing also becomes the
# README GIF in docs/images/. The exit code is not zero when a scenario or a
# check fails.
#
# Environment:
#   HTTPS_PROXY      passed to the image build when set
#   EXTRA_CA_CERT    CA file for a TLS-inspecting proxy, used as a BuildKit
#                    secret while the image is built (not stored in the image)
#   E2E_OUT_DIR      output directory (default: tests/e2e/out)
#   E2E_SCENARIO_FILE  scenario file to run instead of the one in the image
#   DEMO_TIMEOUT_SCALE multiplies all timeouts of the scenarios (default 1)
#   GIF_CROP         crop box w:h:x:y of the recording for the GIF
#   GIF_TRIM_START   seconds to cut from the start of the GIF recording
#   GIF_FPS          GIF frame rate (default 12)
#   SCREEN_W, SCREEN_H  size of the virtual screen (default 1280x800); the GIF
#                    has the same size (no scaling)
#   SKIP_BUILD=1     use the images that exist (CI builds them with buildx)
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out_base="${E2E_OUT_DIR:-$here/out}"
# The size of the virtual screen: the only place with the numbers; the
# container gets them as environment (start.sh, lib.sh)
SCREEN_W="${SCREEN_W:-1280}"
SCREEN_H="${SCREEN_H:-800}"

usage() { sed -n '2,/^set -u/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

target=""
make_gif=0
for arg in "$@"; do
    case "$arg" in
    --gif) make_gif=1 ;;
    -h | --help) usage; exit 0 ;;
    gnome | kde | routing | auth-cancel | all) target="$arg" ;;
    *) echo "unknown argument: $arg" >&2; usage >&2; exit 2 ;;
    esac
done
[ -n "$target" ] || { usage >&2; exit 2; }
if [ "$target" = all ]; then targets=(gnome kde routing auth-cancel); else targets=("$target"); fi

# scenario -> image, GIF name, extra environment of the container
image_of() { case "$1" in kde) echo kde ;; *) echo gnome ;; esac; }
gif_of() {
    case "$1" in
    gnome) echo demo-gnome.gif ;;
    kde) echo demo-kde.gif ;;
    routing) echo demo-routing-dns.gif ;;
    esac
}
env_of() { case "$1" in auth-cancel) echo "-e GP_DEMO_AUTH_TIMEOUT=15" ;; esac; }

export DOCKER_BUILDKIT=1
declare -A built

build_image() {
    local de="$1" args=(--network host)
    [ -n "${built[$de]:-}" ] && return 0
    if [ -n "${HTTPS_PROXY:-}" ]; then
        args+=(--build-arg "https_proxy=$HTTPS_PROXY" --build-arg "HTTPS_PROXY=$HTTPS_PROXY")
    fi
    if [ -n "${EXTRA_CA_CERT:-}" ]; then
        args+=(--secret "id=extra_ca,src=$EXTRA_CA_CERT")
    fi
    docker build "${args[@]}" -f "$here/Dockerfile.$de" -t "gpclient-e2e-$de" "$repo" || return 1
    built[$de]=1
}

make_gif() {
    local name="$1" outdir="$2" gif="$repo/docs/images/$(gif_of "$1")" tmpgif pal
    local fps="${GIF_FPS:-12}" trim="${GIF_TRIM_START:-}" end="" filters
    if [ -z "$trim" ]; then
        trim=0
        [ -s "$outdir/trim-start" ] && trim="$(cat "$outdir/trim-start")"
    fi
    [ -s "$outdir/trim-end" ] && end="$(cat "$outdir/trim-end")"
    filters="fps=$fps,"
    [ -n "${GIF_CROP:-}" ] && filters+="crop=$GIF_CROP,"
    filters="${filters%,}"
    mkdir -p "$(dirname "$gif")"
    pal="$(mktemp --suffix=.png)"
    tmpgif="$(mktemp --suffix=.gif)"
    local to=()
    [ -n "$end" ] && to=(-to "$end")
    ffmpeg -nostdin -y -loglevel error -ss "$trim" "${to[@]}" -i "$outdir/raw.mp4" \
        -vf "$filters,palettegen=stats_mode=diff:max_colors=256" "$pal" &&
        ffmpeg -nostdin -y -loglevel error -ss "$trim" "${to[@]}" -i "$outdir/raw.mp4" -i "$pal" \
            -lavfi "$filters [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
            -loop 0 "$tmpgif" || { rm -f "$pal" "$tmpgif"; return 1; }
    rm -f "$pal"
    mv -f "$tmpgif" "$gif"
    chmod 644 "$gif"
    local size
    size=$(stat -c %s "$gif")
    echo "GIF: $gif ($((size / 1024)) KiB)"
    if [ "$size" -gt $((10 * 1024 * 1024)) ]; then
        echo "WARNING: $gif is larger than 10 MB (lower GIF_FPS, trim, or shorten the scenario)" >&2
    fi
}

# Prints the table of results.json of one scenario
summary() {
    python3 - "$1" << 'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1] + "/results.json"))
except (OSError, ValueError) as error:
    print(f"  no results.json ({error})")
    sys.exit(0)
for item in data["checks"]:
    print(f"  {'ok  ' if item['ok'] else 'FAIL'} {item['name']}")
PY
}

failed=()
for name in "${targets[@]}"; do
    outdir="$out_base/$name"
    image="$(image_of "$name")"
    rm -rf "$outdir"
    mkdir -p "$outdir"
    echo "=== $name"
    if [ "${SKIP_BUILD:-0}" != 1 ]; then
        build_image "$image" || { echo "build of the $image image failed" >&2; failed+=("$name"); continue; }
    fi
    run=(docker run --rm --privileged --network none --hostname demo-laptop
        -e "E2E_SCENARIO=$name" -e "SCREEN_W=$SCREEN_W" -e "SCREEN_H=$SCREEN_H" -e "DEMO_TIMEOUT_SCALE=${DEMO_TIMEOUT_SCALE:-1}"
        $(env_of "$name") -v "$outdir:/out")
    if [ -n "${E2E_SCENARIO_FILE:-}" ]; then
        run+=(-e E2E_SCENARIO_FILE=/opt/gp-demo/override.sh -v "$(readlink -f "$E2E_SCENARIO_FILE"):/opt/gp-demo/override.sh:ro")
    fi
    "${run[@]}" "gpclient-e2e-$image" > "$outdir/container.log" 2>&1
    status=$?
    summary "$outdir"
    if [ "$status" -ne 0 ]; then
        echo "FAILED: $name (exit $status); see $outdir/scenario.log, checks.log, failure.png" >&2
        failed+=("$name")
        continue
    fi
    echo "passed: $name"
    if [ "$make_gif" = 1 ] && [ -n "$(gif_of "$name")" ]; then
        if [ -s "$outdir/raw.mp4" ]; then
            make_gif "$name" "$outdir" || { echo "GIF of $name failed" >&2; failed+=("$name"); }
        else
            echo "no recording in $outdir" >&2
            failed+=("$name")
        fi
    fi
done

echo
if [ "${#failed[@]}" -gt 0 ]; then
    echo "FAILED: ${failed[*]}" >&2
    exit 1
fi
echo "All end-to-end scenarios passed: ${targets[*]}"
