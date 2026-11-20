# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GUI test of the Design flow toolbar, inside the full FreeCAD:

    FreeCAD tests/gui/flow_macro.py      (PS_SMOKE_RESULT=<json>)

The LNA in a QFN-40 with a pin list (its RF pins on fixed GSG leads): the
Pin list & LVS panel lays the wires by net and checks them; the bonding
diagram is written as SVG, CSV and — through Qt — PDF; the KiCad library is
exported. File dialogs are bypassed: the functions behind them are called.
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

PINS = """die,pad,net,kind,lead
ALL_LNA,GND14,GND,ground,5
ALL_LNA,RFIN,RF_IN,rf,6
ALL_LNA,GND15,GND,ground,7
ALL_LNA,GND4,GND,ground,24
ALL_LNA,RFOUT,RF_OUT,rf,25
ALL_LNA,GND3,GND,ground,26
ALL_LNA,VCC,VCC,power,
ALL_LNA,IDC,BIAS,signal,
ALL_LNA,VTMP,TEMP,signal,
"""


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
        from pkgstudio.app import bonding, housings, kicad, model
        from pkgstudio.core import jedec, pinlist
        from pkgstudio.gui import flow_cmds
        doc = FreeCAD.newDocument("FlowGui")
        lna = model.import_chip(doc, os.path.join(ROOT, "examples", "data", "ALL_LNA.gds"),
                                os.path.join(ROOT, "technologies", "IHP-SG13G2"))
        h = housings.from_jedec(doc, jedec.by_name("QFN-40 6x6"))
        housings.place_chip(lna, h)
        folder = os.path.join(FreeCAD.getUserAppDataDir(), "flow_test")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "lna.csv")
        with open(path, "w") as fh:
            fh.write(PINS)
        flow_cmds.state["intent"] = pinlist.read_csv(path, path)
        flow_cmds.state["files"] = [path]
        FreeCADGui.Selection.addSelection(h)
        FreeCADGui.runCommand("PS_PinList")
        QtWidgets.QApplication.processEvents()
        panel = next((w for w in QtWidgets.QApplication.allWidgets()
                      if isinstance(w, QtWidgets.QTreeWidget) and w.headerItem().text(0) == "Rule"), None)
        check("the Pin list & LVS panel opens", panel is not None)
        lay = next(w for w in QtWidgets.QApplication.allWidgets()
                   if isinstance(w, QtWidgets.QPushButton) and w.text() == "Lay wires by net")
        lay.click()
        QtWidgets.QApplication.processEvents()
        status = [w.text() for w in QtWidgets.QApplication.allWidgets() if isinstance(w, QtWidgets.QLabel)
                  and ("matches the pin list" in w.text() or "errors" in w.text())]
        check("Lay wires by net: matches the pin list, every wire within MIL-STD-883", any("✔" in t for t in status),
              status)
        leads = {p.PadName: getattr(p, "LeadNet", "") for p in model.pads_of(h)}
        check("the fixed RF leads carry their nets", (leads["5"], leads["6"], leads["7"], leads["25"])
              == ("GND", "RF_IN", "GND", "RF_OUT"), leads)
        FreeCADGui.Control.closeDialog()
        svg, csv = bonding.export(h, folder, "lna", flow_cmds.state["intent"])
        pdf = flow_cmds._svg_to_pdf(svg)
        check("bonding diagram: SVG, CSV and a PDF through Qt",
              all(os.path.getsize(p) > 1000 for p in (svg, csv, pdf)) and open(pdf, "rb").read(4) == b"%PDF",
              (svg, pdf, csv))
        kinds = {p.net: p.kind for p in flow_cmds.state["intent"].pins}
        mod, sym, step = kicad.export_package(h, folder, kinds=kinds)
        check("KiCad library: footprint, symbol with the nets, STEP",
              "RF_IN" in open(sym).read() and os.path.getsize(step) > 10000 and "(pad \"6\"" in open(mod).read())
    except Exception as exc:
        check("flow GUI test ran to completion", False, f"{exc}\n{traceback.format_exc()}")
    finish()


from PySide import QtCore as _QtCore  # noqa: E402
_QtCore.QTimer.singleShot(1500, run)
