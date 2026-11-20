# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Exact OCCT solids of a chip, for STEP — built from the polygons on demand.

These are built only when something is exported, never for the view: OCCT
costs about 0.6 ms per polygon, so a dense via layer takes minutes. Every
drawn polygon becomes a prism of its layer's height, vertex for vertex —
nothing is merged, simplified or replaced by a block — so the export holds
every detail of the layout whatever the view showed.
"""

import FreeCAD
import Part

from . import mesh as _mesh

V = FreeCAD.Vector


def prism(pts, z0, z1):
    """One polygon (n, 2) as a prism from z0 to z1."""
    ring = [V(float(x), float(y), z0) for x, y in _mesh._clean(pts)]
    face = Part.Face(Part.makePolygon(ring + [ring[0]]))
    return face.extrude(V(0, 0, z1 - z0))


def layer_solid(polygons, z0, z1, progress=None):
    """A compound of one prism per polygon. *progress(done, total)* may
    return False to cancel, which returns None."""
    out = []
    total = len(polygons)
    for i, pts in enumerate(polygons):
        try:
            out.append(prism(pts, z0, z1))
        except Part.OCCError:
            continue                     # a degenerate sliver: no volume to lose
        if progress and i % 500 == 0 and progress(i, total) is False:
            return None
    return Part.makeCompound(out) if out else None


def die_body(outline, layers):
    """[(name, solid)] of the silicon under the stack, top layer first:
    *layers* is [(name, material, thickness mm)] (epi, substrate)."""
    x0, y0, x1, y1 = outline
    out, top = [], 0.0
    for name, _material, t in layers:
        if t <= 0:
            continue
        out.append((name, Part.makeBox(x1 - x0, y1 - y0, t, V(x0, y0, top - t))))
        top -= t
    return out
