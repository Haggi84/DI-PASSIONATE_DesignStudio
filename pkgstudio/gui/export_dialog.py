# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The export dialog: which elements, which formats, where.

Chip layers are always exported from their GDS polygons with every detail,
whatever the view shows. Exact solids cost time — about 0.6 ms a polygon —
so the dialog says up front how many polygons a STEP or MSH export will
build, and the export can be cancelled.

EM simulation has its own command (EM simulation): its model needs ports,
an air box and electrical materials, which a geometry export does not.
"""

import os

import FreeCAD
from PySide import QtCore, QtWidgets

from ..app import exporting

MS_PER_POLYGON = 0.6


class ExportDialog(QtWidgets.QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export elements")
        self.resize(560, 640)
        self.doc = doc
        self.elements = exporting.elements(doc)
        outer = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs)
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        self.tabs.addTab(page, "Files")

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Element", "Polygons"])
        self.tree.setColumnWidth(0, 360)
        groups = {}
        for i, e in enumerate(self.elements):
            head = e.name.split("/", 1)[0]
            if head not in groups:
                g = QtWidgets.QTreeWidgetItem([head])
                g.setFlags(g.flags() | QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsAutoTristate)
                g.setCheckState(0, QtCore.Qt.Checked)
                self.tree.addTopLevelItem(g)
                groups[head] = g
            item = QtWidgets.QTreeWidgetItem([e.name.split("/", 1)[-1]])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked if e.default else QtCore.Qt.Unchecked)
            item.setData(0, QtCore.Qt.UserRole, i)
            if e.kind == "layer":
                item.setText(1, f"{e.obj.Polygons:,}")
            groups[head].addChild(item)
        self.tree.expandAll()
        lay.addWidget(self.tree)

        form = QtWidgets.QFormLayout()
        fmt = QtWidgets.QHBoxLayout()
        self.step = QtWidgets.QCheckBox("STEP")
        self.obj = QtWidgets.QCheckBox("OBJ")
        self.msh = QtWidgets.QCheckBox("MSH (Gmsh)")
        self.step.setChecked(True)
        for w in (self.step, self.obj, self.msh):
            fmt.addWidget(w)
        form.addRow("Formats", fmt)
        self.per_element = QtWidgets.QCheckBox("One file per element")
        form.addRow("", self.per_element)
        self.deflection = QtWidgets.QDoubleSpinBox()
        self.deflection.setRange(0.01, 100)
        self.deflection.setValue(1.0)
        self.deflection.setSuffix(" µm")
        self.deflection.setToolTip("OBJ only, for curved parts (wires, housings). Chip layers are exact.")
        form.addRow("OBJ deviation", self.deflection)
        self.size = QtWidgets.QDoubleSpinBox()
        self.size.setRange(0.001, 100)
        self.size.setDecimals(3)
        self.size.setValue(0.2)
        self.size.setSuffix(" mm")
        self.size.setToolTip("MSH only: the largest element. Wires are meshed finer, by their diameter.")
        form.addRow("MSH max. element", self.size)
        folder_row = QtWidgets.QHBoxLayout()
        start = os.path.dirname(doc.FileName) if doc.FileName else os.path.expanduser("~")
        self.folder = QtWidgets.QLineEdit(start)
        browse = QtWidgets.QPushButton("…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)
        form.addRow("Folder", folder_row)
        self.name = QtWidgets.QLineEdit(doc.Label)
        form.addRow("File name", self.name)
        lay.addLayout(form)

        # A section seen from the top: only what lies inside it is written.
        sec = QtWidgets.QGroupBox("Only a section — a rectangle seen from the top")
        sec.setCheckable(True)
        sec.setChecked(False)
        sv = QtWidgets.QGridLayout(sec)
        self.section_box = sec
        self.section = []
        for i, label in enumerate(("x from", "y from", "x to", "y to")):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setRange(-1e6, 1e6)
            sb.setDecimals(4)
            sb.setSuffix(" mm")
            sb.valueChanged.connect(self._section_changed)
            sv.addWidget(QtWidgets.QLabel(label), i // 2, (i % 2) * 2)
            sv.addWidget(sb, i // 2, (i % 2) * 2 + 1)
            self.section.append(sb)
        pick = QtWidgets.QPushButton("Pick in view…")
        pick.setToolTip("The view turns to the top; click two opposite corners. Esc cancels.")
        pick.clicked.connect(self._pick_section)
        sv.addWidget(pick, 2, 0, 1, 4)
        sec.toggled.connect(self._section_changed)
        lay.addWidget(sec)
        self._section_frame = None
        self._set_section(self._model_extent()[:4])

        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.ok.setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.tabs.currentChanged.connect(self._tab)
        self.tree.itemChanged.connect(self._estimate)
        for w in (self.step, self.obj, self.msh):
            w.toggled.connect(self._estimate)
        self._estimate()

    def _tab(self, index):
        self._show_section(index == 0 and self.section_box.isChecked())

    def done(self, result):
        self._show_section(False)
        super().done(result)

    # ── the section ──
    def _model_extent(self):
        """(x0, y0, x1, y1, ztop) of everything with a shape, global."""
        import FreeCAD
        from ..app import model
        bb = FreeCAD.BoundBox()
        for o in self.doc.Objects:
            shape = getattr(o, "Shape", None)
            if shape is None or shape.isNull() or o.isDerivedFrom("App::Part") or model.role(o) in ("em-region",):
                continue
            g = model.parent_frame(o)
            b = shape.BoundBox
            for x in (b.XMin, b.XMax):
                for y in (b.YMin, b.YMax):
                    for z in (b.ZMin, b.ZMax):
                        bb.add(g.multVec(FreeCAD.Vector(x, y, z)))
        if not bb.isValid():
            return (0.0, 0.0, 1.0, 1.0, 0.0)
        return (bb.XMin, bb.YMin, bb.XMax, bb.YMax, bb.ZMax)

    def region(self):
        """The section (x0, y0, x1, y1) mm, or None for everything."""
        if not self.section_box.isChecked():
            return None
        x0, y0, x1, y1 = (sb.value() for sb in self.section)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _set_section(self, r):
        for sb, v in zip(self.section, r):
            sb.blockSignals(True)
            sb.setValue(v)
            sb.blockSignals(False)
        self._section_changed()

    def _section_changed(self, *_):
        self._show_section(self.section_box.isChecked() and self.tabs.currentIndex() == 0)

    def _show_section(self, on, rect=None):
        """The section as a red frame over the model while it is chosen."""
        import FreeCAD
        import Part
        if self._section_frame is not None:
            try:
                self.doc.removeObject(self._section_frame)
            except Exception:
                pass
            self._section_frame = None
        if not on:
            return
        x0, y0, x1, y1 = rect or self.region() or (0, 0, 0, 0)
        if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
            return
        z = self._model_extent()[4] + 0.01
        V = FreeCAD.Vector
        obj = self.doc.addObject("Part::Feature", "ExportSection")
        obj.Label = "Export section"
        obj.Shape = Part.makePolygon([V(x0, y0, z), V(x1, y0, z), V(x1, y1, z), V(x0, y1, z), V(x0, y0, z)])
        from ..app import model
        model.set_role(obj, "em-region")
        vo = getattr(obj, "ViewObject", None)
        if vo is not None:
            vo.LineColor = (1.0, 0.0, 0.0)
            vo.LineWidth = 4.0
            try:
                vo.Selectable = False
            except Exception:
                pass
        self._section_frame = obj.Name
        self.doc.recompute()

    def _pick_section(self):
        import FreeCAD
        import FreeCADGui
        from . import selection
        z = self._model_extent()[4]
        view = FreeCADGui.ActiveDocument.ActiveView
        view.viewTop()
        self.section_box.setChecked(True)
        self.hide()
        first = []

        def on_point(q):
            first.append(q)
            return True

        def on_move(q):
            if first:
                a = first[0]
                self._show_section(True, (a.x, a.y, q.x, q.y))

        try:
            pts = selection.pick_points(2, FreeCAD.Vector(0, 0, z), FreeCAD.Vector(0, 0, 1),
                                        "Export section: click two opposite corners (Esc cancels)",
                                        on_point, on_move)
        finally:
            self.show()
        if pts and len(pts) == 2:
            a, b = pts
            self._set_section((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
        else:
            self._section_changed()

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Export to folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def chosen(self):
        out = []
        for g in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(g)
            for k in range(top.childCount()):
                item = top.child(k)
                if item.checkState(0) == QtCore.Qt.Checked:
                    out.append(self.elements[item.data(0, QtCore.Qt.UserRole)])
        return out

    def formats(self):
        return tuple(f for f, w in (("step", self.step), ("obj", self.obj), ("msh", self.msh)) if w.isChecked())

    def _estimate(self, *_):
        polys = sum(e.obj.Polygons for e in self.chosen() if e.kind == "layer")
        exact = "step" in self.formats() or "msh" in self.formats()
        if exact and polys > 20_000:
            minutes = polys * MS_PER_POLYGON / 60000.0
            self.note.setText(f"Exact solids for {polys:,} layout polygons: about {minutes:.0f} min. "
                              "Every polygon is written — none is simplified.")
            self.note.setStyleSheet("color: #b9770e;")
        else:
            self.note.setText(f"{len(self.chosen())} elements, {polys:,} layout polygons.")
            self.note.setStyleSheet("")




def _progress(mw, title):
    progress = QtWidgets.QProgressDialog(title, "Cancel", 0, 0, mw)
    progress.setWindowModality(QtCore.Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.show()
    # Whether the user cancelled is recorded here, while the export runs.
    # Asked after the dialog is closed, wasCanceled() is always True (Qt
    # emits canceled() when a progress dialog closes), so every export,
    # finished or not, used to report "Export cancelled".
    state = {"cancelled": False}

    def tick(text):
        progress.setLabelText(text)
        QtWidgets.QApplication.processEvents()
        state["cancelled"] = state["cancelled"] or progress.wasCanceled()
        return not state["cancelled"]
    return progress, state, tick


def run(doc):
    mw = __import__("FreeCADGui").getMainWindow()
    dlg = ExportDialog(doc, mw)
    if not dlg.exec():
        return []
    elems, formats = dlg.chosen(), dlg.formats()
    if not elems or not formats:
        QtWidgets.QMessageBox.information(mw, "Export", "Nothing to export: choose elements and a format.")
        return []
    progress, state, tick = _progress(mw, "Exporting…")
    try:
        files = exporting.export(elems, dlg.folder.text(), dlg.name.text() or "package", formats,
                                 per_element=dlg.per_element.isChecked(),
                                 deflection=dlg.deflection.value() / 1000.0,
                                 msh_size=dlg.size.value(), progress=tick, region=dlg.region())
    except Exception as exc:
        progress.close()
        FreeCAD.Console.PrintError(f"[PackageStudio] Export failed: {exc}\n")
        QtWidgets.QMessageBox.critical(mw, "Export failed", str(exc)[:2000])
        return []
    progress.close()
    if state["cancelled"]:
        QtWidgets.QMessageBox.information(mw, "Export", "Export cancelled." + (
            "\nWritten before that:\n" + "\n".join(files) if files else ""))
    else:
        QtWidgets.QMessageBox.information(mw, "Export", "Written:\n" + "\n".join(files))
    return files
