# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Elements as data (core.elements), the model that fills them in
(app.element_ai, with a fake Ollama), and housings built and rebuilt from
them (app.housings).

Worked out by hand:

QFN-24 4x4, 6 leads a side at 0.5 mm, b = 0.25, paddle 2.1:
  half span of a row  = 2.5 * 0.5 + 0.125            = 1.375 mm
  leads would end at  = 2.1 / 2 + 0.15 (GAP)          = 1.20 mm from the centre
  1.375 > 1.20 - 0.05 on both axes, so the rows would cross in the corners;
  the leads stop at 1.375 + 0.05 (CLR)                = 1.425 mm,
  i.e. they are 2.0 - 1.425                           = 0.575 mm long.

The same with lead_len = 0.4: the leads end at 2.0 - 0.4 = 1.6 mm, clear of
the other rows (1.6 > 1.375 + 0.05), no corner cut. The cavity opens to
min(1.6 + 0.4 finger, 2.0 - 0.1 wall) = 1.9 mm, so each finger is open from
1.6 to 1.9 mm: 0.30 mm long, 0.25 mm wide.

Every catalogue package: before the corner cut, 12 of the 25 had leads of two
sides sharing copper (0.02 to 0.39 mm³); now none touches another.
"""

import json
import os

from _harness import TestCase, tmpdir

from pkgstudio.core import elements as el


def _qfn24(**kw):
    d = dict(el.EXAMPLE, name="QFN-24 4x4", per_side_x=6, per_side_y=6, pitch=0.5, lead_w=0.25,
             paddle_x=2.1, paddle_y=2.1)
    d.update(kw)
    return el.normalise(d)


def _schema_and_checks(tc):
    schema = el.json_schema()
    tc.check("schema: every field is required, families are an enum",
             set(schema["required"]) >= {f.key for f in el.FIELDS} | {"assumed", "notes"}
             and schema["properties"]["family"]["enum"] == list(el.FAMILIES))
    tc.check("the example (QFN-16 4x4) is clean", el.validate(el.EXAMPLE) == [], el.validate(el.EXAMPLE))

    g = el.geometry(_qfn24())
    tc.check("QFN-24 4x4: the rows would cross, the leads are cut back", g["corner_cut"])
    tc.near("QFN-24 4x4: leads end 1.425 mm from the centre", g["inner_x"], 1.425, 1e-12)
    tc.near("QFN-24 4x4: leads are 0.575 mm long", g["len_x"], 0.575, 1e-12)
    g = el.geometry(_qfn24(lead_len=0.4))
    tc.check("QFN-24 4x4, L = 0.4: no corner cut, leads end at 1.6 mm",
             not g["corner_cut"] and abs(g["inner_x"] - 1.6) < 1e-12, g)

    issues = el.validate(_qfn24(family="DFN"))
    bad = [i for i in issues if i.level == "error" and i.field == "per_side_y"]
    tc.check("a DFN with bottom/top leads is an error, the fix moves them to 0",
             bad and bad[0].fix.get("per_side_y") == 0, issues)
    issues = el.validate(_qfn24(lead_w=0.45))
    bad = [i for i in issues if i.field == "lead_w" and i.level == "error"]
    tc.check("0.45 mm leads at 0.5 mm pitch leave 50 µm: an error, fix b = 0.25",
             bad and abs(bad[0].fix["lead_w"] - 0.25) < 1e-12, issues)
    crowded = _qfn24(name="QFN-48 3x3", body_x=3.0, body_y=3.0, per_side_x=12, per_side_y=12, paddle_x=1.0,
                     paddle_y=1.0)
    tc.check("48 leads on a 3 mm body do not fit: errors", el.errors(el.validate(crowded)))
    fixed, left = el.auto_repair(crowded)
    tc.check("auto_repair makes the body big enough to build", not el.errors(left) and fixed["body_x"] > 6.0,
             (fixed["body_x"], left))
    issues = el.validate(_qfn24(name="QFN-20 4x4"))
    tc.check("a name with the wrong lead count is a warning with the right name",
             any(i.field == "name" and i.level == "warning" and i.fix.get("name") == "QFN-24 4x4" for i in issues),
             issues)
    tc.check("a 0.45 mm pitch is not JEDEC: a warning",
             any(i.field == "pitch" and i.level == "warning" for i in el.validate(_qfn24(pitch=0.45, lead_w=0.2))))
    tc.check("normalise: strings become numbers, ints round, unknown keys go",
             el.normalise({"family": "qfn", "pitch": "0.5", "per_side_x": 5.6, "bogus": 1}) ==
             dict(el.normalise({"family": "QFN"}), pitch=0.5, per_side_x=6))


def _library(tc):
    folder = tmpdir("elements")
    e = el.Entry(_qfn24(), ["height"], "MO-220 height", {"by": "ollama", "model": "qwen3:8b", "prompt": "QFN-24"})
    path = el.save(e, folder)
    back = el.load_all(folder)
    tc.check("library: saved and read back unchanged",
             len(back) == 1 and back[0].spec == _qfn24() and back[0].assumed == ["height"]
             and back[0].origin["model"] == "qwen3:8b" and os.path.basename(path) == "QFN-24_4x4.json",
             (path, back))
    with open(path, encoding="utf-8") as fh:
        tc.check("library: a plain JSON file with its schema version", json.load(fh)["schema"] == el.SCHEMA_VERSION)


def _fake_chat(answers, seen):
    """A chat that streams each answer in 7-character pieces."""
    def chat(url, payload, on_text):
        seen.append(payload)
        text = answers[len(seen) - 1]
        for k in range(0, len(text), 7):
            on_text(text[k:k + 7])
        return text
    return chat


def _ai(tc):
    from pkgstudio.app import element_ai as ai
    wrong = dict(_qfn24(family="DFN"), assumed=["height", "lead_t"], notes="typical")
    right = dict(_qfn24(family="DFN", name="DFN-12 4x4", per_side_y=0), assumed=["height", "lead_t"],
                 notes="typical")
    seen, fields = [], []
    draft = ai.generate("DFN 4x4, 12 leads, 0.5 pitch", chat=_fake_chat([json.dumps(wrong), json.dumps(right)], seen),
                        on_field=lambda k, v: fields.append(k))
    tc.check("ai: the request carries the schema, temperature 0, no thinking",
             seen[0]["format"] == el.json_schema() and seen[0]["options"]["temperature"] == 0
             and seen[0]["think"] is False)
    tc.check("ai: a spec that fails the checks goes back once, with the failing field named",
             draft.attempts == 2 and "per_side_y" in seen[1]["messages"][-1]["content"]
             and seen[1]["messages"][-2]["role"] == "assistant", draft.attempts)
    tc.check("ai: the corrected spec is clean, its assumptions kept",
             draft.ok and draft.spec["per_side_y"] == 0 and draft.assumed == ["height", "lead_t"], draft)
    tc.check("ai: fields are reported as they stream, in the answer's order",
             fields[:4] == ["name", "family", "standard", "body_x"] and "standoff" in fields, fields[:6])

    seen = []
    base = _qfn24(lead_len=0.4)
    changed = dict(base, pitch=0.4, lead_w=0.2, assumed=["lead_w"], notes="")
    draft = ai.generate("pitch 0.4", base=base, base_assumed=["height", "pitch"],
                        chat=_fake_chat([json.dumps(changed)], seen))
    tc.check("ai change: the current spec is in the request",
             json.dumps(base) in seen[0]["messages"][1]["content"])
    tc.check("ai change: a value assumed before stays assumed while unchanged; one now set does not",
             draft.assumed == ["height", "lead_w"], draft.assumed)
    tc.check("ai: an answer wrapped in a code fence is read",
             ai.parse("```json\n{\"a\": 1}\n```") == {"a": 1})


def _built(tc):
    import FreeCAD
    from pkgstudio.core import housing, jedec
    total = 0.0
    for spec in jedec.CATALOGUE:
        lf = jedec.build(spec)["leadframe"]
        for i in range(len(lf)):
            for j in range(i + 1, len(lf)):
                if lf[i].BoundBox.intersect(lf[j].BoundBox):
                    total += lf[i].common(lf[j]).Volume
    tc.check("catalogue: no two leadframe pieces share copper", total < 1e-12, f"{total} mm3")

    parts = jedec.build(el.to_spec(_qfn24(lead_len=0.4)))
    a = housing.analyse([parts["body"]] + parts["leadframe"])
    p1 = a.fingers[0]
    tc.check("QFN-24, L = 0.4: 24 fingers, paddle open", len(a.fingers) == 24 and a.attach is not None)
    tc.near("QFN-24, L = 0.4: pin 1's finger is 0.30 mm long", p1.w, 0.30, 1e-9)
    tc.near("QFN-24, L = 0.4: pin 1's finger is 0.25 mm wide", p1.h, 0.25, 1e-9)
    tc.near("QFN-24, L = 0.4: pin 1's finger centred at x = -1.75", p1.x, -1.75, 1e-9)

    from pkgstudio.app import housings, model
    doc = FreeCAD.newDocument("elements_test")
    try:
        h = housings.from_spec_dict(doc, el.EXAMPLE, ["height"], {"by": "hand"})
        spec, assumed, _origin = housings.spec_of(h)
        tc.check("housing: built from a spec keeps it", spec == el.normalise(el.EXAMPLE) and assumed == ["height"])
        names = {p.Name for p in model.pads_of(h)}
        kept, added, removed = housings.rebuild(h, dict(el.EXAMPLE, name="QFN-20 4x4", per_side_x=5,
                                                        per_side_y=5, pitch=0.5, lead_w=0.25, paddle_x=2.2,
                                                        paddle_y=2.2))
        tc.check("rebuild 16 -> 20 leads: 16 pad objects kept, 4 added",
                 (kept, added, removed) == (16, 4, 0) and names <= {p.Name for p in model.pads_of(h)},
                 (kept, added, removed))
        kept, added, removed = housings.rebuild(h, el.EXAMPLE)
        tc.check("rebuild back to 16: 4 pads removed", (kept, added, removed) == (16, 0, 4)
                 and len(model.pads_of(h)) == 16 and h.HousingName == "QFN-16 4x4", (kept, added, removed))
    finally:
        FreeCAD.closeDocument(doc.Name)


def run():
    tc = TestCase("elements")
    tc.guard("schema and checks", lambda: _schema_and_checks(tc))
    tc.guard("library", lambda: _library(tc))
    tc.guard("model", lambda: _ai(tc))
    try:
        import FreeCAD  # noqa: F401
        has_freecad = True
    except ImportError:
        has_freecad = False
    if has_freecad:
        tc.guard("built", lambda: _built(tc))
    else:
        tc.skip("built geometry", "FreeCAD not available")
    return tc.results
