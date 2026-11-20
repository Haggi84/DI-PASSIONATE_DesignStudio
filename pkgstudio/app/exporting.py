# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Exporting the package's elements as STEP, OBJ or MSH, with every detail.

An element is one thing a simulation set-up treats as one body: a chip
layer, the die body's epi or substrate, a body of the housing or lid, a
board component, a bond wire. Each is exported under its own name.

Chip layers are rebuilt from the GDS polygons, exactly — never from what the
view shows, which may be blocks. Everything else comes from its solid as
imported or built. OBJ meshes of chip layers are exact; of curved solids
(wires, housings) they follow the given deflection.
"""

import os
from dataclasses import dataclass

import numpy as np

import FreeCAD
import Part

import csv
import json

from ..core import export as ex, mesh as msh, solids
from . import materials, model

V = FreeCAD.Vector


@dataclass
class Element:
    name: str
    kind: str          # "layer", "solid"
    obj: object
    color: tuple = (0.7, 0.7, 0.7)
    mesh_size: float = None    # a Gmsh size for this element, where it needs one
    default: bool = True       # ticked in the export dialog to begin with

    def _polygons(self, region=None):
        """(polygons, z0, z1) of a chip layer, in the chip's frame — only
        what lies inside *region* (global x0, y0, x1, y1) if one is given."""
        chip = model.parent_of(self.obj)
        L, T = model.chip_layout(chip), model.chip_technology(chip)
        key = (self.obj.GdsLayer, self.obj.GdsDatatype)
        spec = T.layers[key]
        polys = L.polygons.get(key, [])
        if region is not None:
            from ..core import palace
            inv = model.global_placement(chip).inverse()
            x0, y0, x1, y1 = region
            quad = [inv.multVec(V(x, y, 0)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            polys = palace.clip_to(polys, [(q.x, q.y) for q in quad])
        return polys, spec.z0, spec.z1

    def shape(self, progress=None, region=None):
        """The exact solid, in global coordinates; with *region* (global
        x0, y0, x1, y1), only what lies inside it, at every height — None if
        nothing does."""
        if self.kind == "layer":
            polys, z0, z1 = self._polygons(region)
            s = solids.layer_solid(polys, z0, z1, progress)
            if s is None:
                return None
            s.transformShape(model.global_placement(model.parent_of(self.obj)).toMatrix())
            return s
        # Its own placement AND every container's above it: Part.getShape
        # with transform=True applies only the object's own.
        s = self.obj.Shape.copy()
        s.Placement = model.global_placement(self.obj)
        return clip_shape(s, region) if region is not None else s

    def mesh(self, deflection=0.001, region=None):
        """(vertices, triangles) in global coordinates — inside *region* if given."""
        if self.kind == "layer":
            chip = model.parent_of(self.obj)
            if region is None:
                v, t = model.layer_mesh(chip, self.obj, "detail")
            else:
                v, t = msh.extrude(*self._polygons(region))
            m = model.global_placement(chip).toMatrix()
            a = np.array([[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]])
            return (v @ a.T + np.array([m.A14, m.A24, m.A34]) if len(v) else v), t
        import MeshPart
        shape = self.shape(region=region)
        if shape is None:
            return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection,
                                      AngularDeflection=0.2, Relative=False)
        pts, facets = mesh.Topology
        return (np.array([(p.x, p.y, p.z) for p in pts], dtype=float),
                np.array(facets, dtype=np.int64).reshape(-1, 3))


def clip_shape(shape, region):
    """*shape* (global) cut to the box over *region* (x0, y0, x1, y1) at
    every height; None if nothing of it is inside."""
    x0, y0, x1, y1 = region
    bb = shape.BoundBox
    if bb.XMax < x0 or bb.XMin > x1 or bb.YMax < y0 or bb.YMin > y1:
        return None
    if bb.XMin >= x0 and bb.XMax <= x1 and bb.YMin >= y0 and bb.YMax <= y1:
        return shape
    z0, z1 = bb.ZMin - 1.0, bb.ZMax + 1.0
    cut = shape.common(Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0)))
    if cut.isNull() or not (cut.Solids or cut.Faces):
        return None
    return cut


def _color(obj, default=(0.7, 0.7, 0.7)):
    vo = getattr(obj, "ViewObject", None)
    try:
        c = vo.ShapeColor if vo is not None else None
        return tuple(c[:3]) if c else default
    except Exception:
        return default


def _leaves(container):
    """Objects with a solid inside *container*, through nested parts; pads
    are markers, not bodies, and are left out."""
    out = []
    for o in container.Group:
        if model.role(o) == model.PAD:
            continue
        if o.isDerivedFrom("App::Part") or o.isDerivedFrom("App::DocumentObjectGroup"):
            out += _leaves(o)
        elif o.isDerivedFrom("Part::Feature") and not o.Shape.isNull() and o.Shape.Solids:
            out.append(o)
    return out


def elements(doc):
    """Every exportable element of the document, in a stable order."""
    out = []
    for chip in model.by_role(doc, model.CHIP):
        for o in chip.Group:
            if model.role(o) == model.LAYER:
                out.append(Element(f"{chip.Label}/{o.Level}_{o.GdsLayer}_{o.GdsDatatype}", "layer", o,
                                   _color(o)))
            elif model.role(o) == model.DIE_BODY:
                out.append(Element(f"{chip.Label}/{o.Label.split()[-1]}", "solid", o, _color(o)))
        # Oxide, passivation and unused levels are offered but not ticked:
        # the oxide encloses every metal layer, and two solids filling the
        # same space are wrong in any mesh; an unused level is not metal.
        for o in chip.Group:
            if model.role(o) == model.DIELECTRIC:
                out.append(Element(f"{chip.Label}/{o.Level}", "solid", o, _color(o), default=False))
        for o in chip.Group:
            if model.role(o) == model.UNUSED_LEVEL:
                out.append(Element(f"{chip.Label}/{o.Level} (not drawn)", "solid", o, _color(o),
                                   default=False))
    for da in model.by_role(doc, "die-attach"):
        out.append(Element(f"DieAttach/{da.Label}", "solid", da, _color(da)))
    for part_role in model.CONTAINER_ROLES:
        for c in model.by_role(doc, part_role):
            for o in _leaves(c):
                out.append(Element(f"{part_role.capitalize()}/{o.Label}", "solid", o, _color(o)))
    for w in model.by_role(doc, model.WIRE):
        out.append(Element(f"Wires/{w.Label}", "solid", w, (0.85, 0.65, 0.13),
                           mesh_size=0.6 * w.WireDiameter.Value))
    from . import ports
    for p in ports.ports(doc):
        out.append(Element(f"Ports/{p.Label}", "solid", p, (0.85, 0.15, 0.15),
                           mesh_size=min(p.PortWidth.Value, p.PortHeight.Value) / 4.0))
    return out


def export(elems, folder, basename, formats, per_element=False, deflection=0.001,
           msh_size=None, progress=None, region=None):
    """
    Write *elems* as each of *formats* ("step", "obj", "msh") into *folder*:
    one file per format, or one per element with *per_element*. With
    *region* (global x0, y0, x1, y1, mm, as seen from the top), only what
    lies inside it, at every height; elements wholly outside are left out.
    Returns the paths written. *progress(text)* may return False to cancel.
    """
    os.makedirs(folder, exist_ok=True)
    written = []
    groups = [[e] for e in elems] if per_element else [elems]
    lib = materials.available(elems[0].obj.Document) if elems else {}

    def colour(e):
        m = lib.get(materials.material_of(e.obj))
        return tuple(m.color) if m is not None else e.color

    def say(text):
        return progress(text) if progress else True

    for group in groups:
        stem = ex.safe_name(group[0].name) if per_element else basename
        if "obj" in formats:
            # One element at a time — meshed, written, let go — with progress
            # for each, so a whole chip neither sits in memory at once nor
            # leaves the window without a sign of life for a minute.
            def meshes():
                for i, e in enumerate(group, 1):
                    if say(f"OBJ {i}/{len(group)}: {e.name}") is False:
                        raise ex.Cancelled()
                    v, t = e.mesh(deflection, region)
                    if len(t):
                        yield (e.name, v, t, colour(e))
            try:
                written.append(ex.write_obj(os.path.join(folder, stem + ".obj"), meshes(),
                                            tick=lambda: say(f"OBJ: writing {stem}.obj")))
            except ex.Cancelled:
                return written
        if "step" in formats or "msh" in formats:
            shapes = []
            for i, e in enumerate(group):
                if say(f"Solids {i + 1}/{len(group)}: {e.name}") is False:
                    return written
                s = e.shape(lambda done, total: say(f"Solids {i + 1}/{len(group)}: {e.name} "
                                                   f"({done:,}/{total:,} polygons)"), region)
                if s is not None and not s.isNull():
                    shapes.append((e.name, s, e.mesh_size, materials.material_of(e.obj)))
            if "step" in formats:
                say("Writing STEP")
                written.append(ex.write_step(os.path.join(folder, stem + ".step"),
                                             [(n, s) for n, s, _size, _m in shapes]))
            if "msh" in formats:
                say("Meshing with Gmsh")
                written.append(ex.write_msh(os.path.join(folder, stem + ".msh"), shapes,
                                            max_size=msh_size))
        written += write_manifest(os.path.join(folder, stem), group, lib)
        port_objs = [e.obj for e in group if model.role(e.obj) == "port"
                     and (region is None or e.shape(region=region) is not None)]
        if port_objs:
            from . import ports
            path = os.path.join(folder, stem + "_ports.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"units": {"length": "mm", "impedance": "ohm"}, "region": region,
                           "ports": [ports.describe(p) for p in port_objs]}, fh, indent=1)
            written.append(path)
    return written


def write_manifest(stem, elems, lib):
    """<stem>_materials.json and .csv: each element, its material and every
    property of it — what a simulation set-up needs alongside the geometry.
    Unknown materials and missing properties are listed as such, not filled in."""
    rows = [materials.manifest_row(e, lib) for e in elems]
    with open(stem + "_materials.json", "w", encoding="utf-8") as fh:
        json.dump({"units": {"density": "kg/m3", "k": "W/(m K)", "cp": "J/(kg K)", "cte": "ppm/K",
                             "sigma": "S/m", "length": "mm"}, "elements": rows}, fh, indent=1)
    keys = ["element", "material", "category", "density", "k", "cp", "cte", "sigma", "eps_r", "mu_r",
            "tan_d", "color", "source", "unknown"]
    with open(stem + "_materials.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return [stem + "_materials.json", stem + "_materials.csv"]
