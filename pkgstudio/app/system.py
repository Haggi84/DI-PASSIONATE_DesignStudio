# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The next level up: boards in a system — here a 1U CubeSat — and the checks
that only make sense there.

  make_cubesat     the structure (rails, frames, rods), solar panels and a
                   patch antenna, as a System container (role "system")
  add_board        a Board into the system, its top at a given height of
                   the board stack; the spacers follow the stack
  check            envelope (and the 6.5 mm a part may stand out of a face),
                   collisions between modules, the clearance of each board's
                   parts to the board above and below, mass and centre of
                   gravity — against the CubeSat Design Specification

A module is what a collision is counted between: each Board (with whatever
is on it — packages, dies, connectors) and each group of the structure.
Parts of one module may touch; parts of two may touch (a spacer on a board)
but not share volume.

Mass: volume x density of each part's material (Materials panel), or its
`MassOverride` (grams) where a part is a stand-in for something whose
inside is not modelled. Parts without either are listed.
"""

from dataclasses import dataclass, field

import FreeCAD
import Part

from ..core import cubesat as cs, pcb
from . import materials, model

V = FreeCAD.Vector
SYSTEM = "system"
TOL_VOL = 1e-4          # mm³ of shared volume that counts as a collision


def _prop(obj, kind, name, value, tip=""):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, model.GROUP, tip)
    setattr(obj, name, value)


def make_cubesat(doc, name="CubeSat 1U", board_tops=(), antenna=True):
    """A 1U CubeSat system container. *board_tops*: where the spacers go —
    add_board updates them when boards are added."""
    sysobj = doc.addObject("App::Part", "System")
    sysobj.Label = name
    model.set_role(sysobj, SYSTEM)
    _prop(sysobj, "App::PropertyString", "SystemKind", "CubeSat 1U", "What kind of system")
    parts = cs.structure() + cs.solar_panels()
    if antenna:
        parts += cs.patch_antenna(cs.CDS_1U["z"] / 2 - 0.15)
    _add_parts(sysobj, parts)
    return sysobj


def _add_parts(sysobj, parts):
    doc = sysobj.Document
    groups = {g.Label: g for g in sysobj.Group if g.isDerivedFrom("App::DocumentObjectGroup")}
    made = []
    for p in parts:
        grp = groups.get(p.group)
        if grp is None:
            grp = doc.addObject("App::DocumentObjectGroup", "SystemGroup")
            grp.Label = p.group
            _prop(grp, "App::PropertyString", "SystemModule", p.group, "A module of the system")
            sysobj.addObject(grp)
            groups[p.group] = grp
        obj = doc.addObject("Part::Feature", "SystemPart")
        obj.Label = p.name
        obj.Shape = p.shape
        grp.addObject(obj)
        materials.set_material(obj, p.material)
        made.append(obj)
    doc.recompute()
    return made


def boards_of(sysobj):
    return [o for o in sysobj.Group if model.role(o) == model.BOARD]


def board_top_z(board):
    """z of *board*'s top surface, global."""
    top = getattr(board, "BoardTop", None)
    top = float(getattr(top, "Value", top)) if top is not None else 0.0
    return model.global_placement(board).multVec(V(0, 0, top)).z


def add_board(sysobj, board, top_z, turn_deg=0.0):
    """Put *board* into the system with its top surface at *top_z* (system
    frame), centred, and rebuild the spacers for the new stack."""
    model.move_into(board, sysobj)
    top = getattr(board, "BoardTop", None)
    top = float(getattr(top, "Value", top)) if top is not None else 0.0
    board.Placement = FreeCAD.Placement(V(0, 0, top_z - top), FreeCAD.Rotation(V(0, 0, 1), turn_deg))
    _prop(board, "App::PropertyFloat", "StackTop", top_z, "Top of this board in the system's stack, mm")
    rebuild_stack(sysobj)
    return board


def rebuild_stack(sysobj):
    doc = sysobj.Document
    for grp in [g for g in sysobj.Group if getattr(g, "SystemModule", "") == "Stack"]:
        for o in list(grp.Group):
            doc.removeObject(o.Name)
        doc.removeObject(grp.Name)
    tops = []
    for b in boards_of(sysobj):
        t = getattr(b, "BoardThickness", None)
        tops.append((board_top_z(b), float(getattr(t, "Value", t)) if t is not None else 1.6))
    if tops:
        tops.sort()
        _add_parts(sysobj, cs.rods_and_spacers([t for t, _ in tops], board_t=tops[0][1]))
    doc.recompute()


