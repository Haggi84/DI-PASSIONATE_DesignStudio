# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Parametric positions: chips, lids, packages and boards moved by an offset
from where they were placed (core.moving), and their bond wires with them.

Every movable object carries, in its "Position" group:

    OffsetX, OffsetY   along the surface it sits on, mm
    OffsetAngle        turned about the surface normal, through its bottom centre
    PlaceBase          where Place put it (hidden)

Change an offset — in the Move panel, by dragging in the view, or in the
property editor — and the object goes there, and the wires on its pads are
rebuilt. Placing it again makes the new place its base, offsets zero.

Moved by other means since (FreeCAD's own Transform, or its Placement
typed), its base is recomputed from where it is before the next move, so
an offset never throws it back.

Works without the GUI; only the observer that follows property edits waits
for the GUI's event loop before rebuilding wires.
"""

import FreeCAD

from ..core import moving as mv
from . import model

GROUP = "Position"
PROPS = ("OffsetX", "OffsetY", "OffsetAngle")
MOVABLE = (model.CHIP, model.PACKAGE, model.BOARD, model.LID)

_busy = set()       # objects being moved here: the observer leaves them alone


def is_movable(obj):
    return model.role(obj) in MOVABLE and hasattr(obj, "Placement")


def movable(doc):
    """Everything that can be moved, chips first."""
    return sorted((o for o in doc.Objects if is_movable(o)), key=lambda o: (MOVABLE.index(model.role(o)), o.Label))


def ensure(obj):
    """Give *obj* its Position properties; its base is where it is now."""
    if hasattr(obj, "PlaceBase"):
        return
    obj.addProperty("App::PropertyPlacement", "PlaceBase", GROUP, "Where Place put it; offsets are from here")
    obj.addProperty("App::PropertyDistance", "OffsetX", GROUP, "Moved along the surface it sits on, X (mm)")
    obj.addProperty("App::PropertyDistance", "OffsetY", GROUP, "Moved along the surface it sits on, Y (mm)")
    obj.addProperty("App::PropertyAngle", "OffsetAngle", GROUP, "Turned about the surface normal, through its "
                                                                "bottom centre")
    obj.setEditorMode("PlaceBase", 2)
    _busy.add(obj.Name)
    try:
        obj.PlaceBase = obj.Placement
    finally:
        _busy.discard(obj.Name)


def pivot(obj):
    """The bottom centre of *obj*, in its own frame: what it turns about."""
    from ..core import placement as pl
    bb = model.local_bbox(obj)
    return pl.bottom_centre(bb) if bb.isValid() else FreeCAD.Vector()


def offsets(obj):
    if not hasattr(obj, "PlaceBase"):
        return 0.0, 0.0, 0.0
    return obj.OffsetX.Value, obj.OffsetY.Value, obj.OffsetAngle.Value


def expected(obj):
    """Where the base and offsets put *obj*."""
    dx, dy, a = offsets(obj)
    return mv.offset_placement(obj.PlaceBase, pivot(obj), dx, dy, a)


def set_base(obj):
    """Make where *obj* is now its base, offsets zero (after a Place)."""
    ensure(obj)
    _busy.add(obj.Name)
    try:
        obj.PlaceBase = obj.Placement
        obj.OffsetX = obj.OffsetY = 0.0
        obj.OffsetAngle = 0.0
    finally:
        _busy.discard(obj.Name)


def _follow_outside_moves(obj):
    """If *obj* was moved by other means, take that as its base."""
    if not mv.same(obj.Placement, expected(obj), 1e-7):
        dx, dy, a = offsets(obj)
        _busy.add(obj.Name)
        try:
            obj.PlaceBase = mv.rebase(obj.Placement, pivot(obj), dx, dy, a)
        finally:
            _busy.discard(obj.Name)


def apply(obj):
    """Put *obj* where its base and offsets say."""
    _busy.add(obj.Name)
    try:
        obj.Placement = expected(obj)
    finally:
        _busy.discard(obj.Name)


def set_offsets(obj, dx=None, dy=None, angle=None, update=True):
    """Set *obj*'s offsets (None leaves one as it is) and move it there."""
    ensure(obj)
    _follow_outside_moves(obj)
    _busy.add(obj.Name)
    try:
        if dx is not None:
            obj.OffsetX = float(dx)
        if dy is not None:
            obj.OffsetY = float(dy)
        if angle is not None:
            obj.OffsetAngle = float(angle)
    finally:
        _busy.discard(obj.Name)
    apply(obj)
    if update:
        rebuild_wires([obj])


def nudge(objs, ddx=0.0, ddy=0.0, dangle=0.0, step=0.0, update=True):
    """Move each of *objs* by (*ddx*, *ddy*) along its surface and turn it
    by *dangle*, each about its own centre; with *step*, the new offsets
    land on multiples of it."""
    for obj in objs:
        ensure(obj)
        _follow_outside_moves(obj)
        dx, dy, a = offsets(obj)
        set_offsets(obj, mv.snap(dx + ddx, step) if ddx else dx, mv.snap(dy + ddy, step) if ddy else dy,
                    a + dangle, update=False)
    if update:
        rebuild_wires(objs)


def drag(objs, delta, step=0.0, update=True, start=None):
    """
    Move *objs* together by the global vector *delta* — what the mouse
    moved over the surface. Each takes the part of it along its own surface;
    *start* ({name: (dx, dy, angle)} at the start of the drag) makes the
    move from there, so snapping does not accumulate.
    """
    for obj in objs:
        ensure(obj)
        if start is None or obj.Name not in start:
            _follow_outside_moves(obj)
        dx, dy, a = start[obj.Name] if start and obj.Name in start else offsets(obj)
        local = model.parent_frame(obj).Rotation.inverted().multVec(delta)
        ddx, ddy = mv.in_surface(obj.PlaceBase, local)
        set_offsets(obj, mv.snap(dx + ddx, step), mv.snap(dy + ddy, step), a, update=False)
    if update:
        rebuild_wires(objs)


