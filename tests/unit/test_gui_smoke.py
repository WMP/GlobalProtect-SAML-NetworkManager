"""
Tests for the pure logic of the GUI smoke test of the upgrade test
(.github/scripts/gui_smoke_lib.py, used by gui_smoke_gtk.py, gui_smoke_plasma.py
and gui-smoke.sh). GTK, libnm and Qt are not needed: the probes only have to be
importable, and the widget tree they send to the checks is a plain dict.

Every positive test has negative counterparts: a missing field, a wrong text, a
zero-size widget, an ldd "not found", metadata of another service and a .name
file without a plugin are all reported.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import copy
import importlib
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(ROOT, ".github", "scripts")
sys.path.insert(0, SCRIPTS)
lib = importlib.import_module("gui_smoke_lib")
gui_smoke_gtk = importlib.import_module("gui_smoke_gtk")
gui_smoke_plasma = importlib.import_module("gui_smoke_plasma")

SERVICE = "org.freedesktop.NetworkManager.gpclient"
NAME_FILE = os.path.join(ROOT, "plugins", "gnome", "nm-gpclient-service.name")


def entry(text):
    return {"kind": "entry", "text": text}


def combo(items, text):
    return {"kind": "combo", "items": items, "text": text}


def spin(value):
    return {"kind": "spin", "value": value}


def good_tree():
    """What the GTK editor shows for lib.vpn_data(), in the shape the probe sends"""
    return {"kind": "other", "children": [
        {"kind": "label", "text": "Portal or gateway address:"},
        entry("vpn.example.com"),
        {"kind": "check", "text": "Address is a gateway (skip the portal)", "active": True,
         "children": [{"kind": "label", "text": "Address is a gateway (skip the portal)"}]},
        combo(["First proposed by portal (automatic)", "gw-a (a.example.com)", "gw-b (b.example.com)"],
              "gw-b (b.example.com)"),
        combo(["Browser (SAML, passkey, 2FA)", "Username and password (RSA token)"],
              "Username and password (RSA token)"),
        {"kind": "other", "children": [entry("test-user")]},
        {"kind": "other", "children": [spin(240)]},
    ]}


class TestNameFile:
    def test_the_installed_file_has_the_service_and_the_plugin(self):
        with open(NAME_FILE, encoding="utf-8") as handle:
            parsed = lib.parse_name_file(handle.read())

        assert parsed == {"name": "GlobalProtect", "service": SERVICE, "plugin": "libnm-vpn-plugin-gpclient.so"}

    @pytest.mark.parametrize("text", [
        "",
        "[VPN Connection]\nname=GlobalProtect\nservice=x\n",  # no [libnm] plugin
        "[VPN Connection]\nname=GlobalProtect\nservice=x\n[libnm]\nplugin=\n",
        "[VPN Connection]\nname=GlobalProtect\n[libnm]\nplugin=libx.so\n",  # no service
        "[VPN Connection]\nservice=  \n[libnm]\nplugin=libx.so\n",
        "service=x\nplugin=y\n",  # not a keyfile
    ])
    def test_missing_service_or_plugin_is_refused(self, text):
        with pytest.raises(ValueError):
            lib.parse_name_file(text)

    def test_the_gui_probes_find_the_service_of_the_repository_file(self):
        # the vpn.data must not name another service than the file does
        with open(NAME_FILE, encoding="utf-8") as handle:
            assert lib.parse_name_file(handle.read())["service"] == SERVICE


class TestVpnData:
    def test_the_data_of_the_test_connection(self):
        data = lib.vpn_data()

        assert data["gateway"] == "vpn.example.com"
        assert data["preferred-gateway"] == "gw-b (b.example.com)"
        assert data["gateway-list"] == "gw-a (a.example.com);gw-b (b.example.com)"
        assert data["gateway-list-count"] == "2"

    def test_the_caller_cannot_change_the_expectations(self):
        lib.vpn_data()["gateway"] = "other.example.org"

        assert lib.vpn_data()["gateway"] == "vpn.example.com"

    def test_the_list_holds_the_expected_gateways(self):
        assert lib.GATEWAY_LIST.split(";") == lib.GATEWAYS


class TestEditorCheck:
    def test_the_good_tree_passes(self):
        assert lib.check_editor(good_tree(), 700, 300) == []

    def test_flatten_lists_every_descendant(self):
        kinds = [n["kind"] for n in lib.flatten(good_tree())]

        assert kinds.count("entry") == 2
        assert kinds.count("label") == 2
        assert kinds.count("spin") == 1

    @pytest.mark.parametrize("size", [(0, 300), (700, 0), (0, 0), (-1, 5), (None, 5)])
    def test_zero_size_is_reported(self, size):
        problems = lib.check_editor(good_tree(), *size)

        assert any("no size" in p for p in problems)

    def test_missing_gateway_entry(self):
        tree = good_tree()
        tree["children"][1] = entry("")

        problems = lib.check_editor(tree, 700, 300)

        assert problems == [f"no entry with the gateway {lib.GATEWAY!r}"]

    def test_wrong_gateway_text(self):
        tree = good_tree()
        tree["children"][1] = entry("vpn.example.org")

        assert any("gateway 'vpn.example.com'" in p for p in lib.check_editor(tree, 700, 300))

    def test_missing_username(self):
        tree = good_tree()
        tree["children"][5] = {"kind": "other", "children": [entry("")]}

        assert any("username" in p for p in lib.check_editor(tree, 700, 300))

    @pytest.mark.parametrize("items", [
        [],
        ["First proposed by portal (automatic)"],
        ["First proposed by portal (automatic)", "gw-a (a.example.com)"],  # one gateway missing
        ["First proposed by portal (automatic)", "gw-b (b.example.com)"],
    ])
    def test_gateway_list_without_both_gateways(self, items):
        tree = good_tree()
        tree["children"][3] = combo(items, "gw-b (b.example.com)")

        assert any("offers the gateways" in p for p in lib.check_editor(tree, 700, 300))

    def test_wrong_preferred_gateway(self):
        tree = good_tree()
        tree["children"][3]["text"] = "First proposed by portal (automatic)"

        problems = lib.check_editor(tree, 700, 300)

        assert len(problems) == 1 and "preferred gateway" in problems[0]

    def test_the_gateway_entry_alone_does_not_stand_in_for_the_combo(self):
        tree = good_tree()
        del tree["children"][3]

        assert any("offers the gateways" in p for p in lib.check_editor(tree, 700, 300))

    def test_wrong_authentication(self):
        tree = good_tree()
        tree["children"][4]["text"] = "Browser (SAML, passkey, 2FA)"

        problems = lib.check_editor(tree, 700, 300)

        assert len(problems) == 1 and "authentication" in problems[0]

    def test_missing_authentication_combo(self):
        tree = good_tree()
        del tree["children"][4]

        assert any("authentication modes" in p for p in lib.check_editor(tree, 700, 300))

    def test_check_button_not_ticked(self):
        tree = good_tree()
        tree["children"][2]["active"] = False

        assert any("not ticked" in p for p in lib.check_editor(tree, 700, 300))

    def test_check_button_missing(self):
        tree = good_tree()
        del tree["children"][2]

        assert any("no check button" in p for p in lib.check_editor(tree, 700, 300))

    def test_empty_tree(self):
        problems = lib.check_editor({"kind": "other"}, 700, 300)

        assert len(problems) == 6

    def test_missing_spin_button(self):
        tree = good_tree()
        del tree["children"][6]

        assert lib.check_editor(tree, 700, 300) == ["no spin button for the connect timeout"]

    @pytest.mark.parametrize("value", [0, 180, 239, 241, None])
    def test_wrong_connect_timeout(self, value):
        tree = good_tree()
        tree["children"][6]["children"][0] = spin(value)

        problems = lib.check_editor(tree, 700, 300)

        assert len(problems) == 1 and "connect timeout is not 240" in problems[0]

    def test_a_spin_button_is_not_an_entry(self):
        tree = good_tree()
        tree["children"][6]["children"][0] = entry("240")

        assert any("spin button" in p for p in lib.check_editor(tree, 700, 300))

    def test_all_problems_are_listed_together(self):
        tree = good_tree()
        tree["children"][1] = entry("x")
        tree["children"][4]["text"] = "y"

        assert len(lib.check_editor(tree, 0, 0)) == 3

    def test_the_input_is_not_changed(self):
        tree = good_tree()
        before = copy.deepcopy(tree)
        lib.check_editor(tree, 700, 300)

        assert tree == before


def good_results():
    return {case["name"]: (case["shown"], case["saved"]) for case in lib.TIMEOUT_CASES}


class TestTimeoutCases:
    def test_the_expected_results_pass(self):
        assert lib.check_timeout_results(good_results()) == []

    def test_the_constants(self):
        assert lib.TIMEOUT == 240
        assert lib.SAML_TIMEOUT == 180

    def test_the_timeout_of_the_test_connection_is_neither_zero_nor_the_default(self):
        assert lib.TIMEOUT not in (0, lib.SAML_TIMEOUT)

    def test_case_names_are_unique_and_steps_are_known(self):
        names = [case["name"] for case in lib.TIMEOUT_CASES]

        assert len(names) == len(set(names))
        for case in lib.TIMEOUT_CASES:
            assert all(kind in ("auth", "spin") for kind, _ in case["steps"])

    def test_a_new_profile_follows_the_authentication_mode_until_the_user_sets_a_value(self):
        expected = {case["name"]: (case["shown"], case["saved"]) for case in lib.TIMEOUT_CASES}

        assert expected["new profile"] == (180, 180)
        assert expected["new profile, credentials"] == (0, 0)
        assert expected["new profile, credentials, browser"] == (180, 180)
        assert expected["new profile, own value, credentials"] == (300, 300)

    def test_an_existing_profile_is_not_changed(self):
        expected = {case["name"]: (case["shown"], case["saved"]) for case in lib.TIMEOUT_CASES}

        assert expected["existing SAML profile without timeout"] == (0, 0)
        assert expected["existing profile with timeout, browser"] == (240, 240)

    @pytest.mark.parametrize("index", range(6))
    @pytest.mark.parametrize("wrong", [-1, 1, 60, 181, None])
    def test_a_wrong_shown_value_is_reported(self, index, wrong):
        case = lib.TIMEOUT_CASES[index]
        results = good_results()
        results[case["name"]] = (wrong, case["saved"])

        problems = lib.check_timeout_results(results)

        assert len(problems) == 1 and case["name"] in problems[0] and "shown" in problems[0]

    @pytest.mark.parametrize("index", range(6))
    @pytest.mark.parametrize("wrong", [-1, 1, 60, 181, None])
    def test_a_wrong_saved_value_is_reported(self, index, wrong):
        case = lib.TIMEOUT_CASES[index]
        results = good_results()
        results[case["name"]] = (case["shown"], wrong)

        problems = lib.check_timeout_results(results)

        assert len(problems) == 1 and case["name"] in problems[0] and "saved" in problems[0]

    @pytest.mark.parametrize("index", range(6))
    def test_a_missing_case_is_reported(self, index):
        case = lib.TIMEOUT_CASES[index]
        results = good_results()
        del results[case["name"]]

        problems = lib.check_timeout_results(results)

        assert problems == [f"connect timeout case {case['name']!r} did not run"]

    def test_no_results_report_every_case(self):
        assert len(lib.check_timeout_results({})) == len(lib.TIMEOUT_CASES)

    def test_the_input_is_not_changed(self):
        results = good_results()
        before = copy.deepcopy(results)
        lib.check_timeout_results(results)

        assert results == before


def good_mtu_results():
    return {case["name"]: (case["shown"], case["saved"]) for case in lib.MTU_CASES}


class TestMtuCases:
    def test_the_expected_results_pass(self):
        assert lib.check_mtu_results(good_mtu_results()) == []

    def test_the_limits_are_the_ones_of_the_editors(self):
        assert (lib.MTU_MIN, lib.MTU_MAX) == (576, 1500)

    def test_case_names_are_unique(self):
        names = [case["name"] for case in lib.MTU_CASES]

        assert len(names) == len(set(names))

    def test_a_value_below_the_minimum_is_saved_as_the_minimum_and_zero_removes_the_key(self):
        expected = good_mtu_results()

        assert expected["mtu: 400 is saved as 576"] == (400, "576")
        assert expected["mtu: 0 removes the key"] == (0, None)
        assert expected["mtu: 1400 is saved"] == (1400, "1400")

    def test_a_stored_value_the_editor_cannot_show_is_kept_until_the_user_changes_the_field(self):
        expected = good_mtu_results()

        assert expected["mtu: stored 1600 is kept when untouched"] == (0, "1600")
        assert expected["mtu: stored abc is kept when untouched"] == (0, "abc")
        assert expected["mtu: stored 1600, 0 set: the key stays"] == (0, "1600")
        assert expected["mtu: stored 1600 replaced by 1300"] == (1300, "1300")
        assert expected["mtu: stored abc replaced by 400"] == (400, "576")
        # a value that is shown, or only zeros, is not kept as text
        assert expected["mtu: stored 00 is automatic"] == (0, None)
        assert expected["mtu: 0 removes the key"] == (0, None)
        # touched: the user set 1300 and then 0
        assert expected["mtu: stored 1600, 1300 then 0: the key is removed"] == (0, None)

    @pytest.mark.parametrize("index", range(len(lib.MTU_CASES)))
    def test_a_wrong_shown_or_saved_value_is_reported(self, index):
        case = lib.MTU_CASES[index]
        for position, wrong in ((0, case["shown"] + 1), (1, "9999")):
            results = good_mtu_results()
            results[case["name"]] = tuple(wrong if i == position else v
                                          for i, v in enumerate(results[case["name"]]))

            problems = lib.check_mtu_results(results)

            assert len(problems) == 1 and case["name"] in problems[0]

    def test_a_case_that_did_not_run_is_reported(self):
        results = good_mtu_results()
        case = lib.MTU_CASES[0]
        del results[case["name"]]

        assert lib.check_mtu_results(results) == [f"MTU case {case['name']!r} did not run"]

    def test_the_limits_are_the_ones_of_the_gtk_header(self):
        with open(os.path.join(ROOT, "plugins", "gnome", "nm-gpclient-mtu.h"), encoding="utf-8") as handle:
            text = handle.read()

        assert f"#define MTU_MIN {lib.MTU_MIN}\n#define MTU_MAX {lib.MTU_MAX}\n" in text

    @pytest.mark.parametrize("source", ["nm-gpclient-editor.c", "nm-gpclient-editor-gtk3.c"])
    def test_the_gtk_editors_use_the_header_and_name_the_spin_button(self, source):
        with open(os.path.join(ROOT, "plugins", "gnome", source), encoding="utf-8") as handle:
            text = handle.read()

        assert '#include "nm-gpclient-mtu.h"' in text
        assert f'"{lib.MTU_SPIN_NAME}"' in text

    def test_plasma_uses_the_header_and_its_spin_box_has_the_same_maximum(self):
        plasma = os.path.join(ROOT, "plugins", "plasma")
        with open(os.path.join(plasma, "gpclientwidget.cpp"), encoding="utf-8") as handle:
            cpp = handle.read()
        with open(os.path.join(plasma, "gpclientwidget.ui"), encoding="utf-8") as handle:
            ui = handle.read()
        with open(os.path.join(plasma, "CMakeLists.txt"), encoding="utf-8") as handle:
            cmake = handle.read()

        assert '#include "nm-gpclient-mtu.h"' in cpp
        assert "nm_gpclient_mtu_parse(" in cpp
        assert "${CMAKE_CURRENT_SOURCE_DIR}/../gnome" in cmake
        # no second copy of the rule in the editor
        for own in ("parseMtu", "isMtuSpace", "isAutomaticMtuText", "mtuMin", "mtuMax"):
            assert own not in cpp
        box = ui[ui.index('name="mtuSpinBox"'):]
        maximum = box[box.index('<property name="maximum">'):]
        assert f"<number>{lib.MTU_MAX}</number>" in maximum[:maximum.index("</property>")]

    def test_the_gtk_editors_keep_an_invalid_stored_value_by_a_touched_flag(self):
        for source in ("nm-gpclient-editor.c", "nm-gpclient-editor-gtk3.c"):
            with open(os.path.join(ROOT, "plugins", "gnome", source), encoding="utf-8") as handle:
                text = handle.read()

            assert "mtu_touched" in text and "mtu_shown" not in text


class TestEditorTextsMatchTheSources:
    """The expected texts are the ones the C sources put in the widgets"""

    @pytest.mark.parametrize("source", ["nm-gpclient-editor.c", "nm-gpclient-editor-gtk3.c"])
    def test_texts_exist_in_the_editor(self, source):
        with open(os.path.join(ROOT, "plugins", "gnome", source), encoding="utf-8") as handle:
            text = handle.read()

        for expected in lib.AUTH_ITEMS + [lib.AS_GATEWAY_LABEL]:
            assert f'"{expected}"' in text

    @pytest.mark.parametrize("source", ["nm-gpclient-editor.c", "nm-gpclient-editor-gtk3.c"])
    def test_vpn_data_keys_are_read_by_the_editor(self, source):
        with open(os.path.join(ROOT, "plugins", "gnome", source), encoding="utf-8") as handle:
            text = handle.read()

        for key in ("gateway", "as-gateway", "preferred-gateway", "gateway-list", "username", "auth-mode"):
            assert f'"{key}"' in text
            assert key in lib.vpn_data()

    @pytest.mark.parametrize("source, definition", [
        ("gnome/nm-gpclient-editor.c", "#define SAML_TIMEOUT_DEFAULT {}\n"),
        ("gnome/nm-gpclient-editor-gtk3.c", "#define SAML_TIMEOUT_DEFAULT {}\n"),
        # The Plasma editor runs in no smoke test: this is its only check
        ("plasma/gpclientwidget.cpp", "static const int samlTimeoutDefault = {};\n"),
    ])
    def test_the_saml_timeout_is_the_one_of_the_editor(self, source, definition):
        with open(os.path.join(ROOT, "plugins", source), encoding="utf-8") as handle:
            text = handle.read()

        assert definition.format(lib.SAML_TIMEOUT) in text


LDD_GOOD = """\
\tlinux-vdso.so.1 (0x00007ffd)
\tlibQt5Core.so.5 => /lib/x86_64-linux-gnu/libQt5Core.so.5 (0x00007f00)
\t/lib64/ld-linux-x86-64.so.2 (0x00007f01)
"""
LDD_BAD = LDD_GOOD + "\tlibKF5NetworkManagerQt.so.7 => not found\n\tlibfoo.so.1 => not found\n"


class TestLdd:
    def test_all_libraries_found(self):
        assert lib.parse_ldd(LDD_GOOD) == []
        assert lib.ldd_problems(0, LDD_GOOD) == []

    def test_not_found_is_listed(self):
        assert lib.parse_ldd(LDD_BAD) == ["libKF5NetworkManagerQt.so.7", "libfoo.so.1"]
        assert lib.ldd_problems(0, LDD_BAD) == [
            "library not found: libKF5NetworkManagerQt.so.7", "library not found: libfoo.so.1"]

    def test_a_failing_ldd_is_a_problem(self):
        problems = lib.ldd_problems(1, "ldd: ./x.so: No such file or directory")

        assert len(problems) == 1 and "status 1" in problems[0]

    def test_a_library_called_not_found_is_not_missing(self):
        assert lib.parse_ldd("\tlibnot.so => /usr/lib/not found.so (0x1)\n") == []


def plasma_meta(**changes):
    data = {
        "KPlugin": {"Id": "plasmanetworkmanagement_gpclientui", "ServiceTypes": ["PlasmaNetworkManagement/VpnUiPlugin"]},
        "X-NetworkManager-Services": SERVICE,
    }
    data.update(changes)
    return data


class TestPlasmaMetadata:
    def test_the_json_of_the_repository_passes(self):
        import json

        with open(os.path.join(ROOT, "plugins", "plasma", "plasmanetworkmanagement_gpclientui.json"), encoding="utf-8") as handle:
            data = json.load(handle)

        assert lib.check_plasma_metadata(data, SERVICE) == []

    def test_what_qpluginloader_returns_passes(self):
        loader = {"IID": "org.kde.plasma.networkmanagement.VpnUiPlugin", "className": "GpclientUiPlugin",
                  "MetaData": plasma_meta()}

        assert lib.check_plasma_metadata(loader, SERVICE) == []

    @pytest.mark.parametrize("meta", [
        {},
        None,
        "text",
        {"IID": "x", "className": "y"},  # a plugin without embedded JSON
        {"IID": "x", "MetaData": {}},
    ])
    def test_no_metadata(self, meta):
        assert lib.check_plasma_metadata(meta, SERVICE) == ["the plugin has no metadata"]

    def test_metadata_of_another_service(self):
        problems = lib.check_plasma_metadata(plasma_meta(**{"X-NetworkManager-Services": "org.example.other"}), SERVICE)

        assert len(problems) == 1 and "X-NetworkManager-Services" in problems[0]

    def test_a_service_list_may_name_ours(self):
        meta = plasma_meta(**{"X-NetworkManager-Services": f"org.example.other, {SERVICE}"})

        assert lib.check_plasma_metadata(meta, SERVICE) == []

    def test_a_service_that_only_contains_ours_is_not_ours(self):
        meta = plasma_meta(**{"X-NetworkManager-Services": SERVICE + "x"})

        assert lib.check_plasma_metadata(meta, SERVICE)

    def test_no_service_key(self):
        meta = plasma_meta()
        del meta["X-NetworkManager-Services"]

        assert any("X-NetworkManager-Services" in p for p in lib.check_plasma_metadata(meta, SERVICE))

    def test_wrong_plugin_id(self):
        meta = plasma_meta(KPlugin={"Id": "other", "ServiceTypes": ["PlasmaNetworkManagement/VpnUiPlugin"]})

        assert any("KPlugin.Id" in p for p in lib.check_plasma_metadata(meta, SERVICE))

    def test_wrong_service_type(self):
        meta = plasma_meta(KPlugin={"Id": "plasmanetworkmanagement_gpclientui", "ServiceTypes": ["Other/Type"]})

        assert any("ServiceTypes" in p for p in lib.check_plasma_metadata(meta, SERVICE))

    def test_kplugin_is_not_a_dict(self):
        assert len(lib.check_plasma_metadata(plasma_meta(KPlugin="x"), SERVICE)) == 2


class TestQtMajor:
    @pytest.mark.parametrize("path, major", [
        ("/usr/lib/x86_64-linux-gnu/qt5/plugins/plasma/network/vpn/p.so", 5),
        ("/usr/lib/aarch64-linux-gnu/qt6/plugins/plasma/network/vpn/p.so", 6),
    ])
    def test_known(self, path, major):
        assert lib.qt_major(path) == major

    @pytest.mark.parametrize("path", ["/usr/lib/p.so", "/usr/lib/qt4/p.so", "/usr/lib/qt55/p.so", ""])
    def test_unknown(self, path):
        assert lib.qt_major(path) is None


class TestScreenshot:
    def test_first_existing_tool_wins(self):
        found = {"import-im6.q16": "/usr/bin/import-im6.q16", "import-im7.q16": "/usr/bin/import-im7.q16"}

        assert lib.find_import_tool(found.get) == "/usr/bin/import-im7.q16"
        assert lib.find_import_tool({"import": "/usr/bin/import"}.get) == "/usr/bin/import"

    def test_no_tool(self):
        assert lib.find_import_tool(lambda name: None) is None

    def test_command(self):
        assert lib.screenshot_command("/usr/bin/import", ":99", "/out/a.png") == [
            "/usr/bin/import", "-display", ":99", "-window", "root", "/out/a.png"]

    @pytest.mark.parametrize("out", ["/out/a.jpg", "/out/a", ""])
    def test_only_png(self, out):
        with pytest.raises(ValueError):
            lib.screenshot_command("import", ":99", out)


class TestProbes:
    def run(self, module, argv, capsys):
        with pytest.raises(SystemExit) as stop:
            module.main(argv)
        return stop.value.code, capsys.readouterr().err

    def test_gtk_probe_needs_a_valid_name_file(self, tmp_path, capsys):
        bad = tmp_path / "x.name"
        bad.write_text("[VPN Connection]\nservice=x\n")

        code, err = self.run(gui_smoke_gtk, ["--gtk", "3", "--name-file", str(bad), "--screenshot", "/x.png"], capsys)

        assert code == 1 and "no plugin" in err

    def test_gtk_probe_refuses_a_missing_name_file(self, tmp_path, capsys):
        code, err = self.run(gui_smoke_gtk, ["--gtk", "4", "--name-file", str(tmp_path / "none"), "--screenshot", "/x.png"], capsys)

        assert code == 1 and "FAIL:" in err

    def test_gtk_probe_refuses_another_toolkit(self, capsys):
        code, _ = self.run(gui_smoke_gtk, ["--gtk", "2", "--name-file", NAME_FILE, "--screenshot", "/x.png"], capsys)

        assert code == 2

    def test_plasma_probe_refuses_a_missing_plugin(self, tmp_path, capsys):
        code, err = self.run(gui_smoke_plasma, ["--plugin", str(tmp_path / "none.so"), "--name-file", NAME_FILE], capsys)

        assert code == 1 and "does not exist" in err

    def test_plasma_probe_refuses_a_bad_name_file(self, tmp_path, capsys):
        bad = tmp_path / "x.name"
        bad.write_text("")

        code, err = self.run(gui_smoke_plasma, ["--plugin", str(tmp_path / "p.so"), "--name-file", str(bad)], capsys)

        assert code == 1 and "no service" in err


class TestQtLoad:
    """load_with_qt with a fake PyQt: PyQt5 has no QPluginLoader.metaData()
    (CI on Ubuntu 22.04), PyQt6 has; the plugin is loaded either way."""

    def fake_pyqt(self, monkeypatch, major, with_metadata, loads=True):
        class Loader:
            def __init__(self, path):
                self.path = path

            def load(self):
                return loads

            def errorString(self):
                return "cannot open shared object"

        if with_metadata:
            Loader.metaData = lambda self: {"MetaData": {"from": "qt"}}
        core = type(sys)("PyQt%d.QtCore" % major)
        core.QPluginLoader = Loader
        monkeypatch.setitem(sys.modules, "PyQt%d" % major, type(sys)("PyQt%d" % major))
        monkeypatch.setitem(sys.modules, "PyQt%d.QtCore" % major, core)

    def test_metadata_comes_from_qt_when_it_has_it(self, monkeypatch, capsys):
        self.fake_pyqt(monkeypatch, 6, with_metadata=True)

        assert gui_smoke_plasma.load_with_qt("/p.so", 6) == {"MetaData": {"from": "qt"}}
        assert "OK: QPluginLoader loads /p.so" in capsys.readouterr().out

    def test_pyqt5_without_metadata_still_loads_the_plugin(self, monkeypatch, capsys):
        self.fake_pyqt(monkeypatch, 5, with_metadata=False)

        assert gui_smoke_plasma.load_with_qt("/p.so", 5) is None
        assert "OK: QPluginLoader loads /p.so" in capsys.readouterr().out

    @pytest.mark.parametrize("with_metadata", [True, False])
    def test_a_plugin_that_does_not_load_fails(self, monkeypatch, capsys, with_metadata):
        self.fake_pyqt(monkeypatch, 5, with_metadata, loads=False)

        with pytest.raises(SystemExit) as stop:
            gui_smoke_plasma.load_with_qt("/p.so", 5)

        assert stop.value.code == 1
        assert "cannot open shared object" in capsys.readouterr().err

    @pytest.mark.parametrize("major", [5, 6])
    def test_missing_pyqt_fails(self, monkeypatch, capsys, major):
        monkeypatch.setitem(sys.modules, "PyQt%d.QtCore" % major, None)

        with pytest.raises(SystemExit) as stop:
            gui_smoke_plasma.load_with_qt("/p.so", major)

        assert stop.value.code == 1
        assert "PyQt%d is not available" % major in capsys.readouterr().err

    def plugin_dir(self, tmp_path, with_json):
        plugin = tmp_path / "usr" / "lib" / "x" / "qt5" / "plugins" / "plasma" / "network" / "vpn" / "p.so"
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b"")
        name = tmp_path / "x.name"
        name.write_text("[VPN Connection]\nname=gpclient\nservice=org.example.gp\n[libnm]\nplugin=/x/libnm.so\n")
        if with_json:
            plugin.with_suffix(".json").write_text('{"KPlugin": {"Id": "x"}, "X-NetworkManager-Services": "org.example.gp"}')
        return ["--plugin", str(plugin), "--name-file", str(name)]

    def test_main_without_pyqt_fails_even_with_a_good_json_file(self, tmp_path, monkeypatch, capsys):
        # the JSON file is not a substitute for loading the plugin
        argv = self.plugin_dir(tmp_path, with_json=True)
        monkeypatch.setattr(gui_smoke_plasma.subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
        monkeypatch.setitem(sys.modules, "PyQt5.QtCore", None)

        with pytest.raises(SystemExit) as stop:
            gui_smoke_plasma.main(argv)

        assert stop.value.code == 1
        out = capsys.readouterr()
        assert "PyQt5 is not available" in out.err
        assert "WARN" not in out.out and "JSON" not in out.out

    def test_main_with_pyqt5_checks_the_json_file(self, tmp_path, monkeypatch, capsys):
        TestQtLoad().fake_pyqt(monkeypatch, 5, with_metadata=False)
        argv = self.plugin_dir(tmp_path, with_json=False)
        monkeypatch.setattr(gui_smoke_plasma.subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

        with pytest.raises(SystemExit) as stop:
            gui_smoke_plasma.main(argv)

        # PyQt5 loaded the plugin; its metadata come from the JSON file, which is missing here
        assert stop.value.code == 1
        assert "p.json" in capsys.readouterr().err

    def test_metadata_falls_back_to_the_json_file(self, tmp_path):
        (tmp_path / "p.json").write_text('{"KPlugin": {"Id": "x"}}')

        assert gui_smoke_plasma.read_json_metadata(str(tmp_path / "p.so")) == {"KPlugin": {"Id": "x"}}

    def test_a_missing_json_file_fails(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            gui_smoke_plasma.read_json_metadata(str(tmp_path / "p.so"))

        assert "p.json" in capsys.readouterr().err


class TestGuiSmokeScriptPyqt:
    def script(self):
        with open(os.path.join(SCRIPTS, "gui-smoke.sh"), encoding="utf-8") as handle:
            return handle.read()

    def test_a_failed_pyqt_install_fails_the_smoke_test(self):
        text = self.script()

        assert '--no-install-recommends "$PYQT" > /dev/null \\\n        || fail "$PYQT cannot be installed' in text

    def test_the_plugin_path_comes_from_check_upgrade_not_from_a_second_pattern(self):
        text = self.script()

        assert 'check_upgrade.py" --print-plugin' in text
        # only the failure message names the file
        assert "grep" not in text.split("--print-plugin")[1].split("fail ")[0]
        assert text.count("plasmanetworkmanagement_gpclientui") == 1 and 'fail "$PLASMA installs no plasmanetworkmanagement_gpclientui.so"' in text

    def test_no_warning_instead_of_a_failure(self):
        assert "WARN" not in self.script()


class TestGtkVersionPins:
    """Gdk must be pinned with Gtk: unpinned, gi loaded Gdk 4 next to Gtk 3
    (CI on Ubuntu 22.04: "Requiring namespace 'Gdk' version '3.0', but '4.0'
    is already loaded")."""

    def pins(self):
        with open(os.path.join(SCRIPTS, "gui_smoke_gtk.py"), encoding="utf-8") as handle:
            source = handle.read()
        imported = source.index("from gi.repository import")
        return source[:imported]

    @pytest.mark.parametrize("namespace", ["Gtk", "Gdk", "NM"])
    def test_every_imported_namespace_is_pinned_before_the_import(self, namespace):
        assert 'gi.require_version("%s", ' % namespace in self.pins()

    @pytest.mark.parametrize("namespace", ["Gtk", "Gdk"])
    def test_gtk_and_gdk_follow_the_requested_major(self, namespace):
        assert 'gi.require_version("%s", f"{major}.0")' % namespace in self.pins()


class TestProbeNodes:
    """to_node maps a SpinButton before an Entry: a GTK3 SpinButton is an Entry"""

    class Gtk:
        class Container:
            pass

        class Widget:
            pass

        class Entry(Widget):
            pass

        class SpinButton(Entry):
            pass

        class ComboBoxText(Widget):
            pass

        class CheckButton(Widget):
            pass

        class Label(Widget):
            pass

    def test_a_spin_button_becomes_a_spin_node(self):
        spin_button = type("S", (self.Gtk.SpinButton,), {"get_value_as_int": lambda self: 240, "get_text": lambda self: "240"})()

        assert gui_smoke_gtk.to_node(spin_button, self.Gtk, 3) == {"kind": "spin", "value": 240, "children": []}

    def test_an_entry_stays_an_entry(self):
        entry_widget = type("E", (self.Gtk.Entry,), {"get_text": lambda self: "x"})()

        assert gui_smoke_gtk.to_node(entry_widget, self.Gtk, 3)["kind"] == "entry"


class TestTooltips:
    """The probe switches tooltips off in the whole editor before the screenshot:
    Xvfb's pointer sits over the editor and a tooltip covered the DNS and HIP
    fields in the first CI screenshots."""

    class Widget:
        def __init__(self, *kids):
            self.kids = list(kids)
            self.tooltip = True

        def set_has_tooltip(self, value):
            self.tooltip = value

        def get_children(self):
            return self.kids

    class Gtk:
        class Container:
            pass

    def tree(self):
        Widget = type("W", (self.Widget, self.Gtk.Container), {})
        leaf = Widget()
        deep = Widget()
        return Widget(leaf, Widget(deep)), [leaf, deep]

    def test_every_widget_loses_its_tooltip(self):
        root, leaves = self.tree()

        gui_smoke_gtk.without_tooltips(root, self.Gtk, 3)

        assert root.tooltip is False
        assert all(leaf.tooltip is False for leaf in leaves)

    def test_a_widget_outside_the_editor_keeps_its_tooltip(self):
        root, _ = self.tree()
        outside = self.Widget()

        gui_smoke_gtk.without_tooltips(root, self.Gtk, 3)

        assert outside.tooltip is True

    def test_tooltips_go_before_the_window_is_shown(self):
        with open(os.path.join(SCRIPTS, "gui_smoke_gtk.py"), encoding="utf-8") as handle:
            source = handle.read()
        main = source[source.index("def main("):]

        assert main.index("without_tooltips(widget") < main.index("window = Gtk.Window()")
