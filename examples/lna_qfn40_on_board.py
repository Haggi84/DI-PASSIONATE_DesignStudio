# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Example: the SG13G2 LNA (ALL_LNA.gds) in an open-cavity QFN-40 6x6 on its
RF test board — an assembly ready for the EM simulation.

Built with the same functions the toolbar buttons call, in the order of the
README's "An assembly, step by step":

  1. Technology        IHP SG13G2
  2. Import GDS chips  ALL_LNA.gds (780 x 980 um, 38 pads)
  3. Housing library   JEDEC "QFN-40 6x6", chip on the paddle, 25 um die attach
  4. Bond wires        25 um Al wedge-wedge, by name — a GSG triple per RF port,
                       the bias pads, VTMP. The on-wafer calibration structures
                       (OPEN, SHORT, LOAD, THROUGH, BIG) are not bonded:
                         lead 5 / 6 / 7     GND14 / RFIN  / GND15
                         lead 24 / 25 / 26  GND4  / RFOUT / GND3
                         lead 13 15 17 19   VCC, IDC, IHEAT, GNDHEAT
                         lead 36            VTMP
  5. Import STEP part  the RF strip of the RO4350B test board (data/…_rf_cutout.step,
                       from lna_pcb/build_step.py): 0.254 mm core, 50-ohm GCPW
                       on y = -0.25 mm, ground plane, via fences, 35 um copper
  6. Place             the package onto the board's footprint: its leads on the
                       QFN lands, its centre on the board origin
  7. Ports             four lumped 50-ohm ports, so the result is the S-matrix
                       of everything between connector and die, to be cascaded
                       with the LNA's own S-parameters in a circuit simulator:
                         Port1 / Port2  at the board edges, standing on the
                                        line's end from the ground plane (z 0)
                                        up to the line (z 0.254); Port1 excited
                         Port3 / Port4  on the die, flat in the TopMetal2 gap
                                        between RFIN / RFOUT and its GND pad
                                        above (GND14 / GND3) — where the
                                        on-wafer probe of the LNA's data sat
  8. Materials         RO4350B and copper on the board; the rest comes from
                       the PDK and the housing library
  9. EM simulation     readiness, model, OTTER's checks; with the GUI also the
                       package (openEMS by default, 0.5 - 10 GHz). Of the die
                       only TopMetal2 is built (the pads and what joins them):
                       the circuit below is the LNA's measured S-parameters, not
                       the EM model's job, and its 1-um metals would make the
                       mesh 150 M cells. Mesh "coarse": about 26 M cells, 4 GB.

Run it from FreeCAD's Python console (Macro > Macros… or exec(open(...).read())),
or headless:  freecadcmd examples/lna_qfn40_on_board.py
The headless run builds and checks everything and saves the .FCStd; the
OTTER add-on that writes the solver scripts needs the GUI, so the package is
written only when FreeCAD's window is up.

