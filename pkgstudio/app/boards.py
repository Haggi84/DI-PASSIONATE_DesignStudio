# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Boards made by the workbench (core.pcb), not imported from STEP: a Board
container holding one object per piece — laminate, ground plane, lands,
lines, connectors, component bodies — each with its material, grouped by
what it belongs to. Everything else (placing a package on the lands, ports,
the EM model, putting the board into a system) treats it like any Board.
"""

import FreeCAD

from . import materials, model

V = FreeCAD.Vector


def _prop(obj, kind, name, value, tip=""):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, model.GROUP, tip)
    setattr(obj, name, value)


def make_board(doc, pieces, name="Board", stack=None):
    """A Board container from core.pcb Pieces. Returns it."""
    container = doc.addObject("App::Part", "Board")
    container.Label = name
    model.set_role(container, model.BOARD)
    if stack is not None:
        _prop(container, "App::PropertyLength", "BoardThickness", stack.total, "Board thickness")
        _prop(container, "App::PropertyLength", "BoardTop", stack.top, "z of the top copper's top (board frame)")
        _prop(container, "App::PropertyLength", "BoardBottom", stack.bottom, "z of the underside (board frame)")
    groups = {}
    for p in pieces:
        obj = doc.addObject("Part::Feature", "BoardPiece")
        obj.Label = p.name
        obj.Shape = p.shape
        _prop(obj, "App::PropertyString", "BoardPiece", p.kind, "What this piece of the board is")
        _prop(obj, "App::PropertyString", "BoardGroup", p.group, "What it belongs to on the board")
        for k, v in p.props.items():
            kind = {bool: "App::PropertyBool", int: "App::PropertyInteger", float: "App::PropertyFloat"}.get(
                type(v), "App::PropertyString")
            _prop(obj, kind, k, v)
        g = p.group or "Board"
        if g not in groups:
            grp = doc.addObject("App::DocumentObjectGroup", "BoardGroup")
            grp.Label = g
            container.addObject(grp)
            groups[g] = grp
        groups[g].addObject(obj)
        materials.set_material(obj, p.material)
        vo = getattr(obj, "ViewObject", None)
        if vo is not None and p.kind == "dielectric":
            vo.Transparency = 35
    doc.recompute()
    return container


def pieces(board, kind=None, group=None):
    out = []
    for o in materials._leaves(board):
        if kind is not None and getattr(o, "BoardPiece", "") != kind:
            continue
        if group is not None and getattr(o, "BoardGroup", "") != group:
            continue
        out.append(o)
    return out


def top_faces(objs, z=None, tol=1e-6):
    """The upward faces of *objs* at their top (global): what a package is put on."""
    out = []
    for o in objs:
        s = o.Shape.copy()
        s.Placement = model.global_placement(o)
        top = s.BoundBox.ZMax if z is None else z
        out += [f for f in s.Faces if abs(f.BoundBox.ZMin - top) < tol and abs(f.BoundBox.ZMax - top) < tol
                and f.normalAt(*f.Surface.parameter(f.CenterOfMass)).z > 0.99]
    return out


def place_package(package, board, footprint):
    """Put *package* on the lands of *footprint* (a group name on *board*):
    centred on them, into the board."""
    from . import placing
    lands = [o for o in pieces(board, "copper", footprint) if getattr(o, "Land", False)]
    if not lands:
        raise ValueError(f"{board.Label} has no footprint '{footprint}'")
    return placing.place(package, top_faces(lands), onto=board)
