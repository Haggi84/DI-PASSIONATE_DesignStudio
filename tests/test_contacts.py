# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Contact points on a surface, and wires landing on them.

The frame: a level face at z = 0 and a chip centred at (1, 2, 5): the frame's
origin is (1, 2, 0), u is +x, v is +y, so the point (1.5, 2, 0) is
(u, v) = (0.5, 0). Turn the chip 90 degrees and u becomes +y. On a face
rising 30 degrees toward +x, u lies along the slope: (cos 30, 0, sin 30).

The document: tests/test_flow.py's chip (centre at the origin, 12 pads,
TopMetal2 pads at z 0.0142303) beside a board whose top face is z = 0,
x 1.5 .. 6, y -3 .. 3. A point clicked at (2, 0.5, 0.3) drops onto the face
at (2, 0.5, 0); one at (0.5, 0, 0) is off the board and refused. Mirrored
across the chip's x axis, (2.5, -0.5) gives (2.5, 0.5); across its y axis
(-2.5, ...) is off the board and is not made.
"""

import math

import FreeCAD
import Part

from _harness import TestCase, new_document, tmpdir

from pkgstudio.app import model, wires
from pkgstudio.core import contacts as ct
import test_flow as tf

V = FreeCAD.Vector


def _near(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def run():
    tc = TestCase("contacts")

    f = ct.face_frame((7, 7, 0), (0, 0, 1), (1, 2, 5))
    tc.check("frame: origin is the chip centre dropped onto the face", _near(f.origin, (1, 2, 0)), f.origin)
    tc.check("frame: u along the chip's x, v along its y", _near(f.u, (1, 0, 0)) and _near(f.v, (0, 1, 0)))
    tc.check("frame: (1.5, 2, 0) is (0.5, 0)", _near(f.to_uv((1.5, 2, 0)), (0.5, 0)), f.to_uv((1.5, 2, 0)))
    tc.check("frame: a point above the face drops onto it", _near(f.project((3, 4, 9)), (3, 4, 0)))
    turned = ct.face_frame((0, 0, 0), (0, 0, 1), (0, 0, 1), x_axis=(0, 1, 0), y_axis=(-1, 0, 0))
    tc.check("frame: a chip turned 90 deg turns u to +y", _near(turned.u, (0, 1, 0)), turned.u)
    s30 = math.radians(30)
    slope = ct.face_frame((0, 0, 0), (-math.sin(s30), 0, math.cos(s30)), (0, 0, 1))
    tc.check("frame: on a face rising 30 deg, u runs up the slope (cos 30, 0, sin 30)",
             _near(slope.u, (math.cos(s30), 0, math.sin(s30))), slope.u)
    tc.check("snap: (12.3, -4.7) um to a 10 um grid is (10, 0)",
             _near(ct.snap((0.0123, -0.0047), 0.01), (0.01, 0.0)))
    tc.check("snap: no grid leaves it", ct.snap((0.0123, 0.5), 0) == (0.0123, 0.5))
    tc.check("mirror both: the three images, in order",
             ct.mirrored([(0.1, 0.2)], "both") == [(0.1, -0.2), (-0.1, 0.2), (-0.1, -0.2)])
    tc.check("mirror: an image already there is not made twice",
             ct.mirrored([(0.1, 0.2), (0.1, -0.2)], "x") == [])

    # A 100 x 50 mm rectangle, turned 30 deg about z, its corner at (10, 20, 3).
    c30, s30_ = math.cos(math.radians(30)), math.sin(math.radians(30))
    u, v, p0 = (c30, s30_, 0.0), (-s30_, c30, 0.0), (10.0, 20.0, 3.0)
    at = lambda a, b: tuple(p0[i] + a * u[i] + b * v[i] for i in range(3))
    corners = [at(0, 0), at(100, 0), at(100, 50), at(0, 50)]
    edges = [tuple(q[i] - p[i] for i in range(3)) for p, q in zip(corners, corners[1:] + corners[:1])]
    fr, w, h = ct.corner_frame(corners, (0, 0, 1), edges)
    tc.check("corner frame: a 100 x 50 rectangle is 100 x 50, X along its long edge",
             abs(w - 100) < 1e-9 and abs(h - 50) < 1e-9 and _near(fr.u, u), (w, h, fr.u))
    tc.check("corner frame: (0, 0) is its corner", _near(fr.origin, p0), fr.origin)
    tc.check("corner frame: X 25.5, Y 10 is exactly 25.5 mm along and 10 mm across",
             _near(fr.to_xyz((25.5, 10)), at(25.5, 10)) and _near(fr.to_uv(at(25.5, 10)), (25.5, 10)))
    tc.check("mirror over X through the middle (y = 25): (25.5, 10) -> (25.5, 40)",
             _near(ct.mirror_uv((25.5, 10), "x", (50, 25)), (25.5, 40)))
    tc.check("mirror over Y through the middle (x = 50): (25.5, 10) -> (74.5, 10)",
             _near(ct.mirror_uv((25.5, 10), "y", (50, 25)), (74.5, 10)))

    # Grid spacing: about ten lines across, 1-2-5 steps; fine lines a fifth (a half for a 2).
    tc.check("grid: a 100 x 50 face gets lines every 10 mm, fine lines every 2 mm",
             ct.grid_steps(100, 50) == (10.0, 2.0), ct.grid_steps(100, 50))
    tc.check("grid: a 30 x 30 face, 5 mm and 1 mm", ct.grid_steps(30, 30) == (5.0, 1.0), ct.grid_steps(30, 30))
    major, minor = ct.grid_steps(4.5, 6)
    tc.check("grid: a 4.5 x 6 face, 1 mm and 0.2 mm", abs(major - 1) < 1e-12 and abs(minor - 0.2) < 1e-12,
             (major, minor))
    tc.check("grid: a set spacing of 2 mm has fine lines every 1 mm", ct.grid_steps(100, 50, 2.0) == (2.0, 1.0))
    tc.check("grid values: 0 .. 4.5 every 0.5 mm, ten of them, ending on the edge",
             len(ct.grid_values(4.5, 0.5)) == 10 and abs(ct.grid_values(4.5, 0.5)[-1] - 4.5) < 1e-12)
    tc.check("grid values: none when there would be too many lines to draw",
             ct.grid_values(1000, 0.001) == [])

    doc = new_document("Contacts")
    chip = model.import_chip(doc, tf.build_chip(tmpdir("contacts")), tf.IHP)
    board = doc.addObject("App::Part", "Board")
    model.set_role(board, model.BOARD)
    slab = doc.addObject("Part::Feature", "PCB")
    slab.Shape = Part.makeBox(4.5, 6, 1.6, V(1.5, -3, -1.6))
    board.addObject(slab)
    doc.recompute()
    top = next(fc for fc in slab.Shape.Faces if abs(fc.CenterOfMass.z) < 1e-9)

    made, off = model.add_contact_points(board, top, [V(2.0, 0.5, 0.3), V(0.5, 0, 0)], 0.1, 0.1, chip, 0.01)
    tc.check("a click above the board drops onto its face; one beside the board is refused",
             len(made) == 1 and len(off) == 1, (len(made), len(off)))
    c1 = made[0]
    tc.check("the point: (2, 0.5, 0), named C1, a package pad facing up",
             (model.pad_centre(c1) - V(2, 0.5, 0)).Length < 1e-9 and c1.PadName == "C1"
             and c1.PadSide == "package" and (model.pad_normal(c1) - V(0, 0, 1)).Length < 1e-12,
             (model.pad_centre(c1), model.pad_normal(c1)))
    tc.check("its table position: (2000, 500) um from the chip centre",
             _near(model.contact_uv(c1), (2.0, 0.5)), model.contact_uv(c1))

    model.move_contact_point(c1, (2.5, -0.5), top)
    tc.check("typed to (2500, -500) um it moves there", (model.pad_centre(c1) - V(2.5, -0.5, 0)).Length < 1e-9)
    try:
        model.move_contact_point(c1, (0.0, 0.0), top)
        tc.check("a typed position off the face is refused", False)
    except ValueError:
        tc.check("a typed position off the face is refused", (model.pad_centre(c1) - V(2.5, -0.5, 0)).Length < 1e-9)

    new = model.mirror_contact_points(board, "x", top)
    tc.check("mirrored across the chip's x axis: C2 at (2.5, 0.5)",
             len(new) == 1 and (model.pad_centre(new[0]) - V(2.5, 0.5, 0)).Length < 1e-9 and new[0].PadName == "C2")
    tc.check("mirrored across its y axis: off the board, so none",
             model.mirror_contact_points(board, "y", top) == [])
    tc.check("contact points are listed in the order they were made",
             [o.PadName for o in model.contact_points(board)] == ["C1", "C2"])

    # The board's top face in its corner frame: x 1.5 .. 6, y -3 .. 3 is 4.5 x 6 from (1.5, -3, 0).
    cf, cw, ch = model.face_corner_frame(top, chip)
    tc.check("the board face from its corner: 4.5 x 6 mm, (0, 0) at (1.5, -3, 0)",
             abs(cw - 4.5) < 1e-9 and abs(ch - 6) < 1e-9 and _near(cf.origin, (1.5, -3, 0)), (cw, ch, cf.origin))
    typed = model.add_contact_point(board, cf, (2.0, 1.0), 0.1, 0.1)
    tc.check("a point typed at X 2, Y 1 sits at (3.5, -2, 0)",
             (model.pad_centre(typed) - V(3.5, -2, 0)).Length < 1e-9, model.pad_centre(typed))
    centre = (cw / 2, ch / 2)
    over_x = model.mirror_points([typed], "x", cf, centre, top)
    tc.check("mirrored over X through the face's middle (Y 3): Y 1 -> 5, at (3.5, 2, 0)",
             len(over_x) == 1 and (model.pad_centre(over_x[0]) - V(3.5, 2, 0)).Length < 1e-9)
    over_y = model.mirror_points([typed], "y", cf, centre, top)
    tc.check("mirrored over Y through the face's middle (X 2.25): X 2 -> 2.5, at (4, -2, 0)",
             len(over_y) == 1 and (model.pad_centre(over_y[0]) - V(4, -2, 0)).Length < 1e-9)
    both = model.mirror_points([typed], "both", cf, centre, top)
    tc.check("mirrored over both: only the third image is new, at (4, 2, 0)",
             len(both) == 1 and (model.pad_centre(both[0]) - V(4, 2, 0)).Length < 1e-9, len(both))
    tc.check("only the selected point is mirrored: the others have no new images",
             len(model.contact_points(board)) == 6, len(model.contact_points(board)))
    model.resize_contact_point(typed, 0.0625, 0.0375)
    bb = typed.Shape.BoundBox
    tc.check("resized to 62.5 x 37.5 um, it stays where it was",
             abs(bb.XLength - 0.0625) < 1e-12 and abs(bb.YLength - 0.0375) < 1e-12
             and (model.pad_centre(typed) - V(3.5, -2, 0)).Length < 1e-9 and abs(typed.PadWidth.Value - 0.0625) < 1e-12)
    model.move_contact_point(typed, (0.5, 0.5), top, cf)
    tc.check("typed in the table as X 0.5, Y 0.5 it moves to (2, -2.5, 0)",
             (model.pad_centre(typed) - V(2, -2.5, 0)).Length < 1e-9 and _near(model.uv_in(typed, cf), (0.5, 0.5)))
    for o in (typed, *over_x, *over_y, *both):
        doc.removeObject(o.Name)

    pad = min(model.pads_of(chip), key=lambda p: (model.pad_centre(p) - V(0.6, 0, 0)).Length)
    w = wires.create(doc, pad, c1)
    doc.recompute()
    sh = w.Shape
    lands = [fc for fc in sh.Faces if fc.Surface.__class__.__name__ == "Plane"
             and abs(fc.BoundBox.ZMin) < 1e-9 and abs(fc.BoundBox.ZMax) < 1e-9]
    tc.check("a wire from a chip pad to the contact point is one valid solid",
             sh.ShapeType == "Solid" and sh.isValid(), sh.ShapeType)
    lb = lands[0].BoundBox if len(lands) == 1 else None
    tc.check("its second wedge lies flat on the board, round the point (2.5, -0.5)",
             lb is not None and lb.XMin <= 2.5 <= lb.XMax and lb.YMin <= -0.5 <= lb.YMax, lb)

    # The board tilted 20 deg about the x axis through (0, 0, 0): its points tilt with it.
    board.Placement = FreeCAD.Placement(V(0, 0, 0), FreeCAD.Rotation(V(1, 0, 0), 20))
    doc.recompute()
    wires.update_all(doc)
    n = model.pad_normal(c1)
    tc.check("a contact point on a tilted board faces where the board's face does",
             (n - FreeCAD.Rotation(V(1, 0, 0), 20).multVec(V(0, 0, 1))).Length < 1e-12, n)
    c = model.pad_centre(c1)
    flat = [fc for fc in w.Shape.Faces if fc.Surface.__class__.__name__ == "Plane"
            and fc.normalAt(0, 0).getAngle(-n) < 1e-6 and abs((fc.CenterOfMass - c).dot(n)) < 1e-9]
    tc.check("... and the wire's wedge lies flat on the tilted face",
             w.Shape.isValid() and len(flat) == 1, len(flat))
    FreeCAD.closeDocument(doc.Name)
    return tc.results
