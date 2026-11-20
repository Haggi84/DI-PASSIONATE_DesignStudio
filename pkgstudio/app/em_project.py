# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
One EM setup of an assembly: the simulation model, its OTTER settings, and
the package of solver files written from them.

It is OTTER_Local's Project with three things changed, so the assistant's
tools (otter_llm.assistant.tools), which drive a Project, work unchanged:

  * The model comes from the open document (app.em_model), not from a file
    read by a second FreeCAD.
  * Ports come from the document: every port drawn with Ports becomes a
    lumped port with its own impedance; the ones marked Excited are driven,
    the first of them is the active one.
  * Generation runs in this FreeCAD. The generator is passed in from the
    GUI, because the OTTER add-on's dialog needs it.

A material chosen here for a layer is written back to the Material property
of the parts in it (remember_materials), so the Materials panel, the
exports and the next EM setup all say the same.
"""

import json
import os
import shutil
import zipfile
from pathlib import Path

import FreeCAD
import Part

from ..core import otter_path
from . import em_materials, em_model, materials as mats, model

otter_path.ensure()

from otter_llm.mesh import script_mesh_file  # noqa: E402
from otter_llm.overview import overview_svg  # noqa: E402
from otter_llm.project import Project, run_note  # noqa: E402
from otter_llm.settings import SOLVERS  # noqa: E402
from otter_llm.starter import NOT_SIMULATED, build_settings, groups  # noqa: E402


class GenerationResult:
    """What a generator reports back; the fields of otter_llm.generate.GenerateResult."""

    def __init__(self, ok, solver=None, files=(), warnings=(), error=None, log=""):
        self.ok = bool(ok)
        self.solver = solver
        self.files = list(files)
        self.warnings = list(warnings)
        self.error = error
        self.log = log


def excitation_axis(port):
    """
    OTTER's direction for a lumped port: the global axis its excitation runs
    along ("x", "y" or "z"). A port lies in a face's plane or stands on an
    edge, and its Direction says which of its own axes is excited; OTTER
    assumed z for every port, which is wrong for one lying flat between a
    lead and the paddle — "the port has zero length". The sign is not
    needed: openEMS runs a lumped port from its box's start to its end.
    """
    if port is None or not hasattr(port, "Direction"):
        return "z"
    from . import ports as pt
    d = pt.describe(port).get("direction") or [0.0, 0.0, 1.0]
    return "xyz"[max(range(3), key=lambda k: abs(d[k]))]


def default_work_dir(doc_name):
    """Where packages go unless the panel says otherwise."""
    return Path(FreeCAD.getUserAppDataDir()) / "PackageStudio" / "em" / str(doc_name or "document")


_SIZE_OLD = """	def size(x):
		x = np.atleast_1d(x)[:, None]
		return np.minimum(maxRes, np.min(h + g * np.maximum(0, np.maximum(a - x, x - b)), axis=1))
"""
_SIZE_NEW = """	def size(x):
		# Package Studio: evaluated in chunks, the same numbers: all points against
		# all regions at once took gigabytes on a board cut-out (hundreds of regions)
		x = np.atleast_1d(np.asarray(x, dtype=float))
		out = np.empty(len(x))
		for i in range(0, len(x), 20000):
			xc = x[i:i + 20000][:, None]
			out[i:i + 20000] = np.min(h + g * np.maximum(0, np.maximum(a - xc, xc - b)), axis=1)
		return np.minimum(maxRes, out)
