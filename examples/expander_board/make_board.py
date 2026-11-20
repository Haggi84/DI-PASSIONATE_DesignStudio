# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 2 of the expander evaluation board: the KiCad project around the
package of step 1 (make_package.py).

  kicad/expander_eval.kicad_sch   schematic (net labels on every pin)
  kicad/expander_eval.kicad_pcb   4-layer board, 45 x 32 mm, placed; routed
                                  by route_board.py (Freerouting)
  kicad/expander_eval.kicad_pro   project: design rules, net classes
  kicad/ExpanderEval.pretty, ExpanderEval.kicad_sym, *.step   the package

What the board does: it makes the expander usable from any I2C controller
with 3.3 V logic (Qwiic / STEMMA QT, daisy-chained):
  - 3.3 V in on J1/J2 -> AP2112K-1.2 LDO -> 0-ohm R2 (lift it to measure the
    core current) -> the two VDD pads, 100 nF at each, 1 uF bulk
  - 50 MHz CMOS oscillator (Abracon ASE, 3.3 V) -> 33 ohm series -> io_clock:
    the one fast net, routed short on F.Cu over the In1 ground plane
  - reset: 10 k pull-up + 100 nF (holds reset for ~1 ms while the core
    supply comes up; IHP asks for >= 1 us), push button, header J4
  - SCL/SDA 2.2 k pull-ups (400 kHz), INT 10 k pull-up
  - address: 10 k pull-downs, solder jumpers to 3.3 V (default 0b000)
  - GPIO0-7 on header J3 and on LEDs (1 k, low current)

Run with KiCad's Python (KiCad 7 or newer; pcbnew must import):
    python3 examples/expander_board/make_board.py
