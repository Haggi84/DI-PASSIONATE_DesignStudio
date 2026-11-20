# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Placing one thing on another: bottom centre onto a target, up along a normal.

Every placement in the workbench is the same operation — a chip into a
housing cavity, a lid onto a housing, the whole package onto a board's
footprint: take the object's bottom centre (in its own frame), turn the
object so its +Z follows the target's normal and by a quarter-turn step
about it, and put the bottom centre on the target point. Centring is what
makes the result symmetric: on a face it is the face's centre, on a set of
footprint pads it is the centre of their combined outline.
"""

import FreeCAD

V = FreeCAD.Vector


def bottom_centre(bbox):
    return V((bbox.XMin + bbox.XMax) / 2.0, (bbox.YMin + bbox.YMax) / 2.0, bbox.ZMin)


def placement_onto(local_bbox, target, normal=V(0, 0, 1), turn_deg=0.0):
    """
    The Placement that puts the bottom centre of an object whose shape has
    *local_bbox* (in its own frame) on *target*, its +Z along *normal*,
    turned *turn_deg* about that normal.
    """
    n = V(normal)
    if n.Length < 1e-12:
        raise ValueError("the target has no normal")
    n.normalize()
    align = FreeCAD.Rotation(V(0, 0, 1), n)
    spin = FreeCAD.Rotation(V(0, 0, 1), float(turn_deg))
    rot = align.multiply(spin)
    base = bottom_centre(local_bbox)
    return FreeCAD.Placement(V(target) - rot.multVec(base), rot)


def face_target(face):
    """(centre, outward normal) of a planar face, in its shape's frame."""
    u0, u1, v0, v1 = face.ParameterRange
    normal = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    bb = face.BoundBox
    centre = V((bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0, (bb.ZMin + bb.ZMax) / 2.0)
    return centre, normal


def faces_target(faces):
    """(centre, normal) for a set of coplanar faces — e.g. a footprint's pads:
    the centre of their combined outline, on their surface, and their common
    normal. Raises ValueError when they are not parallel."""
    if not faces:
        raise ValueError("no faces")
    normals = [face_target(f)[1] for f in faces]
    n = normals[0]
    for m in normals[1:]:
        if n.getAngle(m) > 1e-3:
            raise ValueError("the selected faces do not face the same way")
    bb = faces[0].BoundBox
    for f in faces[1:]:
        bb.add(f.BoundBox)
    centre = V((bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0, (bb.ZMin + bb.ZMax) / 2.0)
    return centre, n
