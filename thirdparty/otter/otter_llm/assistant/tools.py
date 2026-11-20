"""What the assistant may do: change the project's settings through these tools, nothing else.

It never writes or edits solver scripts; the OTTER add-on's tested generators do that from
the settings. So a wrong or manipulated answer can at worst choose a bad setting, which the
checks and the user see, and cannot put code into a user's script.
"""
import json

from otter_llm.checks import run_checks
from otter_llm.ports import SIDES, PortSpec
from otter_llm.project import Project
from otter_llm.settings import SOLVERS
from otter_llm.starter import DENSITY, MATERIALS, NOT_SIMULATED

NOT_SIMULATED_NAME = "not simulated"


def _tool(name, description, properties, required=None):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties) if required is None else required,
                             "additionalProperties": False}}


TOOLS = [
    _tool("set_solver", "Choose the solver the package is written for.",
          {"solver": {"type": "string", "enum": list(SOLVERS)}}),
    _tool("set_frequency", "Frequency range of interest in GHz; mesh sizes and excitation follow from it. "
          "Only for models uploaded without an OTTER .ini.",
          {"fmin_ghz": {"type": "number"}, "fmax_ghz": {"type": "number"}}),
    _tool("set_material", "Material of one layer or spanning body (name as in the stack). Only for models "
          "uploaded without an OTTER .ini.",
          {"group": {"type": "string"},
           "material": {"type": "string", "enum": list(MATERIALS) + [NOT_SIMULATED_NAME]}}),
    _tool("set_mesh", "Mesh density for every layer (coarse: quick look, normal: the tested rules, fine: convergence "
          "check) and the smallest cell in um no rule goes below (openEMS: the smallest cell sets the timestep). "
          "Only for models uploaded without an OTTER .ini.",
          {"density": {"type": "string", "enum": list(DENSITY)}, "smallest_cell_um": {"type": "number"}}),
    _tool("set_layer_cell", "Largest mesh cell of one layer or spanning body in um, replacing the density rule: "
          "xy_um across the layer (conductors), z_um through it. 0 keeps or restores the rule. openEMS uses both, "
          "Palace only xy_um (element size). Only for models uploaded without an OTTER .ini.",
          {"group": {"type": "string"}, "xy_um": {"type": "number"}, "z_um": {"type": "number"}}),
    _tool("add_port_between_layers", "Add a lumped port spanning the gap from the top of the lower layer to the "
          "bottom of the upper layer, on one side of a body of one of the two layers, as wide as that body.",
          {"lower": {"type": "string", "description": "lower (reference) layer"},
           "upper": {"type": "string", "description": "upper (signal) layer"},
           "body": {"type": "string", "description": "body on the lower or upper layer the port sits on"},
           "side": {"type": "string", "enum": list(SIDES)},
           "resistance_ohm": {"type": "number"},
           "excited": {"type": "boolean"}}),
    _tool("add_port_on_face", "Add a lumped port on a port face or box already drawn in the model.",
          {"face": {"type": "string"}, "resistance_ohm": {"type": "number"}, "excited": {"type": "boolean"}}),
    _tool("remove_port", "Remove a port.", {"name": {"type": "string"}}),
    _tool("set_excited_port", "Excite this port; the others only receive.", {"name": {"type": "string"}}),
    _tool("search_findings", "Search OTTER's debugging findings, solver change logs and user guides.",
          {"query": {"type": "string"}}),
    _tool("run_checks", "Run the checks again and list the problems.", {}),
]

# Ollama takes OpenAI-style function definitions
OLLAMA_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in TOOLS]
GEMINI_TOOLS = [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                           "parametersJsonSchema": t["input_schema"]} for t in TOOLS]}]


class ToolError(Exception):
    pass


