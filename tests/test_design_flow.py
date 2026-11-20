# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The design flow round the package (docs/plan): the pin list and the package
LVS, crossing-free wiring with fixed leads, the bonding diagram, KiCad in
and out, the circuit model, the comparison with a measurement.

Worked out by hand:

KiCad pad position: a footprint at (10, 20) turned 90°, a pad at (1, 0) in
its frame. KiCad's y points down, ours up: the pad is (1, 0) in ours, turned
90° counter-clockwise -> (0, 1); the footprint is at (10, -20): the pad at
(10, -19).

Stack-up z (KiCad order, top first): F.Cu 0.035, core 0.254, In1.Cu 0.035,
prepreg 1.2, B.Cu 0.035 — 1.559 in all; F.Cu from 1.524 to 1.559, core
1.270–1.524, In1.Cu 1.235–1.270, prepreg 0.035–1.235, B.Cu 0–0.035.

A track from x = 2 to 12, 0.44 wide (round ends), cut to the region x 1..6:
1.78 .. 6 = 4.22 mm; centred on the region (3.5): -1.72 .. 2.5.

A 20 x 10 mm board with a 3.2 mm hole: dielectric of the core =
(200 - pi 1.6^2) * 0.254 = 48.7573 mm³.

Bonding diagram, wire list: a die centred at (1, 2), its pad at (1.1, 2.05):
the first bond at (+100, +50) µm from the die's centre; the lead at (-2, 0)
-> (-2000, 0) µm from the package centre.

Validation: a 0.5 dB attenuator after the simulated network is a measured
network 0.500 dB lower in |S21| everywhere; the IEEE P370 2x-thru removes a
fixture that is a plain line exactly.
"""

import math
import os

from _harness import TestCase, tmpdir

from pkgstudio.core import bonding_diagram as bd, kicad_export, kicad_pcb as kp, mcm, package_lvs as lvs, pinlist

HERE = os.path.dirname(os.path.abspath(__file__))
KDATA = os.path.join(HERE, "data", "kicad")

CSV = """# test
die,pad,net,kind,lead
A,RFIN,RF_IN,rf,3
A,G1,GND,ground,2
A,G2,GND,ground,4
A,VCC,VCC,power,
A,EN,EN,signal,
B,VDD,VCC,power,
B,VSS,GND,ground,
B,IO,EN,signal,
B,SPARE,X,nc,
"""

XSCHEM = """v {xschem version=3.4.5 file_version=1.2}
C {ipin.sym} -200 0 0 0 {name=p1 lab=RFIN}
C {opin.sym} 200 0 0 0 {name=p2 lab=RFOUT}
C {devices/iopin.sym} 0 -200 0 0 {name=p3 lab=VDD}
C {iopin.sym} 0 200 0 0 {name=p4 lab=GND}
C {res.sym} 0 0 0 0 {name=R1 value=50}
"""

LEF = """MACRO top
  PIN clk
    DIRECTION INPUT ;
    USE SIGNAL ;
  END clk
  PIN VPWR
    USE POWER ;
  END VPWR
  PIN VGND
    USE GROUND ;
  END VGND
