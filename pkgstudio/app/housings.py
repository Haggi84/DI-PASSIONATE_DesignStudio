# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Housings in the document — from MirrorSemi, a JEDEC outline, or a STEP file
— with their pads defined and their materials assigned on arrival.

Whatever the source, a housing is analysed (core.housing) and rebuilt as:

    Housing (App::Part)
      Body          the moulded or ceramic body     HousingPart = body
      Leadframe     paddle, leads, tie bars          HousingPart = leadframe
      Pads 1..N     one per bond finger, JEDEC pin numbers

and remembers where its die paddle is open, so a chip can be put on it
without selecting anything. A die attach layer goes between the two: the
chip does not sit on bare copper, and in a thermal model the 25 um of epoxy
under it is one of the largest resistances on the way out.
"""

import os

import FreeCAD
import Part

import json

from ..core import elements as el, housing as hs, jedec, pads as pd
from . import materials, model

V = FreeCAD.Vector

LID_MATERIALS = {
    "Glass": "Borosilicate glass",
    "Ceramic": "Alumina 96 % (Al2O3)",
    "Plastic (LCP)": "Liquid crystal polymer (LCP)",
    "Kovar": "Kovar (FeNiCo)",
}


def _prop(obj, kind, name, value, tip=""):
    model._prop(obj, kind, name, value, tip)


def from_solids(doc, solids, name, source):
    """A Housing in the package from a list of solids."""
    a = hs.analyse(solids)
    if a.body is None:
        raise ValueError(f"{name}: no solids to make a housing of")
    container = doc.addObject("App::Part", "Housing")
    container.Label = f"Housing — {name}"
    model.set_role(container, model.HOUSING)
    _prop(container, "App::PropertyString", "HousingName", name, "Package")
    _prop(container, "App::PropertyString", "HousingSource", source, "Where the model came from")

    body = doc.addObject("Part::Feature", "Body")
    body.Label = f"{name} body"
    body.Shape = solids[a.body]
    _prop(body, "App::PropertyString", "HousingPart", "body", "Part of the housing")
    container.addObject(body)
    rest = [s for i, s in enumerate(solids) if i != a.body]
    if rest:
        lf = doc.addObject("Part::Feature", "Leadframe")
        lf.Label = f"{name} leadframe"
        lf.Shape = Part.makeCompound(rest) if len(rest) > 1 else rest[0]
        _prop(lf, "App::PropertyString", "HousingPart", "leadframe", "Part of the housing")
        container.addObject(lf)

    for finger in a.fingers:
        model._pad_object(doc, container, finger, "package")
    if a.attach is not None:
        _prop(container, "App::PropertyVector", "AttachCentre", V(a.attach.x, a.attach.y, a.attach.z),
              "Centre of the open die paddle (housing frame)")
        _prop(container, "App::PropertyLength", "AttachWidth", a.attach.w, "Open paddle, x")
        _prop(container, "App::PropertyLength", "AttachHeight", a.attach.h, "Open paddle, y")
    bb = solids[a.body].BoundBox
    _prop(container, "App::PropertyVector", "BodyCentreTop", V((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2,
                                                               bb.ZMax), "Body top centre (housing frame)")
    _prop(container, "App::PropertyLength", "BodyX", bb.XLength, "Body size x")
    _prop(container, "App::PropertyLength", "BodyY", bb.YLength, "Body size y")
    pkg = model.package_for_housing(doc)
    pkg.addObject(container)
    _beside_others(pkg)
    materials.auto_assign(doc)
    doc.recompute()
    return container


#: space between packages set out side by side, mm
PACKAGE_GAP = 2.0


def _beside_others(pkg):
    """A new package goes beside the ones already there, on the side away
    from the chips, so two housings are never one inside the other."""
    doc = pkg.Document
    others = [p for p in model.packages(doc) if p.Name != pkg.Name and p.getParentGeoFeatureGroup() is None]
    if not others or pkg.getParentGeoFeatureGroup() is not None:
        return
    bottom = None
    for p in others:
        bb = model.global_bbox(p)
        if bb.isValid():
            bottom = bb.YMin if bottom is None else min(bottom, bb.YMin)
    own = model.local_bbox(pkg)
    if bottom is None or not own.isValid():
        return
    pkg.Placement = FreeCAD.Placement(V(0, bottom - PACKAGE_GAP - own.YMax, 0), FreeCAD.Rotation())


def from_step(doc, path, name=None, source="STEP file"):
    shape = Part.read(path)
    solids = shape.Solids
    if not solids:
        raise ValueError(f"{os.path.basename(path)} holds no solids")
    return from_solids(doc, solids, name or os.path.splitext(os.path.basename(path))[0], source)


def from_jedec(doc, spec, assumed=(), origin=None):
    """A housing built from a JEDEC Spec. It keeps the spec, so it can be
    opened in the Element Forge and rebuilt in place."""
    parts = jedec.build(spec)
    h = from_solids(doc, [parts["body"]] + parts["leadframe"], spec.name, spec.standard)
    _keep_spec(h, el.from_spec(spec), assumed, origin)
    return h


def from_spec_dict(doc, d, assumed=(), origin=None):
    """A housing from an element spec dict (the Element Forge, the library)."""
    issues = el.validate(d)
    if el.errors(issues):
        raise ValueError("; ".join(i.message for i in el.errors(issues)))
    return from_jedec(doc, el.to_spec(d), assumed, origin)


SPEC_GROUP = "Element"


def _keep_spec(housing, d, assumed=(), origin=None):
    """The spec as JSON on the housing — what the Element Forge reads back."""
    if not hasattr(housing, "ElementSpec"):
        housing.addProperty("App::PropertyString", "ElementSpec", SPEC_GROUP,
                            "The numbers this housing was built from (JSON); edit them in the Element Forge")
        housing.setEditorMode("ElementSpec", 1)
    housing.ElementSpec = json.dumps({"spec": el.normalise(d), "assumed": list(assumed),
                                      "origin": dict(origin or {})})


def spec_of(housing):
    """(spec dict, assumed list, origin) of a housing built from a spec, or None."""
    raw = getattr(housing, "ElementSpec", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return el.normalise(data.get("spec", {})), list(data.get("assumed", [])), dict(data.get("origin", {}))


def rebuild(housing, d, assumed=(), origin=None):
    """
    Build *housing* again from spec *d*, in place: same object, same package,
    same placement. Body and leadframe get the new shapes; pads keep their
    objects where the pin number still exists — so a bond wire to pin 7
    stays on pin 7 and follows it — new pins get new pads, and pads (with
    their wires) of pins that no longer exist are removed.

    Every chip on the paddle is put back on it (the paddle may now be higher
    or lower) where it sat — the dies of a multi-chip module keep their
    arrangement.

    Returns (kept, added, removed) pad counts.
    """
    from . import moving, wires
    issues = el.validate(d)
    if el.errors(issues):
        raise ValueError("; ".join(i.message for i in el.errors(issues)))
    doc = housing.Document
    placed = {}
    for chip in chips_on(housing):          # where each die sits on the paddle now
        die = _paddle_frame_die(chip, housing)
        placed[chip.Name] = (die.x, die.y)
    spec = el.to_spec(d)
    parts = jedec.build(spec)
    solids = [parts["body"]] + parts["leadframe"]
    a = hs.analyse(solids)
    for obj in housing.Group:
        part = getattr(obj, "HousingPart", "")
        if part == "body":
            obj.Shape = solids[a.body]
            obj.Label = f"{spec.name} body"
        elif part == "leadframe":
            rest = [s for i, s in enumerate(solids) if i != a.body]
            obj.Shape = Part.makeCompound(rest) if len(rest) > 1 else rest[0]
            obj.Label = f"{spec.name} leadframe"

    old = {p.PadName: p for p in model.pads_of(housing)}
    kept = added = 0
    for finger in a.fingers:
        pad = old.pop(finger.name, None)
        if pad is None:
            model._pad_object(doc, housing, finger, "package")
            added += 1
            continue
        pts = [V(x - finger.x, y - finger.y, 0) for x, y in finger.outline]
        try:
            pad.Shape = Part.Face(Part.makePolygon(pts + [pts[0]]))
        except Part.OCCError:
            pad.Shape = Part.makePlane(finger.w, finger.h, V(-finger.w / 2, -finger.h / 2, 0))
        pad.Placement = FreeCAD.Placement(V(finger.x, finger.y, finger.z), FreeCAD.Rotation())
        pad.PadWidth, pad.PadHeight = finger.w, finger.h
        kept += 1
    removed = len(old)
    gone = {p.Name for p in old.values()}
    for w in wires.wires(doc):
        if getattr(w, "PackagePad", None) is not None and w.PackagePad.Name in gone:
            doc.removeObject(w.Name)
    for p in old.values():
        doc.removeObject(p.Name)

    housing.Label = f"Housing — {spec.name}"
    housing.HousingName = spec.name
    housing.HousingSource = spec.standard
    if a.attach is not None:
        _prop(housing, "App::PropertyVector", "AttachCentre", V(a.attach.x, a.attach.y, a.attach.z),
              "Centre of the open die paddle (housing frame)")
        _prop(housing, "App::PropertyLength", "AttachWidth", a.attach.w, "Open paddle, x")
        _prop(housing, "App::PropertyLength", "AttachHeight", a.attach.h, "Open paddle, y")
    bb = solids[a.body].BoundBox
    housing.BodyCentreTop = V((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2, bb.ZMax)
    housing.BodyX, housing.BodyY = bb.XLength, bb.YLength
    _keep_spec(housing, d, assumed, origin)
    materials.auto_assign(doc)
    doc.recompute()
    pkg = model.package_of(housing)
    for da in model.by_role(doc, "die-attach"):
        chip = doc.getObject(getattr(da, "ChipName", "") or "")
        if chip is None or pkg is None or model.package_of(chip) is not pkg or a.attach is None:
            continue
        # back on the (maybe higher or lower) paddle, where it sat on it — a
        # Move offset becomes part of where it sits
        _dx, _dy, angle = moving.offsets(chip)
        place_chip(chip, housing, attach_t=da.Shape.BoundBox.ZLength, offset=placed.get(chip.Name, (0.0, 0.0)))
        if angle:
            moving.set_offsets(chip, 0.0, 0.0, angle)
    wires.update_all(doc)
    return kept, added, removed


def attach_target(housing):
    """(global centre on the paddle surface, normal) — None without an open paddle."""
    if not hasattr(housing, "AttachCentre"):
        return None
    g = model.global_placement(housing)
    return g.multVec(housing.AttachCentre), g.Rotation.multVec(V(0, 0, 1))


def place_chip(chip, housing, attach_t=0.025, turn_deg=0.0, offset=(0.0, 0.0)):
    """Put *chip* on *housing*'s die paddle, on a die attach layer *attach_t*
    thick (0 for none): centred, or *offset* (dx, dy) mm from the paddle
    centre in the housing's frame — one of several dies of a multi-chip
    module (place_chips)."""
    from ..core import placement as pl
    from . import wires
    doc = chip.Document
    target = attach_target(housing)
    if target is None:
        raise ValueError(f"{housing.Label} has no open die paddle")
    centre, normal = target
    if offset[0] or offset[1]:
        centre = centre + model.global_placement(housing).Rotation.multVec(V(offset[0], offset[1], 0))
    bb = model.chip_local_bbox(chip)
    # the chip belongs to this housing's package from now on, and moves with it
    pkg = model.package_of(housing) or model.package(doc)
    model.move_into(chip, pkg)

    for old in model.by_role(doc, "die-attach"):
        if getattr(old, "ChipName", "") == chip.Name:
            doc.removeObject(old.Name)
    if attach_t > 0:
        da = doc.addObject("Part::Feature", "DieAttach")
        da.Label = f"Die attach — {chip.Label}"
        da.Shape = Part.makeBox(bb.XLength, bb.YLength, attach_t, V(-bb.XLength / 2, -bb.YLength / 2, 0))
        model.set_role(da, "die-attach")
        _prop(da, "App::PropertyString", "ChipName", chip.Name, "The chip it holds")
        rot = FreeCAD.Rotation(V(0, 0, 1), normal).multiply(FreeCAD.Rotation(V(0, 0, 1), turn_deg))
        pkg.addObject(da)
        da.Placement = model.to_local(da, FreeCAD.Placement(centre, rot))
        materials.set_material(da, materials.DIE_ATTACH)
        centre = centre + normal * attach_t
    plc = pl.placement_onto(bb, centre, normal, turn_deg)
    chip.Placement = model.to_local(chip, plc)
    from . import moving
    moving.set_base(chip)
    doc.recompute()
    wires.regroup(doc)
    wires.update_all(doc)
    return chip.Placement


def chips_on(housing):
    """The chips on *housing*'s paddle (those with a die attach in its package)."""
    doc = housing.Document
    pkg = model.package_of(housing)
    out = []
    for da in model.by_role(doc, "die-attach"):
        chip = doc.getObject(getattr(da, "ChipName", "") or "")
        if chip is not None and pkg is not None and model.package_of(chip) is pkg:
            out.append(chip)
    return out


