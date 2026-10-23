# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometric constraint solver for one sketch — NumPy only.

CAD_Sketcher hands this job to SolveSpace (``py_slvs``), a compiled module
that IngeTrazo's frozen Python cannot load. The same idea is small enough to
write on ``numpy.linalg``: every constraint is one or two equations ``f = 0``
over the free parameters (point coordinates, circle radii), solved by Newton
steps of MINIMUM NORM (``lstsq``) — of all the ways to satisfy the
constraints, the sketch moves the least, which is what makes an
under-constrained sketch stay put while one dimension changes.

Units: metres in, metres out; angles in degrees in the constraint values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .model import ents, radius

TOL = 1e-7          # a residual below this (m, or rad) is «satisfied»
EPS = 1e-12


@dataclass
class Result:
    ok: bool = True
    dof: int = 0
    failed: list = field(default_factory=list)     # constraint ids
    redundant: bool = False
    residual: float = 0.0
    iterations: int = 0

    @property
    def text(self) -> str:
        if not self.ok:
            return f"Solver failed — {len(self.failed)} constraint(s) in conflict"
        if self.dof == 0:
            return "Fully constrained" + (" (redundant constraints)"
                                          if self.redundant else "")
        return f"Solved — {self.dof} degree(s) of freedom"


def _hyp(ax, ay, bx, by):
    return math.hypot(bx - ax, by - ay)


def _cross(ax, ay, bx, by, px, py):
    """z of (b − a) × (p − a): twice the signed area of a, b, p."""
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


