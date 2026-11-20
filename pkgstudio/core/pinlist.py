# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
What the chip designer intends: which pad of which die is which net, what
kind of net, and — where it is fixed — which package lead it must go to.

Geometry alone cannot say that RFIN must go to pin 3, that the eight GND
pads are one net, or that two pads of two dies belong together. The pin list
can, and the package is then checked against it (core.package_lvs).

Read from:

  CSV       die,pad,net[,kind][,lead]   — the full intent; '#' comments
  xschem    the top cell's ipin/opin/iopin symbols (*.sch): one pin per
            label, pad = net = the label
  LEF       the PIN blocks of a macro (from the digital flow, e.g. ORFS):
            pad = net = the pin name, kind from USE POWER/GROUND/SIGNAL
  Verilog   the ports of the top module (the netlist the digital flow
            starts from, e.g. SpinalHDL/ORFS's top with the pad cells):
            pad = net = the port name; buses [n:0] become name_0 … name_n

Several sources merge: later ones override earlier ones pin by pin, so a
CSV can fix the leads and kinds of pins an xschem or LEF file named.

Kinds: signal, rf, power, ground, nc (not connected — not bonded).
FreeCAD-free.
"""

import csv
import io
import re
from dataclasses import dataclass, field

KINDS = ("signal", "rf", "power", "ground", "nc")
_GROUND = re.compile(r"^(gnd|vss|agnd|dgnd|vssa|vssd|vssio|iovss|sub!?)(\w*)$", re.I)
_POWER = re.compile(r"^(vdd|vcc|vddio|iovdd|avdd|dvdd|vdda|vddd|vbat|v\d+v\d*)(\w*)$", re.I)
_RF = re.compile(r"^(rf|lo|if)(in|out|_in|_out|p|n)?\w*$", re.I)


def guess_kind(name):
    """A net's kind from its name: GND*/VSS* ground, VDD*/VCC* power, RF*
    rf, else signal."""
    n = name.strip()
    if _GROUND.match(n):
        return "ground"
    if _POWER.match(n):
        return "power"
    if _RF.match(n):
        return "rf"
    return "signal"


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class Pin:
    die: str
    pad: str
    net: str
    kind: str = "signal"
    lead: str = ""              # a fixed package lead ("" = the tool chooses)

    @property
    def key(self):
        return (norm(self.die), self.pad)


@dataclass
class Intent:
    pins: list = field(default_factory=list)
    sources: list = field(default_factory=list)

    def nets(self):
        out = {}
        for p in self.pins:
            out.setdefault(p.net, []).append(p)
        return out

    def pin(self, die, pad):
        k = (norm(die), pad)
        return next((p for p in self.pins if p.key == k), None)

    def dies(self):
        return sorted({p.die for p in self.pins})

    def die_to_die_nets(self):
        """Nets meant to join pads of two dies inside the package: on more
        than one die, not a supply, and on no fixed lead."""
        out = []
        for net, pins in self.nets().items():
            if len({norm(p.die) for p in pins}) > 1 and all(p.kind in ("signal", "rf") for p in pins) \
                    and not any(p.lead for p in pins):
                out.append(net)
        return out

    def merge(self, other):
        """Pins of *other* override pins of self with the same die and pad;
        new ones are added. Returns self."""
        index = {p.key: i for i, p in enumerate(self.pins)}
        for p in other.pins:
            if p.key in index:
                old = self.pins[index[p.key]]
                self.pins[index[p.key]] = Pin(p.die, p.pad, p.net or old.net, p.kind or old.kind,
                                              p.lead or old.lead)
            else:
                index[p.key] = len(self.pins)
                self.pins.append(p)
        self.sources += other.sources
        return self


def _text(path_or_text):
    import os
    if "\n" in path_or_text or not path_or_text.strip() or not os.path.isfile(path_or_text):
        return path_or_text
    with open(path_or_text, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_csv(path_or_text, source="csv"):
    """die,pad,net[,kind][,lead]; a header row is recognised by 'die' in its first cell."""
    rows = [r for r in csv.reader(io.StringIO(_text(path_or_text)))
            if r and r[0].strip() and not r[0].strip().startswith("#")]
    if rows and rows[0][0].strip().lower() == "die":
        head = [h.strip().lower() for h in rows[0]]
        rows = rows[1:]
    else:
        head = ["die", "pad", "net", "kind", "lead"]
    out = Intent(sources=[source])
    for r in rows:
        d = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(head)}
        if not d.get("pad"):
            continue
        net = d.get("net") or d["pad"]
        kind = (d.get("kind") or guess_kind(net)).lower()
        if kind not in KINDS:
            raise ValueError(f"{source}: '{kind}' is not a kind ({', '.join(KINDS)})")
        out.pins.append(Pin(d.get("die", ""), d["pad"], net, kind, d.get("lead", "")))
    return out


_XSCHEM_PIN = re.compile(r"^C\s+\{(?:[^}]*/)?(ipin|opin|iopin)\.sym\}[^{]*\{([^}]*)\}", re.M)
_ATTR = re.compile(r"(\w+)\s*=\s*(\"[^\"]*\"|\S+)")


def read_xschem(path_or_text, die, source="xschem"):
    """The top cell's pins of an xschem schematic: one Pin per lab=."""
    out = Intent(sources=[source])
    for kind_sym, attrs in _XSCHEM_PIN.findall(_text(path_or_text)):
        a = {k: v.strip('"') for k, v in _ATTR.findall(attrs)}
        lab = a.get("lab")
        if lab:
            out.pins.append(Pin(die, lab, lab, guess_kind(lab)))
    return out


def read_lef(path_or_text, die, source="lef"):
    """The PIN blocks of a LEF macro: USE POWER/GROUND/SIGNAL -> kind."""
    out = Intent(sources=[source])
    text = _text(path_or_text)
    for m in re.finditer(r"^\s*PIN\s+(\S+)(.*?)^\s*END\s+\1\b", text, re.M | re.S):
        name, body = m.group(1), m.group(2)
        use = re.search(r"\bUSE\s+(\w+)", body)
        use = use.group(1).upper() if use else ""
        kind = {"POWER": "power", "GROUND": "ground"}.get(use, guess_kind(name) if use != "SIGNAL" else "signal")
        if kind == "signal" and _RF.match(name):
            kind = "rf"
        out.pins.append(Pin(die, name, name, kind))
    return out


_VPORT = re.compile(r"\b(input|output|inout)\b\s*(?:wire|reg|logic|tri)?\s*(?:signed\s*)?(\[\s*(\d+)\s*:\s*(\d+)\s*\])?"
                    r"\s*([A-Za-z_][\w$]*(?:\s*,\s*(?!input|output|inout)[A-Za-z_][\w$]*)*)")


def read_verilog(path_or_text, die, module=None, source="verilog"):
    """The ports of a Verilog module (the first, or *module*): one Pin each."""
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", _text(path_or_text), flags=re.S)
    mods = re.findall(r"\bmodule\s+(\w+)\s*(#\s*\(.*?\)\s*)?\((.*?)\)\s*;(.*?)\bendmodule\b", text, re.S)
    if not mods:
        raise ValueError(f"{source}: no module found")
    name, _params, header, body = next((m for m in mods if module in (None, m[0])), mods[0])
    out = Intent(sources=[source])
    seen = set()
    for direction, rng, hi, lo, names in _VPORT.findall(header + ";" + body):
        for n in (x.strip() for x in names.split(",")):
            if not n or n in seen:
                continue
            seen.add(n)
            bits = [f"{n}_{i}" for i in range(min(int(lo), int(hi)), max(int(lo), int(hi)) + 1)] if rng else [n]
            for b in bits:
                out.pins.append(Pin(die, b, b, guess_kind(b)))
    return out


def write_csv(intent, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["die", "pad", "net", "kind", "lead"])
        for p in intent.pins:
            w.writerow([p.die, p.pad, p.net, p.kind, p.lead])
    return path
