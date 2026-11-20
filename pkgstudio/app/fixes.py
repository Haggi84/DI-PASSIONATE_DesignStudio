# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
What to do about a finding: an explanation in the terms of the assembly,
what to look at in the 3-D view, and — where it can be worked out — the fix
itself, to be previewed and applied with one click.

A finding says what is wrong ("Port_1: no conductor at its bottom end"),
not where to look nor what would be right. Both are in the model: the port
is a face in the simulation document, the conductors are there with their
materials. So a suggestion carries

  * shapes to draw (global coordinates, the simulation model's): the parts
    the finding is about ("problem"), what a fix would connect
    ("conductor"), and the fix itself ("proposed");
  * actions: callables that change the assembly or the EM settings, each
    saying whether the model must be built again afterwards.

The worked-out fix is the lumped port's span. A lumped port must run from
one conductor to another along its excitation: OTTER checks that its two
end planes touch a conductor's box, as here. So in the port's footprint
the nearest conductor ending below its centre and the nearest one starting
above it are the two it should join; the fix is the port stretched or
shrunk exactly between them, applied to the port in the assembly by its own
frame (its height or width, and its centre).

Qt-free, so it is testable headlessly; the preview lives in gui/fix_preview.
"""

from dataclasses import dataclass, field

import FreeCAD
import Part

from ..core import otter_path
from . import em_materials, model, ports as pt

otter_path.ensure()

from otter_llm.starter import floor125, groups, wire_diameter  # noqa: E402

V = FreeCAD.Vector
TOL = 1e-6          # mm: touching, as OTTER's checks count it
AXES = "xyz"


@dataclass
class Action:
    text: str
    apply: object               # () -> str, what was done
    rebuild: bool = False       # the model must be built again afterwards


@dataclass
class Suggestion:
    title: str
    text: str
    shapes: list = field(default_factory=list)      # (name, shape, style) style: problem | conductor | proposed
    select: list = field(default_factory=list)      # assembly object Names to select
    actions: list = field(default_factory=list)
    command: str = ""                               # the command that fixes it by hand


# ── the model's conductors ─────────────────────────────────────────────────

def conductor_labels(project):
    """Labels of the bodies the simulation treats as conductors: those in
    groups whose chosen material is a conductor (also guessed ones)."""
    out = set()
    for group in groups(project.model):
        material = project.design.materials.get(group.name)
        if material and em_materials.is_conductor(material):
            out.update(group.bodies)
    return out


def _box(project, label):
    body = project.model.bodies.get(label)
    return body["bbox"] if body else None


def _shape(project, label):
    obj = project.sim.doc.getObject(label) or next(iter(project.sim.doc.getObjectsByLabel(label)), None)
    return obj.Shape.copy() if obj is not None else None


def _overlaps(a, b, axes, tol=TOL):
    return all(b[k] <= a[k + 3] + tol and b[k + 3] >= a[k] - tol for k in axes)


def _gap(box, point, axis):
    """Distance from *point* (one coordinate per axis) to *box*."""
    d = 0.0
    for k in range(3):
        lo, hi = box[k], box[k + 3]
        d += max(lo - point[k], 0.0, point[k] - hi) ** 2
    return d ** 0.5


@dataclass
class Span:
    label: str
    axis: int
    lo: float = None            # the new bottom, or None: nothing below in the footprint
    hi: float = None
    below: str = ""             # conductor it would start on
    above: str = ""
    nearest_below: str = ""     # with nothing in the footprint: the nearest anywhere
    nearest_above: str = ""

    @property
    def complete(self):
        return self.lo is not None and self.hi is not None and self.hi - self.lo > TOL


def port_span(project, label, axis=2):
    """Where the lumped port *label* (in the simulation model) should start
    and end along *axis*: from the top of the nearest conductor below its
    centre to the bottom of the nearest above, in its footprint."""
    box = _box(project, label)
    span = Span(label, axis)
    if box is None:
        return span
    others = [k for k in range(3) if k != axis]
    centre = (box[axis] + box[axis + 3]) / 2
    conductors = [(lbl, _box(project, lbl)) for lbl in conductor_labels(project) if lbl != label]
    conductors = [(lbl, b) for lbl, b in conductors if b is not None]
    inside = [(lbl, b) for lbl, b in conductors if _overlaps(box, b, others)]
    below = [(lbl, b) for lbl, b in inside if b[axis + 3] <= centre + TOL]
    above = [(lbl, b) for lbl, b in inside if b[axis] >= centre - TOL]
    if below:
        lbl, b = max(below, key=lambda e: e[1][axis + 3])
        span.lo, span.below = b[axis + 3], lbl
    if above:
        lbl, b = min(above, key=lambda e: e[1][axis])
        span.hi, span.above = b[axis], lbl
    mid = [(box[k] + box[k + 3]) / 2 for k in range(3)]
    for end, attr, side in ((box[axis], "nearest_below", -1), (box[axis + 3], "nearest_above", 1)):
        if getattr(span, attr.replace("nearest_", "")):
            continue
        point = list(mid)
        point[axis] = end
        candidates = [(lbl, b) for lbl, b in conductors
                      if (b[axis + 3] <= end + TOL if side < 0 else b[axis] >= end - TOL)]
        if candidates:
            setattr(span, attr, min(candidates, key=lambda e: _gap(e[1], point, axis))[0])
    return span


def proposed_face(face, axis, old_lo, old_hi, lo, hi):
    """*face* (a polygon) with its extent along *axis* moved from
    old_lo..old_hi to lo..hi."""
    pts = []
    for v in face.OuterWire.OrderedVertexes:
        p = [v.Point.x, v.Point.y, v.Point.z]
        p[axis] = lo if abs(p[axis] - old_lo) <= abs(p[axis] - old_hi) else hi
        pts.append(V(*p))
    return Part.Face(Part.makePolygon(pts + [pts[0]]))


def apply_port_span(port, axis, lo, hi):
    """Resize and move the assembly's *port* so it runs from *lo* to *hi*
    (global, mm) along *axis*, through its own frame: its height when its Y
    lies along the axis, its width when its X does."""
    g = model.global_placement(port)
    u = g.Rotation.multVec(V(1, 0, 0))
    v = g.Rotation.multVec(V(0, 1, 0))
    if abs(abs(v[axis]) - 1) < 1e-6:
        along, sign = "v", v[axis]
    elif abs(abs(u[axis]) - 1) < 1e-6:
        along, sign = "u", u[axis]
    else:
        raise ValueError(f"{port.Label} does not stand along {AXES[axis]}: redraw it upright")
    d = ((lo + hi) / 2 - g.Base[axis]) / sign
    frame = model.stored_frame(port)
    x, y = port.PortX.Value, port.PortY.Value
    if along == "v":
        pt.move_port(port, (x, y + d), frame)
        pt.resize_port(port, port.PortWidth.Value, hi - lo)
    else:
        pt.move_port(port, (x + d, y), frame)
        pt.resize_port(port, hi - lo, port.PortHeight.Value)
    port.Document.recompute()


# ── suggestions ────────────────────────────────────────────────────────────

def _source(project, label):
    part = project.sim.part(label)
    if part is None or not part.source:
        return None
    return project.source().getObject(part.source)


def _um(mm):
    return f"{mm * 1000:.4g} µm"


def for_port(project, label):
    """The port *label* touches nothing at one or both ends: its span."""
    port = _source(project, label)
    box = _box(project, label)
    face = _shape(project, label)
    s = Suggestion(f"{label}: make it touch both conductors", "", command="PS_Ports")
    if box is None or face is None:
        s.text = f"{label} is not in the model any more; build the model again."
        return s
    s.shapes.append((label, face, "problem"))
    if port is not None:
        s.select.append(port.Name)
    span = port_span(project, label)
    for lbl in (span.below, span.above, span.nearest_below, span.nearest_above):
        shape = _shape(project, lbl) if lbl else None
        if shape is not None and not any(n == lbl for n, _s, _st in s.shapes):
            s.shapes.append((lbl, shape, "conductor"))
    lo, hi = box[2], box[5]
    if span.complete:
        new = proposed_face(face, 2, lo, hi, span.lo, span.hi)
        s.shapes.append((f"{label} (fixed)", new, "proposed"))
        s.text = (f"{label} runs from z {_um(lo)} to {_um(hi)} and touches no conductor at "
                  f"{'either end' if (lo != span.lo and hi != span.hi) else 'one end'}. In its footprint, "
                  f"{span.below} ends below it at z {_um(span.lo)} and {span.above} starts above it at "
                  f"z {_um(span.hi)}. The green port runs exactly between the two (yellow): "
                  f"{_um(span.hi - span.lo)} high.")
        if port is not None:
            s.actions.append(Action(
                f"Apply: {label} from {_um(span.lo)} to {_um(span.hi)}",
                lambda: (apply_port_span(port, 2, span.lo, span.hi),
                         f"{port.Label} now runs from z {_um(span.lo)} to {_um(span.hi)}.")[1], rebuild=True))
        return s
    missing = []
    if span.lo is None:
        missing.append("below" + (f" (the nearest conductor below is {span.nearest_below}, outside its footprint)"
                                  if span.nearest_below else ""))
    if span.hi is None:
        missing.append("above" + (f" (the nearest conductor above is {span.nearest_above}, outside its footprint)"
                                  if span.nearest_above else ""))
    s.text = (f"{label} runs from z {_um(lo)} to {_um(hi)}. In its footprint there is no conductor "
              + " and none ".join(missing) + ". A lumped port joins two conductors — a trace and its ground, "
              "a lead and the board. Move it where two conductors face each other (yellow: the nearest ones), "
              "or draw it again with Ports › Draw on an edge › To the next edge.")
    if span.lo is not None or span.hi is not None:
        end = span.lo if span.lo is not None else span.hi
        new_lo, new_hi = (end, hi) if span.lo is not None else (lo, end)
        if new_hi - new_lo > TOL and port is not None:
            s.shapes.append((f"{label} (one end fixed)", proposed_face(face, 2, lo, hi, new_lo, new_hi), "proposed"))
            s.actions.append(Action(f"Apply the end that can be fixed: z {_um(new_lo)} to {_um(new_hi)}",
                                    lambda: (apply_port_span(port, 2, new_lo, new_hi),
                                             f"{port.Label}: one end now on a conductor.")[1], rebuild=True))
    return s


def _group_of(project, label):
    return next((g for g in groups(project.model) if label in g.bodies), None)


def for_issue(project, issue):
    """A Suggestion for one of OTTER's checks (otter_llm.checks.Issue)."""
    labels = [o for o in (issue.objects or []) if o in project.model.bodies]
    if issue.code == "port-not-touching-metal" and labels:
        return for_port(project, labels[-1])
    s = Suggestion(issue.code.replace("-", " ").capitalize(), issue.message)
    for lbl in labels:
        shape = _shape(project, lbl)
        if shape is not None:
            s.shapes.append((lbl, shape, "problem"))
            src = _source(project, lbl)
            if src is not None:
                s.select.append(src.Name)
    if issue.code == "wire-cells" and labels:
        cells = {}
        for lbl in labels:
            g, body = _group_of(project, lbl), project.model.bodies.get(lbl)
            d = wire_diameter(body) if body else 0.0
            if g is not None and d:
                cell = floor125(1000 * d / 1.5)
                cells[g.name] = min(cells.get(g.name, cell), cell)
        if cells:
            s.text += (" Red: those parts. The fix gives each its own cells of at most its diameter / 1.5 — fine "
                       "cells only there, not in the whole model.")

            def fine():
                for name, cell in cells.items():
                    project.set_cell(name, xy_um=cell, z_um=cell)
                return "Cells set: " + ", ".join(f"{n} {c:g} µm" for n, c in cells.items())
            s.actions.append(Action("Apply: " + ", ".join(f"{n} cells {c:g} µm" for n, c in list(cells.items())[:3]),
                                    fine))
    elif issue.code == "mesh-too-large":
        biggest = max((lbl for lbl in project.model.bodies if lbl != "AirBox"),
                      key=lambda lbl: _extent(project.model.bodies[lbl]["bbox"]), default=None)
        if biggest is not None:
            b = project.model.bodies[biggest]["bbox"]
            shape = _shape(project, biggest)
            if shape is not None:
                s.shapes.append((biggest, shape, "problem"))
            s.text += (f" Red: {biggest}, {b[3] - b[0]:.3g} × {b[4] - b[1]:.3g} mm, sets the model's extent; every "
                       "millimetre of it is meshed at the cells the smallest parts need. A coarser mesh or a larger "
                       "smallest cell cuts the cell count most; a board cut down to the part near the package "
                       "(Export geometry › Only a section) cuts it more.")
        if project.design.mesh != "coarse":
            s.actions.append(Action("Apply: mesh density coarse",
                                    lambda: (project.set_mesh("coarse"), "Mesh density: coarse.")[1]))
        if project.design.min_cell_um < 2:
            s.actions.append(Action("Apply: smallest cell 2 µm",
                                    lambda: (project.set_mesh(min_cell_um=2.0), "Smallest cell: 2 µm.")[1]))
    elif issue.code == "port-z-cells":
        import re
        m = re.search(r"at most ([0-9.]+) µm", issue.message)
        if m:
            cell = max(0.5, float(m.group(1)))
            cell = floor_nice(cell)
            s.actions.append(Action(f"Apply: smallest cell {cell:g} µm",
                                    lambda: (project.set_mesh(min_cell_um=cell), f"Smallest cell: {cell:g} µm.")[1]))
    elif issue.code == "tiny-cell":
        s.actions.append(Action("Apply: smallest cell 1 µm",
                                lambda: (project.set_mesh(min_cell_um=max(project.design.min_cell_um, 1.0)),
                                         "Smallest cell: 1 µm.")[1]))
    elif issue.code in ("no-excited-port", "several-excited-ports"):
        plist = project.settings.ports()
        if plist:
            name = plist[0].name
            s.actions.append(Action(f"Apply: excite {name} only",
                                    lambda: (project.settings.set_active_port(name), f"{name} excited.")[1]))
    elif issue.code == "boundary-too-close":
        from . import em_model
        s.actions.append(Action("Apply: air box a quarter wavelength away",
                                lambda: (em_model.fit_airbox(project.sim, project.design.fmax_ghz)
                                         and project._reload_model(), "Air box grown.")[1]))
    return s


def floor_nice(um_value):
    """The largest of 1, 2, 5 x 10^n not above *um_value*."""
    import math
    e = 10 ** math.floor(math.log10(um_value))
    return max(m * e for m in (1, 2, 5) if m * e <= um_value + 1e-12)


def _extent(b):
    return max(b[3] - b[0], b[4] - b[1])


_COMMANDS = {"Ports": "PS_Ports", "Materials": "PS_Materials", "Move": "PS_Move", "Place": "PS_Place",
             "Bond wires": "PS_BondWires", "Import GDS chip": "PS_ImportGds", "Import STEP part": "PS_ImportStep"}


def command_for(fix):
    """The command a Finding's fix names ("Ports: …" -> PS_Ports)."""
    for text, name in _COMMANDS.items():
        if fix.startswith(text):
            return name
    return ""


def for_finding(doc, finding, project=None):
    """A Suggestion for one of the readiness findings."""
    if project is not None and finding.model_objects:
        port = next((lbl for lbl in finding.model_objects if lbl in project.model.bodies), None)
        if port is not None and "conductor" in finding.message:
            return for_port(project, port)
    s = Suggestion(finding.message.split(":")[0][:60], finding.message + (f"\n\n→ {finding.fix}" if finding.fix else ""),
                   select=[n for n in finding.objects if doc.getObject(n) is not None],
                   command=command_for(finding.fix or ""))
    for name in s.select:
        obj = doc.getObject(name)
        try:
            if obj.isDerivedFrom("App::Part"):          # a chip, a package: everything in it
                shape = model.container_shape(obj)
            else:
                shape = obj.Shape.copy()
                shape.Placement = model.global_placement(obj)
            if not shape.isNull():
                s.shapes.append((obj.Label, shape, "problem"))
        except Exception:
            pass
    return s