class _System:
    """The sketch as parameters and equations."""

    def __init__(self, sk: dict) -> None:
        self.sk = sk
        self.by = ents(sk)
        self.index: dict = {}          # (entity id, slot) → column
        self.x0: list = []
        self.const: dict = {}
        self.eqs: list = []            # (fn, keys, constraint id | None)
        for e in sk["ents"]:
            if e["t"] == "point":
                for k in ("x", "y"):
                    self._param((e["id"], k), e[k], free=not e.get("fixed"))
            elif e["t"] == "circle":
                self._param((e["id"], "r"), e["r"], free=True)
        for e in sk["ents"]:
            if e["t"] == "arc":         # both ends at the same distance
                self._eq(lambda cx, cy, ax, ay, bx, by:
                         _hyp(cx, cy, ax, ay) - _hyp(cx, cy, bx, by),
                         self._p(e["c"]) + self._p(e["p1"]) + self._p(e["p2"]),
                         None)
        for c in sk["cons"]:
            try:
                self._constraint(c)
            except KeyError:
                continue                # refers to something deleted

    # -- parameters -----------------------------------------------------------
    def _param(self, key, value, free: bool) -> None:
        if free:
            self.index[key] = len(self.x0)
            self.x0.append(float(value))
        else:
            self.const[key] = float(value)

    def _p(self, pid) -> list:
        return [(pid, "x"), (pid, "y")]

    def _line(self, lid) -> list:
        e = self.by[lid]
        return self._p(e["p1"]) + self._p(e["p2"])

    def _val(self, key) -> float:
        i = self.index.get(key)
        return self.x0[i] if i is not None else self.const[key]

    def _vals(self, keys) -> list:
        return [self._val(k) for k in keys]

    def _eq(self, fn, keys, cid) -> None:
        self.eqs.append((fn, keys, cid))

    def _rad(self, eid):
        """``(keys, fn)`` giving the radius of a circle or an arc."""
        e = self.by[eid]
        if e["t"] == "circle":
            return [(eid, "r")], (lambda r: r)
        return self._p(e["c"]) + self._p(e["p1"]), _hyp

    # -- constraints ----------------------------------------------------------
    def _constraint(self, c: dict) -> None:
        t, ids, cid = c["t"], c["e"], c["id"]
        by = self.by
        kinds = [by[i]["t"] for i in ids]
        v = float(c.get("v", 0.0))
        add = lambda fn, keys: self._eq(fn, keys, cid)   # noqa: E731

        if t == "coincident":
            if kinds == ["point", "point"]:
                a, b = ids
                add(lambda p, q: p - q, [(a, "x"), (b, "x")])
                add(lambda p, q: p - q, [(a, "y"), (b, "y")])
            elif kinds[1] == "line":
                add(lambda px, py, ax, ay, bx, by_:
                    _cross(ax, ay, bx, by_, px, py)
                    / max(_hyp(ax, ay, bx, by_), EPS),
                    self._p(ids[0]) + self._line(ids[1]))
            else:
                rk, rf = self._rad(ids[1])
                n = len(rk)
                add(lambda px, py, cx, cy, *r:
                    _hyp(cx, cy, px, py) - rf(*r[:n]),
                    self._p(ids[0]) + self._p(by[ids[1]]["c"]) + rk)

        elif t in ("horizontal", "vertical"):
            k = "y" if t == "horizontal" else "x"
            a, b = (by[ids[0]]["p1"], by[ids[0]]["p2"]) \
                if kinds == ["line"] else ids
            add(lambda p, q: p - q, [(a, k), (b, k)])

        elif t in ("parallel", "perpendicular", "angle"):
            keys = self._line(ids[0]) + self._line(ids[1])

            def parts(a, b, c_, d, e, f, g, h):
                ux, uy, wx, wy = c_ - a, d - b, g - e, h - f
                n = max(math.hypot(ux, uy) * math.hypot(wx, wy), EPS)
                return (ux * wy - uy * wx) / n, (ux * wx + uy * wy) / n
            if t == "parallel":
                add(lambda *q: parts(*q)[0], keys)
            elif t == "perpendicular":
                add(lambda *q: parts(*q)[1], keys)
            else:
                s0, _c0 = parts(*self._vals(keys))
                sign = -1.0 if s0 < 0 else 1.0
                target = math.radians(v)

                def ang(*q):
                    s, co = parts(*q)
                    d = sign * math.atan2(s, co) - target
                    return (d + math.pi) % (2 * math.pi) - math.pi
                add(ang, keys)

        elif t in ("equal", "ratio"):
            k = v if t == "ratio" else 1.0
            if kinds[0] == "line":
                add(lambda a, b, c_, d, e, f, g, h:
                    _hyp(a, b, c_, d) - k * _hyp(e, f, g, h),
                    self._line(ids[0]) + self._line(ids[1]))
            else:
                k1, f1 = self._rad(ids[0])
                k2, f2 = self._rad(ids[1])
                n = len(k1)
                add(lambda *q: f1(*q[:n]) - f2(*q[n:]), k1 + k2)

        elif t == "tangent":
            self._tangent(c)

        elif t == "midpoint":
            p, ln = ids
            a, b = by[ln]["p1"], by[ln]["p2"]
            for k in ("x", "y"):
                add(lambda q, m, n: q - 0.5 * (m + n), [(p, k), (a, k), (b, k)])

        elif t == "symmetric":
            p, q, ln = ids
            keys = self._p(p) + self._p(q) + self._line(ln)
            add(lambda px, py, qx, qy, ax, ay, bx, by_:
                _cross(ax, ay, bx, by_, 0.5 * (px + qx), 0.5 * (py + qy))
                / max(_hyp(ax, ay, bx, by_), EPS), keys)
            add(lambda px, py, qx, qy, ax, ay, bx, by_:
                ((qx - px) * (bx - ax) + (qy - py) * (by_ - ay))
                / max(_hyp(ax, ay, bx, by_), EPS), keys)

        elif t == "distance":
            axis = c.get("axis")
            if kinds == ["point", "line"]:
                keys = self._p(ids[0]) + self._line(ids[1])

                def dist(px, py, ax, ay, bx, by_):
                    return _cross(ax, ay, bx, by_, px, py) \
                        / max(_hyp(ax, ay, bx, by_), EPS)
                sign = -1.0 if dist(*self._vals(keys)) < 0 else 1.0
                add(lambda *q: sign * dist(*q) - v, keys)
            else:
                a, b = (by[ids[0]]["p1"], by[ids[0]]["p2"]) \
                    if kinds == ["line"] else ids
                if axis in ("h", "v"):
                    k = "x" if axis == "h" else "y"
                    keys = [(a, k), (b, k)]
                    p0, q0 = self._vals(keys)
                    sign = -1.0 if q0 - p0 < 0 else 1.0
                    add(lambda p, q: sign * (q - p) - v, keys)
                else:
                    add(lambda ax, ay, bx, by_: _hyp(ax, ay, bx, by_) - v,
                        self._p(a) + self._p(b))

        elif t in ("radius", "diameter"):
            k = 2.0 if t == "diameter" else 1.0
            rk, rf = self._rad(ids[0])
            add(lambda *q: k * rf(*q) - v, rk)

    def _tangent(self, c: dict) -> None:
        ids, cid, by = c["e"], c["id"], self.by
        a, b = by[ids[0]], by[ids[1]]
        if a["t"] == "line":
            ends = {a["p1"], a["p2"]}
            shared = [p for p in (b.get("p1"), b.get("p2")) if p in ends]
            if shared:                    # radius ⟂ line at the shared end
                self._eq(lambda px, py, cx, cy, ax, ay, bx, by_:
                         ((px - cx) * (bx - ax) + (py - cy) * (by_ - ay))
                         / max(_hyp(ax, ay, bx, by_), EPS),
                         self._p(shared[0]) + self._p(b["c"])
                         + self._line(a["id"]), cid)
                return
            rk, rf = self._rad(b["id"])
            keys = self._p(b["c"]) + self._line(a["id"])

            def dist(cx, cy, ax, ay, bx, by_):
                return _cross(ax, ay, bx, by_, cx, cy) \
                    / max(_hyp(ax, ay, bx, by_), EPS)
            sign = -1.0 if dist(*self._vals(keys)) < 0 else 1.0
            self._eq(lambda *q: sign * dist(*q[:6]) - rf(*q[6:]),
                     keys + rk, cid)
            return
        shared = [p for p in (a.get("p1"), a.get("p2"))
                  if p is not None and p in (b.get("p1"), b.get("p2"))]
        if shared:                        # both centres in line with the point
            self._eq(lambda px, py, ax, ay, bx, by_:
                     _cross(px, py, ax, ay, bx, by_)
                     / max(_hyp(px, py, ax, ay) * _hyp(px, py, bx, by_), EPS),
                     self._p(shared[0]) + self._p(a["c"]) + self._p(b["c"]),
                     cid)
            return
        k1, f1 = self._rad(a["id"])
        k2, f2 = self._rad(b["id"])
        n = len(k1)
        ck = self._p(a["c"]) + self._p(b["c"])
        d0 = _hyp(*self._vals(ck))
        r1, r2 = f1(*self._vals(k1)), f2(*self._vals(k2))
        if abs(d0 - (r1 + r2)) <= abs(d0 - abs(r1 - r2)):      # outside
            self._eq(lambda *q: _hyp(*q[:4]) - f1(*q[4:4 + n]) - f2(*q[4 + n:]),
                     ck + k1 + k2, cid)
        else:                                                    # one inside
            s = 1.0 if r1 >= r2 else -1.0
            self._eq(lambda *q: _hyp(*q[:4])
                     - s * (f1(*q[4:4 + n]) - f2(*q[4 + n:])),
                     ck + k1 + k2, cid)

    # -- evaluation -----------------------------------------------------------
    def residuals(self, x: np.ndarray) -> np.ndarray:
        idx, const = self.index, self.const
        out = np.empty(len(self.eqs))
        for i, (fn, keys, _cid) in enumerate(self.eqs):
            out[i] = fn(*[x[idx[k]] if k in idx else const[k] for k in keys])
        return out

    def jacobian(self, x: np.ndarray) -> np.ndarray:
        """Central differences, each equation over ITS few parameters only."""
        idx, const = self.index, self.const
        jac = np.zeros((len(self.eqs), len(x)))
        for i, (fn, keys, _cid) in enumerate(self.eqs):
            vals = [x[idx[k]] if k in idx else const[k] for k in keys]
            for j, k in enumerate(keys):
                col = idx.get(k)
                if col is None:
                    continue
                h = 1e-6 * max(1.0, abs(vals[j]))
                keep = vals[j]
                vals[j] = keep + h
                up = fn(*vals)
                vals[j] = keep - h
                dn = fn(*vals)
                vals[j] = keep
                jac[i, col] += (up - dn) / (2.0 * h)
        return jac


