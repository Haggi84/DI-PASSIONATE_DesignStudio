# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Ports drawn on an edge, and their height to the next edge.

A stack built by hand: ground 10 x 10 x 0.1 mm (z 0 .. 0.1), a trace
x 0 .. 5, y 4 .. 6, z 0.5 .. 0.535, a lid z 2 .. 2.2. A port on the trace's
end (x = 5, y 4 .. 6) at its top edge, z 0.535:

  * next edge below: the ground's top, 0.1 - 0.535 = -0.435 — not the
    trace's own underside, 0.035 down;
  * next edge above: the lid's underside, 2 - 0.535 = 1.465;
  * from the trace's bottom edge (z 0.5): 0.1 - 0.5 = -0.4.

The port standing on it, 0.435 down: 2 mm wide (y 4 .. 6), centred at
(5, 5, 0.535 - 0.2175) = (5, 5, 0.3175), facing +x (u = +y, v = +z,
n = u x v = +x); "+Y" excites it straight up.

The same as GDS polygons: ground layer 0 .. 0.1, trace layer 0.5 .. 0.535,
lid layer 2 .. 2.2, plus an island at x 8 .. 9 off to the side and a pad
x 5 .. 6, y 6 .. 7 that only touches the port's corner (5, 6). Same levels,
and neither the island nor the corner pad is under the port.

The chip of tests/test_flow.py (IHP SG13G2): Metal1 runs x -0.5 .. 0.5 mm,
y -0.3 .. -0.25, z 1.04 .. 1.46 um (the stackup's Zmin/Zmax). A port on its
end at x = 0.5, from its top edge, reaches down to the silicon's top at
z = 0 (the die body; the oxide is hidden): -0.00146 mm; from its bottom
edge -0.00104. Nothing drawn and shown lies above. Lifting the chip 0.5 mm
changes neither.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document, tmpdir

from pkgstudio.app import model, ports
from pkgstudio.core import edgeport as ep
import test_flow as tf

V = FreeCAD.Vector


def run():
    tc = TestCase("edge ports")
    _frame(tc)
    _shapes(tc)
    _layout(tc)
    _document(tc)
    _chip(tc)
    return tc.results


def _frame(tc):
    fr, w, h = ep.frame((5, 4, 0.535), (5, 6, 0.535), -0.435)
    tc.near("the port is 2 mm wide", w, 2.0)
    tc.near("and 0.435 mm high", h, 0.435)
    tc.check("centred at (5, 5, 0.3175)", (V(*fr.origin) - V(5, 5, 0.3175)).Length < 1e-12, str(fr.origin))
    tc.check("u along the edge (+y), v straight up, n out of it (+x)",
             (V(*fr.u) - V(0, 1, 0)).Length < 1e-12 and (V(*fr.v) - V(0, 0, 1)).Length < 1e-12
             and (V(*fr.n) - V(1, 0, 0)).Length < 1e-12, f"{fr.u} {fr.v} {fr.n}")
    end = ep.point_at_length((1, 1, 0.5), (4, 5, 0.5), 2.0)
    tc.check("a typed 2 mm runs 2 mm towards the click: (1, 1) + 2 * (0.6, 0.8) = (2.2, 2.6)",
             (V(*end) - V(2.2, 2.6, 0.5)).Length < 1e-12, str(end))
    end = ep.point_at_length((0, 0, 0), (0.5, 0, 0), 3.0)
    tc.check("the click only gives the way: 3 mm though it was 0.5 mm off",
             (V(*end) - V(3, 0, 0)).Length < 1e-12, str(end))
    for name, call in (("two points the same", lambda: ep.frame((1, 1, 0), (1, 1, 0), 1.0)),
                       ("no height", lambda: ep.frame((0, 0, 0), (1, 0, 0), 0.0))):
        try:
            call()
            tc.check(f"a port with {name} is refused", False)
        except ValueError:
            tc.check(f"a port with {name} is refused", True)


def _stack():
    return [Part.makeBox(10, 10, 0.1), Part.makeBox(5, 2, 0.035, V(0, 4, 0.5)),
            Part.makeBox(10, 10, 0.2, V(0, 0, 2))]


def _shapes(tc):
    top_a, top_b = (5, 4, 0.535), (5, 6, 0.535)
    levels = ep.shape_levels(_stack(), top_a, top_b)
    tc.near("B-rep: next edge below is the ground's top, not the trace's underside",
            ep.next_level(levels, "-Z"), -0.435)
    tc.near("B-rep: next edge above is the lid's underside", ep.next_level(levels, "+Z"), 1.465)
    tc.check("B-rep: without the lid nothing is above",
             ep.next_level(ep.shape_levels(_stack()[:2], top_a, top_b), "+Z") is None)
    tc.near("B-rep: from the trace's bottom edge, -0.4",
            ep.next_level(ep.shape_levels(_stack(), (5, 4, 0.5), (5, 6, 0.5)), "-Z"), -0.4)
    own = ep.shape_levels(_stack(), top_a, top_b, skip_own=False)
    tc.check("B-rep: not skipping its own body, the underside -0.035 is a level",
             any(abs(z + 0.035) < 1e-9 for z in own), str(own))
    sheet = Part.makePlane(10, 10, V(0, 0, 0.2))
    tc.near("B-rep: a sheet with no thickness at z 0.2 is an edge: -0.335",
            ep.next_level(ep.shape_levels([_stack()[1], sheet], top_a, top_b), "-Z"), -0.335)


def _rect(x0, y0, x1, y1):
    import numpy as np
    return np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=float)


