"""openEMS mesh size: an estimate from the model and settings before anything is generated, and the
exact size from a generated script.

Both use the add-on's own meshGradedLines() (GeneratedScriptHelpers_openems.py, plain numpy), the
function the generated script runs, so the numbers are what openEMS will get. Graded mesh only:
'Fixed Distance' grids are the largest cell inside their objects' bounding box, the smallest
wins where they overlap, every geometry plane is a mesh line, cells grow by at most 30 % up to
lambda / N in air, and the domain is the structure plus lambda / 4 of air plus the PML cells.
With a smallest cell (mesh settings minCell_um / minCellZ_um) the lines are thinned like the script's
applyMinCell(): conductor faces first, then the rest.
"""
import ast
import importlib.util
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from otter_llm.config import otter_addon_dir
from otter_llm.model import Model
from otter_llm.settings import OtterSettings

C0 = 299792458.0
BYTES_PER_CELL = 160  # benchmark: 2.17 M cells, 0.35 GB


@dataclass
class MeshSize:
    nx: int
    ny: int
    nz: int
    smallest_mm: float
    timestep_s: float  # Courant limit of the smallest cells in vacuum

    @property
    def cells(self) -> int:
        return self.nx * self.ny * self.nz

    @property
    def ram_gb(self) -> float:
        return self.cells * BYTES_PER_CELL / 1e9

    def summary(self) -> str:
        return (f"{self.nx} × {self.ny} × {self.nz} = {self.cells / 1e6:.2f} M cells, smallest cell "
                f"{self.smallest_mm * 1000:.3g} µm, about {self.ram_gb:.2g} GB RAM")


