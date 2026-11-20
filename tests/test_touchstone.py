# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Palace's port-S.csv to Touchstone.

A 2-port written by hand in Palace's column format, at two frequencies:
|S11| = -20 dB (0.1), |S21| = -0.5 dB (0.944061), S12 = S21, S22 = S11. The
Touchstone 2-port line order is S11 S21 S12 S22. A 3-port puts one matrix row
per line. A missing entry (port 2 never excited) is an error.
"""

import os

from _harness import TestCase, tmpdir

from pkgstudio.core import touchstone as ts

HEAD = ("f (GHz),|S[1][1]| (dB),arg(S[1][1]) (deg.),|S[2][1]| (dB),arg(S[2][1]) (deg.),"
        "|S[1][2]| (dB),arg(S[1][2]) (deg.),|S[2][2]| (dB),arg(S[2][2]) (deg.)\n")


def run():
    tc = TestCase("touchstone")
    out = tmpdir("touchstone")
    p = os.path.join(out, "port-S.csv")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(HEAD)
        fh.write("1.0,-20,10,-0.5,-30,-0.5,-30,-20,12\n")
        fh.write("2.0,-18,20,-0.7,-60,-0.7,-60,-18,22\n")
    freqs, s = ts.read_palace_csv(p)
    tc.check("csv: two frequencies, four entries", freqs == [1.0, 2.0] and len(s) == 4, (freqs, sorted(s)))
    tc.near("csv: -20 dB is a magnitude of 0.1", s[(1, 1)][0][0], 0.1, 1e-12)
    tc.near("csv: -0.5 dB is 0.944061", s[(2, 1)][0][0], 10 ** (-0.5 / 20), 1e-12)
    path = ts.convert(p)
    tc.check("s2p: named for its port count", os.path.basename(path) == "package.s2p", path)
    with open(path, encoding="ascii") as fh:
        lines = [l for l in fh.read().splitlines() if l and not l.startswith("!")]
    tc.check("s2p: option line GHz, S, magnitude-angle, 50 ohm", lines[0] == "# GHz S MA R 50", lines[0])
    first = lines[1].split()
    tc.check("s2p: one line per frequency, S11 S21 S12 S22",
             len(lines) == 3 and float(first[0]) == 1.0 and abs(float(first[1]) - 0.1) < 1e-9
             and float(first[2]) == 10 and abs(float(first[3]) - 0.944061) < 1e-6 and float(first[4]) == -30
             and float(first[8]) == 12, lines)

    three = {(i, j): [(0.5, float(10 * i + j))] for i in (1, 2, 3) for j in (1, 2, 3)}
    p3 = ts.write_touchstone(os.path.join(out, "x.s3p"), [5.0], three)
    with open(p3, encoding="ascii") as fh:
        rows = [l.split() for l in fh.read().splitlines()[1:]]
    tc.check("s3p: one matrix row per line, the first led by the frequency",
             [len(r) for r in rows] == [7, 6, 6] and rows[0][2] == "11" and rows[1][1] == "21"
             and rows[2][5] == "33", rows)
    del three[(2, 2)]
    try:
        ts.write_touchstone(os.path.join(out, "bad.s3p"), [5.0], three)
        tc.check("a missing matrix entry is an error", False)
    except ValueError:
        tc.check("a missing matrix entry is an error", True)
    return tc.results
