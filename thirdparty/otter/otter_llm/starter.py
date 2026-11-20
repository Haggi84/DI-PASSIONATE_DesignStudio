"""Settings for a model that comes without an OTTER .ini: a material per layer and a frequency
range are enough, the rest follows from the layer stack.

The rules copy the hand-made settings in OTTER's Test folder and the PCB-to-chip benchmark
(examples/pcb_to_chip/*/CHANGES.md), which gave correct S-parameters:

- openEMS: conductors with conductivity; one 'Fixed Distance' grid per layer. The add-on builds a
  graded mesh from them: each is the largest cell inside its objects, every geometry plane is a
  mesh line, so thin layers need no cells of their own. Cells (density "normal", see DENSITY):
  conductors narrowest feature / 5 in x, y (at most lambda / 20 in the densest dielectric) and
  thickness / 2 in z, dielectrics only z = thickness / 5 (line.FCStd: 100/50/17.5 um line, 50 um
  FR4); wires (round, not boxes) diameter / 1.5 in all directions, the benchmark's 20 um corridor
  around 30 um bondwires. No cell below the smallest cell (1 um): the smallest cell sets the FDTD
  timestep, a 0.04 um cell in a 0.2 um layer makes the run ~30x longer than the benchmark's 1.3 um.
  Gaussian excitation with f0 = fc = fmax / 2. tan delta becomes kappa at the centre frequency,
  openEMS ignores tan delta.
- EMerge: conductors as PEC, frequency sweep; geometry scale factor 1000 when a part is under
  10 um (the add-on's own rule). 'FEM Max Size' per layer as for Palace: EMerge's own sizing
  (resolution 0.33 = a third of the wavelength, 3 mm in air at 30 GHz) leaves wires, lines and the
  chip oxide coarse, gmsh refines only where the geometry forces it. The EMerge benchmark sets a
  size on every object (examples/pcb_to_chip/emerge).
- Palace: conductors with conductivity, 'FEM Max Size' per layer: dielectrics lambda / 20 in the
  material, conductors narrowest feature / 3, at most lambda / 20 in the dielectric they touch
  (an element is local, unlike a grid line), wires 2 diameters along the wire (density "normal");
  frequency sweep.
- Mesh density coarse / normal / fine scales these counts, a cell size set for one layer replaces
  its rule.
- EMerge and Palace: absorbing boundary on the airbox.
- Conductors thinner than 1 um become conducting sheets in openEMS and Palace (no z cells
  across them), as Metal5 in the benchmark: sub-micron cells shrink the FDTD timestep and the
  FEM element quality (docs/findings, lesson 6). Not vias: a conductor touching conductors at
  its bottom and its top carries current along z and stays a volume (benchmark TopVia1).
- Object priority: ports, conductors, dielectrics, so a conductor is never cut by the
  dielectric it touches. Global mesh settings are the add-on's defaults (they derive their
  sizes from the frequency range).
"""
import json
import math
import re
from dataclasses import dataclass, field

from otter_llm.model import Layer, Model, encloses, sandwiched
from otter_llm.settings import OtterSettings

C0 = 299_792_458.0
EPS0 = 8.8541878128e-12
SHEET_BELOW_MM = 1e-3  # conductors thinner than this are conducting sheets (openEMS, Palace)


@dataclass(frozen=True)
class Material:
    name: str
    conductor: bool
    sigma: float = 0.0  # S/m, conductors and lossy dielectrics (doped silicon)
    epsilon: float = 1.0  # relative permittivity, dielectrics
    tand: float = 0.0


MATERIALS = {m.name: m for m in [
    Material("Copper", True, sigma=5.8e7),
    Material("Gold", True, sigma=4.561e7),
    Material("Aluminium", True, sigma=3.77e7),
    Material("Solder SAC305", True, sigma=7.6e6),
    Material("Silver epoxy", True, sigma=2.5e5),  # conductive die attach (EPO-TEK H20E)
    Material("PEC", True, sigma=math.inf),
    Material("FR4", False, epsilon=4.3, tand=0.015),
    Material("Rogers RO4350B", False, epsilon=3.66, tand=0.0037),
    Material("Rogers RO4003C", False, epsilon=3.55, tand=0.0027),
    Material("Alumina", False, epsilon=9.8, tand=0.0001),
    Material("SiO2", False, epsilon=4.1),
    Material("Silicon", False, epsilon=11.9),
    Material("Silicon substrate", False, sigma=2.0, epsilon=11.9),  # chip bulk, ~50 Ohm cm
    Material("Silicon EPI", False, sigma=5.0, epsilon=11.9),
    Material("Mold compound", False, epsilon=3.9, tand=0.015),
    Material("Solder mask", False, epsilon=3.5, tand=0.025),
    Material("Air", False, epsilon=1.0),
]}
NOT_SIMULATED = ""  # material choice for a group left out of the simulation


