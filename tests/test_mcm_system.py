# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Multi-chip modules (core.mcm, the die-to-die wire rule), generated boards
(core.pcb) and systems (core.cubesat, app.system).

Worked out by hand:

arrange, a row along x, gap 0.3, dies 1.0 and 2.0 wide:
  row = 1.0 + 0.3 + 2.0 = 3.3, from -1.65: centres -1.15 and +0.65.
arrange, corner to corner, dies 0.78 x 0.98 and 1.05 x 1.05, gap 0.3:
  second die (0.78 + 1.05) / 2 + 0.3 = 1.215 right, (0.98 + 1.05) / 2 + 0.3
  = 1.315 down; extent x -0.39 .. 1.74, y -1.84 .. 0.49 -> centred by
  (0.675, -0.675): centres (-0.675, 0.675) and (0.54, -0.64).

QFN-16 4x4 lands (the Element Forge example: 4 a side at 0.65, b 0.30,
paddle 2.3): leads end 2.3 / 2 + 0.15 = 1.30 from the centre (no corner
cut: 1.5 * 0.65 + 0.15 = 1.125 < 1.25), 0.70 long; a left land runs from
-(2.0 + 0.25) = -2.25 to -(2.0 - 0.70 - 0.05) = -1.25, 0.35 wide.

GCPW from (0, 0) to (10, 0), w 0.44, s 0.20, ground 1.0: the ground strips'
centre lines at ±(0.22 + 0.20 + 0.50) = ±0.92; vias every 1 mm, 11 a side.

A bare PC/104 board (95.89 x 90.17, four 3.18 mm holes, 1.6 mm: 0.254
RO4350B, 0.035 Cu, 1.276 FR-4 under the ground plane), in grams:
  A = 95.89 * 90.17 - 4 pi 1.59^2           = 8614.64 mm²
  A (0.254 * 1.860 + 0.035 * 8.960 + 1.276 * 1.850) e-3 g/mm³ = 26.98 g
