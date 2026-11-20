# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 2 of the LNA evaluation board: the KiCad project around the QFN-24 of
step 1 (make_package.py).

  kicad/lna_eval.kicad_sch, lna_eval.kicad_pcb, lna_eval.kicad_pro
  kicad/LNAEval.pretty, LNAEval.kicad_sym, QFN-24_4x4_LNA.step

The board is an RF board, laid out as such:

  - Rogers RO4350B, 0.508 mm, two layers, 35 um copper, ENIG. Top: signals
    and a ground pour; bottom: one unbroken ground plane (layer type
    "power": nothing is routed on it).
  - RF in and out: grounded coplanar waveguide (GCPW), 50 ohm: w = 0.85 mm,
    gap s = 0.20 mm (the top pour's clearance), on 0.508 mm RO4350B
    (design Dk 3.66): eps_eff 2.57. Via fences 1.0 mm off the line centre,
    1.0 mm pitch. Edge-launch SMA (Samtec SMA-J-P-X-ST-EM1, 0-20 GHz) at
    each end, the line straight into the QFN's RF lead, ground-signal-ground
    to leads 3/4/5 and 14/15/16.
  - 10 pF C0G DC blocks (0603) in both RF lines, 8 mm from the board edge;
    a 0402 shunt-capacitor footprint 1.55 mm before each RF lead, not
    fitted: there a capacitor of about L_wire/Z0^2 (~0.6 pF for 1.5 nH)
    turns the bond wire into a low-pass 50-ohm section, if the EM
    simulation says it is needed.
  - Bias, not regulated on the board (VCC and the bias current are open):
    VCC from header J3 through a ferrite bead, 10 uF / 1 uF / 10 nF / 100 pF
    to ground, the 100 pF at the lead; IDC through 100 ohm with 10 nF to
    ground (an RC filter that keeps the lab supply's noise off the bias);
    the heater pins straight to J3.
  - A 2x-thru on its own strip at the top: two SMAs, the same GCPW, the same
    DC blocks and shunt footprints, exactly twice the fixture from SMA to
    the reference plane at the QFN's lands (2 x 17.75 mm). Measured with a
    VNA it de-embeds the board from the LNA measurement (IEEE P370, the
    "Compare with measurement" command).

Run with KiCad's Python (pcbnew must import), after make_package.py:
    python3 examples/lna_board/make_board.py
"""

import json
import math
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "expander_board"))
import make_board as kit  # noqa: E402  (the expander board's KiCad helpers)

PKG = os.path.join(HERE, "out", "package")
kit.PKG = PKG
OUT = os.path.join(HERE, "kicad")
NAME = "lna_eval"
U1_FP = "QFN-24_4x4_LNA"
LIB = "LNAEval"
uid = kit.uid

# geometry, KiCad millimetres (x right, y down)
W_RF, GAP, FENCE, PITCH = 0.85, 0.20, 1.0, 1.0
U1_AT = (120.0, 112.0)
Y_RF = 112.25                       # lead 4 and lead 15 (pad centres at y = +0.25)
X_LAND_L, X_LAND_R = 117.75, 122.25  # the outer ends of the RF lands: the reference planes
X_PAD_L, X_PAD_R = 118.1875, 121.8125
Y_THRU = 103.0
EDGE = (100.0, 100.0, 140.0, 130.0)
NOTCH_X = 102.25, 137.75             # the 2x-thru strip: 35.5 mm = 2 x 17.75 mm
NOTCH_Y = 106.0
X_VCC, X_IDC, Y_J3 = 117.21, 119.75, 127.3   # the bias lines run straight down to the header J3
SMA = "Connector_Coaxial:SMA_Samtec_SMA-J-P-X-ST-EM1_EdgeMount"
C0603, C0402, C0805 = "Capacitor_SMD:C_0603_1608Metric", "Capacitor_SMD:C_0402_1005Metric", \
    "Capacitor_SMD:C_0805_2012Metric"
R0402, FB0603 = "Resistor_SMD:R_0402_1005Metric", "Inductor_SMD:L_0603_1608Metric"
HDR5 = "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical"
HOLE = "MountingHole:MountingHole_2.2mm_M2"

U1_PINS = {"3": "GND", "4": "RF_IN", "5": "GND", "7": "GND", "8": "VCC", "9": "IDC", "10": "IHEAT",
           "11": "GNDHEAT", "12": "GND", "14": "GND", "15": "RF_OUT", "16": "GND", "18": "GND", "21": "GND",
           "22": "GND", "24": "GND", "25": "GND"}

# ref: (symbol, value, footprint, {pin: net}, schematic (x, y, rot), pcb (x, y, rot), dnp)
PARTS = {
    "U1": ("LNAEval:" + U1_FP, "ALL_LNA", "LNAEval:" + U1_FP, U1_PINS, (190.5, 101.6, 0), (*U1_AT, 0), False),
    # RF input
    "J1": ("Connector:Conn_Coaxial", "SMA RF in", SMA, {"1": "RF_IN_J", "2": "GND"}, (40.64, 101.6, 0),
           (100.0, Y_RF, 270), False),
    "C1": ("Device:C", "10p C0G", C0603, {"1": "RF_IN_J", "2": "RF_IN"}, (76.2, 96.52, 90), (108.0, Y_RF, 0), False),
    "C3": ("Device:C", "0.6p (DNP)", C0402, {"1": "RF_IN", "2": "GND"}, (106.68, 111.76, 0),
           (X_LAND_L - 1.55, Y_RF + 0.48, 270), True),
    # RF output
    "J2": ("Connector:Conn_Coaxial", "SMA RF out", SMA, {"1": "RF_OUT_J", "2": "GND"}, (340.36, 101.6, 180),
           (140.0, Y_RF, 90), False),
    "C2": ("Device:C", "10p C0G", C0603, {"1": "RF_OUT", "2": "RF_OUT_J"}, (304.8, 96.52, 90), (132.0, Y_RF, 0), False),
    "C4": ("Device:C", "0.6p (DNP)", C0402, {"1": "RF_OUT", "2": "GND"}, (274.32, 111.76, 0),
           (X_LAND_R + 1.55, Y_RF + 0.48, 270), True),
    # bias
    "C5": ("Device:C", "100p", C0402, {"1": "VCC", "2": "GND"}, (124.46, 167.64, 0), (X_VCC - 0.48, 116.9, 180), False),
    "C6": ("Device:C", "10n", C0402, {"1": "VCC", "2": "GND"}, (137.16, 167.64, 0), (X_VCC - 0.48, 118.0, 180), False),
    "C7": ("Device:C", "1u", C0603, {"1": "VCC_IN", "2": "GND"}, (99.06, 167.64, 0), (X_VCC - 0.775, 122.3, 180), False),
    "C13": ("Device:C", "10u", C0805, {"1": "VCC_IN", "2": "GND"}, (86.36, 167.64, 0), (X_VCC - 0.95, 124.3, 180), False),
    "FB1": ("Device:FerriteBead", "600R@100MHz", FB0603, {"1": "VCC_IN", "2": "VCC"}, (111.76, 160.02, 90),
            (X_VCC, 120.0, 90), False),
    "R1": ("Device:R", "100R", R0402, {"1": "IDC_IN", "2": "IDC"}, (165.1, 160.02, 90), (X_IDC, 119.5, 90), False),
    "C8": ("Device:C", "10n", C0402, {"1": "IDC", "2": "GND"}, (177.8, 167.64, 0), (X_IDC - 0.48, 116.4, 180), False),
    "J3": ("Connector_Generic:Conn_01x05", "VCC IDC IHEAT GNDHEAT GND", HDR5,
           {"1": "VCC_IN", "2": "IDC_IN", "3": "IHEAT", "4": "GNDHEAT", "5": "GND"}, (226.06, 165.1, 0),
           (X_VCC, Y_J3, 90), False),
    # 2x-thru: the fixture twice, SMA to SMA
    "J4": ("Connector:Conn_Coaxial", "SMA thru A", SMA, {"1": "THRU_A", "2": "GND"}, (40.64, 50.8, 0),
           (NOTCH_X[0], Y_THRU, 270), False),
    "C9": ("Device:C", "10p C0G", C0603, {"1": "THRU_A", "2": "THRU_M"}, (76.2, 45.72, 90),
           (NOTCH_X[0] + 8.0, Y_THRU, 0), False),
    "C11": ("Device:C", "0.6p (DNP)", C0402, {"1": "THRU_M", "2": "GND"}, (106.68, 60.96, 0),
            (NOTCH_X[0] + 17.75 - 1.55, Y_THRU + 0.48, 270), True),
    "C12": ("Device:C", "0.6p (DNP)", C0402, {"1": "THRU_M", "2": "GND"}, (274.32, 60.96, 0),
            (NOTCH_X[1] - 17.75 + 1.55, Y_THRU + 0.48, 270), True),
    "C10": ("Device:C", "10p C0G", C0603, {"1": "THRU_M", "2": "THRU_B"}, (304.8, 45.72, 90),
            (NOTCH_X[1] - 8.0, Y_THRU, 0), False),
    "J5": ("Connector:Conn_Coaxial", "SMA thru B", SMA, {"1": "THRU_B", "2": "GND"}, (340.36, 50.8, 180),
           (NOTCH_X[1], Y_THRU, 90), False),
}
HOLES = {"H1": (103.0, 127.0), "H2": (137.0, 127.0), "H3": (103.5, 121.5), "H4": (136.5, 121.5)}
FLAGS = {"#FLG01": ("GND", (50.8, 152.4)), "#FLG02": ("VCC", (63.5, 152.4)), "#FLG03": ("GNDHEAT", (76.2, 152.4))}
RF_NETS = ("RF_IN_J", "RF_IN", "RF_OUT", "RF_OUT_J", "THRU_A", "THRU_M", "THRU_B")
POWER_NETS = ("VCC", "VCC_IN", "IHEAT", "GNDHEAT", "GND")

NOTES = [((20.32, 25.4), "LNA evaluation board: RO4350B 0.508 mm, GCPW 50 ohm (w 0.85, gap 0.20 mm), edge SMA"),
         ((20.32, 33.0), "2x-thru (J4-J5): the RF fixture twice, for de-embedding (IEEE P370)"),
         ((20.32, 83.82), "RF: DC blocks 10 pF C0G; C3/C4 shunt (not fitted): bond-wire compensation ~L/Z0^2"),
         ((20.32, 142.24), "Bias from the lab: VCC via ferrite + 10u/1u/10n/100p, IDC via 100R/10n RC filter, heater direct")]


def write_schematic(path):
    root = uid("lna-sheet")
    used = sorted({p[0] for p in PARTS.values()} | {"power:PWR_FLAG", "Mechanical:MountingHole"})
    font = "(effects (font (size 1.27 1.27))"
    out = [f'(kicad_sch (version 20230121) (generator eeschema)', f'  (uuid {root})', '  (paper "A3")',
           '  (title_block (title "ALL_LNA evaluation board") (rev "A") (company "Package Studio example") '
           '(comment 1 "ALL_LNA (SG13G2) in QFN-24 4x4, RO4350B 0.508 mm, GCPW 50 ohm") '
           '(comment 2 "VCC and the bias current from the lab supply: not regulated on the board"))',
           "  (lib_symbols"]
    out += ["    " + kit.flat_symbol(s) for s in used]
    out.append("  )")
    parts = {k: v for k, v in PARTS.items()}
    for ref, (net, xy) in FLAGS.items():
        parts[ref] = ("power:PWR_FLAG", "PWR_FLAG", "", {"1": net}, (xy[0], xy[1], 0), None, False)
    for k, (ref, xy) in enumerate(HOLES.items()):
        parts[ref] = ("Mechanical:MountingHole", "M2", HOLE, {}, (300.0 + 12.7 * k, 160.02, 0), None, False)
    for ref, (lib_id, value, fp, pins, sch, _pcb, dnp) in sorted(parts.items()):
        x, y, rot = sch
        hidden = ref.startswith("#")
        out.append(f'  (symbol (lib_id "{lib_id}") (at {x:.2f} {y:.2f} {rot}) (unit 1) (in_bom {"no" if hidden else "yes"}) '
                   f'(on_board {"no" if hidden else "yes"}) (dnp {"yes" if dnp else "no"}) (uuid {uid("sym", ref)})')
        out.append(f'    (property "Reference" "{ref}" (at {x:.2f} {y - 6.35:.2f} 0) {font}{" hide" if hidden else ""}))')
        out.append(f'    (property "Value" "{value}" (at {x:.2f} {y + 6.35:.2f} 0) {font}))')
        out.append(f'    (property "Footprint" "{fp}" (at {x:.2f} {y:.2f} 0) {font} hide))')
        out.append(f'    (property "Datasheet" "" (at {x:.2f} {y:.2f} 0) {font} hide))')
        allpins = kit.symbol_pins(lib_id)
        for num in allpins:
            out.append(f'    (pin "{num}" (uuid {uid("pin", ref, num)}))')
        out.append(f'    (instances (project "{NAME}" (path "/{root}" (reference "{ref}") (unit 1))))')
        out.append("  )")
        for num, (px, py, ang, ptype) in allpins.items():
            rx, ry = kit._rot(px, py, rot)
            sx, sy = x + rx, y - ry
            da = math.radians(ang + rot)
            ox, oy = -math.cos(da), math.sin(da)
            net = pins.get(num)
            if net is None:
                if ptype != "no_connect":
                    out.append(f'  (no_connect (at {sx:.2f} {sy:.2f}) (uuid {uid("nc", ref, num)}))')
                continue
            ex, ey = sx + 2.54 * ox, sy + 2.54 * oy
            out.append(f'  (wire (pts (xy {sx:.2f} {sy:.2f}) (xy {ex:.2f} {ey:.2f})) (stroke (width 0) (type default)) '
                       f'(uuid {uid("w", ref, num)}))')
            ang_l = {(-1, 0): 180, (1, 0): 0, (0, -1): 90, (0, 1): 270}[(round(ox), round(oy))]
            just = "right" if ang_l in (180, 270) else "left"
            out.append(f'  (label "{net}" (at {ex:.2f} {ey:.2f} {ang_l}) (fields_autoplaced) '
                       f'(effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {uid("l", ref, num)}))')
    for (x, y), t in NOTES:
        out.append(f'  (text "{t}" (at {x:.2f} {y:.2f} 0) (effects (font (size 1.5 1.5)) (justify left bottom)) '
                   f'(uuid {uid("t", t)}))')
    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return root


STACKUP = """    (stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Paste" (type "Top Solder Paste"))
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 0.508) (material "RO4350B") (epsilon_r 3.66) (loss_tangent 0.0037))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (layer "B.Paste" (type "Bottom Solder Paste"))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "ENIG")
      (dielectric_constraints no)
    )
