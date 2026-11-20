# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The package workflow with a library housing, headless.

A 2 x 2 mm chip with 12 pads (tests/test_flow.py's) goes into a generated
QFN-16 4x4 open-cavity package:

  * the housing arrives with 16 package pads and its materials assigned;
  * the chip goes onto the die paddle with nothing selected, on a 25 um die
    attach layer: the paddle top is z 0.2, so the chip's underside lands at
    0.225 and its pads at 0.225 + 0.18375 + 0.0142303;
  * wires default to 25 um Al wedge-wedge and pass MIL-STD-883 class B;
  * a glass lid lands on the body top (z 0.85), centred;
  * the export carries a material manifest.
"""

import json
import os

import FreeCAD

from _harness import TestCase, TECH, new_document, tmpdir

from pkgstudio.app import exporting, housings, materials, model, wires
from pkgstudio.core import jedec, wirerules
import test_flow as tf

V = FreeCAD.Vector


def run():
    tc = TestCase("package flow")
    doc = new_document("PkgLib")
    chip = model.import_chip(doc, tf.build_chip(tmpdir("pkglib")), tf.IHP)
    tc.check("chip layers get their PDK materials on import",
             all(materials.material_of(o).startswith("IHP SG13G2 / ") for o in chip.Group
                 if model.role(o) in (model.LAYER, model.DIE_BODY)),
             [(o.Label, materials.material_of(o)) for o in chip.Group if model.role(o) == model.LAYER])

    h = housings.from_jedec(doc, jedec.by_name("QFN-16 4x4"))
    pads = model.pads_of(h)
    tc.check("housing: 16 package pads, named 1..16", len(pads) == 16 and pads[0].PadName == "1")
    parts = {o.HousingPart: materials.material_of(o) for o in h.Group if hasattr(o, "HousingPart")}
    tc.check("housing: body is mould compound, leadframe copper alloy",
             parts == {"body": "Mould compound (EMC)", "leadframe": "Copper alloy C194 (leadframe)"}, parts)

    housings.place_chip(chip, h, attach_t=0.025)
    bb = model.chip_local_bbox(chip)
    bottom = model.global_placement(chip).multVec(V(0, 0, bb.ZMin))
    tc.check("chip on the paddle, centred, on 25 um of die attach",
             (bottom - V(0, 0, 0.225)).Length < 1e-9, bottom)
    da = model.by_role(doc, "die-attach")
    tc.check("die attach: one layer, the chip's footprint, Ag epoxy",
             len(da) == 1 and materials.material_of(da[0]) == materials.DIE_ATTACH
             and abs(da[0].Shape.BoundBox.XLength - 2.0) < 1e-9)
    tc.near("chip pads at 0.225 + die body + TopMetal2",
            model.pad_centre(model.all_pads(doc, "chip")[0]).z, 0.225 + tf.BODY + tf.TM2_TOP, 1e-9)

    made = wires.create_all(doc, wires.auto_pair(doc))
    tc.check("12 wires, aluminium wedge-wedge by default, material assigned",
             len(made) == 12 and all(w.BondType == "wedge-wedge" and materials.material_of(w)
                                     == "Aluminium 1% Si wire (AlSi1)" for w in made))
    found = wires.check(doc, "B")
    rej, gl = wirerules.summary(found)
    tc.check("the plan passes MIL-STD-883 class B", rej == 0,
             [f"{f.wire}: {f.rule}: {f.message}" for fs in found.values() for f in fs
              if not f.ok and f.severity == "reject"][:6])

    lid = housings.make_lid(doc, h, "Glass", 0.25)
    lb = model.container_shape(lid).BoundBox
    tc.check("lid: on the body top, centred, glass", abs(lb.ZMin - 0.85) < 1e-9
             and abs(lb.XMin + 2.0) < 1e-9 and materials.material_of(lid.Group[0]) == "Borosilicate glass", lb)
    found = wires.check(doc, "B")
    tc.check("with the lid on, the wires still clear it (class B)",
             not any(f.rule == "lid clearance" and not f.ok for fs in found.values() for f in fs))

    out = tmpdir("pkglib_out")
    elems = exporting.elements(doc)
    pick = [e for e in elems if e.name.startswith(("Housing/", "Lid/", "DieAttach/"))] + \
           [e for e in elems if e.name.startswith("Wires/")][:1]
    files = exporting.export(pick, out, "pkg", ("step",))
    man = json.load(open(os.path.join(out, "pkg_materials.json"), encoding="utf-8"))
    rows = {r["element"]: r for r in man["elements"]}
    tc.check("export: a manifest beside the STEP, every element with its material",
             any(f.endswith("pkg_materials.csv") for f in files) and len(rows) == len(pick)
             and all(r.get("material") for r in rows.values()), list(rows))
    wire_row = next(r for k, r in rows.items() if k.startswith("Wires/"))
    tc.check("manifest: the wire's conductivity and conductivity units",
             wire_row["sigma"] == 3.4e7 and man["units"]["sigma"] == "S/m", wire_row)
    FreeCAD.closeDocument(doc.Name)
    return tc.results
