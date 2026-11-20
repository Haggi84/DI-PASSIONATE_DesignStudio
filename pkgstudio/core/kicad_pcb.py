# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
KiCad boards read: the .kicad_pcb S-expression file (KiCad 6 to 9) to plain
data — the stack-up, the outline, nets, footprints with their pads,
tracks, vias and filled zones — so the board a designer drew in KiCad is
the board the package sits on and the EM model sees, instead of one drawn
again here.

Coordinates are turned into the workbench's: mm, y *up* (KiCad's y points
down), angles counter-clockwise. A pad's position in the file is relative
to its footprint and in the footprint's own frame; its angle is absolute.

Plain Python, FreeCAD-free (core.kicad_board builds the solids).
"""

import math
import re
from dataclasses import dataclass, field

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


class Q(str):
    """A quoted string atom (kept apart from bare atoms only for writing)."""


def parse(text):
    """The S-expression *text* as nested lists of str."""
    stack, cur = [], []
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"cannot read the S-expression at {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done, cur = cur, stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(m.group(4))
    if stack:
        raise ValueError("unbalanced parentheses")
    return cur[0] if len(cur) == 1 else cur


def find(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def findall(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def _xy(node, key):
    n = find(node, key)
    return (float(n[1]), -float(n[2])) if n else None


def _val(node, key, default=None, cast=float):
    n = find(node, key)
    return cast(n[1]) if n and len(n) > 1 else default


@dataclass
class Layer:
    name: str
    kind: str                     # "copper" | "dielectric"
    thickness: float
    material: str = ""
    eps_r: float = 0.0
    tan_d: float = 0.0


@dataclass
class Pad:
    ref: str                      # footprint reference
    number: str
    shape: str                    # rect | roundrect | circle | oval | trapezoid | custom
    kind: str                     # smd | thru_hole | np_thru_hole | connect
    x: float
    y: float
    w: float
    h: float
    angle: float
    layers: list
    net: str = ""
    drill: float = 0.0


@dataclass
class Board:
    thickness: float = 1.6
    stack: list = field(default_factory=list)     # Layer, top to bottom
    outline: list = field(default_factory=list)   # [[(x, y)…]] closed loops; the largest is the board
    nets: dict = field(default_factory=dict)      # number -> name
    footprints: dict = field(default_factory=dict)  # ref -> {"name", "x", "y", "angle", "layer"}
    pads: list = field(default_factory=list)
    tracks: list = field(default_factory=list)    # {"layer", "net", "w", "pts": [(x, y)…]}
    vias: list = field(default_factory=list)      # {"x", "y", "d", "drill", "net", "layers"}
    zones: list = field(default_factory=list)     # {"layer", "net", "pts": [(x, y)…]}
    version: str = ""

    def copper_layers(self):
        return [l.name for l in self.stack if l.kind == "copper"]

    def bbox(self):
        pts = [p for loop in self.outline for p in loop]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


def _arc_points(start, mid, end, seg_deg=10.0):
    """Points on the circle through start, mid, end (y-up), start to end via mid."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, end]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1, a2, a3 = (math.atan2(y - uy, x - ux) for x, y in (start, mid, end))

    def ccw(a, b):
        return (b - a) % (2 * math.pi)
    if ccw(a1, a2) <= ccw(a1, a3):
        sweep = ccw(a1, a3)
    else:
        sweep = -ccw(a3, a1)
    n = max(int(abs(math.degrees(sweep)) / seg_deg), 2)
    return [(ux + r * math.cos(a1 + sweep * k / n), uy + r * math.sin(a1 + sweep * k / n)) for k in range(n + 1)]


