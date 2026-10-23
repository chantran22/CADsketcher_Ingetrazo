# SPDX-License-Identifier: GPL-3.0-or-later
"""Solids from sketches: extrude and loft as plain polygons.

Every builder returns ``(faces, soft)``:

- ``faces``: ``[{"loop": [(x, y, z)…], "holes": [[(x, y, z)…]…]}]`` in world
  metres, wound so the normal looks OUT of the solid;
- ``soft``: ``[(p, q)…]`` edges to draw smooth (the seams of a round wall).

The sweep is not here: IngeTrazo already has one (``core.sweep``, the Follow
Me engine) and :mod:`host` simply feeds it the profile and the path.
No Qt, no host — testable on its own.
"""
from __future__ import annotations

import numpy as np

from .model import normal, to_world

PLANAR_TOL = 1e-6


class FeatureError(ValueError):
    """Why a feature could not be built — shown beside it in the panel."""


def _ring(sk: dict, loop: dict, offset: float = 0.0) -> list:
    n = normal(sk)
    return [tuple(c + n[i] * offset for i, c in enumerate(to_world(sk, u, v)))
            for u, v in loop["pts"]]


def flat_faces(sk: dict, regs: list) -> tuple[list, list]:
    """The sketch's regions as flat faces (to push/pull by hand)."""
    faces = [{"loop": _ring(sk, r["outer"]),
              "holes": [_ring(sk, h) for h in r["holes"]]} for r in regs]
    return faces, []


def extrude(sk: dict, regs: list, distance: float,
            symmetric: bool = False) -> tuple[list, list]:
    """Pull the regions ``distance`` metres along the workplane normal
    (negative: the other way; ``symmetric``: half to each side)."""
    if abs(distance) < 1e-9:
        raise FeatureError("The extrusion distance is zero.")
    if not regs:
        raise FeatureError("The sketch has no closed profile.")
    lo, hi = (-abs(distance) / 2, abs(distance) / 2) if symmetric \
        else (min(0.0, distance), max(0.0, distance))
    faces, soft = [], []
    for r in regs:
        loops = [r["outer"]] + r["holes"]
        bot = [_ring(sk, lp, lo) for lp in loops]
        top = [_ring(sk, lp, hi) for lp in loops]
        faces.append({"loop": bot[0][::-1], "holes": [h[::-1] for h in bot[1:]]})
        faces.append({"loop": top[0], "holes": top[1:]})
        for lp, b, t in zip(loops, bot, top):
            m = len(b)
            for i in range(m):
                j = (i + 1) % m
                faces.append({"loop": [b[i], b[j], t[j], t[i]], "holes": []})
                if lp["smooth"][i]:
                    soft.append((b[i], t[i]))
    return faces, soft


# ---- Loft -----------------------------------------------------------------------

def _params(ring: np.ndarray) -> np.ndarray:
    """Normalised arc length of every vertex of a closed ring (0 ≤ t < 1)."""
    seg = np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1)
    total = float(seg.sum())
    if total < 1e-12:
        raise FeatureError("A loft section has no size.")
    return np.concatenate(([0.0], np.cumsum(seg)[:-1])) / total


def _at(ring: np.ndarray, t_own: np.ndarray, t: float) -> np.ndarray:
    """The point of the ring at arc-length parameter ``t``."""
    i = int(np.searchsorted(t_own, t, side="right")) - 1
    t0 = t_own[i]
    t1 = t_own[i + 1] if i + 1 < len(t_own) else 1.0
    k = 0.0 if t1 - t0 < 1e-15 else (t - t0) / (t1 - t0)
    return ring[i] + (ring[(i + 1) % len(ring)] - ring[i]) * k


def _newell(ring: np.ndarray) -> np.ndarray:
    nxt = np.roll(ring, -1, axis=0)
    return np.array([np.sum((ring[:, 1] - nxt[:, 1]) * (ring[:, 2] + nxt[:, 2])),
                     np.sum((ring[:, 2] - nxt[:, 2]) * (ring[:, 0] + nxt[:, 0])),
                     np.sum((ring[:, 0] - nxt[:, 0]) * (ring[:, 1] + nxt[:, 1]))])


