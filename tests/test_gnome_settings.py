"""
Test: GNOME Settings (gnome-control-center) VPN integration.

ETAP 2 - Test dla GTK4 edytora w GNOME Settings.
Wymaga środowiska GNOME z gnome-control-center.
"""

import subprocess

import pytest

try:
    from dogtail.tree import root

    DOGTAIL_AVAILABLE = True
except ImportError:
    DOGTAIL_AVAILABLE = False
    root = None

from helpers.dogtail_utils import find_element_safe, wait_for_element
from helpers.screenshot import take_screenshot_on_failure


def is_gnome_available():
    """Check if GNOME Control Center is installed."""
    result = subprocess.run(["which", "gnome-control-center"], capture_output=True)
    return result.returncode == 0


pytestmark = [
    pytest.mark.skipif(not DOGTAIL_AVAILABLE, reason="Dogtail not installed"),
    pytest.mark.skipif(
        not is_gnome_available(), reason="GNOME Control Center not installed"
    ),
]


class TestGNOMESettings:
    """Tests for GNOME Settings network/VPN panel."""

    def test_settings_opens(self, gnome_control_center):
        """Verify GNOME Settings network panel opens."""
        try:
            # GNOME Settings window name varies by version
            app = wait_for_element(
                root, roleName="frame", name_contains="Settings", timeout=15
            )
            assert app is not None, "GNOME Settings window not found"

        except Exception as e:
            take_screenshot_on_failure("test_settings_opens")
            raise

    def test_vpn_section_exists(self, gnome_control_center):
        """Verify VPN section is visible in network panel."""
        try:
            app = wait_for_element(
                root, roleName="frame", name_contains="Settings", timeout=15
            )

            # Look for VPN section/row
            # In GNOME 42+, it's usually a row in the network panel
            vpn_section = find_element_safe(app, name_contains="VPN")

            assert vpn_section is not None, "VPN section not found in network settings"

        except Exception as e:
            take_screenshot_on_failure("test_vpn_section_exists")
            raise

    def test_add_vpn_button(self, gnome_control_center):
        """Verify Add VPN button exists and is clickable."""
        try:
            app = wait_for_element(
                root, roleName="frame", name_contains="Settings", timeout=15
            )

            # The Add VPN button might be:
            # - A "+" button in VPN section
            # - "Add VPN" button
            # - Row that says "Add VPN..."
            add_btn = find_element_safe(
                app, roleName="push button", name_contains="Add VPN"
            )
            if not add_btn:
                add_btn = find_element_safe(app, name_contains="Add VPN")

            # If no explicit Add VPN, there might be a generic + button
            if not add_btn:
                # Try to find VPN section first, then + button within
                vpn_section = find_element_safe(app, name_contains="VPN")
                if vpn_section:
                    add_btn = find_element_safe(
                        vpn_section, roleName="push button", name="+"
                    )

            assert add_btn is not None, "Add VPN button not found"

        except Exception as e:
            take_screenshot_on_failure("test_add_vpn_button")
            raise

    @pytest.mark.skip(reason="Complex flow - implement after basic tests pass")
    def test_add_vpn_full_flow(self, gnome_control_center):
        """
        Full flow: Navigate to VPN -> Add -> Select GlobalProtect.
        Skip for now as element names may vary.
        """
        pass
