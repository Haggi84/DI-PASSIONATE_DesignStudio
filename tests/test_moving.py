# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Assemblies and parametric moves, headless.

Expected, by hand:
  * core: a base turned 90° about Z at (1, 2, 0): an offset of 1 mm in X
    goes along the container's X, not the turned object's, so the base
    moves to (2, 2, 0); a turn of 90° about the pivot (1, 0, 0) of the
    object's own frame (the point (1, 3, 0) of the container) leaves that
    point where it is.
  * two 2 x 2 mm chips imported one after the other: the first at x 0
    (it spans -1..1), the second 0.5 mm to its right, centred at
    1 + 0.5 + 1 = 2.5.
  * both moved 0.1 mm in X: x 0.1 and 2.6; their offsets read 0.1.
  * the package turned 90° about Z, a chip in it moved 0.1 mm in X: its
    surface X is the package's X, which is the world's Y: the chip goes
    0.1 mm in global y.
  * two QFN housings: two packages; the second is set 2 mm below the
    first (the first body's y spans -2..2, so the second's centre is at
    -2 - 2 - 2 = -6).
"""

import FreeCAD

from _harness import TestCase, new_document, tmpdir

from pkgstudio.app import housings, model, moving, wires
from pkgstudio.core import jedec, moving as mv
import test_flow as tf

V = FreeCAD.Vector


def _touches(wire, pad):
    s = wire.Shape.copy()
    s.Placement = model.global_placement(wire)
    return s.isInside(model.pad_centre(pad) + V(0, 0, 0.001), 1e-9, True)


def run():
    tc = TestCase("moving")
    base = FreeCAD.Placement(V(1, 2, 0), FreeCAD.Rotation(V(0, 0, 1), 90))
    moved = mv.offset_placement(base, V(1, 0, 0), 1.0, 0.0, 0.0)
    tc.check("core: an X offset goes along the container's X, whatever the object's turn",
             moved.Base.isEqual(V(2, 2, 0), 1e-12), moved.Base)
    turned = mv.offset_placement(base, V(1, 0, 0), 0.0, 0.0, 90.0)
    tc.check("core: a turn leaves the pivot where it is", turned.multVec(V(1, 0, 0)).isEqual(V(1, 3, 0), 1e-12),
             turned.multVec(V(1, 0, 0)))
    both = mv.offset_placement(base, V(1, 0, 0), 0.3, -0.2, 37.0)
    back = mv.rebase(both, V(1, 0, 0), 0.3, -0.2, 37.0)
    tc.check("core: rebase undoes an offset exactly", mv.same(back, base, 1e-12), back)
    tilted = FreeCAD.Placement(V(), FreeCAD.Rotation(V(1, 0, 0), 90))      # standing on a face with normal -Y
    u, v, n = mv.surface_axes(tilted)
    tc.check("core: on an upright surface X stays X and Y is up the surface",
             u.isEqual(V(1, 0, 0), 1e-12) and v.isEqual(V(0, 0, 1), 1e-12) and n.isEqual(V(0, -1, 0), 1e-12),
             (u, v, n))
    tc.near("core: snap to 0.05", mv.snap(0.123, 0.05), 0.1, 1e-12)
    a, b, c = (FreeCAD.BoundBox(0, 0, 0, 2, 2, 1), FreeCAD.BoundBox(1.5, 1.8, 0.5, 3, 3, 2),
               FreeCAD.BoundBox(0, 0, 1, 2, 2, 2))
    hits = mv.overlaps([a, b, c])
    tc.check("core: overlaps — a/b by 0.5 x 0.2, a/c only touch, b/c overlap",
             [(i, j) for i, j, _x, _y in hits] == [(0, 1), (1, 2)] and abs(hits[0][2] - 0.5) < 1e-12
             and abs(hits[0][3] - 0.2) < 1e-12, hits)

    doc = new_document("Moving")
    folder = tmpdir("moving")
    gds = tf.build_chip(folder)
    c1 = model.import_chip(doc, gds, tf.IHP)
    c2 = model.import_chip(doc, gds, tf.IHP)
    tc.check("two chips imported: both loose", model.loose_chips(doc) == [c1, c2])
    tc.near("the first at x 0", model.global_placement(c1).Base.x, 0.0, 1e-12)
    tc.near("the second 0.5 mm to its right, centred at 2.5", model.global_placement(c2).Base.x, 2.5, 1e-12)
    tc.check("side by side: no overlap", moving.overlap_report(doc) == [], moving.overlap_report(doc))

    moving.nudge([c1, c2], 0.1, 0.0)
    tc.check("both moved 0.1 mm in X", abs(model.global_placement(c1).Base.x - 0.1) < 1e-12
             and abs(model.global_placement(c2).Base.x - 2.6) < 1e-12)
    tc.check("their offsets say so", moving.offsets(c1) == (0.1, 0.0, 0.0) and moving.offsets(c2)[0] == 0.1,
             (moving.offsets(c1), moving.offsets(c2)))
    moving.set_offsets(c2, dx=-2.5)
    tc.check("chip 2 moved onto chip 1 (offset typed): the overlap is reported",
             len(moving.overlap_report(doc)) == 1 and c1.Label in moving.overlap_report(doc)[0])
    moving.reset([c2])
    tc.near("back to where placed", model.global_placement(c2).Base.x, 2.5, 1e-12)
    pl = c2.Placement
    c2.Placement = FreeCAD.Placement(pl.Base + V(1, 0, 0), pl.Rotation)        # moved by other means
    moving.nudge([c2], 0.0, 0.2)
    g = model.global_placement(c2).Base
    tc.check("moved by other means, the next offset keeps that move", abs(g.x - 3.5) < 1e-12
             and abs(g.y - 0.2) < 1e-12, g)
    c1.OffsetY = 0.3                       # as typed in the property editor
    moving.OffsetObserver().slotChangedObject(c1, "OffsetY")
    tc.near("an offset typed in the property editor moves the chip", model.global_placement(c1).Base.y, 0.3, 1e-12)

    h1 = housings.from_jedec(doc, jedec.by_name("QFN-16 4x4"))
    h2 = housings.from_jedec(doc, jedec.by_name("QFN-16 4x4"))
    p1, p2 = model.package_of(h1), model.package_of(h2)
    tc.check("two housings: two packages", p1 is not None and p2 is not None and p1.Name != p2.Name
             and len(model.packages(doc)) == 2)
    b1, b2 = model.global_bbox(p1), model.global_bbox(p2)
    tc.near("the second package is set 2 mm below the first", (b2.YMin + b2.YMax) / 2, -6.0, 1e-9)
    housings.place_chip(c1, h1)
    housings.place_chip(c2, h2)
    tc.check("each chip goes into its housing's package", model.package_of(c1) is p1 and model.package_of(c2) is p2)
    pairs = wires.auto_pair(doc)
    tc.check("auto-pair: each chip's 12 pads to the leads of its own package",
             len(pairs) == 24 and all(model.package_of(c) is model.package_of(q) for c, q in pairs),
             len(pairs))
    made = wires.create_all(doc, pairs)
    tc.check("each package keeps its own wires", all(model.package_of(w) is model.package_of(w.ChipPad)
                                                     for w in made))
    p1.Placement = FreeCAD.Placement(V(), FreeCAD.Rotation(V(0, 0, 1), 90))
    doc.recompute()
    before = model.global_placement(c1).Base
    moving.nudge([c1], 0.1, 0.0)
    after = model.global_placement(c1).Base
    tc.check("a chip in a turned package moves along the package's X (the world's Y)",
             abs(after.y - before.y - 0.1) < 1e-9 and abs(after.x - before.x) < 1e-9, (before, after))
    start = {c1.Name: moving.offsets(c1)}
    moving.drag([c1], V(0, 0.2, 0), step=0.05, start=start)
    dragged = model.global_placement(c1).Base
    tc.check("dragged 0.2 mm in global y in the turned package: 0.2 mm along the package's X, offset X 0.3",
             abs(dragged.y - after.y - 0.2) < 1e-9 and abs(moving.offsets(c1)[0] - 0.3) < 1e-9,
             (dragged, moving.offsets(c1)))
    mine = wires.wires_of([c1])
    tc.check("its 12 wires were rebuilt and still stand on both pads",
             len(mine) == 12 and all(_touches(w, w.ChipPad) and _touches(w, w.PackagePad) for w in mine))
    # move to a point: the midpoint of two points, along chosen axes only
    tc.check("midpoint of (0,0,0) and (2,4,6) is (1,2,3)", moving.midpoint([V(0, 0, 0), V(2, 4, 6)]).isEqual(V(1, 2, 3), 1e-12))
    tc.check("XY mask drops Z, YZ drops X", moving.masked(V(1, 2, 3), "XY").isEqual(V(1, 2, 0), 1e-12)
             and moving.masked(V(1, 2, 3), "yz").isEqual(V(0, 2, 3), 1e-12))
    ref = moving.reference_point([c1])
    z0 = model.global_placement(c1).Base.z
    target = ref + V(0.4, -0.3, 0.25)
    delta, lifted = moving.move_to([c1], target, axes="XY")
    now = moving.reference_point([c1])
    tc.check("move to a point, XY: the chip's centre goes to the target in x and y, its z stays",
             abs(now.x - target.x) < 1e-9 and abs(now.y - target.y) < 1e-9 and abs(now.z - ref.z) < 1e-9
             and lifted < 1e-12, (now, target, lifted))
    off = moving.offsets(c1)
    delta, lifted = moving.move_to([c1], target, axes="Z")
    now2 = moving.reference_point([c1])
    tc.check("then Z only: lifted 0.25 mm off its surface, x/y and the offsets unchanged",
             abs(now2.z - target.z) < 1e-9 and abs(lifted - 0.25) < 1e-9 and abs(now2.x - now.x) < 1e-9
             and moving.offsets(c1) == off, (now2, lifted, moving.offsets(c1), off))
    moving.nudge([c1], 0.05, 0.0)
    tc.near("the lift survives the next step (its base moved, not an outside move undone)",
            model.global_placement(c1).Base.z - z0, 0.25, 1e-9)
    moving.translate([c1], V(0, 0, -0.25))
    tc.near("and goes back down by -0.25 in Z", model.global_placement(c1).Base.z, z0, 1e-9)
    others = wires.wires_of([c2])
    tc.check("the other chip's wires are not its", len(others) == 12 and not set(w.Name for w in others)
             & set(w.Name for w in mine))
    FreeCAD.closeDocument(doc.Name)
    return tc.results
