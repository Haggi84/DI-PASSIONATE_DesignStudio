# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
GUI smoke test — runs inside the full FreeCAD (start it with
tests/run_gui_smoke.py). Drives every command and panel on the synthetic
parts of tests/test_flow.py, with the file and choice dialogs answered here
and faces selected in the 3-D view the way a click would select them.
"""

import json
import os
import sys
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


def subname_of(root, leaf, face):
    """The selection path from *root* to *face* (a Face of *leaf*'s shape)."""
    path = []
    o = leaf
    while o is not None and o is not root:
        path.insert(0, o.Name)
        o = o.getParentGeoFeatureGroup()
    for i, f in enumerate(leaf.Shape.Faces, 1):
        if f.isSame(face) or (abs(f.Area - face.Area) < 1e-12
                              and (f.CenterOfMass - face.CenterOfMass).Length < 1e-9):
            return ".".join(path) + f".Face{i}"
    return None


def select_edges(doc, container, pick):
    """Select every edge of *container*'s leaves for which pick(edge_global) is true."""
    from pkgstudio.app import model
    FreeCADGui.Selection.clearSelection()
    root = container
    while root.getParentGeoFeatureGroup() is not None:
        root = root.getParentGeoFeatureGroup()
    n = 0
    for leaf in [o for o in doc.Objects if o.isDerivedFrom("Part::Feature") and model.role(o) != model.PAD]:
        p, inside = leaf, False
        while p is not None:
            inside = inside or p is container
            p = p.getParentGeoFeatureGroup()
        if not inside:
            continue
        gpl = model.global_placement(leaf)
        for i, e in enumerate(leaf.Shape.Edges, 1):
            g = e.copy()
            g.Placement = gpl.multiply(leaf.Placement.inverse()).multiply(g.Placement)
            if pick(g):
                path, o = [], leaf
                while o is not None and o is not root:
                    path.insert(0, o.Name)
                    o = o.getParentGeoFeatureGroup()
                FreeCADGui.Selection.addSelection(doc.Name, root.Name, ".".join(path) + f".Edge{i}")
                n += 1
                return n
    return n


def select_faces(doc, container, pick):
    """Select every face of *container*'s leaves for which pick(face_global) is true."""
    import Part
    from pkgstudio.app import model
    FreeCADGui.Selection.clearSelection()
    root = container
    while root.getParentGeoFeatureGroup() is not None:
        root = root.getParentGeoFeatureGroup()
    n = 0
    for leaf in [o for o in doc.Objects if o.isDerivedFrom("Part::Feature") and model.role(o) != model.PAD]:
        p = leaf
        inside = False
        while p is not None:
            if p is container:
                inside = True
            p = p.getParentGeoFeatureGroup()
        if not inside:
            continue
        gpl = model.global_placement(leaf)
        for i, f in enumerate(leaf.Shape.Faces, 1):
            g = f.copy()
            g.Placement = gpl.multiply(leaf.Placement.inverse()).multiply(g.Placement)
            if pick(g):
                path, o = [], leaf
                while o is not None and o is not root:
                    path.insert(0, o.Name)
                    o = o.getParentGeoFeatureGroup()
                FreeCADGui.Selection.addSelection(doc.Name, root.Name, ".".join(path) + f".Face{i}")
                n += 1
    return n


def library_flow(tf, model, wires, panels, export_dialog):
    """A housing from the library dialog, the chip on its paddle with nothing
    selected, materials, the MIL-STD-883 check, an export with its manifest."""
    from PySide import QtCore, QtWidgets
    from pkgstudio.gui import housing_dialog
    from pkgstudio.app import materials
    doc = FreeCAD.newDocument("SmokeLibrary")
    model.import_chip(doc, tf.build_chip(tf.tmpdir("gui_lib")), tf.IHP)
    dlg = housing_dialog.HousingDialog(doc)
    dlg.tabs.setCurrentIndex(1)
    item = dlg.jd_list.findItems("QFN-16 4x4", QtCore.Qt.MatchExactly, 0)[0]
    dlg.jd_list.setCurrentItem(item)
    dlg.place.setChecked(False)
    h = dlg.build()
    check("Housing library: QFN-16 4x4 arrives with 16 lead pads",
          len(model.pads_of(h)) == 16, len(model.pads_of(h)))

    place = panels.PlacePanel()
    place.mode.setCurrentIndex(0)              # chip onto the die paddle, nothing selected
    place.place()
    chip = model.by_role(doc, model.CHIP)[0]
    check("Place: chip onto the die paddle with nothing selected, on 25 um die attach",
          abs(model.global_placement(chip).Base.z - (0.225 + tf.BODY)) < 1e-9, place.status.text())

    mp = panels.MaterialsPanel()
    unset = [e.name for e in mp.items if not materials.material_of(e.obj)]
    check("Materials: every element arrives with a material", not unset, unset)
    body = next(o for o in h.Group if getattr(o, "HousingPart", "") == "body")
    mp.tree.clearSelection()
    for i in range(mp.tree.topLevelItemCount()):
        top = mp.tree.topLevelItem(i)
        for k in range(top.childCount()):
            if mp.items[top.child(k).data(0, QtCore.Qt.UserRole)].obj is body:
                top.child(k).setSelected(True)
    mp.choice.setCurrentText("Liquid crystal polymer (LCP)")
    mp.assign()
    check("Materials: the body re-assigned to LCP from the panel",
          materials.material_of(body) == "Liquid crystal polymer (LCP)")

    wp = panels.WiresPanel()
    check("Bond wires: the panel starts with Al 25 um wedge-wedge",
          wp.material.currentText() == "Al" and wp.bond.currentText() == "wedge-wedge"
          and abs(wp.diameter.currentData() - 0.025) < 1e-12)
    wp.auto()
    wp.build()
    rows = [wp.table.item(r, 5).text() for r in range(wp.table.rowCount())]
    check("Bond wires: 12 built and checked against MIL-STD-883 class B — every row passes",
          len(wires.wires(doc)) == 12 and all(t.startswith("✓") for t in rows), (rows, wp.status.text()))
    wp.cls.setCurrentIndex(1)
    wp.check()
    check("Bond wires: the class S check runs too", "Class S" in wp.status.text(), wp.status.text())

    out = tf.tmpdir("gui_lib_export")
    dlg = export_dialog.ExportDialog(doc)
    dlg.folder.setText(out)
    dlg.name.setText("lib")
    dlg.exec = lambda: 1
    orig = export_dialog.ExportDialog
    export_dialog.ExportDialog = lambda *_a, **_k: dlg
    files = export_dialog.run(doc)
    export_dialog.ExportDialog = orig
    check("Export: STEP with its material manifest",
          sorted(os.path.basename(f) for f in files) == ["lib.step", "lib_materials.csv", "lib_materials.json"],
          files)

    from pkgstudio.gui import browser
    FreeCAD.setActiveDocument(doc.Name)
    FreeCADGui.setActiveDocument(doc.Name)
    texts = [item.text(0) for item, _objs in browser.show().widget()._rows.values()]
    check("Layers: the 14 stack levels this chip does not draw (17 less Metal1, Via1, TopMetal2), and the oxide",
          "Unused PDK levels (14)" in texts and "Dielectrics (2)" in texts,
          [t for t in texts if "(" in t])
    em_flow(doc, h, model, out)


def lead_port(doc, h, model, pt):
    """A lumped port where a QFN is fed: in the leadframe's top plane, across
    the gap between an unwired lead's inner end and the die paddle's edge,
    exciting along that gap — touching both conductors, overlapping neither."""
    from pkgstudio.app import wires as wr
    from pkgstudio.core import contacts as ct, housing as hs
    body = next(o for o in h.Group if getattr(o, "HousingPart", "") == "body")
    lf = next(o for o in h.Group if getattr(o, "HousingPart", "") == "leadframe")
    g = model.global_placement(lf)
    sol = [s.transformGeometry(g.toMatrix()) for s in lf.Shape.Solids]
    a = hs.analyse([body.Shape] + lf.Shape.Solids)
    paddle = sol[a.paddle - 1]
    pb = paddle.BoundBox
    wired = {w.PackagePad.Name for w in wr.wires(doc) if w.PackagePad is not None}
    for pad in sorted(model.pads_of(h), key=lambda p: p.PadName):
        if pad.Name in wired:
            continue
        c = model.pad_centre(pad)
        lead = min((s for s in sol if s is not paddle), key=lambda s: s.distToShape(
            __import__("Part").Vertex(c))[0])
        lb = lead.BoundBox
        if abs(lb.Center.x - pb.Center.x) < abs(lb.Center.y - pb.Center.y):
            continue                     # a lead on the +-y sides: keep to the x sides here
        out = 1 if lb.Center.x > pb.Center.x else -1
        xa, xb = (pb.XMax, lb.XMin) if out > 0 else (lb.XMax, pb.XMin)
        ya, yb = max(lb.YMin, pb.YMin), min(lb.YMax, pb.YMax)
        if xb - xa <= 1e-6 or yb - ya <= 1e-6:
            continue
        frame = ct.FaceFrame(origin=((xa + xb) / 2, (ya + yb) / 2, lb.ZMax), n=(0, 0, 1), u=(1, 0, 0),
                             v=(0, 1, 0))
        made = pt.add_port(h, frame, (0.0, 0.0), xb - xa, yb - ya, direction="+X")
        doc.recompute()
        return made
    return None


def fix_flow(panel, doc, h, model, pt):
    """A port floating above a lead: click OTTER's finding, see the fix in
    the 3-D view, apply it. Above the lead is nothing conductive, so only its
    bottom end can be fixed: it is put down onto the lead's top (z 0.2)."""
    from pkgstudio.app import wires as wr
    from pkgstudio.gui import fix_preview
    wired = {w.PackagePad.Name for w in wr.wires(doc) if w.PackagePad is not None}
    lead = next(p for p in sorted(model.pads_of(h), key=lambda p: p.PadName) if p.Name not in wired)
    c = model.pad_centre(lead)
    port = pt.add_edge_port(h, (c.x - 0.05, c.y, 0.25), (c.x + 0.05, c.y, 0.25), 0.1)
    doc.recompute()
    panel.build_model()
    rows = [k for k, i in enumerate(panel._issues) if i.code == "port-not-touching-metal"]
    check("Fix: OTTER reports the floating port", rows, [i.code for i in panel._issues])
    if not rows:
        return
    panel._check_clicked(panel.checks.item(rows[0]))
    box = panel.check_fix
    group = doc.getObject(fix_preview.GROUP)
    styles = sorted(o.Label.rsplit(" — ", 1)[-1] for o in (group.Group if group else []))
    check("Fix: a click shows what to do, and draws problem, conductor and fix in the 3-D view",
          not box.isHidden() and "Port_1" in box.title.text() and set(styles) == {"conductor", "problem", "proposed"},
          (box.title.text(), styles))
    check("Fix: the fix that can be worked out is offered as a button",
          len(box.suggestion.actions) == 1 and "fixed" in box.suggestion.actions[0].text,
          [a.text for a in box.suggestion.actions])
    panel.apply_fix(box.suggestion.actions[0])
    s = port.Shape.copy()
    s.Placement = model.global_placement(port)
    check("Fix: applied, the port stands on the lead's top (z 0.2), the preview is gone, the model rebuilt",
          abs(s.BoundBox.ZMin - 0.2) < 1e-9 and doc.getObject(fix_preview.GROUP) is None
          and not any(i.code == "port-not-touching-metal" and "bottom" in i.message for i in panel._issues),
          (s.BoundBox.ZMin, [i.message[:60] for i in panel._issues]))
    doc.removeObject(port.Name)
    doc.recompute()


def em_flow(doc, h, model, out):
    """The EM panel on the QFN assembly: readiness, chip metal, a port on a
    lead, the whole package written by OTTER in one go; the assistant panel."""
    from PySide import QtWidgets
    from pkgstudio.app import ports as pt
    from pkgstudio.gui import em_panel, em_state, assistant_panel
    FreeCAD.setActiveDocument(doc.Name)
    FreeCADGui.setActiveDocument(doc.Name)
    mw = FreeCADGui.getMainWindow()
    panel = em_panel.EMSimulationPanel(mw)
    texts = [panel.ready.item(i).text() for i in range(panel.ready.count())]
    check("EM: without a port, the readiness list says so and names the command",
          any("No port" in t and "Ports" in t for t in texts), texts)
    check("EM: one row of chip metal, the default region and levels counted",
          panel.chips.rowCount() == 1 and panel.chips.item(0, 3).text().replace(",", "").isdigit(),
          panel.chips.item(0, 3).text() if panel.chips.rowCount() else None)
    fix_flow(panel, doc, h, model, pt)
    lead_port(doc, h, model, pt)
    panel.out_dir.setText(os.path.join(out, "em"))
    QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    ok = panel.make_all()
    project = em_state.project_for(doc)
    zips = [os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(out, "em")) for f in fs if f.endswith(".zip")]
    check("EM: 'Make simulation package' checks, builds and writes the openEMS package with OTTER",
          ok and project is not None and zips, (panel.result.text()[:600], zips))
    readmes = [os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(out, "em")) for f in fs if f == "README.txt"]
    text = open(readmes[0], encoding="utf-8").read() if readmes else ""
    check("EM: the package README has no sub-micron cell and no port overlapping a conductor",
          readmes and "smallest cell is" not in text and "overlaps conductor" not in text, text[-1500:])
    if project is not None:
        metal = [p for p in project.sim.parts if p.kind == "metal"]
        check("EM: the chip's metal is in the model, built from the layout (Metal1, Via1, TopMetal2)",
              {p.level for p in metal} == {"Metal1", "Via1", "TopMetal2"}, [(p.label, p.level) for p in metal])
        check("EM: the layer table lists the stack with materials", panel.layers.rowCount() > 3,
              panel.layers.rowCount())
        check("EM: the port is in OTTER's settings", panel.ports.rowCount() == 1, panel.ports.rowCount())
        FreeCAD.closeDocument(project.sim.doc.Name)
    FreeCAD.setActiveDocument(doc.Name)
    FreeCADGui.setActiveDocument(doc.Name)
    panel.close()

    chat = assistant_panel.EMAssistantPanel(mw)
    chat.provider.setCurrentIndex(chat.provider.findData("ollama"))
    check("EM assistant: Ollama is offered, and the panel says whether it answers",
          chat.provider.currentData() == "ollama" and chat.question.isEnabled()
          and ("nothing leaves it" in chat.notice.text() or "does not answer" in chat.notice.text()),
          chat.notice.text())
    chat.close()


