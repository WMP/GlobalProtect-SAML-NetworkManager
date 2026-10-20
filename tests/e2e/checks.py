#!/usr/bin/python3
"""Checks of an end-to-end run (installed in the image as /opt/gp-demo/checks.py).

Usage: checks.py <gnome|kde|routing|auth-cancel> <out-dir>

Reads what the scenario saved in the out dir (state-<name>.txt from lib.sh
`snapshot`, scenario.exit, NetworkManager.log, edge-wrapper.log, ...), writes
<out-dir>/results.json and prints a table. The exit code is 1 when a check
fails. The checks only read files, so tests/unit/test_e2e_checks.py can run
them on made-up states.

results.json: {"scenario": ..., "ok": bool,
               "checks": [{"name": ..., "ok": bool, "detail": ...}, ...]}
"""

import glob
import json
import os
import re
import sys

TUNNEL_ADDRESS = "10.20.30.45/32"
VPN_DNS = "10.20.0.53 10.20.0.54"
VPN_DOMAIN = "contoso.example"
WIRED_DEVICE = "enp0s31f6"
WIRED_DNS = "192.168.1.1"
VPN_NAME = "Contoso VPN"
EDGE_RE = re.compile(r"msedge|microsoft-edge")

# The states each scenario saves (name -> kind)
#   baseline: no VPN, "full": connected with the default route through the VPN,
#   "split": connected with "only for resources on its network"
SCENARIO_STATES = {
    "gnome": {"start": "baseline", "connected": "full", "disconnected": "baseline"},
    "kde": {"start": "baseline", "connected": "full", "disconnected": "baseline"},
    "routing": {
        "before": "baseline",
        "connected-default": "full",
        "disconnected": "baseline",
        "connected-never-default": "split",
        "disconnected-final": "baseline",
    },
    "auth-cancel": {"before": "baseline", "failed": "baseline"},
}


class Run:
    """The files of one run."""

    def __init__(self, out):
        self.out = out
        self._states = {}

    def text(self, name):
        try:
            with open(os.path.join(self.out, name), encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def state(self, name):
        """{section: [lines]} of state-<name>.txt, or None when it is missing."""
        if name not in self._states:
            text = self.text(f"state-{name}.txt")
            sections = None
            if text is not None:
                sections = {}
                current = None
                for line in text.splitlines():
                    if line.startswith("## "):
                        current = line[3:].strip()
                        sections[current] = []
                    elif current is not None:
                        sections[current].append(line)
            self._states[name] = sections
        return self._states[name]

    def state_names(self, prefix):
        names = []
        for path in glob.glob(os.path.join(self.out, f"state-{prefix}*.txt")):
            names.append(os.path.basename(path)[len("state-") : -len(".txt")])
        return sorted(names)


class Results:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail=""):
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})


def section(state, title):
    return state.get(title, []) if state else []


def lines_with(lines, text):
    return [line for line in lines if text in line]


