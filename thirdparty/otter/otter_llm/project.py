"""One user's work in the web app: the uploaded model and settings, port edits, and the
download package (solver files + the edited model and settings + how to run it).
"""
import json
import os
import shlex
import shutil
import zipfile
from pathlib import Path

from otter_llm.checks import Issue, run_checks
from otter_llm.generate import GenerateResult, generate
from otter_llm.mesh import MeshSize, estimate, script_mesh_file
from otter_llm.model import Model, read_model
from otter_llm.overview import overview_svg
from otter_llm.ports import PortSpec, check_new_port, port_face
from otter_llm.settings import OtterSettings
from otter_llm.starter import Design, build_settings, default_design, groups

RUN_NOTES = {
    "openEMS": "Needs openEMS with its Python modules (CSXCAD, openEMS).\n\n    cd {sim}\n    {python} {script}\n",
    "EMerge": "Needs EMerge (pip install emerge).\n\n    cd {sim}\n    {python} {script}\n",
    "Palace": "Needs Palace, MPI and the gmsh Python module.\n\n    cd {sim}\n    {palace_env}bash run_palace.sh"
              "          # NP=8 ... to choose the MPI process count\n",
}


def run_note(solver: str, script: str) -> str:
    """How to run the package; OTTER_Local passes the user's solver Python, Palace binary and mpirun."""
    python = os.environ.get("OTTER_LLM_SOLVER_PYTHON", "")
    palace = {"PY": python, "PALACE_BIN": os.environ.get("OTTER_LLM_PALACE_BIN", ""),
              "MPIRUN": os.environ.get("OTTER_LLM_MPIRUN", "")}  # the variables run_palace.sh reads
    palace_env = "".join(f"{key}={shlex.quote(value)} " for key, value in palace.items() if value)
    return RUN_NOTES[solver].format(sim="simulation", script=script, python=shlex.quote(python) if python else "python3",
                                    palace_env=palace_env)


