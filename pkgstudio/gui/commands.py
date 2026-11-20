# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The workbench's commands, in the order the work goes:

    Technology · Import GDS chips · Housing library · Element Forge · Import STEP part ·
    Place · Move · Pads · Ports · Bond wires · Materials ·
    EM simulation · EM assistant

and, in the menu only, Layers and Export geometry.
"""

import os
import traceback

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from ..app import model
from ..core import layout as lay, tech as tc

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "resources", "icons")
PARAMS = "User parameter:BaseApp/Preferences/Mod/PackageStudio"


def icon(name):
    return os.path.join(ICONS, name + ".svg")


def params():
    return FreeCAD.ParamGet(PARAMS)


def active_technology():
    """The technology folder chosen last, or None."""
    folder = params().GetString("TechFolder", "")
    return folder if folder and os.path.isfile(os.path.join(folder, "tech.json")) else None


def _doc():
    return FreeCAD.ActiveDocument or FreeCAD.newDocument("Package")


def _mw():
    return FreeCADGui.getMainWindow()


def _fail(title, exc):
    FreeCAD.Console.PrintError(f"[PackageStudio] {title}: {exc}\n{traceback.format_exc()}\n")
    QtWidgets.QMessageBox.critical(_mw(), title, str(exc)[:2000])


def choose_technology():
    """Ask for the technology; remember it. Returns the folder or None."""
    found = tc.presets()
    names = [n for n, _f in found] + ["Other folder…"]
    current = active_technology()
    idx = next((i for i, (_n, f) in enumerate(found) if os.path.normcase(f) == os.path.normcase(current or "")), 0)
    name, ok = QtWidgets.QInputDialog.getItem(
        _mw(), "Technology", "Chips are imported with this technology:", names, idx, False)
    if not ok:
        return None
    if name == "Other folder…":
        folder = QtWidgets.QFileDialog.getExistingDirectory(_mw(), "Technology folder (with tech.json)")
        if not folder or not os.path.isfile(os.path.join(folder, "tech.json")):
            if folder:
                QtWidgets.QMessageBox.warning(_mw(), "Technology", "That folder has no tech.json.")
            return None
    else:
        folder = dict(found)[name]
    params().SetString("TechFolder", folder)
    FreeCAD.Console.PrintMessage(f"[PackageStudio] Technology: {tc.load(folder).name}\n")
    return folder


def confirm_technology(gds_path, folder):
    """*folder*, or a better-fitting technology if the user takes it, or
    None to cancel. A layout read with the wrong technology builds no layer
    at all and comes in as a bare die block."""
    try:
        better = model.technology_suggestion(gds_path, folder)
    except Exception as exc:
        FreeCAD.Console.PrintWarning(f"[PackageStudio] Technology check skipped: {exc}\n")
        return folder
    if better is None:
        return folder
    name, other, known, chosen_known, total = better
    T = tc.load(folder)
    chosen = T.name
    built = len(set(lay.layers_in(gds_path)) & set(T.layers))
    box = QtWidgets.QMessageBox(_mw())
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setWindowTitle("Technology")
    box.setText(f"This layout looks like {name}: it knows {known} of the {total} layers drawn.\n"
                f"{chosen} knows {chosen_known}"
                + (", none of them built in 3-D — the chip would come in as a bare block."
                   if built == 0 else f", {built} of them built in 3-D."))
    use = box.addButton(f"Use {name.split('(')[0].strip()}", QtWidgets.QMessageBox.AcceptRole)
    keep = box.addButton(f"Keep {chosen.split('(')[0].strip()}", QtWidgets.QMessageBox.DestructiveRole)
    box.addButton(QtWidgets.QMessageBox.Cancel)
    box.setDefaultButton(use)
    box.exec()
    if box.clickedButton() is use:
        params().SetString("TechFolder", other)
        return other
    return folder if box.clickedButton() is keep else None


class _Command:
    menu, tip, icon_name = "", "", ""

    def GetResources(self):
        return {"MenuText": self.menu, "ToolTip": self.tip, "Pixmap": icon(self.icon_name)}

    def IsActive(self):
        return True


class Technology(_Command):
    menu, icon_name = "Technology", "technology"
    tip = "Choose the PDK technology chips are imported with (IHP SG13G2, SKY130, or your own)."

    def Activated(self):
        choose_technology()


class ImportGds(_Command):
    menu, icon_name = "Import GDS chips", "import_gds"
    tip = ("Import one or several chip layouts (GDSII) with the chosen technology — select several files at "
           "once. Each is checked against the technology and comes in beside the others.")

    def Activated(self):
        folder = active_technology() or choose_technology()
        if not folder:
            return
        start = params().GetString("LastGdsDir", os.path.expanduser("~"))
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(_mw(), "Import GDS chips", start,
                                                          "GDSII (*.gds *.GDS *.gds2 *.gds.gz)")
        if not paths:
            return
        params().SetString("LastGdsDir", os.path.dirname(paths[0]))
        doc = _doc()
        progress = QtWidgets.QProgressDialog("Importing…", None, 0, 0, _mw())
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)

        made, failed = [], []
        for k, path in enumerate(paths, 1):
            try:
                path = model.plain_gds(path)
            except OSError as exc:
                failed.append((path, exc))
                continue
            chosen = confirm_technology(path, folder)       # each layout may be another PDK's
            if not chosen:
                continue
            progress.show()
            prefix = f"{k}/{len(paths)} {os.path.basename(path)}: " if len(paths) > 1 else ""

            def say(text, prefix=prefix):
                progress.setLabelText(prefix + text)
                QtWidgets.QApplication.processEvents()

            try:
                doc.openTransaction("Import GDS chip")
                chip = model.import_chip(doc, path, chosen, say)
                doc.commitTransaction()
                made.append(chip)
            except Exception as exc:
                doc.abortTransaction()
                failed.append((path, exc))
                FreeCAD.Console.PrintError(f"[PackageStudio] Import GDS {path}: {exc}\n{traceback.format_exc()}\n")
            progress.hide()
        progress.close()
        if made:
            FreeCADGui.SendMsgToActiveView("ViewFit")
            from . import browser
            browser.show()
        for chip in made:
            FreeCAD.Console.PrintMessage(
                f"[PackageStudio] {chip.Label}: {len([o for o in chip.Group if model.role(o) == model.LAYER])} "
                f"layers, {len(model.pads_of(chip))} pads ({chip.Technology}).\n")
        if failed:
            QtWidgets.QMessageBox.warning(_mw(), "Import GDS", "Not imported:\n\n" + "\n".join(
                f"{os.path.basename(p)}: {str(e)[:300]}" for p, e in failed))


class ImportStep(_Command):
    menu, icon_name = "Import STEP part", "import_step"
    tip = "Import a board (e.g. a KiCad board exported as STEP), a lid, or a housing as it is."

    def Activated(self):
        kinds = {"Board (KiCad STEP)": model.BOARD, "Lid": model.LID, "Housing (as it is)": model.HOUSING}
        kind, ok = QtWidgets.QInputDialog.getItem(_mw(), "Import STEP", "This part is the", list(kinds), 0, False)
        if not ok:
            return
        start = params().GetString("LastStepDir", os.path.expanduser("~"))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(_mw(), f"Import {kind}", start, "STEP (*.step *.stp *.STEP *.STP)")
        if not path:
            return
        params().SetString("LastStepDir", os.path.dirname(path))
        doc = _doc()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            doc.openTransaction(f"Import {kind}")
            model.import_part(doc, path, kinds[kind])
            doc.commitTransaction()
        except Exception as exc:
            doc.abortTransaction()
            return _fail("Import STEP", exc)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        FreeCADGui.SendMsgToActiveView("ViewFit")
        from . import browser
        browser.refresh()


class Housing(_Command):
    menu, icon_name = "Housing library", "housing"
    tip = ("Choose a housing — MirrorSemi open-cavity packages, JEDEC outlines, or your own STEP files. "
           "Its bond fingers become pads; the chip can go straight onto its die paddle.")

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from . import housing_dialog
        housing_dialog.run(FreeCAD.ActiveDocument)


class ElementForge(_Command):
    menu, icon_name = "Element Forge", "element_forge"
    tip = ("New housings from a sentence or a datasheet (local model, Ollama) or by hand — every number "
           "editable and checked as you type, with a live preview. Select a housing first to change it; its "
           "pads keep their pin numbers and wires stay on their pins.")

    def IsActive(self):
        return FreeCADGui.Control.activeDialog() is False

    def Activated(self):
        from . import element_panel
        try:
            FreeCADGui.Control.showDialog(element_panel.ElementPanel())
        except Exception as exc:
            _fail("Element Forge", exc)


class _PanelCommand(_Command):
    panel = None

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and FreeCADGui.Control.activeDialog() is False

    def Activated(self):
        from . import panels
        FreeCADGui.Control.showDialog(getattr(panels, self.panel)())


class Place(_PanelCommand):
    menu, icon_name, panel = "Place", "place", "PlacePanel"
    tip = ("Put a chip into a housing or onto a board, a lid onto a housing, or a package onto a board "
           "footprint — centred. What is placed then moves with what it stands on.")


class Move(_PanelCommand):
    menu, icon_name, panel = "Move", "move", "MovePanel"
    tip = ("Move placed chips, lids, packages or boards — one or several at once: typed offsets, arrow steps, "
           "or drag them in the view. The offsets stay editable; bond wires follow.")


class Pads(_PanelCommand):
    menu, icon_name, panel = "Pads", "pads", "PadsPanel"
    tip = ("Chip pads from the layout; package pads from the bond fingers (one click finds them all); contact "
           "points on any surface.")


class Ports(_PanelCommand):
    menu, icon_name, panel = "Ports", "ports", "PortsPanel"
    tip = ("Simulation ports: rectangles in a face's plane, or standing on an edge of a chip layer, board or "
           "housing up to the next edge.")


class BondWires(_PanelCommand):
    menu, icon_name, panel = "Bond wires", "bond_wires", "WiresPanel"
    tip = "Pair pads symmetrically and build exact bond wires."


class Materials(_PanelCommand):
    menu, icon_name, panel = "Materials", "materials", "MaterialsPanel"
    tip = "What every element is made of — thermal and electrical properties, carried into the exports."


class Layers(_Command):
    menu, icon_name = "Layers", "layers"
    tip = "Show or hide every layer and part."

    def Activated(self):
        from . import browser
        browser.show()


class Export(_Command):
    menu, icon_name = "Export geometry", "export"
    tip = "Export elements as STEP, OBJ or MSH with every detail of the layout, for thermal or mechanical work."

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from . import export_dialog
        export_dialog.run(FreeCAD.ActiveDocument)


_docks = {}


def toggle_dock(key, factory):
    """Show the dock *key* (made once by *factory*), or hide it if shown."""
    mw = _mw()
    dock = _docks.get(key)
    if dock is None:
        dock = factory(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        _docks[key] = dock
    if dock.isVisible():
        dock.hide()
    else:
        dock.show()
        dock.raise_()
    return dock


class EMSimulation(_Command):
    menu, icon_name = "EM simulation", "em_simulation"
    tip = ("Make a ready-to-run openEMS, EMerge or Palace package of the assembly with OTTER: readiness check, "
           "exact chip metal, materials from the Materials panel, ports, OTTER's checks.")

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from .em_panel import EMSimulationPanel
        toggle_dock("em", EMSimulationPanel)


class EMAssistant(_Command):
    menu, icon_name = "EM assistant", "em_assistant"
    tip = ("Ask about the EM setup and have it changed in plain words — a local model through Ollama (nothing "
           "leaves the computer), or Claude / Gemini with your own key. It changes settings, never solver code.")

    def Activated(self):
        from .assistant_panel import EMAssistantPanel
        toggle_dock("assistant", EMAssistantPanel)


COMMANDS = [
    ("PS_Technology", Technology), ("PS_ImportGds", ImportGds), ("PS_Housing", Housing),
    ("PS_ElementForge", ElementForge),
    ("PS_ImportStep", ImportStep), ("PS_Place", Place), ("PS_Move", Move), ("PS_Pads", Pads),
    ("PS_Ports", Ports), ("PS_BondWires", BondWires), ("PS_Materials", Materials),
    ("PS_EMSimulation", EMSimulation), ("PS_EMAssistant", EMAssistant),
]
#: in the menu, not on the toolbar
MENU_ONLY = [("PS_Layers", Layers), ("PS_Export", Export)]


def register():
    """Register every command; returns (toolbar names, menu-only names)."""
    for name, cls in COMMANDS + MENU_ONLY:
        FreeCADGui.addCommand(name, cls())
    return [name for name, _cls in COMMANDS], [name for name, _cls in MENU_ONLY]


def register_flow():
    """The design-flow toolbar's commands (gui.flow_cmds); returns their names."""
    from . import flow_cmds
    for name, cls in flow_cmds.COMMANDS:
        FreeCADGui.addCommand(name, cls())
    return [name for name, _cls in flow_cmds.COMMANDS]


def register_system():
    """The MCM & system toolbar's commands (gui.system_cmds); returns their names."""
    from . import system_cmds
    for name, cls in system_cmds.COMMANDS:
        FreeCADGui.addCommand(name, cls())
    return [name for name, _cls in system_cmds.COMMANDS]