@dataclass
class Design:
    """What the user chooses; the settings are rebuilt from it for every solver."""
    materials: dict[str, str] = field(default_factory=dict)  # layer/spanning group name -> material name or ""
    fmin_ghz: float = 1.0
    fmax_ghz: float = 30.0
    mesh: str = "normal"  # key of DENSITY
    min_cell_um: float = 1.0  # no rule makes a smaller cell
    cells: dict[str, dict] = field(default_factory=dict)  # group name -> {"xy": um, "z": um} set by the user


# cells across a conductor's narrowest width / its thickness / a dielectric's thickness / a wire's
# diameter, and cells per wavelength (densest dielectric, and air)
DENSITY = {
    "coarse": {"across": 3, "thickness": 1, "dielectric": 3, "wire": 1.5, "wavelength": 15},
    "normal": {"across": 5, "thickness": 2, "dielectric": 5, "wire": 1.5, "wavelength": 20},
    "fine": {"across": 8, "thickness": 3, "dielectric": 8, "wire": 2.5, "wavelength": 30},
}


# the add-on's defaults for the parts the web app does not change (from Test/line/line_openEMS.ini)
SIMULATION_PARAMS = {
    "max_timestamps": 1000000, "min_decrement": 1e-05,
    "BCxmin": "PML", "BCxmax": "PML", "BCymin": "PML", "BCymax": "PML", "BCzmin": "PML", "BCzmax": "PML",
    "PMLxmincells": 8, "PMLxmaxcells": 8, "PMLymincells": 8, "PMLymaxcells": 8, "PMLzmincells": 8, "PMLzmaxcells": 8,
    "OverSampling": 1, "generateJustPreview": False, "generateDebugPEC": False,
    "mFileExecCommand": "octave {opt} \"{filename}\"", "base_length_unit_m": "mm",
    "min_gridspacing_enable": False, "min_gridspacing_x": 1e-06, "min_gridspacing_y": 1e-06, "min_gridspacing_z": 1e-06,
    "base_length_unit_m_emerge": "mm", "solverEngine_emerge": "", "disableRAMCheck_emerge": False,
    "generateTouchstoneFile_emerge": True, "generateTouchstoneFileDense_emerge": False,
    "generateTouchstoneFileDenseNPoints_emerge": 1001, "useFrequencyGroups_emerge": False,
    "frequencyGroupCount_emerge": 1, "outputScriptType": "python",
}
PALACE_PARAMS = {
    "problemType": "Driven", "problemVerbose": 2, "problemOutput": "sim_result",
    "modelMeshName": "mesh/simulation_model.msh", "modelMeshBaseUnits": "mm", "linearSolverType": "SuperLU",
    "linearSolverKSPType": "GMRES", "linearSolverTolerance": 0.001, "linearSolverMaxIterationCount": 200,
    "solverOrder": 2, "solverDevice": "CPU", "saveStep": 0, "useNf2ff": False,
    "nf2ffBoundaryConditionName": "absorbing - airbox", "nf2ffFreqMHz": 15000.0, "nf2ffTheta": 0.0,
    "nf2ffPhi": 90.0, "nf2ffNSample": 11050,
}
MESH_SETTINGS = {
    "openems": {"graded": True, "finalize": True, "mergeSameMaterial": True, "maxRatio": 1.3, "cellsPerWavelength": 20,
                "mergeRel": 0.5, "mergeTol_nm": 50.0, "airMargin_mm": 0.0, "minCell_um": 0.0, "minCellZ_um": 0.0,
                "structureIntoPml": False},
    "emerge": {"scaleFactor": 1.0, "curvedBoundary": 6, "portFaceElements": 5, "safeMode": True},
    "palace": {"airSize_mm": 0.0, "airMargin_mm": 0.0, "curvature": 20, "portElements": 5, "sizeGrowth": 2.5,
               "fuseSameMaterial": True, "footRadius": 10.0, "footGrowth": 1.5, "loopFactor": 5.0, "loopPad": 4.0,
               "loopGrowth": 2.0, "threads": 0},
}
GRID_COMMON = {"coordsType": "rectangular", "generateLinesInside": "false",
               "gridOffset": json.dumps({"x": 0.0, "y": 0.0, "z": 0.0, "units": "um"}),
               "topPriorityLines": "true", "unitsAngle": "deg"}
