# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The stack: every height, in both stackup formats, and what a layout leaves out.

Expected values are the IHP SG13G2 and SkyWater SKY130 stackups, read off by
hand (um, z = 0 at the top of the silicon):

  SG13G2   Metal1 1.04-1.46, Via1 1.46-2.00, MIM 5.6043-5.7540,
           TopMetal1 6.4303-8.4303, TopMetal2 11.2303-14.2303;
           SiO2 0-15.7303, Passive 15.7303-16.1303, EPI -3.75-0.
  SKY130   met5 5.3711-6.6311, SiO2 0-6.6311, EPI -2-0.

tests/data holds two current IHP files from gds2palace (GPL-3.0-or-later):
SG13G2_FEM_200um.xml (schema 3.1: Reference/ReferenceEdge and <Variables>,
total height 200 um so the substrate is 180.12 um) and the schema 3.0
reference example. They must give the same heights as the bundled schema 2.0
file, which uses absolute Zmin/Zmax and a Substrate Offset instead.
"""

import os

import gdstk

from _harness import TestCase, TECH, new_document, tmpdir

from pkgstudio.core import tech

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
IHP = os.path.join(TECH, "IHP-SG13G2")
SKY = os.path.join(TECH, "SKY130")
UM = 1e-3

SG13G2 = {"Metal1": (1.04, 1.46), "Via1": (1.46, 2.0), "MIM": (5.6043, 5.754),
          "TopVia1": (5.58, 6.4303), "TopMetal1": (6.4303, 8.4303), "TopVia2": (8.4303, 11.2303),
          "TopMetal2": (11.2303, 14.2303)}

ALL_LNA = [r"C:\Program Files\FreeCAD 1.1\Mod\DI-PASSIONATE-FreeCAD\samples\ALL_LNA.gds",
           os.path.expanduser(r"~\Desktop\freeCAD_parts\DI-PASSIONATE\ALL_LNA.gds")]


def _levels(path):
    levels, substrate, _m, diel = tech.read_stackup(path)
    return {lv["name"]: (lv["z0"], lv["z1"]) for lv in levels}, substrate, \
        {n: (z0, z1) for n, _mat, z0, z1 in diel}


def _check_sg13g2(tc, label, levels, diel):
    bad = {n: levels.get(n) for n, (a, b) in SG13G2.items()
           if n not in levels or abs(levels[n][0] - a * UM) > 1e-12 or abs(levels[n][1] - b * UM) > 1e-12}
    tc.check(f"{label}: every metal and via level at its height", not bad, bad)
    tc.check(f"{label}: SiO2 0 - 15.7303 um, passivation 15.7303 - 16.1303 um",
             abs(diel.get("SiO2", (9, 9))[0]) < 1e-12 and abs(diel["SiO2"][1] - 15.7303 * UM) < 1e-12
             and abs(diel.get("Passive", (9, 9))[0] - 15.7303 * UM) < 1e-12
             and abs(diel["Passive"][1] - 16.1303 * UM) < 1e-12, diel)
    tc.check(f"{label}: EPI directly under the surface, -3.75 - 0 um",
             abs(diel.get("EPI", (9, 9))[0] + 3.75 * UM) < 1e-12 and abs(diel["EPI"][1]) < 1e-12, diel)
    tc.check(f"{label}: air is not part of the die", "AIR" not in diel, sorted(diel))


def _write_gds(path, keys):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    top = lib.new_cell("TOP")
    for i, (layer, dt) in enumerate(keys):
        top.add(gdstk.rectangle((i * 10, 0), (i * 10 + 5, 5), layer=layer, datatype=dt))
    lib.write_gds(path)
    return path


def run():
    tc = TestCase("stackup")

    # ── expressions ──
    tc.near("expressions: =total_thickness-19.88 with total 200", tech.evaluate("t - 19.88", {"t": 200.0}),
            180.12, 1e-12)
    tc.near("expressions: precedence and powers", tech.evaluate("-2 + 3 * 2 ** 2", {}), 10.0)
    for bad in ("__import__('os')", "open('x')", "t.real", "[1]"):
        try:
            tech.evaluate(bad, {"t": 1.0})
            tc.check(f"expressions: {bad!r} is refused", False)
        except (ValueError, KeyError):
            tc.check(f"expressions: {bad!r} is refused", True)

    # ── the three formats agree ──
    lv2, sub2, d2 = _levels(os.path.join(IHP, "SG13G2_200um.xml"))
    _check_sg13g2(tc, "SG13G2 schema 2.0 (bundled)", lv2, d2)
    tc.check("SG13G2 2.0: die body 3.75 um epi on 180 um substrate, top first",
             [(n, round(t / UM, 6)) for n, _m, t in sub2] == [("EPI", 3.75), ("Substrate", 180.0)], sub2)
    tc.check("SG13G2 2.0: ground planes and sheets are not levels",
             not {"SUBGND", "BACKSIDEGND", "LBE"} & set(lv2), sorted(lv2))

    fem = os.path.join(DATA, "SG13G2_FEM_200um.xml")
    lv3, sub3, d3 = _levels(fem)
    _check_sg13g2(tc, "SG13G2 schema 3.1 (FEM)", lv3, d3)
    tc.near("SG13G2 3.1: substrate = total_thickness - 19.88 = 180.12 um",
            dict((n, t) for n, _m, t in sub3).get("Substrate", 0), 180.12 * UM, 1e-12)
    tc.check("SG13G2 3.1: sheets (resistors, SUBGND) and the LBE brick are not levels",
             not {"RSIL", "RPPD", "RHIGH", "SUBGND", "BACKSIDEGND", "LBE", "REF_FOR_TRANSISTOR"} & set(lv3),
             sorted(lv3))
    tc.check("SG13G2 3.1: the same levels as the 2.0 file", set(lv3) == set(lv2), set(lv3) ^ set(lv2))

    lvr, _s, dr = _levels(os.path.join(DATA, "SG13G2_reference_schema3.0.xml"))
    _check_sg13g2(tc, "SG13G2 schema 3.0 (Reference)", lvr, dr)

    lvs, subs, ds = _levels(os.path.join(SKY, "SKY130A_300um.xml"))
    tc.check("SKY130: met5 5.3711 - 6.6311 um",
             abs(lvs["met5"][0] - 5.3711 * UM) < 1e-12 and abs(lvs["met5"][1] - 6.6311 * UM) < 1e-12, lvs.get("met5"))
    tc.check("SKY130: SiO2 0 - 6.6311 um, EPI -2 - 0 um",
             abs(ds["SiO2"][1] - 6.6311 * UM) < 1e-12 and abs(ds["EPI"][0] + 2 * UM) < 1e-12, ds)

    # ── technology ──
    ihp = tech.load(IHP)
    unused = [lv["name"] for lv in ihp.unused_levels({(134, 0), (9, 0), (8, 0), (19, 0), (134, 22)})]
    want = ["Activ", "Cont", "Metal2", "Via2", "Metal3", "Via3", "Metal4", "Via4", "Metal5",
            "TopVia1", "MIM", "Vmim", "TopMetal1", "TopVia2"]
    tc.check("unused levels: TopMetal2 + Metal1 + Via1 drawn leaves the 14 others, bottom first",
             unused == want, unused)
    tc.check("unused levels: metal fill alone counts as drawing the level",
             "Metal2" not in [lv["name"] for lv in ihp.unused_levels({(10, 22)})])

    out = tmpdir("stackup")
    sky_gds = _write_gds(os.path.join(out, "sky.gds"), [(68, 20), (68, 44), (69, 20), (71, 20), (72, 20), (76, 20)])
    from pkgstudio.core import layout
    keys = layout.layers_in(sky_gds)
    ranked = tech.match(keys, TECH)
    tc.check("detection: a SKY130 layout matches SKY130 best (6 of 6)",
             ranked and "SKY130" in ranked[0][0] and ranked[0][2] == 6, ranked)
    s = tech.suggestion(IHP, keys, TECH)
    tc.check("detection: read as SG13G2, SKY130 is suggested", s is not None and "SKY130" in s[0], s)
    tc.check("detection: read as SKY130, nothing is suggested", tech.suggestion(SKY, keys, TECH) is None)

    lna = next((p for p in ALL_LNA if os.path.isfile(p)), None)
    if lna is None:
        tc.skip("detection: ALL_LNA is an SG13G2 layout", "ALL_LNA.gds not found")
    else:
        keys = layout.layers_in(lna)
        s = tech.suggestion(SKY, keys, TECH)
        tc.check("detection: ALL_LNA read as SKY130 suggests SG13G2 (the 'square block' case)",
                 s is not None and "SG13G2" in s[0] and s[2] > 0.9 * s[4], s)
        built = [k for k in keys if k in tech.load(SKY).layers]
        tc.check("detection: SKY130 builds none of ALL_LNA's layers — only the die body was left",
                 built == [], built)
        tc.check("detection: ALL_LNA read as SG13G2 needs no suggestion",
                 tech.suggestion(IHP, keys, TECH) is None)

    # ── the import builds the slabs ──
    from pkgstudio.app import exporting, model
    gds = _write_gds(os.path.join(out, "ihp.gds"), [(189, 4), (134, 0), (9, 0), (8, 0)])
    doc = new_document("stackup")
    chip = model.import_chip(doc, gds, IHP)
    slabs = {o.Level: o for o in chip.Group if model.role(o) == model.UNUSED_LEVEL}
    tc.check("import: one slab per unused level (TopMetal2, Metal1 drawn; 15 not)",
             len(slabs) == 15 and "TopMetal2" not in slabs and "Metal1" not in slabs, sorted(slabs))
    t1 = slabs.get("TopMetal1")
    die = model.chip_layout(chip).outline
    tc.check("import: TopMetal1 slab at 6.4303 - 8.4303 um, as large as the die",
             t1 is not None and abs(t1.Shape.BoundBox.ZMin - 6.4303 * UM) < 1e-9
             and abs(t1.Shape.BoundBox.ZMax - 8.4303 * UM) < 1e-9
             and abs(t1.Shape.BoundBox.XLength - (die[2] - die[0])) < 1e-9, t1 and t1.Shape.BoundBox)
    diel = {o.Level: o for o in chip.Group if model.role(o) == model.DIELECTRIC}
    tc.check("import: oxide and passivation built, not the silicon twice",
             set(diel) == {"SiO2", "Passive"}, sorted(diel))
    if "SiO2" in diel:
        tc.near("import: SiO2 slab is 15.7303 um thick", diel["SiO2"].Shape.BoundBox.ZLength, 15.7303 * UM)
    els = [e for e in exporting.elements(doc) if e.obj in list(slabs.values()) + list(diel.values())]
    tc.check("export: slabs are offered but not ticked",
             len(els) == len(slabs) + len(diel) and not any(e.default for e in els))
    tc.check("export: drawn layers are still ticked",
             all(e.default for e in exporting.elements(doc) if e.kind == "layer"))
    import FreeCAD
    FreeCAD.closeDocument(doc.Name)
    return tc.results
