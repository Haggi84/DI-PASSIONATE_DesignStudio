# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Elements as data: the housing outlines the workbench can build, described
by a fixed set of numbers that a person, a form or a language model fills
in — and that is checked before anything is built.

Why data and not code: a model asked to write FreeCAD code for a package
writes code that runs or does not, and when it runs nobody knows whether
the leads overlap. Asked to fill in twenty numbers against a schema
(Ollama constrains its output to the schema, token by token), it can only
produce a spec, and every spec goes through the same checks — here, before
building, and core.housing's analysis after — as one typed into the form.
The spec is also what is kept: a JSON file in the user's library, and a
property on the housing in the document, so an element made by the model
is edited afterwards exactly like one made by hand.

FreeCAD-free (jedec is imported only to build), so it is testable with any
Python.

Geometry rules mirror core.jedec.build (all mm):

  GAP   0.15  paddle edge to finger tips
  WALL  0.10  cavity wall left inside the body edge
  CLR   0.05  least clearance between two leadframe pieces
"""

import json
import math
import os
import re
import time
from dataclasses import dataclass, field

SCHEMA_VERSION = 1
GAP, WALL, CLR = 0.15, 0.10, 0.05
MIN_LEAD = 0.15                 # shortest lead worth building, mm
MIN_SPACE = 0.10                # least space between two leads of one side, mm
STANDARD_PITCHES = (0.35, 0.4, 0.5, 0.65, 0.8, 1.0, 1.27)

FAMILIES = ("QFN", "DFN", "QFP", "SOIC", "TSSOP")
GULL_WING = ("QFP", "SOIC", "TSSOP")

# Which sides carry leads. per_side_x counts the leads on the LEFT and RIGHT
# sides (they run along y); per_side_y those on the BOTTOM and TOP sides.
# This is the convention a model gets wrong most often, so it is spelled out
# in every field description and checked.
SIDES = {"QFN": "four", "QFP": "four", "DFN": "left/right", "SOIC": "bottom/top", "TSSOP": "bottom/top"}


@dataclass(frozen=True)
class Field:
    key: str
    kind: str                   # "float" | "int" | "str" | "enum"
    label: str
    unit: str = ""
    lo: float = None
    hi: float = None
    help: str = ""
    choices: tuple = ()
    sym: str = ""               # the datasheet symbol, as a package drawing labels it


FIELDS = (
    Field("name", "str", "Name", help="Short name with lead count and body size, e.g. 'QFN-24 4x4'."),
    Field("family", "enum", "Family", choices=FAMILIES,
          help="QFN/DFN: no leads outside the body, leadframe flush with the underside. "
               "QFP/SOIC/TSSOP: gull-wing leads."),
    Field("standard", "str", "Outline", help="The JEDEC outline, e.g. 'JEDEC MO-220', or 'custom'."),
    Field("body_x", "float", "Body X", "mm", 0.8, 40.0, "Body size along x (D).", sym="D"),
    Field("body_y", "float", "Body Y", "mm", 0.8, 40.0, "Body size along y (E).", sym="E"),
    Field("height", "float", "Height", "mm", 0.3, 5.0, "Body height (A).", sym="A"),
    Field("pitch", "float", "Pitch", "mm", 0.3, 2.54, "Lead pitch (e).", sym="e"),
    Field("lead_w", "float", "Lead width", "mm", 0.1, 1.0, "Lead width (b).", sym="b"),
    Field("per_side_x", "int", "Leads left/right", "", 0, 64,
          "Number of leads on EACH of the LEFT and RIGHT sides. QFN/QFP: leads/4. DFN: leads/2. "
          "SOIC/TSSOP: 0.", sym="n L/R"),
    Field("per_side_y", "int", "Leads bottom/top", "", 0, 64,
          "Number of leads on EACH of the BOTTOM and TOP sides. QFN/QFP: leads/4. DFN: 0. "
          "SOIC/TSSOP: leads/2.", sym="n B/T"),
    Field("paddle_x", "float", "Paddle X", "mm", 0.2, 30.0, "Exposed die paddle size along x (D2).", sym="D2"),
    Field("paddle_y", "float", "Paddle Y", "mm", 0.2, 30.0, "Exposed die paddle size along y (E2).", sym="E2"),
    Field("finger", "float", "Bond finger", "mm", 0.1, 2.0,
          "Length of each lead left open in the cavity for wire bonding.", sym="Finger"),
    Field("lead_len", "float", "Lead length", "mm", 0.0, 10.0,
          "How far a lead reaches in from the body edge (L for QFN). 0 = up to the paddle.", sym="L"),
    Field("lead_t", "float", "Leadframe thickness", "mm", 0.05, 0.6, "Leadframe thickness.", sym="t"),
    Field("foot", "float", "Foot length", "mm", 0.2, 2.0, "Gull-wing foot length (QFP/SOIC/TSSOP).", sym="Foot"),
    Field("standoff", "float", "Standoff", "mm", 0.0, 0.5, "Body standoff above the board (A1, gull-wing).", sym="A1"),
)
BY_KEY = {f.key: f for f in FIELDS}
NUMERIC = tuple(f.key for f in FIELDS if f.kind in ("float", "int"))

# The family defaults core.jedec uses for what an outline drawing rarely gives.
FAMILY_DEFAULTS = {
    "QFN": dict(standard="JEDEC MO-220", height=0.85, finger=0.40, lead_t=0.20, foot=0.60, standoff=0.10),
    "DFN": dict(standard="JEDEC MO-229", height=0.85, finger=0.35, lead_t=0.20, foot=0.60, standoff=0.10),
    "QFP": dict(standard="JEDEC MS-026", height=1.40, finger=0.80, lead_t=0.127, foot=0.60, standoff=0.10),
    "SOIC": dict(standard="JEDEC MS-012", height=1.45, finger=0.60, lead_t=0.15, foot=0.60, standoff=0.10),
    "TSSOP": dict(standard="JEDEC MO-153", height=1.00, finger=0.60, lead_t=0.15, foot=0.60, standoff=0.10),
}

EXAMPLE = {"name": "QFN-16 4x4", "family": "QFN", "standard": "JEDEC MO-220", "body_x": 4.0, "body_y": 4.0,
           "height": 0.85, "pitch": 0.65, "lead_w": 0.30, "per_side_x": 4, "per_side_y": 4, "paddle_x": 2.3,
           "paddle_y": 2.3, "finger": 0.40, "lead_len": 0.0, "lead_t": 0.20, "foot": 0.60, "standoff": 0.10}


# -- specs as dicts ----------------------------------------------------------------------------

def blank(family="QFN"):
    d = dict(EXAMPLE)
    d.update(FAMILY_DEFAULTS[family], family=family)
    if family == "DFN":
        d.update(per_side_y=0, body_x=3.0, body_y=3.0, paddle_x=1.4, paddle_y=2.2, per_side_x=4)
    elif family in ("SOIC", "TSSOP"):
        d.update(per_side_x=0)
    return d


def normalise(d):
    """A complete spec dict from *d*: missing fields from the family's
    defaults, numbers as numbers, ints rounded. Unknown keys are dropped."""
    family = str(d.get("family") or "QFN").upper()
    family = family if family in FAMILIES else "QFN"
    out = blank(family)
    for f in FIELDS:
        if f.key not in d or d[f.key] is None:
            continue
        v = d[f.key]
        try:
            if f.kind == "float":
                out[f.key] = float(v)
            elif f.kind == "int":
                out[f.key] = int(round(float(v)))
            elif f.kind == "enum":
                out[f.key] = str(v).upper()
            else:
                out[f.key] = str(v).strip()
        except (TypeError, ValueError):
            pass
    out["family"] = family
    return out


def leads(d):
    return 2 * int(d["per_side_x"]) + 2 * int(d["per_side_y"])


def describe(d):
    return (f"{d['name']} — {d['body_x']:g} x {d['body_y']:g} x {d['height']:g} mm, {leads(d)} leads, "
            f"{d['pitch']:g} mm pitch ({d['standard']})")


def to_spec(d):
    """The core.jedec.Spec for *d* (needs FreeCAD)."""
    from . import jedec
    d = normalise(d)
    return jedec.Spec(**{f.key: d[f.key] for f in FIELDS})


def from_spec(spec):
    return normalise({f.key: getattr(spec, f.key, None) for f in FIELDS})


# -- checks ------------------------------------------------------------------------------------

@dataclass
class Issue:
    level: str                  # "error" | "warning" | "info"
    field: str                  # the field to look at ("" for the whole spec)
    message: str
    fix: dict = field(default_factory=dict)     # values that would resolve it

    def __str__(self):
        return f"{self.level}: {self.message}"


def _floor(x, step=0.05):
    return math.floor(x / step + 1e-9) * step


def geometry(d):
    """
    The extents core.jedec.build works with (it calls this), for checks and
    display: half body sizes, where the leads end inside (inner_x for the
    left/right leads, inner_y for bottom/top), and the half span of each
    side's row of leads.

    Leads run from the body edge in to `GAP` short of the paddle, or
    `lead_len` in. On four sides that made the outermost leads of two
    neighbouring sides cross in the corners — 12 of the 25 built-in JEDEC
    packages had overlapping leads, 0.02–0.39 mm³ of shared copper, i.e. two
    pins shorted in the EM model. So where two rows would meet, the leads
    stop `CLR` clear of the other row (`corner_cut` is then True), as a real
    leadframe's leads stop short of the corners.
    """
    hx, hy = d["body_x"] / 2.0, d["body_y"] / 2.0
    inner_x, inner_y = d["paddle_x"] / 2.0 + GAP, d["paddle_y"] / 2.0 + GAP
    if d.get("lead_len", 0) > 0:
        inner_x, inner_y = max(inner_x, hx - d["lead_len"]), max(inner_y, hy - d["lead_len"])
    span = lambda n: (n - 1) / 2.0 * d["pitch"] + d["lead_w"] / 2.0 if n else 0.0  # noqa: E731
    span_lr, span_bt = span(d["per_side_x"]), span(d["per_side_y"])
    corner_cut = False
    if d["per_side_x"] and d["per_side_y"] and span_bt > inner_x - CLR and span_lr > inner_y - CLR:
        inner_x, inner_y = max(inner_x, span_bt + CLR), max(inner_y, span_lr + CLR)
        corner_cut = True
    return dict(hx=hx, hy=hy, inner_x=inner_x, inner_y=inner_y, span_lr=span_lr, span_bt=span_bt,
                corner_cut=corner_cut, len_x=hx - inner_x, len_y=hy - inner_y)


def validate(d):
    """[Issue] for spec dict *d* (normalised first). Errors stop a build:
    the geometry would overlap, leave no lead or no finger. Warnings are
    things a datasheet would rarely say (an odd pitch, a name that
    disagrees with the numbers)."""
    d = normalise(d)
    out = []
    err = lambda f, m, **fix: out.append(Issue("error", f, m, fix))      # noqa: E731
    warn = lambda f, m, **fix: out.append(Issue("warning", f, m, fix))   # noqa: E731

    for f in FIELDS:
        if f.kind in ("float", "int") and f.lo is not None:
            v = d[f.key]
            if not (f.lo <= v <= f.hi):
                err(f.key, f"{f.label} {v:g} {f.unit} is outside {f.lo:g}–{f.hi:g} {f.unit}".strip(),
                    **{f.key: min(max(v, f.lo), f.hi)})
    if not d["name"]:
        err("name", "The element has no name.", name=f"{d['family']}-{leads(d)} {d['body_x']:g}x{d['body_y']:g}")
    if out:
        return out              # the rest assumes sane numbers

    fam, nx, ny = d["family"], d["per_side_x"], d["per_side_y"]
    total = leads(d)
    if fam in ("QFN", "QFP") and (nx == 0 or ny == 0):
        n = max(nx, ny)
        err("per_side_y" if ny == 0 else "per_side_x",
            f"A {fam} has leads on all four sides; left/right {nx}, bottom/top {ny}.", per_side_x=n, per_side_y=n)
    if fam == "DFN" and ny:
        err("per_side_y", "A DFN has leads on the left and right sides only (bottom/top must be 0).",
            per_side_x=nx or ny, per_side_y=0)
    if fam in ("SOIC", "TSSOP") and nx:
        err("per_side_x", f"A {fam} has leads on the bottom and top sides only (left/right must be 0).",
            per_side_x=0, per_side_y=ny or nx)
    if total == 0:
        err("per_side_x", "The element has no leads.")
        return out

    if d["pitch"] - d["lead_w"] < MIN_SPACE - 1e-9:
        err("lead_w", f"Leads {d['lead_w']:g} mm wide at {d['pitch']:g} mm pitch leave "
                      f"{(d['pitch'] - d['lead_w']) * 1000:.0f} µm between them (at least "
                      f"{MIN_SPACE * 1000:.0f} µm).", lead_w=round(d["pitch"] * 0.5, 3))
    if not any(abs(d["pitch"] - p) < 1e-6 for p in STANDARD_PITCHES):
        warn("pitch", f"{d['pitch']:g} mm is not a JEDEC pitch ({', '.join(f'{p:g}' for p in STANDARD_PITCHES)}).")

    g = geometry(d)
    # leads must fit along their side, inside the cavity wall
    for n, span, half, side, key, body_key in ((nx, g["span_lr"], g["hy"], "left/right", "per_side_x", "body_y"),
                                               (ny, g["span_bt"], g["hx"], "bottom/top", "per_side_y", "body_x")):
        if n and span > half - WALL + 1e-9:
            fit = int((2 * (half - WALL) - d["lead_w"]) / d["pitch"]) + 1
            need = math.ceil((2 * (span + 2 * WALL)) * 20) / 20
            err(key, f"{n} leads at {d['pitch']:g} mm pitch span {2 * span:.2f} mm, but the {side} sides "
                     f"leave room for {2 * (half - WALL):.2f} mm ({max(fit, 0)} leads).",
                **{body_key: need})
        elif n and span > half - 2 * WALL + 1e-9:
            warn(key, f"The outermost {side} leads come within {(half - span) * 1000:.0f} µm of the body edge: "
                      "the cavity wall covers part of their fingers.")
    # every side with leads needs lead length outside the paddle (and the corners)
    for n, half, length, key, axis in ((nx, g["hx"], g["len_x"], "paddle_x", "x"),
                                       (ny, g["hy"], g["len_y"], "paddle_y", "y")):
        if n and length < MIN_LEAD - 1e-9:
            if g["corner_cut"]:
                err("per_side_x", f"Leads along {axis} have {max(length, 0) * 1000:.0f} µm left once they stop "
                                  "clear of the corners: too many leads for this body.")
            else:
                err(key, f"The paddle leaves {max(length, 0) * 1000:.0f} µm for the leads along {axis} "
                         f"(at least {MIN_LEAD * 1000:.0f} µm).",
                    **{key: round(_floor(2 * (half - MIN_LEAD - GAP)), 3)})
    if g["corner_cut"]:
        out.append(Issue("info", "lead_len", f"Leads stop {CLR * 1000:.0f} µm clear of the neighbouring side's row: "
                         f"{g['len_x']:.2f} mm (left/right) and {g['len_y']:.2f} mm (bottom/top) long, "
                         "shorter than up to the paddle."))
    if fam in GULL_WING and d["height"] < 2 * d["lead_t"] + 0.1:
        err("height", f"A gull-wing leadframe {d['lead_t']:g} mm thick needs a body of at least "
                      f"{2 * d['lead_t'] + 0.1:.2f} mm.", height=round(2 * d["lead_t"] + 0.2, 2))
    if fam not in GULL_WING and d["height"] < d["lead_t"] + 0.15:
        err("height", "The body is barely thicker than the leadframe: no cavity is left.",
            height=round(d["lead_t"] + 0.5, 2))

    # the name should agree with the numbers
    m = re.search(r"(\d+)", d["name"])
    if m and int(m.group(1)) != total:
        warn("name", f"The name says {m.group(1)} leads, the numbers give {total}.",
             name=re.sub(r"\d+", str(total), d["name"], count=1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", d["name"])
    if m and (abs(float(m.group(1)) - d["body_x"]) > 0.05 or abs(float(m.group(2)) - d["body_y"]) > 0.05):
        warn("name", f"The name says {m.group(1)} x {m.group(2)} mm, the body is {d['body_x']:g} x {d['body_y']:g} mm.")
    if total % 2:
        warn("per_side_x", "An odd number of leads.")
    return out


def errors(issues):
    return [i for i in issues if i.level == "error"]


def apply_fixes(d, issues):
    """*d* with every fix of *issues* applied (the first fix wins per field)."""
    out = normalise(d)
    done = set()
    for issue in issues:
        for k, v in issue.fix.items():
            if k not in done:
                out[k] = v
                done.add(k)
    return normalise(out)


def auto_repair(d, rounds=4):
    """Apply fixes until no error is left or nothing changes. Returns (d, issues)."""
    d = normalise(d)
    for _ in range(rounds):
        issues = validate(d)
        if not errors(issues):
            return d, issues
        fixed = apply_fixes(d, errors(issues))
        if fixed == d:
            break
        d = fixed
    return d, validate(d)


# -- the user's library ------------------------------------------------------------------------

@dataclass
class Entry:
    spec: dict
    assumed: list = field(default_factory=list)   # fields a model chose without being told
    notes: str = ""
    origin: dict = field(default_factory=dict)    # {"by": "ollama"|"hand", "model", "prompt", "created"}
    path: str = ""

    @property
    def name(self):
        return self.spec.get("name", "")

    def to_json(self):
        return {"schema": SCHEMA_VERSION, "kind": "housing", "spec": normalise(self.spec),
                "assumed": list(self.assumed), "notes": self.notes, "origin": dict(self.origin)}


def library_dir():
    """Where the user's elements are kept: $PKGSTUDIO_ELEMENTS, else
    <FreeCAD user data>/PackageStudio/elements."""
    env = os.environ.get("PKGSTUDIO_ELEMENTS")
    if env:
        return env
    try:
        import FreeCAD
        base = FreeCAD.getUserAppDataDir()
    except Exception:
        base = os.path.expanduser("~/.pkgstudio")
    return os.path.join(base, "PackageStudio", "elements")


def _slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "element"


def save(entry, folder=None):
    """Write *entry* as <folder>/<name>.json (overwrites the same name)."""
    folder = folder or library_dir()
    os.makedirs(folder, exist_ok=True)
    entry.origin.setdefault("created", time.strftime("%Y-%m-%d %H:%M"))
    path = entry.path if entry.path and os.path.dirname(entry.path) == folder else \
        os.path.join(folder, _slug(entry.name) + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entry.to_json(), fh, indent=2, ensure_ascii=False)
    entry.path = path
    return path


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    spec = data.get("spec", data)
    return Entry(normalise(spec), list(data.get("assumed", [])), data.get("notes", ""),
                 dict(data.get("origin", {})), path)


def load_all(folder=None):
    folder = folder or library_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".json"):
            try:
                out.append(load(os.path.join(folder, name)))
            except (OSError, ValueError):
                continue
    return out


def delete(entry):
    if entry.path and os.path.isfile(entry.path):
        os.remove(entry.path)


# -- what a language model is given ------------------------------------------------------------

def json_schema():
    """The JSON schema a model's answer must follow (Ollama's `format`)."""
    props = {}
    for f in FIELDS:
        if f.kind == "enum":
            props[f.key] = {"type": "string", "enum": list(f.choices), "description": f.help}
        elif f.kind == "str":
            props[f.key] = {"type": "string", "description": f.help}
        else:
            props[f.key] = {"type": "integer" if f.kind == "int" else "number",
                            "description": f"{f.help} [{f.unit}]".replace(" []", "")}
    props["assumed"] = {"type": "array", "items": {"type": "string", "enum": [f.key for f in FIELDS]},
                        "description": "Every field whose value was NOT given by the user and was chosen "
                                       "from typical values."}
    props["notes"] = {"type": "string", "description": "One or two sentences: what was assumed and why."}
    return {"type": "object", "properties": props, "required": [f.key for f in FIELDS] + ["assumed", "notes"]}


def field_guide():
    return "\n".join(f"- {f.key} ({', '.join(x for x in (f.sym if len(f.sym) <= 2 else '', f.unit or f.kind) if x)}): "
                     f"{f.help}" for f in FIELDS)
