# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Placing chips, lids and packages — always centred, so symmetric.

One operation (core.placement.placement_onto) for all of them: the object's
bottom centre onto the centre of the selected face (or of several faces,
such as a footprint's pads), its +Z along their normal, turned in quarter
steps.

Placed onto a housing, a lid or a board, the object goes into it — into the
housing's package, or into the board — so it moves with what it stands on:
a chip on a board's copper is chip-on-board and goes where the board goes;
a package on a board's footprint is one part of the assembly. Its new place
becomes its base for Move (app.moving), offsets zero. Afterwards the wires
are regrouped with their pads and rebuilt.
"""

from ..core import placement as pl
from . import model, moving, wires


def owner_for(obj, onto):
    """The container *obj* goes into when placed on a face of *onto* (the
    object the face belongs to), or None to stay where it is."""
    if onto is None:
        return None
    owner = model.assembly_owner(onto)
    if owner is None or owner.Name == obj.Name or any(a.Name == obj.Name for a in model.ancestors(owner)):
        return None
    if model.role(obj) == model.BOARD:
        return None                       # a board is never put into something
    if model.role(obj) == model.PACKAGE and model.role(owner) == model.PACKAGE:
        return None                       # a package does not go into another one
    return owner


def place(obj, faces, turn_deg=0.0, onto=None):
    """Put *obj* (a chip, lid, housing or a package) on *faces* (global
    coordinates), centred; *onto* is what the faces belong to."""
    target, normal = pl.faces_target(faces) if len(faces) > 1 else pl.face_target(faces[0])
    owner = owner_for(obj, onto)
    if owner is not None:
        model.move_into(obj, owner)
    placement = pl.placement_onto(model.local_bbox(obj), target, normal, turn_deg)
    obj.Placement = model.to_local(obj, placement)
    moving.set_base(obj)
    obj.Document.recompute()
    wires.regroup(obj.Document)
    wires.update_all(obj.Document)
    return obj.Placement


def turn(obj, degrees):
    """Turn *obj* about its own vertical centre line, staying where it is —
    as a Move offset, so it can be typed back."""
    moving.nudge([obj], dangle=degrees)
