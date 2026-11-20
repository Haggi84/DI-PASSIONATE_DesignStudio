# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 3 of the LNA evaluation board: the KiCad board back in FreeCAD, the
QFN-24 on U1, and the RF path cut out for the EM model.

  out/assembly/lna_eval_board.FCStd   the whole board, package placed
  out/assembly/lna_rf_em.FCStd        the RF path: 7.25 mm of GCPW each side of
                                      U1, the package and four ports
  out/assembly/assembly_report.json

The four ports (add_ports), numbered as the S-parameters come out:

  P1  RF in at the cut of the input line, 7.25 mm before U1's land: standing on
      the line's end face, from its underside down to the ground plane
      (0.508 mm). 50 ohm, excited.
  P2  on the die: RFIN pad to its ground neighbour GND14, flat in the pads'
      plane (TopMetal2's top), across the 44 um gap. 50 ohm.
  P3  on the die: RFOUT to GND3, the same.
  P4  RF out at the cut of the output line, like P1.

P1 -> P2 is the input transition the LNA sees (SMA side to die pad: line,
lead, bond wire), P3 -> P4 the output transition; P1 -> P4 (and P2 -> P3) is
the coupling from output back to input around the die, the number that
decides whether the packaged LNA stays stable. The die's own metal is left
out, only its six RF pads take part as TopMetal2 blocks (add_die_pads), so
P2 and P3 see the package, not the LNA: the LNA's S-parameters are cascaded
with these afterwards (circuit model).

    freecadcmd examples/lna_board/make_assembly.py
or, in FreeCAD's Python console:
    exec(open(r"<...>/examples/lna_board/make_assembly.py").read())
"""

import json
import os
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples", "lna_board")
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from pkgstudio.app import boards, kicad, model  # noqa: E402

BOARD = os.path.join(HERE, "kicad", "lna_eval.kicad_pcb")
OUT = os.environ.get("PS_EXAMPLE_OUT") or os.path.join(HERE, "out", "assembly")
# the RF path in KiCad's coordinates (x right, y down), mm: U1 at (120, 112),
# the lines at y = 112.25, 7.25 mm of each (cut between two fence vias), the
# via fences and 3 mm of ground either side; the bias lines are cut 2 mm below the package
RF_REGION_KICAD = (110.5, 108.5, 129.5, 116.0)
Z0 = 50.0
# what the EM panel shows when lna_rf_em.FCStd is opened (app.em_settings)
EM_SETTINGS = dict(solver="openEMS", fmin_ghz=0.5, fmax_ghz=6.0, mesh="coarse", min_cell_um=50.0,
                   note="S-band LNA package + board: 0.5-6 GHz (stability beyond the band), coarse / 50 um "
                        "first, then MIN_CELL_UM=25 as a convergence check; z cells capped at 10 um")
# the ready-to-open copy in the repository, beside this script
SHIPPED = os.path.join(HERE, "lna_rf_em.FCStd")


def say(text):
    FreeCAD.Console.PrintMessage(f"[lna] {text}\n")


def _gbox(o):
    sh = o.Shape.copy()
    sh.Placement = model.global_placement(o)
    return sh.BoundBox


def _lead_vs_land(housing, board, lead="1"):
    pad = {p.PadName: p for p in model.pads_of(housing)}[lead]
    c = model.pad_centre(pad)
    lands = [o for o in boards.pieces(board, "copper", "U1") if getattr(o, "Land", False)]
    best = min(lands, key=lambda o: (o.Shape.BoundBox.Center - c).Length)
    d = best.Shape.BoundBox.Center - c
    return best.Label, (d.x ** 2 + d.y ** 2) ** 0.5


def _top_face(o):
    s = o.Shape.copy()
    s.Placement = model.global_placement(o)
    return max(s.Faces, key=lambda f: f.BoundBox.ZMax + f.Area * 1e-9)


RF_PADS = ("RFIN", "GND14", "GND15", "RFOUT", "GND3", "GND4")


def add_die_pads(doc):
    """The die's RF pads as TopMetal2 blocks, and no other die metal in the
    EM model (EmChipMetal = none): the LNA's own TopMetal2 (its inductors,
    its lines) must not hang on the die ports, they are to see the package
    only. Each block is the pad's opening, TopMetal2 thick, its top in the
    pad's plane where the wire's wedge and the port sit."""
    import Part
    from pkgstudio.app import em_model, materials
    chip = next(o for o in doc.Objects if model.role(o) == "chip")
    lv = {d[0]: d for d in em_model.drawn_levels(chip)}
    t = (lv["TopMetal2"][3] - lv["TopMetal2"][2]) if "TopMetal2" in lv else 0.003
    pads = {o.PadName: o for o in model.all_pads(doc, "chip")}
    made = []
    for name in RF_PADS:
        b = _gbox(pads[name])
        o = doc.addObject("Part::Feature", "DiePad")
        o.Label = f"Die pad {name} (TopMetal2)"
        o.Shape = Part.makeBox(b.XLength, b.YLength, t, FreeCAD.Vector(b.XMin, b.YMin, b.ZMax - t))
        materials.set_material(o, "IHP SG13G2 / TopMetal2")
        made.append(o)
    if not hasattr(chip, "EmChipMetal"):
        chip.addProperty("App::PropertyString", "EmChipMetal", model.GROUP,
                         "Die metal in the EM model: top, top1, all or none (the EM panel's default)")
    chip.EmChipMetal = "none"
    return made


def add_ports(board, doc):
    from pkgstudio.app import ports
    copper = boards.pieces(board, "copper")
    boxes = [(o, _gbox(o)) for o in copper]
    plane = min(((o, b) for o, b in boxes if b.ZMin < 1e-6 and not o.Label.startswith("vias")),
                key=lambda ob: ob[1].ZMax)                      # the bottom ground plane: the cut's extent
    bb_all, ground_top = plane[1], plane[1].ZMax
    made = []

    # P1, P4: the RF lines' cut ends, at the region's left and right edge
    for name, side in (("P1_RF_in_line", "left"), ("P4_RF_out_line", "right")):
        x_cut = bb_all.XMin if side == "left" else bb_all.XMax
        ends = [(o, b) for o, b in boxes if b.ZMin > ground_top + 0.1 and abs((b.XMin if side == "left" else b.XMax) - x_cut) < 1e-3
                and 0.7 < b.YLength < 1.0]                       # the 0.85 mm line, not the ground pour
        o, b = min(ends, key=lambda ob: abs(ob[1].Center.y))
        x = x_cut
        made.append(ports.add_edge_port(board, FreeCAD.Vector(x, b.YMin, b.ZMin), FreeCAD.Vector(x, b.YMax, b.ZMin),
                                        ground_top - b.ZMin, z0=Z0, name=name))
    # P2, P3: on the die, signal pad to its ground neighbour
    pads = {o.PadName: o for o in model.all_pads(doc, "chip")}
    chip = next(o for o in doc.Objects if model.role(o) == "chip")
    for name, sig, gnd in (("P2_die_RFIN", "RFIN", "GND14"), ("P3_die_RFOUT", "RFOUT", "GND3")):
        a, g = _gbox(pads[sig]), _gbox(pads[gnd])
        lo, hi = (a, g) if a.YMax < g.YMin else (g, a)
        frame = model.contact_frame(_top_face(pads[sig]), chip)
        c = FreeCAD.Vector((a.XMin + a.XMax) / 2, (lo.YMax + hi.YMin) / 2, a.ZMax)
        made.append(ports.add_port(chip, frame, frame.to_uv(tuple(c)), a.XLength, hi.YMin - lo.YMax, z0=Z0,
                                   direction="+Y", name=name))
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    made.sort(key=lambda p: order[p.Label[:2]])
    for k, p in enumerate(made):
        p.PortIndex = k + 1
        p.Excited = k == 0
        pb = _gbox(p)
        say(f"{p.Label}: port {p.PortIndex}, x {pb.XLength:.3f} y {pb.YLength:.3f} z {pb.ZLength:.3f} mm "
            f"at ({pb.Center.x:.3f}, {pb.Center.y:.3f}, {pb.Center.z:.3f})")
    return made


def run():
    os.environ["PS_LNA_IMPORT"] = "1"        # import step 1 without running it
    try:
        import make_package
    finally:
        os.environ.pop("PS_LNA_IMPORT", None)
    os.makedirs(OUT, exist_ok=True)
    report = {}
    for which, region in (("board", None), ("rf_em", RF_REGION_KICAD)):
        doc, pkg_report = make_package.run()
        housing = next(o for o in doc.Objects if getattr(o, "HousingName", ""))
        package = model.assembly_owner(housing)
        r = None
        if region is not None:
            x0, y0, x1, y1 = region
            r = (x0, -y1, x1, -y0)                       # Package Studio: y up
        board, rep = kicad.import_board(doc, BOARD, r, name="LNA eval board" + (" (RF path)" if r else ""))
        boards.place_package(package, board, "U1")
        land, off = _lead_vs_land(housing, board)
        made_ports = []
        if region is not None:
            add_die_pads(doc)
            made_ports = add_ports(board, doc)
            from pkgstudio.app import em_settings
            em_settings.save(doc, **EM_SETTINGS)
            for o in doc.Objects:                        # the die's metal: not part of the package model
                if model.role(o) in (model.LAYER, getattr(model, "UNUSED_LEVEL", "-")):
                    o.Visibility = False
        doc.recompute()
        name = "lna_eval_board.FCStd" if region is None else "lna_rf_em.FCStd"
        doc.saveAs(os.path.join(OUT, name))
        if region is not None and not os.environ.get("PS_EXAMPLE_OUT"):
            import shutil
            shutil.copyfile(os.path.join(OUT, name), SHIPPED)
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


# runs when started as a file (freecadcmd, macro, exec) - not when imported by the tests
if not os.environ.get("PS_EXAMPLE_NO_RUN") and not os.environ.get("PS_LNA_ASSEMBLY_IMPORT"):
    run()
