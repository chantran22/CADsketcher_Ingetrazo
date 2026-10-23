# SPDX-License-Identifier: GPL-3.0-or-later
"""The viewport side: one IngeTrazo tool that draws, selects and moves sketch
entities, and the overlay that paints the sketches over the 3D view.

Interaction is IngeTrazo's click–move–click (not Blender's press-drag):

    Select     click an entity or a dimension to (de)select it; empty = none
    Move       click a point / line / circle, move, click to drop
    Point      click
    Line       click, click, … (a chain); Esc or a click on an existing point ends
    Rectangle  two corners        Circle  centre, then radius
    Arc        centre, start, end (counter-clockwise)
    Fillet / Chamfer   click a corner of two lines, give the radius / distance

While drawing a line or a circle, type a length + Enter: the segment (or the
radius) takes it AND keeps it, as a dimension.
"""
from __future__ import annotations

import copy
import logging
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPen, QPolygonF, QVector3D

from . import convert, edit, host, model, solver
from .controller import fmt_len

try:
    from tools.base import Tool
except ImportError:                      # outside IngeTrazo (tests of the maths)
    class Tool:                          # type: ignore[no-redef]
        pass

log = logging.getLogger("ingetrazo.plugins.cad_sketcher")

POINT_PX = 10.0
CURVE_PX = 7.0
AXIS_SNAP_DEG = 3.0

C_LINE = QColor(20, 70, 160)
C_CONSTR = QColor(110, 120, 140)
C_IDLE = QColor(90, 110, 150, 150)
C_SEL = QColor(255, 122, 0)
C_HOVER = QColor(0, 160, 230)
C_FIXED = QColor(120, 60, 160)
C_DIM = QColor(0, 110, 60)
C_FAIL = QColor(210, 30, 30)
C_PREVIEW = QColor(255, 122, 0, 220)
C_REF = QColor(0, 140, 140)

GLYPH = {"coincident": "●", "horizontal": "H", "vertical": "V",
         "parallel": "∥", "perpendicular": "⊥", "equal": "=",
         "tangent": "T", "midpoint": "M", "symmetric": "⇔"}

KEY_MODE = {Qt.Key_S: "select", Qt.Key_M: "move", Qt.Key_P: "point",
            Qt.Key_L: "line", Qt.Key_R: "rect", Qt.Key_C: "circle",
            Qt.Key_A: "arc", Qt.Key_F: "fillet", Qt.Key_K: "chamfer",
            Qt.Key_T: "trim", Qt.Key_O: "offset", Qt.Key_E: "ref",
            Qt.Key_X: "extend", Qt.Key_B: "crect", Qt.Key_U: "slot",
            Qt.Key_H: "semi"}
KEY_GEOMETRIC = {Qt.Key_C: "coincident", Qt.Key_V: "vertical",
                 Qt.Key_H: "horizontal", Qt.Key_E: "equal",
                 Qt.Key_P: "parallel", Qt.Key_N: "perpendicular",
                 Qt.Key_T: "tangent", Qt.Key_M: "midpoint",
                 Qt.Key_S: "symmetric", Qt.Key_R: "ratio"}
KEY_DIMENSION = {Qt.Key_D: ("distance", None), Qt.Key_H: ("distance", "h"),
                 Qt.Key_V: ("distance", "v"), Qt.Key_A: ("angle", None),
                 Qt.Key_O: ("diameter", None), Qt.Key_R: ("radius", None)}

HINTS = {
    "select": "Select — click entities or dimensions; then pick a constraint",
    "move": "Move — click a point, a line or a circle, move, click to drop",
    "point": "Point — click to place",
    "line": "Line — click the points; type a length + Enter; Esc ends the chain",
    "rect": "Rectangle — click two opposite corners",
    "circle": "Circle — click the centre, then the radius (or type it + Enter)",
    "arc": "Arc — click the centre, the start, then the end (counter-clockwise)",
    "fillet": "Fillet — click a corner where two lines meet, then give the radius",
    "chamfer": "Chamfer — click a corner where two lines meet, then give the "
               "distance",
    "trim": "Trim — click the stretch to cut away, between its crossings",
    "offset": "Offset — click an outline, then click to its side (or type the "
              "distance + Enter)",
    "ref": "Reference — click a marked spot of ANOTHER sketch (point, midpoint, "
           "quadrant, outline centre) to bring it into this one",
    "extend": "Extend — click a line or an arc near the end to lengthen, up to "
              "what is in its way",
    "crect": "Centre rectangle — click the centre, then a corner",
    "slot": "Slot — click the centres of its two round ends, then its "
            "half-width (or type it + Enter)",
    "semi": "Semicircle — click the two ends of its diameter (it bulges to the "
            "right of that direction)",
    "paste": "Paste — click where the copy goes (Esc to give up)",
}


# ---- projection helpers ---------------------------------------------------------

def to_pixels(viewport, pts3) -> tuple:
    """World points ``(N, 3)`` → ``(xy (N, 2), visible (N,))``."""
    pts3 = np.asarray(pts3, dtype=float).reshape(-1, 3)
    if len(pts3) == 0:
        return np.zeros((0, 2)), np.zeros(0, dtype=bool)
    px, py, front = viewport.world_to_pixels(pts3)
    return np.column_stack([px, py]), np.asarray(front, dtype=bool)


def world_pts(sk: dict, uv) -> np.ndarray:
    uv = np.asarray(uv, dtype=float).reshape(-1, 2)
    return (np.asarray(sk["o"], dtype=float)
            + np.outer(uv[:, 0], sk["x"]) + np.outer(uv[:, 1], sk["y"]))


def cursor_uv(viewport, sk: dict, x: float, y: float):
    """Where the camera ray through pixel (x, y) meets the workplane, in
    workplane coordinates — or ``None`` when the view grazes the plane."""
    origin, direction = viewport._pixel_to_ray(x, y)
    o = np.array([origin.x(), origin.y(), origin.z()])
    d = np.array([direction.x(), direction.y(), direction.z()])
    n = np.array(model.normal(sk))
    den = float(d @ n)
    if abs(den) < 1e-6:
        return None
    t = float((np.asarray(sk["o"]) - o) @ n) / den
    return model.to_local(sk, o + d * t)


