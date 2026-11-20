# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The simulation model: a flat copy of the assembly that OTTER can read.

OTTER finds everything by label and takes each object's own Shape, and the
assembly document is not built for that, in four ways that would each give
a plausible but wrong simulation:

  * Chip layers are display meshes, possibly blocks. An EM model of them
    would have no metal, or metal where the view merges vias. So each chip's
    metal is rebuilt from its GDS polygons, exactly, level by level — as the
    exports do — but only inside the chip's EM region: a whole chip cannot
    be meshed for EM (ALL_LNA has 600,000 vias) and does not need to be,
    since what the package adds is pad, wire and lead. Fill is left out; via
    arrays become blocks whose conductivity is scaled by how much of them
    is via (core.palace.merge_vias). The rest of the die is there as its
    silicon, oxide and passivation.
  * Placement. A chip sits in a package that sits on a board, and obj.Shape
    is in the innermost container's frame. Every copy is placed globally.
  * Labels repeat, carry spaces and µ signs, and one with the word "port"
    makes OTTER treat a solid as a port. Every copy gets a plain, unique one.
  * Helpers — pad markers, contact points, grids, the faint slabs of levels
    the layout does not draw — are shapes too, and none of them is matter.

So the model is a separate document: one Part::Feature per physical part,
where it really is, with the material its Material property names
(app.em_materials); one face per port; an air box round everything. The
assembly document is never changed.

The layer stack, settings and checks then come from otter_llm unchanged.
"""

import math
import re
from dataclasses import dataclass, field
from types import SimpleNamespace

import FreeCAD
import Part

from ..core import otter_path, palace, solids
from . import em_materials, materials as mats, model, ports as pt

otter_path.ensure()

from otter_llm.model import build_model, is_port_label  # noqa: E402
from otter_llm.starter import NOT_SIMULATED, default_design, groups, guess_from_names  # noqa: E402

V = FreeCAD.Vector
C0 = 299_792_458.0
AIRBOX_LABEL = "AirBox"
QUARTER_WAVE = 0.25         # OTTER findings, lesson 3: an absorbing boundary at least λ/4 away
#: polygons a build may turn into exact solids; more takes minutes and meshes for hours
MAX_POLYGONS = 60_000
REGION_MARGIN = 0.02        # mm round the wired pads


# ── which part of each chip ────────────────────────────────────────────────

@dataclass
class ChipRegion:
    """What of one chip goes into the model: a rectangle in the chip's own
    frame (mm, x0 y0 x1 y1) and the stack levels whose metal is built."""
    chip: str
    rect: tuple
    levels: list
    whole: bool = False


def wired_pads(chip):
    from . import wires
    return [w.ChipPad for w in wires.wires(chip.Document)
            if w.ChipPad is not None and model.parent_of(w.ChipPad) is not None
            and model.parent_of(w.ChipPad).Name == chip.Name]


def drawn_levels(chip):
    """[(level, kind, z0, z1)] the chip's layout draws, bottom first."""
    T, L = model.chip_technology(chip), model.chip_layout(chip)
    used = {T.layers[k].level for k in L.polygons if k in T.layers}
    return [(lv["name"], lv["kind"], lv["z0"], lv["z1"]) for lv in sorted(T.levels, key=lambda l: l["z0"])
            if lv["name"] in used]


def default_levels(chip):
    """The two highest drawn metals and the vias between them — where pads
    and top-level RF routing are."""
    drawn = drawn_levels(chip)
    metals = [d for d in drawn if d[1] == "conductor"]
    if not metals:
        return []
    lo = metals[-2:][0][2]
    return [d[0] for d in drawn if d[2] >= lo - 1e-12]


def default_rect(chip):
    """Round the chip pads that have wires, REGION_MARGIN beyond them; round
    all its pads without wires; the whole die without pads."""
    pads = wired_pads(chip) or model.pads_of(chip)
    if not pads:
        return tuple(model.chip_layout(chip).outline)
    xs0 = [p.Placement.Base.x - p.PadWidth.Value / 2 for p in pads]
    xs1 = [p.Placement.Base.x + p.PadWidth.Value / 2 for p in pads]
    ys0 = [p.Placement.Base.y - p.PadHeight.Value / 2 for p in pads]
    ys1 = [p.Placement.Base.y + p.PadHeight.Value / 2 for p in pads]
    m = REGION_MARGIN
    x0, y0, x1, y1 = model.chip_layout(chip).outline
    return (max(min(xs0) - m, x0), max(min(ys0) - m, y0), min(max(xs1) + m, x1), min(max(ys1) + m, y1))


