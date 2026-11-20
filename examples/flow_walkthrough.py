# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Every command of the Design flow toolbar once, on the files in
examples/flow/ — the LNA in a QFN-40. What the GUI does, step by step
(examples/flow/README.md says which button and which file):

  1 Pin list & LVS     lna_top.sch + lna_pinlist.csv -> wires by net -> LVS
  2 LVS finds errors   lna_pinlist_broken.csv against the same wires
  3 Bonding diagram    SVG + CSV
  4 KiCad library      footprint, symbol with the nets, STEP
  5 KiCad board        lna_qfn40_board.kicad_pcb: whole, and the RF input
                       region for EM; the package placed on U1
  6 Circuit model      touchstone/lna_pkg_sim.s2p -> SPICE + xschem symbol
  7 Measurement        touchstone/lna_pkg_meas.s2p against lna_pkg_sim.s2p,
                       fixture removed with lna_2xthru.s2p

  8 Verilog pin list  public/i2c_gpio_expander_SG13G2Top.v (the real top of
                       IHP's open tapeout): 16 ports, no supply ports
  9 P370, public       public/p370: fixture-DUT-fixture and 2x-thru from the
                       scikit-rf repository, de-embedded and compared with
                       the published result of an independent tool (AICC)
 10 Real measurement   public/msl_measured vs public/msl_simulated: a
                       microstrip line measured on an Anritsu VNA (scikit-rf
                       repository) against its simulation; and a circuit
                       model of the measured stepped line, 0–3 GHz

Steps 6–7 use SYNTHETIC Touchstone files (scikit-rf models): they show how
the tools work, not how good the package is. Steps 8–10 use public, real
files (examples/flow/public/README.md says where from).

    freecadcmd examples/flow_walkthrough.py      (about 1 min; out: examples/out/flow/)
"""

import os
import shutil
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples")
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pkgstudio.app import bonding, boards, housings, kicad, model, netlist, wires  # noqa: E402
from pkgstudio.core import jedec  # noqa: E402

FLOW = os.path.join(HERE, "flow")
OUT = os.environ.get("PS_EXAMPLE_OUT", os.path.join(HERE, "out", "flow"))
TECH = os.path.join(ROOT, "technologies", "IHP-SG13G2")


def say(text=""):
    FreeCAD.Console.PrintMessage(text + "\n")


def run():
    os.makedirs(OUT, exist_ok=True)
    result = {}
    doc = FreeCAD.newDocument("FlowWalkthrough")
    lna = model.import_chip(doc, os.path.join(HERE, "data", "ALL_LNA.gds"), TECH)
    h = housings.from_jedec(doc, jedec.by_name("QFN-40 6x6"))
    housings.place_chip(lna, h)

    say("1  Pin list & LVS — lna_top.sch (xschem: the pins) + lna_pinlist.csv (grounds, fixed RF leads)")
    sch, csv = os.path.join(FLOW, "lna_top.sch"), os.path.join(FLOW, "lna_pinlist.csv")
    intent = netlist.load([sch, csv], {sch: "ALL_LNA"})
    say(f"   {len(intent.pins)} pins, nets {', '.join(sorted(intent.nets()))}")
    made = netlist.assign(h, intent)
    found, nets = netlist.lvs(h, intent)
    rejects = [f for v in wires.check(doc).values() for f in v if not f.ok and f.severity == "reject"]
    say(f"   {len(made)} wires; LVS: {[str(f) for f in found] or 'clean'}; MIL-STD-883 rejects: {len(rejects)}")
    result["lvs"] = [str(f) for f in found]

    say("2  The same wires against lna_pinlist_broken.csv (Check only)")
    broken = netlist.load([os.path.join(FLOW, "lna_pinlist_broken.csv")])
    bad, _n = netlist.lvs(h, broken)
    for f in bad:
        say(f"   {f}")
    result["lvs_broken"] = [str(f) for f in bad]
    netlist.lvs(h, intent)                       # the right nets back on the leads

    say("3  Bonding diagram")
    result["bonding"] = bonding.export(h, OUT, "LNA_QFN40", intent)
    say("   " + "\n   ".join(result["bonding"]))

    say("4  KiCad library")
    kinds = {p.net: p.kind for p in intent.pins}
    result["kicad_library"] = kicad.export_package(h, os.path.join(OUT, "kicad"), "LNA_QFN40", lib="LNA", kinds=kinds)
    say("   " + "\n   ".join(result["kicad_library"]))

    say("5  KiCad board — lna_qfn40_board.kicad_pcb")
    board_file = os.path.join(FLOW, "lna_qfn40_board.kicad_pcb")
    board, rep = kicad.import_board(doc, board_file)
    pkg = model.assembly_owner(h)
    boards.place_package(pkg, board, "U1")
    say(f"   whole board: {rep['pieces']} pieces, nets {rep['nets']}; package placed on U1")
    region = (85.0, -103.0, 99.0, -97.0)           # x 85..99, KiCad y 97..103 (y up here): RF input + package edge
    cut, rep2 = kicad.import_board(doc, board_file, region, name="RF input (EM region)")
    say(f"   region x 85..99, y 97..103 mm: {rep2['pieces']} pieces — what an EM model of the input needs")
    result["kicad_board"] = {"pieces": rep["pieces"], "region_pieces": rep2["pieces"]}
    doc.saveAs(os.path.join(OUT, "FlowWalkthrough.FCStd"))

    ts = os.path.join(FLOW, "touchstone")
    try:
        import skrf  # noqa: F401
    except ImportError:
        say("6/7 skipped: scikit-rf is not installed (python -m pip install scikit-rf matplotlib)")
        return doc, result
    from pkgstudio.core import circuit_model as cm, validation

    say("6  Circuit model — touchstone/lna_pkg_sim.s2p")
    src = os.path.join(OUT, "lna_pkg_sim.s2p")
    shutil.copy(os.path.join(ts, "lna_pkg_sim.s2p"), src)
    cir, r = cm.write_spice(src)
    sym = cm.write_xschem_symbol(os.path.splitext(cir)[0] + ".sym", r["name"], r["ports"], cir, ["RF_IN", "RF_OUT"])
    chk = cm.check_against(cir, src)
    say(f"   {cir}\n   {sym}\n   {r['poles']} poles, RMS {r['rms_error']:.4f}, passive: {r['passive']}"
        + (f"; ngspice vs Touchstone: max {chk['max_db_error']:.2f} dB" if chk else "; (no ngspice on PATH)"))
    result["circuit_model"] = dict(r, symbol=sym, ngspice=chk)

    say("7  Compare with measurement — lna_pkg_meas.s2p vs lna_pkg_sim.s2p, fixture: lna_2xthru.s2p")
    v = validation.report(os.path.join(ts, "lna_pkg_meas.s2p"), os.path.join(ts, "lna_pkg_sim.s2p"),
                          os.path.join(OUT, "validation"), thru=os.path.join(ts, "lna_2xthru.s2p"), tol_db=1.0)
    for k, c in v["comparison"].items():
        if k.startswith("S"):
            say(f"   {k}: max {c['max_db']:.2f} dB, RMS {c['rms_db']:.2f} dB  {'pass' if c['pass'] else 'FAIL'}")
    quality = ", ".join(f"{k} {q['evaluation']}" for k, q in v["quality"].items())
    say(f"   P370 quality: {quality}")
    say(f"   report: {v['markdown']}")
    result["validation"] = v["comparison"]

    pub = os.path.join(FLOW, "public")
    say("8  Verilog pin list — public/i2c_gpio_expander_SG13G2Top.v (IHP open tapeout)")
    from pkgstudio.core import pinlist
    vi = pinlist.read_verilog(os.path.join(pub, "i2c_gpio_expander_SG13G2Top.v"), "i2c_gpio_expander")
    kinds = sorted({p.kind for p in vi.pins})
    say(f"   {len(vi.pins)} ports ({', '.join(kinds)}): {', '.join(p.pad for p in vi.pins[:5])} …")
    say("   signal ports only — the supply pads are not ports of the top; a CSV adds them (merge)")
    result["verilog_ports"] = len(vi.pins)

    say("9  P370 de-embedding on public data — public/p370 (scikit-rf repository)")
    import skrf
    from skrf.calibration.deembedding import IEEEP370_SE_NZC_2xThru
    thru, fdf = skrf.Network(os.path.join(pub, "p370", "se_2xthru.s2p")), skrf.Network(os.path.join(pub, "p370", "se_fdf.s2p"))
    ref = os.path.join(pub, "p370", "deembedded_SE_NZC_se_fdf.s2p")
    dm = IEEEP370_SE_NZC_2xThru(dummy_2xthru=thru, z0=50)
    aicc = validation.compare(dm.s_side1.inv ** fdf ** dm.s_side2.inv, ref, floor_db=-60)
    say(f"   against the published result of the AICC tool (its side-2 convention): |S21| within "
        f"{aicc['S21']['max_db']:.2f} dB, |S11| within {aicc['S11']['max_db']:.2f} dB")
    result["p370_vs_aicc"] = {k: aicc[k]["max_db"] for k in ("S11", "S21")}

    say("10 Real measurement — public/msl_measured vs public/msl_simulated (Anritsu MS46524B, 2018)")
    for n in ("P1-MSL_Thru_100-P2", "P1-MSL_Stepped_140-P2"):
        r = validation.report(os.path.join(pub, "msl_measured", f"{n}.s2p"), os.path.join(pub, "msl_simulated", f"{n}.s2p"),
                              os.path.join(OUT, "validation_" + n), tol_db=1.0)
        c = r["comparison"]
        say(f"   {n}: |S21| max {c['S21']['max_db']:.2f} dB, RMS {c['S21']['rms_db']:.2f} dB — "
            f"{'pass' if c['S21']['pass'] else 'FAIL'} (the measurement has connectors, the simulation does not)")
        result.setdefault("msl", {})[n] = {k: c[k]["max_db"] for k in ("S11", "S21")}
    meas = skrf.Network(os.path.join(pub, "msl_measured", "P1-MSL_Stepped_140-P2.s2p"))[::25]
    src = os.path.join(OUT, "msl_stepped_measured.s2p")
    meas.write_touchstone(os.path.splitext(src)[0], r_ref=50)
    try:
        cm.write_spice(src)
        say("   circuit model, full 0–10 GHz band: written (unexpected)")
    except cm.FitError as exc:
        say(f"   circuit model, full band: refused — {str(exc)[:90]}…")
    cir, r = cm.write_spice(src, fmax=3e9)
    chk = cm.check_against(cir, src, fmax=3e9)
    say(f"   circuit model, 0–3 GHz: {r['poles']} poles, passive, RMS {r['rms_error']:.4f}"
        + (f"; ngspice vs measurement: max {chk['max_db_error']:.2f} dB" if chk else ""))
    result["msl_model"] = dict(r, ngspice=chk)
    return doc, result


# freecadcmd runs a file under its own name, FreeCAD's macro runner as __main__
if (__name__ in ("__main__", "flow_walkthrough") or os.environ.get("PS_EXAMPLE_RUN")) and not os.environ.get("PS_EXAMPLE_NO_RUN"):
    run()
