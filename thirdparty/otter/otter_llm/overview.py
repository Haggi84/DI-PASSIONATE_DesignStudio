"""A static picture of the structure: side view (x-z, height exaggerated) and top view (x-y),
coloured like in FreeCAD. Works without WebGL, and goes into the download package as
overview.svg.

Bodies are drawn as the projection of their FreeCAD triangles (the STL files read_model writes
for the 3D view), so bondwires show as arcs; without them as bounding boxes.
"""
import struct
from html import escape
from pathlib import Path

from otter_llm.checks import is_conductor
from otter_llm.model import Model
from otter_llm.settings import OtterSettings

PANEL_W, PANEL_H, PAD = 440, 260, 28
FILL = {"port": "#ef4444", "metal": "#f59e0b", "dielectric": "#a7f3d0", "air": "#e2e8f0"}
OPACITY = {"port": 0.95, "metal": 1.0, "dielectric": 0.5, "air": 0.25}


def css_color(rgb, fallback, brightness=1.0) -> str:
    if not rgb:
        rgb = [int(fallback[k:k + 2], 16) / 255 for k in (1, 3, 5)]
    return "#" + "".join(f"{round(255 * c * brightness):02x}" for c in rgb)


def read_stl(path: Path) -> list[tuple]:
    """Triangles of a binary STL as ((x, y, z), (x, y, z), (x, y, z))."""
    data = path.read_bytes()
    if len(data) < 84:
        return []
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) < 84 + 50 * count:
        return []
    triangles = []
    for k in range(count):
        values = struct.unpack_from("<12f", data, 84 + 50 * k)
        triangles.append((values[3:6], values[6:9], values[9:12]))
    return triangles


def overview_svg(model: Model, settings: OtterSettings, view_dir=None) -> str:
    material_of = {label: parent for _, parent, label in settings.assignments("Material")}
    conductors = {name for name in settings.materials() if is_conductor(settings.sections[f"MATERIAL-{name}"])}
    stack = [label for group in model.layers + model.spanning for label in group.bodies]
    labels = [label for label in stack + model.ports if label in model.bodies]
    if not labels:
        return ""

    def kind(label):
        if label in model.ports:
            return "port"
        if material_of.get(label) in conductors:
            return "metal"
        return "dielectric" if label in material_of else "air"

    triangles = {}
    for label in labels:
        mesh = model.bodies[label].get("mesh")
        if view_dir is not None and mesh and (Path(view_dir) / mesh).is_file():
            triangles[label] = read_stl(Path(view_dir) / mesh)

    boxes = {label: model.bodies[label]["bbox"] for label in labels}
    lo = [min(b[k] for b in boxes.values()) for k in range(3)]
    hi = [max(b[k] for b in boxes.values()) for k in range(3, 6)]
    span = [max(hi[k] - lo[k], 1e-9) for k in range(3)]
    zScale = min(max(0.35 * max(span[0], span[1]) / span[2], 1.0), 80.0)
    rank = {"air": 0, "dielectric": 1, "metal": 2, "port": 3}

    def panel(ox, title, a, b, order, shade_by_height):
        """Project on axes a (right) and b (up); `order` sorts the bodies back to front."""
        fb = zScale if b == 2 else 1.0
        scale = min((PANEL_W - 2 * PAD) / span[a], (PANEL_H - 2 * PAD) / (span[b] * fb))
        x0 = ox + (PANEL_W - span[a] * scale) / 2
        y0 = PANEL_H - (PANEL_H - span[b] * fb * scale) / 2

        def point(p):
            return f"{x0 + (p[a] - lo[a]) * scale:.1f},{y0 - (p[b] - lo[b]) * fb * scale:.1f}"

        parts = [f'<text x="{ox + PAD}" y="18" class="t">{escape(title)}</text>']
        for label in sorted(labels, key=lambda l: order(boxes[l], rank[kind(l)])):
            k = kind(label)
            # top view: lower metals darker, so a patch is not lost on a ground plane of the same colour
            brightness = 0.5 + 0.5 * (boxes[label][5] - lo[2]) / span[2] if shade_by_height and k == "metal" else 1.0
            fill = FILL[k] if k in ("port", "air") else css_color(model.bodies[label].get("color"), FILL[k], brightness)
            parts.append(f'<g opacity="{OPACITY[k]}" fill="{fill}" stroke="{fill}" stroke-width="0.4">'
                         f'<title>{escape(label)} ({k})</title>')
            tris = triangles.get(label)
            if tris:
                for tri in tris:
                    parts.append(f'<polygon points="{" ".join(point(p) for p in tri)}"/>')
            else:
                box = boxes[label]
                c = [list(box[:3]), list(box[:3]), list(box[3:]), list(box[3:])]
                c[1][a], c[3][a] = box[a + 3], box[a]
                c[1][b], c[3][b] = box[b], box[b + 3]
                parts.append(f'<polygon points="{" ".join(point(p) for p in c)}"/>')
            # outline of box-shaped bodies, so same-coloured neighbours (line on ground) stay apart
            parts.append(f'<rect x="{x0 + (boxes[label][a] - lo[a]) * scale:.1f}" '
                         f'y="{y0 - (boxes[label][b + 3] - lo[b]) * fb * scale:.1f}" '
                         f'width="{max((boxes[label][a + 3] - boxes[label][a]) * scale, 0.8):.1f}" '
                         f'height="{max((boxes[label][b + 3] - boxes[label][b]) * fb * scale, 0.8):.1f}" '
                         'fill="none" stroke="#0f172a" stroke-opacity="0.35" stroke-width="0.6"/>'
                         if not tris or model.bodies[label].get("is_box") or (shade_by_height and k == "metal") else "")
            parts.append("</g>")
        return parts

    # side view looks along +y: far (large y) first. Top view: dielectrics under the metals (seen
    # through a substrate they only mix colours), metals from low to high, outlined and shaded
    side = panel(0, f"side view (x–z), height ×{zScale:.0f}", 0, 2,
                 order=lambda box, r: (-(box[1] + box[4]), r), shade_by_height=False)
    top = panel(PANEL_W, "top view (x–y), lower metals darker", 0, 1, order=lambda box, r: (r, box[5]),
                shade_by_height=True)
    legend = []
    for k, (name, color) in enumerate([("metal", FILL["metal"]), ("dielectric (see-through)", FILL["dielectric"]),
                                       ("port", FILL["port"])]):
        legend.append(f'<rect x="{PAD + k * 150}" y="{PANEL_H + 8}" width="14" height="10" fill="{color}"/>'
                      f'<text x="{PAD + k * 150 + 20}" y="{PANEL_H + 17}" class="l">{name}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {2 * PANEL_W} {PANEL_H + 26}" '
            f'width="{2 * PANEL_W}" height="{PANEL_H + 26}" font-family="sans-serif">'
            '<style>.t{font-size:12px;fill:#334155}.l{font-size:11px;fill:#475569}</style>'
            '<rect width="100%" height="100%" fill="#f8fafc"/>' + "".join(side + top + legend) + "</svg>")