def loft(sections: list) -> tuple[list, list]:
    """A skin through the OUTER loop of each section, in order.

    ``sections`` = ``[(sketch, region)…]``, two or more. Each section keeps
    its exact outline: the rings are not resampled, they are given each
    other's vertices (as points on their own edges), so a square stays a
    square where it meets a circle.
    """
    if len(sections) < 2:
        raise FeatureError("A loft needs two or more sketches.")
    rings, hard = [], []
    for sk, reg in sections:
        rings.append(np.array(_ring(sk, reg["outer"]), dtype=float))
        hard.append([not s for s in reg["outer"]["smooth"]])
    cents = [r.mean(axis=0) for r in rings]
    if any(np.linalg.norm(cents[k + 1] - cents[k]) < 1e-9
           for k in range(len(cents) - 1)):
        raise FeatureError("Two loft sections sit on the same spot.")

    # every ring turns the same way about the loft's direction
    axis = cents[-1] - cents[0]
    for k, ring in enumerate(rings):
        if float(_newell(ring) @ axis) < 0:
            rings[k] = ring[::-1].copy()
            hard[k] = hard[k][::-1]
    # every ring starts at the vertex facing the first ring's start
    ref = rings[0][0] - cents[0]
    ref /= max(np.linalg.norm(ref), 1e-12)
    for k in range(1, len(rings)):
        rel = rings[k] - cents[k]
        rel /= np.maximum(np.linalg.norm(rel, axis=1, keepdims=True), 1e-12)
        s = int(np.argmax(rel @ ref))
        rings[k] = np.roll(rings[k], -s, axis=0)
        hard[k] = hard[k][s:] + hard[k][:s]
        ref = rings[k][0] - cents[k]
        ref /= max(np.linalg.norm(ref), 1e-12)

    own = [_params(r) for r in rings]
    merged = sorted({round(float(t), 9) for ts in own for t in ts})
    corner = set()
    for ts, hd in zip(own, hard):
        corner.update(round(float(t), 9) for t, h in zip(ts, hd) if h)
    full = [np.array([_at(r, ts, t) for t in merged]) for r, ts in zip(rings, own)]
    m = len(merged)

    def tup(p):
        return tuple(float(c) for c in p)

    faces = [{"loop": [tup(p) for p in full[0][::-1]], "holes": []},
             {"loop": [tup(p) for p in full[-1]], "holes": []}]
    soft = []
    for k in range(len(full) - 1):
        a, b = full[k], full[k + 1]
        for i in range(m):
            j = (i + 1) % m
            quad = [a[i], a[j], b[j], b[i]]
            nrm = np.cross(quad[1] - quad[0], quad[2] - quad[0])
            size = np.linalg.norm(nrm)
            off = abs(float(nrm @ (quad[3] - quad[0]))) / size if size > 1e-15 \
                else 1.0
            if size > 1e-15 and off < PLANAR_TOL:
                faces.append({"loop": [tup(p) for p in quad], "holes": []})
            else:
                for tri in ((quad[0], quad[1], quad[2]),
                            (quad[0], quad[2], quad[3])):
                    if np.linalg.norm(np.cross(tri[1] - tri[0],
                                               tri[2] - tri[0])) > 1e-15:
                        faces.append({"loop": [tup(p) for p in tri],
                                      "holes": []})
                soft.append((tup(quad[0]), tup(quad[2])))
            if merged[i] not in corner:
                soft.append((tup(a[i]), tup(b[i])))
        if k > 0:                                   # a ring between two spans
            for i in range(m):
                soft.append((tup(a[i]), tup(a[(i + 1) % m])))
    return faces, soft


