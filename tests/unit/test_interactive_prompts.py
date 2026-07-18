"""
Unit tests for interactive prompt detection in the nm-gpclient service
(issue #6: portals with RSA token / standard login challenges).

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""


class TestStripAnsi:
    def test_plain_text_unchanged(self, service_module):
        assert service_module.strip_ansi("Please enter RSA token") == (
            "Please enter RSA token"
        )

    def test_csi_color_sequences_removed(self, service_module):
        raw = "\x1b[32m?\x1b[0m \x1b[1mUsername:\x1b[0m "
        assert service_module.strip_ansi(raw) == "? Username: "

    def test_cursor_and_clear_sequences_removed(self, service_module):
        raw = "\x1b[2K\x1b[1G? Password: \x1b[?25l"
        assert service_module.strip_ansi(raw) == "? Password: "

    def test_newlines_preserved(self, service_module):
        raw = "line1\r\nline2\n"
        assert service_module.strip_ansi(raw) == "line1\r\nline2\n"


class TestAuthBanner:
    def test_rsa_token_banner(self, service_module):
        # Exact line from issue #6
        banner = service_module.parse_auth_banner(
            "Please enter RSA token (Portal: vpneast.comtechtel.com)"
        )
        assert banner == {
            "message": "Please enter RSA token",
            "kind": "Portal",
            "server": "vpneast.comtechtel.com",
        }

    def test_gateway_banner(self, service_module):
        banner = service_module.parse_auth_banner(
            "Please enter the login credentials (Gateway: gw.example.com)"
        )
        assert banner["kind"] == "Gateway"
        assert banner["server"] == "gw.example.com"

    def test_non_banner_lines(self, service_module):
        assert service_module.parse_auth_banner("gpclient started: 2.5.1") is None
        assert (
            service_module.parse_auth_banner("[INFO] connecting to portal") is None
        )


class TestDetectPrompt:
    def test_username_prompt(self, service_module):
        assert service_module.detect_prompt("? Username: ") == "Username"

    def test_password_prompt(self, service_module):
        assert service_module.detect_prompt("? Password: ") == "Password"

    def test_custom_label_from_server(self, service_module):
        assert service_module.detect_prompt("? Passcode: ") == "Passcode"

    def test_mfa_prompt_without_colon(self, service_module):
        # Gateway MFA challenges use a server-provided message with no colon
        assert (
            service_module.detect_prompt("? Enter the next tokencode ")
            == "Enter the next tokencode"
        )

    def test_regular_output_is_not_a_prompt(self, service_module):
        assert service_module.detect_prompt("Connecting to gateway...") is None
        assert service_module.detect_prompt("") is None

    def test_masked_echo_is_not_a_prompt(self, service_module):
        # While the user "types", inquire renders masked characters
        assert service_module.detect_prompt("? Password: ******") is None

    def test_finalized_text_answer_is_not_a_prompt(self, service_module):
        assert service_module.detect_prompt("? Username: jdoe") is None

    def test_echo_of_our_answer_is_skipped(self, service_module):
        assert (
            service_module.detect_prompt(
                "? Enter the next tokencode 123456", last_answer="123456"
            )
            is None
        )


class TestClassifyPrompt:
    def test_username_labels(self, service_module):
        for label in ("Username", "User", "Login", "Email", "E-mail address"):
            assert service_module.classify_prompt(label) == "username"

    def test_secret_labels(self, service_module):
        for label in ("Password", "Passcode", "PIN", "Enter the next tokencode"):
            assert service_module.classify_prompt(label) == "password"


class TestOneTimeSecret:
    def test_rsa_banner_is_one_time(self, service_module):
        assert service_module.is_one_time_secret("Please enter RSA token")

    def test_otp_labels_are_one_time(self, service_module):
        for text in ("Passcode", "PIN", "OTP", "Enter the next tokencode"):
            assert service_module.is_one_time_secret(text)

    def test_plain_password_is_not_one_time(self, service_module):
        assert not service_module.is_one_time_secret("Password")
        assert not service_module.is_one_time_secret(
            "Please enter the login credentials"
        )


class TestOutputScanner:
    def test_complete_lines_and_tail(self, service_module):
        scanner = service_module.OutputScanner()
        lines = scanner.feed("line1\r\nline2\n? Password: ")
        assert lines == ["line1", "line2"]
        assert scanner.tail == "? Password: "

    def test_tail_accumulates_across_chunks(self, service_module):
        scanner = service_module.OutputScanner()
        scanner.feed("? Pass")
        lines = scanner.feed("word: ")
        assert lines == []
        assert scanner.tail == "? Password: "

    def test_carriage_return_redraw_splits_lines(self, service_module):
        # inquire redraws the prompt line using \r
        scanner = service_module.OutputScanner()
        scanner.feed("? Username: \r? Username: j\r? Username: jd")
        assert scanner.tail == "? Username: jd"

    def test_full_rsa_flow_from_issue_6(self, service_module):
        scanner = service_module.OutputScanner()
        raw = (
            "[2026-07-16T23:25:20Z INFO  gpclient::cli] gpclient started: 2.5.1\r\n"
            "Please enter RSA token (Portal: vpneast.comtechtel.com)\r\n"
            "\x1b[32m?\x1b[0m Username: "
        )
        lines = scanner.feed(service_module.strip_ansi(raw))

        banners = [
            b
            for b in (service_module.parse_auth_banner(line) for line in lines)
            if b
        ]
        assert len(banners) == 1
        assert banners[0]["message"] == "Please enter RSA token"

        label = service_module.detect_prompt(scanner.tail)
        assert label == "Username"
        assert service_module.classify_prompt(label) == "username"
