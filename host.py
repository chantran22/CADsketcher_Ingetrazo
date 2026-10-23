# SPDX-License-Identifier: GPL-3.0-or-later
"""The ONE module that touches IngeTrazo's engine (``core.*``).

It turns the features of the document into groups and keeps them in step:
:func:`commit` stores the document AND swaps every group whose sketch changed,
as one undo step — so Undo takes back the sketch edit and the solid together.

A feature's group is found again through ``group.ext["cad_sketcher"]``, which
IngeTrazo saves in the .igz and carries along with copies.
"""
from __future__ import annotations

import json

from . import convert, features, model
from .features import FeatureError

EXT = "cad_sketcher"
_VOLATILE = ("uid", "sig", "error", "warn", "name", "detached")


def _vec(p):
    from PySide6.QtGui import QVector3D
    return QVector3D(float(p[0]), float(p[1]), float(p[2]))


def mesh_from_faces(faces: list, soft: list):
    """A host ``Mesh`` from the plain polygons of :mod:`features`."""
    from core.mesh import Mesh
    mesh = Mesh()
    for f in faces:
        mesh.add_face([_vec(p) for p in f["loop"]],
                      [[_vec(p) for p in h] for h in f.get("holes", ())])
    if soft:
        def key(p):
            return (round(p[0], 6), round(p[1], 6), round(p[2], 6))
        want = {frozenset((key(a), key(b))) for a, b in soft}
        for e in mesh.edges:
            a, b = e.a, e.b
            if frozenset((key((a.x(), a.y(), a.z())),
                          key((b.x(), b.y(), b.z())))) in want:
                e.soft = True
    return mesh


def _sketch(doc: dict, sid, what: str = "sketch") -> dict:
    sk = model.sketch(doc, sid)
    if sk is None:
        raise FeatureError(f"The {what} was deleted.")
    return sk


def _regions(doc: dict, sk: dict) -> list:
    loops = convert.walk(sk, int(doc.get("segments", 32)))[0]
    if not loops:
        raise FeatureError(f"«{sk['name']}» has no closed profile.")
    if convert.crossing(loops):
        raise FeatureError(f"«{sk['name']}»: outlines cross each other — "
                           "separate them, or mark one as Construction.")
    return convert.regions(loops)


def _sweep(doc: dict, feat: dict):
    """Profile along path with the host's Follow Me engine."""
    from core.mesh import Mesh
    from core.sweep import orient_closed_path, sweep_profile
    prof = _sketch(doc, feat.get("profile"), "profile sketch")
    path_sk = _sketch(doc, feat.get("path"), "path sketch")
    reg = _regions(doc, prof)[0]
    loops, paths = convert.walk(path_sk, int(doc.get("segments", 32)))
    if paths:
        pts2, closed = paths[0]["pts"], False
    elif loops:
        pts2, closed = loops[0]["pts"], True
    else:
        raise FeatureError(f"«{path_sk['name']}» has no path to follow.")
    if len(pts2) < 2:
        raise FeatureError("The path is too short.")
    path = [_vec(model.to_world(path_sk, u, v)) for u, v in pts2]
    mesh = Mesh()
    face = mesh.add_face(
        [_vec(model.to_world(prof, u, v)) for u, v in reg["outer"]["pts"]],
        [[_vec(model.to_world(prof, u, v)) for u, v in h["pts"]]
         for h in reg["holes"]])
    if not closed:
        # start the walk at the end of the path nearer the profile
        c = face.centroid()
        if (path[-1] - c).length() < (path[0] - c).length():
            path.reverse()
    else:
        path = list(orient_closed_path(path, face))
    if not sweep_profile(mesh, face, path, closed):
        raise FeatureError("The profile cannot follow that path (it must not "
                           "lie along the path's first segment, and the path "
                           "must not double back).")
    return mesh


def build_mesh(doc: dict, feat: dict):
    """The feature's geometry as a host mesh; raises :class:`FeatureError`."""
    t = feat["t"]
    if t == "sweep":
        return _sweep(doc, feat)
    if t == "loft":
        secs = []
        for sid in feat.get("sketches", ()):
            sk = _sketch(doc, sid)
            secs.append((sk, _regions(doc, sk)[0]))
        faces, soft = features.loft(secs)
    else:
        sk = _sketch(doc, feat.get("sketch"))
        regs = _regions(doc, sk)
        if t == "face":
            faces, soft = features.flat_faces(sk, regs)
        elif t == "extrude":
            faces, soft = features.extrude(sk, regs, float(feat["distance"]),
                                           bool(feat.get("symmetric")))
        elif t == "revolve":
            faces, soft = features.revolve(
                sk, regs, revolve_axis(sk, feat.get("axis", "x")),
                float(feat.get("angle", 360.0)), int(doc.get("segments", 32)))
        else:
            raise FeatureError(f"Unknown feature type «{t}».")
    return mesh_from_faces(faces, soft)