FEM_MESH_OFF = {"femMaxSizeUnits": "mm", "femMaxElementSize": 1.0, "femMaxBoundarySize": 1.0, "femMaxFaceSize": 1.0,
                "femMaxDomainSize": 1.0, "femMaxUserDefined": "", "femSurfaceMeshSizeSizeMin": 0.1,
                "femSurfaceMeshSizeSizeMax": 10.0, "femSurfaceMeshSizeDistanceMin": 0.0,
                "femSurfaceMeshSizeDistanceMax": 10.0, "femUseMaxElementSize": False, "femUseMaxBoundarySize": False,
                "femUseMaxFaceSize": False, "femUseMaxDomainSize": False, "femUseMaxUserDefined": False,
                "femUseSurfaceMeshSize": False}

CONDUCTOR_WORDS = ("cu", "copper", "gnd", "ground", "metal", "line", "pad", "trace", "via", "tm", "bond", "wire",
                   "gold", "au", "patch", "strip", "signal")
# Names are only hints, in any language a model may use; the geometry rules in default_design() decide where the
# names say nothing. Whole words (label split at "_", " ", "-", "."), checked first: short words that would match
# inside others ("lot" in "slot", "air" in "pair").
TOKEN_WORDS = {"air": NOT_SIMULATED, "luft": NOT_SIMULATED, "domain": NOT_SIMULATED,
               "lot": "Solder SAC305", "lpi": "Solder mask", "epi": "Silicon EPI", "ag": "Silver epoxy",
               "kupfer": "Copper"}
MATERIAL_WORDS = {"fr4": "FR4", "sio2": "SiO2", "oxide": "SiO2", "gold": "Gold", "_au": "Gold", "alu": "Aluminium",
                  "4350": "Rogers RO4350B", "4003": "Rogers RO4003C", "alumina": "Alumina", "silicon": "Silicon",
                  "copper": "Copper", "pec": "PEC", "solder_mask": "Solder mask", "soldermask": "Solder mask",
                  "loetstopp": "Solder mask", "lötstopp": "Solder mask", "solder": "Solder SAC305",
                  "sac305": "Solder SAC305", "epoxy": "Silver epoxy", "die_attach": "Silver epoxy",
                  "mold": "Mold compound", "mould": "Mold compound"}
DIELECTRIC_WORDS = ("sub", "diel", "fr4", "sio2", "oxide", "core", "prepreg", "rogers", "insul")
JOINT_MAX_MM = 0.05  # a body this thin with metal on both faces is a solder joint or die attach


def groups(model: Model) -> list[Layer]:
    """Layers and spanning bodies, each gets one material."""
    return model.layers + model.spanning


def guess_from_names(model: Model, group: Layer) -> str | None:
    """Material named by the labels or the groups (App::Part) they sit in ("Bonddraehte_Au"), None if no name says."""
    containers = [model.bodies[label].get("container") or "" for label in group.bodies]
    names = " ".join(group.bodies + containers).lower()
    tokens = set(re.split(r"[\s_.\-]+", names))
    for word, material in TOKEN_WORDS.items():
        if word in tokens:
            return material
    for word, material in MATERIAL_WORDS.items():
        if word in names:
            return material
    if any(word in names for word in DIELECTRIC_WORDS):
        return "FR4"
    if any(word in names for word in CONDUCTOR_WORDS):
        return "Copper"
    return None


def guess_from_colour(model: Model, group: Layer) -> str:
    color = model.bodies[group.bodies[0]].get("color") or [0.5, 0.5, 0.5]
    yellowish = color[0] > 0.7 and color[1] > 0.45 and color[2] < 0.4  # FreeCAD users colour copper yellow/orange
    return "Copper" if yellowish else "FR4"


def guess_material(model: Model, group: Layer) -> str:
    """A starting choice from the names and colour; the user (or the assistant) corrects it."""
    named = guess_from_names(model, group)
    return named if named is not None else guess_from_colour(model, group)


