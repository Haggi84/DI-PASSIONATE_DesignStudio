# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Reading a GDS with a technology, and finding its pads.

The layout is built here, in um, with SG13G2 layer numbers:

  * a 200 x 100 die: an outline on 189/4 (a boundary marker, not built);
  * TopMetal2 (134/0): a 30 x 30 pad metal at each of the 4 corners — drawn
    once in a cell PAD and placed 4 times — and a 20 x 20 square at the
    centre with a 10 x 10 slit (134/24) through its middle;
  * a 20 x 20 Passiv opening (9/0) on each pad, labelled "VDD", "GND",
    "GND" and none;
  * a TopMetal2 pin shape (134/2), which is a marker and must not be read.

The die centre (100, 50) becomes the origin.
"""

import os

import gdstk
import numpy as np

from _harness import TestCase, TECH, tmpdir

from pkgstudio.core import layout as lay, pads as pd, tech


def build_gds():
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    pad = lib.new_cell("PAD")
    pad.add(gdstk.rectangle((-15, -15), (15, 15), layer=134, datatype=0))
    pad.add(gdstk.rectangle((-10, -10), (10, 10), layer=9, datatype=0))
    top = lib.new_cell("TOP")
    top.add(gdstk.rectangle((0, 0), (200, 100), layer=189, datatype=4))
    for x, y, label in ((30, 70, "VDD"), (170, 70, "GND"), (170, 30, "GND"), (30, 30, None)):
        top.add(gdstk.Reference(pad, (x, y)))
        if label:
            top.add(gdstk.Label(label, (x, y), layer=134, texttype=25))
    top.add(gdstk.rectangle((90, 40), (110, 60), layer=134, datatype=0))
    top.add(gdstk.rectangle((95, 45), (105, 55), layer=134, datatype=24))
    top.add(gdstk.rectangle((0, 0), (5, 5), layer=134, datatype=2))
    path = os.path.join(tmpdir("layout"), "chip.gds")
    lib.write_gds(path)
    return path


def run():
    tc = TestCase("layout")
    ihp = tech.load(os.path.join(TECH, "IHP-SG13G2"))
    path = build_gds()
    L = lay.read(path, ihp)

    tc.check("the top cell is TOP", L.cell == "TOP", L.cell)
    tc.check("outline: 0.2 x 0.1 mm, centred on the origin",
             np.allclose(L.outline, (-0.1, -0.05, 0.1, 0.05)), L.outline)
    tc.check("the GDS centre is kept", np.allclose(L.origin_gds_mm, (0.1, 0.05)))
    tm2 = L.polygons.get((134, 0), [])
    area = sum(abs(0.5 * np.dot(p[:, 0], np.roll(p[:, 1], -1)) - 0.5 * np.dot(p[:, 1], np.roll(p[:, 0], -1)))
               for p in tm2)
    tc.near("TopMetal2: 4 pad metals of 30x30 + a 20x20 square minus its 10x10 slit (um^2 -> mm^2)",
            area, (4 * 900 + 400 - 100) * 1e-6, 1e-12)
    tc.check("the pin shape 134/2 is not read", (134, 2) not in L.polygons)
    tc.check("the slit layer itself is consumed, not kept", (134, 24) not in L.polygons)
    tc.check("the pad layer 9/0 is read, from all 4 placements", L.count((9, 0)) == 4, L.count((9, 0)))

    P = pd.chip_pads(L, ihp)
    tc.check("4 pads found", len(P) == 4, len(P))
    if len(P) == 4:
        tc.check("pads are 0.02 mm squares", all(abs(p.w - 0.02) < 1e-12 and abs(p.h - 0.02) < 1e-12 for p in P))
        tc.check("each pad sits on top of TopMetal2 (14.2303 um)",
                 all(abs(p.z - 0.0142303) < 1e-12 for p in P), [p.z for p in P])
        tc.check("counter-clockwise from the top-left: VDD, then the unlabelled, then the two GND",
                 [p.name for p in P] == ["VDD", "P2", "GND", "GND_2"], [p.name for p in P])
        tc.check("VDD is at the top-left corner (-0.07, 0.02)",
                 abs(P[0].x + 0.07) < 1e-12 and abs(P[0].y - 0.02) < 1e-12, (P[0].x, P[0].y))
    return tc.results
