# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The document: a package and what is in it.

    Package (App::Part)            moves as one — onto a board, say
      Chip (App::Part)             a GDS layout, centred on its own origin
        layer meshes               one Mesh::Feature per drawn layer
        die body                   epi and substrate, Part solids
        Chip pads                  markers on the top metal
      Housing / Lid (App::Part)    imported STEP
        Package pads               markers on the bond fingers
      Bond wires                   parametric, linked to their two pads
    Board (App::Part)              imported STEP, outside the package

Every object carries a PkgRole property saying what it is, and every tool
finds things by role, never by name or label, which the user may change.

A chip remembers its GDS file and technology, so its exact geometry can
always be rebuilt from the source — the view shows meshes, the exports
rebuild exact solids, and neither depends on the other.

Works without the GUI, so the whole flow can be tested headless.
"""

import os
import tempfile

import FreeCAD
import Part

from ..core import clusters, layout as lay, mesh as msh, pads as pd, solids, tech as tc

V = FreeCAD.Vector
GROUP = "PackageStudio"

ROLE = "PkgRole"
PACKAGE, CHIP, LAYER, DIE_BODY, PAD, HOUSING, LID, BOARD, WIRE = (
    "package", "chip", "layer", "die-body", "pad", "housing", "lid", "board", "bond-wire")
CONTAINER_ROLES = (HOUSING, LID, BOARD)
DIELECTRIC, UNUSED_LEVEL = "dielectric", "unused-level"

#: layers above this many polygons start as blocks in the view (vias always do
#: above BLOCK_VIAS_ABOVE); the exports always write every polygon.
BLOCK_ABOVE = 200_000
BLOCK_VIAS_ABOVE = 2_000

_layouts = {}       # (doc name, chip name) -> Layout — read once per session


# ── roles and lookup ────────────────────────────────────────────────────────

def _prop(obj, kind, name, value, doc=""):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, GROUP, doc)
    setattr(obj, name, value)


def set_role(obj, role):
    _prop(obj, "App::PropertyString", ROLE, role, "What this object is to the package workbench")
    obj.setEditorMode(ROLE, 1)


def role(obj):
    return getattr(obj, ROLE, "")


def by_role(doc, wanted):
    return [o for o in doc.Objects if role(o) == wanted]


def packages(doc):
    return by_role(doc, PACKAGE)


def new_package(doc):
    pkg = doc.addObject("App::Part", "Package")
    n = len(packages(doc))
    pkg.Label = "Package" if n == 0 else f"Package {n + 1}"
    set_role(pkg, PACKAGE)
    return pkg


def package(doc, create=True):
    """The document's first package (made if there is none and *create*)."""
    found = packages(doc)
    if found or not create:
        return found[0] if found else None
    return new_package(doc)


def package_for_housing(doc):
    """The package a new housing goes into: the first one that has no
    housing yet, else a new one. An assembly holds as many packages as it
    has housings."""
    for pkg in packages(doc):
        if not any(role(o) == HOUSING for o in pkg.Group):
            return pkg
    return new_package(doc)


def ancestors(obj):
    """The App::Parts *obj* is in, innermost first."""
    out, p = [], obj.getParentGeoFeatureGroup()
    while p is not None:
        out.append(p)
        p = p.getParentGeoFeatureGroup()
    return out


def package_of(obj):
    """The Package *obj* is in, or None."""
    return next((p for p in ancestors(obj) if role(p) == PACKAGE), None)


def assembly_owner(obj):
    """What a thing placed on *obj* belongs to, so it moves with it: the
    Package of a housing or lid, the Board itself, or None (the document)."""
    for p in [obj] + ancestors(obj):
        if role(p) == PACKAGE:
            return p
        if role(p) in (HOUSING, LID):
            return package_of(p) or p
        if role(p) == BOARD:
            return p
    return None


def common_owner(a, b):
    """The innermost Package or Board both *a* and *b* are in, or None."""
    theirs = set(o.Name for o in ancestors(b))
    for p in ancestors(a):
        if p.Name in theirs and role(p) in (PACKAGE, BOARD):
            return p
    return None


