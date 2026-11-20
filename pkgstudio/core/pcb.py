# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Printed circuit boards, generated: the stack, the outline with its mounting
holes, footprints (QFN lands), traces, grounded coplanar waveguides with via
fences, and component bodies — as named solids with their materials, for the
workbench to put into a Board (app.boards).

Frame (as the board project of the LNA example): z = 0 is the top of the
inner ground plane (L2); the RF laminate runs 0..H, the top copper H..H+T;
below the ground plane the FR-4 core to the board's underside. x, y: the
board centre.

A generator, not a layout tool: it draws what a script or a panel tells it,
so a reference board can be built, checked and simulated from code — and
re-drawn when the package or the stack changes.

PC/104 (CubeSat) form factor: 90.17 x 95.89 mm, 1.6 mm (PC/104 Consortium:
"3.550 x 3.775 inch"; Pumpkin CubeSat Kit datasheets: 1.6 mm PCB, 15 mm
stack pitch, components up to 11 mm above and 2 mm below). The four corner
holes here sit 5.08 mm (0.2 inch) in from the edges, d 3.18 mm (0.125 inch):
the common pattern, *not* checked against the PC/104 drawing — compare with
your structure's drawing before a board is made.
"""

import math
from dataclasses import dataclass, field

import FreeCAD
import Part

from . import elements as el

V = FreeCAD.Vector

PC104_X, PC104_Y = 95.89, 90.17      # mm (3.775 x 3.550 in)
PC104_HOLE_INSET = 5.08              # mm, assumed (see the module doc)
PC104_HOLE_D = 3.18
STACK_PITCH = 15.0                   # board top to board top, CubeSat Kit
MAX_ABOVE, MAX_BELOW = 11.0, 2.0     # component height, CubeSat Kit datasheets


@dataclass
class Stack:
    """A board's build-up: an RF laminate on a ground plane over an FR-4 core."""
    total: float = 1.6
    rf_h: float = 0.254              # RF laminate, L1 to L2
    cu: float = 0.035                # copper (1 oz)
    rf_material: str = "Rogers RO4350B"
    core_material: str = "FR-4"

    @property
    def top(self):                   # top of the top copper
        return self.rf_h + self.cu

    @property
    def bottom(self):
        return self.top - self.total


@dataclass
class Piece:
    name: str
    shape: object
    material: str
    kind: str                        # "dielectric" | "copper" | "component" | "connector"
    group: str = ""                  # what it belongs to on the board ("RF", "MCM footprint", "H1" …)
    props: dict = field(default_factory=dict)


def pc104_holes(w=PC104_X, h=PC104_Y, inset=PC104_HOLE_INSET, d=PC104_HOLE_D):
    return [(sx * (w / 2 - inset), sy * (h / 2 - inset), d) for sx in (-1, 1) for sy in (-1, 1)]


def _slab(w, h, z0, z1, holes):
    s = Part.makeBox(w, h, z1 - z0, V(-w / 2, -h / 2, z0))
    for x, y, d in holes:
        s = s.cut(Part.makeCylinder(d / 2, z1 - z0 + 2, V(x, y, z0 - 1)))
    return s


def board(w=PC104_X, h=PC104_Y, holes=None, stack=None):
    """The laminate and the ground plane, holes cut through."""
    stack = stack or Stack()
    holes = pc104_holes(w, h) if holes is None else holes
    return [
        Piece("RF laminate", _slab(w, h, 0.0, stack.rf_h, holes), stack.rf_material, "dielectric", "stack"),
        Piece("Ground plane L2", _slab(w, h, -stack.cu, 0.0, holes), "Copper (Cu)", "copper", "stack"),
        Piece("FR-4 core", _slab(w, h, stack.bottom, -stack.cu, holes), stack.core_material, "dielectric", "stack"),
    ]


def _copper(rect, stack):
    x0, y0, x1, y1 = rect
    return Part.makeBox(x1 - x0, y1 - y0, stack.cu, V(x0, y0, stack.rf_h))


