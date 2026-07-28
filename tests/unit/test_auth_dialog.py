"""
Unit tests for the NetworkManager auth dialog.

The dialog talks the auth-dialog protocol over stdin/stdout, so it is driven as
a subprocess here. Only the paths that need no GTK are covered - showing the
dialog itself requires an X11/Wayland session.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import subprocess
import sys

DIALOG = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "auth-dialog", "nm-gpclient-auth-dialog.py"
    )
)
SERVICE = "org.freedesktop.NetworkManager.gpclient"


def run_dialog(data=None, secrets=None, hints=(), interaction=True, reprompt=False):
    args = [
        sys.executable,
        DIALOG,
        "-u",
        "e5b3e5b3-0000-0000-0000-000000000000",
        "-n",
        "Work VPN",
        "-s",
        SERVICE,
    ]
    if interaction:
        args.append("-i")
    if reprompt:
        args.append("-r")
    for hint in hints:
        args.extend(["-t", hint])

    lines = []
    for key, value in (data or {}).items():
        lines.append(f"DATA_KEY={key}")
        lines.append(f"DATA_VAL={value}")
    for key, value in (secrets or {}).items():
        lines.append(f"SECRET_KEY={key}")
        lines.append(f"SECRET_VAL={value}")
    lines.append("DONE")
    lines.append("QUIT")

    return subprocess.run(
        args,
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
    )


class TestSamlConnections:
    """Issue #8: a SAML connection has no stored secrets, and saying so must
    look like success - an error is reported as "user canceled the secrets
    request" and NetworkManager then waits out its 25 s timeout."""

    def test_no_secrets_needed_is_a_success(self):
        result = run_dialog(data={"gateway": "vpn.example.com"})
        assert result.returncode == 0
        assert result.stdout == "\n\n"

    def test_works_without_interaction_allowed(self):
        # Opening the connection editor asks without allowing interaction
        result = run_dialog(data={"gateway": "vpn.example.com"}, interaction=False)
        assert result.returncode == 0
        assert result.stdout == "\n\n"

    def test_explicit_saml_auth_mode(self):
        result = run_dialog(data={"gateway": "vpn.example.com", "auth-mode": "saml"})
        assert result.returncode == 0
        assert result.stdout == "\n\n"


class TestCredentialsConnections:
    def test_stored_password_is_returned_without_ui(self):
        result = run_dialog(
            data={"gateway": "vpn.example.com", "auth-mode": "credentials"},
            secrets={"password": "s3cret"},
        )
        assert result.returncode == 0
        assert result.stdout == "password\ns3cret\n\n\n"

    def test_password_not_required_returns_nothing(self):
        result = run_dialog(
            data={
                "gateway": "vpn.example.com",
                "auth-mode": "credentials",
                "password-flags": "4",
            }
        )
        assert result.returncode == 0
        assert result.stdout == "\n\n"

    def test_missing_password_without_interaction_fails(self):
        result = run_dialog(
            data={"gateway": "vpn.example.com", "auth-mode": "credentials"},
            interaction=False,
        )
        assert result.returncode == 1
        assert "interaction is not allowed" in result.stderr


class TestHints:
    def test_challenge_hint_needs_interaction(self):
        # A one-time token from the service (SecretsRequired) must never be
        # answered from storage
        result = run_dialog(
            data={"gateway": "vpn.example.com"},
            secrets={"otp": "123456"},
            hints=["x-vpn-message:Please enter RSA token", "otp"],
            interaction=False,
            reprompt=True,
        )
        assert result.returncode == 1

    def test_unsupported_service_is_rejected(self):
        result = subprocess.run(
            [
                sys.executable,
                DIALOG,
                "-u",
                "uuid",
                "-n",
                "Work VPN",
                "-s",
                "org.freedesktop.NetworkManager.openvpn",
            ],
            input="DONE\n",
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 1
        assert "Unsupported VPN service" in result.stdout + result.stderr
