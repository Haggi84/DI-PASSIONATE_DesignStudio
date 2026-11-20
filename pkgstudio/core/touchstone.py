# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Palace's S-parameters as a Touchstone file.

Palace writes postpro/port-S.csv: a frequency column "f (GHz)" and, for
every port i and excitation j, "|S[i][j]| (dB)" and "arg(S[i][j]) (deg.)".
Circuit simulators, scikit-rf and every VNA tool read Touchstone instead, so
the run script converts it to <name>.sNp (version 1.1, "# GHz S MA R 50").

The column names are read, never their positions: Palace orders columns by
excitation, and a run excited at a subset of the ports has fewer of them. A
matrix entry Palace did not compute is an error, not a zero.

Plain Python, no FreeCAD: it runs from the model folder's run script.
"""

import csv
import math
import os
import re
import sys

_MAG = re.compile(r"\|S\[(\d+)\]\[(\d+)\]\|\s*\(dB\)")
_ARG = re.compile(r"arg\(S\[(\d+)\]\[(\d+)\]\)\s*\(deg\.?\)")


def read_palace_csv(path):
    """(frequencies in GHz, {(i, j): [(magnitude, degrees)]}) from port-S.csv."""
    with open(path, encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]
    head = [h.strip() for h in rows[0]]
    mag, arg = {}, {}
    for k, h in enumerate(head):
        m = _MAG.fullmatch(h)
        if m:
            mag[(int(m[1]), int(m[2]))] = k
        a = _ARG.fullmatch(h)
        if a:
            arg[(int(a[1]), int(a[2]))] = k
    fcol = next((k for k, h in enumerate(head) if h.startswith("f")), 0)
    freqs, s = [], {key: [] for key in mag}
    for r in rows[1:]:
        freqs.append(float(r[fcol]))
        for key, k in mag.items():
            db = float(r[k])
            s[key].append((10.0 ** (db / 20.0), float(r[arg[key]]) if key in arg else 0.0))
    return freqs, s


def write_touchstone(path, freqs, s, r0=50.0, comment=()):
    """An n-port Touchstone 1.1 file, magnitude/angle. n is the largest port
    index; every entry of the n x n matrix must be there."""
    n = max(max(i, j) for i, j in s)
    missing = [(i, j) for i in range(1, n + 1) for j in range(1, n + 1) if (i, j) not in s]
    if missing:
        raise ValueError(f"S-parameters missing for {missing[:6]}{' ...' if len(missing) > 6 else ''}: "
                         "was every port excited?")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for c in comment:
            fh.write(f"! {c}\n")
        fh.write(f"# GHz S MA R {r0:g}\n")
        for k, f in enumerate(freqs):
            if n == 2:
                # Touchstone's one exception: a 2-port line is S11 S21 S12 S22.
                order = [[(1, 1), (2, 1), (1, 2), (2, 2)]]
            else:
                order = [[(i, j) for j in range(1, n + 1)] for i in range(1, n + 1)]
            first = True
            for row in order:
                # at most four pairs on a line
                for start in range(0, len(row), 4):
                    pairs = " ".join(f"{s[key][k][0]:.9g} {s[key][k][1]:.6g}" for key in row[start:start + 4])
                    fh.write((f"{f:.9g} " if first else "  ") + pairs + "\n")
                    first = False
    return path


def convert(csv_path, out_path=None, r0=50.0):
    """port-S.csv -> .sNp beside it (or *out_path*). Returns the path."""
    freqs, s = read_palace_csv(csv_path)
    if not s:
        raise ValueError(f"{csv_path} has no S-parameter columns")
    n = max(max(i, j) for i, j in s)
    out_path = out_path or os.path.join(os.path.dirname(os.path.abspath(csv_path)), f"package.s{n}p")
    return write_touchstone(out_path, freqs, s, r0, comment=(f"from Palace {os.path.basename(csv_path)}",))


def db(magnitude):
    return 20.0 * math.log10(max(magnitude, 1e-300))


if __name__ == "__main__":
    print(convert(*sys.argv[1:3]))