def default_region(chip):
    """Round the wired pads, the top metals; a chip whose EmChipMetal says
    "none" (a script that models the die's pads itself) without metal."""
    region = ChipRegion(chip.Name, default_rect(chip), default_levels(chip))
    choice = getattr(chip, "EmChipMetal", "")
    metals = [d[0] for d in drawn_levels(chip) if d[1] == "conductor"]
    if choice == "none":
        region.levels = []
    elif choice == "top1":
        region.levels = metals[-1:]
    return region


def whole_region(chip):
    return ChipRegion(chip.Name, tuple(model.chip_layout(chip).outline),
                      [d[0] for d in drawn_levels(chip)], whole=True)


def _level_layers(T, levels):
    """{level: [layer keys]} of the chosen levels, fill (purpose filler) left out."""
    by_level = {}
    for key, spec in T.layers.items():
        if spec.level in levels and not spec.name.lower().endswith(".filler"):
            by_level.setdefault(spec.level, []).append(key)
    return by_level


def region_polygons(chip, region):
    """How many polygons *region* of *chip* turns into solids."""
    T, L = model.chip_technology(chip), model.chip_layout(chip)
    n = 0
    for keys in _level_layers(T, region.levels).values():
        for key in keys:
            n += len(palace.clip(L.polygons.get(key, []), region.rect))
    return n


# ── labels and shapes ──────────────────────────────────────────────────────

def plain_label(text, fallback="part"):
    """ASCII letters, digits and underscores; never a word OTTER reads as a port."""
    label = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "")).strip("_") or fallback
    if is_port_label(label):
        label = re.sub(r"(?i)ports?", "prt", label)
    if label[0].isdigit():
        label = f"{fallback}_{label}"
    return label


def _unique(label, taken):
    candidate, number = label, 2
    while candidate in taken:
        candidate = f"{label}_{number}"
        number += 1
    taken.add(candidate)
    return candidate


def global_shape(obj):
    s = obj.Shape.copy()
    s.Placement = model.global_placement(obj)
    return s


_NOT_MATTER = (model.LAYER, model.PAD, pt.ROLE, model.UNUSED_LEVEL, "em-region")


def physical_objects(doc):
    """Every object that is matter, other than chip layers (rebuilt from the
    layout): die bodies, dielectric slabs, housings, leadframes, lids, die
    attach, board parts, wires — whatever has a solid and is not a helper or
    the input of another feature."""
    out = []
    for obj in doc.Objects:
        if model.role(obj) in _NOT_MATTER or model.is_contact(obj):
            continue
        if not obj.isDerivedFrom("Part::Feature") or obj.isDerivedFrom("Part::Datum"):
            continue
        if obj.Name.startswith("PS_") or obj.Label.startswith("PS_"):
            continue                    # previews and grids of the panels
        if any(p.isDerivedFrom("Part::Feature") and not p.isDerivedFrom("App::Part") for p in obj.InList):
            continue                    # the base of a Cut, the profile of an extrusion
        try:
            if obj.Shape.isNull() or not obj.Shape.Solids:
                continue
        except Exception:
            continue
        out.append(obj)
    return out


def _view_colour(obj):
    view = getattr(obj, "ViewObject", None)
    try:
        return tuple(view.ShapeColor[:3]) if view is not None else None
    except Exception:
        return None


# ── the model ──────────────────────────────────────────────────────────────

@dataclass
class SimPart:
    label: str              # in the simulation document
    source: str             # Name of the object in the assembly ("" for built metal and the air box)
    source_label: str
    kind: str               # "part", "metal", "port" or "airbox"
    material: str | None = None     # OTTER material name, NOT_SIMULATED, or None (OTTER guesses)
    material_source: str = ""
    impedance: float = 50.0
    chip: str = ""          # metal: the chip it was built from
    level: str = ""         # metal: its stack level


@dataclass
class SimModel:
    doc: object                     # the FreeCAD simulation document
    source_doc: str                 # Name of the assembly document
    parts: list
    model: object = None            # otter_llm.model.Model
    design: object = None           # otter_llm.starter.Design
    regions: dict = field(default_factory=dict)     # chip name -> ChipRegion
    guessed: dict = field(default_factory=dict)     # group name -> material OTTER guessed
    skipped: list = field(default_factory=list)     # (label, why)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    air_margin_mm: float = 0.0

    def part(self, label):
        return next((p for p in self.parts if p.label == label), None)

    @property
    def ports(self):
        return [p for p in self.parts if p.kind == "port"]


