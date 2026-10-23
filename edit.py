# SPDX-License-Identifier: GPL-3.0-or-later
"""Editing tools on a sketch: Trim and Offset.

Both work on the sketch as plain 2D geometry (workplane metres) and leave
behind entities AND the constraints that keep the result meaningful when the
sketch is edited later: a trimmed end stays on the curve that cut it, an
offset line stays parallel to its source, an offset arc keeps its centre.
No Qt, no host.
"""
from __future__ import annotations

import math

from . import convert, model
from .model import SketchError

TOL = 1e-7
TWO_PI = 2.0 * math.pi


# ---- carriers and intersections -------------------------------------------------

def _geom(by: dict, e: dict) -> tuple:
    """``("seg", a, b)`` or ``("circ", c, r, a0, sweep)`` (a circle sweeps 2π;
    an arc runs counter-clockwise from ``a0``)."""
    def pt(i):
        return (by[i]["x"], by[i]["y"])
    if e["t"] == "line":
        return ("seg", pt(e["p1"]), pt(e["p2"]))
    c = pt(e["c"])
    if e["t"] == "circle":
        return ("circ", c, float(e["r"]), 0.0, TWO_PI)
    p1, p2 = pt(e["p1"]), pt(e["p2"])
    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    sweep = (math.atan2(p2[1] - c[1], p2[0] - c[0]) - a0) % TWO_PI
    return ("circ", c, math.hypot(p1[0] - c[0], p1[1] - c[1]), a0,
            sweep if sweep > 1e-12 else TWO_PI)


def _line_circle(a, b, c, r) -> list:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-24:
        return []
    t = ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / L2
    fx, fy = a[0] + t * dx, a[1] + t * dy
    h2 = r * r - ((fx - c[0]) ** 2 + (fy - c[1]) ** 2)
    if h2 < -1e-9 * max(r * r, 1e-12):
        return []
    h = math.sqrt(max(h2, 0.0) / L2)
    if h * math.sqrt(L2) < 1e-9:
        return [(fx, fy)]                               # tangent
    return [(fx - h * dx, fy - h * dy), (fx + h * dx, fy + h * dy)]


def _hits(g1: tuple, g2: tuple) -> list:
    """Where the CARRIERS (infinite line / whole circle) of two shapes meet."""
    if g1[0] == "seg" and g2[0] == "seg":
        (a, b), (c, d) = g1[1:], g2[1:]
        ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
        den = ux * vy - uy * vx
        if abs(den) < 1e-12 * max(math.hypot(ux, uy) * math.hypot(vx, vy), 1e-12):
            return []
        t = ((c[0] - a[0]) * vy - (c[1] - a[1]) * vx) / den
        return [(a[0] + t * ux, a[1] + t * uy)]
    if g1[0] == "seg":
        return _line_circle(g1[1], g1[2], g2[1], g2[2])
    if g2[0] == "seg":
        return _line_circle(g2[1], g2[2], g1[1], g1[2])
    (c1, r1), (c2, r2) = g1[1:3], g2[1:3]
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    d = math.hypot(dx, dy)
    if d < 1e-12:
        return []
    a = (d * d + r1 * r1 - r2 * r2) / (2.0 * d)
    h2 = r1 * r1 - a * a
    if h2 < -1e-9 * max(r1 * r1, 1e-12):
        return []
    h = math.sqrt(max(h2, 0.0))
    mx, my = c1[0] + a * dx / d, c1[1] + a * dy / d
    if h < 1e-9:
        return [(mx, my)]
    return [(mx - h * dy / d, my + h * dx / d), (mx + h * dy / d, my - h * dx / d)]


def _param(g: tuple, p, clamp: bool = False):
    """Where ``p`` sits along the shape, 0 … 1 — ``None`` when it is off it."""
    if g[0] == "seg":
        a, b = g[1], g[2]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 < 1e-24:
            return None
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
        tol = TOL / math.sqrt(L2)
    else:
        _k, c, r, a0, sweep = g
        ang = (math.atan2(p[1] - c[1], p[0] - c[0]) - a0) % TWO_PI
        tol = TOL / max(r * sweep, 1e-12)
        if sweep < TWO_PI - 1e-12 and ang > sweep and TWO_PI - ang < tol * sweep:
            ang = 0.0                               # a hair before the start
        t = ang / sweep
    if clamp:
        return max(0.0, min(1.0, t))
    return t if -tol <= t <= 1.0 + tol else None


