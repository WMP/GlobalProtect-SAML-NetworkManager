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

Optionally also store the second-phase (gateway/AD) password:

```bash
nmcli connection modify "My VPN" +vpn.secrets password=SuperSecret
```

### Plain username + password portal (no SAML, no MFA)

Fully non-interactive setup — ask NetworkManager to collect and store the
password upfront:

```bash
nmcli connection modify "My VPN" +vpn.data username=jdoe
nmcli connection modify "My VPN" +vpn.data auth-mode=credentials
nmcli connection modify "My VPN" +vpn.secrets password=SuperSecret
```

With `auth-mode=credentials` the service reports to NetworkManager that the
`vpn` setting needs secrets before connecting, so GUI applets will prompt for
(and can save) the password like for any other VPN type. Without it the
password is only requested mid-connection when gpclient actually asks.

### SAML portals

Nothing changes — `auth-mode` defaults to `saml` and authentication happens
in the browser as before.

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

`--gateway` is deliberately never passed to `gpclient`: it makes gpclient abort
with `Cannot find gateway specified` when the value is unknown, which would
remove any chance of falling back
([issue #7](https://github.com/WMP/GlobalProtect-SAML-NetworkManager/issues/7)).

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

`GP_AUTH_TIMEOUT` applies to each browser login. A portal that asks for a second
login at the gateway can open the browser twice, so 360 s may not be enough.
Then tick **Address is a gateway** (one login) or raise `vpn.timeout` (for
example to 660 s).