def qfn_lands(d, toe=0.25, heel=0.05, widen=0.05, gap=0.2):
    """
    The land pattern of a QFN/DFN element (core.elements spec dict) in the
    package frame: [(rect, side)] for the leads and the exposed pad's rect.
    Each land is the lead's underside (width b, from the body edge in by the
    lead length) grown by *toe* outwards, *heel* inwards and *widen* across
    — the shape of an IPC-7351 nominal land, simplified. Lands at a corner
    are shortened at the heel to keep *gap* from the other row's lands (grown
    by heel and width, the corner lands of a 4x4 QFN-24 touched).
    """
    d = el.normalise(d)
    g = el.geometry(d)
    out = []
    b = d["lead_w"] + widen
    rows = {"x": (d["per_side_x"], g["hx"], g["len_x"]), "y": (d["per_side_y"], g["hy"], g["len_y"])}

    def reach(axis):
        """(outermost land edge across the row, the row's inner end) of the rows on *axis*."""
        n, half, length = rows[axis]
        if n <= 0:
            return None
        return ((n - 1) / 2.0 * d["pitch"] + b / 2, half - length - heel)

    for axis, (n, half, length) in rows.items():
        other = reach("y" if axis == "x" else "x")
        for i in range(n):
            off = (i - (n - 1) / 2.0) * d["pitch"]
            inner = half - length - heel
            if other is not None and abs(off) + b / 2 + gap > other[1]:
                inner = max(inner, other[0] + gap)
            for sign in (-1, 1):
                a0, a1 = sign * inner, sign * (half + toe)
                lo, hi = min(a0, a1), max(a0, a1)
                rect = (lo, off - b / 2, hi, off + b / 2) if axis == "x" else (off - b / 2, lo, off + b / 2, hi)
                side = ("R" if sign > 0 else "L") if axis == "x" else ("T" if sign > 0 else "B")
                out.append((rect, side))
    ep = (-d["paddle_x"] / 2, -d["paddle_y"] / 2, d["paddle_x"] / 2, d["paddle_y"] / 2)
    return out, ep


def footprint(d, at=(0.0, 0.0), stack=None, name="U1"):
    """Copper lands of element *d* at *at* (board frame), one Piece each."""
    stack = stack or Stack()
    lands, ep = qfn_lands(d)
    pieces = []
    for i, (r, side) in enumerate(lands, 1):
        rect = (r[0] + at[0], r[1] + at[1], r[2] + at[0], r[3] + at[1])
        pieces.append(Piece(f"{name} land {i} {side}", _copper(rect, stack), "Copper (Cu)", "copper", name,
                            {"Land": True}))
    rect = (ep[0] + at[0], ep[1] + at[1], ep[2] + at[0], ep[3] + at[1])
    pieces.append(Piece(f"{name} exposed pad", _copper(rect, stack), "Copper (Cu)", "copper", name,
                        {"Land": True, "ExposedPad": True}))
    return pieces


def _segment_rect(a, b, w):
    """A rectangle of width *w* from point *a* to point *b* (axis-parallel or not), as a face."""
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy * w / 2, ux * w / 2
    pts = [V(ax + nx, ay + ny, 0), V(bx + nx, by + ny, 0), V(bx - nx, by - ny, 0), V(ax - nx, ay - ny, 0)]
    return Part.Face(Part.makePolygon(pts + [pts[0]]))


def trace(points, w, stack=None, square_ends=True):
    """A top-copper trace along *points* [(x, y)], *w* wide (a solid)."""
    stack = stack or Stack()
    faces = [_segment_rect(a, b, w) for a, b in zip(points, points[1:])]
    pads = [Part.Face(Part.makePolygon([V(x - w / 2, y - w / 2, 0), V(x + w / 2, y - w / 2, 0),
                                        V(x + w / 2, y + w / 2, 0), V(x - w / 2, y + w / 2, 0),
                                        V(x - w / 2, y - w / 2, 0)])) for x, y in points[1:-1]]
    face = faces[0].fuse(faces[1:] + pads) if len(faces) + len(pads) > 1 else faces[0]
    face = face.removeSplitter()
    solid = face.extrude(V(0, 0, stack.cu))
    solid.translate(V(0, 0, stack.rf_h))
    return solid


