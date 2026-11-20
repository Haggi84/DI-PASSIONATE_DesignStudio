# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A grid and rulers drawn on the face contact points are placed on, and a
crosshair that follows the mouse.

Clicking a point on a bare board face is guesswork without a scale: the
grid shows the face's own coordinates (the panel's origin and axes), the
rulers along two edges give the millimetres, and the crosshair shows where
a click would land, snapped, before it is made.

All of it is temporary document objects in one group, not pickable and of
no role, so nothing exports or checks them; the panel removes them when it
closes or the face changes. Drawn a hair above the face, so the face does
not hide them. Labels are Draft texts lying in the face; without the Draft
workbench the grid is drawn without them.
"""

import FreeCAD
import Part

from ..core import contacts as ct

V = FreeCAD.Vector
GROUP_LABEL = "Contact grid (temporary)"
MINOR, MAJOR, CROSS = (0.62, 0.66, 0.70), (0.25, 0.30, 0.38), (0.90, 0.10, 0.10)


def _view(obj, rgb, width, selectable=False):
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return
    for name, value in (("LineColor", rgb), ("LineWidth", width), ("PointSize", 1.0),
                        ("Selectable", selectable)):
        try:
            setattr(vo, name, value)
        except Exception:
            pass


class FaceGrid:
    def __init__(self, doc, frame, width, height, origin_is_corner=True):
        self.doc, self.frame = doc, frame
        self.width, self.height = width, height
        self.corner = origin_is_corner
        self.group = None
        self.cross = None
        self.major = self.minor = 0.0
        size = max(width, height, 1e-6)
        self.lift = size * 2e-4                 # just above the face
        self.text_size = size * 0.025

    def _xyz(self, x, y):
        return V(*self.frame.to_xyz((x, y))) + V(*self.frame.n) * self.lift

    def _lines(self, values_x, values_y, x0, x1, y0, y1):
        edges = [Part.LineSegment(self._xyz(x, y0), self._xyz(x, y1)).toShape() for x in values_x]
        edges += [Part.LineSegment(self._xyz(x0, y), self._xyz(x1, y)).toShape() for y in values_y]
        return Part.makeCompound(edges) if edges else None

    def _add(self, name, shape, rgb, width):
        obj = self.doc.addObject("Part::Feature", name)
        obj.Shape = shape
        self.group.addObject(obj)
        _view(obj, rgb, width)
        return obj

    def show(self, major=None):
        """Draw the grid and rulers; *major* spacing in mm, or None for one
        that gives about ten lines across the face."""
        self.remove()
        self.major, self.minor = ct.grid_steps(self.width, self.height, major)
        self.group = self.doc.addObject("App::DocumentObjectGroup", "ContactGrid")
        self.group.Label = GROUP_LABEL
        if self.corner:
            x0, x1, y0, y1 = 0.0, self.width, 0.0, self.height
            xs = lambda step: ct.grid_values(self.width, step)
            ys = lambda step: ct.grid_values(self.height, step)
        else:
            # the chip frame: lines through the origin, out to the face's size each way
            x0, x1, y0, y1 = -self.width, self.width, -self.height, self.height
            xs = lambda step: [-v for v in ct.grid_values(self.width, step)[:0:-1]] + ct.grid_values(self.width, step)
            ys = lambda step: [-v for v in ct.grid_values(self.height, step)[:0:-1]] + ct.grid_values(self.height, step)
        minor = self._lines(xs(self.minor), ys(self.minor), x0, x1, y0, y1)
        if minor is not None:
            self._add("GridMinor", minor, MINOR, 1.0)
        major_x, major_y = xs(self.major), ys(self.major)
        major_shape = self._lines(major_x, major_y, x0, x1, y0, y1)
        if major_shape is not None:
            self._add("GridMajor", major_shape, MAJOR, 2.0)
        self._labels(major_x, major_y, x0, y0)
        self.cross = None
        self.doc.recompute()
        return self.major, self.minor

    def _labels(self, xs, ys, x0, y0):
        """The ruler: each major line's value just outside the face, along
        the X edge and the Y edge, lying in the face."""
        try:
            import Draft
        except Exception:
            return
        rot = FreeCAD.Rotation(V(*self.frame.u), V(*self.frame.v), V(*self.frame.n), "ZXY")
        off = 1.6 * self.text_size
        labels = [(f"{x:g}", x - 0.3 * self.text_size, y0 - off) for x in xs]
        labels += [(f"{y:g}", x0 - off * 1.8, y - 0.4 * self.text_size) for y in ys]
        labels += [("X (mm)", xs[-1] + off if xs else off, y0 - off), ("Y (mm)", x0 - off * 1.8, (ys[-1] if ys else 0) + off)]
        for text, x, y in labels:
            try:
                t = Draft.make_text([text], placement=FreeCAD.Placement(self._xyz(x, y), rot))
                vo = getattr(t, "ViewObject", None)
                if vo is not None:
                    vo.FontSize = self.text_size
                    vo.TextColor = MAJOR
                    try:
                        vo.DisplayMode = "World"
                    except Exception:
                        pass
                    try:
                        vo.Selectable = False
                    except Exception:
                        pass
                self.group.addObject(t)
            except Exception:
                return

    def move_cross(self, x, y):
        """The crosshair at (x, y) of the frame: where a click lands."""
        if self.group is None:
            return
        r = max(self.minor, 1e-6)
        c = self._xyz(x, y) + V(*self.frame.n) * self.lift
        u, v = V(*self.frame.u) * r, V(*self.frame.v) * r
        shape = Part.makeCompound([Part.LineSegment(c - u, c + u).toShape(),
                                   Part.LineSegment(c - v, c + v).toShape(),
                                   Part.makeCircle(0.35 * r, c, V(*self.frame.n))])
        if self.cross is None:
            self.cross = self._add("GridCursor", shape, CROSS, 2.0)
        else:
            self.cross.Shape = shape

    def remove(self):
        if self.group is None:
            return
        names = [o.Name for o in self.group.Group] + [self.group.Name]
        for n in names:
            try:
                self.doc.removeObject(n)
            except Exception:
                pass
        self.group = self.cross = None
