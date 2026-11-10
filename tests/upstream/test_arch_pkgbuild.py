"""
The pins of the user mode of arch/PKGBUILD (pkgver, sdbus, the commits and URLs of
the three submodules) against the checked-out submodules. The unit tests skip this
check without the submodules (CI runs them without); here it FAILS instead, like
the other tests of tests/upstream. The check and its negative cases are in
tests/unit/test_arch_build.py (user_mode_problems).

Run with: python3 -m pytest tests/upstream -q
"""

import importlib.util
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUBMODULE = os.path.join(ROOT, "external", "GlobalProtect-openconnect")


def load_unit_test_module():
    path = os.path.join(ROOT, "tests", "unit", "test_arch_build.py")
    spec = importlib.util.spec_from_file_location("arch_build_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def test_the_user_mode_of_the_pkgbuild_pins_the_checked_out_submodules():
    assert os.path.isfile(os.path.join(SUBMODULE, ".gitmodules")), \
        "external/GlobalProtect-openconnect is not checked out: git submodule update --init --recursive"
    checks = load_unit_test_module()
    problems = checks.user_mode_problems(read("arch/PKGBUILD"), read("debian/changelog"), read("rpm/sources"),
                                         checks.all_gitmodules(), checks.real_links())
    assert problems == []
