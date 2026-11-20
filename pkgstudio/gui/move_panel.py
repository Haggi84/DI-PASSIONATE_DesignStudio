# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Move: chips, lids, packages and boards moved after they were placed —
typed, stepped or dragged, one or several at once.

The table lists everything movable with its offset from where it was
placed (app.moving): edit a number and it moves there. The rows selected
in the table are what the buttons move; "Take 3-D selection" selects the
rows of what is selected in the view (a face of a chip selects the chip).

Move to a midpoint: two points picked in the view give a target (their
midpoint); the rows selected move so that a reference — the centre of
their box, of its underside, or points picked on them — lands on it,
along XYZ, XY, XZ, YZ or one axis only (app.moving.move_to).

Dragging: press "Drag in the view…", click a point to grab, move the mouse
— everything selected follows over the surface it stands on — and click to
drop. Esc puts everything back. Before the first click the arrow keys step
by the step size and R turns by the turn step. The wires on what is being
dragged are hidden during the drag and rebuilt on the drop: a rebuild per
mouse move would make the drag stutter on any real chip.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from ..app import model, moving, wires
from . import selection
from .panels import _Panel, _refresh_browser, _status

_COLUMNS = ["Object", "X (mm)", "Y (mm)", "Turn (°)", "In"]


def movable_of(obj):
    """What moves when *obj* (a selected leaf) is to move: the chip it is
    part of, else its lid, package or board."""
    for p in [obj] + model.ancestors(obj):
        if model.role(p) == model.CHIP:
            return p
    for p in [obj] + model.ancestors(obj):
        if moving.is_movable(p):
            return p
    return None


class _Drag:
    """One drag in the 3-D view: grab, follow, drop; Esc cancels."""

    def __init__(self, panel, objs):
        self.panel, self.objs = panel, objs
        self.doc = objs[0].Document
        self.view = FreeCADGui.ActiveDocument.ActiveView
        first = objs[0]
        g = model.global_placement(first)
        self.plane_point = g.multVec(moving.pivot(first))
        self.normal = g.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        self.normal.normalize()
        self.grab = None
        self.start = {o.Name: moving.offsets(o) for o in objs}
        self.loop = QtCore.QEventLoop()
        self.cancelled = False
        self.hidden = []

    def _on_plane(self, x, y):
        p = self.view.getPoint(x, y)
        d = self.view.getViewDirection()
        denom = d.dot(self.normal)
        if abs(denom) < 1e-12:
            return None
        return p + d * ((self.plane_point - p).dot(self.normal) / denom)

    def _hide_wires(self):
        for w in wires.wires_of(self.objs):
            vo = getattr(w, "ViewObject", None)
            if vo is not None and vo.Visibility:
                vo.Visibility = False
                self.hidden.append(vo)

    def _show_wires(self):
        for vo in self.hidden:
            try:
                vo.Visibility = True
            except Exception:
                pass
        self.hidden = []

    def on_click(self, info):
        if info.get("State") != "DOWN" or info.get("Button") != "BUTTON1":
            return
        q = self._on_plane(*info["Position"])
        if q is None:
            return
        if self.grab is None:
            self.grab = q
            self._hide_wires()
            self.panel.say("Move the mouse; click to drop, Esc puts everything back.")
        else:
            self.loop.quit()

    def on_motion(self, info):
        if self.grab is None:
            return
        q = self._on_plane(*info["Position"])
        if q is None:
            return
        delta = q - self.grab
        delta = delta - self.normal * delta.dot(self.normal)
        moving.drag(self.objs, delta, self.panel.step(), update=False, start=self.start)
        self.panel.show_offsets()

    def on_key(self, info):
        if info.get("State") != "DOWN":
            return
        key = info.get("Key")
        if key == "ESCAPE":
            self.cancelled = True
            self.loop.quit()
            return
        if self.grab is not None:
            return
        step, turn = self.panel.step() or 0.01, self.panel.turn_step()
        moves = {"LEFT_ARROW": (-step, 0, 0), "RIGHT_ARROW": (step, 0, 0), "UP_ARROW": (0, step, 0),
                 "DOWN_ARROW": (0, -step, 0), "R": (0, 0, turn)}
        if key in moves:
            moving.nudge(self.objs, *moves[key], step=self.panel.step())
            self.start = {o.Name: moving.offsets(o) for o in self.objs}
            self.panel.show_offsets()

    def run(self):
        cbs = [("SoMouseButtonEvent", self.view.addEventCallback("SoMouseButtonEvent", self.on_click)),
               ("SoLocation2Event", self.view.addEventCallback("SoLocation2Event", self.on_motion)),
               ("SoKeyboardEvent", self.view.addEventCallback("SoKeyboardEvent", self.on_key))]
        try:
            self.loop.exec_() if hasattr(self.loop, "exec_") else self.loop.exec()
        finally:
            for kind, cb in cbs:
                self.view.removeEventCallback(kind, cb)
            if self.cancelled:
                for o in self.objs:
                    dx, dy, a = self.start[o.Name]
                    moving.set_offsets(o, dx, dy, a, update=False)
            self._show_wires()
            moving.rebuild_wires(self.objs)
        return not self.cancelled


