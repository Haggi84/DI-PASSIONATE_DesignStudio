# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Step 3: route kicad/expander_eval.kicad_pcb with Freerouting (Specctra DSN
out, session in), then pour the ground on the outer layers and fill.

In1 (GND) and In2 (+3.3 V) are planes only (layer type "power"): every SMD
pad of those nets first gets a short track and a via to its plane (fan-out,
which Freerouting does not do), then the router routes the signals on F.Cu
and B.Cu. The outer ground pours are left out while routing (to the router
they would be planes that block both outer layers) and filled afterwards.

    python3 route_board.py /path/to/freerouting.jar
(KiCad's Python; Java 17+; on a machine without a display: xvfb-run python3 ...)
"""

import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "kicad", "expander_eval.kicad_pcb")


def _tokens(text):
    import re
    return re.findall(r'"[^"]*"|\(|\)|[^\s()]+', text)


def _tree(tokens):
    stack = [[]]
    for t in tokens:
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t.strip('"'))
    return stack[0][0]


def import_ses(board, path):
    """The wires and vias of a Specctra session file onto *board* (KiCad 7's
    ImportSpecctraSES works only inside the PCB editor)."""
    tree = _tree(_tokens(open(path, encoding="utf-8").read()))
    routes = next(x for x in tree if isinstance(x, list) and x and x[0] == "routes")
    res = next(x for x in routes if isinstance(x, list) and x[0] == "resolution")
    unit = {"um": 1e-3, "mm": 1.0, "mil": 0.0254, "inch": 25.4}[res[1]] / float(res[2])   # mm per count
    netout = next(x for x in routes if isinstance(x, list) and x[0] == "network_out")
    layers = {board.GetLayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}
    nets = board.GetNetsByName()

    def pt(x, y):
        return pcbnew.VECTOR2I(pcbnew.FromMM(float(x) * unit), pcbnew.FromMM(-float(y) * unit))

    n_t = n_v = 0
    for net in netout[1:]:
        ni = nets[net[1]] if net[1] in nets else None
        for item in net[2:]:
            if item[0] == "wire":
                path = next(x for x in item if isinstance(x, list) and x[0] == "path")
                layer, width, xy = layers[path[1]], float(path[2]) * unit, path[3:]
                for k in range(0, len(xy) - 2, 2):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pt(xy[k], xy[k + 1]))
                    t.SetEnd(pt(xy[k + 2], xy[k + 3]))
                    t.SetWidth(pcbnew.FromMM(width))
                    t.SetLayer(layer)
                    if ni is not None:
                        t.SetNet(ni)
                    board.Add(t)
                    n_t += 1
            elif item[0] == "via":
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pt(item[2], item[3]))
                v.SetWidth(pcbnew.FromMM(0.6))
                v.SetDrill(pcbnew.FromMM(0.3))
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                if ni is not None:
                    v.SetNet(ni)
                board.Add(v)
                n_v += 1
    return n_t, n_v


PLANE_NETS = ("GND", "+3V3")      # In1 and In2: planes only (layer type "power"), reached by vias


def _mm(v):
    return pcbnew.ToMM(v)


def _rect_dist(px, py, r):
    """Distance from (px, py) to the rectangle r = (x0, y0, x1, y1), 0 inside."""
    dx = max(r[0] - px, 0.0, px - r[2])
    dy = max(r[1] - py, 0.0, py - r[3])
    return (dx * dx + dy * dy) ** 0.5


def fanout(board, via_d=0.6, drill=0.3, width=0.3, clearance=0.2):
    """A short track and a via from every SMD pad of a plane net to its plane:
    the router (Freerouting) does no fan-out to planes of its own. Each via
    goes where it keeps *clearance* from all other copper and the edge."""
    x0, y0, x1, y1 = BOARD_MM(board)
    pads = [p for p in board.GetPads()]
    rects = []
    for p in pads:
        bb = p.GetBoundingBox()
        rects.append(((_mm(bb.GetLeft()), _mm(bb.GetTop()), _mm(bb.GetRight()), _mm(bb.GetBottom())),
                      p.GetNetname(), p))
    placed = []                       # (x, y, r, net)
    nets = board.GetNetsByName()
    added = 0
    for p in pads:
        net = p.GetNetname()
        if net not in PLANE_NETS or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
            continue
        bb = p.GetBoundingBox()
        r = (_mm(bb.GetLeft()), _mm(bb.GetTop()), _mm(bb.GetRight()), _mm(bb.GetBottom()))
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        if r[2] - r[0] > 1.6 and r[3] - r[1] > 1.6:          # an exposed pad: vias in the pad
            for dx in (-0.5, 0.5):
                for dy in (-0.5, 0.5):
                    placed.append((cx + dx, cy + dy, via_d / 2, net))
                    _via(board, cx + dx, cy + dy, via_d, drill, nets[net])
                    added += 1
            continue
        fc = p.GetParent().GetPosition()
        ox, oy = cx - _mm(fc.x), cy - _mm(fc.y)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        dirs.sort(key=lambda d: -(d[0] * ox + d[1] * oy))   # away from the part's centre first
        done = False
        for step in (0.55, 0.8, 1.1, 1.5):
            for dx, dy in dirs:
                hx = (r[2] - r[0]) / 2 if dx else (r[3] - r[1]) / 2
                vx, vy = cx + dx * (hx + step), cy + dy * (hx + step)
                if not (x0 + 0.6 < vx < x1 - 0.6 and y0 + 0.6 < vy < y1 - 0.6):
                    continue
                ok = True
                for rr, n, q in rects:
                    if q is p:
                        continue
                    need_v = via_d / 2 + clearance + (0.0 if n == net else 0.0)
                    if n != net and _rect_dist(vx, vy, rr) < need_v:
                        ok = False
                        break
                    if n != net:                         # the track from the pad to the via
                        for k in range(1, 6):
                            t = k / 6
                            if _rect_dist(cx + (vx - cx) * t, cy + (vy - cy) * t, rr) < width / 2 + clearance:
                                ok = False
                                break
                        if not ok:
                            break
                if ok:
                    for (ax, ay, ar, an) in placed:
                        if ((ax - vx) ** 2 + (ay - vy) ** 2) ** 0.5 < ar + via_d / 2 + clearance:
                            ok = False
                            break
                if ok:
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(cx), pcbnew.FromMM(cy)))
                    t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(vx), pcbnew.FromMM(vy)))
                    t.SetWidth(pcbnew.FromMM(width))
                    t.SetLayer(pcbnew.F_Cu)
                    t.SetNet(nets[net])
                    board.Add(t)
                    _via(board, vx, vy, via_d, drill, nets[net])
                    placed.append((vx, vy, via_d / 2, net))
                    rects.append(((vx - width / 2, vy - width / 2, vx + width / 2, vy + width / 2), net, None))
                    added += 1
                    done = True
                    break
            if done:
                break
        if not done:                                     # a QFN ground lead: straight in to its exposed pad
            ep = next((q for q in p.GetParent().Pads() if q is not p and q.GetNetname() == net
                       and _mm(q.GetBoundingBox().GetWidth()) > 1.6), None)
            if ep is not None:
                eb = ep.GetBoundingBox()
                ex, ey = _mm(eb.GetCenter().x), _mm(eb.GetCenter().y)
                hx, hy = _mm(eb.GetWidth()) / 2 - 0.2, _mm(eb.GetHeight()) / 2 - 0.2
                tx = min(max(cx, ex - hx), ex + hx) if abs(cx - ex) > abs(cy - ey) else cx
                ty = min(max(cy, ey - hy), ey + hy) if abs(cy - ey) >= abs(cx - ex) else cy
                if abs(cx - ex) > abs(cy - ey):
                    tx = ex - hx if cx < ex else ex + hx
                else:
                    ty = ey - hy if cy < ey else ey + hy
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(cx), pcbnew.FromMM(cy)))
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(tx), pcbnew.FromMM(ty)))
                t.SetWidth(pcbnew.FromMM(0.25))
                t.SetLayer(pcbnew.F_Cu)
                t.SetNet(nets[net])
                board.Add(t)
                done = True
        if not done:
            print(f"fan-out: no room beside {p.GetParent().GetReference()} pad {p.GetNumber()} ({net})")
    return added


def snap_ends(board, reach=0.3):
    """A track end that stops just short of a pad of its net (the session's
    coordinates are rounded) gets a short piece to the pad's centre."""
    pads = list(board.GetPads())
    added = 0
    for t in [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]:
        for end in (t.GetStart(), t.GetEnd()):
            for p in pads:
                if p.GetNetCode() != t.GetNetCode() or not p.IsOnLayer(t.GetLayer()):
                    continue
                if p.HitTest(end):
                    break
                bb = p.GetBoundingBox()
                d = _rect_dist(_mm(end.x), _mm(end.y), (_mm(bb.GetLeft()), _mm(bb.GetTop()),
                                                        _mm(bb.GetRight()), _mm(bb.GetBottom())))
                if 0.001 < d < reach:
                    s = pcbnew.PCB_TRACK(board)
                    s.SetStart(end)
                    s.SetEnd(p.GetPosition())
                    s.SetWidth(t.GetWidth())
                    s.SetLayer(t.GetLayer())
                    s.SetNet(t.GetNet())
                    board.Add(s)
                    added += 1
                    break
    return added


def _via(board, x, y, d, drill, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    v.SetWidth(pcbnew.FromMM(d))
    v.SetDrill(pcbnew.FromMM(drill))
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNet(net)
    board.Add(v)


def BOARD_MM(board):
    bb = board.GetBoardEdgesBoundingBox()
    return _mm(bb.GetLeft()), _mm(bb.GetTop()), _mm(bb.GetRight()), _mm(bb.GetBottom())


def main(jar, passes=60, tries=4):
    """Routes; Freerouting does not always finish a dense corner, so it tries again."""
    for k in range(tries):
        left = route_once(jar, passes)
        if left == 0:
            return
        print(f"try {k + 1}: {left} unconnected, routing again")
    raise SystemExit(f"{left} connections left unrouted after {tries} tries")


def route_once(jar, passes):
    board = pcbnew.LoadBoard(PCB)
    for t in list(board.GetTracks()):                    # start from the placed board
        board.Remove(t)
    for z in board.Zones():
        z.UnFill()
    outer = [z for z in board.Zones() if z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu)]
    kept = []
    for z in outer:
        kept.append(z.Duplicate())
        board.Remove(z)
    print(f"fan-out: {fanout(board)} vias to the planes")
    dsn = os.path.join(HERE, "kicad", "expander_eval.dsn")
    ses = os.path.join(HERE, "kicad", "expander_eval.ses")
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        raise RuntimeError("DSN export failed")
    subprocess.run(["java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", str(passes)], check=True,
                   timeout=900)
    for t in list(board.GetTracks()):                    # the session holds all wiring, the fan-out too
        board.Remove(t)
    import_ses(board, ses)
    for z in kept:
        board.Add(z)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    for f in (dsn, ses):
        os.remove(f)
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    board.BuildConnectivity()
    left = board.GetConnectivity().GetUnconnectedCount(True)
    print(f"routed: {len(tracks)} tracks, {len(vias)} vias, {left} unconnected")
    return left


if __name__ == "__main__":
    main(sys.argv[1])
