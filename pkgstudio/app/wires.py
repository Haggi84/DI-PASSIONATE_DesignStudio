# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Bond wires as document objects: linked to their two pads, rebuilt from them,
checked against MIL-STD-883.

A wire stores which chip pad and which lead it joins and how it is bonded —
material, bond type, every bond dimension — and nothing about where it is.
Its shape is rebuilt on every recompute from where the pads are now, in the
Package's frame, so it follows the chip and the package wherever they move.
check() runs the MIL-STD-883 TM 2010 rules and the assembly guidelines
(core.wirerules) on the wires as they stand.
"""

import FreeCAD
import Part

from ..core import bondwire as bw, materials as mat, symmetry as sym, wirerules
from . import model

GROUP = "Bond wire"
LENGTHS = [  # (property, WireParams field, tooltip)
    ("WireDiameter", "wire_d", "Wire diameter"),
    ("LoopHeight", "loop_h", "Loop top above the higher wire exit"),
    ("BallDiameter", "ball_d", "Bonded ball diameter (ball-stitch); MIL-STD-883: 2.0 - 5.0 d"),
    ("BallHeight", "ball_h", "Bonded ball height (ball-stitch)"),
    ("WedgeWidth", "wedge_w", "First wedge bond width (wedge-wedge); MIL: 1.2 - 3.0 d"),
    ("WedgeLength", "wedge_l", "First wedge bond length; MIL: 1.5 - 6.0 d"),
    ("StitchWidth", "stitch_w", "Second bond width (wedge or crescent stitch)"),
    ("StitchLength", "stitch_l", "Second bond length; MIL: 1.5 - 6.0 d"),
    ("Tail", "tail", "Tail beyond the last bond; MIL: <= 2 d"),
]
ANGLES = [("ExitAngle", "exit_deg", "Wire leaves the first bond at this angle (wedge: the tool's feed angle)"),
          ("LandingAngle", "land_deg", "Approach into the second bond, from horizontal")]


class BondWire:
    """FeaturePython proxy. Keep this import path: saved documents refer to it."""

    def __init__(self, obj):
        obj.Proxy = self
        self.init_properties(obj)

    @staticmethod
    def init_properties(obj):
        """Add whatever properties *obj* lacks — also for wires saved before they existed."""
        d = bw.preset("Al", 0.025)
        if not hasattr(obj, "ChipPad"):
            # LinkGlobal, not Link: a plain link from an object in the Package
            # makes the Package claim the pad, pulling it out of its chip.
            obj.addProperty("App::PropertyLinkGlobal", "ChipPad", GROUP, "Pad on the chip (first bond)")
            obj.addProperty("App::PropertyLinkGlobal", "PackagePad", GROUP, "Lead (second bond)")
        if not hasattr(obj, "WireMaterial"):
            obj.addProperty("App::PropertyEnumeration", "WireMaterial", GROUP, "Wire metal")
            obj.WireMaterial = list(mat.WIRE_MATERIALS)
            obj.WireMaterial = d.material
        if not hasattr(obj, "BondType"):
            obj.addProperty("App::PropertyEnumeration", "BondType", GROUP, "How it is bonded")
            obj.BondType = [bw.WEDGE, bw.BALL]
            obj.BondType = d.bond
        for prop, field, tip in LENGTHS:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyLength", prop, GROUP, tip)
                setattr(obj, prop, getattr(d, field))
        for prop, field, tip in ANGLES:
            if not hasattr(obj, prop):
                obj.addProperty("App::PropertyAngle", prop, GROUP, tip)
                setattr(obj, prop, getattr(d, field))
        if not hasattr(obj, "Reach"):
            obj.addProperty("App::PropertyFloat", "Reach", GROUP, "Landing approach, fraction of the span")
            obj.Reach = d.reach
        if not hasattr(obj, "WireLength"):
            obj.addProperty("App::PropertyLength", "WireLength", GROUP, "Length of the wire")
            obj.setEditorMode("WireLength", 1)

    def onDocumentRestored(self, obj):
        self.init_properties(obj)

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def execute(self, obj):
        if obj.ChipPad is None or obj.PackagePad is None:
            obj.Shape = Part.Shape()
            return
        p = params_of(obj)
        # Built directly in the Package's frame from the pads' positions in
        # it: a rigid move of a finished shape is stored as its location,
        # which a recompute drops.
        frame = _frame(obj).inverse()
        start = frame.multVec(model.pad_centre(obj.ChipPad))
        end = frame.multVec(model.pad_centre(obj.PackagePad))
        # Each bond lies on its own pad's surface, level or not.
        n0 = frame.Rotation.multVec(model.pad_normal(obj.ChipPad))
        n1 = frame.Rotation.multVec(model.pad_normal(obj.PackagePad))
        obj.Shape = bw.solid(tuple(start), tuple(end), p, n0, n1)
        obj.WireLength = bw.length(tuple(start), tuple(end), p, n0, n1)


def _frame(obj):
    container = obj.getParentGeoFeatureGroup()
    return (model.global_placement(container) if container is not None
            else FreeCAD.Placement()).multiply(obj.Placement)


def params_of(obj):
    p = bw.WireParams(material=obj.WireMaterial, bond=obj.BondType, reach=obj.Reach)
    for prop, field, _tip in LENGTHS + ANGLES:
        setattr(p, field, getattr(obj, prop).Value)
    return p


def set_params(obj, p):
    obj.WireMaterial = p.material
    obj.BondType = p.bond
    obj.Reach = p.reach
    for prop, field, _tip in LENGTHS + ANGLES:
        setattr(obj, prop, getattr(p, field))
    from . import materials
    materials.set_material(obj, mat.WIRE_MATERIALS.get(p.material, ""))


GROUP_ROLE = "bond-wires"


def _group(doc, owner=None):
    """The Bond wires group of *owner* (a Package or Board), or of the
    document's top level for wires between two of them."""
    if owner is not None:
        for o in owner.Group:
            if model.role(o) == GROUP_ROLE:
                return o
    else:
        for o in model.by_role(doc, GROUP_ROLE):
            if o.getParentGeoFeatureGroup() is None:
                return o
    grp = doc.addObject("App::DocumentObjectGroup", "BondWires")
    grp.Label = "Bond wires"
    model.set_role(grp, GROUP_ROLE)
    if owner is not None:
        owner.addObject(grp)
    return grp