def default_design(model: Model) -> Design:
    """Names first, then rules from the geometry for the groups no name explains, so a model in any language
    and from any tool gets a working start:
    - a solid enclosing every other body is the air domain (a second one beside the airbox): not simulated,
    - a thin body with metal ending at its bottom and starting at its top is a solder joint or die attach:
      as dielectric it would join the metal above only through a capacitor (open signal path),
    - a dielectric right under the chip's oxide or EPI is the chip's silicon, not a board laminate."""
    named = {group.name: guess_from_names(model, group) for group in groups(model)}
    materials = {group.name: named[group.name] if named[group.name] is not None else guess_from_colour(model, group)
                 for group in groups(model)}
    others = [label for label, body in model.bodies.items() if label != model.airbox and body.get("kind") == "solid"]
    sizes = [b["bbox"][k + 3] - b["bbox"][k] for b in model.bodies.values() for k in range(3)]
    tol = 1e-6 * max(sizes + [1.0])

    for group in groups(model):
        if named[group.name] is None and len(group.bodies) == 1:
            box = model.bodies[group.bodies[0]]["bbox"]
            if all(encloses(box, model.bodies[label]["bbox"]) for label in others if label != group.bodies[0]):
                materials[group.name] = NOT_SIMULATED

    metalBoxes = [model.bodies[label]["bbox"] for group in groups(model)
                  if materials[group.name] and MATERIALS[materials[group.name]].conductor for label in group.bodies]
    for group in groups(model):
        material = materials[group.name]
        if (named[group.name] is None and material and not MATERIALS[material].conductor
                and 0 < group.thickness <= JOINT_MAX_MM
                and all(sandwiched(model.bodies[label]["bbox"], metalBoxes, tol) for label in group.bodies)):
            materials[group.name] = "Solder SAC305"

    chipBottoms = [g.zmin for g in groups(model) if materials[g.name] in ("SiO2", "Silicon EPI")]
    for group in groups(model):
        if materials[group.name] == "FR4" and any(abs(group.zmax - z) <= 1e-4 for z in chipBottoms):
            materials[group.name] = "Silicon substrate"
    return Design(materials=materials)


def narrowest(model: Model, group: Layer) -> float:
    """Smallest in-plane width of the group's bodies (mm)."""
    sizes = []
    for label in group.bodies:
        xmin, ymin, _, xmax, ymax, _ = model.bodies[label]["bbox"]
        sizes += [size for size in (xmax - xmin, ymax - ymin) if size > 0]
    return min(sizes) if sizes else group.thickness


def smallest_dimension(model: Model, chosen) -> float:
    """Smallest non-zero bounding box side of the simulated bodies (mm)."""
    sizes = []
    for group, _ in chosen:
        for label in group.bodies:
            bbox = model.bodies[label]["bbox"]
            sizes += [bbox[k + 3] - bbox[k] for k in range(3) if bbox[k + 3] - bbox[k] > 0]
    return min(sizes, default=math.inf)


def nice(value: float) -> float:
    """3 significant digits, grid values are shown to users."""
    return float(f"{value:.3g}")


