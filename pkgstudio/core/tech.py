# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Technologies: what a GDS layer is made of and where it sits.

A technology is a folder under technologies/ holding the PDK's KLayout layer
properties (.lyp — names and colours), its stackup (.xml — heights and
materials) and a tech.json that names the files and the layer the bond-pad
openings are drawn on. Pick one before importing a chip; the import reads
everything it needs from it.

Which layers become 3-D geometry is decided here and nowhere else:

  * a layer the stackup names exactly, by number and datatype (SKY130 does);
  * otherwise the stackup's bare layer number, but only for the purposes that
    are real material — "drawing" and "filler". Metal fill is metal; a pin,
    label, net or boundary shape is a marker, and building it would put
    metal where the chip has none.
  * "slit" shapes are holes cut into wide metal; the import subtracts them.

Nothing here needs FreeCAD, so it can be tested and reused anywhere.
"""

import ast
import json
import operator
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

PHYSICAL_PURPOSES = {"drawing", "filler"}
CUT_PURPOSE = "slit"

TECH_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "technologies")


@dataclass
class LayerSpec:
    """One GDS layer that is built in 3-D."""
    key: tuple            # (layer, datatype)
    name: str             # the .lyp name, e.g. "Metal1.drawing"
    level: str            # the stackup level, e.g. "Metal1"
    kind: str             # "conductor" or "via"
    z0: float             # mm
    z1: float             # mm
    color: tuple          # (r, g, b) 0..1
    material: str

    @property
    def thickness(self):
        return self.z1 - self.z0


@dataclass
class Technology:
    name: str
    folder: str
    layers: dict = field(default_factory=dict)      # key -> LayerSpec
    cuts: dict = field(default_factory=dict)        # cut key -> [layer keys it cuts]
    pad_layers: list = field(default_factory=list)  # [(layer, datatype)]
    lyp_names: dict = field(default_factory=dict)   # key -> lyp name, every entry
    substrate: list = field(default_factory=list)   # [(name, material, thickness mm)], top first
    materials: dict = field(default_factory=dict)   # material name -> {"color": (r,g,b), "type": str}
    levels: list = field(default_factory=list)      # every stackup level: dict(name, kind, z0, z1, ...)
    dielectrics: list = field(default_factory=list) # [(name, material, z0, z1)] top first, no air

    def unused_levels(self, keys_present):
        """The stack levels none of *keys_present* is built on — what the
        PDK has at that height but this layout does not draw — bottom first.
        A level with no layer in the .lyp at all is left out: no layout can
        draw it, so it says nothing about this one."""
        used = {self.layers[k].level for k in keys_present if k in self.layers}
        built = {s.level for s in self.layers.values()}
        return sorted((lv for lv in self.levels if lv["name"] in built and lv["name"] not in used),
                      key=lambda lv: (lv["z0"], lv["z1"]))

    def known(self, keys):
        """How many of the GDS (layer, datatype) *keys* this technology names."""
        return sum(1 for k in keys if k in self.lyp_names or k in self.layers)

    @property
    def body_thickness(self):
        """Silicon below the stack (epi + substrate), mm."""
        return sum(t for _n, _m, t in self.substrate)

    @property
    def stack_top(self):
        return max((l.z1 for l in self.layers.values()), default=0.0)

    def top_conductor_at(self, keys_present):
        """The highest conductor level among *keys_present* — where pads sit."""
        tops = [self.layers[k] for k in keys_present
                if k in self.layers and self.layers[k].kind == "conductor"]
        return max(tops, key=lambda l: l.z1) if tops else None


# ── file readers ────────────────────────────────────────────────────────────

def _hex_rgb(text, default=(0.6, 0.6, 0.6)):
    text = (text or "").strip().lstrip("#")
    if len(text) != 6:
        return default
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return default


def parse_source(source):
    """'68/20', '68/20@1' -> (68, 20). KLayout appends @n per layout."""
    src = str(source).strip().split("@", 1)[0]
    layer, _, datatype = src.partition("/")
    return int(layer), int(datatype or 0)


def read_lyp(path):
    """{(layer, datatype): (name, fill colour)} for every entry with a source."""
    out = {}
    root = ET.parse(path).getroot()
    for prop in root.iter("properties"):
        source = prop.findtext("source")
        if not source or "/" not in source:
            continue
        try:
            key = parse_source(source)
        except ValueError:
            continue
        name = (prop.findtext("name") or "").strip() or f"{key[0]}/{key[1]}"
        out.setdefault(key, (name, _hex_rgb(prop.findtext("fill-color"))))
    return out


# Two ways of writing a stackup are in use, and both must give the same
# heights:
#
#   * schema 2.0 (the bundled files): absolute Zmin/Zmax for each layer, a
#     <Substrate Offset> that shifts them, dielectrics stacked by Thickness;
#   * schema 3.x (current IHP files, e.g. SG13G2_FEM_200um.xml): a layer or
#     dielectric sits on another one's Top or Bottom edge
#     (Reference/ReferenceEdge), and any attribute may be "=expression" over
#     <Variables>.
#
# Read by the 2.0 rules alone, a 3.x file has every Zmin at 0 and gives
# nonsense heights. Every position is resolved in the file's own frame and
# then shifted so that z = 0 is the top of the silicon, which is where this
# workbench puts the die surface.

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def evaluate(expr, variables):
    """An arithmetic expression over *variables*: + - * / **, parentheses,
    numbers and names, nothing else — a stackup file is data, not code.
    An unknown name raises KeyError."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise KeyError(node.id)
            return variables[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"not allowed in a stackup expression: {expr!r}")
    return ev(ast.parse(expr.strip(), mode="eval"))


