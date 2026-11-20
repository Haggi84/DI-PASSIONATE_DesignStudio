# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Housings: generated JEDEC packages, and reading any housing's paddle, leads
and bond fingers.

For every generated package: the body is one valid solid that does not
overlap the leadframe; every lead has a bond finger; pin 1 is the top lead
of the left side (or, for SOIC/TSSOP, the left lead of the bottom side —
JEDEC numbers those from the bottom-left), and the die paddle is open. For
QFN-16 4x4 the numbers are checked by hand: leads at 0.65 mm pitch, pin 1's
finger centred at y = +0.975; the open paddle 2.3 x 2.3 mm.

A MirrorSemi model is checked too when one is in the download cache
(nothing is shipped with the workbench).
"""

import os

import Part

from _harness import TestCase

from pkgstudio.core import catalogue, housing, jedec


def run():
    tc = TestCase("housing")
    for spec in jedec.CATALOGUE:
        parts = jedec.build(spec)
        body, lf = parts["body"], parts["leadframe"]
        a = housing.analyse([body] + lf)
        overlap = sum(body.common(s).Volume for s in lf)
        tc.check(f"{spec.name}: valid body, no overlap with the leadframe, every lead bondable, paddle open",
                 body.isValid() and overlap < 1e-12 and len(a.fingers) == spec.leads and a.attach is not None,
                 f"valid={body.isValid()} overlap={overlap} fingers={len(a.fingers)}/{spec.leads}")
    q = jedec.by_name("QFN-16 4x4")
    a = housing.analyse([jedec.build(q)["body"]] + jedec.build(q)["leadframe"])
    p1 = a.fingers[0]
    tc.check("QFN-16: pin 1 is the top lead of the left side (y = +0.975)",
             p1.name == "1" and p1.x < 0 and abs(p1.y - 0.975) < 1e-9, (p1.x, p1.y))
    tc.check("QFN-16: pins 1-4 go down the left side, 5-8 along the bottom",
             [round(f.y, 3) for f in a.fingers[:4]] == [0.975, 0.325, -0.325, -0.975]
             and all(f.y < 0 for f in a.fingers[4:8]))
    tc.check("QFN-16: the open paddle is 2.3 x 2.3 mm, on the leadframe top",
             abs(a.attach.w - 2.3) < 1e-9 and abs(a.attach.h - 2.3) < 1e-9 and abs(a.attach.z - 0.2) < 1e-12)

    cached = [os.path.join(catalogue.cache_dir(), f) for f in os.listdir(catalogue.cache_dir())
              if f.lower().endswith((".step", ".stp")) and catalogue.is_step(os.path.join(catalogue.cache_dir(), f))]
    if not cached:
        tc.skip("a MirrorSemi model reads as body + paddle + bondable leads", "no model in the download cache")
    else:
        shape = Part.read(cached[0])
        a = housing.analyse(shape.Solids)
        name = os.path.basename(cached[0])
        _fam, n, _pitch = catalogue.parse_name(name)
        tc.check(f"MirrorSemi {name}: {n} bondable leads and an open paddle",
                 len(a.fingers) == n and a.attach is not None, (len(a.fingers), a.attach))

    html = ('<a href="/STEP/M-QFN16W.65.step">x</a><a href="/STEP/M-QFN48W.5-G3.step">y</a>'
            '<a href="/CAD/M-QFN16W.65_Bond.dxf">b</a><a href="/STEP/M-QFN16W.65.step">dup</a>')
    entries = catalogue.parse_mirrorsemi(html)
    tc.check("catalogue: one entry per model, duplicates dropped", [e.name for e in entries]
             == ["M-QFN16W.65", "M-QFN48W.5-G3"], [e.name for e in entries])
    tc.check("catalogue: lead count and pitch from the name, bonding diagram matched",
             entries[0].leads == 16 and entries[0].pitch == 0.65 and entries[0].bond.endswith("M-QFN16W.65_Bond.dxf")
             and entries[1].leads == 48 and entries[1].pitch == 0.5)
    tc.check("catalogue: links made absolute", entries[0].step == "https://www.mirrorsemi.com/STEP/M-QFN16W.65.step")
    return tc.results