def move_into(obj, container):
    """Put *obj* into *container* (None: the document's top level), where
    it is now: its global placement is kept."""
    g = global_placement(obj)
    old = obj.getParentGeoFeatureGroup()
    if old is container or (old is not None and container is not None and old.Name == container.Name):
        return
    if old is not None:
        old.removeObject(obj)
    if container is not None:
        container.addObject(obj)
    obj.Placement = to_local(obj, g)


def parent_of(obj):
    for p in obj.InList:
        if p.isDerivedFrom("App::Part") or p.isDerivedFrom("App::DocumentObjectGroup"):
            if obj in getattr(p, "Group", []):
                return p
    return None


def global_placement(obj):
    """
    *obj*'s placement in the document, through every App::Part it is in.

    FreeCAD's own getGlobalPlacement() returned only the object's own
    placement when called from a bond wire's recompute — every wire started
    at its pad's position inside the chip, not on the chip, and came out
    5 mm long instead of 1. Walking the containers directly does not depend
    on when it is asked.
    """
    pl = obj.Placement if hasattr(obj, "Placement") else FreeCAD.Placement()
    parent = obj.getParentGeoFeatureGroup()
    while parent is not None:
        pl = parent.Placement.multiply(pl)
        parent = parent.getParentGeoFeatureGroup()
    return pl


def parent_frame(obj):
    """The global Placement of the frame *obj*'s own Placement is relative to."""
    parent = obj.getParentGeoFeatureGroup()
    return global_placement(parent) if parent is not None else FreeCAD.Placement()


def to_local(obj, placement):
    """The Placement *obj* needs for it to sit at the global *placement*,
    whatever containers it is in."""
    return parent_frame(obj).inverse().multiply(placement)


def _mesh_from(verts, tris):
    import Mesh
    fd, path = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        msh.write_binary_stl(path, verts, tris)
        return Mesh.Mesh(path)
    finally:
        os.remove(path)


def _style(obj, rgb, transparency=0):
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return
    for name, value in (("ShapeColor", tuple(rgb)), ("Transparency", transparency)):
        try:
            setattr(vo, name, value)
        except Exception:
            pass
    try:
        vo.DisplayMode = "Shaded"
    except Exception:
        pass


# ── chips ───────────────────────────────────────────────────────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def relocated(path, doc=None):
    """*path* as it is on this computer. A document made elsewhere (an
    example from the repository, a file from a colleague) names its chip's
    GDS and technology with that computer's paths: when such a path is not
    here, the same file is looked for in this Package Studio
    (technologies/<name>, examples/...), then beside the document."""
    if not path or os.path.exists(path):
        return path
    parts = path.replace("\\", "/").split("/")
    for anchor in ("technologies", "examples"):
        if anchor in parts:
            k = len(parts) - 1 - parts[::-1].index(anchor)
            here = os.path.join(_ROOT, *parts[k:])
            if os.path.exists(here):
                return here
    if doc is not None and getattr(doc, "FileName", ""):
        here = os.path.join(os.path.dirname(doc.FileName), parts[-1])
        if os.path.exists(here):
            return here
    return path


def chip_technology(chip):
    return tc.load(relocated(chip.TechFolder, chip.Document))


def chip_layout(chip):
    """The chip's Layout: from this session's cache, or read again from its
    GDS — after a document is reopened, say."""
    key = (chip.Document.Name, chip.Name)
    if key not in _layouts:
        _layouts[key] = lay.read(relocated(chip.GdsPath, chip.Document), chip_technology(chip), chip.Cell or None)
    return _layouts[key]


def chip_local_bbox(chip):
    """The chip's extent in its own frame: die outline, die body bottom to
    stack top. Exact, and no shape needed."""
    L, T = chip_layout(chip), chip_technology(chip)
    x0, y0, x1, y1 = L.outline
    top = max((T.layers[k].z1 for k in L.polygons if k in T.layers), default=0.0)
    return FreeCAD.BoundBox(x0, y0, -T.body_thickness, x1, y1, top)


def layer_mesh(chip, layer_obj, mode=None):
    """(vertices, triangles) of one layer, as *mode* ("detail" or "blocks")."""
    L, T = chip_layout(chip), chip_technology(chip)
    key = (layer_obj.GdsLayer, layer_obj.GdsDatatype)
    spec = T.layers[key]
    mode = mode or layer_obj.Display
    polys = L.polygons.get(key, [])
    if mode == "blocks":
        return msh.boxes(clusters.layer_blocks(polys, spec.z0, spec.z1))
    return msh.extrude(polys, spec.z0, spec.z1)


