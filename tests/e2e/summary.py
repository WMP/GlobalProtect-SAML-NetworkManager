#!/usr/bin/python3
"""Markdown table of the results of one end-to-end scenario.

Usage: summary.py <out-dir> [title]

Reads <out-dir>/results.json (written by checks.py) and prints a table for the
GitHub job summary. Prints a note instead when the file is missing.
"""

import json
import os
import sys


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = argv[1]
    title = argv[2] if len(argv) > 2 else os.path.basename(out.rstrip("/"))
    try:
        with open(os.path.join(out, "results.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        print(f"### End-to-end: {title}\n\nNo results.json ({error}). See the artifact for the logs.")
        return 0
    passed = sum(1 for item in data["checks"] if item["ok"])
    print(f"### End-to-end: {title} - {'passed' if data['ok'] else 'FAILED'} ({passed} of {len(data['checks'])} checks)\n")
    print("| Check | Result | Detail |")
    print("|---|---|---|")
    for item in data["checks"]:
        detail = "" if item["ok"] else item["detail"].replace("|", "\\|").replace("\n", " ")[:200]
        print(f"| {item['name']} | {'ok' if item['ok'] else 'FAIL'} | {detail} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
