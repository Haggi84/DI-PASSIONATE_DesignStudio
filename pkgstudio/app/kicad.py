# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
KiCad both ways.

  import_board(doc, path, region)   the designer's .kicad_pcb as a Board
                                    (core.kicad_board): its stack-up, lines,
                                    vias, planes, and its footprints' pads
                                    as lands to place packages on
  export_package(housing, folder)   the package as a KiCad library: footprint
                                    (lands from the element spec, numbered
                                    as the leads), symbol (pins named with
                                    the nets the package LVS found), and the
                                    3-D model (STEP: body, leadframe — and
                                    the dies and wires, for the open cavity)
"""

import os
from types import SimpleNamespace

import FreeCAD

from ..core import export as ex, kicad_board, kicad_export, pcb
from . import boards, housings, materials, model, wires

V = FreeCAD.Vector


def import_board(doc, path, region=None, name=None, centre=True):
    """A Board from a KiCad board file. Returns (board, report)."""
    lib = set(materials.available(doc))
    ps, report = kicad_board.pieces(path, region=region, centre=centre, library=lib)
    top = report["top"]
    stack = SimpleNamespace(total=report["thickness"], top=top, bottom=0.0)
    label = name or os.path.splitext(os.path.basename(path))[0] + (" (cut-out)" if region else "")
    board = boards.make_board(doc, ps, label, stack)
    for k, kind, value in (("KicadFile", "App::PropertyFile", path),
                           ("KicadRegion", "App::PropertyString", str(region or ""))):
        if not hasattr(board, k):
            board.addProperty(kind, k, model.GROUP, "Where this board came from")
        setattr(board, k, value)
    return board, report


def _lands_by_lead(housing):
    """[{"number", "x", "y", "w", "h", "ep"}] (package frame, y up): the
    element's lands, each numbered with the lead whose finger lies on it."""
    found = housings.spec_of(housing)
    if not found:
        raise ValueError(f"{housing.Label} was not built from numbers (JEDEC or Element Forge): no lands to export")
    spec = found[0]
    lands, ep = pcb.qfn_lands(spec)
    inv = model.global_placement(housing).inverse()
    out, used = [], set()
    centre = getattr(housing, "BodyCentreTop", V())
    for lead in model.pads_of(housing):
        c = inv.multVec(model.pad_centre(lead)) - V(centre.x, centre.y, 0)
        hit = None
        for k, (r, _side) in enumerate(lands):
            if k not in used and r[0] - 1e-6 <= c.x <= r[2] + 1e-6 and r[1] - 1e-6 <= c.y <= r[3] + 1e-6:
                hit = k
                break
        if hit is None:
            raise ValueError(f"lead {lead.PadName}: no land under its finger ({c.x:.3f}, {c.y:.3f})")
        used.add(hit)
        r = lands[hit][0]
        out.append({"number": lead.PadName, "x": (r[0] + r[2]) / 2, "y": (r[1] + r[3]) / 2,
                    "w": r[2] - r[0], "h": r[3] - r[1], "ep": False})
    n = len(out) + 1
    out.append({"number": str(n), "x": (ep[0] + ep[2]) / 2, "y": (ep[1] + ep[3]) / 2, "w": ep[2] - ep[0],
                "h": ep[3] - ep[1], "ep": True})
    return out, spec


def export_package(housing, folder, name=None, lib="PackageStudio", kinds=None, with_dies=True):
    """
    The KiCad library for *housing*'s package in *folder*:
    <lib>.pretty/<name>.kicad_mod, <lib>.kicad_sym, <name>.step.
    Pin names: each lead's LeadNet (run the package LVS first), the exposed
    pad's: the paddle's net — the ground net when the pin list has one, else
    "EP". *kinds*: {net: kind}. Returns the three paths.
    """
    pads, spec = _lands_by_lead(housing)
    name = (name or spec["name"]).replace(" ", "_")
    os.makedirs(os.path.join(folder, f"{lib}.pretty"), exist_ok=True)
    kinds = kinds or {}
    nets = {p.PadName: getattr(p, "LeadNet", "") for p in model.pads_of(housing)}
    ground = next((n for n, k in kinds.items() if k == "ground"), "EP")
    step = os.path.join(folder, f"{name}.step")
    descr = (f"{spec['name']} ({spec['standard']}), open cavity, made with Package Studio; pins named by the "
             f"package LVS")
    mod = kicad_export.footprint(name, pads, (spec["body_x"], spec["body_y"]), descr,
                                 model_path=f"${{KIPRJMOD}}/{os.path.basename(step)}")
    mod_path = os.path.join(folder, f"{lib}.pretty", f"{name}.kicad_mod")
    with open(mod_path, "w", encoding="utf-8") as fh:
        fh.write(mod)
    pins = []
    for p in pads:
        if p["ep"]:
            pins.append({"number": p["number"], "name": ground, "kind": "ground" if ground != "EP" else "passive"})
        else:
            net = nets.get(p["number"], "")
            pins.append({"number": p["number"], "name": net or "NC", "kind": kinds.get(net, "signal") if net else "nc"})
    sym_path = os.path.join(folder, f"{lib}.kicad_sym")
    with open(sym_path, "w", encoding="utf-8") as fh:
        fh.write(kicad_export.symbol(name, pins, f"{lib}:{name}", descr))
    _step(housing, step, with_dies)
    return mod_path, sym_path, step


def _step(housing, path, with_dies):
    """The package as STEP in its own frame: centred on the body, underside at z = 0."""
    inv = model.global_placement(housing).inverse()
    centre = getattr(housing, "BodyCentreTop", V())
    bodies = [o for o in housing.Group if getattr(o, "HousingPart", "") in ("body", "leadframe")]
    move = FreeCAD.Placement(V(-centre.x, -centre.y, 0), FreeCAD.Rotation()).multiply(inv)

    def local(obj):
        s = obj.Shape.copy()
        s.Placement = move.multiply(model.global_placement(obj))
        return s

    elements = [(o.Label, local(o)) for o in bodies]
    if with_dies:
        for chip in housings.chips_on(housing):
            for o in chip.OutListRecursive:
                if model.role(o) == "die-body" and o.isDerivedFrom("Part::Feature"):
                    elements.append((chip.Label, local(o)))
        for w in wires.wires(housing.Document):
            c = wires.chip_of(w.ChipPad) if w.ChipPad is not None else None
            if c is not None and c in housings.chips_on(housing) and not w.Shape.isNull():
                elements.append((w.Label, local(w)))
    z0 = min(s.BoundBox.ZMin for _n, s in elements[:len(bodies)]) if bodies else 0.0
    shift = FreeCAD.Placement(V(0, 0, -z0), FreeCAD.Rotation())
    for _n, s in elements:
        s.Placement = shift.multiply(s.Placement)
    return ex.write_step(path, elements)
