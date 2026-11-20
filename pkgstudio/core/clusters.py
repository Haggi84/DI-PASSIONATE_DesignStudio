# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Proximity clusters of boxes — how a dense via layer is SHOWN.

A via layer can hold hundreds of thousands of cuts. Showing each one is
pointless at any zoom where the layer is visible as a whole, and costly. A
cluster is a group of boxes each within *gap* of another (single linkage);
one block per cluster keeps separate arrays separate, which one box per layer
would not. This only decides the display: the exports always write every via.

The method (from DI-PASSIONATE, where it replaced a 147 s pairwise version):
boxes go into a grid of cells at least *gap* wide, so touching boxes share or
neighbour a cell; a cell whose boxes all touch one another is merged in one
step, decided from its extremes; neighbouring cells are compared as whole
NumPy arrays, only while not yet connected.
"""

import math
from collections import defaultdict

import numpy as np

DEFAULT_GAP_MM = 0.01


def _touching(lo, hi, gap):
    LO, HI = np.asarray(lo, dtype=float), np.asarray(hi, dtype=float)

    def pairs(rows, cols, first_only=False):
        a_lo, a_hi, b_lo, b_hi = LO[rows], HI[rows], LO[cols], HI[cols]
        ok = np.ones((len(rows), len(cols)), dtype=bool)
        for ax in range(3):
            ok &= a_lo[:, ax, None] - gap <= b_hi[None, :, ax]
            ok &= a_hi[:, ax, None] + gap >= b_lo[None, :, ax]
        if first_only:
            flat = int(np.argmax(ok))
            return ((flat // ok.shape[1],), (flat % ok.shape[1],)) if ok.flat[flat] else ((), ())
        return np.nonzero(ok)
    return pairs


def cluster(lo, hi, gap=DEFAULT_GAP_MM):
    """[(lo, hi)] — the combined extent of each cluster of the boxes given as
    parallel lists of (x, y, z) minima and maxima."""
    n = len(lo)
    if not n:
        return []
    sizes = sorted(max(h[0] - l[0], h[1] - l[1]) for l, h in zip(lo, hi))
    cell = max(gap, sizes[n // 2], 1e-6)

    grid = defaultdict(list)
    for i in range(n):
        for cx in range(math.floor(lo[i][0] / cell), math.floor(hi[i][0] / cell) + 1):
            for cy in range(math.floor(lo[i][1] / cell), math.floor(hi[i][1] / cell) + 1):
                grid[(cx, cy)].append(i)

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    touching = _touching(lo, hi, gap)
    whole = {}
    for key, idxs in grid.items():
        if len(idxs) == 1 or all(max(lo[i][a] for i in idxs) - gap <= min(hi[i][a] for i in idxs)
                                 for a in range(3)):
            for i in idxs[1:]:
                union(idxs[0], i)
            whole[key] = True
        else:
            for a, b in zip(*touching(idxs, idxs)):
                union(idxs[a], idxs[b])
            whole[key] = False

    for (cx, cy), idxs in grid.items():
        for nkey in ((cx + 1, cy - 1), (cx + 1, cy), (cx + 1, cy + 1), (cx, cy + 1)):
            other = grid.get(nkey)
            if other is None:
                continue
            if whole[(cx, cy)] and whole[nkey]:
                if find(idxs[0]) == find(other[0]):
                    continue
                found = touching(idxs, other, first_only=True)
            else:
                found = touching(idxs, other)
            for a, b in zip(*found):
                union(idxs[a], other[b])

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [((min(lo[i][0] for i in g), min(lo[i][1] for i in g), min(lo[i][2] for i in g)),
             (max(hi[i][0] for i in g), max(hi[i][1] for i in g), max(hi[i][2] for i in g)))
            for g in groups.values()]


def polygon_extents(polygons, z0, z1):
    """(lo, hi) per polygon, vectorised: one pass over all their points."""
    polygons = [p for p in polygons if len(p)]
    if not polygons:
        return [], []
    lens = np.fromiter((len(p) for p in polygons), dtype=np.int64, count=len(polygons))
    pts = np.concatenate(polygons)
    starts = np.concatenate(([0], np.cumsum(lens)[:-1]))
    x0 = np.minimum.reduceat(pts[:, 0], starts)
    x1 = np.maximum.reduceat(pts[:, 0], starts)
    y0 = np.minimum.reduceat(pts[:, 1], starts)
    y1 = np.maximum.reduceat(pts[:, 1], starts)
    lo = [(a, b, z0) for a, b in zip(x0.tolist(), y0.tolist())]
    hi = [(a, b, z1) for a, b in zip(x1.tolist(), y1.tolist())]
    return lo, hi


def layer_blocks(polygons, z0, z1, gap=DEFAULT_GAP_MM):
    """The cluster extents of a layer's polygons."""
    lo, hi = polygon_extents(polygons, z0, z1)
    return cluster(lo, hi, gap)
