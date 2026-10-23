# SPDX-License-Identifier: GPL-3.0-or-later
"""Sketch data model — plain JSON-safe dicts, no Qt and no IngeTrazo imports.

The whole extension state is ONE document value (saved inside the .igz):

    {"version": 1, "next_id": 12, "segments": 32,
     "sketches": [{"id", "name", "visible",
                   "o": [x, y, z], "x": [..], "y": [..],      # workplane, world metres
                   "ents": [entity, ...], "cons": [constraint, ...]}],
     "features": [{"id", "t": "extrude" | "loft" | "sweep" | "face", ...}]}

Entities (2D, in the workplane's u/v coordinates, metres) follow CAD_Sketcher /
SolveSpace: a curve never stores coordinates, it points at its points.

    point   {"id", "t": "point", "x", "y", "fixed", "con"}
    line    {"id", "t": "line", "p1", "p2", "con"}
    circle  {"id", "t": "circle", "c", "r", "con"}
    arc     {"id", "t": "arc", "c", "p1", "p2", "con"}     # counter-clockwise p1 → p2

``con`` = construction: solved, but ignored when the sketch becomes geometry.

Constraints: {"id", "t", "e": [entity ids], "v": value?, "axis": "h" | "v"?}
Lengths in metres, angles in degrees.
"""
from __future__ import annotations

import copy
import math

VERSION = 1
CURVES = ("line", "circle", "arc")

GEOMETRIC = ("coincident", "horizontal", "vertical", "parallel",
             "perpendicular", "equal", "tangent", "midpoint", "symmetric")
DIMENSIONAL = ("distance", "radius", "diameter", "angle", "ratio")

#: Standard workplanes: (x axis, y axis); the normal is x × y.
PLANES = {
    "XY": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "XZ": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "YZ": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
}


class SketchError(ValueError):
    """A user-facing refusal (wrong selection for a constraint, …)."""


# ---- Document -------------------------------------------------------------------

def new_doc() -> dict:
    return {"version": VERSION, "next_id": 1, "segments": 32,
            "sketches": [], "features": []}


def normalize(raw) -> dict:
    """A private, complete copy of whatever the document holds."""
    doc = new_doc()
    if isinstance(raw, dict):
        doc.update(copy.deepcopy(raw))
    doc.setdefault("sketches", [])
    doc.setdefault("features", [])
    for sk in doc["sketches"]:
        sk.setdefault("ents", [])
        sk.setdefault("cons", [])
        sk.setdefault("visible", True)
    return doc


def new_id(doc: dict) -> int:
    i = int(doc.get("next_id", 1))
    doc["next_id"] = i + 1
    return i


def find(items: list, ident) -> dict | None:
    for it in items:
        if it.get("id") == ident:
            return it
    return None


def sketch(doc: dict, sid) -> dict | None:
    return find(doc["sketches"], sid)


# ---- Workplane ------------------------------------------------------------------

def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(sum(c * c for c in a))
    if n < 1e-12:
        raise SketchError("Degenerate workplane axis.")
    return tuple(c / n for c in a)


def normal(sk: dict) -> tuple:
    return _unit(_cross(sk["x"], sk["y"]))


def to_world(sk: dict, u: float, v: float) -> tuple:
    """Workplane (u, v) → world (x, y, z), metres."""
    o, x, y = sk["o"], sk["x"], sk["y"]
    return (o[0] + x[0] * u + y[0] * v, o[1] + x[1] * u + y[1] * v,
            o[2] + x[2] * u + y[2] * v)


def to_local(sk: dict, p) -> tuple:
    """World point → workplane (u, v) (the point is projected on the plane)."""
    o, x, y = sk["o"], sk["x"], sk["y"]
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    return (d[0] * x[0] + d[1] * x[1] + d[2] * x[2],
            d[0] * y[0] + d[1] * y[1] + d[2] * y[2])


