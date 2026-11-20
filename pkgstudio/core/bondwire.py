# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bond-wire geometry as a bonder makes it.

Two bond types:

  wedge–wedge (ultrasonic, aluminium): a wedge bond on the chip pad and one
      on the lead. The wire leaves the first wedge's heel at the tool's wire
      feed angle — never vertically — and enters the second at the landing
      angle; a short tail is left beyond the last wedge.
  ball–stitch (thermosonic, gold or copper): a flattened ball on the chip pad
      with the capillary's chamfer cone on top, the wire leaving it
      vertically, and a crescent stitch on the lead.

Proportions follow MIL-STD-883 TM 2010 (internal visual):

  ball diameter              2.0 – 5.0  x wire diameter   (default 2.5)
  ultrasonic wedge width     1.2 – 3.0  x                 (default 1.5)
  thermosonic stitch width   1.5 – 3.0  x                 (default 2.5)
  wedge / stitch length      1.5 – 6.0  x                 (default 2.0)
  tail                       <= 2       x                 (default 0.5, wedges)

A bond's thickness is not set but follows from the wire it is made of: the
flattened cross-section keeps the wire's area, so a wider wedge is a
thinner one, as on the bonder.

The wire is ONE sweep along one spine: the first foot, a bend out of its
heel, the loop, a bend into the second foot, the second foot and the tail.
The cross-section is the flattened bond on the feet and the round wire in
the loop, changing over the bends — a wedge is the wire squashed, not a
block the wire sits on. The loop is one cubic Bezier, its tangents fixed by
the exit and landing angles and its pull solved so its highest point is
exactly the loop height — it cannot fold back on itself, which a curve
through hand-placed points did. The sweep keeps the wire plane's normal as
binormal, so a wire and its mirror image are mirror images.

Each end lies on its own surface (a pad normal, default +z): a bond on a
tilted face lies flat on it. All lengths in mm.
"""

import math
from dataclasses import dataclass, asdict, replace

import FreeCAD
import Part

V = FreeCAD.Vector
UP = V(0, 0, 1)
# The bend out of a wedge's heel (and into the second bond's) has a radius of
# 1.5 wire diameters: the tool's heel radius, about.
BEND = 1.5
# A foot is sunk this fraction of its thickness into the pad and cut flat, so
# it meets the pad in a face — a round or flattened section only touching a
# plane along a line is what OCCT's booleans and a mesher both fail on.
SINK = 0.1

WEDGE, BALL = "wedge-wedge", "ball-stitch"


@dataclass
class WireParams:
    material: str = "Al"           # key of core.materials.WIRE_MATERIALS
    bond: str = WEDGE
    wire_d: float = 0.025
    loop_h: float = 0.150          # loop top above the higher wire exit
    exit_deg: float = 45.0         # wedge: wire leaves the first bond at the tool's feed angle
    land_deg: float = 25.0         # approach into the second bond, from horizontal
    reach: float = 0.35            # landing approach length, fraction of the span
    ball_d: float = 0.0625         # 2.5 d
    ball_h: float = 0.015          # 0.6 d
    wedge_w: float = 0.0375        # first/ultrasonic wedge width, 1.5 d
    wedge_l: float = 0.050         # 2.0 d
    stitch_w: float = 0.0625       # second bond width: wedge for wedge-wedge, crescent for ball-stitch
    stitch_l: float = 0.050
    tail: float = 0.0125           # 0.5 d

    def as_dict(self):
        return asdict(self)

    def scaled(self, wire_d):
        """These proportions for another wire diameter."""
        f = wire_d / self.wire_d
        return replace(self, wire_d=wire_d, ball_d=self.ball_d * f, ball_h=self.ball_h * f,
                       wedge_w=self.wedge_w * f, wedge_l=self.wedge_l * f,
                       stitch_w=self.stitch_w * f, stitch_l=self.stitch_l * f, tail=self.tail * f)


def preset(material="Al", wire_d=0.025):
    """The usual bond for a wire material: aluminium is wedge-bonded,
    gold, copper and silver ball-bonded."""
    d = wire_d
    if material == "Al":
        return WireParams(material="Al", bond=WEDGE, wire_d=d, ball_d=2.5 * d, ball_h=0.6 * d,
                          wedge_w=1.5 * d, wedge_l=2.0 * d, stitch_w=1.5 * d, stitch_l=2.0 * d,
                          tail=0.5 * d)
    return WireParams(material=material, bond=BALL, wire_d=d, exit_deg=90.0, ball_d=2.5 * d,
                      ball_h=0.6 * d, wedge_w=1.5 * d, wedge_l=2.0 * d, stitch_w=2.5 * d,
                      stitch_l=2.0 * d, tail=0.0)


def flat_thickness(wire_d, width):
    """Thickness of a bond flattened to *width*, keeping the wire's area."""
    return math.pi * wire_d * wire_d / (4.0 * width)


