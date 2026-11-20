# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Materials on document objects, assigned automatically where the model says
what something is, and carried into every export.

Each element holds its material's name in a Material property. Assigned
automatically:

  chip layers, die body   the PDK stackup's material for that layer
  housing body            mould compound   (from the housing analysis)
  leadframe               copper alloy C194
  die attach              Ag-filled epoxy
  lid                     what the lid was made of
  board                   FR-4 for the board's laminate
  bond wire               its wire metal

Everything else is left for the Materials panel. A name not in the library
stays as written and is reported in the export manifest as unknown, rather
than silently replaced.
"""

from ..core import materials as mat, tech as tc
from . import model

PROP = "Material"
GROUP = "Material"

HOUSING_DEFAULTS = {"body": "Mould compound (EMC)", "leadframe": "Copper alloy C194 (leadframe)"}
DIE_ATTACH = "Die attach epoxy, Ag-filled"
BOARD = "FR-4"


def set_material(obj, name):
    if not hasattr(obj, PROP):
        obj.addProperty("App::PropertyString", PROP, GROUP, "What this element is made of (Materials panel)")
    setattr(obj, PROP, name or "")
    m = available(obj.Document).get(name)
    vo = getattr(obj, "ViewObject", None)
    if m is not None and vo is not None and model.role(obj) not in (model.LAYER, model.UNUSED_LEVEL):
        try:
            vo.ShapeColor = tuple(m.color)
        except Exception:
            pass


def material_of(obj):
    return getattr(obj, PROP, "") or ""


def available(doc):
    """{name: Material}: the library, every chip's technology materials, the user's own."""
    lib = mat.library()           # bundled + the user's own
    out = dict(lib)
    seen = set()
    for chip in model.by_role(doc, model.CHIP):
        folder = model.relocated(getattr(chip, "TechFolder", ""), chip.Document)
        if folder and folder not in seen:
            seen.add(folder)
            try:
                out.update(mat.technology_materials(tc.load(folder), lib))
            except Exception:
                pass
    return out


def auto_assign(doc, overwrite=False):
    """Give every element the material its kind implies. Returns how many
    were assigned. Keeps a material already chosen unless *overwrite*."""
    n = 0

    def give(obj, name):
        nonlocal n
        if name and (overwrite or not material_of(obj)):
            set_material(obj, name)
            n += 1

    for chip in model.by_role(doc, model.CHIP):
        T = model.chip_technology(chip)
        for o in chip.Group:
            if model.role(o) == model.LAYER:
                spec = T.layers.get((o.GdsLayer, o.GdsDatatype))
                give(o, mat.layer_material(T, spec) if spec else None)
            elif model.role(o) in (model.DIE_BODY, model.DIELECTRIC, model.UNUSED_LEVEL):
                give(o, mat.body_material(T, getattr(o, "StackMaterial", "")))
    for o in doc.Objects:
        part = getattr(o, "HousingPart", "")
        if part in HOUSING_DEFAULTS:
            give(o, HOUSING_DEFAULTS[part])
        elif model.role(o) == "die-attach":
            give(o, DIE_ATTACH)
        elif model.role(o) == model.WIRE:
            give(o, mat.WIRE_MATERIALS.get(getattr(o, "WireMaterial", "Al")))
        elif getattr(o, "LidMaterial", ""):
            give(o, o.LidMaterial)
    for board in model.by_role(doc, model.BOARD):
        leaves = [o for o in _leaves(board)]
        if leaves:
            give(max(leaves, key=lambda o: o.Shape.BoundBox.XLength * o.Shape.BoundBox.YLength), BOARD)
    doc.recompute()
    return n


def _leaves(container):
    out = []
    for o in container.Group:
        if o.isDerivedFrom("App::Part") or o.isDerivedFrom("App::DocumentObjectGroup"):
            out += _leaves(o)
        elif o.isDerivedFrom("Part::Feature") and model.role(o) != model.PAD and o.Shape.Solids:
            out.append(o)
    return out


def manifest_row(element, lib):
    """One manifest entry: element, material and every property, or the
    material's name flagged as unknown."""
    name = material_of(element.obj)
    m = lib.get(name)
    row = {"element": element.name, "material": name or None}
    if m is not None:
        row.update({k: v for k, v in m.as_dict().items() if k != "name"})
    elif name:
        row["unknown"] = True
    return row