def add_sketch(doc: dict, name: str, origin, x, y) -> dict:
    """A new sketch on the workplane (origin, x, y); the y axis is squared to
    x. It starts with one FIXED point at the workplane origin — base the
    drawing on it and the sketch has nowhere to drift."""
    x = _unit(x)
    n = _unit(_cross(x, y))
    y = _cross(n, x)
    sk = {"id": new_id(doc), "name": name, "visible": True,
          "o": [float(c) for c in origin], "x": list(x), "y": list(y),
          "base": {"x": list(x), "y": list(y)}, "rot": [0.0, 0.0, 0.0],
          "ents": [], "cons": []}
    doc["sketches"].append(sk)
    add_point(doc, sk, 0.0, 0.0, fixed=True, con=True)
    return sk


# ---- Placement: origin X, Y, Z and turns about X, Y, Z ----------------------------
#
# A sketch remembers the plane it was made on (``base``) and three angles
# (``rot``, degrees). Its axes are the base axes turned about the WORLD X axis,
# then the world Y axis, then the world Z axis. The numbers typed are the
# numbers stored, so the Placement window always shows what was entered.
#
# For a sketch made on YZ (it faces along X), that reads naturally:
#   about X = spin the drawing in its own plane,
#   about Y = tilt it up / down (pitch),
#   about Z = turn it left / right (heading).

def _turn(v, rx: float, ry: float, rz: float) -> tuple:
    """``v`` turned ``rx``° about world X, then ``ry``° about Y, ``rz``° about Z."""
    x, y, z = v
    a = math.radians(rx)
    y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
    a = math.radians(ry)
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    a = math.radians(rz)
    x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
    return (x, y, z)


def placement(sk: dict) -> tuple:
    """``(origin, angles)`` of a sketch — angles (0, 0, 0) for one made before
    placements existed (its current plane counts as its base)."""
    return list(sk["o"]), list(sk.get("rot") or [0.0, 0.0, 0.0])


def placed_axes(sk: dict, angles) -> tuple:
    """The ``(x, y, normal)`` the sketch would have with ``angles``."""
    base = sk.get("base") or {"x": sk["x"], "y": sk["y"]}
    x = _turn(base["x"], *angles)
    y = _turn(base["y"], *angles)
    return x, y, _unit(_cross(x, y))


def place_sketch(sk: dict, origin, angles) -> None:
    """Move the sketch to ``origin`` (world metres) and turn it to ``angles``
    (degrees about world X, Y, Z from its base plane). Everything drawn moves
    with it — the drawing's own coordinates do not change."""
    if "base" not in sk:
        sk["base"] = {"x": list(sk["x"]), "y": list(sk["y"])}
        sk.setdefault("rot", [0.0, 0.0, 0.0])
    x, y, _n = placed_axes(sk, angles)
    sk["o"] = [float(c) for c in origin]
    sk["x"], sk["y"] = [float(c) for c in _unit(x)], [float(c) for c in _unit(y)]
    sk["rot"] = [float(a) for a in angles]
    if any(abs(a) > 1e-12 for a in angles):
        sk.pop("plane", None)          # no longer a standard view to align to
    elif sk["base"].get("plane"):
        sk["plane"] = sk["base"]["plane"]


def duplicate_sketch(doc: dict, src: dict, name: str, offset: float = 0.0,
                     plane: str | None = None) -> dict:
    """A full copy of ``src`` — every entity, every constraint and dimension,
    flags and references to other sketches — with its own fresh ids.

    It lies on the same workplane moved ``offset`` metres along its normal,
    or, with ``plane`` = "XY" / "XZ" / "YZ", on that standard plane at
    ``offset`` from the origin (the drawing keeps its own u, v coordinates).
    It is placed right after ``src`` in the list."""
    new = copy.deepcopy(src)
    if plane in PLANES:
        x, y = PLANES[plane]
        n = _unit(_cross(x, y))
        new.update(o=[c * offset for c in n], x=list(x), y=list(y), plane=plane,
                   base={"x": list(x), "y": list(y)}, rot=[0.0, 0.0, 0.0])
    else:
        n = normal(src)
        new["o"] = [src["o"][i] + n[i] * offset for i in range(3)]
    new.update(id=new_id(doc), name=name, visible=True)
    ids = {e["id"]: new_id(doc) for e in new["ents"]}
    for e in new["ents"]:
        e["id"] = ids[e["id"]]
        for k in ("p1", "p2", "c"):
            if k in e:
                e[k] = ids[e[k]]
    for c in new["cons"]:
        c["id"] = new_id(doc)
        c["e"] = [ids[i] for i in c["e"]]
    doc["sketches"].insert(doc["sketches"].index(src) + 1, new)
    return new


