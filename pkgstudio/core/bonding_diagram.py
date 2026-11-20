# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
The bonding diagram and wire list an assembly house asks for.

  SVG   the package from above, to scale: body, paddle, leads with their
        numbers and nets, the dies with their pads, every wire, pin 1, a
        title block (package, dies, wire, date) and the wire table
  CSV   one row per wire: net, first bond (die, pad, x/y in µm from that
        die's centre), second bond (lead number or die/pad, x/y in µm from
        the package centre — or from the other die's centre), wire
        material, diameter, loop height, length

Coordinates: the package frame seen from above, x right, y up, mm in;
µm in the CSV, as bond programmes take them. FreeCAD-free: the app layer
(app.bonding) gathers the data.

Data:
  {"title", "package": {"name", "body": (w, h), "paddle": (cx, cy, w, h),
                        "leads": [{"name", "x", "y", "w", "h", "net"}]},
   "dies": [{"name", "x", "y", "w", "h", "pads": [{"name", "x", "y", "w", "h", "net"}]}],
   "wires": [{"name", "net", "a": (die, pad), "b": (die or "", pad or lead),
              "pa": (x, y), "pb": (x, y), "material", "d_um", "loop_um", "length_mm"}],
   "notes": [str]}
"""

import csv
import datetime
import html

NET_COLOURS = {"ground": "#2e7d32", "power": "#c62828", "rf": "#6a1b9a", "signal": "#1565c0", "": "#555"}


def _esc(t):
    return html.escape(str(t), quote=True)


def svg(data, kinds=None, size=900):
    """The diagram as an SVG string. *kinds*: {net: kind} for the colours."""
    kinds = kinds or {}
    pkg = data["package"]
    bw, bh = pkg["body"]
    margin = 0.18 * max(bw, bh)
    span = max(bw, bh) + 2 * margin
    s = size / span                                   # px per mm
    ox, oy = size / 2, size / 2

    def X(x):
        return ox + x * s

    def Y(y):
        return oy - y * s

    col = lambda net: NET_COLOURS.get(kinds.get(net, ""), NET_COLOURS[""])  # noqa: E731
    table_w = 640
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size + table_w}" height="{max(size, 120 + 16 * len(data["wires"]))}" '
           f'font-family="DejaVu Sans, Arial, sans-serif" font-size="11">',
           '<rect width="100%" height="100%" fill="white"/>']
    out.append(f'<rect x="{X(-bw / 2):.2f}" y="{Y(bh / 2):.2f}" width="{bw * s:.2f}" height="{bh * s:.2f}" '
               'fill="#e8e8e8" stroke="#333" stroke-width="1.5"/>')
    if pkg.get("paddle"):
        cx, cy, pw, ph = pkg["paddle"]
        out.append(f'<rect x="{X(cx - pw / 2):.2f}" y="{Y(cy + ph / 2):.2f}" width="{pw * s:.2f}" height="{ph * s:.2f}" '
                   'fill="#f3d9b8" stroke="#a0662a" stroke-dasharray="4 2"/>')
    for ld in pkg["leads"]:
        out.append(f'<rect x="{X(ld["x"] - ld["w"] / 2):.2f}" y="{Y(ld["y"] + ld["h"] / 2):.2f}" '
                   f'width="{ld["w"] * s:.2f}" height="{ld["h"] * s:.2f}" fill="#d9a066" stroke="#7a4b1d"/>')
        # number outside the body, net further out
        dx, dy = ld["x"], ld["y"]
        horiz = abs(dx) / max(bw, 1e-9) > abs(dy) / max(bh, 1e-9)
        if horiz:
            sx = 1 if dx > 0 else -1
            tx, ty = X(sx * (bw / 2 + margin * 0.12)), Y(dy) + 4
            anchor = "start" if sx > 0 else "end"
            nx = X(sx * (bw / 2 + margin * 0.35))
            out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{_esc(ld["name"])}</text>')
            if ld.get("net"):
                out.append(f'<text x="{nx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" fill="{col(ld["net"])}">'
                           f'{_esc(ld["net"])}</text>')
        else:
            sy = 1 if dy > 0 else -1
            tx, ty = X(dx), Y(sy * (bh / 2 + margin * 0.1)) + (0 if sy > 0 else 10)
            out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle">{_esc(ld["name"])}</text>')
            if ld.get("net"):
                ny = Y(sy * (bh / 2 + margin * 0.3))
                out.append(f'<text x="{tx:.1f}" y="{ny:.1f}" text-anchor="start" fill="{col(ld["net"])}" '
                           f'transform="rotate(-90 {tx:.1f} {ny:.1f})">{_esc(ld["net"])}</text>' if sy > 0 else
                           f'<text x="{tx:.1f}" y="{ny + 10:.1f}" text-anchor="end" fill="{col(ld["net"])}" '
                           f'transform="rotate(-90 {tx:.1f} {ny + 10:.1f})">{_esc(ld["net"])}</text>')
    for d in data["dies"]:
        out.append(f'<rect x="{X(d["x"] - d["w"] / 2):.2f}" y="{Y(d["y"] + d["h"] / 2):.2f}" width="{d["w"] * s:.2f}" '
                   f'height="{d["h"] * s:.2f}" fill="#b0bec5" stroke="#37474f" stroke-width="1.2"/>')
        out.append(f'<text x="{X(d["x"]):.1f}" y="{Y(d["y"]):.1f}" text-anchor="middle" font-size="10" '
                   f'fill="#263238">{_esc(d["name"])}</text>')
        for p in d["pads"]:
            out.append(f'<rect x="{X(p["x"] - p["w"] / 2):.2f}" y="{Y(p["y"] + p["h"] / 2):.2f}" '
                       f'width="{max(p["w"] * s, 1):.2f}" height="{max(p["h"] * s, 1):.2f}" fill="#eceff1" '
                       'stroke="#455a64" stroke-width="0.5"/>')
    for w in data["wires"]:
        (x0, y0), (x1, y1) = w["pa"], w["pb"]
        out.append(f'<line x1="{X(x0):.2f}" y1="{Y(y0):.2f}" x2="{X(x1):.2f}" y2="{Y(y1):.2f}" '
                   f'stroke="{col(w["net"])}" stroke-width="1.1"/>')
        out.append(f'<circle cx="{X(x0):.2f}" cy="{Y(y0):.2f}" r="1.6" fill="{col(w["net"])}"/>')
    # pin 1
    if pkg["leads"]:
        p1 = next((ld for ld in pkg["leads"] if str(ld["name"]) == "1"), pkg["leads"][0])
        out.append(f'<circle cx="{X(-bw / 2 + 0.06 * bw):.1f}" cy="{Y(bh / 2 - 0.06 * bh):.1f}" r="4" fill="#333"/>'
                   if p1["x"] < 0 and p1["y"] > 0 else "")
    # title and table
    tx = size + 10
    lines = [f'<text x="{tx}" y="22" font-size="15" font-weight="bold">{_esc(data.get("title", "Bonding diagram"))}</text>',
             f'<text x="{tx}" y="40">{_esc(pkg.get("name", ""))} — {len(data["wires"])} wires — '
             f'{datetime.date.today().isoformat()}</text>']
    y = 62
    head = ["wire", "net", "from", "to", "mm"]
    xs = [tx, tx + 32, tx + 120, tx + 470, tx + 560]
    lines += [f'<text x="{x}" y="{y}" font-weight="bold">{h}</text>' for x, h in zip(xs, head)]
    for i, w in enumerate(data["wires"], 1):
        y += 15
        cells = [i, w["net"], f'{w["a"][0]} {w["a"][1]}',
                 f'lead {w["b"][1]}' if not w["b"][0] else f'{w["b"][0]} {w["b"][1]}', f'{w["length_mm"]:.2f}']
        lines += [f'<text x="{x}" y="{y}" fill="{col(w["net"]) if k == 1 else "#000"}">{_esc(str(c)[:48])}</text>'
                  for k, (x, c) in enumerate(zip(xs, cells))]
    y += 25
    for n in data.get("notes", []):
        lines.append(f'<text x="{tx}" y="{y}" font-size="10" fill="#555">{_esc(n)}</text>')
        y += 14
    out += lines
    out.append("</svg>")
    return "\n".join(x for x in out if x)


def write_svg(data, path, kinds=None):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg(data, kinds))
    return path


def rows(data):
    """The wire list rows (dicts), µm, first bond relative to its die's centre."""
    dies = {d["name"]: d for d in data["dies"]}
    out = []
    for i, w in enumerate(data["wires"], 1):
        da = dies.get(w["a"][0])
        ax, ay = w["pa"]
        if da:
            ax, ay = ax - da["x"], ay - da["y"]
        bx, by = w["pb"]
        ref = "package centre"
        if w["b"][0] and w["b"][0] in dies:
            db = dies[w["b"][0]]
            bx, by = bx - db["x"], by - db["y"]
            ref = f'{w["b"][0]} centre'
        out.append({"no": i, "wire": w["name"], "net": w["net"],
                    "first_die": w["a"][0], "first_pad": w["a"][1],
                    "first_x_um": round(ax * 1000, 1), "first_y_um": round(ay * 1000, 1),
                    "second": f'lead {w["b"][1]}' if not w["b"][0] else f'{w["b"][0]} {w["b"][1]}',
                    "second_x_um": round(bx * 1000, 1), "second_y_um": round(by * 1000, 1), "second_ref": ref,
                    "material": w.get("material", ""), "diameter_um": w.get("d_um", ""),
                    "loop_height_um": w.get("loop_um", ""), "length_mm": round(w["length_mm"], 3)})
    return out


def write_csv(data, path):
    rs = rows(data)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rs[0].keys()) if rs else ["no"])
        w.writeheader()
        w.writerows(rs)
    return path
