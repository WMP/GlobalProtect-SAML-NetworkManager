# Debian Package Structure

This project builds **3 separate Debian packages** to support different desktop environments.

## Package Overview

```
┌─────────────────────────────────────────┐
│  network-manager-gpclient (BASE)        │
│  ├── Python VPN service                 │
│  ├── gpclient, gpauth                   │
│  ├── Configuration files                │
│  └── Helper scripts (edge-wrapper)      │
└─────────────────────────────────────────┘
                  ▲
                  │ depends on
    ┌─────────────┴──────────────┐
    │                            │
┌───┴────────────────┐  ┌────────┴─────────────┐
│ ...-gnome          │  │ ...-plasma           │
│ ├── GTK4 plugin    │  │ └── Qt5 plugin       │
│ ├── GTK3 plugin    │  │                      │
│ └── Simple plugin  │  │                      │
└────────────────────┘  └──────────────────────┘
```

## Packages

### 1. network-manager-gpclient (core)

**Required by both GUI packages.**

Contents:
- `/usr/lib/NetworkManager/nm-gpclient-service` - Python VPN service
- `/usr/lib/NetworkManager/VPN/nm-gpclient-service.name` - Service descriptor
- `/usr/libexec/gpclient/edge-wrapper` - Browser wrapper script
- `/usr/bin/gpclient`, `/usr/bin/gpauth` - VPN client binaries
- `/usr/share/doc/network-manager-gpclient/third-party-licenses.txt` - licenses of the Rust crates (see "Licenses")
- D-Bus and systemd configuration files

