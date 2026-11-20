# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Makes the example files of examples/flow/ (they are in the repository; run
this only to make them again):

  kicad/FlowExample.pretty/QFN-40_6x6.kicad_mod, kicad/FlowExample.kicad_sym,
  kicad/QFN-40_6x6.step     the LNA's QFN-40 exported by Package Studio
  lna_qfn40_board.kicad_pcb a KiCad board with that footprint: RO4350B over
                            a ground plane, 50-ohm GCPW to RFIN (lead 6) and
                            from RFOUT (lead 25), via fences, ground pours
  touchstone/*.s2p          SYNTHETIC S-parameters (scikit-rf models, not an
                            EM run, not a measurement): a "simulated" package
                            path, a "measured" one with a test fixture and a
                            slightly different wire, and the fixture's 2x-thru

    freecadcmd examples/flow/make_example_files.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import FreeCAD  # noqa: E402

from pkgstudio.app import housings, kicad, model, netlist  # noqa: E402
from pkgstudio.core import jedec, kicad_pcb as kp  # noqa: E402

TECH = os.path.join(ROOT, "technologies", "IHP-SG13G2")
LNA = os.path.join(ROOT, "examples", "data", "ALL_LNA.gds")
W, S = 0.44, 0.20                         # GCPW on 0.254 mm RO4350B (the LNA example's board)
FP_AT = (100.0, 100.0)                    # footprint position, KiCad mm (y down)
EDGE = (85.0, 90.0, 115.0, 110.0)         # board outline


def footprint_and_library():
    doc = FreeCAD.newDocument("flow_files")
    lna = model.import_chip(doc, LNA, TECH)
    h = housings.from_jedec(doc, jedec.by_name("QFN-40 6x6"))
    housings.place_chip(lna, h)
    intent = netlist.load([os.path.join(HERE, "lna_top.sch"), os.path.join(HERE, "lna_pinlist.csv")],
                          {os.path.join(HERE, "lna_top.sch"): "ALL_LNA"})
    netlist.assign(h, intent)
    found, nets = netlist.lvs(h, intent)
    kinds = {p.net: p.kind for p in intent.pins}
    paths = kicad.export_package(h, os.path.join(HERE, "kicad"), "QFN-40_6x6", lib="FlowExample", kinds=kinds)
    FreeCAD.closeDocument(doc.Name)
    return paths, nets


def board(mod_path, nets):
    fp = kp.parse(open(mod_path).read())
    names = ["", "GND", "RF_IN", "RF_OUT"] + sorted({n for n in nets.values()} - {"GND", "RF_IN", "RF_OUT"})
    num = {n: i for i, n in enumerate(names)}
    pads = {}
    pad_text = []
    for p in kp.findall(fp, "pad"):
        no = str(p[1])
        at, size = kp.find(p, "at"), kp.find(p, "size")
        x, y, w, h = float(at[1]), float(at[2]), float(size[1]), float(size[2])
        pads[no] = (FP_AT[0] + x, FP_AT[1] + y, w, h)
        net = "GND" if no == "41" else nets.get(no, "")
        extra = f' (net {num[net]} "{net}")' if net else ""
        paste = " (solder_paste_margin_ratio -0.2)" if no == "41" else ""
        pad_text.append(f'    (pad "{no}" smd rect (at {x} {y}) (size {w} {h}) '
                        f'(layers "F.Cu" "F.Paste" "F.Mask"){paste}{extra})')
    x0, y0, x1, y1 = EDGE
    yin, yout = pads["6"][1], pads["25"][1]
    toe_in = pads["6"][0] - pads["6"][2] / 2
    toe_out = pads["25"][0] + pads["25"][2] / 2
    out = ['(kicad_pcb (version 20240108) (generator "pkgstudio_example") (generator_version "1.0")',
           "  (general (thickness 1.559) (legacy_teardrops no))",
           '  (paper "A4")',
           '  (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (31 "B.Cu" signal) (34 "B.Paste" user) '
           '(35 "F.Paste" user) (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user) '
           '(46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard") (49 "F.Fab" user) '
           '(37 "F.SilkS" user "F.Silkscreen"))',
           "  (setup",
           "    (stackup",
           '      (layer "F.Cu" (type "copper") (thickness 0.035))',
           '      (layer "dielectric 1" (type "core") (thickness 0.254) (material "RO4350B") (epsilon_r 3.66) '
           "(loss_tangent 0.0037))",
           '      (layer "In1.Cu" (type "copper") (thickness 0.035))',
           '      (layer "dielectric 2" (type "prepreg") (thickness 1.2) (material "FR4") (epsilon_r 4.5) '
           "(loss_tangent 0.02))",
           '      (layer "B.Cu" (type "copper") (thickness 0.035))',
           "      (copper_finish \"ENIG\") (dielectric_constraints no))",
           "    (pad_to_mask_clearance 0))"]
    out += [f'  (net {i} "{n}")' for i, n in enumerate(names)]
    out.append(f'  (gr_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.1) (type default)) (fill none) '
               '(layer "Edge.Cuts"))')
    out.append(f'  (footprint "FlowExample:QFN-40_6x6" (layer "F.Cu") (at {FP_AT[0]} {FP_AT[1]})')
    out.append('    (property "Reference" "U1" (at 0 -4.5 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
    out.append('    (property "Value" "LNA QFN-40" (at 0 4.5 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
    out += pad_text
    out.append("  )")
    out.append(f'  (segment (start {x0} {yin}) (end {pads["6"][0]} {yin}) (width {W}) (layer "F.Cu") (net {num["RF_IN"]}))')
    out.append(f'  (segment (start {pads["25"][0]} {yout}) (end {x1} {yout}) (width {W}) (layer "F.Cu") '
               f'(net {num["RF_OUT"]}))')
    g = W / 2 + S

    def zone(layer, pts):
        p = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
        return (f'  (zone (net {num["GND"]}) (net_name "GND") (layer "{layer}") (hatch edge 0.5) '
                f"(connect_pads (clearance 0.2)) (min_thickness 0.2) (fill yes (thermal_gap 0.3) "
                f"(thermal_bridge_width 0.3)) (polygon (pts {p})) (filled_polygon (layer \"{layer}\") (pts {p})))")
    stop_in, stop_out = toe_in - 0.4, toe_out + 0.4
    for (xa, xb, yl) in ((x0, stop_in, yin), (stop_out, x1, yout)):
        out.append(zone("F.Cu", [(xa, y0), (xb, y0), (xb, yl - g), (xa, yl - g)]))
        out.append(zone("F.Cu", [(xa, yl + g), (xb, yl + g), (xb, y1), (xa, y1)]))
    out.append(zone("In1.Cu", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
    vias = []
    for (xa, xb, yl) in ((x0 + 0.6, stop_in - 0.3, yin), (stop_out + 0.3, x1 - 0.6, yout)):
        n = int((xb - xa) / 1.0)
        for k in range(n + 1):
            x = xa + (xb - xa) * k / max(n, 1)
            for y in (yl - g - 0.5, yl + g + 0.5):
                vias.append((x, y))
    for dx in (-1.0, 1.0):
        for dy in (-1.0, 1.0):
            vias.append((FP_AT[0] + dx, FP_AT[1] + dy))
    out += [f'  (via (at {x:.4f} {y:.4f}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net {num["GND"]}))'
            for x, y in vias]
    out.append(")")
    path = os.path.join(HERE, "lna_qfn40_board.kicad_pcb")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def touchstones():
    import numpy as np
    import skrf
    from skrf.media import DefinedGammaZ0
    f = skrf.Frequency(0.1, 10, 400, "GHz")
    m = DefinedGammaZ0(f, z0=50, gamma=(0.02 + 1j) * 2 * np.pi * f.f / 3e8 * 1.9)

    def path(L, C):          # board line, bond wire, pad, line
        return m.line(4e-3, "m") ** m.inductor(L) ** m.shunt_capacitor(C) ** m.line(2e-3, "m")
    sim = path(1.0e-9, 0.15e-12)
    dut = path(1.08e-9, 0.17e-12)            # "the real part": a slightly longer wire, a bigger pad
    rng = np.random.default_rng(7)
    noise = (rng.normal(0, 0.002, dut.s.shape) + 1j * rng.normal(0, 0.002, dut.s.shape))
    dut.s = dut.s + (noise + noise.transpose(0, 2, 1)) / 2     # small, reciprocal measurement noise
    fix = m.line(12e-3, "m")                  # connector-to-package launch: a plain 12 mm line
    out = os.path.join(HERE, "touchstone")
    os.makedirs(out, exist_ok=True)
    note = "SYNTHETIC example data (scikit-rf model) - not an EM result, not a measurement"
    for name, nw, what in (("lna_pkg_sim", sim, "'simulated' package path: 4 mm line, 1.00 nH, 0.15 pF, 2 mm line"),
                           ("lna_pkg_meas", fix ** dut ** fix.flipped(),
                            "'measured' DUT in its fixture: 12 mm line each side, 1.08 nH, 0.17 pF, noise"),
                           ("lna_2xthru", fix ** fix.flipped(), "the fixture's 2x-thru: 24 mm line")):
        nw.comments = f"{note}\n{what}"
        nw.write_touchstone(os.path.join(out, name), r_ref=50)
    return out


if __name__ == "__main__" or "__file__" in globals():
    lib, nets = footprint_and_library()
    print(lib)
    print(board(lib[0], nets))
    print(touchstones())
