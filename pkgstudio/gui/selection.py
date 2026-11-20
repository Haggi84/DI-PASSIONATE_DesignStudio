# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
What is selected in the 3-D view, in global coordinates.

Selections are taken unresolved — the top-level object and the full path to
the face — so a face of a body inside the Housing inside the Package comes
back where it really is, with every container's placement applied.
"""

import FreeCADGui
import Part

from ..app import model


def _entries():
    return FreeCADGui.Selection.getSelectionEx("", 0)


def faces():
    """[(face in global coordinates, the leaf object it belongs to)]."""
    out = []
    for sel in _entries():
        for sub in sel.SubElementNames:
            if "Face" not in sub.rsplit(".", 1)[-1]:
                continue
            try:
                face = Part.getShape(sel.Object, sub, needSubElement=True, transform=True)
            except Exception:
                continue
            if face.isNull() or face.ShapeType != "Face":
                continue
            leaf = sel.Object.getSubObject(sub, retType=1) or sel.Object
            out.append((face, leaf))
    return out


def objects():
    """The leaf objects selected (whole objects or through a sub-element)."""
    out = []
    for sel in _entries():
        subs = sel.SubElementNames or [""]
        for sub in subs:
            leaf = sel.Object.getSubObject(sub, retType=1) if sub else sel.Object
            leaf = leaf or sel.Object
            if leaf not in out:
                out.append(leaf)
    return out


def _element_point(shape, mode, picked):
    """One point for a selected sub-element *shape* (global)."""
    import FreeCAD
    if mode == "picked" and picked is not None:
        return FreeCAD.Vector(picked)
    t = shape.ShapeType
    if t == "Vertex":
        return FreeCAD.Vector(shape.Point)
    if t == "Edge":
        curve = getattr(shape, "Curve", None)
        if curve is not None and hasattr(curve, "Center") and hasattr(curve, "Radius"):
            return FreeCAD.Vector(curve.Center)          # a circle or arc: its centre
        a, b = shape.ParameterRange
        return shape.valueAt((a + b) / 2)
    if t == "Face":
        return FreeCAD.Vector(shape.CenterOfMass)
    return FreeCAD.Vector(shape.BoundBox.Center)


def points(mode="centre"):
    """
    The points selected in the view, global, in the order picked: one per
    sub-element — a vertex itself; an edge its midpoint, a circle or arc
    its centre; a face its centre — or, with *mode* "picked", the clicked
    point on it. A whole object selected counts as the centre of its
    bounding box. Points that coincide count once.
    """
    out = []
    for sel in _entries():
        subs = list(sel.SubElementNames)
        picked = list(getattr(sel, "PickedPoints", []) or [])
        if not subs:
            leaf = sel.Object
            out.append(model.global_bbox(leaf).Center)
            continue
        for k, sub in enumerate(subs):
            try:
                shape = Part.getShape(sel.Object, sub, needSubElement=True, transform=True)
            except Exception:
                continue
            if shape.isNull():
                continue
            out.append(_element_point(shape, mode, picked[k] if k < len(picked) else None))
    kept = []                       # the same element reached twice (through a link, a layer body) is one point
    for p in out:
        if all((p - q).Length > 1e-9 for q in kept):
            kept.append(p)
    return kept


def pads(side=None):
    return [o for o in objects() if model.role(o) == model.PAD and (side is None or o.PadSide == side)]


def container_of(obj, roles=model.CONTAINER_ROLES):
    """The Housing, Lid or Board *obj* is in, or None."""
    p = obj
    while p is not None:
        if model.role(p) in roles:
            return p
        p = p.getParentGeoFeatureGroup()
    return None


SURFACE_ITEMS = "surface-items"


def container_for(obj):
    """Where things placed on *obj*'s surface go, so they move with it: its
    Board, Housing or Lid; else the App::Part it is in (a STEP opened with
    File › Import, say); else a "Surface items" group of the document."""
    c = container_of(obj)
    if c is not None:
        return c
    part = obj.getParentGeoFeatureGroup()
    if part is not None:
        return part
    doc = obj.Document
    found = model.by_role(doc, SURFACE_ITEMS)
    if found:
        return found[0]
    grp = doc.addObject("App::DocumentObjectGroup", "SurfaceItems")
    grp.Label = "Surface items"
    model.set_role(grp, SURFACE_ITEMS)
    return grp


def _is_plane(face):
    return face.Surface.__class__.__name__ == "Plane"


def _normal(face):
    u0, u1, v0, v1 = face.ParameterRange
    return face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)


def faces_at_edge(shape, edge, tol=1e-6):
    """The flat faces of *shape* (global) the *edge* (global) lies on."""
    pts = [edge.Vertexes[0].Point, edge.Vertexes[-1].Point,
           edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2.0)]
    out = []
    for f in shape.Faces:
        if _is_plane(f) and all(f.distToShape(Part.Vertex(p))[0] < tol for p in pts):
            out.append(f)
    return out


def surface():
    """
    (flat face, leaf object, how) from the selection: a selected face, or,
    for a selected edge, the flat face beside it that stands most upright —
    for an edge on a board's top, the board's edge face, which is the plane
    a port across a trace at the edge lies in, and much harder to click than
    the edge itself. None if nothing usable is selected.
    """
    for sel in _entries():
        for sub in sel.SubElementNames:
            element = sub.rsplit(".", 1)[-1]
            try:
                shape = Part.getShape(sel.Object, sub, needSubElement=True, transform=True)
            except Exception:
                continue
            leaf = sel.Object.getSubObject(sub, retType=1) or sel.Object
            if element.startswith("Face") and shape.ShapeType == "Face":
                return shape, leaf, "face"
            if element.startswith("Edge") and shape.ShapeType == "Edge":
                owner = sub[:len(sub) - len(element)]
                try:
                    whole = Part.getShape(sel.Object, owner, needSubElement=False, transform=True)
                except Exception:
                    continue
                beside = faces_at_edge(whole, shape)
                if beside:
                    # most upright: its normal most nearly level
                    face = min(beside, key=lambda f: abs(_normal(f).z))
                    return face, leaf, "edge"
    return None


def pick_points(n, plane_point, plane_normal, prompt=None, on_point=None, on_move=None, tol=1e-3):
    """
    Clicks in the 3-D view, each as a global point on the plane through
    *plane_point* with *plane_normal*: where the click hits a shape lying in
    that plane, that point (exact in any camera), otherwise where the view
    ray meets the plane — so a click lands on the surface even between two
    shapes. *n* clicks, or with n None as many as the user makes until
    Escape. *on_point(p)*, if given, is called with each and may return
    False to refuse it; *on_move(p)* follows the mouse over the plane. A
    shape within *tol* of the plane counts as on it (a grid drawn just
    above, say). Returns the points kept; Escape with n set cancels
    (None). Runs its own event loop: a modal dialog must be hidden first, or
    the view never sees the clicks.
    """
    import FreeCAD
    from PySide import QtCore
    view = FreeCADGui.ActiveDocument.ActiveView
    points, loop = [], QtCore.QEventLoop()
    state = {"cancelled": False}
    normal = FreeCAD.Vector(plane_normal).normalize()

    def on_plane(q):
        return q - normal * (q - plane_point).dot(normal)

    def hit(x, y):
        info = view.getObjectInfo((x, y))
        if info and all(k in info for k in ("x", "y", "z")):
            q = FreeCAD.Vector(info["x"], info["y"], info["z"])
            if abs((q - plane_point).dot(normal)) < tol:
                return on_plane(q)
        p = view.getPoint(x, y)
        d = view.getViewDirection()
        denom = d.dot(normal)
        if abs(denom) < 1e-12:
            return None
        return p + d * ((plane_point - p).dot(normal) / denom)

    def on_click(info):
        if info.get("State") != "DOWN" or info.get("Button") != "BUTTON1":
            return
        q = hit(*info["Position"])
        if q is None or (on_point is not None and on_point(q) is False):
            return
        points.append(q)
        FreeCAD.Console.PrintMessage(f"[PackageStudio] point {len(points)}" + (f"/{n}" if n else "") + "\n")
        if n and len(points) >= n:
            loop.quit()

    def on_motion(info):
        q = hit(*info["Position"])
        if q is not None:
            on_move(q)

    def on_key(info):
        if info.get("Key") == "ESCAPE" and info.get("State") == "DOWN":
            state["cancelled"] = bool(n)
            loop.quit()

    if prompt:
        FreeCAD.Console.PrintMessage(f"[PackageStudio] {prompt}\n")
    cb1 = view.addEventCallback("SoMouseButtonEvent", on_click)
    cb2 = view.addEventCallback("SoKeyboardEvent", on_key)
    cb3 = view.addEventCallback("SoLocation2Event", on_motion) if on_move is not None else None
    try:
        loop.exec_() if hasattr(loop, "exec_") else loop.exec()
    finally:
        view.removeEventCallback("SoMouseButtonEvent", cb1)
        view.removeEventCallback("SoKeyboardEvent", cb2)
        if cb3 is not None:
            view.removeEventCallback("SoLocation2Event", cb3)
    return None if state["cancelled"] else points
