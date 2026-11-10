# NetworkManager GlobalProtect VPN Plugin

NetworkManager VPN plugin for GlobalProtect (Palo Alto Networks) with SAML/SSO authentication support.

![Adding and connecting a GlobalProtect VPN in GNOME Settings, sign-in in Microsoft Edge](docs/images/demo-gnome.webp)

<details>
<summary>The same on KDE Plasma</summary>

![Adding and connecting a GlobalProtect VPN in KDE Plasma System Settings, sign-in in Microsoft Edge](docs/images/demo-kde.webp)
</details>

<details>
<summary>Screenshots</summary>

<img width="511" height="355" alt="image" src="https://github.com/user-attachments/assets/adb4a229-ae91-4415-8450-4b106ebeb475" />
<img width="597" height="363" alt="image" src="https://github.com/user-attachments/assets/7613a682-068c-4992-92d7-80139969c351" />

<img width="405" height="475" alt="image" src="https://github.com/user-attachments/assets/db5196a5-942e-4692-856b-e87fa647b2c7" />

<img width="451" height="600" alt="image" src="https://github.com/user-attachments/assets/29c54314-33f3-4b07-9378-a8e4c462409c" />
</details>

The recordings are made by the end-to-end tests ([tests/e2e](tests/e2e/README.md)):
the desktop, NetworkManager, the editors, the VPN service and Edge are real;
the company "Contoso", its sign-in pages and the VPN gateway are fictional.

## Features

- **NetworkManager integration** - manage VPN like any other connection
- **SAML/2FA authentication** via browser (Edge, Firefox, Chrome)
- **Standard login & RSA token portals** - interactive username/password/OTP
  prompts via desktop dialogs (see [docs/INTERACTIVE_AUTH.md](docs/INTERACTIVE_AUTH.md))
- **Desktop support** - GNOME Settings (GTK3/GTK4) and KDE Plasma
- **Routing control** - configure which traffic goes through VPN
- **Systemd service** - automatic VPN service management via D-Bus

## Installation

Add the apt repository and install - dependencies, upgrades and the GNOME/Plasma
choice are handled by apt:

```bash
# old one-line setup from earlier instructions, if present
sudo rm -f /etc/apt/sources.list.d/gpclient.list /usr/share/keyrings/gpclient-archive-keyring.gpg
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSLo /etc/apt/keyrings/gpclient-archive-keyring.gpg \
  https://wmp.github.io/GlobalProtect-SAML-NetworkManager/gpclient-archive-keyring.gpg

sudo tee /etc/apt/sources.list.d/gpclient.sources > /dev/null <<EOF
Types: deb
URIs: https://wmp.github.io/GlobalProtect-SAML-NetworkManager
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: main
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/gpclient-archive-keyring.gpg
EOF

sudo apt update
sudo apt install network-manager-gpclient-gnome    # GNOME, MATE, Cinnamon, XFCE
# or
sudo apt install network-manager-gpclient-plasma   # KDE Plasma
# or
sudo apt install network-manager-gpclient-plasma-6 # KDE neon (Ubuntu 24.04 with Plasma 6), amd64 only
```

`network-manager-gpclient-plasma` is built for the Plasma of the release (Qt5 on Ubuntu 22.04 and 24.04, Qt6 on 26.04). On Ubuntu 24.04 with Plasma 6 it does not fit. For **KDE neon** (Ubuntu 24.04 with Plasma 6 from `archive.neon.kde.org`) install `network-manager-gpclient-plasma-6` instead: it is built against the neon repository, for `amd64` only, and conflicts with `network-manager-gpclient-plasma` (only one Plasma generation can use the plugin). Ubuntu 24.04 with Plasma 6 from other sources (for example Kubuntu Backports) is not tested.

Supported: Ubuntu 22.04, 24.04 and 26.04, `amd64` and `arm64`. Details, removal and
migration from the old `.list` file (the `sudo rm -f` line of the commands above deletes it; `apt update` stops with "Conflicting values set for option Signed-By" otherwise): [docs/APT_REPO.md](docs/APT_REPO.md).

