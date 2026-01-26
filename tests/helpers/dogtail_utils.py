"""
Dogtail/AT-SPI utilities for GUI testing.
Provides wait_for, retry, and cleanup functions.
"""

import os
import subprocess
import time


def wait_for_element(
    parent, roleName=None, name=None, name_contains=None, timeout=10, retry_interval=0.5
):
    """
    Wait for an AT-SPI element with retry logic.

    Args:
        parent: Parent node to search in (e.g., dogtail.tree.root)
        roleName: AT-SPI role (e.g., 'frame', 'push button', 'dialog')
        name: Exact element name
        name_contains: Partial name match (case-insensitive)
        timeout: Max wait time in seconds
        retry_interval: Time between retries

    Returns:
        Found element node

    Raises:
        TimeoutError: If element not found within timeout
    """
    from dogtail.predicate import GenericPredicate

    start = time.time()
    last_error = None

    while time.time() - start < timeout:
        try:
            if name_contains:
                # Search using predicate for partial match
                pred = GenericPredicate(roleName=roleName)
                for node in parent.findChildren(pred, recursive=True):
                    node_name = node.name or ""
                    if name_contains.lower() in node_name.lower():
                        return node
            else:
                # Exact name match
                return parent.child(roleName=roleName, name=name)
        except Exception as e:
            last_error = e

        time.sleep(retry_interval)

    error_msg = f"Element not found: role={roleName}, name={name or name_contains}"
    if last_error:
        error_msg += f" (last error: {last_error})"
    raise TimeoutError(error_msg)


def find_element_safe(parent, roleName=None, name=None, name_contains=None):
    """
    Find element without raising exception if not found.

    Returns:
        Element node or None
    """
    try:
        return wait_for_element(parent, roleName, name, name_contains, timeout=2)
    except TimeoutError:
        return None


def cleanup_process(proc, name):
    """
    Terminate process and ensure it's not hanging.

    Args:
        proc: subprocess.Popen instance
        name: Process name for pkill fallback
    """
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    # Additional cleanup via pkill
    subprocess.run(["pkill", "-f", name], capture_output=True)


def check_accessibility_enabled():
    """
    Check if AT-SPI accessibility is enabled.

    Returns:
        bool: True if enabled
    """
    try:
        result = subprocess.run(
            [
                "gsettings",
                "get",
                "org.gnome.desktop.interface",
                "toolkit-accessibility",
            ],
            capture_output=True,
            text=True,
        )
        return "true" in result.stdout.lower()
    except Exception:
        return False


def check_x11_session():
    """
    Check if running in X11 session (not Wayland).

    Returns:
        bool: True if X11
    """
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    return session_type == "x11"


def get_session_info():
    """
    Get current session information for debugging.

    Returns:
        dict with session details
    """
    return {
        "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "DISPLAY": os.environ.get("DISPLAY", "not set"),
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "not set"),
        "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
            "DBUS_SESSION_BUS_ADDRESS", "not set"
        ),
        "accessibility_enabled": check_accessibility_enabled(),
    }
