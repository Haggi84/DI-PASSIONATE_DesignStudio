# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The LNA's EM ports on the open document: for the GUI way of the guide.

Open (or build in the GUI) the RF cut-out with the package on U1, then in
FreeCAD's Python console:

    exec(open(r"<...>/examples/lna_board/add_em_ports.py").read())

It adds what make_assembly.py adds to lna_rf_em.FCStd:
  - the die's six RF pads as TopMetal2 blocks, and EmChipMetal = none on
    the chip (no other die metal in the EM model),
  - P1 / P4 at the cut ends of the input / output line, down to the bottom
    ground; P2 / P3 on the die, RFIN to GND14 and RFOUT to GND3. P1 excited.
Run it once per document (it refuses when ports are already there).
"""

import os
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples", "lna_board")
for p in (os.path.dirname(os.path.dirname(HERE)), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ["PS_LNA_ASSEMBLY_IMPORT"] = "1"
try:
    import make_assembly as _asm
finally:
    os.environ.pop("PS_LNA_ASSEMBLY_IMPORT", None)

from pkgstudio.app import model, ports as _ports  # noqa: E402


def add_to(doc=None):
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise RuntimeError("No document open.")
    if _ports.ports(doc):
        raise RuntimeError(f"{doc.Label} has ports already - nothing added.")
    boards = model.by_role(doc, model.BOARD)
    if not boards:
        raise RuntimeError("No board in the document: import the RF region first (KiCad board).")
    board = boards[-1]
    _asm.add_die_pads(doc)
    made = _asm.add_ports(board, doc)
    doc.recompute()
    return made


if not os.environ.get("PS_EXAMPLE_NO_RUN"):
    for _p in add_to():
        FreeCAD.Console.PrintMessage(f"[lna] added {_p.Label}\n")
