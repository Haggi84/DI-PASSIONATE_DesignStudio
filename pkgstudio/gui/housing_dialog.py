# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The housing library: choose a package, and it arrives ready to bond.

Three sources in one dialog:

  MirrorSemi   their open-cavity QFN/DFN models, downloaded when chosen
               (nothing is shipped: their files are all rights reserved)
  JEDEC        open-cavity packages generated from JEDEC outlines
  Mine         elements from the Element Forge (the user's library)
  Local folder your own STEP housings

Whichever is chosen is analysed on import: its bond fingers become pads,
numbered as JEDEC numbers pins, its body and leadframe get their materials,
and — if asked — the chip goes onto the die paddle and a lid on top.
"""

import os

import FreeCAD
from PySide import QtCore, QtWidgets

from ..app import housings, model
from ..core import catalogue, elements, jedec
from . import commands


class HousingDialog(QtWidgets.QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Housing library")
        self.resize(640, 620)
        self.doc = doc
        self.ms_entries = []
        lay = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        lay.addWidget(self.tabs)

        # MirrorSemi
        ms = QtWidgets.QWidget()
        mv = QtWidgets.QVBoxLayout(ms)
        note = QtWidgets.QLabel("Open-cavity QFN/DFN packages from MirrorSemi (mirrorsemi.com). The list and "
                                "the chosen model are downloaded from their site on request and cached; "
                                "their files are for your own use.")
        note.setWordWrap(True)
        mv.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        load = QtWidgets.QPushButton("Load catalogue")
        self.ms_filter = QtWidgets.QLineEdit()
        self.ms_filter.setPlaceholderText("Filter, e.g. QFN16 or .5")
        row.addWidget(load)
        row.addWidget(self.ms_filter)
        mv.addLayout(row)
        self.ms_list = self._table(["Package", "Leads", "Pitch (mm)"])
        mv.addWidget(self.ms_list)
        self.tabs.addTab(ms, "MirrorSemi")
        load.clicked.connect(self.load_mirrorsemi)
        self.ms_filter.textChanged.connect(self._fill_ms)

        # JEDEC
        jd = QtWidgets.QWidget()
        jv = QtWidgets.QVBoxLayout(jd)
        jnote = QtWidgets.QLabel("Open-cavity packages generated from JEDEC outlines: leadframe (paddle and "
                                 "leads) and a body with a cavity down to the bond fingers.")
        jnote.setWordWrap(True)
        jv.addWidget(jnote)
        self.jd_list = self._table(["Package", "Leads", "Pitch (mm)", "Outline"])
        for spec in jedec.CATALOGUE:
            self._row(self.jd_list, [spec.name, str(spec.leads), f"{spec.pitch:g}", spec.standard], spec.name)
        jv.addWidget(self.jd_list)
        self.tabs.addTab(jd, "JEDEC")

        # Mine: the Element Forge's library
        mn = QtWidgets.QWidget()
        mnv = QtWidgets.QVBoxLayout(mn)
        mnote = QtWidgets.QLabel("Your own elements, made in the Element Forge — by hand or from a description. "
                                 "Kept as JSON files in " + elements.library_dir())
        mnote.setWordWrap(True)
        mnote.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        mnv.addWidget(mnote)
        self.mine_list = self._table(["Package", "Leads", "Pitch (mm)", "Made by"])
        self.mine_entries = elements.load_all()
        for i, e in enumerate(self.mine_entries):
            by = e.origin.get("model") or e.origin.get("by", "hand")
            flag = f"  ≈{len(e.assumed)}" if e.assumed else ""
            self._row(self.mine_list, [e.name + flag, str(elements.leads(e.spec)), f"{e.spec['pitch']:g}", by], i)
        mnv.addWidget(self.mine_list)
        forge = QtWidgets.QPushButton("Open the Element Forge…")
        forge.clicked.connect(self._forge)
        mnv.addWidget(forge)
        self.tabs.addTab(mn, "Mine")

        # Local
        lc = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(lc)
        frow = QtWidgets.QHBoxLayout()
        self.folder = QtWidgets.QLineEdit(commands.params().GetString("HousingFolder", ""))
        browse = QtWidgets.QPushButton("Folder…")
        frow.addWidget(self.folder)
        frow.addWidget(browse)
        lv.addLayout(frow)
        self.lc_list = self._table(["File", "Leads", "Pitch (mm)"])
        lv.addWidget(self.lc_list)
        self.tabs.addTab(lc, "Local folder")
        browse.clicked.connect(self._browse)
        self.folder.editingFinished.connect(self._fill_local)
        self._fill_local()

        # what happens after
        after = QtWidgets.QGroupBox("Then")
        af = QtWidgets.QFormLayout(after)
        self.place = QtWidgets.QCheckBox("Put the chip on the die paddle")
        chips = model.by_role(doc, model.CHIP)
        self.place.setChecked(bool(chips))
        self.place.setEnabled(bool(chips))
        self.attach = QtWidgets.QDoubleSpinBox()
        self.attach.setRange(0, 200)
        self.attach.setValue(25)
        self.attach.setSuffix(" µm die attach (Ag epoxy)")
        self.lid = QtWidgets.QComboBox()
        self.lid.addItems(["No lid"] + list(housings.LID_MATERIALS))
        self.lid_t = QtWidgets.QDoubleSpinBox()
        self.lid_t.setRange(50, 2000)
        self.lid_t.setValue(250)
        self.lid_t.setSuffix(" µm")
        af.addRow(self.place, self.attach)
        af.addRow("Lid", self.lid)
        af.addRow("Lid thickness", self.lid_t)
        lay.addWidget(after)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.tabs.setCurrentIndex(1)

    @staticmethod
    def _table(headers):
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.setColumnWidth(0, 260)
        return t

    @staticmethod
    def _row(table, cells, key):
        item = QtWidgets.QTreeWidgetItem(cells)
        item.setData(0, QtCore.Qt.UserRole, key)
        table.addTopLevelItem(item)

    def load_mirrorsemi(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.ms_entries = catalogue.fetch_mirrorsemi()
            self.status.setText(f"{len(self.ms_entries)} MirrorSemi models listed.")
        except Exception as exc:
            self.status.setText(f"Could not reach mirrorsemi.com: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._fill_ms()

    def _fill_ms(self, *_):
        self.ms_list.clear()
        text = self.ms_filter.text().strip().lower().replace(" ", "")
        for i, e in enumerate(self.ms_entries):
            if text and text not in e.name.lower():
                continue
            self._row(self.ms_list, [e.name, str(e.leads or ""), f"{e.pitch:g}" if e.pitch else ""], i)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder of housing STEP files", self.folder.text())
        if d:
            self.folder.setText(d)
            self._fill_local()

    def _fill_local(self):
        self.lc_list.clear()
        self.local_entries = catalogue.scan_folder(self.folder.text())
        commands.params().SetString("HousingFolder", self.folder.text())
        for i, e in enumerate(self.local_entries):
            self._row(self.lc_list, [e.name, str(e.leads or ""), f"{e.pitch:g}" if e.pitch else ""], i)

    def chosen(self):
        """("mirrorsemi"|"jedec"|"local", key) or None."""
        tab = self.tabs.currentIndex()
        table = (self.ms_list, self.jd_list, self.mine_list, self.lc_list)[tab]
        item = table.currentItem()
        if item is None:
            return None
        return (("mirrorsemi", "jedec", "mine", "local")[tab], item.data(0, QtCore.Qt.UserRole))

    def _forge(self):
        self.reject()
        import FreeCADGui
        FreeCADGui.runCommand("PS_ElementForge")

    def build(self):
        """Import what is chosen; returns the housing."""
        kind, key = self.chosen()
        if kind == "jedec":
            h = housings.from_jedec(self.doc, jedec.by_name(key))
        elif kind == "mine":
            e = self.mine_entries[key]
            h = housings.from_spec_dict(self.doc, e.spec, e.assumed, e.origin)
        elif kind == "mirrorsemi":
            entry = self.ms_entries[key]
            h = housings.from_step(self.doc, catalogue.download(entry), entry.name, "MirrorSemi")
        else:
            entry = self.local_entries[key]
            h = housings.from_step(self.doc, entry.step, entry.name, f"local: {entry.source}")
        if self.place.isChecked():
            # the first chip not in a housing yet: with several, each new
            # housing takes the next one
            chips = model.loose_chips(self.doc)
            if chips and hasattr(h, "AttachCentre"):
                housings.place_chip(chips[0], h, self.attach.value() / 1000.0)
        if self.lid.currentIndex() > 0:
            housings.make_lid(self.doc, h, self.lid.currentText(), self.lid_t.value() / 1000.0)
        return h


def run(doc):
    import FreeCADGui
    mw = FreeCADGui.getMainWindow()
    dlg = HousingDialog(doc, mw)
    while dlg.exec():
        if dlg.chosen() is None:
            QtWidgets.QMessageBox.information(mw, "Housing", "Choose a package in the list.")
            continue
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            doc.openTransaction("Housing from library")
            h = dlg.build()
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            QtWidgets.QApplication.restoreOverrideCursor()
            FreeCAD.Console.PrintError(f"[PackageStudio] Housing: {exc}\n")
            QtWidgets.QMessageBox.critical(mw, "Housing", str(exc)[:2000])
            continue
        QtWidgets.QApplication.restoreOverrideCursor()
        n = len(model.pads_of(h))
        FreeCAD.Console.PrintMessage(f"[PackageStudio] {h.Label}: {n} bond fingers became package pads.\n")
        FreeCADGui.SendMsgToActiveView("ViewFit")
        from . import browser
        browser.refresh()
        return h
    return None
