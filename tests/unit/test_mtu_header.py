"""
Tests of plugins/gnome/nm-gpclient-mtu.h, the MTU rule of the GTK and Plasma
editors.

The header is plain C (no GLib, no Qt). It is compiled into a small program,
once as C and once as C++ (the Plasma editor includes it from C++), and every
result is compared with parse_mtu() of the service: the same rule in every
place. Without a compiler the tests are skipped on a developer machine and FAIL
in CI (the CI environment variable is set), so CI never skips them silently.

Run with: make test-unit  (or: python3 -m pytest tests/unit -v)
"""

import os
import shutil
import subprocess

import pytest

HEADER_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "gnome")

PROGRAM = r"""
#include <stdio.h>
#include "nm-gpclient-mtu.h"

int main (int argc, char **argv)
{
    int i;
    for (i = 1; i < argc; i++)
        printf ("%d\n", nm_gpclient_mtu_parse (argv[i]));
    return 0;
}
"""

# (text, result of the header: 0 = automatic, -1 = invalid). Whitespace and
# non-ASCII characters are written as escapes.
CASES = [
    ("1300", 1300),
    (" \t1300\n", 1300),
    ("\v1300\v", 1300),
    ("\f1300\r", 1300),
    ("01300", 1300),
    ("0" * 50 + "1300", 1300),
    ("576", 576),
    ("1500", 1500),
    ("500", 576),
    ("575", 576),
    ("1", 576),
    ("", 0),
    ("   ", 0),
    ("\v", 0),
    ("0", 0),
    ("00", 0),
    ("0000", 0),
    (" 0 ", 0),
    ("\v0\v", 0),
    ("0" * 50, 0),
    ("0 0", -1),
    ("0\t0", -1),
    ("13 00", -1),
    ("1501", -1),
    ("1600", -1),
    ("99999999999", -1),
    ("9" * 5000, -1),
    ("abc", -1),
    ("+1300", -1),
    ("-1300", -1),
    ("1300.0", -1),
    ("1300x", -1),
    ("0x578", -1),
    (" 1300", -1),  # no-break space: not stripped
    (" 1300", -1),  # em space: not stripped
    ("١٣٠٠", -1),  # Arabic-Indic digits
    ("²", -1),  # superscript two: str.isdigit() is true for it
]


def find_compiler(names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def build(tmp_path_factory, names, source_name, extra):
    compiler = find_compiler(names)
    if compiler is None:
        message = f"no compiler ({' or '.join(names)}) for {source_name}"
        if os.environ.get("CI"):
            pytest.fail(message + " in CI")
        pytest.skip(message)
    tmp = tmp_path_factory.mktemp("mtu_header")
    source = tmp / source_name
    source.write_text(PROGRAM)
    binary = tmp / "check"
    result = subprocess.run(
        [compiler, "-Wall", "-Wextra", "-Werror"] + extra + ["-I", HEADER_DIR, str(source), "-o", str(binary)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return str(binary)


@pytest.fixture(scope="module", params=["c", "c++"])
def header_program(request, tmp_path_factory):
    if request.param == "c":
        return build(tmp_path_factory, ["cc", "gcc", "clang"], "check.c", ["-std=c99"])
    return build(tmp_path_factory, ["c++", "g++", "clang++"], "check.cpp", [])


def run_program(binary, texts):
    # Arguments are UTF-8 bytes; the header must treat every byte >= 0x80 as invalid
    args = [binary] + [os.fsencode(text) for text in texts]
    result = subprocess.run(args, capture_output=True, check=True)
    return [int(line) for line in result.stdout.decode().splitlines()]


def test_the_rule(header_program):
    assert run_program(header_program, [text for text, _ in CASES]) == [expected for _, expected in CASES]


def test_the_header_gives_what_the_service_gives(header_program, service_module):
    results = run_program(header_program, [text for text, _ in CASES])

    for (text, _), result in zip(CASES, results):
        parsed = service_module.parse_mtu(text)
        # the service: 0 = automatic, None = invalid
        assert result == (-1 if parsed is None else parsed), repr(text)


def test_the_cases_are_consistent_with_the_service(service_module):
    # The expected values above are the rule itself, not a copy of the output
    for text, expected in CASES:
        parsed = service_module.parse_mtu(text)
        assert (-1 if parsed is None else parsed) == expected, repr(text)