def build_settings(model: Model, design: Design, solver: str) -> OtterSettings:
    """Complete OTTER settings for `solver`; ports are added afterwards (Project carries them over)."""
    fmin, fmax = design.fmin_ghz, design.fmax_ghz
    if not 0 < fmin < fmax:
        raise ValueError("frequency range: 0 < fmin < fmax")
    if design.mesh not in DENSITY:
        raise ValueError(f"mesh density must be one of {list(DENSITY)}")
    if not design.min_cell_um > 0:
        raise ValueError("smallest cell must be > 0")
    density = DENSITY[design.mesh]
    fem = solver in ("EMerge", "Palace")

    settings = OtterSettings({"FILE-INFO": {"version": "0.1"}})
    settings.sections["EXCITATION-excitation"] = {
        "custom": json.dumps({"functionStr": "0", "f0": 0}),
        "fem_gaussian": json.dumps({"f0": 0, "fc": 0, "timeOversampling": 1}),
        "gaussian": json.dumps({"fc": fmax / 2, "f0": fmax / 2}),   # not rounded: f0 + fc is the chosen fmax
        "sinusodial": json.dumps({"f0": 0}),
        "sweep": json.dumps({"fmin": fmin, "fmax": fmax, "npoints": max(11, min(201, round((fmax - fmin) / 0.5) + 1)),
                             "resolution": 0.33}),
        "type": "sweep" if fem else "gaussian",
        "units": "GHz",
    }

    chosen = [(group, MATERIALS[design.materials[group.name]]) for group in groups(model)
              if design.materials.get(group.name, NOT_SIMULATED) != NOT_SIMULATED]
    conductorBoxes = [model.bodies[label]["bbox"] for group, m in chosen if m.conductor for label in group.bodies]
    vias = {group.name for group, m in chosen if m.conductor and is_via(model, group, conductorBoxes)}
    conductors = [(g, m) for g, m in chosen if m.conductor]
    dielectrics = [(g, m) for g, m in chosen if not m.conductor]

    usedMaterials = []
    for group, material in conductors + dielectrics:
        sectionName = materialSection(material, solver, group, vias)
        if sectionName not in usedMaterials:
            usedMaterials.append(sectionName)
            settings.sections[f"MATERIAL-{sectionName}"] = materialKeys(material, solver, (fmin + fmax) / 2, group, vias)

    if fem and model.airbox:
        settings.sections["BOUNDARYCONDITION-absorbing"] = {
            "customType": "",
            "surfaceImpedance": json.dumps({"material": None, "conductance": 0.0, "roughness": 0.0, "roughnessUnits": "nm",
                                            "thickness": 0.0, "thicknessUnits": "um"}),
            "type": "Absorbing"}

    # cells never larger than lambda / N in the densest dielectric, also on wide conductors (ground planes)
    maxEpsilon = max([m.epsilon for _, m in dielectrics] + [1.0])
    maxCell_mm = C0 / (fmax * 1e9) * 1000 / math.sqrt(maxEpsilon) / density["wavelength"]

    grids = {}
    for group, material in conductors + dielectrics:
        cap_mm = maxCell_mm
        if fem and material.conductor:
            # an element is local, a grid line runs through the whole model: a ground plane under RO4350B needs
            # no elements sized for the silicon of a chip elsewhere (QFN example: 145 um instead of 261 um, and
            # the airbox below the board filled from those faces)
            epsilon = touching_epsilon(model, group, dielectrics)
            cap_mm = C0 / (fmax * 1e9) * 1000 / math.sqrt(epsilon) / density["wavelength"]
        grid = gridKeys(model, group, material, solver, fmax, cap_mm, vias, design)
        if grid is not None:
            grids[group.name] = grid
            settings.sections[f"GRID-{group.name}"] = grid

    settings.sections["SIMULATION-Hardwired Name 1"] = {
        "name": "Hardwired Name 1", "params": json.dumps(SIMULATION_PARAMS), "paramsPalace": json.dumps(PALACE_PARAMS)}
    settings.sections["SOLVER-settings1"] = {"engine": "", "solver": solver}
    meshSettings = json.loads(json.dumps(MESH_SETTINGS))
    meshSettings["openems"]["cellsPerWavelength"] = density["wavelength"]
    if solver == "openEMS":
        minCell, minCellZ = openems_min_cells(model, chosen, vias, design)
        meshSettings["openems"].update(minCell_um=minCell, minCellZ_um=minCellZ,
                                       structureIntoPml=board_on_domain_edge(model, chosen))
    if solver == "EMerge" and smallest_dimension(model, chosen) < 10e-3:
        # the add-on's rule (checkEmergeScaleFactor): micrometre parts fail in gmsh unless scaled, EMerge >= 3.0
        meshSettings["emerge"]["scaleFactor"] = 1000.0
    settings.sections["MESHSETTINGS-solver"] = {"params": json.dumps(meshSettings)}

    for group, material in conductors + dielectrics:
        for label in group.bodies:
            settings.assign("Material", materialSection(material, solver, group, vias), label)
    if fem and model.airbox:
        settings.assign("BoundaryCondition", "absorbing", model.airbox)
    for group, _ in conductors + dielectrics:
        if group.name in grids:
            for label in group.bodies:
                settings.assign("Grid", group.name, label)
    return settings


def floor125(value: float) -> float:
    """Largest 1-2-5 step (..., 1, 2, 5, 10, ...) not above value."""
    if value <= 0:
        return 0.0
    decade = 10 ** math.floor(math.log10(value))
    return nice(max(step * decade for step in (1, 2, 5) if step * decade <= value * (1 + 1e-9)))


CHIP_STACK_GAP_MM = 2e-3  # metals closer than this face each other through a chip's inter-metal oxide