@lru_cache(maxsize=1)
def _helpers():
    path = otter_addon_dir() / "utilsOtter" / "ScriptLinesGenerator" / "GeneratedScriptHelpers_openems.py"
    spec = importlib.util.spec_from_file_location("otter_mesh_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def axis_lines(fixed, regions, max_res, struct_min, struct_max, pml=(8, 8), air_margin=0.0, ratio=1.3, merge_rel=0.5):
    """meshFinalizeLines() of the generated script for a graded mesh, without CSXCAD."""
    ends = [struct_min - air_margin - pml[0] * max_res, struct_max + air_margin + pml[1] * max_res]
    domain = list(fixed) + [r[0] for r in regions] + [r[1] for r in regions] + ends
    return _helpers().meshGradedLines(list(fixed) + [min(domain), max(domain)], regions, max_res, ratio, merge_rel)


def min_cells_mm(meshSettings: dict) -> list[float]:
    """Smallest cell [x, y, z] in mm from the openEMS mesh settings, 0 = off."""
    xy = float(meshSettings.get("minCell_um", 0) or 0)
    z = float(meshSettings.get("minCellZ_um", 0) or 0) or xy
    return [xy / 1000, xy / 1000, z / 1000]


def thin_lines(lines, minCells, conductorBoxes=(), must=()):
    """meshMinCell() of the generated script on each axis with a smallest cell; conductorBoxes are bounding boxes
    [xmin, ymin, zmin, xmax, ymax, zmax], must are lines kept (port planes)."""
    import numpy as np

    boxes = [(None, np.array([b[:3], b[3:]], dtype=float)) for b in conductorBoxes]
    return [_helpers().meshMinCell(v, boxes, k, minCells[k], [m[k] for m in must]) if minCells[k] > 0 else v
            for k, v in enumerate(lines)]


def mesh_size(lines) -> MeshSize:
    import numpy as np

    smallest = [float(np.min(np.diff(v))) for v in lines if len(v) > 1]
    dt = 1 / (C0 * math.sqrt(sum(1 / (float(np.min(np.diff(v))) * 1e-3) ** 2 for v in lines if len(v) > 1)))
    return MeshSize(*(len(v) for v in lines), min(smallest), dt)


# -- from a generated script ---------------------------------------------------------------------

def script_mesh(script: str) -> MeshSize | None:
    """Exact mesh of a generated openEMS script (graded mesh with finalization), None otherwise."""
    values = {}
    for name in ("mesh_graded", "mesh_finalize", "mesh_max_ratio", "mesh_merge_rel", "structure_min", "structure_max",
                 "pml_cells"):
        match = re.search(rf"^{name} = (.+?)(\s+#.*)?$", script, re.M)
        if match is None:
            return None
        values[name] = ast.literal_eval(match.group(1))
    if not (values["mesh_graded"] and values["mesh_finalize"]):
        return None
    f0 = re.search(r"^f0 = ([0-9.e+-]+)\*([0-9.e+-]+)$", script, re.M)
    fc = re.search(r"^fc = ([0-9.e+-]+)\*([0-9.e+-]+)$", script, re.M)
    unit = re.search(r"^unit\s*= ([0-9.e+-]+)", script, re.M)
    cells = re.search(r"^mesh_max_res = wavelength_min / ([0-9.]+)", script, re.M)
    if not (f0 and fc and unit and cells) or "max_res = C0 / (f0 + fc) / 20" not in script:
        return None
    fmax = float(f0.group(1)) * float(f0.group(2)) + float(fc.group(1)) * float(fc.group(2))
    wavelength = C0 / fmax / float(unit.group(1))
    air = re.search(r"^mesh_air_margin = ([0-9.e+-]+)e-3 / unit", script, re.M)
    air_margin = float(air.group(1)) * 1e-3 / float(unit.group(1)) if air else wavelength / 4

    fixed = {d: ast.literal_eval(m) for d, m in re.findall(r"^mesh_fixed\['([xyz])'\] = (\[.*\])$", script, re.M)}
    regions = {"x": [], "y": [], "z": []}
    for d, args in re.findall(r"^\s*mesh_regions\['([xyz])'\]\.append\((\(.*?\))\)", script, re.M):
        regions[d].append(ast.literal_eval(args))
    lines = [axis_lines(fixed[d], regions[d], wavelength / float(cells.group(1)), values["structure_min"][k],
                        values["structure_max"][k], values["pml_cells"][d], air_margin, values["mesh_max_ratio"],
                        values["mesh_merge_rel"]) for k, d in enumerate("xyz")]
    xy = re.search(r"^mesh_min_cell_um = float\(os\.environ\.get\('MIN_CELL_UM', ([0-9.e+-]+)\)\)", script, re.M)
    z = re.search(r"^mesh_min_cell_z_um = float\(os\.environ\.get\('MIN_CELL_Z_UM', ([0-9.e+-]+)\)\)", script, re.M)
    if xy:  # no conductor boxes without CSXCAD: lines are thinned in order, close to what the script gets
        toUnit = 1e-6 / float(unit.group(1))
        xyCell = float(xy.group(1)) * toUnit
        zCell = float(z.group(1)) * toUnit if z else xyCell
        lines = thin_lines(lines, [xyCell, xyCell, zCell])
    return mesh_size(lines)


# -- estimate from the model -------------------------------------------------------------------

_estimates: dict = {}


def estimate(model: Model, settings: OtterSettings) -> MeshSize | None:
    """estimate_mesh(), remembered per model and settings (the checks ask after every change)."""
    key = (id(model), settings.dumps())
    if key not in _estimates:
        if len(_estimates) > 64:
            _estimates.clear()
        try:
            _estimates[key] = estimate_mesh(model, settings)
        except Exception:  # an estimate must never block the page or the checks
            _estimates[key] = None
    return _estimates[key]


def estimate_mesh(model: Model, settings: OtterSettings) -> MeshSize | None:
    """Mesh the settings will give, from bounding boxes (fixed lines at every body's faces).

    The real script also puts lines at sheet corners and inside thin STL layers, so it can be a
    little larger. None when it cannot be estimated (no graded openEMS mesh, no Gaussian pulse).
    """
    if settings.solver != "openEMS":
        return None
    meshSettings = json.loads(settings.sections.get("MESHSETTINGS-solver", {}).get("params", "{}")).get("openems", {})
    if not (meshSettings.get("graded", True) and meshSettings.get("finalize", True)):
        return None
    excitation = settings.sections.get("EXCITATION-excitation", {})
    if excitation.get("type") != "gaussian":
        return None
    pulse = json.loads(excitation.get("gaussian", "{}"))
    scale = {"Hz": 1, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}.get(excitation.get("units", "GHz"), 1e9)
    fmax = (pulse.get("f0", 0) + pulse.get("fc", 0)) * scale
    if fmax <= 0:
        return None
    wavelength = C0 / fmax * 1000  # mm, the model is in mm
    max_res = wavelength / meshSettings.get("cellsPerWavelength", 20)
    air_margin = meshSettings.get("airMargin_mm") or wavelength / 4

    simulated = {label for _, _, label in settings.assignments("Material")}
    simulated |= {label for port in settings.ports() for label in port.objects}
    boxes = [model.bodies[label]["bbox"] for label in simulated if label in model.bodies]
    if not boxes:
        return None

    regions = {"x": [], "y": [], "z": []}
    for _, grid, label in settings.assignments("Grid"):
        section = settings.sections.get(f"GRID-{grid}", {})
        if section.get("type") != "Fixed Distance" or label not in model.bodies:
            continue
        spacing = json.loads(section.get("fixedDistance", "{}"))
        factor = {"um": 1e-3, "mm": 1.0, "nm": 1e-6, "m": 1000.0}.get(section.get("units", "mm"), 1.0)
        bbox = model.bodies[label]["bbox"]
        for k, d in enumerate("xyz"):
            if section.get(f"{d}enabled") == "true" and spacing.get(d, 0) > 0:
                regions[d].append((bbox[k], bbox[k + 3], spacing[d] * factor))

    params = json.loads(settings.sections.get("SIMULATION-Hardwired Name 1", {}).get("params", "{}"))
    lines = []
    for k, d in enumerate("xyz"):
        fixed = sorted({round(b[k], 6) for b in boxes} | {round(b[k + 3], 6) for b in boxes})
        pml = tuple(int(params.get(f"PML{d}{side}cells", 8)) if params.get(f"BC{d}{side}", "PML") == "PML" else 0
                    for side in ("min", "max"))
        lines.append(axis_lines(fixed, regions[d], max_res, fixed[0], fixed[-1], pml, air_margin,
                                meshSettings.get("maxRatio", 1.3), meshSettings.get("mergeRel", 0.5)))
    minCells = min_cells_mm(meshSettings)
    if max(minCells) > 0:
        conductors = {name[len("MATERIAL-"):] for name, section in settings.sections.items() if name.startswith("MATERIAL-")
                      and (section.get("type") in ("metal", "conducting sheet") or float(section.get("material_sigma", 0) or 0) > 1e4)}
        conductorBoxes = [model.bodies[label]["bbox"] for _, material, label in settings.assignments("Material")
                          if material in conductors and label in model.bodies]
        portBoxes = [model.bodies[label]["bbox"] for port in settings.ports() for label in port.objects if label in model.bodies]
        lines = thin_lines(lines, minCells, conductorBoxes, [b[:3] for b in portBoxes] + [b[3:] for b in portBoxes])
    return mesh_size(lines)


def script_mesh_file(path: Path) -> MeshSize | None:
    return script_mesh(Path(path).read_text(errors="replace"))
