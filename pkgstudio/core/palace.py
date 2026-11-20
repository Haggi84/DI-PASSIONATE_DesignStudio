# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
An EM model of the package for Palace: a conformal Gmsh mesh and config.json.

What Palace needs, and why it is built this way (the conventions follow
IHP's gds2palace, which is how the SG13G2 PDK is meant to be simulated):

  * One mesh in which every touching body shares its faces. Bodies are
    fragmented together, so a wire, the oxide it passes through and the air
    around it meet on the same triangles. Meshed separately, the field
    would see gaps and overlaps at every interface.
  * Metals are not meshed inside. A metal is a hole in the mesh whose
    surface carries its conductivity (a Palace "Conductivity" boundary), as
    in gds2palace: meshing the skin depth inside a 3 um aluminium track at
    10 GHz would take more elements than the rest of the package.
  * Where two bodies overlap, the one that is really there wins: a
    conductor over a dielectric, the die's oxide over the air. A bond foot
    sunk into the passivation is metal, not oxide.
  * Fill is left out of the chip metal (purpose "filler"): gds2palace reads
    purpose 0 only, and IHP's flow removes fill before any EM run.
  * Lumped ports are rectangles from a conductor to the ground it is
    measured against, a 50 ohm resistor across them; air-box walls are
    absorbing.

Gmsh runs in a separate process (FreeCAD's own python.exe, see main()): a
mesh can take minutes, and a crash inside Gmsh must not take FreeCAD down.
Nothing here imports FreeCAD.
"""

import json
import math
import os
import subprocess
import sys
import time

C0_MM = 299792458.0e3          # speed of light, mm/s

CONDUCTOR, DIELECTRIC, AIR = "conductor", "dielectric", "air"
_RANK = {CONDUCTOR: 2, DIELECTRIC: 1, AIR: 0}


class Cancelled(Exception):
    """The user cancelled."""


class MissingProperty(ValueError):
    """A material lacks a value Palace needs."""


# ── layout ──────────────────────────────────────────────────────────────────

def clip(polygons, rect, precision=1e-7):
    """The parts of *polygons* (each (n, 2), mm) inside *rect* (x0, y0, x1, y1)."""
    import gdstk
    import numpy as np
    if not polygons:
        return []
    x0, y0, x1, y1 = rect
    return clip_to(polygons, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], precision)


def clip_to(polygons, outline, precision=1e-7):
    """The parts of *polygons* (each (n, 2), mm) inside the polygon
    *outline* [(x, y)] — a turned rectangle, say. Polygons wholly outside
    its extent are dropped before the boolean, so a 600,000-via layer costs
    only what lies near it."""
    import gdstk
    import numpy as np
    if not polygons:
        return []
    out = np.asarray(outline, dtype=float)
    x0, y0 = out.min(axis=0)
    x1, y1 = out.max(axis=0)
    keep = []
    for p in polygons:
        p = np.asarray(p, dtype=float)
        if (p[:, 0].max() < x0 or p[:, 0].min() > x1 or p[:, 1].max() < y0 or p[:, 1].min() > y1):
            continue
        keep.append(gdstk.Polygon(p))
    if not keep:
        return []
    return [np.asarray(q.points, dtype=float)
            for q in gdstk.boolean(keep, gdstk.Polygon(out), "and", precision=precision)]


def polygon_area(p):
    """The area of polygon *p* (n, 2) — the shoelace formula."""
    import numpy as np
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def merge_vias(polys):
    """
    [(outline (n, 2), fill)]: via arrays merged into one shape each, and
    how much of it is via — as gds2palace merges via arrays. Every via
    grows by 0.75 via widths (half the spacing inside an array), they are
    united and shrunk back, so neighbours in an array join and separate
    arrays do not; a ring of vias stays a ring. Bounding boxes did not: in a
    corner of 6_final, 3,965 vias round a power ring became one box over the
    whole region, 3 % via, with no metal above or below most of it.
    """
    import gdstk
    import numpy as np
    from . import pads
    if not polys:
        return []
    sizes = sorted(max(np.ptp(p[:, 0]), np.ptp(p[:, 1])) for p in polys)
    d = 0.75 * sizes[len(sizes) // 2]
    grown = gdstk.offset([gdstk.Polygon(p) for p in polys], d, join="miter", precision=1e-7)
    merged = [np.asarray(m.points, dtype=float) for m in gdstk.offset(grown, -d, join="miter", precision=1e-7)]
    merged = [m for m in merged if len(m) >= 3 and polygon_area(m) > 0]
    area = [0.0] * len(merged)
    boxes = [(m[:, 0].min(), m[:, 1].min(), m[:, 0].max(), m[:, 1].max()) for m in merged]
    for p in polys:
        x, y = float(p[:, 0].mean()), float(p[:, 1].mean())
        for k, (x0, y0, x1, y1) in enumerate(boxes):
            if x0 <= x <= x1 and y0 <= y <= y1 and pads.point_in_polygon(x, y, merged[k]):
                area[k] += polygon_area(p)
                break
    return [(m, min(1.0, a / polygon_area(m))) for m, a in zip(merged, area)]


def max_cell(fmax_hz, eps_max, cells_per_wavelength=10):
    """The largest element that still resolves the shortest wavelength, mm."""
    return C0_MM / (fmax_hz * math.sqrt(max(eps_max, 1.0))) / cells_per_wavelength


# ── the mesher (runs in its own process) ────────────────────────────────────

def mesh_model(job, log=print):
    """
    *job*: {"bodies": [{name, file, role, material, priority, mesh_size}],
            "sheets": [{name, file, role ("port"/"pec"), mesh_size}],
            "max_size", "min_size", "msh"}
    Each file is a BREP. Returns the physical groups written:
    {"domains": [{name, material, tag}], "conductors": [{name, material, tag, faces}],
     "sheets": [{name, role, tag}], "absorbing": tag, "elements": n}.
    """
    from . import gmsh_c as g
    g.initialize()
    try:
        g.set_number("General.Terminal", 1)
        g.set_number("General.Verbosity", 2)
        g.add_model("package")
        bodies, sheets = job["bodies"], job.get("sheets", [])

        objects, owner_of_input = [], []
        for i, b in enumerate(bodies):
            for dt in g.import_shapes(b["file"]):
                if dt[0] == 3:
                    objects.append(dt)
                    owner_of_input.append(("body", i))
        tools = []
        for j, s in enumerate(sheets):
            for dt in g.import_shapes(s["file"]):
                if dt[0] == 2:
                    tools.append(dt)
                    owner_of_input.append(("sheet", j))
        log(f"fragmenting {len(objects)} volumes and {len(tools)} sheets")
        _out, maps = g.fragment(objects, tools)

        def rank(i):
            b = bodies[i]
            return (_RANK[b["role"]], b.get("priority", 0), i)

        owner = {}                     # volume tag -> body index
        sheet_faces = {j: set() for j in range(len(sheets))}
        for (kind, idx), produced in zip(owner_of_input, maps):
            for dim, tag in produced:
                if kind == "body" and dim == 3:
                    if tag not in owner or rank(idx) > rank(owner[tag]):
                        owner[tag] = idx
                elif kind == "sheet" and dim == 2:
                    sheet_faces[idx].add(tag)
        g.synchronize()

        conductor_vols = {t for t, i in owner.items() if bodies[i]["role"] == CONDUCTOR}
        domain_vols = {t for t in owner if t not in conductor_vols}
        if not domain_vols:
            raise RuntimeError("nothing to mesh: no dielectric or air volume")

        # A conductor's surface is where it meets a meshed volume; where two
        # conductors touch, the face is inside metal and carries nothing.
        conductor_faces = {}
        for i, b in enumerate(bodies):
            if b["role"] != CONDUCTOR:
                continue
            vols = [(3, t) for t, o in owner.items() if o == i]
            if not vols:
                continue
            faces = set()
            for _d, f in g.boundary(vols, combined=False):
                up, _down = g.adjacencies(2, f)
                if any(u in domain_vols for u in up):
                    faces.add(f)
            if faces:
                conductor_faces[i] = faces

        everything = [(3, t) for t in owner]
        taken = set().union(*conductor_faces.values(), *sheet_faces.values())
        outer = [f for _d, f in g.boundary(everything, combined=True) if f not in taken]

        if conductor_vols:
            g.remove_entities([(3, t) for t in sorted(conductor_vols)], recursive=False)

        result = {"domains": [], "conductors": [], "sheets": [], "absorbing": None}
        for i, b in enumerate(bodies):
            vols = sorted(t for t in domain_vols if owner[t] == i)
            if vols:
                tag = g.add_physical_group(3, vols, b["name"])
                result["domains"].append({"name": b["name"], "material": b["material"], "tag": tag,
                                          "volumes": len(vols)})
        for i, faces in conductor_faces.items():
            b = bodies[i]
            tag = g.add_physical_group(2, sorted(faces), b["name"])
            result["conductors"].append({"name": b["name"], "material": b["material"], "tag": tag,
                                         "faces": len(faces), "thickness": b.get("thickness")})
        for j, faces in sheet_faces.items():
            if faces:
                tag = g.add_physical_group(2, sorted(faces), sheets[j]["name"])
                result["sheets"].append({"name": sheets[j]["name"], "role": sheets[j]["role"], "tag": tag,
                                         "port": sheets[j].get("port")})
        if outer:
            result["absorbing"] = g.add_physical_group(2, sorted(outer), "Absorbing_boundary")

        # Sizes: on the points of each small part, so a 25 um wire gets
        # elements of its own size without the air around it being meshed
        # that fine everywhere.
        sized = [(conductor_faces.get(i, ()), b.get("mesh_size")) for i, b in enumerate(bodies)]
        sized += [(sheet_faces[j], s.get("mesh_size")) for j, s in enumerate(sheets)]
        for faces, size in sorted(sized, key=lambda fs: -(fs[1] or 0)):
            if faces and size:
                pts = [dt for dt in g.boundary([(2, f) for f in faces], combined=False, recursive=True)
                       if dt[0] == 0]
                if pts:
                    g.set_size(pts, float(size))
        g.set_number("Mesh.MeshSizeMax", float(job["max_size"]))
        g.set_number("Mesh.MeshSizeMin", float(job.get("min_size", 0.0)))
        g.set_number("Mesh.MeshSizeFromCurvature", 0)
        g.set_number("Mesh.Algorithm", 6)
        g.set_number("Mesh.Algorithm3D", 1)
        g.set_number("Mesh.Optimize", 1)
        g.set_number("General.AbortOnError", 0)

        log("meshing")
        g.generate(3)
        empty = [t for t in domain_vols if not g.element_types(3, t)]
        if empty:
            # Delaunay can fail to recover a boundary in thin layers; HXT
            # usually does not (gds2palace retries the same way).
            log(f"{len(empty)} volume(s) without elements, retrying with HXT")
            g.clear_mesh()
            g.set_number("Mesh.Algorithm3D", 10)
            g.generate(3)
            empty = [t for t in domain_vols if not g.element_types(3, t)]
            if empty:
                raise RuntimeError(f"3-D meshing failed: {len(empty)} volume(s) have no elements")
        result["elements"] = g.element_count(3)
        g.set_number("Mesh.MshFileVersion", 2.2)      # what Palace reads
        g.set_number("Mesh.Binary", 0)
        g.set_number("Mesh.SaveAll", 0)
        g.write(job["msh"])
        return result
    finally:
        g.finalize()


def main(job_path):
    """Entry point of the mesher process: job JSON in, result JSON out."""
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    out = os.path.splitext(job_path)[0] + "_result.json"
    try:
        result = mesh_model(job, log=lambda m: print(f"[mesher] {m}", flush=True))
        result["ok"] = True
    except Exception as exc:
        import traceback
        result = {"ok": False, "error": f"{exc}\n{traceback.format_exc()}"}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    return 0 if result["ok"] else 1


def run_mesher(job, workdir, python, tick=None, timeout=7200):
    """
    Run mesh_model(*job*) in a separate *python* process; returns its result.
    *tick()* is called about twice a second and may return False to cancel,
    which stops Gmsh and raises Cancelled.
    """
    os.makedirs(workdir, exist_ok=True)
    job_path = os.path.join(workdir, "mesh_job.json")
    with open(job_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=1)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from pkgstudio.core import palace; sys.exit(palace.main(sys.argv[2]))")
    log_path = os.path.join(workdir, "mesh.log")
    flags = 0x08000000 if os.name == "nt" else 0          # no console window
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen([python, "-I", "-c", code, root, job_path], stdout=log,
                                stderr=subprocess.STDOUT, cwd=workdir, creationflags=flags)
        start = time.time()
        while proc.poll() is None:
            time.sleep(0.5)
            if (tick is not None and tick() is False) or time.time() - start > timeout:
                proc.kill()
                proc.wait()
                raise Cancelled()
    result_path = os.path.splitext(job_path)[0] + "_result.json"
    if not os.path.isfile(result_path):
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            raise RuntimeError(f"the mesher stopped ({proc.returncode}):\n{fh.read()[-3000:]}")
    with open(result_path, encoding="utf-8") as fh:
        result = json.load(fh)
    if not result.get("ok"):
        raise RuntimeError(f"meshing failed: {result.get('error', '')[-3000:]}")
    return result


# ── Palace configuration ────────────────────────────────────────────────────

def config(result, materials, ports, fstart_ghz, fstop_ghz, fstep_ghz, order=2,
           mesh_file="model.msh", l0=1e-3, output="output", absorbing_order=2):
    """
    The Palace config.json for a mesh *result* (see mesh_model).

    *materials*: {name: {"eps_r", "tan_d", "sigma", "mu_r"}}.
    *ports*: [{"index", "sheet" (its name), "direction" ("+Z", "-X", ...), "r"}].
    A dielectric without a permittivity, or a conductor without a
    conductivity, is an error naming it — a guessed value would give a
    plausible but wrong result.
    """
    def need(name, key, what):
        m = materials.get(name)
        if m is None or m.get(key) is None:
            raise MissingProperty(f"material '{name}' has no {what}; set it in the Materials panel")
        return m[key]

    domains = []
    for d in result["domains"]:
        entry = {"Attributes": [d["tag"]], "Permittivity": float(need(d["material"], "eps_r", "permittivity"))}
        m = materials[d["material"]]
        if m.get("mu_r"):
            entry["Permeability"] = float(m["mu_r"])
        if m.get("tan_d"):
            entry["LossTan"] = float(m["tan_d"])
        if m.get("sigma"):
            entry["Conductivity"] = float(m["sigma"])     # silicon: lossy, not an insulator
        domains.append(entry)

    conductors = []
    for c in result["conductors"]:
        entry = {"Attributes": [c["tag"]],
                 "Conductivity": float(need(c["material"], "sigma", "conductivity"))}
        if c.get("thickness"):
            entry["Thickness"] = float(c["thickness"])    # mesh units, like gds2palace
        conductors.append(entry)

    by_name = {s["name"]: s for s in result["sheets"]}
    lumped = []
    for p in ports:
        sheet = by_name.get(p["sheet"])
        if sheet is None:
            raise RuntimeError(f"port {p['index']} ({p['sheet']}) did not survive meshing")
        lumped.append({"Index": int(p["index"]), "R": float(p.get("r", 50.0)),
                       "Direction": p["direction"], "Excitation": int(p["index"]),
                       "Attributes": [sheet["tag"]]})
    boundaries = {"Conductivity": conductors, "LumpedPort": lumped}
    pec = [s["tag"] for s in result["sheets"] if s["role"] == "pec"]
    if pec:
        boundaries["PEC"] = {"Attributes": pec}
    if result.get("absorbing") is not None:
        boundaries["Absorbing"] = {"Attributes": [result["absorbing"]], "Order": absorbing_order}

    return {
        "Problem": {"Type": "Driven", "Verbose": 2, "Output": output},
        "Model": {"Mesh": mesh_file, "L0": l0},
        "Domains": {"Materials": domains},
        "Boundaries": boundaries,
        "Solver": {
            "Order": int(order), "Device": "CPU",
            "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": 1e-6, "MaxIts": 400},
            "Driven": {"Samples": [{"Type": "Linear", "MinFreq": float(fstart_ghz),
                                    "MaxFreq": float(fstop_ghz), "FreqStep": float(fstep_ghz),
                                    "SaveStep": 0}],
                       "AdaptiveTol": 1e-3},
        },
    }


def physical_groups(msh_path):
    """{(dim, tag): name} from a .msh file (2.2 or 4.1)."""
    out, inside = {}, False
    with open(msh_path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("$PhysicalNames"):
                inside = True
                next(fh)
                continue
            if line.startswith("$EndPhysicalNames"):
                break
            if inside:
                dim, tag, name = line.split(maxsplit=2)
                out[(int(dim), int(tag))] = name.strip().strip('"')
    return out


def msh22_stats(msh_path):
    """(nodes, distinct node positions, {element type: count}) of an ASCII
    2.2 mesh — the same position twice means two parts that should share a
    face do not: the mesh is not conformal."""
    nodes, positions, types = 0, set(), {}
    with open(msh_path, encoding="utf-8", errors="ignore") as fh:
        section = None
        for line in fh:
            if line.startswith("$"):
                section = line.strip()
                count_next = section in ("$Nodes", "$Elements")
                continue
            if count_next:
                count_next = False
                continue
            if section == "$Nodes":
                _i, x, y, z = line.split()
                nodes += 1
                positions.add((round(float(x), 9), round(float(y), 9), round(float(z), 9)))
            elif section == "$Elements":
                t = int(line.split()[1])
                types[t] = types.get(t, 0) + 1
    return nodes, len(positions), types


#: the image tools/palace/build_palace.ps1 builds
IMAGE = "pkgdesigner/palace:0.18.1"

_PS1 = r"""# Runs this Palace model in Docker, then writes the S-parameters as Touchstone.
#   .\run_palace.ps1            all {np} processes
#   .\run_palace.ps1 -Np 4      fewer, if memory is short
# The image comes from PackageStudio\tools\palace\build_palace.ps1.
param([int]$Np = {np}, [string]$Image = "{image}")
# Not "Stop": PowerShell 5.1 turns every line on stderr (mpirun's notes) into an error.
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
# Docker Desktop is often not running after a restart: start it and wait.
docker info *> $null
if ($LASTEXITCODE -ne 0) {{
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) {{
        Write-Host "Starting Docker Desktop ..."
        Start-Process $desktop
        for ($i = 0; $i -lt 60; $i++) {{ Start-Sleep 3; docker info *> $null; if ($LASTEXITCODE -eq 0) {{ break }} }}
    }}
    if ($LASTEXITCODE -ne 0) {{ Write-Host "The Docker engine is not running." -ForegroundColor Red; exit 1 }}
}}
docker image inspect $Image *> $null
if ($LASTEXITCODE -ne 0) {{
    Write-Host "The Palace image $Image is missing. Build it once with:" -ForegroundColor Red
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"{tools}\build_palace.ps1`""
    exit 1
}}
$t = Get-Date
docker run --rm --shm-size=4g -v "${{PSScriptRoot}}:/work" -w /work $Image -np $Np {config} 2>&1 |
    Tee-Object -FilePath palace.log
$code = $LASTEXITCODE
Write-Host ("Palace finished with exit code {{0}} after {{1:N1}} min" -f $code, ((Get-Date) - $t).TotalMinutes)
if ($code -ne 0) {{ exit $code }}
$csv = Join-Path $PSScriptRoot "output\port-S.csv"
if (Test-Path $csv) {{
    & "{python}" -I "{touchstone}" $csv
}} else {{
    Write-Host "No output\port-S.csv - see palace.log" -ForegroundColor Yellow
}}
"""

_BAT = """@echo off
rem Double-click to run this model in Palace (Docker); see run_palace.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_palace.ps1" %*
pause
"""

_SH = """#!/bin/sh
# Runs this model with Palace: natively if `palace` is on PATH (Linux, WSL),
# otherwise in the Docker image built by tools/palace/build_palace.ps1.
cd "$(dirname "$0")"
NP=${{NP:-{np}}}
if command -v palace >/dev/null 2>&1; then
    palace -np "$NP" {config} | tee palace.log
else
    docker run --rm --shm-size=4g -v "$PWD:/work" -w /work {image} -np "$NP" {config} | tee palace.log
fi
"""


def write_scripts(folder, config_name="config.json", np_default=8, python="python",
                  package_root=None, image=IMAGE):
    """run_palace.ps1 (and a .bat to double-click) and run_palace.sh beside
    the config: run Palace, log it, convert output/port-S.csv to .sNp."""
    package_root = package_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    values = dict(np=np_default, image=image, config=config_name, python=python,
                  tools=os.path.join(package_root, "tools", "palace"),
                  touchstone=os.path.join(package_root, "pkgstudio", "core", "touchstone.py"))
    out = []
    for name, text, nl in (("run_palace.ps1", _PS1, "\r\n"), ("run_palace.bat", _BAT, "\r\n"),
                           ("run_palace.sh", _SH, "\n")):
        path = os.path.join(folder, name)
        # PowerShell 5.1 reads a script without a BOM as the ANSI code page
        enc = "utf-8-sig" if name.endswith(".ps1") else "utf-8"
        with open(path, "w", encoding=enc, newline=nl) as fh:
            fh.write(text.format(**values))
        out.append(path)
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
