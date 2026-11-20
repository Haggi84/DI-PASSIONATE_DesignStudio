# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 4: the routed KiCad board back in FreeCAD, the package on U1, and the
clock path cut out for an EM model.

  out/assembly/expander_eval_board.FCStd   the whole board, package placed
  out/assembly/expander_clock_em.FCStd     the clock path: X1 -> R1 (33 ohm)
                                           -> U1 lead 7, with the package and
                                           four ports (add_ports); the EM panel
                                           goes on from here
  out/assembly/assembly_report.json

    freecadcmd examples/expander_board/make_assembly.py
"""

import json
import os
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples", "expander_board")
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from pkgstudio.app import boards, kicad, model  # noqa: E402

BOARD = os.path.join(HERE, "kicad", "expander_eval.kicad_pcb")
OUT = os.environ.get("PS_EXAMPLE_OUT") or os.path.join(HERE, "out", "assembly")
# the clock path in KiCad's coordinates (x right, y down), mm: oscillator X1,
# series resistor R1, U1 and the ground around them
CLOCK_REGION_KICAD = (113.0, 110.0, 126.5, 125.0)


def say(text):
    FreeCAD.Console.PrintMessage(f"[expander] {text}\n")


def _lead_vs_land(housing, board, lead="1"):
    """Distance in the board plane between lead *lead*'s finger and U1's land
    with that number: the package must sit with pin 1 on pad 1."""
    pad = {p.PadName: p for p in model.pads_of(housing)}[lead]
    c = model.pad_centre(pad)
    lands = [o for o in boards.pieces(board, "copper", "U1") if getattr(o, "Land", False)]
    best = min(lands, key=lambda o: (o.Shape.BoundBox.Center - c).Length)
    d = best.Shape.BoundBox.Center - c
    return best.Label, (d.x ** 2 + d.y ** 2) ** 0.5


def _gbox(o):
    sh = o.Shape.copy()
    sh.Placement = model.global_placement(o)
    return sh.BoundBox


def add_ports(board):
    """The clock path's ports, as the guide sets them in the GUI:
      Port_1  X1 pad 3 (OUT), standing on its edge towards pad 2, down to In1 (GND)
      Port_2  U1 lead 7 (CLK) land, on its inner edge, down to In1
      Port_3  U1 lead 8 (RESET) land, the same: crosstalk from the clock
      Port_4  R1 as a 33-ohm element in the gap between its pads (a lumped
              port that is not excited is a resistor of its Z0 in openEMS)
    Only Port_1 is excited."""
    from pkgstudio.app import ports
    lands = {o.Label: o for o in boards.pieces(board, "copper") if getattr(o, "Land", False)}
    in1_top = max(_gbox(o).ZMax for o in boards.pieces(board, "copper") if o.Label.startswith("In1.Cu"))
    made = []

    def down(land, name, edge):
        bb = _gbox(lands[land])
        y = bb.YMax if edge == "north" else bb.YMin
        made.append(ports.add_edge_port(board, FreeCAD.Vector(bb.XMin, y, bb.ZMin), FreeCAD.Vector(bb.XMax, y, bb.ZMin),
                                        in1_top - bb.ZMin, z0=50.0, name=name))

    down("X1 pad 3", "P1_oscillator_out", "south")
    down("U1 pad 7", "P2_lead7_CLK", "north")
    down("U1 pad 8", "P3_lead8_RESET", "north")
    a, b = lands["R1 pad 1"], lands["R1 pad 2"]
    ab, bb = _gbox(a), _gbox(b)
    lo, hi = (ab, bb) if ab.YMax < bb.YMin else (bb, ab)
    sa = a.Shape.copy()
    sa.Placement = model.global_placement(a)                 # the frame wants the face where it is
    face = max(sa.Faces, key=lambda f: f.BoundBox.ZMax + f.Area * 1e-9)   # the top face, not a side face
    frame = model.contact_frame(face, board)
    c = FreeCAD.Vector((ab.XMin + ab.XMax) / 2, (lo.YMax + hi.YMin) / 2, ab.ZMax)
    made.append(ports.add_port(board, frame, frame.to_uv(tuple(c)), 0.3, hi.YMin - lo.YMax, z0=33.0,
                               direction="+Y", name="P4_R1_33ohm"))
    for p in made[1:]:
        p.Excited = False
    for p in made:                                             # every port flat or upright as meant
        pb = _gbox(p)
        FreeCAD.Console.PrintMessage(f"[expander] {p.Label}: x {pb.XLength:.3f} y {pb.YLength:.3f} z {pb.ZLength:.3f} mm\n")
    return made


def run():
    os.environ["PS_EXPANDER_IMPORT"] = "1"        # import step 1 without running it
    try:
        import make_package
    finally:
        os.environ.pop("PS_EXPANDER_IMPORT", None)
    os.makedirs(OUT, exist_ok=True)
    report = {}
    for which, region in (("board", None), ("clock_em", CLOCK_REGION_KICAD)):
        doc, pkg_report = make_package.run()
        housing = next(o for o in doc.Objects if getattr(o, "HousingName", ""))
        package = model.assembly_owner(housing)
        r = None
        if region is not None:
            x0, y0, x1, y1 = region
            r = (x0, -y1, x1, -y0)                       # Package Studio: y up
        board, rep = kicad.import_board(doc, BOARD, r, name="Expander eval board" + (" (clock path)" if r else ""))
        boards.place_package(package, board, "U1")
        land, off = _lead_vs_land(housing, board)
        made_ports = add_ports(board) if region is not None else []
        if region is not None:                              # the die's layer bodies: not needed for this path
            for o in doc.Objects:
                if model.role(o) in (model.LAYER, getattr(model, "UNUSED_LEVEL", "-")):
                    o.Visibility = False
        doc.recompute()
        name = "expander_eval_board.FCStd" if region is None else "expander_clock_em.FCStd"
        doc.saveAs(os.path.join(OUT, name))
        report[which] = {"pieces": rep["pieces"], "footprints": rep["footprints"], "nets": sorted(rep["nets"]),
                         "unknown_materials": rep.get("unknown_materials", []),
                         "lead1_to_land_mm": round(off, 4), "land": land, "file": name,
                         "ports": [p.Label for p in made_ports]}
        say(f"{which}: {rep['pieces']} pieces, {rep['footprints']} footprints, {len(rep['nets'])} nets; "
            f"lead 1 sits {off * 1000:.0f} um from {land}")
        FreeCAD.closeDocument(doc.Name)
    with open(os.path.join(OUT, "assembly_report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    return report


if (__name__ in ("__main__", "make_assembly") or os.environ.get("PS_EXAMPLE_RUN")) and \
        not os.environ.get("PS_EXAMPLE_NO_RUN"):
    run()
