# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Writers for OBJ, STEP and Gmsh MSH.

Every writer takes named elements, and every name survives into the file —
an OBJ group, a STEP product, a Gmsh physical group — because a simulation
set-up assigns materials and boundary conditions by name, not by position.

OBJ is written from exact triangle meshes (see core.mesh); STEP from exact
solids; MSH by Gmsh itself from the STEP, so the volume mesh is meshed from
the exact geometry rather than from a triangulation of it.
"""

import os
import re
import shutil
import subprocess
import tempfile

import numpy as np


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "element"


# ── OBJ ─────────────────────────────────────────────────────────────────────

class Cancelled(Exception):
    """The user cancelled an export."""


CHUNK = 250_000


def write_obj(path, elements, tick=None):
    """
    *elements*: (name, vertices (n, 3), triangles (m, 3), (r, g, b)) items —
    a list, or a generator that meshes each element only when it is reached,
    so a whole chip (15 million triangles) is never in memory at once. One
    object group per element, its colour as a material in a .mtl beside it.

    *tick()* is called between chunks of 250,000 rows; returning False
    cancels. A cancelled or failed export leaves no half-written file
    behind: the .obj is removed, and the .mtl is written only at the end.
    """
    mtl_path = os.path.splitext(path)[0] + ".mtl"
    colours = []

    def rows(fh, arr, fmt):
        for i in range(0, len(arr), CHUNK):
            np.savetxt(fh, arr[i:i + CHUNK], fmt=fmt)
            if tick is not None and tick() is False:
                raise Cancelled()

    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# PackageStudio export, units: mm\nmtllib {os.path.basename(mtl_path)}\n")
            base = 1
            for name, verts, tris, rgb in elements:
                colours.append((name, rgb))
                if not len(tris):
                    continue
                fh.write(f"o {safe_name(name)}\nusemtl {safe_name(name)}\n")
                rows(fh, np.asarray(verts, dtype=float), "v %.9g %.9g %.9g")
                rows(fh, np.asarray(tris, dtype=np.int64) + base, "f %d %d %d")
                base += len(verts)
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    with open(mtl_path, "w", encoding="utf-8") as mtl:
        for name, rgb in colours:
            mtl.write(f"newmtl {safe_name(name)}\nKd {rgb[0]:.4f} {rgb[1]:.4f} {rgb[2]:.4f}\n\n")
    return path


def read_obj_counts(path):
    """{object name: (vertices, faces)} — for checking what was written."""
    counts, cur = {}, None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("o "):
                cur = line[2:].strip()
                counts[cur] = [0, 0]
            elif line.startswith("v ") and cur:
                counts[cur][0] += 1
            elif line.startswith("f ") and cur:
                counts[cur][1] += 1
    return {k: tuple(v) for k, v in counts.items()}


# ── STEP ────────────────────────────────────────────────────────────────────

def write_step(path, elements):
    """
    *elements*: [(name, Part.Shape)]. Written through a scratch document so
    every element becomes a named product in the STEP file.
    """
    import FreeCAD
    import Import

    try:
        doc = FreeCAD.newDocument("PkgDesignerStepExport", hidden=True)
    except TypeError:                     # FreeCAD before 1.0
        doc = FreeCAD.newDocument("PkgDesignerStepExport")
    try:
        objs = []
        for name, shape in elements:
            obj = doc.addObject("Part::Feature", safe_name(name))
            obj.Label = name
            obj.Shape = shape
            objs.append(obj)
        Import.export(objs, path)
    finally:
        FreeCAD.closeDocument(doc.Name)
    return path


# ── Gmsh ────────────────────────────────────────────────────────────────────

def gmsh_executable():
    """The gmsh shipped with FreeCAD, or one on PATH, or None."""
    try:
        import FreeCAD
        candidate = os.path.join(FreeCAD.getHomePath(), "bin",
                                 "gmsh.exe" if os.name == "nt" else "gmsh")
        if os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    return shutil.which("gmsh")


def write_msh(path, elements, max_size=None, dim=3, gmsh=None, timeout=3600):
    """
    *elements*: [(name, Part.Shape)], optionally followed by a mesh size and
    a material name. Each element is written to its own STEP and becomes one
    physical group of its name in a single .msh (format 4.1); elements of
    one material are also grouped as MAT_<material>, which is how a solver
    assigns properties.
    The parts are meshed independently, so the mesh is not conformal across
    touching elements. Returns the path, or raises RuntimeError with Gmsh's
    output when it fails.
    """
    gmsh = gmsh or gmsh_executable()
    if not gmsh:
        raise RuntimeError("Gmsh was not found — it ships with FreeCAD as bin/gmsh.")
    work = tempfile.mkdtemp(prefix="pkgstudio_msh_")
    lines = ['SetFactory("OpenCASCADE");', "Geometry.OCCTargetUnit = \"MM\";"]
    by_material = {}
    if max_size:
        lines.append(f"Mesh.MeshSizeMax = {float(max_size)};")
    for i, element in enumerate(elements):
        name, shape = element[0], element[1]
        size = element[2] if len(element) > 2 else None
        step = os.path.join(work, f"e{i}.step").replace("\\", "/")
        shape.exportStep(step)
        lines.append(f'e{i}() = ShapeFromFile("{step}");')
        group = "Volume" if shape.Solids else "Surface"
        lines.append(f'Physical {group}("{safe_name(name)}") = e{i}();')
        if len(element) > 3 and element[3]:
            by_material.setdefault(element[3], []).append((i, group))
        if size:
            # A size on the element's own points. A 25 um wire meshed with
            # only a global maximum got so few points round its circle that
            # the boundary crossed itself; sized by curvature instead it
            # took 33,000 elements and sometimes never finished. Sized by its
            # diameter it meshes in 0.3 s.
            lines.append(f"MeshSize{{ PointsOf{{ {group}{{e{i}()}}; }} }} = {float(size)};")
    for material, members in by_material.items():
        for group in ("Volume", "Surface"):
            ids = [f"e{i}()" for i, g in members if g == group]
            if ids:
                lines.append(f'Physical {group}("MAT_{safe_name(material)}") = {{{", ".join(ids)}}};')
    out = os.path.abspath(path).replace("\\", "/")
    lines += [f"Mesh {dim if any(e[1].Solids for e in elements) else 2};",
              "Mesh.MshFileVersion = 4.1;", f'Save "{out}";']
    geo = os.path.join(work, "model.geo")
    with open(geo, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    run = subprocess.run([gmsh, geo, "-"], capture_output=True, text=True, timeout=timeout)
    if run.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError(f"Gmsh failed ({run.returncode}):\n{run.stdout[-2000:]}\n{run.stderr[-2000:]}")
    shutil.rmtree(work, ignore_errors=True)
    return path


def msh_physical_names(path):
    """{name: dimension} from a .msh file's $PhysicalNames section."""
    names, inside = {}, False
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("$PhysicalNames"):
                inside = True
                next(fh)
                continue
            if line.startswith("$EndPhysicalNames"):
                break
            if inside:
                dim, _tag, name = line.split(maxsplit=2)
                names[name.strip().strip('"')] = int(dim)
    return names