def openems_min_cells(model: Model, chosen, vias, design: Design) -> tuple[float, float]:
    """Smallest openEMS cell (um) in x/y and in z, 0 = off. The FDTD time step follows the smallest cell: the
    geometry lines alone gave 0.65 um cells and more than 10 h for a QFN package, 5 um (x/y) and 2 um (z) matched
    the Palace reference in about 1.5 h (OTTER_Local/New_Example/README.md).
    - x/y: the thinnest bondwire / cells across (5 for normal density): 25 um wire -> 5 um; no wires -> off.
    - z: the thinnest gap between two conductor bodies facing each other (overlapping in x/y, no conductor covering
      the overlap between them) / 2.5; with fewer cells the two merge (QFN: chip ground M5 5.65 um under the signal
      pad TM2, 10 um shorted them, 2 um was right). Not counted: gaps as tall as a via layer (TM1 and TM2 face each
      other where no TopVia2 sits, they are joined by it), gaps below CHIP_STACK_GAP_MM (a chip's inter-metal
      oxide, resolving it would take sub-micron cells; such metals are treated as joined), bondwires (curves).
    Only for models with micrometre parts (any side below 10 um, as the EMerge scale factor rule): a board alone
    keeps OTTER's mesh. Never below the user's smallest cell."""
    if smallest_dimension(model, chosen) >= 10e-3:
        return 0.0, 0.0
    density = DENSITY[design.mesh]
    conductors = [(g, m) for g, m in chosen if m.conductor]
    wires = [g for g, _ in conductors if is_wire(model, g)]
    xy = floor125(1000 * min(wire_size(model, g) for g in wires) / density["across"]) if wires else 0.0

    viaLevels = [(g.zmin, g.zmax) for g, _ in conductors if g.name in vias]
    boxes = [(label, model.bodies[label]["bbox"]) for g, _ in conductors if g not in wires for label in g.bodies]
    tol = 1e-9
    gaps = []
    for la, A in boxes:
        for lb, B in boxes:
            gap = B[2] - A[5]
            if gap < CHIP_STACK_GAP_MM or (gaps and gap >= min(gaps)):
                continue
            if any(abs(z0 - A[5]) <= 1e-6 and abs(z1 - B[2]) <= 1e-6 for z0, z1 in viaLevels):
                continue
            lo = [max(A[k], B[k]) for k in range(2)]
            hi = [min(A[k + 3], B[k + 3]) for k in range(2)]
            if not all(lo[k] < hi[k] for k in range(2)):
                continue
            covered = any(C[2] >= A[5] - tol and C[5] <= B[2] + tol
                          and all(C[k] <= lo[k] + tol and C[k + 3] >= hi[k] - tol for k in range(2))
                          for lc, C in boxes if lc not in (la, lb))
            if not covered:
                gaps.append(gap)
    z = floor125(1000 * min(gaps) / 2.5) if gaps else 0.0
    if xy and z:
        z = min(z, xy)
    floor = design.min_cell_um
    # z: the user's smallest cell, but never more than half the thinnest board-scale conductor (>= 10 um, a
    # board's 35 um copper, a leadframe): with 50 um z cells a board cut-out kept neither its copper nor the
    # ends of the ports standing on it (S21 = -112 dB). z lines are cheap, the cell count hardly changes.
    thick = [model.bodies[label]["bbox"][5] - model.bodies[label]["bbox"][2]
             for g, _ in conductors if g not in wires for label in g.bodies]
    thick = [t for t in thick if t >= 10e-3]
    zfloor = min(floor, floor125(1000 * min(thick) / 2)) if thick else floor
    return (max(xy, floor) if xy else 0.0), (max(z, zfloor) if z else 0.0)


def board_on_domain_edge(model: Model, chosen, tol=1e-6) -> bool:
    """A simulated body with an x or y face on the airbox's face is cut out of a bigger board: it should run into
    the PML like in the reference solvers (its end in air reflects and rings)."""
    if not model.airbox:
        return False
    air = model.bodies[model.airbox]["bbox"]
    return any(abs(model.bodies[label]["bbox"][k + side] - air[k + side]) <= tol * max(1.0, abs(air[k + side]))
               for group, _ in chosen for label in group.bodies for k in range(2) for side in (0, 3))


