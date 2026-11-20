# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GUI test of the Element Forge, inside the full FreeCAD:

    FreeCAD tests/gui/element_macro.py      (PS_SMOKE_RESULT=<json>, PS_SMOKE_SHOT=<png>)

A fake Ollama on a free local port streams a first answer whose leads are
too wide for their pitch (0.45 mm at 0.5 mm), then the corrected one — so
Generate is driven end to end: the worker thread, the fields filling as the
answer streams, the repair round, the amber marks on assumed values, the
build check, Insert, a hand edit and Update housing.
"""

import http.server
import json
import os
import sys
import threading
import time
import traceback

import FreeCAD
import FreeCADGui

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULT = os.environ.get("PS_SMOKE_RESULT", "")
SHOT = os.environ.get("PS_SMOKE_SHOT", "")
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "passed": bool(ok), "detail": str(detail)})


def finish():
    if RESULT:
        with open(RESULT, "w", encoding="utf-8") as fh:
            json.dump({"checks": checks}, fh, indent=1)
    os._exit(0 if checks and all(c["passed"] for c in checks) else 1)


ANSWER = {"name": "QFN-24 4x4", "family": "QFN", "standard": "JEDEC MO-220", "body_x": 4.0, "body_y": 4.0,
          "height": 0.85, "pitch": 0.5, "lead_w": 0.45, "per_side_x": 6, "per_side_y": 6, "paddle_x": 2.6,
          "paddle_y": 2.6, "finger": 0.4, "lead_len": 0.4, "lead_t": 0.2, "foot": 0.6, "standoff": 0.1,
          "assumed": ["height", "lead_w", "lead_len", "lead_t", "finger"],
          "notes": "Height, lead width, L and leadframe thickness are MO-220 typical values."}
ANSWERS = [ANSWER, dict(ANSWER, lead_w=0.25)]
requests = []


class FakeOllama(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
        requests.append(payload)
        text = json.dumps(ANSWERS[min(len(requests) - 1, 1)])
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        for k in range(0, len(text), 9):
            self.wfile.write((json.dumps({"message": {"role": "assistant", "content": text[k:k + 9]},
                                         "done": False}) + "\n").encode())
            self.wfile.flush()
            time.sleep(0.004)
        self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}) + "\n").encode())


def wait_for(cond, seconds=60):
    from PySide import QtWidgets
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def run():
    try:
        from PySide import QtWidgets
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        os.environ["PKGSTUDIO_ELEMENTS"] = os.path.join(FreeCAD.getUserAppDataDir(), "elements_test")

        sys.path.insert(0, ROOT)
        exec(open(os.path.join(ROOT, "InitGui.py"), encoding="utf-8").read(), {"__name__": "InitGui"})
        FreeCADGui.activateWorkbench("PackageStudioWorkbench")
        from pkgstudio.gui import element_panel, em_preferences
        from pkgstudio.app import housings, model
        from pkgstudio.core import elements as el
        em_preferences.put("OllamaUrl", url)
        doc = FreeCAD.newDocument("Forge")

        FreeCADGui.runCommand("PS_ElementForge")
        panel = element_panel.last_panel
        check("Element Forge opens", panel is not None)
        check("models listed from Ollama", panel.model_box.currentText() == "qwen3:8b", panel.model_box.currentText())

        streamed = []
        panel.prompt.setPlainText("QFN-24 4x4 mm, 0.5 mm pitch, exposed pad 2.6 mm")
        orig = panel._on_field

        def spy(k, v):
            streamed.append(k)
            orig(k, v)
        panel._on_field = spy
        panel.gen_button.click()
        done = wait_for(lambda: panel.worker is None and len(requests) >= 1, 60)
        check("Generate finishes", done, panel.stream.text())
        spec = panel.spec()
        check("the form holds the corrected answer (b 0.45 -> 0.25 after one repair)",
              len(requests) == 2 and abs(spec["lead_w"] - 0.25) < 1e-9 and spec["per_side_x"] == 6,
              (len(requests), spec))
        check("fields filled while the answer streamed", len(streamed) >= 15, streamed)
        check("the request asked for the schema", requests[0].get("format") == el.json_schema())
        check("assumed values are marked", panel.assumed == {"height", "lead_w", "lead_len", "lead_t", "finger"}
              and panel.inputs["height"][1].text().startswith("≈"), panel.assumed)
        check("status says it corrected itself", "corrected itself 1" in panel.stream.text(), panel.stream.text())
        issues = panel.refresh()
        check("the generated element builds clean", not el.errors(issues), issues)
        check("the preview is in the document", doc.getObject(element_panel.PREVIEW) is not None)
        if SHOT:
            wait_for(lambda: False, 0.5)
            FreeCADGui.getMainWindow().grab().save(SHOT.replace(".png", "_generated.png"))

        panel.insert_button.click()
        hs = model.by_role(doc, model.HOUSING)
        check("Insert makes a housing with 24 pads that keeps its numbers",
              len(hs) == 1 and len(model.pads_of(hs[0])) == 24 and housings.spec_of(hs[0])[0]["pitch"] == 0.5,
              [h.Label for h in hs])
        check("the preview is gone after Insert", doc.getObject(element_panel.PREVIEW) is None)

        # a hand edit: one more lead per side
        panel.inputs["per_side_x"][0].setValue(7)
        panel.inputs["per_side_y"][0].setValue(7)
        panel.inputs["body_x"][0].setValue(5.0)
        panel.inputs["body_y"][0].setValue(5.0)
        panel.inputs["name"][0].setText("QFN-28 5x5")
        panel.refresh()
        FreeCADGui.Selection.clearSelection()
        FreeCADGui.Selection.addSelection(hs[0])
        panel.refresh()
        check("Update is offered for the selected housing", panel.update_button.isEnabled())
        panel.update_button.click()
        check("Update rebuilds it in place: 24 pads kept, 4 added",
              "24 pads kept, 4 added" in panel.status.text() and len(model.pads_of(hs[0])) == 28,
              panel.status.text())
        check("a hand-edited value is no longer marked assumed", "lead_w" in panel.assumed and
              "per_side_x" not in panel.assumed)

        panel.save_button.click()
        check("Save to library writes the element", any(e.name == "QFN-28 5x5" for e in el.load_all()),
              panel.status.text())
        if SHOT:
            FreeCADGui.SendMsgToActiveView("ViewFit")
            FreeCADGui.activeDocument().activeView().viewIsometric()
            wait_for(lambda: False, 0.5)
            FreeCADGui.getMainWindow().grab().save(SHOT)
        FreeCADGui.Control.closeDialog()
    except Exception as exc:
        check("element forge test ran to completion", False, f"{exc}\n{traceback.format_exc()}")
    finish()


from PySide import QtCore as _QtCore  # noqa: E402
_QtCore.QTimer.singleShot(1500, run)