def _paddle_frame_die(chip, housing):
    """mcm.Die of *chip* as it sits on *housing*'s paddle: its extent in the
    paddle frame, however it is turned."""
    from ..core import mcm
    bb = model.chip_local_bbox(chip)
    to_h = model.global_placement(housing).inverse().multiply(model.global_placement(chip))
    pts = [to_h.multVec(V(x, y, 0)) for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax)]
    c = getattr(housing, "AttachCentre", V())
    x0, x1 = min(p.x for p in pts) - c.x, max(p.x for p in pts) - c.x
    y0, y1 = min(p.y for p in pts) - c.y, max(p.y for p in pts) - c.y
    return mcm.Die(chip.Label, x1 - x0, y1 - y0, (x0 + x1) / 2, (y0 + y1) / 2)


def place_chips(chips, housing, attach_t=0.025, gap=0.3, direction="x", offsets=None, turns=None):
    """
    Several dies on one paddle — a multi-chip module. *offsets*: [(dx, dy)]
    from the paddle centre per chip; without, they are arranged in a row
    along *direction* ("x", "y" or "diagonal"), *gap* mm apart. *turns*:
    degrees per chip. Returns the mcm.check issues of the result.
    """
    from ..core import mcm
    turns = list(turns or [0.0] * len(chips))
    if offsets is None:
        sizes = []
        for chip, t in zip(chips, turns):
            bb = model.chip_local_bbox(chip)
            sizes.append((bb.YLength, bb.XLength) if round(t / 90.0) % 2 else (bb.XLength, bb.YLength))
        offsets = mcm.arrange(sizes, gap=gap, direction=direction)
    for chip, off, t in zip(chips, offsets, turns):
        place_chip(chip, housing, attach_t=attach_t, turn_deg=t, offset=off)
    return check_mcm(housing)


