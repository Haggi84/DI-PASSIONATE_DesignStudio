# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Run the OTTER add-on's script generators on the simulation model, in this
FreeCAD, without showing its dialog.

The generators live inside the add-on's dialog: they read solver, materials,
grids and ports from its widgets, so the only way to get the scripts OTTER
tested is to fill the dialog the way its own "Load settings" button does and
press "Generate". OTTER_Local does exactly that in a second, headless
FreeCAD; here the document is already open, so it happens in-process with
the same steps (otter_llm/headless/run_otter.py):

  * the dialog is built but never shown, and the file chooser it opens for
    "Load settings" is answered with our .ini;
  * its message boxes are collected instead of blocking the GUI — they carry
    the export warnings that go into the package README;
  * the simulation document is made active for the run, since the add-on
    works on FreeCAD.ActiveDocument, and the previous one is made active
    again afterwards.

The dialog also changes the working directory and starts a document
observer; both are undone, so the add-on leaves nothing behind in the
session.
"""

import importlib.util
import os

import FreeCAD
import FreeCADGui

from ..core import otter_path
from ..app.em_project import GenerationResult


def _load_dialog_module():
    otter_path.ensure()
    path = os.path.join(otter_path.ADDON_DIR, "OtterDialog.py")
    spec = importlib.util.spec_from_file_location("OtterDialog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_messages(messages):
    """Patch the add-on's message boxes; returns a function that undoes it."""
    from utilsOtter.GuiHelpers.GuiHelpers import GuiHelpers

    original = (GuiHelpers.displayMessage, GuiHelpers.displayYesNoMessage)

    def display_message(self, text, forceModal=True):
        messages.append({"kind": "message" if forceModal else "status", "text": str(text)})

    def display_yes_no(self, text):
        messages.append({"kind": "question", "text": str(text), "answer": "no"})
        return False

    GuiHelpers.displayMessage = display_message
    GuiHelpers.displayYesNoMessage = display_yes_no

    def restore():
        GuiHelpers.displayMessage, GuiHelpers.displayYesNoMessage = original
    return restore


def generate(doc, ini_path, out_dir, solver, python_output=True):
    """Write *solver*'s files for *doc* into *out_dir*; returns a GenerationResult."""
    from PySide import QtCore, QtWidgets     # the add-on's own Qt binding

    from otter_llm.generate import GenerateResult

    if not FreeCAD.GuiUp:
        return GenerationResult(False, error="The OTTER add-on needs the FreeCAD GUI.")
    out_dir, ini_path = str(out_dir), str(ini_path)
    os.makedirs(out_dir, exist_ok=True)

    app = QtWidgets.QApplication.instance()
    previous_doc = FreeCAD.ActiveDocument
    previous_cwd = os.getcwd()
    previous_open = QtWidgets.QFileDialog.getOpenFileName
    messages, dialog, restore_messages = [], None, None
    try:
        FreeCAD.setActiveDocument(doc.Name)
        FreeCADGui.setActiveDocument(doc.Name)
        module = _load_dialog_module()
        restore_messages = _collect_messages(messages)

        dialog = module.OtterDialog()
        app.processEvents()      # runs the dialog's singleShot(0, updateUiBasedOnSolverType)

        QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (ini_path, "*.ini"))
        dialog.loadFromFileSettingsButtonClicked()

        combo = dialog.form.comboBox_solverType
        index = combo.findText(solver, QtCore.Qt.MatchFixedString)
        if index < 0:
            raise ValueError(f"the add-on has no solver '{solver}', it offers "
                             f"{[combo.itemText(k) for k in range(combo.count())]}")
        combo.setCurrentIndex(index)
        app.processEvents()
        if python_output:
            dialog.form.radioButton_pythonType.setChecked(True)
        dialog.radioButtonOutputScriptsTypeClicked()

        dialog.simulationOutputDir = out_dir
        dialog.generateOpenEMSScriptButtonClicked()

        files = sorted(os.path.relpath(os.path.join(root, name), out_dir).replace(os.sep, "/")
                       for root, _dirs, names in os.walk(out_dir) for name in names)
        warnings = GenerateResult(True, solver, out_dir, files, messages).warnings
        if not files:
            return GenerationResult(False, solver, files, warnings,
                                    error="The add-on wrote no files.\n" + "\n".join(warnings))
        return GenerationResult(True, dialog.getSolverType(), files, warnings)
    except Exception as exc:
        import traceback
        return GenerationResult(False, solver, error=f"{exc}\n{traceback.format_exc()}",
                                warnings=[m["text"] for m in messages])
    finally:
        QtWidgets.QFileDialog.getOpenFileName = previous_open
        if dialog is not None:
            try:
                dialog.observer.endObservation()
            except Exception:
                pass
            try:
                dialog.form.close()
                dialog.form.deleteLater()
            except Exception:
                pass
        if restore_messages is not None:
            restore_messages()
        os.chdir(previous_cwd)
        if previous_doc is not None and previous_doc.Name in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(previous_doc.Name)
            try:
                FreeCADGui.setActiveDocument(previous_doc.Name)
            except Exception:
                pass
