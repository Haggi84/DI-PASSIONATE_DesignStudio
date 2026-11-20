# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Task panels: placing, pads, ports, bond wires.

Each panel says in one line what to select in the 3-D view, does one thing
per button, and reports what it did in its status line. Nothing happens on
selection alone, so a stray click never moves anything.

Pads and Ports share how a face is chosen (_SurfacePanel): select it, "Use
selected face", pick where (0, 0) is, and a grid with rulers is drawn on it.
They are two panels, not two tabs of one: pads are what wires land on,
ports are where the simulation feeds the model, and they are done at
different times.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from ..app import model, placing, ports, wires
from ..core import bondwire as bw, pads as pd
from . import selection


def _status(label, text, ok=True):
    label.setText(text)
    label.setStyleSheet("" if ok else "color: #c0392b;")


def _refresh_browser():
    from . import browser
    browser.refresh()


class _Panel:
    title = ""

    def __init__(self):
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(self.title)
        self.layout = QtWidgets.QVBoxLayout(self.form)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)

    def finish(self):
        self.layout.addWidget(self.status)
        self.layout.addStretch(1)

    def getStandardButtons(self):
        return QtWidgets.QDialogButtonBox.Close

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True

    def accept(self):
        return self.reject()

    @staticmethod
    def doc():
        return FreeCAD.ActiveDocument


def _nearest_chip(doc, point):
    """The chip whose centre is nearest *point* (global), or None."""
    chips = model.by_role(doc, model.CHIP)
    if not chips:
        return None
    return min(chips, key=lambda c: (model.global_placement(c).Base - point).Length)


# ── placing ─────────────────────────────────────────────────────────────────

class PlacePanel(_Panel):
    title = "Place"
    MODES = [
        ("Chip onto a die paddle (automatic)", "chip-paddle",
         "Nothing to select: the chip is centred on the housing's open die paddle, on a die attach layer, "
         "and belongs to that housing's package from then on."),
        ("Chip onto a selected face", model.CHIP,
         "Select the face the chip sits on — in a housing's cavity, or a board's copper (chip-on-board). "
         "It then moves with what it sits on."),
        ("Lid onto the housing", model.LID, "Select the housing face the lid sits on."),
        ("Package onto a board", model.PACKAGE,
         "Select the footprint's pads on the board (all of them) — the package is centred on them and "
         "becomes part of the board."),
    ]

    def __init__(self):
        super().__init__()
        form = QtWidgets.QFormLayout()
        self.mode = QtWidgets.QComboBox()
        for text, _role, _hint in self.MODES:
            self.mode.addItem(text)
        self.which = QtWidgets.QComboBox()
        self.onto = QtWidgets.QComboBox()
        self.onto.setToolTip("Which housing's paddle the chip goes on.")
        self.turn = QtWidgets.QComboBox()
        for deg in (0, 90, 180, 270):
            self.turn.addItem(f"{deg}°", deg)
        self.attach = QtWidgets.QDoubleSpinBox()
        self.attach.setRange(0, 200)
        self.attach.setValue(25)
        self.attach.setSuffix(" µm")
        self.attach.setToolTip("Die attach (Ag epoxy) under the chip; 0 for none.")
        form.addRow("Place", self.mode)
        form.addRow("Object", self.which)
        form.addRow("Housing", self.onto)
        form.addRow("Turned", self.turn)
        form.addRow("Die attach", self.attach)
        self._onto_label = form.labelForField(self.onto)
        self._attach_label = form.labelForField(self.attach)
        self.layout.addLayout(form)
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        self.layout.addWidget(self.hint)
        row = QtWidgets.QHBoxLayout()
        place = QtWidgets.QPushButton("Place")
        spin = QtWidgets.QPushButton("Turn 90° in place")
        row.addWidget(place)
        row.addWidget(spin)
        self.layout.addLayout(row)
        more = QtWidgets.QLabel("To move it from there — by typed offsets or by dragging, one chip or several — "
                                "use Move.")
        more.setWordWrap(True)
        more.setStyleSheet("color: #5a6b7d;")
        self.layout.addWidget(more)
        self.finish()
        self.mode.currentIndexChanged.connect(self._mode_changed)
        place.clicked.connect(self.place)
        spin.clicked.connect(self.spin)
        self._mode_changed()

    def _role(self):
        return self.MODES[self.mode.currentIndex()][1]

    def _targets(self):
        role = self._role()
        doc = self.doc()
        if role == "chip-paddle":
            return model.by_role(doc, model.CHIP)
        return model.by_role(doc, role)

    def _mode_changed(self, *_):
        self.hint.setText(self.MODES[self.mode.currentIndex()][2])
        self.which.clear()
        for obj in self._targets():
            self.which.addItem(obj.Label, obj.Name)
        self.onto.clear()
        paddle = self._role() == "chip-paddle"
        for h in model.by_role(self.doc(), model.HOUSING):
            if hasattr(h, "AttachCentre"):
                self.onto.addItem(h.Label, h.Name)
        for w in (self.onto, self._onto_label, self.attach, self._attach_label):
            if w is not None:
                w.setVisible(paddle)

    def _object(self):
        name = self.which.currentData()
        return self.doc().getObject(name) if name else None

    def place(self):
        obj = self._object()
        if obj is None:
            return _status(self.status, "Nothing of that kind in the document yet.", False)
        if self._role() == "chip-paddle":
            from ..app import housings
            target = self.doc().getObject(self.onto.currentData()) if self.onto.currentData() else None
            if target is None:
                return _status(self.status, "No housing with an open die paddle — take one from the "
                                            "Housing library, or place on a selected face.", False)
            self.doc().openTransaction("Chip onto the die paddle")
            housings.place_chip(obj, target, self.attach.value() / 1000.0, self.turn.currentData())
            self.doc().commitTransaction()
            _refresh_browser()
            return _status(self.status, f"{obj.Label} on {target.Label}'s die paddle, centred, on "
                                        f"{self.attach.value():.0f} µm of die attach. Bond wires updated.")
        chosen = selection.faces()
        if not chosen:
            return _status(self.status, "Select the target face(s) in the 3-D view first.", False)
        try:
            self.doc().openTransaction("Place")
            placing.place(obj, [f for f, _o in chosen], self.turn.currentData(), onto=chosen[0][1])
            self.doc().commitTransaction()
        except ValueError as exc:
            self.doc().abortTransaction()
            return _status(self.status, str(exc), False)
        b = model.global_placement(obj).Base
        owner = obj.getParentGeoFeatureGroup()
        _refresh_browser()
        _status(self.status, f"{obj.Label} placed, centred on the selection at "
                             f"({b.x:.4f}, {b.y:.4f}, {b.z:.4f}) mm"
                             + (f", part of {owner.Label}" if owner is not None else "") + ". Bond wires updated.")
        FreeCADGui.Selection.clearSelection()

    def spin(self):
        obj = self._object()
        if obj is None:
            return
        self.doc().openTransaction("Turn")
        placing.turn(obj, 90)
        self.doc().commitTransaction()
        _status(self.status, f"{obj.Label} turned 90° about its centre. Bond wires updated.")


