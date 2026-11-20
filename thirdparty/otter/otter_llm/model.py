"""Read a .FCStd and describe it as a layer stack: which bodies, at which heights, in which order.

FreeCAD lists the bodies (headless/read_model.py); building the stack is plain Python here, so
it can be tested without FreeCAD.

Bodies with the same z range form a group. A group that holds no other group is a *layer*
(metal, substrate, a sheet at one height). A group whose z range holds layers is a *spanning*
body: a pad or via from substrate to line, a bondwire, a chip's SiO2 around its metals.
Layers whose ranges partly overlap are listed as "overlaps", usually two parts sitting at
slightly different heights.
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from otter_llm.freecad import run_in_freecad


@dataclass
class Layer:
    name: str  # label of the body with the largest footprint
    zmin: float
    zmax: float
    bodies: list[str]  # labels, largest footprint first
    contains: list[str] = field(default_factory=list)  # spanning only: layers inside its z range, bottom to top
    overlaps: list[str] = field(default_factory=list)  # layers only: layers whose z range partly overlaps

    @property
    def thickness(self) -> float:
        return self.zmax - self.zmin


@dataclass
class Model:
    file: str
    unit: str
    layers: list[Layer]  # bottom to top
    spanning: list[Layer]  # bottom to top
    airbox: str | None  # solid enclosing everything else
    ports: list[str]  # vertical sheets and bodies labelled "port"
    construction: list[str]  # bodies another object is built from (extrusion base, ...)
    other: list[str]  # wires, tilted or curved sheets
    bodies: dict[str, dict]  # label -> facts from FreeCAD
    warnings: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("layers", "spanning"):
            for layer, layerData in zip(getattr(self, key), data[key]):
                layerData["thickness"] = layer.thickness
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Model":
        data = dict(data)
        for key in ("layers", "spanning"):
            data[key] = [Layer(**{k: v for k, v in layer.items() if k != "thickness"}) for layer in data[key]]
        return cls(**data)


def read_model(fcstd, view_dir=None, timeout=300) -> Model:
    """Open the .FCStd in FreeCAD (a copy, never the original) and build its layer stack.

    view_dir: also write an STL per body there (bodies[label]["mesh"] is the file name).
    """
    fcstd = Path(fcstd).resolve()
    with tempfile.TemporaryDirectory(prefix="otter_llm_") as work:
        work = Path(work)
        shutil.copy2(fcstd, work / fcstd.name)
        job = {"fcstd": str(work / fcstd.name)}
        writable = []
        if view_dir is not None:
            Path(view_dir).mkdir(parents=True, exist_ok=True)
            job["view_dir"] = str(Path(view_dir).resolve())
            writable.append(Path(view_dir).resolve())
        run = run_in_freecad("read_model.py", job, work, timeout=timeout, writable=writable)

    if run.error:
        raise RuntimeError(f"reading {fcstd.name} failed:\n{run.error}\n{run.log[-2000:]}")
    return build_model(run.result["file"], run.result["unit"], run.result["bodies"], run.result["warnings"])


def build_model(file, unit, bodies, warnings=()) -> Model:
    """Sort FreeCAD's body list into layers, spanning bodies, airbox, ports and the rest."""
    byLabel = {body["label"]: body for body in bodies}
    counts = {}
    for body in bodies:
        counts[body["label"]] = counts.get(body["label"], 0) + 1
    warnings = list(warnings) + [
        f"{n} bodies share the label '{label}': OTTER exports them as one STL with one material, rename them in "
        "FreeCAD if they are different materials" for label, n in counts.items() if n > 1]
    construction = [b["label"] for b in bodies if b["used_by"]]

    stackBodies, ports, other = [], [], []
    for body in bodies:
        if body["used_by"]:
            continue
        if is_port_label(body["label"]) or (body["kind"] == "sheet" and body["flat_axis"] in ("x", "y")):
            ports.append(body)
        elif body["kind"] == "solid" or (body["kind"] == "sheet" and body["flat_axis"] == "z"):
            stackBodies.append(body)
        else:
            other.append(body)

    airbox = find_airbox(stackBodies, ports)
    if airbox is not None:
        stackBodies = [b for b in stackBodies if b["label"] != airbox]

    layers, spanning = build_layers(stackBodies)
    return Model(file=file, unit=unit, layers=layers, spanning=spanning, airbox=airbox,
                 ports=[b["label"] for b in ports], construction=construction,
                 other=[b["label"] for b in other], bodies=byLabel, warnings=list(warnings))


def is_port_label(label: str) -> bool:
    """ "port" as a word of the label: Port_1, port2, LumpedPort, RF ports; not import_5 (a STEP import), Export, Support."""
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", label).lower()
    return re.search(r"(?<![a-z])ports?(?![a-z])", words) is not None


def find_airbox(stackBodies, ports) -> str | None:
    """The largest solid whose box encloses every other body, if there is one."""
    solids = sorted((b for b in stackBodies if b["kind"] == "solid"), key=lambda b: b["volume"], reverse=True)
    if len(solids) < 2:
        return None
    biggest = solids[0]
    if all(encloses(biggest["bbox"], b["bbox"]) for b in stackBodies + ports if b is not biggest):
        return biggest["label"]
    return None


def encloses(outer, inner) -> bool:
    return all(outer[k] <= inner[k] for k in range(3)) and all(outer[k] >= inner[k] for k in range(3, 6))