def touching_epsilon(model: Model, group: Layer, dielectrics, tol=1e-4) -> float:
    """Largest permittivity among the dielectrics touching or overlapping the group (1, air, if none)."""
    def touch(a, b):
        return all(a[k] <= b[k + 3] + tol and b[k] <= a[k + 3] + tol for k in range(3))
    boxes = [model.bodies[label]["bbox"] for label in group.bodies]
    return max([m.epsilon for g, m in dielectrics
                if any(touch(box, model.bodies[label]["bbox"]) for label in g.bodies for box in boxes)] + [1.0])


def is_via(model: Model, group: Layer, conductorBoxes) -> bool:
    """Every body of the group touches a conductor at its bottom and at its top face."""
    tol = 1e-6 * max(group.thickness, 1e-9) + 1e-9
    def touchesAt(bbox, z):
        return any(abs(other[5] - z) <= tol or abs(other[2] - z) <= tol
                   for other in conductorBoxes if other is not bbox and other[2] - tol <= z <= other[5] + tol
                   and all(bbox[k] <= other[k + 3] + tol and other[k] <= bbox[k + 3] + tol for k in range(2)))
    boxes = [model.bodies[label]["bbox"] for label in group.bodies]
    return all(touchesAt(b, b[2]) and touchesAt(b, b[5]) for b in boxes)


def is_sheet(material: Material, solver: str, group: Layer, vias=frozenset()) -> bool:
    return (material.conductor and not math.isinf(material.sigma) and solver in ("openEMS", "Palace")
            and 0 < group.thickness < SHEET_BELOW_MM and group.name not in vias)


def materialSection(material: Material, solver: str, group: Layer, vias=frozenset()) -> str:
    """EMerge (>= 3.0) turns a volume with sigma > 1e4 S/m into a surface impedance by itself: metals keep their
    conductivity there (PEC lost the conductor loss, QFN example 0.3 % instead of 5 % at 1 GHz); only zero-thickness
    sheets are PEC, they have no volume."""
    if material.conductor and (math.isinf(material.sigma) or (solver == "EMerge" and group.thickness <= 0)):
        return "PEC"
    name = section_safe(material.name)
    if is_sheet(material, solver, group, vias):
        return f"{name} sheet {nice(group.thickness * 1000):g} um"
    return name


def section_safe(name: str) -> str:
    """A material name as an ini section may carry it. QSettings reads "/" and "\\" as group separators:
    "IHP SG13G2 / TopMetal2" became a group "IHP SG13G2 " with nothing in it, and every chip material vanished
    from the solver script."""
    return name.replace("/", "-").replace("\\", "-")


def materialKeys(material: Material, solver: str, fcenter_ghz: float, group: Layer, vias=frozenset()) -> dict:
    if materialSection(material, solver, group, vias) == "PEC":
        return {"type": "metal"}
    if is_sheet(material, solver, group, vias):
        return {"type": "conducting sheet", "conductingSheetThicknessValue": f"{nice(group.thickness * 1000):g}",
                "conductingSheetThicknessUnits": "um", "conductingSheetConductivity": f"{material.sigma:g}",
                "conductingSheetPermeability": "1"}
    if material.conductor:
        return {"type": "userdefined", "material_epsilon": "1", "material_mue": "1", "material_kappa": "0",
                "material_sigma": f"{material.sigma:g}", "material_tand": "0"}
    kappa, sigma = 0.0, material.sigma
    if solver == "openEMS" and (material.tand > 0 or sigma > 0):
        # openEMS has no tan delta, the same loss at the centre frequency as conductivity; its sigma is magnetic,
        # so the silicon's conductivity goes into kappa as well
        kappa = 2 * math.pi * fcenter_ghz * 1e9 * EPS0 * material.epsilon * material.tand + sigma
        sigma = 0.0
    return {"type": "userdefined", "material_epsilon": f"{material.epsilon:g}", "material_mue": "1",
            "material_kappa": f"{kappa:.4g}", "material_sigma": f"{sigma:g}", "material_tand": f"{material.tand:g}"}


def wire_diameter(body: dict) -> float:
    """Diameter of a round wire from its volume and surface, pi r^2 L / (2 pi r L) = d / 4 (mm, 0 if unknown)."""
    volume, area = body.get("volume") or 0.0, body.get("area") or 0.0
    return 4 * volume / area if volume > 0 and area > 0 else 0.0


def wire_size(model: Model, group: Layer) -> float:
    """Thinnest wire of the group (mm), the narrowest bounding box side when the diameter is unknown."""
    return min(wire_diameter(model.bodies[label]) or narrowest(model, Layer(group.name, 0, 0, [label]))
               for label in group.bodies)