def revolve_axis(sk: dict, axis) -> tuple:
    """Two workplane points on the axis: ``"x"`` / ``"y"`` = the workplane's
    own axes through its origin; a number = the id of a line of the sketch
    (make it a Construction line so it stays out of the profile)."""
    if axis == "x":
        return (0.0, 0.0), (1.0, 0.0)
    if axis == "y":
        return (0.0, 0.0), (0.0, 1.0)
    by = model.ents(sk)
    ln = by.get(axis)
    if ln is None or ln["t"] != "line":
        raise FeatureError("The axis line was deleted — edit the revolve "
                           "and choose an axis.")
    a, b = by[ln["p1"]], by[ln["p2"]]
    return (a["x"], a["y"]), (b["x"], b["y"])


def feature_sketches(feat: dict) -> list:
    """Ids of the sketches a feature is built from."""
    if feat["t"] == "loft":
        return list(feat.get("sketches", ()))
    if feat["t"] == "sweep":
        return [feat.get("profile"), feat.get("path")]
    return [feat.get("sketch")]


def signature(doc: dict, feat: dict) -> str:
    """Everything the feature's shape depends on, as one string: when it is
    the same as last time the group is left alone."""
    own = {k: v for k, v in feat.items() if k not in _VOLATILE}
    used = []
    for sid in feature_sketches(feat):
        sk = model.sketch(doc, sid)
        used.append(None if sk is None else
                    [sk["o"], sk["x"], sk["y"], sk["ents"],
                     [c["e"] for c in sk["cons"] if c["t"] == "coincident"]])
    return json.dumps([own, used, doc.get("segments", 32)], sort_keys=True)


def feature_groups(scene, doc: dict) -> dict:
    """``feature id → its group`` in the scene (a copy the user made of a
    feature's group carries the same tag: the feature keeps the original)."""
    want = {f["id"]: f.get("uid") for f in doc["features"]}
    found: dict = {}
    for g in scene.groups_by_uid().values():
        rec = (getattr(g, "ext", None) or {}).get(EXT)
        fid = rec.get("feature") if isinstance(rec, dict) else None
        if fid is None:
            continue
        if fid not in found or g.uid == want.get(fid):
            found[fid] = g
    return found


def _swap_command(scene, removed: list, replaced: list, added: list):
    from core.history import Command, group_owner_list

    class _SwapFeatures(Command):
        def do(self, sc) -> None:
            self._back = []
            sel = sc.selection if isinstance(sc.selection, set) else set()
            for old, new in replaced:
                lst = group_owner_list(sc, old)
                if lst is None:
                    lst = sc.groups
                    lst.append(new)
                    self._back.append((lst, None, new, None))
                    continue
                i = lst.index(old)
                lst[i] = new
                self._back.append((lst, i, new, old))
                if old in sel:
                    sel.discard(old)
                    sel.add(new)
            for old in removed:
                lst = group_owner_list(sc, old)
                if lst is not None:
                    i = lst.index(old)
                    del lst[i]
                    self._back.append((lst, i, None, old))
                    sel.discard(old)
            for new in added:
                sc.groups.append(new)
                self._back.append((sc.groups, None, new, None))

        def undo(self, sc) -> None:
            sel = sc.selection if isinstance(sc.selection, set) else set()
            for lst, i, new, old in reversed(self._back):
                if new is not None and old is not None:
                    lst[lst.index(new)] = old
                    if new in sel:
                        sel.discard(new)
                        sel.add(old)
                elif new is not None:
                    if new in lst:
                        lst.remove(new)
                    sel.discard(new)
                else:
                    lst.insert(min(i, len(lst)), old)

    return _SwapFeatures()