# ── where things are ────────────────────────────────────────────────────────
#
# Every end has its own surface: a pad's normal n (default +z). The wire's
# direction at an end is the other end's direction projected into that
# surface, so a bond lies flat on a tilted face as on a level one.

def _vec(v, default=None):
    if v is None:
        return V(default if default is not None else UP)
    out = V(*v) if not isinstance(v, FreeCAD.Vector) else V(v)
    if out.Length < 1e-12:
        raise ValueError("a pad normal of zero length")
    return out.normalize()


def ends(start, end, n0=None, n1=None):
    """The two ends' frames: s, e, normals n0/n1, in-surface directions
    u0/u1 (toward the other end), the mean normal m, the side vector (the
    wire plane's normal) and the span across m."""
    s, e = V(*start), V(*end)
    n0, n1 = _vec(n0), _vec(n1)
    d = e - s
    u0 = d - n0 * d.dot(n0)
    u1 = d - n1 * d.dot(n1)
    if u0.Length < 1e-9 or u1.Length < 1e-9:
        raise ValueError("the two pads are on top of each other")
    u0.normalize()
    u1.normalize()
    m = n0 + n1
    m = V(n0) if m.Length < 1e-9 else m.normalize()
    across = d - m * d.dot(m)
    um = u0 + u1
    um = V(u0) if um.Length < 1e-9 else um.normalize()
    side = m.cross(um)
    side = V(n0.cross(u0)) if side.Length < 1e-9 else side.normalize()
    return dict(s=s, e=e, n0=n0, n1=n1, u0=u0, u1=u1, m=m, side=side, span=across.Length)


def frame(start, end, n0=None, n1=None):
    """(start, end, u — direction start -> end in the first pad's surface, span)."""
    g = ends(start, end, n0, n1)
    return g["s"], g["e"], g["u0"], g["span"]


def profile_thickness(wire_d, width):
    """Thickness of the flattened bond cross-section — a stadium *width*
    wide with round edges — that keeps the wire's area. A width of one
    diameter or less is the round wire."""
    if width <= wire_d:
        return wire_d
    a, c = math.pi / 4.0 - 1.0, math.pi * wire_d * wire_d / 4.0
    return (-width + math.sqrt(width * width + 4.0 * a * c)) / (2.0 * a)


def _bond_geometry(start, end, p, n0=None, n1=None):
    """Spine points and tangents of the whole wire, both feet included."""
    g = ends(start, end, n0, n1)
    s, e, u0, u1 = g["s"], g["e"], g["u0"], g["u1"]
    n0, n1 = g["n0"], g["n1"]
    d = p.wire_d
    r = BEND * d

    t1_flat = profile_thickness(d, p.stitch_w)
    h1 = (0.5 - SINK) * t1_flat
    heel1 = e - u1 * (p.stitch_l / 2.0) + n1 * h1
    tail_end = e + u1 * (p.stitch_l / 2.0 + p.tail) + n1 * h1
    b_ang = math.radians(p.land_deg)
    b = heel1 - u1 * (r * math.sin(b_ang)) + n1 * (r * (1.0 - math.cos(b_ang)))
    tb = u1 * math.cos(b_ang) - n1 * math.sin(b_ang)
    g.update(heel1=heel1, tail_end=tail_end, b=b, tb=tb, b_ang=b_ang, t1_flat=t1_flat)

    if p.bond == BALL:
        g.update(a=s + n0 * (p.ball_h + 0.5 * d), ta=V(n0))
    else:
        t0_flat = profile_thickness(d, p.wedge_w)
        h0 = (0.5 - SINK) * t0_flat
        toe0 = s - u0 * (p.wedge_l / 2.0) + n0 * h0
        heel0 = s + u0 * (p.wedge_l / 2.0) + n0 * h0
        a_ang = math.radians(p.exit_deg)
        a = heel0 + u0 * (r * math.sin(a_ang)) + n0 * (r * (1.0 - math.cos(a_ang)))
        g.update(toe0=toe0, heel0=heel0, a=a, ta=u0 * math.cos(a_ang) + n0 * math.sin(a_ang),
                 a_ang=a_ang, t0_flat=t0_flat)
    return g


