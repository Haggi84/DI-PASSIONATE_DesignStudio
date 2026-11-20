# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Ports drawn on an edge: a rectangle standing on a piece of an edge, reaching
up or down in Z.

The usual port of a chip or a board stands on the end of a trace and reaches
down to the ground under it. Placing that with a face and a centre means
working out the centre and the height by hand; drawing it from the edge
gives both from the geometry. The rectangle stands in the vertical plane
through the edge: its width is along the edge, its height along Z.

Its height can be "to the next edge": the nearest horizontal edge of other
geometry above or below that lies over or under the port's length. That
geometry comes in two kinds, and both are measured exactly:

* B-rep shapes (a board, a housing, a lid, the die body) are cut with the
  port's own vertical rectangle; a cut through a horizontal face is a
  horizontal line, and its Z is a level.
* A chip's layers are meshes in the view, with no edges to cut, so its
  levels come from the GDS polygons themselves: every polygon the port's
  footprint crosses gives its layer's bottom and top.

The body the port starts on is not "next": from the top of a trace, the next
edge below is the ground, not the trace's own underside a copper thickness
away.

No GUI and no document: shapes and polygons in, numbers out.
"""

import math

import numpy as np

from .contacts import FaceFrame

#: below this a port is a line, not a surface; also how close counts as "on"
MIN_EXTENT = 1e-6     # mm

#: the plane is cut this far in front of and behind the port, not in it: a
#: port on a layer's rim lies in that layer's side face, and a cut exactly
#: there is coplanar with it (degenerate). Both sides are cut, so what is
#: under the layer and what reaches past it are both found.
OFFSET = 1e-5         # mm


def _horizontal(a, b):
    """Unit vector from *a* to *b* in the XY plane, and the distance."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < MIN_EXTENT:
        raise ValueError("A port needs two different points along the edge: they are "
                         f"{length:.6f} mm apart.")
    return (dx / length, dy / length, 0.0), length


def point_at_length(start, toward, length):
    """The point *length* mm from *start* towards *toward*, at *start*'s
    height. A port of a typed length still needs a side to go to: the click
    that would have ended it gives the side, the number how far."""
    (ux, uy, _), _ = _horizontal(start, toward)
    length = abs(float(length))
    return (start[0] + ux * length, start[1] + uy * length, start[2])


def frame(start, end, height):
    """
    (FaceFrame, width, |height|) of the port standing on *start*-*end* and
    reaching *height* mm in Z (negative: down).

    The frame's origin is the rectangle's centre, u runs along the edge, v
    straight up and n out of the plane — so the port is the usual w x h
    rectangle centred at (0, 0), and "+Y" is the excitation from its bottom
    edge to its top. The edge is taken at *start*'s height: an edge that
    climbs in Z still gives a vertical port.
    """
    u, width = _horizontal(start, end)
    if abs(height) < MIN_EXTENT:
        raise ValueError("A port needs a height: it is a surface, not a line.")
    v = (0.0, 0.0, 1.0)
    n = (u[1], -u[0], 0.0)                       # u x v
    origin = ((start[0] + start[0] + u[0] * width) / 2.0,
              (start[1] + start[1] + u[1] * width) / 2.0,
              start[2] + height / 2.0)
    return FaceFrame(origin=origin, n=n, u=u, v=v), width, abs(height)


def next_level(levels, direction):
    """The nearest of *levels* (Z offsets from the port's start) above
    (+Z) or below (-Z), skipping the start itself; None if there is none."""
    if direction == "-Z":
        below = [z for z in levels if z < -MIN_EXTENT]
        return max(below) if below else None
    above = [z for z in levels if z > MIN_EXTENT]
    return min(above) if above else None


# ── levels from B-rep shapes ────────────────────────────────────────────────

def _column(start, end, reach):
    import FreeCAD
    xs, ys = (start[0], end[0]), (start[1], end[1])
    return FreeCAD.BoundBox(min(xs) - reach, min(ys) - reach, -1e12,
                            max(xs) + reach, max(ys) + reach, 1e12)


def shape_levels(shapes, start, end, skip_own=True):
    """
    Z offsets from *start* of the horizontal edges of *shapes* (global) in
    the port's plane over its length. Each solid (or, for sheet geometry,
    face) is a body; with *skip_own*, the one the port starts on is left
    out. Bodies whose box misses the port's footprint are not cut.
    """
    import FreeCAD
    import Part
    V = FreeCAD.Vector
    u, _ = _horizontal(start, end)
    normal = V(u[1], -u[0], 0.0)
    a0, b0 = V(*start), V(end[0], end[1], start[2])
    column = _column(start, end, 2 * OFFSET)
    tip = Part.Vertex(a0)
    levels = set()
    for shape in shapes:
        if shape is None or shape.isNull() or not shape.BoundBox.intersect(column):
            continue
        for body in (shape.Solids or shape.Faces):
            if not body.BoundBox.intersect(column):
                continue
            if skip_own:
                try:
                    if body.distToShape(tip)[0] < MIN_EXTENT:
                        continue
                except Exception:
                    pass
            for face in body.Faces:
                box = face.BoundBox
                if not box.intersect(column):
                    continue
                z0, z1 = box.ZMin - 1.0, box.ZMax + 1.0
                for side in (-OFFSET, OFFSET):
                    a, b = a0 + normal * side, b0 + normal * side
                    cut = Part.Face(Part.makePolygon([V(a.x, a.y, z0), V(b.x, b.y, z0), V(b.x, b.y, z1),
                                                      V(a.x, a.y, z1), V(a.x, a.y, z0)]))
                    try:
                        section = face.section(cut)
                    except Exception:
                        continue
                    for edge in section.Edges:
                        if edge.BoundBox.ZLength < MIN_EXTENT and edge.Length > MIN_EXTENT:
                            levels.add(round(edge.BoundBox.ZMin - start[2], 9))
    return sorted(levels)