def is_wire(model: Model, group: Layer) -> bool:
    """Round, long bodies in 3D (bondwires): no box, rising in z and running in x, y by several diameters (a
    leadframe, a pad, a via bar or a solder layer is about as thick as its 4 V / A), every bounding box side at
    least the diameter, and one tube: its length (area / (pi d)) at least the bounding box's longest side.
    A wire lying flat or standing upright is meshed as any conductor, which resolves it as well."""
    def wire(body):
        if body.get("area") is None:  # no surface area from FreeCAD: any body that is not a box
            return not body.get("is_box", True)
        d = wire_diameter(body)
        if body.get("is_box", True) or d == 0:
            return False
        sides = [body["bbox"][k + 3] - body["bbox"][k] for k in range(3)]
        length = body["area"] / (math.pi * d)
        return min(sides) >= 0.9 * d and sides[2] >= 3 * d and max(sides[:2]) >= 3 * d and length >= 0.9 * max(sides)
    return all(wire(model.bodies[label]) for label in group.bodies)


def cell_sizes(model: Model, group: Layer, material: Material, solver: str, fmax_ghz: float, maxCell_mm: float,
               vias=frozenset(), design: Design | None = None) -> dict[str, float]:
    """Largest cell (um) of the group: {"xy", "z"} for openEMS ("z" missing for sheets, "xy" for dielectrics),
    {"xy"} (element size) for EMerge and Palace. A size the user set replaces the rule."""
    design = design or Design()
    density = DENSITY[design.mesh]
    floor = design.min_cell_um

    def um(size_mm):
        return nice(max(1000 * size_mm, floor))

    if solver == "openEMS":
        if material.conductor and is_wire(model, group):
            # the wire's diameter, not its bounding box: a 30 um bondwire loop is 130 um wide, and in 87 um cells
            # the wire falls apart into pieces (QFN example)
            xy = um(min(wire_size(model, group) / density["wire"], maxCell_mm))
            sizes = {"xy": xy, "z": xy}
        elif material.conductor:
            sizes = {"xy": um(min(narrowest(model, group) / density["across"], maxCell_mm))}
            if group.thickness > 0 and not is_sheet(material, solver, group, vias):  # no z cells across a sheet
                sizes["z"] = um(group.thickness / density["thickness"])
        else:
            sizes = {"z": um(group.thickness / density["dielectric"])}
    else:  # EMerge, Palace
        wavelength_mm = C0 / (fmax_ghz * 1e9) * 1000 / math.sqrt(max(material.epsilon, 1.0))
        if material.conductor and is_wire(model, group):
            # the round surface is resolved by curved boundary meshing (6 segments around), the size only sets the
            # elements along the wire: 2 diameters (normal), the benchmark has 100 um on its bondwires
            size = min(3 * wire_size(model, group) / density["wire"], maxCell_mm)
        elif material.conductor:
            size = min(narrowest(model, group) / density["across"] * 5 / 3, maxCell_mm)
        else:
            size = wavelength_mm / density["wavelength"]
        sizes = {"xy": um(size)}
    for key, value in design.cells.get(group.name, {}).items():
        if key in sizes and value:
            sizes[key] = float(value)
    return sizes


def gridKeys(model: Model, group: Layer, material: Material, solver: str, fmax_ghz: float, maxCell_mm: float,
             vias=frozenset(), design: Design | None = None) -> dict | None:
    sizes = cell_sizes(model, group, material, solver, fmax_ghz, maxCell_mm, vias, design)
    if not sizes:
        return None
    if solver == "openEMS":
        xy, z = sizes.get("xy", 0), sizes.get("z", 0)
        spacing = {"x": xy, "y": xy, "z": z if z else xy}
        enabled = ["true" if xy else "false"] * 2 + ["true" if z else "false"]
        return {**GRID_COMMON, "fixedDistance": json.dumps(spacing), "type": "Fixed Distance", "units": "um",
                "xenabled": enabled[0], "yenabled": enabled[1], "zenabled": enabled[2]}

    # EMerge, Palace
    femMesh = dict(FEM_MESH_OFF, femMaxElementSize=nice(sizes["xy"] / 1000), femUseMaxElementSize=True)
    return {**GRID_COMMON, "femMesh": json.dumps(femMesh), "type": "FEM Max Size", "units": "mm",
            "xenabled": "false", "yenabled": "false", "zenabled": "false"}