# ---- trim -------------------------------------------------------------------------

def _point_at(doc, sk, p, on_id) -> int:
    """The point at ``p``: one that is already there (so two trimmed curves
    end up SHARING it and the outline closes), else a new one held on the
    curve ``on_id`` that did the cutting."""
    for e in sk["ents"]:
        if e["t"] == "point" and math.hypot(e["x"] - p[0], e["y"] - p[1]) < TOL:
            return e["id"]
    new = model.add_point(doc, sk, p[0], p[1])
    model.add_constraint(doc, sk, "coincident", [new["id"], on_id])
    return new["id"]


def _drop_if_unused(sk, pid) -> None:
    used = {p for e in sk["ents"] for p in model.point_refs(e)}
    e = model.ents(sk).get(pid)
    if e is not None and pid not in used and not e.get("fixed"):
        model.delete_entities(sk, [pid])


def _try(doc, sk, ctype, ids) -> None:
    try:
        model.add_constraint(doc, sk, ctype, ids)
    except SketchError:
        pass


def trim(doc, sk, eid: int, uv) -> None:
    """Cut away the stretch of entity ``eid`` under ``uv``, up to the nearest
    crossings with other entities on either side (CAD_Sketcher's Trim).
    With no crossing at all the entity is deleted."""
    by = model.ents(sk)
    e = by.get(eid)
    if e is None or e["t"] == "point":
        raise SketchError("Click a line, an arc or a circle to trim it.")
    g = _geom(by, e)
    full = e["t"] == "circle"
    t0 = _param(g, uv, clamp=True)
    cuts = []
    for o in sk["ents"]:
        if o is e or o["t"] == "point":
            continue
        go = _geom(by, o)
        for p in _hits(g, go):
            t = _param(g, p)
            if t is None or _param(go, p) is None:
                continue
            if not full and (t < 1e-6 or t > 1.0 - 1e-6):
                continue                        # at an end: nothing beyond it
            if all(abs(t - c[0]) > 1e-9 for c in cuts):
                cuts.append((t % 1.0 if full else t, p, o["id"]))
    cuts.sort()

    if full:
        if len(cuts) < 2:
            raise SketchError("A circle needs two crossings to be trimmed.")
        below = max((c for c in cuts if c[0] < t0), default=cuts[-1])
        above = min((c for c in cuts if c[0] > t0), default=cuts[0])
        if below is above:
            raise SketchError("A circle needs two crossings to be trimmed.")
        start = _point_at(doc, sk, above[1], above[2])
        end = _point_at(doc, sk, below[1], below[2])
        e.pop("r", None)
        e.update(t="arc", p1=start, p2=end)     # what is left, counter-clockwise
        return

    below = max((c for c in cuts if c[0] < t0), default=None)
    above = min((c for c in cuts if c[0] > t0), default=None)
    if below is None and above is None:
        model.delete_entities(sk, [eid])
        return
    first, last = e["p1"], e["p2"]
    both = below is not None and above is not None
    for c in list(sk["cons"]):
        if c["t"] == "distance" and c["e"] == [eid]:
            if both:
                c["e"] = [first, last]          # still the overall length
            else:
                sk["cons"].remove(c)
    if both:
        xa = _point_at(doc, sk, below[1], below[2])
        xb = _point_at(doc, sk, above[1], above[2])
        e["p2"] = xa
        if e["t"] == "line":
            model.add_line(doc, sk, xb, last, con=e.get("con", False))
            _try(doc, sk, "coincident", [xb, eid])      # the two halves stay
            _try(doc, sk, "coincident", [last, eid])    # on one straight line
        else:
            new = model.add_arc(doc, sk, e["c"], xb, last, con=e.get("con", False))
            _try(doc, sk, "equal", [eid, new["id"]])
    elif below is not None:
        e["p2"] = _point_at(doc, sk, below[1], below[2])
        _drop_if_unused(sk, last)
    else:
        e["p1"] = _point_at(doc, sk, above[1], above[2])
        _drop_if_unused(sk, first)


