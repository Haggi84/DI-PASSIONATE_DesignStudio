# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The layout helpers the EM model is built with (core.palace): clipping a
chip's polygons to its EM region, merging via arrays into blocks, and the
largest cell a wavelength allows.

Expected, by hand: clipping a 2 x 2 square at the origin to (1, 1, 3, 3)
leaves area 1; vias of 1 um at 2 um pitch, grown by 0.75 um, touch, and a
row of 10 shrunk back is one 19 x 1 um bar, fill 10 / 19.
"""

import numpy as np

from _harness import TestCase

from pkgstudio.core import palace


def run():
    tc = TestCase("palace")

    sq = np.array([[0, 0], [2, 0], [2, 2], [0, 2]], float)
    got = palace.clip([sq], (1, 1, 3, 3))
    tc.check("clip: a 2x2 square cut to (1,1)-(3,3) leaves one 1x1 square",
             len(got) == 1 and abs(palace.polygon_area(got[0]) - 1.0) < 1e-12, got)
    tc.check("clip: a polygon outside the region is dropped", palace.clip([sq + 10], (1, 1, 3, 3)) == [])
    # Vias 1 x 1 um at 2 um pitch (um -> mm). Grown by 0.75 um they touch,
    # shrunk back the row is one 19 x 1 um bar: fill 10 / 19 = 0.526.
    via = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float) * 1e-3
    row = [via + [2e-3 * i, 0] for i in range(10)]
    m = palace.merge_vias(row)
    tc.check("vias: a row of 10 at 2 um pitch is one array, 19 x 1 um, fill 10/19",
             len(m) == 1 and abs(palace.polygon_area(m[0][0]) - 19e-6) < 1e-12 and abs(m[0][1] - 10 / 19) < 1e-9,
             [(palace.polygon_area(x), f) for x, f in m])
    far = row + [v + [0, 50e-3] for v in row]
    tc.check("vias: two rows 50 um apart stay two arrays", len(palace.merge_vias(far)) == 2)
    ring = [via + [2e-3 * i, 0] for i in range(10)] + [via + [2e-3 * i, 18e-3] for i in range(10)] + \
           [via + [0, 2e-3 * j] for j in range(1, 9)] + [via + [18e-3, 2e-3 * j] for j in range(1, 9)]
    rm = palace.merge_vias(ring)
    tc.check("vias: a ring of 36 stays a ring — far less than its 19 x 19 um box, fill above 0.4",
             sum(palace.polygon_area(x) for x, _f in rm) < 0.25 * 361e-6 and all(f > 0.4 for _x, f in rm),
             [(palace.polygon_area(x), f) for x, f in rm])
    tc.near("max cell: 20 GHz in silicon, 10 per wavelength = 0.4346 mm",
            palace.max_cell(20e9, 11.9, 10), 299792458e3 / 20e9 / 11.9 ** 0.5 / 10, 1e-12)

    return tc.results
