# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM Simulation panel: from the open assembly to a ready-to-run openEMS,
EMerge or Palace package.

"Make simulation package" does it all in one go — check, build, write —
and stops at the first thing that would make the result wrong, saying
which command fixes it. Below it, the same steps one by one, for when a
setting is to be changed on the way:

  0. Ready?          what the assembly still lacks (app.readiness)
  1. Chip metal      per chip, the region and the levels built exactly
  2. Model           the flat simulation copy (app.em_model)
  3. Solver          solver, frequency range, mesh density, smallest cell
  4. Layers          the layer stack with each layer's material and where
                     it came from; a material changed here goes back to the
                     Material property of its parts
  5. Ports           the document's ports as lumped ports, which is excited
  6. Checks          OTTER's checks, and the openEMS mesh estimate
  7. Package         written by the OTTER add-on itself

Every change goes through the EmProject — the same calls the assistant's
tools make — so the two can be used side by side (gui.em_state).
"""

import os

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from . import em_preferences as preferences, em_state as state

_LEVEL_COLOURS = {"error": "#c0392b", "warning": "#b7791f", "info": "#5a6b7d"}
_GUESSED_BACKGROUND = "#fff4d6"
_LEVEL_PRESETS = [("Top two metals and the vias between", "top"), ("Top metal only", "top1"),
                  ("Every drawn level", "all"), ("No metal (silicon and oxide only)", "none")]
_REGION_PRESETS = [("Round the wired pads", "pads"), ("Whole chip", "whole")]


def _section(title):
    box = QtWidgets.QGroupBox(title)
    layout = QtWidgets.QVBoxLayout(box)
    layout.setContentsMargins(6, 6, 6, 6)
    return box, layout


def _html(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))


class _FixBox(QtWidgets.QFrame):
    """What to do about the finding clicked above: the explanation, the
    preview in the 3-D view, and the fix as a button where there is one."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet("_FixBox { background: #f3f8f3; }")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QtWidgets.QLabel()
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.text = QtWidgets.QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.legend = QtWidgets.QLabel('<span style="color:#d01a1a">■</span> the problem &nbsp; '
                                       '<span style="color:#e0b000">■</span> conductors it should join &nbsp; '
                                       '<span style="color:#1aa040">■</span> the fix')
        self.actions = QtWidgets.QVBoxLayout()
        row = QtWidgets.QHBoxLayout()
        self.zoom = QtWidgets.QPushButton("Show in 3-D")
        self.command = QtWidgets.QPushButton()
        self.close_button = QtWidgets.QPushButton("Clear")
        row.addWidget(self.zoom)
        row.addWidget(self.command)
        row.addWidget(self.close_button)
        for w in (self.title, self.text, self.legend):
            lay.addWidget(w)
        lay.addLayout(self.actions)
        lay.addLayout(row)
        self.zoom.clicked.connect(self._show)
        self.command.clicked.connect(self._run_command)
        self.close_button.clicked.connect(self.clear)
        self.suggestion = None
        self.hide()

    def present(self, suggestion):
        self.suggestion = suggestion
        self.title.setText(suggestion.title)
        self.text.setText(suggestion.text)
        self.legend.setVisible(bool(suggestion.shapes))
        while self.actions.count():
            w = self.actions.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for action in suggestion.actions:
            b = QtWidgets.QPushButton(action.text)
            b.setStyleSheet("font-weight: bold;")
            b.clicked.connect(lambda _=False, a=action: self.panel.apply_fix(a))
            self.actions.addWidget(b)
        names = {"PS_Ports": "Ports", "PS_Materials": "Materials", "PS_Move": "Move", "PS_Place": "Place",
                 "PS_BondWires": "Bond wires", "PS_ImportGds": "Import GDS", "PS_ImportStep": "Import STEP"}
        self.command.setVisible(bool(suggestion.command))
        self.command.setText(f"Open {names.get(suggestion.command, suggestion.command)}")
        self.show()
        self._show()

    def _show(self):
        if self.suggestion is not None:
            self.panel.preview.show(self.panel.source_doc(), self.suggestion)

    def _run_command(self):
        if self.suggestion is not None and self.suggestion.command:
            doc = self.panel.source_doc()
            if doc is not None:
                FreeCADGui.setActiveDocument(doc.Name)
            if FreeCADGui.Control.activeDialog():
                FreeCADGui.Control.closeDialog()
            FreeCADGui.runCommand(self.suggestion.command)

    def clear(self):
        self.panel.preview.clear()
        self.suggestion = None
        self.hide()


class EMSimulationPanel(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__("EM Simulation (OTTER)", parent)
        self.setObjectName("PS_EMSimulationPanel")
        self._updating = False
        self._last_zip = None
        self._findings, self._issues = [], []
        from .fix_preview import FixPreview
        self.preview = FixPreview()
        self._build_ui()
        state.add_listener(self.refresh)
        self.refresh()
        self.check_ready()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body)
        self.setWidget(scroll)

        self.make_button = QtWidgets.QPushButton("Make simulation package")
        self.make_button.setToolTip("Check the assembly, build the model, run OTTER's checks and write the "
                                    "package — stopping at the first error, with what fixes it.")
        f = self.make_button.font()
        f.setBold(True)
        self.make_button.setFont(f)
        self.make_button.clicked.connect(self.make_all)
        root.addWidget(self.make_button)

        box, layout = _section("0. Ready to simulate?")
        self.ready = QtWidgets.QListWidget()
        self.ready.setWordWrap(True)
        self.ready.setMaximumHeight(150)
        self.ready.setToolTip("Click a line: what to do about it, shown in the 3-D view.")
        self.ready.itemClicked.connect(self._ready_clicked)
        layout.addWidget(self.ready)
        self.ready_fix = _FixBox(self)
        layout.addWidget(self.ready_fix)
        check = QtWidgets.QPushButton("Check the assembly")
        check.clicked.connect(self.check_ready)
        layout.addWidget(check)
        root.addWidget(box)

        box, layout = _section("1. Chip metal — built exactly from the layout")
        self.chips = QtWidgets.QTableWidget(0, 4)
        self.chips.setHorizontalHeaderLabels(["Chip", "Region", "Levels", "Polygons"])
        self.chips.horizontalHeader().setStretchLastSection(True)
        self.chips.verticalHeader().setVisible(False)
        layout.addWidget(self.chips)
        note = QtWidgets.QLabel("A whole chip is too much to mesh; what the package adds is pad, wire and lead. "
                                "The rest of the die is its silicon and oxide.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #5a6b7d;")
        layout.addWidget(note)
        root.addWidget(box)

        box, layout = _section("2. Simulation model")
        row = QtWidgets.QHBoxLayout()
        self.build_button = QtWidgets.QPushButton("Build model")
        self.build_button.setToolTip("Copy every physical part, the chip metal, every port and an air box into "
                                     "a separate document OTTER can read. The assembly is not changed.")
        self.build_button.clicked.connect(self.build_model)
        self.show_button = QtWidgets.QPushButton("Show")
        self.show_button.clicked.connect(self.show_model)
        row.addWidget(self.build_button, 1)
        row.addWidget(self.show_button)
        layout.addLayout(row)
        self.summary = QtWidgets.QLabel("No model yet.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        root.addWidget(box)

        box, layout = _section("3. Solver")
        form = QtWidgets.QFormLayout()
        self.solver = QtWidgets.QComboBox()
        self.solver.addItems(["openEMS", "EMerge", "Palace"])
        self.solver.setToolTip("openEMS: FDTD, broadband in one run. EMerge: FEM sweep in Python, curved parts "
                               "keep their shape. Palace: FEM with MPI, for large models.")
        self.solver.currentTextChanged.connect(self._solver_changed)
        form.addRow("Solver:", self.solver)
        freq = QtWidgets.QHBoxLayout()
        self.fmin = self._spin(0.001, 1000, 3, 1.0, " GHz")
        self.fmax = self._spin(0.001, 1000, 3, 30.0, " GHz")
        apply_freq = QtWidgets.QPushButton("Apply")
        apply_freq.clicked.connect(self._frequency_changed)
        freq.addWidget(self.fmin)
        freq.addWidget(QtWidgets.QLabel("to"))
        freq.addWidget(self.fmax)
        freq.addWidget(apply_freq)
        form.addRow("Frequency:", freq)
        mesh = QtWidgets.QHBoxLayout()
        self.density = QtWidgets.QComboBox()
        self.density.addItems(["coarse", "normal", "fine"])
        self.density.setToolTip("coarse: a quick look; normal: OTTER's tested rules; fine: a convergence check")
        self.min_cell = self._spin(0.01, 1000, 2, 1.0, " µm")
        self.min_cell.setToolTip("No rule makes a smaller cell. In openEMS the smallest cell sets the timestep.")
        apply_mesh = QtWidgets.QPushButton("Apply")
        apply_mesh.clicked.connect(self._mesh_changed)
        mesh.addWidget(self.density)
        mesh.addWidget(QtWidgets.QLabel("smallest"))
        mesh.addWidget(self.min_cell)
        mesh.addWidget(apply_mesh)
        form.addRow("Mesh:", mesh)
        layout.addLayout(form)
        root.addWidget(box)

        box, layout = _section("4. Layers and materials")
        self.layers = QtWidgets.QTableWidget(0, 4)
        self.layers.setHorizontalHeaderLabels(["Layer", "z (µm)", "Material", "From"])
        self.layers.horizontalHeader().setStretchLastSection(True)
        self.layers.verticalHeader().setVisible(False)
        self.layers.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.layers.itemSelectionChanged.connect(self._select_layer_parts)
        layout.addWidget(self.layers)
        self.remember_button = QtWidgets.QPushButton("Save chosen materials to the assembly")
        self.remember_button.setToolTip("Write the materials of guessed or changed layers onto their parts' "
                                        "Material property — the Materials panel shows them from then on.")
        self.remember_button.clicked.connect(self.remember_materials)
        layout.addWidget(self.remember_button)
        root.addWidget(box)

        box, layout = _section("5. Ports")
        self.ports = QtWidgets.QTableWidget(0, 4)
        self.ports.setHorizontalHeaderLabels(["Port", "Face", "R", "Excited"])
        self.ports.horizontalHeader().setStretchLastSection(True)
        self.ports.verticalHeader().setVisible(False)
        layout.addWidget(self.ports)
        row = QtWidgets.QHBoxLayout()
        self.excite_button = QtWidgets.QPushButton("Excite selected")
        self.excite_button.clicked.connect(self._excite_selected)
        self.remove_port_button = QtWidgets.QPushButton("Remove selected")
        self.remove_port_button.clicked.connect(self._remove_selected_port)
        row.addWidget(self.excite_button)
        row.addWidget(self.remove_port_button)
        layout.addLayout(row)
        note = QtWidgets.QLabel("Ports are drawn with the Ports command and carry their impedance.")
        note.setWordWrap(True)
        layout.addWidget(note)
        root.addWidget(box)

        box, layout = _section("6. Checks")
        self.checks = QtWidgets.QListWidget()
        self.checks.setWordWrap(True)
        self.checks.setToolTip("Click a finding: what to do about it, shown in the 3-D view, with the fix where "
                               "one can be worked out.")
        self.checks.itemClicked.connect(self._check_clicked)
        layout.addWidget(self.checks)
        self.check_fix = _FixBox(self)
        layout.addWidget(self.check_fix)
        self.mesh_label = QtWidgets.QLabel("")
        self.mesh_label.setWordWrap(True)
        layout.addWidget(self.mesh_label)
        root.addWidget(box)

        box, layout = _section("7. Package")
        form = QtWidgets.QFormLayout()
        out = QtWidgets.QHBoxLayout()
        self.out_dir = QtWidgets.QLineEdit(preferences.get("OutputDir"))
        self.out_dir.setPlaceholderText("default: FreeCAD user folder / PackageStudio / em")
        browse = QtWidgets.QPushButton("…")
        browse.clicked.connect(self._browse_out_dir)
        out.addWidget(self.out_dir, 1)
        out.addWidget(browse)
        form.addRow("Folder:", out)
        self.solver_python = QtWidgets.QLineEdit(preferences.get("SolverPython"))
        self.solver_python.setPlaceholderText("python3")
        self.solver_python.setToolTip("The Python with openEMS / EMerge / gmsh, for the run command in README.txt")
        form.addRow("Solver Python:", self.solver_python)
        self.palace_bin = QtWidgets.QLineEdit(preferences.get("PalaceBin"))
        self.palace_bin.setPlaceholderText("palace-*.bin (Palace only)")
        form.addRow("Palace binary:", self.palace_bin)
        layout.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton("Generate package")
        self.generate_button.clicked.connect(self.generate)
        self.open_button = QtWidgets.QPushButton("Open folder")
        self.open_button.clicked.connect(self._open_package_folder)
        self.open_button.setEnabled(False)
        row.addWidget(self.generate_button, 1)
        row.addWidget(self.open_button)
        layout.addLayout(row)
        self.result = QtWidgets.QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.result)
        root.addWidget(box)
        root.addStretch(1)

    @staticmethod
    def _spin(low, high, decimals, value, suffix):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    # ── state ────────────────────────────────────────────────────────────────

    def _doc(self):
        """The assembly: the active document, or the one whose model is active."""
        doc = FreeCAD.ActiveDocument
        for name in FreeCAD.listDocuments():
            project = state.project_for(FreeCAD.getDocument(name))
            if project is not None and doc is not None and project.sim.doc.Name == doc.Name:
                return FreeCAD.getDocument(name)
        return doc

    def _project(self):
        return state.project_for(self._doc())

    def _run(self, what, fn):
        try:
            fn()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "EM Simulation", f"{what}: {exc}")
        state.changed()

    # ── 0. readiness ─────────────────────────────────────────────────────────

    def _show_findings(self, findings, extra=()):
        self.ready.clear()
        self._findings = list(findings) + list(extra)
        for f in list(findings) + list(extra):
            item = QtWidgets.QListWidgetItem(("✖ " if f.level == "error" else "⚠ " if f.level == "warning"
                                              else "• ") + f.message + (f"\n   → {f.fix}" if f.fix else ""))
            item.setForeground(QtGui.QColor(_LEVEL_COLOURS[f.level]))
            self.ready.addItem(item)
        if self.ready.count() == 0 or all(f.level == "info" for f in list(findings) + list(extra)):
            ok = QtWidgets.QListWidgetItem("✔ Ready to simulate.")
            ok.setForeground(QtGui.QColor("#1e7d32"))
            self.ready.insertItem(0, ok)
            self._findings.insert(0, None)

    def _load_settings(self, doc):
        """The solver, frequency and mesh stored in *doc* (app.em_settings),
        once per document, while it has no model yet."""
        if getattr(self, "_settings_doc", None) == doc.Name or self._project() is not None:
            return
        self._settings_doc = doc.Name
        from ..app import em_settings
        stored = em_settings.load(doc)
        if not stored:
            return
        if "solver" in stored:
            self.solver.setCurrentText(stored["solver"])
        if "fmin_ghz" in stored:
            self.fmin.setValue(stored["fmin_ghz"])
        if "fmax_ghz" in stored:
            self.fmax.setValue(stored["fmax_ghz"])
        if "mesh" in stored:
            self.density.setCurrentText(stored["mesh"])
        if "min_cell_um" in stored:
            self.min_cell.setValue(stored["min_cell_um"])

    def _save_settings(self, doc):
        from ..app import em_settings
        try:
            em_settings.save(doc, solver=self.solver.currentText(), fmin_ghz=self.fmin.value(),
                             fmax_ghz=self.fmax.value(), mesh=self.density.currentText(),
                             min_cell_um=self.min_cell.value())
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] EM settings not stored: {exc}\n")

    def check_ready(self):
        doc = self._doc()
        if doc is None:
            self.ready.clear()
            self.ready.addItem("Open an assembly document first.")
            return []
        from ..app import readiness
        self._fill_chips(doc)
        self._load_settings(doc)
        try:
            found = readiness.check(doc, self.regions(doc))
        except Exception as exc:
            found = [readiness.Finding("error", f"The check failed: {exc}")]
        project = self._project()
        extra = readiness.check_model(project.sim) if project is not None else []
        self._show_findings(found, extra)
        return found + extra

    # ── fixes ────────────────────────────────────────────────────────────────

    def source_doc(self):
        project = self._project()
        return project.source() if project is not None else self._doc()

    def _ready_clicked(self, item):
        row = self.ready.row(item)
        finding = self._findings[row] if row < len(self._findings) else None
        if finding is None:
            return self.ready_fix.clear()
        from ..app import fixes
        self.check_fix.hide()
        self.ready_fix.present(fixes.for_finding(self._doc(), finding, self._project()))

    def _check_clicked(self, item):
        row = self.checks.row(item)
        project = self._project()
        if project is None or row >= len(self._issues):
            return self.check_fix.clear()
        from ..app import fixes
        self.ready_fix.hide()
        self.check_fix.present(fixes.for_issue(project, self._issues[row]))

    def apply_fix(self, action):
        """Apply a suggested fix; build the model again if the assembly changed."""
        doc = self.source_doc()
        self.preview.clear()
        if doc is not None:
            doc.openTransaction("Fix")
        try:
            done = action.apply()
            if doc is not None:
                doc.commitTransaction()
        except Exception as exc:
            if doc is not None:
                doc.abortTransaction()
            QtWidgets.QMessageBox.warning(self, "EM Simulation", f"The fix was not applied:\n{exc}")
            return
        self.ready_fix.clear()
        self.check_fix.clear()
        if action.rebuild:
            self.build_model()
        else:
            state.changed()
            self.check_ready()
        self.result.setText(_html(done or "Fixed.") + "<br>The checks above are up to date.")

    def closeEvent(self, event):
        self.preview.clear()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.preview.clear()
        super().hideEvent(event)

    # ── 1. chip metal ────────────────────────────────────────────────────────

    def _fill_chips(self, doc):
        from ..app import model
        previous = {}
        for r in range(self.chips.rowCount()):
            name = self.chips.item(r, 0).data(QtCore.Qt.UserRole)
            previous[name] = (self.chips.cellWidget(r, 1).currentData(), self.chips.cellWidget(r, 2).currentData())
        chips = model.by_role(doc, model.CHIP)
        self.chips.setRowCount(len(chips))
        for r, chip in enumerate(chips):
            item = QtWidgets.QTableWidgetItem(chip.Label)
            item.setData(QtCore.Qt.UserRole, chip.Name)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.chips.setItem(r, 0, item)
            region, levels = previous.get(chip.Name, ("pads", getattr(chip, "EmChipMetal", "") or "top"))
            for col, presets, value in ((1, _REGION_PRESETS, region), (2, _LEVEL_PRESETS, levels)):
                combo = QtWidgets.QComboBox()
                for text, key in presets:
                    combo.addItem(text, key)
                combo.setCurrentIndex(max(0, combo.findData(value)))
                combo.currentIndexChanged.connect(self._chips_changed)
                self.chips.setCellWidget(r, col, combo)
        self._count_polygons(doc)
        self.chips.resizeColumnsToContents()

    def _chips_changed(self, *_):
        doc = self._doc()
        if doc is not None:
            self._count_polygons(doc)

    def _count_polygons(self, doc):
        from ..app import em_model
        regions = self.regions(doc)
        for r in range(self.chips.rowCount()):
            name = self.chips.item(r, 0).data(QtCore.Qt.UserRole)
            chip = doc.getObject(name)
            try:
                n = em_model.region_polygons(chip, regions[name])
                text = f"{n:,}" + (" — too many" if n > em_model.MAX_POLYGONS else "")
            except Exception as exc:
                text = f"? ({exc})"
            cell = QtWidgets.QTableWidgetItem(text)
            cell.setFlags(cell.flags() & ~QtCore.Qt.ItemIsEditable)
            if "too many" in text:
                cell.setForeground(QtGui.QColor(_LEVEL_COLOURS["error"]))
            self.chips.setItem(r, 3, cell)

    def regions(self, doc):
        """{chip name: ChipRegion} as the chip table says."""
        from ..app import em_model
        out = {}
        for r in range(self.chips.rowCount()):
            name = self.chips.item(r, 0).data(QtCore.Qt.UserRole)
            chip = doc.getObject(name)
            if chip is None:
                continue
            region = em_model.whole_region(chip) if self.chips.cellWidget(r, 1).currentData() == "whole" \
                else em_model.default_region(chip)
            preset = self.chips.cellWidget(r, 2).currentData()
            drawn = [d[0] for d in em_model.drawn_levels(chip)]
            metals = [d[0] for d in em_model.drawn_levels(chip) if d[1] == "conductor"]
            if preset == "all":
                region.levels = drawn
            elif preset == "top1":
                region.levels = metals[-1:]
            elif preset == "none":
                region.levels = []
            else:
                region.levels = em_model.default_levels(chip)
            out[name] = region
        return out

    # ── 2. model ─────────────────────────────────────────────────────────────

    def build_model(self):
        doc = self._doc()
        if doc is None:
            QtWidgets.QMessageBox.information(self, "EM Simulation", "Open an assembly document first.")
            return False
        from ..app.em_project import EmProject
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            project = EmProject.from_document(doc, self.solver.currentText(), fmin_ghz=self.fmin.value(),
                                              fmax_ghz=self.fmax.value(), work_dir=self._work_dir(doc),
                                              regions=self.regions(doc),
                                              progress=lambda t: (self.summary.setText(t),
                                                                  QtWidgets.QApplication.processEvents()))
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "EM Simulation", f"The model could not be built:\n{exc}")
            return False
        # the mesh as set in the panel before the build: the project starts from
        # OTTER's defaults (1 µm cells), which on a board-sized model make a grid
        # that does not fit in memory — and the checks below compute it
        try:
            project.set_mesh(self.density.currentText(), self.min_cell.value())
        except ValueError as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] Mesh setting not taken: {exc}\n")
        QtWidgets.QApplication.restoreOverrideCursor()
        self._save_settings(doc)
        state.set_project(doc, project)
        self.check_ready()
        return True

    def show_model(self):
        project = self._project()
        if project is not None:
            FreeCADGui.setActiveDocument(project.sim.doc.Name)
            FreeCADGui.SendMsgToActiveView("ViewFit")

    def remember_materials(self):
        project = self._project()
        if project is None:
            return
        count = project.remember_materials()
        project.source().recompute()
        self.result.setText(f"Material set on {count} part(s) of {project.source().Label}.")
        state.changed()

    def _solver_changed(self, solver):
        project = self._project()
        if project is not None and not self._updating and project.settings.solver != solver:
            self._run("Solver", lambda: project.set_solver(solver))

    def _frequency_changed(self):
        project = self._project()
        if project is not None:
            self._run("Frequency", lambda: project.set_frequency(self.fmin.value(), self.fmax.value()))

    def _mesh_changed(self):
        project = self._project()
        if project is not None:
            self._run("Mesh", lambda: project.set_mesh(self.density.currentText(), self.min_cell.value()))

    def _material_changed(self, group, value):
        project = self._project()
        if project is None or self._updating:
            return
        from ..app.em_materials import NOT_SIMULATED, NOT_SIMULATED_NAME
        material = NOT_SIMULATED if value == NOT_SIMULATED_NAME else value
        self._run(f"Material of {group}", lambda: project.set_material(group, material))

    def _selected_port(self):
        rows = {i.row() for i in self.ports.selectedItems()}
        if len(rows) != 1:
            return None
        return self.ports.item(rows.pop(), 0).text()

    def _excite_selected(self):
        project, name = self._project(), self._selected_port()
        if project is not None and name:
            self._run("Port", lambda: project.settings.set_active_port(name))

    def _remove_selected_port(self):
        project, name = self._project(), self._selected_port()
        if project is not None and name:
            self._run("Port", lambda: project.remove_port(name))

    def _select_layer_parts(self):
        project = self._project()
        rows = {i.row() for i in self.layers.selectedItems()}
        if project is None or len(rows) != 1 or self._updating:
            return
        group = self.layers.item(rows.pop(), 0).text()
        target = next((g for g in project.model.layers + project.model.spanning if g.name == group), None)
        if target is None:
            return
        source = project.source()
        FreeCADGui.Selection.clearSelection()
        for label in target.bodies:
            part = project.sim.part(label)
            if part is not None and part.source and source.getObject(part.source) is not None:
                FreeCADGui.Selection.addSelection(source.Name, part.source)

    # ── 7. package ───────────────────────────────────────────────────────────

    def _browse_out_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Package folder", self.out_dir.text())
        if path:
            self.out_dir.setText(path)

    def _work_dir(self, doc):
        base = self.out_dir.text().strip()
        return os.path.join(base, doc.Name) if base else None

    def _save_paths(self):
        preferences.put("OutputDir", self.out_dir.text().strip())
        preferences.put("SolverPython", self.solver_python.text().strip())
        preferences.put("PalaceBin", self.palace_bin.text().strip())
        preferences.apply_run_environment()

    def generate(self, ask=True):
        project = self._project()
        if project is None:
            QtWidgets.QMessageBox.information(self, "EM Simulation", "Build the model first.")
            return False
        errors = [i for i in project.checks() if i.level == "error"]
        if errors and ask:
            answer = QtWidgets.QMessageBox.question(
                self, "EM Simulation",
                "OTTER's checks found errors, the result would be wrong:\n\n"
                + "\n".join(f"• {i.message}" for i in errors[:6]) + "\n\nGenerate anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return False
        self._save_paths()
        work = self._work_dir(project.source())
        if work:
            from pathlib import Path
            project.work_dir = Path(work)
        from . import otter_runner
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result, zip_path = project.build_package(otter_runner.generate)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not result.ok:
            self.result.setText(f"<b>Generation failed.</b><br>{_html(result.error or '')}")
            self.open_button.setEnabled(False)
            FreeCAD.Console.PrintError(f"[PackageStudio] EM generation failed:\n{result.error}\n")
            return False
        self._last_zip = str(zip_path)
        warnings = "".join(f"<br>• {_html(w)}" for w in result.warnings)
        self.result.setText(f"<b>{result.solver}</b>: {len(result.files)} file(s) in<br>{_html(str(zip_path))}"
                            + (f"<br><br>Export warnings:{warnings}" if warnings else ""))
        self.open_button.setEnabled(True)
        FreeCAD.Console.PrintMessage(f"[PackageStudio] EM package written: {zip_path}\n")
        state.changed()
        return True

    def make_all(self):
        """Check, build, check again, write — stopping at the first error."""
        from ..app import readiness
        found = self.check_ready()
        if not readiness.ready(found):
            self.result.setText("<b>Not ready</b>: fix the errors under 0. first.")
            return False
        if not self.build_model():
            return False
        project = self._project()
        found = [f for f in readiness.check_model(project.sim) if f.level == "error"]
        errors = [i for i in project.checks() if i.level == "error"]
        if found or errors:
            self.result.setText("<b>Not written</b>: " + _html("; ".join(
                [f.message for f in found] + [i.message for i in errors][:6])))
            return False
        return self.generate(ask=False)

    def _open_package_folder(self):
        if self._last_zip:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(self._last_zip)))

    # ── redraw ───────────────────────────────────────────────────────────────

    def refresh(self):
        project = self._project()
        self._updating = True
        try:
            enabled = project is not None
            for widget in (self.show_button, self.layers, self.ports, self.excite_button,
                           self.remove_port_button, self.generate_button, self.remember_button):
                widget.setEnabled(enabled)
            if not enabled:
                self.summary.setText("No model yet. Build it from the open assembly.")
                self.layers.setRowCount(0)
                self.ports.setRowCount(0)
                self.checks.clear()
                self.mesh_label.setText("")
                return
            from ..app import em_model
            self.summary.setText(_html(em_model.summary(project.sim)))
            self.solver.setCurrentText(project.settings.solver)
            self.fmin.setValue(project.design.fmin_ghz)
            self.fmax.setValue(project.design.fmax_ghz)
            self.density.setCurrentText(project.design.mesh)
            self.min_cell.setValue(project.design.min_cell_um)
            self._fill_layers(project)
            self._fill_ports(project)
            self._fill_checks(project)
        finally:
            self._updating = False

    def _fill_layers(self, project):
        from ..app import em_materials
        rows = list(reversed(project.material_table()))     # top first, as a stack-up drawing
        self.layers.setRowCount(len(rows))
        choices = em_materials.choices(project.source())
        for r, (group, z0, z1, material, origin, labels) in enumerate(rows):
            name = QtWidgets.QTableWidgetItem(group)
            name.setToolTip("Parts: " + ", ".join(labels[:20]) + (" …" if len(labels) > 20 else ""))
            self.layers.setItem(r, 0, name)
            self.layers.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{z0 * 1000:.4g} … {z1 * 1000:.4g}"))
            combo = QtWidgets.QComboBox()
            current = material or em_materials.NOT_SIMULATED_NAME
            combo.addItems(choices if current in choices else [current] + choices)
            combo.setCurrentText(current)
            combo.setToolTip(em_materials.described(material))
            combo.currentTextChanged.connect(lambda value, g=group: self._material_changed(g, value))
            self.layers.setCellWidget(r, 2, combo)
            source = QtWidgets.QTableWidgetItem(origin)
            source.setToolTip(origin)
            self.layers.setItem(r, 3, source)
            if group in project.sim.guessed:
                for c in (0, 1, 3):
                    self.layers.item(r, c).setBackground(QtGui.QColor(_GUESSED_BACKGROUND))
        self.layers.resizeColumnsToContents()

    def _fill_ports(self, project):
        port_list = project.settings.ports()
        self.ports.setRowCount(len(port_list))
        for r, port in enumerate(port_list):
            self.ports.setItem(r, 0, QtWidgets.QTableWidgetItem(port.name))
            self.ports.setItem(r, 1, QtWidgets.QTableWidgetItem(", ".join(port.objects)))
            self.ports.setItem(r, 2, QtWidgets.QTableWidgetItem(port.resistance))
            self.ports.setItem(r, 3, QtWidgets.QTableWidgetItem("yes" if port.active else ""))
        self.ports.resizeColumnsToContents()

    def _fill_checks(self, project):
        self.checks.clear()
        self._issues = []
        mesh_text = ""
        for issue in project.checks():
            if issue.code == "mesh":
                mesh_text = issue.message
                continue
            self._issues.append(issue)
            item = QtWidgets.QListWidgetItem(f"{issue.level.upper()}: {issue.message}"
                                             + (f"\n  ({issue.finding})" if issue.finding else ""))
            item.setForeground(QtGui.QColor(_LEVEL_COLOURS.get(issue.level, "#000000")))
            self.checks.addItem(item)
        if self.checks.count() == 0:
            self.checks.addItem("No problems found.")
        self.mesh_label.setText(mesh_text)