def solve(sk: dict, drag: dict | None = None, max_iter: int = 80) -> Result:
    """Solve ``sk`` in place. ``drag`` = ``{point id: (x, y)}`` or
    ``{circle id: radius}``: where the user is pulling — those parameters
    start there and resist change, the rest of the sketch follows.

    A sketch that cannot be solved is left untouched and the constraints in
    conflict are returned in ``Result.failed``.
    """
    sys_ = _System(sk)
    n = len(sys_.x0)
    if n == 0 or not sys_.eqs:
        return Result(ok=True, dof=n)
    x = np.array(sys_.x0, dtype=float)
    weight = np.ones(n)
    for key, target in (drag or {}).items():
        if isinstance(target, (int, float)):
            cols = [(sys_.index.get((key, "r")), float(target))]
        else:
            cols = [(sys_.index.get((key, "x")), float(target[0])),
                    (sys_.index.get((key, "y")), float(target[1]))]
        for col, value in cols:
            if col is not None:
                x[col] = value
                weight[col] = 1e-3

    r = sys_.residuals(x)
    it = 0
    for it in range(1, max_iter + 1):
        if not np.all(np.isfinite(r)):
            break
        if np.max(np.abs(r)) < 1e-12:
            break
        jac = sys_.jacobian(x)
        step = weight * np.linalg.lstsq(jac * weight, -r, rcond=1e-10)[0]
        norm0, t = float(r @ r), 1.0
        while True:
            x_new = x + t * step
            r_new = sys_.residuals(x_new)
            if np.all(np.isfinite(r_new)) and float(r_new @ r_new) < norm0:
                break
            t *= 0.5
            if t < 1e-6:
                break
        if t < 1e-6:
            break
        x, r = x_new, r_new

    res = Result(iterations=it)
    res.residual = float(np.max(np.abs(r))) if np.all(np.isfinite(r)) else math.inf
    res.ok = res.residual < TOL
    jac = sys_.jacobian(x if res.ok else np.array(sys_.x0))
    sv = np.linalg.svd(jac, compute_uv=False)
    rank = int(np.sum(sv > 1e-7 * max(float(sv[0]) if len(sv) else 1.0, 1.0)))
    res.dof = n - rank
    res.redundant = rank < len(sys_.eqs)
    if not res.ok:
        bad = {cid for (_f, _k, cid), v in zip(sys_.eqs, r)
               if cid is not None and not abs(v) < TOL}
        res.failed = sorted(bad) or sorted(
            {cid for _f, _k, cid in sys_.eqs if cid is not None})
        return res
    for (eid, k), col in sys_.index.items():
        sys_.by[eid][k] = float(x[col])
    return res