# ---- Entities -------------------------------------------------------------------

def ents(sk: dict) -> dict:
    return {e["id"]: e for e in sk["ents"]}


def add_point(doc, sk, x: float, y: float, fixed=False, con=False) -> dict:
    e = {"id": new_id(doc), "t": "point", "x": float(x), "y": float(y),
         "fixed": bool(fixed), "con": bool(con)}
    sk["ents"].append(e)
    return e


def add_line(doc, sk, p1: int, p2: int, con=False) -> dict:
    e = {"id": new_id(doc), "t": "line", "p1": p1, "p2": p2, "con": bool(con)}
    sk["ents"].append(e)
    return e


def add_circle(doc, sk, c: int, r: float, con=False) -> dict:
    e = {"id": new_id(doc), "t": "circle", "c": c, "r": float(r),
         "con": bool(con)}
    sk["ents"].append(e)
    return e


def add_arc(doc, sk, c: int, p1: int, p2: int, con=False) -> dict:
    """Counter-clockwise from ``p1`` to ``p2`` around ``c``."""
    e = {"id": new_id(doc), "t": "arc", "c": c, "p1": p1, "p2": p2,
         "con": bool(con)}
    sk["ents"].append(e)
    return e


def point_refs(e: dict) -> tuple:
    """Ids of the points an entity is defined by."""
    t = e["t"]
    if t == "line":
        return (e["p1"], e["p2"])
    if t == "circle":
        return (e["c"],)
    if t == "arc":
        return (e["c"], e["p1"], e["p2"])
    return ()


def radius(sk_ents: dict, e: dict) -> float:
    if e["t"] == "circle":
        return float(e["r"])
    c, p = sk_ents[e["c"]], sk_ents[e["p1"]]
    return math.hypot(p["x"] - c["x"], p["y"] - c["y"])


def delete_entities(sk: dict, ids) -> None:
    """Remove entities with everything that cannot live without them: the
    curves on a deleted point, the constraints on anything deleted, and the
    points a deleted curve leaves unused."""
    by = ents(sk)
    gone = {i for i in ids if i in by}
    orphans = set()
    changed = True
    while changed:
        changed = False
        for e in sk["ents"]:
            if e["id"] not in gone and any(p in gone for p in point_refs(e)):
                gone.add(e["id"])
                changed = True
    for i in gone:
        orphans.update(point_refs(by[i]))
    keep = [e for e in sk["ents"] if e["id"] not in gone]
    used = {p for e in keep for p in point_refs(e)}
    for p in orphans - gone - used:
        if not by[p].get("fixed"):
            gone.add(p)
    sk["ents"] = [e for e in sk["ents"] if e["id"] not in gone]
    sk["cons"] = [c for c in sk["cons"] if not any(i in gone for i in c["e"])]


# ---- Constraints ----------------------------------------------------------------

def _pick(by: dict, ids, *kinds):
    """Order the selected ids as ``kinds`` asks ("point", "line", "curve" =
    circle or arc), or ``None`` when the selection is something else."""
    left = [by[i] for i in ids if i in by]
    if len(left) != len(kinds):
        return None
    out = []
    for k in kinds:
        for e in left:
            if e["t"] == k or (k == "curve" and e["t"] in ("circle", "arc")):
                out.append(e["id"])
                left.remove(e)
                break
        else:
            return None
    return out