END top
"""


def _pinlist(tc):
    i = pinlist.read_csv(CSV)
    tc.check("pin list: 9 pins, 4 nets used by two dies", len(i.pins) == 9 and len(i.nets()) == 5)
    tc.check("pin list: kinds and fixed leads read", i.pin("A", "RFIN").kind == "rf" and i.pin("A", "RFIN").lead == "3")
    tc.check("pin list: EN is meant to join the two dies (no lead, two dies, a signal)",
             i.die_to_die_nets() == ["EN"], i.die_to_die_nets())
    x = pinlist.read_xschem(XSCHEM, "LNA")
    tc.check("xschem: the four top pins, kinds from the names",
             [(p.pad, p.kind) for p in x.pins] == [("RFIN", "rf"), ("RFOUT", "rf"), ("VDD", "power"), ("GND", "ground")],
             [(p.pad, p.kind) for p in x.pins])
    lf = pinlist.read_lef(LEF, "DIG")
    tc.check("LEF: USE POWER / GROUND / SIGNAL", [(p.pad, p.kind) for p in lf.pins]
             == [("clk", "signal"), ("VPWR", "power"), ("VGND", "ground")])
    m = x.merge(pinlist.read_csv("die,pad,net,kind,lead\nLNA,RFIN,RF_IN,rf,7\n"))
    tc.check("merge: the CSV fixes RFIN's net and lead, keeps the rest",
             m.pin("LNA", "RFIN").net == "RF_IN" and m.pin("LNA", "RFIN").lead == "7" and len(m.pins) == 4)
    tc.check("guess_kind", [pinlist.guess_kind(n) for n in ("VSSA", "vdd_io", "RFOUT", "SCL", "GND_PA")]
             == ["ground", "power", "rf", "signal", "ground"])


def _lvs(tc):
    i = pinlist.read_csv(CSV)
    W = lvs.Wire
    good = [W("w1", "A", "RFIN", "", "3"), W("w2", "A", "G1", "", "2"), W("w3", "A", "G2", "", "4"),
            W("w4", "A", "VCC", "", "5"), W("w5", "A", "EN", "B", "IO"), W("w6", "B", "VDD", "", "6"),
            W("w7", "B", "VSS", "", "7")]
    nb = {"3": ["2", "4"], "2": ["1", "3"], "4": ["3", "5"]}
    f, nets = lvs.check(i, good, [str(k) for k in range(1, 9)], nb)
    tc.check("LVS: the intended package has no error or warning",
             not [x for x in f if x.level != "info"], [str(x) for x in f])
    tc.check("LVS: lead nets", nets == {"3": "RF_IN", "2": "GND", "4": "GND", "5": "VCC", "6": "VCC", "7": "GND"}, nets)
    bad = [W("w1", "A", "RFIN", "", "5"), W("w2", "A", "G1", "", "2"), W("w3", "A", "G2", "", "4"),
           W("w4", "A", "VCC", "", "5"), W("w5", "A", "EN", "B", "VSS"), W("w8", "B", "SPARE", "", "8"),
           W("w9", "B", "NOPE", "", "1")]
    f, _n = lvs.check(i, bad, [str(k) for k in range(1, 9)], {"5": ["4", "6"]})
    rules = sorted({x.rule for x in f if x.level == "error"})
    tc.check("LVS: wrong lead, short (lead and die to die), nc bonded, unknown pad, open",
             rules == ["nc bonded", "open", "short", "unknown pad", "wrong lead"], rules)
    tc.check("LVS: lead 5 carries RF_IN and VCC", any("Lead 5 carries RF_IN, VCC" in x.message for x in f))
    f2, _n = lvs.check(i, [W("w1", "A", "RFIN", "", "3"), W("w4", "A", "VCC", "", "4")], ["3", "4"],
                       {"3": ["4"]})
    tc.check("LVS: an RF lead beside a supply lead: no ground-signal-ground (warning)",
             any(x.rule == "rf return" and x.level == "warning" for x in f2), [str(x) for x in f2])
    f, _n = lvs.check(i, [W("w1", "A", "EN", "", "1")], ["1"])
    tc.check("LVS: a supply without a lead", any(x.rule == "no lead" and x.net == "VCC" for x in f))


def _ring(n=16, r=3.0):
    return [(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def _crossing(segs):
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    for i, (a, b) in enumerate(segs):
        for c, d in segs[i + 1:]:
            if ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d):
                return True
    return False


def _anchored(tc):
    leads = _ring()
    pads = [(0.5 * math.cos(2 * math.pi * k / 8 + 0.1), 0.5 * math.sin(2 * math.pi * k / 8 + 0.1)) for k in range(8)]
    pairs = mcm.pair_anchored(pads, leads, [(0, 0), (4, 9)], (0, 0))
    tc.check("anchored: the anchors are kept", (0, 0) in pairs and (4, 9) in pairs, pairs)
    tc.check("anchored: every pad once, no lead twice", sorted(i for i, _j in pairs) == list(range(8))
             and len({j for _i, j in pairs}) == 8)
    tc.check("anchored: no two wires cross", not _crossing([(pads[i], leads[j]) for i, j in pairs]))
    try:
        mcm.pair_anchored(pads, leads, [(0, 0), (1, 2), (2, 1)], (0, 0))
        tc.check("anchored: crossing anchors are refused", False)
    except ValueError:
        tc.check("anchored: crossing anchors are refused", True)
    try:
        mcm.pair_anchored(pads, leads, [(0, 0), (4, 2)], (0, 0))
        tc.check("anchored: three pads between leads 0 and 2 (one lead) are refused", False)
    except ValueError as exc:
        tc.check("anchored: three pads between leads 0 and 2 (one lead) are refused", "between two fixed" in str(exc))
    # two dies, one with fixed leads: the exhaustive arc search
    a = [(-1.0 + 0.3 * math.cos(t), 0.3 * math.sin(t)) for t in [2 * math.pi * k / 4 for k in range(4)]]
    b = [(1.0 + 0.3 * math.cos(t), 0.3 * math.sin(t)) for t in [2 * math.pi * k / 6 for k in range(6)]]
    res = mcm.assign_arcs([{"centre": (-1, 0), "pads": a, "anchors": {2: 8}}, {"centre": (1, 0), "pads": b}],
                          leads, (0, 0))
    segs = [(a[i], leads[j]) for i, j in res[0]] + [(b[i], leads[j]) for i, j in res[1]]
    tc.check("arcs: the left die keeps its fixed lead 8", (2, 8) in res[0], res[0])
    tc.check("arcs: 10 wires, no lead twice, none crossing",
             len(segs) == 10 and len({j for r in res for _i, j in r}) == 10 and not _crossing(segs))


def _diagram(tc):
    d = {"title": "t", "package": {"name": "P", "body": (5, 5), "paddle": (0, 0, 3, 3),
                                   "leads": [{"name": "1", "x": -2.0, "y": 0.0, "w": 0.3, "h": 0.25, "net": "N"}]},
         "dies": [{"name": "D", "x": 1.0, "y": 2.0, "w": 1.0, "h": 1.0,
                   "pads": [{"name": "P1", "x": 1.1, "y": 2.05, "w": 0.06, "h": 0.06, "net": "N"}]}],
         "wires": [{"name": "w", "net": "N", "a": ("D", "P1"), "b": ("", "1"), "pa": (1.1, 2.05), "pb": (-2.0, 0.0),
                    "material": "Al", "d_um": 25.0, "loop_um": 150.0, "length_mm": 3.6}]}
    r = bd.rows(d)[0]
    tc.check("wire list: first bond +100/+50 µm from the die centre",
             abs(r["first_x_um"] - 100) < 1e-6 and abs(r["first_y_um"] - 50) < 1e-6, r)
    tc.check("wire list: lead at -2000/0 µm from the package centre",
             r["second_x_um"] == -2000.0 and r["second_y_um"] == 0.0 and r["second"] == "lead 1")
    svg = bd.svg(d, {"N": "rf"})
    tc.check("diagram: an SVG with the net in the RF colour", svg.startswith("<svg") and "#6a1b9a" in svg)


SYNTH = """(kicad_pcb (version 20240108) (generator "test")
  (general (thickness 1.559))
  (setup (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.254) (material "RO4350B") (epsilon_r 3.66) (loss_tangent 0.0037))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "prepreg") (thickness 1.2) (material "FR4") (epsilon_r 4.5))
    (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "GND") (net 2 "RF")
  (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts"))
  (gr_line (start 20 0) (end 20 10) (layer "Edge.Cuts"))
  (gr_line (start 20 10) (end 0 10) (layer "Edge.Cuts"))
  (gr_line (start 0 10) (end 0 0) (layer "Edge.Cuts"))
  (footprint "test:hole" (layer "F.Cu") (at 3 3)
    (property "Reference" "H1")
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu")))
  (footprint "test:two" (layer "F.Cu") (at 10 20 90)
    (property "Reference" "U1")
    (pad "1" smd rect (at 1 0 90) (size 0.5 0.3) (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "RF")))
  (segment (start 2 5) (end 12 5) (width 0.44) (layer "F.Cu") (net 2))
  (via (at 15 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
  (zone (net 1) (net_name "GND") (layer "In1.Cu")
    (filled_polygon (layer "In1.Cu") (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
  (gr_arc (start 30 0) (mid 31 1) (end 32 0) (layer "F.SilkS"))
)
"""


def _kicad_read(tc):
    b = kp.read(SYNTH)
    p = next(q for q in b.pads if q.ref == "U1")
    tc.near("KiCad: pad x after the footprint's 90° turn", p.x, 10.0, 1e-12)
    tc.near("KiCad: pad y after the footprint's 90° turn (y up)", p.y, -19.0, 1e-12)
    z = kp.z_ranges(b)
    want = [(z["F.Cu"][0], 1.524), (z["F.Cu"][1], 1.559), (z["dielectric 1"][0], 1.270), (z["In1.Cu"][0], 1.235),
            (z["B.Cu"][1], 0.035)]
    tc.check("KiCad: stack-up z ranges", all(abs(a - b_) < 1e-9 for a, b_ in want), z)
    tc.check("KiCad: outline chained into one 20 x 10 loop", len(b.outline) == 1 and b.bbox() == (0, -10, 20, 0),
             b.bbox())
    tc.check("KiCad: track, via, zone with nets", b.tracks[0]["net"] == "RF" and b.vias[0]["net"] == "GND"
             and b.zones[0]["net"] == "GND" and b.zones[0]["layer"] == "In1.Cu")
    pts = kp._arc_points((0, 0), (1, 1), (2, 0))
    tc.check("KiCad: an arc through start, mid and end stays on its circle",
             all(abs(math.hypot(x - 1, y) - 1) < 1e-9 for x, y in pts) and math.dist(pts[0], (0, 0)) < 1e-12
             and max(y for _x, y in pts) > 0.99)
    real = kp.read(os.path.join(KDATA, "basic_test.kicad_pcb"))
    tc.check("KiCad 7 file (basic_test): 9 footprints, 26 tracks, 4 vias, 7 nets, 35 x 22 board",
             (len(real.footprints), len(real.tracks), len(real.vias), len(real.nets)) == (9, 26, 4, 7)
             and real.bbox() == (188.0, -160.0, 223.0, -138.0),
             (len(real.footprints), len(real.tracks), len(real.vias), len(real.nets), real.bbox()))
    mw = kp.read(os.path.join(KDATA, "microwave.kicad_pcb"))
    tc.check("KiCad 9 file (microwave): four footprints with copper polygons, named apart",
             len(mw.footprints) == 4 and len(mw.zones) == 4 and "POLY_4" in mw.footprints)


def _kicad_write(tc):
    pads = [{"number": "1", "x": -2.4375, "y": 1.75, "w": 0.875, "h": 0.25},
            {"number": "2", "x": 0.0, "y": 0.0, "w": 3.45, "h": 3.45, "ep": True}]
    text = kicad_export.footprint("T", pads, (5, 5), model_path="${KIPRJMOD}/T.step")
    fp = kp.parse(text)
    p1 = next(x for x in kp.findall(fp, "pad") if x[1] == "1")
    tc.check("footprint: pin 1 at KiCad (-2.4375, -1.75) — y flipped", kp.find(p1, "at")[1:3] == ["-2.4375", "-1.75"])
    tc.check("footprint: the exposed pad has a reduced paste opening",
             any(kp.find(x, "solder_paste_margin_ratio") for x in kp.findall(fp, "pad") if x[1] == "2"))
    sym = kp.parse(kicad_export.symbol("T", [{"number": "1", "name": "RF_IN", "kind": "rf"},
                                            {"number": "2", "name": "GND", "kind": "ground"}], "lib:T"))
    unit = [x for x in kp.findall(kp.find(sym, "symbol"), "symbol") if x[1] == "T_1_1"][0]
    pins = [(p[1], kp.find(p, "name")[1], kp.find(p, "number")[1]) for p in kp.findall(unit, "pin")]
    tc.check("symbol: pins with nets and electrical types", pins == [("passive", "RF_IN", "1"), ("power_in", "GND", "2")],
             pins)
    try:
        from kiutils.footprint import Footprint
    except ImportError:
        tc.skip("kiutils reads the footprint", "kiutils not installed")
        return
    path = os.path.join(tmpdir("kicad_fp"), "T.kicad_mod")
    with open(path, "w") as fh:
        fh.write(text)
    tc.check("kiutils (an independent reader) reads the footprint", len(Footprint.from_file(path).pads) == 2)


def _circuit(tc):
    try:
        import numpy as np
        import skrf
        from skrf.media import DefinedGammaZ0
    except ImportError:
        tc.skip("circuit model and validation", "scikit-rf not installed")
        return
    import shutil
    import warnings
    from pkgstudio.core import circuit_model as cm, validation as val
    warnings.filterwarnings("ignore")
    f = skrf.Frequency(0.1, 10, 200, "GHz")
    m = DefinedGammaZ0(f, z0=50, gamma=(0.02 + 1j) * 2 * np.pi * f.f / 3e8 * 1.9)
    pkg = m.line(4e-3, "m") ** m.inductor(1.0e-9) ** m.shunt_capacitor(0.15e-12) ** m.line(2e-3, "m")
    folder = tmpdir("circuit")
    pkg.write_touchstone(os.path.join(folder, "pkg_like"), r_ref=50)
    ts = os.path.join(folder, "pkg_like.s2p")
    cir, rep = cm.write_spice(ts)
    tc.check("circuit model: passive as fitted, RMS error under 0.005", rep["passive"] and rep["rms_error"] < 0.005
             and rep["how"] == "passive as fitted", rep)
    text = open(cir).read()
    tc.check("circuit model: .SUBCKT pkg_like p1 p2", ".SUBCKT pkg_like p1 p2" in text)
    sym = cm.write_xschem_symbol(os.path.join(folder, "pkg_like.sym"), "pkg_like", 2, cir)
    tc.check("circuit model: an xschem symbol with two pins that includes the subcircuit",
             open(sym).read().count("dir=inout") == 2 and ".include" in open(sym).read())
    if shutil.which("ngspice"):
        chk = cm.check_against(cir, ts)
        tc.check("circuit model: ngspice reproduces |S| within 0.25 dB", chk["max_db_error"] < 0.25, chk)
    else:
        tc.skip("circuit model in ngspice", "ngspice not on PATH")
    c = val.compare(pkg ** m.attenuator(-0.5), pkg, params=[(2, 1)])
    tc.near("validation: a 0.5 dB attenuator is 0.500 dB in |S21|", c["S21"]["max_db"], 0.5, 1e-9)
    fix = m.line(10e-3, "m")
    dut = m.inductor(0.8e-9) ** m.shunt_capacitor(0.2e-12) ** m.line(1e-3, "m")
    c = val.compare(val.deembed(fix ** dut ** fix.flipped(), fix ** fix.flipped()), dut)
    tc.check("validation: P370 2x-thru removes a plain-line fixture exactly",
             c["S11"]["max_db"] < 1e-4 and c["S21"]["max_db"] < 1e-4, (c["S11"], c["S21"]))
    q = val.quality(pkg)
    tc.check("validation: a passive reciprocal network rates 'good' in P370 passivity and reciprocity",
             q["passivity"]["evaluation"] == "good" and q["reciprocity"]["evaluation"] == "good", q)


def _public(tc):
    """The public files in examples/flow/public/ (GitHub: IHP TO_Nov2024, scikit-rf)."""
    from pkgstudio.core import pinlist
    from pkgstudio.core import circuit_model as cm
    pub = os.path.join(os.path.dirname(HERE), "examples", "flow", "public")
    vi = pinlist.read_verilog(os.path.join(pub, "i2c_gpio_expander_SG13G2Top.v"), "exp")
    names = [p.pad for p in vi.pins]
    tc.check("Verilog: the public IHP top has 16 ports, clock and I2C among them",
             len(names) == 16 and {"io_clock_PAD", "io_i2c_scl_PAD", "io_i2c_sda_PAD"} <= set(names), names)
    vt = pinlist.read_verilog("module m(input [1:0] a, output y); endmodule", "d")
    tc.check("Verilog: text with a bus expands", [p.pad for p in vt.pins] == ["a_0", "a_1", "y"], [p.pad for p in vt.pins])
    try:
        import skrf
    except ImportError:
        tc.skip("public Touchstone", "scikit-rf missing")
        return
    meas = skrf.Network(os.path.join(pub, "msl_measured", "P1-MSL_Stepped_140-P2.s2p"))[::50]
    try:
        cm.fit(meas)
        tc.check("circuit model: refuses the measured 140 mm line over 0-10 GHz", False)
    except cm.FitError:
        tc.check("circuit model: refuses the measured 140 mm line over 0-10 GHz", True)
    r = cm.fit(meas, fmax=2e9)[1]
    tc.check("circuit model: the same line up to 2 GHz fits passively", r["passive"] and r["rms_error"] < 0.01, r)


def _die_names(tc):
    """A die whose GDS was renamed (7-Zip unpacks IHP's archive as
    FMD_QNC_SG13G2Top.gds) is still found when it is the only one left."""
    import gzip
    from types import SimpleNamespace as NS
    from pkgstudio.app import model, netlist
    from pkgstudio.core import pinlist
    intent = pinlist.read_csv("die,pad,net\ni2c_gpio_expander_SG13G2Top,a,A\n", "t")
    ihp = NS(Label="FMD_QNC_SG13G2Top", Cell="SG13G2Top", GdsPath="x/FMD_QNC_SG13G2Top.gds")
    lna = NS(Label="ALL_LNA", Cell="", GdsPath="")
    tc.check("die names: the only chip is taken for the only die", netlist.match_dies(intent, [ihp]) ==
             {"i2c_gpio_expander_SG13G2Top": ihp} and netlist.guessed(intent, [ihp]))
    two = pinlist.read_csv("die,pad,net\ni2c_gpio_expander_SG13G2Top,a,A\nALL_LNA,b,B\n", "t")
    tc.check("die names: by elimination in an MCM, the named one by name",
             netlist.match_dies(two, [lna, ihp]) == {"ALL_LNA": lna, "i2c_gpio_expander_SG13G2Top": ihp})
    tc.check("die names: two unknown chips are not guessed",
             netlist.match_dies(intent, [ihp, NS(Label="other", Cell="", GdsPath="")]) == {})
    d = tmpdir("plain_gds")
    gz = os.path.join(d, "die_SG13G2Top.gds.gz")
    with gzip.open(gz, "wb") as f:
        f.write(b"GDS")
    out = model.plain_gds(gz)
    tc.check("gz GDS: unpacked beside it under the archive's name",
             out == gz[:-3] and open(out, "rb").read() == b"GDS", out)


def _expander_board(tc):
    """examples/expander_board: the routed KiCad board reads back, and the
    QFN-24 4x4 land pattern keeps 0.2 mm between lands (its corner lands
    touched before)."""
    from pkgstudio.core import elements as el, jedec, pcb
    root = os.path.dirname(HERE)
    b = kp.read(os.path.join(root, "examples", "expander_board", "kicad", "expander_eval.kicad_pcb"))
    names = set(b.nets.values())
    tc.check("expander board: 48 footprints, 4 copper layers, the clock and I2C nets",
             len(b.footprints) == 48 and len(b.copper_layers()) == 4
             and {"CLK_50M", "SCL", "SDA", "+1V2", "GND"} <= names, (len(b.footprints), sorted(names)))
    tc.check("expander board: routed (tracks on CLK_50M)",
             any(b.nets.get(t["net"]) == "CLK_50M" or t["net"] == "CLK_50M" for t in b.tracks))
    for name in ("QFN-24 4x4", "QFN-40 6x6", "QFN-16 4x4"):
        lands, _ep = pcb.qfn_lands(el.from_spec(jedec.by_name(name)))
        rects = [r for r, _s in lands]
        gaps = []
        for i, a in enumerate(rects):
            for c in rects[i + 1:]:
                dx = max(c[0] - a[2], a[0] - c[2], 0.0)
                dy = max(c[1] - a[3], a[1] - c[3], 0.0)
                gaps.append((dx * dx + dy * dy) ** 0.5)
        tc.check(f"{name} lands: >= 0.199 mm apart", min(gaps) >= 0.199, round(min(gaps), 4))


def _lna_board(tc):
    """examples/lna_board: the analog RF board as written by make_board.py."""
    root = os.path.dirname(HERE)
    folder = os.path.join(root, "examples", "lna_board", "kicad")
    b = kp.read(os.path.join(folder, "lna_eval.kicad_pcb"))
    names = set(b.nets.values())
    tc.check("LNA board: 25 footprints, 2 copper layers, RF and bias nets",
             len(b.footprints) == 25 and len(b.copper_layers()) == 2
             and {"RF_IN", "RF_OUT", "RF_IN_J", "VCC", "IDC", "GND", "THRU_M"} <= names,
             (len(b.footprints), len(b.copper_layers()), sorted(names)))
    text = open(os.path.join(folder, "lna_eval.kicad_pcb"), encoding="utf-8").read()
    tc.check("LNA board: RO4350B 0.508 mm, Dk 3.66", '(material "RO4350B")' in text and "(thickness 0.508)" in text
             and "(epsilon_r 3.66)" in text)
    rf = [t for t in b.tracks if (b.nets.get(t["net"], t["net"]) in ("RF_IN", "RF_IN_J"))]
    tc.check("LNA board: the input line is 0.85 mm GCPW", any(abs(t.get("w", 0) - 0.85) < 1e-6 for t in rf),
             [t.get("w") for t in rf])
    pins = open(os.path.join(root, "examples", "lna_board", "lna_pinlist.csv"), encoding="utf-8").read()
    tc.check("LNA pin list: RFIN on lead 4 between GND14 (3) and GND15 (5)",
             "ALL_LNA,RFIN,RF_IN,rf,4" in pins and "ALL_LNA,GND14,GND,ground,3" in pins
             and "ALL_LNA,GND15,GND,ground,5" in pins)
    # the 2x-thru is exactly twice the fixture: SMA to reference plane 17.75 mm, thru 35.5 mm
    import importlib.util
    spec = importlib.util.spec_from_file_location("lna_make_board_consts",
                                                  os.path.join(root, "examples", "lna_board", "make_board.py"))
    src = open(spec.origin, encoding="utf-8").read()
    tc.check("LNA 2x-thru: 35.5 mm = 2 x (117.75 - 100)", "NOTCH_X = 102.25, 137.75" in src
             and "X_LAND_L, X_LAND_R = 117.75, 122.25" in src)


def _em_settings_and_paths(tc):
    """The EM panel's choices kept in the document; a chip's GDS and
    technology found again in this Package Studio when the document was
    saved on another computer (a file shipped in the repository)."""
    import FreeCAD
    from pkgstudio.app import em_settings, model
    doc = FreeCAD.newDocument("EmSettings")
    try:
        tc.check("EM settings: none stored -> {}", em_settings.load(doc) == {})
        em_settings.save(doc, solver="openEMS", fmin_ghz=0.5, fmax_ghz=6.0, mesh="coarse", min_cell_um=50.0)
        got = em_settings.load(doc)
        tc.check("EM settings: stored and read back", got == {"solver": "openEMS", "fmin_ghz": 0.5, "fmax_ghz": 6.0,
                                                             "mesh": "coarse", "min_cell_um": 50.0}, got)
        em_settings.save(doc, fmax_ghz=8.0)
        tc.check("EM settings: one object, updated in place",
                 len(model.by_role(doc, em_settings.ROLE)) == 1 and em_settings.load(doc)["fmax_ghz"] == 8.0)
    finally:
        FreeCAD.closeDocument(doc.Name)
    root = os.path.dirname(HERE)
    elsewhere = "/somewhere/else/PackageStudio/technologies/IHP-SG13G2"
    tc.check("a technology path from another computer is found in this Package Studio",
             model.relocated(elsewhere) == os.path.join(root, "technologies", "IHP-SG13G2"), model.relocated(elsewhere))
    win = r"C:\Users\x\AppData\Roaming\FreeCAD\Mod\PackageStudio\examples\data\ALL_LNA.gds"
    tc.check("... a Windows path to an example GDS too",
             model.relocated(win) == os.path.join(root, "examples", "data", "ALL_LNA.gds"), model.relocated(win))
    tc.check("... and a path that exists stays as it is", model.relocated(__file__) == __file__)
    shipped = os.path.join(root, "examples", "lna_board", "lna_rf_em.FCStd")
    tc.check("the ready-to-open LNA EM model is in the repository", os.path.isfile(shipped))


def _mesh_lines_merge(tc):
    """Forced mesh lines a float's rounding apart are one line (a port edge
    at -0.42500000000001 next to its trace at -0.425 made a 1e-14 mm cell)."""
    import numpy as np
    from pkgstudio.core import otter_path
    otter_path.ensure()
    try:
        import otter_llm.mesh as M
    except ImportError:
        tc.skip("mesh lines merge", "OTTER not available")
        return
    lines = np.array([-1.0, -0.425, 0.0, 0.425, 1.0])
    boxes = [[-0.425, 0, 0, 0.425, 1, 1]]
    out = M.thin_lines([lines, lines, lines], [0.05, 0.05, 0.05], boxes,
                       [[-0.42500000000001, 0, 0], [0.4250000000000109, 1, 1]])[0]
    tc.check("mesh: -0.42500000000001 and -0.425 are one line", float(np.min(np.diff(out))) > 1e-6, list(out))


def _zone_rings(tc):
    """KiCad stores a fill with holes as one outline with slits: the outline
    and each hole come back as rings of their own (the In1 ground under U1
    was lost before)."""
    from pkgstudio.core import kicad_board as kb
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    # into the hole at (4,4)-(6,6) along a slit from (0,5) to (4,5), round it, back out
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 5), (4, 5), (4, 6), (6, 6), (6, 4), (4, 4), (4, 5), (0, 5)]
    rings = kb._rings(pts)
    areas = sorted(abs(kb._ring_area(r)) for r in rings)
    tc.check("zone fill: outline and hole apart (areas 4 and 100)", len(rings) == 2 and areas == [4.0, 100.0], rings)
    tc.check("zone fill without holes: one ring", len(kb._rings(outer)) == 1)
    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        return
    f = kb._face(pts)
    tc.near("zone fill: face area 100 - 4 = 96", f.Area, 96.0, 1e-9)


def _chunked_mesh(tc):
    """The generated openEMS script's size() in chunks gives the same lines."""
    import numpy as np
    from pkgstudio.app import em_project as ep
    helper = ("def meshGradedLines(fixed, regions, maxRes, ratio=1.3, mergeRel=0.5):\n"
              "\tfixed = np.unique(np.round(np.asarray(fixed, dtype=float), 9))\n"
              "\tg = ratio - 1\n"
              "\ta = np.array([r[0] for r in regions])[None]\n"
              "\tb = np.array([r[1] for r in regions])[None]\n"
              "\th = np.array([r[2] for r in regions])[None]\n" + ep._SIZE_OLD +
              "\treturn size(np.linspace(fixed[0], fixed[-1], 50001))\n")
    d = tmpdir("chunked")
    path = os.path.join(d, "s.py")
    with open(path, "w") as fh:
        fh.write(helper)
    g1 = {"np": np}
    exec(helper, g1)
    tc.check("chunked size(): the script is changed", ep.lighten_script(path) and not ep.lighten_script(path))
    g2 = {"np": np}
    exec(open(path).read(), g2)
    rng = np.random.default_rng(3)
    regions = [(x, x + 1.0, 0.05 + 0.1 * k / 40) for k, x in enumerate(rng.uniform(-5, 5, 40))]
    tc.check("chunked size(): the same numbers", np.array_equal(g1["meshGradedLines"]([-9, 9], regions, 2.0),
                                                                g2["meshGradedLines"]([-9, 9], regions, 2.0)))


def _kicad_board(tc):
    from pkgstudio.core import kicad_board
    ps, rep = kicad_board.pieces(kp.read(SYNTH), centre=False)
    core = next(p for p in ps if p.props.get("KicadLayer") == "dielectric 1")
    tc.near("KiCad board: the core's volume, the hole cut (48.7573 mm³)", core.shape.Volume,
            (200 - math.pi * 1.6 ** 2) * 0.254, 1e-6)
    tc.check("KiCad board: RO4350B mapped, FR4 mapped", core.material == "Rogers RO4350B"
             and next(p for p in ps if p.props.get("KicadLayer") == "dielectric 2").material == "FR-4")
    land = next(p for p in ps if p.props.get("Land"))
    bb = land.shape.BoundBox
    tc.check("KiCad board: U1's pad is a land at (10, -19), turned (0.3 x 0.5), on F.Cu",
             abs(bb.Center.x - 10) < 1e-9 and abs(bb.Center.y + 19) < 1e-9 and abs(bb.XLength - 0.3) < 1e-9
             and abs(bb.YLength - 0.5) < 1e-9 and abs(bb.ZMin - 1.524) < 1e-9, (bb.Center, bb.XLength, bb.YLength))
    ps2, _r = kicad_board.pieces(kp.read(SYNTH), region=(1, -6, 6, -4), centre=True)
    rf = [p for p in ps2 if p.props.get("Net") == "RF" and p.props.get("KicadLayer") == "F.Cu"]
    # the line runs from x = 2 (round end, r 0.22) to 12; the region 1..6 keeps 1.78..6 = 4.22 mm,
    # centred on the region's 3.5: -1.72 .. 2.5
    tc.check("KiCad board: a region cuts the line to 4.22 mm, the region centred",
             rf and abs(rf[0].shape.BoundBox.XLength - 4.22) < 1e-6 and abs(rf[0].shape.BoundBox.XMax - 2.5) < 1e-6,
             [p.shape.BoundBox for p in rf])
    ps3, rep3 = kicad_board.pieces(os.path.join(KDATA, "basic_test.kicad_pcb"))
    tc.check("KiCad board from a real KiCad 7 file: built, 9 footprints", rep3["footprints"] == 9 and len(ps3) > 10,
             rep3)


def _roundtrip(tc):
    """Our footprint in a KiCad board, read back, a housing put on it: every lead over its land."""
    import FreeCAD
    from pkgstudio.app import boards, housings, kicad, model
    from pkgstudio.core import jedec
    doc = FreeCAD.newDocument("kicad_roundtrip")
    try:
        h = housings.from_jedec(doc, jedec.by_name("QFN-32 5x5"))
        folder = tmpdir("kicad_rt")
        mod, _sym, _step = kicad.export_package(h, folder, lib="RT")
        fp = kp.parse(open(mod).read())
        pads = "\n".join("    " + _sx(p) for p in kp.findall(fp, "pad"))
        board = f"""(kicad_pcb (version 20240108) (generator "test")
  (general (thickness 1.6))
  (net 0 "")
  (gr_rect (start 40 40) (end 60 60) (layer "Edge.Cuts"))
  (footprint "RT:QFN" (layer "F.Cu") (at 50 50)
    (property "Reference" "U1")
{pads}))
"""
        path = os.path.join(folder, "rt.kicad_pcb")
        with open(path, "w") as fh:
            fh.write(board)
        b, _rep = kicad.import_board(doc, path)
        pkg = model.assembly_owner(h)
        boards.place_package(pkg, b, "U1")
        lands = {getattr(o, "PadNumber", ""): o for o in boards.pieces(b, "copper", "U1")}
        worst = 0.0
        for lead in model.pads_of(h):
            c = model.pad_centre(lead)
            bb = model.global_bbox(lands[lead.PadName])
            inside = bb.XMin - 1e-6 <= c.x <= bb.XMax + 1e-6 and bb.YMin - 1e-6 <= c.y <= bb.YMax + 1e-6
            worst = max(worst, 0.0 if inside else 1.0)
        tc.check("round trip: exported footprint -> KiCad board -> imported -> housing placed: all 32 leads "
                 "over the land of the same number", worst == 0.0 and len(model.pads_of(h)) == 32)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _sx(node):
    if isinstance(node, list):
        return "(" + " ".join(_sx(x) for x in node) + ")"
    return f'"{node}"' if isinstance(node, kp.Q) else str(node)


def _walkthrough(tc):
    """examples/flow_walkthrough.py on the files in examples/flow/."""
    import importlib.util
    os.environ["PS_EXAMPLE_OUT"] = tmpdir("flow_walkthrough")
    root = os.path.dirname(HERE)
    spec = importlib.util.spec_from_file_location("walk", os.path.join(root, "examples", "flow_walkthrough.py"))
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    import FreeCAD
    doc, r = ex.run()
    try:
        tc.check("walkthrough: the example pin list matches the wires (only unused leads)",
                 all(f.startswith("info") for f in r["lvs"]), r["lvs"])
        rules = {f.split("]")[0].split("[")[1] for f in r["lvs_broken"] if f.startswith("error")}
        tc.check("walkthrough: the broken pin list: wrong lead, unknown pads, open",
                 rules == {"wrong lead", "unknown pad", "open"}, r["lvs_broken"])
        tc.check("walkthrough: the example KiCad board read, and its RF input region",
                 r["kicad_board"]["pieces"] > 40 and 10 < r["kicad_board"]["region_pieces"] < r["kicad_board"]["pieces"])
        if "validation" in r:
            v = r["validation"]
            tc.check("walkthrough: synthetic 'measurement' passes in |S21| (0.21 dB), fails in |S11|",
                     v["S21"]["pass"] and not v["S11"]["pass"] and abs(v["S21"]["max_db"] - 0.21) < 0.02, v["S21"])
            tc.check("walkthrough: circuit model passive", r["circuit_model"]["passive"])
        tc.check("walkthrough: public Verilog top read (16 ports)", r.get("verilog_ports") == 16, r.get("verilog_ports"))
        if "p370_vs_aicc" in r:
            tc.check("walkthrough: public P370 data agrees with the AICC tool (|S21| < 0.1 dB)",
                     r["p370_vs_aicc"]["S21"] < 0.1, r["p370_vs_aicc"])
            tc.check("walkthrough: measured vs simulated thru differs (connectors), 1-2 dB",
                     1.0 < r["msl"]["P1-MSL_Thru_100-P2"]["S21"] < 2.0, r["msl"])
            tc.check("walkthrough: measured stepped line, 0-3 GHz model passive and fitted",
                     r["msl_model"]["passive"] and r["msl_model"]["rms_error"] < 0.01, r["msl_model"])
    finally:
        FreeCAD.closeDocument(doc.Name)


def run():
    tc = TestCase("design-flow")
    tc.guard("pin list", lambda: _pinlist(tc))
    tc.guard("package LVS", lambda: _lvs(tc))
    tc.guard("anchored pairing", lambda: _anchored(tc))
    tc.guard("bonding diagram", lambda: _diagram(tc))
    tc.guard("KiCad read", lambda: _kicad_read(tc))
    tc.guard("KiCad write", lambda: _kicad_write(tc))
    tc.guard("circuit model and validation", lambda: _circuit(tc))
    tc.guard("public files", lambda: _public(tc))
    tc.guard("zone fills with holes", lambda: _zone_rings(tc))
    tc.guard("chunked mesh helper", lambda: _chunked_mesh(tc))
    tc.guard("mesh lines merge", lambda: _mesh_lines_merge(tc))
    try:
        import FreeCAD  # noqa: F401
        tc.guard("die names", lambda: _die_names(tc))
        tc.guard("expander board", lambda: _expander_board(tc))
        tc.guard("LNA board", lambda: _lna_board(tc))
        tc.guard("EM settings and paths", lambda: _em_settings_and_paths(tc))
        tc.guard("KiCad board", lambda: _kicad_board(tc))
        tc.guard("KiCad round trip", lambda: _roundtrip(tc))
        tc.guard("walkthrough", lambda: _walkthrough(tc))
    except ImportError:
        tc.skip("KiCad board", "FreeCAD not available")
    return tc.results
