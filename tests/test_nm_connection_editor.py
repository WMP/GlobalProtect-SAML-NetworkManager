"""
Test: nm-connection-editor VPN dialog flow.

ETAP 1 - Stabilny test używający nm-connection-editor.
Ten test działa na wszystkich środowiskach desktopowych (GNOME, KDE, MATE, etc.)
"""

import pytest

# Dogtail imports - will fail gracefully if not installed
try:
    from dogtail.predicate import GenericPredicate
    from dogtail.tree import root

    DOGTAIL_AVAILABLE = True
except ImportError:
    DOGTAIL_AVAILABLE = False
    root = None

from helpers.dogtail_utils import find_element_safe, wait_for_element
from helpers.screenshot import take_screenshot_on_failure

pytestmark = pytest.mark.skipif(
    not DOGTAIL_AVAILABLE, reason="Dogtail not installed. Run: pip3 install dogtail"
)


class TestNMConnectionEditor:
    """Tests for nm-connection-editor VPN workflow."""

    def test_editor_opens(self, nm_connection_editor):
        """Verify nm-connection-editor window appears."""
        try:
            app = wait_for_element(
                root, roleName="frame", name="Network Connections", timeout=10
            )
            assert app is not None, "nm-connection-editor window not found"
            assert app.showing, "Window is not visible"

        except Exception as e:
            take_screenshot_on_failure("test_editor_opens")
            raise

    def test_add_button_exists(self, nm_connection_editor):
        """Verify Add button is present and clickable."""
        try:
            app = wait_for_element(
                root, roleName="frame", name="Network Connections", timeout=10
            )

            # The Add button might have different labels depending on theme
            # Try common variants
            add_btn = find_element_safe(app, roleName="push button", name="Add")
            if not add_btn:
                add_btn = find_element_safe(
                    app, roleName="push button", name_contains="Add"
                )
            if not add_btn:
                # Some themes use icon-only button
                add_btn = find_element_safe(app, roleName="push button", name="+")

            assert add_btn is not None, "Add button not found"
            assert add_btn.sensitive, "Add button is not sensitive (disabled)"

        except Exception as e:
            take_screenshot_on_failure("test_add_button_exists")
            raise

    def test_open_add_connection_dialog(self, nm_connection_editor):
        """
        Click Add button and verify connection type dialog appears.
        """
        try:
            app = wait_for_element(
                root, roleName="frame", name="Network Connections", timeout=10
            )

            # Find and click Add button
            add_btn = find_element_safe(app, roleName="push button", name="Add")
            if not add_btn:
                add_btn = find_element_safe(
                    app, roleName="push button", name_contains="Add"
                )

            assert add_btn is not None, "Add button not found"
            add_btn.click()

            # Wait for dialog to appear
            # Dialog name varies: "Choose a Connection Type", "New Connection", etc.
            dialog = wait_for_element(root, roleName="dialog", timeout=5)

            assert dialog is not None, "Add connection dialog did not appear"

        except Exception as e:
            take_screenshot_on_failure("test_open_add_connection_dialog")
            raise

    def test_vpn_option_available(self, nm_connection_editor):
        """
        Open Add dialog and verify VPN/GlobalProtect option exists in combo box.
        """
        import time
        try:
            app = wait_for_element(
                root, roleName="frame", name="Network Connections", timeout=10
            )

            # Click Add
            add_btn = find_element_safe(app, roleName="push button", name="Add")
            if not add_btn:
                add_btn = find_element_safe(
                    app, roleName="push button", name_contains="Add"
                )
            add_btn.click()

            # Wait for dialog
            dialog = wait_for_element(root, roleName="dialog", timeout=5)

            # Find combo box with connection types
            combo = find_element_safe(dialog, roleName="combo box")
            assert combo is not None, "Connection type combo box not found"

            # Click combo box to open menu
            combo.click()
            time.sleep(0.2)

            # Navigate down in menu using keyboard to reach VPN section
            from dogtail.rawinput import pressKey
            menu = find_element_safe(dialog, roleName="menu")
            if menu:
                for _ in range(16):  # Navigate to VPN section
                    pressKey("Down")
                    time.sleep(0.02)
                time.sleep(0.1)

            # Look for VPN category or GlobalProtect in the menu
            vpn_item = find_element_safe(dialog, roleName="menu item", name="VPN")
            gp_item = find_element_safe(dialog, roleName="menu item", name_contains="GlobalProtect")

            assert vpn_item is not None or gp_item is not None, \
                "VPN option not found in connection type menu"

            if gp_item:
                print("GlobalProtect VPN plugin is available in the menu!")

        except Exception as e:
            take_screenshot_on_failure("test_vpn_option_available")
            raise

    def test_select_vpn_and_create(self, nm_connection_editor):
        """
        Full flow: Add -> Select GlobalProtect VPN -> Create -> Verify VPN editor dialog.
        """
        import time
        try:
            app = wait_for_element(
                root, roleName="frame", name="Network Connections", timeout=10
            )

            # Click Add
            add_btn = find_element_safe(app, roleName="push button", name="Add")
            if not add_btn:
                add_btn = find_element_safe(
                    app, roleName="push button", name_contains="Add"
                )
            add_btn.click()

            # Wait for dialog
            dialog = wait_for_element(root, roleName="dialog", timeout=5)

            # Find combo box with connection types
            combo = find_element_safe(dialog, roleName="combo box")
            assert combo is not None, "Connection type combo box not found"

            # Click combo box to open menu
            combo.click()
            time.sleep(0.2)

            # Navigate down in menu using keyboard to reach VPN/GlobalProtect section
            from dogtail.rawinput import pressKey
            menu = find_element_safe(dialog, roleName="menu")
            if menu:
                # Press Down arrow multiple times to reach GlobalProtect (it's near the bottom)
                for _ in range(18):  # Navigate past Hardware, Virtual sections to VPN
                    pressKey("Down")
                    time.sleep(0.02)
                time.sleep(0.1)

            # Find GlobalProtect in the menu (note: has leading spaces)
            gp_item = find_element_safe(dialog, roleName="menu item", name="    GlobalProtect")
            if not gp_item:
                # Try without leading spaces
                gp_item = find_element_safe(dialog, roleName="menu item", name_contains="GlobalProtect")

            assert gp_item is not None, "GlobalProtect VPN option not found in menu"
            print(f"Found GlobalProtect: [{gp_item.roleName}] '{gp_item.name}'")
            gp_item.click()
            time.sleep(0.2)

            # Click Create button
            create_btn = find_element_safe(
                dialog, roleName="push button", name="Create…"
            )
            if not create_btn:
                create_btn = find_element_safe(
                    dialog, roleName="push button", name_contains="Create"
                )

            assert create_btn is not None, "Create button not found"
            create_btn.click()
            time.sleep(1)  # Wait for dialog to appear

            # Debug: list all visible dialogs
            from dogtail.predicate import GenericPredicate
            print("Looking for dialogs...")
            for node in root.findChildren(GenericPredicate(roleName="dialog"), recursive=True):
                print(f"  Found dialog: '{node.name}' showing={node.showing}")
            for node in root.findChildren(GenericPredicate(roleName="frame"), recursive=True):
                print(f"  Found frame: '{node.name}' showing={node.showing}")

            # Verify VPN editor dialog appears (editing the new VPN connection)
            # Try to find by frame first (some GTK dialogs are frames)
            vpn_dialog = find_element_safe(root, roleName="frame", name_contains="Editing")
            if not vpn_dialog:
                vpn_dialog = find_element_safe(root, roleName="dialog", name_contains="Editing")
            if not vpn_dialog:
                # Last resort - find any dialog/frame with VPN in name
                vpn_dialog = find_element_safe(root, roleName="frame", name_contains="VPN")
            if not vpn_dialog:
                vpn_dialog = find_element_safe(root, roleName="dialog", name_contains="VPN")
            assert vpn_dialog is not None, "VPN editor dialog did not appear (plugin load failed?)"

            # Check that this is a VPN editing dialog with VPN-specific fields
            # Look for Gateway label which is our plugin's field
            gateway_label = find_element_safe(vpn_dialog, name="Gateway:")
            vpn_tab = find_element_safe(vpn_dialog, roleName="page tab", name="VPN")

            assert gateway_label or vpn_tab, "VPN editor dialog doesn't have expected VPN fields"
            print("GlobalProtect VPN connection editor opened successfully!")

        except Exception as e:
            take_screenshot_on_failure("test_select_vpn_and_create")
            raise
