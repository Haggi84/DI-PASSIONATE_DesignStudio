# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Multi-chip modules: several dies on one die paddle.

Where each die goes (`arrange`) and whether the arrangement can be built
(`check`): every die on the paddle with a margin for the die-attach fillet,
no two dies closer than the gap the attach and the pick-and-place need, and
no die where the leads' bond fingers are.

All in the paddle's own frame, mm, die centres; FreeCAD-free.

The numbers are guidelines, not a standard: a die-to-die gap of 0.15 mm and
a paddle margin of 0.05 mm are what epoxy die attach with a fillet and a
pick-and-place tolerance of ±25 µm typically need. A process with a tighter
dispense or a film attach can go lower; they are parameters.
"""

from dataclasses import dataclass

from .elements import Issue

GAP = 0.15          # least die-to-die gap, mm
MARGIN = 0.05       # least distance from a die to the paddle edge, mm


@dataclass
class Die:
    name: str
    w: float            # along x, as placed (a die turned 90° has w and h swapped)
    h: float
    x: float = 0.0      # centre, paddle frame
    y: float = 0.0

    @property
    def rect(self):
        return (self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2)


def _gap(a, b):
    """The clear distance between two rectangles (negative: they overlap)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(bx0 - ax1, ax0 - bx1)
    dy = max(by0 - ay1, ay0 - by1)
    if dx < 0 and dy < 0:
        return max(dx, dy)          # overlap depth (negative)
    return (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5 if dx > 0 and dy > 0 else max(dx, dy)


def arrange(sizes, gap=0.3, direction="x"):
    """
    Centres for dies of *sizes* [(w, h)] in a row along *direction* ("x" or
    "y"), *gap* apart, the row centred on the paddle centre; or "diagonal":
    each next die beside the last one in x and y — corner to corner, so the
    wires of one die leave on two sides the other die does not block.
    Returns [(x, y)].
    """
    n = len(sizes)
    if n == 0:
        return []
    if direction == "diagonal":
        xs, ys = [0.0], [0.0]
        for (w0, h0), (w1, h1) in zip(sizes, sizes[1:]):
            xs.append(xs[-1] + (w0 + w1) / 2 + gap)
            ys.append(ys[-1] - (h0 + h1) / 2 - gap)
        cx = (min(x - w / 2 for x, (w, _h) in zip(xs, sizes)) + max(x + w / 2 for x, (w, _h) in zip(xs, sizes))) / 2
        cy = (min(y - h / 2 for y, (_w, h) in zip(ys, sizes)) + max(y + h / 2 for y, (_w, h) in zip(ys, sizes))) / 2
        return [(x - cx, y - cy) for x, y in zip(xs, ys)]
    k = 0 if direction == "x" else 1
    total = sum(s[k] for s in sizes) + gap * (n - 1)
    out, pos = [], -total / 2
    for s in sizes:
        c = pos + s[k] / 2
        out.append((c, 0.0) if k == 0 else (0.0, -c))     # a column runs top to bottom
        pos += s[k] + gap
    return out


def check(dies, paddle_w, paddle_h, keepouts=(), gap=GAP, margin=MARGIN):
    """
    [Issue] for *dies* (Die, paddle frame) on a paddle *paddle_w* x
    *paddle_h* centred on 0. *keepouts*: rectangles no die may touch (the
    bond fingers, a down-bond area).
    """
    out = []
    hx, hy = paddle_w / 2, paddle_h / 2
    for d in dies:
        x0, y0, x1, y1 = d.rect
        inside = min(x0 + hx, hx - x1, y0 + hy, hy - y1)
        if inside < -1e-9:
            out.append(Issue("error", d.name, f"{d.name} sticks out of the paddle by {-inside * 1000:.0f} µm."))
        elif inside < margin - 1e-9:
            out.append(Issue("warning", d.name, f"{d.name} is {inside * 1000:.0f} µm from the paddle edge "
                                                f"(at least {margin * 1000:.0f} µm for the die-attach fillet)."))
        for k in keepouts:
            if _gap(d.rect, k) < 0:
                out.append(Issue("error", d.name, f"{d.name} lies on a keep-out (bond fingers)."))
                break
    for i, a in enumerate(dies):
        for b in dies[i + 1:]:
            g = _gap(a.rect, b.rect)
            if g < 0:
                out.append(Issue("error", a.name, f"{a.name} and {b.name} overlap by {-g * 1000:.0f} µm."))
            elif g < gap - 1e-9:
                out.append(Issue("error", a.name, f"{a.name} and {b.name} are {g * 1000:.0f} µm apart "
                                                  f"(at least {gap * 1000:.0f} µm for die attach and placement)."))
    return out


def footprint(dies):
    """(width, height) of the smallest rectangle round *dies*, centred where they are."""
    if not dies:
        return 0.0, 0.0
    x0 = min(d.rect[0] for d in dies)
    y0 = min(d.rect[1] for d in dies)
    x1 = max(d.rect[2] for d in dies)
    y1 = max(d.rect[3] for d in dies)
    return x1 - x0, y1 - y0


def share_leads(centres, need, leads, step=0.02, rounds=2000, preset=None):
    """
    Share the *leads* [(x, y)] out among dies with centres *centres*, die k
    needing *need*[k]: each lead to the die it is nearest after subtracting
    that die's reach w_k, the reach of a die that has too few grown until all
    have enough (a weighted Voronoi split; on a ring of leads it gives each
    die one contiguous arc). Spare leads stay with the die nearest them.
    *preset*: {lead index: die index} for leads a die must have (a pin
    list's fixed leads). Returns [[lead index]] per die. Raises ValueError
    when there are fewer leads than pads.
    """
    preset = preset or {}
    if sum(need) > len(leads):
        raise ValueError(f"{sum(need)} pads to bond, {len(leads)} leads")
    w = [0.0] * len(centres)

    def assign():
        out = [[] for _ in centres]
        for j, (x, y) in enumerate(leads):
            if j in preset:
                out[preset[j]].append(j)
                continue
            k = min(range(len(centres)),
                    key=lambda i: ((x - centres[i][0]) ** 2 + (y - centres[i][1]) ** 2) ** 0.5 - w[i])
            out[k].append(j)
        return out

    for _ in range(rounds):
        share = assign()
        short = [k for k in range(len(centres)) if len(share[k]) < need[k]]
        if not short:
            return share
        for k in short:
            w[k] += step
    raise ValueError("could not share the leads out")


def _ang(p, c):
    import math
    return math.atan2(p[1] - c[1], p[0] - c[0]) % (2 * math.pi)


def _monotone(a_ang, b_ang):
    """Order-preserving match of every a to a distinct b (len(a) <= len(b)),
    least total angular offset. Both lists already in order. [(i, j)]."""
    n, m = len(a_ang), len(b_ang)
    if n == 0:
        return []
    INF = float("inf")
    cost = [[abs(((a - b) + 3.141592653589793) % 6.283185307179586 - 3.141592653589793) for b in b_ang]
            for a in a_ang]
    dp = [[INF] * m for _ in range(n)]
    arg = [[-1] * m for _ in range(n)]
    for j in range(m):
        dp[0][j] = cost[0][j]
    for i in range(1, n):
        best, bj = INF, -1
        for j in range(m):
            if j - 1 >= 0 and dp[i - 1][j - 1] < best:
                best, bj = dp[i - 1][j - 1], j - 1
            if best < INF:
                dp[i][j], arg[i][j] = cost[i][j] + best, bj
    j = min(range(m), key=lambda k: dp[n - 1][k])
    if dp[n - 1][j] == INF:
        raise ValueError("no order-preserving match")
    out = []
    for i in range(n - 1, -1, -1):
        out.append((i, j))
        j = arg[i][j]
    return out[::-1]


def pair_anchored(pads, leads, anchors, centre):
    """
    [(pad index, lead index)]: every pad to a lead, crossing-free round
    *centre*, the *anchors* [(pad index, lead index)] kept as they are (a
    pin list's fixed leads). Between two neighbouring anchors the free pads
    go, in order, to the free leads between the anchors' leads. Raises
    ValueError when the anchors cross each other or leave too few leads
    between two of them.
    """
    if not anchors:
        from .symmetry import pair
        return pair(pads, leads, centre)
    po = sorted(range(len(pads)), key=lambda i: _ang(pads[i], centre))
    lo = sorted(range(len(leads)), key=lambda j: _ang(leads[j], centre))
    a_pad = {p: l for p, l in anchors}
    # rotate so the first anchor (in pad order) comes first
    first = next(k for k, i in enumerate(po) if i in a_pad)
    po = po[first:] + po[:first]
    lstart = lo.index(a_pad[po[0]])
    lo = lo[lstart:] + lo[:lstart]
    lpos = {j: k for k, j in enumerate(lo)}
    anchor_seq = [i for i in po if i in a_pad]
    lead_pos = [lpos[a_pad[i]] for i in anchor_seq]
    if lead_pos != sorted(lead_pos):
        raise ValueError("the fixed leads cross each other: their order round the die is not the pads' order")
    out = [(i, a_pad[i]) for i in anchor_seq]
    ppos = {i: k for k, i in enumerate(po)}
    for k, i in enumerate(anchor_seq):
        nxt = anchor_seq[k + 1] if k + 1 < len(anchor_seq) else None
        p_lo, p_hi = ppos[i] + 1, (ppos[nxt] if nxt is not None else len(po))
        l_lo, l_hi = lpos[a_pad[i]] + 1, (lpos[a_pad[nxt]] if nxt is not None else len(lo))
        seg_p, seg_l = po[p_lo:p_hi], lo[l_lo:l_hi]
        if len(seg_p) > len(seg_l):
            raise ValueError(f"{len(seg_p)} pads lie between two fixed pins, but only {len(seg_l)} leads do")
        a_ang = [_ang(pads[x], centre) for x in seg_p]
        b_ang = [_ang(leads[x], centre) for x in seg_l]
        out += [(seg_p[a], seg_l[b]) for a, b in _monotone(a_ang, b_ang)]
    return out


def _cost(pairs, pads, leads, centre):
    total = 0.0
    for i, j in pairs:
        d = _ang(pads[i], centre) - _ang(leads[j], centre)
        total += abs((d + 3.141592653589793) % 6.283185307179586 - 3.141592653589793)
    return total


def assign_arcs(dies, leads, paddle_centre=(0.0, 0.0), max_dies=3):
    """
    Every die's pads to leads, crossing-free across dies too: the ring of
    leads is cut into one contiguous arc per die (in the order the dies lie
    round the paddle centre), and each die is paired within its arc round
    its own centre, its anchors kept (pair_anchored). Of all cuts that work,
    the one with the least total angular offset is taken — an exhaustive
    search, fine for two or three dies (32 leads, two dies: 1024 cuts).

    *dies*: [{"centre": (x, y), "pads": [(x, y)], "anchors": {pad index: lead index}}],
    *leads*: [(x, y)]. Returns [[(pad index, lead index)]] per die. Raises
    ValueError when no cut works, naming why for the best try.
    """
    import itertools
    n, N = len(dies), len(leads)
    if n == 0:
        return []
    if n > max_dies:
        raise ValueError(f"exhaustive arc search is for up to {max_dies} dies")
    ring = sorted(range(N), key=lambda j: _ang(leads[j], paddle_centre))
    order = sorted(range(n), key=lambda k: _ang(dies[k]["centre"], paddle_centre))
    best, why = None, "no cut of the lead ring gives every die enough leads"
    need = [len(d["pads"]) for d in dies]
    for cuts in itertools.combinations(range(N), n):
        for rot in range(n):                     # which die gets the arc that starts at cuts[0]
            arcs = {}
            for k in range(n):
                a, b = cuts[k], cuts[(k + 1) % n] if k + 1 < n else cuts[0] + N
                arcs[order[(k + rot) % n]] = [ring[x % N] for x in range(a, b)]
            if any(len(arcs[k]) < need[k] for k in range(n)):
                continue
            if any(l not in arcs[k] for k in range(n) for l in dies[k].get("anchors", {}).values()):
                continue
            total, result = 0.0, []
            try:
                for k in range(n):
                    mine = arcs[k]
                    local = {j: x for x, j in enumerate(mine)}
                    anchors = [(i, local[l]) for i, l in dies[k].get("anchors", {}).items()]
                    pts = [leads[j] for j in mine]
                    pairs = pair_anchored(dies[k]["pads"], pts, anchors, dies[k]["centre"])
                    total += _cost(pairs, dies[k]["pads"], pts, dies[k]["centre"])
                    result.append([(i, mine[j]) for i, j in pairs])
            except ValueError as exc:
                why = str(exc)
                continue
            if best is None or total < best[0] - 1e-12:
                best = (total, result)
    if best is None:
        raise ValueError(why)
    return best[1]
