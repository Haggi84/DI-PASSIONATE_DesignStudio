# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Is the assembly ready to simulate? One list, worst first, each line saying
what is wrong and which command fixes it.

OTTER's own checks run on the built model and speak in its terms (groups,
cells, boundaries). These run on the assembly before anything is built, in
the terms of this workbench — a chip, a wire, a material, a port — so a
missing port or a lead without a material is found in a second, not after
a model build and a mesh estimate. After a build, check_model() adds what
only the built model can tell: a port that touches no conductor feeds
nothing, and the solver would report S-parameters of an open circuit.

Levels: "error" stops a simulation from making sense; "warning" is a guess
the result depends on; "info" is worth knowing.
"""

from dataclasses import dataclass, field

from . import em_materials, em_model, materials as mats, model, ports as pt, wires


@dataclass
class Finding:
    level: str          # "error" | "warning" | "info"
    message: str
    fix: str = ""       # the command that fixes it
    objects: list = field(default_factory=list)         # assembly object Names it is about
    model_objects: list = field(default_factory=list)   # labels in the simulation model

    def __str__(self):
        return f"{self.level.upper()}: {self.message}" + (f" — {self.fix}" if self.fix else "")


_ORDER = {"error": 0, "warning": 1, "info": 2}


def check(doc, regions=None):
    """[Finding] for the assembly *doc*, worst first."""
    out = []
    def add(level, msg, fix="", objects=()):
        out.append(Finding(level, msg, fix, [o.Name for o in objects]))
    chips = model.by_role(doc, model.CHIP)
    physical = em_model.physical_objects(doc)
    if not chips and not physical:
        add("error", "Nothing to simulate: no chip, housing or board.", "Import GDS chip / Import STEP part")
        return out

    # ports
    plist = pt.ports(doc)
    if not plist:
        add("error", "No port: a simulation needs at least one, where the signal goes in.", "Ports")
    elif not any(bool(getattr(p, "Excited", True)) for p in plist):
        add("error", "No port is excited.", "Ports: tick Excited on one (property view)")
    numbers = [p.PortIndex for p in plist]
    for n in sorted({n for n in numbers if numbers.count(n) > 1}):
        add("error", f"Two ports have the number {n}.", "Ports: renumber one (PortIndex)",
            [p for p in plist if p.PortIndex == n])

    # materials: one per element, with the values a solver needs
    problems = em_materials.register(doc)
    lib = mats.available(doc)
    unset = []
    for obj in physical:
        name = mats.material_of(obj)
        if not name:
            unset.append(obj)
        elif name.lower() == em_materials.NOT_SIMULATED_NAME:
            continue
        elif name not in lib:
            add("error", f"{obj.Label}: material '{name}' is not in the library.", "Materials", [obj])
        elif name in problems:
            add("error", f"{obj.Label}: {problems[name]}.", "Materials: give it ε_r or σ", [obj])
    if unset:
        shown = ", ".join(o.Label for o in unset[:5]) + (f" and {len(unset) - 5} more" if len(unset) > 5 else "")
        add("warning", f"{len(unset)} part(s) have no material, OTTER will guess from name and colour: {shown}.",
            "Materials", unset)
    for chip in chips:
        for layer in chip.Group:
            if model.role(layer) == model.LAYER:
                name = mats.material_of(layer)
                if name and name in problems:
                    add("error", f"{chip.Label} {layer.Label}: {problems[name]}.", "Materials", [chip])

    # chips: placed, not overlapping, a region that can be built
    regions = em_model.regions_for(doc, regions)
    for chip in chips:
        if chip.getParentGeoFeatureGroup() is None and (model.by_role(doc, model.HOUSING)
                                                        or model.by_role(doc, model.BOARD)):
            add("warning", f"{chip.Label} is not placed: it is in no housing and on no board.", "Place", [chip])
        region = regions.get(chip.Name)
        if region is None:
            continue
        n = em_model.region_polygons(chip, region)
        if not region.levels:
            add("warning", f"{chip.Label}: no metal level is chosen; the chip is only its silicon and oxide.",
                "EM simulation: chip metal", [chip])
        elif n > em_model.MAX_POLYGONS:
            add("error", f"{chip.Label}: the EM region holds {n:,} polygons (at most "
                         f"{em_model.MAX_POLYGONS:,}).", "EM simulation: a smaller region or fewer levels", [chip])
        else:
            add("info", f"{chip.Label}: {n:,} polygons of {', '.join(region.levels)} are built exactly.")
    from ..core import moving as mv
    for i, j, wx, wy in mv.overlaps([model.global_bbox(c) for c in chips]):
        add("error", f"{chips[i].Label} and {chips[j].Label} overlap by {wx * 1000:.0f} × {wy * 1000:.0f} µm.",
            "Move", [chips[i], chips[j]])

    # wires
    ws = wires.wires(doc)
    broken = [w for w in ws if w.ChipPad is None or w.PackagePad is None]
    if broken:
        add("error", f"{len(broken)} wire(s) lost a pad: {', '.join(w.Label for w in broken[:5])}.",
            "Bond wires: build them again", broken)
    if ws and not broken:
        try:
            found = wires.check(doc)
            rejects = sum(1 for fs in found.values() for f in fs if f.severity == "reject" and not f.ok)
            if rejects:
                add("warning", f"{rejects} MIL-STD-883 reject(s) on the wires.", "Bond wires: Check")
        except Exception:
            pass
    elif chips and not ws:
        add("info", "No bond wires: chips are connected to nothing.", "Bond wires")
    return sorted(out, key=lambda f: _ORDER[f.level])


def check_model(sim):
    """[Finding] only the built model can tell."""
    out = []
    for port, touching in em_model.port_contacts(sim).items():
        source = [sim.part(port).source] if sim.part(port) is not None and sim.part(port).source else []
        if not touching:
            out.append(Finding("error", f"{port} touches no conductor: it would feed nothing.",
                               "Ports: put it between two conductors (a trace and its ground, a lead and the "
                               "board), or take more chip metal levels", source, [port]))
        elif len(touching) == 1:
            out.append(Finding("warning", f"{port} touches only {touching[0]}: a lumped port spans the gap "
                                          "between two conductors, the signal and its ground.",
                               "Ports: “To the next edge” ends it on the ground below or above", source,
                               [port] + touching))
    if not any(p.kind in ("part", "metal") and p.material and em_materials.is_conductor(p.material)
               for p in sim.parts):
        out.append(Finding("error", "The model has no conductor at all.", "Materials / EM simulation: chip metal"))
    return out


def ready(findings):
    return not any(f.level == "error" for f in findings)