"""

import math
import os
import re
import shutil
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "out", "package")            # make_package.py's output
OUT = os.path.join(HERE, "kicad")
NAME = "expander_eval"
SYMDIR = os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
FPDIR = os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
U1_FP = "QFN-24_4x4_Expander"
NS = uuid.UUID("6b1f9a52-5d0e-4c55-9a4e-2f0c3b7d1e10")


def uid(*parts):
    return str(uuid.uuid5(NS, "/".join(str(p) for p in parts)))


# -- the design -----------------------------------------------------------------------------
# ref: (lib symbol, value, footprint, {pin number: net}, schematic (x, y, rot), pcb (x, y, rot), side)
R0402, C0402, C0805 = "Resistor_SMD:R_0402_1005Metric", "Capacitor_SMD:C_0402_1005Metric", \
    "Capacitor_SMD:C_0805_2012Metric"
LED0603 = "LED_SMD:LED_0603_1608Metric"
QWIIC = "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"

U1_PINS = {"2": "+1V2", "3": "GND", "4": "GPIO7", "5": "GPIO6", "6": "GPIO5", "7": "CLK_50M", "8": "RESET_N",
           "9": "SCL", "10": "SDA", "11": "INT", "13": "+1V2", "14": "GND", "15": "A0", "16": "A1", "17": "A2",
           "20": "GPIO4", "21": "GPIO3", "22": "GPIO2", "23": "GPIO1", "24": "GPIO0", "25": "GND"}

PARTS = {
    # supply
    "J1": ("Connector_Generic:Conn_01x04", "Qwiic in", QWIIC, {"1": "GND", "2": "+3V3", "3": "SDA", "4": "SCL"},
           (30.48, 45.72, 0), (106.0, 128.7, 0)),
    "J2": ("Connector_Generic:Conn_01x04", "Qwiic out", QWIIC, {"1": "GND", "2": "+3V3", "3": "SDA", "4": "SCL"},
           (30.48, 66.04, 0), (114.0, 128.7, 0)),
    "C1": ("Device:C", "10u", C0805, {"1": "+3V3", "2": "GND"}, (55.88, 45.72, 0), (102.6, 122.5, 90)),
    "U2": ("Regulator_Linear:AP2112K-1.2", "AP2112K-1.2", "Package_TO_SOT_SMD:SOT-23-5",
           {"1": "+3V3", "2": "GND", "3": "+3V3", "5": "+1V2_LDO"}, (86.36, 45.72, 0), (106.5, 107.5, 0)),
    "C2": ("Device:C", "1u", C0402, {"1": "+3V3", "2": "GND"}, (68.58, 45.72, 0), (103.4, 107.5, 90)),
    "C3": ("Device:C", "1u", C0402, {"1": "+1V2_LDO", "2": "GND"}, (104.14, 45.72, 0), (109.6, 107.5, 90)),
    "R2": ("Device:R", "0R", R0402, {"1": "+1V2_LDO", "2": "+1V2"}, (116.84, 40.64, 90), (112.0, 107.5, 90)),
    "C6": ("Device:C", "1u", C0402, {"1": "+1V2", "2": "GND"}, (129.54, 45.72, 0), (116.6, 111.0, 90)),
    # core
    "U1": ("ExpanderEval:" + U1_FP, "i2c-gpio-expander", "ExpanderEval:" + U1_FP, U1_PINS,
           (205.74, 101.6, 0), (122.0, 114.0, 0)),
    "C4": ("Device:C", "100n", C0402, {"1": "+1V2", "2": "GND"}, (142.24, 45.72, 0), (118.4, 113.4, 90)),
    "C5": ("Device:C", "100n", C0402, {"1": "+1V2", "2": "GND"}, (154.94, 45.72, 0), (125.6, 117.1, 0)),
    # clock
    "X1": ("Oscillator:ASE-xxxMHz", "50MHz", "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
           {"1": "+3V3", "2": "GND", "3": "CLK_OSC", "4": "+3V3"}, (45.72, 127.0, 0), (117.6, 122.6, 0)),
    "C7": ("Device:C", "100n", C0402, {"1": "+3V3", "2": "GND"}, (30.48, 127.0, 0), (114.8, 121.8, 90)),
    "R1": ("Device:R", "33R", R0402, {"1": "CLK_OSC", "2": "CLK_50M"}, (66.04, 121.92, 90), (120.75, 119.6, 90)),
    # control
    "R3": ("Device:R", "2k2", R0402, {"1": "+3V3", "2": "SCL"}, (114.3, 165.1, 0), (123.0, 120.2, 90)),
    "R4": ("Device:R", "2k2", R0402, {"1": "+3V3", "2": "SDA"}, (124.46, 165.1, 0), (124.2, 120.2, 90)),
    "R5": ("Device:R", "10k", R0402, {"1": "+3V3", "2": "INT"}, (134.62, 165.1, 0), (125.4, 120.2, 90)),
    "R6": ("Device:R", "10k", R0402, {"1": "+3V3", "2": "RESET_N"}, (144.78, 165.1, 0), (126.6, 120.2, 90)),
    "C8": ("Device:C", "100n", C0402, {"1": "RESET_N", "2": "GND"}, (157.48, 165.1, 0), (127.8, 120.2, 90)),
    "SW1": ("Switch:SW_Push", "Reset", "Button_Switch_SMD:SW_SPST_TL3342", {"1": "RESET_N", "2": "GND"},
            (175.26, 165.1, 0), (137.5, 127.0, 0)),
    "J4": ("Connector_Generic:Conn_01x03", "INT RESET GND", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
           {"1": "INT", "2": "RESET_N", "3": "GND"}, (198.12, 165.1, 0), (126.0, 129.0, 90)),
    "R7": ("Device:R", "10k", R0402, {"1": "A0", "2": "GND"}, (251.46, 76.2, 0), (128.2, 111.5, 90)),
    "R8": ("Device:R", "10k", R0402, {"1": "A1", "2": "GND"}, (261.62, 76.2, 0), (129.4, 111.5, 90)),
    "R9": ("Device:R", "10k", R0402, {"1": "A2", "2": "GND"}, (271.78, 76.2, 0), (130.6, 111.5, 90)),
    "JP1": ("Jumper:SolderJumper_2_Open", "A0", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
            {"1": "A0", "2": "+3V3"}, (256.54, 101.6, 0), (129.0, 116.0, 0)),
    "JP2": ("Jumper:SolderJumper_2_Open", "A1", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
            {"1": "A1", "2": "+3V3"}, (256.54, 111.76, 0), (132.6, 116.0, 0)),
    "JP3": ("Jumper:SolderJumper_2_Open", "A2", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
            {"1": "A2", "2": "+3V3"}, (256.54, 121.92, 0), (130.8, 119.2, 0)),
    "J3": ("Connector_Generic:Conn_02x05_Odd_Even", "GPIO", "Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical",
           {"1": "GPIO0", "2": "GPIO1", "3": "GPIO2", "4": "GPIO3", "5": "GPIO4", "6": "GPIO5", "7": "GPIO6",
            "8": "GPIO7", "9": "+3V3", "10": "GND"}, (342.9, 76.2, 0), (126.0, 102.4, 270)),
    # test points
    "TP1": ("Connector:TestPoint", "CLK_50M", "TestPoint:TestPoint_Pad_D1.0mm", {"1": "CLK_50M"},
            (78.74, 116.84, 0), (120.0, 125.5, 0)),
    "TP2": ("Connector:TestPoint", "+1V2", "TestPoint:TestPoint_Pad_D1.0mm", {"1": "+1V2"},
            (129.54, 30.48, 0), (114.5, 110.0, 0)),
    "TP3": ("Connector:TestPoint", "+3V3", "TestPoint:TestPoint_Pad_D1.0mm", {"1": "+3V3"},
            (55.88, 30.48, 0), (102.5, 112.0, 0)),
    "TP4": ("Connector:TestPoint", "GND", "TestPoint:TestPoint_Pad_D1.0mm", {"1": "GND"},
            (68.58, 30.48, 0), (102.5, 115.0, 0)),
}
# GPIO LEDs: Rn from the GPIO, the LED to ground
for k in range(8):
    PARTS[f"R{10 + k}"] = ("Device:R", "1k", R0402, {"1": f"GPIO{k}", "2": f"LED{k}"},
                           (292.1, 50.8 + 15.24 * k, 90), (136.6, 103.4 + 2.6 * k, 0))
    PARTS[f"D{1 + k}"] = ("Device:LED", "LED", LED0603, {"2": f"LED{k}", "1": "GND"},
                          (314.96, 50.8 + 15.24 * k, 180), (140.8, 103.4 + 2.6 * k, 0))
# power flags (schematic only)
FLAGS = {"#FLG01": ("+3V3", (30.48, 91.44)), "#FLG02": ("GND", (45.72, 91.44)), "#FLG03": ("+1V2", (60.96, 91.44))}

BOARD = (100.0, 100.0, 145.0, 132.0)
POWER = ("+3V3", "+1V2", "+1V2_LDO", "GND")


# -- small S-expression tools ---------------------------------------------------------------

def block(text, start):
    """The balanced (...) starting at *start*; strings skipped."""
    d, j = 0, start
    while j < len(text):
        c = text[j]
        if c == '"':
            j += 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
        elif c == "(":
            d += 1
        elif c == ")":
            d -= 1
            if d == 0:
                return text[start:j + 1]
        j += 1
    raise ValueError("unbalanced")


def lib_block(lib, name, path=None):
    text = open(path or os.path.join(SYMDIR, f"{lib}.kicad_sym"), encoding="utf-8").read()
    m = re.search(r'\(symbol "%s"' % re.escape(name), text)
    if not m:
        raise KeyError(f"{lib}:{name}")
    return text, block(text, m.start())


def flat_symbol(lib_id):
    """The symbol definition for lib_symbols: named lib:name, 'extends' flattened."""
    lib, name = lib_id.split(":")
    own = os.path.join(PKG, f"{lib}.kicad_sym")          # the package's own library (ExpanderEval, LNAEval)
    path = own if os.path.isfile(own) else None
    text, blk = lib_block(lib, name, path)
    ext = re.match(r'\(symbol "[^"]+"\s*\(extends "([^"]+)"\)', blk)
    if ext:
        parent = block(text, re.search(r'\(symbol "%s"' % re.escape(ext.group(1)), text).start())
        props = dict(re.findall(r'\(property "([^"]+)" ("[^"]*")', blk))
        body = parent.replace(f'"{ext.group(1)}_', f'"{name}_')
        for key, val in props.items():
            body = re.sub(r'(\(property "%s" )"[^"]*"' % re.escape(key), lambda m: m.group(1) + val, body)
        blk = body
    blk = re.sub(r'^\(symbol "[^"]+"', f'(symbol "{lib_id}"', blk, count=1)
    # KiCad 8 tokens a KiCad 7 reader does not take
    blk = blk.replace("(hide yes)", "hide").replace("(exclude_from_sim no)", "")
    return blk


def symbol_pins(lib_id):
    """{number: (x, y, angle)} in symbol coordinates (y up), unit 1 / common."""
    blk = flat_symbol(lib_id)
    out = {}
    for m in re.finditer(r'\(pin (\w+) \w+ \(at ([-\d.]+) ([-\d.]+) (\d+)\)', blk):
        tail = blk[m.end():m.end() + 400]
        num = re.search(r'\(number "([^"]*)"', tail).group(1)
        out[num] = (float(m.group(2)), float(m.group(3)), int(m.group(4)), m.group(1))
    return out


# -- schematic ------------------------------------------------------------------------------

def _rot(px, py, rot):
    """Symbol coordinates (y up) of a pin turned by the symbol's rotation."""
    a = math.radians(rot)
    return px * math.cos(a) - py * math.sin(a), px * math.sin(a) + py * math.cos(a)