class Project:
    """With an uploaded .ini its settings are edited; without one (`design` set) the settings are
    rebuilt from the user's choices whenever solver, materials or frequency change."""

    def __init__(self, work_dir: Path, fcstd: Path, settings: OtterSettings, model: Model, design: Design | None = None):
        self.work_dir = work_dir
        self.fcstd = fcstd
        self.settings = settings
        self.model = model
        self.design = design
        self.new_faces: dict[str, list] = {}  # face label -> corner points, for ports added here
        self.package_mesh: MeshSize | None = None  # exact openEMS mesh of the last package

    @classmethod
    def open(cls, work_dir, fcstd, ini=None) -> "Project":
        """Read the uploaded model (FreeCAD, ~13 s) and settings, or start settings from the model."""
        model = read_model(fcstd, view_dir=Path(work_dir) / "view")
        if ini is not None:
            return cls(Path(work_dir), Path(fcstd), OtterSettings.load(ini), model)
        design = default_design(model)
        return cls(Path(work_dir), Path(fcstd), build_settings(model, design, "openEMS"), model, design)

    # -- choices -------------------------------------------------------------------------------

    def set_solver(self, solver: str):
        if self.design is None:
            self.settings.solver = solver  # the uploaded grids and excitation stay as they are
        else:
            self.rebuild(solver)

    def set_material(self, group: str, material: str):
        self.design.materials[group] = material
        self.rebuild()

    def set_frequency(self, fmin_ghz: float, fmax_ghz: float):
        old = (self.design.fmin_ghz, self.design.fmax_ghz)
        self.design.fmin_ghz, self.design.fmax_ghz = fmin_ghz, fmax_ghz
        try:
            self.rebuild()
        except ValueError:
            self.design.fmin_ghz, self.design.fmax_ghz = old
            raise

    def set_mesh(self, density: str | None = None, min_cell_um: float | None = None):
        old = (self.design.mesh, self.design.min_cell_um)
        if density is not None:
            self.design.mesh = density
        if min_cell_um is not None:
            self.design.min_cell_um = float(min_cell_um)
        try:
            self.rebuild()
        except ValueError:
            self.design.mesh, self.design.min_cell_um = old
            raise

    def set_cell(self, group: str, xy_um: float | None = None, z_um: float | None = None):
        """Largest cell of one layer (um); None or 0 goes back to the rule."""
        if group not in [g.name for g in groups(self.model)]:
            raise ValueError(f"no layer or spanning body '{group}'")
        for value in (xy_um, z_um):
            if value is not None and value < 0:
                raise ValueError("cell size must be > 0")
        sizes = {key: float(value) for key, value in (("xy", xy_um), ("z", z_um)) if value}
        if sizes:
            self.design.cells[group] = sizes
        else:
            self.design.cells.pop(group, None)
        self.rebuild()

    def cells_of(self, group_name: str) -> dict[str, float]:
        """Largest cells of a layer as the settings use them (um), {} when it has none."""
        grid = self.settings.sections.get(f"GRID-{group_name}")
        if grid is None:
            return {}
        if grid.get("type") == "FEM Max Size":
            return {"xy": round(json.loads(grid["femMesh"])["femMaxElementSize"] * 1000, 6)}
        spacing = json.loads(grid["fixedDistance"])
        sizes = {}
        if grid.get("xenabled") == "true":
            sizes["xy"] = spacing["x"]
        if grid.get("zenabled") == "true":
            sizes["z"] = spacing["z"]
        return sizes

    def mesh(self) -> MeshSize | None:
        """openEMS mesh the settings give (estimate, ~1 s for a chip model, remembered)."""
        return estimate(self.model, self.settings)

    def rebuild(self, solver: str | None = None):
        """New settings from the design, keeping the ports."""
        settings = build_settings(self.model, self.design, solver or self.settings.solver)
        for port in self.settings.ports():
            settings.add_lumped_port(port.name, port.objects[0], resistance_ohm=float(port.resistance.split()[0]),
                                     direction=port.direction or "z", active=port.active)
        self.settings = settings

    @property
    def name(self) -> str:
        return self.fcstd.stem

    # -- layers --------------------------------------------------------------------------------

    def materials_of(self, labels) -> list[str]:
        assigned = {label: parent for _, parent, label in self.settings.assignments("Material")}
        return sorted({assigned[label] for label in labels if label in assigned})

    # -- ports ---------------------------------------------------------------------------------

    def next_port_name(self, new_face: bool = True) -> str:
        """portN not used yet; for a port that gets a new face also no object labelled portN_face."""
        taken = {port.name for port in self.settings.ports()}
        number = 1
        while f"port{number}" in taken or (new_face and f"port{number}_face" in self.model.bodies):
            number += 1
        return f"port{number}"

    def add_port(self, spec: PortSpec):
        check_new_port(self.model, self.settings, spec)
        points = port_face(self.model, spec)
        self.settings.add_lumped_port(spec.name, spec.face_label, resistance_ohm=spec.resistance_ohm, active=spec.active)
        if spec.active:
            self.settings.set_active_port(spec.name)
        self.new_faces[spec.face_label] = points

    def remove_port(self, name: str):
        for port in self.settings.ports():
            if port.name == name:
                for label in port.objects:
                    self.new_faces.pop(label, None)
        self.settings.remove_port(name)

    def add_existing_port(self, face_label: str, name: str, resistance_ohm: float = 50, active: bool = False):
        """A port on a face or box already drawn in the model."""
        if face_label not in self.model.ports:
            raise ValueError(f"'{face_label}' is not a port face of the model")
        if name in [port.name for port in self.settings.ports()]:
            raise ValueError(f"port '{name}' already exists")
        self.settings.add_lumped_port(name, face_label, resistance_ohm=resistance_ohm, active=active)
        if active:
            self.settings.set_active_port(name)

    def unused_port_faces(self) -> list[str]:
        used = {label for port in self.settings.ports() for label in port.objects}
        return [label for label in self.model.ports if label not in used]

    def is_new_port(self, name: str) -> bool:
        return any(label in self.new_faces for port in self.settings.ports() if port.name == name for label in port.objects)

    # -- checks --------------------------------------------------------------------------------

    def checks(self) -> list[Issue]:
        return run_checks(self.model, self.settings, self.new_faces)

    # -- package -------------------------------------------------------------------------------

    def build_package(self) -> tuple[GenerateResult, Path | None]:
        """Generate the solver files and zip them with the edited model and settings."""
        solver = self.settings.solver
        root = self.work_dir / "package"
        shutil.rmtree(root, ignore_errors=True)
        packageDir = root / f"{self.name}_{solver}"
        simDir = packageDir / "simulation"
        projectDir = packageDir / "project"
        projectDir.mkdir(parents=True)

        faces = [{"label": label, "points": points} for label, points in self.new_faces.items()]
        result = generate(self.fcstd, self.settings, simDir, add_faces=faces, save_fcstd=projectDir / self.fcstd.name)
        if not result.ok:
            return result, None
        self.settings.save(projectDir / f"{self.name}.ini")
        self.package_mesh = None
        if solver == "openEMS":
            scripts = [simDir / name for name in result.files if name.endswith(".py") and "/" not in name]
            try:
                self.package_mesh = script_mesh_file(scripts[0]) if scripts else None
            except Exception:  # the exact size is a bonus, the package is complete without it
                pass
        (packageDir / "README.txt").write_text(self.readme(solver, result))
        (packageDir / "overview.svg").write_text(overview_svg(self.model, self.settings, self.work_dir / "view"))

        zipPath = root / f"{self.name}_{solver}.zip"
        with zipfile.ZipFile(zipPath, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(packageDir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        return result, zipPath

    def readme(self, solver: str, result: GenerateResult) -> str:
        scripts = [name for name in result.files if name.endswith(".py") and "/" not in name]
        text = (f"{self.name}: {solver} simulation generated by OTTER_LLM with the OTTER add-on.\n\n"
                f"RUN\n\n" + run_note(solver, scripts[0] if scripts else "<script>.py")
                + "\nFILES\n\n"
                "    overview.svg  side and top view of the structure\n"
                "    simulation/   solver script and the geometry it loads\n"
                f"    project/      {self.fcstd.name} and {self.name}.ini, open them in FreeCAD with the OTTER\n"
                "                  add-on to change the setup (ports added in the web app are included)\n")
        if self.package_mesh is not None:
            text += f"\nMESH\n\n    {self.package_mesh.summary()}\n"
        issues = [issue for issue in self.checks() if issue.level != "info"]
        if issues:
            text += "\nCHECKS (OTTER debugging findings)\n\n" + "\n".join(
                f"  - {issue.level.upper()}: {issue.message}" for issue in issues) + "\n"
        if result.warnings:
            text += "\nWARNINGS FROM THE EXPORT\n\n" + "\n".join(f"  - {w}" for w in result.warnings) + "\n"
        return text
