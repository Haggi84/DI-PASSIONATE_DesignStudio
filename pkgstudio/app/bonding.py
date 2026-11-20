# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The bonding diagram and wire list of a package from the document
(core.bonding_diagram): everything in the housing's frame, seen from above.
"""

import os

from ..core import bonding_diagram as bd
from . import housings, model, wires


def _local(housing, v):
    return model.global_placement(housing).inverse().multVec(v)


def _len(q):
    return float(getattr(q, "Value", q))


def data(housing, title="", notes=()):
    """The core.bonding_diagram data dict for *housing* and the dies on it."""
    doc = housing.Document
    chips = housings.chips_on(housing)
    leads = []
    for p in model.pads_of(housing):
        c = _local(housing, model.pad_centre(p))
        leads.append({"name": p.PadName, "x": c.x, "y": c.y, "w": _len(p.PadWidth), "h": _len(p.PadHeight),
                      "net": getattr(p, "LeadNet", "")})
    body = (_len(getattr(housing, "BodyX", 0)), _len(getattr(housing, "BodyY", 0)))
    ac = getattr(housing, "AttachCentre", None)
    paddle = (ac.x, ac.y, _len(housing.AttachWidth), _len(housing.AttachHeight)) if ac is not None else None
    dies = []
    names = {}
    for chip in chips:
        die = housings._paddle_frame_die(chip, housing)
        cx, cy = die.x + (ac.x if ac else 0), die.y + (ac.y if ac else 0)
        pads = []
        for p in model.pads_of(chip):
            c = _local(housing, model.pad_centre(p))
            pads.append({"name": p.PadName, "x": c.x, "y": c.y, "w": _len(p.PadWidth), "h": _len(p.PadHeight),
                         "net": getattr(p, "PadNet", "")})
        dies.append({"name": chip.Label, "x": cx, "y": cy, "w": die.w, "h": die.h, "pads": pads})
        names[chip.Name] = chip.Label
    ws = []
    lead_names = {p.Name for p in model.pads_of(housing)}
    for w in wires.wires(doc):
        if w.ChipPad is None or w.PackagePad is None:
            continue
        ca = wires.chip_of(w.ChipPad)
        if ca is None or ca.Name not in names:
            continue
        to_die = ""
        if getattr(w.PackagePad, "PadSide", "") == "chip":
            cb = wires.chip_of(w.PackagePad)
            to_die = names.get(cb.Name, cb.Label) if cb is not None else "?"
        elif w.PackagePad.Name not in lead_names:
            continue
        pa = _local(housing, model.pad_centre(w.ChipPad))
        pb = _local(housing, model.pad_centre(w.PackagePad))
        net = getattr(w.ChipPad, "PadNet", "") or getattr(w.PackagePad, "LeadNet", "")
        ws.append({"name": w.Label, "net": net, "a": (names[ca.Name], w.ChipPad.PadName),
                   "b": (to_die, w.PackagePad.PadName), "pa": (pa.x, pa.y), "pb": (pb.x, pb.y),
                   "material": getattr(w, "WireMaterial", ""), "d_um": round(_len(w.WireDiameter) * 1000, 1),
                   "loop_um": round(_len(w.LoopHeight) * 1000, 1), "length_mm": _len(w.WireLength)})
    ws.sort(key=lambda w: (w["b"][0] != "", _num(w["b"][1])))
    return {"title": title or f"Bonding diagram — {getattr(housing, 'HousingName', housing.Label)}",
            "package": {"name": getattr(housing, "HousingName", housing.Label), "body": body, "paddle": paddle,
                        "leads": leads},
            "dies": dies, "wires": ws, "notes": list(notes)}


def _num(name):
    try:
        return int(name)
    except (TypeError, ValueError):
        return 10 ** 6


def export(housing, folder, stem=None, intent=None, title="", notes=()):
    """<stem>_bonding.svg and <stem>_wires.csv in *folder*. Returns both paths."""
    os.makedirs(folder, exist_ok=True)
    d = data(housing, title, notes)
    kinds = {p.net: p.kind for p in intent.pins} if intent is not None else {}
    stem = stem or getattr(housing, "HousingName", housing.Label).replace(" ", "_")
    return (bd.write_svg(d, os.path.join(folder, f"{stem}_bonding.svg"), kinds),
            bd.write_csv(d, os.path.join(folder, f"{stem}_wires.csv")))
