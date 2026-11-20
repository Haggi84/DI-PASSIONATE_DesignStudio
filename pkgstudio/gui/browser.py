# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The layer browser: every layer and part, shown or hidden with one click.

A dock beside the 3-D view. Chip layers list their polygon count and height
and can be shown in full detail or as cluster blocks — that changes the view
only; the exports always write every polygon. Pads and wires toggle as sets.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from ..app import model, wires

DOCK_NAME = "PackageStudioLayers"
_dock = None


def _visible(obj):
    vo = getattr(obj, "ViewObject", None)
    return bool(vo and vo.Visibility)


def _set_visible(objs, on):
    for o in objs:
        vo = getattr(o, "ViewObject", None)
        if vo is not None:
            vo.Visibility = on


def _swatch(obj):
    vo = getattr(obj, "ViewObject", None)
    pix = QtGui.QPixmap(12, 12)
    try:
        r, g, b = vo.ShapeColor[:3]
        pix.fill(QtGui.QColor.fromRgbF(r, g, b))
    except Exception:
        pix.fill(QtCore.Qt.transparent)
    return QtGui.QIcon(pix)


class LayerBrowser(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Element", "View", "Polygons", "Height (µm)"])
        self.tree.setColumnWidth(0, 230)
        lay.addWidget(self.tree)
        row = QtWidgets.QHBoxLayout()
        for text, on in (("Show all", True), ("Hide all", False)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, v=on: self._all(v))
            row.addWidget(b)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        lay.addLayout(row)
        self._rows = {}            # tree item -> [objects]
        self._updating = False
        self.tree.itemChanged.connect(self._toggled)
        self.refresh()

    def _add(self, parent, text, objs, icon_obj=None):
        item = QtWidgets.QTreeWidgetItem([text])
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(0, QtCore.Qt.Checked if objs and any(_visible(o) for o in objs)
                           else QtCore.Qt.Unchecked)
        if icon_obj is not None:
            item.setIcon(0, _swatch(icon_obj))
        (parent.addChild if isinstance(parent, QtWidgets.QTreeWidgetItem) else parent.addTopLevelItem)(item)
        self._rows[id(item)] = (item, objs)
        return item

    def refresh(self):
        doc = FreeCAD.ActiveDocument
        self._updating = True
        self.tree.clear()
        self._rows = {}
        if doc is None:
            self._updating = False
            return
        for chip in model.by_role(doc, model.CHIP):
            top = self._add(self.tree, f"Chip — {chip.Label}  ({chip.Technology})",
                            [o for o in chip.Group])
            layers = sorted((o for o in chip.Group if model.role(o) == model.LAYER),
                            key=lambda o: (-o.Z1.Value, o.Label))
            for o in layers:
                it = self._add(top, o.Label, [o], o)
                it.setText(2, f"{o.Polygons:,}")
                it.setText(3, f"{o.Z0.Value * 1000:.3f} – {o.Z1.Value * 1000:.3f}")
                combo = QtWidgets.QComboBox()
                combo.addItems(["detail", "blocks"])
                combo.setCurrentText(o.Display)
                combo.setToolTip("The view only — exports always write every polygon.")
                combo.currentTextChanged.connect(lambda mode, obj=o: self._display(obj, mode))
                self.tree.setItemWidget(it, 1, combo)
            for o in chip.Group:
                if model.role(o) == model.DIE_BODY:
                    self._add(top, o.Label, [o], o)
            # Oxide and passivation, then the levels the PDK has and this
            # layout does not draw — each at its true height, top first.
            for group_role, title in ((model.DIELECTRIC, "Dielectrics"),
                                      (model.UNUSED_LEVEL, "Unused PDK levels")):
                slabs = sorted((o for o in chip.Group if model.role(o) == group_role),
                               key=lambda o: -o.Z1.Value)
                if not slabs:
                    continue
                g = self._add(top, f"{title} ({len(slabs)})", slabs)
                for o in slabs:
                    it = self._add(g, o.Label, [o], o)
                    it.setText(2, "—")
                    it.setText(3, f"{o.Z0.Value * 1000:.3f} – {o.Z1.Value * 1000:.3f}")
            chip_pads = model.pads_of(chip)
            if chip_pads:
                self._add(top, f"Chip pads ({len(chip_pads)})", chip_pads, chip_pads[0])
            top.setExpanded(True)
        for part_role in model.CONTAINER_ROLES:
            for c in model.by_role(doc, part_role):
                top = self._add(self.tree, f"{part_role.capitalize()} — {c.Label.split('—')[-1].strip()}",
                                [c])
                pk = model.pads_of(c)
                if pk:
                    self._add(top, f"Package pads ({len(pk)})", pk, pk[0])
        ws = wires.wires(doc)
        if ws:
            self._add(self.tree, f"Bond wires ({len(ws)})", ws, ws[0])
        self._updating = False

    def _toggled(self, item, column):
        if self._updating or column != 0:
            return
        entry = self._rows.get(id(item))
        if entry:
            on = item.checkState(0) == QtCore.Qt.Checked
            _set_visible(entry[1], on)
            if on:
                # Showing a layer inside a hidden chip shows nothing: show the parents too.
                p = item.parent()
                while p is not None:
                    pe = self._rows.get(id(p))
                    if pe and pe[1] and len(pe[1]) == 1:
                        _set_visible(pe[1], True)
                    p = p.parent()

    def _display(self, obj, mode):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            model.set_layer_display(obj, mode)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _all(self, on):
        for _item, objs in self._rows.values():
            _set_visible(objs, on)
        self.refresh()


def show():
    global _dock
    mw = FreeCADGui.getMainWindow()
    if _dock is None:          # closing a dock only hides it; create once
        _dock = QtWidgets.QDockWidget("Layers", mw)
        _dock.setObjectName(DOCK_NAME)
        _dock.setWidget(LayerBrowser())
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, _dock)
    _dock.widget().refresh()
    _dock.show()
    _dock.raise_()
    return _dock


def refresh():
    if _dock is not None and _dock.isVisible():
        _dock.widget().refresh()
