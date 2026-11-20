# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Reference case: an S-band receiver front end for a 1U CubeSat — from two
real SG13G2 dies to the satellite.

  1  Dies       analog/RF: ALL_LNA.gds, the SG13G2 LNA of the QFN-40 example
                digital:   i2c-gpio-expander (IHP-Open-DesignLib, MPW T576,
                           Nov 2024; ORFS flow, 1.05 x 1.05 mm, 8-bit I2C GPIO
                           expander, 50 MHz clock; GPLv3, see
                           data/i2c_gpio_expander.COPYING.GPLv3)
  2  MCM        both dies on the paddle of an open-cavity JEDEC QFN-32 5x5,
                corner to corner (core.mcm), 25 um Al wedge wires laid by
                the pin list data/cubesat_mcm_pinlist.csv (app.netlist): the
                RF pins on fixed ground-signal-ground leads, every other pad
                crossing-free round them (an exact search over the lead arcs
                of both dies). Checked twice: the package LVS against the pin
                list, MIL-STD-883 TM 2010 on every wire. Out: the bonding
                diagram and wire list for the assembly house, and the package
                as a KiCad library (footprint, symbol with the nets, STEP).
                No die-to-die wire: the earlier GPIO -> LNA heater wire was
                not plausible (a GPIO does not drive a heater); the GPIOs
                go to leads, the two dies share ground through leads.
  3  Board      the COM board, PC/104 CubeSat size (core.pcb): 0.254 mm
                RO4350B over the ground plane on FR-4, 1.6 mm. A right-angle
                MMCX (from the antenna) -> 50-ohm GCPW -> the MCM's RFIN lead;
                RFOUT -> GCPW -> the transceiver (a placeholder body). Clock
                from a 50 MHz oscillator, reset/I2C/interrupt to the CubeSat
                Kit bus header H1, LDO and decoupling (bodies; supply planes
                not drawn).
  4  System     a 1U CubeSat (core.cubesat): rails, frames, rods, spacers,
                solar panels, an S-band patch on +Z, and the stack EPS /
                battery / OBC (stand-in boards) / COM. Checked against the
                CubeSat Design Specification Rev 14.1: envelope, collisions,
                stack clearances, mass (<= 2.00 kg), centre of gravity (±2 cm).
  5  EM         the RF path cut out of the COM board — GCPW in, package,
                wires, both dies — with a port at each cut line edge and one
                in the TopMetal2 gap at each RF pad of the LNA, built as the
                openEMS model of the LNA example (S-parameters to cascade with
                the LNA's own). Built and checked here; the solve runs where
                openEMS is (the GUI writes the package).

The expander as taped out has a known issue (IHP's own notes): no IOVDD /
IOVSS pads. So electrically this is not a flight design — mechanically and
for the EM model it is a real die.

Run:  freecadcmd examples/cubesat_sband_mcm.py      (or exec() in FreeCAD)
Out:  out/cubesat/CubeSat_SBand_MCM.FCStd, …_RF_EM.FCStd, …_report.json
"""

import gzip
import json
import os
import shutil
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples")
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pkgstudio.app import (bonding, boards, em_model, housings, kicad, model, netlist, ports,  # noqa: E402
                           readiness, system, wires)
from pkgstudio.app.em_project import EmProject  # noqa: E402
from pkgstudio.core import cubesat as cs, jedec, pcb  # noqa: E402

V = FreeCAD.Vector
DATA = os.path.join(HERE, "data")
OUT = os.environ.get("PS_EXAMPLE_OUT", os.path.join(HERE, "out", "cubesat"))
TECH = os.path.join(ROOT, "technologies", "IHP-SG13G2")
LNA_GDS = os.path.join(DATA, "ALL_LNA.gds")
DIG_GDS_GZ = os.path.join(DATA, "i2c_gpio_expander_SG13G2Top.gds.gz")
NAME = "CubeSat_SBand_MCM"

PACKAGE = "QFN-32 5x5"
DIE_GAP = 0.3                     # mm between the dies
# what of each die is bonded to a lead (the LNA's on-wafer calibration
# structures OPEN/SHORT/LOAD/THROUGH/BIG are not)
PINLIST = os.path.join(DATA, "cubesat_mcm_pinlist.csv")
# the expander's four supply pads carry the names of the IO ring's labels;
# by position and its pin-out table they are:
DIG_RENAME = {"IO_CORNER_NORTH_WEST_INST.vdd_RING": "VDD_W", "IO_CORNER_NORTH_WEST_INST.vss_RING": "VSS_W",
              "IO_CORNER_NORTH_WEST_INST.vdd_RING_2": "VDD_E", "IO_CORNER_NORTH_WEST_INST.vss_RING_2": "VSS_E"}

STACK = pcb.Stack()                # 0.254 mm RO4350B over L2, FR-4, 1.6 mm
LINE_W, GAP_S = 0.44, 0.20         # 50-ohm GCPW on 0.254 mm RO4350B (the LNA example's board)
U1 = (-20.0, -10.0)                # MCM centre on the COM board
MMCX_X = -41.0
TRANSCEIVER = (2.0, -1.0)          # placeholder, where RFOUT's line ends
STACK_TOPS = {"EPS": -40.0, "Battery": -24.0, "OBC": -2.0, "COM": 13.0}   # board tops in the CubeSat, mm
EM_RECT = (-32.0, -17.0, -13.0, -4.0)   # x0, y0, x1, y1: the RF path cut out for the EM model


def say(text):
    FreeCAD.Console.PrintMessage(f"[cubesat] {text}\n")


def _digital_gds():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "i2c_gpio_expander.gds")
    if not os.path.isfile(path):
        with gzip.open(DIG_GDS_GZ, "rb") as src, open(path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return path


# -- 1 + 2: the dies in their package -----------------------------------------------------------

def build_mcm(doc):
    """Both dies on the paddle of a QFN-32, wired. Returns (housing, package, lna, dig, report)."""
    lna = model.import_chip(doc, LNA_GDS, TECH)
    dig = model.import_chip(doc, _digital_gds(), TECH)
    for p in model.pads_of(dig):
        if p.PadName in DIG_RENAME:
            p.PadName = DIG_RENAME[p.PadName]
    housing = housings.from_jedec(doc, jedec.by_name(PACKAGE))
    layout = housings.place_chips([lna, dig], housing, gap=DIE_GAP, direction="diagonal")
    intent = netlist.load([PINLIST])
    missing = netlist.apply(doc, intent, [lna, dig])
    made = netlist.assign(housing, intent)
    lvs, lead_nets = netlist.lvs(housing, intent)
    rules = wires.check(doc)
    rejects = [f"{f.wire}: {f.rule}: {f.message}" for v in rules.values() for f in v
               if not f.ok and f.severity == "reject"]
    lengths = {w.Label: round(float(getattr(w.WireLength, "Value", w.WireLength)), 3) for w in wires.wires(doc)}
    pin_map = {w.PackagePad.PadName: w.ChipPad.PadName for w in made
               if getattr(w.PackagePad, "PadSide", "") != "chip"}
    report = {
        "package": PACKAGE,
        "pin_list": os.path.basename(PINLIST),
        "dies": {c.Label: [round(v, 3) for v in (housings._paddle_frame_die(c, housing).x,
                                                  housings._paddle_frame_die(c, housing).y)] for c in (lna, dig)},
        "layout_issues": [str(i) for i in layout],
        "pins_not_found": [f"{p.die} {p.pad}" for p in missing],
        "pin_map": pin_map,
        "lead_nets": lead_nets,
        "package_lvs": [str(f) for f in lvs],
        "die_to_die": [w.Label for w in wires.die_to_die(doc)],
        "wire_rule_rejects": rejects,
        "wires_checked": len(rules),
        "wire_lengths_mm": lengths,
    }
    report["_intent"] = intent
    return housing, model.assembly_owner(housing), lna, dig, report


def _lead_xy(housing, name):
    """Lead *name*'s finger centre in the housing frame."""
    pad = {p.PadName: p for p in model.pads_of(housing)}[name]
    return model.global_placement(housing).inverse().multVec(model.pad_centre(pad))


# -- 3: the COM board ---------------------------------------------------------------------------

def com_board_pieces(housing, pin_map):
    """core.pcb pieces of the COM board for the MCM in *housing* (pin_map:
    lead -> die pad), and the points the EM ports go on."""
    spec = housings.spec_of(housing)[0]
    lead = {v: k for k, v in pin_map.items()}               # die pad -> lead
    ux, uy = U1
    hx = spec["body_x"] / 2
    toe = hx + 0.25                                          # land toe from the package centre (core.pcb.qfn_lands)
    p = pcb.board(stack=STACK)
    p += pcb.footprint(spec, U1, STACK, "U1")

    # RFIN: the MMCX to the left-side lead; ground tabs onto the GSG leads beside it
    rfin = _lead_xy(housing, lead["RFIN"])
    y_in = uy + rfin.y
    a, b = (MMCX_X, y_in), (ux - toe, y_in)
    p += pcb.gcpw(a, b, LINE_W, GAP_S, stack=STACK, stop_a=2.5, stop_b=0.25, name="RF in")
    for g in ("GND14", "GND15"):
        gy = uy + _lead_xy(housing, lead[g]).y
        lo, hi = sorted((y_in + (LINE_W / 2 + GAP_S) * (1 if gy > y_in else -1), gy + 0.12 * (1 if gy > y_in else -1)))
        p.append(pcb.Piece(f"RF in ground tab {g}", pcb._copper((ux - toe - 0.25, lo, ux - toe, hi), STACK),
                           "Copper (Cu)", "copper", "RF in"))
    p.append(pcb.mmcx_right_angle("J1 MMCX (antenna)", MMCX_X, y_in, STACK, facing="-x"))

    # RFOUT: wherever its lead is — the line leaves the package straight out, then turns to the transceiver
    rfout = _lead_xy(housing, lead["RFOUT"])
    side = max((("T", rfout.y), ("B", -rfout.y), ("R", rfout.x), ("L", -rfout.x)), key=lambda s: s[1])[0]
    if side in ("T", "B"):
        sgn = 1 if side == "T" else -1
        x_out = ux + rfout.x
        start = (x_out, uy + sgn * toe)
        corner = (x_out, TRANSCEIVER[1])
        p += pcb.gcpw(corner, start, LINE_W, GAP_S, stack=STACK, stop_a=1.0, stop_b=0.25, name="RF out")
        p += pcb.gcpw(corner, TRANSCEIVER, LINE_W, GAP_S, stack=STACK, stop_a=1.0, stop_b=3.0, name="RF out 2")
        out_port = ("y", x_out)
    else:
        sgn = 1 if side == "R" else -1
        y_out = uy + rfout.y
        start = (ux + sgn * toe, y_out)
        p += pcb.gcpw(start, (TRANSCEIVER[0], y_out), LINE_W, GAP_S, stack=STACK, stop_a=0.25, stop_b=3.0,
                      name="RF out")
        out_port = ("x", y_out)
    for g in ("GND3", "GND4"):
        q = _lead_xy(housing, lead[g])
        if side in ("T", "B"):
            gx = ux + q.x
            lo, hi = sorted((start[0] + (LINE_W / 2 + GAP_S) * (1 if gx > start[0] else -1),
                             gx + 0.12 * (1 if gx > start[0] else -1)))
            y0 = start[1] if sgn > 0 else start[1] - 0.25
            p.append(pcb.Piece(f"RF out ground tab {g}", pcb._copper((lo, y0, hi, y0 + 0.25), STACK),
                               "Copper (Cu)", "copper", "RF out"))
    p.append(pcb.box("U2 transceiver (placeholder)", TRANSCEIVER[0] + 3.0, TRANSCEIVER[1], 5.0, 5.0, 0.9,
                     "Mould compound (EMC)", "U2", stack=STACK))

    # clock: the oscillator below the package; reset / I2C / interrupt to H1
    land_y = uy - toe
    clk = _lead_xy(housing, lead["io_clock_PAD"])
    osc = (ux + clk.x, uy - 7.0)
    p.append(pcb.Piece("Clock trace", pcb.trace([(ux + clk.x, land_y), osc], 0.2, STACK), "Copper (Cu)",
                       "copper", "Digital"))
    p.append(pcb.box("Y1 50 MHz oscillator", osc[0], osc[1] - 0.6, 2.5, 2.0, 0.8, "Alumina 96 % (Al2O3)", "Y1",
                     stack=STACK))
    sigs = sorted(("io_i2c_interrupt_PAD", "io_i2c_sda_PAD", "io_i2c_scl_PAD", "io_reset_PAD"),
                  key=lambda n: -_lead_xy(housing, lead[n]).x)       # rightmost lead first (see the module doc)
    h1_y = 37.0
    for k, n in enumerate(sigs):
        xl = ux + _lead_xy(housing, lead[n]).x
        y_d = uy - 4.0 - 0.6 * k
        x_r = 36.0 + 0.6 * k
        y_a = 34.3 + 0.35 * k
        x_p = -33.02 + 1.27 + 2.54 * (21 + k)
        pts = [(xl, land_y), (xl, y_d), (x_r, y_d), (x_r, y_a), (x_p, y_a), (x_p, h1_y - 1.27)]
        p.append(pcb.Piece(f"{n.replace('io_', '').replace('_PAD', '')} trace", pcb.trace(pts, 0.2, STACK),
                           "Copper (Cu)", "copper", "Digital"))
    p.append(pcb.header("H1 CubeSat Kit bus", 0.0, h1_y, stack=STACK))
    p.append(pcb.header("H2 CubeSat Kit bus", 0.0, h1_y - 5.6, stack=STACK))
    p.append(pcb.box("U3 LDO 1.1 V (SOT-23-5)", ux - 8.0, uy - 7.5, 2.9, 1.6, 1.1, "Mould compound (EMC)", "U3",
                     stack=STACK))
    for i, (x, y) in enumerate([(ux + 4.5, uy + dy) for dy in (-1.5, -0.5, 0.5, 1.5)], 1):
        p.append(pcb.box(f"C{i} 100 nF 0402", x, y, 1.0, 0.5, 0.5, "Alumina 96 % (Al2O3)", "Decoupling",
                         stack=STACK))
    return p, {"in_y": y_in, "out": out_port}


def stand_in_board(name, components):
    """A PC/104 board with the bus headers and *components* (Pieces) — the
    other boards of the stack, for mass, centre of gravity and clearances."""
    p = pcb.board(stack=STACK)
    p.append(pcb.header("H1 CubeSat Kit bus", 0.0, 37.0, stack=STACK))
    p.append(pcb.header("H2 CubeSat Kit bus", 0.0, 31.4, stack=STACK))
    return p + components


def stand_ins():
    import Part
    top = STACK.top
    eps = stand_in_board("EPS", [pcb.box(f"L{i} power inductor", x, -15.0, 6.0, 6.0, 3.0, "Alumina 96 % (Al2O3)",
                                         "EPS", stack=STACK) for i, x in enumerate((-30, -15, 0, 15), 1)]
                         + [pcb.box("U1 EPS controller (QFN-48)", 25.0, 5.0, 7.0, 7.0, 0.9, "Mould compound (EMC)",
                                    "EPS", stack=STACK)])
    cells = [pcb.Piece(f"Cell {i} 18650", Part.makeCylinder(9.2, 65.0, V(-32.5, y, top + 9.2), V(1, 0, 0)),
                       "Li-ion cell 18650 (lumped)", "component", "Battery") for i, y in enumerate((-12.0, 10.0), 1)]
    bat = stand_in_board("Battery", cells)
    obc = stand_in_board("OBC", [pcb.box("U1 MCU (LQFP-64)", -10.0, 0.0, 12.0, 12.0, 1.6, "Mould compound (EMC)",
                                         "OBC", stack=STACK),
                                 pcb.box("U2 flash (SOIC-8)", 15.0, -10.0, 4.9, 6.0, 1.75, "Mould compound (EMC)",
                                         "OBC", stack=STACK)])
    return {"EPS": eps, "Battery": bat, "OBC": obc}


# -- 5: the RF path for the EM model -------------------------------------------------------------

def _global_box(obj):
    shape = obj.Shape.copy()
    shape.Placement = model.global_placement(obj)
    return shape


def die_port(chip, signal, ground, name):
    """As in lna_qfn40_on_board.py: a lumped port in the TopMetal2 gap
    between pad *signal* and pad *ground* of *chip*."""
    from pkgstudio.core import palace
    pads = {p.PadName: p for p in model.pads_of(chip)}
    sig, gnd = _global_box(pads[signal]), _global_box(pads[ground])
    inv = model.global_placement(chip).inverse()
    cs_, cg = inv.multVec(sig.BoundBox.Center), inv.multVec(gnd.BoundBox.Center)
    T, L = model.chip_technology(chip), model.chip_layout(chip)
    keys = [k for k, sp in T.layers.items() if sp.level == "TopMetal2" and not sp.name.lower().endswith(".filler")]
    lo, hi = min(cs_.y, cg.y), max(cs_.y, cg.y)
    strip = []
    for key in keys:
        strip += palace.clip(L.polygons.get(key, []), (cs_.x - 1e-4, lo, cs_.x + 1e-4, hi))
    spans = [(float(p[:, 1].min()), float(p[:, 1].max())) for p in strip]
    s_span = next(sp for sp in spans if sp[0] - 1e-6 <= cs_.y <= sp[1] + 1e-6)
    g_span = next(sp for sp in spans if sp[0] - 1e-6 <= cg.y <= sp[1] + 1e-6)
    y0, y1 = (s_span[1], g_span[0]) if cg.y > cs_.y else (g_span[1], s_span[0])
    face = sig.Faces[0]
    frame = model.contact_frame(face, chip)
    centre = model.global_placement(chip).multVec(V(cs_.x, (y0 + y1) / 2.0, cs_.z))
    centre.z = face.BoundBox.ZMax
    return ports.add_port(chip, frame, frame.to_uv(tuple(centre)), sig.BoundBox.XLength, y1 - y0,
                          z0=50.0, direction="+Y", name=name)


def build_em(report, fmin=1.0, fmax=4.0):
    """The RF path in a document of its own; returns (doc, project, report)."""
    doc = FreeCAD.newDocument(f"{NAME}_RF_EM")
    housing, package, lna, dig, _r = build_mcm(doc)
    _r.pop("_intent", None)
    pieces, at = com_board_pieces(housing, report["mcm"]["pin_map"])
    # what the EM model needs of the board: the stack, the footprint and the RF lines
    cut = pcb.cutout([q for q in pieces if q.group in ("stack", "U1", "RF in", "RF out", "RF out 2")
                      and q.kind in ("dielectric", "copper")], EM_RECT)
    board = boards.make_board(doc, cut, "COM board — RF path (EM cut-out)", STACK)
    boards.place_package(package, board, "U1")
    x0, y0, x1, y1 = EM_RECT
    ports.add_edge_port(board, V(x0, at["in_y"] - LINE_W / 2, 0), V(x0, at["in_y"] + LINE_W / 2, 0), STACK.rf_h,
                        z0=50.0, name="Port1_RFIN_line")
    axis, c = at["out"]
    if axis == "y":
        ports.add_edge_port(board, V(c - LINE_W / 2, y1, 0), V(c + LINE_W / 2, y1, 0), STACK.rf_h,
                            z0=50.0, name="Port2_RFOUT_line")
    else:
        ports.add_edge_port(board, V(x1, c - LINE_W / 2, 0), V(x1, c + LINE_W / 2, 0), STACK.rf_h,
                            z0=50.0, name="Port2_RFOUT_line")
    die_port(lna, "RFIN", "GND14", "Port3_RFIN_die")
    die_port(lna, "RFOUT", "GND3", "Port4_RFOUT_die")
    for p in ports.ports(doc)[1:]:
        p.Excited = False
    doc.recompute()
    em = {"ports": [p.Label for p in ports.ports(doc)],
          "readiness": [f"{f.level}: {f.message}" for f in readiness.check(doc)]}
    regions = {c.Name: em_model.ChipRegion(c.Name, em_model.default_rect(c), ["TopMetal2"])
               for c in model.by_role(doc, model.CHIP)}
    project = EmProject.from_document(doc, "openEMS", fmin_ghz=fmin, fmax_ghz=fmax, regions=regions,
                                      work_dir=os.path.join(OUT, "em"))
    project.set_mesh("coarse", min_cell_um=5.0)
    em.update({"band_GHz": [fmin, fmax], "model": em_model.summary(project.sim),
               "after_build": [f"{f.level}: {f.message}" for f in readiness.check_model(project.sim)],
               "otter_checks": [f"{i.level}: {i.message}" for i in project.checks()],
               "mesh": project.mesh().summary()})
    if FreeCAD.GuiUp:
        from pkgstudio.gui import otter_runner
        result, zip_path = project.build_package(otter_runner.generate, out_dir=OUT)
        em["package"] = str(zip_path) if result.ok else f"failed: {result.error}"
    return doc, project, em


# -- the whole chain ------------------------------------------------------------------------------

def build(with_em=True):
    os.makedirs(OUT, exist_ok=True)
    report = {}
    doc = FreeCAD.newDocument(NAME)

    housing, package, lna, dig, report["mcm"] = build_mcm(doc)
    intent = report["mcm"].pop("_intent")
    say(f"MCM {PACKAGE}: {len(report['mcm']['pin_map'])} wires to leads, "
        f"LVS errors {sum(f.startswith('error') for f in report['mcm']['package_lvs'])}, "
        f"{len(report['mcm']['wire_rule_rejects'])} wire-rule rejects")
    # for the assembly house and the board designer
    kinds = {p.net: p.kind for p in intent.pins}
    report["deliverables"] = list(bonding.export(housing, OUT, "CubeSat_MCM_QFN32", intent,
                                                 notes=[f"Pin list: {os.path.basename(PINLIST)}",
                                                        "Al 25 um wedge-wedge; coordinates in µm",
                                                        "GPIO6, GPIO7 not bonded"]))
    report["deliverables"] += list(kicad.export_package(housing, os.path.join(OUT, "kicad"), "CubeSat_MCM_QFN32",
                                                        kinds=kinds))

    pieces, _at = com_board_pieces(housing, report["mcm"]["pin_map"])
    com = boards.make_board(doc, pieces, "COM board — S-band receiver", STACK)
    boards.place_package(package, com, "U1")

    sat = system.make_cubesat(doc)
    for name, p in stand_ins().items():
        b = boards.make_board(doc, p, f"{name} board (stand-in)", STACK)
        system.add_board(sat, b, STACK_TOPS[name])
    system.add_board(sat, com, STACK_TOPS["COM"])
    findings, summary = system.check(sat)
    report["system"] = {"findings": [str(f) for f in findings], **summary}
    for f in findings:
        say(str(f))
    path = os.path.join(OUT, f"{NAME}.FCStd")
    doc.saveAs(path)
    report["files"] = [path]

    if with_em:
        em_doc, _project, report["em"] = build_em(report)
        em_path = os.path.join(OUT, f"{NAME}_RF_EM.FCStd")
        em_doc.saveAs(em_path)
        report["files"].append(em_path)
    with open(os.path.join(OUT, f"{NAME}_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    return doc, report


# freecadcmd runs a file under its own name, FreeCAD's macro runner as __main__
if (__name__ in ("__main__", "cubesat_sband_mcm") or os.environ.get("PS_EXAMPLE_RUN")) and not os.environ.get("PS_EXAMPLE_NO_RUN"):
    build(with_em=os.environ.get("PS_EXAMPLE_EM", "1") != "0")
