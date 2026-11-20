# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bond-wire geometry, both bond types, against values worked out by hand.

A chip pad at (0, 0, 0.2), a lead 1 mm out along +x at z 0.1; 25 um wire.

Wedge-wedge (Al, the default): wedges 37.5 um wide (1.5 d), 50 um long
(2 d), tail 12.5 um (0.5 d). The flattened section is a stadium of that
width with round edges and the wire's area, (w - t) t + pi t^2 / 4 =
pi d^2 / 4, which gives t = 14.25 um. Sunk a tenth of t into the pad and cut
flat, it meets the pad over w - t + 2 sqrt((t/2)^2 - (0.4 t)^2) = w - 0.4 t
= 31.8 um.

The heel's bend has a radius of 1.5 d = 37.5 um; leaving at 45 deg it ends
37.5 sin 45 = 26.52 um beyond the heel (x = 25 um) and 37.5 (1 - cos 45) =
10.98 um above the foot's centre line (0.4 t over the pad); landing at
25 deg the second bend starts 37.5 sin 25 = 15.85 um before the second heel
(x = 975 um) and 3.51 um above. The loop's top is 0.15 mm above the first
(higher) exit. Each bend is a quarter-ish arc: 37.5 um x 45 deg = 29.45 um
long.

Ball-stitch (Au): ball 62.5 um (2.5 d), 15 um high, a 12.5 um chamfer cone
on top: the wire leaves at z 0.2 + 0.015 + 0.0125, vertically.
"""

import math

import FreeCAD

from _harness import TestCase

from pkgstudio.core import bondwire as bw

V = FreeCAD.Vector
S, E = (0.0, 0.0, 0.2), (1.0, 0.0, 0.1)
D = 0.025


def _flat_faces(shape, z):
    return [f for f in shape.Faces if f.Surface.__class__.__name__ == "Plane"
            and abs(f.BoundBox.ZMin - z) < 1e-9 and abs(f.BoundBox.ZMax - z) < 1e-9]


def run():
    tc = TestCase("bondwire")
    p = bw.preset("Al", 0.025)
    tc.check("aluminium is wedge-wedge by default", p.bond == bw.WEDGE and p.material == "Al")

    w = 1.5 * D
    t = bw.profile_thickness(D, w)
    tc.near("flattened section keeps the wire's area: (w - t) t + pi t^2 / 4 = pi d^2 / 4",
            (w - t) * t + math.pi * t * t / 4, math.pi * D * D / 4, 1e-15)
    tc.near("... which for a 1.5 d wedge is 14.25 um thick", t, 0.01425, 5e-6)
    tc.near("a section no wider than the wire is the round wire", bw.profile_thickness(D, D), D, 1e-15)

    h = 0.4 * t
    c = bw.centreline(S, E, p)
    a, b = c.value(c.FirstParameter), c.value(c.LastParameter)
    r = 1.5 * D
    want_a = V(0.025 + r * math.sin(math.pi / 4), 0, 0.2 + h + r * (1 - math.cos(math.pi / 4)))
    want_b = V(0.975 - r * math.sin(math.radians(25)), 0, 0.1 + h + r * (1 - math.cos(math.radians(25))))
    tc.check("wedge: the round wire starts where the heel's bend ends (51.52 um, 10.98 um up)",
             (a - want_a).Length < 1e-12, (a, want_a))
    tc.check("wedge: and ends where the second bend starts (959.15 um)", (b - want_b).Length < 1e-12, (b, want_b))
    t0 = c.tangent(c.FirstParameter)[0]
    tc.near("wedge: it leaves at the tool's 45 deg feed angle", math.degrees(t0.getAngle(V(1, 0, 0))), 45.0, 1e-6)
    t1 = c.tangent(c.LastParameter)[0]
    tc.near("wedge: it lands at 25 deg", math.degrees(t1.getAngle(V(1, 0, 0))), 25.0, 1e-6)
    tc.near("wedge: loop top exactly 0.15 mm above the higher exit", bw.highest_point(S, E, p),
            want_a.z + 0.15, 1e-5)
    tc.check("no bend tighter than the wire diameter", bw.min_bend_radius(S, E, p) > p.wire_d,
             bw.min_bend_radius(S, E, p))
    tc.check("the loop never dips below the lower bond", bw.lowest_point(S, E, p) >= 0.1)

    edges = bw.spine(S, E, p)
    tc.check("the spine: foot, bend, loop, bend, foot with tail — one tangent-continuous line",
             len(edges) == 5 and all((edges[i].Vertexes[-1].Point - edges[i + 1].Vertexes[0].Point).Length < 1e-12
                                     and edges[i].tangentAt(edges[i].LastParameter).getAngle(
                                         edges[i + 1].tangentAt(edges[i + 1].FirstParameter)) < 1e-6
                                     for i in range(4)))
    tc.near("the first foot is the wedge's 50 um", edges[0].Length, 0.05, 1e-12)
    tc.near("the heel's bend is 37.5 um x 45 deg = 29.45 um long", edges[1].Length, r * math.pi / 4, 3e-4 * r)
    tc.near("the second foot runs on 12.5 um past the wedge: the tail", edges[4].Length, 0.0625, 1e-12)

    one = bw.solid(S, E, p)
    tc.check("the wire is ONE valid solid — no separate bond blocks", one.ShapeType == "Solid" and one.isValid())
    total = bw.length(S, E, p)
    tc.near("its volume is its length x the wire's cross-section (less the sunk slivers cut off)",
            one.Volume, total * math.pi * D * D / 4, 0.02 * one.Volume)
    first, second = _flat_faces(one, 0.2), _flat_faces(one, 0.1)
    tc.check("each wedge meets its pad in one flat face, in the pad's plane", len(first) == 1 and len(second) == 1,
             (len(first), len(second)))
    if first and second:
        fb, sb = first[0].BoundBox, second[0].BoundBox
        # the section's round edges are a B-spline through 48 points: a few nm off a true arc
        tc.near("... 31.8 um wide: w - 0.4 t", fb.YLength, w - 0.4 * t, 1e-5)
        tc.check("... the first covers its whole 50 um wedge (toe at -25 um, heel at 25 um)",
                 fb.XMin <= -0.025 + 1e-9 and fb.XMax >= 0.025, fb)
        tc.check("... the second its wedge and tail (975 to 1037.5 um)",
                 sb.XMin <= 0.975 and abs(sb.XMax - 1.0375) < 1e-9, sb)
    tc.check("nothing reaches below either pad (to 1 nm, OCCT's tolerance)",
             one.optimalBoundingBox().ZMin >= 0.1 - 1e-6,
             one.optimalBoundingBox().ZMin)

    mirrored = bw.solid(S, (-1.0, 0.0, 0.1), p).optimalBoundingBox()
    ob = one.optimalBoundingBox()
    tc.check("a wire mirrored across the chip centre is the exact mirror image",
             abs(ob.XMax + mirrored.XMin) < 1e-5 and abs(ob.XMin + mirrored.XMax) < 1e-5
             and abs(ob.ZMax - mirrored.ZMax) < 1e-5, (ob, mirrored))
    turned = _flat_faces(bw.solid(S, (0.0, 1.0, 0.1), p), 0.2)
    tc.check("the wedges turn with the wire: lengthwise along +y for a wire to +y",
             len(turned) == 1 and turned[0].BoundBox.YLength > turned[0].BoundBox.XLength)

    # The lead's surface tilted 30 deg (rising toward +x): its wedge lies on it.
    n1 = V(-math.sin(math.radians(30)), 0, math.cos(math.radians(30)))
    tilted = bw.solid(S, E, p, n1=n1)
    on = [f for f in tilted.Faces if f.Surface.__class__.__name__ == "Plane"
          and f.normalAt(0, 0).getAngle(-n1) < 1e-6 and abs((f.CenterOfMass - V(*E)).dot(n1)) < 1e-9]
    tc.check("on a tilted surface the wedge lies flat on it: a face in that plane, facing into it",
             tilted.isValid() and len(on) == 1 and on[0].Area > 0.5 * p.stitch_l * p.stitch_w, len(on))

    fp = bw.footprints(S, E, p)
    tc.check("wedge footprints: stadiums of the set size, along the wire",
             fp["first"][:3] == ("stadium", (0.0, 0.0), (1.0, 0.0)) and abs(fp["first"][3] - 0.05) < 1e-12
             and abs(fp["first"][4] - 0.0375) < 1e-12 and fp["second"][0] == "stadium", fp)

    q = bw.preset("Au", 0.025)
    tc.check("gold is ball-stitch, ball 2.5 d, no tail", q.bond == bw.BALL and abs(q.ball_d - 0.0625) < 1e-15
             and q.tail == 0)
    c = bw.centreline(S, E, q)
    a = c.value(c.FirstParameter)
    tc.check("ball: the wire leaves the cone's top, 0.2 + 15 + 12.5 um",
             (a - V(0, 0, 0.2275)).Length < 1e-12, a)
    tc.check("ball: straight up", c.tangent(c.FirstParameter)[0].getAngle(V(0, 0, 1)) < 1e-9)
    ball = bw.solid(S, E, q)
    tc.check("ball: one fused valid solid, the stitch flat on the lead",
             ball.ShapeType == "Solid" and ball.isValid() and len(_flat_faces(ball, 0.1)) == 1)
    fb = _flat_faces(ball, 0.2)
    tc.near("ball: 62.5 um across, on the pad", fb[0].BoundBox.XLength if fb else 0, 0.0625, 1e-9)
    tc.check("ball footprint is a circle of the ball's radius",
             bw.footprints(S, E, q)["first"] == ("circle", (0.0, 0.0), 0.03125))
    tc.check("scaled(): a 50 um wire keeps the proportions",
             abs(p.scaled(0.05).wedge_w - 0.075) < 1e-15 and abs(p.scaled(0.05).tail - 0.025) < 1e-15)
    return tc.results
