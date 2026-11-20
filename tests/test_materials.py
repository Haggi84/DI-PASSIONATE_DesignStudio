# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Materials: the library, the technology's own, and the user's.

From SG13G2's stackup by hand: Metal1 conducts 21.64 MS/m and names
ThermalMaterial="Aluminium", so it takes aluminium's 237 W/(m K); Via1 names
none, so it is a stated guess (tungsten, 173 W/(m K)); the substrate is
2 S/m silicon with eps 11.9.
"""

import os

from _harness import TestCase, TECH, tmpdir

from pkgstudio.core import materials as mat, tech


def run():
    tc = TestCase("materials")
    lib = mat.library()
    tc.check("library: wire metals, leadframe, moulding, laminates, ceramics, glass",
             all(n in lib for n in ("Gold (Au)", "Aluminium 1% Si wire (AlSi1)", "Copper alloy C194 (leadframe)",
                                    "Mould compound (EMC)", "FR-4", "Alumina 96 % (Al2O3)", "Borosilicate glass")))
    tc.check("library: every entry has every thermal and electrical value, and a source",
             all(not m.missing and m.source for m in lib.values()), [m.name for m in lib.values() if m.missing])
    tc.check("every wire metal is in the library", all(v in lib for v in mat.WIRE_MATERIALS.values()))

    T = tech.load(os.path.join(TECH, "IHP-SG13G2"))
    tm = mat.technology_materials(T, lib)
    m1 = tm.get("IHP SG13G2 / Metal1")
    tc.check("SG13G2 Metal1: the PDK's conductivity, aluminium's thermal values",
             m1 is not None and abs(m1.sigma - 21640000.0) < 1e-3 and m1.k == 237 and "ThermalMaterial" in m1.source,
             m1)
    v1 = tm.get("IHP SG13G2 / Via1")
    tc.check("SG13G2 Via1: tungsten, and the manifest says it is a guess",
             v1 is not None and v1.k == 173 and "guess" in v1.source, v1)
    sub = tm.get("IHP SG13G2 / Substrate")
    tc.check("SG13G2 substrate: 2 S/m, eps 11.9, silicon's 148 W/(m K)",
             sub is not None and sub.sigma == 2.0 and sub.eps_r == 11.9 and sub.k == 148, sub)
    spec = T.layers[(8, 0)]
    tc.check("a layer's material is its stackup material", mat.layer_material(T, spec) == "IHP SG13G2 / Metal1")

    path = os.path.join(tmpdir("mat"), "materials.json")
    mat.save_user_material(mat.Material("My glue", "polymer", (1, 0, 0), 1200, 0.2, 1500, 60, 0, 3.0, 1, 0.02,
                                        "my datasheet"), path)
    merged = mat.library(extra_files=[path])
    tc.check("a user material joins the library", "My glue" in merged and merged["My glue"].k == 0.2)
    return tc.results