def revolve(sk: dict, regs: list, axis_uv: tuple, angle_deg: float = 360.0,
            segments: int = 32) -> tuple[list, list]:
    """Turn the regions about an axis lying in the workplane.

    ``axis_uv`` = two points ``((u, v), (u, v))`` on the axis, workplane
    metres; ``angle_deg`` up to 360 (a full turn closes on itself, less
    leaves the two cut faces); ``segments`` per full turn. The profile may
    touch the axis but must stay on one side of it.
    """
    if not regs:
        raise FeatureError("The sketch has no closed profile.")
    (u0, v0), (u1, v1) = axis_uv
    L = float(np.hypot(u1 - u0, v1 - v0))
    if L < 1e-9:
        raise FeatureError("The revolve axis has no length.")
    angle = max(-360.0, min(360.0, float(angle_deg)))
    if abs(angle) < 1e-6:
        raise FeatureError("The revolve angle is zero.")
    full = abs(abs(angle) - 360.0) < 1e-9
    side = [((u - u0) * (v1 - v0) - (v - v0) * (u1 - u0)) / L
            for r in regs for lp in [r["outer"]] + r["holes"]
            for u, v in lp["pts"]]
    if min(side) < -1e-7 and max(side) > 1e-7:
        raise FeatureError("The profile crosses the revolve axis — keep it "
                           "on one side.")
    if max(abs(s) for s in side) < 1e-7:
        raise FeatureError("The profile lies on the revolve axis.")

    origin = np.array(to_world(sk, u0, v0))
    k = np.array(to_world(sk, u1, v1)) - origin
    k /= np.linalg.norm(k)
    steps = max(3, int(np.ceil(max(8, int(segments)) * abs(angle) / 360.0 - 1e-6)))

    def turn(pts: np.ndarray, a: float) -> np.ndarray:
        """Rodrigues: ``pts`` turned ``a`` radians about the axis."""
        p = pts - origin
        return origin + p * np.cos(a) + np.cross(k, p) * np.sin(a) \
            + np.outer(p @ k, k) * (1.0 - np.cos(a))

    def tup(p):
        return tuple(float(c) for c in p)

    def add(faces, loop):
        """A polygon without its doubled corners (points ON the axis do not
        move, so a quad there is really a triangle)."""
        out = []
        for p in loop:
            if not out or np.linalg.norm(p - out[-1]) > 1e-9:
                out.append(p)
        if len(out) > 1 and np.linalg.norm(out[0] - out[-1]) <= 1e-9:
            out.pop()
        if len(out) >= 3:
            faces.append({"loop": [tup(p) for p in out], "holes": []})

    faces, soft = [], []
    stations = steps if full else steps + 1
    for r in regs:
        loops = [r["outer"]] + r["holes"]
        base = [np.array(_ring(sk, lp), dtype=float) for lp in loops]
        rings = [[turn(b, np.radians(angle) * s / steps) for b in base]
                 for s in range(stations)]
        if not full:
            faces.append({"loop": [tup(p) for p in rings[0][0][::-1]],
                          "holes": [[tup(p) for p in h[::-1]]
                                    for h in rings[0][1:]]})
            faces.append({"loop": [tup(p) for p in rings[-1][0]],
                          "holes": [[tup(p) for p in h] for h in rings[-1][1:]]})
        for s in range(steps):
            a_rings, b_rings = rings[s], rings[(s + 1) % stations]
            for lp, a, b in zip(loops, a_rings, b_rings):
                m = len(a)
                for i in range(m):
                    j = (i + 1) % m
                    add(faces, [a[i], a[j], b[j], b[i]])
                    if lp["smooth"][i]:
                        soft.append((tup(a[i]), tup(b[i])))
                    if full or s > 0:           # a meridian inside the turn
                        soft.append((tup(a[i]), tup(a[j])))
    if volume(faces) < 0:
        for f in faces:
            f["loop"].reverse()
            for h in f["holes"]:
                h.reverse()
    return faces, soft


def volume(faces: list) -> float:
    """Signed volume of a closed set of faces (holes ignored) — tests."""
    v = 0.0
    for f in faces:
        p = np.array(f["loop"], dtype=float)
        for i in range(1, len(p) - 1):
            v += float(np.dot(p[0], np.cross(p[i], p[i + 1]))) / 6.0
    return v