def owner_of(chip_pad, package_pad):
    """Where a wire between the two pads belongs: the Package or Board both
    are in, so it moves with them; None when they are in different ones."""
    return model.common_owner(chip_pad, package_pad)


def regroup(doc):
    """Every wire into the group of the Package or Board both its pads are
    in now — after a chip was placed into a housing, say. Its shape is
    rebuilt from the pads, so only the group changes. Returns how many moved."""
    moved = 0
    for w in wires(doc):
        if w.ChipPad is None or w.PackagePad is None:
            continue
        owner = owner_of(w.ChipPad, w.PackagePad)
        here = w.getParentGeoFeatureGroup()
        if (here is None and owner is None) or (here is not None and owner is not None
                                                and here.Name == owner.Name):
            continue
        for g in w.InList:
            if model.role(g) == GROUP_ROLE:
                g.removeObject(w)
        _group(doc, owner).addObject(w)
        w.Placement = FreeCAD.Placement()
        moved += 1
    return moved


def wires(doc):
    return model.by_role(doc, model.WIRE)


def wires_of(objs):
    """The wires with a pad inside any of *objs* (chips, packages, boards)."""
    names = set()
    for o in objs:
        names.add(o.Name)
    out = []
    for w in wires(objs[0].Document) if objs else []:
        for pad in (w.ChipPad, w.PackagePad):
            if pad is not None and (pad.Name in names or any(a.Name in names for a in model.ancestors(pad))):
                out.append(w)
                break
    return out


def create(doc, chip_pad, package_pad, p=None):
    obj = doc.addObject("Part::FeaturePython", "BondWire")
    BondWire(obj)
    model.set_role(obj, model.WIRE)
    obj.ChipPad, obj.PackagePad = chip_pad, package_pad
    obj.Label = f"Wire {chip_pad.PadName} - {package_pad.PadName}"
    _group(doc, owner_of(chip_pad, package_pad)).addObject(obj)
    set_params(obj, p or bw.preset("Al", 0.025))
    if getattr(obj, "ViewObject", None) is not None:
        try:
            obj.ViewObject.Proxy = 0
        except Exception:
            pass
    return obj


def die_to_die(doc):
    """The wires from one die to another (a multi-chip module's inner connections)."""
    return [w for w in wires(doc) if w.ChipPad is not None and w.PackagePad is not None
            and getattr(w.PackagePad, "PadSide", "") == "chip"]


