# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Exact triangle meshes of extruded layout polygons.

A drawn layer is a set of flat polygons pushed up by the layer's thickness,
so a triangle mesh of it is not an approximation: every face is planar and
every vertex is a vertex of the drawing. That makes meshes the right thing
for both the 3-D view and the OBJ export — no CAD kernel involved, and fast:
an OCCT solid costs about 0.6 ms per polygon to build and as much again for
FreeCAD to tessellate for display, which froze the window for minutes on a
real chip. Here a million rectangles are a few numpy operations.

Convex polygons (rectangles above all — nearly everything on a chip) are
triangulated as fans in one vectorised pass per vertex count; only the
non-convex ones go through ear clipping, one at a time.
"""

import numpy as np


def _signed_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _clean(pts):
    """Drop a repeated closing vertex and consecutive duplicates; make CCW."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.any(np.abs(np.diff(pts, axis=0)) > 1e-12, axis=1)
    pts = pts[keep]
    if len(pts) >= 3 and _signed_area(pts) < 0:
        pts = pts[::-1]
    return pts


def _is_convex(pts):
    d1 = np.roll(pts, -1, axis=0) - pts
    d2 = np.roll(d1, -1, axis=0)
    cross = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    return bool(np.all(cross >= -1e-15))


def ear_clip(pts):
    """Triangles [(i, j, k)] of the simple CCW polygon *pts* (n, 2)."""
    n = len(pts)
    idx = list(range(n))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return (cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0)

    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if cross(a, b, c) <= 1e-18:
                continue                      # reflex or degenerate corner
            if any(inside(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break                             # no ear: a self-touching outline
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def _sort_polygons(polygons):
    """
    ({vertex count: (k, n, 2) convex CCW rings}, [other CCW rings]).

    Done in bulk, one batch per vertex count: closing duplicates, clockwise
    rings, zero areas and convexity are decided for a whole batch at once.
    Cleaning each polygon with a handful of small NumPy calls cost 0.6 ms a
    polygon — 9 s for a 14,000-rectangle fill layer, most of a large chip's
    import. A polygon with a repeated vertex inside it is the rare case and
    takes the careful path, one at a time.
    """
    by_n = {}
    for p in polygons:
        by_n.setdefault(len(p), []).append(p)
    groups, others = {}, []

    def add_group(n, rings):
        groups[n] = rings if n not in groups else np.concatenate([groups[n], rings])

    for n, batch in by_n.items():
        if n < 3:
            continue
        arr = np.asarray(batch, dtype=float)                       # (k, n, 2)
        closing = np.all(np.abs(arr[:, 0] - arr[:, -1]) <= 1e-12, axis=1)
        for sub in (arr[~closing], arr[closing][:, :-1] if closing.any() else None):
            if sub is None or not len(sub) or sub.shape[1] < 3:
                continue
            m = sub.shape[1]
            edge = np.roll(sub, -1, axis=1) - sub                    # (k, m, 2)
            dup = np.any(np.all(np.abs(edge) <= 1e-12, axis=2), axis=1)
            for raw in sub[dup]:                                     # rare: careful path
                pts = _clean(raw)
                if len(pts) >= 3 and abs(_signed_area(pts)) > 1e-18:
                    if _is_convex(pts):
                        add_group(len(pts), pts[None])
                    else:
                        others.append(pts)
            sub = sub[~dup]
            if not len(sub):
                continue
            x, y = sub[:, :, 0], sub[:, :, 1]
            area = 0.5 * np.sum(x * np.roll(y, -1, axis=1) - y * np.roll(x, -1, axis=1), axis=1)
            keep = np.abs(area) > 1e-18
            sub, area = sub[keep], area[keep]
            cw = area < 0
            sub[cw] = sub[cw, ::-1]
            d1 = np.roll(sub, -1, axis=1) - sub
            d2 = np.roll(d1, -1, axis=1)
            cross = d1[:, :, 0] * d2[:, :, 1] - d1[:, :, 1] * d2[:, :, 0]
            convex = np.all(cross >= -1e-15, axis=1)
            if convex.any():
                add_group(m, sub[convex])
            others.extend(sub[~convex])
    return groups, others


def extrude(polygons, z0, z1):
    """
    (vertices (N, 3), triangles (M, 3) int) of *polygons* extruded from z0 to
    z1. Outward-facing, CCW seen from outside. z1 == z0 gives the flat top
    faces only.
    """
    flat = z1 - z0 <= 0.0
    groups, others = _sort_polygons(polygons)

    verts, tris, base = [], [], 0

    def emit(rings, cap):
        """rings (k, n, 2) CCW; cap (t, 3) triangle indices into one ring."""
        nonlocal base
        k, n = rings.shape[0], rings.shape[1]
        top = np.concatenate([rings, np.full((k, n, 1), z1)], axis=2)
        parts = [top]
        if not flat:
            parts.append(np.concatenate([rings, np.full((k, n, 1), z0)], axis=2))
        v = np.concatenate(parts, axis=1)                  # (k, n or 2n, 3)
        offs = (base + np.arange(k) * v.shape[1])[:, None, None]
        t = [cap[None, :, :] + offs]                       # top, CCW = up
        if not flat:
            t.append(cap[None, :, ::-1] + n + offs)        # bottom, reversed = down
            i = np.arange(n)
            j = (i + 1) % n
            # bottom i, bottom j, top j  /  bottom i, top j, top i  — outward for CCW rings
            side = np.stack([np.stack([i + n, j + n, j], 1),
                             np.stack([i + n, j, i], 1)], 1).reshape(-1, 3)
            t.append(side[None, :, :] + offs)
        verts.append(v.reshape(-1, 3))
        tris.append(np.concatenate(t, axis=1).reshape(-1, 3))
        base += k * v.shape[1]

    for n, rings in groups.items():
        fan = np.stack([np.zeros(n - 2, int), np.arange(1, n - 1), np.arange(2, n)], 1)
        emit(np.asarray(rings), fan)
    for pts in others:
        cap = np.asarray(ear_clip(pts), dtype=int).reshape(-1, 3)
        if len(cap):
            emit(pts[None, :, :], cap)

    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    return np.concatenate(verts), np.concatenate(tris).astype(np.int64)


def boxes(extents):
    """(vertices, triangles) of axis-aligned boxes [((x0,y0,z0), (x1,y1,z1))]."""
    vs, ts, base = [], [], 0
    for (x0, y0, z0), (x1, y1, z1) in extents:
        v, t = extrude([np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])], z0, z1)
        vs.append(v)
        ts.append(t + base)
        base += len(v)
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    return np.concatenate(vs), np.concatenate(ts)


def volume(verts, tris):
    """Enclosed volume of a closed, outward-facing triangle mesh."""
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def merge(meshes):
    """One (vertices, triangles) from several."""
    vs, ts, base = [], [], 0
    for v, t in meshes:
        vs.append(v)
        ts.append(t + base)
        base += len(v)
    if not vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    return np.concatenate(vs), np.concatenate(ts)


def write_binary_stl(path, verts, tris):
    """Binary STL — FreeCAD's mesh reader loads this in C++, far faster than a
    Python list of facets."""
    tri = verts[tris].astype(np.float32)                  # (m, 3, 3)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lens, out=np.zeros_like(normals), where=lens > 0)
    rec = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = normals, tri
    with open(path, "wb") as fh:
        fh.write(b"PackageStudio".ljust(80, b" "))
        fh.write(np.uint32(len(rec)).tobytes())
        rec.tofile(fh)
