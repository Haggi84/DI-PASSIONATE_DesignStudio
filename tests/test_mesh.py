# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Extruded meshes are exact: closed, outward-facing, and enclosing exactly
area x height — checked against volumes worked out by hand, for a
rectangle, a non-convex L, a clockwise-drawn polygon and a mixed batch.
"""

from collections import Counter

import numpy as np

from _harness import TestCase

from pkgstudio.core import mesh


def closed(tris):
    """Every edge used exactly twice, once in each direction."""
    edges = Counter()
    for a, b, c in tris.tolist():
        for e in ((a, b), (b, c), (c, a)):
            edges[e] += 1
    return all(edges.get((b, a), 0) == n for (a, b), n in edges.items())


def run():
    tc = TestCase("mesh")
    rect = np.array([[0, 0], [2, 0], [2, 1], [0, 1]], float)
    v, t = mesh.extrude([rect], 0.0, 0.5)
    tc.near("2 x 1 rectangle, 0.5 high: volume 1.0", mesh.volume(v, t), 1.0, 1e-12)
    tc.check("rectangle: 12 triangles (2 top, 2 bottom, 8 sides)", len(t) == 12, len(t))
    tc.check("rectangle: closed and consistently oriented", closed(t))

    ell = np.array([[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]], float)
    v, t = mesh.extrude([ell], 1.0, 1.25)
    tc.near("non-convex L of area 3, 0.25 high: volume 0.75", mesh.volume(v, t), 0.75, 1e-12)
    tc.check("L: closed", closed(t))
    tc.check("L: lies between z 1.0 and 1.25", v[:, 2].min() == 1.0 and v[:, 2].max() == 1.25)

    v, t = mesh.extrude([rect[::-1]], 0.0, 0.5)
    tc.near("a clockwise-drawn rectangle still gives +1.0 (outward)", mesh.volume(v, t), 1.0, 1e-12)

    closing = np.vstack([rect, rect[:1]])
    v, t = mesh.extrude([closing], 0.0, 0.5)
    tc.check("a repeated closing vertex is ignored (still 12 triangles)", len(t) == 12, len(t))

    batch = [rect + [5 * i, 0] for i in range(50)] + [ell + [0, 10]]
    v, t = mesh.extrude(batch, 0.0, 0.1)
    tc.near("50 rectangles + an L: volume (50 x 2 + 3) x 0.1", mesh.volume(v, t), 10.3, 1e-9)
    tc.check("batch: closed", closed(t))

    v, t = mesh.extrude([rect], 0.2, 0.2)
    tc.check("zero height gives the flat top face only, facing up",
             len(t) == 2 and np.allclose(v[:, 2], 0.2)
             and np.cross(v[t[0, 1]] - v[t[0, 0]], v[t[0, 2]] - v[t[0, 0]])[2] > 0)

    v, t = mesh.boxes([((0, 0, 0), (1, 2, 3)), ((5, 5, 5), (6, 6, 6))])
    tc.near("boxes: 1x2x3 + 1x1x1 = 7", mesh.volume(v, t), 7.0, 1e-12)

    degenerate = np.array([[0, 0], [1, 0], [2, 0]], float)
    v, t = mesh.extrude([degenerate], 0, 1)
    tc.check("a zero-area polygon is skipped, not meshed", len(t) == 0)
    return tc.results