def write_schematic(path):
    root = uid("sheet")
    used = sorted({p[0] for p in PARTS.values()} | {"power:PWR_FLAG"})
    out = [f'(kicad_sch (version 20230121) (generator eeschema)', f'  (uuid {root})', '  (paper "A3")',
           '  (title_block (title "I2C GPIO expander evaluation board") (rev "A") '
           '(company "Package Studio example") (comment 1 "IHP i2c-gpio-expander (SG13G2, TO_Nov2024) in QFN-24 4x4") '
           '(comment 2 "Die has no IOVDD/IOVSS pads (IHP known issues): IO function to be verified"))',
           "  (lib_symbols"]
    out += ["    " + flat_symbol(s) for s in used]
    out.append("  )")
    font = "(effects (font (size 1.27 1.27))"
    parts = dict(PARTS)
    for ref, (net, xy) in FLAGS.items():
        parts[ref] = ("power:PWR_FLAG", "PWR_FLAG", "", {"1": net}, (xy[0], xy[1], 0), None)
    for ref, (lib_id, value, fp, pins, sch, *_rest) in sorted(parts.items()):
        x, y, rot = sch
        su = uid("sym", ref)
        allpins = symbol_pins(lib_id)
        out.append(f'  (symbol (lib_id "{lib_id}") (at {x:.2f} {y:.2f} {rot}) (unit 1) (in_bom {"no" if ref.startswith("#") else "yes"}) '
                   f'(on_board {"no" if ref.startswith("#") else "yes"}) (dnp no) (uuid {su})')
        out.append(f'    (property "Reference" "{ref}" (at {x:.2f} {y - 6.35:.2f} 0) {font}{" hide" if ref.startswith("#") else ""}))')
        out.append(f'    (property "Value" "{value}" (at {x:.2f} {y + 6.35:.2f} 0) {font}))')
        out.append(f'    (property "Footprint" "{fp}" (at {x:.2f} {y:.2f} 0) {font} hide))')
        out.append(f'    (property "Datasheet" "" (at {x:.2f} {y:.2f} 0) {font} hide))')
        for num in allpins:
            out.append(f'    (pin "{num}" (uuid {uid("pin", ref, num)}))')
        out.append(f'    (instances (project "{NAME}" (path "/{root}" (reference "{ref}") (unit 1))))')
        out.append("  )")
        for num, (px, py, ang, ptype) in allpins.items():
            rx, ry = _rot(px, py, rot)
            sx, sy = x + rx, y - ry                    # pin end in the sheet (y down)
            da = math.radians(ang + rot)
            ox, oy = -math.cos(da), math.sin(da)        # outward, sheet coordinates
            net = pins.get(num)
            if net is None:
                if ptype != "no_connect":
                    out.append(f'  (no_connect (at {sx:.2f} {sy:.2f}) (uuid {uid("nc", ref, num)}))')
                continue
            ex, ey = sx + 2.54 * ox, sy + 2.54 * oy
            out.append(f'  (wire (pts (xy {sx:.2f} {sy:.2f}) (xy {ex:.2f} {ey:.2f})) (stroke (width 0) (type default)) '
                       f'(uuid {uid("w", ref, num)}))')
            ang_l = {(-1, 0): 180, (1, 0): 0, (0, -1): 90, (0, 1): 270}[(round(ox), round(oy))]
            just = "right" if ang_l == 180 or ang_l == 270 else "left"
            out.append(f'  (label "{net}" (at {ex:.2f} {ey:.2f} {ang_l}) (fields_autoplaced) '
                       f'(effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {uid("l", ref, num)}))')
    notes = [((20.32, 20.32), "Supply: Qwiic 3.3 V in/out -> AP2112K-1.2 -> R2 (0R: lift to measure core current)"),
             ((20.32, 105.0), "Clock: 50 MHz CMOS oscillator, 33R series at the source, short line over GND"),
             ((100.0, 150.0), "I2C 400 kHz: 2k2 pull-ups; reset 10k/100n (~1 ms, IHP: >= 1 us), button, J4"),
             ((240.0, 60.0), "Address: 10k pull-downs, solder jumper closes to 3.3 V"),
             ((285.0, 35.0), "GPIO0-7: header J3 and LEDs (1k)"),
             ((175.0, 75.0), "U1 die: no IOVDD/IOVSS pads (IHP known issues) - IO function to be verified")]
    for (x, y), t in notes:
        out.append(f'  (text "{t}" (at {x:.2f} {y:.2f} 0) (effects (font (size 1.5 1.5)) (justify left bottom)) '
                   f'(uuid {uid("t", t)}))')
    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return root


