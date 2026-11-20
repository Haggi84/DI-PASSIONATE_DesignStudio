"""Checks of a model and its settings before the script is written, from OTTER's debugging
findings (OTTER/docs/findings): each problem here once gave a wrong or failed simulation.

Plain Python on the layer stack and the settings, no FreeCAD. The add-on's export warnings
cover what is only known while writing the script; these come earlier and point at objects.
"""
import json
import math
from dataclasses import dataclass, field

from otter_llm.model import Layer, Model, sandwiched
from otter_llm.starter import JOINT_MAX_MM, is_wire, wire_diameter
from otter_llm.settings import OtterSettings

C0 = 299_792_458.0
CONDUCTOR_SIGMA = 1e3  # S/m, above this a userdefined material counts as conductor


@dataclass
class Issue:
    level: str  # "error": the result will be wrong; "warning": likely wrong or slow; "info"
    code: str
    message: str
    finding: str = ""  # where the lesson comes from
    objects: list[str] = field(default_factory=list)


def run_checks(model: Model, settings: OtterSettings, new_faces: dict | None = None) -> list[Issue]:
    """All checks; new_faces are port faces the web app adds (label -> corner points)."""
    context = Context(model, settings, new_faces or {})
    issues = []
    for check in (check_ports_exist, check_ports_touch_metal, check_metal_priority, check_boundary_distance,
                  check_sub_micron_metal, check_emerge_scaling, check_unassigned_bodies, check_pec_losses,
                  check_dielectric_joint, check_wire_cells, check_excitation_band, check_mesh, check_port_z_cells):
        issues += check(context)
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(issues, key=lambda issue: order[issue.level])


class Context:
    def __init__(self, model: Model, settings: OtterSettings, new_faces: dict):
        self.model = model
        self.settings = settings
        self.solver = settings.solver or "openEMS"
        self.boxes = {label: body["bbox"] for label, body in model.bodies.items()}
        for label, points in new_faces.items():
            xs, ys, zs = zip(*points)
            self.boxes[label] = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]
        sizes = [b[k + 3] - b[k] for b in self.boxes.values() for k in range(3)]
        self.tol = 1e-6 * max(sizes + [1.0])

        self.material_of = {label: parent for _, parent, label in settings.assignments("Material")}
        self.conductor_materials = {name for name in settings.materials() if is_conductor(settings.sections[f"MATERIAL-{name}"])}
        self.conductors = [label for label, material in self.material_of.items()
                           if material in self.conductor_materials and label in self.boxes]
        self.dielectrics = [label for label, material in self.material_of.items()
                            if material not in self.conductor_materials and label in self.boxes]

    def is_via(self, label) -> bool:
        """Touches another conductor at its bottom and at its top face: carries current along z."""
        box = self.boxes[label]
        def metalAt(z):
            face = [box[0], box[1], z, box[3], box[4], z]
            return any(touches(face, self.boxes[other], self.tol) for other in self.conductors if other != label)
        return metalAt(box[2]) and metalAt(box[5])

    def fmax_ghz(self) -> float | None:
        excitation = self.settings.sections.get("EXCITATION-excitation", {})
        try:
            if excitation.get("type") == "sweep":
                return float(self.settings.json_value("EXCITATION-excitation", "sweep")["fmax"])
            gaussian = self.settings.json_value("EXCITATION-excitation", "gaussian")
            return float(gaussian["f0"]) + float(gaussian["fc"])
        except (TypeError, KeyError, ValueError):
            return None


def is_conductor(keys: dict) -> bool:
    if keys.get("type") in ("metal", "conducting sheet"):
        return True
    try:
        return max(float(keys.get("material_sigma") or 0), float(keys.get("material_kappa") or 0)) > CONDUCTOR_SIGMA
    except ValueError:
        return False


def touches(a, b, tol) -> bool:
    """Boxes overlap or touch."""
    return all(a[k] <= b[k + 3] + tol and b[k] <= a[k + 3] + tol for k in range(3))


def um(mm: float) -> str:
    return f"{mm * 1000:.4g} µm"


# ---------------------------------------------------------------------------------------------

def check_ports_exist(c: Context) -> list[Issue]:
    ports = c.settings.ports()
    if not ports:
        return [Issue("error", "no-port", "No port: the solver has nothing to excite and no S-parameters to compute.")]
    issues = []
    active = [p.name for p in ports if p.active]
    if not active:
        issues.append(Issue("error", "no-excited-port", "No port is excited, every S-parameter would be zero. "
                            "Tick 'excited' on one port.", objects=[p.name for p in ports]))
    elif len(active) > 1:
        issues.append(Issue("warning", "several-excited-ports", f"{len(active)} ports are excited at once "
                            f"({', '.join(active)}); S-parameters assume one excited port per run.", objects=active))
    for port in ports:
        if not port.objects:
            issues.append(Issue("error", "port-without-object", f"Port {port.name} has no FreeCAD object.", objects=[port.name]))
    return issues


