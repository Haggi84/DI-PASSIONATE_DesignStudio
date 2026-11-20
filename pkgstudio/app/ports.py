# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Ports: rectangles where a simulation feeds and measures the model.

A port is a flat rectangle of a set width and height in the plane of a face
the user picks — a board's edge face for a wave port across a trace, say, or
any face for a lumped port between two conductors. Its centre is given in
that face's coordinates (core/contacts: from the face's corner, or from the
chip's centre), so it is placed exactly, and it may reach beyond the face:
a wave port is larger than the board edge it stands on.

A port is a document object of role "port" inside the face's container, so
it moves with the board. It carries what a solver needs: its number, type
(lumped or wave), reference impedance and, for a lumped port, the direction
of excitation in its own plane (along its width, X, or its height, Y). The
exports write each as a named surface, and ports.json lists them all.

A port can also be drawn on an edge (add_edge_port): standing on a piece of
a chip layer's or a board's edge and reaching up or down in Z, by a typed
height or to the next edge above or below (core/edgeport). It is then an
ordinary port in a vertical frame of its own, so everything above applies.

In the view a port is an opaque face drawn in front of the faces it lies in
(gui/port_view): a port on an edge is coplanar with the side faces there,
and see-through it read as an outline rather than a surface.
"""

import FreeCAD
import Part

from ..core import contacts as ct, edgeport as ep
from . import model

V = FreeCAD.Vector
ROLE = "port"
GROUP = "Port"
KINDS = ["lumped", "wave"]
DIRECTIONS = ["+X", "-X", "+Y", "-Y"]


def ports(doc):
    """Every port, in the order they were made."""
    return sorted(model.by_role(doc, ROLE), key=lambda o: o.PortIndex)


def _rect(w, h):
    return Part.Face(Part.makePolygon([V(-w / 2, -h / 2, 0), V(w / 2, -h / 2, 0), V(w / 2, h / 2, 0),
                                       V(-w / 2, h / 2, 0), V(-w / 2, -h / 2, 0)]))


def _place(obj, frame, xy):
    rot = FreeCAD.Rotation(V(*frame.u), V(*frame.v), V(*frame.n), "ZXY")
    obj.Placement = model.to_local(obj, FreeCAD.Placement(V(*frame.to_xyz(xy)), rot))
    obj.PortX, obj.PortY = xy


def add_port(container, frame, xy, w, h, kind="lumped", z0=50.0, direction="+Y", name=None):
    """A *w* x *h* mm port centred at (x, y) mm of *frame*, in *container*."""
    doc = container.Document
    k = max([o.PortIndex for o in ports(doc)], default=0) + 1
    # FeaturePython only for its view provider, which draws the port in
    # front of coplanar faces; the shape is set here, there is no recipe.
    obj = doc.addObject("Part::FeaturePython", "Port")
    obj.Label = name or f"Port{k}"     # not "P1": chip pads are often called that
    obj.Shape = _rect(w, h)
    model.set_role(obj, ROLE)
    p = obj.addProperty
    p("App::PropertyInteger", "PortIndex", GROUP, "Port number (Palace's Index)")
    p("App::PropertyEnumeration", "PortType", GROUP, "lumped: across a gap; wave: a line's cross-section")
    p("App::PropertyFloat", "Impedance", GROUP, "Reference impedance, ohm")
    p("App::PropertyEnumeration", "Direction", GROUP, "Lumped port: excitation along its width (X) or height (Y)")
    p("App::PropertyBool", "Excited", GROUP, "Driven in the simulation")
    p("App::PropertyLength", "PortWidth", GROUP, "Along the face's X")
    p("App::PropertyLength", "PortHeight", GROUP, "Along the face's Y")
    p("App::PropertyDistance", "PortX", GROUP, "Centre, X in the face it was placed on")
    p("App::PropertyDistance", "PortY", GROUP, "Centre, Y in the face it was placed on")
    obj.PortIndex = k
    obj.PortType = KINDS
    obj.PortType = kind
    obj.Impedance = float(z0)
    obj.Direction = DIRECTIONS
    obj.Direction = direction
    obj.Excited = True
    obj.PortWidth, obj.PortHeight = w, h
    container.addObject(obj)
    model._frame_props(obj, frame)
    _place(obj, frame, xy)
    show_as_face(obj)
    return obj


PORT_COLOUR = (0.85, 0.15, 0.15)


def show_as_face(obj):
    """Draw port *obj* as an opaque face in front of the faces it lies in
    (for ports from older documents too). Nothing without a GUI."""
    vo = getattr(obj, "ViewObject", None)
    if vo is None or not FreeCAD.GuiUp:
        return
    try:
        from ..gui import port_view
        port_view.adopt(obj, PORT_COLOUR)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[PackageStudio] {obj.Label}: port view not set: {exc}\n")


def add_edge_port(container, start, end, height, kind="lumped", z0=50.0, direction="+Y", name=None):
    """
    A port standing on the edge piece *start*-*end* (global, mm) and reaching
    *height* mm in Z (negative: down), in *container*. Its frame is its own
    vertical plane through the edge, centred on the rectangle; "+Y" excites
    it from its bottom edge to its top.
    """
    frame, w, h = ep.frame(tuple(start), tuple(end), float(height))
    return add_port(container, frame, (0.0, 0.0), w, h, kind, z0, direction, name)


_NOT_AN_EDGE = (model.LAYER, model.PAD, model.WIRE, ROLE, model.UNUSED_LEVEL)


def _shown(obj):
    return bool(getattr(obj, "Visibility", True))


def chip_levels(chip, start, end):
    """Levels (Z offsets from *start*, global) of *chip*'s shown layers under
    or over the port *start*-*end*, from its GDS polygons."""
    L, T = model.chip_layout(chip), model.chip_technology(chip)
    g = model.global_placement(chip)
    inv = g.inverse()
    a, b = inv.multVec(V(*start)), inv.multVec(V(*end))
    polygons, spans = {}, {}
    for layer in chip.Group:
        if model.role(layer) != model.LAYER or not _shown(layer):
            continue
        key = (layer.GdsLayer, layer.GdsDatatype)
        if key in T.layers and key in L.polygons:
            polygons[key] = L.polygons[key]
            spans[key] = (T.layers[key].z0, T.layers[key].z1)
    local = ep.layout_levels(polygons, spans, tuple(a), tuple(b))
    # back to global offsets: the chip may sit on a paddle, or be turned
    return [round(g.multVec(V(a.x, a.y, a.z + dz)).z - start[2], 9) for dz in local]


def edge_shapes(doc, skip=()):
    """The shown B-rep geometry a port can end at, global: boards, housings,
    lids, die bodies, shown dielectrics. Chip layers are meshes (their levels
    come from chip_levels); pads, wires, ports and the faint slabs of levels
    the layout does not draw are not edges a port ends at."""
    shapes = []
    for obj in doc.Objects:
        if obj.Name in skip or not _shown(obj):
            continue
        if model.role(obj) in _NOT_AN_EDGE or model.is_contact(obj):
            continue
        if not obj.isDerivedFrom("Part::Feature") or obj.isDerivedFrom("Part::Datum"):
            continue
        parent = obj.getParentGeoFeatureGroup()
        if parent is not None and not _shown(parent):
            continue
        shape = obj.Shape
        if shape.isNull():
            continue
        shape = shape.copy()
        shape.transformShape(model.parent_frame(obj).toMatrix())
        shapes.append(shape)
    return shapes


def next_edge_heights(doc, start, end, skip=()):
    """(up, down): the signed heights from the edge piece *start*-*end*
    (global) to the next edge above and below, None where there is none."""
    levels = set(ep.shape_levels(edge_shapes(doc, skip), tuple(start), tuple(end)))
    for chip in model.by_role(doc, model.CHIP):
        if _shown(chip):
            levels.update(chip_levels(chip, tuple(start), tuple(end)))
    levels = sorted(levels)
    return ep.next_level(levels, "+Z"), ep.next_level(levels, "-Z")


def move_port(obj, xy, frame=None):
    """Centre port *obj* at (x, y) of *frame* (default: the one it was made on)."""
    frame = frame or model.stored_frame(obj)
    model._frame_props(obj, frame)
    _place(obj, frame, xy)


def resize_port(obj, w, h):
    pl = obj.Placement
    obj.Shape = _rect(w, h)
    obj.Placement = pl
    obj.PortWidth, obj.PortHeight = w, h


def xy_in(obj, frame):
    return frame.to_uv(tuple(model.global_placement(obj).Base))


def mirror_ports(objs, axis, frame, centre=(0.0, 0.0), tol=1e-6):
    """Mirror images of *objs* over *frame*'s x or y centre line (or both),
    where no port is yet; each keeps its original's size and settings."""
    if not objs:
        return []
    doc = objs[0].Document
    have = [xy_in(o, frame) for o in ports(doc)]
    axes = ("x", "y", "both") if axis == "both" else (axis,)
    made = []
    for o in objs:
        for a in axes:
            q = ct.mirror_uv(xy_in(o, frame), a, centre)
            if any(abs(q[0] - h[0]) <= tol and abs(q[1] - h[1]) <= tol for h in have):
                continue
            have.append(q)
            made.append(add_port(model.parent_of(o), frame, q, o.PortWidth.Value, o.PortHeight.Value,
                                 o.PortType, o.Impedance, o.Direction))
    return made


def describe(obj):
    """What a solver needs of *obj*, global coordinates, mm and ohm."""
    g = model.global_placement(obj)
    axis = {"+X": V(1, 0, 0), "-X": V(-1, 0, 0), "+Y": V(0, 1, 0), "-Y": V(0, -1, 0)}[obj.Direction]
    vec = lambda v: [round(c, 12) for c in v]
    return {"name": obj.Label, "index": obj.PortIndex, "type": obj.PortType, "impedance_ohm": obj.Impedance,
            "excited": bool(obj.Excited), "centre_mm": vec(g.Base),
            "normal": vec(g.Rotation.multVec(V(0, 0, 1))),
            "x_axis": vec(g.Rotation.multVec(V(1, 0, 0))), "y_axis": vec(g.Rotation.multVec(V(0, 1, 0))),
            "width_mm": obj.PortWidth.Value, "height_mm": obj.PortHeight.Value,
            "direction": vec(g.Rotation.multVec(axis)) if obj.PortType == "lumped" else None,
            "element": f"Ports/{obj.Label}"}