def read_variables(root):
    """{name: value} of <Variables>, resolved in dependency order whatever
    the order in the file."""
    pending = {v.get("Name"): (v.get("Value") or "").strip()
               for v in root.iter("Variable") if v.get("Name")}
    out = {}
    while pending:
        progress = False
        for name, text in list(pending.items()):
            try:
                if text.startswith("="):
                    out[name] = evaluate(text[1:], out)
                else:
                    try:
                        out[name] = float(text)
                    except ValueError:
                        out[name] = text
            except KeyError:
                continue                    # needs one not resolved yet
            del pending[name]
            progress = True
        if not progress:
            raise ValueError(f"stackup variables cannot be resolved: {sorted(pending)}")
    return out


def _attr(el, name, variables, default=None):
    """An attribute as a float, or the text where it is not a number;
    "=expr" is evaluated. *default* when absent."""
    text = (el.get(name) or "").strip()
    if not text:
        return default
    if text.startswith("="):
        return evaluate(text[1:], variables)
    try:
        return float(text)
    except ValueError:
        return text


def _resolve_relative(elements, placed, var, others=None, dielectric=False):
    """Put each Reference-positioned element on its reference's edge, over
    and over until every reference is known: a reference may itself be
    relative, in any file order. Positions in um, into *placed*."""
    pending = list(elements)
    while pending:
        progress = False
        for el in list(pending):
            ref = el.get("Reference")
            target = placed.get(ref) or (others or {}).get(ref)
            if target is None:
                continue
            top = (el.get("ReferenceEdge") or "Top").strip().lower() == "top"
            edge = target[1] if top else target[0]
            lo = _attr(el, "Zmin", var, 0.0)
            if dielectric:
                hi = _attr(el, "Zmax", var, lo + _attr(el, "Thickness", var, 0.0))
            else:
                hi = _attr(el, "Zmax", var, 0.0)
            placed[el.get("Name")] = (edge + lo, edge + hi)
            pending.remove(el)
            progress = True
        if not progress:
            raise ValueError("stackup references that cannot be resolved: "
                             f"{[(e.get('Name'), e.get('Reference')) for e in pending]}")


def _is_air(name, material):
    return "AIR" in (str(name).strip().upper(), str(material).strip().upper())