class ToolBox:
    def __init__(self, project: Project, knowledge):
        self.project = project
        self.knowledge = knowledge

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(result text, whether the settings changed); errors come back as text for the model.

        After a change the result ends with the checks, so the model reports them, not a guess.
        """
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            return f"error: no tool '{name}'", False
        try:
            result, changed = method(**args)
        except (ToolError, ValueError, TypeError, KeyError) as error:
            return f"error: {error}", False
        if changed:
            result += "\nchecks now:\n" + format_checks(self.project)
        return result, changed

    def _starter_only(self):
        if self.project.design is None:
            raise ToolError("this model was uploaded with an OTTER .ini; materials and frequency are set in that "
                            "file (FreeCAD add-on), not here")

    def tool_set_solver(self, solver):
        if solver not in SOLVERS:
            raise ToolError(f"solver must be one of {SOLVERS}")
        self.project.set_solver(solver)
        return f"ok: solver is {solver}", True

    def tool_set_frequency(self, fmin_ghz, fmax_ghz):
        self._starter_only()
        self.project.set_frequency(float(fmin_ghz), float(fmax_ghz))
        return f"ok: {fmin_ghz:g}–{fmax_ghz:g} GHz", True

    def tool_set_material(self, group, material):
        self._starter_only()
        names = [g.name for g in self.project.model.layers + self.project.model.spanning]
        if group not in names:
            raise ToolError(f"no layer or spanning body '{group}', they are: {names}")
        value = NOT_SIMULATED if material == NOT_SIMULATED_NAME else material
        if value != NOT_SIMULATED and value not in MATERIALS:
            raise ToolError(f"material must be one of {list(MATERIALS)} or '{NOT_SIMULATED_NAME}'")
        self.project.set_material(group, value)
        return f"ok: {group} is {material}", True

    def tool_set_mesh(self, density, smallest_cell_um):
        self._starter_only()
        if density not in DENSITY:
            raise ToolError(f"density must be one of {list(DENSITY)}")
        self.project.set_mesh(density=density, min_cell_um=float(smallest_cell_um))
        return f"ok: {density} mesh, smallest cell {smallest_cell_um:g} um" + mesh_text(self.project), True

    def tool_set_layer_cell(self, group, xy_um, z_um):
        self._starter_only()
        self.project.set_cell(group, float(xy_um) or None, float(z_um) or None)
        cells = self.project.cells_of(group)
        return (f"ok: {group} cells now " + (", ".join(f"{k} {v:g} um" for k, v in cells.items()) or "none")
                + mesh_text(self.project)), True

    def tool_add_port_between_layers(self, lower, upper, body, side, resistance_ohm, excited):
        name = self.project.next_port_name()
        self.project.add_port(PortSpec(name, lower=lower, upper=upper, body=body, side=side,
                                       resistance_ohm=float(resistance_ohm), active=bool(excited)))
        return f"ok: {name} between {lower} and {upper} at the {side} side of {body}, {resistance_ohm:g} Ω", True

    def tool_add_port_on_face(self, face, resistance_ohm, excited):
        name = self.project.next_port_name(new_face=False)
        self.project.add_existing_port(face, name, resistance_ohm=float(resistance_ohm), active=bool(excited))
        return f"ok: {name} on {face}, {resistance_ohm:g} Ω", True

    def tool_remove_port(self, name):
        if name not in [p.name for p in self.project.settings.ports()]:
            raise ToolError(f"no port '{name}'")
        self.project.remove_port(name)
        return f"ok: {name} removed", True

    def tool_set_excited_port(self, name):
        if name not in [p.name for p in self.project.settings.ports()]:
            raise ToolError(f"no port '{name}'")
        self.project.settings.set_active_port(name)
        return f"ok: {name} is excited", True

    def tool_search_findings(self, query):
        from otter_llm.assistant.knowledge import format_sections
        found = self.knowledge.search(query, k=3) if self.knowledge else []
        return (format_sections(found) if found else "nothing found"), False

    def tool_run_checks(self):
        return format_checks(self.project), False


def mesh_text(project: Project) -> str:
    size = project.mesh()
    return f"; openEMS mesh {size.summary()}" if size else ""


def format_checks(project: Project) -> str:
    issues = run_checks(project.model, project.settings, project.new_faces)
    if not issues:
        return "no problems"
    return "\n".join(f"- {i.level}: {i.message}" + (f" ({i.finding})" if i.finding else "") for i in issues)


def describe_project(project: Project) -> str:
    """The current state as compact text for the model, rebuilt every turn."""
    model, settings = project.model, project.settings
    material_of = {label: parent for _, parent, label in settings.assignments("Material")}

    def materials(group):
        return sorted({material_of[b] for b in group.bodies if b in material_of}) or ["none (air)"]

    def containers(group):  # FreeCAD groups (App::Part) the bodies sit in, they often name the material
        names = sorted({model.bodies[b].get("container") for b in group.bodies} - {None, ""})
        return f", FreeCAD group {', '.join(names)}" if names else ""

    lines = [f"Model {model.file}, lengths in mm. Solver: {settings.solver}. "
             + ("Settings: built here from materials and frequency "
                f"({project.design.fmin_ghz:g}–{project.design.fmax_ghz:g} GHz)." if project.design
                else "Settings: uploaded OTTER .ini (materials and mesh fixed in the file).")]
    def cells(group):
        if project.design is None:
            return ""
        sizes = project.cells_of(group.name)
        own = project.design.cells.get(group.name, {})
        return (", cells " + ", ".join(f"{k} {v:g} um{' (set)' if k in own else ''}" for k, v in sizes.items())
                if sizes else "")

    if project.design is not None:
        lines.append(f"Mesh: {project.design.mesh} density, smallest cell {project.design.min_cell_um:g} um"
                     + mesh_text(project) + ".")
    lines.append("Layers, bottom to top (name: z range, thickness, material, bodies, largest mesh cells):")
    for layer in model.layers:
        lines.append(f"- {layer.name}: z {layer.zmin:.4g}..{layer.zmax:.4g}, t={layer.thickness:.4g}, "
                     f"{', '.join(materials(layer))}, bodies {layer.bodies[:6]}{' …' if len(layer.bodies) > 6 else ''}"
                     f"{containers(layer)}{cells(layer)}")
    if model.spanning:
        lines.append("Spanning bodies (through layers):")
        for group in model.spanning:
            lines.append(f"- {group.name}: z {group.zmin:.4g}..{group.zmax:.4g}, {', '.join(materials(group))}, "
                         f"through {' → '.join(group.contains)}{containers(group)}{cells(group)}")
    lines.append(f"Air box: {model.airbox or 'none'}. Port faces drawn in the model: {model.ports or 'none'}.")
    ports = settings.ports()
    lines.append("Ports: " + ("; ".join(f"{p.name} on {', '.join(p.objects)} {p.resistance}"
                                        f"{' EXCITED' if p.active else ''}" for p in ports) if ports else "none"))
    lines.append("Checks:\n" + format_checks(project))
    return "\n".join(lines)


def tool_args(raw) -> dict:
    """Tool arguments arrive as a dict (Claude, Gemini, Ollama) or a JSON string (some Ollama models)."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
