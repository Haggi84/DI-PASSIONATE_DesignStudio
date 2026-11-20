# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Suggestions for findings, headless: what a click on a finding shows and
what its fix does.

A microstrip on a board: a copper ground 1 x 1 x 0.035 mm (z 0 .. 0.035)
and a copper trace 1 x 0.2 mm across it at z 0.5 .. 0.535 (y 0.4 .. 0.6).
A port stands upright in the plane x 0.5, y 0.45 .. 0.55, from z 0.1 to
0.4 — between the two, touching neither.

Expected, by hand:
  * OTTER reports both of the port's ends as touching no conductor;
  * in the port's footprint the ground ends below its centre (z 0.25) at
    0.035 and the trace starts above it at 0.5: the fix runs the port from
    0.035 to 0.5, 0.465 mm high, centred at 0.2675;
  * applied, the port in the assembly is exactly that, and OTTER no longer
    reports it;
  * a second port at y 1.45 .. 1.55 is off the board: nothing in its
    footprint, no fix to apply, the ground named as the nearest conductor
    below.
"""

import FreeCAD
import Part

from _harness import TestCase, new_document

from pkgstudio.app import em_model, fixes, materials, model, ports, readiness
from pkgstudio.app.em_project import EmProject

V = FreeCAD.Vector


def _board(doc):
    board = doc.addObject("App::Part", "Board")
    model.set_role(board, model.BOARD)
    for name, shape in (("Ground", Part.makeBox(1, 1, 0.035)),
                        ("Trace", Part.makeBox(1, 0.2, 0.035, V(0, 0.4, 0.5)))):
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = shape
        board.addObject(obj)
        materials.set_material(obj, "Copper (Cu)")
    doc.recompute()
    return board


def run():
    tc = TestCase("fixes")
    doc = new_document("Fixes")
    board = _board(doc)
    port = ports.add_edge_port(board, (0.5, 0.45, 0.1), (0.5, 0.55, 0.1), 0.3)
    doc.recompute()

    project = EmProject(em_model.build(doc, fmax_ghz=30.0), "openEMS")
    floating = [i for i in project.checks() if i.code == "port-not-touching-metal"]
    tc.check("OTTER: both ends of the floating port touch nothing", len(floating) == 2,
             [i.message for i in floating])
    s = fixes.for_issue(project, floating[0])
    span = fixes.port_span(project, "Port_1")
    tc.check("the fix runs from the ground below (z 0.035) to the trace above (z 0.5)",
             span.complete and abs(span.lo - 0.035) < 1e-9 and abs(span.hi - 0.5) < 1e-9
             and span.below == "Ground" and span.above == "Trace", span)
    styles = {st for _n, _s, st in s.shapes}
    proposed = next(sh for _n, sh, st in s.shapes if st == "proposed")
    tc.check("the preview shows the port (problem), ground and trace (conductors) and the fix",
             styles == {"problem", "conductor", "proposed"}
             and sorted(n for n, _s, st in s.shapes if st == "conductor") == ["Ground", "Trace"], s.shapes)
    tc.near("the proposed port is 0.465 mm high", proposed.BoundBox.ZLength, 0.465, 1e-9)
    tc.check("one action, which rebuilds the model", len(s.actions) == 1 and s.actions[0].rebuild,
             [a.text for a in s.actions])
    tc.check("it opens Ports to fix it by hand too, and selects the port", s.command == "PS_Ports"
             and s.select == [port.Name])

    s.actions[0].apply()
    g = model.global_placement(port)
    bb = port.Shape.copy()
    bb.Placement = g
    tc.near("applied: the port's height is 0.465 mm", port.PortHeight.Value, 0.465, 1e-12)
    tc.near("applied: it starts on the ground's top (z 0.035)", bb.BoundBox.ZMin, 0.035, 1e-9)
    tc.near("applied: it ends on the trace's underside (z 0.5)", bb.BoundBox.ZMax, 0.5, 1e-9)
    tc.near("applied: centred at z 0.2675, still at x 0.5, y 0.5", (g.Base - V(0.5, 0.5, 0.2675)).Length, 0, 1e-9)
    FreeCAD.closeDocument(project.sim.doc.Name)
    project = EmProject(em_model.build(doc, fmax_ghz=30.0), "openEMS")
    left = [i.message for i in project.checks() if i.code == "port-not-touching-metal"]
    tc.check("built again, OTTER no longer reports the port", not left, left)
    tc.check("and it touches both conductors", sorted(em_model.port_contacts(project.sim)["Port_1"])
             == ["Ground", "Trace"], em_model.port_contacts(project.sim))

    issue = type(floating[0])("warning", "mesh-too-large", "The mesh has 135 M cells.")
    s = fixes.for_issue(project, issue)
    tc.check("too many cells: the part that sets the extent is shown, coarse mesh offered",
             any(st == "problem" for _n, _s, st in s.shapes) and any("coarse" in a.text for a in s.actions))
    next(a for a in s.actions if "coarse" in a.text).apply()
    tc.check("applied: mesh density coarse", project.design.mesh == "coarse", project.design.mesh)
    FreeCAD.closeDocument(project.sim.doc.Name)

    off = ports.add_edge_port(board, (0.5, 1.45, 0.1), (0.5, 1.55, 0.1), 0.3)
    doc.recompute()
    project = EmProject(em_model.build(doc, fmax_ghz=30.0), "openEMS")
    span = fixes.port_span(project, "Port_2")
    s = fixes.for_port(project, "Port_2")
    tc.check("a port off the board: nothing in its footprint, the ground named as nearest below, no action",
             span.lo is None and span.hi is None and span.nearest_below == "Ground" and not s.actions
             and "Ground" in s.text, (span, s.text))
    found = readiness.check_model(project.sim)
    f2 = next(f for f in found if "Port_2" in f.message)
    tc.check("readiness: the finding names the port in the assembly and in the model",
             f2.objects == [off.Name] and f2.model_objects == ["Port_2"], (f2.objects, f2.model_objects))
    tc.check("clicked, it gives the same port suggestion", fixes.for_finding(doc, f2, project).title.startswith(
        "Port_2"))
    FreeCAD.closeDocument(project.sim.doc.Name)

    bare = doc.addObject("Part::Feature", "Bare")
    bare.Shape = Part.makeBox(1, 1, 1, V(5, 0, 0))
    doc.recompute()
    f = next(f for f in readiness.check(doc) if "no material" in f.message)
    s = fixes.for_finding(doc, f)
    tc.check("a part without a material: selected, shown, and Materials offered",
             s.select == [bare.Name] and s.command == "PS_Materials" and len(s.shapes) == 1, (s.select, s.command))
    FreeCAD.closeDocument(doc.Name)
    return tc.results
