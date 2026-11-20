# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The second toolbar, for what goes beyond one die in one package:

    Arrange dies (MCM) · Wire the MCM · PC/104 board · CubeSat 1U ·
    Into the system · Integration check

Each is a thin layer over app.housings / app.wires / app.boards /
app.system, so a script does the same with the same functions
(examples/cubesat_sband_mcm.py).
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from ..app import housings, model, system, wires
from .commands import _Command, _doc, _fail, _mw
from .panels import _Panel


def _selected():
    return [s for s in FreeCADGui.Selection.getSelection()]


def _housing_in(objs):
    for o in objs:
        p = o
        while p is not None:
            if model.role(p) == model.HOUSING:
                return p
            if model.role(p) == model.PACKAGE:
                return next((c for c in p.Group if model.role(c) == model.HOUSING), None)
            p = p.getParentGeoFeatureGroup()
    return None


def _system_in(doc, objs=()):
    for o in objs:
        for p in [o] + model.ancestors(o):
            if model.role(p) == system.SYSTEM:
                return p
    found = model.by_role(doc, system.SYSTEM)
    return found[0] if found else None


class ArrangeDies(_Command):
    menu, icon_name = "Arrange dies (MCM)", "mcm"
    tip = ("Several dies on one paddle — a multi-chip module. Select the housing and the chips; they are "
           "placed in a row, a column or corner to corner, a gap apart, and checked: on the paddle, clear "
           "of each other and of the bond fingers. Fine-tune each with Move.")

    def Activated(self):
        try:
            sel = _selected()
            housing = _housing_in(sel)
            chips = [o for o in sel if model.role(o) == model.CHIP] or model.loose_chips(_doc())
            if housing is None or not chips:
                QtWidgets.QMessageBox.information(_mw(), self.menu, "Select a housing and the chips to put on "
                                                                    "its paddle (Ctrl+click).")
                return
            how, ok = QtWidgets.QInputDialog.getItem(_mw(), self.menu, f"{len(chips)} dies on {housing.Label}:",
                                                     ["corner to corner", "in a row (x)", "in a column (y)"], 0,
                                                     False)
            if not ok:
                return
            gap, ok = QtWidgets.QInputDialog.getDouble(_mw(), self.menu, "Gap between the dies, mm:", 0.3, 0.05,
                                                       5.0, 3)
            if not ok:
                return
            direction = {"corner to corner": "diagonal", "in a row (x)": "x", "in a column (y)": "y"}[how]
            issues = housings.place_chips(chips, housing, gap=gap, direction=direction)
            text = "\n".join(f"{i.level}: {i.message}" for i in issues) or "On the paddle, clear of each other " \
                                                                            "and of the bond fingers."
            QtWidgets.QMessageBox.information(_mw(), self.menu, text)
        except Exception as exc:
            _fail(self.menu, exc)


class WireMcm(_Command):
    menu, icon_name = "Wire the MCM", "mcm_wires"
    tip = ("Bond wires for every die on a paddle at once, crossing-free: the leads are shared out among the "
           "dies, then each die is paired round its own centre. Select the housing and the chip pads to bond "
           "(none selected: every pad, if there are leads enough). Die-to-die wires: Bond wires panel, two "
           "chip pads.")

    def Activated(self):
        try:
            sel = FreeCADGui.Selection.getSelection()
            housing = _housing_in(sel)
            if housing is None:
                QtWidgets.QMessageBox.information(_mw(), self.menu, "Select the housing (and the pads to bond).")
                return
            doc = housing.Document
            pads = [o for o in sel if model.role(o) == model.PAD and getattr(o, "PadSide", "") == "chip"]
            if not pads:
                pads = [p for c in housings.chips_on(housing) for p in model.pads_of(c)]
            leads = model.pads_of(housing)
            if len(pads) > len(leads):
                QtWidgets.QMessageBox.warning(_mw(), self.menu, f"{len(pads)} pads, {len(leads)} leads: select the "
                                                                "pads to bond.")
                return
            doc.openTransaction("Wire the MCM")
            made = [wires.create(doc, a, b) for a, b in wires.auto_pair_mcm(housing, pads)]
            doc.recompute()
            doc.commitTransaction()
            found = wires.check(doc, ws=made)
            bad = sum(1 for v in found.values() for f in v if not f.ok and f.severity == "reject")
            QtWidgets.QMessageBox.information(_mw(), self.menu, f"{len(made)} wires; MIL-STD-883 rejects: {bad}.")
        except Exception as exc:
            _fail(self.menu, exc)


class Pc104Board(_Command):
    menu, icon_name = "PC/104 board", "pc104"
    tip = ("A CubeSat board, PC/104 size (90.17 x 95.89 x 1.6 mm): 0.254 mm RO4350B over a ground plane on "
           "FR-4, the four corner holes, the CubeSat Kit bus headers H1/H2. Put packages on it with Place; "
           "lands, lines and parts from a script (core.pcb).")

    def Activated(self):
        try:
            from ..app import boards
            from ..core import pcb
            p = pcb.board()
            p.append(pcb.header("H1 CubeSat Kit bus", 0.0, 37.0))
            p.append(pcb.header("H2 CubeSat Kit bus", 0.0, 31.4))
            boards.make_board(_doc(), p, "PC/104 board", pcb.Stack())
            FreeCADGui.SendMsgToActiveView("ViewFit")
        except Exception as exc:
            _fail(self.menu, exc)