def exit_point(start, end, p, n0=None, n1=None):
    """(point, tangent) where the round wire leaves the first bond: the top
    of the ball's cone, or the end of the heel's bend out of the wedge."""
    g = _bond_geometry(start, end, p, n0, n1)
    return g["a"], g["ta"]


def entry_point(start, end, p, n0=None, n1=None):
    """(point, tangent) where the round wire starts its bend into the second bond."""
    g = _bond_geometry(start, end, p, n0, n1)
    return g["b"], g["tb"]


def _bezier_top(p0, p1, p2, p3):
    return max(((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3
               for t in (i / 400.0 for i in range(401)))


def control_points(start, end, p, n0=None, n1=None):
    g = _bond_geometry(start, end, p, n0, n1)
    a, t0, b, t1, m = g["a"], g["ta"], g["b"], g["tb"], g["m"]
    back = b - t1 * (p.reach * g["span"])
    top = max(a.dot(m), b.dot(m)) + p.loop_h
    lo, hi = 0.0, 20.0 * (top - min(a.dot(m), b.dot(m))) + g["span"]
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _bezier_top(a.dot(m), (a + t0 * mid).dot(m), back.dot(m), b.dot(m)) < top:
            lo = mid
        else:
            hi = mid
    return [a, a + t0 * hi, back, b]


def centreline(start, end, p, n0=None, n1=None):
    """The loop: from where the wire leaves the first bond to where it bends
    into the second."""
    curve = Part.BezierCurve()
    curve.setPoles(control_points(start, end, p, n0, n1))
    return curve


def _bend(p0, t0, p1, t1, angle, radius):
    """A cubic close to the circular arc from p0 (tangent t0) to p1 (tangent t1)."""
    k = 4.0 / 3.0 * math.tan(angle / 4.0) * radius
    c = Part.BezierCurve()
    c.setPoles([p0, p0 + t0 * k, p1 - t1 * k, p1])
    return c.toShape()


def spine(start, end, p, n0=None, n1=None):
    """The wire's whole centreline as edges, tangent-continuous: first foot,
    bend out of its heel, loop, bend into the second foot, second foot and
    tail. For a ball bond it starts at the top of the ball's cone."""
    g = _bond_geometry(start, end, p, n0, n1)
    r = BEND * p.wire_d
    edges = []
    if p.bond != BALL:
        edges.append(Part.LineSegment(g["toe0"], g["heel0"]).toShape())
        edges.append(_bend(g["heel0"], g["u0"], g["a"], g["ta"], g["a_ang"], r))
    edges.append(centreline(start, end, p, n0, n1).toShape())
    edges.append(_bend(g["b"], g["tb"], g["heel1"], g["u1"], g["b_ang"], r))
    edges.append(Part.LineSegment(g["heel1"], g["tail_end"]).toShape())
    return edges


def sample(start, end, p, n=120, n0=None, n1=None):
    """Points along the loop's centreline."""
    c = centreline(start, end, p, n0, n1)
    a, b = c.FirstParameter, c.LastParameter
    return [c.value(a + (b - a) * i / n) for i in range(n + 1)]


def length(start, end, p, n0=None, n1=None):
    """The wire's length, bonds and tail included."""
    total = sum(e.Length for e in spine(start, end, p, n0, n1))
    if p.bond == BALL:
        total += p.ball_h + 0.5 * p.wire_d
    return total


def loop_length(start, end, p, n0=None, n1=None):
    return centreline(start, end, p, n0, n1).length()


def lowest_point(start, end, p, samples=200):
    return min(q.z for q in sample(start, end, p, samples))


def highest_point(start, end, p, samples=400):
    return max(q.z for q in sample(start, end, p, samples))


def min_bend_radius(start, end, p, samples=400):
    curve = centreline(start, end, p)
    a, b = curve.FirstParameter, curve.LastParameter
    k = max(curve.curvature(a + (b - a) * i / samples) for i in range(samples + 1))
    return float("inf") if k <= 0 else 1.0 / k


# ── bond footprints (for the rules) ─────────────────────────────────────────

def footprints(start, end, p, n0=None, n1=None):
    """
    {"first": ..., "second": ...}: each ("circle", (x, y), r) for a ball or
    ("stadium", (x, y), (ux, uy), length, width) for a wedge or stitch, on
    the pad plane — what MIL-STD-883 measures against the pad opening.
    """
    g = ends(start, end, n0, n1)
    s, e, u0, u1 = g["s"], g["e"], g["u0"], g["u1"]
    if p.bond == BALL:
        first = ("circle", (s.x, s.y), p.ball_d / 2.0)
    else:
        first = ("stadium", (s.x, s.y), (u0.x, u0.y), p.wedge_l, p.wedge_w)
    second = ("stadium", (e.x, e.y), (u1.x, u1.y), p.stitch_l, p.stitch_w)
    return {"first": first, "second": second}


# ── the solid ───────────────────────────────────────────────────────────────
#
# One swept solid along the whole spine. Its cross-section is the round wire in
# the loop and the flattened bond on each foot — the same area, as the bonder's
# tool squashes the wire — changing over the bend at each heel, so there is
# no separate bond block for the wire to sit on: before, a stadium block
# under a round wire read as an extra object. Each foot is sunk a tenth of
# its thickness into the pad and cut off at the pad's plane, so it meets the
# surface in a flat face, as a bond does, rather than touching it along a
# line.

SECTION_POINTS = 48


def _stadium_points(half_w, half_t, n):
    """*n* points evenly spaced along a stadium's outline — a segment of
    half-length half_w - half_t thickened by half_t — counter-clockwise from
    its outermost point on +x. A circle is the stadium with no straight part,
    so every section is parametrised alike. Points evenly spaced by angle
    bunched on the flat sides of a thin bond and the interpolated outline
    overshot its round edges: 6 % too little area."""
    a, r = max(half_w - half_t, 0.0), half_t
    arc = math.pi * r
    perim = 2.0 * arc + 4.0 * a
    pts = []
    for i in range(n):
        s = perim * i / n
        # right half-arc (upper quarter), top, left half-arc, bottom, right (lower quarter)
        if s < arc / 2.0:
            th = s / r
            pts.append((a + r * math.cos(th), r * math.sin(th)))
            continue
        s -= arc / 2.0
        if s < 2.0 * a:
            pts.append((a - s, r))
            continue
        s -= 2.0 * a
        if s < arc:
            th = math.pi / 2.0 + s / r
            pts.append((-a + r * math.cos(th), r * math.sin(th)))
            continue
        s -= arc
        if s < 2.0 * a:
            pts.append((-a + s, -r))
            continue
        s -= 2.0 * a
        th = -math.pi / 2.0 + s / r
        pts.append((a + r * math.cos(th), r * math.sin(th)))
    return pts


def _section(centre, tangent, side, width, thick):
    """A closed periodic B-spline cross-section at *centre*, perpendicular to
    *tangent*: *width* across the side vector, *thick* across the other. All
    sections have the same parametrisation, so the sweep between a flat one
    and a round one does not twist."""
    t = V(tangent).normalize()
    sd = V(side) - t * V(side).dot(t)
    sd.normalize()
    up = t.cross(sd).normalize()
    pts = [V(centre) + sd * x + up * y
           for x, y in _stadium_points(width / 2.0, thick / 2.0, SECTION_POINTS)]
    curve = Part.BSplineCurve()
    curve.interpolate(Points=pts, PeriodicFlag=True)
    return Part.Wire(curve.toShape())


def _below(point, u, n, back, ahead, half_side, depth):
    """A box under the pad plane at *point*, from *back* behind to *ahead*
    of it along u: what is cut off the sunk foot."""
    side = n.cross(u).normalize()
    rot = FreeCAD.Rotation(V(u), side, V(n), "ZXY")
    box = Part.makeBox(back + ahead, 2.0 * half_side, depth)
    box.Placement = FreeCAD.Placement(point - u * back - side * half_side - n * depth, rot)
    return box


def _ball(centre, n, p):
    """Flattened ball plus the capillary chamfer's cone up to the wire."""
    c = V(centre)
    ball = Part.makeCylinder(p.ball_d / 2.0, p.ball_h, c, n)
    cone = Part.makeCone(min(0.3 * p.ball_d, p.ball_d / 2.0), p.wire_d / 2.0, 0.5 * p.wire_d,
                         c + n * p.ball_h, n)
    return ball.fuse(cone).removeSplitter()


def _sweep(start, end, p, n0, n1):
    """
    The swept wire, one solid. Each spine edge is swept on its own between
    the sections at its two ends, and the pieces' side faces are sewn into
    one shell. One sweep with six sections along the whole spine failed in
    OCCT (NCollection_Array1::Value) or twisted; a loft through many
    sections wiggled — 70 % too much volume; fusing the five pieces took
    1.4 s a wire, sewing them 0.05 s.
    """
    g = _bond_geometry(start, end, p, n0, n1)
    edges = spine(start, end, p, n0, n1)
    d = p.wire_d
    # A flat section's width lies in its own pad's surface (n x u), so the
    # foot lies flat on a tilted pad too; round sections all start their
    # outline on the wire plane's side vector, so where two pieces meet their
    # sections are the same curve and sew together.
    side, side0, side1 = g["side"], g["n0"].cross(g["u0"]).normalize(), g["n1"].cross(g["u1"]).normalize()
    flat1 = ((p.stitch_w, g["t1_flat"]), side1)
    round_ = ((d, d), side)
    # (section at the start, at the end, binormal of the sweep)
    plan = [(round_, round_, side), (round_, flat1, side1), (flat1, flat1, side1)]
    if p.bond != BALL:
        flat0 = ((p.wedge_w, g["t0_flat"]), side0)
        plan = [(flat0, flat0, side0), (flat0, round_, side0)] + plan
    faces = []
    first = edges[0].valueAt(edges[0].FirstParameter)
    last = edges[-1].valueAt(edges[-1].LastParameter)
    for edge, (a, b, binormal) in zip(edges, plan):
        u0, u1 = edge.FirstParameter, edge.LastParameter
        pipe = Part.BRepOffsetAPI.MakePipeShell(Part.Wire(edge))
        pipe.setBiNormalMode(binormal)
        # Both ends given, always: where the wire is not in one plane, a
        # section carried along by the sweep turned slightly on the way and
        # no longer met the next piece's, which then did not sew to it.
        pipe.add(_section(edge.valueAt(u0), edge.tangentAt(u0), a[1], *a[0]), False, False)
        pipe.add(_section(edge.valueAt(u1), edge.tangentAt(u1), b[1], *b[0]), False, False)
        pipe.build()
        pipe.makeSolid()
        for f in pipe.shape().Faces:
            if f.Surface.__class__.__name__ == "Plane":
                # a cap: keep only the wire's two ends, not where pieces meet
                c, tol = f.CenterOfMass, 0.1 * d     # joints are at least 0.6 d from either end
                if (c - first).Length > tol and (c - last).Length > tol:
                    continue
            faces.append(f)
    shell = Part.Shell(faces)
    shell.sewShape()
    wire = Part.Solid(shell)
    if wire.Volume < 0:
        wire.reverse()
    return wire, g


def solids(start, end, p, n0=None, n1=None):
    """The wire's parts before they are made one: [ball, swept wire] for a
    ball bond, [swept wire] for wedges — the wedges are part of the sweep."""
    wire, g = _sweep(start, end, p, n0, n1)
    return [_ball(g["s"], g["n0"], p), wire] if p.bond == BALL else [wire]


def solid(start, end, p, n0=None, n1=None):
    """The wire as ONE solid, its bonds lying flat on their pads. Falls back
    to the uncut parts as a compound if a boolean ever fails."""
    wire, g = _sweep(start, end, p, n0, n1)
    d, r = p.wire_d, BEND * p.wire_d
    shape = wire
    try:
        cuts = [_below(g["e"], g["u1"], g["n1"], p.stitch_l / 2.0 + r + d, p.stitch_l / 2.0 + p.tail + d,
                       p.stitch_w + d, 2.0 * d)]
        if p.bond != BALL:
            cuts.append(_below(g["s"], g["u0"], g["n0"], p.wedge_l / 2.0 + d, p.wedge_l / 2.0 + r + d,
                               p.wedge_w + d, 2.0 * d))
        shape = shape.cut(cuts)
        if p.bond == BALL:
            shape = shape.fuse(_ball(g["s"], g["n0"], p))
        shape = shape.removeSplitter()
        if shape.isValid() and len(shape.Solids) == 1:
            return shape.Solids[0]
    except Part.OCCError:
        pass
    parts = solids(start, end, p, n0, n1)
    return parts[0] if len(parts) == 1 else Part.makeCompound(parts)