def measure(sk: dict, ctype: str, refs: list, axis: str | None = None) -> float:
    """What a dimension over ``refs`` reads right now — the value a new
    dimensional constraint starts with (metres; degrees for an angle)."""
    by = ents(sk)

    def pt(i):
        return by[i]["x"], by[i]["y"]

    def seg(i):
        return pt(by[i]["p1"]) + pt(by[i]["p2"])

    kinds = [by[i]["t"] for i in refs]
    if ctype == "distance":
        if kinds == ["point", "line"]:
            px, py = pt(refs[0])
            ax, ay, bx, by_ = seg(refs[1])
            return abs(_cross(ax, ay, bx, by_, px, py)) \
                / max(_hyp(ax, ay, bx, by_), EPS)
        ax, ay, bx, by_ = seg(refs[0]) if kinds == ["line"] \
            else pt(refs[0]) + pt(refs[1])
        if axis == "h":
            return abs(bx - ax)
        if axis == "v":
            return abs(by_ - ay)
        return _hyp(ax, ay, bx, by_)
    if ctype in ("radius", "diameter"):
        r = radius(by, by[refs[0]])
        return 2.0 * r if ctype == "diameter" else r
    if ctype in ("angle", "ratio"):
        a, b, c, d = seg(refs[0])
        e, f, g, h = seg(refs[1])
        if ctype == "ratio":
            return _hyp(a, b, c, d) / max(_hyp(e, f, g, h), EPS)
        ux, uy, wx, wy = c - a, d - b, g - e, h - f
        return abs(math.degrees(math.atan2(ux * wy - uy * wx,
                                           ux * wx + uy * wy)))
    return 0.0