The unit `config/nm-gpclient.service` (used for D-Bus activation) runs the
service as root with a small set of sandbox options (issue #54):
`ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`,
`LockPersonality`, `RestrictRealtime`, `RestrictSUIDSGID` and
`SystemCallArchitectures=native`. The unit file lists, with reasons, the
options it does not use: the browser runs inside this unit's process tree, so
`ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, `RestrictNamespaces`,
`MemoryDenyWriteExecute`, `PrivateNetwork`, `ProtectProc`, `PrivateDevices`
and `ProtectSystem=strict` stay off. `ProtectClock` stays off too: systemd
before 252 (Ubuntu 22.04) turns it into a device allow-list that blocks
`/dev/net/tun`.

An upgrade does not reload systemd (the unit is installed with `--no-restart-after-upgrade`
and is not enabled), so the new options apply from the next start of the service after
`systemctl daemon-reload` or a reboot.

How to test a change of the sandbox options (on a real machine, on Ubuntu
22.04 with systemd 249 as well as on a current release, because some options
behave differently in older systemd):

- [ ] `systemd-analyze security nm-gpclient.service` before and after.
- [ ] After an upgrade and `systemctl daemon-reload`:
      `systemctl show nm-gpclient.service -p ProtectKernelModules` shows `yes`.
- [ ] `systemctl show nm-gpclient.service -p DeviceAllow -p DevicePolicy`
      while connected: `DeviceAllow` is empty and `DevicePolicy=auto`.
- [ ] SAML login with each browser: Edge, Firefox (also the snap on Ubuntu),
      Chrome and the default one.
- [ ] Login with credentials and with RSA or another one-time password.
- [ ] Login with a HIP script.
- [ ] DNS with systemd-resolved, and with a plain `/etc/resolv.conf`.
- [ ] A connection with `never-default`: the default route stays unchanged.
- [ ] Disconnect: the tun device, routes and DNS settings are removed.
- [ ] `journalctl -b -u nm-gpclient.service` and `journalctl -b -k` show no
      "Operation not permitted" or `seccomp` denials.

Dependencies:
- `network-manager`
- `openconnect`
- `python3`, `python3-sdbus`

### 2. network-manager-gpclient-gnome

**For GNOME, MATE, Cinnamon, XFCE, etc.**

Contents:
- `/usr/lib/<multiarch>/NetworkManager/libnm-vpn-plugin-gpclient.so`
- `/usr/lib/<multiarch>/NetworkManager/libnm-vpn-plugin-gpclient-editor.so` (GTK3)
- `/usr/lib/<multiarch>/NetworkManager/libnm-gtk4-vpn-plugin-gpclient-editor.so` (GTK4, Ubuntu 24.04+)

Supports:
- GNOME Settings (GTK4)
- nm-connection-editor (GTK3)
- Other GTK-based network managers

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `network-manager-gnome`

### 3. network-manager-gpclient-plasma

**For KDE Plasma desktop.**

Contents (Qt5 on Ubuntu 22.04 and 24.04, Qt6 on 26.04):
- `/usr/lib/<multiarch>/qt5/plugins/plasma/network/vpn/plasmanetworkmanagement_gpclientui.so`
  (`qt6/` on 26.04)
- `/usr/share/kservices5/plasmanetworkmanagement_gpclientui.desktop` (Qt5 only)

Supports:
- KDE Plasma NetworkManager applet

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm` (on Ubuntu 22.04 and 24.04 `plasma-nm (<< 4:6)`, on 26.04
  `plasma-nm (>= 4:6)`: the plugin fits the Plasma generation of its Qt)

### 4. network-manager-gpclient-plasma-6

**For KDE neon: Ubuntu 24.04 with Plasma 6.** `amd64` only.

Ubuntu 24.04 ships Plasma 5, KDE neon ships Plasma 6 / KF6 from
`https://archive.neon.kde.org/user`, so `network-manager-gpclient-plasma` (Qt5) does not
work there. This package has the same plugin built for Qt6 / KF6 against the neon
repository, in `/usr/lib/<multiarch>/qt6/plugins/plasma/network/vpn/`. It is built by
`debian/control.ubuntu24.04-neon` with `Dockerfile.ubuntu24.04-neon`: that control file
has this package only, and `debian/rules` then builds the Plasma plugin and nothing else.
Version `<version>~noble1` like the other 24.04 packages; it goes to the `noble` suite.

Dependencies:
- `network-manager-gpclient (= ${binary:Version})`
- `plasma-nm (>= 4:6)`

Conflicts with `network-manager-gpclient-plasma`: only one Plasma generation can use the
plugin (Qt5 or Qt6), so only one of the two packages makes sense on a system. They share no
file (the Qt5 plugin is in the `qt5` directory, this one in `qt6`). The Qt5 package depends
on `plasma-nm (<< 4:6)`, so apt refuses it on Plasma 6.

## Building Packages

### Recommended method (Docker-based):

Run `./build-all.sh` (all targets) or `./build-all.sh <target>`. Targets, the
neon variant, output directories and `VERSION_SUFFIX` are in [BUILD.md](BUILD.md).

RPM packages (Fedora, EL 10, openSUSE; the same three packages, Plasma 6 only)
are built with `rpm/build.sh <target> <amd64|arm64>`: see [RPM_REPO.md](RPM_REPO.md).
Arch Linux packages (the same three, `x86_64` only, `arch/PKGBUILD`) are built with
`arch/build.sh`: see [ARCH_REPO.md](ARCH_REPO.md).

### Mandatory access control

**SELinux (every RPM target: Fedora, EL, openSUSE).** The core RPM installs the
`network-manager-gpclient` SELinux policy module (`rpm/network-manager-gpclient.te`
and `.fc`). NetworkManager starts the program of the plugin in the domain
`NetworkManager_t`; the program hands over to `nm-gpclient.service` with
`systemctl start`. The module allows this and nothing else for the unit:

- The unit file has its own type, `nm_gpclient_unit_file_t`. The RPM relabels
  it with `%selinux_relabel_pre` (`%pre`) and `%selinux_relabel_post`
  (`%posttrans`), which also work in transactional updates. NetworkManager may
  `start` and get the `status` of this unit only, not of every unit file.
- The service needs its PTY (`ptmx`/`devpts`) and must inspect the desktop
  session bus socket while it starts SAML authentication. This applies when it
  runs in `NetworkManager_t`.

The module is loaded by the RPM scriptlets and removed when the package is
removed. On openSUSE the RPM requires the SELinux policy only when SELinux is
installed (AppArmor is still an install option there).

**AppArmor (`.deb` and Arch Linux), complain mode.** The package installs
`/etc/apparmor.d/usr.lib.NetworkManager.nm-gpclient-service`
(`config/apparmor/`). The profile is in complain mode: it blocks nothing. It
logs every access that its rules do not allow, to build an enforcing profile
later. To read the log lines:

```bash
journalctl -k | grep apparmor
```

`env`, the Python interpreter and `gpclient` stay under the profile (`ix`; on Ubuntu 26.04 `env` resolves to Rust coreutils or to `gnuenv`, so the rules name every path). `gpauth` runs unconfined (`ux`), so
that the browser keeps its own profile. To add local rules, use
`/etc/apparmor.d/local/usr.lib.NetworkManager.nm-gpclient-service`. The Debian
package loads the profile with `dh_apparmor`; on Arch Linux the install script
loads it when AppArmor is active, and `apparmor` is an optional dependency.

The profile has the flag `attach_disconnected`. The service runs in the mount
namespace of NetworkManager, and AppArmor cannot resolve a path outside it (a
"disconnected" path). Without the flag such a lookup fails also in complain
mode, and the service does not start (#188).

A downgrade to a version without the profile keeps the profile file and the
loaded profile. To remove them:

```bash
sudo apparmor_parser -R /etc/apparmor.d/usr.lib.NetworkManager.nm-gpclient-service
sudo rm /etc/apparmor.d/usr.lib.NetworkManager.nm-gpclient-service
```

### Version in `debian/changelog`

The package is native (`debian/source/format` is `3.0 (native)`), so a new entry has the
form `X.Y.Z` with no Debian revision (`2.0.0`, not `2.0.0-1`); the top line is for example
`network-manager-gpclient (2.0.0) unstable; urgency=medium`. Entries up to `1.5.0-1` keep
their revision. The release tag is `vX.Y.Z`; `build-release.yml` (`check_tag_version.py`)
rejects a tag that differs from the entry and a revision on a version from 2.0.0 on. CI adds `~<codename>1` (and `+pr<N>.<run>` for a
pull request). When a file lists a version, such as `debian/*.maintscript`, write it so that
`dpkg --compare-versions` orders it correctly: use `2.0.0~`, not `2.0.0-1~`.

### Local build:

```bash
dpkg-buildpackage -us -uc -b
```

## Licenses

`debian/copyright` (DEP-5) names the license of each part of the source:
this project, the upstream workspace crates, the bundled openconnect fork
(including the files with another license than LGPL-2.1-only: `lzo.c`,
`json/`, `jsondump.c`) and the vendored Rust crates (`vendor/`). The License
tag of `rpm/network-manager-gpclient.spec` is the same set as SPDX IDs
(`tests/unit/test_rpm_license.py` checks it).

`scripts/third_party_licenses.py` writes `third-party-licenses.txt`: every
Rust crate linked into `gpclient` and `gpauth` (Linux, normal dependencies,
no upstream workspace crates) with its SPDX license expression, and the full
text of its license and notice files. `debian/rules` and the spec run it after
`cargo build` (offline) and install the file (`/usr/share/doc/...`, `%license`).
It fails when a crate has no license expression, or no license file where the
license needs a notice. A crate that is known to have no file in its package
goes to `NOTICE_ALLOWLIST` in the script (a person checks each entry). After
a change of `Cargo.lock` run `scripts/vendor-crates.sh`, then
`scripts/third_party_licenses.py -o /tmp/x.txt`, and update `debian/copyright`
(`vendor/*`) and the spec License tag when a new license ID appears.

## Installation

Download packages from [GitHub Releases](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/releases) for your Ubuntu version.

Install two packages:
1. **network-manager-gpclient** - core package (required)
2. **network-manager-gpclient-gnome** - for GNOME/GTK desktops, or
   **network-manager-gpclient-plasma** - for KDE Plasma, or
   **network-manager-gpclient-plasma-6** - for KDE neon (Ubuntu 24.04 with Plasma 6)

```bash
sudo dpkg -i <downloaded-packages>.deb
sudo apt-get install -f  # install dependencies
```

## Supported Platforms

| Ubuntu Version | GTK3 | GTK4 | Plasma  |
|----------------|------|------|---------|
| 22.04 LTS      | ✅   | ❌   | ✅ (Qt5) |
| 24.04 LTS      | ✅   | ✅   | ✅ (Qt5; Qt6 on KDE neon, `amd64`) |
| 26.04 LTS      | ✅   | ✅   | ✅ (Qt6) |

Notes:
- Packages are built for `amd64` and `arm64`; `<multiarch>` in the paths above is
  `x86_64-linux-gnu` or `aarch64-linux-gnu` respectively.
- GTK4 editor requires `libnma-gtk4` which is not available on Ubuntu 22.04.
- The Plasma plugin is built against Qt5/KF5 on 22.04/24.04 and Qt6/KF6 on 26.04, the one the
  `Build-Depends` of `debian/control.ubuntu<version>` list (`debian/rules` reads it);
  install paths differ (`/usr/lib/<multiarch>/qt5/plugins/...` vs `qt6/`).

## Why Separate Packages?

1. **Smaller footprint** - GNOME users don't need Qt5 libraries, Plasma users don't need GTK4
2. **Cleaner dependencies** - No conflicting desktop environment packages
3. **Flexibility** - Install/remove GUI packages independently
4. **Best practices** - Follows pattern of other NetworkManager VPN plugins

## Verification

```bash
# Check installed packages
dpkg -l | grep network-manager-gpclient

# List package contents
dpkg -L network-manager-gpclient
dpkg -L network-manager-gpclient-gnome
dpkg -L network-manager-gpclient-plasma
```