# -- project and board ----------------------------------------------------------------------

def write_project(path):
    import json
    pro = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1},
            "rules": {"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.5,
                      "min_through_hole_diameter": 0.25, "min_copper_edge_clearance": 0.3},
            "track_widths": [0.0, 0.2, 0.3, 0.4], "via_dimensions": [{"diameter": 0.0, "drill": 0.0},
                                                                     {"diameter": 0.6, "drill": 0.3}]}},
        "meta": {"filename": f"{NAME}.kicad_pro", "version": 1},
        "net_settings": {"classes": [
            {"name": "Default", "clearance": 0.15, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
             "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
             "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
             "wire_width": 6, "bus_width": 12},
            {"name": "Power", "clearance": 0.15, "track_width": 0.3, "via_diameter": 0.6, "via_drill": 0.3,
             "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
             "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
             "wire_width": 6, "bus_width": 12}],
            "meta": {"version": 3},
            "netclass_assignments": None,
            "netclass_patterns": [{"netclass": "Power", "pattern": p} for p in POWER]},
        "sheets": [[uid("sheet"), ""]],
        "text_variables": {}}
    with open(path, "w") as fh:
        json.dump(pro, fh, indent=2)


def kicad7_footprint(src, dst):
    """The package's footprint in KiCad 7 syntax (the build runs on KiCad 7;
    the library in the project stays as Package Studio wrote it)."""
    s = open(src, encoding="utf-8").read()
    s = s.replace("(version 20240108)", "(version 20221018)").replace('\t(generator_version "1.0")\n', "")
    s = re.sub(r'\(property "Reference" "([^"]*)"', r'(fp_text reference "\1"', s)
    s = re.sub(r'\(property "Value" "([^"]*)"', r'(fp_text value "\1"', s)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(s)