# ── levels from a chip's polygons ───────────────────────────────────────────

def _inside(poly, x, y):
    """Even-odd point-in-polygon, *poly* an (n, 2) array."""
    px, py = poly[:, 0], poly[:, 1]
    qx, qy = np.roll(px, -1), np.roll(py, -1)
    crosses = (py > y) != (qy > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = px + (y - py) * (qx - px) / (qy - py)
    return bool(np.count_nonzero(crosses & (x < xs)) % 2)


def _segment_hits(poly, a, b):
    """Whether the segment *a*-*b* (x, y) reaches into polygon *poly*."""
    if _inside(poly, *a) or _inside(poly, *b):
        return True
    p = poly
    e = np.roll(p, -1, axis=0) - p
    d = np.array(b) - np.array(a)
    ap = p - np.array(a)
    denom = d[0] * e[:, 1] - d[1] * e[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (ap[:, 0] * e[:, 1] - ap[:, 1] * e[:, 0]) / denom
        s = (ap[:, 0] * d[1] - ap[:, 1] * d[0]) / denom
    return bool(np.any((np.abs(denom) > 1e-18) & (t >= 0) & (t <= 1) & (s >= 0) & (s <= 1)))


def _distance_to_outline(poly, x, y):
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    ll = (ab * ab).sum(axis=1)
    t = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1]) / np.where(ll > 0, ll, 1.0), 0, 1)
    return float(np.min(np.hypot(a[:, 0] + t * ab[:, 0] - x, a[:, 1] + t * ab[:, 1] - y)))


def layout_levels(polygons, spans, start, end, skip_own=True):
    """
    Z offsets from *start* of the layers whose polygons the port's footprint
    crosses: each such polygon gives its layer's bottom and top.

    *polygons* {key: [(n, 2) array]} and *start*, *end* are in the chip's
    own frame (mm); *spans* {key: (z0, z1)}. The footprint is the edge moved
    OFFSET to either side, so a polygon the port stands beside counts, and
    one it only shares a corner with does not. With *skip_own*, the polygon
    the port starts on — whose outline the start lies on, at a height within
    its layer — is left out.
    """
    u, _ = _horizontal(start, end)
    nx, ny = u[1], -u[0]
    ends = (start[0], start[1]), (end[0], end[1])
    # also pulled in at both ends: a polygon that only touches the port's
    # corner is beside it, not under it
    a_in = (ends[0][0] + u[0] * OFFSET, ends[0][1] + u[1] * OFFSET)
    b_in = (ends[1][0] - u[0] * OFFSET, ends[1][1] - u[1] * OFFSET)
    sides = [((a_in[0] + nx * s, a_in[1] + ny * s), (b_in[0] + nx * s, b_in[1] + ny * s))
             for s in (-OFFSET, OFFSET)]
    lo_x, hi_x = min(start[0], end[0]) - 2 * OFFSET, max(start[0], end[0]) + 2 * OFFSET
    lo_y, hi_y = min(start[1], end[1]) - 2 * OFFSET, max(start[1], end[1]) + 2 * OFFSET
    levels = set()
    for key, polys in polygons.items():
        if key not in spans:
            continue
        z0, z1 = spans[key]
        own_layer = z0 - MIN_EXTENT <= start[2] <= z1 + MIN_EXTENT
        for poly in polys:
            poly = np.asarray(poly, dtype=float)
            if len(poly) < 3:
                continue
            if (poly[:, 0].max() < lo_x or poly[:, 0].min() > hi_x
                    or poly[:, 1].max() < lo_y or poly[:, 1].min() > hi_y):
                continue
            if skip_own and own_layer and (_distance_to_outline(poly, *ends[0]) < MIN_EXTENT
                                           or _inside(poly, *ends[0])):
                continue
            if any(_segment_hits(poly, a, b) for a, b in sides):
                levels.add(round(z0 - start[2], 9))
                levels.add(round(z1 - start[2], 9))
    return sorted(levels)


def nearest_side(polygons, x, y, search=0.05):
    """
    ((ax, ay), (bx, by)): the side of any of *polygons* nearest (x, y), or
    None if none is within *search* mm (widened tenfold, twice). This is the
    edge a click on a chip layer means: the layer is a mesh, whose triangle
    edges are not the drawn polygon's.
    """
    for _ in range(3):
        best, best_d = None, None
        for poly in polygons:
            p = np.asarray(poly, dtype=float)
            if len(p) < 2:
                continue
            if (p[:, 0].min() - search > x or p[:, 0].max() + search < x
                    or p[:, 1].min() - search > y or p[:, 1].max() + search < y):
                continue
            a, b = p, np.roll(p, -1, axis=0)
            ab = b - a
            ll = (ab * ab).sum(axis=1)
            t = np.clip(((x - a[:, 0]) * ab[:, 0] + (y - a[:, 1]) * ab[:, 1]) / np.where(ll > 0, ll, 1.0), 0, 1)
            d = np.hypot(a[:, 0] + t * ab[:, 0] - x, a[:, 1] + t * ab[:, 1] - y)
            d[ll <= 0] = np.inf
            i = int(np.argmin(d))
            if best_d is None or d[i] < best_d:
                best_d, best = d[i], ((float(a[i, 0]), float(a[i, 1])), (float(b[i, 0]), float(b[i, 1])))
        if best is not None and best_d <= search:
            return best
        search *= 10.0
    return None
