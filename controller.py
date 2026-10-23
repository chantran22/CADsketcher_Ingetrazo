# SPDX-License-Identifier: GPL-3.0-or-later
"""The extension's state and every operation on it.

The panel and the viewport tool are two views of this object: they call its
methods and repaint on ``changed``. Each operation that alters the document
ends in :meth:`commit` — solve the active sketch, store the document, rebuild
the solids that depend on it — which is one undo step in IngeTrazo.
"""
from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QObject, Signal

from . import edit, host, model, solver
from .model import SketchError

log = logging.getLogger("ingetrazo.plugins.cad_sketcher")

VIEW_OF_PLANE = {"XY": "top", "XZ": "front", "YZ": "right"}
FEATURE_NAMES = {"extrude": "Extrude", "revolve": "Revolve", "loft": "Loft",
                 "sweep": "Sweep", "face": "Face"}


def fmt_len(v: float) -> str:
    """A length (metres) in the document's unit, without trailing zeros."""
    name, k = host.unit()
    dec = {"m": 4, "cm": 2, "mm": 1}[name]
    s = f"{v * k:.{dec}f}".rstrip("0").rstrip(".")
    return f"{s or '0'} {name}"


class Controller(QObject):
    changed = Signal()

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.doc = model.normalize(app.document_data(None))
        self.active: int | None = None          # sketch being edited
        self.sel: list = []                     # entity ids, in pick order
        self.sel_cons: list = []                # constraint ids
        self.hover = None                       # ("ent" | "con", id) | None
        self.result: solver.Result | None = None
        self.mode = "select"
        self.auto = True                        # automatic constraints
        self.grid = {"show": False, "snap": False, "step": 0.1}   # metres
        self.clip = None                        # copied entities (edit.snapshot)
        self.tool = None                        # set by tool.SketchTool
        self.message = ""
        self._committing = False
        self._bevel_last: dict = {}             # last fillet / chamfer size

    # ---- access -----------------------------------------------------------------
    @property
    def viewport(self):
        return self.app.viewport

    def sketch(self) -> dict | None:
        return model.sketch(self.doc, self.active) \
            if self.active is not None else None

    def say(self, text: str, ms: int = 5000) -> None:
        self.message = text
        flash = getattr(self.viewport, "flash_status", None)
        if callable(flash) and text:
            try:
                flash(f"CAD Sketcher: {text}", ms)
            except Exception:  # noqa: BLE001
                pass

    def refresh(self) -> None:
        self.changed.emit()
        self.viewport.update()

    # ---- document ---------------------------------------------------------------
    def reload(self) -> None:
        """The document changed under us (undo, redo, New, Open)."""
        if self._committing:
            return
        # IngeTrazo also signals a change AFTER our own commit (at the next
        # paint): nothing to do then — and a line chain must survive it.
        if model.normalize(self.app.document_data(None)) == self.doc:
            self.changed.emit()      # a solid may have been hidden or shown
            return
        if self.tool is not None:
            self.tool.reset()
        self.doc = model.normalize(self.app.document_data(None))
        sk = self.sketch()
        if sk is None:
            self.active = None
            self.sel, self.sel_cons, self.result = [], [], None
        else:
            by = model.ents(sk)
            self.sel = [i for i in self.sel if i in by]
            cons = {c["id"] for c in sk["cons"]}
            self.sel_cons = [i for i in self.sel_cons if i in cons]
            self.result = solver.solve(copy.deepcopy(sk))
        self.hover = None
        self.changed.emit()

    def commit(self, solve: bool = True) -> None:
        """Solve, store, rebuild the solids — one undo step."""
        sk = self.sketch()
        if sk is not None and solve:
            self.result = solver.solve(sk)
            if not self.result.ok:
                self.say(self.result.text)
        self._follow_references()
        self._committing = True
        try:
            host.commit(self.app, self.doc)
        finally:
            self._committing = False
        errors = [f"{f['name']}: {f.get('error') or f['warn']}"
                  for f in self.doc["features"]
                  if (f.get("error") or f.get("warn"))
                  and (sk is None or sk["id"] in host.feature_sketches(f))]
        if errors and (self.result is None or self.result.ok):
            self.say(errors[0])
        self.refresh()

    def _follow_references(self) -> None:
        """Sketches that refer to another one follow it: their reference
        points move, they are solved again (and so on down the chain)."""
        for _ in range(len(self.doc["sketches"]) + 1):
            changed = model.update_refs(self.doc)
            if not changed:
                return
            for sid in changed:
                sk = model.sketch(self.doc, sid)
                res = solver.solve(sk)
                if sid == self.active:
                    self.result = res
                elif not res.ok:
                    self.say(f"«{sk['name']}» can no longer follow its "
                             f"references: {res.text}")

    def add_reference(self, cand: dict):
        """A reference point in the active sketch for a spot of another
        sketch (``cand`` from :func:`model.ref_candidates`)."""
        sk = self.sketch()
        if sk is None:
            return None
        try:
            p = model.add_ref_point(self.doc, sk, cand)
        except SketchError as exc:
            self.say(str(exc))
            return None
        self.sel = [p["id"]]
        self.commit()
        src = model.sketch(self.doc, cand["sk"])
        self.say(f"Reference to the {model.REF_NAMES[cand['kind']]} of "
                 f"«{src['name']}» — it is selected: pick what goes with it, "
                 "then a constraint.", 9000)
        return p

    # ---- sketches ---------------------------------------------------------------
    def _unique(self, base: str, items: list) -> str:
        names = {it["name"] for it in items}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def new_sketch(self, plane: str = "XY", offset: float = 0.0,
                   name: str | None = None) -> dict:
        x, y = model.PLANES[plane]
        n = model._cross(x, y)
        sk = model.add_sketch(self.doc, name or self._unique(
            "Sketch", self.doc["sketches"]), [c * offset for c in n], x, y)
        sk["plane"] = sk["base"]["plane"] = plane
        self.active = sk["id"]
        self.sel, self.sel_cons = [], []
        self.commit()
        self.edit(sk["id"])
        return sk

    def place_sketch(self, sid, origin, angles) -> None:
        """Move / turn a whole sketch (see :func:`model.place_sketch`); the
        solids made from it — and sketches referring to it — follow."""
        sk = model.sketch(self.doc, sid)
        if sk is None:
            return
        model.place_sketch(sk, origin, angles)
        self.commit()
        if sid == self.active:
            self.say("Sketch placed — Align view looks straight at it again."
                     if sk.get("plane") else "Sketch placed.")

    def new_sketch_on_face(self) -> dict:
        face = next((e for e in self.viewport.scene.selection
                     if type(e).__name__ == "Face"), None)
        if face is None:
            raise SketchError("Select a face first (with IngeTrazo's Select "
                              "tool), then choose «On selected face».")
        return self.new_sketch_on_plane(*host.face_plane(face))

    def new_sketch_on_plane(self, origin, x, y) -> dict:
        """A sketch on any plane in the model (a picked face of a solid)."""
        sk = model.add_sketch(self.doc, self._unique(
            "Sketch", self.doc["sketches"]), origin, x, y)
        self.active = sk["id"]
        self.sel, self.sel_cons = [], []
        self.commit()
        self.edit(sk["id"])
        return sk

    def pick_face(self) -> None:
        """Hand the viewport a one-shot tool: click a face of anything in
        the model — a built solid too — and a sketch starts on it."""
        from .tool import PickFaceTool
        if self.active is not None:
            self.finish()
        vp = self.viewport
        vp.set_active_tool(PickFaceTool(self))
        vp.setFocus()
        self.say("Click the face to sketch on (Esc to cancel).", 10000)

    def toggle_grid_snap(self) -> None:
        self.grid["snap"] = not self.grid["snap"]
        self.grid["show"] = self.grid["show"] or self.grid["snap"]
        self.say(f"Grid snap {'on' if self.grid['snap'] else 'off'} "
                 f"({fmt_len(self.grid['step'])})")
        self.refresh()

    # ---- selection helpers, copies ------------------------------------------------
    def select_all(self) -> None:
        sk = self.sketch()
        if sk is not None:
            self.sel = [e["id"] for e in sk["ents"]
                        if e["t"] != "point" or not e.get("fixed")]
            self.refresh()

    def select_chain(self, eid=None) -> None:
        """Add the whole connected outline through ``eid`` (default: the
        last entity selected) to the selection."""
        sk = self.sketch()
        if sk is None:
            return
        if eid is None:
            eid = next((i for i in reversed(self.sel)
                        if model.ents(sk).get(i, {}).get("t") in ("line", "arc")),
                       None)
        if eid is None:
            self.say("Select a line or an arc of the outline first.")
            return
        for i in edit.chain_ids(sk, eid):
            if i not in self.sel:
                self.sel.append(i)
        self.refresh()

    def _edit(self, fn, select=None):
        """Run a sketch edit that may refuse (``SketchError``), then commit."""
        sk = self.sketch()
        if sk is None:
            return None
        try:
            out = fn(sk)
        except SketchError as exc:
            self.say(str(exc))
            return None
        self.sel = select(out) if select else []
        self.hover = None
        self.commit()
        return out

    @staticmethod
    def _new_ids(maps) -> list:
        return [new for m in maps for new in m.values()]

    def extend(self, eid, uv) -> bool:
        return self._edit(lambda sk: edit.extend(self.doc, sk, eid, uv) or True) \
            is not None

    def copy(self) -> bool:
        sk = self.sketch()
        if sk is None:
            return False
        try:
            self.clip = edit.snapshot(sk, self.sel)
        except SketchError as exc:
            self.say(str(exc))
            return False
        self.say("Copied — Paste (Ctrl+V), then click where it goes.")
        return True

    def paste_at(self, uv):
        """The clipboard, its middle at ``uv``; the copy ends up selected."""
        if self.clip is None:
            self.say("Nothing copied yet.")
            return None
        cx, cy = self.clip["centre"]
        du, dv = uv[0] - cx, uv[1] - cy
        return self._edit(
            lambda sk: edit.paste(self.doc, sk, self.clip,
                                  lambda x, y: (x + du, y + dv)),
            select=lambda m: list(m.values()))

    def mirror(self, axis=None):
        """Mirror the selection: ``"x"`` / ``"y"``, or ``None`` = about the
        line selected LAST (the copy then stays symmetric)."""
        if axis is None:
            axis = self.sel[-1] if self.sel else None
        ids = list(self.sel)
        return self._edit(lambda sk: edit.mirror(self.doc, sk, ids, axis),
                          select=lambda m: list(m.values()))

    def pattern_linear(self, count: int, du: float, dv: float):
        ids = list(self.sel)
        return self._edit(
            lambda sk: edit.pattern_linear(self.doc, sk, ids, count, du, dv),
            select=self._new_ids)

    def pattern_circular(self, count: int, angle: float = 360.0):
        """About the point selected LAST, if the selection ends with one
        that is not part of what is copied; else about the sketch origin."""
        sk = self.sketch()
        if sk is None:
            return None
        by = model.ents(sk)
        ids, centre = list(self.sel), (0.0, 0.0)
        last = by.get(ids[-1]) if ids else None
        if last is not None and last["t"] == "point" and len(ids) > 1:
            centre = (last["x"], last["y"])
            ids = ids[:-1]
        return self._edit(
            lambda s: edit.pattern_circular(self.doc, s, ids, count, centre, angle),
            select=self._new_ids)

    def edit(self, sid) -> None:
        """Make ``sid`` the active sketch and hand the viewport our tool."""
        if model.sketch(self.doc, sid) is None:
            return
        self.active = sid
        self.sel, self.sel_cons, self.hover = [], [], None
        self.result = solver.solve(copy.deepcopy(self.sketch()))
        self.set_mode(self.mode if self.mode != "move" else "select")

    def finish(self) -> None:
        """Leave the sketch (CAD_Sketcher's «Leave Sketch»)."""
        self.active = None
        self.sel, self.sel_cons, self.hover, self.result = [], [], None, None
        vp = self.viewport
        if self.tool is not None and getattr(vp, "active_tool", None) is self.tool:
            try:
                from tools.select import SelectTool
                vp.set_active_tool(SelectTool())
            except Exception:  # noqa: BLE001
                log.debug("could not go back to Select", exc_info=True)
        self.refresh()

    def delete_sketch(self, sid) -> None:
        if self.active == sid:
            self.finish()
        self.doc["sketches"] = [s for s in self.doc["sketches"] if s["id"] != sid]
        self.commit(solve=False)

    def duplicate_sketch(self, sid, name: str | None = None,
                         offset: float = 0.0, plane: str | None = None):
        """Copy a whole sketch (shape, constraints, dimensions) and start
        editing the copy — see :func:`model.duplicate_sketch`."""
        src = model.sketch(self.doc, sid)
        if src is None:
            return None
        if self.active is not None:
            self.finish()
        new = model.duplicate_sketch(
            self.doc, src, (name or "").strip()
            or self._unique(f"{src['name']} copy", self.doc["sketches"]),
            offset, plane)
        self.active = new["id"]
        self.commit()                  # solved (references re-projected) + stored
        self.edit(new["id"])
        self.say(f"«{new['name']}» is a copy of «{src['name']}» — edit it freely, "
                 "the original is untouched.")
        return new

    def rename_sketch(self, sid, name: str) -> None:
        sk = model.sketch(self.doc, sid)
        if sk is not None and name.strip():
            sk["name"] = name.strip()
            self.commit(solve=False)

    def set_visible(self, sid, on: bool) -> None:
        sk = model.sketch(self.doc, sid)
        if sk is not None:
            sk["visible"] = bool(on)
            self.commit(solve=False)

    def align_view(self) -> None:
        sk = self.sketch()
        view = VIEW_OF_PLANE.get((sk or {}).get("plane"))
        if view is None:
            self.say("Only sketches on XY, XZ or YZ have a standard view.")
            return
        try:
            self.viewport.camera.set_view(view)
            self.viewport.update()
        except Exception:  # noqa: BLE001
            log.debug("align view failed", exc_info=True)

    # ---- tool mode --------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if self.tool is not None:
            self.tool.reset()
            vp = self.viewport
            if self.active is not None \
                    and getattr(vp, "active_tool", None) is not self.tool:
                vp.set_active_tool(self.tool)
            if self.active is not None:
                vp.setFocus()           # so the sketch shortcuts reach us
                from .tool import HINTS
                self.say(HINTS.get(mode, ""), 8000)
        self.refresh()

    # ---- selection --------------------------------------------------------------
    def toggle(self, kind: str, ident) -> None:
        lst = self.sel if kind == "ent" else self.sel_cons
        if ident in lst:
            lst.remove(ident)
        else:
            lst.append(ident)
        self.refresh()

    def clear_selection(self) -> None:
        if self.sel or self.sel_cons:
            self.sel, self.sel_cons = [], []
            self.refresh()

    # ---- constraints ------------------------------------------------------------
    def ask_value(self, title: str, default: float, angle: bool = False,
                  plain: bool = False):
        """Ask the user for a dimension; ``None`` when cancelled. Lengths are
        shown in the document's unit and returned in metres."""
        from PySide6.QtWidgets import QInputDialog
        name, k = ("°", 1.0) if angle else ("", 1.0) if plain else host.unit()
        value, ok = QInputDialog.getDouble(
            self.viewport.window(), "CAD Sketcher",
            f"{title} ({name}):" if name else f"{title}:",
            default * k, -1e9, 1e9, 4)
        return value / k if ok else None

    def constrain(self, ctype: str, axis: str | None = None, value=None):
        """Add a constraint over the selected entities."""
        sk = self.sketch()
        if sk is None:
            return None
        try:
            refs = model.constraint_refs(sk, ctype, self.sel)
            if ctype in model.DIMENSIONAL and value is None:
                now = solver.measure(sk, ctype, refs, axis)
                title = {"h": "Horizontal distance", "v": "Vertical distance"
                         }.get(axis, ctype.capitalize())
                value = self.ask_value(title, now,
                                       angle=ctype == "angle",
                                       plain=ctype == "ratio")
                if value is None:
                    return None
            if ctype in ("distance", "radius", "diameter", "ratio") \
                    and value is not None and value <= 0:
                raise SketchError("The value must be positive.")
            c = model.add_constraint(self.doc, sk, ctype, refs, value, axis)
        except SketchError as exc:
            self.say(str(exc))
            return None
        self.sel = []
        self.commit()
        return c

    def bevel(self, pid, value=None, chamfer: bool = False):
        """Fillet (or chamfer) the corner at point ``pid``; the size is asked
        for and stays in the sketch as an editable dimension."""
        sk = self.sketch()
        if sk is None:
            return None
        key = "chamfer" if chamfer else "fillet"
        if value is None:
            value = self.ask_value("Chamfer distance" if chamfer
                                   else "Fillet radius",
                                   self._bevel_last.get(key, 0.1))
            if value is None:
                return None
        try:
            new = model.bevel(self.doc, sk, pid, float(value), chamfer)
        except SketchError as exc:
            self.say(str(exc))
            return None
        self._bevel_last[key] = float(value)
        self.sel = []
        self.commit()
        return new

    def trim(self, eid, uv) -> bool:
        """Cut entity ``eid`` back to its nearest crossings around ``uv``."""
        sk = self.sketch()
        if sk is None:
            return False
        try:
            edit.trim(self.doc, sk, eid, uv)
        except SketchError as exc:
            self.say(str(exc))
            return False
        self.sel, self.hover = [], None
        self.commit()
        return True

    def offset(self, eid, distance: float) -> list:
        """Offset the outline through ``eid`` by ``distance`` (signed: see
        :func:`edit.offset_plan`). Returns the new entity ids."""
        sk = self.sketch()
        if sk is None:
            return []
        try:
            made = edit.offset_apply(self.doc, sk,
                                     edit.offset_plan(sk, eid, distance))
        except SketchError as exc:
            self.say(str(exc))
            return []
        self.sel = []
        self.commit()
        return made

    def edit_value(self, cid) -> None:
        sk = self.sketch()
        c = model.find(sk["cons"], cid) if sk else None
        if c is None or "v" not in c:
            return
        v = self.ask_value(model.describe(sk, c, fmt_len).split(" ")[0],
                           c["v"], angle=c["t"] == "angle",
                           plain=c["t"] == "ratio")
        if v is not None:
            self.set_value(cid, v)

    def set_value(self, cid, value: float) -> None:
        sk = self.sketch()
        c = model.find(sk["cons"], cid) if sk else None
        if c is not None:
            c["v"] = float(value)
            self.commit()

    def delete_selected(self) -> None:
        sk = self.sketch()
        if sk is None or not (self.sel or self.sel_cons):
            return
        sk["cons"] = [c for c in sk["cons"] if c["id"] not in self.sel_cons]
        model.delete_entities(sk, self.sel)
        self.sel, self.sel_cons, self.hover = [], [], None
        self.commit()

    def toggle_flag(self, flag: str) -> None:
        """``"con"`` (construction) on anything, ``"fixed"`` on points."""
        sk = self.sketch()
        if sk is None:
            return
        by = model.ents(sk)
        hit = [by[i] for i in self.sel if i in by
               and (flag == "con" or by[i]["t"] == "point")]
        if not hit:
            self.say("Select points first." if flag == "fixed"
                     else "Select entities first.")
            return
        on = not all(e.get(flag) for e in hit)
        for e in hit:
            e[flag] = on
        self.commit()

    # ---- features ---------------------------------------------------------------
    def add_feature(self, kind: str, **params) -> dict:
        feat = {"id": model.new_id(self.doc), "t": kind,
                "name": self._unique(FEATURE_NAMES[kind], self.doc["features"])}
        feat.update(params)
        self.doc["features"].append(feat)
        # The solid now shows the shape: its sketches step back (the eye in
        # the sketch list brings one out again).
        for sid in host.feature_sketches(feat):
            sk = model.sketch(self.doc, sid)
            if sk is not None:
                sk["visible"] = False
        self.commit(solve=False)
        if feat.get("error"):
            self.say(f"{feat['name']}: {feat['error']}")
        return feat

    def update_feature(self, fid, **params) -> None:
        feat = model.find(self.doc["features"], fid)
        if feat is not None:
            feat.update(params)
            # Editing a solid that was deleted or used up in the model
            # builds it afresh.
            if feat.pop("detached", None):
                feat.pop("uid", None)
            self.commit(solve=False)
            if feat.get("error"):
                self.say(f"{feat['name']}: {feat['error']}")

    def delete_feature(self, fid) -> None:
        self.doc["features"] = [f for f in self.doc["features"]
                                if f["id"] != fid]
        self.commit(solve=False)

    def solid_visible(self, fid):
        """True / False as the feature's group is shown or hidden in the
        model; ``None`` when it has no group (it could not be built)."""
        g = host.feature_groups(self.viewport.scene, self.doc).get(fid)
        return None if g is None else not bool(getattr(g, "hidden", False))

    def set_solid_visible(self, fid, on: bool) -> None:
        """Hide or show a feature's solid — IngeTrazo's own Hide, so it is
        an undo step and Edit ▸ Unhide knows about it."""
        g = host.feature_groups(self.viewport.scene, self.doc).get(fid)
        if g is None or bool(getattr(g, "hidden", False)) == (not on):
            return
        host.set_hidden(self.viewport, g, not on)
        self.refresh()

    def set_segments(self, n: int) -> None:
        if int(n) != int(self.doc.get("segments", 32)):
            self.doc["segments"] = max(8, int(n))
            self.commit(solve=False)
