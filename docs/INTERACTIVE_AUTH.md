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