def air_margin_for(fmax_ghz, minimum_mm=0.5):
    """A quarter wavelength in air at *fmax_ghz*, mm, 2 % to spare, rounded
    up to 0.1 mm. The spare: OTTER's add-on checks with c = 3e8 m/s, so a
    box of exactly λ/4 with the true c0 would always be reported 2 µm short."""
    quarter = QUARTER_WAVE * C0 / (float(fmax_ghz) * 1e9) * 1000.0
    return max(math.ceil(quarter * 1.02 * 10 - 1e-9) / 10, minimum_mm)


def _bounds(shapes):
    box = None
    for shape in shapes:
        bb = shape.BoundBox
        box = bb if box is None else box.united(bb)
    return box


class _Builder:
    def __init__(self, source, doc, say):
        self.source, self.doc, self.say = source, doc, say
        self.parts, self.shapes, self.skipped, self.notes = [], [], [], []
        self.taken = {AIRBOX_LABEL}
        self.lib = mats.available(source)

    def add(self, label, shape, kind, material, origin, source=None, colour=None, **extra):
        label = _unique(plain_label(label), self.taken)
        copy = self.doc.addObject("Part::Feature", label)
        copy.Label = label
        copy.Shape = shape
        if colour and getattr(copy, "ViewObject", None) is not None:
            copy.ViewObject.ShapeColor = colour
        self.shapes.append(shape)
        part = SimPart(label, source.Name if source is not None else "", source.Label if source is not None else "",
                       kind, material, origin, **extra)
        self.parts.append(part)
        return part

    def material_of(self, obj):
        """(OTTER material or None, where it came from) for an assembly object."""
        name = mats.material_of(obj)
        if not name:
            return None, "no material set: OTTER guesses from name and colour"
        if name.lower() == em_materials.NOT_SIMULATED_NAME:
            return NOT_SIMULATED, "left out (Material: not simulated)"
        if name not in em_materials.MATERIALS:
            return None, f"'{name}' has no electrical values: OTTER guesses"
        return name, f"Material: {name}"

    def objects(self):
        for obj in physical_objects(self.source):
            try:
                shape = global_shape(obj)
            except Exception as exc:
                self.skipped.append((obj.Label, f"shape could not be read: {exc}"))
                continue
            material, origin = self.material_of(obj)
            solids = shape.Solids
            if len(solids) > 1 and material not in (None, NOT_SIMULATED) and em_materials.is_conductor(material):
                # One part per solid: OTTER meshes a conductor from its bounding box, and a STEP compound
                # of 300 via barrels is one box the size of the board — no mesh line meets a single barrel,
                # openEMS fills none of them ("Unused primitive"), and the top ground floats.
                for i, solid in enumerate(solids, start=1):
                    self.add(f"{obj.Label}_{i}", solid, "part", material, origin, source=obj, colour=_view_colour(obj))
                continue
            self.add(obj.Label, shape, "part", material, origin, source=obj, colour=_view_colour(obj))

    def chip_metal(self, chip, region):
        T, L = model.chip_technology(chip), model.chip_layout(chip)
        g = model.global_placement(chip)
        levels = {lv["name"]: lv for lv in T.levels}
        layer_objs = {(o.GdsLayer, o.GdsDatatype): o for o in chip.Group if model.role(o) == model.LAYER}
        by_level = _level_layers(T, region.levels)
        dropped = sum(len(L.polygons.get(k, ())) for k, sp in T.layers.items()
                      if sp.level in region.levels and sp.name.lower().endswith(".filler"))
        if dropped:
            self.notes.append(f"{chip.Label}: {dropped:,} fill polygons left out")
        for level in sorted(by_level, key=lambda n: levels[n]["z0"]):
            lv = levels[level]
            polys = []
            for key in by_level[level]:
                polys += palace.clip(L.polygons.get(key, []), region.rect)
            if not polys:
                continue
            self.say(f"EM model: {chip.Label} {level}, {len(polys):,} polygons")
            key = by_level[level][0]
            layer = layer_objs.get(key)
            name = mats.material_of(layer) if layer is not None else ""
            from ..core import materials as cmat
            name = name or cmat.layer_material(T, T.layers[key]) or ""
            if name not in em_materials.MATERIALS:
                material, origin = None, f"'{name}' has no electrical values: OTTER guesses"
            else:
                material, origin = name, f"PDK stackup via Material: {name}"
            colour = tuple(T.layers[key].color)
            if lv["kind"] == "via" and material is not None:
                merged = palace.merge_vias(polys)
                bins = {}
                for m, fill in merged:
                    bins.setdefault(max(round(fill * 20) / 20, 0.05), []).append(m)
                for fill, shapes in sorted(bins.items()):
                    shape = solids.layer_solid(shapes, lv["z0"], lv["z1"])
                    if shape is None:
                        continue
                    shape.transformShape(g.toMatrix())
                    scaled = em_materials.register_scaled(material, fill)
                    self.add(f"{chip.Label}_{level}_fill{int(round(fill * 100))}", shape, "metal", scaled,
                             f"{origin}, σ × {fill:.2f} (via array)", colour=colour, chip=chip.Name, level=level)
                self.notes.append(f"{chip.Label} {level}: {len(polys):,} vias merged into {len(merged)} arrays")
                continue
            shape = solids.layer_solid(polys, lv["z0"], lv["z1"])
            if shape is None:
                continue
            shape.transformShape(g.toMatrix())
            self.add(f"{chip.Label}_{level}", shape, "metal", material, origin, colour=colour,
                     chip=chip.Name, level=level)

    def port_faces(self):
        for port in pt.ports(self.source):
            try:
                shape = global_shape(port)
            except Exception as exc:
                self.skipped.append((port.Label, f"port face could not be read: {exc}"))
                continue
            label = _unique(f"Port_{port.PortIndex}", self.taken)
            copy = self.doc.addObject("Part::Feature", label)
            copy.Label = label
            copy.Shape = shape
            if getattr(copy, "ViewObject", None) is not None:
                copy.ViewObject.ShapeColor = (1.0, 0.0, 0.0)
            self.shapes.append(shape)
            self.parts.append(SimPart(label, port.Name, port.Label, "port",
                                      impedance=float(port.Impedance or 50.0)))