def check_state(results, run, name, kind, scenario):
    """Checks of one saved state."""
    state = run.state(name)
    label = f"[{name}]"
    if state is None:
        results.add(f"{label} state saved", False, f"state-{name}.txt is missing")
        return
    active = section(state, "nmcli connection show --active")
    gpd0_addr = [line.strip() for line in section(state, "ip -4 addr show gpd0") if line.strip().startswith("inet ")]
    route_default = section(state, "ip route show default")
    get_inet = " ".join(section(state, "ip route get 9.9.9.9"))
    get_corp = " ".join(section(state, "ip route get 10.20.0.10"))
    status = " ".join(section(state, "resolvectl status gpd0"))
    dns = section(state, "resolvectl dns")
    domain = section(state, "resolvectl domain")
    procs = section(state, "processes")
    windows = section(state, "windows")

    # The wired connection is never touched
    results.add(
        f"{label} wired connection stays active",
        bool(lines_with(active, "Wired connection 1:802-3-ethernet:activated")),
        "; ".join(active),
    )

    if kind in ("full", "split"):
        results.add(
            f"{label} VPN activated",
            bool(lines_with(active, f"{VPN_NAME}:vpn:activated")),
            "; ".join(active),
        )
        results.add(
            f"{label} tunnel address is exactly {TUNNEL_ADDRESS}",
            len(gpd0_addr) == 1 and gpd0_addr[0].split()[1] == TUNNEL_ADDRESS,
            "; ".join(gpd0_addr) or "no address on gpd0",
        )
        if kind == "full":
            results.add(
                f"{label} full tunnel: default route and Internet go through gpd0",
                bool(lines_with(route_default, "dev gpd0")) and "dev gpd0" in get_inet,
                f"default: {'; '.join(route_default)} | 9.9.9.9: {get_inet}",
            )
            results.add(
                f"{label} full tunnel: company network goes through gpd0",
                "dev gpd0" in get_corp,
                get_corp,
            )
            results.add(
                f"{label} full tunnel: gpd0 is the DNS default route",
                "+DefaultRoute" in status,
                status,
            )
        else:
            results.add(
                f"{label} never-default: default route stays on the wired link",
                bool(route_default)
                and not lines_with(route_default, "gpd0")
                and all(WIRED_DEVICE in line for line in route_default),
                "; ".join(route_default),
            )
            results.add(
                f"{label} never-default: Internet via wired link, company network via gpd0",
                f"dev {WIRED_DEVICE}" in get_inet and "dev gpd0" in get_corp,
                f"9.9.9.9: {get_inet} | 10.20.0.10: {get_corp}",
            )
            results.add(
                f"{label} never-default: gpd0 is not the DNS default route",
                "-DefaultRoute" in status,
                status,
            )
        results.add(
            f"{label} VPN DNS servers and domain on gpd0",
            f"DNS Servers: {VPN_DNS}" in status and f"DNS Domain: {VPN_DOMAIN}" in status,
            status,
        )
        results.add(
            f"{label} browser window gone and no browser running",
            not [line for line in procs if EDGE_RE.search(line)]
            and not [line for line in windows if "Sign in" in line or "GlobalProtect Portal" in line],
            "; ".join(lines_with(procs, "edge") + [w for w in windows if "Sign in" in w or "Portal" in w]),
        )
    else:
        results.add(
            f"{label} VPN not active",
            not lines_with(active, VPN_NAME),
            "; ".join(active),
        )
        results.add(
            f"{label} no tunnel device, no route through it",
            not gpd0_addr
            and not [line for line in section(state, "ip route") if "gpd0" in line]
            and not lines_with(active, "gpd0"),
            "; ".join(gpd0_addr + lines_with(section(state, "ip route"), "gpd0")),
        )
        results.add(
            f"{label} routes: Internet via the wired link",
            f"dev {WIRED_DEVICE}" in get_inet and f"via {WIRED_DNS}" in get_inet,
            get_inet,
        )
        dns_text = " ".join(dns)
        results.add(
            f"{label} DNS: only the wired link's server",
            f"({WIRED_DEVICE}): {WIRED_DNS}" in dns_text and "10.20." not in dns_text and VPN_DOMAIN not in " ".join(domain),
            f"dns: {dns_text} | domain: {' '.join(domain)}",
        )
        results.add(
            f"{label} no VPN helper left running",
            not [line for line in procs if re.search(r"gpclient|gpauth", line)]
            and not [line for line in procs if EDGE_RE.search(line)],
            "; ".join(procs),
        )


