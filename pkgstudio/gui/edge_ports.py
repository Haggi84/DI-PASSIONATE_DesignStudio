# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Drawing ports on an edge in the 3-D view, one after another.

Three clicks make a port:

    1. click an edge (of a chip layer, a board, a housing): the port starts
       there
    2. click again along the edge: its length — or, with a typed length,
       only which way along the edge it runs
    3. move up or down and click: its height — the cursor's, a typed one,
       or to the next edge above or below — and which way it reaches

Then the next port. Esc abandons the port being drawn; Esc again ends.

A chip layer is a mesh in the view, and a mesh's triangle edges are not the
drawn polygon's, so a click on a layer takes the side of the GDS polygon
nearest the click, at the layer's top or bottom, whichever is nearer — the
exact edge, where a triangle's would not be. A click on a face of a B-rep
part takes that face's nearest edge: hitting a hairline exactly is luck.

The second click need not be on the edge: the port's end is the point of
the edge nearest the view ray. The height is where the ray meets the port's
own vertical plane, so what the preview shows is what is built.

Like selection.pick_points, this runs its own event loop, so the Pads panel
stays as it is and the view gets every click.
"""

import FreeCAD
import FreeCADGui
import Part

from ..app import model, ports
from ..core import edgeport as ep
from . import selection

V = FreeCAD.Vector
PREVIEW = "PS_EdgePortPreview"
FOLLOW, TYPED, NEXT = "follow", "typed", "next"
LENGTH_CLICKS, LENGTH_TYPED = "clicks", "typed"

_EDGE, _END, _HEIGHT = "edge", "end", "height"


def _closest_on_segment(a, b, p, d):
    """The point of segment *a*-*b* nearest the line *p* + t *d* (the view
    ray): the edge point the cursor means."""
    u = b - a
    uu, dd, ud = u.dot(u), d.dot(d), u.dot(d)
    w = a - p
    denom = uu * dd - ud * ud
    s = 0.0 if abs(denom) < 1e-18 else (ud * d.dot(w) - dd * u.dot(w)) / denom
    s = min(max(s, 0.0), 1.0)
    return a + u * s


def layer_edge(layer, click):
    """(start, end) global of the side of *layer*'s GDS polygon nearest the
    global point *click*, at the layer's top or bottom; None if none near."""
    chip = model.parent_of(layer)
    if chip is None:
        return None
    L, T = model.chip_layout(chip), model.chip_technology(chip)
    key = (layer.GdsLayer, layer.GdsDatatype)
    spec = T.layers.get(key)
    if spec is None:
        return None
    g = model.global_placement(chip)
    local = g.inverse().multVec(click)
    side = ep.nearest_side(L.polygons.get(key, []), local.x, local.y)
    if side is None:
        return None
    z = spec.z1 if abs(local.z - spec.z1) <= abs(local.z - spec.z0) else spec.z0
    (ax, ay), (bx, by) = side
    return g.multVec(V(ax, ay, z)), g.multVec(V(bx, by, z))


def part_edge(obj, component, click):
    """(start, end) global of the B-rep edge *component* of *obj*, or of the
    nearest edge of the face *component*; None for anything else. A curved
    edge gives its chord: a port is flat."""
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    try:
        element = shape.getElement(component).copy()
    except Exception:
        return None
    element.transformShape(model.parent_frame(obj).toMatrix())
    if element.ShapeType == "Face":
        tip = Part.Vertex(click)
        if not element.Edges:
            return None
        element = min(element.Edges, key=lambda e: e.distToShape(tip)[0])
    if element.ShapeType != "Edge" or len(element.Vertexes) < 2:
        return None
    return element.Vertexes[0].Point, element.Vertexes[-1].Point