def check_ports_touch_metal(c: Context) -> list[Issue]:
    issues = []
    for port in c.settings.ports():
        if port.type != "lumped" or port.direction != "z":
            continue
        for label in port.objects:
            box = c.boxes.get(label)
            if box is None:
                issues.append(Issue("error", "port-object-missing", f"Port {port.name}: object '{label}' is not in the model.",
                                    objects=[port.name, label]))
                continue
            if box[5] - box[2] <= c.tol:
                issues.append(Issue("error", "port-flat-along-z", f"Port {port.name} ({label}) has no height along z, "
                                    "its direction: the port has zero length.", "lesson 5", [port.name, label]))
                continue
            for end, z in (("bottom", box[2]), ("top", box[5])):
                endFace = [box[0], box[1], z, box[3], box[4], z]
                metal = [m for m in c.conductors if m != label and touches(endFace, c.boxes[m], c.tol)]
                if not metal:
                    issues.append(Issue(
                        "error", "port-not-touching-metal",
                        f"Port {port.name} ({label}): no conductor at its {end} end (z = {um(z)}). The port floats: "
                        "open circuit, S11 ≈ 0 dB. Put the port between the two conductors, touching both.",
                        "lesson 5: ports must touch metal exactly", [port.name, label]))
    return issues


def check_metal_priority(c: Context) -> list[Issue]:
    entries = c.settings.sections.get("PRIORITYLIST-OBJECTS", {})
    rank = {}
    for key, value in entries.items():
        parts = key.split(", ")
        if len(parts) == 3 and parts[0] == "Material":
            rank[parts[2]] = int(value)  # lower number = higher priority
    pairs = []
    for metal in c.conductors:
        for dielectric in c.dielectrics:
            if metal in rank and dielectric in rank and rank[dielectric] < rank[metal] \
                    and touches(c.boxes[metal], c.boxes[dielectric], c.tol):
                pairs.append(f"{dielectric} above {metal}")
    if not pairs or c.solver == "EMerge":
        return []
    return [Issue("warning", "dielectric-over-metal",
                  f"Dielectric has higher priority than the conductor it touches: {', '.join(pairs[:6])}"
                  f"{' …' if len(pairs) > 6 else ''}. The dielectric replaces the metal on shared faces.",
                  "lesson 4: metal wins over dielectric")]


def check_boundary_distance(c: Context) -> list[Issue]:
    if c.solver == "openEMS":
        return []  # the add-on places the PML lambda/4 away itself (air margin 'auto')
    model = c.model
    if model.airbox is None:
        if c.solver == "EMerge":
            return [Issue("error", "no-airbox", "EMerge needs an air box around the model for the absorbing boundary; "
                          "none was found (a solid enclosing everything).", "lesson 3")]
        return [Issue("info", "no-airbox", "No air box: Palace adds air around the model itself (0.3 λ at fmax).")]
    if ("BoundaryCondition", "absorbing", model.airbox) not in c.settings.assignments() and \
            not any(label == model.airbox for _, _, label in c.settings.assignments("BoundaryCondition")):
        return [Issue("warning", "airbox-without-boundary", f"The air box '{model.airbox}' has no boundary condition, "
                      "assign an absorbing boundary to it.", "lesson 3", [model.airbox])]
    fmax = c.fmax_ghz()
    if not fmax:
        return []
    inner = [c.boxes[label] for label in c.material_of if label in c.boxes and label != model.airbox]
    if not inner:
        return []
    union = [min(b[k] for b in inner) for k in range(3)] + [max(b[k] for b in inner) for k in range(3, 6)]
    outer = model.bodies[model.airbox]["bbox"]
    margin = min([union[k] - outer[k] for k in range(3)] + [outer[k] - union[k] for k in range(3, 6)])
    quarter = C0 / (fmax * 1e9) / 4 * 1000
    if margin < quarter:
        return [Issue("warning", "boundary-too-close",
                      f"The absorbing boundary is only {margin:.3g} mm from the structure, less than λ/4 = {quarter:.3g} mm "
                      f"at {fmax:g} GHz. Radiation is reflected back; make the air box larger.",
                      "lesson 3: boundary at least λ/4 away", [model.airbox])]
    return []


