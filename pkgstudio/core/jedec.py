# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Parametric open-cavity packages from JEDEC outlines.

For packages no catalogue has: a leadframe (die paddle and leads) and a
body with a cavity down to the leadframe, so the die paddle and every lead's
bond finger are open for die attach and wire bonding — the open-cavity
form used for prototyping. The body is cut around the leadframe: no two
solids overlap, which a mesher needs.

Families and outlines (dimensions from the JEDEC outlines, nominal values;
the table entries follow DI-PASSIONATE's package database):

  QFN/DFN   MO-220 / MO-229   no leads outside; leads and paddle flush with
                              the underside (soldered there)
  LQFP/TQFP MS-026            gull-wing leads, leadframe at mid-height
  SOIC      MS-012/MS-013     gull-wing, two sides
  TSSOP     MO-153            gull-wing, two sides

All dimensions mm. build() returns plain Part solids; analyse them with
core.housing like any imported model.
"""

from dataclasses import dataclass

import FreeCAD
import Part

V = FreeCAD.Vector


@dataclass
class Spec:
    name: str
    family: str            # QFN, DFN, QFP, SOIC, TSSOP
    standard: str
    body_x: float
    body_y: float
    height: float          # body height (A)
    pitch: float
    lead_w: float          # b
    per_side_x: int        # leads on each of the left/right sides
    per_side_y: int        # leads on each of the top/bottom sides (0 for two-sided)
    paddle_x: float
    paddle_y: float
    finger: float = 0.40   # exposed bond-finger length inside the cavity
    lead_t: float = 0.20   # leadframe thickness
    foot: float = 0.60     # gull-wing foot length (QFP/SOIC/TSSOP)
    standoff: float = 0.10
    lead_len: float = 0.0  # how far a lead reaches in from the body edge; 0 = up to the paddle

    @property
    def leads(self):
        return 2 * self.per_side_x + 2 * self.per_side_y

    def describe(self):
        return (f"{self.name} — {self.body_x:g} x {self.body_y:g} x {self.height:g} mm, "
                f"{self.leads} leads, {self.pitch:g} mm pitch ({self.standard})")


def _qfn(name, body, n_side, pitch, lead_w, paddle, finger=0.40, height=0.85, std="JEDEC MO-220"):
    return Spec(name, "QFN", std, body, body, height, pitch, lead_w, n_side, n_side, paddle, paddle, finger)


def _dfn(name, bx, by, n_side, pitch, lead_w, px, py, height=0.85):
    return Spec(name, "DFN", "JEDEC MO-229", bx, by, height, pitch, lead_w, n_side, 0, px, py, 0.35)


def _qfp(name, body, n_side, pitch, lead_w, paddle, height=1.40, std="JEDEC MS-026"):
    return Spec(name, "QFP", std, body, body, height, pitch, lead_w, n_side, n_side, paddle, paddle,
                finger=0.80, lead_t=0.127, foot=0.60, standoff=0.10)


def _so(name, family, std, bx, by, n_side, pitch, lead_w, px, py, height):
    # Leads leave the two long sides (top and bottom, the body's length along x).
    return Spec(name, family, std, bx, by, height, pitch, lead_w, 0, n_side, px, py,
                finger=0.60, lead_t=0.15, foot=0.60, standoff=0.10)


CATALOGUE = [
    _qfn("QFN-8 2x2", 2.0, 2, 0.50, 0.25, 1.00),
    _qfn("QFN-12 3x3", 3.0, 3, 0.65, 0.30, 1.50),
    _qfn("QFN-16 3x3", 3.0, 4, 0.50, 0.25, 1.45),
    _qfn("QFN-16 4x4", 4.0, 4, 0.65, 0.30, 2.30),
    _qfn("QFN-20 4x4", 4.0, 5, 0.50, 0.25, 2.20),
    _qfn("QFN-24 4x4", 4.0, 6, 0.50, 0.25, 2.10),
    _qfn("QFN-28 5x5", 5.0, 7, 0.50, 0.25, 3.00),
    _qfn("QFN-32 5x5", 5.0, 8, 0.50, 0.25, 2.90),
    _qfn("QFN-40 6x6", 6.0, 10, 0.50, 0.25, 3.70),
    _qfn("QFN-48 7x7", 7.0, 12, 0.50, 0.25, 4.60),
    _qfn("QFN-64 9x9", 9.0, 16, 0.50, 0.25, 6.00),
    _dfn("DFN-6 2x2", 2.0, 2.0, 3, 0.65, 0.30, 0.90, 1.40),
    _dfn("DFN-8 3x3", 3.0, 3.0, 4, 0.65, 0.30, 1.40, 2.20),
    _dfn("DFN-10 3x3", 3.0, 3.0, 5, 0.50, 0.25, 1.40, 2.20),
    _qfp("TQFP-32 7x7", 7.0, 8, 0.80, 0.37, 3.00, height=1.00),
    _qfp("LQFP-32 7x7", 7.0, 8, 0.80, 0.37, 3.00),
    _qfp("LQFP-44 10x10", 10.0, 11, 0.80, 0.37, 5.00),
    _qfp("LQFP-48 7x7", 7.0, 12, 0.50, 0.22, 3.50),
    _qfp("LQFP-64 10x10", 10.0, 16, 0.50, 0.22, 5.00),
    _qfp("LQFP-100 14x14", 14.0, 25, 0.50, 0.22, 8.00),
    _so("SOIC-8", "SOIC", "JEDEC MS-012", 4.90, 3.90, 4, 1.27, 0.41, 2.30, 2.30, 1.45),
    _so("SOIC-14", "SOIC", "JEDEC MS-012", 8.65, 3.90, 7, 1.27, 0.41, 3.00, 2.30, 1.45),
    _so("SOIC-16", "SOIC", "JEDEC MS-012", 9.90, 3.90, 8, 1.27, 0.41, 3.00, 2.30, 1.45),
    _so("TSSOP-16", "TSSOP", "JEDEC MO-153", 5.00, 4.40, 8, 0.65, 0.25, 2.80, 2.50, 1.00),
    _so("TSSOP-20", "TSSOP", "JEDEC MO-153", 6.50, 4.40, 10, 0.65, 0.25, 3.50, 2.50, 1.00),
]


def by_name(name):
    return next(s for s in CATALOGUE if s.name == name)


def _lead_positions(spec):
    """[(side, offset)] — side in "L", "B", "R", "T"; offset along the side."""
    out = []
    for side, n in (("L", spec.per_side_x), ("B", spec.per_side_y), ("R", spec.per_side_x),
                    ("T", spec.per_side_y)):
        for i in range(n):
            out.append((side, (i - (n - 1) / 2.0) * spec.pitch))
    return out


def build(spec):
    """{"body": solid, "leadframe": [solids]} for *spec*, centred on the origin,
    underside of the package at z = 0."""
    hx, hy = spec.body_x / 2.0, spec.body_y / 2.0
    gull = spec.family in ("QFP", "SOIC", "TSSOP")
    z_lf = spec.standoff + spec.height / 2.0 - spec.lead_t / 2.0 if gull else 0.0
    z_body = spec.standoff if gull else 0.0
    t = spec.lead_t
    gap = 0.15                                      # paddle to finger tips

    paddle = Part.makeBox(spec.paddle_x, spec.paddle_y, t, V(-spec.paddle_x / 2, -spec.paddle_y / 2, z_lf))
    # Fingers end `gap` short of the paddle (or shorter); the cavity opens `finger` of each lead.
    # Where the leads end inside: short of the paddle, or lead_len in, and in
    # any case clear of the neighbouring side's row (elements.geometry).
    from . import elements
    geo = elements.geometry(elements.from_spec(spec))
    inner_x, inner_y = geo["inner_x"], geo["inner_y"]
    leads = []
    for side, off in _lead_positions(spec):
        w = spec.lead_w
        if side in ("L", "R"):
            inner = inner_x
            length = hx - inner
            x0 = -hx if side == "L" else inner
            lead = Part.makeBox(length, w, t, V(x0, off - w / 2, z_lf))
        else:
            inner = inner_y
            length = hy - inner
            y0 = -hy if side == "B" else inner
            lead = Part.makeBox(w, length, t, V(off - w / 2, y0, z_lf))
        if gull:
            lead = lead.fuse(_gull_wing(spec, side, off, z_lf)).removeSplitter()
        leads.append(lead)

    # The cavity opens `finger` of each lead beyond its tip, and along each
    # side far enough to uncover the outermost lead — but leaves a wall.
    def reach(n_along):
        return max((abs(o) + spec.lead_w / 2.0 + 0.10 for s_, o in _lead_positions(spec) if s_ in n_along),
                   default=0.0)
    cav_x = max(inner_x + (spec.finger if spec.per_side_x else 0.0), spec.paddle_x / 2.0 + gap, reach("BT"))
    cav_y = max(inner_y + (spec.finger if spec.per_side_y else 0.0), spec.paddle_y / 2.0 + gap, reach("LR"))
    cav_x, cav_y = min(cav_x, hx - 0.10), min(cav_y, hy - 0.10)
    floor = z_lf + t
    top = z_body + spec.height
    box = Part.makeBox(spec.body_x, spec.body_y, spec.height, V(-hx, -hy, z_body))
    cavity = Part.makeBox(2 * cav_x, 2 * cav_y, top - floor + 0.01, V(-cav_x, -cav_y, floor))
    body = box.cut(cavity).cut([paddle] + leads).removeSplitter()
    body = max(body.Solids, key=lambda s: s.Volume) if body.Solids else body
    return {"body": body, "leadframe": [paddle] + leads}


def _gull_wing(spec, side, off, z_lf):
    """The outside part of a gull-wing lead: out from the body, down, a foot."""
    t, w = spec.lead_t, spec.lead_w
    out = 0.25                                       # shoulder outside the body
    hx, hy = spec.body_x / 2.0, spec.body_y / 2.0
    drop = z_lf
    parts = []
    if side in ("L", "R"):
        s = -1 if side == "L" else 1
        edge = s * hx
        shoulder = Part.makeBox(out, w, t, V(edge if s > 0 else edge - out, off - w / 2, z_lf))
        xv = edge + s * out
        vertical = Part.makeBox(t, w, drop + t, V(xv if s > 0 else xv - t, off - w / 2, 0))
        xf = xv + (0 if s > 0 else -spec.foot)
        foot = Part.makeBox(spec.foot, w, t, V(xf if s > 0 else xf, off - w / 2, 0))
    else:
        s = -1 if side == "B" else 1
        edge = s * hy
        shoulder = Part.makeBox(w, out, t, V(off - w / 2, edge if s > 0 else edge - out, z_lf))
        yv = edge + s * out
        vertical = Part.makeBox(w, t, drop + t, V(off - w / 2, yv if s > 0 else yv - t, 0))
        yf = yv + (0 if s > 0 else -spec.foot)
        foot = Part.makeBox(w, spec.foot, t, V(off - w / 2, yf, 0))
    parts = [shoulder, vertical, foot]
    return parts[0].fuse(parts[1:]).removeSplitter()


def lid(body_x, body_y, thickness=0.25):
    """A flat lid the size of the body, its underside at z = 0."""
    return Part.makeBox(body_x, body_y, thickness, V(-body_x / 2, -body_y / 2, 0))