def sandwiched(box, metalBoxes, tol) -> bool:
    """Metal ends exactly at the box's bottom face and metal starts exactly at its top face, overlapping it in
    x, y: a solder joint or die attach between a land and a lead (or a capacitor dielectric)."""
    def metalEndsAt(z, side):  # side 5: metal below ends at z, side 2: metal above starts at z
        return any(abs(other[side] - z) <= tol and all(box[k] <= other[k + 3] + tol and other[k] <= box[k + 3] + tol
                                                       for k in range(2)) for other in metalBoxes if other is not box)
    return metalEndsAt(box[2], 5) and metalEndsAt(box[5], 2)


def footprint(body) -> float:
    xmin, ymin, _, xmax, ymax, _ = body["bbox"]
    return (xmax - xmin) * (ymax - ymin)


def build_layers(bodies) -> tuple[list[Layer], list[Layer]]:
    """Group bodies with the same z range, split the groups into layers and spanning bodies.
    Bodies in different FreeCAD containers (App::Part) are different groups even at the same height: a chip's
    via bodies (container Chip_Metall) and the oxide around them (Chip_SiO2) need different materials."""
    if not bodies:
        return [], []
    zLow = min(b["bbox"][2] for b in bodies)
    zHigh = max(b["bbox"][5] for b in bodies)
    tol = 1e-6 * max(zHigh - zLow, 1e-9)

    groups = []  # [zmin, zmax, container, [bodies]]
    for body in sorted(bodies, key=lambda b: (b["bbox"][2], b["bbox"][5])):
        zmin, zmax, container = body["bbox"][2], body["bbox"][5], body.get("container")
        for group in groups:
            if abs(group[0] - zmin) <= tol and abs(group[1] - zmax) <= tol and group[2] == container:
                group[3].append(body)
                break
        else:
            groups.append([zmin, zmax, container, [body]])

    allGroups, names = [], set()
    for zmin, zmax, container, members in groups:
        members.sort(key=footprint, reverse=True)
        name = members[0]["label"]
        if name in names:  # several bodies share a label (FreeCAD allows it): the group name must stay unique
            name = next(f"{name} ({n})" for n in range(2, len(groups) + 2) if f"{name} ({n})" not in names)
        names.add(name)
        allGroups.append(Layer(name=name, zmin=zmin, zmax=zmax, bodies=list(dict.fromkeys(b["label"] for b in members))))

    spanning = [g for g in allGroups if any(contains(g, other, tol) for other in allGroups if other is not g)]
    layers = [g for g in allGroups if g not in spanning]

    for group in spanning:
        group.contains = [layer.name for layer in layers if contains(group, layer, tol)]
    for layer in layers:
        layer.overlaps = [other.name for other in layers if other is not layer and overlap(layer, other) > tol]
    return layers, spanning


def contains(outer: Layer, inner: Layer, tol) -> bool:
    if abs(outer.zmin - inner.zmin) <= tol and abs(outer.zmax - inner.zmax) <= tol:
        return False
    if not (outer.zmin - tol <= inner.zmin and inner.zmax <= outer.zmax + tol):
        return False
    if inner.thickness <= tol:
        # a sheet on the top or bottom face only touches the group
        return outer.zmin + tol < inner.zmin < outer.zmax - tol
    return True


def overlap(a: Layer, b: Layer) -> float:
    return min(a.zmax, b.zmax) - max(a.zmin, b.zmin)


def format_stack(model: Model) -> str:
    """The stack as a text table, top layer first like a PCB stack-up drawing."""
    def row(group, width):
        count = len(group.bodies)
        return (f"  {group.name:{width}}  z {group.zmin:9.4g} .. {group.zmax:<9.4g} "
                f"t={group.thickness:<9.4g} {count} bod{'y' if count == 1 else 'ies'}")

    width = max([len(g.name) for g in model.layers + model.spanning] + [5])
    lines = [f"{model.file}  (lengths in {model.unit})", "", "  LAYERS (top first)"]
    for layer in reversed(model.layers):
        lines.append(row(layer, width))
        if layer.bodies[1:]:
            lines.append(f"  {'':{width}}    also: {', '.join(layer.bodies[1:])}")
        if layer.overlaps:
            lines.append(f"  {'':{width}}    overlaps: {', '.join(layer.overlaps)}")
    if model.spanning:
        lines += ["", "  SPANNING BODIES"]
        for group in model.spanning:
            lines.append(row(group, width))
            if group.bodies[1:]:
                lines.append(f"  {'':{width}}    also: {', '.join(group.bodies[1:])}")
            lines.append(f"  {'':{width}}    through: {' -> '.join(group.contains)}")
    lines.append("")
    lines.append(f"  airbox:       {model.airbox or '-'}")
    lines.append(f"  ports:        {', '.join(model.ports) or '-'}")
    lines.append(f"  construction: {', '.join(model.construction) or '-'}")
    if model.other:
        lines.append(f"  other:        {', '.join(model.other)}")
    for warning in model.warnings:
        lines.append(f"  WARNING: {warning}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Show the layer stack of a FreeCAD model.")
    parser.add_argument("fcstd", help="FreeCAD model (.FCStd)")
    parser.add_argument("--json", help="also write the full description to this JSON file")
    args = parser.parse_args(argv)

    model = read_model(args.fcstd)
    print(format_stack(model))
    if args.json:
        Path(args.json).write_text(json.dumps(model.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
