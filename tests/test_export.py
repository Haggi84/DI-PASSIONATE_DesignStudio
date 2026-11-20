# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
OBJ, STEP and MSH writers: names survive, geometry survives.

Every file is read back rather than trusted: OBJ by counting what each named
group holds, STEP by re-importing it, MSH by its physical names.
"""

import os

import FreeCAD
import Part
import numpy as np

from _harness import TestCase, tmpdir

from pkgstudio.core import export as ex, mesh, solids

V = FreeCAD.Vector


def run():
    tc = TestCase("export")
    out = tmpdir("export")
    rect = np.array([[0, 0], [2, 0], [2, 1], [0, 1]], float)
    v1, t1 = mesh.extrude([rect], 0, 0.5)
    v2, t2 = mesh.extrude([rect + 5], 0, 0.25)
    obj = ex.write_obj(os.path.join(out, "parts.obj"),
                       [("Chip/Metal 1", v1, t1, (1, 0, 0)), ("Lid", v2, t2, (0, 0, 1))])
    counts = ex.read_obj_counts(obj)
    tc.check("OBJ: one named group per element, names made file-safe",
             set(counts) == {"Chip_Metal_1", "Lid"}, counts)
    tc.check("OBJ: every vertex and triangle written",
             counts.get("Chip_Metal_1") == (len(v1), len(t1)) and counts.get("Lid") == (len(v2), len(t2)), counts)
    tc.check("OBJ: a material file with the colours", os.path.isfile(os.path.join(out, "parts.mtl")))

    meshed = []

    def lazily():
        for name, (v, t) in (("a", (v1, t1)), ("b", (v2, t2))):
            meshed.append(name)
            yield name, v, t, (0, 1, 0)
    ex.write_obj(os.path.join(out, "streamed.obj"), lazily())
    tc.check("OBJ: elements can arrive one at a time (a generator), each meshed when reached",
             meshed == ["a", "b"] and ex.read_obj_counts(os.path.join(out, "streamed.obj")) ==
             {"a": (len(v1), len(t1)), "b": (len(v2), len(t2))})
    try:
        ex.write_obj(os.path.join(out, "cancelled.obj"), [("a", v1, t1, (1, 0, 0))], tick=lambda: False)
        tc.check("OBJ: cancelling raises Cancelled", False)
    except ex.Cancelled:
        tc.check("OBJ: a cancelled export leaves no half-written .obj and no .mtl behind",
                 not os.path.exists(os.path.join(out, "cancelled.obj"))
                 and not os.path.exists(os.path.join(out, "cancelled.mtl")))
    back = None
    try:
        import Mesh
        back = Mesh.Mesh(obj)
    except Exception as exc:
        tc.check("OBJ: FreeCAD reads it back", False, exc)
    if back is not None:
        tc.near("OBJ: FreeCAD reads it back with the exact volume (1.0 + 0.5)", back.Volume, 1.5, 1e-9)

    layer = solids.layer_solid([rect, rect + [3, 0]], 0.0, 0.5)
    tc.near("solids: two prisms of 2 x 1 x 0.5", layer.Volume, 2.0, 1e-9)
    body = solids.die_body((-1, -1, 1, 1), [("EPI", "EPI", 0.004), ("Substrate", "Si", 0.2)])
    tc.check("die body: epi on top of the substrate, below z = 0",
             [n for n, _ in body] == ["EPI", "Substrate"] and abs(body[0][1].BoundBox.ZMax) < 1e-12
             and abs(body[1][1].BoundBox.ZMin + 0.204) < 1e-12, [(n, s.BoundBox) for n, s in body])

    step = ex.write_step(os.path.join(out, "parts.step"),
                         [("Metal1", layer), ("Substrate", body[1][1])])
    tc.check("STEP: file written", os.path.isfile(step) and os.path.getsize(step) > 0)
    shape = Part.read(step)
    tc.near("STEP: reads back with the exact total volume", shape.Volume,
            2.0 + 2 * 2 * 0.2, 1e-6)
    with open(step, encoding="utf-8", errors="ignore") as fh:
        text = fh.read()
    tc.check("STEP: the element names are product names", "'Metal1'" in text and "'Substrate'" in text)

    if not ex.gmsh_executable():
        tc.skip("MSH: Gmsh meshes the elements with their names", "gmsh not found")
        return tc.results
    msh = ex.write_msh(os.path.join(out, "parts.msh"),
                       [("Housing", Part.makeBox(1, 1, 1)),
                        ("Wire 1", Part.makeCylinder(0.1, 1, V(3, 0, 0)))], max_size=0.2)
    names = ex.msh_physical_names(msh)
    tc.check("MSH: each element is a named 3-D physical group",
             names == {"Housing": 3, "Wire_1": 3}, names)
    return tc.results