def check_sub_micron_metal(c: Context) -> list[Issue]:
    if c.solver == "EMerge":
        return []
    thin = []
    for label in c.conductors:
        material = c.settings.sections[f"MATERIAL-{c.material_of[label]}"]
        box = c.boxes[label]
        thickness = box[5] - box[2]
        if material.get("type") != "conducting sheet" and 0 < thickness < 1e-3 and not c.is_via(label):
            thin.append(f"{label} ({um(thickness)})")
    if not thin:
        return []
    return [Issue("warning", "sub-micron-metal",
                  f"Sub-micron conductors as volumes: {', '.join(thin)}. Their cells shrink the time step "
                  "(openEMS) and the element quality (FEM); model them as conducting sheets.",
                  "lesson 6: sheet models for sub-micron metals", [t.split(" (")[0] for t in thin])]


def check_emerge_scaling(c: Context) -> list[Issue]:
    if c.solver != "EMerge":
        return []
    scale = (c.settings.json_value("MESHSETTINGS-solver", "params") or {}).get("emerge", {}).get("scaleFactor", 1.0)
    sizes = [b[k + 3] - b[k] for label, b in c.boxes.items() if label in c.material_of for k in range(3) if b[k + 3] - b[k] > 0]
    smallest = min(sizes, default=math.inf)
    if float(scale) == 1.0 and smallest < 10e-3:
        return [Issue("warning", "emerge-scaling",
                      f"Smallest part {um(smallest)}: in metres it is close to the OpenCASCADE tolerance and gmsh can "
                      "fail. Set the EMerge geometry scale factor to 1000 (EMerge 3.0).", "EMerge findings")]
    return []


def check_dielectric_joint(c: Context) -> list[Issue]:
    """A thin dielectric with metal on both faces: solder or die attach set as dielectric leaves the metal above
    joined only through a capacitor (QFN example: S21 -27 dB at 1 GHz instead of -0.3 dB)."""
    metalBoxes = [c.boxes[other] for other in c.conductors]

    def besideVias(box):  # oxide between a chip's vias: conductors of the same height next to it join the metals
        return any(abs(o[2] - box[2]) <= c.tol and abs(o[5] - box[5]) <= c.tol
                   and all(box[k] <= o[k + 3] + c.tol and o[k] <= box[k + 3] + c.tol for k in range(2)) for o in metalBoxes)

    joints = []
    for label in c.dielectrics:
        box = c.boxes[label]
        thickness = box[5] - box[2]
        if not 0 < thickness <= JOINT_MAX_MM:
            continue
        if sandwiched(box, metalBoxes, c.tol) and not besideVias(box):
            joints.append(label)
    if not joints:
        return []
    return [Issue("warning", "dielectric-joint",
                  f"Thin dielectric between two conductors: {', '.join(joints[:8])}{' …' if len(joints) > 8 else ''}. "
                  "If it is solder or a conductive die attach, give it a conductor material, otherwise the metal "
                  "above is connected only through a capacitor. A capacitor dielectric is fine.", objects=joints)]


def check_wire_cells(c: Context) -> list[Issue]:
    """openEMS takes an edge as metal when its middle lies inside the body: a wire in cells larger than its
    diameter falls apart into pieces (QFN example: 30 um bondwires in 87 um cells, open signal path)."""
    if c.solver != "openEMS":
        return []
    gridOf = {label: parent for _, parent, label in c.settings.assignments("Grid")}
    coarse = []
    for label in c.conductors:
        body = c.model.bodies.get(label)
        diameter = wire_diameter(body) if body else 0.0
        if not diameter or not is_wire(c.model, Layer(label, 0, 0, [label])):
            continue
        grid = c.settings.sections.get(f"GRID-{gridOf.get(label)}", {})
        if grid.get("type") != "Fixed Distance":
            coarse.append(f"{label} ({um(diameter)} wire, no cell size)")
            continue
        factor = {"um": 1e-3, "mm": 1.0, "nm": 1e-6, "m": 1000.0}.get(grid.get("units", "mm"), 1.0)
        spacing = json.loads(grid.get("fixedDistance") or "{}")
        cell = max(float(spacing.get(k) or 0) for k in "xyz") * factor
        if cell > diameter:
            coarse.append(f"{label} ({um(diameter)} wire, {um(cell)} cells)")
    if not coarse:
        return []
    return [Issue("warning", "wire-cells",
                  f"Wires in cells larger than the wire: {', '.join(coarse[:6])}{' …' if len(coarse) > 6 else ''}. "
                  "In the FDTD grid such a wire falls apart into pieces and the path is open. Cells of at most "
                  "diameter / 1.5 in all directions.", objects=[t.split(" (")[0] for t in coarse])]


def check_unassigned_bodies(c: Context) -> list[Issue]:
    missing = [label for group in c.model.layers + c.model.spanning for label in group.bodies
               if label not in c.material_of]
    if not missing:
        return []
    return [Issue("info", "not-simulated", f"Not in the simulation (no material, so air): {', '.join(missing[:8])}"
                  f"{' …' if len(missing) > 8 else ''}.", objects=missing)]