def _chain(segments, tol=1e-4):
    """Closed loops from segments [[(x, y)…]] that meet end to end."""
    segs = [list(s) for s in segments if len(s) >= 2]
    loops = []
    while segs:
        loop = segs.pop(0)
        changed = True
        while changed and math.dist(loop[0], loop[-1]) > tol:
            changed = False
            for i, s in enumerate(segs):
                if math.dist(loop[-1], s[0]) <= tol:
                    loop += s[1:]
                elif math.dist(loop[-1], s[-1]) <= tol:
                    loop += s[::-1][1:]
                elif math.dist(loop[0], s[-1]) <= tol:
                    loop = s[:-1] + loop
                elif math.dist(loop[0], s[0]) <= tol:
                    loop = s[::-1][:-1] + loop
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if math.dist(loop[0], loop[-1]) <= tol:
            loops.append(loop[:-1])
    return loops


def _rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def _layer_name(node):
    lay = find(node, "layer")
    return str(lay[1]) if lay else ""


def read(path_or_text):
    """A Board from a .kicad_pcb file (or its text)."""
    text = path_or_text
    if "\n" not in path_or_text and not path_or_text.lstrip().startswith("("):
        with open(path_or_text, encoding="utf-8") as fh:
            text = fh.read()
    root = parse(text)
    if not root or root[0] != "kicad_pcb":
        raise ValueError("not a KiCad board (kicad_pcb)")
    b = Board(version=str(_val(root, "version", "", str)))
    general = find(root, "general")
    if general:
        b.thickness = _val(general, "thickness", 1.6)
    for n in findall(root, "net"):
        if len(n) >= 3:
            b.nets[str(n[1])] = str(n[2])

    setup = find(root, "setup")
    stack = find(setup, "stackup") if setup else None
    if stack:
        for lay in findall(stack, "layer"):
            name = str(lay[1])
            typ = str(_val(lay, "type", "", str))
            t = _val(lay, "thickness", 0.0)
            if typ == "copper":
                b.stack.append(Layer(name, "copper", t))
            elif typ in ("core", "prepreg") or name.startswith("dielectric"):
                b.stack.append(Layer(name, "dielectric", t, str(_val(lay, "material", "", str)),
                                     _val(lay, "epsilon_r", 0.0), _val(lay, "loss_tangent", 0.0)))
    if not b.stack:                     # no stack-up section: two copper layers on FR-4
        b.stack = [Layer("F.Cu", "copper", 0.035), Layer("dielectric 1", "dielectric", b.thickness - 0.07, "FR4", 4.5,
                                                         0.02), Layer("B.Cu", "copper", 0.035)]

    # outline
    segs = []
    for kind in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly"):
        for g in findall(root, kind):
            if _layer_name(g) != "Edge.Cuts":
                continue
            if kind == "gr_line":
                segs.append([_xy(g, "start"), _xy(g, "end")])
            elif kind == "gr_arc":
                if find(g, "mid"):
                    segs.append(_arc_points(_xy(g, "start"), _xy(g, "mid"), _xy(g, "end")))
            elif kind == "gr_rect":
                (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
                b.outline.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            elif kind == "gr_circle":
                (cx, cy), (ex, ey) = _xy(g, "center"), _xy(g, "end")
                r = math.hypot(ex - cx, ey - cy)
                b.outline.append([(cx + r * math.cos(2 * math.pi * k / 48), cy + r * math.sin(2 * math.pi * k / 48))
                                  for k in range(48)])
            else:
                pts = find(g, "pts")
                b.outline.append([(float(p[1]), -float(p[2])) for p in findall(pts, "xy")])
    b.outline += _chain(segs)
    b.outline.sort(key=lambda loop: -abs(_area(loop)))

    # footprints and pads
    for fp in findall(root, "footprint") + findall(root, "module"):
        at = find(fp, "at")
        fx, fy = float(at[1]), -float(at[2])
        fa = float(at[3]) if len(at) > 3 else 0.0
        ref = ""
        for p in findall(fp, "property"):
            if str(p[1]) == "Reference":
                ref = str(p[2])
        for t in findall(fp, "fp_text"):
            if str(t[1]) == "reference" and not ref:
                ref = str(t[2])
        base, k = ref or str(fp[1]), 2
        ref = base
        while ref in b.footprints:          # references need not be unique (graphics footprints, unannotated)
            ref, k = f"{base}_{k}", k + 1
        b.footprints[ref] = {"name": str(fp[1]), "x": fx, "y": fy, "angle": fa, "layer": _layer_name(fp)}
        # copper drawn in the footprint (RF structures are often fp_poly on F.Cu)
        for g in findall(fp, "fp_poly") + findall(fp, "fp_rect"):
            lay = _layer_name(g)
            if not lay.endswith(".Cu"):
                continue
            if g[0] == "fp_poly":
                loc = [(float(q[1]), -float(q[2])) for q in findall(find(g, "pts"), "xy")]
            else:
                (x0, y0), (x1, y1) = _xy(g, "start"), _xy(g, "end")
                loc = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            pts = [(fx + rx, fy + ry) for rx, ry in (_rot(x, y, fa) for x, y in loc)]
            b.zones.append({"layer": lay, "net": "", "pts": pts, "from": ref})
        for pad in findall(fp, "pad"):
            pat = find(pad, "at")
            px, py = float(pat[1]), -float(pat[2])
            pa = float(pat[3]) if len(pat) > 3 else fa
            rx, ry = _rot(px, py, fa)
            size = find(pad, "size")
            layers = [str(x) for x in (find(pad, "layers") or [])[1:]]
            net = find(pad, "net")
            drill = find(pad, "drill")
            dr = 0.0
            if drill:
                nums = [x for x in drill[1:] if not isinstance(x, list) and re.match(r"^-?[\d.]+$", str(x))]
                dr = float(nums[0]) if nums else 0.0
            b.pads.append(Pad(ref, str(pad[1]), str(pad[3]) if len(pad) > 3 else "rect", str(pad[2]),
                              fx + rx, fy + ry, float(size[1]), float(size[2]), pa, layers,
                              str(net[2]) if net and len(net) > 2 else "", dr))

    for g in findall(root, "gr_poly"):          # copper drawn on the board
        lay = _layer_name(g)
        if lay.endswith(".Cu"):
            b.zones.append({"layer": lay, "net": "", "pts": [(float(q[1]), -float(q[2]))
                                                             for q in findall(find(g, "pts"), "xy")]})
    for s in findall(root, "segment"):
        b.tracks.append({"layer": _layer_name(s), "net": b.nets.get(str(_val(s, "net", "", str)), ""),
                         "w": _val(s, "width", 0.2), "pts": [_xy(s, "start"), _xy(s, "end")]})
    for s in findall(root, "arc"):
        b.tracks.append({"layer": _layer_name(s), "net": b.nets.get(str(_val(s, "net", "", str)), ""),
                         "w": _val(s, "width", 0.2),
                         "pts": _arc_points(_xy(s, "start"), _xy(s, "mid"), _xy(s, "end"))})
    for v in findall(root, "via"):
        at = find(v, "at")
        lay = find(v, "layers")
        b.vias.append({"x": float(at[1]), "y": -float(at[2]), "d": _val(v, "size", 0.6),
                       "drill": _val(v, "drill", 0.3), "net": b.nets.get(str(_val(v, "net", "", str)), ""),
                       "layers": [str(x) for x in lay[1:]] if lay else ["F.Cu", "B.Cu"]})
    for z in findall(root, "zone"):
        net = str(_val(z, "net_name", "", str)) or b.nets.get(str(_val(z, "net", "", str)), "")
        for fpoly in findall(z, "filled_polygon"):
            pts = find(fpoly, "pts")
            b.zones.append({"layer": _layer_name(fpoly) or _layer_name(z), "net": net,
                            "pts": [(float(p[1]), -float(p[2])) for p in findall(pts, "xy")]})
    return b


def _area(loop):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(loop, loop[1:] + loop[:1]))


def z_ranges(board):
    """{layer name: (z0, z1)}, z = 0 at the board's underside, copper and
    dielectric from the stack-up."""
    z = sum(l.thickness for l in board.stack)
    out = {}
    for l in board.stack:
        out[l.name] = (z - l.thickness, z)
        z -= l.thickness
    return out
