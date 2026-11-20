"""Read and write OTTER settings .ini files without FreeCAD or Qt.

The add-on saves its settings with QSettings in INI format. The codec here follows Qt's
escaping (percent-encoded keys, quoted and backslash-escaped values) so that a file written
here reads back in QSettings exactly as written; tests/test_settings.py checks that against
Qt itself.

OtterSettings adds what the web app needs on top: solver, ports, and which FreeCAD objects
are assigned to which material, grid or port.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------------------------
#   QSettings INI codec
# ---------------------------------------------------------------------------------------------

_KEY_SAFE = re.compile(r"[A-Za-z0-9_.\-]")
_ESCAPES_OUT = {"\0": "\\0", "\a": "\\a", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r",
                "\t": "\\t", "\v": "\\v", '"': '\\"', "\\": "\\\\"}
_ESCAPES_IN = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
               '"': '"', "'": "'", "\\": "\\", "?": "?"}


def escape_key(key: str) -> str:
    out = []
    for ch in key:
        if ch == "/":
            out.append("\\")
        elif _KEY_SAFE.match(ch):
            out.append(ch)
        elif ord(ch) <= 0xFF:
            out.append(f"%{ord(ch):02X}")
        else:
            out.append(f"%U{ord(ch):04X}")
    return "".join(out)


def unescape_key(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            out.append("/")
            i += 1
        elif ch == "%" and text[i + 1:i + 2] == "U" and re.fullmatch(r"[0-9A-Fa-f]{4}", text[i + 2:i + 6] or ""):
            out.append(chr(int(text[i + 2:i + 6], 16)))
            i += 6
        elif ch == "%" and re.fullmatch(r"[0-9A-Fa-f]{2}", text[i + 1:i + 3] or ""):
            out.append(chr(int(text[i + 1:i + 3], 16)))
            i += 3
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def escape_value(value) -> str:
    """One value as Qt writes it; lists become comma-separated values."""
    if isinstance(value, (list, tuple)):
        return ", ".join(escape_value(v) for v in value) if len(value) != 1 else escape_value(value[0])
    if isinstance(value, bool):
        value = "true" if value else "false"
    value = str(value)
    if value.startswith("@"):
        value = "@" + value

    out, needsQuotes, escapeNextIfHex = [], False, False
    for ch in value:
        if ch in ";,=":
            needsQuotes = True
        if escapeNextIfHex and ch in "0123456789abcdefABCDEF":
            out.append(f"\\x{ord(ch):x}")
            continue
        escapeNextIfHex = False
        if ch in _ESCAPES_OUT:
            out.append(_ESCAPES_OUT[ch])
            escapeNextIfHex = ch == "\0"
        elif ord(ch) <= 0x1F:
            out.append(f"\\x{ord(ch):x}")
            escapeNextIfHex = True
        else:
            out.append(ch)
    text = "".join(out)
    if needsQuotes or (text and (text[0] == " " or text[-1] == " ")):
        text = f'"{text}"'
    return text


def unescape_value(text: str):
    """A value as QSettings returns it: a str, or a list for comma-separated values."""
    items, current, inQuotes, i = [], [], False, 0
    pendingSpaces = 0  # unquoted spaces are dropped at the ends of an element only
    started = False
    while i < len(text):
        ch = text[i]
        if ch == '"':
            inQuotes = not inQuotes
            started = True
            current.append(" " * pendingSpaces)
            pendingSpaces = 0
            i += 1
        elif ch == "\\" and i + 1 < len(text):
            current.append(" " * pendingSpaces)
            pendingSpaces = 0
            started = True
            nxt = text[i + 1]
            if nxt == "x":
                digits = re.match(r"[0-9A-Fa-f]{1,4}", text[i + 2:])
                current.append(chr(int(digits.group(0), 16)) if digits else "x")
                i += 2 + (len(digits.group(0)) if digits else 0)
            elif nxt in "01234567":
                digits = re.match(r"[0-7]{1,3}", text[i + 1:]).group(0)
                current.append(chr(int(digits, 8)))
                i += 1 + len(digits)
            else:
                current.append(_ESCAPES_IN.get(nxt, nxt))
                i += 2
        elif ch == "," and not inQuotes:
            items.append("".join(current))
            current, pendingSpaces, started = [], 0, False
            i += 1
        elif ch in " \t" and not inQuotes:
            if started:
                pendingSpaces += 1
            i += 1
        else:
            current.append(" " * pendingSpaces)
            pendingSpaces = 0
            started = True
            current.append(ch)
            i += 1
    items.append("".join(current))

    values = []
    for item in items:
        if item.startswith("@@"):
            item = item[1:]
        elif item == "@Invalid()":
            item = None
        values.append(item)
    return values[0] if len(values) == 1 else values


def parse_ini(text: str) -> dict[str, dict]:
    """{section: {key: value}} in file order; keys outside a section go under "General"."""
    sections: dict[str, dict] = {}
    current = sections.setdefault("General", {})
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = sections.setdefault(unescape_key(stripped[1:-1]), {})
        elif "=" in line:
            key, raw = line.split("=", 1)
            current[unescape_key(key.strip())] = unescape_value(raw.strip())
    if not sections["General"]:
        del sections["General"]
    return sections


def dump_ini(sections: dict[str, dict]) -> str:
    blocks = []
    for name, keys in sections.items():
        lines = [f"[{escape_key(name)}]"]
        lines += [f"{escape_key(key)}={escape_value(value)}" for key, value in keys.items()]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------------------------
#   OTTER settings
# ---------------------------------------------------------------------------------------------

SOLVERS = ("openEMS", "EMerge", "Palace")
CATEGORIES = ("Material", "Grid", "Port", "Excitation", "Probe", "LumpedPart", "BoundaryCondition")

# a lumped port as the add-on writes it, used when the file has no port to copy from
DEFAULT_LUMPED_PORT = {
    "R": "50", "RUnits": "Ohm", "direction": "z", "directionCustomVector": "[0.0, 0.0, 0.0]",
    "excitationAmplitude": "1", "infiniteResistance": "false", "isActive": "false", "type": "lumped",
}


@dataclass
class Port:
    name: str
    type: str
    active: bool  # excited port; the others only receive
    resistance: str  # with units, e.g. "50 Ohm"; empty for non-lumped ports
    direction: str
    objects: list[str]  # labels of the FreeCAD objects assigned to the port


class OtterSettings:
    """An OTTER settings file. Section names are stored unescaped, values as strings."""

    def __init__(self, sections: dict[str, dict]):
        self.sections = sections

    @classmethod
    def load(cls, path) -> "OtterSettings":
        return cls(parse_ini(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def loads(cls, text: str) -> "OtterSettings":
        return cls(parse_ini(text))

    def dumps(self) -> str:
        return dump_ini(self.sections)

    def save(self, path):
        Path(path).write_text(self.dumps(), encoding="utf-8")

    # -- sections ------------------------------------------------------------------------------

    def _sections(self, prefix):
        """(name, keys) of sections like PORT-port1, name being the part after the dash."""
        return [(name.split("-", 1)[1], keys) for name, keys in self.sections.items()
                if name.split("-", 1)[0] == prefix and "-" in name]

    def json_value(self, section: str, key: str, default=None):
        raw = self.sections.get(section, {}).get(key)
        return default if raw is None else json.loads(raw)

    # -- solver --------------------------------------------------------------------------------

    @property
    def solver(self) -> str | None:
        found = self._sections("SOLVER")
        return found[0][1].get("solver") if found else None

    @solver.setter
    def solver(self, value: str):
        if value not in SOLVERS:
            raise ValueError(f"solver must be one of {SOLVERS}")
        found = [name for name in self.sections if name.startswith("SOLVER-")]
        section = found[0] if found else "SOLVER-settings1"
        self.sections.setdefault(section, {"engine": ""})["solver"] = value

    # -- object assignments --------------------------------------------------------------------

    def assignments(self, category: str | None = None) -> list[tuple[str, str, str]]:
        """(category, parent, object label) for every assigned FreeCAD object."""
        found = []
        for name, keys in self.sections.items():
            if re.match(r"_OBJECT\d+-", name):
                label = name.split("-", 1)[1]
                if category is None or keys.get("category") == category:
                    found.append((keys.get("category"), keys.get("parent"), label))
        return found

    def assign(self, category: str, parent: str, label: str, freecad_name: str = ""):
        """Assign FreeCAD object `label` to e.g. ("Port", "port3"); the add-on finds it by label."""
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        if (category, parent, label) in self.assignments():
            return
        used = [int(m.group(1)) for name in self.sections if (m := re.match(r"_OBJECT(\d+)-", name))]
        number = max(used, default=-1) + 1
        self.sections[f"_OBJECT{number}-{label}"] = {
            "category": category, "freeCadId": freecad_name or label, "parent": parent, "type": "FreeCadObj"}
        if category in ("Material", "Port", "LumpedPart", "BoundaryCondition"):
            self._add_priority("PRIORITYLIST-OBJECTS", f"{category}, {parent}, {label}", first=category == "Port")
        elif category == "Grid":
            self._add_priority("PRIORITYLIST-MESH", f"{category}, {parent}, {label}", first=False)

    def unassign(self, category: str, parent: str, label: str | None = None):
        """Remove assignments of `parent` (all its objects, or just `label`) and their priorities."""
        for name, keys in list(self.sections.items()):
            if re.match(r"_OBJECT\d+-", name) and keys.get("category") == category and keys.get("parent") == parent:
                if label is None or name.split("-", 1)[1] == label:
                    del self.sections[name]
        prefix = f"{category}, {parent}, "
        for listName in ("PRIORITYLIST-OBJECTS", "PRIORITYLIST-MESH"):
            entries = self.sections.get(listName, {})
            for key in list(entries):
                if key.startswith(prefix) and (label is None or key == prefix + label):
                    del entries[key]

    def _add_priority(self, listName: str, entry: str, first: bool):
        """Insert into a priority list (lower number = higher priority), renumbered in steps of 10."""
        entries = self.sections.setdefault(listName, {})
        ordered = sorted(entries, key=lambda key: int(entries[key]))
        if entry in ordered:
            return
        if first:
            # after the existing ports, above everything else, like the add-on adds them
            ports = [key for key in ordered if key.startswith("Port, ")]
            ordered.insert(len(ports), entry)
        else:
            ordered.append(entry)
        entries.clear()
        entries.update({key: str(10 * k) for k, key in enumerate(ordered)})

    # -- ports ---------------------------------------------------------------------------------

    def ports(self) -> list[Port]:
        objects = self.assignments("Port")
        result = []
        for name, keys in self._sections("PORT"):
            resistance = f"{keys.get('R', '')} {keys.get('RUnits', '')}".strip() if keys.get("type") == "lumped" else ""
            result.append(Port(name=name, type=keys.get("type", ""), active=keys.get("isActive") == "true",
                               resistance=resistance, direction=keys.get("direction", ""),
                               objects=[label for _, parent, label in objects if parent == name]))
        return result

    def add_lumped_port(self, name: str, object_label: str, resistance_ohm: float = 50, direction: str = "z",
                        active: bool = False):
        """New lumped port `name` on the FreeCAD object `object_label`, copying the file's port fields."""
        if f"PORT-{name}" in self.sections:
            raise ValueError(f"port '{name}' already exists")
        template = next((dict(keys) for _, keys in self._sections("PORT") if keys.get("type") == "lumped"),
                        dict(DEFAULT_LUMPED_PORT))
        template.update(R=f"{resistance_ohm:g}", RUnits="Ohm", direction=direction,
                        isActive="true" if active else "false", type="lumped")
        self.sections[f"PORT-{name}"] = template
        self._move_before_materials(f"PORT-{name}")
        self.assign("Port", name, object_label)

    def remove_port(self, name: str):
        self.sections.pop(f"PORT-{name}", None)
        self.unassign("Port", name)

    def set_active_port(self, name: str):
        """Excite only this port."""
        for portName, keys in self._sections("PORT"):
            keys["isActive"] = "true" if portName == name else "false"

    def _move_before_materials(self, section: str):
        # keeps the file in the add-on's order (ports next to the other ports); readers don't care
        keys = self.sections.pop(section)
        names = list(self.sections)
        portIdx = [k for k, n in enumerate(names) if n.startswith("PORT-")]
        at = portIdx[-1] + 1 if portIdx else len(names)
        items = list(self.sections.items())
        items.insert(at, (section, keys))
        self.sections = dict(items)

    # -- other lists ---------------------------------------------------------------------------

    def materials(self) -> list[str]:
        return [name for name, _ in self._sections("MATERIAL")]

    def grids(self) -> list[str]:
        return [name for name, _ in self._sections("GRID")]
