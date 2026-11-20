# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Technologies: which GDS layers are built, at what height.

Heights are read off the stackup files by hand: SG13G2's TopMetal2 runs
from 11.2303 to 14.2303 um, its die body is 3.75 um epi on 180 um substrate;
SKY130's met5 is 72/20 and its body 300 um.
"""

import os

from _harness import TestCase, TECH

from pkgstudio.core import tech


def run():
    tc = TestCase("tech")
    names = [n for n, _f in tech.presets(TECH)]
    tc.check("both bundled technologies are found",
             any("SG13G2" in n for n in names) and any("SKY130" in n for n in names), names)

    ihp = tech.load(os.path.join(TECH, "IHP-SG13G2"))
    tm2 = ihp.layers.get((134, 0))
    tc.check("SG13G2: TopMetal2.drawing is built", tm2 is not None)
    if tm2:
        tc.near("SG13G2: TopMetal2 bottom 11.2303 um", tm2.z0, 0.0112303)
        tc.near("SG13G2: TopMetal2 top 14.2303 um", tm2.z1, 0.0142303)
        tc.check("SG13G2: TopMetal2 is a conductor", tm2.kind == "conductor")
    tc.check("SG13G2: metal fill (134/22) is built — fill is metal", (134, 22) in ihp.layers)
    tc.check("SG13G2: a pin shape (134/2) is not built — it is a marker", (134, 2) not in ihp.layers)
    tc.check("SG13G2: a label (8/1) is not built", (8, 1) not in ihp.layers)
    v1 = ihp.layers.get((19, 0))
    tc.check("SG13G2: Via1 is a via from 1.46 to 2.00 um",
             v1 is not None and v1.kind == "via" and abs(v1.z0 - 0.00146) < 1e-12
             and abs(v1.z1 - 0.002) < 1e-12)
    tc.check("SG13G2: the Metal1 slit (8/24) cuts Metal1 drawing and fill",
             set(ihp.cuts.get((8, 24), [])) == {(8, 0), (8, 22)}, ihp.cuts.get((8, 24)))
    tc.near("SG13G2: die body 3.75 + 180 um", ihp.body_thickness, 0.18375)
    tc.check("SG13G2: pads are the Passiv openings 9/0", ihp.pad_layers == [(9, 0)])
    tc.check("SG13G2: nothing below the die surface is built (ground planes)",
             all(s.z0 >= 0 for s in ihp.layers.values()))

    sky = tech.load(os.path.join(TECH, "SKY130"))
    m5 = sky.layers.get((72, 20))
    tc.check("SKY130: met5 72/20 is built from its exact stackup entry", m5 is not None and m5.level == "met5")
    tc.check("SKY130: the via above met1 (68/44) is a via, not met1",
             (68, 44) in sky.layers and sky.layers[(68, 44)].kind == "via")
    tc.near("SKY130: die body 2 + 298 um", sky.body_thickness, 0.300)
    tc.check("SKY130: pads are pad.drawing 76/20", sky.pad_layers == [(76, 20)])
    return tc.results