# ── a face to work on ───────────────────────────────────────────────────────

class _SurfacePanel(_Panel):
    """A panel that works on one chosen flat face, in that face's own
    coordinates, with a grid on it."""

    face_hint = ""

    def _build_surface(self, layout):
        box = QtWidgets.QGroupBox("Surface")
        cv = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel(self.face_hint)
        hint.setWordWrap(True)
        cv.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        use = QtWidgets.QPushButton("Use selected face")
        self.face_label = QtWidgets.QLabel("No face chosen.")
        self.face_label.setWordWrap(True)
        row.addWidget(use)
        row.addWidget(self.face_label, 1)
        cv.addLayout(row)
        form = QtWidgets.QFormLayout()
        self.ct_ref = QtWidgets.QComboBox()
        self.ct_ref.addItem("Face corner — X, Y along the face's edges", "corner")
        self.ct_ref.addItem("Chip centre — X, Y along the chip's axes", "chip")
        self.ct_ref.setToolTip("Where (0, 0) is, and which way X and Y run, on the chosen face.")
        form.addRow("Origin", self.ct_ref)
        grid = QtWidgets.QHBoxLayout()
        self.ct_grid = QtWidgets.QDoubleSpinBox()
        self.ct_grid.setRange(0, 10000)
        self.ct_grid.setDecimals(3)
        self.ct_grid.setSpecialValueText("auto")
        self.ct_grid.setSuffix(" mm")
        self.ct_grid.setToolTip("Spacing of the grid's labelled lines; auto gives about ten across the face. "
                                "The fine lines are a fifth of it.")
        self.ct_show = QtWidgets.QCheckBox("show")
        self.ct_show.setChecked(True)
        self.ct_snapto = QtWidgets.QCheckBox("clicks snap to the fine lines")
        self.ct_snapto.setChecked(True)
        grid.addWidget(self.ct_grid)
        grid.addWidget(self.ct_show)
        grid.addWidget(self.ct_snapto, 1)
        form.addRow("Grid", grid)
        cv.addLayout(form)
        self.ct_cursor = QtWidgets.QLabel("")
        cv.addWidget(self.ct_cursor)
        layout.addWidget(box)

        self._face = self._face_container = None
        self._frame, self._size, self._extent = None, (0.0, 0.0), (0.0, 0.0)
        self._grid = None
        self.ct_grid.valueChanged.connect(self._draw_grid)
        self.ct_show.toggled.connect(self._draw_grid)
        use.clicked.connect(self.use_face)
        self.ct_ref.currentIndexChanged.connect(self._reference_changed)

    def _frame_changed(self):
        """What depends on the frame, redrawn — each panel its own."""

    def _contact_chip(self):
        """The chip the face's 'chip centre' origin refers to: the one
        nearest the face."""
        if self._face is None:
            chips = model.by_role(self.doc(), model.CHIP)
            return chips[0] if chips else None
        return _nearest_chip(self.doc(), self._face.CenterOfMass)

    def _bond_size(self):
        """(length, width) in um of the second bond as the wires are set:
        stitch and tail by stitch width — a point no bigger than the bond
        that lands on it (25 um Al: 62.5 x 37.5 um)."""
        ws = wires.wires(self.doc()) if self.doc() is not None else []
        p = wires.params_of(ws[0]) if ws else bw.preset("Al", 0.025)
        return (p.stitch_l + p.tail) * 1000.0, p.stitch_w * 1000.0

    def _make_frame(self):
        if self._face is None:
            self._frame, self._size, self._extent = None, (0.0, 0.0), (0.0, 0.0)
            return
        f, w, h = model.face_corner_frame(self._face, self._contact_chip())
        self._extent = (w, h)
        if self.ct_ref.currentData() == "corner":
            self._frame, self._size = f, (w, h)
        else:
            self._frame, self._size = model.contact_frame(self._face, self._contact_chip()), (0.0, 0.0)

    def _draw_grid(self, *_):
        """The grid and rulers on the chosen face, in the panel's frame."""
        from . import face_grid
        if self._grid is not None:
            self._grid.remove()
            self._grid = None
        if self._face is None or not self.ct_show.isChecked():
            return
        self._grid = face_grid.FaceGrid(self.doc(), self._frame, *self._extent,
                                        origin_is_corner=self.ct_ref.currentData() == "corner")
        major, minor = self._grid.show(self.ct_grid.value() or None)
        self.ct_grid.setToolTip(f"Labelled lines every {major:g} mm, fine lines every {minor:g} mm. "
                                "0 = auto.")

    def _snap_pitch(self):
        if not self.ct_snapto.isChecked():
            return 0.0
        from ..core import contacts as ct
        return ct.grid_steps(*self._extent, self.ct_grid.value() or None)[1]

    def reject(self):
        if self._grid is not None:
            self._grid.remove()
            self._grid = None
        return super().reject()

    def _centre(self):
        """Where the mirror lines cross: the face's middle in the corner
        frame, the chip's centre (0, 0) in the chip frame."""
        if self.ct_ref.currentData() == "corner":
            return (self._size[0] / 2.0, self._size[1] / 2.0)
        return (0.0, 0.0)

    def _reference_changed(self, *_):
        self._make_frame()
        self._describe_face()
        self._frame_changed()
        self._draw_grid()

    def _describe_face(self):
        if self._face is None:
            self.face_label.setText("No face chosen.")
            return
        text = f"{self._face_container.Label}: {self._face.Area:.3f} mm²"
        if self.ct_ref.currentData() == "corner":
            text += f", {self._size[0]:.4f} × {self._size[1]:.4f} mm (X × Y)"
        self.face_label.setText(text)

    def use_face(self):
        chosen = selection.surface()
        if chosen is None:
            return _status(self.status, "Select a flat face — or an edge, for the upright face beside it "
                                        "(a board's edge face) — first.", False)
        face, leaf, how = chosen
        if face.Surface.__class__.__name__ != "Plane":
            return _status(self.status, "That face is not flat: points and ports go on a flat face.", False)
        container = selection.container_for(leaf)
        self._face, self._face_container = face, container
        self._reference_changed()
        FreeCADGui.Selection.clearSelection()
        _status(self.status, ("The edge's upright face is chosen" if how == "edge" else "Face chosen")
                + f" ({container.Label}).")

    def _need_face(self):
        if self._face is None:
            _status(self.status, "Choose the face first: select it, then “Use selected face”.", False)
            return False
        return True


