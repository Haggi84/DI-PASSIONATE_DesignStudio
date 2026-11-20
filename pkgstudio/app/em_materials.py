# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
One material system for the 3-D model and the EM simulation.

Every element's Material property (app.materials) names a material of the
library, of a chip's PDK, or of the user's own file, with its conductivity,
permittivity and loss tangent. The EM simulation uses exactly that: each
such material is registered with OTTER under the same name, so what the
Materials panel says is what the solver gets, and a material changed in the
EM panel is written back to the Material property and seen everywhere.

OTTER on its own has to guess materials from labels and colours. That is
kept only for parts nobody gave a material — a board's components from a
KiCad STEP, say — and the readiness check lists them.

The line between conductor and dielectric is the one the Palace export of
Package Designer drew: from 1e4 S/m up a material is a metal. Ag-filled die
attach (1e5 S/m) is then a conductor, doped silicon (2 S/m) a lossy
dielectric, which is how each acts at GHz.
"""

from ..core import otter_path
from . import materials as mats

otter_path.ensure()

from otter_llm.starter import MATERIALS, NOT_SIMULATED, Material  # noqa: E402

#: what a person picks to leave a part out of the simulation
NOT_SIMULATED_NAME = "not simulated"
#: S/m from which a material is a conductor
CONDUCTOR_SIGMA = 1e4


def otter_material(m):
    """The OTTER Material for library material *m*, or (None, why not)."""
    sigma = m.sigma or 0.0
    if sigma >= CONDUCTOR_SIGMA:
        return Material(m.name, True, sigma=float(sigma)), ""
    if m.eps_r is None:
        return None, f"{m.name}: no relative permittivity (ε_r) and not a conductor"
    return Material(m.name, False, sigma=float(sigma), epsilon=float(m.eps_r), tand=float(m.tan_d or 0.0)), ""


def register(doc):
    """Register every material *doc* can use with OTTER. Returns
    {name: reason} for those that cannot be simulated as they stand."""
    problems = {}
    for name, m in mats.available(doc).items():
        om, why = otter_material(m)
        if om is None:
            problems[name] = why
        else:
            MATERIALS[name] = om
    return problems


def register_scaled(base, fill):
    """A via array's material: *base*'s conductivity times *fill* (the share
    of the block that is via), under its own name. Returns the name."""
    m = MATERIALS[base]
    name = f"{base} (via fill {fill:.2f})"
    MATERIALS[name] = Material(name, True, sigma=m.sigma * fill)
    return name


def is_conductor(name):
    m = MATERIALS.get(name)
    return bool(m and m.conductor)


def choices(doc):
    """What the EM panel offers for a layer: every material of the
    document that OTTER can simulate, then "not simulated"."""
    register(doc)
    names = [n for n in mats.available(doc) if n in MATERIALS]
    return sorted(names, key=str.lower) + [NOT_SIMULATED_NAME]


def described(name):
    """'Copper (Cu): σ 5.96e7 S/m' — for tooltips and the assistant."""
    m = MATERIALS.get(name)
    if m is None:
        return name or NOT_SIMULATED_NAME
    if m.conductor:
        return f"{name}: σ {m.sigma:.3g} S/m"
    return f"{name}: ε_r {m.epsilon:g}, tan δ {m.tand:g}" + (f", σ {m.sigma:g} S/m" if m.sigma else "")
