# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Checking a bond plan against MIL-STD-883 TM 2010 and manufacturing rules.

Two kinds of finding, never mixed up:

  reject     a criterion of MIL-STD-883 Method 2010 (internal visual),
             for class level B (Condition B) or S (Condition A) — a device
             built like this fails inspection;
  guideline  an assembly-house rule of thumb (maximum wire length, minimum
             loop height) — worth a look, not a standard.

Every finding quotes its clause. The numbers (Method 2010.11):

  3.2.1.1a  ball bond diameter 2.0 – 5.0 x wire diameter
  3.2.1.1c  ball: wire centre exit within the pad opening
  3.2.1.2a  ultrasonic wedge: width 1.2 – 3.0 x, length 1.5 – 6.0 x
  3.2.1.2b  thermosonic wedge/stitch: width 1.5 – 3.0 x, length 1.5 – 6.0 x
  3.2.1.4a  bond on the die: >= 75 % (S) / >= 50 % (B) within the pad opening
  3.2.1.4d  tail <= 2 wire diameters
  3.2.1.4e  bond completely within the package post
  3.2.1.4f  bond to another bond: >= 0.25 mil (S) / a visible gap (B)
  3.2.2a    wire to other wires, bonds, posts, exposed die area, lid plane:
            >= 2 wire diameters (S) / >= 1 (B); class S allows 1.0 mil within
            5.0 mil of the bond on the die
  3.2.2d    no straight-line run without an arc
  3.2.2e    no wires crossing seen from above, except at different heights
            with >= 2 wire diameters clearance

Geometry is passed in as plain numbers (see WireSpec, Env) so the checks
work on anything — the workbench's wires, or a plan from elsewhere.
"""

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from . import bondwire as bw
from .pads import point_in_polygon

MIL = "MIL-STD-883 TM 2010"
MIL_1 = 0.0254            # 1 mil, mm
CLASSES = {
    "B": {"label": "Class B (Condition B)", "clear": 1.0, "on_die": 0.50, "bond_gap": 1e-6, "near": None},
    "S": {"label": "Class S (Condition A)", "clear": 2.0, "on_die": 0.75, "bond_gap": 0.25 * MIL_1,
          "near": (5.0 * MIL_1, 1.0 * MIL_1)},
}
GUIDE_MAX_LENGTH_D = 200.0         # wire length <= 200 x d (5 mm for 25 um)
GUIDE_MIN_LOOP_BALL = 0.075        # neck damage below about 75 um (forward ball bonding)
GUIDE_MIN_LOOP_WEDGE_D = 2.0


@dataclass
class WireSpec:
    name: str
    start: tuple                   # chip-side bond, on the pad surface (global mm)
    end: tuple                     # lead-side bond
    params: object                 # core.bondwire.WireParams
    chip_pad: list                 # pad opening outline [(x, y)], global
    lead_pad: list                 # lead finger outline [(x, y)], global
    chip_pad_id: str = ""
    lead_pad_id: str = ""
    die_to_die: bool = False       # the second bond is on another die's pad (multi-chip module)


@dataclass
class Env:
    lid_z: float = None                             # underside of the lid, if there is one
    die_edges: list = field(default_factory=list)   # [((x0, y0, z), (x1, y1, z))] die top perimeter
    posts: list = field(default_factory=list)       # [(pad id, outline [(x, y)], z)] every lead finger


@dataclass
class Finding:
    wire: str
    rule: str
    ok: bool
    severity: str                  # "reject" or "guideline"
    message: str
    ref: str = ""


# ── geometry helpers ────────────────────────────────────────────────────────

def _footprint_points(fp, n=24):
    """Points covering a bond footprint, for area fractions."""
    if fp[0] == "circle":
        (cx, cy), r = fp[1], fp[2]
        g = np.linspace(-r, r, n)
        pts = [(cx + x, cy + y) for x in g for y in g if x * x + y * y <= r * r]
    else:
        (cx, cy), (ux, uy), length, width = fp[1], fp[2], fp[3], fp[4]
        sx, sy = -uy, ux
        r = width / 2.0
        half = max(length / 2.0 - r, 0.0)
        pts = []
        for a in np.linspace(-length / 2.0, length / 2.0, n):
            for b in np.linspace(-r, r, n):
                excess = max(abs(a) - half, 0.0)
                if excess * excess + b * b <= r * r + 1e-18:
                    pts.append((cx + ux * a + sx * b, cy + uy * a + sy * b))
    return pts


def fraction_inside(fp, outline):
    pts = _footprint_points(fp)
    poly = np.asarray(outline, dtype=float)
    if not pts or len(poly) < 3:
        return 0.0
    return sum(point_in_polygon(x, y, poly) for x, y in pts) / len(pts)


def _seg_dist(p, a, b):
    """Distance from points p (n, 3) to segment ab."""
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-30), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _outline_points(outline, z, n_per_edge=8):
    pts = []
    for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]):
        for t in np.linspace(0, 1, n_per_edge, endpoint=False):
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z))
    return np.asarray(pts, dtype=float)


def _segments_cross_2d(p, q):
    """True if the top views of polylines p and q (n, 3) cross."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    for i in range(len(p) - 1):
        a, b = p[i], p[i + 1]
        for j in range(len(q) - 1):
            c, d = q[j], q[j + 1]
            d1, d2 = ccw(a, b, c), ccw(a, b, d)
            d3, d4 = ccw(c, d, a), ccw(c, d, b)
            if d1 * d2 < 0 and d3 * d4 < 0:
                return True
    return False


