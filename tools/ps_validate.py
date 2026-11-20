#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Validate a simulation against a measurement (docs/plan/phase1_validation.md).

    python tools/ps_validate.py measured.s2p simulated.s2p [--thru 2xthru.s2p]
           [--tol 1.0] [--fmin 0.1e9] [--fmax 10e9] [--params 21,11] [--out folder]

Writes <out>/validation.md, .json and .png; exit status 0 on pass, 1 on fail.
Needs scikit-rf and matplotlib (pip install scikit-rf matplotlib).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pkgstudio.core import validation  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("measured")
    ap.add_argument("simulated")
    ap.add_argument("--thru", help="2x-thru coupon, measured on the same panel (IEEE P370 NZC de-embedding)")
    ap.add_argument("--tol", type=float, default=1.0, help="acceptance, max |Δ| in dB (default 1.0)")
    ap.add_argument("--fmin", type=float)
    ap.add_argument("--fmax", type=float)
    ap.add_argument("--params", default="", help="e.g. 21,11 (default: all)")
    ap.add_argument("--out", default="validation")
    a = ap.parse_args(argv)
    params = [(int(p[0]), int(p[1])) for p in a.params.split(",") if p] or None
    r = validation.report(a.measured, a.simulated, a.out, thru=a.thru, params=params, tol_db=a.tol,
                          fmin=a.fmin, fmax=a.fmax)
    c = r["comparison"]
    for k, v in c.items():
        if k.startswith("S"):
            print(f"{k}: max {v['max_db']:.2f} dB, RMS {v['rms_db']:.2f} dB, phase {v['max_deg']:.1f}°  "
                  f"{'pass' if v['pass'] else 'FAIL'}")
    q = ", ".join(f"{k} {v['evaluation']}" for k, v in r["quality"].items())
    print(f"measurement quality (IEEE P370): {q}")
    print(f"{'PASS' if c['pass'] else 'FAIL'} — {r['markdown']}")
    return 0 if c["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