def extend(doc, sk, eid: int, uv) -> None:
    """Lengthen the end of line / arc ``eid`` nearer ``uv`` until it meets
    the next entity in its way (the opposite of :func:`trim`)."""
    by = model.ents(sk)
    e = by.get(eid)
    if e is None or e["t"] not in ("line", "arc"):
        raise SketchError("Click a line or an arc near the end to extend.")
    g = _geom(by, e)
    at_end = _param(g, uv, clamp=True) >= 0.5
    turn = TWO_PI / g[4] if g[0] == "circ" else None      # one lap, in params
    best = None
    for o in sk["ents"]:
        if o is e or o["t"] == "point":
            continue
        go = _geom(by, o)
        for p in _hits(g, go):
            if _param(go, p) is None:
                continue
            if g[0] == "seg":
                a, b = g[1], g[2]
                dx, dy = b[0] - a[0], b[1] - a[1]
                t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
            else:
                t = ((math.atan2(p[1] - g[1][1], p[0] - g[1][0]) - g[3])
                     % TWO_PI) / g[4]
                if not at_end:
                    t -= turn                     # the way back from the start
            reach = t - 1.0 if at_end else -t
            if reach > 1e-6 and (turn is None or reach < turn - 1.0 - 1e-6) \
                    and (best is None or reach < best[0]):
                best = (reach, p, o["id"])
    if best is None:
        raise SketchError("Nothing in the way to extend to.")
    key = "p2" if at_end else "p1"
    old = e[key]
    sk["cons"] = [c for c in sk["cons"]
                  if not (c["t"] == "distance" and c["e"] == [eid])]
    e[key] = _point_at(doc, sk, best[1], best[2])
    _drop_if_unused(sk, old)


# ---- copy, mirror, pattern --------------------------------------------------------

