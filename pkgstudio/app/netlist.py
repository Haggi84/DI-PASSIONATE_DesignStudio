# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The pin list (core.pinlist) in the document: which chip is which die of the
pin list, every pad's net, the wires laid by net, and the package checked
against the pin list (core.package_lvs).

  match_dies   intent die name -> chip (by label, top cell or GDS name)
  apply        PadNet / PadKind on every chip pad the pin list names;
               the pin list's pads no chip has are returned
  assign       the wires the pin list asks for: fixed leads first, then
               die-to-die nets, then every other pad crossing-free on the
               leads left (wires.auto_pair_mcm); returns the new wires
  lvs          (findings, lead nets): the check, with LeadNet set on each
               lead that carries one net
"""

import os

from ..core import package_lvs, pinlist
from . import housings, model, wires


def _prop(obj, kind, name, value, tip=""):
    if not hasattr(obj, name):
        obj.addProperty(kind, name, "Netlist", tip)
    setattr(obj, name, value)


def _names(chip):
    out = {pinlist.norm(chip.Label), pinlist.norm(getattr(chip, "Cell", "") or "")}
    gds = getattr(chip, "GdsPath", "") or ""
    if gds:
        stem = os.path.basename(gds)
        for ext in (".gz", ".gds", ".GDS", ".oas"):
            stem = stem[:-len(ext)] if stem.endswith(ext) else stem
        out.add(pinlist.norm(stem))
    return out - {""}


def match_dies(intent, chips):
    """{intent die name: chip}, by label, top cell or GDS file name. When
    exactly one die and one chip are left over, they are taken to be the
    same (a single-die package whose GDS was renamed); `guessed` tells."""
    out = {}
    for die in intent.dies():
        n = pinlist.norm(die)
        hit = [c for c in chips if n in _names(c) and c not in out.values()]
        if hit:
            out[die] = hit[0]
    left = [d for d in intent.dies() if d not in out]
    free = [c for c in chips if c not in out.values()]
    if len(left) == 1 and len(free) == 1:
        out[left[0]] = free[0]
    return out


def guessed(intent, chips):
    """[(die, chip label)] matched only because they were the ones left over."""
    return [(d, c.Label) for d, c in match_dies(intent, chips).items() if pinlist.norm(d) not in _names(c)]


def apply(doc, intent, chips=None):
    """Set PadNet/PadKind on the pads the pin list names. Returns the pins
    no chip has (by die, then by pad)."""
    chips = chips if chips is not None else model.by_role(doc, model.CHIP)
    dies = match_dies(intent, chips)
    missing = []
    for p in intent.pins:
        chip = dies.get(p.die)
        pad = None
        if chip is not None:
            pad = next((q for q in model.pads_of(chip) if q.PadName == p.pad), None)
        if pad is None:
            missing.append(p)
            continue
        _prop(pad, "App::PropertyString", "PadNet", p.net, "The net of this pad (pin list)")
        _prop(pad, "App::PropertyString", "PadKind", p.kind, "signal, rf, power, ground or nc")
    return missing


def _chip_pad(dies, die, pad):
    chip = dies.get(die)
    if chip is None:
        return None
    return next((q for q in model.pads_of(chip) if q.PadName == pad), None)


def assign(housing, intent, replace=True):
    """Lay the wires the pin list asks for on *housing*'s dies. With
    *replace*, wires already on these dies are removed first."""
    doc = housing.Document
    chips = housings.chips_on(housing) or model.by_role(doc, model.CHIP)
    dies = match_dies(intent, chips)
    apply(doc, intent, chips)
    if replace:
        mine = {c.Name for c in chips}
        for w in list(wires.wires(doc)):
            c = wires.chip_of(w.ChipPad) if w.ChipPad is not None else None
            if c is not None and c.Name in mine:
                doc.removeObject(w.Name)
    leads = {p.PadName: p for p in model.pads_of(housing)}
    made, fixed, done = [], [], set()
    # 1 fixed leads: anchors for the crossing-free pairing (step 3)
    for p in intent.pins:
        if p.kind == "nc" or not p.lead:
            continue
        pad = _chip_pad(dies, p.die, p.pad)
        if pad is None or p.lead not in leads:
            continue
        fixed.append((pad, leads[p.lead]))
        done.add(p.key)
    # 2 die to die: each pad of the net to the nearest pad of the net on another die, as a chain
    for net in intent.die_to_die_nets():
        ps = [p for p in intent.nets()[net] if _chip_pad(dies, p.die, p.pad) is not None]
        for a, b in zip(ps, ps[1:]):
            pa, pb = _chip_pad(dies, a.die, a.pad), _chip_pad(dies, b.die, b.pad)
            if pinlist.norm(a.die) != pinlist.norm(b.die):
                made.append(wires.create(doc, pa, pb))
            done.update({a.key, b.key})
    # 3 the rest, crossing-free round the fixed ones
    rest = [p for p in intent.pins if p.kind != "nc" and p.key not in done]
    pads = [q for q in (_chip_pad(dies, p.die, p.pad) for p in rest) if q is not None]
    if len(pads) + len(fixed) > len(leads):
        raise ValueError(f"{len(pads) + len(fixed)} pads to bond, {len(leads)} leads")
    for a, b in wires.auto_pair_mcm(housing, pads, list(leads.values()), fixed):
        made.append(wires.create(doc, a, b))
    doc.recompute()
    return made


def _neighbours(housing):
    """{lead: [leads beside it on the same side]}: within 1.6 pitches."""
    leads = model.pads_of(housing)
    pts = {p.PadName: model.pad_centre(p) for p in leads}
    d = sorted(((a - b).Length for i, a in enumerate(pts.values()) for b in list(pts.values())[i + 1:]))
    pitch = d[0] if d else 0.0
    return {n: [m for m, q in pts.items() if m != n and (q - p).Length < 1.6 * pitch] for n, p in pts.items()}


def lvs(housing, intent):
    """(findings, {lead: net}) for the wires on *housing*'s dies."""
    doc = housing.Document
    chips = housings.chips_on(housing) or model.by_role(doc, model.CHIP)
    dies = match_dies(intent, chips)
    die_of = {c.Name: d for d, c in dies.items()}
    lead_names = {p.Name: p.PadName for p in model.pads_of(housing)}
    out = []
    for w in wires.wires(doc):
        if w.ChipPad is None or w.PackagePad is None:
            continue
        c = wires.chip_of(w.ChipPad)
        if c is None or c.Name not in {x.Name for x in chips}:
            continue
        die = die_of.get(c.Name, c.Label)
        if getattr(w.PackagePad, "PadSide", "") == "chip":
            c2 = wires.chip_of(w.PackagePad)
            out.append(package_lvs.Wire(w.Label, die, w.ChipPad.PadName, die_of.get(c2.Name, c2.Label),
                                        w.PackagePad.PadName))
        elif w.PackagePad.Name in lead_names:
            out.append(package_lvs.Wire(w.Label, die, w.ChipPad.PadName, "", lead_names[w.PackagePad.Name]))
    found, nets = package_lvs.check(intent, out, list(lead_names.values()), _neighbours(housing))
    for p in model.pads_of(housing):
        _prop(p, "App::PropertyString", "LeadNet", nets.get(p.PadName, ""), "The net on this lead (package LVS)")
    for d, label in guessed(intent, chips):
        found.insert(0, package_lvs.Finding("info", "die by elimination",
                                            f"Die '{d}' of the pin list taken as chip '{label}', the only one left "
                                            "(names differ: rename the chip to the die's name to be sure)."))
    unmatched = [d for d in intent.dies() if d not in dies]
    for d in unmatched:
        found.insert(0, package_lvs.Finding("error", "no die", f"No chip in the package answers to die '{d}' of "
                                                               "the pin list (label, top cell or GDS name)."))
    return found, nets


def load(paths, dies=None):
    """An Intent from files: *.csv, *.sch (xschem), *.lef, *.v. *dies*:
    {path: die name} for xschem, LEF and Verilog files (default: the
    file's stem)."""
    intent = pinlist.Intent()
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        die = (dies or {}).get(path) or os.path.splitext(os.path.basename(path))[0]
        if ext == ".csv":
            intent.merge(pinlist.read_csv(path, path))
        elif ext == ".sch":
            intent.merge(pinlist.read_xschem(path, die, path))
        elif ext == ".lef":
            intent.merge(pinlist.read_lef(path, die, path))
        elif ext in (".v", ".sv"):
            intent.merge(pinlist.read_verilog(path, die, source=path))
        else:
            raise ValueError(f"{path}: a pin list is .csv, .sch (xschem), .lef or .v (Verilog)")
    return intent