STACKUP = """    (stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Paste" (type "Top Solder Paste"))
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.0152))
      (layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.0152))
      (layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "FR4") (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (layer "B.Paste" (type "Bottom Solder Paste"))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "ENIG")
      (dielectric_constraints no)
    )
"""


def write_board(path, root):
    import pcbnew
    tmp = os.path.join(OUT, "_k7")
    os.makedirs(os.path.join(tmp, "ExpanderEval.pretty"), exist_ok=True)
    kicad7_footprint(os.path.join(PKG, "ExpanderEval.pretty", U1_FP + ".kicad_mod"),
                     os.path.join(tmp, "ExpanderEval.pretty", U1_FP + ".kicad_mod"))
    board = pcbnew.NewBoard(path)
    board.SetCopperLayerCount(4)
    for layer in (pcbnew.In1_Cu, pcbnew.In2_Cu):          # planes only: no tracks on them, the router keeps off
        board.SetLayerType(layer, pcbnew.LT_POWER)
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(pcbnew.FromMM(1.6))
    nets = {}

    def net(name):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]

    for ref, (lib_id, value, fp_id, pins, _sch, pcb_at) in sorted(PARTS.items()):
        lib, name = fp_id.split(":")
        folder = os.path.join(tmp, f"{lib}.pretty") if lib == "ExpanderEval" else os.path.join(FPDIR, f"{lib}.pretty")
        fp = pcbnew.FootprintLoad(folder, name)
        if fp is None:
            raise FileNotFoundError(fp_id)
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        if fp.GetArea() < pcbnew.FromMM(1) * pcbnew.FromMM(1) * 12:   # 0402/0603 parts: small reference on silk
            fp.Reference().SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
            fp.Reference().SetTextThickness(pcbnew.FromMM(0.12))
        fp.SetValue(value)
        fp.SetPath(pcbnew.KIID_PATH(f"/{uid('sym', ref)}"))
        x, y, rot = pcb_at
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            n = pins.get(pad.GetNumber())
            if n:
                pad.SetNet(net(n))
        board.Add(fp)
    # outline
    x0, y0, x1, y1 = BOARD
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
        s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b[0]), pcbnew.FromMM(b[1])))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(pcbnew.FromMM(0.1))
        board.Add(s)
    # planes: In1 GND, In2 +3V3, ground pours on both outer layers
    for layer, name in ((pcbnew.In1_Cu, "GND"), (pcbnew.In2_Cu, "+3V3"), (pcbnew.F_Cu, "GND"), (pcbnew.B_Cu, "GND")):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(net(name))
        z.SetLocalClearance(pcbnew.FromMM(0.2))
        z.SetMinThickness(pcbnew.FromMM(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if layer in (pcbnew.In1_Cu, pcbnew.In2_Cu)
                           else pcbnew.ZONE_CONNECTION_FULL)
        z.SetThermalReliefGap(pcbnew.FromMM(0.25))
        z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.3))
        z.SetAssignedPriority(0 if layer in (pcbnew.In1_Cu, pcbnew.In2_Cu) else 1)
        o = z.Outline()
        o.NewOutline()
        for px, py in ((x0 + 0.3, y0 + 0.3), (x1 - 0.3, y0 + 0.3), (x1 - 0.3, y1 - 0.3), (x0 + 0.3, y1 - 0.3)):
            o.Append(pcbnew.FromMM(px), pcbnew.FromMM(py))
        board.Add(z)
    # title and a note on the silk
    t = pcbnew.PCB_TEXT(board)
    t.SetText("I2C GPIO expander eval  SG13G2")
    t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(122.0), pcbnew.FromMM(130.6)))
    t.SetLayer(pcbnew.B_SilkS)
    t.SetMirrored(True)
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.0), pcbnew.FromMM(1.0)))
    board.Add(t)
    board.Save(path)
    shutil.rmtree(tmp, ignore_errors=True)
    # stack-up: KiCad 7's API does not set it, write it into the file
    s = open(path, encoding="utf-8").read()
    s = re.sub(r"\(setup\n", "(setup\n" + STACKUP, s, count=1) if "(stackup" not in s else s
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in ("ExpanderEval.kicad_sym", U1_FP + ".step"):
        shutil.copy(os.path.join(PKG, f), os.path.join(OUT, f))
    shutil.copytree(os.path.join(PKG, "ExpanderEval.pretty"), os.path.join(OUT, "ExpanderEval.pretty"),
                    dirs_exist_ok=True)
    with open(os.path.join(OUT, "sym-lib-table"), "w") as fh:
        fh.write('(sym_lib_table\n  (lib (name "ExpanderEval")(type "KiCad")(uri "${KIPRJMOD}/ExpanderEval.kicad_sym")'
                 '(options "")(descr "Package Studio: the expander in QFN-24"))\n)\n')
    with open(os.path.join(OUT, "fp-lib-table"), "w") as fh:
        fh.write('(fp_lib_table\n  (lib (name "ExpanderEval")(type "KiCad")(uri "${KIPRJMOD}/ExpanderEval.pretty")'
                 '(options "")(descr "Package Studio: the expander in QFN-24"))\n)\n')
    root = write_schematic(os.path.join(OUT, f"{NAME}.kicad_sch"))
    try:
        import pcbnew  # noqa: F401
        write_board(os.path.join(OUT, f"{NAME}.kicad_pcb"), root)
    except ImportError:
        print("pcbnew not importable: schematic written, board not (run with KiCad's Python)")
    write_project(os.path.join(OUT, f"{NAME}.kicad_pro"))     # after the board: saving it writes a default one
    print("written:", OUT)


if __name__ == "__main__":
    main()