def snapshot(sk: dict, ids) -> dict:
    """The entities ``ids`` with the points they stand on and the
    constraints that live entirely among them — a clipboard that still
    works after the sketch changes, or in another sketch."""
    by = model.ents(sk)
    keep, order = set(), []
    for i in ids:
        e = by.get(i)
        if e is None:
            continue
        for j in model.point_refs(e) + (i,):
            if j not in keep:
                keep.add(j)
                order.append(j)
    ents_ = [dict(by[i]) for i in order]
    pts = [e for e in ents_ if e["t"] == "point"]
    if not pts:
        raise SketchError("Select what to copy first.")
    us, vs = [p["x"] for p in pts], [p["y"] for p in pts]
    return {"ents": ents_,
            "cons": [dict(c, e=list(c["e"])) for c in sk["cons"]
                     if all(i in keep for i in c["e"])],
            "centre": ((min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2)}


def paste(doc, sk, snap: dict, f, flip: bool = False, turned: bool = False,
          link: bool = False, constraints: bool = True) -> dict:
    """Draw a snapshot again with every point sent through ``f(x, y)``.

    ``flip``: ``f`` mirrors (arcs then run the other way round). ``turned``:
    ``f`` is not axis-aligned, so Horizontal / Vertical and horizontal /
    vertical distances no longer apply and are left out. ``link``: the
    snapshot came from THIS sketch — radii and line lengths are tied to the
    originals with Equal instead of being copied as numbers, so one
    dimension drives every copy. Returns ``{old id: new id}``."""
    ids: dict = {}
    for e in snap["ents"]:
        if e["t"] == "point":
            x, y = f(e["x"], e["y"])
            ids[e["id"]] = model.add_point(doc, sk, x, y,
                                           con=e.get("con", False))["id"]
    for e in snap["ents"]:
        con = e.get("con", False)
        if e["t"] == "line":
            new = model.add_line(doc, sk, ids[e["p1"]], ids[e["p2"]], con)
        elif e["t"] == "circle":
            new = model.add_circle(doc, sk, ids[e["c"]], e["r"], con)
        elif e["t"] == "arc":
            a, b = (e["p2"], e["p1"]) if flip else (e["p1"], e["p2"])
            new = model.add_arc(doc, sk, ids[e["c"]], ids[a], ids[b], con)
        else:
            continue
        ids[e["id"]] = new["id"]
    kinds = {e["id"]: e["t"] for e in snap["ents"]}
    sized = set()
    for c in snap["cons"] if constraints else ():
        t = c["t"]
        if turned and (t in ("horizontal", "vertical") or c.get("axis")):
            continue
        own_size = t in ("radius", "diameter") or (
            t == "distance" and len(c["e"]) == 1)
        if link and own_size:
            sized.add(c["e"][0])
            continue
        new = {"id": model.new_id(doc), "t": t, "e": [ids[i] for i in c["e"]]}
        for k in ("v", "axis"):
            if k in c:
                new[k] = c[k]
        sk["cons"].append(new)
    if link:
        for i, t in kinds.items():
            if t == "circle" or i in sized:
                _try(doc, sk, "equal", [i, ids[i]])
    return ids


def mirror(doc, sk, ids, axis) -> dict:
    """A mirrored copy of ``ids``. ``axis`` = ``"x"`` / ``"y"`` (the
    sketch's own axes) or the id of a line of the sketch — then every copied
    point is tied to its original with Symmetric, so the copy follows."""
    by = model.ents(sk)
    if axis in ("x", "y"):
        f = (lambda x, y: (x, -y)) if axis == "x" else (lambda x, y: (-x, y))
        return paste(doc, sk, snapshot(sk, ids), f, flip=True, link=True)
    ln = by.get(axis)
    if ln is None or ln["t"] != "line":
        raise SketchError("Select the mirror line LAST (it must be a line).")
    ids = [i for i in ids if i != axis]
    a, b = by[ln["p1"]], by[ln["p2"]]
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        raise SketchError("The mirror line has no length.")

    def f(x, y):
        t = ((x - a["x"]) * dx + (y - a["y"]) * dy) / L2
        return (2 * (a["x"] + t * dx) - x, 2 * (a["y"] + t * dy) - y)

    snap = snapshot(sk, ids)
    out = paste(doc, sk, snap, f, flip=True, turned=True, constraints=False)
    for e in snap["ents"]:
        if e["t"] == "point" and e["id"] not in (ln["p1"], ln["p2"]):
            _try(doc, sk, "symmetric", [e["id"], out[e["id"]], axis])
        elif e["t"] == "circle":
            _try(doc, sk, "equal", [e["id"], out[e["id"]]])
    return out


def pattern_linear(doc, sk, ids, count: int, du: float, dv: float) -> list:
    """``count`` in all (the original included), each ``(du, dv)`` further."""
    if count < 2:
        raise SketchError("A pattern needs a count of 2 or more.")
    if math.hypot(du, dv) < 1e-9:
        raise SketchError("The pattern spacing is zero.")
    snap = snapshot(sk, ids)
    return [paste(doc, sk, snap, lambda x, y, k=k: (x + du * k, y + dv * k),
                  link=True) for k in range(1, count)]


def pattern_circular(doc, sk, ids, count: int, centre, angle_deg: float = 360.0
                     ) -> list:
    """``count`` in all, spread over ``angle_deg`` about ``centre`` (u, v);
    a full turn does not repeat the original."""
    if count < 2:
        raise SketchError("A pattern needs a count of 2 or more.")
    full = abs(abs(angle_deg) - 360.0) < 1e-9
    step = math.radians(angle_deg) / (count if full else count - 1)
    if abs(step) < 1e-12:
        raise SketchError("The pattern angle is zero.")
    snap = snapshot(sk, ids)
    cx, cy = centre

    def turn(k):
        co, si = math.cos(step * k), math.sin(step * k)
        return lambda x, y: (cx + (x - cx) * co - (y - cy) * si,
                             cy + (x - cx) * si + (y - cy) * co)

    return [paste(doc, sk, snap, turn(k), turned=True, link=True)
            for k in range(1, count)]


def chain_ids(sk: dict, eid: int) -> list:
    """Ids of the connected run of lines and arcs through ``eid``."""
    e = model.ents(sk).get(eid)
    if e is None or e["t"] not in ("line", "arc"):
        return [eid] if e is not None else []
    return [x["id"] for x, _rev in _chain(sk, eid)[0]]


# ---- offset -----------------------------------------------------------------------

def _chain(sk: dict, eid: int) -> tuple[list, bool]:
    """The connected run of lines and arcs through ``eid``:
    ``([(entity, reversed)…], closed)``. It stops where three curves meet."""
    by = model.ents(sk)
    e = by[eid]
    node = convert._nodes(sk)
    at: dict = {}
    for x in sk["ents"]:
        if x["t"] in ("line", "arc"):
            at.setdefault(node[x["p1"]], []).append(x)
            at.setdefault(node[x["p2"]], []).append(x)
    if node[e["p1"]] == node[e["p2"]]:
        return [(e, False)], True

    def grow(start_node, forward: bool):
        out, cur, last = [], start_node, e
        while len(at[cur]) == 2:
            x = next((y for y in at[cur] if y is not last), None)
            if x is None or node[x["p1"]] == node[x["p2"]]:
                break
            if x is e:
                return out, True
            enters_at_p1 = node[x["p1"]] == cur
            out.append((x, (not enters_at_p1) if forward else enters_at_p1))
            cur = node[x["p2"]] if enters_at_p1 else node[x["p1"]]
            last = x
        return out, False

    ahead, closed = grow(node[e["p2"]], True)
    if closed:
        return [(e, False)] + ahead, True
    behind, _c = grow(node[e["p1"]], False)
    return behind[::-1] + [(e, False)] + ahead, False


def _prim(by: dict, e: dict, rev: bool) -> dict:
    """An entity as walked: start, end, and for an arc its turn direction."""
    def pt(i):
        return (by[i]["x"], by[i]["y"])
    a, b = (e["p2"], e["p1"]) if rev else (e["p1"], e["p2"])
    out = {"e": e, "rev": rev, "a": pt(a), "b": pt(b)}
    if e["t"] == "arc":
        c = pt(e["c"])
        out.update(c=c, r=math.hypot(out["a"][0] - c[0], out["a"][1] - c[1]),
                   ccw=not rev)
    return out


def _side(pr: dict, uv) -> float:
    """Signed distance of ``uv`` to the LEFT of a walked primitive."""
    if "c" in pr:
        d = math.hypot(uv[0] - pr["c"][0], uv[1] - pr["c"][1])
        return pr["r"] - d if pr["ccw"] else d - pr["r"]
    (ax, ay), (bx, by_) = pr["a"], pr["b"]
    L = math.hypot(bx - ax, by_ - ay)
    return ((bx - ax) * (uv[1] - ay) - (by_ - ay) * (uv[0] - ax)) / max(L, 1e-12)


def offset_side(sk: dict, eid: int, uv) -> float:
    """The signed offset that would put the copy of ``eid`` through ``uv``."""
    by = model.ents(sk)
    e = by[eid]
    if e["t"] == "circle":
        c = by[e["c"]]
        return math.hypot(uv[0] - c["x"], uv[1] - c["y"]) - e["r"]
    return _side(_prim(by, e, False), uv)


def _shifted(pr: dict, d: float) -> tuple:
    """The carrier of a primitive moved ``d`` to its left, and its ends."""
    if "c" in pr:
        r2 = pr["r"] - d if pr["ccw"] else pr["r"] + d
        if r2 < 1e-9:
            raise SketchError("The offset is larger than an arc's radius.")
        k = r2 / pr["r"]
        c = pr["c"]
        ends = [(c[0] + (p[0] - c[0]) * k, c[1] + (p[1] - c[1]) * k)
                for p in (pr["a"], pr["b"])]
        return ("circ", c, r2, 0.0, TWO_PI), ends
    (ax, ay), (bx, by_) = pr["a"], pr["b"]
    L = math.hypot(bx - ax, by_ - ay)
    if L < 1e-12:
        raise SketchError("A line in that outline has no length.")
    nx, ny = -(by_ - ay) / L * d, (bx - ax) / L * d
    a, b = (ax + nx, ay + ny), (bx + nx, by_ + ny)
    return ("seg", a, b), [a, b]


def offset_plan(sk: dict, eid: int, d: float) -> dict:
    """What an offset of ``d`` metres (positive: to the left of the entity's
    own direction; a circle: outward) of the outline through ``eid`` is —
    without touching the sketch. Raises :class:`SketchError` when it cannot be."""
    by = model.ents(sk)
    e = by.get(eid)
    if e is None or e["t"] == "point":
        raise SketchError("Click a line, an arc or a circle to offset it.")
    if abs(d) < 1e-9:
        raise SketchError("The offset distance is zero.")
    if e["t"] == "circle":
        if e["r"] + d < 1e-9:
            raise SketchError("The offset is larger than the circle's radius.")
        c = by[e["c"]]
        return {"circle": (e["c"], e["r"] + d, (c["x"], c["y"]))}
    chain, closed = _chain(sk, eid)
    prims = [_prim(by, x, rev) for x, rev in chain]
    moved = [_shifted(pr, d) for pr in prims]
    n = len(prims)
    pts = [None] * (n if closed else n + 1)
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        joint = prims[i]["b"]
        target = moved[i][1][1]
        hits = _hits(moved[i][0], moved[j][0])
        if not hits:
            gap = math.hypot(target[0] - moved[j][1][0][0],
                             target[1] - moved[j][1][0][1])
            if gap > 1e-6:
                raise SketchError("The offset outline does not close at a "
                                  "corner — try a smaller distance.")
            hits = [target]
        pts[j] = min(hits, key=lambda p: math.hypot(p[0] - joint[0],
                                                    p[1] - joint[1]))
    if not closed:
        pts[0], pts[n] = moved[0][1][0], moved[-1][1][1]
    for i, pr in enumerate(prims):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            raise SketchError("The offset is too large: a side vanishes.")
        if "c" not in pr:                    # a side must not flip over
            (ax, ay), (bx, by_) = pr["a"], pr["b"]
            if (b[0] - a[0]) * (bx - ax) + (b[1] - a[1]) * (by_ - ay) <= 0:
                raise SketchError("The offset is too large: a side vanishes.")
    return {"prims": prims, "pts": pts, "closed": closed}


def offset_preview(plan: dict, segments: int = 48) -> list:
    """The planned offset as polylines."""
    if "circle" in plan:
        _c_id, r, centre = plan["circle"]
        pts = convert.circle_points(centre, r, segments)
        return [pts + pts[:1]]
    out, pts = [], plan["pts"]
    for i, pr in enumerate(plan["prims"]):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if "c" in pr:
            s, t = (a, b) if pr["ccw"] else (b, a)
            out.append(convert.arc_points(pr["c"], s, t, segments))
        else:
            out.append([a, b])
    return out


def offset_apply(doc, sk, plan: dict) -> list:
    """Draw the planned offset. Lines stay parallel to their source and arcs
    share its centre; returns the new entity ids."""
    if "circle" in plan:
        c_id, r, _centre = plan["circle"]
        return [model.add_circle(doc, sk, c_id, r)["id"]]
    ids = [model.add_point(doc, sk, *p)["id"] for p in plan["pts"]]
    made = []
    for i, pr in enumerate(plan["prims"]):
        a, b = ids[i], ids[(i + 1) % len(ids)]
        src = pr["e"]
        if "c" in pr:
            s, t = (a, b) if pr["ccw"] else (b, a)
            new = model.add_arc(doc, sk, src["c"], s, t, con=src.get("con", False))
        else:
            new = model.add_line(doc, sk, a, b, con=src.get("con", False))
            _try(doc, sk, "parallel", [src["id"], new["id"]])
        made.append(new["id"])
    return made