class CubeSat(_Command):
    menu, icon_name = "CubeSat 1U", "cubesat"
    tip = ("A 1U CubeSat to put boards into: rails, frames, M3 rods, spacers, solar panels, an S-band patch — "
           "checked against the CubeSat Design Specification Rev 14.1 by Integration check.")

    def Activated(self):
        try:
            system.make_cubesat(_doc())
            FreeCADGui.SendMsgToActiveView("ViewFit")
        except Exception as exc:
            _fail(self.menu, exc)


class IntoSystem(_Command):
    menu, icon_name = "Into the system", "into_system"
    tip = ("Put the selected board into the CubeSat's stack, 15 mm above the board below (the CubeSat Kit "
           "pitch) — or at the height you type.")

    def Activated(self):
        try:
            sel = _selected()
            doc = _doc()
            board = next((p for o in sel for p in [o] + model.ancestors(o) if model.role(p) == model.BOARD), None)
            sat = _system_in(doc, sel)
            if board is None or sat is None:
                QtWidgets.QMessageBox.information(_mw(), self.menu, "Select a board (and have a CubeSat in the "
                                                                    "document).")
                return
            others = [b for b in system.boards_of(sat) if b.Name != board.Name]
            default = max((system.board_top_z(b) for b in others), default=-55.0) + 15.0
            z, ok = QtWidgets.QInputDialog.getDouble(_mw(), self.menu, "Top of the board in the stack, mm "
                                                     "(0 = the CubeSat's centre):", default, -55.0, 55.0, 2)
            if ok:
                system.add_board(sat, board, z)
        except Exception as exc:
            _fail(self.menu, exc)


class IntegrationPanel(_Panel):
    title = "Integration check"

    def __init__(self, sat):
        super().__init__()
        self.sat = sat
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Check", "Result"])
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.tree.itemClicked.connect(self._select)
        self.layout.addWidget(self.tree)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)
        rerun = QtWidgets.QPushButton("Check again")
        rerun.clicked.connect(self.run)
        self.layout.addWidget(rerun)
        self.finish()
        self.run()

    def run(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            found, s = system.check(self.sat)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.tree.clear()
        colours = {"error": "#c0392b", "warning": "#b9770e", "info": "#1e8449"}
        for f in found:
            item = QtWidgets.QTreeWidgetItem([f.check, f.message])
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(colours[f.level])))
            item.setData(0, QtCore.Qt.UserRole, f.objects)
            self.tree.addTopLevelItem(item)
        rows = ""
        for r in s["stack"]:
            room = "" if r["clearance_up"] is None else f"{r['clearance_up']:.1f}"
            rows += f"<tr><td>{r['board']}</td><td align=right>{r['above']:.1f}</td><td align=right>{room}</td></tr>"
        errors = sum(f.level == "error" for f in found)
        self.summary.setText(
            f"<b>{'✔ passes' if not errors else f'✖ {errors} errors'}</b> — {s['mass_g']:.0f} g, centre of gravity "
            f"({s['cg_mm'][0]:+.1f}, {s['cg_mm'][1]:+.1f}, {s['cg_mm'][2]:+.1f}) mm<br>"
            f"<table><tr><th>Board</th><th>parts above, mm</th><th>room left, mm</th></tr>{rows}</table>"
            f"<small>{s['limits']}</small>")

    def _select(self, item, _col):
        FreeCADGui.Selection.clearSelection()
        for name in item.data(0, QtCore.Qt.UserRole) or []:
            obj = self.sat.Document.getObject(name)
            if obj is not None:
                FreeCADGui.Selection.addSelection(obj)


class IntegrationCheck(_Command):
    menu, icon_name = "Integration check", "integration"
    tip = ("The system against its specification: envelope (and what may stand out of a face), collisions "
           "between modules, each board's parts against the room in the stack, mass and centre of gravity "
           "(CubeSat Design Specification Rev 14.1). Click a finding to select what it is about.")

    def IsActive(self):
        doc = FreeCAD.ActiveDocument
        return doc is not None and bool(model.by_role(doc, system.SYSTEM)) and \
            FreeCADGui.Control.activeDialog() is False

    def Activated(self):
        try:
            sat = _system_in(FreeCAD.ActiveDocument, _selected())
            FreeCADGui.Control.showDialog(IntegrationPanel(sat))
        except Exception as exc:
            _fail(self.menu, exc)


COMMANDS = [("PS_ArrangeDies", ArrangeDies), ("PS_WireMcm", WireMcm), ("PS_Pc104Board", Pc104Board),
            ("PS_CubeSat", CubeSat), ("PS_IntoSystem", IntoSystem), ("PS_IntegrationCheck", IntegrationCheck)]
