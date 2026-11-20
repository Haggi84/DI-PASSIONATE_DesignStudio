# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A chip layout, read once from GDS and kept as polygons.

The polygons are the single source of every later step: the meshes shown in
the 3-D view, the exact solids written to STEP, the pads. Nothing reads the
GDS a second time, and nothing reconstructs geometry from what is displayed —
so what is exported is always what was drawn, whatever the view shows.

Coordinates are millimetres, with the die centred on the origin: placing a
chip, mirroring pads and pairing bond wires symmetrically all work around
that centre. Where it sat in the GDS is kept in ``origin_gds_mm``.

Polygons come out of gdstk's own per-layer query, which walks the cell
hierarchy in C++: flattening the whole layout in Python first was 7 s on a
46 MB file and did not finish on a 168 MB one.
"""

from dataclasses import dataclass, field

import gdstk
import numpy as np

CONTEXT_CELL = "$$$CONTEXT_INFO$$$"   # KLayout's metadata cell, never geometry


@dataclass
class Layout:
    path: str
    cell: str
    origin_gds_mm: tuple                      # GDS position of the die centre
    outline: tuple                            # (xmin, ymin, xmax, ymax), mm, centred
    polygons: dict = field(default_factory=dict)   # key -> [np.ndarray (n, 2)]
    labels: list = field(default_factory=list)     # [(text, x, y, (layer, texttype))]

    @property
    def size(self):
        x0, y0, x1, y1 = self.outline
        return x1 - x0, y1 - y0

    def count(self, key):
        return len(self.polygons.get(key, ()))


def top_cells(lib):
    return [c for c in lib.top_level() if c.name != CONTEXT_CELL] or list(lib.cells)


def layers_in(path):
    """{(layer, datatype)} drawn anywhere in *path* — cheap, nothing flattened."""
    try:
        return {tuple(k) for k in gdstk.gds_info(path)["layers_and_datatypes"]}
    except (AttributeError, KeyError):      # gdstk before 0.9
        pass
    lib = gdstk.read_gds(path)
    keys = set()
    for cell in lib.cells:
        for poly in cell.polygons:
            keys.add((poly.layer, poly.datatype))
        for fp in cell.paths:
            for lay, dt in zip(fp.layers, fp.datatypes):
                keys.add((lay, dt))
    return keys


def read(path, tech, cell=None):
    """
    The Layout of *path* for the Technology *tech*: every physical layer, the
    pad layers, and the slit layers subtracted from what they cut.
    """
    lib = gdstk.read_gds(path)
    unit_mm = lib.unit * 1000.0
    tops = top_cells(lib)
    if cell:
        tops = [c for c in lib.cells if c.name == cell] or tops
    present = layers_in(path)

    wanted = (set(tech.layers) | set(tech.pad_layers) | set(tech.cuts)) & present

    raw = {}
    for key in wanted:
        polys = [p for c in tops for p in c.get_polygons(layer=key[0], datatype=key[1])]
        if polys:
            raw[key] = polys

    # Slits are holes in the metal they share a layer number with.
    for cut_key, targets in tech.cuts.items():
        cuts = raw.pop(cut_key, None)
        if not cuts:
            continue
        for target in targets:
            if target in raw:
                raw[target] = gdstk.boolean(raw[target], cuts, "not",
                                            layer=target[0], datatype=target[1])

    # Die outline: the extent of everything the top cells draw.
    boxes = [c.bounding_box() for c in tops]
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        raise ValueError(f"{path}: the layout draws nothing")
    lo = np.min([b[0] for b in boxes], axis=0) * unit_mm
    hi = np.max([b[1] for b in boxes], axis=0) * unit_mm
    centre = (lo + hi) / 2.0

    polygons = {key: [(np.asarray(p.points, dtype=float) * unit_mm) - centre for p in polys]
                for key, polys in raw.items()}

    labels = []
    for c in tops:
        for lab in c.get_labels():
            x, y = np.asarray(lab.origin, dtype=float) * unit_mm - centre
            labels.append((lab.text, float(x), float(y), (lab.layer, lab.texttype)))

    return Layout(path=path, cell=tops[0].name,
                  origin_gds_mm=(float(centre[0]), float(centre[1])),
                  outline=(float(lo[0] - centre[0]), float(lo[1] - centre[1]),
                           float(hi[0] - centre[0]), float(hi[1] - centre[1])),
                  polygons=polygons, labels=labels)
