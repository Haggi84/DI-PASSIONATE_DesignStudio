# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM simulation, headless: an assembly to a simulation model, its
materials, ports, readiness, the OTTER project and the assistant.

The assembly: tests/test_flow.py's 2 x 2 mm SG13G2 chip on the die paddle
of a QFN-16 4x4 (paddle top z 0.2, 25 um die attach), wired.

Expected, by hand:
  * the chip origin is at z 0.225 + 0.18375 (die body), so TopMetal2's top
    is at 0.225 + 0.18375 + 0.0142303 globally;
  * the default levels: the layout draws Metal1, Via1 and TopMetal2; the two
    highest metals are Metal1 and TopMetal2, and everything from Metal1 up
    is taken: Metal1, Via1, TopMetal2;
  * a region of 0.1 x 0.1 mm round the pad at (-0.5, 0.6), TopMetal2 only,
    holds exactly that pad: 60 x 60 um of TopMetal2;
  * the Via1 array (60 x 50 vias of 0.5 um at 1 um pitch) merges into one
    block: grown by 0.375 um, united, shrunk back: 59.5 x 49.5 um, of which
    3,000 x 0.25 um² = 750 um² is via: fill 0.2547, binned to 0.25, so its
    conductivity is a quarter of Via1's;
  * the air box at 30 GHz: lambda/4 = 2.4983 mm, x 1.02 = 2.548, up to 0.1 mm:
    2.6 mm round everything.