def gcpw(a, b, w, s, ground_w=1.0, stack=None, via_d=0.3, via_pitch=1.0, stop_a=0.0, stop_b=0.0, name="RF"):
    """
    A grounded coplanar waveguide from *a* to *b* (straight): the line *w*
    wide, top ground *s* away on both sides, *ground_w* wide, stitched to the
    ground plane by a fence of vias every *via_pitch* along each side. The
    ground strips stop *stop_a* / *stop_b* short of the ends (to clear a
    footprint or a connector).
    """
    stack = stack or Stack()
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy, ux
    pieces = [Piece(f"{name} line", trace([a, b], w, stack), "Copper (Cu)", "copper", name)]
    off = w / 2 + s + ground_w / 2
    g0, g1 = stop_a, L - stop_b
    vias = []
    for sign, side in ((1, "left"), (-1, "right")):
        p0 = (ax + ux * g0 + sign * nx * off, ay + uy * g0 + sign * ny * off)
        p1 = (ax + ux * g1 + sign * nx * off, ay + uy * g1 + sign * ny * off)
        pieces.append(Piece(f"{name} ground {side}", trace([p0, p1], ground_w, stack), "Copper (Cu)", "copper",
                            name))
        n = max(int((g1 - g0) / via_pitch), 1)
        for k in range(n + 1):
            t = g0 + (g1 - g0) * k / n
            vias.append(Part.makeCylinder(via_d / 2, stack.top, V(ax + ux * t + sign * nx * off,
                                                                   ay + uy * t + sign * ny * off, 0)))
    pieces.append(Piece(f"{name} via fence", Part.makeCompound(vias), "Copper (Cu)", "copper", name,
                        {"Vias": len(vias)}))
    return pieces


def box(name, x, y, w, h, t, material, group="", z0=None, stack=None, kind="component", **props):
    """A component body *w* x *h* x *t* centred on (x, y), standing on the top copper."""
    stack = stack or Stack()
    z0 = stack.top if z0 is None else z0
    return Piece(name, Part.makeBox(w, h, t, V(x - w / 2, y - h / 2, z0)), material, kind, group or name, props)


def header(name, x, y, rows=2, pins=26, pitch=2.54, height=8.5, stack=None):
    """A stacking header body (CubeSat Kit Bus H1/H2: 2 x 26, 2.54 mm)
    centred on (x, y), its long side along x."""
    w, h = pins * pitch, rows * pitch
    return box(name, x, y, w, h, height, "Liquid crystal polymer (LCP)", name, stack=stack, kind="connector",
               Pins=rows * pins)


def mmcx_right_angle(name, x, y, stack=None, facing="-x"):
    """A right-angle MMCX jack on the top side (body 5 x 5 x 4 mm, a 3.6 mm
    socket pointing *facing*) — the usual RF connector of CubeSat boards:
    it stays inside the board outline."""
    stack = stack or Stack()
    body = Part.makeBox(5.0, 5.0, 4.0, V(x - 2.5, y - 2.5, stack.top))
    d = {"-x": V(-1, 0, 0), "+x": V(1, 0, 0), "-y": V(0, -1, 0), "+y": V(0, 1, 0)}[facing]
    socket = Part.makeCylinder(1.8, 3.6, V(x, y, stack.top + 2.0) + d * 2.5, d)
    return Piece(name, body.fuse(socket).removeSplitter(), "Copper alloy C194 (leadframe)", "connector", name)


def sma_edge(name, x, y, stack=None, facing="-x"):
    """An edge-launch SMA jack: flange on the board edge at (x, y), the
    barrel (d 6.35, 9.5 mm) out over the edge — sticks out of a board that
    has to fit a structure; for test boards."""
    stack = stack or Stack()
    d = {"-x": V(-1, 0, 0), "+x": V(1, 0, 0)}[facing]
    flange = Part.makeBox(1.6, 9.5, 9.5, V(x - 0.8, y - 4.75, stack.top - 4.75))
    barrel = Part.makeCylinder(3.175, 9.5, V(x, y, stack.top) + d * 0.8, d)
    return Piece(name, flange.fuse(barrel).removeSplitter(), "Copper alloy C194 (leadframe)", "connector", name)


def cutout(pieces, rect):
    """The part of *pieces* inside *rect* (x0, y0, x1, y1), for an EM model of
    one region of a board: each piece cut to the region, empty ones dropped."""
    x0, y0, x1, y1 = rect
    out = []
    for p in pieces:
        bb = p.shape.BoundBox
        clip = Part.makeBox(x1 - x0, y1 - y0, bb.ZLength + 2, V(x0, y0, bb.ZMin - 1))
        if not bb.isValid() or bb.XMax <= x0 or bb.XMin >= x1 or bb.YMax <= y0 or bb.YMin >= y1:
            continue
        s = p.shape.common(clip)
        if s.isNull() or s.Volume < 1e-12:
            continue
        out.append(Piece(p.name, s, p.material, p.kind, p.group, dict(p.props)))
    return out