#: closer than this, a port face and a body face are the same plane, mm
SNAP_MM = 1e-9


def _snap_ports(doc, parts, tol=SNAP_MM):
    """
    Put every port exactly onto the body faces it was drawn on.

    A port drawn on a lead's edge sits at the lead's top as computed through
    the pad's placement — one rounding step (1e-16 mm) away from the top as
    OTTER reads it from the lead. OTTER keeps a port's planes as mesh lines
    exactly, so that put two lines 1e-13 um apart and the FDTD timestep,
    which follows the smallest cell, to nothing.

    Even with its corners exactly on the lead, a port standing on an edge
    is turned by 90°, and its extent read through that placement comes out
    a rounding step low (cos 90° is 6e-17, not 0). So every port is rebuilt
    from its corners with no placement at all, each coordinate within *tol*
    of a body's face taken from that face. Translating the shape instead
    only adds a location, and the extent read through it is off again.
    """
    coords = [set(), set(), set()]
    for p in parts:
        if p.kind in ("part", "metal"):
            bb = doc.getObject(p.label).Shape.BoundBox
            for k, (lo, hi) in enumerate(((bb.XMin, bb.XMax), (bb.YMin, bb.YMax), (bb.ZMin, bb.ZMax))):
                coords[k].update((lo, hi))
    coords = [sorted(c) for c in coords]

    def snap(value, k):
        best = min(coords[k], key=lambda c: abs(c - value), default=None)
        return best if best is not None and abs(best - value) <= tol else value

    moved = 0
    for p in parts:
        if p.kind != "port":
            continue
        obj = doc.getObject(p.label)
        faces = obj.Shape.Faces
        if len(faces) != 1 or len(faces[0].Wires) != 1:
            continue
        pts = [v.Point for v in faces[0].OuterWire.OrderedVertexes]
        snapped = [V(snap(q.x, 0), snap(q.y, 1), snap(q.z, 2)) for q in pts]
        try:
            obj.Shape = Part.Face(Part.makePolygon(snapped + [snapped[0]]))
            obj.Placement = FreeCAD.Placement()
            moved += 1
        except Part.OCCError:
            pass                    # a port that is not planar after snapping keeps its shape
    return moved


def regions_for(doc, regions=None):
    """{chip name: ChipRegion}: those given, the default for every other chip."""
    out = dict(regions or {})
    for chip in model.by_role(doc, model.CHIP):
        if chip.Name not in out:
            out[chip.Name] = default_region(chip)
    return out