"""


def write_project(path):
    classes = []
    for name, width, clearance in (("Default", 0.25, 0.2), ("RF", W_RF, GAP), ("Power", 0.4, 0.2)):
        classes.append({"name": name, "clearance": clearance, "track_width": width, "via_diameter": 0.6,
                        "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2,
                        "diff_pair_gap": 0.25, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
                        "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6, "bus_width": 12})
    pro = {"board": {"design_settings": {"defaults": {"board_outline_line_width": 0.1},
                                         "rules": {"min_clearance": 0.15, "min_track_width": 0.15,
                                                   "min_via_diameter": 0.5, "min_through_hole_diameter": 0.25,
                                                   "min_copper_edge_clearance": 0.0},
                                         "track_widths": [0.0, 0.25, 0.4, W_RF],
                                         "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                                            {"diameter": 0.6, "drill": 0.3}]}},
           "meta": {"filename": f"{NAME}.kicad_pro", "version": 1},
           "net_settings": {"classes": classes, "meta": {"version": 3}, "netclass_assignments": None,
                            "netclass_patterns": [{"netclass": "RF", "pattern": n} for n in RF_NETS]
                            + [{"netclass": "Power", "pattern": n} for n in POWER_NETS]},
           "sheets": [[uid("lna-sheet"), ""]], "text_variables": {}}
    with open(path, "w") as fh:
        json.dump(pro, fh, indent=2)


def write_board(path):
    import pcbnew
    mm = pcbnew.FromMM

    def V(x, y):
        return pcbnew.VECTOR2I(mm(x), mm(y))

    tmp = os.path.join(OUT, "_k7")
    os.makedirs(os.path.join(tmp, f"{LIB}.pretty"), exist_ok=True)
    kit.kicad7_footprint(os.path.join(PKG, f"{LIB}.pretty", U1_FP + ".kicad_mod"),
                         os.path.join(tmp, f"{LIB}.pretty", U1_FP + ".kicad_mod"))
    board = pcbnew.NewBoard(path)
    board.SetCopperLayerCount(2)
    board.SetLayerType(pcbnew.B_Cu, pcbnew.LT_POWER)          # the ground plane: nothing routed on it
    board.GetDesignSettings().SetBoardThickness(mm(0.508 + 0.07))
    nets = {}

    def net(name):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]

    fps = {}
    for ref, (lib_id, value, fp_id, pins, _sch, at, dnp) in sorted(PARTS.items()):
        lib, name = fp_id.split(":")
        folder = os.path.join(tmp, f"{lib}.pretty") if lib == LIB else os.path.join(kit.FPDIR, f"{lib}.pretty")
        fp = pcbnew.FootprintLoad(folder, name)
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPath(pcbnew.KIID_PATH(f"/{uid('sym', ref)}"))
        if dnp:
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)
        fp.SetPosition(V(at[0], at[1]))
        fp.SetOrientationDegrees(at[2])
        for pad in fp.Pads():
            n = pins.get(pad.GetNumber())
            if n:
                pad.SetNet(net(n))
        board.Add(fp)
        fps[ref] = fp
    for ref, (x, y) in HOLES.items():
        fp = pcbnew.FootprintLoad(os.path.join(kit.FPDIR, "MountingHole.pretty"), HOLE.split(":")[1])
        fp.SetReference(ref)
        fp.SetPath(pcbnew.KIID_PATH(f"/{uid('sym', ref)}"))
        fp.SetPosition(V(x, y))
        board.Add(fp)

    # outline: the board with a narrower strip on top for the 2x-thru
    x0, y0, x1, y1 = EDGE
    nx0, nx1 = NOTCH_X
    outline = [(nx0, y0), (nx1, y0), (nx1, NOTCH_Y), (x1, NOTCH_Y), (x1, y1), (x0, y1), (x0, NOTCH_Y), (nx0, NOTCH_Y)]
    for a, b in zip(outline, outline[1:] + outline[:1]):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(*a))
        s.SetEnd(V(*b))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)

    def track(a, b, w, n, layer=pcbnew.F_Cu):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(V(*a))
        t.SetEnd(V(*b))
        t.SetWidth(mm(w))
        t.SetLayer(layer)
        t.SetNet(net(n))
        t.SetLocked(True)
        board.Add(t)

    def via(x, y, n="GND"):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(V(x, y))
        v.SetWidth(mm(0.6))
        v.SetDrill(mm(0.3))
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(net(n))
        v.SetLocked(True)
        board.Add(v)

    def pad_x(ref, num):
        p = [p for p in fps[ref].Pads() if p.GetNumber() == num][0]
        return pcbnew.ToMM(p.GetPosition().x)

    def pad_xy(ref, num):
        p = [p for p in fps[ref].Pads() if p.GetNumber() == num][0]
        return pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)

    def pad_y(ref, num):
        return pad_xy(ref, num)[1]

    # RF lines: SMA centre pad -> DC block -> land, 0.85 mm, the last 0.6 mm at the land's width
    def rf_line(y, sma, cap, x_land, x_pad, n_out, n_in, step):
        xs = pad_x(sma, "1")
        c_out, c_in = sorted((pad_x(cap, "1"), pad_x(cap, "2")), key=lambda v: abs(v - xs))
        track((xs, y), (c_out, y), W_RF, n_out)
        x_narrow = x_land - step * 0.55            # the neck between the GSG lands' neighbours (round track ends)
        track((c_in, y), (x_narrow, y), W_RF, n_in)
        track((x_narrow, y), (x_pad, y), 0.3, n_in)

    rf_line(Y_RF, "J1", "C1", X_LAND_L, X_PAD_L, "RF_IN_J", "RF_IN", 1)
    rf_line(Y_RF, "J2", "C2", X_LAND_R, X_PAD_R, "RF_OUT_J", "RF_OUT", -1)
    # the 2x-thru: the same, mirrored about its middle; the middle is THRU_M, 2 x 17.75 mm in all
    xa, xb = pad_x("J4", "1"), pad_x("J5", "1")
    ca = sorted((pad_x("C9", "1"), pad_x("C9", "2")))
    cb = sorted((pad_x("C10", "1"), pad_x("C10", "2")))
    track((xa, Y_THRU), (ca[0], Y_THRU), W_RF, "THRU_A")
    track((ca[1], Y_THRU), (cb[0], Y_THRU), W_RF, "THRU_M")
    track((cb[1], Y_THRU), (xb, Y_THRU), W_RF, "THRU_B")

    # bias: four lines straight down from the QFN's bottom leads to J3, 45-degree
    # bends; the decoupling hangs off each line, its ground pad with its own via
    yp = 114.25                                    # the outer end of the bottom leads
    d = 2.54
    track((119.25, yp), (119.25, 114.6), 0.25, "VCC")
    track((119.25, 114.6), (X_VCC, 114.6 + 119.25 - X_VCC), 0.25, "VCC")
    track((X_VCC, 114.6 + 119.25 - X_VCC), (X_VCC, pad_y("FB1", "2")), 0.25, "VCC")
    track((X_VCC, pad_y("FB1", "1")), (X_VCC, Y_J3), 0.4, "VCC_IN")
    track((X_IDC, yp), (X_IDC, pad_y("R1", "2")), 0.25, "IDC")
    track((X_IDC, pad_y("R1", "1")), (X_IDC, Y_J3), 0.25, "IDC_IN")
    for x_lead, y_turn, x_end, n in ((120.25, 114.6, X_VCC + 2 * d, "IHEAT"), (120.75, 114.4, X_VCC + 3 * d, "GNDHEAT")):
        track((x_lead, yp), (x_lead, y_turn), 0.25, n)
        track((x_lead, y_turn), (x_end, y_turn + x_end - x_lead), 0.25, n)
        track((x_end, y_turn + x_end - x_lead), (x_end, Y_J3), 0.3, n)
    for ref in ("C5", "C6", "C7", "C13", "C8"):
        gx, gy = pad_xy(ref, "2")
        vx = gx - 0.75 if ref != "C8" else gx
        vy = gy if ref != "C8" else gy + 0.8
        track((gx, gy), (vx, vy), 0.4, "GND")
        via(vx, vy)

    # via fences both sides of every RF line, and the ground stitching
    fence_y = []
    for y, xa_, xb_ in ((Y_RF, x0 + 1.0, X_LAND_L - 0.9), (Y_RF, X_LAND_R + 0.9, x1 - 1.0), (Y_THRU, nx0 + 1.0, nx1 - 1.0)):
        k = 0
        while xa_ + k * PITCH <= xb_:
            for dy in (-FENCE, FENCE):
                fence_y.append((xa_ + k * PITCH, y + dy))
            k += 1
    keep = []                                   # parts' pads: no via within 0.5 mm of another net's copper
    for fp in fps.values():
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            keep.append((pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetRight()),
                         pcbnew.ToMM(bb.GetBottom()), p.GetNetname()))
    holes = [(x, y) for x, y in HOLES.values()]

    def free(x, y, r=0.3 + 0.25):
        if any((x - hx) ** 2 + (y - hy) ** 2 < 2.4 ** 2 for hx, hy in holes):
            return False
        for (l, t, rr, b, n) in keep:
            dx = max(l - x, 0, x - rr)
            dy = max(t - y, 0, y - b)
            if n != "GND" and (dx * dx + dy * dy) ** 0.5 < r:
                return False
            if n == "GND" and dx == 0 and dy == 0 and not (rr - l > 1.6):   # not in small GND pads
                return False
        for yl in (Y_RF, Y_THRU):              # off the RF lines and their gaps
            if abs(y - yl) < W_RF / 2 + GAP + 0.35 and x0 < x < x1:
                if not (X_LAND_L - 0.2 < x < X_LAND_R + 0.2 and yl == Y_RF):
                    return False
        return True

    placed = []
    for (x, y) in fence_y:
        if free(x, y):
            via(x, y)
            placed.append((x, y))
    # thermal vias in the exposed pad (3 x 3)
    for dx in (-0.6, 0.0, 0.6):
        for dy in (-0.6, 0.0, 0.6):
            via(U1_AT[0] + dx, U1_AT[1] + dy)
    # stitching grid, 2.5 mm, where nothing else is (checked after routing by DRC)
    gx = x0 + 1.5
    while gx < x1 - 1.0:
        gy = y0 + 1.5
        while gy < y1 - 1.0:
            inside = (gy > NOTCH_Y + 0.8) or (nx0 + 0.8 < gx < nx1 - 0.8)
            if inside and free(gx, gy, 0.9) and all((gx - px) ** 2 + (gy - py) ** 2 > 0.9 ** 2 for px, py in placed) \
                    and not (U1_AT[0] - 3.2 < gx < U1_AT[0] + 3.2 and U1_AT[1] - 3.2 < gy < U1_AT[1] + 3.2) \
                    and not (113.5 < gx < 129.0 and 113.0 < gy < 129.5):          # the bias lines
                via(gx, gy)
                placed.append((gx, gy))
            gy += 2.5
        gx += 2.5

    # pours: top GND (its clearance is the GCPW gap), bottom GND plane
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(net("GND"))
        z.SetLocalClearance(mm(GAP))
        z.SetMinThickness(mm(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        o = z.Outline()
        o.NewOutline()
        for px, py in outline:
            o.Append(mm(px), mm(py))
        board.Add(z)
    t = pcbnew.PCB_TEXT(board)
    t.SetText("ALL_LNA eval  RO4350B 0.508  GCPW 50R")
    t.SetPosition(V(120.0, 124.5))
    t.SetLayer(pcbnew.B_SilkS)
    t.SetMirrored(True)
    t.SetTextSize(pcbnew.VECTOR2I(mm(1.0), mm(1.0)))
    board.Add(t)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(path)
    shutil.rmtree(tmp, ignore_errors=True)
    s = open(path, encoding="utf-8").read()
    if "(stackup" not in s:
        s = re.sub(r"\(setup\n", "(setup\n" + STACKUP, s, count=1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)


DRU = """(version 1)
# The edge-launch SMAs sit across the board edge by design (their ground legs
# clamp the board): no edge clearance for them and for the line into their pin.
(rule "edge launch SMA"
  (constraint edge_clearance (min 0mm))
  (severity ignore)
  (condition "A.insideCourtyard('J1') || A.insideCourtyard('J2') || A.insideCourtyard('J4') || A.insideCourtyard('J5') || B.insideCourtyard('J1') || B.insideCourtyard('J2') || B.insideCourtyard('J4') || B.insideCourtyard('J5')"))
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in (f"{LIB}.kicad_sym", U1_FP + ".step"):
        shutil.copy(os.path.join(PKG, f), os.path.join(OUT, f))
    shutil.copytree(os.path.join(PKG, f"{LIB}.pretty"), os.path.join(OUT, f"{LIB}.pretty"), dirs_exist_ok=True)
    for kind in ("sym", "fp"):
        ext = "kicad_sym" if kind == "sym" else "pretty"
        with open(os.path.join(OUT, f"{kind}-lib-table"), "w") as fh:
            fh.write(f'({kind}_lib_table\n  (lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB}.{ext}")'
                     f'(options "")(descr "Package Studio: ALL_LNA in QFN-24 4x4"))\n)\n')
    write_schematic(os.path.join(OUT, f"{NAME}.kicad_sch"))
    try:
        import pcbnew  # noqa: F401
        write_board(os.path.join(OUT, f"{NAME}.kicad_pcb"))
    except ImportError:
        print("pcbnew not importable: schematic written, board not (run with KiCad's Python)")
    write_project(os.path.join(OUT, f"{NAME}.kicad_pro"))
    with open(os.path.join(OUT, f"{NAME}.kicad_dru"), "w") as fh:
        fh.write(DRU)
    print("written:", OUT)


if __name__ == "__main__":
    main()
