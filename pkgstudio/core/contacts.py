# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Contact points on a surface: where bond wires land on a board or substrate
that has no pads of its own to land on.

The user picks a face and places points on it, by typing or clicking. A
point is given in the face's own coordinates, (x, y) in mm, in one of two
frames:

  * the face's corner (the default): origin at the corner of the face's
    extent, x along the face's edge that runs most nearly along the chip's
    (or the world's) x axis, y across it — so a 100 x 50 mm rectangle runs
    from (0, 0) to (100, 50), and a point typed as (25.5, 10) is exactly
    there, whatever way the board lies in space;
  * the chip's centre: origin at the chip's centre dropped onto the face,
    axes along the chip's x and y laid into the face.

Mirroring is across the frame's centre lines: in the corner frame of a
100 x 50 face, over x is y -> 50 - y and over y is x -> 100 - x; in the chip
frame they are the chip's own axes, y -> -y and x -> -x.

Plain Python on (x, y, z) tuples: no FreeCAD, so it can be tested anywhere.
"""

import math
from dataclasses import dataclass


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    if n < 1e-12:
        raise ValueError("a direction of zero length")
    return _mul(a, 1.0 / n)


@dataclass
class FaceFrame:
    """A plane with axes: origin on it, unit normal n, in-plane axes u, v."""
    origin: tuple
    n: tuple
    u: tuple
    v: tuple

    def to_uv(self, p):
        """(u, v) of point *p*, after dropping it onto the plane."""
        d = _sub(p, self.origin)
        return (_dot(d, self.u), _dot(d, self.v))

    def to_xyz(self, uv):
        return _add(self.origin, _add(_mul(self.u, uv[0]), _mul(self.v, uv[1])))

    def project(self, p):
        """*p* dropped onto the plane along its normal."""
        return self.to_xyz(self.to_uv(p))

    def height(self, p):
        """How far *p* is above the plane (along n)."""
        return _dot(_sub(p, self.origin), self.n)


def face_frame(point_on_face, normal, centre, x_axis=(1.0, 0.0, 0.0), y_axis=(0.0, 1.0, 0.0)):
    """
    The frame of the plane through *point_on_face* with *normal*: origin at
    *centre* (the chip's centre) dropped onto it, u along *x_axis* (the
    chip's x) laid into the plane. Where the chip's x axis stands straight
    out of the face, its y axis is used instead, and u is then its image.
    """
    n = _unit(normal)
    centre_on = _sub(centre, _mul(n, _dot(_sub(centre, point_on_face), n)))
    ax = _sub(x_axis, _mul(n, _dot(x_axis, n)))
    if math.sqrt(_dot(ax, ax)) < 1e-6:
        ay = _unit(_sub(y_axis, _mul(n, _dot(y_axis, n))))
        u = _unit(_cross(ay, n))
    else:
        u = _unit(ax)
    v = _cross(n, u)
    return FaceFrame(origin=centre_on, n=n, u=u, v=v)


def snap(uv, pitch):
    """(u, v) on a grid of *pitch* mm round the chip's centre; 0 leaves it."""
    if not pitch or pitch <= 0:
        return tuple(uv)
    return tuple(round(c / pitch) * pitch for c in uv)


MIRRORS = {"x": [(1, -1)], "y": [(-1, 1)], "both": [(1, -1), (-1, 1), (-1, -1)]}


def mirrored(uvs, axis, tol=1e-6):
    """The mirror images of *uvs* across the chip's *axis* ("x", "y" or
    "both") that are not there yet — in order, each once."""
    have = list(uvs)
    out = []
    for su, sv in MIRRORS[axis]:
        for u, v in list(uvs):
            q = (u * su, v * sv)
            if any(abs(q[0] - a) <= tol and abs(q[1] - b) <= tol for a, b in have):
                continue
            have.append(q)
            out.append(q)
    return out


def corner_frame(points, normal, edge_dirs=(), x_axis=(1.0, 0.0, 0.0)):
    """
    (frame, width, height) of a face given by its outline *points* and
    *normal*: x along the face's straight edge (*edge_dirs*) that runs most
    nearly along *x_axis*, y across it in the face, origin at the corner of
    the outline's extent in those axes. A face without straight edges takes
    *x_axis* laid into it.
    """
    n = _unit(normal)
    flat = lambda a: _sub(a, _mul(n, _dot(a, n)))
    ref = flat(x_axis)
    if math.sqrt(_dot(ref, ref)) < 1e-6:
        ref = flat((0.0, 1.0, 0.0)) if abs(n[1]) < 0.9 else flat((0.0, 0.0, 1.0))
    ref = _unit(ref)
    u = ref
    best = -1.0
    for d in edge_dirs:
        d = flat(d)
        if math.sqrt(_dot(d, d)) < 1e-9:
            continue
        d = _unit(d)
        c = _dot(d, ref)
        if abs(c) > best + 1e-9:
            best, u = abs(c), (d if c >= 0 else _mul(d, -1.0))
    v = _cross(n, u)
    p0 = points[0]
    us = [_dot(_sub(p, p0), u) for p in points]
    vs = [_dot(_sub(p, p0), v) for p in points]
    origin = _add(p0, _add(_mul(u, min(us)), _mul(v, min(vs))))
    origin = _sub(origin, _mul(n, _dot(_sub(origin, p0), n)))
    return FaceFrame(origin=origin, n=n, u=u, v=v), max(us) - min(us), max(vs) - min(vs)


def mirror_uv(uv, axis, centre=(0.0, 0.0)):
    """*uv* mirrored over the frame's x centre line (y -> 2 cy - y), its y
    centre line (x -> 2 cx - x), or both, through *centre* (cx, cy)."""
    u, v = uv
    cx, cy = centre
    if axis in ("x", "both"):
        v = 2.0 * cy - v
    if axis in ("y", "both"):
        u = 2.0 * cx - u
    return (u, v)


def nice_step(extent, divisions=10):
    """A round grid spacing — 1, 2 or 5 x a power of ten — giving about
    *divisions* lines across *extent*."""
    if extent <= 0:
        return 1.0
    raw = extent / float(divisions)
    p = 10.0 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 5.0, 10.0):
        if m * p >= raw * 0.999:
            return m * p
    return 10.0 * p


def grid_steps(width, height, major=None):
    """(major, minor) spacing for a width x height face: *major* as given or
    about ten across the longer side, minor a fifth of it (a half where the
    major is a 2)."""
    major = major or nice_step(max(width, height))
    mant = round(major / 10.0 ** math.floor(math.log10(major)), 6)
    return major, major / (2.0 if mant == 2.0 else 5.0)


def grid_values(extent, step, limit=400):
    """Grid positions 0, step, 2 step ... up to *extent*; [] if there would
    be more than *limit*."""
    if step <= 0:
        return []
    n = int(math.floor(extent / step + 1e-9))
    if n + 1 > limit:
        return []
    return [i * step for i in range(n + 1)]