def set_layer_display(layer_obj, mode):
    """Show a layer in full detail or as cluster blocks. Only the view
    changes; the exports always write every polygon."""
    chip = parent_of(layer_obj)
    layer_obj.Display = mode
    layer_obj.Mesh = _mesh_from(*layer_mesh(chip, layer_obj, mode))


#: how see-through a dielectric or unused-level slab is drawn, percent
SLAB_TRANSPARENCY = 85


def add_slab(chip, name, label, slab_role, outline, z0, z1, rgb, visible=True, **props):
    """A die-sized box from z0 to z1 (mm) in *chip*: an unused stack level
    or a dielectric. A box, not the layout — so it costs nothing."""
    doc = chip.Document
    x0, y0, x1, y1 = outline
    obj = doc.addObject("Part::Feature", name)
    obj.Label = label
    obj.Shape = Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))
    set_role(obj, slab_role)
    for key, value in props.items():
        _prop(obj, "App::PropertyString", key, value, "From the PDK stackup")
        obj.setEditorMode(key, 1)
    _prop(obj, "App::PropertyLength", "Z0", z0, "Bottom")
    _prop(obj, "App::PropertyLength", "Z1", z1, "Top")
    for p in ("Z0", "Z1"):
        obj.setEditorMode(p, 1)
    chip.addObject(obj)
    _style(obj, rgb, SLAB_TRANSPARENCY)
    vo = getattr(obj, "ViewObject", None)
    if vo is not None:
        vo.Visibility = visible
        try:
            vo.Selectable = False        # never in the way of a pad being picked
        except Exception:
            pass
    return obj


#: space between chips imported side by side, mm
CHIP_GAP = 0.5


def loose_chips(doc):
    """Chips not placed into a package or onto a board yet."""
    return [c for c in by_role(doc, CHIP) if c.getParentGeoFeatureGroup() is None]


def global_bbox(obj):
    """*obj*'s extent in the document, from its local extent's corners."""
    bb = FreeCAD.BoundBox()
    local = local_bbox(obj)
    if local.isValid():
        _add_box(bb, local, global_placement(obj))
    return bb


def _free_x(doc, chip, outline):
    """Where the centre of a new chip with *outline* goes on x: CHIP_GAP to
    the right of every loose chip, or at the origin when it is the first."""
    right = None
    for other in loose_chips(doc):
        if other.Name == chip.Name:
            continue
        bb = global_bbox(other)
        if bb.isValid():
            right = bb.XMax if right is None else max(right, bb.XMax)
    if right is None:
        return 0.0
    return right + CHIP_GAP - outline[0]


def technology_suggestion(gds_path, tech_folder):
    """(name, folder, known, chosen_known, total) of a technology that fits
    *gds_path* clearly better than *tech_folder*, or None."""
    return tc.suggestion(tech_folder, lay.layers_in(gds_path))