def update_all(doc, ws=None):
    """Rebuild every wire (or those in *ws*) from where its pads are now."""
    for w in wires(doc) if ws is None else ws:
        w.touch()
    doc.recompute()


def chip_of(pad):
    p = model.parent_of(pad)
    return p if p is not None and model.role(p) == model.CHIP else None


def centre_xy(doc, chip=None):
    """The centre wires are paired round: *chip*'s, else the first chip's."""
    if chip is None:
        chips = model.by_role(doc, model.CHIP)
        chip = chips[0] if chips else None
    c = model.global_placement(chip).Base if chip is not None else FreeCAD.Vector()
    return (c.x, c.y)


def _pair_round(chip_pads, package_pads, centre):
    a = [tuple(model.pad_centre(p))[:2] for p in chip_pads]
    b = [tuple(model.pad_centre(p))[:2] for p in package_pads]
    return [(chip_pads[i], package_pads[j]) for i, j in sym.pair(a, b, centre)]


def auto_pair(doc, chip_pads=None, package_pads=None):
    """
    [(chip pad, lead)] — crossing-free and symmetric, by angle round the chip.

    With several chips, each is paired on its own, round its own centre,
    with the leads of the package or board it is in (all leads when it is
    loose); a lead one chip took is not offered to the next.
    """
    if chip_pads is not None or package_pads is not None:
        chip_pads = chip_pads if chip_pads is not None else model.all_pads(doc, "chip")
        package_pads = package_pads if package_pads is not None else model.all_pads(doc, "package")
        chips = {c.Name: c for c in (chip_of(p) for p in chip_pads) if c is not None}
        chip = next(iter(chips.values())) if len(chips) == 1 else None
        return _pair_round(chip_pads, package_pads, centre_xy(doc, chip))
    pairs, taken = [], set()
    leads = model.all_pads(doc, "package")
    for chip in model.by_role(doc, model.CHIP):
        own = model.pads_of(chip)
        if not own:
            continue
        owner = model.assembly_owner(chip)
        mine = [q for q in leads if q.Name not in taken
                and (owner is None or model.assembly_owner(q) is not None
                     and model.assembly_owner(q).Name == owner.Name)]
        found = _pair_round(own, mine, centre_xy(doc, chip))
        taken.update(q.Name for _c, q in found)
        pairs += found
    return pairs


def auto_pair_mcm(housing, chip_pads, leads=None, fixed=()):
    """
    [(chip pad, lead)] for the dies of a multi-chip module.

    Paired round one centre, the pads of a die that face the other die (or
    the paddle centre) come out in the wrong order and their wires cross; paired
    die by die over all leads, two dies fan their wires across each other.
    So the leads are shared out first — each lead to the die it is nearest,
    with each die's reach grown until it has a lead for every pad it bonds
    (a weighted Voronoi split of the lead ring: two contiguous arcs for two
    dies) — and then each die is paired round its own centre, crossing-free,
    with its own share. *chip_pads*: the pads to bond to leads (die-to-die
    pads left out); *leads*: default all of *housing*'s. *fixed*: [(pad,
    lead)] that must be (a pin list's fixed leads): they are kept, and the
    other pads are fitted round them without crossing (mcm.pair_anchored).
    """
    from ..core import mcm
    leads = list(leads if leads is not None else model.pads_of(housing))
    fixed = list(fixed)
    for p, l in fixed:
        if p not in chip_pads:
            chip_pads = list(chip_pads) + [p]
        if l not in leads:
            leads.append(l)
    by_chip = {}
    for p in chip_pads:
        c = chip_of(p)
        by_chip.setdefault(c.Name if c is not None else "", (c, []))[1].append(p)
    chips = list(by_chip.values())
    centres = [centre_xy(housing.Document, c) for c, _p in chips]
    lead_index = {q.Name: j for j, q in enumerate(leads)}
    fixed_of = {p.Name: l for p, l in fixed}
    pts = [tuple(model.pad_centre(q))[:2] for q in leads]
    if len(chips) <= 3:
        # exact: one contiguous arc of the lead ring per die, the best cut that works
        ac = model.global_placement(housing).multVec(getattr(housing, "AttachCentre", FreeCAD.Vector()))
        spec = [{"centre": centres[k], "pads": [tuple(model.pad_centre(p))[:2] for p in pads],
                 "anchors": {i: lead_index[fixed_of[p.Name].Name] for i, p in enumerate(pads) if p.Name in fixed_of}}
                for k, (_c, pads) in enumerate(chips)]
        result = mcm.assign_arcs(spec, pts, (ac.x, ac.y))
        return [(chips[k][1][i], leads[j]) for k, pairs in enumerate(result) for i, j in pairs]
    preset = {lead_index[fixed_of[p.Name].Name]: k for k, (_c, pads) in enumerate(chips)
              for p in pads if p.Name in fixed_of}
    share = mcm.share_leads(centres, [len(p) for _c, p in chips], pts, preset=preset)
    out = []
    for k, (chip, pads) in enumerate(chips):
        mine = [leads[j] for j in share[k]]
        a = [tuple(model.pad_centre(p))[:2] for p in pads]
        b = [tuple(model.pad_centre(q))[:2] for q in mine]
        mine_index = {q.Name: j for j, q in enumerate(mine)}
        anchors = [(i, mine_index[fixed_of[p.Name].Name]) for i, p in enumerate(pads) if p.Name in fixed_of]
        out += [(pads[i], mine[j]) for i, j in mcm.pair_anchored(a, b, anchors, centres[k])]
    return out