class EdgePortDrawing:
    """
    The click-by-click state. *settings()* returns the panel's current
    choices — length mode and value, height mode and value, type, Z0,
    direction — read at each step, so changing them while drawing takes
    effect on the next move. *say(text, ok)* reports; *made(obj)* is told
    of each port.
    """

    def __init__(self, doc, settings, say, made, alive=None):
        self.doc, self._read_settings, self._say, self.made = doc, settings, say, made
        self._alive = alive
        self.view = FreeCADGui.ActiveDocument.ActiveView
        self.count = 0
        self.loop = None
        self.stopped = False
        self._last = settings()
        self._reset()

    # ── the panel the drawing reads from may close under it ──
    #
    # The Pads panel can be closed while a drawing runs: the view still sends
    # every mouse move, and reading the panel's deleted widgets raised
    # "Internal C++ object already deleted" on each one — hundreds a second.
    # The drawing keeps the settings it last read and ends instead. Qt deletes
    # a closed panel's widgets only later, so *alive()* — is the panel still
    # shown? — is asked first: the drawing ends as the panel closes, not at
    # the first read of a widget that has gone.
    def settings(self):
        if not self.stopped and self._alive is not None:
            try:
                shown = bool(self._alive())
            except Exception:               # deleted widget, form gone
                shown = False
            if not shown:
                self.stop("the Pads panel was closed")
        if not self.stopped:
            try:
                self._last = self._read_settings()
            except RuntimeError:
                self.stop("the Pads panel was closed")
        return self._last

    def say(self, text, ok=True):
        if self.stopped:
            return
        try:
            self._say(text, ok)
        except RuntimeError:
            self.stop("the Pads panel was closed")

    def stop(self, why=""):
        """End the drawing: the port being drawn is dropped, the ones made
        stay."""
        if self.stopped:
            return
        self.stopped = True
        if why:
            FreeCAD.Console.PrintMessage(f"[PackageStudio] Drawing ports on edges ended: {why}.\n")
        if self.loop is not None:
            self.loop.quit()

    def _guarded(self, handler):
        def call(info):
            self.settings()                 # ends the drawing if the panel has closed
            if self.stopped:
                return
            try:
                handler(info)
            except Exception as exc:
                import traceback
                FreeCAD.Console.PrintError(f"[PackageStudio] Drawing ports on edges stopped: {exc}\n"
                                           f"{traceback.format_exc()}")
                self.stop()
        return call

    # ── loop ──
    def run(self):
        from PySide import QtCore
        self.loop = QtCore.QEventLoop()
        callbacks = [
            ("SoMouseButtonEvent", self._guarded(self._on_button)),
            ("SoLocation2Event", self._guarded(self._on_motion)),
            ("SoKeyboardEvent", self._guarded(self._on_key)),
        ]
        handles = [(kind, self.view.addEventCallback(kind, fn)) for kind, fn in callbacks]
        self._hint()
        try:
            # quit() before exec() is lost: a drawing already stopped must
            # not start the loop, or nothing would ever end it
            if not self.stopped:
                self.loop.exec_() if hasattr(self.loop, "exec_") else self.loop.exec()
        finally:
            self.stopped = True
            for kind, handle in handles:
                try:
                    self.view.removeEventCallback(kind, handle)
                except Exception:           # the view was closed
                    pass
            try:
                self._clear_preview()
            except Exception:               # so was the document
                pass
        return self.count

    def _on_key(self, info):
        if info.get("Key") != "ESCAPE" or info.get("State") != "DOWN":
            return
        if self.state == _EDGE:
            self.stop()
        else:
            self._reset()
            self._clear_preview()
            self._hint()

    def _on_motion(self, info):
        pos = info.get("Position")
        if pos is None:
            return
        if self.state == _END:
            end = self._end_at(pos)
            if end is not None:
                self.end = end
                self._preview_line()
                self._hint()
        elif self.state == _HEIGHT:
            self.height = self._height_at(pos)
            self._preview_face()
            self._hint()

    def _on_button(self, info):
        if info.get("State") != "DOWN" or info.get("Button") != "BUTTON1":
            return
        pos = info.get("Position")
        try:
            if self.state == _EDGE:
                self._pick_edge(pos)
            elif self.state == _END:
                end = self._end_at(pos)
                if end is None or (end - self.start).Length < ep.MIN_EXTENT:
                    return self.say("Click further along the edge to give the port a length.", False)
                self.end = end
                self.state = _HEIGHT
                self.levels = None
            elif self.state == _HEIGHT:
                self.height = self._height_at(pos)
                if not self.height:
                    side = "above" if self._cursor_up(pos) else "below"
                    return self.say(f"No edge {side} the port to end it at — move the other "
                                    "way, or choose another height.", False)
                self._create()
        except Exception as exc:
            self.say(f"Port not drawn: {exc}", False)
        self._hint()

    # ── geometry from the cursor ──
    def _pick_edge(self, pos):
        info = self.view.getObjectInfo(tuple(pos))
        if not info or "Object" not in info:
            return self.say("Nothing there — click on or near an edge.", False)
        obj = self.doc.getObject(info["Object"])
        if obj is None or obj.Name == PREVIEW:
            return
        click = V(info["x"], info["y"], info["z"])
        if model.role(obj) == model.LAYER:
            found = layer_edge(obj, click)
        else:
            found = part_edge(obj, str(info.get("Component", "")), click)
        if found is None:
            return self.say(f"{obj.Label} has no edge there to stand a port on.", False)
        a, b = found
        self.owner = obj
        self.edge = (a, b)
        p, d = self._ray(pos)
        self.start = _closest_on_segment(a, b, p, d) if p is not None else click
        self.end = None
        self.state = _END

    def _ray(self, pos):
        try:
            return V(self.view.getPoint(tuple(pos))), V(self.view.getViewDirection())
        except Exception:
            return None, None

    def _end_at(self, pos):
        p, d = self._ray(pos)
        if p is None:
            return None
        on_edge = _closest_on_segment(self.edge[0], self.edge[1], p, d)
        s = self.settings()
        if s["length_mode"] == LENGTH_TYPED:
            try:
                return V(*ep.point_at_length(tuple(self.start), tuple(on_edge), s["length"]))
            except ValueError:
                return None
        return V(on_edge.x, on_edge.y, self.start.z)

    def _cursor_height(self, pos):
        """Where the view ray meets the port's vertical plane, as a height
        above the start; the cursor's own height when looking along it."""
        p, d = self._ray(pos)
        if p is None:
            return None
        along = self.end - self.start
        n = V(along.y, -along.x, 0.0)
        if n.Length < 1e-12:
            return p.z - self.start.z
        n.normalize()
        denom = d.dot(n)
        if abs(denom) < 1e-6:
            return p.z - self.start.z
        return (p + d * ((self.start - p).dot(n) / denom)).z - self.start.z

    def _cursor_up(self, pos):
        h = self._cursor_height(pos)
        return h is None or h >= 0

    def _height_at(self, pos):
        raw = self._cursor_height(pos)
        if raw is None:
            return self.height
        s = self.settings()
        if s["height_mode"] == TYPED:
            return abs(s["height"]) if raw >= 0 else -abs(s["height"])
        if s["height_mode"] == NEXT:
            if self.levels is None:
                self.levels = ports.next_edge_heights(self.doc, tuple(self.start), tuple(self.end),
                                                      skip=(PREVIEW,))
            up, down = self.levels
            return (up if raw >= 0 else down) or 0.0
        return raw

    # ── preview, creation ──
    def _preview(self):
        obj = self.doc.getObject(PREVIEW)
        if obj is None:
            obj = self.doc.addObject("Part::Feature", PREVIEW)
            obj.Label = "Port preview"
            vo = getattr(obj, "ViewObject", None)
            if vo is not None:
                vo.ShapeColor = vo.LineColor = ports.PORT_COLOUR
                vo.LineWidth = 2.0
                vo.Transparency = 30
                vo.Selectable = False
        return obj

    def _preview_line(self):
        if self.end is not None and (self.end - self.start).Length > ep.MIN_EXTENT:
            self._preview().Shape = Part.makeLine(self.start, self.end)
            FreeCADGui.updateGui()

    def _preview_face(self):
        if not self.height:
            return self._preview_line()
        fr, w, h = ep.frame(tuple(self.start), tuple(self.end), self.height)
        o, u, v = V(*fr.origin), V(*fr.u), V(*fr.v)
        corners = [o + u * sx * (w / 2) + v * sy * (h / 2) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        self._preview().Shape = Part.Face(Part.makePolygon(corners + corners[:1]))
        FreeCADGui.updateGui()

    def _clear_preview(self):
        if self.doc.getObject(PREVIEW) is not None:
            self.doc.removeObject(PREVIEW)
        FreeCADGui.updateGui()

    def _create(self):
        s = self.settings()
        container = selection.container_for(self.owner)
        self.doc.openTransaction("Port on an edge")
        try:
            obj = ports.add_edge_port(container, tuple(self.start), tuple(self.end), self.height,
                                      s["kind"], s["z0"], s["direction"])
            self.doc.recompute()
            self.doc.commitTransaction()
        except Exception:
            self.doc.abortTransaction()
            raise
        self.count += 1
        self.made(obj)
        self.say(f"{obj.Label}: {obj.PortWidth.Value:.4f} × {obj.PortHeight.Value:.4f} mm, "
                 f"{'up' if self.height > 0 else 'down'} from {self.owner.Label}. Next edge, or Esc.")
        self._reset()
        self._clear_preview()

    def _reset(self):
        self.state, self.owner, self.edge = _EDGE, None, None
        self.start = self.end = None
        self.height, self.levels = 0.0, None

    def _hint(self):
        s = self.settings()
        if self.state == _EDGE:
            text = "Click an edge where the port starts (Esc ends)."
        elif self.state == _END:
            text = ("Click along the edge to show which way the port runs." if s["length_mode"] == LENGTH_TYPED
                    else "Click again along the edge to set the port's length.")
            if self.end is not None:
                text += f"  Length {(self.end - self.start).Length:.4f} mm."
        else:
            text = ("Move up or down to choose the edge above or below, then click."
                    if s["height_mode"] == NEXT else "Move up or down to set the height, then click.")
            if self.height:
                text += f"  Height {abs(self.height):.4f} mm {'up' if self.height > 0 else 'down'}."
            elif s["height_mode"] == NEXT and self.levels is not None:
                text += "  No edge on this side."
        self.say(text)