def read_stackup(path):
    """
    (levels, substrate, materials, dielectrics), in mm, z = 0 at the top of
    the silicon:

    levels       [dict(name, kind, z0, z1, layer, datatype, material)]:
                 every conductor and via level at or above the die surface;
    substrate    [(name, material, thickness)], top first: the semiconductor
                 dielectrics — the die body under the stack;
    materials    {name: {color, type, sigma, eps_r, tan_d, k, thermal}};
    dielectrics  [(name, material, z0, z1)], top first: silicon, oxide,
                 passivation. Air is left out; it is not part of the die.
    """
    root = ET.parse(path).getroot()
    var = read_variables(root)

    materials = {}
    for mat in root.iter("Material"):
        name = (mat.get("Name") or "").strip()
        if not name:
            continue
        nums = {a: _attr(mat, a, var) for a in ("Conductivity", "Permittivity",
                                                 "DielectricLossTangent", "ThermalConductivity")}
        nums = {a: (v if isinstance(v, float) else None) for a, v in nums.items()}
        # The PDK's own electrical values: what an EM model must use for
        # this process, rather than a handbook value for the bulk metal.
        materials[name] = {"color": _hex_rgb(str(_attr(mat, "Color", var, ""))),
                           "type": (mat.get("Type") or "").lower(),
                           "sigma": nums["Conductivity"],
                           "eps_r": nums["Permittivity"],
                           "tan_d": nums["DielectricLossTangent"],
                           "k": nums["ThermalConductivity"],
                           "thermal": (mat.get("ThermalMaterial") or "").strip()}

    # Dielectrics, in the file's frame (um): stacked up from 0 by Thickness
    # in file order (listed top first), absolute, or on another's edge.
    diels = list(root.iter("Dielectric"))
    box = {}
    z = 0.0
    for d in reversed(diels):
        if d.get("Reference") is None and (d.get("Zmin") is None or d.get("Zmax") is None):
            t = _attr(d, "Thickness", var, 0.0)
            box[d.get("Name")] = (z, z + t)
            z += t
    for d in diels:
        if d.get("Reference") is None and d.get("Name") not in box:
            box[d.get("Name")] = (_attr(d, "Zmin", var, 0.0), _attr(d, "Zmax", var, 0.0))
    _resolve_relative([d for d in diels if d.get("Reference") is not None], box, var,
                      dielectric=True)

    # Layers: absolute plus the Substrate Offset, or on a dielectric's or
    # another layer's edge.
    offset = 0.0
    for sub in root.iter("Substrate"):
        offset = _attr(sub, "Offset", var, 0.0)
    layers = list(root.iter("Layer"))
    lbox = {}
    for el in layers:
        if el.get("Reference") is None:
            lbox[el.get("Name")] = (_attr(el, "Zmin", var, 0.0) + offset,
                                    _attr(el, "Zmax", var, 0.0) + offset)
    _resolve_relative([el for el in layers if el.get("Reference") is not None],
                      lbox, var, others=box)

    # z = 0 at the top of the silicon. A stack without semiconductor
    # dielectrics keeps its own frame, less its Substrate Offset.
    ground = max((box[d.get("Name")][1] for d in diels
                  if materials.get((d.get("Material") or "").strip(), {}).get("type") == "semiconductor"),
                 default=offset)

    levels = []
    for el in layers:
        kind = (el.get("Type") or "conductor").strip().lower()
        num = _attr(el, "Layer", var)
        if kind not in ("conductor", "via") or not isinstance(num, float):
            continue          # sheets and drawn dielectrics are not built as metal
        z0, z1 = (v - ground for v in lbox[el.get("Name")])
        if z1 <= z0 or z0 < -1e-9:
            continue          # ground planes below the die surface are not drawn layers
        dt = _attr(el, "Datatype", var)
        levels.append(dict(name=el.get("Name", ""), kind=kind, z0=max(z0, 0.0) / 1000.0,
                           z1=z1 / 1000.0, layer=int(num),
                           datatype=int(dt) if isinstance(dt, float) else None,
                           material=(el.get("Material") or "").strip()))

    dielectrics = []
    for d in diels:
        name = d.get("Name", "")
        mat = (d.get("Material") or name).strip()
        z0, z1 = (v - ground for v in box[name])
        if not _is_air(name, mat) and z1 > z0:
            dielectrics.append((name, mat, z0 / 1000.0, z1 / 1000.0))
    dielectrics.sort(key=lambda d: -d[3])
    substrate = [(n, m, z1 - z0) for n, m, z0, z1 in dielectrics
                 if materials.get(m, {}).get("type") == "semiconductor"]
    return levels, substrate, materials, dielectrics


