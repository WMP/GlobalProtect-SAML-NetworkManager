# Interactive authentication (RSA token / standard login portals)

Some GlobalProtect portals do not use SAML at all. Instead of opening a
browser, the portal answers the prelogin request with a *standard* login
challenge and `gpclient` asks for the credentials interactively on its
terminal, for example:

```
Please enter RSA token (Portal: vpn.example.com)
? Username:
? Passcode:
```

After the portal phase, the gateway may run a second authentication round
(e.g. domain username + Active Directory password) and MFA challenges
(one-time codes) can appear as well.

Historically the NetworkManager service spawned `gpclient` without a
terminal, so those prompts could never be displayed or answered and the
connection hung until it was disconnected
([issue #6](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/6)).

## How it works now

The service runs `gpclient` under a **pseudo-terminal (PTY)** and watches its
output:

1. Authentication banners (`Please enter RSA token (Portal: ...)`) are parsed
   to know which server/phase is asking.
2. When an interactive prompt is detected, the service answers it:
   - **Username** — from `vpn.data` key `username` (also passed to
     `gpclient --user`, so usually the prompt never appears), otherwise the
     user is asked.
   - **Password** — from the `password` secret stored in the connection,
     otherwise the user is asked.
   - **One-time secrets** (labels/banners mentioning *token*, *OTP*,
     *passcode*, *PIN*, *code*, *RSA*...) — the user is **always** asked;
     a stored password is never reused for these.
3. "Asking the user" uses the standard NetworkManager interactive secrets
   flow: the service emits the `SecretsRequired` D-Bus signal, the desktop
   applet shows a dialog (the plugin ships an auth dialog for
   GNOME/nm-applet), and the answer comes back via `NewSecrets`.

Because a stored password is only used **once** per connection attempt, a
two-phase flow like *portal: RSA token → gateway: AD password* works: the
token is asked interactively, the AD password can come from the stored
secret (or is asked as well).

## Configuration recipes

### RSA SecurID / one-time token portal

Store the username; the token (and any second-phase password, unless stored)
will be asked in a dialog on every connect:

```bash
nmcli connection modify "My VPN" +vpn.data username=jdoe
```

Optionally also store the second-phase (gateway/AD) password. Type it in the
**Password** field of the editor, or let `nmcli` ask for it (see the next
section). Do not put the password on the `nmcli` command line: other users can
read it in the process list, and your shell keeps it in its history.

### Plain username + password portal (no SAML, no MFA)

Ask NetworkManager to collect the password upfront. In the editor, choose
**Username and password** and type the password in the **Password** field: the
storage menu (the icon in the field in GNOME, the list under the field in
Plasma) sets `password-flags` (1 = this user only, 0 = all users, in the
profile as plain text, 2 = ask every time). Emptying the field does not delete
a stored password (NetworkManager keeps the old one): choose "ask every time"
to remove it from the profile. A new connection starts with
`password-flags=1` and `otp-flags=2`. With `nmcli`, do not give the password on
the command line. Set the flags and let `nmcli` ask:

```bash
nmcli connection modify "My VPN" +vpn.data username=jdoe
nmcli connection modify "My VPN" +vpn.data auth-mode=credentials
nmcli connection modify "My VPN" +vpn.data password-flags=1
nmcli --ask connection up "My VPN"
```

With `auth-mode=credentials` the service reports to NetworkManager that the
`vpn` setting needs secrets before connecting, so GUI applets will prompt for
(and can save) the password like for any other VPN type. Without it the
password is only requested mid-connection when gpclient actually asks.

### SAML portals

Nothing changes — `auth-mode` defaults to `saml` and authentication happens
in the browser as before. gpclient starts the browser itself (through
`gpauth`), as the desktop user, and this stays the default
(`saml-auth=service`). For the other way see the next section.

### SAML login in the auth dialog

With `vpn.data saml-auth=dialog` (and `auth-mode=saml`) the login runs in your
desktop session, not in the root service. The connection editors set it in the
field **SAML login** (**In the service (default)** removes the key, **In the
login dialog** writes `dialog`):

1. The service asks the secret agent for the secret `gpauth-result`. The
   request carries the hint `x-gp-saml:<JSON>` with the server, the
   `--gateway` and `--fix-openssl` settings and the browser.
2. The auth dialog (`nm-gpclient-auth-dialog`, it runs as you) starts
   `/usr/bin/gpauth --log-format json` with the wrapper
   (`/usr/libexec/gpclient/browser-wrapper`, see
   [EDGE_WRAPPER.md](EDGE_WRAPPER.md#who-starts-the-wrapper)). It shows no
   window of its own. When gpauth reports the legacy TLS error and
   `fix-openssl=auto`, it runs gpauth once more with `--fix-openssl`.
3. The dialog returns gpauth's result line. The service checks it (only a
   successful result with a username and a cookie is accepted; anything else
   fails the login), writes it once to gpclient's stdin
   (`gpclient connect --cookie-on-stdin`) and keeps it in memory only, until
   the tunnel is up or gpclient exits. It is never logged and never stored.
   gpclient gets no `--browser` and no `SUDO_UID` in this mode.

Other results:

- Cancel (or close) the browser, or a failed login: the dialog exits with 1.
  NetworkManager treats this as a cancel.
- The server has a standard (password) login: gpauth fails with "non-SAML
  prelogin". The dialog answers `standard` and the service runs gpclient the
  usual way, with the prompts described above.
- The stored preferred gateway is no longer offered by the portal: the service
  restarts gpclient without `--gateway`. The portal may accept a SAML result
  only once, so the dialog asks for a new login before this retry.
- The agent goes away (SIGTERM, end of stdin): the dialog stops gpauth.
- KDE Plasma: plasma-nm does not start the GNOME auth dialog. Its password
  dialog loads the Plasma plugin (`GpclientAuthWidget`), which runs gpauth
  with the same checks as the auth dialog and closes the password dialog by
  itself: it shows "Signing in…" and asks nothing. plasma-nm has no
  non-interactive answer for a VPN plugin, so the widget calls `accept()` (the
  result is ready) or `reject()` (cancelled, failed) on its window. This holds
  for the plasma-nm versions checked (5.24.7, 5.27.11, 6.3.5), whose
  `SecretAgent` shows the dialog and reads `secrets()` after `accepted`. The dialog is visible for the time of the
  login.

Limits:

- **One login per connection.** gpclient uses the result for the portal, or
  for the server when **Address is a gateway** is set. If a portal gateway
  wants its own SAML login, gpclient would have to start gpauth as root, which
  does not work here. The service sees the log record `SAML auth launch:
  gateway=true`, stops and fails the login with a message. Tick **Address is
  a gateway**, or use `saml-auth=service`.
- The connection must be started with a desktop agent (GNOME Shell,
  plasma-nm). A start without an agent fails at once with a clear message.
  nmcli does not work: its agent runs the auth dialog (external UI mode) but
  keeps only the entries it asks for and drops the result (`ShouldAsk=false`),
  so `nmcli --ask` asks for `gpauth-result` on the terminal. Use
  `saml-auth=service` with nmcli.
- The auth dialog refuses to run gpauth as root (an agent that runs as
  root): start the connection as the desktop user.
- The result is a one-time secret: flag `gpauth-result-flags=2` (not saved)
  is written to the profile after the activation has ended (tunnel up,
  failure or disconnect), not before the request: NetworkManager rejects every secrets
  request of an activation whose profile changed (issue #120). The dialog
  never fills it from a stored value.

## Client certificate

A portal or gateway can also ask for a client certificate (a certificate per
device, with SAML or with username and password,
[#134](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/134)).
Give gpclient the files with `vpn.data` keys; the editors have the same fields
(**Client certificate**, **Private key**, **Certificate passphrase**). In the
editors, type the path or click the file icon in the field to choose the file:

```bash
# PKCS#12 file (.p12, .pfx) or PEM file with the certificate and the key
nmcli connection modify "My VPN" +vpn.data usercert=/home/alice/.certs/me.p12
# a separate PEM key
nmcli connection modify "My VPN" +vpn.data userkey=/home/alice/.certs/me.key
# the passphrase of an encrypted key, kept for this user only (the keyring)
nmcli connection modify "My VPN" +vpn.data cert-pass-flags=1
nmcli connection modify "My VPN" vpn.secrets cert-pass=...
```

- The service passes `--certificate=<usercert>` and `--sslkey=<userkey>` to
  gpclient. Both must be absolute paths.
- **The files must be owned by the desktop user** (the user whose session shows
  the browser) and be regular files of at most 1 MiB (a link is followed). They
  must be readable by that user too. A short helper process that runs as the
  desktop user opens each file once, checks the open file and reads it, so a
  file in a directory that the user cannot enter is refused, even if root could
  read it (#183). The service (root) only writes the copy: it gives gpclient
  (root) and gpauth a private copy in a
  directory below `/run/nm-gpclient` that only root can write: owned by the
  desktop user, mode `0400`, with the extension of the original. gpclient never
  reads your path, so changing or replacing the original after the check does
  not change the copy (it is made at each connect). The service refuses another
  owner, a directory, a device or a FIFO, and the connection fails with the
  reason in the log. It deletes the copies when the connection ends or fails,
  and when the service stops; after a crash they stay until the next reboot
  (`/run`).
  The service finds no desktop user in some setups (no session): then it
  refuses `usercert`/`userkey` and the connection fails.
- Upstream picks the format by the extension only: `.p12` or `.pfx` is PKCS#12,
  any other name is PEM. The copy keeps the extension.
- The passphrase is never put on the command line (any user can read it in
  `/proc`). When the key or the PKCS#12 file is encrypted, gpclient asks
  "Enter the PEM passphrase:" or "Enter the PKCS#12 passphrase:". The service
  answers this prompt (and only this prompt) with the `cert-pass` secret. It
  uses the stored value once. When none is stored, the secret agent is asked.
  The VPN password is never typed there.
  `cert-pass-flags` works like `password-flags` (`1` agent, `0` profile file,
  `2` not saved, asked every time).
- A wrong passphrase: only a PEM key is asked again by gpclient. The service
  stops the login with "wrong certificate passphrase" when the same passphrase
  comes back (an agent can return the stored, wrong value without asking you) or
  after 3 passphrases. Correct or remove the stored `cert-pass`, then connect
  again. A wrong PKCS#12 passphrase makes gpclient exit with an error at once.
- Limitation of upstream gpclient: in SAML mode it hands the key passphrase to
  gpauth on the command line, where other users of the machine can read it for
  as long as gpauth runs. Prefer a key without a passphrase in a file with mode
  `0600` that only you can read, or report this upstream.

## Gateway selection

A portal usually offers several gateways and `gpclient` asks which one to use,
again on its terminal:

```
? Which gateway do you want to connect to?
> gw-warsaw (gw1.example.com)
  gw-frankfurt (gw2.example.com)
[↑↓ to move, enter to select, type to filter]
```

The service answers this **without asking the user**:

1. With `preferred-gateway` set in `vpn.data`, the matching entry is selected.
   The value is matched against the whole entry, then the name and the host
   part, then as a substring - so both `gw-frankfurt` and `gw2.example.com`
   work.
2. With no `preferred-gateway` (the default), the portal's first proposal wins.
   gpclient sorts the list by region, so this is the gateway it would have
   picked itself.
3. If a configured gateway is not offered any more, the first proposal is used
   and a warning is logged. The setting is left untouched - the portal may just
   have changed temporarily.

After a successful connection the portal's gateway list is cached in the profile
(`vpn.data gateway-list`, entries separated by `;`), and the connection editors
offer it as a drop-down for **Preferred gateway**. The first entry of that
drop-down, *First proposed by portal (automatic)*, stores nothing.

When the portal's list is longer than one page and the cache does not hold it
completely yet, the service reads it page by page before it selects: it presses
PageDown (which moves down by a page and stops at the last entry) until the
whole list was seen or the cursor reached the end, then Home, which puts the
cursor back on the first proposal. Only gpclient's `Found N gateways in portal
config` line tells the end of the list from a gpclient that stopped redrawing,
so without that line the list is not read at all. Nothing is selected on the
way. After a complete reading the gateway is chosen from the whole list (same
matching as before: whole entry, then name or host, then substring; the first
proposal if the configured gateway is not offered) and reached with Down keys
by its exact name. The list from such a reading, or a list that fits one page
and has N entries, replaces the cached one (gateways the portal dropped
disappear), and `gateway-list-count` is set to N, which marks the cached list
as complete (also when some entries are identical, so there are fewer than N
different ones). As long as that count equals the portal's and the visible page
is in the cache, the list is not read again. A portal config with a single
gateway (`Found 1`, then `Connecting to the only available gateway`) replaces
the cache with that gateway. A connection that sees only part of the list (for
example when gpclient stops redrawing, or logs no count) puts the entries it
saw first and keeps the stored ones it did not see; it never shrinks the cache.

```bash
# Pick a specific gateway from the command line
nmcli connection modify "My VPN" +vpn.data preferred-gateway="gw-frankfurt"

# Back to automatic
nmcli connection modify "My VPN" -vpn.data preferred-gateway

# See what the last successful connection discovered
nmcli -g vpn.data connection show "My VPN"
```

`--gateway` is never set to the server address: it makes gpclient abort with
`Cannot find gateway specified` when the value is unknown
([issue #7](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/7)).
It is passed only for `preferred-gateway`, and only when the portal offered that
gateway before (it is in `gateway-list`): the value is the whole entry the
editors store (`name (address)`), the name or the address. gpclient always gets
the address of that entry (its name only when the entry has no address), because
gpclient takes the first gateway with that name or address and names are not
unique. The option is used only without *Address is a gateway*:
with `as-gateway` the service passes `--as-gateway` and ignores
`preferred-gateway`. When gpclient does not find the gateway after all, the
service starts gpclient once more without the option and answers the gateway
prompt as before. Then the SAML login runs a second time, so a second browser
window or tab may open. With `--gateway` gpclient shows no
gateway prompt, so `gateway-list` is refreshed only on a connect without it (for
example when the preferred gateway is not in the list, or none is set).

### Portal or gateway address?

The address in the connection is normally a **portal**. If your organisation
gave you a gateway address instead, tick *Address is a gateway (skip the
portal)* (`vpn.data as-gateway=true`) - otherwise the portal workflow is tried
first and you may end up authenticating twice. When gpclient itself notices
this, the service logs a hint saying so.

### Two browser windows for one connection

Some portals authenticate you twice, which means two browser windows in a row:

```
gpauth started            <- portal authentication
Connecting to the selected gateway: gw1.example.com (gw1.example.com)
GP response error: ... respMsg = "Authentication failure: Invalid username or password"
Gateway login failed: Gateway login error: <none>
Performing the gateway authentication...
gpauth started            <- and again, for the gateway
```

Nothing is wrong with the credentials. gpclient first tries to log in to the
chosen gateway with the authentication cookie it got from the portal; when the
gateway refuses that cookie, it falls back to a full gateway authentication,
which opens a second browser window. This is gpclient's behaviour, not the
plugin's.

If the address in your connection is *itself* one of the gateways the portal
offers (compare it with the cached `gateway-list`), you can skip the portal
round entirely and get a single authentication:

```bash
nmcli connection modify "My VPN" +vpn.data as-gateway=true
```

The trade-off is that you no longer pick between gateways - you always connect
to the address in the connection.

## Desktop support

- **GNOME / nm-applet**: the plugin installs
  `/usr/libexec/nm-gpclient-auth-dialog` and declares `supports-hints=true`,
  so both upfront and mid-connection (RSA token/OTP) prompts show a GTK
  dialog.
- **KDE Plasma**: plasma-nm shows its own generic VPN secrets dialog for
  interactive requests.
- **nmcli**: activate with `nmcli --ask connection up "My VPN"` so nmcli can
  ask for secrets on the terminal, or store all secrets in the connection.
  Without any way to ask, the service fails the connection with a clear
  login error instead of hanging.

## Timeouts

The user has 300 s to answer an interactive secrets request; afterwards the
connection fails with `LOGIN_FAILED`.

The browser window for SAML authentication has its own timeout
(`GP_AUTH_TIMEOUT`, 300 s by default) - see
[EDGE_WRAPPER.md](EDGE_WRAPPER.md#environment-variables).

NetworkManager also stops a VPN activation after its own connect timeout
(`vpn.timeout`, 0 = default, usually 60 s). The login in the browser counts
towards it, so a slow SAML login with MFA can fail with "connect timeout
exceeded". Set it in the editor (**Connect timeout**) or with
`nmcli connection modify "My VPN" vpn.timeout 360`.

The connect timeout must be longer than `GP_AUTH_TIMEOUT` plus the time to
connect after the login. Then the limit of the browser login, with its clearer
error, comes first. 360 s fits the 300 s default. A new connection with browser
login starts with 360 s. While a connection has no timeout of its own (0) and
you have not changed the field, choosing browser login sets 360 s. If you
raise `GP_AUTH_TIMEOUT`, raise `vpn.timeout` too.

With `saml-auth=dialog` the request for `gpauth-result` has the same 300 s
limit as any secret request and covers the whole browser login; the wrapper
keeps its own `GP_AUTH_TIMEOUT` limit (read from the dialog's environment
now, which is the desktop session's, not the service's). `vpn.timeout`
applies as above.

`GP_AUTH_TIMEOUT` applies to each browser login. A portal that asks for a second
login at the gateway can open the browser twice, so 360 s may not be enough.
Then tick **Address is a gateway** (one login) or raise `vpn.timeout` (for
example to 660 s).
