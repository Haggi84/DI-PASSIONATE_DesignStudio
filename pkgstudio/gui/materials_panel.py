# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The materials panel: every element and what it is made of.

Most elements arrive with a material already — chip layers from the PDK,
housings from their analysis, wires from their metal. Here the rest are
set: select rows (or objects in the 3-D view), pick a material, Assign.
The properties of the material picked are shown below it, with their
source, so a value can be checked before it goes into a simulation. A
material the library lacks can be added; it is kept in the user's own
materials file.
"""

import FreeCADGui
from PySide import QtCore, QtWidgets

from ..app import exporting, materials
from ..core import materials as mat
from . import selection
from .panels import _Panel, _status

UNITS = {"density": "kg/m³", "k": "W/(m·K)", "cp": "J/(kg·K)", "cte": "ppm/K", "sigma": "S/m",
         "eps_r": "", "mu_r": "", "tan_d": ""}
LABELS = {"density": "Density", "k": "Thermal conductivity", "cp": "Specific heat", "cte": "CTE",
          "sigma": "Electrical conductivity", "eps_r": "Permittivity εr", "mu_r": "Permeability μr",
          "tan_d": "Loss tangent"}


class MaterialsPanel(_Panel):
    title = "Materials"

    def __init__(self):
        super().__init__()
        self.lib = materials.available(self.doc())
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Element", "Material"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.layout.addWidget(self.tree)

        row = QtWidgets.QHBoxLayout()
        self.choice = QtWidgets.QComboBox()
        self.choice.setEditable(True)
        self.choice.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.choice.addItems(sorted(self.lib))
        assign = QtWidgets.QPushButton("Assign to selected")
        row.addWidget(self.choice, 1)
        row.addWidget(assign)
        self.layout.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        auto = QtWidgets.QPushButton("Assign automatically")
        auto.setToolTip("PDK materials for the chip, mould compound and copper alloy for housings, "
                        "the wire metal for wires, FR-4 for a board. Keeps what is already set.")
        new = QtWidgets.QPushButton("New material…")
        row2.addWidget(auto)
        row2.addWidget(new)
        self.layout.addLayout(row2)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.layout.addWidget(self.info)
        self.finish()

        assign.clicked.connect(self.assign)
        auto.clicked.connect(self.auto)
        new.clicked.connect(self.new_material)
        self.choice.currentTextChanged.connect(self.show)
        self.tree.itemSelectionChanged.connect(self._picked)
        self.fill()
        self.show(self.choice.currentText())

    def fill(self):
        self.tree.clear()
        groups = {}
        self.items = []
        for e in exporting.elements(self.doc()):
            head = e.name.split("/", 1)[0]
            if head not in groups:
                groups[head] = QtWidgets.QTreeWidgetItem([head])
                self.tree.addTopLevelItem(groups[head])
            name = materials.material_of(e.obj)
            item = QtWidgets.QTreeWidgetItem([e.name.split("/", 1)[-1], name or "— not set —"])
            item.setData(0, QtCore.Qt.UserRole, len(self.items))
            if name and name not in self.lib:
                item.setToolTip(1, "Not in the library: exported by name only.")
            groups[head].addChild(item)
            self.items.append(e)
        self.tree.expandAll()
        missing = sum(1 for e in self.items if not materials.material_of(e.obj))
        _status(self.status, f"{len(self.items)} elements, {missing} without a material.", ok=not missing)

    def _picked(self):
        sel = self.tree.selectedItems()
        if len(sel) == 1 and sel[0].text(1) in self.lib:
            self.choice.setCurrentText(sel[0].text(1))

    def show(self, name):
        m = self.lib.get(name)
        if m is None:
            self.info.setText("")
            return
        lines = [f"<b>{m.name}</b> ({m.category})"]
        for f in mat.FIELDS:
            v = getattr(m, f)
            lines.append(f"{LABELS[f]}: {'—' if v is None else f'{v:g}'} {UNITS[f]}")
        lines.append(f"<i>{m.source}</i>")
        self.info.setText("<br>".join(lines))

    def _targets(self):
        idx = [i.data(0, QtCore.Qt.UserRole) for i in self.tree.selectedItems()
               if i.data(0, QtCore.Qt.UserRole) is not None]
        objs = [self.items[i].obj for i in idx]
        if not objs:
            picked = selection.objects()
            objs = [e.obj for e in self.items if e.obj in picked]
        return objs

    def assign(self):
        name = self.choice.currentText().strip()
        objs = self._targets()
        if not objs:
            return _status(self.status, "Select elements in the list or in the 3-D view.", False)
        self.doc().openTransaction("Assign material")
        for o in objs:
            materials.set_material(o, name)
        self.doc().commitTransaction()
        self.fill()
        note = "" if name in self.lib else " (not in the library — exported by name only)"
        _status(self.status, f"{name} assigned to {len(objs)} element(s){note}.")

    def auto(self):
        self.doc().openTransaction("Assign materials")
        n = materials.auto_assign(self.doc())
        self.doc().commitTransaction()
        self.fill()
        _status(self.status, f"{n} element(s) given their material.")

    def new_material(self):
        dlg = QtWidgets.QDialog(self.form)
        dlg.setWindowTitle("New material")
        form = QtWidgets.QFormLayout(dlg)
        name = QtWidgets.QLineEdit()
        cat = QtWidgets.QLineEdit()
        source = QtWidgets.QLineEdit()
        spins = {}
        form.addRow("Name", name)
        form.addRow("Category", cat)
        for f in mat.FIELDS:
            s = QtWidgets.QDoubleSpinBox()
            s.setDecimals(6)
            s.setRange(0, 1e12)
            spins[f] = s
            form.addRow(f"{LABELS[f]} {UNITS[f]}", s)
        form.addRow("Source", source)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec() or not name.text().strip():
            return
        m = mat.Material(name=name.text().strip(), category=cat.text().strip(), source=source.text().strip(),
                         **{f: spins[f].value() for f in mat.FIELDS})
        path = mat.save_user_material(m)
        self.lib = materials.available(self.doc())
        self.choice.clear()
        self.choice.addItems(sorted(self.lib))
        self.choice.setCurrentText(m.name)
        _status(self.status, f"{m.name} saved to {path}.")
