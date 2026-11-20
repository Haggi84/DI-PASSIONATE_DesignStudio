# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The Element Forge: new housings from a sentence or a datasheet, edited by
hand, checked as you type, seen before they are inserted.

  Describe   a sentence, a part number, or a package drawing's dimension
             table pasted in. *Generate* asks the local model (Ollama) for a
             new element, *Change* for a change to the one in the form
             ("pitch 0.4, keep the body"). The fields fill in one by one as
             the model writes them.
  Numbers    every value of the element, in mm. Values the model chose
             itself are marked amber (≈) until they are confirmed or
             changed — those are the ones to check against the datasheet.
  Checks     run on every change: errors (the geometry would overlap or
             leave no finger) block inserting; each has a fix to apply.
             Then the real build: the preview is jedec.build, and
             housing.analyse counts its bondable fingers and open paddle —
             the same checks the built-in catalogue passes.
  Keep       *Save to library* writes a JSON file to the user's element
             library (it shows up in the Housing library under "Mine");
             *Insert* puts it into the assembly; *Update selected housing*
             rebuilds a housing in place — its pads keep their pin numbers,
             so wires stay on their pins.

The model is optional: everything but Generate/Change works without it.
"""

import json

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from ..app import element_ai, housings, model
from ..core import elements as el, jedec
from .panels import _Panel

PREVIEW = "PS_ElementPreview"
AMBER = "#ffd27a"
FLASH = "#9be7a1"
LEVEL_MARK = {"error": "✖", "warning": "▲", "info": "ℹ"}
#: the panel opened last (for the GUI test)
last_panel = None


def _prefs():
    from . import em_preferences
    return em_preferences


class _Generate(QtCore.QThread):
    field = QtCore.Signal(str, object)
    text = QtCore.Signal(str)
    done = QtCore.Signal(object, str)        # Draft or None, error text

    def __init__(self, prompt, base, base_assumed, model_name, url):
        super().__init__()
        self.args = (prompt, base, base_assumed, model_name, url)

    def run(self):
        prompt, base, base_assumed, model_name, url = self.args
        try:
            draft = element_ai.generate(prompt, base=base, base_assumed=base_assumed, model=model_name, url=url,
                                        on_field=lambda k, v: self.field.emit(k, v),
                                        on_text=lambda t: self.text.emit(t))
            self.done.emit(draft, "")
        except Exception as error:      # shown in the panel, never raised into Qt
            self.done.emit(None, str(error))


class ElementPanel(_Panel):
    title = "Element Forge"

    def __init__(self, housing=None):
        global last_panel
        super().__init__()
        last_panel = self
        self.assumed = set()
        self.origin = {"by": "hand"}
        self.notes = ""
        self.housing = housing
        self.worker = None
        self._loading = False
        self._streamed = 0
        self._preview_fitted = False
        self._quiet = False             # no preview until the numbers change (after Insert)

        # -- describe ------------------------------------------------------------------------
        box = QtWidgets.QGroupBox("Describe it")
        bl = QtWidgets.QVBoxLayout(box)
        self.prompt = QtWidgets.QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "e.g.  QFN-24 4x4 mm, 0.5 mm pitch, exposed pad 2.6 mm\n"
            "or paste a datasheet's package dimension table (D, E, A, e, b, L, D2, E2) …")
        self.prompt.setFixedHeight(76)
        bl.addWidget(self.prompt)
        row = QtWidgets.QHBoxLayout()
        self.model_box = QtWidgets.QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setToolTip("The Ollama model; the list is what your Ollama has")
        self.model_box.setMinimumWidth(130)
        self.gen_button = QtWidgets.QPushButton("✦ Generate")
        self.gen_button.setToolTip("A new element from the description (Ctrl+Enter)")
        self.change_button = QtWidgets.QPushButton("↻ Change")
        self.change_button.setToolTip("Change the element in the form as described, keep the rest")
        row.addWidget(self.model_box, 1)
        row.addWidget(self.gen_button)
        row.addWidget(self.change_button)
        bl.addLayout(row)
        self.stream = QtWidgets.QLabel("")
        self.stream.setWordWrap(True)
        self.stream.setStyleSheet("color: gray;")
        bl.addWidget(self.stream)
        self.layout.addWidget(box)

        # -- start from ----------------------------------------------------------------------
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(QtWidgets.QLabel("Start from"))
        self.start = QtWidgets.QComboBox()
        srow.addWidget(self.start, 1)
        self.layout.addLayout(srow)

        # -- the numbers ---------------------------------------------------------------------
        nums = QtWidgets.QGroupBox("Numbers, mm — datasheet symbols; ≈ amber: assumed by the model")
        grid = QtWidgets.QGridLayout(nums)
        grid.setHorizontalSpacing(6)
        self.inputs = {}
        for i, f in enumerate(el.FIELDS):
            r, c = (i, 0) if i < 3 else ((i - 3) // 2 + 3, ((i - 3) % 2) * 2)
            if f.kind == "enum":
                w = QtWidgets.QComboBox()
                w.addItems(list(f.choices))
                w.currentTextChanged.connect(lambda _t, k=f.key: self._edited(k))
            elif f.kind == "str":
                w = QtWidgets.QLineEdit()
                w.textEdited.connect(lambda _t, k=f.key: self._edited(k))
            elif f.kind == "int":
                w = QtWidgets.QSpinBox()
                w.setRange(int(f.lo), int(f.hi))
                w.valueChanged.connect(lambda _v, k=f.key: self._edited(k))
            else:
                w = QtWidgets.QDoubleSpinBox()
                w.setDecimals(3)
                w.setSingleStep(0.05)
                w.setRange(f.lo, f.hi)
                w.setMinimumWidth(70)
                w.valueChanged.connect(lambda _v, k=f.key: self._edited(k))
            tip = f"<b>{f.label}</b>" + (f" ({f.sym})" if f.sym else "") + f"<br>{f.help}"
            w.setToolTip(tip)
            label = QtWidgets.QLabel(f.sym or f.label)
            label.setToolTip(tip)
            if f.kind in ("str", "enum"):
                grid.addWidget(label, r, 0)
                grid.addWidget(w, r, 1, 1, 3)
            else:
                grid.addWidget(label, r, c)
                grid.addWidget(w, r, c + 1)
            self.inputs[f.key] = (w, label)
        self.layout.addWidget(nums)

        self.derived = QtWidgets.QLabel("")
        self.derived.setWordWrap(True)
        self.layout.addWidget(self.derived)

        # -- checks --------------------------------------------------------------------------
        self.issues = QtWidgets.QTreeWidget()
        self.issues.setHeaderHidden(True)
        self.issues.setRootIsDecorated(False)
        self.issues.setWordWrap(True)
        self.issues.setMaximumHeight(120)
        self.issues.itemDoubleClicked.connect(self._fix_one)
        self.layout.addWidget(self.issues)
        frow = QtWidgets.QHBoxLayout()
        self.fix_button = QtWidgets.QPushButton("Apply fixes")
        self.fix_button.setToolTip("Apply the suggested value of every error (double-click one for just that)")
        self.confirm_button = QtWidgets.QPushButton("Confirm ≈ values")
        self.confirm_button.setToolTip("You checked the values the model chose: unmark them")
        frow.addWidget(self.fix_button)
        frow.addWidget(self.confirm_button)
        self.layout.addLayout(frow)

        # -- keep ----------------------------------------------------------------------------
        krow = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save to library")
        self.insert_button = QtWidgets.QPushButton("Insert")
        self.update_button = QtWidgets.QPushButton("Update housing")
        self.update_button.setToolTip("Rebuild the selected housing from these numbers; pads keep their pin "
                                      "numbers, wires stay on their pins")
        for b in (self.save_button, self.insert_button, self.update_button):
            krow.addWidget(b)
        self.layout.addLayout(krow)
        self.finish()

        self.gen_button.clicked.connect(lambda: self.generate(change=False))
        self.change_button.clicked.connect(lambda: self.generate(change=True))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.prompt,
                        activated=lambda: self.generate(change=False))
        self.start.activated.connect(self._start_from)
        self.fix_button.clicked.connect(self.apply_fixes)
        self.confirm_button.clicked.connect(self.confirm)
        self.save_button.clicked.connect(self.save)
        self.insert_button.clicked.connect(self.insert)
        self.update_button.clicked.connect(self.update_housing)

        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)

        self._fill_models()
        self._fill_start()
        housing = housing or self._selected_housing()
        found = housings.spec_of(housing) if housing is not None else None
        if found:
            self.housing = housing
            spec, assumed, origin = found
            self.load(spec, assumed, origin)
            self.status.setText(f"Editing {housing.Label}. Change the numbers, then Update housing.")
        else:
            self.load(el.EXAMPLE)

    # -- loading and reading the form ----------------------------------------------------------

    def _fill_models(self):
        url = _prefs().get("OllamaUrl")
        found = element_ai.models(url)
        wanted = _prefs().get("Model") or element_ai.DEFAULT_MODEL
        self.model_box.clear()
        if found:
            self.model_box.addItems(found)
        idx = self.model_box.findText(wanted)
        if idx >= 0:
            self.model_box.setCurrentIndex(idx)
        else:
            self.model_box.setEditText(found[0] if found else wanted)
        if found is None:
            self.stream.setText(f"Ollama does not answer at {url}: Generate needs it running "
                                "(ollama.com). Everything else works without it.")

    def _fill_start(self):
        self.start.clear()
        self.start.addItem("— choose a starting point —", None)
        self._library = el.load_all()
        for e in self._library:
            self.start.addItem(f"Mine: {e.name}", ("mine", e))
        for spec in jedec.CATALOGUE:
            self.start.addItem(f"JEDEC: {spec.name}", ("jedec", spec))
        for h in model.by_role(FreeCAD.ActiveDocument, model.HOUSING) if FreeCAD.ActiveDocument else []:
            if housings.spec_of(h):
                self.start.addItem(f"In assembly: {h.Label}", ("doc", h))

    def _start_from(self, idx):
        data = self.start.itemData(idx)
        if not data:
            return
        kind, item = data
        if kind == "mine":
            self.load(item.spec, item.assumed, item.origin, item.notes)
        elif kind == "jedec":
            self.load(el.from_spec(item), (), {"by": "hand", "from": item.name})
        else:
            spec, assumed, origin = housings.spec_of(item)
            self.housing = item
            self.load(spec, assumed, origin)
        self.start.setCurrentIndex(0)

    def load(self, spec, assumed=(), origin=None, notes=""):
        spec = el.normalise(spec)
        self._loading = True
        try:
            for key, value in spec.items():
                self._set(key, value)
        finally:
            self._loading = False
        self._quiet = False
        self.assumed = set(assumed)
        self.origin = dict(origin or {"by": "hand"})
        self.notes = notes
        self._mark()
        self.refresh()

    def _set(self, key, value):
        w = self.inputs[key][0]
        w.blockSignals(True)
        try:
            if isinstance(w, QtWidgets.QComboBox):
                w.setCurrentText(str(value))
            elif isinstance(w, QtWidgets.QLineEdit):
                w.setText(str(value))
            elif isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(round(float(value))))
            else:
                w.setValue(float(value))
        except (TypeError, ValueError):
            pass
        finally:
            w.blockSignals(False)

    def spec(self):
        out = {}
        for key, (w, _l) in self.inputs.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[key] = w.currentText()
            elif isinstance(w, QtWidgets.QLineEdit):
                out[key] = w.text()
            else:
                out[key] = w.value()
        return el.normalise(out)

    def _mark(self):
        for key, (w, label) in self.inputs.items():
            guessed = key in self.assumed
            w.setStyleSheet(f"background: {AMBER};" if guessed else "")
            label.setText(("≈ " if guessed else "") + (el.BY_KEY[key].sym or el.BY_KEY[key].label))

    def _flash(self, key):
        w = self.inputs[key][0]
        w.setStyleSheet(f"background: {FLASH};")
        QtCore.QTimer.singleShot(700, self._mark)

    def _edited(self, key):
        if self._loading:
            return
        self._quiet = False
        if key in self.assumed:          # the person has set it now
            self.assumed.discard(key)
            self._mark()
        self._timer.start()

    # -- checks and preview --------------------------------------------------------------------

    def refresh(self):
        spec = self.spec()
        issues = el.validate(spec)
        built = None
        if not el.errors(issues):
            built, more = self._build_check(spec)
            issues += more
        self.issues.clear()
        for i in issues:
            item = QtWidgets.QTreeWidgetItem([f"{LEVEL_MARK[i.level]}  {i.message}"])
            item.setData(0, QtCore.Qt.UserRole, i)
            item.setForeground(0, QtGui.QBrush(QtGui.QColor(
                {"error": "#c0392b", "warning": "#b9770e", "info": "#2471a3"}[i.level])))
            if i.fix:
                item.setToolTip(0, "Double-click to apply: " + ", ".join(f"{k} = {v:g}" if isinstance(v, float)
                                                                         else f"{k} = {v}" for k, v in i.fix.items()))
            self.issues.addTopLevelItem(item)
        if not issues:
            self.issues.addTopLevelItem(QtWidgets.QTreeWidgetItem(["✔  Builds clean: no overlaps, every lead "
                                                                   "bondable, paddle open."]))
        bad = bool(el.errors(issues))
        self.fix_button.setEnabled(any(i.fix for i in el.errors(issues)))
        self.confirm_button.setEnabled(bool(self.assumed))
        for b in (self.insert_button, self.save_button):
            b.setEnabled(not bad)
        self.update_button.setEnabled(not bad and self._target() is not None)
        g = el.geometry(spec)
        text = (f"<b>{el.leads(spec)} leads</b> · {spec['pitch']:g} mm pitch · leads "
                f"{min(g['len_x'] if spec['per_side_x'] else 99, g['len_y'] if spec['per_side_y'] else 99):.2f} mm "
                f"long · paddle {spec['paddle_x']:g} × {spec['paddle_y']:g} mm")
        if built:
            text += f" · <span style='color:#1e8449'>{built}</span>"
        if self.notes:
            text += f"<br><i>{self.notes}</i>"
        self.derived.setText(text)
        return issues

    def _build_check(self, spec):
        """Build it, analyse it like any housing, show it. ("summary", [Issue])."""
        from ..core import housing as hs
        try:
            parts = jedec.build(el.to_spec(spec))
        except Exception as error:
            return None, [el.Issue("error", "", f"The geometry does not build: {error}")]
        solids = [parts["body"]] + parts["leadframe"]
        a = hs.analyse(solids)
        out = []
        if not parts["body"].isValid():
            out.append(el.Issue("error", "", "The body solid is not valid."))
        if len(a.fingers) != el.leads(spec):
            out.append(el.Issue("error", "finger", f"{len(a.fingers)} of {el.leads(spec)} leads are open for "
                                                   "bonding: the cavity covers the rest."))
        if a.attach is None:
            out.append(el.Issue("error", "paddle_x", "The die paddle is not open."))
        if not self._quiet:
            self._show(parts, a)
        return f"{len(a.fingers)}/{el.leads(spec)} fingers bondable", out

    def _show(self, parts, a):
        import Part
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("Package")
        obj = doc.getObject(PREVIEW)
        if obj is None:
            obj = doc.addObject("Part::Feature", PREVIEW)
            obj.Label = "Element preview"
        shapes = [parts["body"]] + parts["leadframe"]
        compound = Part.makeCompound(shapes)
        # beside the assembly, not inside it
        others = [o for o in doc.RootObjects
                  if o.Name != PREVIEW and (o.TypeId == "App::Part" or hasattr(o, "Shape"))]
        x0 = 0.0
        for o in others:
            bb = model.global_bbox(o) if o.TypeId == "App::Part" else o.Shape.BoundBox
            if bb.isValid():
                x0 = max(x0, bb.XMax + 3.0 + compound.BoundBox.XLength / 2)
        obj.Shape = compound
        obj.Placement = FreeCAD.Placement(FreeCAD.Vector(x0, 0, 0), FreeCAD.Rotation())
        vo = getattr(obj, "ViewObject", None)
        if vo is not None:
            colours = [(0.25, 0.27, 0.30, 0.0)] * len(parts["body"].Faces)
            for s in parts["leadframe"]:
                colours += [(0.85, 0.55, 0.25, 0.0)] * len(s.Faces)
            try:
                vo.DiffuseColor = colours
            except Exception:
                vo.ShapeColor = (0.85, 0.55, 0.25)
            vo.Transparency = 0
            vo.Selectable = False
        doc.recompute()
        if not self._preview_fitted:
            self._preview_fitted = True
            view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
            if view is not None:
                view.viewIsometric()
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(obj)
                FreeCADGui.SendMsgToActiveView("ViewSelection")
                FreeCADGui.Selection.clearSelection()

    def _drop_preview(self):
        for doc in FreeCAD.listDocuments().values():
            if doc.getObject(PREVIEW) is not None:
                doc.removeObject(PREVIEW)

    # -- the model -----------------------------------------------------------------------------

    def generate(self, change):
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            self.stream.setText("Describe the package first.")
            return
        if self.worker is not None:
            return
        name = self.model_box.currentText().strip() or element_ai.DEFAULT_MODEL
        _prefs().put("Model", name)
        base = self.spec() if change else None
        self._streamed = 0
        self._before = self.spec()
        self.stream.setText(f"{name} is writing…")
        self.gen_button.setEnabled(False)
        self.change_button.setEnabled(False)
        self.worker = _Generate(prompt, base, sorted(self.assumed), name, _prefs().get("OllamaUrl"))
        self.worker.field.connect(self._on_field)
        self.worker.text.connect(self._on_text)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _on_text(self, chunk):
        self._streamed += len(chunk)

    def _on_field(self, key, value):
        self._loading = True
        try:
            self._set(key, value)
        finally:
            self._loading = False
        self._flash(key)
        self.stream.setText(f"{self.model_box.currentText()}: {el.BY_KEY[key].label} = {value}")

    def _on_done(self, draft, error):
        self.worker = None
        self.gen_button.setEnabled(True)
        self.change_button.setEnabled(True)
        if draft is None:
            self.stream.setText(f"<span style='color:#c0392b'>{error}</span>")
            self.load(self._before, self.assumed, self.origin, self.notes)
            return
        self.load(draft.spec, draft.assumed, {"by": "ollama", "model": draft.model, "prompt": draft.prompt},
                  draft.notes)
        repairs = f", corrected itself {draft.attempts - 1}×" if draft.attempts > 1 else ""
        n = len(draft.assumed)
        self.stream.setText(f"{draft.model}: {el.describe(draft.spec)}{repairs}. "
                            + (f"{n} value{'s' * (n != 1)} assumed (≈)." if n else "Nothing assumed."))

    # -- fixes ---------------------------------------------------------------------------------

    def _fix_one(self, item, _col=0):
        issue = item.data(0, QtCore.Qt.UserRole)
        if issue is not None and issue.fix:
            self.load(el.apply_fixes(self.spec(), [issue]), self.assumed - set(issue.fix), self.origin, self.notes)

    def apply_fixes(self):
        spec, _issues = el.auto_repair(self.spec())
        changed = {k for k in spec if spec[k] != self.spec()[k]}
        self.load(spec, self.assumed - changed, self.origin, self.notes)
        self.status.setText("Fixed: " + ", ".join(el.BY_KEY[k].label for k in sorted(changed)) if changed
                            else "No fix applies; change the numbers by hand.")

    def confirm(self):
        self.assumed.clear()
        self._mark()
        self.refresh()

    # -- keeping it ----------------------------------------------------------------------------

    def _entry(self):
        return el.Entry(self.spec(), sorted(self.assumed), self.notes, dict(self.origin))

    def save(self):
        entry = self._entry()
        existing = next((e for e in el.load_all() if e.name == entry.name), None)
        if existing and QtWidgets.QMessageBox.question(
                self.form, "Element library", f"'{entry.name}' is in your library. Replace it?") \
                != QtWidgets.QMessageBox.Yes:
            return
        path = el.save(entry)
        self.status.setText(f"Saved to your library: {path}")
        self._fill_start()

    def insert(self):
        doc = FreeCAD.ActiveDocument or FreeCAD.newDocument("Package")
        self._drop_preview()
        self._preview_fitted = False
        try:
            h = housings.from_spec_dict(doc, self.spec(), sorted(self.assumed), self.origin)
        except Exception as error:
            self.status.setText(f"Could not insert: {error}")
            return
        self.housing = h
        self._quiet = True
        self.status.setText(f"Inserted {h.Label} with {len(model.pads_of(h))} pads. It keeps its numbers: "
                            "select it and open the Element Forge to change it.")
        FreeCADGui.SendMsgToActiveView("ViewFit")
        self.refresh()

    def _selected_housing(self):
        for sel in FreeCADGui.Selection.getSelection():
            obj = sel
            while obj is not None:
                if model.role(obj) == model.HOUSING:
                    return obj
                obj = obj.getParentGeoFeatureGroup()
        return None

    def _target(self):
        h = self._selected_housing() or self.housing
        try:
            return h if h is not None and h.Document is not None and housings.spec_of(h) else None
        except ReferenceError:
            return None

    def update_housing(self):
        h = self._target()
        if h is None:
            self.status.setText("Select a housing that was built from numbers (JEDEC or Element Forge).")
            return
        try:
            kept, added, removed = housings.rebuild(h, self.spec(), sorted(self.assumed), self.origin)
        except Exception as error:
            self.status.setText(f"Could not update {h.Label}: {error}")
            return
        text = f"Rebuilt {h.Label}: {kept} pads kept"
        if added:
            text += f", {added} added"
        if removed:
            text += f", {removed} removed with their wires"
        self.status.setText(text + ".")

    # -- task panel ----------------------------------------------------------------------------

    def reject(self):
        if self.worker is not None:
            self.worker.wait(100)
        self._drop_preview()
        return super().reject()