def check_mcm(housing, gap=None, margin=None):
    """mcm.check for the dies on *housing*'s paddle, the bond fingers as keep-outs."""
    from ..core import mcm
    dies = [_paddle_frame_die(c, housing) for c in chips_on(housing)]
    w = getattr(housing, "AttachWidth", None)
    h = getattr(housing, "AttachHeight", None)
    if w is None or h is None:
        return [mcm.Issue("error", "", f"{housing.Label} has no open die paddle")]
    centre = housing.AttachCentre
    keep = []
    for p in model.pads_of(housing):
        c = model.global_placement(housing).inverse().multVec(model.pad_centre(p))
        pw, ph = float(getattr(p.PadWidth, "Value", p.PadWidth)), float(getattr(p.PadHeight, "Value", p.PadHeight))
        keep.append((c.x - centre.x - pw / 2, c.y - centre.y - ph / 2, c.x - centre.x + pw / 2, c.y - centre.y + ph / 2))
    kw = {}
    if gap is not None:
        kw["gap"] = gap
    if margin is not None:
        kw["margin"] = margin
    return mcm.check(dies, float(getattr(w, "Value", w)), float(getattr(h, "Value", h)), keep, **kw)


def make_lid(doc, housing, kind="Glass", thickness=0.25):
    """A flat lid the size of *housing*'s body, laid on its top."""
    lid_mat = LID_MATERIALS.get(kind, kind)
    container = doc.addObject("App::Part", "Lid")
    container.Label = f"Lid — {kind} {thickness * 1000:.0f} um"
    model.set_role(container, model.LID)
    plate = doc.addObject("Part::Feature", "LidPlate")
    plate.Label = f"{kind} lid"
    plate.Shape = jedec.lid(housing.BodyX.Value, housing.BodyY.Value, thickness)
    _prop(plate, "App::PropertyString", "LidMaterial", lid_mat, "What the lid is made of")
    container.addObject(plate)
    (model.package_of(housing) or model.package(doc)).addObject(container)
    g = model.global_placement(housing)
    container.Placement = model.to_local(container, FreeCAD.Placement(g.multVec(housing.BodyCentreTop),
                                                                      g.Rotation))
    materials.set_material(plate, lid_mat)
    doc.recompute()
    return container