"""

import json
import os

import FreeCAD

from _harness import TestCase, new_document, tmpdir

from pkgstudio.app import em_materials, em_model, housings, materials, model, ports, readiness, wires
from pkgstudio.core import jedec
import test_flow as tf

V = FreeCAD.Vector


def _assembly(name):
    doc = new_document(name)
    chip = model.import_chip(doc, tf.build_chip(tmpdir(name)), tf.IHP)
    h = housings.from_jedec(doc, jedec.by_name("QFN-16 4x4"))
    housings.place_chip(chip, h, attach_t=0.025)
    wires.create_all(doc, wires.auto_pair(doc))
    return doc, chip, h


def _close(*docs):
    for d in docs:
        try:
            FreeCAD.closeDocument(d.Name)
        except Exception:
            pass


def run():
    tc = TestCase("em")
    doc, chip, h = _assembly("EmAsm")
    T = model.chip_technology(chip)

    found = readiness.check(doc)
    tc.check("readiness: without a port, an error says so and names the command",
             any(f.level == "error" and "No port" in f.message and f.fix == "Ports" for f in found),
             [str(f) for f in found])
    tc.check("readiness: worst first", [f.level for f in found] == sorted(
        (f.level for f in found), key=lambda lv: {"error": 0, "warning": 1, "info": 2}[lv]))

    tc.check("default levels: Metal1, Via1, TopMetal2 (everything from the lower of the two top metals)",
             em_model.default_levels(chip) == ["Metal1", "Via1", "TopMetal2"], em_model.default_levels(chip))
    rect = em_model.default_rect(chip)
    pads = model.pads_of(chip)
    hw = max(p.PadWidth.Value for p in pads) / 2
    tc.near("default region: round the wired pads, 20 um beyond them (x0)", rect[0], -0.6 - hw - 0.02, 1e-9)
    tc.near("default region: (y1)", rect[3], 0.6 + hw + 0.02, 1e-9)

    # two ports on the first lead: one standing on it, one in the air
    lead = sorted(model.pads_of(h), key=lambda p: p.PadName)[0]
    c = model.pad_centre(lead)
    p1 = ports.add_edge_port(h, (c.x - 0.05, c.y, c.z), (c.x + 0.05, c.y, c.z), 0.1, z0=75.0)
    p2 = ports.add_edge_port(h, (10, 10, 5), (10.1, 10, 5), 0.1)
    doc.recompute()
    found = readiness.check(doc)
    tc.check("readiness: with ports, no port error", not any("port" in f.message.lower() and f.level == "error"
                                                             for f in found), [str(f) for f in found])

    one = em_model.ChipRegion(chip.Name, (-0.55, 0.55, -0.45, 0.65), ["TopMetal2"])
    vias = em_model.ChipRegion(chip.Name, (-0.31, -0.21, -0.23, -0.14), ["Via1"])
    sim = em_model.build(doc, fmax_ghz=30.0, regions={chip.Name: one})
    metal = [p for p in sim.parts if p.kind == "metal"]
    tc.check("chip metal: one part, TopMetal2", len(metal) == 1 and metal[0].level == "TopMetal2",
             [(p.label, p.level) for p in metal])
    spec = next(s for s in T.layers.values() if s.level == "TopMetal2")
    shape = sim.doc.getObject(metal[0].label).Shape
    tc.near("chip metal: exactly the one pad, 60 x 60 um x TopMetal2's thickness", shape.Volume,
            0.06 * 0.06 * (spec.z1 - spec.z0), 1e-12)
    tc.near("chip metal: where the chip is (TopMetal2 top, globally)", shape.BoundBox.ZMax,
            0.225 + tf.BODY + tf.TM2_TOP, 1e-9)
    layer = next(o for o in chip.Group if model.role(o) == model.LAYER and (o.GdsLayer, o.GdsDatatype) == spec.key)
    tc.check("chip metal: the material its layer has in the Materials panel",
             metal[0].material == materials.material_of(layer), (metal[0].material, materials.material_of(layer)))
    tc.near("chip metal: with the PDK's own conductivity",
            em_materials.MATERIALS[metal[0].material].sigma, T.materials[spec.material]["sigma"], 1e-6)

    by_source = {p.source: p for p in sim.parts if p.source}
    lf = next(o for o in h.Group if getattr(o, "HousingPart", "") == "leadframe")
    body = next(o for o in h.Group if getattr(o, "HousingPart", "") == "body")
    tc.check("leadframe: copper alloy, a conductor of 3.77e7 S/m",
             by_source[lf.Name].material == "Copper alloy C194 (leadframe)"
             and em_materials.MATERIALS["Copper alloy C194 (leadframe)"].conductor
             and abs(em_materials.MATERIALS["Copper alloy C194 (leadframe)"].sigma - 3.77e7) < 1)
    mould = em_materials.MATERIALS["Mould compound (EMC)"]
    tc.check("housing body: mould compound, a dielectric of eps_r 3.8, tan d 0.01",
             by_source[body.Name].material == "Mould compound (EMC)" and not mould.conductor
             and abs(mould.epsilon - 3.8) < 1e-12 and abs(mould.tand - 0.01) < 1e-12)
    ws = wires.wires(doc)
    tc.check("all 12 wires are in the model, as aluminium", sum(1 for w in ws if w.Name in by_source) == 12
             and all(by_source[w.Name].material == "Aluminium 1% Si wire (AlSi1)" for w in ws))
    tc.check("nothing that is not matter: no pad markers, no chip layer meshes",
             not any(model.role(doc.getObject(p.source)) in (model.PAD, model.LAYER) for p in sim.parts if p.source))
    labels = [p.label for p in sim.parts]
    tc.check("every label plain and unique", len(labels) == len(set(labels))
             and all(lbl.replace("_", "").isalnum() for lbl in labels), labels)

    tc.check("ports: two, numbered as drawn, with their impedance",
             [p.label for p in sim.ports] == ["Port_1", "Port_2"] and sim.ports[0].impedance == 75.0)
    contacts = em_model.port_contacts(sim)
    tc.check("the port standing on the lead touches the leadframe", any(
        sim.part(lbl).source == lf.Name for lbl in contacts["Port_1"]), contacts)
    tc.check("the port in the air touches nothing", contacts["Port_2"] == [], contacts)
    found = readiness.check_model(sim)
    tc.check("after the build: the port in the air is an error, the other is not",
             any("Port_2" in f.message and f.level == "error" for f in found)
             and not any("Port_1" in f.message and f.level == "error" for f in found), [str(f) for f in found])
    lf_top = sim.doc.getObject(by_source_label(sim, lf.Name)).Shape.BoundBox.ZMax
    tc.check("the port drawn on the lead sits exactly on the lead's top, not a rounding step off it",
             sim.doc.getObject("Port_1").Shape.BoundBox.ZMin == lf_top,
             (sim.doc.getObject("Port_1").Shape.BoundBox.ZMin, lf_top))

    air = sim.doc.getObject(em_model.AIRBOX_LABEL).Shape.BoundBox
    inner = em_model._bounds([o.Shape for o in sim.doc.Objects if o.Name != em_model.AIRBOX_LABEL])
    tc.near("air box: 2.6 mm round everything at 30 GHz (x)", inner.XMin - air.XMin, 2.6, 1e-9)
    tc.near("air box: (z, top)", air.ZMax - inner.ZMax, 2.6, 1e-9)

    from pkgstudio.app.em_project import EmProject
    project = EmProject(sim, "openEMS")
    tiny = [i.message for i in project.checks() if i.code == "tiny-cell"]
    tc.check("OTTER: no sub-micron cell from a port off its lead by a rounding step", not tiny, tiny)
    plist = project.settings.ports()
    tc.check("OTTER project: both ports as lumped ports, the first at 75 ohm",
             len(plist) == 2 and plist[0].resistance == "75 Ohm", [(p.name, p.resistance) for p in plist])
    tc.check("OTTER project: a port standing on an edge is excited up its height, along z",
             plist[0].direction == "z", [(p.name, p.direction) for p in plist])
    from pkgstudio.app.em_project import excitation_axis
    p1.Direction = "+X"
    tc.check("a port excited along its width (+X, the edge's direction, here global x) goes to OTTER as x",
             excitation_axis(p1) == "x", excitation_axis(p1))
    p1.Direction = "+Y"
    rows = project.material_table()
    mould_row = next(r for r in rows if any(lbl == body.Label for lbl in r[5]))
    tc.check("material table: the body's layer comes from the Materials panel",
             mould_row[3] == "Mould compound (EMC)" and "Material:" in mould_row[4], mould_row)
    project.set_material(mould_row[0], "FR-4")
    n = project.remember_materials([mould_row[0]])
    tc.check("a material chosen in the EM panel goes back to the Material property",
             n >= 1 and materials.material_of(body) == "FR-4", (n, materials.material_of(body)))
    _check_assistant(tc, project)
    _check_ollama(tc, project)
    _close(sim.doc)

    sim = em_model.build(doc, regions={chip.Name: vias})
    via = [p for p in sim.parts if p.kind == "metal"]
    base = materials.material_of(next(o for o in chip.Group if model.role(o) == model.LAYER
                                      and o.GdsLayer == 19))
    tc.check("vias: the array is one block, fill 0.25", len(via) == 1 and via[0].material.endswith("(via fill 0.25)"),
             [(p.label, p.material) for p in via])
    if via:
        tc.near("vias: its conductivity is a quarter of Via1's", em_materials.MATERIALS[via[0].material].sigma,
                0.25 * em_materials.MATERIALS[base].sigma, 1e-3)
        vspec = next(s for s in T.layers.values() if s.level == "Via1")
        tc.near("vias: the block is 59.5 x 49.5 um", sim.doc.getObject(via[0].label).Shape.Volume,
                0.0595 * 0.0495 * (vspec.z1 - vspec.z0), 1e-15)
    _close(sim.doc)

    big = em_model.whole_region(chip)
    try:
        em_model.build(doc, regions={chip.Name: big}, max_polygons=100)
        tc.check("a region too big to mesh is refused, naming the chip", False, "built")
    except ValueError as exc:
        tc.check("a region too big to mesh is refused, naming the chip", chip.Label in str(exc), str(exc))
    _close(doc)
    return tc.results


def by_source_label(sim, name):
    return next(p.label for p in sim.parts if p.source == name)


def _check_assistant(tc, project):
    from pkgstudio.app import assistant
    toolbox = assistant.make_toolbox(project)
    sent = []
    replies = [
        {"message": {"role": "assistant", "content": "",
                     "tool_calls": [{"function": {"name": "set_frequency",
                                                  "arguments": {"fmin_ghz": 2, "fmax_ghz": 20}}}]}},
        {"message": {"role": "assistant", "content": "Now 2 to 20 GHz."}},
    ]

    def transport(url, payload, headers):
        sent.append((url, payload, headers))
        return replies[len(sent) - 1]

    bot = assistant.Assistant("ollama", toolbox, model="qwen3:8b", transport=transport)
    events = bot.ask("Simulate from 2 to 20 GHz")
    tc.check("assistant (Ollama): a tool call changes the project",
             project.design.fmax_ghz == 20 and project.design.fmin_ghz == 2,
             (project.design.fmin_ghz, project.design.fmax_ghz, [(e.kind, e.text[:60]) for e in events]))
    tc.check("assistant: the final answer comes back as text", events[-1].kind == "text" and "20" in events[-1].text)
    url, payload, _headers = sent[0]
    tc.check("assistant: the request goes to the local Ollama, no key", url == assistant.OLLAMA_URL + "/api/chat"
             and payload["model"] == "qwen3:8b" and payload["options"].get("num_ctx") == 16384)
    turn = next(m["content"] for m in payload["messages"] if m["role"] == "user")
    tc.check("assistant: the question carries the project, material origins and readiness",
             "<project_state>" in turn and "<material_origins>" in turn and "<readiness>" in turn
             and "Port_2 touches no conductor" in turn, turn[-600:])

    def offline(url, payload, headers):
        raise assistant.TransportError(None, "connection refused")
    bot = assistant.Assistant("ollama", toolbox, transport=offline)
    events = bot.ask("hi")
    tc.check("assistant: an Ollama that does not answer says so, and the turn is not kept",
             events[0].kind == "error" and "Ollama" in events[0].text and len(bot.history) == 1)


# ── a local Ollama, as the assistant meets it on a workstation ─────────────

class _FakeOllama:
    """
    Answers like Ollama: /api/version, /api/tags, and /api/chat as an NDJSON
    stream. It refuses to call a tool unless the request asks for the 16k
    window, as a real model does once its instructions were cut off.
    """

    def __init__(self, models=("qwen3:8b",), gap_s=0.0, first_wait_s=0.0):
        import http.server
        import threading
        fake = self
        self.models, self.gap_s, self.first_wait_s = list(models), gap_s, first_wait_s
        self.requests = []
        self.closed = False

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json(200, {"version": "0.12.6"})
                if self.path == "/api/tags":
                    return self._json(200, {"models": [{"name": m} for m in fake.models]})
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                import time
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append(body)
                if body.get("model") not in fake.models:
                    return self._json(404, {"error": f"model \"{body.get('model')}\" not found, try pulling it first"})
                big = (body.get("options") or {}).get("num_ctx", 0) >= 16384
                asked_tool = any(m.get("role") == "tool" for m in body["messages"])
                if body.get("tools") and big and not asked_tool:
                    chunks = [{"message": {"role": "assistant", "thinking": "the user wants Palace"}},
                              {"message": {"role": "assistant", "content": "", "tool_calls": [
                                  {"function": {"name": "set_solver", "arguments": {"solver": "Palace"}}}]}}]
                elif body.get("tools") and not big:
                    chunks = [{"message": {"role": "assistant", "content": "I cannot see any instructions."}}]
                else:
                    chunks = [{"message": {"role": "assistant", "content": w}} for w in ("Palace ", "it ", "is.")] \
                        if body.get("tools") else [{"message": {"role": "assistant", "content": "OK"}}]
                chunks.append({"done": True})
                self.send_response(200)
                self.send_header("content-type", "application/x-ndjson")
                self.end_headers()
                time.sleep(fake.first_wait_s)
                for chunk in chunks:
                    # A client that gave up has closed the socket: writing to it
                    # raises SIGPIPE, which ends freecadcmd on Linux (exit 141).
                    if fake.closed:
                        return
                    self.wfile.write((json.dumps(chunk) + "\n").encode())
                    self.wfile.flush()
                    time.sleep(fake.gap_s)

        http.server.ThreadingHTTPServer.daemon_threads = True     # closing never waits for a stuck answer
        http.server.ThreadingHTTPServer.block_on_close = False
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.closed = True
        self.server.shutdown()
        self.server.server_close()


def _check_ollama(tc, project):
    from pkgstudio.app import assistant

    import signal
    sigpipe = getattr(signal, "SIGPIPE", None)
    old_sigpipe = signal.signal(sigpipe, signal.SIG_IGN) if sigpipe is not None else None
    dead_proxy = "http://127.0.0.1:9"          # nothing listens there
    saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")}
    for k in ("HTTP_PROXY", "http_proxy"):
        os.environ[k] = dead_proxy
    for k in ("NO_PROXY", "no_proxy"):
        os.environ.pop(k, None)
    project.set_solver("openEMS")
    fake = _FakeOllama(gap_s=0.05)
    try:
        tc.check("the model list comes past a system proxy that would swallow 127.0.0.1",
                 assistant.ollama_models(fake.url) == ["qwen3:8b"], str(assistant.ollama_models(fake.url)))
        progress = []
        bot = assistant.Assistant("ollama", assistant.make_toolbox(project), model="qwen3:8b",
                                  ollama_url=fake.url, progress=lambda kind, n: progress.append(kind))
        events = bot.ask("Use Palace please")
        first = fake.requests[0] if fake.requests else {}
        tc.check("every request asks for a 16384-token window (Ollama's default cuts the instructions off)",
                 first.get("options", {}).get("num_ctx") == 16384, str(first.get("options")))
        tc.check("...and streams", first.get("stream") is True)
        tc.check("the streamed tool call changes the project", project.settings.solver == "Palace",
                 project.settings.solver)
        tc.check("the answer after it is put together from the stream",
                 events[-1].kind == "text" and events[-1].text == "Palace it is.", str(events[-1]))
        tc.check("progress is reported while the model thinks and writes",
                 "thinking" in progress and "answer" in progress, str(progress))
        tc.check("the thinking is not sent back into the history",
                 all("thinking" not in m for m in bot.history), str([list(m) for m in bot.history]))

        slow = _FakeOllama(gap_s=0.6)
        try:
            try:
                reply = assistant.ollama_chat(f"{slow.url}/api/chat", {
                    "model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]}, idle_s=1)
            except assistant.TransportError as error:
                reply = {"error": str(error)}
            tc.check("a slow model is fine as long as it keeps writing: 2 chunks 0.6 s apart under a 1 s limit",
                     reply.get("content") == "OK", str(reply))
        finally:
            slow.close()
        stalled = _FakeOllama(first_wait_s=1.6)
        try:
            raised = None
            try:
                assistant.ollama_chat(f"{stalled.url}/api/chat", {
                    "model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]}, idle_s=0.8)
            except assistant.TransportError as error:
                raised = error
            tc.check("a model that says nothing for longer than the limit is given up",
                     raised is not None and "no answer" in str(raised), str(raised))
        finally:
            stalled.close()

        bot = assistant.Assistant("ollama", assistant.make_toolbox(project), model="llama9:70b", ollama_url=fake.url)
        events = bot.ask("hi")
        tc.check("a model that is not pulled says how to get it",
                 events[0].kind == "error" and "ollama pull llama9:70b" in events[0].text, events[0].text)

        ok, text = assistant.check_ollama(fake.url, "qwen3:8b")
        tc.check("the Test button's check passes when Ollama and the model are there",
                 ok and "OK" in text and "0.12.6" in text, text)
        ok, text = assistant.check_ollama(fake.url, "qwen3:4b")
        tc.check("...and names the pull command when the model is missing",
                 not ok and "ollama pull qwen3:4b" in text, text)
        ok, text = assistant.check_ollama("http://127.0.0.1:9", "qwen3:8b")
        tc.check("...and says when Ollama is not running", not ok and "does not answer" in text, text)
        tc.check("'qwen3' means 'qwen3:latest'", assistant._same_model("qwen3", ["qwen3:latest"])
                 and not assistant._same_model("qwen3", ["qwen3:8b"]))
    finally:
        fake.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if sigpipe is not None:
            signal.signal(sigpipe, old_sigpipe)
