# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bond pads: where a wire starts or lands.

A chip pad is an opening on the technology's pad layer (IHP's Passiv,
SKY130's pad). The wire does not land on the opening — that is a hole in the
passivation — but on the metal under it, so a pad's height is the top of the
highest conductor whose drawing contains the pad's centre.

Names come from the layout's own text labels where one sits on the pad;
otherwise pads are numbered counter-clockwise starting at the top-left, the
usual pin-1 convention, so a re-import numbers them the same way.
"""

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Pad:
    name: str
    x: float
    y: float
    z: float
    w: float
    h: float
    outline: list = field(default_factory=list)   # [(x, y)] in the pad plane

    @property
    def centre(self):
        return (self.x, self.y, self.z)


def point_in_polygon(x, y, pts):
    """Even-odd test; pts (n, 2)."""
    px, py = pts[:, 0], pts[:, 1]
    qx, qy = np.roll(px, -1), np.roll(py, -1)
    crosses = ((py > y) != (qy > y))
    with np.errstate(divide="ignore", invalid="ignore"):
        xs = px + (y - py) * (qx - px) / (qy - py)
    return bool(np.count_nonzero(crosses & (x < xs)) % 2)


def landing_height(layout, tech, x, y):
    """Top of the highest conductor drawn under (x, y); the stack top if none."""
    conductors = sorted((s for k, s in tech.layers.items()
                         if s.kind == "conductor" and k in layout.polygons),
                        key=lambda s: -s.z1)
    for spec in conductors:
        for pts in layout.polygons[spec.key]:
            if (pts[:, 0].min() <= x <= pts[:, 0].max() and pts[:, 1].min() <= y <= pts[:, 1].max()
                    and point_in_polygon(x, y, pts)):
                return spec.z1
    return tech.stack_top


def ccw_order(points, centre=(0.0, 0.0)):
    """Indices of *points* counter-clockwise, starting at the one nearest the
    top-left direction (135 degrees)."""
    def key(i):
        x, y = points[i][0] - centre[0], points[i][1] - centre[1]
        a = math.degrees(math.atan2(y, x))
        return (a - 135.0) % 360.0
    return sorted(range(len(points)), key=key)


def face_pad(face, name=""):
    """A package pad on a planar Part face: its centre and in-plane size."""
    bb = face.BoundBox
    c = face.CenterOfMass
    return Pad(name=name, x=float(c.x), y=float(c.y), z=float(bb.ZMax),
               w=float(bb.XLength), h=float(bb.YLength),
               outline=[(float(v.Point.x), float(v.Point.y)) for v in face.OuterWire.OrderedVertexes])


def similar_faces(shape, ref, tol=1e-4):
    """
    Faces of *shape* that are the same kind of bond finger as *ref*: equal
    area, facing the same way, at the same height. Selecting one finger of a
    leadframe or a substrate then picks all of them, exactly where the model
    has them — no clicking each one, and no clicking one slightly off.
    """
    def normal(f):
        u0, u1, v0, v1 = f.ParameterRange
        return f.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)

    n_ref, z_ref, a_ref = normal(ref), ref.CenterOfMass.z, ref.Area
    out = []
    for f in shape.Faces:
        if abs(f.Area - a_ref) > tol * max(a_ref, 1.0) or abs(f.CenterOfMass.z - z_ref) > tol:
            continue
        try:
            if normal(f).getAngle(n_ref) > 1e-3:
                continue
        except Exception:
            continue
        out.append(f)
    return out


def chip_pads(layout, tech, pad_layers=None, min_size=0.0):
    """
    The pads of *layout*: one per polygon on the pad layers, at least
    *min_size* mm across, centred on the polygon's bounding box.
    """
    keys = [tuple(k) for k in (pad_layers or tech.pad_layers)]
    found = []
    for key in keys:
        for pts in layout.polygons.get(key, ()):
            x0, y0 = pts.min(axis=0)
            x1, y1 = pts.max(axis=0)
            if min(x1 - x0, y1 - y0) < min_size:
                continue
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            found.append(Pad(name="", x=float(cx), y=float(cy),
                             z=float(landing_height(layout, tech, cx, cy)),
                             w=float(x1 - x0), h=float(y1 - y0),
                             outline=[(float(a), float(b)) for a, b in pts]))

    order = ccw_order([(p.x, p.y) for p in found])
    for n, i in enumerate(order, 1):
        pad = found[i]
        label = next((t for t, lx, ly, _k in layout.labels
                      if abs(lx - pad.x) <= pad.w / 2 and abs(ly - pad.y) <= pad.h / 2), None)
        pad.name = label or f"P{n}"
    # Several pads may carry one label (GND, VDD). Wires name their pads, so
    # names must be unique: repeats become GND, GND_2, GND_3 in pad order.
    seen = {}
    for i in order:
        base = found[i].name
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            found[i].name = f"{base}_{seen[base]}"
    return [found[i] for i in order]
