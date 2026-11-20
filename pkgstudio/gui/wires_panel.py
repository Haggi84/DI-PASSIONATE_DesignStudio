# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The bond-wire panel: material and bond type, every bond dimension in um
next to its MIL-STD-883 range, symmetric pairing, and a rule check whose
findings name their clause.

Choosing a material and diameter fills in the usual bond for it (aluminium
wedge-wedge, gold or copper ball-stitch) in proportions inside the MIL
ranges; every number can still be changed. The rule check runs MIL-STD-883
TM 2010 for class B (the default) or S, plus the assembly guidelines, and
marks each wire in the table.
"""

import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from ..app import model, wires
from ..core import bondwire as bw, symmetry as sym, wirerules
from . import selection
from .panels import _Panel, _refresh_browser, _status

DIAMETERS_UM = [15, 18, 20, 23, 25, 30, 33, 38, 50, 75, 100, 125, 200, 300, 500]
# (WireParams field, label, MIL range in d — None where the standard sets none, applies to)
FIELDS = [
    ("loop_h", "Loop height", None, "both"),
    ("wedge_w", "First wedge width", "1.2 – 3.0 d", bw.WEDGE),
    ("wedge_l", "First wedge length", "1.5 – 6.0 d", bw.WEDGE),
    ("ball_d", "Ball diameter", "2.0 – 5.0 d", bw.BALL),
    ("ball_h", "Ball height", None, bw.BALL),
    ("stitch_w", "Second bond width", "1.2 – 3.0 d (wedge), 1.5 – 3.0 d (stitch)", "both"),
    ("stitch_l", "Second bond length", "1.5 – 6.0 d", "both"),
    ("tail", "Tail", "≤ 2 d", "both"),
]


class WiresPanel(_Panel):
    title = "Bond wires"

    def __init__(self):
        super().__init__()
        top = QtWidgets.QGroupBox("Wire")
        tf = QtWidgets.QFormLayout(top)
        self.material = QtWidgets.QComboBox()
        self.material.addItems(["Al", "Au", "Cu", "PdCu", "Ag"])
        self.diameter = QtWidgets.QComboBox()
        for d in DIAMETERS_UM:
            self.diameter.addItem(f"{d} µm", d / 1000.0)
        self.bond = QtWidgets.QComboBox()
        self.bond.addItems([bw.WEDGE, bw.BALL])
        preset = QtWidgets.QPushButton("Usual bond for this wire")
        preset.setToolTip("Aluminium: wedge-wedge. Gold, copper, silver: ball-stitch. Proportions inside "
                          "the MIL-STD-883 ranges.")
        tf.addRow("Material", self.material)
        tf.addRow("Diameter", self.diameter)
        tf.addRow("Bond", self.bond)
        tf.addRow("", preset)
        self.layout.addWidget(top)

        dims = QtWidgets.QGroupBox("Bonds and loop")
        self.df = QtWidgets.QFormLayout(dims)
        self.spins = {}
        for field, label, rng, _kind in FIELDS:
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(0.0, 5000.0)
            s.setDecimals(1)
            s.setSuffix(" µm")
            if rng:
                s.setToolTip(f"MIL-STD-883 TM 2010: {rng}")
            self.spins[field] = s
            self.df.addRow(label + (f"  ({rng})" if rng else ""), s)
        self.exit = QtWidgets.QDoubleSpinBox()
        self.exit.setRange(10, 90)
        self.exit.setSuffix(" °")
        self.exit.setToolTip("Wedge: the tool's wire-feed angle. Ball: 90° (vertical).")
        self.land = QtWidgets.QDoubleSpinBox()
        self.land.setRange(5, 80)
        self.land.setSuffix(" °")
        self.df.addRow("Exit angle", self.exit)
        self.df.addRow("Landing angle", self.land)
        self.layout.addWidget(dims)

        rules_row = QtWidgets.QHBoxLayout()
        self.cls = QtWidgets.QComboBox()
        self.cls.addItem("MIL-STD-883 Class B", "B")
        self.cls.addItem("MIL-STD-883 Class S", "S")
        check = QtWidgets.QPushButton("Check rules")
        rules_row.addWidget(self.cls)
        rules_row.addWidget(check)
        self.layout.addLayout(rules_row)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Chip pad", "Lead", "Length (mm)", "Mirror X", "Mirror Y", "Rules"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.layout.addWidget(self.table)

        grid = QtWidgets.QGridLayout()
        auto = QtWidgets.QPushButton("Pair all, symmetric")
        auto.setToolTip("Every chip pad to a lead, in order round the chip — no crossings.")
        pair = QtWidgets.QPushButton("Pair selected pads")
        pair.setToolTip("Select one chip pad and one lead pad in the 3-D view.")
        self.with_mirrors = QtWidgets.QCheckBox("with their mirror images")
        self.with_mirrors.setChecked(True)
        drop = QtWidgets.QPushButton("Remove selected rows")
        build = QtWidgets.QPushButton("Build wires")
        grid.addWidget(auto, 0, 0)
        grid.addWidget(pair, 0, 1)
        grid.addWidget(drop, 1, 0)
        grid.addWidget(self.with_mirrors, 1, 1)
        grid.addWidget(build, 2, 0, 1, 2)
        self.layout.addLayout(grid)
        self.finish()

        existing = wires.wires(self.doc())
        self.show_params(wires.params_of(existing[0]) if existing else bw.preset("Al", 0.025))
        self.pairs = [(w.ChipPad, w.PackagePad) for w in existing if w.ChipPad and w.PackagePad]
        self.findings = {}
        preset.clicked.connect(self.apply_preset)
        self.bond.currentTextChanged.connect(self._bond_changed)
        auto.clicked.connect(self.auto)
        pair.clicked.connect(self.pair_selected)
        drop.clicked.connect(self.drop)
        build.clicked.connect(self.build)
        check.clicked.connect(self.check)
        self._fill()

    # ── parameters ──────────────────────────────────────────────────────────

    def show_params(self, p):
        self.material.setCurrentText(p.material)
        i = self.diameter.findData(round(p.wire_d, 6))
        if i < 0:
            self.diameter.addItem(f"{p.wire_d * 1000:g} µm", round(p.wire_d, 6))
            i = self.diameter.count() - 1
        self.diameter.setCurrentIndex(i)
        self.bond.setCurrentText(p.bond)
        for field, *_ in FIELDS:
            self.spins[field].setValue(getattr(p, field) * 1000.0)
        self.exit.setValue(p.exit_deg)
        self.land.setValue(p.land_deg)
        self._bond_changed(p.bond)

    def params(self):
        p = bw.WireParams(material=self.material.currentText(), bond=self.bond.currentText(),
                          wire_d=self.diameter.currentData(), exit_deg=self.exit.value(),
                          land_deg=self.land.value())
        for field, *_ in FIELDS:
            setattr(p, field, self.spins[field].value() / 1000.0)
        return p

    def apply_preset(self):
        p = bw.preset(self.material.currentText(), self.diameter.currentData())
        p.loop_h = self.spins["loop_h"].value() / 1000.0 or p.loop_h
        self.show_params(p)
        _status(self.status, f"{p.bond} bond for {p.material} {p.wire_d * 1000:g} µm, inside the "
                             f"MIL-STD-883 ranges.")

    def _bond_changed(self, bond):
        for field, _l, _r, kind in FIELDS:
            self.spins[field].setEnabled(kind in ("both", bond))
        self.exit.setEnabled(bond == bw.WEDGE)

    # ── pairs ───────────────────────────────────────────────────────────────

    def _fill(self):
        doc = self.doc()
        rep = wires.symmetry_of(doc, self.pairs) if self.pairs else {"x": [], "y": []}
        existing = {(w.ChipPad.Name, w.PackagePad.Name): w for w in wires.wires(doc) if w.ChipPad and w.PackagePad}
        self.table.setRowCount(len(self.pairs))
        for r, (c, q) in enumerate(self.pairs):
            w = existing.get((c.Name, q.Name))
            fs = self.findings.get(w.Label, []) if w else []
            bad = [f for f in fs if not f.ok and f.severity == "reject"]
            warn = [f for f in fs if not f.ok and f.severity == "guideline"]
            rule_text = "—" if not fs else ("✓" if not bad else f"✗ {len(bad)}") + (f"  ⚠ {len(warn)}" if warn else "")
            cells = [c.PadName, q.PadName, f"{w.WireLength.Value:.4f}" if w else "—",
                     "✓" if rep["x"][r] else "✗", "✓" if rep["y"][r] else "✗", rule_text]
            for k, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if k >= 3:
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                if k == 5 and fs:
                    item.setToolTip("\n".join(f"{'OK  ' if f.ok else ('REJECT  ' if f.severity == 'reject' else 'NOTE  ')}"
                                              f"{f.rule}: {f.message}" + (f"  [{f.ref}]" if f.ref else "")
                                              for f in fs))
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#c0392b" if bad else
                                                                 ("#b9770e" if warn else "#1e8449"))))
                self.table.setItem(r, k, item)
        both = sum(1 for a, b in zip(rep["x"], rep["y"]) if a and b)
        text = f"{len(self.pairs)} wires planned; {both} have mirror partners about both chip axes." \
            if self.pairs else "No wires planned yet."
        if self.findings:
            rej, gl = wirerules.summary(self.findings)
            text += (f" {self.cls.currentText()}: " + ("all wires pass." if not rej else f"{rej} rejects.")
                     + (f" {gl} guideline notes." if gl else ""))
        _status(self.status, text, ok=not (self.findings and wirerules.summary(self.findings)[0]))

    def auto(self):
        self.pairs = wires.auto_pair(self.doc())
        self.findings = {}
        if not self.pairs:
            return _status(self.status, "Need chip pads and lead pads first (Pads panel or Housing library).", False)
        self._fill()

    def pair_selected(self):
        chip_p, pkg_p = selection.pads("chip"), selection.pads("package")
        if len(chip_p) != 1 or len(pkg_p) != 1:
            return _status(self.status, "Select exactly one chip pad and one lead pad.", False)
        new = [(chip_p[0], pkg_p[0])]
        if self.with_mirrors.isChecked():
            new += self._mirrors(chip_p[0], pkg_p[0])
        used_c, used_q = {c.Name for c, _ in new}, {q.Name for _, q in new}
        self.pairs = [(c, q) for c, q in self.pairs if c.Name not in used_c and q.Name not in used_q] + new
        FreeCADGui.Selection.clearSelection()
        self._fill()

    def _mirrors(self, c, q, tol=1e-4):
        doc = self.doc()
        centre = wires.centre_xy(doc)
        chips, pkgs = model.all_pads(doc, "chip"), model.all_pads(doc, "package")

        def find(pads, pt):
            for p in pads:
                v = model.pad_centre(p)
                if abs(v.x - pt[0]) <= tol and abs(v.y - pt[1]) <= tol:
                    return p
            return None

        out = []
        cc, qq = tuple(model.pad_centre(c))[:2], tuple(model.pad_centre(q))[:2]
        for axes in (("x",), ("y",), ("x", "y")):
            a, b = cc, qq
            for axis in axes:
                a, b = sym.mirror(a, centre, axis), sym.mirror(b, centre, axis)
            mc, mq = find(chips, a), find(pkgs, b)
            if mc is not None and mq is not None and (mc, mq) not in out and mc is not c:
                out.append((mc, mq))
        return out

    def drop(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            del self.pairs[r]
        self._fill()

    def build(self):
        if not self.pairs:
            return _status(self.status, "No wires planned.", False)
        doc = self.doc()
        doc.openTransaction("Build bond wires")
        wires.create_all(doc, self.pairs, self.params())
        doc.commitTransaction()
        _refresh_browser()
        self.check()

    def check(self):
        if not wires.wires(self.doc()):
            return _status(self.status, "Build the wires first.", False)
        self.findings = wires.check(self.doc(), self.cls.currentData())
        self._fill()