#: What each constraint accepts, first match wins.
SIGNATURES = {
    "coincident": (("point", "point"), ("point", "line"), ("point", "curve")),
    "horizontal": (("line",), ("point", "point")),
    "vertical": (("line",), ("point", "point")),
    "parallel": (("line", "line"),),
    "perpendicular": (("line", "line"),),
    "equal": (("line", "line"), ("curve", "curve")),
    "tangent": (("line", "curve"), ("curve", "curve")),
    "midpoint": (("point", "line"),),
    "symmetric": (("point", "point", "line"),),
    "ratio": (("line", "line"),),
    "distance": (("point", "point"), ("point", "line"), ("line",)),
    "radius": (("curve",),),
    "diameter": (("curve",),),
    "angle": (("line", "line"),),
}

_NEEDS = {
    "coincident": "two points, a point and a line, or a point and a circle/arc",
    "horizontal": "a line or two points", "vertical": "a line or two points",
    "parallel": "two lines", "perpendicular": "two lines",
    "equal": "two lines, or two circles/arcs",
    "tangent": "a line and a circle/arc, or two circles/arcs",
    "midpoint": "a point and a line",
    "symmetric": "two points and a line (the mirror axis)",
    "ratio": "two lines",
    "distance": "two points, a point and a line, or a line",
    "radius": "a circle or an arc", "diameter": "a circle or an arc",
    "angle": "two lines",
}


def constraint_refs(sk: dict, ctype: str, ids) -> list:
    """The selection ordered the way ``ctype`` stores it; raises
    :class:`SketchError` (worded for the user) when it does not fit."""
    by = ents(sk)
    for kinds in SIGNATURES.get(ctype, ()):
        refs = _pick(by, list(ids), *kinds)
        if refs is not None:
            return refs
    raise SketchError(f"{ctype.capitalize()}: select {_NEEDS.get(ctype, '?')}.")


def add_constraint(doc, sk, ctype: str, ids, value=None, axis=None) -> dict:
    refs = constraint_refs(sk, ctype, ids)
    for c in sk["cons"]:
        if c["t"] == ctype and sorted(c["e"]) == sorted(refs) \
                and c.get("axis") == axis:
            raise SketchError("That constraint is already there.")
    c = {"id": new_id(doc), "t": ctype, "e": refs}
    if value is not None:
        c["v"] = float(value)
    if axis:
        c["axis"] = axis
    sk["cons"].append(c)
    return c