# ── pads ────────────────────────────────────────────────────────────────────

class PadsPanel(_SurfacePanel):
    title = "Pads"
    face_hint = ("Select the face the wires are to land on (a board's copper, say) and press “Use selected "
                 "face”. Then type a point's X and Y and “Add point”, or “Click points…” on the face (Esc "
                 "ends). Coordinates are in mm on the face; edit them in the table to move a point.")

    def __init__(self):
        super().__init__()
        # Two pages: the pads that come from parts, and contact points placed
        # by hand on a surface — one long column of both did not fit beside
        # the 3-D view.
        self.tabs = QtWidgets.QTabWidget()
        self.layout.addWidget(self.tabs)
        pads_page, surface_page = QtWidgets.QWidget(), QtWidgets.QWidget()
        self._pads_layout = QtWidgets.QVBoxLayout(pads_page)
        self._surface_layout = QtWidgets.QVBoxLayout(surface_page)
        self.tabs.addTab(pads_page, "Pads")
        self.tabs.addTab(surface_page, "Contact points")
        chip_box = QtWidgets.QGroupBox("Chip pads — from the layout")
        cf = QtWidgets.QFormLayout(chip_box)
        self.chip = QtWidgets.QComboBox()
        self.layer = QtWidgets.QComboBox()
        self.min_size = QtWidgets.QDoubleSpinBox()
        self.min_size.setRange(0, 1000)
        self.min_size.setSuffix(" µm")
        self.min_size.setToolTip("Openings smaller than this are not pads (test structures, say).")
        detect = QtWidgets.QPushButton("Find chip pads")
        self.chip_count = QtWidgets.QLabel()
        cf.addRow("Chip", self.chip)
        cf.addRow("Pad layer", self.layer)
        cf.addRow("At least", self.min_size)
        cf.addRow(detect, self.chip_count)
        self._pads_layout.addWidget(chip_box)

        pkg_box = QtWidgets.QGroupBox("Package pads — on the housing or board")
        pv = QtWidgets.QVBoxLayout(pkg_box)
        hint = QtWidgets.QLabel("Select one bond finger's top face, then “Add all alike” "
                                "to get every finger of that kind at once.")
        hint.setWordWrap(True)
        pv.addWidget(hint)
        grid = QtWidgets.QGridLayout()
        add_alike = QtWidgets.QPushButton("Add all alike")
        add_sel = QtWidgets.QPushButton("Add selected faces")
        mirror = QtWidgets.QPushButton("Mirror across the chip axes")
        remove = QtWidgets.QPushButton("Remove selected pads")
        grid.addWidget(add_alike, 0, 0)
        grid.addWidget(add_sel, 0, 1)
        grid.addWidget(mirror, 1, 0)
        grid.addWidget(remove, 1, 1)
        pv.addLayout(grid)
        self.pkg_count = QtWidgets.QLabel()
        pv.addWidget(self.pkg_count)
        self._pads_layout.addWidget(pkg_box)
        self._pads_layout.addStretch(1)
        self._build_surface(self._surface_layout)
        self._build_contacts()
        self._surface_layout.addStretch(1)
        more = QtWidgets.QLabel("Simulation ports have their own command: Ports.")
        more.setStyleSheet("color: #5a6b7d;")
        self.layout.addWidget(more)
        self.finish()

        self.chip.currentIndexChanged.connect(self._fill_layers)
        detect.clicked.connect(self.detect)
        add_alike.clicked.connect(lambda: self.add(alike=True))
        add_sel.clicked.connect(lambda: self.add(alike=False))
        mirror.clicked.connect(self.mirror)
        remove.clicked.connect(self.remove)
        for c in model.by_role(self.doc(), model.CHIP):
            self.chip.addItem(c.Label, c.Name)
        self._counts()

    # ── contact points on a surface ──
    def _build_contacts(self):
        box = QtWidgets.QGroupBox("Contact points")
        cv = QtWidgets.QVBoxLayout(box)
        form = QtWidgets.QFormLayout()
        at = QtWidgets.QHBoxLayout()
        self.ct_x, self.ct_y = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for label, sb in (("X", self.ct_x), ("Y", self.ct_y)):
            sb.setRange(-100000, 100000)
            sb.setDecimals(4)
            sb.setSuffix(" mm")
            at.addWidget(QtWidgets.QLabel(label))
            at.addWidget(sb, 1)
        add = QtWidgets.QPushButton("Add point")
        at.addWidget(add)
        form.addRow("Point at", at)
        size = QtWidgets.QHBoxLayout()
        self.ct_w, self.ct_h = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        bond_l, bond_w = self._bond_size()
        for sb, value in ((self.ct_w, bond_l), (self.ct_h, bond_w)):
            sb.setRange(5, 5000)
            sb.setDecimals(1)
            sb.setValue(value)
            sb.setSuffix(" µm")
            size.addWidget(sb)
        resize = QtWidgets.QPushButton("Apply")
        resize.setToolTip("Give the selected points (all, if none is selected) this size.")
        size.addWidget(resize)
        form.addRow("Point size", size)
        cv.addLayout(form)
        click = QtWidgets.QPushButton("Click points…")
        cv.addWidget(click)

        self.ct_table = QtWidgets.QTableWidget(0, 3)
        self.ct_table.setHorizontalHeaderLabels(["Point", "X (mm)", "Y (mm)"])
        self.ct_table.horizontalHeader().setStretchLastSection(True)
        self.ct_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ct_table.setMinimumHeight(140)
        cv.addWidget(self.ct_table)
        row = QtWidgets.QHBoxLayout()
        self.ct_axis = QtWidgets.QComboBox()
        row.addWidget(QtWidgets.QLabel("Mirror selected"))
        row.addWidget(self.ct_axis, 1)
        mirror = QtWidgets.QPushButton("Mirror")
        row.addWidget(mirror)
        cv.addLayout(row)
        drop = QtWidgets.QPushButton("Remove selected points")
        cv.addWidget(drop)
        self._surface_layout.addWidget(box)

        self._mirror_axes()
        resize.clicked.connect(self.resize_points)
        add.clicked.connect(self.add_typed_point)
        click.clicked.connect(self.click_points)
        mirror.clicked.connect(self.mirror_points)
        drop.clicked.connect(self.remove_points)
        self.ct_table.cellChanged.connect(self._point_edited)

    def _frame_changed(self):
        self._mirror_axes()
        self._fill_points()

    def resize_points(self):
        if self._face_container is None:
            return _status(self.status, "Choose the face first.", False)
        picked = self._selected_points() or model.contact_points(self._face_container)
        w, h = self.ct_w.value() / 1000, self.ct_h.value() / 1000
        self.doc().openTransaction("Resize contact points")
        for o in picked:
            model.resize_contact_point(o, w, h)
        self.doc().commitTransaction()
        _status(self.status, f"{len(picked)} point(s) now {w * 1000:g} x {h * 1000:g} µm.")

    def _mirror_axes(self):
        cx, cy = self._centre()
        self.ct_axis.clear()
        if self.ct_ref.currentData() == "corner":
            self.ct_axis.addItem(f"over X — the face's middle, Y → {2 * cy:g} − Y", "x")
            self.ct_axis.addItem(f"over Y — the face's middle, X → {2 * cx:g} − X", "y")
        else:
            self.ct_axis.addItem("over X — the chip's x axis, Y → −Y", "x")
            self.ct_axis.addItem("over Y — the chip's y axis, X → −X", "y")
        self.ct_axis.addItem("over both (three images)", "both")

    def _fill_points(self):
        self.ct_table.blockSignals(True)
        pts = model.contact_points(self._face_container) if self._face_container is not None else []
        self.ct_table.setRowCount(len(pts))
        for r, obj in enumerate(pts):
            x, y = model.uv_in(obj, self._frame) if self._frame is not None else model.contact_uv(obj)
            name = QtWidgets.QTableWidgetItem(obj.Label)
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            name.setData(QtCore.Qt.UserRole, obj.Name)
            self.ct_table.setItem(r, 0, name)
            self.ct_table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{x:.4f}"))
            self.ct_table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{y:.4f}"))
        self.ct_table.blockSignals(False)
        self._counts()

    def add_typed_point(self):
        if not self._need_face():
            return
        xy = (self.ct_x.value(), self.ct_y.value())
        if not self._face.isInside(FreeCAD.Vector(*self._frame.to_xyz(xy)), 1e-6, True):
            return _status(self.status, f"({xy[0]:g}, {xy[1]:g}) mm is not on the face.", False)
        doc = self.doc()
        doc.openTransaction("Add contact point")
        obj = model.add_contact_point(self._face_container, self._frame, xy,
                                      self.ct_w.value() / 1000, self.ct_h.value() / 1000)
        doc.recompute()
        doc.commitTransaction()
        self._fill_points()
        _refresh_browser()
        _status(self.status, f"{obj.Label} at ({xy[0]:g}, {xy[1]:g}) mm.")

    def click_points(self):
        if not self._need_face():
            return
        from ..core import contacts as ct
        doc, face, container, frame = self.doc(), self._face, self._face_container, self._frame
        w, h, pitch = self.ct_w.value() / 1000, self.ct_h.value() / 1000, self._snap_pitch()
        grid = self._grid
        made = []

        def on_move(q):
            xy = ct.snap(frame.to_uv(tuple(q)), pitch)
            on = face.isInside(FreeCAD.Vector(*frame.to_xyz(xy)), 1e-6, True)
            self.ct_cursor.setText(f"X {xy[0]:.4f}  Y {xy[1]:.4f} mm" + ("" if on else "  — off the face"))
            if grid is not None:
                grid.move_cross(*xy)

        def on_point(q):
            xy = ct.snap(frame.to_uv(tuple(q)), pitch)
            if not face.isInside(FreeCAD.Vector(*frame.to_xyz(xy)), 1e-6, True):
                _status(self.status, "That click is not on the chosen face.", False)
                return False
            made.append(model.add_contact_point(container, frame, xy, w, h))
            doc.recompute()
            self._fill_points()
            _status(self.status, f"{len(made)} point(s) placed — click the next, Esc ends.")
            QtWidgets.QApplication.processEvents()
            return True

        doc.openTransaction("Contact points")
        _status(self.status, "Click the points on the face; Esc ends.")
        try:
            selection.pick_points(None, FreeCAD.Vector(*frame.origin), FreeCAD.Vector(*frame.n),
                                  "Contact points: click them on the face, Esc ends.", on_point, on_move,
                                  tol=1e-3 + (3 * grid.lift if grid is not None else 0.0))
        finally:
            doc.commitTransaction()
            self.ct_cursor.setText("")
            if grid is not None:
                self._draw_grid()          # without the crosshair
        _refresh_browser()
        _status(self.status, f"{len(made)} contact points placed on {container.Label}.")

    def _point_edited(self, row, col):
        if col not in (1, 2):
            return
        obj = self.doc().getObject(self.ct_table.item(row, 0).data(QtCore.Qt.UserRole))
        try:
            xy = (float(self.ct_table.item(row, 1).text().replace(",", ".")),
                  float(self.ct_table.item(row, 2).text().replace(",", ".")))
            self.doc().openTransaction("Move contact point")
            model.move_contact_point(obj, xy, self._face, self._frame)
            self.doc().commitTransaction()
            wires.update_all(self.doc())
            _status(self.status, f"{obj.Label} moved to ({xy[0]:g}, {xy[1]:g}) mm.")
        except ValueError as exc:
            self.doc().abortTransaction()
            _status(self.status, f"{obj.Label} not moved: {exc}", False)
        self._fill_points()

    def _selected_points(self):
        rows = sorted({i.row() for i in self.ct_table.selectedItems()})
        doc = self.doc()
        objs = [doc.getObject(self.ct_table.item(r, 0).data(QtCore.Qt.UserRole)) for r in rows]
        return [o for o in objs if o is not None]

    def mirror_points(self):
        if not self._need_face():
            return
        picked = self._selected_points()
        if not picked:
            return _status(self.status, "Select the points to mirror in the table first.", False)
        doc = self.doc()
        doc.openTransaction("Mirror contact points")
        made = model.mirror_points(picked, self.ct_axis.currentData(), self._frame, self._centre(), self._face)
        doc.recompute()
        doc.commitTransaction()
        self._fill_points()
        _refresh_browser()
        skipped = len(picked) * (3 if self.ct_axis.currentData() == "both" else 1) - len(made)
        _status(self.status, f"{len(made)} mirrored point(s) added"
                             + (f"; {skipped} not made — already there or off the face." if skipped else "."))

    def remove_points(self):
        picked = self._selected_points()
        doc = self.doc()
        doc.openTransaction("Remove contact points")
        for w in wires.wires(doc):
            if w.PackagePad in picked:
                doc.removeObject(w.Name)
        for p in picked:
            doc.removeObject(p.Name)
        doc.commitTransaction()
        self._fill_points()
        _refresh_browser()
        _status(self.status, f"{len(picked)} contact points removed, with their wires.")

    # ── pads from parts ──
    def _chip(self):
        name = self.chip.currentData()
        return self.doc().getObject(name) if name else None

    def _fill_layers(self, *_):
        self.layer.clear()
        chip = self._chip()
        if chip is None:
            return
        L, T = model.chip_layout(chip), model.chip_technology(chip)
        keys = sorted(L.polygons)
        for key in keys:
            name = T.lyp_names.get(key, "")
            self.layer.addItem(f"{key[0]}/{key[1]}  {name}  ({L.count(key):,})", key)
        for i, key in enumerate(keys):
            if key in T.pad_layers:
                self.layer.setCurrentIndex(i)

    def _counts(self):
        doc = self.doc()
        self.chip_count.setText(f"{len(model.all_pads(doc, 'chip'))} chip pads")
        self.pkg_count.setText(f"{len(model.all_pads(doc, 'package'))} package pads")

    def detect(self):
        chip = self._chip()
        if chip is None:
            return _status(self.status, "Import a chip first.", False)
        self.doc().openTransaction("Find chip pads")
        found = model.detect_chip_pads(chip, [self.layer.currentData()], self.min_size.value() / 1000.0)
        self.doc().recompute()
        self.doc().commitTransaction()
        self._counts()
        _refresh_browser()
        _status(self.status, f"{len(found)} chip pads on {chip.Label}, layer {self.layer.currentData()}.",
                ok=bool(found))

    def add(self, alike):
        chosen = selection.faces()
        if not chosen:
            return _status(self.status, "Select a bond finger's top face first.", False)
        face, leaf = chosen[0]
        container = selection.container_of(leaf)
        if container is None:
            return _status(self.status, "That face is not on an imported housing, lid or board.", False)
        if alike:
            faces = pd.similar_faces(model.container_shape(container), face)
        else:
            faces = [f for f, o in chosen if selection.container_of(o) is container]
        have = [model.pad_centre(p) for p in model.pads_of(container)]
        faces = [f for f in faces
                 if not any((f.CenterOfMass - h).Length < 1e-6 and abs(f.BoundBox.ZMax - h.z) < 1e-6
                            for h in have)]
        self.doc().openTransaction("Add package pads")
        made = model.add_package_pads(container, faces)
        self.doc().recompute()
        self.doc().commitTransaction()
        FreeCADGui.Selection.clearSelection()
        self._counts()
        _refresh_browser()
        _status(self.status, f"{len(made)} package pads added on {container.Label}.")

    def mirror(self):
        doc = self.doc()
        if not model.by_role(doc, model.CHIP):
            return _status(self.status, "Mirroring is about the chip's centre — import a chip first.", False)
        doc.openTransaction("Mirror package pads")
        made = []
        containers = {}
        for p in model.all_pads(doc, "package"):
            c = selection.container_of(p)
            if c is not None:
                containers[c.Name] = c
        for container in containers.values():
            # about the chip in the same package, else the nearest one
            owner = model.assembly_owner(container)
            chips = [c for c in model.by_role(doc, model.CHIP)
                     if owner is not None and model.assembly_owner(c) is not None
                     and model.assembly_owner(c).Name == owner.Name]
            chip = chips[0] if chips else _nearest_chip(doc, model.global_placement(container).Base)
            made += model.mirror_package_pads(container, model.global_placement(chip).Base)
        doc.recompute()
        doc.commitTransaction()
        self._counts()
        _refresh_browser()
        _status(self.status, f"{len(made)} mirrored pads added (where none was).")

    def remove(self):
        picked = selection.pads()
        self.doc().openTransaction("Remove pads")
        for w in wires.wires(self.doc()):
            if w.ChipPad in picked or w.PackagePad in picked:
                self.doc().removeObject(w.Name)
        for p in picked:
            self.doc().removeObject(p.Name)
        self.doc().commitTransaction()
        self._counts()
        _refresh_browser()
        _status(self.status, f"{len(picked)} pads removed, with their wires.")


