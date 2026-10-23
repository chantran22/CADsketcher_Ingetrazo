# SPDX-License-Identifier: GPL-3.0-or-later
"""Sketch → closed profiles and open paths (CAD_Sketcher's «converter»).

Curves are walked through the points they SHARE (and through point-on-point
coincident constraints, which CAD_Sketcher's walker does not follow). Arcs
and circles become polylines; every vertex remembers whether it sits inside a
curve (``smooth``) so the solids built from it shade round.

Everything here is 2D, in workplane coordinates (metres). No Qt, no host.
"""
from __future__ import annotations

import math

from .model import ents

WELD = 1e-7


def _area2(pts) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
               - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))


def _inside(p, poly) -> bool:
    x, y = p
    hit = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def arc_points(c, p1, p2, segments: int) -> list:
    """Counter-clockwise from ``p1`` to ``p2`` around ``c``, ends included
    (the ends are the points themselves — no rounding on the radius)."""
    r = math.hypot(p1[0] - c[0], p1[1] - c[1])
    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    a1 = math.atan2(p2[1] - c[1], p2[0] - c[0])
    sweep = (a1 - a0) % (2 * math.pi)
    if sweep < 1e-9:
        sweep = 2 * math.pi
    # (a quarter turn that solved to 90.0000001° is still 8 of 32 segments)
    n = max(2, math.ceil(segments * sweep / (2 * math.pi) - 1e-6))
    mid = [(c[0] + r * math.cos(a0 + sweep * i / n),
            c[1] + r * math.sin(a0 + sweep * i / n)) for i in range(1, n)]
    return [tuple(p1)] + mid + [tuple(p2)]


def circle_points(c, r: float, segments: int) -> list:
    n = max(8, int(segments))
    return [(c[0] + r * math.cos(2 * math.pi * i / n),
             c[1] + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _nodes(sk: dict) -> dict:
    """point id → node id: points welded by a coincident constraint or
    sitting on the same spot are one node."""
    pts = [e for e in sk["ents"] if e["t"] == "point"]
    parent = {p["id"]: p["id"] for p in pts}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by = ents(sk)
    for c in sk["cons"]:
        if c["t"] == "coincident" and len(c["e"]) == 2 \
                and all(by.get(i, {}).get("t") == "point" for i in c["e"]):
            parent[root(c["e"][0])] = root(c["e"][1])
    seen: dict = {}
    for p in pts:
        key = (round(p["x"] / WELD), round(p["y"] / WELD))
        if key in seen:
            parent[root(p["id"])] = root(seen[key])
        else:
            seen[key] = p["id"]
    return {i: root(i) for i in parent}


def walk(sk: dict, segments: int = 32) -> tuple[list, list]:
    """``(loops, paths)`` of the non-construction curves.

    A loop is ``{"pts": [(u, v)…], "smooth": [bool…]}``, counter-clockwise,
    not repeating its first point; a path is the same, open. Curves meeting
    three at a point break the walk there and come out as open paths.
    """
    by = ents(sk)
    node = _nodes(sk)
    loops, paths, edges = [], [], []

    def pt(i):
        return (by[i]["x"], by[i]["y"])

    for e in sk["ents"]:
        if e.get("con") or e["t"] == "point":
            continue
        if e["t"] == "circle":
            if e["r"] > WELD:
                pts = circle_points(pt(e["c"]), e["r"], segments)
                loops.append({"pts": pts, "smooth": [True] * len(pts)})
        elif e["t"] == "line":
            if node[e["p1"]] != node[e["p2"]]:
                edges.append((node[e["p1"]], node[e["p2"]],
                              [pt(e["p1"]), pt(e["p2"])]))
        else:
            edges.append((node[e["p1"]], node[e["p2"]],
                          arc_points(pt(e["c"]), pt(e["p1"]), pt(e["p2"]),
                                     segments)))

    at: dict = {}
    for i, (a, b, _pts) in enumerate(edges):
        at.setdefault(a, []).append(i)
        at.setdefault(b, []).append(i)
    used = [False] * len(edges)

    def chain(start_node, first_edge):
        pts, smooth = [], []
        n, i = start_node, first_edge
        while i is not None and not used[i]:
            used[i] = True
            a, b, seg = edges[i]
            seg = seg if a == n else seg[::-1]
            if not pts:
                pts.append(seg[0])
                smooth.append(False)
            pts.extend(seg[1:])
            smooth.extend([True] * (len(seg) - 2) + [False])
            n = b if a == n else a
            nxt = [j for j in at[n] if not used[j]]
            i = nxt[0] if len(at[n]) == 2 and nxt else None
        return pts, smooth, n

    # open chains first: they start where a node is not a plain pass-through
    for n in list(at):
        if len(at[n]) != 2:
            for i in at[n]:
                if not used[i]:
                    pts, smooth, _end = chain(n, i)
                    paths.append({"pts": pts, "smooth": smooth})
    for i in range(len(edges)):
        if not used[i]:
            start = edges[i][0]
            pts, smooth, end = chain(start, i)
            if end == start and len(pts) > 3:
                loops.append({"pts": pts[:-1], "smooth": smooth[:-1]})
            else:
                paths.append({"pts": pts, "smooth": smooth})

    for lp in loops:
        if _area2(lp["pts"]) < 0:
            lp["pts"].reverse()
            lp["smooth"].reverse()
    return loops, paths


def regions(loops: list) -> list:
    """Closed loops → filled regions by the even-odd rule:
    ``[{"outer": loop, "holes": [loop…]}]``. A loop inside another is its
    hole; a loop inside a hole is a region again. Holes come out clockwise."""
    order = sorted(loops, key=lambda lp: -abs(_area2(lp["pts"])))
    depth, parent = [], []
    for i, lp in enumerate(order):
        inside = [j for j in range(i) if _inside(lp["pts"][0], order[j]["pts"])]
        depth.append(len(inside))
        parent.append(max(inside, key=lambda j: depth[j]) if inside else None)
    out, where = [], {}
    for i, lp in enumerate(order):
        if depth[i] % 2 == 0:
            where[i] = len(out)
            out.append({"outer": lp, "holes": []})
    for i, lp in enumerate(order):
        if depth[i] % 2 == 1 and parent[i] in where:
            out[where[parent[i]]]["holes"].append(
                {"pts": lp["pts"][::-1], "smooth": lp["smooth"][::-1]})
    return out


def crossing(loops: list) -> bool:
    """True when two loops cut through each other, or one cuts itself — a
    drawing with no inside and outside, so no solid."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    segs = []
    for k, lp in enumerate(loops):
        pts = lp["pts"]
        n = len(pts)
        segs.extend((k, i, n, pts[i], pts[(i + 1) % n]) for i in range(n))
    for i, (ka, ia, na, a, b) in enumerate(segs):
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        for kb, ib, _nb, c, d in segs[i + 1:]:
            if ka == kb and (ib - ia) % na in (0, 1, na - 1):
                continue                      # the same or a neighbouring edge
            if max(c[0], d[0]) < lo_x or min(c[0], d[0]) > hi_x \
                    or max(c[1], d[1]) < lo_y or min(c[1], d[1]) > hi_y:
                continue
            if orient(a, b, c) * orient(a, b, d) < -1e-18 \
                    and orient(c, d, a) * orient(c, d, b) < -1e-18:
                return True
    return False


def profile(sk: dict, segments: int = 32) -> list:
    """The filled regions of a sketch."""
    return regions(walk(sk, segments)[0])