def bevel(doc, sk, pid: int, size: float, chamfer: bool = False) -> dict:
    """Round (fillet, radius ``size``) or cut (chamfer, ``size`` along each
    side) the corner at point ``pid``, where exactly two lines meet.

    Nothing is lost: the corner point stays, as a construction point held on
    both lines, so dimensions taken to the corner keep meaning what they
    meant; a length dimension on either line is moved to «far end → corner».
    The size itself becomes a constraint (Radius, or two Distances) — edit
    it and the corner follows. Returns the new arc / line.
    """
    by = ents(sk)
    p = by.get(pid)
    lines = [e for e in sk["ents"] if e["t"] == "line"
             and pid in (e["p1"], e["p2"])]
    # Construction lines that also end at the corner (the diagonal of a
    # centre rectangle, an axis…) are not part of the outline: they stay on
    # the corner point, which is kept, and the two REAL lines are rounded.
    if len(lines) != 2:
        lines = [e for e in lines if not e.get("con")]
    curved = [e for e in sk["ents"] if e["t"] in ("arc", "circle")
              and not e.get("con") and pid in point_refs(e)]
    if p is None or p["t"] != "point" or len(lines) != 2 or curved:
        raise SketchError("Click a corner point where exactly two lines meet.")
    if size <= 0:
        raise SketchError("The value must be positive.")
    far, unit, length = [], [], []
    for ln in lines:
        q = by[ln["p2"] if ln["p1"] == pid else ln["p1"]]
        dx, dy = q["x"] - p["x"], q["y"] - p["y"]
        d = math.hypot(dx, dy)
        if d < 1e-9:
            raise SketchError("A line at that corner has no length.")
        far.append(q["id"])
        unit.append((dx / d, dy / d))
        length.append(d)
    cos = max(-1.0, min(1.0, unit[0][0] * unit[1][0] + unit[0][1] * unit[1][1]))
    half = math.acos(cos) / 2.0
    if math.sin(2 * half) < 1e-6:
        raise SketchError("The two lines are in line — there is no corner.")
    setback = size if chamfer else size / math.tan(half)
    if setback >= min(length) - 1e-9:
        raise SketchError("Too large for that corner: it would use up a "
                          "whole side.")
    ends = []
    for ln, q, u in zip(lines, far, unit):
        t = add_point(doc, sk, p["x"] + u[0] * setback, p["y"] + u[1] * setback)
        ln["p1" if ln["p1"] == pid else "p2"] = t["id"]
        ends.append(t)
        for c in sk["cons"]:
            if c["t"] == "distance" and c["e"] == [ln["id"]]:
                c["e"] = [q, pid]
        add_constraint(doc, sk, "coincident", [pid, ln["id"]])
    p["con"] = True
    if chamfer:
        new = add_line(doc, sk, ends[0]["id"], ends[1]["id"])
        for t in ends:
            add_constraint(doc, sk, "distance", [pid, t["id"]], size)
        return new
    bx, by_ = unit[0][0] + unit[1][0], unit[0][1] + unit[1][1]
    bn = math.hypot(bx, by_)
    reach = size / math.sin(half)
    c = add_point(doc, sk, p["x"] + bx / bn * reach, p["y"] + by_ / bn * reach)
    a, b = ends
    if (a["x"] - c["x"]) * (b["y"] - c["y"]) \
            - (a["y"] - c["y"]) * (b["x"] - c["x"]) < 0:
        a, b = b, a                              # arcs run counter-clockwise
    new = add_arc(doc, sk, c["id"], a["id"], b["id"])
    for ln in lines:
        add_constraint(doc, sk, "tangent", [ln["id"], new["id"]])
    add_constraint(doc, sk, "radius", [new["id"]], size)
    return new


# ---- References to other sketches -------------------------------------------------
#
# A reference point is a FIXED construction point of one sketch that follows
# a spot of ANOTHER sketch, projected square onto this sketch's plane:
#     point["ref"] = {"sk": source sketch id, "kind": …, "e": entity id, "q": 0-3}
# It is how two sketches on different levels are lined up: constrain to the
# reference with the ordinary constraints, and it keeps up when the source moves.

REF_NAMES = {"point": "point", "mid": "midpoint", "quad": "quadrant",
             "bbox": "outline centre"}


def _extent(sk: dict):
    """Bounding box (u0, v0, u1, v1) of the non-construction curves."""
    by = ents(sk)
    us, vs = [], []
    for e in sk["ents"]:
        if e.get("con") or e["t"] == "point":
            continue
        if e["t"] == "line":
            pts = [(by[e["p1"]]["x"], by[e["p1"]]["y"]),
                   (by[e["p2"]]["x"], by[e["p2"]]["y"])]
        else:
            c = by[e["c"]]
            r = radius(by, e)
            a0, sweep = 0.0, 2 * math.pi
            if e["t"] == "arc":
                p1, p2 = by[e["p1"]], by[e["p2"]]
                a0 = math.atan2(p1["y"] - c["y"], p1["x"] - c["x"])
                sweep = (math.atan2(p2["y"] - c["y"], p2["x"] - c["x"]) - a0) \
                    % (2 * math.pi) or 2 * math.pi
            pts = [(c["x"] + r * math.cos(a0 + sweep * i / 64),
                    c["y"] + r * math.sin(a0 + sweep * i / 64))
                   for i in range(65)]
        us.extend(p[0] for p in pts)
        vs.extend(p[1] for p in pts)
    return (min(us), min(vs), max(us), max(vs)) if us else None


