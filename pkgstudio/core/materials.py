# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Materials: what every exported element is made of, for thermal and EM work.

Three sources, merged by name:

  * the bundled library (materials/library.json) — bond-wire metals,
    leadframe alloys, mould compounds, laminates, ceramics, glass;
  * the chip's technology — every material its stackup declares, with the
    PDK's OWN conductivity, permittivity and loss tangent. An EM model of a
    SG13G2 chip must use 21.64 MS/m for Metal1, not a handbook value for
    bulk aluminium. The thermal half comes from the bulk metal the layer is
    made of: the stackup's ThermalMaterial where it names one, otherwise a
    stated guess (a via is tungsten, a metal aluminium, an active area
    silicon) that the manifest marks as such;
  * the user's own file (PackageStudio/materials.json in FreeCAD's user
    folder), same format as the library — it wins on a clash.

A material is plain data; nothing here needs FreeCAD.
"""

import json
import os
from dataclasses import asdict, dataclass

LIBRARY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "materials", "library.json")
FIELDS = ("density", "k", "cp", "cte", "sigma", "eps_r", "mu_r", "tan_d")


@dataclass
class Material:
    name: str
    category: str = ""
    color: tuple = (0.6, 0.6, 0.6)
    density: float = None      # kg/m3
    k: float = None            # W/(m K)
    cp: float = None           # J/(kg K)
    cte: float = None          # ppm/K
    sigma: float = None        # S/m
    eps_r: float = None
    mu_r: float = None
    tan_d: float = None
    source: str = ""

    def as_dict(self):
        d = asdict(self)
        d["color"] = "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in self.color)
        return d

    @property
    def missing(self):
        """The properties nobody has given a value — say so rather than invent one."""
        return [f for f in FIELDS if getattr(self, f) is None]


def _rgb(text):
    text = (text or "").lstrip("#")
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.6, 0.6, 0.6)


def _from_json(entry):
    kw = {f: entry.get(f) for f in FIELDS}
    return Material(name=entry["name"], category=entry.get("category", ""),
                    color=_rgb(entry.get("color")), source=entry.get("source", ""), **kw)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return [_from_json(e) for e in json.load(fh).get("materials", [])]


def user_file():
    try:
        import FreeCAD
        return os.path.join(FreeCAD.getUserAppDataDir(), "PackageStudio", "materials.json")
    except Exception:
        return None


def library(extra_files=()):
    """{name: Material}: the bundled library, then *extra_files*, then the
    user's file — later ones win."""
    out = {m.name: m for m in read(LIBRARY)}
    for path in list(extra_files) + [user_file()]:
        if path and os.path.isfile(path):
            out.update({m.name: m for m in read(path)})
    return out


def save_user_material(material, path=None):
    """Add or replace *material* in the user's file."""
    path = path or user_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"materials": []}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    data["materials"] = [m for m in data.get("materials", []) if m.get("name") != material.name]
    data["materials"].append(material.as_dict())
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return path


# ── wires ───────────────────────────────────────────────────────────────────

WIRE_MATERIALS = {
    "Al": "Aluminium 1% Si wire (AlSi1)",
    "Au": "Gold (Au)",
    "Cu": "Copper (Cu)",
    "PdCu": "Palladium-coated copper (PdCu)",
    "Ag": "Silver (Ag)",
}


# ── technologies ────────────────────────────────────────────────────────────

_BULK = {"aluminium": "Aluminium (Al)", "aluminum": "Aluminium (Al)", "copper": "Copper (Cu)",
         "gold": "Gold (Au)", "tungsten": "Tungsten (W)", "silicon": "Silicon (Si)"}


def _thermal_base(name, info, kind):
    """(library material, how it was chosen) for a stackup material."""
    named = (info.get("thermal") or "").lower()
    if named in _BULK:
        return _BULK[named], "stackup ThermalMaterial"
    low = name.lower()
    if info.get("type") == "semiconductor" or low in ("activ", "diff", "tap", "poly", "epi", "substrate"):
        return "Silicon (Si)", "guess: semiconductor"
    if info.get("type") == "dielectric":
        return "Silicon dioxide (SiO2)", "guess: dielectric"
    if kind == "via" or "via" in low or low in ("cont", "licon1", "mcon"):
        return "Tungsten (W)", "guess: via plug"
    return "Aluminium (Al)", "guess: metal"


def technology_materials(tech, lib=None):
    """
    {name: Material} for every material *tech*'s stackup declares, named
    "<technology> / <material>": the PDK's electrical values, the bulk
    material's thermal ones.
    """
    lib = lib or library()
    short = tech.name.split("(")[0].strip()
    kinds = {}
    for spec in tech.layers.values():
        kinds.setdefault(spec.material, spec.kind)
    out = {}
    for name, info in tech.materials.items():
        base_name, how = _thermal_base(name, info, kinds.get(name))
        base = lib.get(base_name, Material(base_name))
        m = Material(name=f"{short} / {name}", category=info.get("type") or base.category,
                     color=info.get("color") or base.color,
                     density=base.density, k=base.k, cp=base.cp, cte=base.cte,
                     sigma=info["sigma"] if info.get("sigma") is not None else base.sigma,
                     eps_r=info["eps_r"] if info.get("eps_r") is not None else base.eps_r,
                     mu_r=base.mu_r if base.mu_r is not None else 1.0,
                     tan_d=info["tan_d"] if info.get("tan_d") is not None else base.tan_d,
                     source=f"{tech.name} stackup (electrical); {base_name} (thermal, {how})")
        out[m.name] = m
    return out


def layer_material(tech, spec):
    """The material name of a chip layer: its stackup material."""
    short = tech.name.split("(")[0].strip()
    return f"{short} / {spec.material}" if spec.material else None


def body_material(tech, stackup_material):
    short = tech.name.split("(")[0].strip()
    return f"{short} / {stackup_material}"