The board's numbers (why 0.44 mm line, 0.20 mm gap, why lands from 2.05 mm)
are in the board project's README.
"""

import json
import os
import sys

import FreeCAD

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.path.join(
    FreeCAD.getUserAppDataDir(), "Mod", "PackageStudio", "examples")
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pkgstudio.app import em_model, housings, materials, model, moving, placing, ports, readiness, wires  # noqa: E402
from pkgstudio.app.em_project import EmProject  # noqa: E402
from pkgstudio.core import jedec, palace  # noqa: E402

V = FreeCAD.Vector
DATA = os.path.join(HERE, "data")
GDS = os.environ.get("PS_EXAMPLE_GDS", os.path.join(DATA, "ALL_LNA.gds"))
BOARD = os.environ.get("PS_EXAMPLE_BOARD", os.path.join(DATA, "LNA_QFN40_JEDEC_RO4350B_copper_rf_cutout.step"))
TECH = os.path.join(ROOT, "technologies", "IHP-SG13G2")
OUT = os.environ.get("PS_EXAMPLE_OUT", os.path.join(HERE, "out"))
NAME = "LNA_QFN40_on_RO4350B"

# Board (data/…step, frame of lna_pcb/build_step.py): origin = package centre,
# z 0 = top of the ground plane, line underside at H, copper top at H + T.
H, T = 0.254, 0.035
LINE_Y, LINE_W, EDGE_X = -0.25, 0.44, 14.9      # the GCPW and where its land starts at each edge

WIRES = [("GND14", 5), ("RFIN", 6), ("GND15", 7),
         ("GND4", 24), ("RFOUT", 25), ("GND3", 26),
         ("VCC", 13), ("IDC", 15), ("IHEAT", 17), ("GNDHEAT", 19),
         ("VTMP", 36)]
BOARD_MATERIALS = (("RO4350B", "Rogers RO4350B"), ("Copper", "Copper (Cu)"))
DIE_PORTS = (("RFIN", "GND14"), ("RFOUT", "GND3"))   # signal pad, the ground pad next to it
CHIP_LEVELS = ["TopMetal2"]                          # what of the die the EM model builds
MESH = "coarse"
# 4 um off centre in y, nothing for the assembly (the land is 1.3 mm long): centred, the edge of Port4
# on the die lies 2 um from the edge of the board's RF line, two mesh lines openEMS must both keep —
# a 2 um cell that sets the time step of the whole model. 4 um further it is 6 um.
MESH_NUDGE_Y = 0.004
MIN_CELL_UM = 5.0      # openEMS: the time step follows the smallest cell; 5 um instead of 1-2 um is 2.5x faster


def say(text):
    FreeCAD.Console.PrintMessage(f"[example] {text}\n")


def _faces_up(shape, z, tol=1e-6):
    return [f for f in shape.Faces if abs(f.BoundBox.ZMin - z) < tol and abs(f.BoundBox.ZMax - z) < tol
            and f.normalAt(*f.Surface.parameter(f.CenterOfMass)).z > 0.99]


def _global_box(obj):
    shape = obj.Shape.copy()
    shape.Placement = model.global_placement(obj)
    return shape


def die_port(chip, signal, ground, name):
    """A lumped port lying in the TopMetal2 plane, across the gap between the
    metal of pad *signal* and that of pad *ground* (the pads' openings are
    smaller than their metal, so the gap is read from the layout)."""
    doc = chip.Document
    pads = {p.PadName: p for p in model.all_pads(doc, "chip")}
    sig, gnd = _global_box(pads[signal]), _global_box(pads[ground])
    inv = model.global_placement(chip).inverse()
    cs, cg = inv.multVec(sig.BoundBox.Center), inv.multVec(gnd.BoundBox.Center)
    T, L = model.chip_technology(chip), model.chip_layout(chip)
    keys = [k for k, sp in T.layers.items() if sp.level == "TopMetal2" and not sp.name.lower().endswith(".filler")]
    lo, hi = min(cs.y, cg.y), max(cs.y, cg.y)
    strip = []
    for key in keys:
        strip += palace.clip(L.polygons.get(key, []), (cs.x - 1e-4, lo, cs.x + 1e-4, hi))
    spans = [(float(p[:, 1].min()), float(p[:, 1].max())) for p in strip]
    s_span = next(sp for sp in spans if sp[0] - 1e-6 <= cs.y <= sp[1] + 1e-6)
    g_span = next(sp for sp in spans if sp[0] - 1e-6 <= cg.y <= sp[1] + 1e-6)
    y0, y1 = (s_span[1], g_span[0]) if cg.y > cs.y else (g_span[1], s_span[0])
    if y1 <= y0:
        raise ValueError(f"{signal} and {ground} touch on TopMetal2")
    face = sig.Faces[0]
    frame = model.contact_frame(face, chip)
    centre = model.global_placement(chip).multVec(V(cs.x, (y0 + y1) / 2.0, cs.z))
    centre.z = face.BoundBox.ZMax
    port = ports.add_port(chip, frame, frame.to_uv(tuple(centre)), sig.BoundBox.XLength, y1 - y0,
                          z0=50.0, direction="+Y", name=name)
    return port


def build(doc_name=NAME):
    """The assembly. Returns (doc, report) — report lists what was checked."""
    for path in (GDS, BOARD, TECH):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path} is missing; see examples/README.md")
    report = {}
    doc = FreeCAD.newDocument(doc_name)

    # 1–2: the chip, against its PDK
    chip = model.import_chip(doc, GDS, TECH)
    say(f"chip {chip.Label}: {len(model.all_pads(doc, 'chip'))} pads")

    # 3: a JEDEC open-cavity QFN-40, chip on the paddle
    housing = housings.from_jedec(doc, jedec.by_name("QFN-40 6x6"))
    housings.place_chip(chip, housing, attach_t=0.025)
    package = model.assembly_owner(housing)          # a housing always has a package of its own

    # 4: wires, pad by pad
    chip_pads = {p.PadName: p for p in model.all_pads(doc, "chip")}
    leads = {p.PadName: p for p in model.pads_of(housing)}
    made = [wires.create(doc, chip_pads[pad], leads[str(lead)]) for pad, lead in WIRES]
    doc.recompute()
    report["wires"] = [w.Label for w in made]
    rules = wires.check(doc)
    report["wire_rule_findings"] = {k: [str(f) for f in v] for k, v in rules.items() if v}

    # 5: the board
    board = model.import_part(doc, BOARD, model.BOARD)
    for obj in materials._leaves(board):            # the solids, not the STEP's App::Part wrappers
        for key, material in BOARD_MATERIALS:
            if key in obj.Label:
                materials.set_material(obj, material)
                break

    # 6: the package onto the footprint. Its two RF lands are the footprint
    # faces that face up at the copper top; they lie on y = LINE_Y, so the
    # placement is centred there and the offset then puts the package centre
    # on the board's origin, 0.25 mm up, exactly over the QFN lands.
    rf = [o for o in board.OutListRecursive if o.Label.startswith(("Copper_Top_RFIN", "Copper_Top_RFOUT"))]
    faces = []
    for obj in rf:
        shape = obj.Shape.copy()
        shape.Placement = model.global_placement(obj)
        faces += _faces_up(shape, H + T)
    placing.place(package, faces, onto=board)
    hb = model.container_shape(housing).BoundBox
    moving.set_offsets(package, dx=0.0, dy=-(hb.YMin + hb.YMax) / 2.0 + MESH_NUDGE_Y)
    doc.recompute()
    hb = model.container_shape(housing).BoundBox
    report["package_centre"] = ((hb.XMin + hb.XMax) / 2.0, (hb.YMin + hb.YMax) / 2.0, hb.ZMin)

    # 7: a port at each board edge, line to ground, and one at each RF pad of the die
    for x, name in ((-EDGE_X, "Port1_RFIN_SMA"), (EDGE_X, "Port2_RFOUT_SMA")):
        ports.add_edge_port(board, V(x, LINE_Y - LINE_W / 2, 0.0), V(x, LINE_Y + LINE_W / 2, 0.0), H,
                            z0=50.0, name=name)
    for n, (signal, ground) in enumerate(DIE_PORTS, start=3):
        die_port(chip, signal, ground, f"Port{n}_{signal}_die")
    for port in ports.ports(doc)[1:]:
        port.Excited = False                         # Port1 drives; any other can be ticked instead
    doc.recompute()
    report["ports"] = [p.Label for p in ports.ports(doc)]

    # 9a: what the workbench says before a build
    found = readiness.check(doc)
    report["readiness"] = [f"{f.level}: {f.message}" for f in found]
    return doc, report


def die_port_pieces(sim, tol=1e-6):
    """{port label: how many separate solids of the chip metal it touches} (2 = across the gap)."""
    metal = [sim.doc.getObject(p.label).Shape for p in sim.parts if p.kind == "metal" and p.chip]
    out = {}
    for port in sim.ports:
        face = sim.doc.getObject(port.label).Shape
        out[port.label] = sum(1 for m in metal for solid in m.Solids
                              if face.BoundBox.intersect(solid.BoundBox) and face.distToShape(solid)[0] <= tol)
    return out


def simulate(doc, solver="openEMS", fmin_ghz=0.5, fmax_ghz=10.0, mesh=MESH, chip_levels=CHIP_LEVELS,
             write_package=None):
    """Build the EM model, run OTTER's checks and (with the GUI) write the package.
    chip_levels=None builds the default levels (TopMetal1, TopVia2, TopMetal2)."""
    regions = None
    if chip_levels:
        regions = {c.Name: em_model.ChipRegion(c.Name, em_model.default_rect(c), list(chip_levels))
                   for c in model.by_role(doc, model.CHIP)}
    project = EmProject.from_document(doc, solver, fmin_ghz=fmin_ghz, fmax_ghz=fmax_ghz, regions=regions,
                                      work_dir=os.path.join(OUT, "em"))
    if mesh:
        project.set_mesh(mesh, min_cell_um=MIN_CELL_UM)
    report = {
        "model": em_model.summary(project.sim),
        "after_build": [f"{f.level}: {f.message}" for f in readiness.check_model(project.sim)],
        "otter_checks": [f"{i.level}: {i.message}" for i in project.checks()],
        "materials": [(g, round(z0, 4), round(z1, 4), m, o) for g, z0, z1, m, o, _l in project.material_table()],
        "ports": [(p.name, p.objects, p.resistance, p.active, p.direction) for p in project.settings.ports()],
        "port_contacts": em_model.port_contacts(project.sim),
        # The die's TopMetal2 is one part, so the readiness check sees one
        # conductor at Port3/4 and warns. What counts is that the port joins
        # two separate pieces of it (signal pad, ground pad):
        "die_port_pieces": die_port_pieces(project.sim),
        "mesh": project.mesh().summary() if solver == "openEMS" else project.design.mesh,
    }
    if write_package is None:
        write_package = FreeCAD.GuiUp
    if write_package:
        from pkgstudio.gui import otter_runner
        result, zip_path = project.build_package(otter_runner.generate, out_dir=OUT)
        report["package"] = str(zip_path) if result.ok else f"failed: {result.error}"
        report["package_warnings"] = result.warnings
    return project, report


def main():
    os.makedirs(OUT, exist_ok=True)
    doc, report = build()
    path = os.path.join(OUT, f"{NAME}.FCStd")
    doc.saveAs(path)
    report["saved"] = path
    project, sim_report = simulate(doc)
    report.update(sim_report)
    with open(os.path.join(OUT, f"{NAME}_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    say(f"saved {path}")
    say("readiness: " + ("; ".join(report["readiness"]) or "nothing missing"))
    say("OTTER: " + "; ".join(report["otter_checks"]))
    if "package" in report:
        say(f"package: {report['package']}")
    return doc, project, report


if (__name__ in ("__main__", "lna_qfn40_on_board") or os.environ.get("PS_EXAMPLE_RUN")) and not os.environ.get("PS_EXAMPLE_NO_RUN"):
    main()
