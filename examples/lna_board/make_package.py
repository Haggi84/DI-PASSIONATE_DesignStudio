# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 1 of the LNA evaluation board: ALL_LNA in a QFN-24 4x4 — the smallest
of IHP's packages (QFN / open QFN, 24 to 64 pins, 0.5 mm pitch), so the
shortest bond wires —, wired by lna_pinlist.csv with the RF pads ground-
signal-ground on three neighbouring leads, checked (package LVS, MIL-STD-883
TM 2010) and written out for the board designer:

  out/package/LNAEval.pretty/QFN-24_4x4_LNA.kicad_mod, LNAEval.kicad_sym,
  QFN-24_4x4_LNA.step, QFN-24_4x4_LNA_bonding.svg, _wires.csv,
  lna_qfn24.FCStd, package_report.json

    freecadcmd examples/lna_board/make_package.py
or in FreeCAD's Python console:
    exec(open(r".../examples/lna_board/make_package.py").read())
"""

import json
import os
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples", "lna_board")
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pkgstudio.app import bonding, housings, kicad, model, netlist, wires  # noqa: E402
from pkgstudio.core import jedec  # noqa: E402

TECH = os.path.join(ROOT, "technologies", "IHP-SG13G2")
GDS_GZ = os.path.join(ROOT, "examples", "data", "ALL_LNA.gds")
PINLIST = os.path.join(HERE, "lna_pinlist.csv")
PACKAGE = "QFN-24 4x4"
NAME = "QFN-24_4x4_LNA"
OUT = os.environ.get("PS_EXAMPLE_OUT") or os.path.join(HERE, "out", "package")


def say(text):
    FreeCAD.Console.PrintMessage(f"[lna] {text}\n")


def run():
    os.makedirs(OUT, exist_ok=True)
    doc = FreeCAD.newDocument("lna_qfn24")
    chip = model.import_chip(doc, GDS_GZ, TECH)
    housing = housings.from_jedec(doc, jedec.by_name(PACKAGE))
    housings.place_chip(chip, housing)
    intent = netlist.load([PINLIST])
    missing = netlist.apply(doc, intent, [chip])
    made = netlist.assign(housing, intent)
    found, nets = netlist.lvs(housing, intent)
    errors = [str(f) for f in found if f.level == "error"]
    rules = wires.check(doc)
    rejects = [f"{f.wire}: {f.rule}" for v in rules.values() for f in v if not f.ok and f.severity == "reject"]
    kinds = {p.net: p.kind for p in intent.pins}
    lib = kicad.export_package(housing, OUT, NAME, lib="LNAEval", kinds=kinds)
    diagram = bonding.export(housing, OUT, NAME, intent, title="ALL_LNA (SG13G2) in QFN-24 4x4",
                             notes=["Die: ALL_LNA (SG13G2), 0.78 x 0.98 mm",
                                    "RF pads bonded ground-signal-ground: leads 3/4/5 in, 14/15/16 out"])
    lengths = [float(getattr(w.WireLength, "Value", w.WireLength)) for w in wires.wires(doc)]
    report = {"package": PACKAGE, "wires": len(made), "missing": [p.key for p in missing],
              "lvs_errors": errors, "rejects": rejects,
              "wire_mm": [round(min(lengths), 3), round(max(lengths), 3)],
              "lead_nets": dict(sorted(nets.items(), key=lambda kv: int(kv[0]))),
              "files": list(lib) + list(diagram)}
    doc.saveAs(os.path.join(OUT, "lna_qfn24.FCStd"))
    with open(os.path.join(OUT, "package_report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    say(f"{PACKAGE}: {len(made)} wires, {len(errors)} LVS errors, {len(rejects)} MIL-STD-883 rejects, "
        f"wires {report['wire_mm'][0]}-{report['wire_mm'][1]} mm")
    for k, v in report["lead_nets"].items():
        say(f"   lead {k:>2}: {v or '-'}")
    return doc, report


if (__name__ in ("__main__", "make_package") or os.environ.get("PS_EXAMPLE_RUN") or "__file__" not in globals()) \
        and not os.environ.get("PS_EXAMPLE_NO_RUN") and not os.environ.get("PS_LNA_IMPORT"):
    run()