def plain_gds(path):
    """*path*, or for a gzipped GDS (*.gds.gz) the unpacked file beside it,
    named after the archive (not after the name stored inside it: IHP's
    archives store their own file names, and the pin list finds a die by its
    file name). Unpacked again only when the archive is newer."""
    if not path.lower().endswith(".gz"):
        return path
    import gzip
    import shutil
    out = path[:-3]
    if not os.path.splitext(out)[1]:
        out += ".gds"
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(path):
        return out
    try:
        with gzip.open(path, "rb") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return out
    except OSError:
        # beside the archive is not writable (a folder under Program Files, or
        # Windows holds the file open): an unpacked file already there is the
        # same layout; otherwise unpack into the user's own folder
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        cache = os.path.join(FreeCAD.getUserAppDataDir(), "PackageStudio", "gds")
        os.makedirs(cache, exist_ok=True)
        alt = os.path.join(cache, os.path.basename(out))
        with gzip.open(path, "rb") as src, open(alt, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return alt


def import_chip(doc, gds_path, tech_folder, progress=None):
    """
    A Chip in the document's Package from *gds_path*, built with the
    technology in *tech_folder*. *progress(text)* is told what is happening.
    """
    gds_path = plain_gds(gds_path)
    T = tc.load(tech_folder)
    if progress:
        progress(f"Reading {os.path.basename(gds_path)}")
    L = lay.read(gds_path, T)

    chip = doc.addObject("App::Part", "Chip")
    chip.Label = os.path.splitext(os.path.basename(gds_path))[0]
    set_role(chip, CHIP)
    _prop(chip, "App::PropertyFile", "GdsPath", gds_path, "The GDS this chip was read from")
    _prop(chip, "App::PropertyPath", "TechFolder", tech_folder, "Its technology")
    _prop(chip, "App::PropertyString", "Technology", T.name, "Its technology's name")
    _prop(chip, "App::PropertyString", "Cell", L.cell, "The top cell")
    _prop(chip, "App::PropertyVector", "GdsCentre", V(*L.origin_gds_mm, 0),
          "Where the die centre (this chip's origin) is in the GDS, mm")
    _layouts[(doc.Name, chip.Name)] = L
    # A chip comes in loose, beside the chips already loose, so several
    # imported together never overlap; Place puts it into its housing or
    # onto its board, and it moves with that from then on.
    chip.Placement = FreeCAD.Placement(V(_free_x(doc, chip, L.outline), 0, 0), FreeCAD.Rotation())

    keys = sorted((k for k in L.polygons if k in T.layers), key=lambda k: (T.layers[k].z0, k))
    for n, key in enumerate(keys, 1):
        spec = T.layers[key]
        count = L.count(key)
        if progress:
            progress(f"Layer {n}/{len(keys)}: {spec.name} ({count:,} polygons)")
        mode = "blocks" if (count > BLOCK_ABOVE or (spec.kind == "via" and count > BLOCK_VIAS_ABOVE)) \
            else "detail"
        obj = doc.addObject("Mesh::Feature", f"Layer_{key[0]}_{key[1]}")
        obj.Label = f"{spec.name} ({key[0]}/{key[1]})"
        set_role(obj, LAYER)
        _prop(obj, "App::PropertyInteger", "GdsLayer", key[0], "GDS layer number")
        _prop(obj, "App::PropertyInteger", "GdsDatatype", key[1], "GDS datatype")
        _prop(obj, "App::PropertyString", "Level", spec.level, "Stackup level")
        _prop(obj, "App::PropertyString", "StackMaterial", spec.material, "Material in the PDK stackup")
        _prop(obj, "App::PropertyLength", "Z0", spec.z0, "Bottom of the layer")
        _prop(obj, "App::PropertyLength", "Z1", spec.z1, "Top of the layer")
        _prop(obj, "App::PropertyInteger", "Polygons", count, "Drawn polygons")
        _prop(obj, "App::PropertyString", "Display", mode,
              "detail or blocks — the view only; exports write every polygon")
        for p in ("GdsLayer", "GdsDatatype", "Level", "Z0", "Z1", "Polygons", "Display"):
            obj.setEditorMode(p, 1)
        chip.addObject(obj)
        obj.Mesh = _mesh_from(*layer_mesh(chip, obj, mode))
        _style(obj, spec.color)

    for name, solid in solids.die_body(L.outline, T.substrate):
        body = doc.addObject("Part::Feature", f"DieBody_{name}")
        body.Label = f"{chip.Label} {name}"
        body.Shape = solid
        set_role(body, DIE_BODY)
        mat = next((m for n_, m, _t in T.substrate if n_ == name), name)
        _prop(body, "App::PropertyString", "StackMaterial", mat, "Material in the PDK stackup")
        chip.addObject(body)
        _style(body, T.materials.get(mat, {}).get("color", (0.5, 0.5, 0.55)))

    # The oxide and passivation the metal is embedded in: part of the die,
    # so they are there for thermal and EM, but hidden and not pickable —
    # shown, they cover every pad a wire is placed on.
    for name, mat, z0, z1 in T.dielectrics:
        if z0 < -1e-12 or any(name == n for n, _m, _t in T.substrate):
            continue
        add_slab(chip, f"Dielectric_{name}", f"{chip.Label} {name}", DIELECTRIC, L.outline, z0, z1,
                 T.materials.get(mat, {}).get("color", (0.9, 0.9, 0.8)), visible=False,
                 Level=name, StackMaterial=mat)

    # Stack levels the PDK has and this layout does not draw: shown where
    # they would be, as faint die-sized slabs, so the whole stack is visible.
    for lv in T.unused_levels(L.polygons.keys()):
        add_slab(chip, f"Unused_{lv['name']}", f"{lv['name']} (not drawn)", UNUSED_LEVEL, L.outline,
                 lv["z0"], lv["z1"], T.materials.get(lv["material"], {}).get("color", (0.7, 0.7, 0.7)),
                 visible=True, Level=lv["name"], StackMaterial=lv["material"], LevelKind=lv["kind"])

    detect_chip_pads(chip)
    from . import materials
    materials.auto_assign(doc)
    doc.recompute()
    return chip


# ── pads ────────────────────────────────────────────────────────────────────

def _pad_object(doc, container, pad, side):
    obj = doc.addObject("Part::Feature", "Pad")
    obj.Label = f"{pad.name}"
    pts = [V(x - pad.x, y - pad.y, 0) for x, y in pad.outline] or [
        V(-pad.w / 2, -pad.h / 2, 0), V(pad.w / 2, -pad.h / 2, 0),
        V(pad.w / 2, pad.h / 2, 0), V(-pad.w / 2, pad.h / 2, 0)]
    try:
        obj.Shape = Part.Face(Part.makePolygon(pts + [pts[0]]))
    except Part.OCCError:
        obj.Shape = Part.makePlane(pad.w, pad.h, V(-pad.w / 2, -pad.h / 2, 0))
    obj.Placement = FreeCAD.Placement(V(pad.x, pad.y, pad.z), FreeCAD.Rotation())
    set_role(obj, PAD)
    _prop(obj, "App::PropertyString", "PadSide", side, "chip or package")
    _prop(obj, "App::PropertyString", "PadName", pad.name, "Pad name")
    _prop(obj, "App::PropertyLength", "PadWidth", pad.w, "Pad width")
    _prop(obj, "App::PropertyLength", "PadHeight", pad.h, "Pad height")
    container.addObject(obj)
    _style(obj, (1.0, 0.84, 0.0) if side == "chip" else (0.2, 0.9, 0.3))
    return obj


def pads_of(container):
    return [o for o in container.Group if role(o) == PAD]


def all_pads(doc, side):
    return [o for o in doc.Objects if role(o) == PAD and o.PadSide == side]


def pad_centre(pad_obj):
    """The pad's centre on its surface, in global coordinates."""
    return global_placement(pad_obj).Base


def pad_normal(pad_obj):
    """The outward normal of the surface the pad lies on, global: its own z
    axis — +z for a pad on a level surface."""
    return global_placement(pad_obj).Rotation.multVec(V(0, 0, 1))


def detect_chip_pads(chip, pad_layers=None, min_size=0.0):
    """Replace the chip's pads with those on *pad_layers* (default: the
    technology's). Returns the new pad objects."""
    doc = chip.Document
    for old in pads_of(chip):
        doc.removeObject(old.Name)
    found = pd.chip_pads(chip_layout(chip), chip_technology(chip), pad_layers, min_size)
    return [_pad_object(doc, chip, p, "chip") for p in found]


def add_package_pads(container, faces, names=None):
    """
    Package pads on *faces* (global coordinates) of *container*, one each,
    numbered after the ones already there.
    """
    doc = container.Document
    have = len(pads_of(container))
    inv = global_placement(container).inverse()
    out = []
    for i, face in enumerate(faces):
        local = face.copy()
        local.transformShape(inv.toMatrix())
        pad = pd.face_pad(local, (names[i] if names else f"L{have + i + 1}"))
        out.append(_pad_object(doc, container, pad, "package"))
    return out


def mirror_package_pads(container, centre, axes=("x", "y"), tol=1e-6):
    """Add the mirror images of *container*'s package pads across the axes
    through *centre* (global), where no pad is there yet."""
    from ..core import symmetry as sym
    doc = container.Document
    inv = global_placement(container).inverse()
    have = [(pad_centre(p), p) for p in pads_of(container)]
    points = [(c.x, c.y, c.z) for c, _ in have]
    created = []
    for axis in axes:
        for c, p in list(have):
            q = sym.mirror((c.x, c.y, c.z), (centre.x, centre.y), axis)
            if any(abs(q[0] - r[0]) <= tol and abs(q[1] - r[1]) <= tol for r in points):
                continue
            local = inv.multVec(V(*q))
            shape = p.Shape.copy()
            m = FreeCAD.Matrix()
            if axis == "x":
                m.scale(1, -1, 1)
            else:
                m.scale(-1, 1, 1)
            face = shape.transformGeometry(m).Faces[0]
            outline = [(v.Point.x, v.Point.y) for v in face.OuterWire.OrderedVertexes]
            pad = pd.Pad(name=f"L{len(points) + 1}", x=local.x, y=local.y, z=local.z,
                         w=p.PadWidth.Value, h=p.PadHeight.Value,
                         outline=[(local.x + x, local.y + y) for x, y in outline])
            obj = _pad_object(doc, container, pad, "package")
            created.append(obj)
            have.append((pad_centre(obj), obj))
            points.append(q)
    return created


# ── contact points on a surface ─────────────────────────────────────────────
#
# A contact point is a package pad of its own making: a small square on any
# planar face of a board, housing or lid where a wire is to land. It keeps
# the face's frame (core/contacts.FaceFrame) in its container's coordinates,
# so its (u, v) can be edited later and it stays on the board if the board
# moves.

def contact_frame(face, chip=None):
    """The FaceFrame of a planar *face* (global coordinates), measured from
    *chip*'s centre along its axes; from the face's centre without a chip."""
    from ..core import contacts as ct
    if face.Surface.__class__.__name__ != "Plane":
        raise ValueError("contact points go on a flat face")
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    if chip is not None:
        g = global_placement(chip)
        centre, x, y = g.Base, g.Rotation.multVec(V(1, 0, 0)), g.Rotation.multVec(V(0, 1, 0))
    else:
        centre, x, y = face.CenterOfMass, V(1, 0, 0), V(0, 1, 0)
    return ct.face_frame(tuple(face.CenterOfMass), tuple(n), tuple(centre), tuple(x), tuple(y))


def _frame_props(obj, frame):
    """Store *frame* (global) on *obj* in its container's coordinates."""
    inv = parent_frame(obj).inverse()
    _prop(obj, "App::PropertyVector", "FaceOrigin", inv.multVec(V(*frame.origin)), "Contact frame origin")
    for name, axis in (("FaceU", frame.u), ("FaceV", frame.v), ("FaceNormal", frame.n)):
        _prop(obj, "App::PropertyVector", name, inv.Rotation.multVec(V(*axis)), "Contact frame axis")
    for p in ("FaceOrigin", "FaceU", "FaceV", "FaceNormal"):
        obj.setEditorMode(p, 2)


def stored_frame(obj):
    """The FaceFrame a contact point was made on, global."""
    from ..core import contacts as ct
    g = parent_frame(obj)
    vec = lambda v: tuple(g.Rotation.multVec(v))
    return ct.FaceFrame(origin=tuple(g.multVec(obj.FaceOrigin)), n=vec(obj.FaceNormal),
                        u=vec(obj.FaceU), v=vec(obj.FaceV))


def is_contact(obj):
    return role(obj) == PAD and bool(getattr(obj, "ContactPoint", False))


def contact_points(container):
    """*container*'s contact points, in the order they were made."""
    pts = [o for o in pads_of(container) if is_contact(o)]
    return sorted(pts, key=lambda o: getattr(o, "ContactIndex", 0))


def contact_uv(obj):
    return obj.ContactU.Value, obj.ContactV.Value


def _place_contact(obj, frame, uv):
    rot = FreeCAD.Rotation(V(*frame.u), V(*frame.v), V(*frame.n), "ZXY")
    obj.Placement = to_local(obj, FreeCAD.Placement(V(*frame.to_xyz(uv)), rot))
    obj.ContactU, obj.ContactV = uv


def _next_contact_index(container):
    return max([getattr(o, "ContactIndex", 0) for o in contact_points(container)], default=0) + 1


def add_contact_point(container, frame, uv, w=0.1, h=0.1, name=None):
    """One contact point at (u, v) mm of *frame*, a *w* x *h* square."""
    doc = container.Document
    k = _next_contact_index(container)
    name = name or f"C{k}"
    obj = doc.addObject("Part::Feature", "Contact")
    obj.Label = name
    obj.Shape = _square(w, h)
    set_role(obj, PAD)
    _prop(obj, "App::PropertyString", "PadSide", "package", "chip or package")
    _prop(obj, "App::PropertyString", "PadName", name, "Pad name")
    _prop(obj, "App::PropertyLength", "PadWidth", w, "Pad width")
    _prop(obj, "App::PropertyLength", "PadHeight", h, "Pad height")
    _prop(obj, "App::PropertyBool", "ContactPoint", True, "A contact point placed on a surface")
    _prop(obj, "App::PropertyInteger", "ContactIndex", k, "Order in which it was made")
    _prop(obj, "App::PropertyDistance", "ContactU", 0.0, "Along the chip's x axis, from its centre, in the face")
    _prop(obj, "App::PropertyDistance", "ContactV", 0.0, "Along the chip's y axis, from its centre, in the face")
    for p in ("ContactPoint", "ContactIndex"):
        obj.setEditorMode(p, 1)
    container.addObject(obj)
    _frame_props(obj, frame)
    _place_contact(obj, frame, uv)
    _style(obj, (0.2, 0.9, 0.3), 40)
    vo = getattr(obj, "ViewObject", None)
    if vo is not None:
        try:
            vo.DisplayMode = "Flat Lines"
            vo.LineColor = (0.05, 0.45, 0.1)
        except Exception:
            pass
    return obj


def _square(w, h):
    return Part.Face(Part.makePolygon([V(-w / 2, -h / 2, 0), V(w / 2, -h / 2, 0), V(w / 2, h / 2, 0),
                                       V(-w / 2, h / 2, 0), V(-w / 2, -h / 2, 0)]))


def resize_contact_point(obj, w, h):
    """Contact point *obj* as a *w* x *h* square (mm), where it is."""
    pl = obj.Placement
    obj.Shape = _square(w, h)
    obj.Placement = pl
    obj.PadWidth, obj.PadHeight = w, h


def add_contact_points(container, face, points, w=0.1, h=0.1, chip=None, pitch=0.0):
    """
    Contact points on planar *face* (global) of *container* at the global
    *points*, each dropped onto the face and snapped to *pitch* mm round
    the chip's centre. Returns (made, points not on the face).
    """
    from ..core import contacts as ct
    frame = contact_frame(face, chip)
    made, off = [], []
    for p in points:
        uv = ct.snap(frame.to_uv(tuple(p)), pitch)
        q = V(*frame.to_xyz(uv))
        if not face.isInside(q, 1e-6, True):
            off.append(p)
            continue
        made.append(add_contact_point(container, frame, uv, w, h))
    return made, off


def face_corner_frame(face, chip=None):
    """(FaceFrame, width, height) of a flat *face* (global): origin at the
    corner of its extent, x along its straight edge most nearly along the
    chip's x axis (the world's without a chip), y across it — so a
    100 x 50 mm rectangle runs from (0, 0) to (100, 50)."""
    from ..core import contacts as ct
    if face.Surface.__class__.__name__ != "Plane":
        raise ValueError("contact points go on a flat face")
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    x = global_placement(chip).Rotation.multVec(V(1, 0, 0)) if chip is not None else V(1, 0, 0)
    dirs = [tuple(e.Vertexes[-1].Point - e.Vertexes[0].Point) for e in face.Edges
            if e.Curve.__class__.__name__ in ("Line", "LineSegment") and len(e.Vertexes) == 2]
    pts = [tuple(p) for e in face.Edges for p in e.discretize(24)]
    return ct.corner_frame(pts, tuple(n), dirs, tuple(x))


def uv_in(obj, frame):
    """Contact point *obj*'s position in *frame*, (x, y) mm."""
    return frame.to_uv(tuple(pad_centre(obj)))


def move_contact_point(obj, uv, face=None, frame=None):
    """Put contact point *obj* at (x, y) of *frame* (default: the frame it
    was made on), which it keeps from then on. With *face*, a position off
    the face is refused (ValueError)."""
    frame = frame or stored_frame(obj)
    if face is not None and not face.isInside(V(*frame.to_xyz(uv)), 1e-6, True):
        raise ValueError(f"({uv[0]:.4f}, {uv[1]:.4f}) mm is not on the face")
    _frame_props(obj, frame)
    _place_contact(obj, frame, uv)


def mirror_points(objs, axis, frame, centre=(0.0, 0.0), face=None, tol=1e-6):
    """The mirror images of the contact points *objs* over *frame*'s x
    centre line, y centre line, or both (all three images), through
    *centre* — where no point is yet and, with *face*, on the face. Returns
    the new points; they get their originals' size."""
    from ..core import contacts as ct
    if not objs:
        return []
    container = parent_of(objs[0])
    have = [uv_in(o, frame) for o in contact_points(container)]
    axes = ("x", "y", "both") if axis == "both" else (axis,)
    made = []
    for o in objs:
        for a in axes:
            q = ct.mirror_uv(uv_in(o, frame), a, centre)
            if any(abs(q[0] - h[0]) <= tol and abs(q[1] - h[1]) <= tol for h in have):
                continue
            if face is not None and not face.isInside(V(*frame.to_xyz(q)), 1e-6, True):
                continue
            have.append(q)
            made.append(add_contact_point(container, frame, q, o.PadWidth.Value, o.PadHeight.Value))
    return made


def mirror_contact_points(container, axis="both", face=None, tol=1e-6):
    """Add the mirror images of *container*'s contact points across the
    chip's x axis, y axis or both, where none is yet; with *face*, only
    those that land on it. Returns the new points."""
    from ..core import contacts as ct
    pts = contact_points(container)
    if not pts:
        return []
    frame = stored_frame(pts[0])
    w, h = pts[0].PadWidth.Value, pts[0].PadHeight.Value
    made = []
    for uv in ct.mirrored([contact_uv(p) for p in pts], axis, tol):
        if face is not None and not face.isInside(V(*frame.to_xyz(uv)), 1e-6, True):
            continue
        made.append(add_contact_point(container, frame, uv, w, h))
    return made


# ── STEP parts ──────────────────────────────────────────────────────────────

def import_part(doc, step_path, part_role):
    """Import a STEP file as the package's Housing or Lid, or as the Board."""
    if part_role not in CONTAINER_ROLES:
        raise ValueError(part_role)
    before = set(doc.Objects)
    if FreeCAD.GuiUp:
        import ImportGui
        ImportGui.insert(step_path, doc.Name)
    else:
        import Import
        Import.insert(step_path, doc.Name)
    new = [o for o in doc.Objects if o not in before]
    tops = [o for o in new if not any(p in new for p in o.InList)]
    container = doc.addObject("App::Part", part_role.capitalize())
    container.Label = f"{part_role.capitalize()} — {os.path.splitext(os.path.basename(step_path))[0]}"
    set_role(container, part_role)
    _prop(container, "App::PropertyFile", "StepPath", step_path, "The STEP file")
    for o in tops:
        container.addObject(o)
    if part_role == HOUSING:
        package_for_housing(doc).addObject(container)
    elif part_role == LID:
        # onto the last package made: the one being worked on
        (packages(doc) or [package(doc)])[-1].addObject(container)
    doc.recompute()
    return container


def container_shape(container, global_=True):
    """Every solid in *container* as one shape, globally or in its own frame."""
    shape = Part.getShape(container, "", needSubElement=False, transform=False)
    if global_ and not shape.isNull():
        # the container's own placement and every container above it
        shape.transformShape(global_placement(container).toMatrix())
    return shape


def _add_box(bb, box, placement):
    for x in (box.XMin, box.XMax):
        for y in (box.YMin, box.YMax):
            for z in (box.ZMin, box.ZMax):
                bb.add(placement.multVec(V(x, y, z)))


def local_bbox(obj):
    """
    *obj*'s extent in its own frame (its own Placement not applied).

    A package's extent is its bodies only — housing, lid, chip. Wires and pad
    markers are inside it anyway, and a swept wire's bounding box as OCCT
    reports it is loose: counting it put a package 0.3 mm off the centre of
    its footprint.
    """
    if role(obj) == CHIP:
        return chip_local_bbox(obj)
    bb = FreeCAD.BoundBox()
    if role(obj) == PACKAGE:
        for child in obj.Group:
            if role(child) in (HOUSING, LID):
                shape = container_shape(child, global_=False)
                if not shape.isNull():
                    _add_box(bb, shape.BoundBox, child.Placement)
            elif role(child) == CHIP:
                _add_box(bb, chip_local_bbox(child), child.Placement)
        return bb
    shape = container_shape(obj, global_=False)
    if not shape.isNull() and shape.BoundBox.isValid():
        bb.add(shape.BoundBox)
    return bb
