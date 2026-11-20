"""Where a lumped port between two layers goes, as a rectangle the headless run turns into a face.

A port sits on one side of a body (usually the signal conductor's end), as wide as that body,
and spans the gap from the top of the lower layer to the bottom of the upper layer, the way
the ports in OTTER's Test models are drawn (e.g. line.FCStd: port1_face at the line's x start,
line width in y, z from ground top 0 to line bottom 0.254).
"""
from dataclasses import dataclass

from otter_llm.model import Model

SIDES = ("xmin", "xmax", "ymin", "ymax")


@dataclass
class PortSpec:
    name: str  # port name in the settings, e.g. "port3"
    lower: str  # layer names from the model
    upper: str
    body: str  # label of a body on the lower or upper layer, the port sits on its side
    side: str  # one of SIDES
    resistance_ohm: float = 50
    active: bool = False

    @property
    def face_label(self) -> str:
        return f"{self.name}_face"


def port_face(model: Model, spec: PortSpec) -> list[list[float]]:
    """The four corners of the port face, in model units."""
    layers = {layer.name: layer for layer in model.layers}
    for name in (spec.lower, spec.upper):
        if name not in layers:
            raise ValueError(f"'{name}' is not a layer of {model.file}, layers: {list(layers)}")
    lower, upper = layers[spec.lower], layers[spec.upper]
    if spec.body not in lower.bodies + upper.bodies:
        raise ValueError(f"body '{spec.body}' is not on layer '{spec.lower}' or '{spec.upper}'")
    if spec.side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")

    z0, z1 = lower.zmax, upper.zmin
    if z1 - z0 <= 1e-9 * max(abs(z0), abs(z1), 1.0):
        raise ValueError(f"layer '{spec.upper}' does not lie above '{spec.lower}' "
                         f"(top of lower {z0:g}, bottom of upper {z1:g}), a port needs a gap between them")

    xmin, ymin, _, xmax, ymax, _ = model.bodies[spec.body]["bbox"]
    if spec.side in ("xmin", "xmax"):
        x = xmin if spec.side == "xmin" else xmax
        return [[x, ymin, z0], [x, ymax, z0], [x, ymax, z1], [x, ymin, z1]]
    y = ymin if spec.side == "ymin" else ymax
    return [[xmin, y, z0], [xmax, y, z0], [xmax, y, z1], [xmin, y, z1]]


def check_new_port(model: Model, settings, spec: PortSpec):
    """Raise ValueError when the port's name or face label is already taken."""
    if spec.name in [port.name for port in settings.ports()]:
        raise ValueError(f"port '{spec.name}' already exists")
    if spec.face_label in model.bodies:
        # FreeCAD would rename the new face to <label>001 and the settings would miss it
        raise ValueError(f"the model already has an object labelled '{spec.face_label}'")
