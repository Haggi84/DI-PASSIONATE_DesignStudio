# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A KiCad board (core.kicad_pcb) as solids: core.pcb Pieces, ready for
app.boards.make_board — so a package is placed on the footprint the
designer drew, and the EM model sees the designer's lines, vias and planes.

Stack-up from the board's own stack-up (copper and dielectric thicknesses,
materials); z = 0 at the board's underside. Copper per layer and net:
tracks, pads, vias (barrels through the layers they join), filled zones,
copper polygons. SMD pads on the outer layers are lands (`Land` True,
grouped by footprint reference) for app.boards.place_package.

*region* (x0, y0, x1, y1 in the board's coordinates, y up) builds only what
lies in it, cut to it — the board region an EM model needs. *centre* moves
the board (or the region) to the origin.

Dielectric materials: KiCad's names are mapped to the library's
(MATERIALS); an unknown one becomes FR-4 and is reported, with the εr the
stack-up gives, so it can be added to the materials library.
"""

import math

import FreeCAD
import Part

from . import kicad_pcb as kp
from .pcb import Piece

V = FreeCAD.Vector

MATERIALS = {"fr4": "FR-4", "fr-4": "FR-4", "ro4350b": "Rogers RO4350B", "rogers ro4350b": "Rogers RO4350B",
             "ro4003c": "Rogers RO4003C", "rogers ro4003c": "Rogers RO4003C", "polyimide": "Polyimide (Kapton)"}


def _material(layer, report, library=None):
    name = MATERIALS.get(layer.material.strip().lower())
    if name and (library is None or name in library):
        return name
    report.setdefault("unknown_materials", []).append(
        {"layer": layer.name, "material": layer.material, "eps_r": layer.eps_r, "tan_d": layer.tan_d})
    return "FR-4"


def _rings(pts, nd=6):
    """The closed rings of a KiCad filled polygon. KiCad stores a fill with
    holes as ONE outline that runs into each hole and back along a slit
    (the same edge twice, in opposite directions); taking out the edges that
    come back gives the outline and the holes as rings of their own."""
    q = [(round(x, nd), round(y, nd)) for x, y in pts]
    if q[0] == q[-1]:
        q = q[:-1]
    edges = [(q[i], q[(i + 1) % len(q)]) for i in range(len(q)) if q[i] != q[(i + 1) % len(q)]]
    count = {}
    for e in edges:
        count[e] = count.get(e, 0) + 1
    keep = []
    for a, b in edges:
        if count.get((b, a), 0):                     # a slit: drop it once each way
            count[(b, a)] -= 1
            continue
        keep.append((a, b))
    if len(keep) == len(edges):
        return [q]
    nxt = {}
    for a, b in keep:
        nxt.setdefault(a, []).append(b)
    rings = []
    while nxt:
        start = next(iter(nxt))
        ring, cur = [start], start
        while True:
            outs = nxt.get(cur)
            if not outs:
                break
            n = outs.pop()
            if not outs:
                del nxt[cur]
            if n == start:
                break
            ring.append(n)
            cur = n
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def _ring_area(ring):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))


def _ring_face(ring):
    vs = [V(x, y, 0) for x, y in ring] + [V(ring[0][0], ring[0][1], 0)]
    return Part.Face(Part.makePolygon(vs))


def _face(pts):
    rings = _rings(pts)
    if len(rings) == 1:
        return _ring_face(rings[0])
    rings.sort(key=lambda r: -abs(_ring_area(r)))
    face = _ring_face(rings[0])
    holes = [_ring_face(r) for r in rings[1:]]
    if holes:
        face = face.cut(Part.makeCompound(holes)).Faces
        face = face[0] if len(face) == 1 else Part.makeCompound(face)
    return face


def _slab(face, z0, z1):
    s = face.extrude(V(0, 0, z1 - z0))
    s.translate(V(0, 0, z0))
    return s


def _pad_face(p):
    w, h = p.w, p.h
    if p.shape == "circle":
        f = Part.Face(Part.Wire(Part.makeCircle(w / 2, V(0, 0, 0))))
    elif p.shape == "oval" and abs(w - h) > 1e-9:
        r = min(w, h) / 2
        L = max(w, h) - 2 * r
        box = Part.makePlane(L if w > h else w, h if w > h else L, V(-(L if w > h else w) / 2,
                                                                     -(h if w > h else L) / 2, 0))
        ends = [Part.Face(Part.Wire(Part.makeCircle(r, V(sx * L / 2, 0, 0) if w > h else V(0, sx * L / 2, 0))))
                for sx in (-1, 1)]
        f = box.fuse(ends).removeSplitter().Faces[0]
    else:
        f = Part.makePlane(w, h, V(-w / 2, -h / 2, 0))
    f.rotate(V(0, 0, 0), V(0, 0, 1), p.angle)
    f.translate(V(p.x, p.y, 0))
    return f


def _track_face(pts, w):
    faces = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-9:
            continue
        nx, ny = -(by - ay) / L * w / 2, (bx - ax) / L * w / 2
        faces.append(_face([(ax + nx, ay + ny), (bx + nx, by + ny), (bx - nx, by - ny), (ax - nx, ay - ny)]))
    for x, y in pts:                                   # round ends and joints, as KiCad draws them
        faces.append(Part.Face(Part.Wire(Part.makeCircle(w / 2, V(x, y, 0)))))
    f = faces[0].fuse(faces[1:]) if len(faces) > 1 else faces[0]
    return f.removeSplitter()


def _in(bb, region):
    if region is None:
        return True
    x0, y0, x1, y1 = region
    return bb.XMax >= x0 and bb.XMin <= x1 and bb.YMax >= y0 and bb.YMin <= y1


def _clip(shape, region, z0=-10.0, z1=20.0):
    if region is None:
        return shape
    x0, y0, x1, y1 = region
    return shape.common(Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0)))


def pieces(board, region=None, centre=True, library=None, max_vias=5000):
    """(Pieces, report) for KiCad board *board* (kicad_pcb.Board or a path)."""
    b = board if isinstance(board, kp.Board) else kp.read(board)
    report = {"layers": [(l.name, l.kind, l.thickness, l.material) for l in b.stack]}
    zr = kp.z_ranges(b)
    out = []
    if not b.outline:
        raise ValueError("the board has no outline on Edge.Cuts")
    outline = _face(b.outline[0])
    holes = [_face(loop) for loop in b.outline[1:]]
    drills = [(p.x, p.y, p.drill) for p in b.pads if p.drill > 0 and p.kind in ("thru_hole", "np_thru_hole")]
    for l in b.stack:
        if l.kind != "dielectric":
            continue
        z0, z1 = zr[l.name]
        s = _slab(outline, z0, z1)
        for h in holes:
            s = s.cut(_slab(h, z0 - 1, z1 + 1))
        for x, y, d in drills:
            if region is None or (region[0] - d <= x <= region[2] + d and region[1] - d <= y <= region[3] + d):
                s = s.cut(Part.makeCylinder(d / 2, z1 - z0 + 2, V(x, y, z0 - 1)))
        s = _clip(s, region)
        if s.Volume > 1e-9:
            out.append(Piece(f"{l.name} ({l.material or 'dielectric'})", s, _material(l, report, library),
                             "dielectric", "stack", {"KicadLayer": l.name, "EpsR": l.eps_r, "TanD": l.tan_d}))

    copper = {}                                        # (layer, net) -> [solids]

    def add(layer, net, solid):
        copper.setdefault((layer, net), []).append(solid)

    cu = set(b.copper_layers())
    for t in b.tracks:
        if t["layer"] not in cu:
            continue
        f = _track_face(t["pts"], t["w"])
        if not _in(f.BoundBox, region):
            continue
        z0, z1 = zr[t["layer"]]
        add(t["layer"], t["net"], _clip(_slab(f, z0, z1), region))
    for z in b.zones:
        if z["layer"] not in cu or len(z["pts"]) < 3:
            continue
        f = _face(z["pts"])
        if not _in(f.BoundBox, region):
            continue
        z0, z1 = zr[z["layer"]]
        add(z["layer"], z["net"], _clip(_slab(f, z0, z1), region))
    vias = [v for v in b.vias if region is None or (region[0] <= v["x"] <= region[2] and region[1] <= v["y"] <= region[3])]
    if len(vias) > max_vias:
        report["vias_skipped"] = len(vias)
        vias = []
    for v in vias:
        layers = [n for n in b.copper_layers() if n in v["layers"]] or b.copper_layers()
        zs = [zr[n] for n in b.copper_layers()]
        idx = [b.copper_layers().index(n) for n in layers]
        z_top = zs[min(idx)][1]
        z_bot = zs[max(idx)][0]
        barrel = Part.makeCylinder(v["d"] / 2, z_top - z_bot, V(v["x"], v["y"], z_bot))
        add("vias", v["net"], barrel)
    lands = []
    for p in b.pads:
        f = _pad_face(p)
        if not _in(f.BoundBox, region):
            continue
        layers = [n for n in b.copper_layers() if n in p.layers or "*.Cu" in p.layers
                  or (n in ("F.Cu", "B.Cu") and f"{n[0]}&B.Cu" in p.layers)]
        if p.kind == "np_thru_hole":
            continue
        for n in layers:
            z0, z1 = zr[n]
            solid = _clip(_slab(f, z0, z1), region)
            if p.kind == "smd" and n in ("F.Cu", "B.Cu") and solid.Volume > 1e-12:
                lands.append(Piece(f"{p.ref} pad {p.number}", solid, "Copper (Cu)", "copper", p.ref,
                                   {"Land": n == "F.Cu", "PadNumber": p.number, "Net": p.net}))
            else:
                add(n, p.net, solid)
        if p.kind == "thru_hole" and p.drill > 0:
            add("vias", p.net, Part.makeCylinder(max(p.drill / 2 + 0.025, 0.05), sum(l.thickness for l in b.stack),
                                                 V(p.x, p.y, 0)))
    for (layer, net), solids in sorted(copper.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        solids = [s for s in solids if not s.isNull() and s.Volume > 1e-12]
        if not solids:
            continue
        name = f"{layer} {net or '(no net)'}" if layer != "vias" else f"vias {net or '(no net)'}"
        out.append(Piece(name, Part.makeCompound(solids), "Copper (Cu)", "copper", net or "copper",
                         {"KicadLayer": layer, "Net": net}))
    out += lands

    # move to the origin
    if centre:
        if region is not None:
            cx, cy = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
        else:
            x0, y0, x1, y1 = b.bbox()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for p in out:
            p.shape.translate(V(-cx, -cy, 0))
        report["offset"] = (-cx, -cy)
    report.update(thickness=sum(l.thickness for l in b.stack), top=sum(l.thickness for l in b.stack),
                  footprints=len(b.footprints), pieces=len(out), nets=sorted({p.props.get("Net", "") for p in out}
                                                                            - {""}))
    return out, report
