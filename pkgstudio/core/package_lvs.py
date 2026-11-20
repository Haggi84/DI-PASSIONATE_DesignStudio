# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Layout versus schematic for a package: do the bond wires make the
connections the pin list (core.pinlist) intends — no more, no fewer?

From the wires it derives the package netlist (every lead's net) and checks:

  open          an intended pad without a wire; a die-to-die net whose
                pads are not joined
  unknown pad   a wire on a pad the pin list does not name
  short         a lead whose wires carry two nets; a die-to-die wire
                between two nets
  wrong lead    a pad on another lead than the one the pin list fixes
  no lead       a supply net with no lead out of the package
  nc bonded     a pad marked nc with a wire
  rf return     an RF lead with a neighbour that is not ground (GSG); a
                warning — a ground-signal-ground pin-out is what keeps the
                package transition at 50 ohm
  unused        leads without a wire (info)

Plain data in, FreeCAD-free: the app layer (app.netlist) collects it from
the document.
"""

from dataclasses import dataclass, field

from .pinlist import norm


@dataclass
class Wire:
    name: str
    die: str
    pad: str
    to_die: str = ""            # "" : to a lead
    to: str = ""                # the lead's name, or the other die's pad


@dataclass
class Finding:
    level: str                  # "error" | "warning" | "info"
    rule: str
    message: str
    net: str = ""
    items: list = field(default_factory=list)

    def __str__(self):
        return f"{self.level}: [{self.rule}] {self.message}"


def check(intent, wires, leads=(), neighbours=None):
    """
    (findings, lead_nets). *wires*: [Wire]; *leads*: every lead's name;
    *neighbours*: {lead: [leads beside it]} for the GSG check.
    lead_nets: {lead: net} for leads that carry exactly one net.
    """
    out = []
    pins = {p.key: p for p in intent.pins}
    wired = {}                                   # pin key -> [Wire]
    lead_nets = {}                               # lead -> {net}
    for w in wires:
        ends = [(w.die, w.pad)] + ([(w.to_die, w.to)] if w.to_die else [])
        nets = []
        for die, pad in ends:
            p = pins.get((norm(die), pad))
            if p is None:
                out.append(Finding("error", "unknown pad", f"{w.name}: {die} pad {pad} is not in the pin list.",
                                   items=[w.name]))
                continue
            wired.setdefault(p.key, []).append(w)
            nets.append(p.net)
            if p.kind == "nc":
                out.append(Finding("error", "nc bonded", f"{w.name}: {die} {pad} is marked not connected.", p.net,
                                   [w.name]))
        if w.to_die:
            if len(set(nets)) > 1:
                out.append(Finding("error", "short", f"{w.name} joins {nets[0]} ({w.die} {w.pad}) and {nets[1]} "
                                                     f"({w.to_die} {w.to}).", nets[0], [w.name]))
        elif nets:
            lead_nets.setdefault(w.to, set()).add(nets[0])
            p = pins[(norm(w.die), w.pad)]
            if p.lead and p.lead != w.to:
                out.append(Finding("error", "wrong lead", f"{w.die} {w.pad} ({p.net}) is on lead {w.to}; the pin "
                                                          f"list fixes it to lead {p.lead}.", p.net, [w.name]))

    for lead, nets in sorted(lead_nets.items(), key=lambda kv: _num(kv[0])):
        if len(nets) > 1:
            out.append(Finding("error", "short", f"Lead {lead} carries {', '.join(sorted(nets))}.",
                               sorted(nets)[0], [lead]))

    d2d = set(intent.die_to_die_nets())
    for p in intent.pins:
        if p.kind == "nc":
            continue
        if p.key not in wired:
            out.append(Finding("error", "open", f"{p.die} {p.pad} ({p.net}) has no wire.", p.net))
    for net in d2d:
        joined = [w for w in wires if w.to_die and pins.get((norm(w.die), w.pad)) is not None
                  and pins[(norm(w.die), w.pad)].net == net]
        if not joined:
            out.append(Finding("error", "open", f"Net {net} is meant to join two dies and no wire does.", net))

    for net, ps in intent.nets().items():
        if ps[0].kind in ("power", "ground") and not any(net in n for n in lead_nets.values()):
            out.append(Finding("error", "no lead", f"Supply net {net} has no lead out of the package.", net))

    one = {lead: next(iter(n)) for lead, n in lead_nets.items() if len(n) == 1}
    kind_of = {p.net: p.kind for p in intent.pins}
    if neighbours:
        for lead, net in sorted(one.items(), key=lambda kv: _num(kv[0])):
            if kind_of.get(net) != "rf":
                continue
            bad = [n for n in neighbours.get(lead, []) if kind_of.get(one.get(n, ""), "") != "ground"]
            if bad:
                out.append(Finding("warning", "rf return", f"RF lead {lead} ({net}) has neighbour(s) "
                                   f"{', '.join(bad)} that are not ground: no ground-signal-ground pin-out.", net,
                                   [lead] + bad))
    unused = [lead for lead in leads if lead not in lead_nets]
    if unused:
        out.append(Finding("info", "unused", f"{len(unused)} leads without a wire: "
                                             + ", ".join(sorted(unused, key=_num))))
    return out, one


def errors(findings):
    return [f for f in findings if f.level == "error"]


def _num(name):
    try:
        return (0, int(name))
    except (TypeError, ValueError):
        return (1, str(name))
