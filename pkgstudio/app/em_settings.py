# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The EM panel's choices kept in the document: solver, frequency range, mesh
density and smallest cell. A document opened again — or an example opened
straight from a cloned repository — shows the EM panel as it was set.

They live as properties of one small object, "EM settings", that holds no
geometry (the simulation model never sees it). Chip metal per chip is kept
on the chip (EmChipMetal), ports and their excitation on the port objects.
"""

import FreeCAD

from . import model

ROLE = "em-settings"
LABEL = "EM settings"
GROUP = "EM"
FIELDS = (("EmSolver", "App::PropertyString", "Solver: openEMS, EMerge or Palace"),
          ("EmFminGHz", "App::PropertyFloat", "Lowest frequency, GHz"),
          ("EmFmaxGHz", "App::PropertyFloat", "Highest frequency, GHz"),
          ("EmMesh", "App::PropertyString", "Mesh density: coarse, normal or fine"),
          ("EmMinCellUm", "App::PropertyFloat", "Smallest cell, um (x/y; z is capped at half the thinnest copper)"),
          ("EmNote", "App::PropertyString", "Why these settings"))
KEYS = {"solver": "EmSolver", "fmin_ghz": "EmFminGHz", "fmax_ghz": "EmFmaxGHz", "mesh": "EmMesh",
        "min_cell_um": "EmMinCellUm", "note": "EmNote"}


def holder(doc, create=False):
    """The document's EM settings object, or None (made with *create*)."""
    for o in model.by_role(doc, ROLE):
        return o
    if not create:
        return None
    o = doc.addObject("App::DocumentObjectGroup", "EMSettings")
    o.Label = LABEL
    model.set_role(o, ROLE)
    for name, kind, tip in FIELDS:
        o.addProperty(kind, name, GROUP, tip)
    return o


def load(doc):
    """{solver, fmin_ghz, fmax_ghz, mesh, min_cell_um, note} as stored, or {}."""
    o = holder(doc) if doc is not None else None
    if o is None:
        return {}
    out = {}
    for key, prop in KEYS.items():
        v = getattr(o, prop, None)
        if v not in (None, "", 0.0):
            out[key] = v
    return out


def save(doc, **values):
    """Store the given settings (keys as in load) in *doc*."""
    o = holder(doc, create=True)
    for key, value in values.items():
        if key in KEYS and value is not None:
            setattr(o, KEYS[key], value)
    return o
