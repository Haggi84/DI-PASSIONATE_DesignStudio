# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks the schematic's netlist (kicad-cli sch export netlist) against the
design in make_board.py, and the electrical rules KiCad's ERC would apply to
this design: every power input on a driven net, no output on an output, no
net with a single pin. Run: python3 check_netlist.py <netlist.net>"""
import re
import sys

import make_board as mb


def nets_of(path):
    text = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]+)"\)(.*?)\)\s*(?=\(net |\)\s*\)\s*$)', text, re.S):
        name = m.group(1).lstrip("/")
        out[name] = set(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)(?: \(pinfunction "[^"]*"\))?'
                                   r'(?: \(pintype "([^"]+)"\))?', m.group(2)))
    return out


def main(path):
    got = nets_of(path)
    want = {}
    for ref, (lib_id, *_r) in mb.PARTS.items():
        for pin, net in _r[2].items():
            want.setdefault(net, set()).add((ref, pin))
    flagged = {net for net, _xy in mb.FLAGS.values()}     # PWR_FLAGs: not in a netlist, they drive the net
    problems = []
    gotp = {n: {(r, p) for r, p, _t in v} for n, v in got.items() if not n.startswith("unconnected-")}
    for n in sorted(set(want) | set(gotp)):
        if want.get(n, set()) != gotp.get(n, set()):
            problems.append(f"net {n}: schematic {sorted(gotp.get(n, set()) - want.get(n, set()))} extra, "
                            f"{sorted(want.get(n, set()) - gotp.get(n, set()))} missing")
    for n, nodes in got.items():
        types = [t for _r, _p, t in nodes]
        if n.startswith("unconnected-"):
            if any(t not in ("no_connect", "passive") for t in types) or len(nodes) != 1:
                problems.append(f"{n}: {sorted(nodes)}")
            continue
        if len(nodes) < 2:
            problems.append(f"net {n}: one pin only")
        if "power_in" in types and "power_out" not in types and n not in flagged:
            problems.append(f"net {n}: power input not driven")
        if types.count("output") + types.count("power_out") > 1:
            problems.append(f"net {n}: {types.count('output') + types.count('power_out')} outputs")
    print(f"{len(gotp)} nets, {sum(len(v) for v in gotp.values())} pins; "
          + ("OK" if not problems else f"{len(problems)} problems"))
    for p in problems:
        print("  ", p)
    return not problems


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1]) else 1)