**Ubuntu 22.04 only:** `python3-sdbus` is not in apt, install it with pip first.
NetworkManager runs the service as root, so install it system-wide with `sudo`
(a plain `pip3 install` as your user puts it in `~/.local`, where root's Python
does not look):
```bash
sudo apt install python3-pip
sudo pip3 install sdbus
```

<details>
<summary>Alternative: individual .deb files</summary>

Download the packages for your Ubuntu version from
[GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases)
(newer releases list their files in a table by Ubuntu version and architecture; older ones do not).
You need **network-manager-gpclient** plus either
**network-manager-gpclient-gnome**, **network-manager-gpclient-plasma** (or
**network-manager-gpclient-plasma-6** on KDE neon), and
the GUI package requires exactly the same version of the core package - so
install them in one go and let apt sort out the dependencies:

```bash
sudo apt install ./network-manager-gpclient_*.deb ./network-manager-gpclient-gnome_*.deb
```

`dpkg -i` on a single file fails on purpose here; use `apt install ./file.deb`
(or the repository above).
</details>

### Fedora, RHEL 10 and openSUSE (RPM)

RPM repositories for Fedora 43, 44 and 45, RHEL/AlmaLinux/Rocky Linux 10 (with
EPEL) and openSUSE Leap 16.0, Leap 16.1 and Tumbleweed, `x86_64` and `aarch64`. Fedora:

```bash
sudo dnf config-manager addrepo --from-repofile=https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/fedora$(rpm -E %fedora)/network-manager-gpclient.repo
sudo dnf install network-manager-gpclient-gnome    # or network-manager-gpclient-plasma (Plasma 6)
```

openSUSE Tumbleweed (Leap 16.0: `.../rpm/leap16.0/...`, Leap 16.1: `.../rpm/leap16.1/...`):

```bash
sudo zypper addrepo https://wmp.github.io/GlobalProtect-SAML-NetworkManager/rpm/tumbleweed/network-manager-gpclient.repo
sudo zypper refresh
sudo zypper install network-manager-gpclient-gnome # or network-manager-gpclient-plasma
```

RHEL 10 and its rebuilds, removal, and what differs from the Ubuntu packages:
[docs/RPM_REPO.md](docs/RPM_REPO.md). The packages contain the Python module
`sdbus`; do not install it with pip.

### Arch Linux