def _layout(tc):
    polygons = {"ground": [_rect(-1, -1, 10, 10)], "trace": [_rect(0, 4, 5, 6)],
                "lid": [_rect(-1, -1, 10, 10)], "island": [_rect(8, 8, 9, 9)],
                "corner": [_rect(5, 6, 6, 7)]}
    spans = {"ground": (0.0, 0.1), "trace": (0.5, 0.535), "lid": (2.0, 2.2),
             "island": (0.2, 0.3), "corner": (1.0, 1.1)}
    levels = ep.layout_levels(polygons, spans, (5, 4, 0.535), (5, 6, 0.535))
    tc.near("polygons: next edge below is the ground's top", ep.next_level(levels, "-Z"), -0.435)
    tc.near("polygons: next edge above is the lid's underside", ep.next_level(levels, "+Z"), 1.465)
    tc.check("polygons: the island off to the side (0.2 .. 0.3) is not under the port",
             not any(abs(z - (0.2 - 0.535)) < 1e-9 or abs(z - (0.3 - 0.535)) < 1e-9 for z in levels),
             str(levels))
    tc.check("polygons: the pad touching only the port's corner (1.0 .. 1.1) is not over it",
             not any(abs(z - (1.0 - 0.535)) < 1e-9 for z in levels), str(levels))
    tc.check("polygons: the trace's own underside is skipped",
             not any(abs(z + 0.035) < 1e-9 for z in levels), str(levels))
    side = ep.nearest_side(polygons["trace"], 4.97, 5.0)
    tc.check("a click just inside the trace's end picks that side, x = 5",
             side is not None and abs(side[0][0] - 5) < 1e-12 and abs(side[1][0] - 5) < 1e-12, str(side))


def _document(tc):
    doc = new_document("EdgePorts")
    board = doc.addObject("App::Part", "Board")
    model.set_role(board, model.BOARD)
    for name, shape in zip(("Ground", "Trace", "Lid"), _stack()):
        o = doc.addObject("Part::Feature", name)
        o.Shape = shape
        board.addObject(o)
    board.Placement.Base = V(0, 0, 1.0)            # everything 1 mm up
    hidden = doc.addObject("Part::Feature", "Hidden")
    hidden.Shape = Part.makeBox(10, 10, 0.1, V(0, 0, 1.3))
    hidden.Visibility = False
    doc.recompute()

    a, b = (5, 4, 1.535), (5, 6, 1.535)
    up, down = ports.next_edge_heights(doc, a, b)
    tc.near("in a document, through the board's placement: down to the ground, -0.435", down, -0.435)
    tc.near("...and up to the lid, 1.465", up, 1.465)

    port = ports.add_edge_port(board, a, b, down)
    g = model.global_placement(port)
    tc.check("the port is a face with no volume, 2 x 0.435 = 0.87 mm^2",
             len(port.Shape.Faces) == 1 and abs(port.Shape.Area - 0.87) < 1e-9
             and port.Shape.Volume < 1e-12, f"{port.Shape.Area}")
    tc.check("it sits in the board, centred at (5, 5, 1.3175)",
             port in board.Group and (g.Base - V(5, 5, 1.3175)).Length < 1e-9, str(g.Base))
    box = port.Shape.copy()
    box.transformShape(model.parent_frame(port).toMatrix())
    bb = box.BoundBox
    tc.check("it spans exactly from the ground's top (z 1.1) to the trace's top (z 1.535), at x = 5",
             abs(bb.ZMin - 1.1) < 1e-9 and abs(bb.ZMax - 1.535) < 1e-9 and bb.XLength < 1e-9
             and abs(bb.YMin - 4) < 1e-9 and abs(bb.YMax - 6) < 1e-9, str(bb))
    d = ports.describe(port)
    tc.check("ports.json: 2 x 0.435 mm, normal +x, excited straight up",
             abs(d["width_mm"] - 2) < 1e-9 and abs(d["height_mm"] - 0.435) < 1e-9
             and (V(*d["normal"]) - V(1, 0, 0)).Length < 1e-9
             and (V(*d["direction"]) - V(0, 0, 1)).Length < 1e-9, str(d))
    up2, down2 = ports.next_edge_heights(doc, a, b)
    tc.near("a port is not an edge for the next port: still -0.435", down2, -0.435)
    FreeCAD.closeDocument(doc.Name)


def _chip(tc):
    doc = new_document("EdgePortsChip")
    chip = model.import_chip(doc, tf.build_chip(tmpdir("edge_ports")), tf.IHP)
    # The import hides the dielectrics through their view, which there is
    # none of here: hide them as the GUI does, or the passivation's top
    # (14.27 um over Metal1's top) is the next edge above.
    for slab in model.by_role(doc, model.DIELECTRIC):
        slab.Visibility = False
    doc.recompute()
    m1_top, m1_bottom = 0.00146, 0.00104
    _up, down = ports.next_edge_heights(doc, (0.5, -0.3, m1_top), (0.5, -0.25, m1_top))
    tc.near("chip: from Metal1's top edge down to the silicon, -1.46 um", down, -m1_top, 1e-9)
    tc.check("chip: nothing shown and drawn above Metal1's end", _up is None, str(_up))
    _up, down = ports.next_edge_heights(doc, (0.5, -0.3, m1_bottom), (0.5, -0.25, m1_bottom))
    tc.near("chip: from Metal1's bottom edge, -1.04 um", down, -m1_bottom, 1e-9)
    chip.Placement.Base = V(0, 0, 0.5)
    doc.recompute()
    _up, down = ports.next_edge_heights(doc, (0.5, -0.3, 0.5 + m1_top), (0.5, -0.25, 0.5 + m1_top))
    tc.near("chip lifted 0.5 mm: still -1.46 um", down, -m1_top, 1e-9)
    FreeCAD.closeDocument(doc.Name)