def curve_uv(sk: dict, by: dict, e: dict, segments: int = 48) -> list:
    """An entity as a polyline in workplane coordinates."""
    def pt(i):
        return (by[i]["x"], by[i]["y"])
    if e["t"] == "line":
        return [pt(e["p1"]), pt(e["p2"])]
    if e["t"] == "circle":
        pts = convert.circle_points(pt(e["c"]), e["r"], segments)
        return pts + pts[:1]
    return convert.arc_points(pt(e["c"]), pt(e["p1"]), pt(e["p2"]), segments)


def _seg_dist(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p[0] - ax) * dx
                                                  + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


# ---- the tool -------------------------------------------------------------------

class SketchTool(Tool):
    name = "CAD Sketcher"
    description = "Draw and constrain the active sketch."
    uses_snap = False
    vcb_label = "Length"

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        ctrl.tool = self
        self.viewport = None
        self.picks: list = []        # resolved clicks of the shape in progress
        self.cursor = None           # current snap: {"uv", "pid", "curve", "hv"}
        self.grab = None             # Move: {"kind", "id", "base", "from"}
        self.labels: dict = {}       # constraint id → QRectF (set by the overlay)
        self.ref_hit = None          # Reference: the candidate under the cursor

    # -- host hooks -----------------------------------------------------------
    def on_activate(self, viewport) -> None:
        self.viewport = viewport
        self.ctrl.say(HINTS.get(self.ctrl.mode, ""), 8000)

    def on_deactivate(self, viewport) -> None:
        self.reset()

    def reset(self) -> None:
        """Drop whatever was half done (and put a moved entity back)."""
        sk = self.ctrl.sketch()
        if sk is not None:
            if self.grab is not None:
                sk["ents"] = self.grab["base"]
            # points placed for a shape that was never finished
            used = {p for e in sk["ents"] for p in model.point_refs(e)}
            model.delete_entities(sk, [p["pid"] for p in self.picks
                                       if p.get("new") and p["pid"] not in used])
        self.picks, self.cursor, self.grab = [], None, None
        self.ref_hit = None

    def _take(self, sk: dict, s: dict) -> dict:
        """A click kept as a pick, its point made now (``new``: by us)."""
        return dict(s, pid=self._point(sk, s), new=s["pid"] is None)

    def on_cancel(self, viewport) -> None:
        busy = bool(self.picks or self.grab)
        self.reset()
        if not busy:
            if self.ctrl.sel or self.ctrl.sel_cons:
                self.ctrl.clear_selection()
            elif self.ctrl.mode != "select":
                self.ctrl.set_mode("select")
        self.ctrl.refresh()

    def status_clause(self) -> str:
        return HINTS.get(self.ctrl.mode, "")

    def drag_plane(self, viewport):
        sk = self.ctrl.sketch()
        if sk is None:
            return None
        return QVector3D(*sk["o"]), QVector3D(*model.normal(sk))

    # -- picking and snapping -------------------------------------------------
    def pick(self, sk: dict, x: float, y: float, labels: bool = True):
        """What is under the pixel: ``("ent", id)``, ``("con", id)`` or None.
        Points win over curves, curves over dimension labels."""
        vp = self.viewport
        by = model.ents(sk)
        pts = [e for e in sk["ents"] if e["t"] == "point"]
        if pts:
            px, ok = to_pixels(vp, world_pts(sk, [(p["x"], p["y"]) for p in pts]))
            d = np.hypot(px[:, 0] - x, px[:, 1] - y)
            d[~ok] = np.inf
            i = int(np.argmin(d))
            if d[i] <= POINT_PX:
                return ("ent", pts[i]["id"])
        best = None
        for e in sk["ents"]:
            if e["t"] == "point":
                continue
            px, ok = to_pixels(vp, world_pts(sk, curve_uv(sk, by, e)))
            if not ok.all():
                continue
            d = min(_seg_dist((x, y), px[i], px[i + 1])
                    for i in range(len(px) - 1))
            if d <= CURVE_PX and (best is None or d < best[0]):
                best = (d, e["id"])
        if best:
            return ("ent", best[1])
        if labels:
            for cid, rect in self.labels.items():
                if rect.contains(QPointF(x, y)):
                    return ("con", cid)
        return None

    def snap(self, sk: dict, x: float, y: float, anchor=None, skip=()) -> dict | None:
        """The point the cursor means: an existing point, a spot on a curve,
        or free in the plane — pulled level/plumb with ``anchor`` when close."""
        uv = cursor_uv(self.viewport, sk, x, y)
        if uv is None:
            return None
        by = model.ents(sk)
        out = {"uv": uv, "pid": None, "curve": None, "hv": None}
        hit = self.pick(sk, x, y, labels=False)
        if hit and hit[1] not in skip:
            e = by[hit[1]]
            if e["t"] == "point":
                out.update(uv=(e["x"], e["y"]), pid=e["id"])
                return out
            if e["t"] == "line":
                a, b = by[e["p1"]], by[e["p2"]]
                dx, dy = b["x"] - a["x"], b["y"] - a["y"]
                L2 = dx * dx + dy * dy
                if L2 > 1e-18:
                    t = ((uv[0] - a["x"]) * dx + (uv[1] - a["y"]) * dy) / L2
                    out.update(uv=(a["x"] + t * dx, a["y"] + t * dy),
                               curve=e["id"])
                    return out
            else:
                c = by[e["c"]]
                r = model.radius(by, e)
                d = math.hypot(uv[0] - c["x"], uv[1] - c["y"])
                if d > 1e-12:
                    out.update(uv=(c["x"] + (uv[0] - c["x"]) * r / d,
                                   c["y"] + (uv[1] - c["y"]) * r / d),
                               curve=e["id"])
                    return out
        grid = self.ctrl.grid
        if grid["snap"] and grid["step"] > 0:
            st = grid["step"]
            uv = (round(uv[0] / st) * st, round(uv[1] / st) * st)
            out.update(uv=uv, grid=True)
        if anchor is not None and self.ctrl.auto:
            du, dv = uv[0] - anchor[0], uv[1] - anchor[1]
            if math.hypot(du, dv) > 1e-9:
                ang = math.degrees(math.atan2(abs(dv), abs(du)))
                if ang < AXIS_SNAP_DEG:
                    out.update(uv=(uv[0], anchor[1]), hv="h")
                elif ang > 90.0 - AXIS_SNAP_DEG:
                    out.update(uv=(anchor[0], uv[1]), hv="v")
        return out

    def _point(self, sk: dict, s: dict) -> int:
        """The point id for a snap, creating the point when it is new (and
        keeping it ON the curve it was dropped on)."""
        if s["pid"] is not None:
            return s["pid"]
        doc = self.ctrl.doc
        p = model.add_point(doc, sk, *s["uv"])
        if s["curve"] is not None and self.ctrl.auto:
            model.add_constraint(doc, sk, "coincident", [p["id"], s["curve"]])
        return p["id"]

    def _anchor(self, sk: dict):
        if self.ctrl.mode in ("line", "rect", "slot", "semi") and self.picks:
            return self.picks[-1]["uv"]
        return None

    # -- references to other sketches --------------------------------------------
    def ref_spots(self) -> list:
        """``[(candidate, px, py)…]``: what the other sketches offer, where
        it shows on screen."""
        out = []
        for src in self.ctrl.doc["sketches"]:
            if src["id"] == self.ctrl.active:
                continue
            cands = model.ref_candidates(src)
            if not cands:
                continue
            px, ok = to_pixels(self.viewport,
                               world_pts(src, [c["uv"] for c in cands]))
            out.extend((c, float(p[0]), float(p[1]))
                       for c, p, front in zip(cands, px, ok) if front)
        return out

    def ref_pick(self, x: float, y: float):
        best = None
        for cand, px, py in self.ref_spots():
            d = math.hypot(px - x, py - y)
            # a real point wins over the outline centre sitting on it
            d += 0.5 if cand["kind"] == "bbox" else 0.0
            if d <= POINT_PX + 2 and (best is None or d < best[0]):
                best = (d, cand)
        return best[1] if best else None

    # -- events ---------------------------------------------------------------
    def on_hover(self, ctx) -> None:
        self.viewport = ctx.viewport
        sk = self.ctrl.sketch()
        if sk is None:
            return
        x, y = ctx.screen.x(), ctx.screen.y()
        mode = self.ctrl.mode
        if self.grab is not None:
            self._drag(sk, x, y)
        elif mode in ("select", "move", "trim", "extend") \
                or (mode == "offset" and not self.picks):
            hit = self.pick(sk, x, y, labels=mode == "select")
            if hit != self.ctrl.hover:
                self.ctrl.hover = hit
        elif mode == "ref":
            self.ref_hit = self.ref_pick(x, y)
        elif mode == "offset":
            uv = cursor_uv(ctx.viewport, sk, x, y)
            self.cursor = None if uv is None else \
                {"uv": uv, "pid": None, "curve": None, "hv": None}
        else:
            self.cursor = self.snap(sk, x, y, self._anchor(sk))
        ctx.viewport.update()

    def on_click(self, ctx) -> None:
        self.viewport = ctx.viewport
        sk = self.ctrl.sketch()
        if sk is None:
            return
        x, y = ctx.screen.x(), ctx.screen.y()
        mode = self.ctrl.mode
        if mode == "select":
            hit = self.pick(sk, x, y)
            if hit is None:
                self.ctrl.clear_selection()
            else:
                self.ctrl.toggle(*hit)
            return
        if mode == "move":
            self._move_click(sk, x, y)
            return
        if mode in ("trim", "offset", "extend"):
            self._edit_click(sk, x, y, mode)
            return
        if mode == "ref":
            self.ref_hit = self.ref_pick(x, y)
            if self.ref_hit is None:
                self.ctrl.say("Click one of the marked spots of another sketch.")
            else:
                self.ctrl.add_reference(self.ref_hit)
            return
        s = self.snap(sk, x, y, self._anchor(sk))
        if s is None:
            self.ctrl.say("The view is edge-on to the sketch plane — orbit "
                          "or use «Align view».")
            return
        self.cursor = s
        getattr(self, f"_click_{mode}")(sk, s)

    def on_double_click(self, ctx) -> None:
        sk = self.ctrl.sketch()
        if sk is not None and self.ctrl.mode == "select":
            hit = self.pick(sk, ctx.screen.x(), ctx.screen.y())
            if hit and hit[0] == "con":
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.ctrl.edit_value(hit[1]))
                return
            if hit and model.ents(sk)[hit[1]]["t"] in ("line", "arc"):
                self.ctrl.select_chain(hit[1])      # the whole outline
                return
        self.on_click(ctx)

    def _key_action(self, viewport, key, modifiers):
        """What a key does while a sketch is being edited — a callable, or
        ``None`` when the key is not ours (typing a number, IngeTrazo's own)."""
        ctrl = self.ctrl
        if ctrl.sketch() is None or getattr(viewport, "_value_buffer", ""):
            return None
        mods = modifiers & (Qt.ShiftModifier | Qt.AltModifier | Qt.ControlModifier)
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if ctrl.sel or ctrl.sel_cons:
                return lambda: (self.reset(), ctrl.delete_selected())
            return None
        if mods == Qt.NoModifier and key in KEY_MODE:
            return lambda: ctrl.set_mode(KEY_MODE[key])
        if mods == Qt.NoModifier and key == Qt.Key_G:
            return ctrl.toggle_grid_snap
        if mods == Qt.ShiftModifier and key in KEY_GEOMETRIC:
            return lambda: ctrl.constrain(KEY_GEOMETRIC[key])
        if mods == Qt.AltModifier and key in KEY_DIMENSION:
            ctype, axis = KEY_DIMENSION[key]
            from PySide6.QtCore import QTimer
            return lambda: QTimer.singleShot(
                0, lambda: ctrl.constrain(ctype, axis))
        if mods == Qt.ControlModifier:
            return {Qt.Key_C: ctrl.copy, Qt.Key_A: ctrl.select_all,
                    Qt.Key_V: lambda: ctrl.set_mode("paste")
                    if ctrl.clip else ctrl.say("Nothing copied yet."),
                    }.get(key)
        return None

    def claims_key(self, key, modifiers) -> bool:
        """IngeTrazo asks before its own shortcuts fire (L = its Line tool,
        Ctrl+C = its Copy…): while a sketch is edited these keys are ours."""
        return self._key_action(self.viewport, key, modifiers) is not None

    def on_key(self, viewport, key, modifiers) -> bool:
        action = self._key_action(viewport, key, modifiers)
        if action is None:
            return False
        action()
        return True

    def on_value(self, viewport, value) -> bool:
        """A typed length: the segment being drawn, or the circle's radius."""
        sk = self.ctrl.sketch()
        if sk is None or not isinstance(value, (int, float)) or not self.picks:
            return False
        value = abs(float(value))
        mode = self.ctrl.mode
        if value >= 1e-9 and mode == "offset":
            eid = self.picks[0]["eid"]
            side = edit.offset_side(sk, eid, self.cursor["uv"]) \
                if self.cursor else 1.0
            self.picks, self.cursor = [], None
            self.ctrl.offset(eid, math.copysign(value, side))
            return True
        if value >= 1e-9 and mode == "slot" and len(self.picks) == 2:
            self._slot(sk, value, dimension=True)
            return True
        if value < 1e-9 or mode not in ("line", "circle"):
            return False
        a = self.picks[-1]["uv"]
        cur = (self.cursor or {}).get("uv") or (a[0] + 1.0, a[1])
        du, dv = cur[0] - a[0], cur[1] - a[1]
        d = math.hypot(du, dv)
        if d < 1e-12:
            du, dv, d = 1.0, 0.0, 1.0
        if mode == "circle":
            self._click_circle(sk, {"uv": (a[0] + value, a[1]), "pid": None,
                                    "curve": None, "hv": None}, dimension=value)
            return True
        hv = (self.cursor or {}).get("hv")
        self._click_line(sk, {"uv": (a[0] + du / d * value, a[1] + dv / d * value),
                              "pid": None, "curve": None, "hv": hv},
                         dimension=value)
        return True

    # -- drawing --------------------------------------------------------------
    def _click_point(self, sk, s) -> None:
        if s["pid"] is None:
            self._point(sk, s)
            self.ctrl.commit()

    def _click_line(self, sk, s, dimension: float | None = None) -> None:
        if not self.picks:
            self.picks = [self._take(sk, s)]
            self.ctrl.refresh()
            return
        doc = self.ctrl.doc
        a = self.picks[-1]
        if s["pid"] == a["pid"] or math.hypot(s["uv"][0] - a["uv"][0],
                                              s["uv"][1] - a["uv"][1]) < 1e-9:
            return
        closes = s["pid"] is not None
        b = self._point(sk, s)
        ln = model.add_line(doc, sk, a["pid"], b)
        if s.get("hv") and self.ctrl.auto:
            model.add_constraint(doc, sk, "horizontal" if s["hv"] == "h"
                                 else "vertical", [ln["id"]])
        if dimension:
            model.add_constraint(doc, sk, "distance", [ln["id"]], dimension)
        self.picks = [] if closes else [dict(s, pid=b)]
        self.ctrl.commit()
        if not closes:                      # the solver may have moved the end
            e = model.ents(sk)[b]
            self.picks[0]["uv"] = (e["x"], e["y"])

    def _click_rect(self, sk, s) -> None:
        if not self.picks:
            self.picks = [s]
            self.ctrl.refresh()
            return
        a = self.picks[0]
        (u0, v0), (u1, v1) = a["uv"], s["uv"]
        if abs(u1 - u0) < 1e-9 or abs(v1 - v0) < 1e-9:
            return
        doc = self.ctrl.doc
        ids = [self._point(sk, a),
               model.add_point(doc, sk, u1, v0)["id"],
               self._point(sk, s),
               model.add_point(doc, sk, u0, v1)["id"]]
        for i, ctype in enumerate(("horizontal", "vertical",
                                   "horizontal", "vertical")):
            ln = model.add_line(doc, sk, ids[i], ids[(i + 1) % 4])
            model.add_constraint(doc, sk, ctype, [ln["id"]])
        self.picks = []
        self.ctrl.commit()

    def _click_circle(self, sk, s, dimension: float | None = None) -> None:
        if not self.picks:
            self.picks = [self._take(sk, s)]
            self.ctrl.refresh()
            return
        c = self.picks[0]
        r = math.hypot(s["uv"][0] - c["uv"][0], s["uv"][1] - c["uv"][1])
        if r < 1e-9:
            return
        doc = self.ctrl.doc
        circ = model.add_circle(doc, sk, c["pid"], r)
        if dimension:
            model.add_constraint(doc, sk, "radius", [circ["id"]], dimension)
        elif s["pid"] is not None and self.ctrl.auto:
            model.add_constraint(doc, sk, "coincident", [s["pid"], circ["id"]])
        self.picks = []
        self.ctrl.commit()

    def _click_arc(self, sk, s) -> None:
        if len(self.picks) < 2:
            if self.picks and math.hypot(
                    s["uv"][0] - self.picks[0]["uv"][0],
                    s["uv"][1] - self.picks[0]["uv"][1]) < 1e-9:
                return
            self.picks.append(self._take(sk, s))
            self.ctrl.refresh()
            return
        c, a = self.picks
        r = math.hypot(a["uv"][0] - c["uv"][0], a["uv"][1] - c["uv"][1])
        d = math.hypot(s["uv"][0] - c["uv"][0], s["uv"][1] - c["uv"][1])
        if d < 1e-9 or s["pid"] in (c["pid"], a["pid"]):
            return
        if s["pid"] is None:               # the end lands on the radius
            s = dict(s, curve=None,
                     uv=(c["uv"][0] + (s["uv"][0] - c["uv"][0]) * r / d,
                         c["uv"][1] + (s["uv"][1] - c["uv"][1]) * r / d))
        b = self._point(sk, s)
        model.add_arc(self.ctrl.doc, sk, c["pid"], a["pid"], b)
        self.picks = []
        self.ctrl.commit()

    def _click_crect(self, sk, s) -> None:
        """Rectangle from its centre: the centre stays the middle (it is
        held on the midpoint of a construction diagonal)."""
        if not self.picks:
            self.picks = [self._take(sk, s)]
            self.ctrl.refresh()
            return
        c = self.picks[0]
        du, dv = abs(s["uv"][0] - c["uv"][0]), abs(s["uv"][1] - c["uv"][1])
        if du < 1e-9 or dv < 1e-9:
            return
        doc = self.ctrl.doc
        (u, v) = c["uv"]
        ids = [model.add_point(doc, sk, u + a * du, v + b * dv)["id"]
               for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for i, ctype in enumerate(("horizontal", "vertical",
                                   "horizontal", "vertical")):
            ln = model.add_line(doc, sk, ids[i], ids[(i + 1) % 4])
            model.add_constraint(doc, sk, ctype, [ln["id"]])
        diag = model.add_line(doc, sk, ids[0], ids[2], con=True)
        model.add_constraint(doc, sk, "midpoint", [c["pid"], diag["id"]])
        self.picks = []
        self.ctrl.commit()

    def _click_semi(self, sk, s) -> None:
        """Half circle on a diameter: click its two ends. It bulges to the
        right of the first → second direction (click the other way round
        for the other side). A construction chord keeps the centre midway."""
        if not self.picks:
            self.picks = [self._take(sk, s)]
            self.ctrl.refresh()
            return
        a = self.picks[0]
        if s["pid"] == a["pid"] or math.dist(s["uv"], a["uv"]) < 1e-9:
            return
        doc = self.ctrl.doc
        b = self._point(sk, s)
        c = model.add_point(doc, sk, (a["uv"][0] + s["uv"][0]) / 2,
                            (a["uv"][1] + s["uv"][1]) / 2)
        model.add_arc(doc, sk, c["id"], a["pid"], b)
        chord = model.add_line(doc, sk, a["pid"], b, con=True)
        model.add_constraint(doc, sk, "midpoint", [c["id"], chord["id"]])
        if s.get("hv") and self.ctrl.auto:
            model.add_constraint(doc, sk, "horizontal" if s["hv"] == "h"
                                 else "vertical", [chord["id"]])
        self.picks = []
        self.ctrl.commit()

    def _click_slot(self, sk, s) -> None:
        """Slot: the centres of its two round ends, then its half-width."""
        if len(self.picks) < 2:
            if self.picks and math.dist(s["uv"], self.picks[0]["uv"]) < 1e-9:
                return
            self.picks.append(dict(self._take(sk, s), hv=s.get("hv")))
            self.ctrl.refresh()
            return
        r = self._slot_radius(s["uv"])
        if r > 1e-9:
            self._slot(sk, r)

    def _slot_radius(self, uv) -> float:
        (ax, ay), (bx, by) = self.picks[0]["uv"], self.picks[1]["uv"]
        L = math.hypot(bx - ax, by - ay)
        return abs((bx - ax) * (uv[1] - ay) - (by - ay) * (uv[0] - ax)) / L

    def _slot_points(self, r: float) -> list:
        """Corners of the slot outline, counter-clockwise from A's right."""
        (ax, ay), (bx, by) = self.picks[0]["uv"], self.picks[1]["uv"]
        L = math.hypot(bx - ax, by - ay)
        nx, ny = -(by - ay) / L * r, (bx - ax) / L * r
        return [(ax - nx, ay - ny), (bx - nx, by - ny),
                (bx + nx, by + ny), (ax + nx, ay + ny)]

    def _slot(self, sk, r: float, dimension: bool = False) -> None:
        doc = self.ctrl.doc
        a, b = self.picks
        p = [model.add_point(doc, sk, *q)["id"] for q in self._slot_points(r)]
        l1 = model.add_line(doc, sk, p[0], p[1])
        arc_b = model.add_arc(doc, sk, b["pid"], p[1], p[2])
        l2 = model.add_line(doc, sk, p[2], p[3])
        arc_a = model.add_arc(doc, sk, a["pid"], p[3], p[0])
        for ln, arc in ((l1, arc_b), (l2, arc_b), (l2, arc_a), (l1, arc_a)):
            model.add_constraint(doc, sk, "tangent", [ln["id"], arc["id"]])
        model.add_constraint(doc, sk, "equal", [arc_a["id"], arc_b["id"]])
        if b.get("hv") and self.ctrl.auto:
            model.add_constraint(doc, sk, "horizontal" if b["hv"] == "h"
                                 else "vertical", [a["pid"], b["pid"]])
        if dimension:
            model.add_constraint(doc, sk, "radius", [arc_b["id"]], r)
        self.picks = []
        self.ctrl.commit()

    def _click_paste(self, sk, s) -> None:
        if self.ctrl.paste_at(s["uv"]) is not None:
            self.ctrl.set_mode("select")

    def _edit_click(self, sk, x, y, mode: str) -> None:
        """Trim: one click on the stretch to remove. Offset: click the
        outline, then click where its copy goes (or type the distance)."""
        uv = cursor_uv(self.viewport, sk, x, y)
        if uv is None:
            self.ctrl.say("The view is edge-on to the sketch plane — orbit "
                          "or use «Align view».")
            return
        if mode == "offset" and self.picks:
            eid = self.picks[0]["eid"]
            self.picks, self.cursor = [], None
            self.ctrl.offset(eid, edit.offset_side(sk, eid, uv))
            return
        hit = self.pick(sk, x, y, labels=False)
        by = model.ents(sk)
        if hit is None or by[hit[1]]["t"] == "point":
            self.ctrl.say("Click a line, an arc or a circle.")
            return
        if mode == "trim":
            self.ctrl.trim(hit[1], uv)
        elif mode == "extend":
            self.ctrl.extend(hit[1], uv)
        else:
            self.picks = [{"uv": uv, "pid": None, "eid": hit[1]}]
            self.ctrl.hover = None
            self.ctrl.say("Offset — move to the side and click, or type the "
                          "distance + Enter", 8000)
            self.ctrl.refresh()

    def _click_fillet(self, sk, s, chamfer: bool = False) -> None:
        if s["pid"] is None:
            self.ctrl.say("Click the corner POINT where two lines meet.")
            return
        from PySide6.QtCore import QTimer
        pid = s["pid"]
        QTimer.singleShot(0, lambda: self.ctrl.bevel(pid, chamfer=chamfer))

    def _click_chamfer(self, sk, s) -> None:
        self._click_fillet(sk, s, chamfer=True)

    # -- moving ---------------------------------------------------------------
    def _move_click(self, sk, x, y) -> None:
        if self.grab is not None:           # drop
            self.grab = None
            self.ctrl.commit()
            return
        hit = self.pick(sk, x, y, labels=False)
        uv = cursor_uv(self.viewport, sk, x, y)
        if hit is None or uv is None:
            return
        e = model.ents(sk)[hit[1]]
        if e["t"] == "point" and e.get("fixed"):
            self.ctrl.say("That point is fixed.")
            return
        if e["t"] == "arc":
            self.ctrl.say("Move an arc by its points.")
            return
        self.grab = {"kind": e["t"], "id": e["id"], "from": uv,
                     "base": copy.deepcopy(sk["ents"])}
        self.ctrl.hover = hit

    def _drag(self, sk, x, y) -> None:
        g = self.grab
        by = model.ents(sk)
        e = by.get(g["id"])
        if e is None:
            return
        if g["kind"] == "point":
            s = self.snap(sk, x, y, skip=self._attached(sk, e["id"]))
            if s is None:
                return
            drag = {e["id"]: s["uv"]}
        else:
            uv = cursor_uv(self.viewport, sk, x, y)
            if uv is None:
                return
            if g["kind"] == "circle":
                c = by[e["c"]]
                drag = {e["id"]: max(math.hypot(uv[0] - c["x"], uv[1] - c["y"]),
                                     1e-6)}
            else:
                base = {b["id"]: b for b in g["base"]}
                du, dv = uv[0] - g["from"][0], uv[1] - g["from"][1]
                drag = {p: (base[p]["x"] + du, base[p]["y"] + dv)
                        for p in (e["p1"], e["p2"]) if not by[p].get("fixed")}
        self.ctrl.result = solver.solve(sk, drag=drag)

    @staticmethod
    def _attached(sk, pid) -> set:
        """The point itself and the curves hanging from it (not snap targets
        for the point being moved)."""
        return {pid} | {e["id"] for e in sk["ents"]
                        if pid in model.point_refs(e)}

    # -- preview --------------------------------------------------------------
    def preview_uv(self) -> list:
        """Polylines (workplane coordinates) of the shape in progress."""
        if self.cursor is None:
            return []
        mode, cur = self.ctrl.mode, self.cursor["uv"]
        if mode == "paste":
            clip = self.ctrl.clip
            if not clip:
                return []
            du, dv = cur[0] - clip["centre"][0], cur[1] - clip["centre"][1]
            by = {e["id"]: e for e in clip["ents"]}
            return [[(u + du, v + dv) for u, v in curve_uv(None, by, e)]
                    for e in clip["ents"] if e["t"] != "point"]
        if not self.picks:
            return []
        if mode == "crect":
            c = self.picks[0]["uv"]
            du, dv = abs(cur[0] - c[0]), abs(cur[1] - c[1])
            return [[(c[0] - du, c[1] - dv), (c[0] + du, c[1] - dv),
                     (c[0] + du, c[1] + dv), (c[0] - du, c[1] + dv),
                     (c[0] - du, c[1] - dv)]]
        if mode == "semi":
            s = self.picks[0]["uv"]
            if math.dist(s, cur) < 1e-9:
                return []
            mid = ((s[0] + cur[0]) / 2, (s[1] + cur[1]) / 2)
            return [convert.arc_points(mid, s, cur, 48), [s, cur]]
        if mode == "slot":
            if len(self.picks) == 1:
                return [[self.picks[0]["uv"], cur]]
            r = self._slot_radius(cur)
            if r < 1e-9:
                return [[self.picks[0]["uv"], self.picks[1]["uv"]]]
            p = self._slot_points(r)
            a, b = self.picks[0]["uv"], self.picks[1]["uv"]
            return [[p[0], p[1]], convert.arc_points(b, p[1], p[2], 48),
                    [p[2], p[3]], convert.arc_points(a, p[3], p[0], 48)]
        if mode == "offset":
            sk = self.ctrl.sketch()
            eid = self.picks[0]["eid"]
            try:
                return edit.offset_preview(edit.offset_plan(
                    sk, eid, edit.offset_side(sk, eid, cur)))
            except (model.SketchError, KeyError):
                return []                    # too far: nothing to show
        a = self.picks[0]["uv"]
        if mode == "line":
            return [[self.picks[-1]["uv"], cur]]
        if mode == "rect":
            return [[a, (cur[0], a[1]), cur, (a[0], cur[1]), a]]
        if mode == "circle":
            r = math.hypot(cur[0] - a[0], cur[1] - a[1])
            pts = convert.circle_points(a, r, 64)
            return [pts + pts[:1], [a, cur]]
        if mode == "arc":
            if len(self.picks) == 1:
                return [[a, cur]]
            s = self.picks[1]["uv"]
            r = math.hypot(s[0] - a[0], s[1] - a[1])
            d = math.hypot(cur[0] - a[0], cur[1] - a[1])
            if d < 1e-12:
                return []
            end = (a[0] + (cur[0] - a[0]) * r / d, a[1] + (cur[1] - a[1]) * r / d)
            return [convert.arc_points(a, s, end, 64), [a, s], [a, end]]
        return []


class PickFaceTool(Tool):
    """One click on a face of anything in the model — a built solid, a
    component, loose geometry — and a new sketch starts on it."""
    name = "CAD Sketcher: sketch on face"
    description = "Click a face to start a sketch on it."
    uses_snap = False

    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl

    def on_activate(self, viewport) -> None:
        pass

    def on_deactivate(self, viewport) -> None:
        pass

    def status_clause(self) -> str:
        return "Click the face to sketch on"

    def on_hover(self, ctx) -> None:
        pass

    def on_click(self, ctx) -> None:
        try:
            plane = host.pick_face(ctx.viewport, ctx.screen.x(), ctx.screen.y())
        except model.SketchError as exc:
            self.ctrl.say(str(exc))
            return
        if plane is None:
            self.ctrl.say("No face there — click a face of the model "
                          "(Esc to cancel).")
            return
        self.ctrl.new_sketch_on_plane(*plane)     # hands over to SketchTool

    def on_cancel(self, viewport) -> None:
        try:
            from tools.select import SelectTool
            viewport.set_active_tool(SelectTool())
        except Exception:  # noqa: BLE001
            log.debug("could not go back to Select", exc_info=True)


# ---- the overlay ----------------------------------------------------------------

class Overlay:
    """``app.add_overlay(Overlay(ctrl))`` — paints the sketches each frame.

    IngeTrazo drops an overlay for good the first time it raises, so every
    frame is fenced here and a failure is logged once instead."""

    def __init__(self, ctrl) -> None:
        self.ctrl = ctrl
        self._failed = False

    def __call__(self, viewport, painter) -> None:
        try:
            self.paint(viewport, painter)
        except Exception:  # noqa: BLE001
            if not self._failed:
                self._failed = True
                log.exception("CAD Sketcher overlay failed")

    def paint(self, viewport, painter) -> None:
        ctrl = self.ctrl
        doc = ctrl.doc
        if not doc["sketches"]:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        referring = ctrl.active is not None and ctrl.mode == "ref"
        for sk in doc["sketches"]:
            if sk["id"] != ctrl.active and (referring
                                            or sk.get("visible", True)):
                self._idle(viewport, painter, sk)
        sk = ctrl.sketch()
        if sk is not None:
            self._active(viewport, painter, sk)

    @staticmethod
    def _polyline(painter, px, ok) -> None:
        if len(px) >= 2 and ok.all():
            painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in px]))

    def _grid(self, vp, painter, sk) -> None:
        """The sketch grid: around the drawing, never more than ~80 lines a
        way (a fine step over a big sketch is drawn every 5th / 10th line)."""
        step = float(self.ctrl.grid["step"])
        if step <= 0:
            return
        pts = [e for e in sk["ents"] if e["t"] == "point"]
        us, vs = [p["x"] for p in pts], [p["y"] for p in pts]
        tool = self.ctrl.tool
        if tool is not None and tool.cursor is not None:
            us.append(tool.cursor["uv"][0])
            vs.append(tool.cursor["uv"][1])
        pad = max(10 * step, 0.25 * max(max(us) - min(us), max(vs) - min(vs)))
        u0, u1, v0, v1 = min(us) - pad, max(us) + pad, min(vs) - pad, max(vs) + pad
        draw = step
        while max(u1 - u0, v1 - v0) / draw > 80:
            draw *= 5 if round(draw / step) % 10 in (1, 0) else 2
        i0, i1 = math.floor(u0 / draw), math.ceil(u1 / draw)
        j0, j1 = math.floor(v0 / draw), math.ceil(v1 / draw)
        segs = [((i * draw, j0 * draw), (i * draw, j1 * draw))
                for i in range(i0, i1 + 1)]
        segs += [((i0 * draw, j * draw), (i1 * draw, j * draw))
                 for j in range(j0, j1 + 1)]
        px, ok = to_pixels(vp, world_pts(sk, [p for s in segs for p in s]))
        painter.setPen(QPen(QColor(90, 100, 120, 70), 1.0))
        for k in range(len(segs)):
            if ok[2 * k] and ok[2 * k + 1]:
                painter.drawLine(QPointF(*px[2 * k]), QPointF(*px[2 * k + 1]))

    def _idle(self, vp, painter, sk) -> None:
        by = model.ents(sk)
        painter.setPen(QPen(C_IDLE, 1.2))
        for e in sk["ents"]:
            if e["t"] != "point" and not e.get("con"):
                self._polyline(painter, *to_pixels(
                    vp, world_pts(sk, curve_uv(sk, by, e, 32))))

    def _active(self, vp, painter, sk) -> None:
        ctrl = self.ctrl
        tool = ctrl.tool
        by = model.ents(sk)
        sel = set(ctrl.sel)
        hover = ctrl.hover[1] if ctrl.hover and ctrl.hover[0] == "ent" else None
        failed = set(ctrl.result.failed) if ctrl.result else set()

        # the workplane's own axes, from its origin
        if ctrl.grid["show"]:
            self._grid(vp, painter, sk)
        span = 0.25 * max((abs(c) for e in sk["ents"] if e["t"] == "point"
                           for c in (e["x"], e["y"])), default=1.0) + 0.25
        for axis, color in (((span, 0.0), QColor(200, 40, 40, 160)),
                            ((0.0, span), QColor(40, 150, 40, 160))):
            painter.setPen(QPen(color, 1.0, Qt.DashLine))
            self._polyline(painter, *to_pixels(
                vp, world_pts(sk, [(0.0, 0.0), axis])))

        mids: dict = {}                     # entity id → pixel anchor for labels
        for e in sk["ents"]:
            if e["t"] == "point":
                continue
            px, ok = to_pixels(vp, world_pts(sk, curve_uv(sk, by, e)))
            if not ok.all():
                continue
            color = C_SEL if e["id"] in sel else C_HOVER if e["id"] == hover \
                else C_CONSTR if e.get("con") else C_LINE
            painter.setPen(QPen(color, 2.4 if e["id"] in sel else 1.8,
                                Qt.DashLine if e.get("con") else Qt.SolidLine))
            self._polyline(painter, px, ok)
            mids[e["id"]] = tuple(px[len(px) // 2]) if len(px) > 2 \
                else tuple((px[0] + px[1]) / 2)

        pts = [e for e in sk["ents"] if e["t"] == "point"]
        px, ok = to_pixels(vp, world_pts(sk, [(p["x"], p["y"]) for p in pts]))
        for p, (x, y), front in zip(pts, px, ok):
            if not front:
                continue
            mids[p["id"]] = (x, y)
            color = C_SEL if p["id"] in sel else C_HOVER if p["id"] == hover \
                else C_FIXED if p.get("fixed") else C_LINE
            r = 4.0 if p["id"] in sel or p["id"] == hover else 3.0
            painter.setPen(QPen(color, 1.0))
            painter.setBrush(color)
            if p.get("ref"):                 # a reference: a diamond
                if color is C_FIXED:
                    color = C_REF
                    painter.setPen(QPen(color, 1.0))
                    painter.setBrush(color)
                painter.drawPolygon(QPolygonF(
                    [QPointF(x, y - r - 2), QPointF(x + r + 2, y),
                     QPointF(x, y + r + 2), QPointF(x - r - 2, y)]))
            elif p.get("fixed"):
                painter.drawRect(QRectF(x - r, y - r, 2 * r, 2 * r))
            else:
                painter.drawEllipse(QPointF(x, y), r, r)
        painter.setBrush(Qt.NoBrush)

        self._constraints(painter, sk, mids, failed)

        if tool is not None and ctrl.mode == "ref":
            self._ref_spots(painter, tool)
        if tool is not None:
            painter.setPen(QPen(C_PREVIEW, 1.6, Qt.DashLine))
            for line in tool.preview_uv():
                self._polyline(painter, *to_pixels(vp, world_pts(sk, line)))
            cur = tool.cursor
            if cur is not None and ctrl.mode not in ("select", "move"):
                at, front = to_pixels(vp, world_pts(sk, [cur["uv"]]))
                if front[0]:
                    x, y = float(at[0, 0]), float(at[0, 1])
                    painter.setPen(QPen(C_PREVIEW, 1.4))
                    painter.drawEllipse(QPointF(x, y), 5.0, 5.0)
                    mark = "on point" if cur["pid"] is not None \
                        else "on curve" if cur["curve"] is not None \
                        else {"h": "horizontal", "v": "vertical"}.get(
                            cur["hv"], "grid" if cur.get("grid") else None)
                    if mark:
                        painter.drawText(QPointF(x + 9, y - 9), mark)

    @staticmethod
    def _ref_spots(painter, tool) -> None:
        """The spots of the other sketches that can be referred to."""
        hit = tool.ref_hit
        for cand, x, y in tool.ref_spots():
            hot = hit is not None and all(
                cand[k] == hit[k] for k in ("sk", "kind", "e", "q"))
            r = 5.5 if hot else 3.5
            painter.setPen(QPen(C_PREVIEW if hot else C_REF, 1.4))
            painter.setBrush(C_PREVIEW if hot else Qt.NoBrush)
            painter.drawPolygon(QPolygonF(
                [QPointF(x, y - r), QPointF(x + r, y),
                 QPointF(x, y + r), QPointF(x - r, y)]))
            if hot:
                painter.drawText(QPointF(x + 9, y - 9),
                                 model.REF_NAMES[cand["kind"]])
        painter.setBrush(Qt.NoBrush)

    def _constraints(self, painter, sk, mids, failed) -> None:
        ctrl = self.ctrl
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        sel = set(ctrl.sel_cons)
        hover = ctrl.hover[1] if ctrl.hover and ctrl.hover[0] == "con" else None
        stack: dict = {}
        labels: dict = {}
        for c in sk["cons"]:
            anchors = [mids[i] for i in c["e"] if i in mids]
            if not anchors:
                continue
            dim = "v" in c
            if dim:
                text = f"{c['v']:.4g}°" if c["t"] == "angle" \
                    else f"{c['v']:.4g}" if c["t"] == "ratio" else fmt_len(c["v"])
                text = {"radius": "R ", "diameter": "Ø ", "ratio": "× ",
                        "angle": "∠ "}.get(c["t"], {"h": "↔ ", "v": "↕ "}.get(
                            c.get("axis"), "")) + text
            else:
                text = GLYPH.get(c["t"], "?")
            x = sum(a[0] for a in anchors) / len(anchors)
            y = sum(a[1] for a in anchors) / len(anchors)
            key = (round(x / 12), round(y / 12))
            n = stack.get(key, 0)
            stack[key] = n + 1
            w, h = fm.horizontalAdvance(text) + 8, fm.height() + 2
            rect = QRectF(x + 8 + n * 4, y - h - 4 - n * (h + 1), w, h)
            labels[c["id"]] = rect
            color = C_FAIL if c["id"] in failed else C_SEL if c["id"] in sel \
                else C_HOVER if c["id"] == hover else C_DIM
            if dim and len(anchors) == 2:
                painter.setPen(QPen(color, 0.8, Qt.DotLine))
                painter.drawLine(QPointF(*anchors[0]), QPointF(*anchors[1]))
            painter.setPen(QPen(color, 1.0))
            painter.setBrush(QColor(255, 255, 255, 215))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setBrush(Qt.NoBrush)
            painter.drawText(rect, Qt.AlignCenter, text)
        if ctrl.tool is not None:
            ctrl.tool.labels = labels
