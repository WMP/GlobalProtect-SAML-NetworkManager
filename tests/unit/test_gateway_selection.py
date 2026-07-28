"""
Unit tests for gateway selection and browser resolution in the nm-gpclient
service (issue #7: portal addresses, gateway lists, browsers that never opened).

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import asyncio

# A single-page gateway list as inquire renders it: the question, one line per
# option (marker, space, value; '>' marks the cursor) and the help footer. Every
# line is terminated, so nothing is left in the output tail.
SINGLE_PAGE = [
    "[2026-07-20T12:44:26Z INFO  gpapi::portal::config] Retrieve the portal config",
    "? Which gateway do you want to connect to?",
    "> gw-warsaw (gw1.example.com)",
    "  gw-frankfurt (gw2.example.com)",
    "  gw-london (gw3.example.com)",
    "[↑↓ to move, enter to select, type to filter]",
]

# More entries than inquire's page size: the edge row carries a scroll marker
PAGED = [
    "? Which gateway do you want to connect to?",
    "> gw-01 (gw01.example.com)",
    "  gw-02 (gw02.example.com)",
    "  gw-03 (gw03.example.com)",
    "  gw-04 (gw04.example.com)",
    "  gw-05 (gw05.example.com)",
    "  gw-06 (gw06.example.com)",
    "v gw-07 (gw07.example.com)",
    "[↑↓ to move, enter to select, type to filter]",
]


def frame_with_cursor(options, cursor, more=False):
    """Build a detected-frame dict the way detect_select_prompt() would"""
    return {
        "message": "Which gateway do you want to connect to?",
        "options": list(options),
        "cursor": cursor,
        "more": more,
    }


def make_plugin(service_module, preferred="", sent=None):
    plugin = service_module.GpclientVPNPlugin()
    plugin.preferred_gateway = preferred
    if sent is not None:
        plugin._write_keys = lambda data, description: sent.append(data)
    return plugin


class TestDetectSelectPrompt:
    def test_single_page_frame(self, service_module):
        frame = service_module.detect_select_prompt(SINGLE_PAGE)
        assert frame["message"] == "Which gateway do you want to connect to?"
        assert frame["options"] == [
            "gw-warsaw (gw1.example.com)",
            "gw-frankfurt (gw2.example.com)",
            "gw-london (gw3.example.com)",
        ]
        assert frame["cursor"] == 0
        assert frame["more"] is False

    def test_scroll_marker_means_more_entries(self, service_module):
        frame = service_module.detect_select_prompt(PAGED)
        assert frame["more"] is True
        assert len(frame["options"]) == 7
        # The scroll-marked row is still an option
        assert frame["options"][-1] == "gw-07 (gw07.example.com)"

    def test_cursor_is_tracked(self, service_module):
        lines = [
            "? Which gateway do you want to connect to?",
            "  gw-warsaw (gw1.example.com)",
            "> gw-frankfurt (gw2.example.com)",
            "[↑↓ to move, enter to select, type to filter]",
        ]
        frame = service_module.detect_select_prompt(lines)
        assert frame["cursor"] == 1

    def test_option_name_starting_like_a_marker_is_kept(self, service_module):
        # 'v'/'^'/'>' as the first letter of a gateway name must not be eaten:
        # only a marker in column 0 followed by a space is a marker
        lines = [
            "? Which gateway do you want to connect to?",
            "> vpn-central (gwv.example.com)",
            "  ^caret-name (gwc.example.com)",
            "[↑↓ to move, enter to select, type to filter]",
        ]
        frame = service_module.detect_select_prompt(lines)
        assert frame["options"] == [
            "vpn-central (gwv.example.com)",
            "^caret-name (gwc.example.com)",
        ]
        assert frame["more"] is False

    def test_only_the_latest_frame_is_used(self, service_module):
        # A redraw appends a second frame; the newest one wins
        frame = service_module.detect_select_prompt(
            SINGLE_PAGE
            + [
                "? Which gateway do you want to connect to?",
                "  gw-warsaw (gw1.example.com)",
                "> gw-frankfurt (gw2.example.com)",
                "  gw-london (gw3.example.com)",
                "[↑↓ to move, enter to select, type to filter]",
            ]
        )
        assert frame["cursor"] == 1

    def test_regular_output_is_not_a_frame(self, service_module):
        assert service_module.detect_select_prompt(SINGLE_PAGE[:1]) is None
        assert service_module.detect_select_prompt([]) is None

    def test_help_footer_without_question_is_not_a_frame(self, service_module):
        assert (
            service_module.detect_select_prompt(
                ["some output", "[↑↓ to move, enter to select]"]
            )
            is None
        )

    def test_text_prompt_is_not_a_list_frame(self, service_module):
        # The RSA/username flow must keep going through detect_prompt()
        assert (
            service_module.detect_select_prompt(
                ["Please enter RSA token (Portal: vpn.example.com)", "? Username: "]
            )
            is None
        )


class TestGatewayQuestionIsNotAUsername:
    """Regression for the misclassification the list prompt used to cause."""

    def test_question_never_reaches_the_text_prompt_path(self, service_module):
        # The frame ends with a terminated help line, so the tail is empty and
        # detect_prompt() sees nothing to answer
        scanner = service_module.OutputScanner()
        scanner.feed("\r\n".join(SINGLE_PAGE) + "\r\n")
        assert scanner.tail == ""
        assert service_module.detect_prompt(scanner.tail) is None


class TestPickGateway:
    OPTIONS = [
        "gw-warsaw (gw1.example.com)",
        "gw-frankfurt (gw2.example.com)",
        "gw-london (gw3.example.com)",
    ]

    def test_empty_preference_takes_the_first(self, service_module):
        assert service_module.pick_gateway(self.OPTIONS, "") == self.OPTIONS[0]

    def test_exact_entry(self, service_module):
        assert (
            service_module.pick_gateway(self.OPTIONS, "gw-london (gw3.example.com)")
            == self.OPTIONS[2]
        )

    def test_by_name(self, service_module):
        assert (
            service_module.pick_gateway(self.OPTIONS, "gw-frankfurt") == self.OPTIONS[1]
        )

    def test_by_host(self, service_module):
        assert (
            service_module.pick_gateway(self.OPTIONS, "gw3.example.com")
            == self.OPTIONS[2]
        )

    def test_case_insensitive(self, service_module):
        assert service_module.pick_gateway(self.OPTIONS, "GW-WARSAW") == self.OPTIONS[0]

    def test_exact_name_wins_over_substring(self, service_module):
        options = ["gw-1 (a.example.com)", "gw-10 (b.example.com)"]
        # "gw-10" appears in no other name, "gw-1" must not grab it
        assert service_module.pick_gateway(options, "gw-10") == options[1]

    def test_unknown_gateway(self, service_module):
        assert service_module.pick_gateway(self.OPTIONS, "gw-tokyo") is None


class TestAnswerGatewayList:
    def _run(self, plugin, frame):
        asyncio.run(plugin._handle_select_prompt(frame))

    def test_no_preference_selects_the_first_proposal(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="", sent=sent)
        plugin._press_list_down = lambda: (_ for _ in ()).throw(
            AssertionError("must not walk the list")
        )

        self._run(plugin, service_module.detect_select_prompt(SINGLE_PAGE))

        assert sent == [service_module.KEY_ENTER]

    def test_preferred_gateway_is_reached_with_down_keys(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="gw-london", sent=sent)

        options = service_module.detect_select_prompt(SINGLE_PAGE)["options"]
        moves = [frame_with_cursor(options, 1), frame_with_cursor(options, 2)]

        async def fake_down():
            return moves.pop(0)

        plugin._press_list_down = fake_down
        self._run(plugin, service_module.detect_select_prompt(SINGLE_PAGE))

        # Two moves down to the third entry, then confirm
        assert sent == [service_module.KEY_ENTER]
        assert moves == []

    def test_unavailable_preference_falls_back_to_first(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="gw-tokyo", sent=sent)
        plugin._press_list_down = lambda: (_ for _ in ()).throw(
            AssertionError("must not walk a fully visible list")
        )

        self._run(plugin, service_module.detect_select_prompt(SINGLE_PAGE))

        assert sent == [service_module.KEY_ENTER]
        # The user's setting must survive the fallback
        assert plugin.preferred_gateway == "gw-tokyo"

    def test_paged_list_is_walked_and_wraps_back(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="gw-99", sent=sent)

        frame = service_module.detect_select_prompt(PAGED)
        options = frame["options"]
        # Walk through every entry and come back to the starting one
        moves = [
            frame_with_cursor(options, index, more=True)
            for index in list(range(1, len(options))) + [0]
        ]

        async def fake_down():
            return moves.pop(0)

        plugin._press_list_down = fake_down
        self._run(plugin, frame)

        # One Enter after the wrap-around, i.e. the first proposal
        assert sent == [service_module.KEY_ENTER]
        assert moves == []

    def test_stalled_redraw_still_confirms(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="gw-london", sent=sent)

        async def no_redraw():
            return None

        plugin._press_list_down = no_redraw
        self._run(plugin, service_module.detect_select_prompt(SINGLE_PAGE))

        assert sent == [service_module.KEY_ENTER]

    def test_answered_frame_is_not_answered_twice(self, service_module):
        sent = []
        plugin = make_plugin(service_module, preferred="", sent=sent)
        frame = service_module.detect_select_prompt(SINGLE_PAGE)

        self._run(plugin, frame)
        assert plugin._answered_select == frame["message"]

        plugin._recent_lines.extend(SINGLE_PAGE)
        plugin._schedule_prompt_check()
        assert plugin._prompt_task is None


class TestGatewayListCache:
    def test_options_are_recorded_once(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        plugin._record_gateways(["gw-a (a.example.com)", "gw-a (a.example.com)"])
        assert plugin._gateway_list == ["gw-a (a.example.com)"]

    def test_separators_are_stripped_from_entries(self, service_module):
        # nmcli splits +vpn.data values on commas and ';' separates our entries
        plugin = service_module.GpclientVPNPlugin()
        plugin._record_gateways(["gw-a, extra (a.example.com)", "gw-b; x (b.example.com)"])
        assert plugin._gateway_list == [
            "gw-a extra (a.example.com)",
            "gw-b x (b.example.com)",
        ]

    def test_chosen_gateway_line_is_harvested(self, service_module):
        line = (
            "[2026-05-20T11:58:37Z INFO  gpclient::connect] Connecting to the only "
            "available gateway: gp-gw-ext-b2b (vpn.example.com)"
        )
        match = service_module.GATEWAY_CHOSEN_RE.search(line)
        assert match.group("gateway") == "gp-gw-ext-b2b (vpn.example.com)"

    def test_selected_gateway_line_is_harvested(self, service_module):
        line = (
            "[2026-05-20T11:58:37Z INFO  gpclient::connect] Connecting to the "
            "selected gateway: gw-london (gw3.example.com)"
        )
        match = service_module.GATEWAY_CHOSEN_RE.search(line)
        assert match.group("gateway") == "gw-london (gw3.example.com)"

    def test_unchanged_list_does_not_touch_the_profile(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        plugin._connection_uuid = "1234"
        plugin._gateway_list = ["gw-a (a.example.com)"]
        plugin._stored_gateway_list = "gw-a (a.example.com)"

        async def fail(*_args, **_kwargs):
            raise AssertionError("nmcli must not be called")

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fail
        try:
            asyncio.run(plugin._persist_gateway_list())
        finally:
            asyncio.create_subprocess_exec = original

    def test_nothing_is_written_without_a_uuid(self, service_module):
        plugin = service_module.GpclientVPNPlugin()
        plugin._gateway_list = ["gw-a (a.example.com)"]

        async def fail(*_args, **_kwargs):
            raise AssertionError("nmcli must not be called")

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fail
        try:
            asyncio.run(plugin._persist_gateway_list())
        finally:
            asyncio.create_subprocess_exec = original


class TestResolveBrowser:
    def _with_wrapper(self, service_module, monkeypatch, present=True):
        monkeypatch.setattr(
            service_module.os.path,
            "exists",
            lambda path: present if path == service_module.BROWSER_WRAPPER else True,
        )

    def test_empty_defaults_to_wrapped_edge(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch)
        assert service_module.resolve_browser("") == (
            service_module.BROWSER_WRAPPER,
            "edge",
        )

    def test_friendly_names_are_wrapped(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch)
        for value, target in (
            ("firefox", "firefox"),
            ("Firefox", "firefox"),
            ("chrome", "chrome"),
            ("google-chrome", "chrome"),
            ("chromium", "chromium"),
            ("default", "default"),
            ("msedge", "edge"),
        ):
            assert service_module.resolve_browser(value) == (
                service_module.BROWSER_WRAPPER,
                target,
            )

    def test_known_binaries_are_wrapped(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch)
        for path in (
            "/usr/bin/firefox",
            "/usr/bin/microsoft-edge",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
        ):
            assert service_module.resolve_browser(path) == (
                service_module.BROWSER_WRAPPER,
                path,
            )

    def test_legacy_edge_wrapper_is_left_alone(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch)
        assert service_module.resolve_browser(service_module.LEGACY_EDGE_WRAPPER) == (
            service_module.LEGACY_EDGE_WRAPPER,
            None,
        )

    def test_custom_script_is_passed_through(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch)
        assert service_module.resolve_browser("/opt/me/my-wrapper") == (
            "/opt/me/my-wrapper",
            None,
        )

    def test_without_the_wrapper_the_binary_is_used(self, service_module, monkeypatch):
        self._with_wrapper(service_module, monkeypatch, present=False)
        monkeypatch.setattr(
            service_module.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        assert service_module.resolve_browser("firefox") == ("/usr/bin/firefox", None)
        assert service_module.resolve_browser("edge") == (
            "/usr/bin/microsoft-edge",
            None,
        )
