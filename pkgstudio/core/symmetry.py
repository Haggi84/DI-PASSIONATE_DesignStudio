# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Symmetry: pairing chip pads with package pads, and mirroring.

Pairing keeps the angular order around the chip centre. Two wires whose
pads are in opposite order cross each other, which no bond plan wants, so
the pairing is an order-preserving match: walking round the chip, the
package pads are walked round in the same direction, never back. Among all
such matches it takes the one with the smallest total angular offset —
which, for a symmetric layout, is the symmetric one.

All functions work on plain (x, y) tuples in one common frame.
"""

import math

import numpy as np


def angle(p, centre=(0.0, 0.0)):
    return math.atan2(p[1] - centre[1], p[0] - centre[0])


def _angdiff(a, b):
    d = (a - b + math.pi) % (2.0 * math.pi) - math.pi
    return abs(d)


def pair(chip, package, centre=(0.0, 0.0)):
    """
    [(chip index, package index)] — an order-preserving, crossing-free match
    of every pad on the shorter side to one on the longer side, with the
    smallest total angular offset around *centre*.
    """
    if not chip or not package:
        return []
    swap = len(chip) > len(package)
    a_pts, b_pts = (package, chip) if swap else (chip, package)
    a_order = sorted(range(len(a_pts)), key=lambda i: angle(a_pts[i], centre))
    b_order = sorted(range(len(b_pts)), key=lambda j: angle(b_pts[j], centre))
    a_ang = np.array([angle(a_pts[i], centre) for i in a_order])
    b_ang = np.array([angle(b_pts[j], centre) for j in b_order])
    n, m = len(a_ang), len(b_ang)

    best = None
    for shift in range(m):
        b_rot = np.roll(b_ang, -shift)
        cost = np.abs((a_ang[:, None] - b_rot[None, :] + np.pi) % (2 * np.pi) - np.pi)
        # dp[i][j]: best cost matching a[0..i] with a[i] -> b[j], strictly increasing j
        dp = np.full((n, m), np.inf)
        arg = np.zeros((n, m), dtype=int)
        dp[0] = cost[0]
        for i in range(1, n):
            prev = dp[i - 1]
            run_min = np.minimum.accumulate(prev)
            # index of the running minimum: the last position where prev hit it
            run_arg = np.maximum.accumulate(np.where(prev <= run_min, np.arange(m), 0))
            dp[i, 1:] = cost[i, 1:] + run_min[:-1]
            arg[i, 1:] = run_arg[:-1]
        j = int(np.argmin(dp[n - 1]))
        total = dp[n - 1, j]
        if not np.isfinite(total) or (best is not None and total >= best[0] - 1e-12):
            continue
        chosen = [0] * n
        for i in range(n - 1, -1, -1):
            chosen[i] = j
            j = arg[i, j]
        best = (total, [(a_order[i], b_order[(chosen[i] + shift) % m]) for i in range(n)])

    pairs = best[1] if best else []
    pairs = [(j, i) for i, j in pairs] if swap else pairs
    return sorted(pairs)


def mirror(p, centre, axis):
    """*p* mirrored across the line through *centre* parallel to x ("x") or y ("y")."""
    x, y = p[0], p[1]
    if axis == "x":
        return (x, 2 * centre[1] - y) + tuple(p[2:])
    if axis == "y":
        return (2 * centre[0] - x, y) + tuple(p[2:])
    raise ValueError(axis)


def _find(points, q, tol):
    for k, p in enumerate(points):
        if abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol:
            return k
    return None


def symmetry_report(pairs, chip, package, centre=(0.0, 0.0), tol=1e-4):
    """
    For each pair, whether its mirror image across each centre axis is also
    a pair: {"x": [bool], "y": [bool]} in the order of *pairs*. A layout that
    is meant to be symmetric and shows False somewhere has a wire to check.
    """
    pair_set = set(pairs)
    out = {"x": [], "y": []}
    for ci, pj in pairs:
        for axis in ("x", "y"):
            mc = _find(chip, mirror(chip[ci], centre, axis), tol)
            mp = _find(package, mirror(package[pj], centre, axis), tol)
            out[axis].append(mc is not None and mp is not None and (mc, mp) in pair_set)
    return out


def mirrored_points(points, centre, axes=("x", "y"), tol=1e-6):
    """*points* plus their mirror images across *axes*, without duplicates."""
    out = list(points)
    for axis in axes:
        for p in list(out):
            q = mirror(p, centre, axis)
            if _find(out, q, tol) is None:
                out.append(q)
    return out