def check_edge(results, run, scenario):
    """Edge is started by the wrapper as the desktop user, never as root."""
    wrapper = run.text("edge-wrapper.log") or ""
    # gpauth runs as the desktop user, so the wrapper starts as uid 1000; if it
    # was started as root it must drop to uid 1000 before it starts the browser
    calls = wrapper.count("called with args")
    as_user = len(re.findall(r"running as EUID=1000 \(no privilege drop needed\)", wrapper))
    dropped = len(re.findall(r"dropping privileges to demo \(uid=1000\)", wrapper))
    results.add(
        "wrapper runs the browser as the desktop user (uid 1000)",
        calls > 0 and as_user + dropped == calls,
        f"{calls} wrapper runs, {as_user} as uid 1000, {dropped} dropped privileges" if wrapper else "no edge-wrapper.log",
    )
    results.add(
        "wrapper started the browser",
        "started browser with PID" in wrapper,
        "no 'started browser with PID' in edge-wrapper.log",
    )
    opened = run.state_names("edge-open")
    if not opened:
        results.add("browser was running during the sign-in", False, "no state-edge-open-*.txt")
        return
    bad = []
    seen = 0
    for name in opened:
        for line in section(run.state(name), "processes"):
            if EDGE_RE.search(line) and line.split()[2:3] != ["sudo"]:
                seen += 1
                if line.split()[0] != "demo":
                    bad.append(f"{name}: {line.strip()[:100]}")
    results.add(
        "browser ran as demo (uid 1000), not as root",
        seen > 0 and not bad,
        "; ".join(bad) or (f"{seen} browser processes" if seen else "no browser process found"),
    )


def check_logs(results, run, scenario):
    nm = run.text("NetworkManager.log")
    wrapper = run.text("edge-wrapper.log") or ""
    if nm is None:
        results.add("service log present", False, "NetworkManager.log is missing")
        return
    bad = [line for line in nm.splitlines() if "Traceback" in line or "__main__ [ERROR]" in line or "vpnc hook: could not" in line]
    if scenario == "auth-cancel":
        # The failure is expected, but it has to be a clean one
        results.add("no traceback in the service log", "Traceback" not in nm, "")
        results.add(
            "service logged the failed sign-in",
            "Failed to authenticate with the portal" in nm or "SAML authentication timed out" in nm,
            "no 'Failed to authenticate' in the service log",
        )
    else:
        results.add("no errors or tracebacks in the service log", not bad, "; ".join(bad[:3]))
        results.add(
            "wrapper closed the browser when gpauth was done",
            "authentication finished" in wrapper,
            "no 'authentication finished' in edge-wrapper.log",
        )
    results.add(
        "no traceback in the wrapper log",
        "Traceback" not in wrapper,
        "",
    )


def check_cancel(results, run):
    before = run.state("before")
    failed = run.state("failed")
    if before is None or failed is None:
        return
    for title in ("ip route", "resolvectl dns"):
        a = sorted(section(before, title))
        b = sorted(section(failed, title))
        results.add(f"cancelled sign-in left '{title}' unchanged", a == b, f"before: {a} | after: {b}")
    up_log = run.text("nmcli-up.log") or ""
    match = re.search(r"exit code: (\d+)", up_log)
    results.add(
        "connection attempt ended with an error",
        bool(match) and match.group(1) != "0",
        up_log.strip()[-200:] or "no nmcli-up.log",
    )


def run_checks(scenario, out):
    run = Run(out)
    results = Results()
    exit_text = (run.text("scenario.exit") or "").strip()
    results.add("scenario ran to the end (exit 0)", exit_text == "0", f"scenario.exit: {exit_text or 'missing'}")
    for name, kind in SCENARIO_STATES.get(scenario, {}).items():
        check_state(results, run, name, kind, scenario)
    check_edge(results, run, scenario)
    check_logs(results, run, scenario)
    if scenario == "auth-cancel":
        check_cancel(results, run)
    if scenario not in SCENARIO_STATES:
        results.add("known scenario", False, scenario)
    return results.items


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    scenario, out = argv[1], argv[2]
    items = run_checks(scenario, out)
    ok = all(item["ok"] for item in items)
    with open(os.path.join(out, "results.json"), "w", encoding="utf-8") as handle:
        json.dump({"scenario": scenario, "ok": ok, "checks": items}, handle, indent=2)
        handle.write("\n")
    for item in items:
        mark = "ok  " if item["ok"] else "FAIL"
        print(f"{mark} {item['name']}" + ("" if item["ok"] else f"\n       {item['detail']}"))
    failed = [item for item in items if not item["ok"]]
    print(f"\n{scenario}: {len(items) - len(failed)} of {len(items)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