def commit(app, doc: dict) -> None:
    """Store ``doc`` in the document and rebuild the groups of the features
    whose inputs changed — ONE undo step. ``doc`` is updated in place with
    each feature's group uid, signature and error (if it could not build)."""
    from core.group import Group
    from core.history import CompoundCommand, SetPluginDataCommand

    vp = app.viewport
    scene = vp.scene
    have = feature_groups(scene, doc)
    removed, replaced, added = [], [], []
    for feat in doc["features"]:
        sig = signature(doc, feat)
        old = have.get(feat["id"])
        if old is not None:
            feat.pop("detached", None)
            feat["uid"] = old.uid
            if feat.get("sig") == sig and not feat.get("error"):
                continue
        elif feat.get("uid"):
            # It was built, and its group is no longer in the model: the user
            # deleted it or spent it in a solid operation (a pocket cut with
            # Subtract). Building it again would undo that — leave it be
            # until the feature itself is edited (which clears "uid").
            feat["detached"] = True
            continue
        feat["sig"] = sig
        try:
            mesh = build_mesh(doc, feat)
        except FeatureError as exc:
            feat["error"] = str(exc)          # the old solid stays as it was
            continue
        except Exception as exc:  # noqa: BLE001 — a host engine refusal
            feat["error"] = f"{type(exc).__name__}: {exc}"
            continue
        feat.pop("error", None)
        feat.pop("warn", None)
        if feat["t"] != "face":
            from core.solids import solid_report
            if not solid_report(mesh)[0]:
                feat["warn"] = ("Built, but not a closed solid — do profiles "
                                "touch or cross each other?")
        new = Group(mesh, name=old.name if old is not None else feat["name"])
        new.ext = dict(getattr(old, "ext", None) or {},
                       **{EXT: {"feature": feat["id"], "type": feat["t"]}})
        if old is not None:
            new.uid = old.uid
            for attr in ("layer", "material", "hidden"):
                setattr(new, attr, getattr(old, attr, None))
            replaced.append((old, new))
        else:
            added.append(new)
        feat["uid"] = new.uid
    live = {f["id"] for f in doc["features"]}
    removed = [g for fid, g in have.items() if fid not in live]

    cmds = []
    if removed or replaced or added:
        cmds.append(_swap_command(scene, removed, replaced, added))
    cmds.append(SetPluginDataCommand(app.key, doc))
    vp.history.execute(CompoundCommand(cmds))
    notify = getattr(vp, "notify_scene_changed", None)
    if callable(notify):
        notify()
    vp.update()


def set_hidden(viewport, group, hidden: bool) -> None:
    """Hide or show a group with the host's own command (one undo step)."""
    from core.history import HideCommand
    scene = viewport.scene
    if hidden and isinstance(scene.selection, set):
        scene.selection.discard(group)
    viewport.history.execute(HideCommand([group], bool(hidden)))
    notify = getattr(viewport, "notify_scene_changed", None)
    if callable(notify):
        notify()
    viewport.update()


def pick_face(viewport, x: float, y: float):
    """The face under a viewport pixel — of the loose model or of ANY group,
    a built solid included — as ``(origin, x, y)`` of a workplane lying on
    it in world space, or ``None`` when there is no face there."""
    face, placement = viewport.pick_face_placement(x, y)
    if face is None:
        return None
    return face_plane(face, getattr(placement, "xform", None))


def face_plane(face, xform=None):
    """``(origin, x, y)`` of a workplane lying on a host face (``xform``:
    the placement matrix of the component instance it was hit through)."""
    from PySide6.QtGui import QVector3D
    if xform is not None:
        pts = [xform.map(QVector3D(v)) for v in face.vertices]
        c = QVector3D(0.0, 0.0, 0.0)
        n = QVector3D(0.0, 0.0, 0.0)
        for i, p in enumerate(pts):
            q = pts[(i + 1) % len(pts)]
            c += p
            n += QVector3D((p.y() - q.y()) * (p.z() + q.z()),
                           (p.z() - q.z()) * (p.x() + q.x()),
                           (p.x() - q.x()) * (p.y() + q.y()))
        c /= len(pts)
        return _plane_from(c, n)
    return _plane_from(face.centroid(), face.normal())


def _plane_from(c, n):
    if n.length() < 1e-9:
        raise model.SketchError("That face has no area.")
    n = n.normalized()
    try:
        from core.axes import plane_axes
        u, _v = plane_axes(n)
    except Exception:  # noqa: BLE001
        from PySide6.QtGui import QVector3D
        ref = QVector3D(0, 0, 1) if abs(n.z()) < 0.9 else QVector3D(1, 0, 0)
        u = QVector3D.crossProduct(ref, n).normalized()
    from PySide6.QtGui import QVector3D
    v = QVector3D.crossProduct(n, u)
    return ((c.x(), c.y(), c.z()), (u.x(), u.y(), u.z()), (v.x(), v.y(), v.z()))


def unit() -> tuple[str, float]:
    """``(name, per metre)`` of the document's length unit."""
    try:
        from core import units
        name = units.model_unit()
    except Exception:  # noqa: BLE001
        name = "m"
    return {"mm": ("mm", 1000.0), "cm": ("cm", 100.0)}.get(name, ("m", 1.0))
