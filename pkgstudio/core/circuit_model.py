# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
From the EM result back into the circuit designer's simulation.

A Touchstone file is what the EM run gives; the designer simulates in
ngspice from xschem (or Qucs-S, LTspice, Xyce), which cannot read it. So
the S-parameters are fitted by rational functions (vector fitting,
Gustavsen & Semlyen 1999, as implemented in scikit-rf), made passive where
the fit is not (a passive package must not add energy — a non-passive model
can make a stable amplifier oscillate in the transient), and written as a
SPICE subcircuit of R, G, F elements with one pin per port. Beside it, an
xschem symbol with the same pins, so the package drops into the
schematic.

Needs scikit-rf (pip install scikit-rf); everything else is plain Python.

  fit(path)                  -> report dict (poles, rms error, passive)
  write_spice(path, cir)     -> the subcircuit, and its report
  write_xschem_symbol(...)   -> a .sym for it
  check_against(cir, path)   -> the subcircuit's S-parameters from ngspice
                                compared with the Touchstone file (needs
                                ngspice on PATH)
"""

import os
import re
import shutil
import subprocess
import tempfile


def _skrf():
    try:
        import skrf
        from skrf.vectorFitting import VectorFitting
    except ImportError as exc:
        raise ImportError("The circuit model needs scikit-rf: pip install scikit-rf "
                          "(in FreeCAD's Python: FreeCAD's 'python -m pip install scikit-rf')") from exc
    return skrf, VectorFitting


def read(path):
    skrf, _vf = _skrf()
    return skrf.Network(path)


REFUSE = 0.05                    # RMS |S| error above which no model is written


class FitError(ValueError):
    pass


def _band(nw, fmin=None, fmax=None):
    if fmin is None and fmax is None:
        return nw
    lo = fmin if fmin is not None else nw.f[0]
    hi = fmax if fmax is not None else nw.f[-1]
    return nw[f"{lo / 1e9}-{hi / 1e9}ghz"]


def fit(network, tol=0.005, max_cmplx=14, fmin=None, fmax=None):
    """
    A passive rational model of *network* (skrf.Network or a path).

    Vector fitting is tried from few poles to many, with and without a
    constant term; the first model that is passive *as fitted* and within
    *tol* (RMS of |S| error) is taken. Enforcing passivity on a non-passive
    fit afterwards (scikit-rf's passivity_enforce) wrecked the model in
    tests — RMS 0.001 -> 0.27 — so it is only the last resort, and then
    reported. Returns (VectorFitting, report).
    """
    import warnings
    skrf, VectorFitting = _skrf()
    nw = network if isinstance(network, skrf.Network) else skrf.Network(network)
    nw = _band(nw, fmin, fmax)
    tried, best = [], None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for n in range(1, max_cmplx + 1):
            for const in (True, False):
                vf = VectorFitting(nw)
                try:
                    vf.vector_fit(n_poles_real=1, n_poles_cmplx=n, fit_constant=const, fit_proportional=False)
                except Exception:
                    continue
                rms, passive = float(vf.get_rms_error()), bool(vf.is_passive())
                tried.append((n, const, rms, passive))
                if passive and (best is None or rms < best[2]):
                    best = (vf, const, rms)
                if passive and rms <= tol:
                    return vf, _report(nw, vf, tried, True, "passive as fitted")
        if best is not None and best[2] > REFUSE:
            raise FitError(
                f"no passive rational model fits this network (best RMS error {best[2]:.3f}, {len(tried)} fits): "
                "it is electrically long or has many resonances in the band — a lumped SPICE model is the wrong "
                "tool for it. Fit a narrower band (fmax), or model the lines as lines and fit only the "
                "package's discontinuity.")
        if best is not None:
            return best[0], _report(nw, best[0], tried, True,
                                    f"passive as fitted, but RMS {best[2]:.4f} > {tol}: check the result")
        vf = VectorFitting(nw)
        vf.vector_fit(n_poles_real=1, n_poles_cmplx=4, fit_constant=True)
        try:
            vf.passivity_enforce(n_samples=2000)
        except Exception:
            pass
    return vf, _report(nw, vf, tried, bool(vf.is_passive()), "passivity enforced afterwards: check the result")


def _report(nw, vf, tried, passive, how):
    return {"ports": nw.nports, "poles": int(vf.get_model_order(vf.poles)), "rms_error": float(vf.get_rms_error()),
            "passive": passive, "how": how, "fits_tried": len(tried),
            "f_GHz": (float(nw.f[0] / 1e9), float(nw.f[-1] / 1e9))}


def subckt_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem)
    return name if name[:1].isalpha() else "pkg_" + name


def write_spice(touchstone, out=None, name=None, fmin=None, fmax=None):
    """<touchstone stem>.cir beside it (or *out*): `.SUBCKT name p1 … pN`,
    every port referred to ground. Returns (path, report)."""
    out = out or os.path.splitext(touchstone)[0] + ".cir"
    name = name or subckt_name(touchstone)
    vf, report = fit(touchstone, fmin=fmin, fmax=fmax)
    vf.write_spice_subcircuit_s(out, fitted_model_name=name)
    report.update(path=out, name=name, source=os.path.abspath(touchstone), fmin=fmin, fmax=fmax)
    return out, report


def write_xschem_symbol(out, name, ports, cir_path, labels=None):
    """An xschem symbol for subcircuit *name* with *ports* pins (p1 … pN;
    *labels*: what to show beside them). The netlist line includes the
    subcircuit file, so placing the symbol is all it takes."""
    labels = labels or [f"p{i}" for i in range(1, ports + 1)]
    h = 20 * max(ports, 2)
    left = list(range(1, (ports + 1) // 2 + 1))
    right = list(range((ports + 1) // 2 + 1, ports + 1))
    lines = ["v {xschem version=3.4.5 file_version=1.2}",
             "G {type=subcircuit",
             'format="@name @pinlist @symname"',
             'template="name=x1"',
             f'spice_sym_def="tcleval(.include {cir_path})"',
             "}", "V {}", "S {}", "E {}",
             f"B 4 -60 -{h / 2 + 10} 60 {h / 2 + 10} {{fill=false}}"]
    pins = []
    for k, i in enumerate(left):
        y = -h / 2 + 10 + k * 20
        pins.append(f"L 4 -80 {y} -60 {y} {{}}")
        pins.append(f"B 5 -82.5 {y - 2.5} -77.5 {y + 2.5} {{name=p{i} dir=inout}}")
        pins.append(f"T {{{labels[i - 1]}}} -55 {y - 6} 0 0 0.2 0.2 {{}}")
    for k, i in enumerate(right):
        y = -h / 2 + 10 + k * 20
        pins.append(f"L 4 60 {y} 80 {y} {{}}")
        pins.append(f"B 5 77.5 {y - 2.5} 82.5 {y + 2.5} {{name=p{i} dir=inout}}")
        pins.append(f"T {{{labels[i - 1]}}} 55 {y - 6} 0 1 0 0.2 0.2 {{}}")
    lines += pins
    lines += [f"T {{@symname}} -50 {h / 2 + 14} 0 0 0.25 0.25 {{}}", f"T {{@name}} -50 -{h / 2 + 28} 0 0 0.2 0.2 {{}}"]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return out


def _s_from_ngspice(cir, name, ports, freqs_hz, ngspice="ngspice"):
    """S-parameters of subcircuit *name* by AC analysis: port j driven by a
    1 V source behind 50 ohm, every port terminated in 50 ohm.
    S_ij = 2 V_i / 1 V - delta_ij."""
    import numpy as np
    fstart, fstop, n = freqs_hz[0], freqs_hz[-1], len(freqs_hz)
    s = np.zeros((n, ports, ports), dtype=complex)
    with tempfile.TemporaryDirectory() as tmp:
        for j in range(1, ports + 1):
            deck = [f"* S column {j}", f".include {os.path.abspath(cir)}"]
            nodes = " ".join(f"n{i}" for i in range(1, ports + 1))
            deck.append(f"X1 {nodes} {name}")
            for i in range(1, ports + 1):
                if i == j:
                    deck += [f"VS{i} s{i} 0 DC 0 AC 1", f"RS{i} s{i} n{i} 50"]
                else:
                    deck.append(f"RL{i} n{i} 0 50")
            out = os.path.join(tmp, f"col{j}.txt")
            vs = " ".join(f"v(n{i})" for i in range(1, ports + 1))
            deck += [".control", f"ac lin {n} {fstart} {fstop}", f"wrdata {out} {vs}", "quit", ".endc", ".end"]
            path = os.path.join(tmp, f"col{j}.cir")
            with open(path, "w") as fh:
                fh.write("\n".join(deck) + "\n")
            subprocess.run([ngspice, "-b", path], check=True, capture_output=True, timeout=600)
            data = np.loadtxt(out)
            # wrdata: per vector "freq re im"
            for i in range(ports):
                v = data[:, 3 * i + 1] + 1j * data[:, 3 * i + 2]
                s[:, i, j - 1] = 2 * v - (1.0 if i == j - 1 else 0.0)
    return s


def check_against(cir, touchstone, name=None, ngspice=None, fmin=None, fmax=None):
    """The subcircuit, simulated by ngspice, against the Touchstone file:
    {"max_db_error", "max_abs_error", "ngspice"}; None without ngspice."""
    import numpy as np
    ngspice = ngspice or shutil.which("ngspice")
    if not ngspice:
        return None
    nw = _band(read(touchstone), fmin, fmax)          # only where the model was fitted
    name = name or subckt_name(touchstone)
    f = np.linspace(nw.f[0], nw.f[-1], min(len(nw.f), 801))
    s = _s_from_ngspice(cir, name, nw.nports, f, ngspice)
    skrf, _vf = _skrf()
    ref = nw.s if np.allclose(f, nw.f) else nw.interpolate(skrf.Frequency.from_f(f, unit="hz")).s
    err = np.abs(s - ref)
    db = np.abs(20 * np.log10(np.maximum(np.abs(s), 1e-9)) - 20 * np.log10(np.maximum(np.abs(ref), 1e-9)))
    big = np.abs(ref) > 0.1                       # dB errors only where |S| is not tiny
    return {"max_abs_error": float(err.max()), "max_db_error": float(db[big].max()) if big.any() else 0.0,
            "ngspice": ngspice}