# -- checks --------------------------------------------------------------------------------------

@dataclass
class Body:
    obj: object
    module: str
    shape: object                  # global
    material: str
    mass_g: float = None           # None: unknown


@dataclass
class Finding:
    level: str                     # "error" | "warning" | "info"
    check: str
    message: str
    objects: list = field(default_factory=list)

    def __str__(self):
        return f"{self.level}: [{self.check}] {self.message}"


def _module_of(obj, sysobj):
    p, last = obj, obj
    while p is not None and p.Name != sysobj.Name:
        last = p
        p = p.getParentGeoFeatureGroup()
    if model.role(last) == model.BOARD:
        return last.Label
    for g in obj.InList:
        if getattr(g, "SystemModule", ""):
            return g.SystemModule
    return last.Label


def _leaves(container):
    out = []
    for o in container.OutListRecursive:
        if not o.isDerivedFrom("Part::Feature") or model.role(o) in (model.PAD, model.LAYER, model.WIRE):
            continue
        if not o.Shape.Solids and model.role(o) != "die-body":
            continue
        out.append(o)
    return out


def bodies(sysobj):
    lib = materials.available(sysobj.Document)
    out = []
    for o in _leaves(sysobj):
        s = o.Shape.copy()
        s.Placement = model.global_placement(o)
        name = materials.material_of(o)
        mass = None
        if hasattr(o, "MassOverride"):
            mass = float(o.MassOverride)
        elif name in lib and s.Solids:
            mass = s.Volume * lib[name].density * 1e-6       # mm³ · kg/m³ → g
        out.append(Body(o, _module_of(o, sysobj), s, name, mass))
    return out


def _com(shape):
    vs = [(s.Volume, s.CenterOfMass) for s in shape.Solids if s.Volume > 0]
    tot = sum(v for v, _ in vs)
    if tot <= 0:
        return shape.BoundBox.Center
    c = V()
    for v, p in vs:
        c = c + p * (v / tot)
    return c