def run():
    try:
        from PySide import QtWidgets
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        sys.path.insert(0, ROOT)
        # What FreeCAD does at start-up with a Mod folder's InitGui.py.
        exec(open(os.path.join(ROOT, "InitGui.py"), encoding="utf-8").read(), {"__name__": "InitGui"})
        FreeCADGui.activateWorkbench("PackageStudioWorkbench")
        QtWidgets.QApplication.processEvents()
        from pkgstudio.gui import commands, panels, browser, export_dialog
        from pkgstudio.app import model, moving, wires
        import test_flow as tf

        check("workbench activates", FreeCADGui.activeWorkbench().name() == "PackageStudioWorkbench",
              FreeCADGui.activeWorkbench().name())
        registered = set(FreeCADGui.listCommands())
        names = [n for n, _c in commands.COMMANDS]
        menu_only = [n for n, _c in commands.MENU_ONLY]
        from pkgstudio.gui import system_cmds
        system_names = [n for n, _c in system_cmds.COMMANDS]
        check("all 13 toolbar commands, the 6 MCM & System commands and the 2 menu commands registered",
              len(names) == 13 and len(system_names) == 6 and len(menu_only) == 2
              and all(n in registered for n in names + system_names + menu_only))
        bars = [tb for tb in FreeCADGui.getMainWindow().findChildren(QtWidgets.QToolBar)
                if tb.windowTitle() == "Package Studio"]
        buttons = [a.text() for a in bars[0].actions() if not a.isSeparator()] if bars else []
        check("one toolbar of 13, in the order the work goes",
              len(buttons) == 13 and buttons[0] == "Technology" and buttons[3] == "Element Forge"
              and buttons[6] == "Move" and buttons[8] == "Ports" and buttons[-2:] == ["EM simulation", "EM assistant"],
              buttons)
        bars2 = [tb for tb in FreeCADGui.getMainWindow().findChildren(QtWidgets.QToolBar)
                 if tb.windowTitle() == "Package Studio — MCM & System"]
        buttons2 = [a.text() for a in bars2[0].actions() if not a.isSeparator()] if bars2 else []
        from pkgstudio.gui import flow_cmds
        flow_names = [n for n, _c in flow_cmds.COMMANDS]
        bars3 = [tb for tb in FreeCADGui.getMainWindow().findChildren(QtWidgets.QToolBar)
                 if tb.windowTitle() == "Package Studio — Design flow"]
        buttons3 = [a.text() for a in bars3[0].actions() if not a.isSeparator()] if bars3 else []
        check("the Design flow toolbar, from the pin list to the measurement",
              buttons3 == ["Pin list & LVS", "Bonding diagram", "KiCad board", "KiCad library", "Circuit model",
                           "Compare with measurement"] and all(n in registered for n in flow_names), buttons3)
        check("the MCM & System toolbar, from dies to the system",
              buttons2 == ["Arrange dies (MCM)", "Wire the MCM", "PC/104 board", "CubeSat 1U", "Into the system",
                           "Integration check"], buttons2)

        folder = tf.tmpdir("gui")
        gds = tf.build_chip(folder)
        steps = tf.build_steps(folder)
        commands.params().SetString("TechFolder", tf.IHP)

        answers = []
        QtWidgets.QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (answers.pop(0), ""))
        QtWidgets.QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: (answers.pop(0), ""))
        roles = []
        QtWidgets.QInputDialog.getItem = staticmethod(lambda *a, **k: (roles.pop(0), True))

        multi = FreeCAD.newDocument("SmokeMulti")
        answers.append([gds, gds])
        FreeCADGui.runCommand("PS_ImportGds")
        two = model.by_role(multi, model.CHIP)
        from pkgstudio.app import moving
        check("Import GDS: two files chosen at once come in as two chips, side by side",
              len(two) == 2 and abs(model.global_placement(two[1]).Base.x - 2.5) < 1e-9
              and not moving.overlap_report(multi), [model.global_placement(c).Base for c in two])
        FreeCAD.closeDocument(multi.Name)

        doc = FreeCAD.newDocument("Smoke")
        answers.append([gds])
        FreeCADGui.runCommand("PS_ImportGds")
        chips = model.by_role(doc, model.CHIP)
        check("Import GDS: one chip with its layers and 12 pads",
              len(chips) == 1 and len(model.pads_of(chips[0])) == 12, [o.Label for o in doc.Objects][:10])
        for role_text, path in (("Housing (as it is)", steps["housing"]), ("Lid", steps["lid"]),
                                ("Board (KiCad STEP)", steps["board"])):
            roles.append(role_text)
            answers.append(path)
            FreeCADGui.runCommand("PS_ImportStep")
        housing = model.by_role(doc, model.HOUSING)
        lid = model.by_role(doc, model.LID)
        board = model.by_role(doc, model.BOARD)
        check("Import STEP: housing, lid and board", len(housing) == len(lid) == len(board) == 1)
        housing, lid, board = housing[0], lid[0], board[0]

        # Place the chip on the cavity floor, as a user would: select the face, press Place.
        place = panels.PlacePanel()
        n = select_faces(doc, housing, lambda f: abs(f.CenterOfMass.z - 0.5) < 1e-6 and f.Area > 10)
        check("selected the cavity floor in the 3-D view", n == 1, n)
        place.mode.setCurrentIndex(1)          # chip onto a selected face
        place.place()
        chip = chips[0]
        check("Place: chip on the cavity floor centre",
              (model.global_placement(chip).Base - FreeCAD.Vector(4, 4, 0.5 + tf.BODY)).Length < 1e-9,
              place.status.text())
        check("Place: the chip is now part of the housing's package", model.package_of(chip) is not None
              and model.package_of(housing) is not None
              and model.package_of(chip).Name == model.package_of(housing).Name, place.status.text())

        pads = panels.PadsPanel()
        n = select_faces(doc, housing, lambda f: abs(f.CenterOfMass.z - 0.55) < 1e-6
                         and abs(f.Area - 0.045) < 1e-9 and f.CenterOfMass.x > 5)
        check("selected bond-finger faces", n >= 1, n)
        FreeCADGui.Selection.clearSelection()
        select_faces(doc, housing, lambda f: abs(f.CenterOfMass.z - 0.55) < 1e-6 and abs(f.Area - 0.045) < 1e-9
                     and abs(f.CenterOfMass.x - 5.6) < 1e-6 and abs(f.CenterOfMass.y - 4) < 1e-6)
        pads.add(alike=True)
        check("Pads: one finger selected, 'Add all alike' finds 20", len(model.all_pads(doc, "package")) == 20,
              pads.status.text())

        wp = panels.WiresPanel()
        wp.auto()
        wp.spins["loop_h"].setValue(120)
        wp.build()
        ws = wires.wires(doc)
        check("Bond wires: 12 symmetric wires built with the panel's loop height",
              len(ws) == 12 and all(abs(w.LoopHeight.Value - 0.12) < 1e-12 for w in ws), wp.status.text())
        check("Bond wires: the table shows every wire symmetric", "12 have mirror partners" in wp.status.text(),
              wp.status.text())

        place.mode.setCurrentIndex(2)          # lid
        select_faces(doc, housing, lambda f: abs(f.CenterOfMass.z - 1.0) < 1e-6 and f.Area > 40)
        place.place()
        lb = model.container_shape(lid).BoundBox
        check("Place: lid on the housing top", abs(lb.ZMin - 1.0) < 1e-9, lb)

        place.mode.setCurrentIndex(3)          # package onto the board
        n = select_faces(doc, board, lambda f: abs(f.CenterOfMass.z - 1.635) < 1e-6 and abs(f.Area - 0.5) < 1e-9)
        check("selected the 4 footprint pads", n == 4, n)
        place.place()
        hb = model.container_shape(housing).BoundBox
        check("Place: package centred on the footprint (12, 12) on the board",
              abs((hb.XMin + hb.XMax) / 2 - 12) < 1e-9 and abs(hb.ZMin - 1.635) < 1e-9, hb)
        check("Place: the package is now part of the board",
              model.package(doc).getParentGeoFeatureGroup() is not None
              and model.package(doc).getParentGeoFeatureGroup().Name == board.Name)

        # Move: the chip selected in the view, stepped, typed, put back.
        mp = panels.MovePanel()
        select_faces(doc, chip, lambda f: abs(f.CenterOfMass.z - (1.635 + 0.5 + tf.BODY)) < 1e-6
                     and abs(f.Area - 4.0) < 1e-9)
        mp.take_selection()
        check("Move: a face of the chip selected in the view selects the chip's row",
              [o.Name for o in mp.chosen()] == [chip.Name], mp.status.text())
        before = model.global_placement(chip).Base
        mp.step_box.setValue(0.05)
        mp.step_move(1, 0, 0)
        after = model.global_placement(chip).Base
        ws = wires.wires(doc)
        check("Move: one step right moves the chip 0.05 mm in X, its wires rebuilt on the pads",
              abs(after.x - before.x - 0.05) < 1e-9 and abs(after.y - before.y) < 1e-9
              and all(w.Shape.isValid() and w.Shape.Solids for w in ws), (before, after, mp.status.text()))
        row = next(r for r in range(mp.table.rowCount()) if mp.table.item(r, 0).data(__import__("PySide").QtCore.Qt.UserRole) == chip.Name)
        check("Move: the table shows the offset", mp.table.item(row, 1).text() == "0.0500", mp.table.item(row, 1).text())
        mp.table.item(row, 2).setText("-0.1")
        check("Move: typing Y -0.1 in the table moves it there",
              abs(model.global_placement(chip).Base.y - before.y + 0.1) < 1e-9, model.global_placement(chip).Base)
        mp.back()
        check("Move: back to where it was placed", (model.global_placement(chip).Base - before).Length < 1e-9)
        # move to a midpoint: two faces of the chip's top picked, a target 0.3 mm off in x, XY only
        select_faces(doc, chip, lambda f: abs(f.CenterOfMass.z - (1.635 + 0.5 + tf.BODY)) < 1e-6
                     and abs(f.Area - 4.0) < 1e-9)
        pts = __import__("pkgstudio.gui.selection", fromlist=["x"]).points("centre")
        mp.take_target()
        tgt = mp.target()
        check("Move to a midpoint: one face selected, the target is its centre",
              len(pts) == 1 and (tgt - pts[0]).Length < 1e-4, (pts, tgt, mp.status.text()))
        mp.tx.setValue(tgt.x + 0.3)
        mp.tz.setValue(tgt.z + 1.0)
        mp.axes.setCurrentIndex(mp.axes.findData("XY"))
        mp.ref_kind.setCurrentIndex(mp.ref_kind.findData("centre"))
        ref0 = moving.reference_point([chip])
        mp.move_to_target()
        ref1 = moving.reference_point([chip])
        check("Move to a midpoint, XY: the chip's centre goes 0.3 mm in x, z stays (the 1 mm in Z ignored)",
              abs(ref1.x - ref0.x - 0.3) < 1e-6 and abs(ref1.z - ref0.z) < 1e-9 and "Moved by" in mp.status.text(),
              (ref0, ref1, mp.status.text()))
        mp.back()
        mp.reject()

        # Contact points on the board's top face: the panel's steps around the
        # clicks (a click itself needs a person at the mouse).
        cp = panels.PadsPanel()
        FreeCADGui.Selection.clearSelection()
        select_faces(doc, board, lambda f: abs(f.CenterOfMass.z - 1.6) < 1e-6 and f.Area > 100)
        cp.use_face()
        check("Contact points: the board's 30 x 30 mm top face is taken, measured from its corner",
              cp._face is not None and "30.0000 × 30.0000 mm" in cp.face_label.text(), cp.face_label.text())
        grid = cp._grid
        names = [o.Name for o in grid.group.Group] if grid and grid.group else []
        texts = [o for o in (grid.group.Group if grid and grid.group else []) if hasattr(o, "Text")]
        check("Contact points: a grid on the face, lines every 5 mm and 1 mm, with ruler labels",
              grid is not None and (grid.major, grid.minor) == (5.0, 1.0) and "GridMinor" in names
              and "GridMajor" in names and len(texts) >= 14, (names[:4], len(texts)))
        check("Contact points: clicks snap to the 1 mm fine lines", cp._snap_pitch() == 1.0, cp._snap_pitch())
        grid.move_cross(12.0, 7.0)
        check("Contact points: the crosshair follows the mouse", grid.cross is not None
              and abs(grid.cross.Shape.BoundBox.Center.x - 12.0) < 1e-6, grid.cross)
        check("Contact points: a new point is the size of the bond on it, 62.5 x 37.5 um",
              abs(cp.ct_w.value() - 62.5) < 1e-9 and abs(cp.ct_h.value() - 37.5) < 1e-9,
              (cp.ct_w.value(), cp.ct_h.value()))
        cp.ct_x.setValue(25.5)
        cp.ct_y.setValue(10)
        cp.add_typed_point()
        c1 = model.contact_points(board)[0]
        check("Contact points: X 25.5, Y 10 typed and added is exactly (25.5, 10, 1.6)",
              (model.pad_centre(c1) - FreeCAD.Vector(25.5, 10, 1.6)).Length < 1e-9
              and cp.ct_table.item(0, 1).text() == "25.5000", model.pad_centre(c1))
        cp.ct_table.item(0, 1).setText("20")
        check("Contact points: typing X 20 in the table moves it to (20, 10, 1.6)",
              (model.pad_centre(c1) - FreeCAD.Vector(20, 10, 1.6)).Length < 1e-9, model.pad_centre(c1))
        cp.ct_table.selectRow(0)
        cp.ct_axis.setCurrentIndex(0)          # over X, through the face's middle: Y -> 30 - Y
        cp.mirror_points()
        check("Contact points: the selected point mirrored over X lands at Y 20",
              cp.ct_table.rowCount() == 2 and cp.ct_table.item(1, 2).text() == "20.0000", cp.status.text())
        from pkgstudio.app import ports as pt
        pp = panels.PortsPanel()
        check("Ports: its own panel, no contact-point controls in it", not hasattr(pp, "ct_table")
              and not hasattr(cp, "pt_table"))
        FreeCADGui.Selection.clearSelection()
        select_faces(doc, board, lambda f: abs(f.CenterOfMass.z - 1.6) < 1e-6 and f.Area > 100)
        pp.use_face()
        pp.pt_w.setValue(2.0)
        pp.pt_h.setValue(1.0)
        pp.pt_x.setValue(15.0)
        pp.pt_y.setValue(15.0)
        pp.add_typed_port()
        made = pt.ports(doc)
        check("Ports: a 2 x 1 mm port typed at X 15, Y 15 on the board's top face",
              len(made) == 1 and (model.global_placement(made[0]).Base - FreeCAD.Vector(15, 15, 1.6)).Length < 1e-9
              and abs(made[0].Shape.Area - 2.0) < 1e-9 and pp.pt_table.rowCount() == 1, pp.status.text())

        # An edge on the board's top rim (y = 0) chooses the board's edge face, facing -y.
        n = select_edges(doc, board, lambda e: e.Curve.__class__.__name__ in ("Line", "LineSegment")
                         and abs(e.BoundBox.YMax) < 1e-9 and abs(e.BoundBox.ZMin - 1.6) < 1e-9
                         and abs(e.BoundBox.XLength - 30) < 1e-9)
        pp.use_face()
        nrm = pp._face.normalAt(*pp._face.ParameterRange[::2]) if pp._face is not None else None
        check("Ports: selecting the board's top-rim edge chooses its 30 x 1.6 mm edge face",
              n == 1 and nrm is not None and (nrm - FreeCAD.Vector(0, -1, 0)).Length < 1e-9
              and "30.0000 × 1.6000 mm" in pp.face_label.text(), (n, nrm, pp.face_label.text()))
        pp.pt_w.setValue(3.0)
        pp.pt_h.setValue(3.0)
        pp.pt_x.setValue(15.0)
        pp.pt_y.setValue(0.8)
        pp.add_typed_port()
        edge_port = pt.ports(doc)[-1]
        check("Ports: a 3 x 3 mm port at X 15, Y 0.8 stands across the board edge at (15, 0, 0.8)",
              (model.global_placement(edge_port).Base - FreeCAD.Vector(15, 0, 0.8)).Length < 1e-9
              and abs(edge_port.Shape.BoundBox.ZMax - 2.3) < 1e-9, model.global_placement(edge_port).Base)

        # A plain box — not imported through Package Studio — works too.
        import Part as _Part
        plain = doc.addObject("Part::Feature", "Plain")
        plain.Shape = _Part.makeBox(5, 5, 1, FreeCAD.Vector(40, 0, 0))
        doc.recompute()
        FreeCADGui.Selection.clearSelection()
        top = next(i for i, f in enumerate(plain.Shape.Faces, 1) if abs(f.CenterOfMass.z - 1) < 1e-9)
        FreeCADGui.Selection.addSelection(doc.Name, plain.Name, f"Face{top}")
        pp.use_face()
        pp.pt_x.setValue(2.5)
        pp.pt_y.setValue(2.5)
        pp.add_typed_port()
        last = pt.ports(doc)[-1]
        check("Ports: on a face of an object from any import, kept in a 'Surface items' group",
              pp._face is not None and model.parent_of(last).Label == "Surface items"
              and (model.global_placement(last).Base - FreeCAD.Vector(42.5, 2.5, 1)).Length < 1e-9,
              (pp.status.text(), model.global_placement(last).Base))
        pp.reject()
        cp.reject()
        check("Contact points: closing the panel removes the grid",
              not [o for o in doc.Objects if o.Label == "Contact grid (temporary)" or o.Name.startswith("Grid")])

        dock = browser.show()
        tree = dock.widget().tree
        rows = dock.widget()._rows
        layer_item = next(item for item, objs in rows.values()
                          if len(objs) == 1 and model.role(objs[0]) == model.LAYER and objs[0].GdsLayer == 8)
        layer_obj = rows[id(layer_item)][1][0]
        layer_item.setCheckState(0, __import__("PySide").QtCore.Qt.Unchecked)
        check("Layers: unticking Metal1 hides it", not layer_obj.ViewObject.Visibility)
        layer_item.setCheckState(0, __import__("PySide").QtCore.Qt.Checked)
        check("Layers: ticking it shows it again", layer_obj.ViewObject.Visibility)
        via = next(o for o in chip.Group if model.role(o) == model.LAYER and o.GdsLayer == 19)
        before = via.Mesh.CountFacets
        model.set_layer_display(via, "detail")
        check("Layers: the via layer switches from blocks to every via",
              via.Mesh.CountFacets > 100 * before, (before, via.Mesh.CountFacets))
        model.set_layer_display(via, "blocks")

        out = tf.tmpdir("gui_export")
        dlg = export_dialog.ExportDialog(doc)
        dlg.obj.setChecked(True)
        dlg.msh.setChecked(False)
        dlg.folder.setText(out)
        dlg.name.setText("smoke")
        dlg.exec = lambda: 1
        orig = export_dialog.ExportDialog
        export_dialog.ExportDialog = lambda *_a, **_k: dlg
        QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
        files = export_dialog.run(doc)
        export_dialog.ExportDialog = orig
        check("Export: STEP and OBJ of every element written, with the material manifest",
              sorted(os.path.basename(f) for f in files)
              == ["smoke.obj", "smoke.step", "smoke_materials.csv", "smoke_materials.json", "smoke_ports.json"],
              files)
        import Part
        step = Part.read(os.path.join(out, "smoke.step"))
        check("Export: the STEP holds the 3,000 vias, exactly (not the blocks shown)",
              sum(1 for s in step.Solids if abs(s.Volume - 0.0005 ** 2 * 0.00054) < 1e-15) == 3000,
              len(step.Solids))

        # A section from the top: x 0 .. 6 of the 30 x 30 x 1.6 board, nothing else.
        dlg = export_dialog.ExportDialog(doc)
        dlg.section_box.setChecked(True)
        dlg._set_section((0.0, 0.0, 6.0, 30.0))
        check("Export section: shown as a red frame while chosen",
              any(o.Name.startswith("ExportSection") for o in doc.Objects))
        dlg.obj.setChecked(False)
        dlg.folder.setText(out)
        dlg.name.setText("section")
        dlg.exec = lambda: 1
        export_dialog.ExportDialog = lambda *_a, **_k: dlg
        files = export_dialog.run(doc)
        export_dialog.ExportDialog = orig
        dlg.done(1)
        sec = Part.read(os.path.join(out, "section.step"))
        check("Export section: only the board's 6 x 30 x 1.6 mm strip, 288 mm^3; the port outside it is left out",
              abs(sum(s.Volume for s in sec.Solids) - 288.0) < 1e-6
              and not any(f.endswith("_ports.json") for f in files), (sum(s.Volume for s in sec.Solids), files))
        check("Export section: the frame is gone after the dialog",
              not any(o.Name.startswith("ExportSection") for o in doc.Objects))

        library_flow(tf, model, wires, panels, export_dialog)

        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
        QtWidgets.QApplication.processEvents()
        if SHOT:
            FreeCADGui.activeDocument().activeView().saveImage(SHOT, 1400, 900, "Current")
    except Exception as exc:
        check("smoke test ran to completion", False, f"{exc}\n{traceback.format_exc()}")
    finish()


FreeCADGui._pd_smoke = (run,)
from PySide import QtCore as _QtCore  # noqa: E402
_QtCore.QTimer.singleShot(2000, run)