# ── ports ───────────────────────────────────────────────────────────────────

class PortsPanel(_SurfacePanel):
    title = "Ports"
    face_hint = ("For a port in a face's plane (a board's edge face for a wave port across a trace): select "
                 "the face, press “Use selected face”, then type or click port centres. For a port standing "
                 "on an edge, no face is needed: use “Draw on an edge…”.")

    def __init__(self):
        super().__init__()
        self._build_surface(self.layout)
        self._build_ports()
        self.finish()
        self._fill_ports()
        for port in ports.ports(self.doc()):        # ports from older documents too
            ports.show_as_face(port)

    def _frame_changed(self):
        self._fill_ports()

    def _build_ports(self):
        box = QtWidgets.QGroupBox("Ports — rectangles for the simulation")
        pv = QtWidgets.QVBoxLayout(box)
        form = QtWidgets.QFormLayout()
        size = QtWidgets.QHBoxLayout()
        self.pt_w, self.pt_h = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for label, sb, value in (("W", self.pt_w, 1.0), ("H", self.pt_h, 1.0)):
            sb.setRange(0.0001, 10000)
            sb.setDecimals(4)
            sb.setValue(value)
            sb.setSuffix(" mm")
            size.addWidget(QtWidgets.QLabel(label))
            size.addWidget(sb, 1)
        form.addRow("Size (X × Y)", size)
        kind = QtWidgets.QHBoxLayout()
        self.pt_type = QtWidgets.QComboBox()
        self.pt_type.addItems(ports.KINDS)
        self.pt_z0 = QtWidgets.QDoubleSpinBox()
        self.pt_z0.setRange(0.001, 1e6)
        self.pt_z0.setValue(50.0)
        self.pt_z0.setSuffix(" Ω")
        self.pt_dir = QtWidgets.QComboBox()
        self.pt_dir.addItems(ports.DIRECTIONS)
        self.pt_dir.setCurrentText("+Y")
        self.pt_dir.setToolTip("Lumped port: the excitation runs along the port's width (X) or height (Y).")
        kind.addWidget(self.pt_type)
        kind.addWidget(self.pt_z0)
        kind.addWidget(self.pt_dir)
        form.addRow("Type, Z0, field", kind)
        at = QtWidgets.QHBoxLayout()
        self.pt_x, self.pt_y = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for label, sb in (("X", self.pt_x), ("Y", self.pt_y)):
            sb.setRange(-100000, 100000)
            sb.setDecimals(4)
            sb.setSuffix(" mm")
            at.addWidget(QtWidgets.QLabel(label))
            at.addWidget(sb, 1)
        add = QtWidgets.QPushButton("Add port")
        at.addWidget(add)
        form.addRow("Centre at", at)
        pv.addLayout(form)
        click = QtWidgets.QPushButton("Click port centres…")
        pv.addWidget(click)

        # On an edge: no face to choose first; the edge gives the plane.
        edge_box = QtWidgets.QGroupBox("Or draw it on an edge")
        ef = QtWidgets.QFormLayout(edge_box)
        edge_hint = QtWidgets.QLabel(
            "Click an edge of a chip layer, board or housing, click again along it, then move up "
            "or down and click. It stands upright on the edge; Type, Z0 and field are as above.")
        edge_hint.setWordWrap(True)
        ef.addRow(edge_hint)
        from . import edge_ports
        self.pe_length = QtWidgets.QComboBox()
        self.pe_length.addItem("From the two clicks", edge_ports.LENGTH_CLICKS)
        self.pe_length.addItem("W above (second click: which way)", edge_ports.LENGTH_TYPED)
        ef.addRow("Length", self.pe_length)
        self.pe_height = QtWidgets.QComboBox()
        self.pe_height.addItem("Follow the mouse", edge_ports.FOLLOW)
        self.pe_height.addItem("H above (mouse: up or down)", edge_ports.TYPED)
        self.pe_height.addItem("To the next edge above / below", edge_ports.NEXT)
        self.pe_height.setToolTip(
            "To the next edge: the port ends at the nearest edge of other shown geometry above or "
            "below it — the ground under a trace, say. The body it starts on does not count; "
            "hidden layers do not either.")
        ef.addRow("Height", self.pe_height)
        draw = QtWidgets.QPushButton("Draw on an edge…")
        ef.addRow(draw)
        pv.addWidget(edge_box)
        draw.clicked.connect(self.draw_edge_ports)
        self.pt_table = QtWidgets.QTableWidget(0, 7)
        self.pt_table.setHorizontalHeaderLabels(["Port", "X (mm)", "Y (mm)", "W (mm)", "H (mm)", "Type", "Z0 (Ω)"])
        self.pt_table.horizontalHeader().setStretchLastSection(True)
        self.pt_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.pt_table.setMinimumHeight(120)
        pv.addWidget(self.pt_table)
        row = QtWidgets.QHBoxLayout()
        self.pt_axis = QtWidgets.QComboBox()
        for text, key in (("over X", "x"), ("over Y", "y"), ("over both", "both")):
            self.pt_axis.addItem(text, key)
        mirror = QtWidgets.QPushButton("Mirror selected")
        drop = QtWidgets.QPushButton("Remove selected")
        row.addWidget(mirror)
        row.addWidget(self.pt_axis)
        row.addWidget(drop)
        pv.addLayout(row)
        self.layout.addWidget(box)
        add.clicked.connect(self.add_typed_port)
        click.clicked.connect(self.click_ports)
        mirror.clicked.connect(self.mirror_ports)
        drop.clicked.connect(self.remove_ports)
        self.pt_table.cellChanged.connect(self._port_edited)

    def reject(self):
        # a drawing still running reads this panel: end it before it goes
        if getattr(self, "_drawing", None) is not None:
            self._drawing.stop("the Ports panel was closed")
        return super().reject()

    def _edge_settings(self):
        a = self._port_args()
        return dict(length_mode=self.pe_length.currentData(), length=a["w"],
                    height_mode=self.pe_height.currentData(), height=a["h"],
                    kind=a["kind"], z0=a["z0"], direction=a["direction"])

    def _shown(self):
        """Whether this panel is still open. Closing a task dialog sets its
        form to None; isHidden, not isVisible, since a panel on a combo-view
        tab that is not in front is still open."""
        form = getattr(self, "form", None)
        return form is not None and not form.isHidden()

    def draw_edge_ports(self):
        from . import edge_ports
        doc = self.doc()
        if doc is None or FreeCADGui.ActiveDocument is None:
            return _status(self.status, "Open a document with a 3-D view first.", False)

        def made(_obj):
            try:
                self._fill_ports()
            except RuntimeError:            # the panel was closed meanwhile
                pass
            QtWidgets.QApplication.processEvents()

        self._drawing = edge_ports.EdgePortDrawing(doc, self._edge_settings,
                                                   lambda text, ok=True: _status(self.status, text, ok), made,
                                                   alive=self._shown)
        try:
            count = self._drawing.run()
        finally:
            self._drawing = None
        _refresh_browser()
        try:
            self._fill_ports()
            _status(self.status, f"{count} port(s) drawn on edges.")
        except RuntimeError:                # closed while drawing: nothing to show it in
            FreeCAD.Console.PrintMessage(f"[PackageStudio] {count} port(s) drawn on edges.\n")

    def _port_args(self):
        return dict(w=self.pt_w.value(), h=self.pt_h.value(), kind=self.pt_type.currentText(),
                    z0=self.pt_z0.value(), direction=self.pt_dir.currentText())

    def _fill_ports(self):
        self.pt_table.blockSignals(True)
        objs = ports.ports(self.doc())
        self.pt_table.setRowCount(len(objs))
        for r, o in enumerate(objs):
            x, y = ports.xy_in(o, self._frame) if self._frame is not None else (o.PortX.Value, o.PortY.Value)
            name = QtWidgets.QTableWidgetItem(o.Label)
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            name.setData(QtCore.Qt.UserRole, o.Name)
            self.pt_table.setItem(r, 0, name)
            for c, v in enumerate((x, y, o.PortWidth.Value, o.PortHeight.Value), 1):
                self.pt_table.setItem(r, c, QtWidgets.QTableWidgetItem(f"{v:.4f}"))
            self.pt_table.setItem(r, 5, QtWidgets.QTableWidgetItem(o.PortType))
            self.pt_table.setItem(r, 6, QtWidgets.QTableWidgetItem(f"{o.Impedance:g}"))
        self.pt_table.blockSignals(False)

    def _new_port(self, xy):
        a = self._port_args()
        return ports.add_port(self._face_container, self._frame, xy, a["w"], a["h"], a["kind"], a["z0"],
                              a["direction"])

    def add_typed_port(self):
        if not self._need_face():
            return
        doc = self.doc()
        doc.openTransaction("Add port")
        obj = self._new_port((self.pt_x.value(), self.pt_y.value()))
        doc.recompute()
        doc.commitTransaction()
        self._fill_ports()
        _refresh_browser()
        _status(self.status, f"{obj.Label}: {obj.PortType} port, {obj.PortWidth.Value:g} × "
                             f"{obj.PortHeight.Value:g} mm, centred at ({obj.PortX.Value:g}, {obj.PortY.Value:g}) mm.")

    def click_ports(self):
        if not self._need_face():
            return
        from ..core import contacts as ct
        doc, frame, grid, pitch = self.doc(), self._frame, self._grid, self._snap_pitch()
        made = []

        def on_move(q):
            xy = ct.snap(frame.to_uv(tuple(q)), pitch)
            self.ct_cursor.setText(f"X {xy[0]:.4f}  Y {xy[1]:.4f} mm")
            if grid is not None:
                grid.move_cross(*xy)

        def on_point(q):
            made.append(self._new_port(ct.snap(frame.to_uv(tuple(q)), pitch)))
            doc.recompute()
            self._fill_ports()
            _status(self.status, f"{len(made)} port(s) placed — click the next centre, Esc ends.")
            QtWidgets.QApplication.processEvents()
            return True

        doc.openTransaction("Ports")
        try:
            selection.pick_points(None, FreeCAD.Vector(*frame.origin), FreeCAD.Vector(*frame.n),
                                  "Ports: click their centres on the face's plane, Esc ends.", on_point, on_move,
                                  tol=1e-3 + (3 * grid.lift if grid is not None else 0.0))
        finally:
            doc.commitTransaction()
            self.ct_cursor.setText("")
            if grid is not None:
                self._draw_grid()
        _refresh_browser()
        _status(self.status, f"{len(made)} ports placed.")

    def _port_edited(self, row, col):
        obj = self.doc().getObject(self.pt_table.item(row, 0).data(QtCore.Qt.UserRole))
        text = self.pt_table.item(row, col).text().strip().replace(",", ".")
        try:
            self.doc().openTransaction("Edit port")
            if col in (1, 2):
                xy = (float(self.pt_table.item(row, 1).text().replace(",", ".")),
                      float(self.pt_table.item(row, 2).text().replace(",", ".")))
                ports.move_port(obj, xy, self._frame)
            elif col in (3, 4):
                ports.resize_port(obj, float(self.pt_table.item(row, 3).text().replace(",", ".")),
                                  float(self.pt_table.item(row, 4).text().replace(",", ".")))
            elif col == 5:
                if text not in ports.KINDS:
                    raise ValueError(f"type is {' or '.join(ports.KINDS)}")
                obj.PortType = text
            elif col == 6:
                obj.Impedance = float(text)
            self.doc().commitTransaction()
            _status(self.status, f"{obj.Label} changed.")
        except ValueError as exc:
            self.doc().abortTransaction()
            _status(self.status, f"{obj.Label} not changed: {exc}", False)
        self._fill_ports()

    def _selected_ports(self):
        rows = sorted({i.row() for i in self.pt_table.selectedItems()})
        objs = [self.doc().getObject(self.pt_table.item(r, 0).data(QtCore.Qt.UserRole)) for r in rows]
        return [o for o in objs if o is not None]

    def mirror_ports(self):
        if not self._need_face():
            return
        picked = self._selected_ports()
        if not picked:
            return _status(self.status, "Select the ports to mirror in the table first.", False)
        doc = self.doc()
        doc.openTransaction("Mirror ports")
        made = ports.mirror_ports(picked, self.pt_axis.currentData(), self._frame, self._centre())
        doc.recompute()
        doc.commitTransaction()
        self._fill_ports()
        _refresh_browser()
        _status(self.status, f"{len(made)} mirrored port(s) added.")

    def remove_ports(self):
        picked = self._selected_ports()
        doc = self.doc()
        doc.openTransaction("Remove ports")
        for o in picked:
            doc.removeObject(o.Name)
        doc.commitTransaction()
        self._fill_ports()
        _refresh_browser()
        _status(self.status, f"{len(picked)} port(s) removed.")


# The bond-wire, materials and move panels live in their own modules;
# commands find every panel here.
from .wires_panel import WiresPanel  # noqa: E402,F401
from .materials_panel import MaterialsPanel  # noqa: E402,F401
from .move_panel import MovePanel  # noqa: E402,F401
