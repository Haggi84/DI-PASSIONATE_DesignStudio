# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The MIL-STD-883 TM 2010 checks, each made to pass and to fail on purpose.

Wires are 25 um Al wedge-wedge unless said otherwise; pads are squares.
Expected outcomes are worked out from the clause numbers in core.wirerules:
class B wants 1 d (25 um) between wires, class S 2 d (50 um) — except
within 5 mil of the bond on the die, where class S accepts 1 mil.
"""

from _harness import TestCase

from pkgstudio.core import bondwire as bw, wirerules as wr


def square(cx, cy, half):
    return [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half), (cx - half, cy + half)]


def wire(name, s, e, p=None, chip_half=0.04, lead_half=0.08):
    p = p or bw.preset("Al", 0.025)
    return wr.WireSpec(name, s, e, p, square(s[0], s[1], chip_half), square(e[0], e[1], lead_half),
                       f"c-{name}", f"l-{name}")


def rejects(found, name, rule=None):
    return [f for f in found[name] if not f.ok and f.severity == "reject" and (rule is None or f.rule == rule)]


def run():
    tc = TestCase("wirerules")
    good = [wire("A", (0, 0, 0.2), (1, 0, 0.1)), wire("B", (0, 0.3, 0.2), (1, 0.3, 0.1))]
    found = wr.check(good, cls="B")
    tc.check("two well-spaced, well-sized wires: no rejects", not rejects(found, "A") and not rejects(found, "B"),
             [f.message for f in rejects(found, "A") + rejects(found, "B")])
    tc.check("every finding quotes its clause",
             all(f.ref.startswith(wr.MIL) for fs in found.values() for f in fs if f.severity == "reject"))

    close = [wire("A", (0, 0, 0.2), (1, 0, 0.1)), wire("B", (0, 0.03, 0.2), (1, 0.03, 0.1), chip_half=0.012)]
    found = wr.check(close, cls="B")
    tc.check("30 um apart (5 um between surfaces): class B rejects (needs 25 um)",
             rejects(found, "A", "wire to wire"))

    converge = [wire("A", (0, 0, 0.2), (1, 0, 0.1)), wire("B", (0, 0.3, 0.2), (1, 0.06, 0.1), lead_half=0.02)]
    fb, fs = wr.check(converge, cls="B"), wr.check(converge, cls="S")
    tc.check("closing to 35 um between surfaces far from the die: class B accepts (>= 25 um)",
             not rejects(fb, "A", "wire to wire"), [f.message for f in fb["A"] if f.rule == "wire to wire"])
    tc.check("...class S rejects (>= 50 um away from the die bond)", rejects(fs, "A", "wire to wire"))

    bad = bw.preset("Au", 0.025)
    bad.ball_d = 1.5 * bad.wire_d
    found = wr.check([wire("A", (0, 0, 0.2), (1, 0, 0.1), bad)])
    tc.check("a ball of 1.5 d is rejected (2.0 - 5.0 d)", rejects(found, "A", "ball diameter"))
    wide = bw.preset("Al", 0.025)
    wide.wedge_w = 3.5 * wide.wire_d
    found = wr.check([wire("A", (0, 0, 0.2), (1, 0, 0.1), wide, chip_half=0.1)])
    tc.check("a wedge 3.5 d wide is rejected (1.2 - 3.0 d)", rejects(found, "A", "first bond size"))
    tail = bw.preset("Al", 0.025)
    tail.tail = 3 * tail.wire_d
    found = wr.check([wire("A", (0, 0, 0.2), (1, 0, 0.1), tail)])
    tc.check("a tail of 3 d is rejected (<= 2 d)", rejects(found, "A", "tail"))

    # A 62.5 um ball whose centre is 15 um from the edge of a 50 um pad: about 60 % on the pad.
    ball = bw.preset("Au", 0.025)
    spec = wr.WireSpec("A", (0.010, 0, 0.2), (1, 0, 0.1), ball, square(0, 0, 0.025), square(1, 0, 0.08))
    f = wr.fraction_inside(bw.footprints(spec.start, spec.end, ball)["first"], spec.chip_pad)
    tc.check("a ball 10 um off a 50 um pad's centre is about 60 % on it", 0.5 < f < 0.75, f)
    tc.check("...class B accepts it (>= 50 %)", not rejects(wr.check([spec], cls="B"), "A", "bond on the die pad"))
    tc.check("...class S rejects it (>= 75 %)", rejects(wr.check([spec], cls="S"), "A", "bond on the die pad"))

    top = bw.highest_point((0, 0, 0.2), (1, 0, 0.1), bw.preset("Al", 0.025))
    env = wr.Env(lid_z=top + 0.0125 + 0.010)
    found = wr.check([wire("A", (0, 0, 0.2), (1, 0, 0.1))], env, cls="B")
    tc.check("a lid 10 um above the wire is rejected (class B needs 1 d)", rejects(found, "A", "lid clearance"))

    cross = [wire("A", (0, 0, 0.2), (1, 0.4, 0.1)), wire("B", (0, 0.4, 0.2), (1, 0, 0.1))]
    found = wr.check(cross)
    tc.check("two wires crossing seen from above are rejected", rejects(found, "A", "crossing"))

    long_ = wr.check([wire("A", (0, 0, 0.2), (6, 0, 0.1))])
    gl = [f for f in long_["A"] if not f.ok and f.rule == "wire length"]
    tc.check("a 6 mm wire is a guideline warning (<= 200 d = 5 mm), not a reject",
             gl and gl[0].severity == "guideline" and not rejects(long_, "A", "wire length"))
    return tc.results
