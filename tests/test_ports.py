# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Ports, and exporting a section seen from the top.

A board 10 x 6 x 1.6 mm at the origin. Its front face (y = 0, facing -y) in
its corner frame: X along the board's x edge, Y up its 1.6 mm thickness,
(0, 0) at (0, 0, 0). A 3 x 3 mm wave port centred at X 5, Y 0.8 stands at
(5, 0, 0.8) in the plane y = 0, facing -y, reaching 0.7 mm below the board
and above it: a port may be larger than its face. Moved to X 2 and made
2 x 1 mm, then mirrored over Y through the face's middle (X 5): X 2 -> 8.

The section x 0 .. 5, y -1 .. 7 (from the top) keeps half the board,
5 x 6 x 1.6 = 48 mm^3, and the port at X 2 (x 1 .. 3), 2 mm^2; the port at
X 8 is outside and left out, also of ports.json. The chip of
tests/test_flow.py, cut to x -0.3 .. -0.27, keeps 30 of the 60 columns of
its Via1 array (vias 0.5 um wide at 1 um pitch from x = -0.3): 30 x 50 =
1,500 vias.
"""

import json
import os

import FreeCAD
import Part

from _harness import TestCase, new_document, tmpdir

from pkgstudio.app import exporting, model, ports
from pkgstudio.core import export as ex
import test_flow as tf

V = FreeCAD.Vector


def run():
    tc = TestCase("ports")
    doc = new_document("Ports")
    chip = model.import_chip(doc, tf.build_chip(tmpdir("ports")), tf.IHP)
    board = doc.addObject("App::Part", "Board")
    model.set_role(board, model.BOARD)
    slab = doc.addObject("Part::Feature", "PCB")
    slab.Shape = Part.makeBox(10, 6, 1.6)
    board.addObject(slab)
    doc.recompute()
    front = next(f for f in slab.Shape.Faces if abs(f.CenterOfMass.y) < 1e-9)

    frame, w, h = model.face_corner_frame(front, None)
    tc.check("the board's front face: 10 x 1.6 mm, X along the board, Y up, (0, 0) at its corner",
             abs(w - 10) < 1e-9 and abs(h - 1.6) < 1e-9 and (V(*frame.u) - V(1, 0, 0)).Length < 1e-12
             and (V(*frame.v) - V(0, 0, 1)).Length < 1e-12 and V(*frame.origin).Length < 1e-12,
             (w, h, frame.u, frame.v, frame.origin))

    p1 = ports.add_port(board, frame, (5.0, 0.8), 3.0, 3.0, "wave", 50.0)
    g = model.global_placement(p1)
    tc.check("a 3 x 3 mm wave port centred at X 5, Y 0.8: at (5, 0, 0.8), facing out of the board (-y)",
             (g.Base - V(5, 0, 0.8)).Length < 1e-12 and (g.Rotation.multVec(V(0, 0, 1)) - V(0, -1, 0)).Length < 1e-12)
    pb = p1.Shape.BoundBox
    tc.check("it lies in the face's plane, 3 x 3 mm, reaching beyond the 1.6 mm board edge",
             abs(pb.YMin) < 1e-12 and abs(pb.YMax) < 1e-12 and abs(pb.XLength - 3) < 1e-12
             and abs(pb.ZMin + 0.7) < 1e-12 and abs(pb.ZMax - 2.3) < 1e-12, pb)
    tc.check("named Port1, index 1, 50 ohm", p1.Label == "Port1" and p1.PortIndex == 1 and p1.Impedance == 50.0)

    ports.move_port(p1, (2.0, 0.8), frame)
    ports.resize_port(p1, 2.0, 1.0)
    tc.check("moved to X 2 and made 2 x 1 mm",
             (model.global_placement(p1).Base - V(2, 0, 0.8)).Length < 1e-12
             and abs(p1.Shape.Area - 2.0) < 1e-12, (model.global_placement(p1).Base, p1.Shape.Area))
    made = ports.mirror_ports([p1], "y", frame, (w / 2, h / 2))
    tc.check("mirrored over Y through the face's middle: Port2 at X 8, the same size and type",
             len(made) == 1 and (model.global_placement(made[0]).Base - V(8, 0, 0.8)).Length < 1e-12
             and made[0].PortType == "wave" and abs(made[0].Shape.Area - 2.0) < 1e-12 and made[0].Label == "Port2")
    tc.check("mirroring again makes nothing twice", ports.mirror_ports([p1], "y", frame, (w / 2, h / 2)) == [])

    p3 = ports.add_port(board, frame, (5.0, 0.8), 0.5, 0.2, "lumped", 25.0, "+Y")
    lumped = ports.describe(p3)
    tc.check("a lumped port's field runs along its Y — here up the board edge, (0, 0, 1)",
             lumped["direction"] == [0.0, 0.0, 1.0] and lumped["impedance_ohm"] == 25.0, lumped)
    tc.check("a wave port has no field direction to set", ports.describe(p1)["direction"] is None)
    doc.removeObject(p3.Name)

    names = [e.name for e in exporting.elements(doc)]
    tc.check("ports are export elements of their own", "Ports/Port1" in names and "Ports/Port2" in names, names[-4:])

    # ── a section from the top ──
    region = (0.0, -1.0, 5.0, 7.0)
    out = tmpdir("ports_out")
    pick = [e for e in exporting.elements(doc) if e.name in ("Board/PCB", "Ports/Port1", "Ports/Port2")]
    files = exporting.export(pick, out, "half", ("step",), region=region)
    tc.check("section: STEP, manifest and ports written", any(f.endswith("half_ports.json") for f in files),
             ([e.name for e in pick], [os.path.basename(f) for f in files]))
    step = Part.read(os.path.join(out, "half.step"))
    tc.near("section: half the board, 5 x 6 x 1.6 = 48 mm^3", sum(x.Volume for x in step.Solids), 48.0, 1e-6)
    tc.near("section: and the port inside it, 2 mm^2", sum(f.Area for f in step.Faces
                                                            if abs(f.BoundBox.YMax) < 1e-9 and abs(f.BoundBox.YMin) < 1e-9
                                                            and abs(f.Area - 2.0) < 1e-6), 2.0, 1e-6)
    listed = []
    if os.path.isfile(os.path.join(out, "half_ports.json")):
        with open(os.path.join(out, "half_ports.json"), encoding="utf-8") as fh:
            listed = [p["name"] for p in json.load(fh)["ports"]]
    tc.check("section: ports.json lists only the port inside it", listed == ["Port1"], listed)
    with open(os.path.join(out, "half.step"), encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    tc.check("section: the port outside is not in the STEP", "'Ports/Port1'" in text and "'Ports/Port2'" not in text)

    via = next(e for e in exporting.elements(doc) if e.kind == "layer" and e.obj.GdsLayer == 19)
    cut = via.shape(region=(-0.3, -1.0, -0.27, 1.0))
    tc.check("section: a chip layer is cut polygon by polygon — 30 of 60 via columns, 1,500 vias",
             cut is not None and len(cut.Solids) == 1500, len(cut.Solids) if cut else None)
    v, t = via.mesh(region=(-0.3, -1.0, -0.27, 1.0))
    tc.check("section: and meshed the same for OBJ — 12 triangles a via", len(t) == 1500 * 12, len(t))
    tc.check("section: an element wholly outside is left out",
             via.shape(region=(5, 5, 6, 6)) is None and len(via.mesh(region=(5, 5, 6, 6))[1]) == 0)
    FreeCAD.closeDocument(doc.Name)
    return tc.results
