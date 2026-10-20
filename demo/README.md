# README demo recordings

This directory builds the GIFs of the README (`docs/images/demo-gnome.gif`,
`docs/images/demo-kde.gif`). The recording runs in a Docker container with a
real desktop and a screen recorder.

Everything on the screen is real: the desktop (GNOME 46 on Ubuntu 24.04 or
KDE Plasma 6.6 on Ubuntu 26.04, both on X11), NetworkManager, the connection
editor plugins and the VPN service built from this repository, the browser
wrapper and Microsoft Edge. Only these parts are fake:

- `demo/files/gpclient` and `demo/files/gpauth` replace the upstream tools.
- The sign-in pages of "Contoso" are a local HTTPS site
  (`demo/files/sso-server.py`, `demo/files/www/`). A demo CA, made at build
  time, is trusted in the system store and in the NSS database of the user.

## Requirements

- Docker with BuildKit and permission to run privileged containers
  (NetworkManager and udev need it).
- An `amd64` host (Microsoft Edge has no `arm64` package for this image).
- Network access during the build (apt, Microsoft apt repository).

The container runs with `--network none`. Never run it with `--network host`:
NetworkManager would change the network of the host.

## Run

```bash
demo/record.sh gnome     # or: kde, all
```

The script builds the image, runs the scenario and writes the GIF. In
`demo/out/<de>/` you get `raw.mp4`, screenshots of the scenario and the logs
(`start.log`, `NetworkManager.log`, `edge-wrapper.log`, `scenario.log`, ...).
The script prints the size of the GIF and warns when it is larger than 10 MB.

Environment variables of `record.sh`:

| Variable | Meaning |
|---|---|
| `HTTPS_PROXY` | Passed to the build as proxy. |
| `EXTRA_CA_CERT` | CA file for a TLS-inspecting proxy. Passed as BuildKit secret `extra_ca`, used for the Edge download only, not stored in the image. |
| `DEMO_SCENARIO` | Scenario file that replaces the one in the image. |
| `DEMO_OUT_DIR` | Output directory (default `demo/out`). |
| `GIF_TRIM_START`, `GIF_FPS` | Seconds to cut from the start, GIF frame rate (default 12). |
| `SKIP_BUILD=1` | Use the image of the last build. |

## Scenarios

`demo/scenario-gnome.sh` and `demo/scenario-kde.sh` run as the desktop user
`demo` (Alex Morgan) inside the container, on `DISPLAY=:1` (1920x1080).
They source `demo/lib.sh` (`wait_for_window`, `smooth_move`, `click`,
`type_slow`, `key`, `window_geometry`, `screenshot`, `pause`). Recording starts
just before the scenario and stops when it ends. Write screenshots to
`$DEMO_OUT`.

To work on a scenario without rebuilding the image:

```bash
SKIP_BUILD=1 DEMO_SCENARIO=demo/scenario-gnome.sh demo/record.sh gnome
```

Then look at `demo/out/gnome/shot-*.png` to find the coordinates for clicks.

## Known limits

- The container has no systemd and no logind. `demo/files/loginctl` answers
  `loginctl list-users`, so that the VPN service finds the desktop user.
- GNOME starts without `gnome-session` (it needs logind): `session/desktop.sh`
  starts `gnome-shell --x11` by hand. NetworkManager runs without polkit
  (`auth-polkit=false`).
- KDE uses Ubuntu 26.04: Plasma 6.8 (KDE neon) has no X11 session, and the
  Wayland KWin needs a GPU.
