# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The whole workflow, headless, on parts built here with known dimensions.

  chip     2 x 2 mm, IHP SG13G2. 12 pads (3 per side, 0.5 mm pitch, 0.6 mm
           from the centre): TopMetal2 60 um squares with 40 um Passiv
           openings. A Via1 array of 3,000 vias, which the view shows as
           blocks and the exports must still write one by one.
  housing  8 x 8 x 1 mm, a 4 x 4 x 0.5 mm cavity (floor at z 0.5), and 20
           bond fingers 0.3 x 0.15 x 0.05 mm on the floor, 5 per side, 1.6 mm
           out from the centre, 0.5 mm pitch: tops at z 0.55.
  lid      8 x 8 x 0.2 mm.
  board    30 x 30 x 1.6 mm with a 4-pad footprint (1 x 0.5 x 0.035 mm pads)
           centred on (12, 12); pad tops at z 1.635.

Expected, by hand: the chip's bottom centre lands on (4, 4, 0.5); its pads
sit at 0.5 + 0.18375 (die body) + 0.0142303 (TopMetal2 top); the package's
bottom centre lands on (12, 12, 1.635), carrying chip, wires and lid along.
"""

import os

import FreeCAD
import Part
import gdstk

from _harness import TestCase, TECH, new_document, tmpdir

from pkgstudio.app import exporting, model, placing, wires
from pkgstudio.core import export as ex, pads as pd

V = FreeCAD.Vector
IHP = os.path.join(TECH, "IHP-SG13G2")
BODY, TM2_TOP = 0.18375, 0.0142303


def build_chip(folder):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    top = lib.new_cell("CHIP")
    top.add(gdstk.rectangle((-1000, -1000), (1000, 1000), layer=189, datatype=4))
    k = 0
    for side in range(4):
        for i in (-1, 0, 1):
            x, y = [(i * 500, 600), (-600, i * 500), (i * 500, -600), (600, i * 500)][side]
            top.add(gdstk.rectangle((x - 30, y - 30), (x + 30, y + 30), layer=134, datatype=0))
            top.add(gdstk.rectangle((x - 20, y - 20), (x + 20, y + 20), layer=9, datatype=0))
            k += 1
    top.add(gdstk.rectangle((-500, -300), (500, -250), layer=8, datatype=0))
    for i in range(60):
        for j in range(50):
            x, y = -300 + i * 1.0, -200 + j * 1.0
            top.add(gdstk.rectangle((x, y), (x + 0.5, y + 0.5), layer=19, datatype=0))
    path = os.path.join(folder, "chip.gds")
    lib.write_gds(path)
    return path


def build_steps(folder):
    housing = Part.makeBox(8, 8, 1).cut(Part.makeBox(4, 4, 0.5, V(2, 2, 0.5)))
    fingers = []
    for i in range(5):
        o = (i - 2) * 0.5
        for cx, cy, w, h in ((4 + o, 4 + 1.6, 0.15, 0.3), (4 + o, 4 - 1.6, 0.15, 0.3),
                             (4 + 1.6, 4 + o, 0.3, 0.15), (4 - 1.6, 4 + o, 0.3, 0.15)):
            fingers.append(Part.makeBox(w, h, 0.05, V(cx - w / 2, cy - h / 2, 0.5)))
    paths = {}
    for name, shape in (("housing", Part.makeCompound([housing] + fingers)),
                        ("lid", Part.makeBox(8, 8, 0.2)),
                        ("board", Part.makeCompound([Part.makeBox(30, 30, 1.6)] + [
                            Part.makeBox(1, 0.5, 0.035, V(12 + dx - 0.5, 12 + dy - 0.25, 1.6))
                            for dx, dy in ((-3, -3), (3, -3), (-3, 3), (3, 3))]))):
        paths[name] = os.path.join(folder, name + ".step")
        shape.exportStep(paths[name])
    return paths


def faces_at(shape, z, area, tol=1e-6):
    return [f for f in shape.Faces if abs(f.CenterOfMass.z - z) < tol and abs(f.Area - area) < tol
            and f.normalAt(*f.Surface.parameter(f.CenterOfMass)).z > 0.99]


def run():
    tc = TestCase("flow")
    folder = tmpdir("flow")
    doc = new_document("PkgFlow")
    gds = build_chip(folder)
    steps = build_steps(folder)

    chip = model.import_chip(doc, gds, IHP)
    layers = {(o.GdsLayer, o.GdsDatatype): o for o in chip.Group if model.role(o) == model.LAYER}
    tc.check("chip: TopMetal2, Metal1 and Via1 layers built", set(layers) == {(134, 0), (8, 0), (19, 0)},
             sorted(layers))
    tc.check("chip: the 3,000-via layer is shown as blocks", layers[(19, 0)].Display == "blocks")
    tc.check("chip: Metal1 is shown in detail", layers[(8, 0)].Display == "detail")
    # The displayed mesh passes through a binary STL, which holds 32-bit
    # floats: good to a few parts per million, plenty to look at. Exports
    # never use it — they rebuild from the 64-bit polygons.
    tc.near("chip: Metal1 mesh volume = 1000 x 50 um x 0.42 um (display precision)",
            layers[(8, 0)].Mesh.Volume, 1.0 * 0.05 * (0.00146 - 0.00104), 1e-5 * 2.1e-5)
    chip_pads = model.all_pads(doc, "chip")
    tc.check("chip: 12 pads", len(chip_pads) == 12, len(chip_pads))

    housing = model.import_part(doc, steps["housing"], model.HOUSING)
    lid = model.import_part(doc, steps["lid"], model.LID)
    board = model.import_part(doc, steps["board"], model.BOARD)
    tc.check("housing and lid go into the package; the board does not",
             model.parent_of(housing) is model.package(doc) and model.parent_of(lid) is model.package(doc)
             and model.parent_of(board) is None)

    floor = faces_at(model.container_shape(housing), 0.5, 16 - 0.0)
    floor = floor or [f for f in model.container_shape(housing).Faces
                      if abs(f.CenterOfMass.z - 0.5) < 1e-6 and f.Area > 10]
    tc.check("chip: comes in loose, at the document's top level", chip.getParentGeoFeatureGroup() is None)
    placing.place(chip, floor[:1], onto=housing)
    tc.check("chip: placed on the housing's floor, it belongs to the housing's package",
             model.package_of(chip) is model.package(doc))
    bb = model.chip_local_bbox(chip)
    bottom = model.global_placement(chip).multVec(V((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2, bb.ZMin))
    tc.check("chip: bottom centre on the cavity floor centre (4, 4, 0.5)",
             (bottom - V(4, 4, 0.5)).Length < 1e-9, bottom)
    z_pad = model.pad_centre(chip_pads[0]).z
    tc.near("chip pads now sit at 0.5 + die body + TopMetal2 top", z_pad, 0.5 + BODY + TM2_TOP, 1e-9)

    h_shape = model.container_shape(housing)
    one = faces_at(h_shape, 0.55, 0.3 * 0.15)[0]
    fingers = pd.similar_faces(h_shape, one)
    tc.check("one finger selected finds all 20 alike", len(fingers) == 20, len(fingers))
    pk = model.add_package_pads(housing, fingers)
    tc.check("20 package pads, on the finger tops (z 0.55)",
             len(pk) == 20 and all(abs(model.pad_centre(p).z - 0.55) < 1e-9 for p in pk))

    pairs = wires.auto_pair(doc)
    tc.check("auto-pair: all 12 chip pads paired, each package pad once",
             len(pairs) == 12 and len({q.Name for _c, q in pairs}) == 12)
    rep = wires.symmetry_of(doc, pairs)
    tc.check("auto-pair: symmetric about both chip axes, wire by wire",
             all(rep["x"]) and all(rep["y"]), rep)
    made = wires.create_all(doc, pairs)
    tc.check("12 wires built, all valid", len(made) == 12 and all(w.Shape.isValid() and w.Shape.Solids for w in made))
    def touches(wire, pad):
        """The wire's solid fills the spot 1 um above the pad's centre."""
        s = wire.Shape.copy()
        s.Placement = model.global_placement(wire)
        return s.isInside(model.pad_centre(pad) + V(0, 0, 0.001), 1e-9, True)

    w0 = made[0]
    tc.check("each wire is one solid, standing on both its pads",
             all(len(w.Shape.Solids) == 1 and touches(w, w.ChipPad) and touches(w, w.PackagePad)
                 for w in made))
    lengths = sorted(round(w.WireLength.Value, 9) for w in made)
    tc.check("symmetric plan: the 12 wires come in at most 3 lengths (centre, and the two outer pairs)",
             len(set(lengths)) <= 3, lengths)

    top = faces_at(model.container_shape(housing), 1.0, 64 - 16)
    placing.place(lid, top[:1], onto=housing)
    lb = model.container_shape(lid).BoundBox
    tc.check("lid: on the housing top, centred", abs(lb.ZMin - 1.0) < 1e-9
             and abs((lb.XMin + lb.XMax) / 2 - 4) < 1e-9 and abs((lb.YMin + lb.YMax) / 2 - 4) < 1e-9, lb)

    footprint = faces_at(model.container_shape(board), 1.635, 0.5)
    tc.check("board: 4 footprint pads found", len(footprint) == 4, len(footprint))
    placing.place(model.package(doc), footprint, onto=board)
    tc.check("package on the board's footprint: it becomes part of the board",
             model.package(doc).getParentGeoFeatureGroup() is board)
    hb = model.container_shape(housing).BoundBox
    tc.check("package on the board: housing bottom on the pads, centred on (12, 12)",
             abs(hb.ZMin - 1.635) < 1e-9 and abs((hb.XMin + hb.XMax) / 2 - 12) < 1e-9
             and abs((hb.YMin + hb.YMax) / 2 - 12) < 1e-9, hb)
    tc.near("the chip pads came along", model.pad_centre(chip_pads[0]).z,
            1.635 + 0.5 + BODY + TM2_TOP, 1e-9)
    tc.check("and so did the wires: still standing on both pads after the move",
             all(touches(w, w.ChipPad) and touches(w, w.PackagePad) for w in made))

    elems = exporting.elements(doc)
    names = [e.name for e in elems]
    tc.check("export elements: layers, die body, housing, lid, board, wires",
             any("TopMetal2" in n for n in names) and any("Substrate" in n for n in names)
             and any(n.startswith("Housing/") for n in names) and any(n.startswith("Lid/") for n in names)
             and any(n.startswith("Board/") for n in names) and sum(n.startswith("Wires/") for n in names) == 12,
             names)
    via = next(e for e in elems if e.obj is layers[(19, 0)])
    vs = via.shape()
    tc.check("export: the via layer shown as blocks is written via by via (3,000 solids)",
             len(vs.Solids) == 3000, len(vs.Solids))
    tc.near("export: and exactly (3,000 x 0.5 x 0.5 um x 0.54 um)", vs.Volume,
            3000 * 0.0005 * 0.0005 * 0.00054, 1e-15)
    tc.near("export: layers are where the chip is (Via1 bottom, globally)", vs.BoundBox.ZMin,
            1.635 + 0.5 + BODY + 0.00146, 1e-9)

    out = tmpdir("flow_out")
    pick = [e for e in elems if "TopMetal2" in e.name or e.name.startswith("Lid/")] + \
           [e for e in elems if e.name.startswith("Wires/")][:2]
    files = exporting.export(pick, out, "pkg", ("step", "obj", "msh"), msh_size=0.2)
    tc.check("export: STEP, OBJ and MSH written, with the material manifest (JSON, CSV)",
             sorted(os.path.splitext(f)[1] for f in files) == [".csv", ".json", ".msh", ".obj", ".step"]
             and all(os.path.getsize(f) > 0 for f in files), files)
    step = Part.read(next(f for f in files if f.endswith(".step")))
    tc.check("export STEP: TopMetal2 (12 pads), the lid, 2 wires (each one fused solid)",
             len(step.Solids) == 12 + 1 + 2, len(step.Solids))
    objs = ex.read_obj_counts(next(f for f in files if f.endswith(".obj")))
    tc.check("export OBJ: one group per element", len(objs) == len(pick), sorted(objs))
    names = ex.msh_physical_names(next(f for f in files if f.endswith(".msh")))
    elements = {n: d for n, d in names.items() if not n.startswith("MAT_")}
    tc.check("export MSH: one physical volume per element", len(elements) == len(pick)
             and all(d == 3 for d in elements.values()), names)
    tc.check("export MSH: and one per material (the chip's TopMetal2, the Al wires)",
             "MAT_IHP_SG13G2_TopMetal2" in names and "MAT_Aluminium_1_Si_wire_AlSi1" in names, names)
    FreeCAD.closeDocument(doc.Name)
    return tc.results