A pacman repository for Arch Linux, `x86_64` only. Trust the key once, then add the repository.
Before `pacman-key --lsign-key`, compare the key's fingerprint (`gpg --show-keys /tmp/gpclient.gpg`)
with the one on the [landing page](https://wmp.github.io/GlobalProtect-SAML-NetworkManager/):

```bash
curl -fsSLo /tmp/gpclient.gpg https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/GPG-KEY-gpclient
sudo pacman-key --add /tmp/gpclient.gpg
sudo pacman-key --lsign-key "$(gpg --show-keys --with-colons /tmp/gpclient.gpg | awk -F: '/^fpr:/ {print $10; exit}')"

sudo tee -a /etc/pacman.conf > /dev/null <<'EOF'

[gpclient]
SigLevel = Required
Server = https://wmp.github.io/GlobalProtect-SAML-NetworkManager/arch/$arch
EOF
sudo pacman -Syu network-manager-gpclient-gnome     # or network-manager-gpclient-plasma (Plasma 6)
```

To build from source instead: `git clone --branch v2.0.0 https://github.com/WMP/GlobalProtect-SAML-NetworkManager`,
`cd GlobalProtect-SAML-NetworkManager/arch && makepkg -si` (needs the network: GitHub, crates.io, PyPI;
the repository above is the signed, prebuilt alternative).

Removal, single files, and what differs from the Ubuntu packages:
[docs/ARCH_REPO.md](docs/ARCH_REPO.md). Arch Linux ARM is not supported. The
package contains the Python module `sdbus`; do not install it with pip. It
conflicts with the AUR package `globalprotect-openconnect(-git)`.

### Migrating from `globalprotect-openconnect`

If you previously had the `globalprotect-openconnect` package installed,
remove it first — our package declares a `Conflicts:` against it and
`dpkg -i` will otherwise refuse to install:

```bash
sudo apt remove globalprotect-openconnect
sudo apt autoremove
```

Installing from the apt repository pulls the runtime prerequisites
(`openconnect`, `python3-sdbus`, `vpnc-scripts`) in by itself. On Ubuntu 22.04
`python3-sdbus` is not in apt — use the `sudo pip3 install sdbus` step shown
above instead.

Thanks to @ottuzzi for the writeup
([#3](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/3)).

## Usage

1. Open GNOME Settings → Network or KDE Network Settings
2. Add VPN → GlobalProtect
3. Enter the portal address (e.g. `vpn.example.com`)
4. Connect - a browser opens for SAML authentication

If your organisation gave you a *gateway* address rather than a portal
address, also tick **Address is a gateway (skip the portal)**.

When the portal offers several gateways, the first one it proposes is used
automatically; after the first successful connection the full list appears
in the **Preferred gateway** drop-down, so you can pin a different one.

Portals that use a standard login (username/password) or an RSA SecurID
token instead of SAML are supported too - the plugin asks for the
credentials in a dialog when gpclient needs them. See
[docs/INTERACTIVE_AUTH.md](docs/INTERACTIVE_AUTH.md) for details and for
storing the username/password in the connection.

```bash
# Or via command line
nmcli connection up "GlobalProtect VPN"
```

### Connection settings

Everything the editors show lives in the connection's `vpn.data`, so it can
also be set with `nmcli connection modify "My VPN" +vpn.data key=value`:

| Key | Default | Meaning |
|-----|---------|---------|
| `gateway` | (required) | Portal address, or gateway address with `as-gateway=true` |
| `as-gateway` | `false` | The address is a gateway - skip the portal workflow |
| `preferred-gateway` | (empty) | Gateway to use; empty means the portal's first proposal. A gateway the portal offered before (entry, name or address) is passed to gpclient as `--gateway` (not with "Address is a gateway"); otherwise (or when gpclient does not find it, which runs the SAML login again) the service answers gpclient's gateway prompt. Falls back to the first proposal when the value is not offered |
| `gateway-list` | (written by the service) | The portal's gateway list, `;`-separated. Read by the editors to fill the drop-down. The whole list after reading it page by page, or a list that fits one page (both need gpclient's `Found N` line); otherwise the stored entries plus the ones seen last time |
| `gateway-list-count` | (written by the service) | gpclient's `Found N gateways in portal config` count when the list was last read completely. It marks `gateway-list` as complete; the list is read again on a connect without `--gateway` (for example when the preferred gateway is not in the list, or none is set) when the portal reports another number, and never without that line. With `--gateway` gpclient shows no gateway prompt, so the list is not refreshed |
| `auth-mode` | `saml` | `saml` = browser login, `credentials` = username/password collected upfront |
| `username` | (empty) | Username for portals that ask on the terminal |
| `password-flags` | `1` for a new connection from an editor, `0` otherwise | Where the `password` secret is kept: `1` = for this user only (secret agent, e.g. the keyring), `0` = for all users (in the profile file, as plain text), `2` = not saved, asked every time. The editors show this as the **Password** field (only for `auth-mode=credentials`) with a storage menu. Emptying the field keeps a stored password; choose `2` to remove it from the profile |
| `otp-flags` | `2` for a new connection from an editor | `2` = a one-time code is never stored. The service also sets it, but only after the first one-time prompt of an activation has ended (tunnel up, failure or disconnect): NetworkManager rejects secrets requests after a profile change |
| `gpauth-result-flags` | `2` for a new connection from an editor | `2` = the result of the SAML login (`saml-auth=dialog`) is never stored. The service sets it like `otp-flags` if it is missing |
| `saml-auth` | `service` | Who runs the SAML login. `service` = gpclient, started by the service as before. `dialog` = the NetworkManager auth dialog runs `gpauth` in your desktop session and hands the result to gpclient (`--cookie-on-stdin`); set in the editors with **SAML login** (GNOME and Plasma); needs a desktop secret agent (GNOME Shell, plasma-nm; not nmcli, which drops the result) and an `auth-mode=saml` connection. Works for one login per connection: use it with **Address is a gateway**, or with a portal whose gateway needs no second login ([details](docs/INTERACTIVE_AUTH.md#saml-login-in-the-auth-dialog)) |
| `browser` | `edge` | `edge`, `firefox`, `chrome`, `chromium`, `default`, or the absolute path (or command name) of your own wrapper ([details](docs/EDGE_WRAPPER.md#alternative-browsers)) |
| `fix-openssl` | `auto` | Legacy TLS renegotiation for portals with an old TLS stack. `auto` retries once when the portal needs it and then stores `true` in the profile; `true` uses it from the start; `false` never does |
| `mtu` | (empty) | MTU of the tunnel, 576-1500 (smaller values are raised to 576); empty or `0` is automatic. A value that is not a plain number up to 1500 is ignored with a warning in the log. openconnect uses it only when the gateway sends no MTU (its log then says "No MTU received. Calculated N ..."); a gateway MTU wins. Lowering it (`nmcli connection modify "My VPN" +vpn.data mtu=1300`) may help when the tunnel falls back from ESP to HTTPS with "ESP receive error: Message too long", as seen on mobile hotspots. The editors show a stored value they cannot show as 0 and keep it on save while the field is untouched |
| `hip` | `true` | Send the HIP (Host Integrity Protection) report |
| `hip-script` | (empty) | Absolute path to a custom HIP executable, passed as `--hip=<path>`; ignored when `hip=false`. The service runs it as root, so it must be a root-owned regular file, not group- or world-writable, in directories that are root-owned and not group- or world-writable; otherwise the connection fails (pick it under a root-owned directory such as `/usr/local/libexec`, not in your home). The editors have a file chooser icon in the field |
| `usercert` | (empty) | Absolute path to the client certificate, passed as `--certificate=<path>` (PEM or PKCS#12 `.p12`/`.pfx`). The file must be a regular file owned by your desktop user, at most 1 MiB (the service refuses others; it gives gpclient a private copy). The format comes from the extension: `.p12`/`.pfx` is PKCS#12, any other is PEM. The editors have a file chooser icon in the field. See [Client certificate](docs/INTERACTIVE_AUTH.md#client-certificate) |
| `userkey` | (empty) | Absolute path to the private key (PEM), passed as `--sslkey=<path>`; ignored without `usercert`. Not needed when the key is in the certificate file. The editors have a file chooser icon in the field |
| `cert-pass-flags` | `1` for a new connection from an editor, `0` otherwise | Where the `cert-pass` secret (the passphrase of an encrypted key or PKCS#12 file) is kept, like `password-flags`: `1` = for this user only, `0` = in the profile file, `2` = not saved. The service never puts the passphrase on the command line: it types it at gpclient's passphrase prompt, and only there. (Upstream gpclient itself passes it to gpauth on the command line in SAML mode: prefer an unencrypted key with mode `0600`.) The editors show it as the **Certificate passphrase** field |
| `dns` | (empty) | Override the VPN DNS servers, `;`-separated. Empty uses the servers the gateway pushes |
| `dns-domains` | (empty) | Extra search domains, space-separated, added to the ones the gateway pushes |
| `nm-routing` | `no` | `yes` = NetworkManager configures the tunnel's routes, DNS and default route from what the gateway sends, instead of vpnc-script (experimental). It needs no other setting. Routes the gateway excludes from the tunnel are ignored. Set in the editors with **Let NetworkManager set the routes and DNS (experimental)**; the editors write the key only when it is `yes` |

To use a custom HIP script:

```bash
nmcli connection modify "My VPN" +vpn.data hip-script=/usr/local/bin/gp-hip-report.sh
```

The **Connect timeout** field is NetworkManager's own `vpn.timeout`, not a
`vpn.data` item: set it with `nmcli connection modify "My VPN" vpn.timeout 360`.
NetworkManager stops the connection after that many seconds, and the login in
the browser counts towards it. `0` means the NetworkManager default, usually
60 s, which is often too short for a SAML login with MFA. A new connection with
browser login starts with 360 s. While a connection has no timeout of its own
(0) and you have not changed the field, choosing browser login sets 360 s
([#46](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/46)).
Choosing browser login on a connection with 0 writes 360 s, which replaces a
global default from `NetworkManager.conf`; set the field back to 0 to keep that
default.

An existing connection with 0 keeps 0: the editor cannot tell it from a
deliberate 0, and `NetworkManager.conf` can set a longer global default for 0.
If it fails with "connect timeout exceeded", set **Connect timeout** to 360 or
run the `nmcli` command above.

The DNS servers and search domains the gateway pushes are reported to
NetworkManager as part of the VPN's IP configuration, so they survive
NetworkManager's own DNS recalculations (they used to be applied only by
vpnc-script and were wiped on the next recalculation - see
[#15](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/15)).
This needs the vpnc hooks `/etc/vpnc/connect.d/90-gpclient-routing` (also in
`disconnect.d`) and
`/etc/vpnc/post-connect.d/90-gpclient-notify` (also in `reconnect.d`) that the
package installs. The gateway's split-DNS domains are routing-only (`~domain`):
names under them go to the VPN DNS servers, but a short name is not completed
with them. Add a domain to `dns-domains` to make it a search domain as well.

By default all traffic goes through the VPN. With **Use this connection only
for resources on its network** (IPv4 tab of the connection,
`ipv4.never-default yes`) only the networks the gateway announces go through
the tunnel, and the VPN DNS servers answer only for the VPN's domains. On a
dual-stack tunnel NetworkManager then adds no IPv6 default route either, and the
vpnc hook removes the `::/0` entry from the gateway's IPv6 split-include list
(or sets an empty list when the gateway sends none), so vpnc-script sets none
([#109](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/109)):

![ip route and resolvectl before connecting, with the default settings and with "Use this connection only for resources on its network"](docs/images/demo-routing-dns.webp)

With `nm-routing yes` NetworkManager sets the routes and DNS itself, from what
the gateway sends, instead of vpnc-script. It applies `ipv4.never-default`,
`ignore-auto-routes` and the routes of the connection itself. Routes that the
gateway excludes from the tunnel are ignored. A gateway that sends only split
routes (no default route) makes the tunnel split, as in NetworkManager-openconnect,
whatever `ipv4.never-default` says.

The password for `auth-mode=credentials` is a secret, not data. Type it in the
**Password** field of the editor and choose where to keep it with the icon in
the field. Do not put it on the `nmcli` command line: other users of the
machine can read it in the process list, and your shell keeps it in its
history. Use `password-flags=1` and let `nmcli` ask for it:

```bash
nmcli connection modify "My VPN" +vpn.data password-flags=1
nmcli --ask connection up "My VPN"
```

### Import and export

A profile is a NetworkManager keyfile (`.nmconnection`), so you can copy it to
another machine. The exported file holds no secrets and no user permissions.

- **GNOME**: in the network settings (or `nm-connection-editor`) use the *Import from file...* entry of the VPN list, and *Export...* in the editor of a connection.
- **Plasma**: in the network settings (KCM) use the import button of the connection list and *Export* in the menu of a connection. This works with Plasma 5.27 and newer; Plasma 5.24 (Ubuntu 22.04) does not call the plugin for this. The export writes the saved connection as NetworkManager has it, so export a new connection after you save it.
- **Command line**:

```bash
nmcli connection import type gpclient file ~/office.nmconnection
nmcli connection export "My VPN" ~/office.nmconnection
```

An export has no secrets (no password), so you can share the file. An import
checks the settings like the editors do, drops the secrets of the file and gives
the connection a new UUID: it never replaces the connection that it came from.

Older versions added `resolve [!UNAVAIL=return]` to the `hosts:` line of `/etc/nsswitch.conf` and kept a backup in `/etc/nsswitch.conf.gpclient-backup`. Version 2.0.0 does not change this file. If you do not want that edit, restore the backup.

## Packages

| Package | Description |
|---------|-------------|
| `network-manager-gpclient` | Core VPN service (required) |
| `network-manager-gpclient-gnome` | GNOME/GTK integration |
| `network-manager-gpclient-plasma` | KDE Plasma integration, built for the Plasma of the release (Qt5 on Ubuntu 22.04 and 24.04, Qt6 on 26.04) |
| `network-manager-gpclient-plasma-6` | KDE Plasma 6 integration for KDE neon (Ubuntu 24.04 with Plasma 6), built against the neon repository; `amd64` only |

## Architecture

```
┌─────────────────────────┐
│   GNOME Settings        │
│   KDE Plasma NM         │
│   nm-connection-editor  │
└───────────┬─────────────┘
            │ Configuration
            ▼
┌─────────────────────────┐
│   NetworkManager        │
└───────────┬─────────────┘
            │ D-Bus
            ▼
┌─────────────────────────┐
│ nm-gpclient-service     │  ← Python VPN Service (systemd)
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│   gpclient / gpauth     │  ← VPN connection + SAML auth
└─────────────────────────┘
```

## Project Structure

```
├── service/                    # Python VPN service backend
│   └── nm-gpclient-service.py
├── plugins/
│   ├── gnome/                  # GNOME/GTK plugins (C)
│   └── plasma/                 # KDE Plasma plugin (C++/Qt)
├── config/                     # NetworkManager & systemd configuration
├── .github/
│   ├── scripts/                # build-apt-repo.sh (apt repository builder)
│   └── workflows/              # build/release and Pages publishing
├── scripts/                    # Helper scripts (browser-wrapper)
├── external/
│   └── GlobalProtect-openconnect/  # VPN client (submodule)
└── debian/                     # Debian packaging
```

## Building from Source

### Requirements

- GNOME plugins: `libglib2.0-dev`, `libnm-dev`, `libgtk-3-dev`, `libgtk-4-dev`, `libnma-dev`
- Plasma plugin: `cmake`, `extra-cmake-modules`, `plasma-nm-dev`, Qt5 libraries
- VPN client: `cargo` (Rust), `libssl-dev`, `libopenconnect-dev`

### Build

```bash
./build-all.sh          # Build for all Ubuntu versions and variants (22.04, 24.04, 26.04, 24.04-neon on amd64 hosts only)
./build-all.sh 24.04    # Build for Ubuntu 24.04 only
./build-all.sh 26.04    # Build for Ubuntu 26.04 only (Plasma 6 / Qt6 / KF6)
./build-all.sh 24.04-neon  # Build the KDE neon variant (amd64 only)
VERSION_SUFFIX=+pr54.1 ./build-all.sh 24.04  # Add the suffix of CI pull request builds to the version
```

Notes for Ubuntu 26.04:
- The Plasma plugin is built against Qt6/KF6 (`plasma-nm`/`plasma-nm-dev`,
  `libkf6networkmanagerqt-dev`, `qt6-base-dev`).
- The Plasma plugin module installs into `/usr/lib/<multiarch>/qt6/plugins/` (`<multiarch>` is
  `x86_64-linux-gnu` on amd64, `aarch64-linux-gnu` on arm64)
  instead of the `qt5/` path used on 22.04/24.04.

### Build Individual Components

```bash
make gnome-plugins      # Build only GNOME plugins
cd plugins/plasma && ./build.sh  # Build only Plasma plugin
```

## GlobalProtect-openconnect version

Upstream version: [`v3.2.0`](https://github.com/yuezk/GlobalProtect-openconnect/releases/tag/v3.2.0)

The `external/GlobalProtect-openconnect` submodule is pinned to a release tag,
never to upstream `main`.

Clone with the submodules:

```bash
git clone --recursive https://github.com/WMP/GlobalProtect-SAML-NetworkManager.git
```

Update an existing clone. `git submodule sync` is needed because upstream 3.x
moved the nested openconnect submodule to `github.com/yuezk/openconnect`:

```bash
git pull
git submodule sync --recursive
git submodule update --init --recursive
```

To bump the version, see [docs/UPSTREAM.md](docs/UPSTREAM.md).

## Why Microsoft Edge?

Microsoft Edge is the recommended browser for SAML authentication because:

- **Microsoft Intune compatibility** - Edge integrates with Microsoft Entra ID (Azure AD) and Intune MDM, enabling seamless SSO authentication without additional password prompts
- **Keyless authentication** - When enrolled in Intune, Edge can use device certificates and Windows Hello credentials stored in the system, eliminating manual credential entry
- **GlobalProtect callback handling** - Edge properly handles the `globalprotectcallback://` protocol used to pass authentication tokens back to the VPN client

The included `edge-wrapper` script handles:
- Running Edge with correct Wayland/X11 display settings
- Working around a read-only home (ProtectHome=read-only), see the note below
- Auto-closing Edge window after successful authentication
- Setting up Edge policies for automatic protocol handling

**Security note:** the service normally runs in its own systemd unit (`nm-gpclient.service`), where your home directory is writable. The edge-wrapper then uses **your own Edge profile** in `~/.config/microsoft-edge` (saved sign-ins, cookies, the Intune/Entra ID device registration), which single sign-on needs. Only when the home directory is read-only (the service fell back to NetworkManager's sandbox, `ProtectHome=read-only`, e.g. when `systemctl` could not start the unit) does the wrapper use a separate profile in a private directory (mode 700, owned by you): `/tmp/edge-wrapper-$UID/`. A directory in `/tmp` that is not yours or not private is never used; the wrapper takes a new random one instead. The journal says which profile was used ("using the user's own browser profile" or "using temp HOME").

Firefox and Chrome also work but may require manual credential entry for Intune-protected portals.

## Troubleshooting

```bash
# Watch the service's own logs (preferred — this works while the
# service is auto-activated by NetworkManager/systemd)
sudo journalctl -u nm-gpclient -f

# Or filter NetworkManager logs for plugin activity
sudo journalctl -u NetworkManager -f | grep gpclient

# Verify installation
ls -l /usr/lib/NetworkManager/nm-gpclient-service
ls -l /usr/lib/*-linux-gnu/NetworkManager/libnm-vpn-plugin-gpclient*.so
```

### Running the service manually for debugging

The service is normally **auto-started** on demand the first time
NetworkManager touches a GlobalProtect VPN connection: NetworkManager runs the
program of the `.name` file, which starts the systemd unit `nm-gpclient.service`
(so the service and the SAML browser run in that unit, not in NetworkManager's
sandbox). You do not need to start it by hand for normal use.

If you want to run it manually (for example with `--debug`), you have
to stop the auto-started instance first, otherwise both processes try
to claim the same D-Bus name and you get
`sd_bus_internals.SdBusRequestNameExistsError` (see [#1](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/1)):

```bash
sudo systemctl stop nm-gpclient
sudo /usr/lib/NetworkManager/nm-gpclient-service --debug --persist
```

Without `--persist` the service exits by itself after 3 minutes with no VPN
session (NetworkManager starts it again when it needs it).

That error on its own does **not** mean the VPN is broken — it just
means the service is already running. The actual error from a failing
VPN connect will be in `journalctl -u nm-gpclient`.

### The VPN fails immediately with an SSL error

```
error:0A000152:SSL routines:final_renegotiate:unsafe legacy renegotiation disabled
```

The portal's TLS stack needs renegotiation that OpenSSL 3 refuses by default, so
the connection dies before any browser can open. The service notices this, and
retries once with gpclient's `--fix-openssl` workaround, then records it in the
profile (**Legacy TLS renegotiation: Always on** in the editor) so the failed
first attempt does not repeat. To set it up front, or to turn it off:

```bash
nmcli connection modify "My VPN" +vpn.data fix-openssl=true    # or false
```

### Two browser windows open for one connection

gpclient tries the gateway with the portal's authentication cookie first and
authenticates again when the gateway refuses it. If the address in your
connection is itself one of the gateways in `gateway-list`, tick **Address is a
gateway (skip the portal)** to get a single authentication. Details:
[docs/INTERACTIVE_AUTH.md](docs/INTERACTIVE_AUTH.md#two-browser-windows-for-one-connection).

### The authentication window has a different size every time

The `--window-size` the wrapper passes only applies when *it* creates the
window. If a browser is already running, the URL is handed to that instance,
which ignores the flag and restores its own remembered geometry. Wayland gives
no way to fix this from outside the browser - GNOME's compositor exposes no
window control at all, and KWin ignores a geometry change while the window is
still being mapped.

On KDE you can pin it down once with a window rule, which KWin applies during
placement: *System Settings → Window Management → Window Rules → Add New*, then
**Window class** → *Substring match* → `127.0.0.1` (the authentication window is
an app-mode window whose class is derived from the local callback address, so
this matches it and nothing else), and set **Size** to *Apply Initially*.

### The authentication browser does not open, or closes too early

```bash
# The wrapper logs every launch and always says why it finished
# Messages written as root and before the private directory exists are in the journal
journalctl -t gpclient-browser-wrapper | grep -E "done:|WARNING|ERROR"
# The part that runs as the desktop user writes <base>/wrapper.log
# (<base> as in docs/EDGE_WRAPPER.md; /tmp/edge-wrapper-$(id -u), or the mktemp directory)
grep -E "done:|WARNING|ERROR" /tmp/edge-wrapper-$(id -u)/wrapper.log

# Try the browser on its own - the window must open and stay open
GP_BROWSER=firefox /usr/libexec/gpclient/browser-wrapper "https://example.com"
```

The service log shows which user gets the browser. gpclient builds the session
environment of gpauth itself, so the service does not log it:

```bash
sudo journalctl -u nm-gpclient | grep "Environment: SUDO_UID"
```

More in [docs/EDGE_WRAPPER.md](docs/EDGE_WRAPPER.md#troubleshooting).

### Debug vpnc-script

The repository has a small wrapper, `scripts/vpnc-script-debug`. It writes the tunnel settings that openconnect gives to vpnc-script (`reason`, `TUNDEV`, `VPNGATEWAY`, `INTERNAL_IP4_*`, `INTERNAL_IP6_*`, `CISCO_SPLIT_*`, `MTU`, `CISCO_DEF_DOMAIN`) and the `-x` trace of the real script to the journal, then runs the real script (`/usr/share/vpnc-scripts/vpnc-script`, else `/etc/vpnc/vpnc-script` on the RPM distributions, or the file in `VPNC_SCRIPT_REAL`) and returns its exit status. The package does **not** install it. Do not overwrite the system `vpnc-script`: install the wrapper as a separate file and point gpclient (or openconnect) at it:

```bash
sudo curl -o /usr/local/sbin/vpnc-script-debug https://raw.githubusercontent.com/WMP/GlobalProtect-SAML-NetworkManager/main/scripts/vpnc-script-debug
sudo chmod 755 /usr/local/sbin/vpnc-script-debug
sudo gpclient connect --script /usr/local/sbin/vpnc-script-debug <portal>   # manual run
sudo journalctl -t vpnc-script-debug
```

Remove the file when you are done: `sudo rm /usr/local/sbin/vpnc-script-debug`.

## Documentation

- [docs/README.md](docs/README.md) - Full documentation
- [docs/APT_REPO.md](docs/APT_REPO.md) - apt repository: using it, releasing, key rotation
- [docs/RPM_REPO.md](docs/RPM_REPO.md) - RPM packages and repositories (Fedora, RHEL 10, openSUSE)
- [docs/ARCH_REPO.md](docs/ARCH_REPO.md) - Arch Linux packages and pacman repository
- [docs/EDGE_WRAPPER.md](docs/EDGE_WRAPPER.md) - Browser wrapper and browser integration
- [docs/PYTHON_SERVICE.md](docs/PYTHON_SERVICE.md) - Service implementation details
- [docs/INTERACTIVE_AUTH.md](docs/INTERACTIVE_AUTH.md) - Standard login / RSA token / OTP authentication, gateway selection
- [docs/GNOME_SETTINGS_INTEGRATION.md](docs/GNOME_SETTINGS_INTEGRATION.md) - GNOME integration
- [docs/PLASMA_IMPLEMENTATION.md](docs/PLASMA_IMPLEMENTATION.md) - Plasma plugin details

## License

See [debian/copyright](debian/copyright).

## Credits

This project uses [GlobalProtect-openconnect](https://github.com/yuezk/GlobalProtect-openconnect) by yuezk as a submodule. From that project we build and include:
- `gpclient` - VPN client binary that handles the actual VPN connection
- `gpauth` - SAML authentication handler

The NetworkManager integration (plugins for GNOME/Plasma, Python service, D-Bus configuration) is original work in this repository.

## Related Projects

- [GlobalProtect-openconnect](https://github.com/yuezk/GlobalProtect-openconnect) - VPN client backend by yuezk
- [NetworkManager](https://networkmanager.dev/) - Linux network management
