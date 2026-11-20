#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
A package's S-parameters as a SPICE subcircuit (and an xschem symbol), so the
chip designer simulates the circuit in its package.

    python tools/ps_circuit_model.py package.s4p [--labels RFIN_line,RFOUT_line,RFIN_die,RFOUT_die]
                                                 [--tol 0.005] [--check]

Writes package.cir (.SUBCKT package p1 … pN) and package.sym beside the
Touchstone file. --check simulates the subcircuit with ngspice and compares
it with the Touchstone file. Needs scikit-rf.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pkgstudio.core import circuit_model as cm  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("touchstone")
    ap.add_argument("--labels", default="")
    ap.add_argument("--check", action="store_true", help="compare with ngspice (needs ngspice on PATH)")
    ap.add_argument("--fmin", type=float, help="fit only from this frequency, Hz")
    ap.add_argument("--fmax", type=float, help="fit only up to this frequency, Hz")
    a = ap.parse_args(argv)
    cir, rep = cm.write_spice(a.touchstone, fmin=a.fmin, fmax=a.fmax)
    labels = [s for s in a.labels.split(",") if s] or None
    sym = cm.write_xschem_symbol(os.path.splitext(cir)[0] + ".sym", rep["name"], rep["ports"],
                                 os.path.abspath(cir), labels)
    rep["symbol"] = sym
    if a.check:
        rep["ngspice_check"] = cm.check_against(cir, a.touchstone, rep["name"], fmin=a.fmin, fmax=a.fmax)
    print(json.dumps(rep, indent=1))
    return 0 if rep["passive"] else 1


if __name__ == "__main__":
    sys.exit(main())
