# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
How a port is drawn: an opaque face, in front of the faces it lies in.

A port on an edge stands exactly in the plane of that body's side face (a
wave port on a board's edge face, a lumped port on a trace's end), and one
that reaches down to the ground lies in the side faces of every layer in
between. Drawn like any other face, coplanar surfaces fight over each pixel;
at the 50 % transparency ports used to have, the edges behind showed through
too, and the port read as an outline rather than a surface.

So a port is opaque, and its faces are pulled towards the viewer by a
polygon offset — FreeCAD's own "Flat Lines" offset pushes faces back so
their edges show, and for a port it is turned around. Only the picture
changes; the geometry is untouched.

The offset is in the Coin scene, which FreeCAD rebuilds when a document is
opened, so it is applied from attach(). Saved documents reference this
class by its path: keep it at pkgstudio.gui.port_view.PortView. Ports from
before it existed are plain Part::Features, which take no proxy; they get
the colour and the offset for the session whenever the Pads panel opens.
"""

#: towards the viewer; one depth unit wins against a coplanar face
_FACTOR, _UNITS = -1.0, -1.0


def bring_forward(vobj):
    """Turn every polygon offset in *vobj*'s scene towards the viewer;
    returns how many were turned."""
    try:
        from pivy import coin
    except ImportError:
        return 0
    turned, stack = 0, [vobj.RootNode]
    while stack:
        node = stack.pop()
        if isinstance(node, coin.SoPolygonOffset):
            node.factor.setValue(_FACTOR)
            node.units.setValue(_UNITS)
            turned += 1
        if hasattr(node, "getNumChildren"):
            stack.extend(node.getChild(i) for i in range(node.getNumChildren()))
    return turned


class PortView:
    """View provider proxy of a port."""

    def __init__(self, vobj=None):
        if vobj is not None:
            vobj.Proxy = self

    def attach(self, vobj):
        bring_forward(vobj)

    def onChanged(self, vobj, prop):
        if prop == "DisplayMode":
            bring_forward(vobj)

    def updateData(self, _obj, _prop):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def __getstate__(self):
        return None

    def __setstate__(self, _state):
        return None


def adopt(obj, colour):
    """Draw port *obj* as an opaque *colour* face in front of its plane."""
    vo = obj.ViewObject
    if hasattr(vo, "Proxy") and not isinstance(vo.Proxy, PortView):
        PortView(vo)
    # giving a view provider its proxy resets it to grey: colour after
    vo.ShapeColor = tuple(colour)
    vo.LineColor = (0.55, 0.05, 0.05)
    vo.Transparency = 0
    vo.DisplayMode = "Flat Lines"
    bring_forward(vo)