# ── technologies ────────────────────────────────────────────────────────────

def presets(root=TECH_ROOT):
    """[(name, folder)] of every technology under *root*, sorted by name."""
    found = []
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            cfg = os.path.join(root, entry, "tech.json")
            if os.path.isfile(cfg):
                with open(cfg, encoding="utf-8") as fh:
                    found.append((json.load(fh).get("name", entry), os.path.join(root, entry)))
    return sorted(found)


def load(folder):
    """The Technology described by *folder*/tech.json."""
    with open(os.path.join(folder, "tech.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    lyp = read_lyp(os.path.join(folder, cfg["lyp"]))
    levels, substrate, materials, dielectrics = read_stackup(os.path.join(folder, cfg["stackup"]))
    tech = Technology(name=cfg.get("name", os.path.basename(folder)), folder=folder,
                      pad_layers=[tuple(k) for k in cfg.get("pad_layers", [])],
                      lyp_names={k: v[0] for k, v in lyp.items()},
                      substrate=substrate, materials=materials,
                      levels=levels, dielectrics=dielectrics)

    exact = {(lv["layer"], lv["datatype"]): lv for lv in levels if lv["datatype"] is not None}
    bare = {}
    for lv in levels:
        if lv["datatype"] is None:
            bare.setdefault(lv["layer"], lv)

    for key, (name, color) in lyp.items():
        purpose = name.rsplit(".", 1)[-1].lower() if "." in name else "drawing"
        level = exact.get(key)
        if level is None and purpose in PHYSICAL_PURPOSES:
            level = bare.get(key[0])
        if level is not None:
            mat_color = materials.get(level["material"], {}).get("color")
            tech.layers[key] = LayerSpec(key=key, name=name, level=level["name"],
                                         kind=level["kind"], z0=level["z0"], z1=level["z1"],
                                         color=color if color else mat_color,
                                         material=level["material"])
        elif purpose == CUT_PURPOSE:
            tech.cuts[key] = []

    # A slit cuts the physical layers that share its layer number.
    for cut in tech.cuts:
        tech.cuts[cut] = [k for k in tech.layers if k[0] == cut[0]]
    return tech


def match(keys, root=TECH_ROOT):
    """
    [(name, folder, known, total)] for every technology under *root*, the
    best match for the GDS (layer, datatype) *keys* first.

    A layout read with the wrong technology builds nothing — no layer
    matches — and comes in as a bare die body; an IHP layout read as SKY130
    looked exactly like that. Counting which technology knows the layout's
    layers tells which one it was drawn in.
    """
    keys = set(keys)
    out = []
    for name, folder in presets(root):
        try:
            out.append((name, folder, load(folder).known(keys), len(keys)))
        except Exception:
            continue
    return sorted(out, key=lambda m: (-m[2], m[0]))


def suggestion(chosen_folder, keys, root=TECH_ROOT):
    """The technology to suggest instead of *chosen_folder* for *keys*, as
    (name, folder, known, chosen_known, total), or None when the chosen one
    is right: it knows most of the layout, or no other knows clearly more."""
    ranked = match(keys, root)
    if not ranked:
        return None
    chosen = next((m for m in ranked if os.path.normcase(os.path.abspath(m[1]))
                   == os.path.normcase(os.path.abspath(chosen_folder))), None)
    best = ranked[0]
    have = chosen[2] if chosen else 0
    if chosen is best or best[2] == 0:
        return None
    # Clearly better: more than half the layout's layers, and twice as many
    # as the chosen technology knows.
    if best[2] > 0.5 * best[3] and best[2] >= 2 * max(have, 1):
        return best[0], best[1], best[2], have, best[3]
    return None
