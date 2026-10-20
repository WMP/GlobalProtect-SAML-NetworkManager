# End-to-end tests

These tests run this plugin on a **real desktop in Docker** and check what it
does. The README GIFs (`docs/images/demo-*.gif`) are a by-product: the same runs
are recorded.

Everything on the screen is real: the desktop (GNOME 46 on Ubuntu 24.04 or KDE
Plasma 6.6 on Ubuntu 26.04, both on X11), NetworkManager, systemd-resolved, the
connection editor plugins and the VPN service built from this repository, the
vpnc hook of the plugin, the browser wrapper and Microsoft Edge. Only these
parts are fake:

- `files/gpclient` and `files/gpauth` replace the upstream tools. The fake
  `gpclient` creates the tunnel device and runs the real
  `/usr/share/vpnc-scripts/vpnc-script` like openconnect does, so addresses,
  routes and DNS come from the real stack.
- The sign-in pages of "Contoso" are a local HTTPS site (`files/sso-server.py`,
  `files/www/`). A demo CA, made at build time, is trusted in the system store
  and in the NSS database of the user.
- The "home router" is a network namespace (`router`) at the other end of a
  veth cable to the desktop's wired card `enp0s31f6` (192.168.1.20/24).

## Scenarios and checks

| Scenario | What it does |
|---|---|
| `gnome` | Settings > Network > "+" > GlobalProtect, fill the editor, connect with the row switch, sign in, quick settings. GIF: `demo-gnome.gif` |
| `kde` | Tray > Networks > configure > "+" > GlobalProtect, fill the editor, save, connect from the tray, sign in. GIF: `demo-kde.gif` |
| `routing` | Terminal: `ip route` and `resolvectl` before connecting, with the default settings, and with "Use this connection only for resources on its network" (Settings > VPN > IPv4). GIF: `demo-routing-dns.gif` |
| `auth-cancel` | Negative: the user closes the sign-in window. The connection must fail cleanly. No GIF |

After each scenario `checks.py` runs in the container and writes
`results.json` (`{"scenario", "ok", "checks": [{"name", "ok", "detail"}]}`). The
run fails when one of these is wrong:

- the VPN is not activated; `gpd0` has not exactly one address, 10.20.30.45/32
  (no byte-swapped extra address); the wired connection was touched;
- Edge was not started by the wrapper as uid 1000, ran as root, was still
  running or its window still open after the authentication;
- full tunnel: the default route, the Internet (`ip route get 9.9.9.9`) and the
  company network (`10.20.0.10`) do not go through `gpd0`, or `gpd0` is not the
  DNS default route; never-default: the default route and the Internet are not
  on the wired link, the company network is not on `gpd0`, or `gpd0` is the DNS
  default route; the VPN DNS servers and domain are not on `gpd0`;
- errors or tracebacks in the service log (NetworkManager log);
- after the disconnect: `gpd0` or a route through it is left, DNS is not back to
  the wired link's 192.168.1.1, a gpclient/gpauth/Edge process is left;
- `auth-cancel`: the VPN is active, helpers are left, routes or DNS changed, no
  clean failure in the service log, or the connection attempt ended without an
  error.

`tests/unit/test_e2e_checks.py` runs `checks.py` on made-up output: a good run
passes, and every check fails when its condition is broken.

## Run

Requirements: Docker with BuildKit and permission to run privileged containers
(NetworkManager and udev need it), an `amd64` host (Microsoft Edge has no
`arm64` package for this image), network access for the build.

```bash
tests/e2e/run.sh all             # gnome, kde, routing, auth-cancel
tests/e2e/run.sh gnome --gif     # also docs/images/demo-gnome.gif
```

The containers run with `--network none`. Never run them with `--network host`:
NetworkManager would change the network of the host.

Output of a scenario is in `tests/e2e/out/<scenario>/`: `results.json`,
`checks.log`, `scenario.log`, `state-*.txt` (connections, addresses, routes and
DNS at each step), `NetworkManager.log` (it has the service log),
`edge-wrapper.log`, screenshots (`step-*.png`, `failure.png`) and `raw.mp4`.

| Variable | Meaning |
|---|---|
| `HTTPS_PROXY` | Passed to the build as proxy. |
| `EXTRA_CA_CERT` | CA file for a TLS-inspecting proxy. Passed as BuildKit secret `extra_ca`, used for the Edge download only, not stored in the image. |
| `E2E_OUT_DIR` | Output directory (default `tests/e2e/out`). |
| `E2E_SCENARIO_FILE` | Scenario file to run instead of the one in the image. |
| `DEMO_TIMEOUT_SCALE` | Multiplies all timeouts of the scenarios. |
| `GIF_CROP`, `GIF_TRIM_START`, `GIF_FPS` | Crop box `w:h:x:y`, seconds to cut from the start, frame rate (default 12) of the GIF. |
| `SKIP_BUILD=1` | Use the images that exist (CI builds them with buildx). |

GitHub: the workflow `e2e.yml` runs by hand (Actions tab, inputs `scenario` and
`gif`) or when a pull request gets the label `e2e`. Results, logs, screenshots,
the recording and the GIFs are artifacts for 14 days; the job summary has the
table of checks. It uses no secrets.

## Write or change a scenario

Scenarios (`scenario-<name>.sh`) run as the desktop user `demo` on
`DISPLAY=:1` (1920x1080) and source `lib.sh` (`wait_for_window`, `smooth_move`,
`click`, `type_slow`, `key`, `window_geometry`, `win_box`, `screenshot`, `shot`,
`snapshot`, `pause`, `edge_signin`, ...). Rules:

- Every wait ends with `|| fail "..."`: the exit code is not zero, the reason is
  in `scenario.log`, `failure.png` shows the screen, and no GIF is made.
- `snapshot <name>` saves the state the checks read (`state-<name>.txt`); the
  names a scenario saves are listed in `SCENARIO_STATES` of `checks.py`.
- Use coordinates from window geometry (`win_box`), not from the screen.
- For a GIF: `mark_start` cuts the setup before it, `mark_end` cuts the
  disconnect after it.

To work on a scenario without rebuilding: `SKIP_BUILD=1
E2E_SCENARIO_FILE=tests/e2e/scenario-gnome.sh tests/e2e/run.sh gnome`.

## Known limits

- The container has no systemd and no logind. `files/loginctl` answers
  `loginctl list-users`, so that the VPN service finds the desktop user.
- GNOME starts without `gnome-session` (it needs logind): `session/desktop.sh`
  starts `gnome-shell --x11` by hand. NetworkManager runs without polkit
  (`auth-polkit=false`).
- The wired card is a veth device. NetworkManager does not manage a veth
  device whose peer is in another namespace, so the card is activated first
  and the router's end is moved afterwards. The Plasma tray icon still looks
  disconnected, and GNOME Settings hides the "Wired" section, because both
  treat the device as software.
- The sign-in page remembers the account in `localStorage` after the password
  step, so the second sign-in is the short one (account tile, no typing).
- KDE uses Ubuntu 26.04: Plasma 6.8 (KDE neon) has no X11 session, and the
  Wayland KWin needs a GPU.
