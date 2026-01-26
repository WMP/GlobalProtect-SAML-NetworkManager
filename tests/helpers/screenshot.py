"""
Screenshot utilities for test failure debugging.
"""

import os
import subprocess
from datetime import datetime

ARTIFACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "artifacts"
)


def ensure_artifacts_dir():
    """Create artifacts directory if it doesn't exist."""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)


def take_screenshot(name=None):
    """
    Take a screenshot and save to artifacts directory.

    Args:
        name: Screenshot filename (without extension).
              If None, uses timestamp.

    Returns:
        Path to saved screenshot or None on failure
    """
    ensure_artifacts_dir()

    if name is None:
        name = datetime.now().strftime("screen_%Y%m%d_%H%M%S")

    filepath = os.path.join(ARTIFACTS_DIR, f"{name}.png")

    # Try gnome-screenshot first
    result = subprocess.run(["gnome-screenshot", "-f", filepath], capture_output=True)

    if result.returncode == 0 and os.path.exists(filepath):
        return filepath

    # Fallback to ImageMagick import
    result = subprocess.run(
        ["import", "-window", "root", filepath], capture_output=True
    )

    if result.returncode == 0 and os.path.exists(filepath):
        return filepath

    # Last resort: scrot
    result = subprocess.run(["scrot", filepath], capture_output=True)

    if result.returncode == 0 and os.path.exists(filepath):
        return filepath

    return None


def take_screenshot_on_failure(test_name):
    """
    Take screenshot with test name prefix for failure debugging.

    Args:
        test_name: Name of the failing test

    Returns:
        Path to saved screenshot or None
    """
    timestamp = datetime.now().strftime("%H%M%S")
    name = f"fail_{test_name}_{timestamp}"
    path = take_screenshot(name)

    if path:
        print(f"Screenshot saved: {path}")
    else:
        print(f"Failed to take screenshot for {test_name}")

    return path