"""


def lighten_script(script):
    """The graded-mesh helper of a generated openEMS script with its size()
    evaluated in chunks (same result). True when the script was changed."""
    path = Path(script)
    text = path.read_text(encoding="utf-8", errors="replace")
    if _SIZE_OLD not in text:
        return False
    path.write_text(text.replace(_SIZE_OLD, _SIZE_NEW), encoding="utf-8")
    return True


def _lighten_otter_mesh():
    """The same chunked size() for OTTER's own copy of the helper, which
    otter_llm.mesh loads to work out a script's exact mesh: patched in memory
    once (the add-on's files stay as they are). True when in place."""
    try:
        import inspect
        import otter_llm.mesh as M
        helpers = M._helpers()
        if getattr(helpers, "_ps_chunked", False):
            return True
        src = inspect.getsource(helpers.meshGradedLines)
        if _SIZE_OLD not in src:
            return False
        ns = dict(helpers.__dict__)
        exec(src.replace(_SIZE_OLD, _SIZE_NEW), ns)
        helpers.meshGradedLines = ns["meshGradedLines"]
        helpers._ps_chunked = True
        return True
    except Exception:
        return False


def _script_mesh_affordable(script, budget_bytes=1.5e9):
    """Whether the exact mesh of a generated openEMS script can be worked out
    here. OTTER's graded-mesh helper samples up to 400 000 points per gap,
    each against every mesh region of the axis at once: with hundreds of
    regions (a board cut-out with its pours and vias) that is gigabytes, and
    it ran the FreeCAD session out of memory. The model's own estimate stays."""
    import re
    text = Path(script).read_text(errors="replace")
    per_axis = [len(re.findall(rf"^\s*mesh_regions\['{d}'\]\.append", text, re.M)) for d in "xyz"]
    return max(per_axis) * 400_000 * 8 * 4 <= budget_bytes


class EmProject(Project):
    """An OTTER project on a simulation model built from the open document."""

    def __init__(self, sim, solver="openEMS", work_dir=None):
        if solver not in SOLVERS:
            raise ValueError(f"solver must be one of {SOLVERS}")
        self.sim = sim
        work_dir = Path(work_dir or default_work_dir(sim.source_doc))
        settings = build_settings(sim.model, sim.design, solver)
        super().__init__(work_dir, work_dir / f"{sim.doc.Name}.FCStd", settings, sim.model, sim.design)
        self._add_document_ports()

    @classmethod
    def from_document(cls, doc, solver="openEMS", fmin_ghz=1.0, fmax_ghz=30.0, air_margin_mm=None,
                      work_dir=None, regions=None, progress=None):
        sim = em_model.build(doc, fmax_ghz=fmax_ghz, fmin_ghz=fmin_ghz, air_margin_mm=air_margin_mm,
                             regions=regions, progress=progress)
        return cls(sim, solver, work_dir)

    def source(self):
        return FreeCAD.getDocument(self.sim.source_doc)

    def _add_document_ports(self):
        source = self.source()
        first = None
        for number, port in enumerate(self.sim.ports, start=1):
            name = f"port{number}"
            obj = source.getObject(port.source) if port.source else None
            excited = bool(getattr(obj, "Excited", True)) if obj is not None else number == 1
            if excited and first is None:
                first = name
            self.settings.add_lumped_port(name, port.label, resistance_ohm=port.impedance,
                                          direction=excitation_axis(obj), active=excited)
        if first is not None:
            self.settings.set_active_port(first)

    # -- the choices that touch the model ---------------------------------------------------------

    def set_frequency(self, fmin_ghz, fmax_ghz):
        """As Project.set_frequency, and the air box grows to λ/4 at the new fmax."""
        super().set_frequency(fmin_ghz, fmax_ghz)
        if em_model.fit_airbox(self.sim, fmax_ghz):
            self._reload_model()

    def _reload_model(self):
        """The stack again after the geometry changed, keeping every choice made so far."""
        design = self.design
        em_model.refresh_model(self.sim, design.fmin_ghz, design.fmax_ghz)
        fresh = self.sim.design
        for name, material in design.materials.items():
            if name in fresh.materials:
                fresh.materials[name] = material
        fresh.mesh, fresh.min_cell_um, fresh.cells = design.mesh, design.min_cell_um, dict(design.cells)
        self.model, self.design = self.sim.model, fresh
        self.rebuild()

    # -- materials --------------------------------------------------------------------------------

    def material_table(self):
        """One row per layer or spanning body, bottom to top:
        (group name, z min, z max, material, where it came from, [source labels])."""
        rows = []
        for group in groups(self.model):
            material = self.design.materials.get(group.name, NOT_SIMULATED)
            if group.name in self.sim.guessed and material == self.sim.guessed[group.name]:
                origin = "guessed by OTTER from names and colours"
            else:
                sources = {self.sim.part(label).material_source for label in group.bodies if self.sim.part(label)}
                origin = "; ".join(sorted(s for s in sources if s)) or "chosen here"
            labels = [self.sim.part(label).source_label or label for label in group.bodies if self.sim.part(label)]
            rows.append((group.name, group.zmin, group.zmax, material, origin, labels))
        return rows

    def remember_materials(self, group_names=None):
        """
        Write the materials of guessed or changed groups back onto the
        assembly as Material, so the Materials panel and the next EM setup
        start from them. Chip metal built from the layout is written to its
        layer objects. Returns how many objects changed.
        """
        source = self.source()
        changed = 0
        for group in groups(self.model):
            if group_names is not None and group.name not in group_names:
                continue
            material = self.design.materials.get(group.name, NOT_SIMULATED)
            value = em_materials.NOT_SIMULATED_NAME if material == NOT_SIMULATED else material
            if " (via fill " in value:
                continue                  # a scaled via array: its layer keeps the PDK material
            for label in group.bodies:
                part = self.sim.part(label)
                if part is None or part.material == material:
                    continue
                targets = []
                if part.source:
                    targets = [source.getObject(part.source)]
                elif part.kind == "metal":
                    chip = source.getObject(part.chip)
                    T = model.chip_technology(chip)
                    targets = [o for o in chip.Group if model.role(o) == model.LAYER
                               and T.layers.get((o.GdsLayer, o.GdsDatatype)) is not None
                               and T.layers[(o.GdsLayer, o.GdsDatatype)].level == part.level]
                for obj in targets:
                    if obj is not None:
                        mats.set_material(obj, value)
                        changed += 1
                part.material, part.material_source = material, f"Material: {value}"
        self.sim.guessed = {k: v for k, v in self.sim.guessed.items()
                            if group_names is not None and k not in group_names}
        return changed

    # -- package ----------------------------------------------------------------------------------

    def _add_new_faces(self):
        """Port faces added here (OTTER's 'port between layers') as faces of the model."""
        doc = self.sim.doc
        for label, points in self.new_faces.items():
            vectors = [FreeCAD.Vector(*p) for p in points]
            face = Part.Face(Part.makePolygon(vectors + [vectors[0]]))
            obj = next(iter(doc.getObjectsByLabel(label)), None) or doc.addObject("Part::Feature", label)
            obj.Label = label
            obj.Shape = face
            if getattr(obj, "ViewObject", None) is not None:
                obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0)
        doc.recompute()

    def build_package(self, generator, out_dir=None):
        """
        Write the solver files and zip them with the simulation model and its
        settings. *generator(doc, ini_path, sim_dir, solver)* runs the OTTER
        add-on and returns a GenerationResult. Returns (result, zip path or None).
        """
        solver = self.settings.solver
        root = Path(out_dir or self.work_dir) / "package"
        package_dir = root / f"{self.name}_{solver}"
        shutil.rmtree(package_dir, ignore_errors=True)
        sim_dir, project_dir = package_dir / "simulation", package_dir / "project"
        sim_dir.mkdir(parents=True)
        project_dir.mkdir(parents=True)

        self._add_new_faces()
        fcstd = project_dir / f"{self.name}.FCStd"
        # The add-on names its output after the document's file: save first.
        self.sim.doc.saveAs(str(fcstd))
        self.fcstd = fcstd
        ini = project_dir / f"{self.name}.ini"
        self.settings.save(ini)

        result = generator(self.sim.doc, ini, sim_dir, solver)
        if not result.ok:
            return result, None

        self.package_mesh = None
        if solver == "openEMS":
            scripts = [sim_dir / f for f in result.files if f.endswith(".py") and "/" not in f.replace(os.sep, "/")]
            for sc in scripts:
                try:
                    lighten_script(sc)
                except OSError:
                    pass
            try:
                if scripts and (_lighten_otter_mesh() or _script_mesh_affordable(scripts[0])):
                    self.package_mesh = script_mesh_file(scripts[0])
            except Exception:
                pass
        (package_dir / "README.txt").write_text(self.readme(solver, result), encoding="utf-8")
        try:
            svg = overview_svg(self.model, self.settings)
            if svg:
                (package_dir / "overview.svg").write_text(svg, encoding="utf-8")
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageStudio] overview picture skipped: {exc}\n")
        (package_dir / "materials.json").write_text(json.dumps(
            [{"group": g, "zmin_mm": z0, "zmax_mm": z1, "material": m or "not simulated",
              "values": em_materials.described(m), "origin": origin, "parts": labels}
             for g, z0, z1, m, origin, labels in self.material_table()], indent=2), encoding="utf-8")

        zip_path = root / f"{self.name}_{solver}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        return result, zip_path

    def readme(self, solver, result):
        scripts = [f for f in result.files if f.endswith(".py") and "/" not in f.replace(os.sep, "/")]
        text = (f"{self.name}: {solver} simulation written by Package Studio with the OTTER add-on.\n\n"
                "RUN\n\n" + run_note(solver, scripts[0] if scripts else "<script>.py")
                + "\nFILES\n\n"
                "    overview.svg     side and top view of the structure\n"
                "    materials.json   the material of every layer, its values and where it came from\n"
                "    simulation/      solver script and the geometry it loads\n"
                f"    project/         {self.name}.FCStd and {self.name}.ini, the flat simulation model:\n"
                "                     open them in FreeCAD with the OTTER add-on to change the setup there\n")
        regions = []
        for name, region in self.sim.regions.items():
            x0, y0, x1, y1 = region.rect
            regions.append(f"    {name}: x {x0:.4f}..{x1:.4f}, y {y0:.4f}..{y1:.4f} mm (chip frame), "
                           f"levels {', '.join(region.levels) or 'none'}")
        if regions:
            text += "\nCHIP METAL (exact, from the GDS, inside these regions)\n\n" + "\n".join(regions) + "\n"
        if self.package_mesh is not None:
            text += f"\nMESH\n\n    {self.package_mesh.summary()}\n"
        issues = [i for i in self.checks() if i.level != "info"]
        if issues:
            text += "\nCHECKS (OTTER debugging findings)\n\n" + "\n".join(
                f"  - {i.level.upper()}: {i.message}" for i in issues) + "\n"
        if result.warnings:
            text += "\nWARNINGS FROM THE EXPORT\n\n" + "\n".join(f"  - {w}" for w in result.warnings) + "\n"
        if self.sim.guessed:
            text += ("\nGUESSED MATERIALS\n\n    No material was on record for: " + ", ".join(self.sim.guessed)
                     + ".\n    OTTER guessed them from names and colours; check them.\n")
        return text
