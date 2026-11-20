# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Placing a part on a face and a package on a footprint, checked against
positions worked out by hand.
"""

import FreeCAD
import Part

from _harness import TestCase

from pkgstudio.core import placement as pl
from pkgstudio.core import clusters

V = FreeCAD.Vector


def run():
    tc = TestCase("placement")
    chip = Part.makeBox(2, 1, 0.3, V(-1, -0.5, -0.2))          # bottom centre (0, 0, -0.2)
    housing = Part.makeBox(10, 10, 2, V(0, 0, 0)).cut(Part.makeBox(6, 6, 1.5, V(2, 2, 0.5)))
    floor = min((f for f in housing.Faces if abs(f.CenterOfMass.z - 0.5) < 1e-9
                 and f.Area < 40), key=lambda f: f.Area)
    target, n = pl.face_target(floor)
    tc.check("the cavity floor's centre is (5, 5, 0.5), facing up",
             (target - V(5, 5, 0.5)).Length < 1e-9 and n.getAngle(V(0, 0, 1)) < 1e-9, (target, n))
    plc = pl.placement_onto(chip.BoundBox, target, n, 90)
    placed = chip.copy()
    placed.Placement = plc
    bb = placed.BoundBox
    tc.check("the chip's bottom centre lands on the floor centre",
             (pl.bottom_centre(bb) - V(5, 5, 0.5)).Length < 1e-9, bb)
    tc.check("turned 90 degrees: 1 wide in x, 2 long in y",
             abs(bb.XLength - 1) < 1e-9 and abs(bb.YLength - 2) < 1e-9, bb)

    under = pl.placement_onto(chip.BoundBox, V(0, 0, 0), V(0, 0, -1))
    placed = chip.copy()
    placed.Placement = under
    tc.check("onto a downward face: hangs below it, bottom up at the target",
             abs(placed.BoundBox.ZMax - 0.0) < 1e-9, placed.BoundBox)

    pads = [Part.makePlane(1, 0.5, V(x, y, 1.6)) for x, y in ((10, 20), (14, 20), (10, 24), (14, 24))]
    centre, n = pl.faces_target(pads)
    tc.check("four footprint pads: centre of their outline (12, 22.25), on their surface",
             (centre - V(12.5, 22.25, 1.6)).Length < 1e-9, centre)
    try:
        pl.faces_target([pads[0], Part.makePlane(1, 1, V(0, 0, 0), V(1, 0, 0))])
        tc.check("faces facing different ways are refused", False)
    except ValueError:
        tc.check("faces facing different ways are refused", True)

    lo = [(i * 0.001, 0, 0) for i in range(10)] + [(1 + i * 0.001, 0, 0) for i in range(10)]
    hi = [(x + 0.0005, 0.0005, 0.001) for x, _y, _z in lo]
    blocks = clusters.cluster(lo, hi, 0.01)
    tc.check("clusters: two via arrays 1 mm apart are two blocks",
             len(blocks) == 2, blocks)
    return tc.results