def symmetry_of(doc, pairs, tol=1e-4):
    chip_pads = model.all_pads(doc, "chip")
    package_pads = model.all_pads(doc, "package")
    a = [tuple(model.pad_centre(p))[:2] for p in chip_pads]
    b = [tuple(model.pad_centre(p))[:2] for p in package_pads]
    idx = [(chip_pads.index(c), package_pads.index(q)) for c, q in pairs]
    return sym.symmetry_report(idx, a, b, centre_xy(doc), tol)


def create_all(doc, pairs, p=None, replace=True):
    if replace:
        for w in wires(doc):
            doc.removeObject(w.Name)
    made = [create(doc, c, q, p) for c, q in pairs]
    doc.recompute()
    return made


# ── rules ───────────────────────────────────────────────────────────────────

def _outline(pad):
    """A pad's outline in global x, y. Its Shape already carries the pad's own
    placement, so only the containers' frame is applied."""
    face = pad.Shape.Faces[0] if pad.Shape.Faces else None
    if face is None:
        return []
    frame = model.parent_frame(pad)
    return [(p.x, p.y) for p in (frame.multVec(v.Point) for v in face.OuterWire.OrderedVertexes)]


def environment(doc):
    lids = model.by_role(doc, model.LID)
    lid_z = None
    for lid in lids:
        s = model.container_shape(lid)
        if not s.isNull():
            lid_z = s.BoundBox.ZMin if lid_z is None else min(lid_z, s.BoundBox.ZMin)
    edges = []
    for chip in model.by_role(doc, model.CHIP):
        bb = model.chip_local_bbox(chip)
        g = model.global_placement(chip)
        c = [g.multVec(FreeCAD.Vector(x, y, bb.ZMax)) for x, y in
             ((bb.XMin, bb.YMin), (bb.XMax, bb.YMin), (bb.XMax, bb.YMax), (bb.XMin, bb.YMax))]
        edges += [((a.x, a.y, a.z), (b.x, b.y, b.z)) for a, b in zip(c, c[1:] + c[:1])]
    posts = [(p.Name, _outline(p), model.pad_centre(p).z) for p in model.all_pads(doc, "package")]
    return wirerules.Env(lid_z=lid_z, die_edges=edges, posts=posts)


def specs(doc, ws=None):
    out = []
    for w in ws if ws is not None else wires(doc):
        if w.ChipPad is None or w.PackagePad is None:
            continue
        out.append(wirerules.WireSpec(
            name=w.Label, start=tuple(model.pad_centre(w.ChipPad)), end=tuple(model.pad_centre(w.PackagePad)),
            params=params_of(w), chip_pad=_outline(w.ChipPad), lead_pad=_outline(w.PackagePad),
            chip_pad_id=w.ChipPad.Name, lead_pad_id=w.PackagePad.Name,
            die_to_die=getattr(w.PackagePad, "PadSide", "") == "chip"))
    return out


def check(doc, cls="B", ws=None):
    """{wire label: [Finding]} — MIL-STD-883 TM 2010 class *cls* ("B" or "S")
    and the assembly guidelines, for every wire as it stands."""
    return wirerules.check(specs(doc, ws), environment(doc), cls)