def check_pec_losses(c: Context) -> list[Issue]:
    pec = [label for label in c.conductors if c.settings.sections[f"MATERIAL-{c.material_of[label]}"].get("type") == "metal"]
    if not pec:
        return []
    return [Issue("info", "pec-no-loss",
                  f"{len(pec)} conductor(s) are PEC, without loss. On the PCB-to-chip benchmark lossy copper and gold "
                  "moved the S21 notch from 27.0 to 28.5 GHz (CST 28.53 GHz).", "lesson 1: losses shift the resonance", pec)]


def check_excitation_band(c: Context) -> list[Issue]:
    excitation = c.settings.sections.get("EXCITATION-excitation", {})
    if c.solver != "openEMS" or excitation.get("type") != "gaussian":
        return []
    gaussian = c.settings.json_value("EXCITATION-excitation", "gaussian") or {}
    f0, fc = float(gaussian.get("f0", 0)), float(gaussian.get("fc", 0))
    if f0 > 0 and fc < 0.9 * f0:
        return [Issue("warning", "narrow-excitation",
                      f"The Gaussian pulse covers only {f0 - fc:g}…{f0 + fc:g} GHz; outside it the S-parameters are "
                      f"noise. For broadband results set f0 = fc = fmax / 2.", "openEMS findings: broadband pulse")]
    return []


def check_mesh(c: Context) -> list[Issue]:
    if c.solver != "openEMS":
        return []
    from otter_llm.mesh import estimate

    size = estimate(c.model, c.settings)
    if size is None:
        return []
    issues = []
    if size.cells > 50e6:
        issues.append(Issue("warning", "mesh-too-large",
                            f"The mesh has {size.cells / 1e6:.0f} M cells (~{size.ram_gb:.0f} GB RAM). Use a coarser mesh "
                            "density or larger cells on the big layers; fine cells only where fields change fast "
                            "(ports, thin metals, wires).",
                            "pcb_to_chip openEMS step 9: the exported mesh had 206 M cells, the graded one 2.2 M"))
    if size.smallest_mm < 0.5e-3:
        issues.append(Issue("warning", "tiny-cell",
                            f"The smallest cell is {size.smallest_mm * 1000:.2g} µm: the FDTD timestep follows it, so "
                            "the run takes many more steps. Raise the smallest cell or the thin layer's cell size, or "
                            "check for nearly coincident faces in the model.",
                            "findings lesson 6, sub-micron cells shrink the timestep"))
    issues.append(Issue("info", "mesh", f"openEMS mesh (estimate): {size.summary()}."))
    return issues


def check_port_z_cells(c: Context) -> list[Issue]:
    """openEMS: the smallest z cell against the z-ports and the metal at their ends. With a smallest cell the
    script thins the z lines; a cell taller than the conductor a port ends on (35 um board copper under a
    50 um cell) or than a third of the port leaves the port and its metal on a line or two at best: on a
    board cut-out that gave S21 = -112 dB (the port floated). A cell of at most half that thickness keeps them."""
    if c.solver != "openEMS":
        return []
    try:
        params = c.settings.json_value("MESHSETTINGS-solver", "params") or {}
    except Exception:
        return []
    mesh = params.get("openems", {}) if isinstance(params, dict) else {}
    zcell = float(mesh.get("minCellZ_um", 0) or 0) or float(mesh.get("minCell_um", 0) or 0)
    if zcell <= 0:
        return []
    need, why = None, []
    for port in c.settings.ports():
        if port.type != "lumped" or port.direction != "z":
            continue
        for label in port.objects:
            box = c.boxes.get(label)
            if box is None or box[5] - box[2] <= c.tol:
                continue
            limit = 1000 * (box[5] - box[2]) / 3
            for z in (box[2], box[5]):
                face = [box[0], box[1], z, box[3], box[4], z]
                for m in c.conductors:
                    b = c.boxes[m]
                    if m != label and touches(face, b, c.tol) and b[5] - b[2] > c.tol:
                        limit = min(limit, 1000 * (b[5] - b[2]) / 2)
            if zcell > limit + 1e-9:
                why.append(port.name)
                need = limit if need is None else min(need, limit)
    if need is None:
        return []
    return [Issue("warning", "port-z-cells",
                  f"Smallest z cell {zcell:g} µm is coarser than the ports {', '.join(why)} and the metal at their "
                  f"ends allow (at most {need:.3g} µm): the mesh can lose the port's ends, the port then floats "
                  "(S21 near -100 dB). Lower the smallest cell, or set MIN_CELL_Z_UM when running the script.",
                  "board cut-out: 50 um cells on 35 um copper, the ports floated", list(why))]
