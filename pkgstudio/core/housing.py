# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Reading a housing: body, die paddle, leads, and where each lead can be
bonded.

A housing model — MirrorSemi's, a generated JEDEC package, or your own STEP
— is a set of solids: one body (moulded or ceramic) and the leadframe's
pieces. Which is which is read from the geometry, not from names:

  body     the solid with the largest footprint; everything else lies inside it
  paddle   the leadframe piece under the body's centre
  leads    pieces that reach exactly one side of the body — a piece reaching
           two sides is a corner tie bar
  finger   a lead's top face where the body does not cover it: the part of
           the lead open to the cavity, which is where a wire can land

Exposed areas are found by lifting each top face by 1 um and cutting it
with the body: whatever the body covers goes, the bondable finger stays.
A closed (fully moulded) package exposes nothing and has no fingers; the
open-cavity ones this workbench is for expose every lead.

Leads are numbered as JEDEC does: pin 1 at the top left, counter-clockwise.
"""

from dataclasses import dataclass, field

import FreeCAD
import Part

from .pads import Pad

V = FreeCAD.Vector
LIFT = 0.001
EDGE_TOL = 0.05


@dataclass
class Analysis:
    body: int = None                       # solid index
    paddle: int = None
    leads: list = field(default_factory=list)
    others: list = field(default_factory=list)
    fingers: list = field(default_factory=list)    # [Pad] in lead order (pin 1 first)
    finger_of: dict = field(default_factory=dict)  # lead solid index -> finger index
    attach: object = None                  # Pad: the exposed paddle top (die attach area)
    centre: tuple = (0.0, 0.0)             # body centre, xy

    @property
    def ok(self):
        return self.body is not None and bool(self.fingers)


def _up_faces_at_top(solid, tol=1e-6):
    top = solid.BoundBox.ZMax
    out = []
    for f in solid.Faces:
        try:
            u0, u1, v0, v1 = f.ParameterRange
            n = f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
        except Exception:
            continue
        if n.z > 0.999 and abs(f.BoundBox.ZMax - f.BoundBox.ZMin) < tol:
            out.append(f)
    if not out:
        return []
    z = max(f.BoundBox.ZMax for f in out)
    return [f for f in out if abs(f.BoundBox.ZMax - z) < tol]


def exposed(solid, body, min_area=1e-6):
    """The parts of *solid*'s top faces the body does not cover, at their own height."""
    out = []
    for f in _up_faces_at_top(solid):
        lifted = f.translated(V(0, 0, LIFT))
        try:
            open_ = lifted.cut(body)
        except Part.OCCError:
            continue
        for g in open_.Faces:
            if g.Area > min_area:
                out.append(g.translated(V(0, 0, -LIFT)))
    return out


def _sides_touched(bb, body_bb, tol=EDGE_TOL):
    """How many sides of the body a piece reaches — or passes, as a
    gull-wing lead does."""
    sides = 0
    sides += bb.XMin <= body_bb.XMin + tol
    sides += bb.XMax >= body_bb.XMax - tol
    sides += bb.YMin <= body_bb.YMin + tol
    sides += bb.YMax >= body_bb.YMax - tol
    return sides


def _pad_from(face, name):
    bb = face.BoundBox
    c = face.CenterOfMass
    return Pad(name=name, x=float(c.x), y=float(c.y), z=float(bb.ZMax),
               w=float(bb.XLength), h=float(bb.YLength),
               outline=[(float(v.Point.x), float(v.Point.y)) for v in face.OuterWire.OrderedVertexes])


def analyse(solids):
    """The Analysis of a housing given as a list of solids (one frame)."""
    a = Analysis()
    if not solids:
        return a
    def foot(s):
        bb = s.BoundBox
        return bb.XLength * bb.YLength
    a.body = max(range(len(solids)), key=lambda i: (foot(solids[i]), solids[i].Volume))
    body = solids[a.body]
    bb = body.BoundBox
    cx, cy = (bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0
    a.centre = (cx, cy)
    rest = [i for i in range(len(solids)) if i != a.body]

    under = [i for i in rest if solids[i].BoundBox.XMin <= cx <= solids[i].BoundBox.XMax
             and solids[i].BoundBox.YMin <= cy <= solids[i].BoundBox.YMax]
    if under:
        a.paddle = max(under, key=lambda i: foot(solids[i]))
        top = exposed(solids[a.paddle], body)
        if top:
            a.attach = _pad_from(max(top, key=lambda f: f.Area), "die attach")

    candidates = []
    for i in rest:
        if i == a.paddle:
            continue
        sides = _sides_touched(solids[i].BoundBox, bb)
        open_ = exposed(solids[i], body) if sides == 1 else []
        if sides == 1 and open_:
            candidates.append((i, max(open_, key=lambda f: f.Area)))
        elif sides == 1:
            a.leads.append(i)            # a lead, but covered: nothing to bond
        else:
            a.others.append(i)
    # JEDEC numbering by side, not by angle: the left side top to bottom,
    # the bottom left to right, the right bottom to top, the top right to
    # left. Ordered by angle round the centre, a corner's leads came out in
    # the wrong order — fingers sit inset from the edge, so the top side's
    # first lead can be "before" the corner the left side starts at.
    def side_key(k):
        i, f = candidates[k]
        lb = solids[i].BoundBox
        c = f.CenterOfMass
        if lb.XMin <= bb.XMin + EDGE_TOL:
            return (0, -c.y)
        if lb.YMin <= bb.YMin + EDGE_TOL:
            return (1, c.x)
        if lb.XMax >= bb.XMax - EDGE_TOL:
            return (2, c.y)
        return (3, -c.x)
    order = sorted(range(len(candidates)), key=side_key)
    for n, k in enumerate(order, 1):
        i, face = candidates[k]
        a.leads.append(i)
        a.finger_of[i] = len(a.fingers)
        a.fingers.append(_pad_from(face, str(n)))
    return a
