# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The design-flow toolbar: from the chip designer's intent to the assembly
house, the board designer, the circuit simulation and the measurement.

    Pin list & LVS · Bonding diagram · KiCad board · KiCad library ·
    Circuit model · Compare with measurement

Thin over app.netlist, app.bonding, app.kicad, core.circuit_model and
core.validation — the same functions the tools/ scripts and the examples use.
"""

import os

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from ..app import bonding, housings, kicad, model, netlist
from .commands import _Command, _doc, _fail, _mw, params
from .panels import _Panel

#: the pin list loaded last in this session (the KiCad library names its pins with it)
state = {"intent": None, "files": []}


def _housing():
    for o in FreeCADGui.Selection.getSelection():
        for p in [o] + model.ancestors(o):
            if model.role(p) == model.HOUSING:
                return p
            if model.role(p) == model.PACKAGE:
                h = next((c for c in p.Group if model.role(c) == model.HOUSING), None)
                if h is not None:
                    return h
    doc = FreeCAD.ActiveDocument
    found = model.by_role(doc, model.HOUSING) if doc else []
    return found[0] if len(found) == 1 else None


def _folder(key, title):
    start = params().GetString(key, os.path.expanduser("~"))
    path = QtWidgets.QFileDialog.getExistingDirectory(_mw(), title, start)
    if path:
        params().SetString(key, path)
    return path


class PinListPanel(_Panel):
    title = "Pin list & package LVS"

    def __init__(self, housing):
        super().__init__()
        self.housing = housing
        self.files = QtWidgets.QLabel()
        self.files.setWordWrap(True)
        self.layout.addWidget(self.files)
        row = QtWidgets.QHBoxLayout()
        load = QtWidgets.QPushButton("Load pin list…")
        load.setToolTip("CSV (die,pad,net,kind,lead), xschem top cell (.sch), LEF (.lef) or Verilog top (.v); "
                        "several merge — a CSV loaded last fixes kinds and leads. The die of an .sch/.lef/.v is "
                        "its file name, or the one chip in the package")
        self.assign = QtWidgets.QPushButton("Lay wires by net")
        self.assign.setToolTip("Fixed leads first, then every other pad crossing-free round them; replaces the "
                               "wires on these dies")
        self.check = QtWidgets.QPushButton("Check (LVS)")
        for b in (load, self.assign, self.check):
            row.addWidget(b)
        self.layout.addLayout(row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Rule", "Finding"])
        self.tree.setRootIsDecorated(False)
        self.tree.setWordWrap(True)
        self.layout.addWidget(self.tree)
        self.finish()
        load.clicked.connect(self.load)
        self.assign.clicked.connect(self.lay)
        self.check.clicked.connect(self.run)
        self._show_files()

    def _show_files(self):
        i = state["intent"]
        if i is None:
            self.files.setText(f"{self.housing.Label}: no pin list loaded.")
        else:
            self.files.setText(f"{self.housing.Label}: {len(i.pins)} pins, {len(i.nets())} nets, dies "
                               f"{', '.join(i.dies())} — from {', '.join(os.path.basename(f) for f in state['files'])}")
        for b in (self.assign, self.check):
            b.setEnabled(i is not None)

    def load(self):
        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            _mw(), "Pin list", params().GetString("PinListFolder", os.path.expanduser("~")),
            "Pin lists (*.csv *.sch *.lef *.v *.sv);;All files (*)")
        if not paths:
            return
        params().SetString("PinListFolder", os.path.dirname(paths[0]))
        try:
            state["intent"] = netlist.load(paths)
            state["files"] = paths
            missing = netlist.apply(self.housing.Document, state["intent"],
                                    housings.chips_on(self.housing) or None)
            self.status.setText(f"{len(missing)} pins of the list are on no chip here."
                                if missing else "Every pin of the list found on the dies.")
        except Exception as exc:
            self.status.setText(f"Could not read: {exc}")
        self._show_files()

    def lay(self):
        doc = self.housing.Document
        doc.openTransaction("Lay wires by net")
        try:
            made = netlist.assign(self.housing, state["intent"])
            doc.commitTransaction()
            self.status.setText(f"{len(made)} wires laid.")
        except Exception as exc:
            doc.abortTransaction()
            self.status.setText(f"Could not lay the wires: {exc}")
            return
        self.run()

    def run(self):
        from ..app import wires
        found, nets = netlist.lvs(self.housing, state["intent"])
        rules = wires.check(self.housing.Document)
        rejects = [(k, f) for k, v in rules.items() for f in v if not f.ok and f.severity == "reject"]
        self.tree.clear()
        colours = {"error": "#c0392b", "warning": "#b9770e", "info": "#2471a3"}
        for f in found:
            item = QtWidgets.QTreeWidgetItem([f.rule, f.message])
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(colours[f.level])))
            self.tree.addTopLevelItem(item)
        for k, f in rejects:
            item = QtWidgets.QTreeWidgetItem([f"MIL-883: {f.rule}", f"{k}: {f.message}"])
            item.setForeground(1, QtGui.QBrush(QtGui.QColor(colours["error"])))
            self.tree.addTopLevelItem(item)
        errors = sum(f.level == "error" for f in found) + len(rejects)
        self.status.setText(("✔ matches the pin list, every wire within MIL-STD-883" if not errors else
                             f"✖ {errors} errors") + f" — {len(nets)} leads with a net.")


class PinList(_Command):
    menu, icon_name = "Pin list & LVS", "pinlist"
    tip = ("What the chip designer intends — a pin list from CSV, the xschem top cell or the digital flow's "
           "LEF: lay the wires by net (fixed leads kept, the rest crossing-free) and check the package against "
           "it: opens, shorts, wrong leads, supplies without a lead, RF pins without ground neighbours.")

    def Activated(self):
        h = _housing()
        if h is None:
            QtWidgets.QMessageBox.information(_mw(), self.menu, "Select the housing (or its package).")
            return
        FreeCADGui.Control.showDialog(PinListPanel(h))


class BondingDiagram(_Command):
    menu, icon_name = "Bonding diagram", "bonding"
    tip = ("The bonding diagram (SVG, PDF) and the wire list (CSV, µm, first bond from the die centre) the "
           "assembly house asks for — for the selected housing and the dies on it.")

    def Activated(self):
        try:
            h = _housing()
            if h is None:
                QtWidgets.QMessageBox.information(_mw(), self.menu, "Select the housing (or its package).")
                return
            folder = _folder("BondingFolder", "Folder for the bonding diagram")
            if not folder:
                return
            stem = getattr(h, "HousingName", h.Label).replace(" ", "_")
            svg, csv = bonding.export(h, folder, stem, state["intent"])
            pdf = _svg_to_pdf(svg)
            QtWidgets.QMessageBox.information(_mw(), self.menu, "\n".join(x for x in (svg, pdf, csv) if x))
        except Exception as exc:
            _fail(self.menu, exc)


def _svg_to_pdf(svg):
    try:
        from PySide import QtSvg
    except ImportError:
        return ""
    renderer = QtSvg.QSvgRenderer(svg)
    pdf = os.path.splitext(svg)[0] + ".pdf"
    writer = QtGui.QPdfWriter(pdf)
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A3))
    writer.setPageOrientation(QtGui.QPageLayout.Landscape)
    painter = QtGui.QPainter(writer)
    size = renderer.defaultSize()
    page = writer.pageLayout().paintRectPixels(writer.resolution())
    scale = min(page.width() / max(size.width(), 1), page.height() / max(size.height(), 1))
    renderer.render(painter, QtCore.QRectF(0, 0, size.width() * scale, size.height() * scale))
    painter.end()
    return pdf


class KicadBoard(_Command):
    menu, icon_name = "KiCad board", "kicad_import"
    tip = ("The designer's board from KiCad (.kicad_pcb): stack-up, lines, vias, planes, footprints — all of "
           "it, or a region for the EM model. Packages go onto its footprints' lands with Place.")

    def Activated(self):
        try:
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                _mw(), self.menu, params().GetString("KicadFolder", os.path.expanduser("~")), "KiCad board (*.kicad_pcb)")
            if not path:
                return
            params().SetString("KicadFolder", os.path.dirname(path))
            text, ok = QtWidgets.QInputDialog.getText(_mw(), self.menu, "Region x0, y0, x1, y1 in mm, KiCad's "
                                                      "coordinates (y down) — empty for the whole board:")
            if not ok:
                return
            region = None
            if text.strip():
                x0, y0, x1, y1 = (float(v) for v in text.replace(";", ",").split(","))
                region = (min(x0, x1), -max(y0, y1), max(x0, x1), -min(y0, y1))
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                board, report = kicad.import_board(_doc(), path, region)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            msg = f"{board.Label}: {report['pieces']} pieces, {report['footprints']} footprints, nets " \
                  f"{len(report['nets'])}."
            if report.get("unknown_materials"):
                msg += "\nDielectrics not in the library (taken as FR-4): " + ", ".join(
                    f"{m['layer']} {m['material']} εr {m['eps_r']}" for m in report["unknown_materials"])
            FreeCADGui.SendMsgToActiveView("ViewFit")
            QtWidgets.QMessageBox.information(_mw(), self.menu, msg)
        except Exception as exc:
            _fail(self.menu, exc)


class KicadLibrary(_Command):
    menu, icon_name = "KiCad library", "kicad_export"
    tip = ("The package for the board designer: a KiCad footprint (lands numbered as the leads), a symbol "
           "whose pins carry the nets the package LVS found, and the 3-D model (STEP, with dies and wires).")

    def Activated(self):
        try:
            h = _housing()
            if h is None:
                QtWidgets.QMessageBox.information(_mw(), self.menu, "Select the housing (or its package).")
                return
            folder = _folder("KicadExportFolder", "Folder for the KiCad library")
            if not folder:
                return
            if state["intent"] is not None:
                netlist.lvs(h, state["intent"])
            kinds = {p.net: p.kind for p in state["intent"].pins} if state["intent"] is not None else {}
            paths = kicad.export_package(h, folder, kinds=kinds)
            QtWidgets.QMessageBox.information(_mw(), self.menu, "\n".join(paths))
        except Exception as exc:
            _fail(self.menu, exc)


class CircuitModel(_Command):
    menu, icon_name = "Circuit model", "circuit"
    tip = ("The EM result for the circuit simulation: a Touchstone file as a passive SPICE subcircuit (vector "
           "fitting) and an xschem symbol — the package dropped into the designer's schematic. With ngspice on "
           "the PATH, the subcircuit is simulated and compared with the Touchstone file.")

    def Activated(self):
        try:
            from ..core import circuit_model as cm
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                _mw(), self.menu, params().GetString("TouchstoneFolder", os.path.expanduser("~")),
                "Touchstone (*.s1p *.s2p *.s3p *.s4p *.s5p *.s6p *.s8p)")
            if not path:
                return
            params().SetString("TouchstoneFolder", os.path.dirname(path))
            text, ok = QtWidgets.QInputDialog.getText(_mw(), self.menu, "Fit up to (GHz) — empty for the whole "
                                                      "file. A long line needs a narrower band:")
            if not ok:
                return
            try:
                fmax = float(text.replace(",", ".")) * 1e9 if text.strip() else None
            except ValueError:
                QtWidgets.QMessageBox.warning(_mw(), self.menu, f"Not a frequency in GHz: {text!r}")
                return
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                cir, rep = cm.write_spice(path, fmax=fmax)
                sym = cm.write_xschem_symbol(os.path.splitext(cir)[0] + ".sym", rep["name"], rep["ports"], cir)
                chk = cm.check_against(cir, path, rep["name"], fmax=fmax)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            text = (f"{cir}\n{sym}\n\n{rep['poles']} poles, RMS error {rep['rms_error']:.4f}, "
                    f"{'passive' if rep['passive'] else 'NOT passive'} ({rep['how']}).")
            text += (f"\nngspice against the Touchstone file: max {chk['max_db_error']:.2f} dB" if chk else
                     "\n(ngspice not on the PATH: not simulated)")
            QtWidgets.QMessageBox.information(_mw(), self.menu, text)
        except Exception as exc:
            _fail(self.menu, exc)


class Validate(_Command):
    menu, icon_name = "Compare with measurement", "validate"
    tip = ("Is the simulation right? A measured Touchstone file against the simulated one: IEEE P370 quality "
           "of the measurement, the fixture removed with a 2x-thru (optional), max/RMS difference per "
           "S-parameter, a plot and a report.")

    def Activated(self):
        try:
            from ..core import validation
            start = params().GetString("TouchstoneFolder", os.path.expanduser("~"))
            filt = "Touchstone (*.s2p *.s4p *.snp *.s*p)"
            meas, _f = QtWidgets.QFileDialog.getOpenFileName(_mw(), "Measured", start, filt)
            if not meas:
                return
            sim, _f = QtWidgets.QFileDialog.getOpenFileName(_mw(), "Simulated", os.path.dirname(meas), filt)
            if not sim:
                return
            thru, _f = QtWidgets.QFileDialog.getOpenFileName(_mw(), "2x-thru (cancel: no de-embedding)",
                                                             os.path.dirname(meas), filt)
            out = os.path.join(os.path.dirname(meas), "validation")
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            try:
                r = validation.report(meas, sim, out, thru=thru or None)
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
            c = r["comparison"]
            lines = [f"{k}: max {v['max_db']:.2f} dB, RMS {v['rms_db']:.2f} dB" for k, v in c.items()
                     if k.startswith("S")]
            q = ", ".join(f"{k} {v['evaluation']}" for k, v in r["quality"].items())
            QtWidgets.QMessageBox.information(_mw(), self.menu, f"{'PASS' if c['pass'] else 'FAIL'} "
                                              f"(±{c['tol_db']:g} dB)\n" + "\n".join(lines)
                                              + f"\n\nMeasurement (IEEE P370): {q}\n\n{r['markdown']}")
        except Exception as exc:
            _fail(self.menu, exc)


COMMANDS = [("PS_PinList", PinList), ("PS_BondingDiagram", BondingDiagram), ("PS_KicadBoard", KicadBoard),
            ("PS_KicadLibrary", KicadLibrary), ("PS_CircuitModel", CircuitModel), ("PS_Validate", Validate)]