def build(source_doc, fmax_ghz=30.0, fmin_ghz=1.0, air_margin_mm=None, regions=None, doc_name=None,
          progress=None, max_polygons=MAX_POLYGONS):
    """
    The simulation document for *source_doc* and its OTTER model.

    regions: {chip name: ChipRegion}, default round each chip's wired pads.
    air_margin_mm: None for a quarter wavelength at fmax, 0 for no air box.
    Raises ValueError, naming what to change, when there is nothing to
    simulate or a region holds more than *max_polygons* polygons.
    """
    if source_doc is None:
        raise ValueError("No document to simulate.")
    say = progress or (lambda _t: None)
    regions = regions_for(source_doc, regions)
    for name, region in regions.items():
        chip = source_doc.getObject(name)
        n = region_polygons(chip, region)
        if n > max_polygons:
            raise ValueError(f"{chip.Label}: the EM region holds {n:,} polygons (at most {max_polygons:,}). "
                             "Make the region smaller or take fewer metal levels in the EM panel.")
    if not physical_objects(source_doc) and not regions:
        raise ValueError("The document has nothing to simulate: no chip, housing or board.")
    problems = em_materials.register(source_doc)

    previous = FreeCAD.ActiveDocument
    doc = FreeCAD.newDocument(doc_name or f"{source_doc.Name}_EM")
    try:
        doc.Label = f"{source_doc.Label} (EM model)"
    except Exception:
        pass
    b = _Builder(source_doc, doc, say)
    b.objects()
    for name, region in regions.items():
        b.chip_metal(source_doc.getObject(name), region)
    b.port_faces()
    _snap_ports(doc, b.parts)
    margin = air_margin_for(fmax_ghz) if air_margin_mm is None else float(air_margin_mm)
    if margin > 0 and b.shapes:
        _make_airbox(doc, _bounds(b.shapes), margin)
        b.parts.append(SimPart(AIRBOX_LABEL, "", "", "airbox", NOT_SIMULATED, "air round the structure"))
    doc.recompute()
    sim = SimModel(doc=doc, source_doc=source_doc.Name, parts=b.parts, regions=regions, skipped=b.skipped,
                   notes=b.notes + [f"material '{k}': {v}" for k, v in problems.items()], air_margin_mm=margin)
    refresh_model(sim, fmin_ghz, fmax_ghz)
    _activate(previous or source_doc)
    return sim


def _activate(doc):
    try:
        FreeCAD.setActiveDocument(doc.Name)
        if FreeCAD.GuiUp:
            import FreeCADGui
            FreeCADGui.setActiveDocument(doc.Name)
    except Exception:
        pass


def _make_airbox(doc, bounds, margin):
    box = Part.makeBox(bounds.XLength + 2 * margin, bounds.YLength + 2 * margin, bounds.ZLength + 2 * margin,
                       V(bounds.XMin - margin, bounds.YMin - margin, bounds.ZMin - margin))
    air = doc.getObject(AIRBOX_LABEL) or doc.addObject("Part::Feature", AIRBOX_LABEL)
    air.Label = AIRBOX_LABEL
    air.Shape = box
    view = getattr(air, "ViewObject", None)
    if view is not None:
        view.ShapeColor = (0.8, 0.9, 1.0)
        view.Transparency = 90
        view.DisplayMode = "Wireframe"
    return air


# ── OTTER's view of it ─────────────────────────────────────────────────────

def describe_body(obj, container=None):
    """The facts otter_llm.model.build_model needs about one body — the same
    fields and rules as OTTER_Local's headless/read_model.py. *container*
    stands in for the App::Part OTTER uses to keep bodies of one height in
    separate groups: here it is the material, so a group never mixes two."""
    shape = obj.Shape
    bb = shape.BoundBox
    size = (bb.XLength, bb.YLength, bb.ZLength)
    tol = 1e-6 * max(max(size), 1e-9)
    kind = "solid" if shape.Solids else ("sheet" if shape.Faces else "wire")
    flat_axis = None
    if kind == "sheet":
        flat = [axis for axis, length in zip("xyz", size) if length <= tol]
        flat_axis = flat[0] if len(flat) == 1 else None
    box_volume = size[0] * size[1] * size[2]
    is_box = (kind == "solid" and len(shape.Faces) == 6 and box_volume > 0
              and abs(shape.Volume - box_volume) <= 1e-6 * box_volume)
    colour = _view_colour(obj)
    view = getattr(obj, "ViewObject", None)
    return {
        "name": obj.Name, "label": obj.Label, "type": obj.TypeId, "kind": kind,
        "bbox": [bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax],
        "volume": shape.Volume if kind == "solid" else 0.0, "area": shape.Area, "faces": len(shape.Faces),
        "is_box": is_box, "flat_axis": flat_axis, "used_by": [],
        "visible": bool(getattr(view, "Visibility", True)) if view is not None else True,
        "color": [round(c, 4) for c in colour] if colour else None,
        "transparency": getattr(view, "Transparency", 0) if view is not None else 0,
        "container": container,
    }