# ── the checks ──────────────────────────────────────────────────────────────

def check(wires, env=None, cls="B", samples=120):
    """{wire name: [Finding]} for every wire in *wires* (WireSpec)."""
    env = env or Env()
    rules = CLASSES[cls]
    out = {w.name: [] for w in wires}
    lines = {w.name: np.array([(q.x, q.y, q.z) for q in bw.sample(w.start, w.end, w.params, samples)])
             for w in wires}

    def add(w, rule, ok, severity, message, ref=""):
        out[w.name].append(Finding(w.name, rule, bool(ok), severity, message, ref))

    for w in wires:
        p, d = w.params, w.params.wire_d
        fps = bw.footprints(w.start, w.end, p)
        # bond proportions
        if p.bond == bw.BALL:
            r = p.ball_d / d
            add(w, "ball diameter", 2.0 <= r <= 5.0, "reject",
                f"ball {p.ball_d * 1000:.1f} um = {r:.2f} d (2.0 – 5.0 d)", f"{MIL} 3.2.1.1a")
            add(w, "ball centre on the pad", point_in_polygon(w.start[0], w.start[1], np.asarray(w.chip_pad)),
                "reject", "wire centre exit within the pad opening", f"{MIL} 3.2.1.1c")
            first_lim, first_kind = None, None
        else:
            first_lim, first_kind = (1.2, 3.0), "ultrasonic wedge"
        second_lim = (1.2, 3.0) if p.bond == bw.WEDGE else (1.5, 3.0)
        second_kind = "ultrasonic wedge" if p.bond == bw.WEDGE else "thermosonic stitch"
        for label, width, length_, lim, kind, ref in (
                ("first bond", p.wedge_w, p.wedge_l, first_lim, first_kind, "3.2.1.2a"),
                ("second bond", p.stitch_w, p.stitch_l, second_lim, second_kind,
                 "3.2.1.2a" if p.bond == bw.WEDGE else "3.2.1.2b")):
            if lim is None:
                continue
            rw, rl = width / d, length_ / d
            add(w, f"{label} size", lim[0] <= rw <= lim[1] and 1.5 <= rl <= 6.0, "reject",
                f"{kind} {rw:.2f} d wide ({lim[0]} – {lim[1]}), {rl:.2f} d long (1.5 – 6.0)", f"{MIL} {ref}")
        add(w, "tail", p.tail <= 2.0 * d + 1e-12, "reject",
            f"tail {p.tail / d:.2f} d (<= 2 d)", f"{MIL} 3.2.1.4d")
        # bonds on their pads
        f = fraction_inside(fps["first"], w.chip_pad)
        add(w, "bond on the die pad", f >= rules["on_die"] - 1e-9, "reject",
            f"{f * 100:.0f} % of the chip bond within the pad opening (>= {rules['on_die'] * 100:.0f} %)",
            f"{MIL} 3.2.1.4a")
        g = fraction_inside(fps["second"], w.lead_pad)
        if w.die_to_die:
            # die to die: the second bond is on a die pad too, so the die-pad rule applies to it
            add(w, "second bond on the die pad", g >= rules["on_die"] - 1e-9, "reject",
                f"{g * 100:.0f} % of the second bond within the other die's pad opening "
                f"(>= {rules['on_die'] * 100:.0f} %)", f"{MIL} 3.2.1.4a")
        else:
            add(w, "bond on the post", g >= 0.999, "reject",
                f"{g * 100:.0f} % of the lead bond on the lead (100 %)", f"{MIL} 3.2.1.4e")
        # an arc
        top = lines[w.name][:, 2].max()
        rise = top - max(lines[w.name][0, 2], lines[w.name][-1, 2])
        add(w, "arc", rise >= d, "reject", f"loop rises {rise * 1000:.1f} um above its bonds",
            f"{MIL} 3.2.2d")
        # guidelines
        length = bw.length(w.start, w.end, p)
        add(w, "wire length", length <= GUIDE_MAX_LENGTH_D * d, "guideline",
            f"{length:.3f} mm (guideline <= {GUIDE_MAX_LENGTH_D:.0f} d = {GUIDE_MAX_LENGTH_D * d:.2f} mm)")
        min_loop = GUIDE_MIN_LOOP_BALL if p.bond == bw.BALL else GUIDE_MIN_LOOP_WEDGE_D * d
        add(w, "loop height", p.loop_h >= min_loop - 1e-12, "guideline",
            f"{p.loop_h * 1000:.0f} um (guideline >= {min_loop * 1000:.0f} um"
            f"{', neck damage below' if p.bond == bw.BALL else ''})")
        # lid
        if env.lid_z is not None:
            gap = env.lid_z - (top + d / 2.0)
            add(w, "lid clearance", gap >= rules["clear"] * d, "reject",
                f"{gap * 1000:.1f} um below the lid (>= {rules['clear']:.0f} d = {rules['clear'] * d * 1000:.0f} um)",
                f"{MIL} 3.2.2a")
        # die edge: the loop must clear the die's top edges
        if env.die_edges:
            pts = lines[w.name]
            near_start = np.linalg.norm(pts[:, :2] - np.asarray(w.start[:2]), axis=1) > max(p.ball_d, p.wedge_l)
            gap = min(_seg_dist(pts[near_start], np.asarray(a), np.asarray(b)).min()
                      for a, b in env.die_edges) - d / 2.0 if near_start.any() else float("inf")
            add(w, "die edge clearance", gap >= rules["clear"] * d, "reject",
                f"{gap * 1000:.1f} um to the die edge (>= {rules['clear']:.0f} d)", f"{MIL} 3.2.2a")
        # other posts
        others = [(pid, outl, z) for pid, outl, z in env.posts if pid != w.lead_pad_id]
        if others:
            gap = min(np.min(np.linalg.norm(lines[w.name][:, None, :] - _outline_points(o, z)[None, :, :], axis=2))
                      for _pid, o, z in others) - d / 2.0
            add(w, "clearance to other leads", gap >= rules["clear"] * d, "reject",
                f"{gap * 1000:.1f} um to the nearest other lead (>= {rules['clear']:.0f} d)", f"{MIL} 3.2.2a")

    # pairs of wires
    for a, b in itertools.combinations(wires, 2):
        common = a.chip_pad_id == b.chip_pad_id or a.lead_pad_id == b.lead_pad_id
        if common:
            continue                                   # common wires are excluded
        pa, pb = lines[a.name], lines[b.name]
        if (pa.min(0) - pb.max(0) > 0.5).any() or (pb.min(0) - pa.max(0) > 0.5).any():
            continue                                   # far apart
        dist = np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=2)
        d = max(a.params.wire_d, b.params.wire_d)
        gap = dist.min() - (a.params.wire_d + b.params.wire_d) / 2.0
        need = rules["clear"] * d
        if rules["near"] is not None:
            radius, relaxed = rules["near"]
            i, j = np.unravel_index(np.argmin(dist), dist.shape)
            near_die = (np.linalg.norm(pa[i, :2] - np.asarray(a.start[:2])) <= radius + a.params.ball_d / 2
                        or np.linalg.norm(pb[j, :2] - np.asarray(b.start[:2])) <= radius + b.params.ball_d / 2)
            if near_die:
                need = relaxed
        ok = gap >= need
        msg = f"{gap * 1000:.1f} um between {a.name} and {b.name} (>= {need * 1000:.1f} um)"
        for w in (a, b):
            add_to = out[w.name]
            add_to.append(Finding(w.name, "wire to wire", ok, "reject", msg, f"{MIL} 3.2.2a"))
        if _segments_cross_2d(pa, pb):
            tiers = abs(a.end[2] - b.end[2]) > d
            ok = tiers and gap >= 2.0 * d
            msg = f"{a.name} and {b.name} cross seen from above" + \
                  ("" if ok else " (only allowed at different heights with >= 2 d clearance)")
            for w in (a, b):
                out[w.name].append(Finding(w.name, "crossing", ok, "reject", msg, f"{MIL} 3.2.2e"))
        # bond to bond
        for (wa, ka), (wb, kb) in (((a, "first"), (b, "first")), ((a, "second"), (b, "second"))):
            ca = wa.start if ka == "first" else wa.end
            cb = wb.start if kb == "first" else wb.end
            fa = bw.footprints(wa.start, wa.end, wa.params)[ka]
            fb = bw.footprints(wb.start, wb.end, wb.params)[kb]
            if math.hypot(ca[0] - cb[0], ca[1] - cb[1]) - _radius(fa) - _radius(fb) >= 0.2:
                continue
            # The real gap between the two footprints — a wedge is narrower
            # across than along, so side by side at fine pitch it fits where
            # a circle of its length would not.
            pa_, pb_ = np.asarray(_footprint_points(fa, 32)), np.asarray(_footprint_points(fb, 32))
            gap = np.min(np.linalg.norm(pa_[:, None, :] - pb_[None, :, :], axis=2))
            if gap < 0.2:
                ok = gap >= rules["bond_gap"]
                msg = f"{gap * 1000:.1f} um between the {ka} bonds of {wa.name} and {wb.name}"
                for w in (wa, wb):
                    out[w.name].append(Finding(w.name, "bond to bond", ok, "reject", msg, f"{MIL} 3.2.1.4f"))
    return out


def _radius(fp):
    """A footprint's reach from its centre (conservative)."""
    return fp[2] if fp[0] == "circle" else fp[3] / 2.0


def summary(findings):
    """(rejects, guideline warnings) counted over every wire."""
    rej = sum(1 for fs in findings.values() for f in fs if not f.ok and f.severity == "reject")
    gl = sum(1 for fs in findings.values() for f in fs if not f.ok and f.severity == "guideline")
    return rej, gl