class MovePanel(_Panel):
    title = "Move"

    def __init__(self):
        super().__init__()
        hint = QtWidgets.QLabel(
            "Select the rows to move (Ctrl/Shift for several), or “Take 3-D selection”. Offsets are from "
            "where each was placed, along the surface it stands on; edit them in the table, step them with "
            "the arrows, or drag. Wires follow.")
        hint.setWordWrap(True)
        self.layout.addWidget(hint)
        self.table = QtWidgets.QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setMinimumHeight(160)
        self.layout.addWidget(self.table)
        take = QtWidgets.QPushButton("Take 3-D selection")
        self.layout.addWidget(take)

        steps = QtWidgets.QFormLayout()
        self.step_box = QtWidgets.QDoubleSpinBox()
        self.step_box.setRange(0, 1000)
        self.step_box.setDecimals(4)
        self.step_box.setValue(0.05)
        self.step_box.setSuffix(" mm")
        self.step_box.setToolTip("How far an arrow moves, and the grid a drag snaps to (0: no snapping).")
        self.turn_box = QtWidgets.QComboBox()
        for deg in (1, 5, 15, 45, 90):
            self.turn_box.addItem(f"{deg}°", deg)
        self.turn_box.setCurrentIndex(4)
        steps.addRow("Step", self.step_box)
        steps.addRow("Turn step", self.turn_box)
        self.layout.addLayout(steps)

        pad = QtWidgets.QGridLayout()
        buttons = {}
        for text, r, c, move in (("↑ Y+", 0, 1, (0, 1, 0)), ("← X−", 1, 0, (-1, 0, 0)), ("X+ →", 1, 2, (1, 0, 0)),
                                 ("↓ Y−", 2, 1, (0, -1, 0)), ("⟲ turn", 0, 0, (0, 0, 1)),
                                 ("turn ⟳", 0, 2, (0, 0, -1))):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, m=move: self.step_move(*m))
            pad.addWidget(b, r, c)
            buttons[text] = b
        self.layout.addLayout(pad)

        by = QtWidgets.QHBoxLayout()
        self.by_x, self.by_y = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for label, sb in (("ΔX", self.by_x), ("ΔY", self.by_y)):
            sb.setRange(-100000, 100000)
            sb.setDecimals(4)
            sb.setSuffix(" mm")
            by.addWidget(QtWidgets.QLabel(label))
            by.addWidget(sb, 1)
        apply_by = QtWidgets.QPushButton("Move by")
        by.addWidget(apply_by)
        self.layout.addLayout(by)

        self._build_to_point()

        drag = QtWidgets.QPushButton("Drag in the view…")
        drag.setToolTip("Click to grab, move, click to drop; Esc puts it back. Before grabbing, the arrow keys "
                        "step and R turns.")
        self.layout.addWidget(drag)
        row = QtWidgets.QHBoxLayout()
        back = QtWidgets.QPushButton("Back to where placed")
        keep = QtWidgets.QPushButton("Keep here as placed")
        keep.setToolTip("Make where it is now its new base: offsets become zero.")
        row.addWidget(back)
        row.addWidget(keep)
        self.layout.addLayout(row)
        self.finish()

        take.clicked.connect(self.take_selection)
        apply_by.clicked.connect(self.move_by)
        drag.clicked.connect(self.drag)
        back.clicked.connect(self.back)
        keep.clicked.connect(self.keep)
        self.table.cellChanged.connect(self._edited)
        self.fill()
        self.take_selection(quiet=True)

    # ── move to a midpoint ──
    def _build_to_point(self):
        box = QtWidgets.QGroupBox("Move to a midpoint")
        lay = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel(
            "Select two points in the view (vertices, edges, faces; Ctrl for the second) and press “Take "
            "target”: the midpoint is the target. The rows selected in the table then move so that their "
            "reference lands on it, along the chosen axes only.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QtWidgets.QFormLayout()
        self.pt_mode = QtWidgets.QComboBox()
        self.pt_mode.addItem("Element centre (vertex, edge middle / circle centre, face centre)", "centre")
        self.pt_mode.addItem("Clicked point", "picked")
        form.addRow("Points", self.pt_mode)
        tgt = QtWidgets.QHBoxLayout()
        self.tx, self.ty, self.tz = QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox(), QtWidgets.QDoubleSpinBox()
        for label, sb in (("X", self.tx), ("Y", self.ty), ("Z", self.tz)):
            sb.setRange(-100000, 100000)
            sb.setDecimals(4)
            tgt.addWidget(QtWidgets.QLabel(label))
            tgt.addWidget(sb, 1)
        take_t = QtWidgets.QPushButton("Take target")
        take_t.setToolTip("Midpoint of the points selected in the view (one point: that point).")
        tgt.addWidget(take_t)
        form.addRow("Target (mm)", tgt)
        self.ref_kind = QtWidgets.QComboBox()
        self.ref_kind.addItem("Centre of its bounding box", "centre")
        self.ref_kind.addItem("Centre of its underside", "bottom")
        self.ref_kind.addItem("Points on it (take from the selection)", "picked")
        ref_row = QtWidgets.QHBoxLayout()
        ref_row.addWidget(self.ref_kind, 1)
        take_r = QtWidgets.QPushButton("Take reference")
        take_r.setToolTip("Midpoint of the points selected in the view, on the object to move.")
        ref_row.addWidget(take_r)
        form.addRow("Reference", ref_row)
        self.axes = QtWidgets.QComboBox()
        for key, text in (("XYZ", "XYZ – all three"), ("XY", "XY – Z stays"), ("XZ", "XZ – Y stays"),
                          ("YZ", "YZ – X stays"), ("X", "X only"), ("Y", "Y only"), ("Z", "Z only")):
            self.axes.addItem(text, key)
        self.axes.setCurrentIndex(1)
        form.addRow("Axes", self.axes)
        lay.addLayout(form)
        go = QtWidgets.QPushButton("Move to target")
        lay.addWidget(go)
        self.layout.addWidget(box)
        self._ref_point = None
        take_t.clicked.connect(self.take_target)
        take_r.clicked.connect(self.take_reference)
        go.clicked.connect(self.move_to_target)

    def _selected_points(self, what):
        pts = selection.points(self.pt_mode.currentData())
        if not pts:
            self.say(f"{what}: select one or two points in the view first.", False)
            return None
        if len(pts) > 2:
            self.say(f"{what}: {len(pts)} points selected — the centre of all of them is taken.")
        return pts

    def take_target(self):
        pts = self._selected_points("Target")
        if pts is None:
            return
        c = moving.midpoint(pts)
        for sb, v in ((self.tx, c.x), (self.ty, c.y), (self.tz, c.z)):
            sb.setValue(v)
        what = "midpoint of 2 points" if len(pts) == 2 else ("the point" if len(pts) == 1 else f"centre of {len(pts)} points")
        self.say(f"Target: {what}, ({c.x:.4f}, {c.y:.4f}, {c.z:.4f}) mm. Now select the rows to move.")

    def take_reference(self):
        pts = self._selected_points("Reference")
        if pts is None:
            return
        self._ref_point = moving.midpoint(pts)
        self.ref_kind.setCurrentIndex(self.ref_kind.findData("picked"))
        c = self._ref_point
        self.say(f"Reference: ({c.x:.4f}, {c.y:.4f}, {c.z:.4f}) mm.")

    def target(self):
        return FreeCAD.Vector(self.tx.value(), self.ty.value(), self.tz.value())

    def move_to_target(self):
        kind = self.ref_kind.currentData()
        if kind == "picked" and self._ref_point is None:
            return self.say("Take the reference from the selection first, or choose a centre.", False)
        axes = self.axes.currentData()
        result = {}

        def go(objs):
            ref = self._ref_point if kind == "picked" else None
            result["delta"], result["lifted"] = moving.move_to(objs, self.target(), reference=ref, axes=axes,
                                                               kind=kind if kind != "picked" else "centre")
        self._do(f"Move to target ({axes})", go)
        if "delta" in result:
            d = result["delta"]
            text = self.status.text() if hasattr(self.status, "text") else ""
            note = f"\nMoved by ({d.x:.4f}, {d.y:.4f}, {d.z:.4f}) mm along {axes}."
            if result["lifted"] > 1e-9:
                note += f" ⚠ {result['lifted'] * 1000:.1f} µm off the surface it stood on."
            self.say(text + note, ok=result["lifted"] <= 1e-9)
            if kind == "picked":
                self._ref_point = None          # the reference moved with the object

    # ── table ──
    def fill(self):
        doc = self.doc()
        chosen = {o.Name for o in self.chosen()}
        self.table.blockSignals(True)
        objs = moving.movable(doc) if doc is not None else []
        self.table.setRowCount(len(objs))
        for r, obj in enumerate(objs):
            name = QtWidgets.QTableWidgetItem(obj.Label)
            name.setFlags(name.flags() & ~QtCore.Qt.ItemIsEditable)
            name.setData(QtCore.Qt.UserRole, obj.Name)
            name.setToolTip(model.role(obj))
            self.table.setItem(r, 0, name)
            owner = obj.getParentGeoFeatureGroup()
            where = QtWidgets.QTableWidgetItem(owner.Label if owner is not None else "—")
            where.setFlags(where.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, 4, where)
        self.show_offsets()
        self.table.blockSignals(False)
        self._select_names(chosen)

    def _select_names(self, names):
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(QtCore.Qt.UserRole) in names:
                self.table.selectRow(r)
        self.table.setSelectionMode(mode)

    def show_offsets(self):
        doc = self.doc()
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            obj = doc.getObject(self.table.item(r, 0).data(QtCore.Qt.UserRole))
            if obj is None:
                continue
            dx, dy, a = moving.offsets(obj)
            for c, v, fmt in ((1, dx, "{:.4f}"), (2, dy, "{:.4f}"), (3, a, "{:.2f}")):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(fmt.format(v)))
        self.table.blockSignals(False)
        QtWidgets.QApplication.processEvents()

    def chosen(self):
        doc = self.doc()
        if doc is None:
            return []
        rows = sorted({i.row() for i in self.table.selectedItems()})
        objs = [doc.getObject(self.table.item(r, 0).data(QtCore.Qt.UserRole)) for r in rows]
        return [o for o in objs if o is not None]

    def step(self):
        return self.step_box.value()

    def turn_step(self):
        return float(self.turn_box.currentData())

    def say(self, text, ok=True):
        _status(self.status, text, ok)

    def _report(self, objs, what):
        warnings = moving.overlap_report(self.doc())
        self.say(f"{what}: {', '.join(o.Label for o in objs)}. Wires rebuilt."
                 + ("\n⚠ " + "\n⚠ ".join(warnings) if warnings else ""), ok=not warnings)

    def take_selection(self, quiet=False):
        found = []
        for leaf in selection.objects():
            m = movable_of(leaf)
            if m is not None and m.Name not in [f.Name for f in found]:
                found.append(m)
        if not found:
            if not quiet:
                self.say("Nothing movable is selected in the view.", False)
            return
        self._select_names({o.Name for o in found})
        self.say(f"{len(found)} selected: {', '.join(o.Label for o in found)}.")

    def _need(self):
        objs = self.chosen()
        if not objs:
            self.say("Select what to move in the table first (or “Take 3-D selection”).", False)
        return objs

    # ── moves ──
    def _do(self, what, fn):
        objs = self._need()
        if not objs:
            return
        doc = self.doc()
        doc.openTransaction(what)
        try:
            fn(objs)
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            return self.say(f"{what}: {exc}", False)
        self.show_offsets()
        self._report(objs, what)

    def step_move(self, sx, sy, sa):
        s = self.step() or 0.01
        self._do("Move", lambda objs: moving.nudge(objs, sx * s, sy * s, sa * self.turn_step(), step=self.step()))

    def move_by(self):
        self._do("Move by", lambda objs: moving.nudge(objs, self.by_x.value(), self.by_y.value()))

    def back(self):
        self._do("Back to where placed", moving.reset)

    def keep(self):
        def freeze(objs):
            for o in objs:
                moving.set_base(o)
        self._do("Keep here as placed", freeze)

    def _edited(self, row, col):
        if col not in (1, 2, 3):
            return
        doc = self.doc()
        obj = doc.getObject(self.table.item(row, 0).data(QtCore.Qt.UserRole))
        try:
            value = float(self.table.item(row, col).text().replace(",", ".").replace("°", ""))
        except ValueError:
            self.show_offsets()
            return self.say("Type a number.", False)
        doc.openTransaction("Move")
        moving.set_offsets(obj, *(value if c == col else None for c in (1, 2, 3)))
        doc.commitTransaction()
        self.show_offsets()
        self._report([obj], "Moved")

    def drag(self):
        objs = self._need()
        if not objs:
            return
        if FreeCADGui.ActiveDocument is None:
            return self.say("Open the document's 3-D view first.", False)
        doc = self.doc()
        self.say("Click a point to grab (arrow keys step, R turns first); Esc cancels.")
        doc.openTransaction("Drag")
        try:
            done = _Drag(self, objs).run()
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            return self.say(f"Drag: {exc}", False)
        try:
            self.show_offsets()
            if done:
                self._report(objs, "Dragged")
            else:
                self.say("Drag cancelled; everything is back where it was.")
        except RuntimeError:            # the panel was closed during the drag
            pass
        _refresh_browser()
