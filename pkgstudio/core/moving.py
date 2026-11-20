# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Moving placed things by an offset: the arithmetic, without a document.

Place puts a chip, lid or package exactly — centred on a paddle, a face or
a footprint. Moving it afterwards must not lose that: the place it was put
is kept as its base, and a move is an offset from the base — so far along
X, so far along Y, turned so much — that can be typed, changed and set
back to zero at any time. Several chips moved together each keep their own
base and get the same offset.

The offset is measured in the surface the object sits on (the plane
through its base, normal to its base's Z): X and Y are its container's X
and Y laid into that plane, so on a level paddle or board they are simply
the package's or board's X and Y, whichever way the chip was turned when
placed. A turn is about the base's Z, through the object's bottom centre,
so a chip turns where it stands instead of swinging round its container's
origin.
"""

import FreeCAD

V = FreeCAD.Vector


def surface_axes(base):
    """(u, v, n): the X and Y of a move, and the surface normal, for an
    object whose base placement is *base* (in its container's frame)."""
    n = base.Rotation.multVec(V(0, 0, 1))
    n.normalize()
    u = V(1, 0, 0) - n * n.x            # the container's X, laid into the surface
    if u.Length < 1e-9:                 # standing on a face whose normal is X
        u = V(0, 1, 0) - n * n.y
    u.normalize()
    v = n.cross(u)
    return u, v, n


def offset_placement(base, pivot, dx, dy, angle_deg):
    """
    The placement of an object whose base is *base* when moved *dx*, *dy*
    (mm) along its surface and turned *angle_deg* about the surface normal
    through *pivot* (a point in the object's own frame, its bottom centre).
    """
    u, v, n = surface_axes(base)
    centre = base.multVec(V(pivot))
    move = FreeCAD.Placement(u * float(dx) + v * float(dy), FreeCAD.Rotation(n, float(angle_deg)), centre)
    return move.multiply(base)


def rebase(current, pivot, dx, dy, angle_deg):
    """The base that, with the offset (*dx*, *dy*, *angle_deg*), gives the
    *current* placement — for an object moved by other means since."""
    u, v, n = surface_axes(current)     # the turn does not tilt the normal
    # the pivot moved with the object: undo the turn about where it is now
    centre = current.multVec(V(pivot))
    turn = FreeCAD.Placement(V(), FreeCAD.Rotation(n, -float(angle_deg)), centre)
    unturned = turn.multiply(current)
    return FreeCAD.Placement(unturned.Base - (u * float(dx) + v * float(dy)), unturned.Rotation)


def same(a, b, tol=1e-9):
    return a.Base.isEqual(b.Base, tol) and a.Rotation.isSame(b.Rotation, tol)


def in_surface(base, delta):
    """A move *delta* (a vector in the container's frame) as (dx, dy) along
    the surface of an object with *base*: what it moves along its surface."""
    u, v, _n = surface_axes(base)
    return delta.dot(u), delta.dot(v)


def snap(value, step):
    """*value* to the nearest multiple of *step* (step 0: unchanged)."""
    if not step:
        return float(value)
    return round(float(value) / step) * step


def overlaps(boxes, tol=1e-9):
    """
    [(i, j, wx, wy)] for every pair of the 3-D boxes *boxes* (BoundBoxes)
    that overlap with volume: their overlap's width in x and y, mm. Boxes
    that only touch, or lie one above the other, do not overlap.
    """
    out = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            wx = min(a.XMax, b.XMax) - max(a.XMin, b.XMin)
            wy = min(a.YMax, b.YMax) - max(a.YMin, b.YMin)
            wz = min(a.ZMax, b.ZMax) - max(a.ZMin, b.ZMin)
            if wx > tol and wy > tol and wz > tol:
                out.append((i, j, wx, wy))
    return out
