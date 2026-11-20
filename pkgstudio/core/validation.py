# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Is the simulation right? Measured against simulated S-parameters.

The steps of a validation (docs/plan/phase1_validation.md):

  quality    is the measurement itself any good: passivity, reciprocity
             and causality in the frequency domain, IEEE P370 (scikit-rf's
             IEEEP370_FD_QM). Bad data does not validate anything.
  deembed    remove the test fixture (connector, launch, feed line) with a
             2x-thru measured on the same board: IEEE P370 NZC 2x-thru
             (scikit-rf's IEEEP370_SE_NZC_2xThru). Only what the simulation
             also contains is left.
  compare    on the frequencies both have: per S-parameter the largest and
             the RMS difference in dB, the largest phase difference, and
             pass/fail against a tolerance.
  report     Markdown with the numbers and a PNG of the curves.

Needs scikit-rf (and matplotlib for the plot). FreeCAD-free.
"""

import json
import os

import numpy as np


def _skrf():
    try:
        import skrf
    except ImportError as exc:
        raise ImportError("Validation needs scikit-rf: pip install scikit-rf") from exc
    return skrf


def load(path_or_network):
    skrf = _skrf()
    return path_or_network if isinstance(path_or_network, skrf.Network) else skrf.Network(path_or_network)


def quality(network):
    """IEEE P370 frequency-domain quality: {'causality'|'passivity'|'reciprocity':
    {'value': %, 'evaluation': 'good'|'acceptable'|'inconclusive'|'poor'}}."""
    import warnings
    from skrf.calibration.deembedding import IEEEP370_FD_QM
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        qm = IEEEP370_FD_QM().check_se_quality(load(network))
    out = {}
    for k, v in qm.items():
        if isinstance(v, dict):
            out[k] = {"value": float(v.get("value", float("nan"))), "evaluation": str(v.get("evaluation", ""))}
    return out


def deembed(dut, thru, z0=50.0):
    """*dut* with the fixture halves of 2x-thru *thru* removed (IEEE P370 NZC)."""
    import warnings
    from skrf.calibration.deembedding import IEEEP370_SE_NZC_2xThru
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return IEEEP370_SE_NZC_2xThru(dummy_2xthru=load(thru), z0=z0).deembed(load(dut))


def _common(a, b, fmin=None, fmax=None, n=401):
    skrf = _skrf()
    lo = max(a.f[0], b.f[0], fmin or 0.0)
    hi = min(a.f[-1], b.f[-1], fmax or float("inf"))
    if hi <= lo:
        raise ValueError("The two have no frequencies in common")
    f = skrf.Frequency.from_f(np.linspace(lo, hi, n), unit="hz")
    return a.interpolate(f), b.interpolate(f)


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def compare(measured, simulated, params=None, fmin=None, fmax=None, tol_db=1.0, floor_db=-40.0):
    """
    {"Sij": {"max_db", "rms_db", "max_deg", "pass"}, ..., "band_GHz", "tol_db"}.
    *params*: [(i, j)] 1-based (default every entry); dB differences only
    where the measurement is above *floor_db* (near the noise floor a dB
    difference means nothing); phase where both are above it.
    """
    m, s = _common(load(measured), load(simulated), fmin, fmax)
    n = m.nports
    params = params or [(i, j) for i in range(1, n + 1) for j in range(1, n + 1)]
    out = {"band_GHz": [float(m.f[0] / 1e9), float(m.f[-1] / 1e9)], "tol_db": tol_db, "floor_db": floor_db}
    for i, j in params:
        a, b = m.s[:, i - 1, j - 1], s.s[:, i - 1, j - 1]
        ok = (_db(a) > floor_db)
        d = np.abs(_db(a) - _db(b))[ok]
        both = ok & (_db(b) > floor_db)
        ph = np.abs(np.angle(a[both] / b[both], deg=True)) if both.any() else np.array([0.0])
        out[f"S{i}{j}"] = {"max_db": float(d.max()) if d.size else 0.0,
                           "rms_db": float(np.sqrt(np.mean(d ** 2))) if d.size else 0.0,
                           "max_deg": float(ph.max()), "points": int(ok.sum()),
                           "pass": bool(d.size == 0 or d.max() <= tol_db)}
    out["pass"] = all(v["pass"] for k, v in out.items() if k.startswith("S"))
    return out


def plot(measured, simulated, path, params=None, labels=("measured", "simulated")):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    m, s = _common(load(measured), load(simulated))
    n = m.nports
    params = params or [(i, j) for i in range(1, n + 1) for j in range(1, n + 1)]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    for k, (i, j) in enumerate(params):
        c = f"C{k}"
        ax.plot(m.f / 1e9, _db(m.s[:, i - 1, j - 1]), c, label=f"S{i}{j} {labels[0]}")
        ax.plot(s.f / 1e9, _db(s.s[:, i - 1, j - 1]), c + "--", label=f"S{i}{j} {labels[1]}")
    ax.set_xlabel("Frequency (GHz)")
    ax.set_ylabel("|S| (dB)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def report(measured, simulated, out_dir, thru=None, params=None, tol_db=1.0, fmin=None, fmax=None, name="validation"):
    """The whole validation into *out_dir*: <name>.md, <name>.json, <name>.png."""
    os.makedirs(out_dir, exist_ok=True)
    meas = load(measured)
    result = {"measured": str(measured), "simulated": str(simulated), "quality": quality(meas)}
    if thru is not None:
        result["thru"] = str(thru)
        result["thru_quality"] = quality(thru)
        meas = deembed(meas, thru)
        result["deembedded"] = "IEEE P370 NZC 2x-thru"
    result["comparison"] = compare(meas, simulated, params, fmin, fmax, tol_db)
    png = os.path.join(out_dir, f"{name}.png")
    try:
        plot(meas, simulated, png, params)
        result["plot"] = png
    except ImportError:
        pass
    with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    c = result["comparison"]
    lines = [f"# Validation: {os.path.basename(str(simulated))} against {os.path.basename(str(measured))}", "",
             f"Band {c['band_GHz'][0]:g}–{c['band_GHz'][1]:g} GHz, tolerance {tol_db:g} dB "
             f"(where the measurement is above {c['floor_db']:g} dB). "
             f"**{'PASS' if c['pass'] else 'FAIL'}**", "", "## Measurement quality (IEEE P370)", "",
             "| | value | evaluation |", "|---|---|---|"]
    lines += [f"| {k} | {v['value']:.1f} % | {v['evaluation']} |" for k, v in result["quality"].items()]
    if thru is not None:
        lines += ["", "Fixture removed: IEEE P370 NZC 2x-thru."]
    lines += ["", "## Simulation against measurement", "", "| | max Δ dB | RMS Δ dB | max Δ phase ° | |",
              "|---|---|---|---|---|"]
    lines += [f"| {k} | {v['max_db']:.2f} | {v['rms_db']:.2f} | {v['max_deg']:.1f} | {'✔' if v['pass'] else '✖'} |"
              for k, v in c.items() if k.startswith("S")]
    if "plot" in result:
        lines += ["", f"![S-parameters]({os.path.basename(png)})"]
    md = os.path.join(out_dir, f"{name}.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    result["markdown"] = md
    return result
