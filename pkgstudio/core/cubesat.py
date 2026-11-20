# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A 1U CubeSat to put boards into, and the numbers it is checked against.

Limits (CubeSat Design Specification Rev 14.1, Cal Poly, 2022):
  1U envelope        100 x 100 x 113.5 mm
  mass               <= 2.00 kg
  centre of gravity  within 2 cm of the geometric centre in X, Y and Z
  protrusions        <= 6.5 mm normal to a face (ISO 17770 / CDS), never on
                     the rails
Board stack (Pumpkin CubeSat Kit datasheets): PC/104-size boards, 15 mm from
board to board, components <= 11 mm above a board and <= 2 mm below.

The structure is generic, not a vendor's: four L-section rails 8.5 mm wide
(1 mm wall), a frame at each end, M3 rods through the board stack on the
PC/104 holes, spacers between the boards, a solar panel on each side face.
Good enough for envelope, collision, clearance, mass and centre of gravity;
replace it by your structure's STEP for anything that depends on its detail.

Frame: the CubeSat's geometric centre at the origin, z along the 113.5 mm.
"""

from dataclasses import dataclass

import FreeCAD
import Part

from . import pcb

V = FreeCAD.Vector

CDS_1U = dict(x=100.0, y=100.0, z=113.5, mass_kg=2.00, cg_mm=(20.0, 20.0, 20.0), protrusion=6.5,
              rail=8.5, source="CubeSat Design Specification Rev 14.1 (Cal Poly, 2022)")


@dataclass
class Part_:
    name: str
    shape: object
    material: str
    group: str


def _l_rail(cx, cy, length, size=8.5, wall=1.0):
    """An L-section rail in the corner (sign cx, cy), along z."""
    sx, sy = (1 if cx > 0 else -1), (1 if cy > 0 else -1)
    ox, oy = sx * 50.0, sy * 50.0
    a = Part.makeBox(size, wall, length, V(min(ox, ox - sx * size), min(oy, oy - sy * wall), -length / 2))
    b = Part.makeBox(wall, size, length, V(min(ox, ox - sx * wall), min(oy, oy - sy * size), -length / 2))
    return a.fuse(b).removeSplitter()


def structure(length=113.5, frame_t=3.0, holes=None):
    """The 1U structure's parts (Part_)."""
    holes = holes or pcb.pc104_holes()
    out = []
    for cx in (-1, 1):
        for cy in (-1, 1):
            out.append(Part_(f"Rail {'+' if cx > 0 else '-'}X{'+' if cy > 0 else '-'}Y", _l_rail(cx, cy, length),
                             "Aluminium 6061-T6", "Structure"))
    for sign, label in ((-1, "Bottom frame (-Z)"), (1, "Top frame (+Z)")):
        z0 = sign * length / 2 - (frame_t if sign > 0 else 0)
        ring = Part.makeBox(98.0, 98.0, frame_t, V(-49, -49, z0)).cut(
            Part.makeBox(83.0, 83.0, frame_t + 2, V(-41.5, -41.5, z0 - 1)))
        out.append(Part_(label, ring, "Aluminium 6061-T6", "Structure"))
    return out


def solar_panels(length=100.0, t=1.0, cell_t=0.14):
    """A panel with two GaAs cells on each side face, inside the rails."""
    out = []
    for axis, sign in (("x", 1), ("x", -1), ("y", 1), ("y", -1)):
        name = f"{'+' if sign > 0 else '-'}{axis.upper()}"
        inner = 49.85 - t
        if axis == "x":
            x0 = inner if sign > 0 else -49.85
            panel = Part.makeBox(t, 83.0, length, V(x0, -41.5, -length / 2))
            cx0 = 49.85 if sign > 0 else -49.85 - cell_t
            cells = [Part.makeBox(cell_t, 38.0, 80.0, V(cx0, y, -40.0)) for y in (-39.5, 1.5)]
        else:
            y0 = inner if sign > 0 else -49.85
            panel = Part.makeBox(83.0, t, length, V(-41.5, y0, -length / 2))
            cy0 = 49.85 if sign > 0 else -49.85 - cell_t
            cells = [Part.makeBox(38.0, cell_t, 80.0, V(x, cy0, -40.0)) for x in (-39.5, 1.5)]
        out.append(Part_(f"Solar panel {name}", panel, "FR-4", "Solar panels"))
        out.append(Part_(f"Solar cells {name}", Part.makeCompound(cells), "Gallium arsenide (GaAs)", "Solar panels"))
    return out


def rods_and_spacers(board_tops, board_t=1.6, length=113.5, frame_t=3.0, holes=None, rod_d=3.0,
                     spacer_d=5.0):
    """M3 rods on the PC/104 holes from frame to frame, and a spacer on every
    hole between neighbouring boards. *board_tops*: z of each board's top."""
    holes = holes or pcb.pc104_holes()
    z0, z1 = -length / 2 + frame_t, length / 2 - frame_t
    out = []
    rods = [Part.makeCylinder(rod_d / 2, z1 - z0, V(x, y, z0)) for x, y, _d in holes]
    out.append(Part_("M3 rods", Part.makeCompound(rods), "Stainless steel 304", "Stack"))
    tops = sorted(board_tops)
    levels = [(z0, tops[0] - board_t)] + [(a, b - board_t) for a, b in zip(tops, tops[1:])]
    for i, (lo, hi) in enumerate(levels):
        if hi - lo <= 1e-6:
            continue
        sp = [Part.makeCylinder(spacer_d / 2, hi - lo, V(x, y, lo)).cut(
            Part.makeCylinder(rod_d / 2 + 0.1, hi - lo + 2, V(x, y, lo - 1))) for x, y, _d in holes]
        out.append(Part_(f"Spacers {i + 1}", Part.makeCompound(sp), "Aluminium 6061-T6", "Stack"))
    return out


def patch_antenna(z_top, size=80.0, h=1.524, patch=36.0):
    """An S-band patch on the +Z face (placeholder geometry: laminate,
    ground, square patch; not designed)."""
    z0 = z_top - h - 0.035
    lam = Part.makeBox(size, size, h, V(-size / 2, -size / 2, z0))
    gnd = Part.makeBox(size, size, 0.035, V(-size / 2, -size / 2, z0 - 0.035))
    p = Part.makeBox(patch, patch, 0.035, V(-patch / 2, -patch / 2, z0 + h))
    return [Part_("S-band patch laminate", lam, "Rogers RO4350B", "Antenna"),
            Part_("S-band patch copper", Part.makeCompound([gnd, p]), "Copper (Cu)", "Antenna")]