def ref_source_uv(src: dict, ref: dict):
    """Where a reference points, in the SOURCE sketch's coordinates — or
    ``None`` when what it pointed at is gone."""
    by = ents(src)
    kind = ref.get("kind")
    if kind == "bbox":
        box = _extent(src)
        return None if box is None else ((box[0] + box[2]) / 2,
                                         (box[1] + box[3]) / 2)
    e = by.get(ref.get("e"))
    if e is None:
        return None
    if kind == "point" and e["t"] == "point":
        return (e["x"], e["y"])
    if kind == "mid" and e["t"] == "line":
        a, b = by[e["p1"]], by[e["p2"]]
        return ((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2)
    if kind == "quad" and e["t"] == "circle":
        c = by[e["c"]]
        a = math.pi / 2 * int(ref.get("q", 0))
        return (c["x"] + e["r"] * math.cos(a), c["y"] + e["r"] * math.sin(a))
    return None


def ref_candidates(src: dict) -> list:
    """Every spot of ``src`` another sketch can refer to:
    ``[{"sk", "kind", "e", "q", "uv"}…]`` (``uv`` in ``src``'s coordinates)."""
    out = []

    def add(kind, e=None, q=0):
        ref = {"sk": src["id"], "kind": kind, "e": e, "q": q}
        uv = ref_source_uv(src, ref)
        if uv is not None:
            out.append(dict(ref, uv=uv))

    add("bbox")
    for e in src["ents"]:
        if e["t"] == "point":
            add("point", e["id"])
        elif e["t"] == "line":
            add("mid", e["id"])
        elif e["t"] == "circle":
            for q in range(4):
                add("quad", e["id"], q)
    return out


def ref_uv(doc: dict, dst: dict, ref: dict):
    """A reference, projected onto the plane of the sketch that holds it."""
    src = sketch(doc, ref.get("sk"))
    if src is None or src is dst:
        return None
    uv = ref_source_uv(src, ref)
    return None if uv is None else to_local(dst, to_world(src, *uv))


def add_ref_point(doc, sk, ref: dict) -> dict:
    """The reference point for ``ref`` in ``sk`` (the one already there, if
    this spot is referenced already)."""
    ref = {k: ref.get(k) for k in ("sk", "kind", "e", "q")}
    if ref["sk"] == sk["id"]:
        raise SketchError("Reference a point of ANOTHER sketch.")
    uv = ref_uv(doc, sk, ref)
    if uv is None:
        raise SketchError("That reference has nothing to point at.")
    for e in sk["ents"]:
        if e["t"] == "point" and e.get("ref") == ref:
            return e
    p = add_point(doc, sk, uv[0], uv[1], fixed=True, con=True)
    p["ref"] = ref
    return p


def update_refs(doc: dict) -> list:
    """Move every reference point to where its source is now. Returns the
    ids of the sketches that changed (they need solving again)."""
    changed = []
    for sk in doc["sketches"]:
        moved = False
        for e in sk["ents"]:
            ref = e.get("ref") if e["t"] == "point" else None
            if not ref:
                continue
            uv = ref_uv(doc, sk, ref)
            if uv is None:
                continue                    # source gone: stays where it was
            e["fixed"] = True
            if abs(e["x"] - uv[0]) > 1e-12 or abs(e["y"] - uv[1]) > 1e-12:
                e["x"], e["y"] = float(uv[0]), float(uv[1])
                moved = True
        if moved:
            changed.append(sk["id"])
    return changed


def describe(sk: dict, c: dict, fmt_len=lambda v: f"{v:.4g}") -> str:
    """One line for the constraints list."""
    t = c["t"]
    name = {"distance": {"h": "Horizontal distance", "v": "Vertical distance"}
            .get(c.get("axis"), "Distance")}.get(t, t.capitalize())
    if "v" in c:
        if t == "angle":
            return f"{name} {c['v']:.4g}°"
        if t == "ratio":
            return f"{name} {c['v']:.4g}"
        return f"{name} {fmt_len(c['v'])}"
    return name
