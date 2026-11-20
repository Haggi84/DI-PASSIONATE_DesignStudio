# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GUI test of the MCM & System toolbar, inside the full FreeCAD:

    FreeCAD tests/gui/system_macro.py      (PS_SMOKE_RESULT=<json>)

The commands without dialogs are run as a click runs them (PS_CubeSat,
PS_Pc104Board); the ones that ask go through the functions their dialogs
call; the Integration check panel is opened and read.
"""

import json
import os
import sys
import traceback

import FreeCAD
import FreeCADGui

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULT = os.environ.get("PS_SMOKE_RESULT", "")
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "passed": bool(ok), "detail": str(detail)})


def finish():
    if RESULT:
        with open(RESULT, "w", encoding="utf-8") as fh:
            json.dump({"checks": checks}, fh, indent=1)
    os._exit(0 if checks and all(c["passed"] for c in checks) else 1)


def run():
    try:
        from PySide import QtWidgets
        sys.path.insert(0, ROOT)
        exec(open(os.path.join(ROOT, "InitGui.py"), encoding="utf-8").read(), {"__name__": "InitGui"})
        FreeCADGui.activateWorkbench("PackageStudioWorkbench")
        from pkgstudio.app import model, system
        from pkgstudio.gui import system_cmds
        names = [n for n, _c in system_cmds.COMMANDS]
        check("the MCM & System commands are registered", all(n in FreeCADGui.listCommands() for n in names),
              names)
        bars = [t.windowTitle() for t in FreeCADGui.getMainWindow().findChildren(QtWidgets.QToolBar)]
        check("the MCM & System toolbar is there", any("MCM & System" in b for b in bars), bars)
        doc = FreeCAD.newDocument("SystemGui")
        FreeCADGui.runCommand("PS_CubeSat")
        FreeCADGui.runCommand("PS_Pc104Board")
        QtWidgets.QApplication.processEvents()
        sats = model.by_role(doc, system.SYSTEM)
        bs = model.by_role(doc, model.BOARD)
        check("CubeSat 1U and PC/104 board made", len(sats) == 1 and len(bs) == 1)
        system.add_board(sats[0], bs[0], 0.0)
        check("Integration check is offered", FreeCADGui.Command.get("PS_IntegrationCheck").isActive())
        FreeCADGui.runCommand("PS_IntegrationCheck")
        QtWidgets.QApplication.processEvents()
        panel = next((w for w in QtWidgets.QApplication.allWidgets()
                      if isinstance(w, QtWidgets.QTreeWidget) and w.headerItem().text(0) == "Check"), None)
        items = [panel.topLevelItem(i).text(1) for i in range(panel.topLevelItemCount())] if panel else []
        check("the Integration check lists mass and centre of gravity",
              any(" g of 2000 g" in t for t in items) and any("geometric centre" in t for t in items), items)
        labels = [w.text() for w in QtWidgets.QApplication.allWidgets() if isinstance(w, QtWidgets.QLabel)]
        check("the summary says it passes", any("passes" in t for t in labels))
        FreeCADGui.Control.closeDialog()
    except Exception as exc:
        check("system GUI test ran to completion", False, f"{exc}\n{traceback.format_exc()}")
    finish()


from PySide import QtCore as _QtCore  # noqa: E402
_QtCore.QTimer.singleShot(1500, run)