_GUESS = "guess: "


def _split_by_name(body):
    """A part with no material on record, kept apart from parts whose names
    say another material. OTTER puts the bodies of one height range into one
    group with one material; with nothing on record that merged a PCB's vias
    with the laminate they pass through, and 289 copper barrels became
    RO4350B. So such a part gets OTTER's guess from its name first."""
    one = SimpleNamespace(bodies={body["label"]: body})
    named = guess_from_names(one, SimpleNamespace(bodies=[body["label"]]))
    if named is not None:
        body = dict(body, container=_GUESS + named)
    return body


def refresh_model(sim, fmin_ghz=None, fmax_ghz=None):
    """(Re)build the OTTER model and the starting design from the simulation
    document: the layer stack, a material per group, the frequency range."""
    containers = {p.label: p.material for p in sim.parts if p.kind in ("part", "metal")}
    described = [describe_body(obj, containers.get(obj.Label)) for obj in sim.doc.Objects
                 if hasattr(obj, "Shape") and not obj.Shape.isNull()]
    bodies = [_split_by_name(body) if body["container"] is None else body for body in described]
    otter_model = build_model(sim.doc.Label, "mm", bodies, sim.warnings)
    design = default_design(otter_model)
    guessed = {}
    for group in groups(otter_model):
        recorded = {containers.get(label) for label in group.bodies}
        if len(recorded) == 1 and None not in recorded:
            design.materials[group.name] = next(iter(recorded))
        else:
            guessed[group.name] = design.materials.get(group.name, NOT_SIMULATED)
    if fmin_ghz is not None:
        design.fmin_ghz = float(fmin_ghz)
    if fmax_ghz is not None:
        design.fmax_ghz = float(fmax_ghz)
    sim.model, sim.design, sim.guessed = otter_model, design, guessed
    return sim


def fit_airbox(sim, fmax_ghz):
    """Grow the air box to λ/4 at *fmax_ghz* when it is closer; True when it
    changed. A box already large enough stays: shrinking would move a
    boundary a person may have checked."""
    air = sim.doc.getObject(AIRBOX_LABEL)
    if air is None:
        return False
    wanted = air_margin_for(fmax_ghz)
    if wanted <= sim.air_margin_mm * (1 + 1e-9):
        return False
    inner = [o.Shape for o in sim.doc.Objects if o.Name != AIRBOX_LABEL and hasattr(o, "Shape")]
    _make_airbox(sim.doc, _bounds(inner), wanted)
    sim.doc.recompute()
    sim.air_margin_mm = wanted
    return True


def port_contacts(sim, tol=1e-6):
    """{port label: [labels of the conductors it touches]} — a lumped port
    that touches no conductor feeds nothing."""
    conductors = [(p.label, sim.doc.getObject(p.label).Shape) for p in sim.parts
                  if p.kind in ("part", "metal") and p.material and em_materials.is_conductor(p.material)]
    out = {}
    for port in sim.ports:
        face = sim.doc.getObject(port.label).Shape
        out[port.label] = [label for label, shape in conductors
                           if face.BoundBox.intersect(shape.BoundBox) and face.distToShape(shape)[0] <= tol]
    return out


def summary(sim):
    """A short text for the panel and the report: counts and what was left out."""
    parts = [p for p in sim.parts if p.kind in ("part", "metal")]
    metal = [p for p in parts if p.kind == "metal"]
    recorded = [p for p in parts if p.material]
    lines = [f"{len(parts)} parts ({len(metal)} chip metal), {len(sim.ports)} ports, "
             f"{len(recorded)} with a material, {len(sim.guessed)} group(s) guessed by OTTER"]
    if sim.air_margin_mm > 0:
        lines.append(f"air box {sim.air_margin_mm:.3g} mm round the structure")
    lines += sim.notes[:6]
    for label, why in sim.skipped[:10]:
        lines.append(f"left out: {label}: {why}")
    return "\n".join(lines)