def check(sysobj, limits=None, min_gap=0.5):
    """[Finding] for the system, and a summary dict (mass, centre of gravity,
    stack clearances)."""
    limits = limits or cs.CDS_1U
    out = []
    bs = bodies(sysobj)
    hx, hy, hz = limits["x"] / 2, limits["y"] / 2, limits["z"] / 2

    # envelope
    for b in bs:
        bb = b.shape.BoundBox
        over = max(bb.XMax - hx, -hx - bb.XMin, bb.YMax - hy, -hy - bb.YMin, bb.ZMax - hz, -hz - bb.ZMin)
        if over > 1e-6:
            rail = limits["rail"]
            on_rail = (bb.XMax > hx - rail or bb.XMin < -hx + rail) and (bb.YMax > hy - rail or bb.YMin < -hy + rail)
            if over <= limits["protrusion"] and not on_rail:
                out.append(Finding("warning", "envelope", f"{b.obj.Label} ({b.module}) stands {over:.2f} mm out of "
                                   f"the {limits['x']:g} x {limits['y']:g} x {limits['z']:g} mm envelope "
                                   f"(<= {limits['protrusion']} mm allowed on a face, not on the rails).",
                                   [b.obj.Name]))
            else:
                out.append(Finding("error", "envelope", f"{b.obj.Label} ({b.module}) stands {over:.2f} mm out of "
                                   f"the envelope{' on a rail' if on_rail else ''}.", [b.obj.Name]))

    # collisions between modules
    for i, a in enumerate(bs):
        for b in bs[i + 1:]:
            if a.module == b.module or not a.shape.BoundBox.intersect(b.shape.BoundBox):
                continue
            try:
                v = a.shape.common(b.shape).Volume
            except Part.OCCError:
                continue
            if v > TOL_VOL:
                out.append(Finding("error", "collision", f"{a.obj.Label} ({a.module}) and {b.obj.Label} "
                                   f"({b.module}) share {v:.3f} mm³.", [a.obj.Name, b.obj.Name]))

    # the board stack: what stands above and below each board, and the room it has
    boards = sorted(boards_of(sysobj), key=board_top_z)
    stack = []
    for k, board in enumerate(boards):
        top = board_top_z(board)
        t = getattr(board, "BoardThickness", None)
        bottom = top - (float(getattr(t, "Value", t)) if t is not None else 1.6)
        mine = [b for b in bs if b.module == board.Label]
        above = max([b.shape.BoundBox.ZMax for b in mine] + [top]) - top
        below = bottom - min([b.shape.BoundBox.ZMin for b in mine] + [bottom])
        room_up = (board_top_z(boards[k + 1]) - (float(getattr(getattr(boards[k + 1], "BoardThickness", 1.6),
                                                                  "Value", 1.6))) - top) if k + 1 < len(boards) else None
        # whatever of another module is above this board's area: the next board, the antenna, a frame
        bb_board = model.container_shape(board).BoundBox
        others_above = [b for b in bs if b.module not in (board.Label, "Stack") and b.shape.BoundBox.ZMin >= top - 1e-6
                        and b.shape.BoundBox.XMin < bb_board.XMax and b.shape.BoundBox.XMax > bb_board.XMin
                        and b.shape.BoundBox.YMin < bb_board.YMax and b.shape.BoundBox.YMax > bb_board.YMin]
        gap_up, nearest = None, None
        if others_above:
            low = min(others_above, key=lambda b: b.shape.BoundBox.ZMin)
            gap_up, nearest = low.shape.BoundBox.ZMin - (top + above), low
        stack.append(dict(board=board.Label, top=round(top, 3), above=round(above, 3), below=round(below, 3),
                          room_up=None if room_up is None else round(room_up, 3),
                          clearance_up=None if gap_up is None else round(gap_up, 3),
                          nearest_up=None if nearest is None else f"{nearest.obj.Label} ({nearest.module})"))
        if above > pcb.MAX_ABOVE + 1e-6:
            out.append(Finding("warning", "stack", f"{board.Label}: parts stand {above:.2f} mm above the board "
                                                   f"(CubeSat Kit: <= {pcb.MAX_ABOVE:g} mm at 15 mm pitch); the "
                                                   "board above needs a taller spacer.", [board.Name]))
        if below > pcb.MAX_BELOW + 1e-6:
            out.append(Finding("warning", "stack", f"{board.Label}: parts reach {below:.2f} mm below the board "
                                                   f"(<= {pcb.MAX_BELOW:g} mm).", [board.Name]))
        if gap_up is not None and gap_up < min_gap:
            lvl = "error" if gap_up <= 0 else "warning"
            out.append(Finding(lvl, "stack", f"{board.Label}: {gap_up:.2f} mm from its tallest part to "
                                             f"{nearest.obj.Label} ({nearest.module}) above (>= {min_gap:g} mm).",
                               [board.Name]))

    # mass and centre of gravity
    known = [b for b in bs if b.mass_g is not None]
    unknown = [b for b in bs if b.mass_g is None]
    total = sum(b.mass_g for b in known)
    cg = V()
    for b in known:
        cg = cg + _com(b.shape) * (b.mass_g / total) if total > 0 else cg
    if unknown:
        out.append(Finding("warning", "mass", f"{len(unknown)} parts have no material and no MassOverride: "
                           + ", ".join(sorted({b.obj.Label for b in unknown})[:8]),
                           [b.obj.Name for b in unknown]))
    if total / 1000 > limits["mass_kg"]:
        out.append(Finding("error", "mass", f"{total / 1000:.3f} kg (<= {limits['mass_kg']:.2f} kg)."))
    else:
        out.append(Finding("info", "mass", f"{total:.1f} g of {limits['mass_kg'] * 1000:.0f} g allowed."))
    lim = limits["cg_mm"]
    off = (cg.x, cg.y, cg.z)
    bad = [a for a, o, l in zip("XYZ", off, lim) if abs(o) > l]
    out.append(Finding("error" if bad else "info", "centre of gravity",
                       f"({cg.x:+.1f}, {cg.y:+.1f}, {cg.z:+.1f}) mm from the geometric centre "
                       f"(within ±{lim[0]:g}/±{lim[1]:g}/±{lim[2]:g} mm)"
                       + (f"; out in {', '.join(bad)}" if bad else "") + "."))
    by_module = {}
    for b in known:
        by_module[b.module] = by_module.get(b.module, 0.0) + b.mass_g
    summary = dict(mass_g=round(total, 2), cg_mm=tuple(round(c, 2) for c in off), stack=stack,
                   mass_by_module={k: round(v, 2) for k, v in sorted(by_module.items(), key=lambda kv: -kv[1])},
                   limits=limits["source"])
    return out, summary
