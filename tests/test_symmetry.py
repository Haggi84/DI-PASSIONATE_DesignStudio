# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Pairing pads without crossings, symmetrically.

A square chip with 3 pads per side (12) inside a square ring of 5 package
pads per side (20), both symmetric about both axes. The expected pairing is
worked out by hand, by angle around the centre: a middle chip pad (0, 1) is
at 90 deg and takes the middle package pad (0, 3); an outer one, (0.5, 1),
is at 63.4 deg, nearer the package pad (2, 3) at 56.3 deg than (1, 3) at
71.6 deg — so offsets 0 and +-0.5 on the chip go to 0 and +-2 on the
package.
"""

import itertools
import math

from _harness import TestCase

from pkgstudio.core import symmetry as sym


def ring(per_side, half, pitch):
    """per_side pads on each side of a square of half-width *half*, CCW
    from the top side, pitch apart, centred on each side."""
    offs = [(i - (per_side - 1) / 2.0) * pitch for i in range(per_side)]
    pts = [(o, half) for o in offs] + [(-half, o) for o in offs] + \
          [(o, -half) for o in offs] + [(half, o) for o in offs]
    return pts


def crosses(pairs, chip, pkg, centre=(0, 0)):
    """True if any two wires cross in angular order."""
    def ang(p):
        return math.atan2(p[1] - centre[1], p[0] - centre[0])
    for (a, b), (c, d) in itertools.combinations(pairs, 2):
        x = (ang(chip[a]) - ang(chip[c])) % (2 * math.pi)
        y = (ang(pkg[b]) - ang(pkg[d])) % (2 * math.pi)
        if (x < math.pi) != (y < math.pi):
            return True
    return False


def run():
    tc = TestCase("symmetry")
    chip = ring(3, 1.0, 0.5)
    pkg = ring(5, 3.0, 1.0)
    pairs = sym.pair(chip, pkg)
    tc.check("every chip pad is paired once", sorted(a for a, _ in pairs) == list(range(12)), pairs)
    tc.check("no package pad is used twice", len({b for _, b in pairs}) == len(pairs))
    tc.check("no two wires cross", not crosses(pairs, chip, pkg))
    straight = all(
        (abs(chip[a][1]) == 1.0 and abs(pkg[b][1]) == 3.0 and math.copysign(1, chip[a][1]) == math.copysign(1, pkg[b][1])
         and abs(pkg[b][0] - 4 * chip[a][0]) < 1e-9) or
        (abs(chip[a][0]) == 1.0 and abs(pkg[b][0]) == 3.0 and math.copysign(1, chip[a][0]) == math.copysign(1, pkg[b][0])
         and abs(pkg[b][1] - 4 * chip[a][1]) < 1e-9)
        for a, b in pairs)
    tc.check("each wire goes to the angularly nearest pad on its own side (offset 0.5 -> 2)",
             straight, [(chip[a], pkg[b]) for a, b in pairs])
    rep = sym.symmetry_report(pairs, chip, pkg)
    tc.check("the plan is mirror-symmetric about both axes, wire by wire",
             all(rep["x"]) and all(rep["y"]), rep)

    broken = [(a, b) for a, b in pairs]
    broken[0] = (broken[0][0], (broken[0][1] + 1) % len(pkg))
    rep = sym.symmetry_report(broken, chip, pkg)
    tc.check("moving one wire breaks the symmetry of that wire and its mirrors only",
             not all(rep["x"]) and sum(not ok for ok in rep["x"]) <= 2, rep)

    swapped = sym.pair(pkg, chip)
    tc.check("pairing works the other way round too (more on the first side)",
             sorted((b, a) for a, b in swapped) == pairs, swapped)

    tc.check("mirror about the y axis", sym.mirror((1.0, 2.0), (0.5, 0.0), "y") == (0.0, 2.0))
    pts = sym.mirrored_points([(1.0, 2.0)], (0.0, 0.0))
    tc.check("one point mirrored about both axes gives 4, no duplicates",
             sorted(pts) == sorted([(1.0, 2.0), (1.0, -2.0), (-1.0, 2.0), (-1.0, -2.0)]), pts)
    return tc.results
