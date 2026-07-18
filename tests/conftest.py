"""
Pytest configuration and fixtures for GUI tests.
"""

import os
import subprocess
import time

import pytest
from helpers.dogtail_utils import (
    check_accessibility_enabled,
    check_x11_session,
    cleanup_process,
    get_session_info,
)
from helpers.screenshot import take_screenshot_on_failure


def _only_unit_tests(config):
    """True when the run targets only tests/unit (no GUI environment needed)."""
    unit_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "unit"))
    args = config.args or []
    if not args:
        return False
    for arg in args:
        path = os.path.abspath(str(arg).split("::")[0])
        if not (path == unit_dir or path.startswith(unit_dir + os.sep)):
            return False
    return True


def pytest_configure(config):
    """Check environment before running tests."""
    if _only_unit_tests(config):
        return

    session_info = get_session_info()

    print("\n=== Session Info ===")
    for key, value in session_info.items():
        print(f"  {key}: {value}")
    print("====================\n")

    if not check_x11_session():
        pytest.exit(
            f"GUI tests require X11 session. Current: {os.environ.get('XDG_SESSION_TYPE', 'unknown')}\n"
            "Switch to X11 session or run with Xvfb."
        )

    if not check_accessibility_enabled():
        print("WARNING: Accessibility may not be enabled. Run:")
        print("  gsettings set org.gnome.desktop.interface toolkit-accessibility true")


@pytest.fixture
def nm_connection_editor():
    """
    Start nm-connection-editor and clean up after test.

    Yields:
        subprocess.Popen: The editor process
    """
    # Kill any existing instances
    subprocess.run(["pkill", "-f", "nm-connection-editor"], capture_output=True)
    time.sleep(0.5)

    # Start editor
    proc = subprocess.Popen(
        ["nm-connection-editor"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # Wait for window to appear
    time.sleep(2)

    yield proc

    # Cleanup
    cleanup_process(proc, "nm-connection-editor")


@pytest.fixture
def gnome_control_center():
    """
    Start GNOME Settings (gnome-control-center) network panel.

    Yields:
        subprocess.Popen: The settings process
    """
    subprocess.run(["pkill", "-f", "gnome-control-center"], capture_output=True)
    time.sleep(0.5)

    proc = subprocess.Popen(
        ["gnome-control-center", "network"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    time.sleep(3)  # GNOME Settings is slower to start

    yield proc

    cleanup_process(proc, "gnome-control-center")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Take screenshot on test failure."""
    outcome = yield
    rep = outcome.get_result()

    if rep.when == "call" and rep.failed:
        take_screenshot_on_failure(item.name)
