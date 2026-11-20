# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A suggestion (app.fixes) drawn in the 3-D view of the assembly.

The shapes come from the simulation model, which is in global coordinates,
and are drawn in the assembly the person works in, so they appear where the
parts are: the problem in red (wireframe, thick lines, so the part itself
stays visible), the conductors a fix would join in yellow, the fix in
green. They are plain features in one group, named PS_FixPreview…, which
the EM model and the readiness check skip; they are removed when another
finding is clicked, a fix is applied, or the panel closes. The view zooms
to them.
"""

import FreeCAD
import FreeCADGui

GROUP = "PS_FixPreview"
STYLES = {  # colour, transparency %, display mode, line width
    "problem": ((0.9, 0.1, 0.1), 0, "Wireframe", 4.0),
    "conductor": ((1.0, 0.8, 0.0), 55, "Shaded", 2.0),
    "proposed": ((0.1, 0.8, 0.25), 25, "Flat Lines", 3.0),
}


class FixPreview:
    def __init__(self):
        self.doc_name = None
        self.names = []

    def clear(self):
        doc = FreeCAD.getDocument(self.doc_name) if self.doc_name in FreeCAD.listDocuments() else None
        if doc is not None:
            for name in self.names + [GROUP]:
                if doc.getObject(name) is not None:
                    try:
                        doc.removeObject(name)
                    except Exception:
                        pass
            doc.recompute()
        self.names, self.doc_name = [], None

    def show(self, doc, suggestion):
        """Draw *suggestion* in *doc*, select what it is about, zoom to it."""
        self.clear()
        if doc is None or not suggestion.shapes:
            self._select(doc, suggestion)
            return
        self.doc_name = doc.Name
        group = doc.addObject("App::DocumentObjectGroup", GROUP)
        group.Label = "Fix preview (temporary)"
        for k, (label, shape, style) in enumerate(suggestion.shapes):
            obj = doc.addObject("Part::Feature", f"{GROUP}{k}")
            obj.Label = f"{label} — {style}"
            obj.Shape = shape
            group.addObject(obj)
            self.names.append(obj.Name)
            vo = getattr(obj, "ViewObject", None)
            if vo is None:
                continue
            colour, transparency, mode, width = STYLES.get(style, STYLES["problem"])
            for prop, value in (("ShapeColor", colour), ("LineColor", colour), ("Transparency", transparency),
                                ("DisplayMode", mode), ("LineWidth", width), ("Selectable", False)):
                try:
                    setattr(vo, prop, value)
                except Exception:
                    pass
        doc.recompute()
        self._select(doc, suggestion, zoom=True)

    def _select(self, doc, suggestion, zoom=False):
        if doc is None or not FreeCAD.GuiUp:
            return
        try:
            FreeCAD.setActiveDocument(doc.Name)
            FreeCADGui.setActiveDocument(doc.Name)
            FreeCADGui.Selection.clearSelection()
            for name in self.names or suggestion.select:
                if doc.getObject(name) is not None:
                    FreeCADGui.Selection.addSelection(doc.Name, name)
            if zoom or suggestion.select:
                FreeCADGui.SendMsgToActiveView("ViewSelection")
            FreeCADGui.Selection.clearSelection()
            for name in suggestion.select:          # the real objects stay selected
                obj = doc.getObject(name)
                if obj is not None:
                    root = obj
                    while root.getParentGeoFeatureGroup() is not None:
                        root = root.getParentGeoFeatureGroup()
                    sub = ""
                    if root is not obj:
                        path, o = [], obj
                        while o is not None and o is not root:
                            path.insert(0, o.Name)
                            o = o.getParentGeoFeatureGroup()
                        sub = ".".join(path) + "."
                    FreeCADGui.Selection.addSelection(doc.Name, root.Name, sub)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] fix preview: {exc}\n")
