# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The package for the board designer: a KiCad footprint (.kicad_mod), a
symbol (.kicad_sym) whose pins carry the package's nets, and the 3-D model
beside them — so the MCM is placed in KiCad as it was built here, with the
pin-out the package LVS checked.

KiCad 8 file format (footprint version 20240108, symbol library 20231120;
KiCad 9 reads both). Coordinates: KiCad's, y down — the package frame's y is
flipped on writing.

  footprint(name, pads, body, ...)   pads: [{"number", "x", "y", "w", "h",
                                      "ep"}] in the package frame (y up)
  symbol(name, pins, footprint_ref)  pins: [{"number", "name", "kind"}],
                                      kind as core.pinlist (signal, rf,
                                      power, ground, nc)

FreeCAD-free.
"""

ETYPE = {"signal": "bidirectional", "rf": "passive", "power": "power_in", "ground": "power_in", "nc": "no_connect"}


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 5e-5 else "0"


def footprint(name, pads, body, descr="", model_path=None, courtyard=0.25, paste_ratio=-0.2):
    """The .kicad_mod text. *body*: (w, h) mm. Exposed pads get a reduced
    paste opening (*paste_ratio* of their size, KiCad's
    solder_paste_margin_ratio) against voids and floating."""
    bw, bh = body
    xs = [abs(p["x"]) + p["w"] / 2 for p in pads] + [bw / 2]
    ys = [abs(p["y"]) + p["h"] / 2 for p in pads] + [bh / 2]
    cx, cy = max(xs) + courtyard, max(ys) + courtyard
    font = "(effects (font (size 1 1) (thickness 0.15)))"
    L = ["(footprint " + _q(name),
         "\t(version 20240108)", '\t(generator "pkgstudio")', '\t(generator_version "1.0")', '\t(layer "F.Cu")',
         f"\t(descr {_q(descr or name)})", '\t(tags "QFN Package Studio")',
         f'\t(property "Reference" "REF**" (at 0 {_n(-cy - 1.0)} 0) (layer "F.SilkS") {font})',
         f'\t(property "Value" {_q(name)} (at 0 {_n(cy + 1.0)} 0) (layer "F.Fab") {font})',
         "\t(attr smd)"]

    def line(x0, y0, x1, y1, layer, w):
        L.append(f"\t(fp_line (start {_n(x0)} {_n(y0)}) (end {_n(x1)} {_n(y1)}) "
                 f"(stroke (width {w}) (type solid)) (layer {_q(layer)}))")

    hx, hy = bw / 2, bh / 2
    # fab outline with the pin-1 chamfer (pin 1 top left, as JEDEC and KiCad's library)
    ch = min(1.0, 0.2 * min(bw, bh))
    pts = [(-hx + ch, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy + ch), (-hx + ch, -hy)]
    for a, b in zip(pts, pts[1:]):
        line(*a, *b, "F.Fab", 0.1)
    # silk corners, clear of the pads
    pad_x = max((abs(p["x"]) + p["w"] / 2 for p in pads if not p.get("ep")), default=hx)
    s = hx + 0.11
    k = max(s - (pad_x - hx) - 0.4, 0.2)
    for sx in (-1, 1):
        for sy in (-1, 1):
            line(sx * s, sy * s, sx * (s - min(k, 0.6)), sy * s, "F.SilkS", 0.12)
            line(sx * s, sy * s, sx * s, sy * (s - min(k, 0.6)), "F.SilkS", 0.12)
    for a, b in (((-cx, -cy), (cx, -cy)), ((cx, -cy), (cx, cy)), ((cx, cy), (-cx, cy)), ((-cx, cy), (-cx, -cy))):
        line(*a, *b, "F.CrtYd", 0.05)
    L.append(f'\t(fp_text user "${{REFERENCE}}" (at 0 0 0) (layer "F.Fab") {font})')
    for p in pads:
        layers = '"F.Cu" "F.Paste" "F.Mask"'
        extra = f" (solder_paste_margin_ratio {paste_ratio})" if p.get("ep") else ""
        L.append(f'\t(pad {_q(p["number"])} smd rect (at {_n(p["x"])} {_n(-p["y"])}) (size {_n(p["w"])} {_n(p["h"])}) '
                 f"(layers {layers}){extra})")
    if model_path:
        L.append(f"\t(model {_q(model_path)} (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))")
    L.append(")")
    return "\n".join(L) + "\n"


def symbol(name, pins, footprint_ref="", descr=""):
    """The .kicad_sym library text with one symbol *name*. Pins sorted by
    number, the first half on the left (top to bottom), the rest on the
    right (bottom to top) — the order they run round the package."""
    pins = sorted(pins, key=lambda p: (len(str(p["number"])), str(p["number"])))
    n = len(pins)
    left, right = pins[:(n + 1) // 2], pins[(n + 1) // 2:]
    step = 2.54
    h = max(len(left), len(right)) * step + step
    w = 15.24
    font = "(effects (font (size 1.27 1.27)))"
    hide = "(effects (font (size 1.27 1.27)) (hide yes))"
    L = ["(kicad_symbol_lib", "\t(version 20231120)", '\t(generator "pkgstudio")', '\t(generator_version "1.0")',
         f"\t(symbol {_q(name)}", "\t\t(pin_names (offset 1.016))", "\t\t(exclude_from_sim no)",
         "\t\t(in_bom yes)", "\t\t(on_board yes)",
         f'\t\t(property "Reference" "U" (at 0 {_n(h / 2 + 1.27)} 0) {font})',
         f'\t\t(property "Value" {_q(name)} (at 0 {_n(-h / 2 - 1.27)} 0) {font})',
         f'\t\t(property "Footprint" {_q(footprint_ref)} (at 0 0 0) {hide})',
         f'\t\t(property "Datasheet" "" (at 0 0 0) {hide})',
         f'\t\t(property "Description" {_q(descr or name)} (at 0 0 0) {hide})',
         f"\t\t(symbol {_q(name + '_0_1')}",
         f"\t\t\t(rectangle (start {_n(-w / 2)} {_n(h / 2)}) (end {_n(w / 2)} {_n(-h / 2)}) "
         "(stroke (width 0.254) (type default)) (fill (type background)))", "\t\t)",
         f"\t\t(symbol {_q(name + '_1_1')}"]

    def pin(p, x, y, angle):
        kind = p.get("kind", "signal")
        L.append(f"\t\t\t(pin {ETYPE.get(kind, 'passive')} line (at {_n(x)} {_n(y)} {angle}) (length 2.54)"
                 f" (name {_q(p['name'] or '~')} {font}) (number {_q(p['number'])} {font}))")

    top = h / 2 - step
    for i, p in enumerate(left):
        pin(p, -w / 2 - 2.54, top - i * step, 0)
    for i, p in enumerate(right):
        pin(p, w / 2 + 2.54, -h / 2 + step + i * step, 180)
    L += ["\t\t)", "\t)", ")"]
    return "\n".join(L) + "\n"