"""

import math

from _harness import TestCase

from pkgstudio.core import mcm


def _mcm(tc):
    c = mcm.arrange([(1.0, 1.0), (2.0, 1.0)], gap=0.3, direction="x")
    tc.check("row: centres -1.15 and +0.65", abs(c[0][0] + 1.15) < 1e-12 and abs(c[1][0] - 0.65) < 1e-12, c)
    c = mcm.arrange([(0.78, 0.98), (1.05, 1.05)], gap=0.3, direction="diagonal")
    tc.check("corner to corner: (-0.675, 0.675) and (0.54, -0.64)",
             all(abs(a - b) < 1e-12 for a, b in zip(c[0] + c[1], (-0.675, 0.675, 0.54, -0.64))), c)
    dies = [mcm.Die("A", 0.78, 0.98, *c[0]), mcm.Die("B", 1.05, 1.05, *c[1])]
    tc.check("on a 2.9 mm paddle: nothing to report", mcm.check(dies, 2.9, 2.9) == [])
    tc.check("on a 2.2 mm paddle: both stick out",
             sum(i.level == "error" and "sticks out" in i.message for i in mcm.check(dies, 2.2, 2.2)) == 2)
    close = [mcm.Die("A", 1.0, 1.0, -0.55), mcm.Die("B", 1.0, 1.0, 0.55)]
    tc.check("0.10 mm apart: an error (gap 0.15)", any("100 µm apart" in i.message for i in mcm.check(close, 4, 4)))
    over = [mcm.Die("A", 1.0, 1.0, -0.4), mcm.Die("B", 1.0, 1.0, 0.4)]
    tc.check("overlapping by 0.2 mm: an error", any("overlap by 200 µm" in i.message for i in mcm.check(over, 4, 4)))
    tc.check("a die on a bond finger: an error",
             any("keep-out" in i.message for i in mcm.check([mcm.Die("A", 1, 1)], 4, 4, [(0.4, -0.1, 0.6, 0.1)])))
    ring = [(math.cos(a), math.sin(a)) for a in [2 * math.pi * k / 12 for k in range(12)]]
    share = mcm.share_leads([(-0.5, 0.0), (0.5, 0.0)], [3, 8], ring)
    tc.check("share_leads: every die gets at least what it needs, every lead once",
             len(share[0]) >= 3 and len(share[1]) >= 8 and sorted(share[0] + share[1]) == list(range(12)), share)
    left = sorted(share[0])
    tc.check("share_leads: the left die's leads are one arc round 180°",
             all(abs(math.atan2(ring[j][1], ring[j][0])) > math.pi / 2 for j in left), left)
    try:
        mcm.share_leads([(0, 0)], [13], ring)
        tc.check("share_leads: more pads than leads is refused", False)
    except ValueError:
        tc.check("share_leads: more pads than leads is refused", True)


def _die_to_die_rule(tc):
    from pkgstudio.core import bondwire as bw, wirerules
    sq = lambda x, y, h=0.035: [(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)]  # noqa: E731
    p = bw.preset("Al", 0.025)
    w = wirerules.WireSpec("d2d", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), p, sq(0, 0), sq(1, 0), "a", "b", die_to_die=True)
    found = {f.rule: f for f in wirerules.check([w])["d2d"]}
    tc.check("die to die: the second bond is judged as on a die pad (3.2.1.4a), not on a post",
             "second bond on the die pad" in found and "bond on the post" not in found
             and found["second bond on the die pad"].ok, sorted(found))


def _pcb(tc):
    from pkgstudio.core import elements as el, pcb
    lands, ep = pcb.qfn_lands(el.EXAMPLE)
    left = [r for r, side in lands if side == "L"]
    r = left[0]
    tc.check("QFN-16 lands: 16 and the exposed pad 2.3 x 2.3",
             len(lands) == 16 and abs(ep[2] - ep[0] - 2.3) < 1e-12)
    tc.near("QFN-16 left land: toe at x = -2.25", r[0], -2.25, 1e-12)
    # corner land: unshortened its heel (x -1.25, y 0.8..1.15) would be 0.14 mm
    # from the top row's corner land (x 0.8..1.15, y from 1.25): shortened to
    # 0.2 mm from that row's outer edge, 0.975 + 0.35/2 + 0.2 = 1.35
    tc.near("QFN-16 left corner land: heel at x = -1.35 (0.2 mm from the top row)", r[2], -1.35, 1e-12)
    tc.near("QFN-16 left middle land: heel at x = -1.25", [q for q in left
            if abs((q[1] + q[3]) / 2) < 0.5][0][2], -1.25, 1e-12)
    tc.near("QFN-16 left land: 0.35 wide", r[3] - r[1], 0.35, 1e-12)
    g = pcb.gcpw((0, 0), (10, 0), 0.44, 0.20, ground_w=1.0)
    strips = [q for q in g if "ground" in q.name]
    ys = sorted(round((q.shape.BoundBox.YMin + q.shape.BoundBox.YMax) / 2, 9) for q in strips)
    tc.check("GCPW: ground strips centred at ±0.92", ys == [-0.92, 0.92], ys)
    vias = next(q for q in g if q.name.endswith("via fence"))
    tc.check("GCPW: 22 vias", vias.props["Vias"] == 22 and len(vias.shape.Solids) == 22)
    st = pcb.Stack()
    tc.near("stack: top copper top at 0.289", st.top, 0.289, 1e-12)
    tc.near("stack: underside at -1.311", st.bottom, -1.311, 1e-12)


def _system(tc):
    import FreeCAD
    from pkgstudio.app import boards, system
    from pkgstudio.core import pcb
    doc = FreeCAD.newDocument("system_test")
    try:
        sat = system.make_cubesat(doc, antenna=False)
        bare = boards.make_board(doc, pcb.board(), "Bare", pcb.Stack())
        system.add_board(sat, bare, -20.0)
        found, s = system.check(sat)
        errors = [f for f in found if f.level == "error"]
        tc.check("a bare board in the CubeSat: no errors", not errors, [str(f) for f in errors])
        A = 95.89 * 90.17 - 4 * math.pi * 1.59 ** 2
        want = A * (0.254 * 1.860 + 0.035 * 8.960 + 1.276 * 1.850) * 1e-3
        tc.near("bare board mass = 26.98 g (by hand)", s["mass_by_module"]["Bare"], want, 0.01)
        tc.check("the stack's spacers and rods are there", "Stack" in s["mass_by_module"])

        # a tall part under the next board
        tall = boards.make_board(doc, pcb.board() + [pcb.box("Tall", 0, 0, 5, 5, 14.0, "Mould compound (EMC)")],
                                 "Tall", pcb.Stack())
        system.add_board(sat, tall, 0.0)
        found, s = system.check(sat)
        bad = [f for f in found if f.check == "stack" and "Tall" in f.message]
        tc.check("parts 14 mm high: above 11 mm (warning)", any(f.level == "warning" for f in bad),
                 [str(f) for f in found])
        top = boards.make_board(doc, pcb.board(), "Next", pcb.Stack())
        system.add_board(sat, top, 15.0)
        found, s = system.check(sat)
        hit = [f for f in found if f.level == "error" and ("collision" in f.check or "stack" in f.check)]
        tc.check("a 14 mm part under a board 15 mm up: collides with it", hit, [str(f) for f in found])

        # an edge-launch SMA on a board stands out of the CubeSat and into the -X panel
        sma = boards.make_board(doc, pcb.board() + [pcb.sma_edge("SMA", -pcb.PC104_X / 2, 0.0)], "SMA board",
                                pcb.Stack())
        system.add_board(sat, sma, 35.0)
        found, s = system.check(sat)
        tc.check("an edge SMA: out of the envelope", any(f.check == "envelope" and "SMA" in f.message
                                                         for f in found), [str(f) for f in found])
        tc.check("an edge SMA: collides with the -X solar panel",
                 any(f.check == "collision" and "SMA" in f.message and "-X" in f.message for f in found))
    finally:
        FreeCAD.closeDocument(doc.Name)


def _reference_case(tc):
    """examples/cubesat_sband_mcm.py, without the EM part: the whole chain on the real dies."""
    import importlib.util
    import os
    from _harness import tmpdir
    os.environ["PS_EXAMPLE_OUT"] = tmpdir("cubesat")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("cubesat_example", os.path.join(here, "examples",
                                                                                  "cubesat_sband_mcm.py"))
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    import FreeCAD
    doc, r = ex.build(with_em=False)
    try:
        m, s = r["mcm"], r["system"]
        tc.check("reference: both dies on the paddle, nothing to report", m["layout_issues"] == [], m["layout_issues"])
        tc.check("reference: 29 wires to leads from the pin list, none die to die",
                 len(m["pin_map"]) == 29 and m["die_to_die"] == [], m["die_to_die"])
        tc.check("reference: the RF pins on their fixed GSG leads (2/3/4, 27/28/29)",
                 [m["pin_map"].get(k) for k in ("2", "3", "4", "27", "28", "29")]
                 == ["GND14", "RFIN", "GND15", "GND4", "RFOUT", "GND3"], m["pin_map"])
        tc.check("reference: package LVS without errors or warnings",
                 not [f for f in m["package_lvs"] if not f.startswith("info")], m["package_lvs"])
        tc.check("reference: no MIL-STD-883 reject on any of the 29 wires",
                 m["wire_rule_rejects"] == [] and m["wires_checked"] == 29, m["wire_rule_rejects"][:3])
        tc.check("reference: bonding diagram, wire list and KiCad library written",
                 len(r["deliverables"]) == 5 and all(os.path.isfile(p) for p in r["deliverables"]), r["deliverables"])
        errors = [f for f in s["findings"] if f.startswith("error")]
        tc.check("reference: the CubeSat passes (no errors)", errors == [], errors)
        tc.check("reference: the battery stand-in's 18.4 mm cells are flagged (CubeSat Kit 11 mm)",
                 any("Battery" in f and "18.40 mm" in f for f in s["findings"]))
        tc.check("reference: mass under 2 kg, centre of gravity within 2 cm",
                 s["mass_g"] < 2000 and all(abs(c) <= 20 for c in s["cg_mm"]), (s["mass_g"], s["cg_mm"]))
    finally:
        FreeCAD.closeDocument(doc.Name)


def run():
    tc = TestCase("mcm-system")
    tc.guard("mcm", lambda: _mcm(tc))
    tc.guard("die-to-die rule", lambda: _die_to_die_rule(tc))
    try:
        import FreeCAD  # noqa: F401
        tc.guard("pcb", lambda: _pcb(tc))
        tc.guard("system", lambda: _system(tc))
        tc.guard("reference case", lambda: _reference_case(tc))
    except ImportError:
        tc.skip("pcb and system", "FreeCAD not available")
    return tc.results