# axes a move to a point may use: the letters of the global axes that count
AXES = ("XYZ", "XY", "XZ", "YZ", "X", "Y", "Z")


def masked(delta, axes="XYZ"):
    """*delta* with only the components of *axes* (e.g. "XY") kept."""
    axes = axes.upper()
    return FreeCAD.Vector(delta.x if "X" in axes else 0.0, delta.y if "Y" in axes else 0.0,
                          delta.z if "Z" in axes else 0.0)


def midpoint(points):
    """The mean of *points* (two points: their midpoint)."""
    points = [FreeCAD.Vector(p) for p in points]
    if not points:
        raise ValueError("no point")
    c = FreeCAD.Vector()
    for p in points:
        c += p
    return c * (1.0 / len(points))


REFERENCES = ("centre", "bottom")


def reference_point(objs, kind="centre"):
    """The point of *objs* (taken together) that a move to a point puts on
    the target, global: the centre of their bounding box, or the centre of
    its underside ("bottom")."""
    bb = FreeCAD.BoundBox()
    for o in objs:
        b = model.global_bbox(o)
        if b.isValid():
            bb.add(b)
    if not bb.isValid():
        raise ValueError("nothing with a shape to move")
    z = bb.ZMin if kind == "bottom" else bb.Center.z
    return FreeCAD.Vector(bb.Center.x, bb.Center.y, z)


def translate(objs, delta, update=True):
    """
    Move *objs* together by the global vector *delta*, in any direction.
    The part along the surface each stands on goes into its offsets (as a
    drag); the part across it (off the surface: a lift, or Z for a chip on
    a paddle) moves its base, so the offsets stay what they mean. Returns
    the largest distance moved across a surface, mm.
    """
    lifted = 0.0
    for obj in objs:
        ensure(obj)
        _follow_outside_moves(obj)
        dx, dy, a = offsets(obj)
        local = model.parent_frame(obj).Rotation.inverted().multVec(FreeCAD.Vector(delta))
        u, v, n = mv.surface_axes(obj.PlaceBase)
        ddx, ddy = local.dot(u), local.dot(v)
        across = n * local.dot(n)
        set_offsets(obj, dx + ddx, dy + ddy, a, update=False)
        if across.Length > 1e-12:
            _busy.add(obj.Name)
            try:
                obj.PlaceBase = FreeCAD.Placement(obj.PlaceBase.Base + across, obj.PlaceBase.Rotation)
            finally:
                _busy.discard(obj.Name)
            apply(obj)
            lifted = max(lifted, across.Length)
    if update:
        rebuild_wires(objs)
    return lifted


def move_to(objs, target, reference=None, axes="XYZ", kind="centre", update=True):
    """
    Move *objs* together so that *reference* (global; default their
    reference_point of *kind*) lands on *target*, along *axes* only ("XY":
    the Z stays). Returns (the move made, lifted as translate()).
    """
    if reference is None:
        reference = reference_point(objs, kind)
    delta = masked(FreeCAD.Vector(target) - FreeCAD.Vector(reference), axes)
    return delta, translate(objs, delta, update=update)


def reset(objs, update=True):
    """Back to where each was placed."""
    for obj in objs:
        if hasattr(obj, "PlaceBase"):
            set_offsets(obj, 0.0, 0.0, 0.0, update=False)
    if update:
        rebuild_wires(objs)


def rebuild_wires(objs):
    """Rebuild the wires on pads inside *objs*."""
    objs = [o for o in objs if o is not None]
    if not objs:
        return
    from . import wires
    doc = objs[0].Document
    ws = wires.wires_of(objs)
    wires.update_all(doc, ws)


def overlap_report(doc, objs=None):
    """Messages for chips that overlap one another (as boxes, in 3-D)."""
    chips = [c for c in (objs or model.by_role(doc, model.CHIP)) if model.role(c) == model.CHIP]
    boxes = [model.global_bbox(c) for c in chips]
    return [f"{chips[i].Label} and {chips[j].Label} overlap by {wx * 1000:.0f} × {wy * 1000:.0f} µm"
            for i, j, wx, wy in mv.overlaps(boxes)]


class OffsetObserver:
    """Follows offsets typed in the property editor: moves the object, and
    rebuilds its wires once the edit is over (never inside the callback,
    where a recompute would recurse into the property editor)."""

    def __init__(self, later=None):
        self.later = later

    def slotChangedObject(self, obj, prop):
        if prop not in PROPS or getattr(obj, "Name", None) in _busy or not hasattr(obj, "PlaceBase"):
            return
        try:
            apply(obj)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] {obj.Label}: not moved: {exc}\n")
            return
        if self.later is not None:
            self.later(lambda o=obj: rebuild_wires([o]) if o.Document is not None else None)
        else:
            rebuild_wires([obj])


_observer = None


def install_observer():
    """Once per session, with the GUI: offsets edited anywhere move things."""
    global _observer
    if _observer is not None:
        return _observer
    later = None
    if FreeCAD.GuiUp:
        from PySide import QtCore
        later = lambda fn: QtCore.QTimer.singleShot(0, fn)     # noqa: E731
    _observer = OffsetObserver(later)
    FreeCAD.addDocumentObserver(_observer)
    return _observer
