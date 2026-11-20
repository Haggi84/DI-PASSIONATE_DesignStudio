"""Runs inside FreeCAD (GUI build on Qt's offscreen platform), never imported by the host side.

Drives the unmodified OTTER add-on dialog without a visible window: open the .FCStd, load
the .ini through the same "Load settings" handler the GUI uses, then press "Generate".
Message boxes are collected into the result instead of being shown.

The GUI build is needed, not freecadcmd: object colours (ViewObject), which the EMerge
generator reads, are only loaded when the GUI is up.

Runs through freecad_job.runJob, see there for the job and result files.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.environ["OTTER_LLM_HEADLESS_DIR"])
from freecad_job import runJob


def run(job, result):
    from PySide import QtCore, QtWidgets

    import FreeCAD

    result.update(solver=None, messages=[], files=[])
    app = QtWidgets.QApplication.instance()

    addonDir = job["addon_dir"]
    sys.path.insert(0, addonDir)

    doc = FreeCAD.openDocument(job["fcstd"])
    FreeCAD.setActiveDocument(doc.Name)

    for face in job.get("add_faces", []):
        addFace(doc, face["label"], face["points"])
    doc.recompute()
    if job.get("save_fcstd"):
        doc.saveCopy(job["save_fcstd"])  # unlike saveAs, keeps the file name the generators use

    patchMessageBoxes(result["messages"])

    spec = importlib.util.spec_from_file_location("OtterDialog", os.path.join(addonDir, "OtterDialog.py"))
    otterDialogModule = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(otterDialogModule)

    dialog = otterDialogModule.OtterDialog()
    app.processEvents()  # runs the dialog's QTimer.singleShot(0, updateUiBasedOnSolverType)

    # same path as the "Load settings" button, the file dialog just answers with our .ini
    iniFile = job["ini"]
    QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *args, **kwargs: (iniFile, "*.ini"))
    dialog.loadFromFileSettingsButtonClicked()

    if job.get("solver"):
        solverCombo = dialog.form.comboBox_solverType
        solverIndex = solverCombo.findText(job["solver"], QtCore.Qt.MatchFixedString)
        if solverIndex < 0:
            raise ValueError(f"unknown solver '{job['solver']}', the add-on offers: "
                             f"{[solverCombo.itemText(k) for k in range(solverCombo.count())]}")
        solverCombo.setCurrentIndex(solverIndex)  # signal runs updateUiBasedOnSolverType
        app.processEvents()

    if job.get("python_output", True):
        dialog.form.radioButton_pythonType.setChecked(True)
    dialog.radioButtonOutputScriptsTypeClicked()

    result["solver"] = dialog.getSolverType()

    outDir = job["out_dir"]
    dialog.simulationOutputDir = outDir
    dialog.generateOpenEMSScriptButtonClicked()

    for root, _dirs, files in os.walk(outDir):
        for name in files:
            result["files"].append(os.path.relpath(os.path.join(root, name), outDir))
    result["files"].sort()


def addFace(doc, label, points):
    """A flat face through `points` (port faces from otter_llm/ports.py)."""
    import FreeCAD
    import Part

    vectors = [FreeCAD.Vector(*point) for point in points]
    obj = doc.addObject("Part::Feature", label)
    obj.Label = label
    if obj.Label != label:
        raise ValueError(f"the model already has an object labelled '{label}'")
    obj.Shape = Part.Face(Part.makePolygon(vectors + [vectors[0]]))
    obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0)


def patchMessageBoxes(messages):
    """All add-on message boxes go through GuiHelpers, collect them instead of blocking on exec()."""
    from utilsOtter.GuiHelpers.GuiHelpers import GuiHelpers

    def displayMessage(self, msgText, forceModal=True):
        messages.append({"kind": "message" if forceModal else "status", "text": str(msgText)})

    def displayYesNoMessage(self, msgText):
        messages.append({"kind": "question", "text": str(msgText), "answer": "no"})
        return False

    GuiHelpers.displayMessage = displayMessage
    GuiHelpers.displayYesNoMessage = displayYesNoMessage


runJob(run)
