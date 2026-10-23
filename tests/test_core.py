"""Solver, converter, features and scene sync — against IngeTrazo's REAL engine.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_core.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))      # …/plugins
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ingetrazo_env  # noqa: E402

ingetrazo_env.install()
from PySide6.QtWidgets import QApplication  # noqa: E402

_qt = QApplication.instance() or QApplication([])

from Cad_sketcher import convert, features, host, model, solver  # noqa: E402

PASSED = []


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"{name}: {detail}")
    PASSED.append(name)
    print(f"  ok  {name} {detail}")


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---- helpers ---------------------------------------------------------------------

def rect(doc, sk, x0, y0, x1, y1, constrain=True):
    """Four points, four lines, H/V constraints — as the Rectangle tool."""
    p = [model.add_point(doc, sk, x, y)["id"]
         for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    ln = [model.add_line(doc, sk, p[i], p[(i + 1) % 4])["id"] for i in range(4)]
    if constrain:
        for i, t in enumerate(("horizontal", "vertical", "horizontal", "vertical")):
            model.add_constraint(doc, sk, t, [ln[i]])
    return p, ln


def new_sketch(doc, name="S", plane="XY", offset=0.0):
    x, y = model.PLANES[plane]
    n = model._cross(x, y)
    return model.add_sketch(doc, name, [c * offset for c in n], x, y)


def origin(sk):
    return sk["ents"][0]["id"]


class FakeViewport:
    def __init__(self):
        from core.history import History
        from core.scene import Scene
        self.scene = Scene()
        self.history = History(self.scene)

    def update(self):
        pass

    def notify_scene_changed(self):
        pass


class FakeApp:
    key = "Cad_sketcher"

    def __init__(self):
        self.viewport = FakeViewport()

    @property
    def scene(self):
        return self.viewport.scene

    def document_data(self, default=None):
        import copy
        return copy.deepcopy(self.scene.plugin_data.get(self.key, default))


# ---- solver ----------------------------------------------------------------------

def test_solver():
    print("solver")
    doc = model.new_doc()
    sk = new_sketch(doc)
    p, ln = rect(doc, sk, 0.1, 0.2, 2.3, 1.4)
    res = solver.solve(sk)
    check("rect H/V solves", res.ok and res.dof == 4, f"dof={res.dof}")
    model.add_constraint(doc, sk, "coincident", [p[0], origin(sk)])
    model.add_constraint(doc, sk, "distance", [ln[0]], 3.0)
    model.add_constraint(doc, sk, "distance", [ln[1]], 1.5)
    res = solver.solve(sk)
    by = model.ents(sk)
    check("rect fully constrained", res.ok and res.dof == 0, res.text)
    check("rect exact size",
          near(by[p[2]]["x"], 3.0) and near(by[p[2]]["y"], 1.5)
          and near(by[p[0]]["x"], 0.0) and near(by[p[0]]["y"], 0.0),
          f"corner=({by[p[2]]['x']:.9f}, {by[p[2]]['y']:.9f})")

    # a dimension that contradicts another: reported, sketch untouched
    bad = model.add_constraint(doc, sk, "distance", [p[0], p[1]], 5.0)
    res = solver.solve(sk)
    check("conflict detected", (not res.ok) and bad["id"] in res.failed,
          f"failed={res.failed}")
    check("conflict leaves sketch alone", near(model.ents(sk)[p[2]]["x"], 3.0))
    sk["cons"].remove(bad)

    # editing a dimension moves the sketch
    next(c for c in sk["cons"] if c.get("v") == 3.0)["v"] = 4.25
    res = solver.solve(sk)
    check("edit dimension", res.ok and near(model.ents(sk)[p[1]]["x"], 4.25))

    # arc tangent to two lines (a filleted corner), radius dimension
    doc = model.new_doc()
    sk = new_sketch(doc)
    o = origin(sk)
    a = model.add_point(doc, sk, 2.0, 0.02)["id"]
    b = model.add_point(doc, sk, 2.52, 0.48)["id"]
    c = model.add_point(doc, sk, 2.0, 0.5)["id"]
    top = model.add_point(doc, sk, 2.5, 2.0)["id"]
    l1 = model.add_line(doc, sk, o, a)["id"]
    arc = model.add_arc(doc, sk, c, a, b)["id"]
    l2 = model.add_line(doc, sk, b, top)["id"]
    model.add_constraint(doc, sk, "horizontal", [l1])
    model.add_constraint(doc, sk, "vertical", [l2])
    model.add_constraint(doc, sk, "tangent", [l1, arc])
    model.add_constraint(doc, sk, "tangent", [l2, arc])
    model.add_constraint(doc, sk, "radius", [arc], 0.4)
    model.add_constraint(doc, sk, "distance", [o, top], 2.5, axis="h")
    model.add_constraint(doc, sk, "distance", [o, top], 1.8, axis="v")
    res = solver.solve(sk)
    by = model.ents(sk)
    check("fillet solves", res.ok and res.dof == 0, res.text)
    check("fillet geometry",
          near(by[a]["x"], 2.1) and near(by[a]["y"], 0.0)
          and near(by[b]["x"], 2.5) and near(by[b]["y"], 0.4)
          and near(by[c]["x"], 2.1) and near(by[c]["y"], 0.4),
          f"a=({by[a]['x']:.6f},{by[a]['y']:.6f}) b=({by[b]['x']:.6f},{by[b]['y']:.6f})")

    # the rest of the constraint family
    doc = model.new_doc()
    sk = new_sketch(doc)
    o = origin(sk)
    q = [model.add_point(doc, sk, x, y)["id"]
         for x, y in ((1, 0.1), (0.2, 1.1), (1.3, 1.2), (0.6, 0.4), (-0.7, 0.5),
                      (0.9, 0.8))]
    la = model.add_line(doc, sk, o, q[0])["id"]
    lb = model.add_line(doc, sk, o, q[1])["id"]
    lc = model.add_line(doc, sk, q[1], q[2])["id"]
    circ = model.add_circle(doc, sk, q[5], 0.3)["id"]
    model.add_constraint(doc, sk, "horizontal", [la])
    model.add_constraint(doc, sk, "distance", [la], 2.0)
    model.add_constraint(doc, sk, "angle", [la, lb], 60.0)
    model.add_constraint(doc, sk, "equal", [la, lb])
    model.add_constraint(doc, sk, "parallel", [la, lc])
    model.add_constraint(doc, sk, "ratio", [lc, la], 0.5)
    model.add_constraint(doc, sk, "midpoint", [q[3], la])
    model.add_constraint(doc, sk, "symmetric", [q[3], q[4], lb])
    model.add_constraint(doc, sk, "coincident", [q[2], circ])
    model.add_constraint(doc, sk, "diameter", [circ], 1.0)
    model.add_constraint(doc, sk, "distance", [q[5], la], 1.4)
    res = solver.solve(sk)
    by = model.ents(sk)
    X = lambda i: (by[i]["x"], by[i]["y"])  # noqa: E731
    check("family solves", res.ok, res.text)
    check("angle+equal", near(X(q[1])[0], 1.0) and near(X(q[1])[1], math.sqrt(3)),
          str(X(q[1])))
    check("parallel+ratio", near(X(q[2])[1], math.sqrt(3))
          and near(abs(X(q[2])[0] - 1.0), 1.0), str(X(q[2])))
    check("midpoint", near(X(q[3])[0], 1.0) and near(X(q[3])[1], 0.0))
    check("symmetric", near(math.hypot(*X(q[4])), 1.0)
          and near(X(q[4])[0], -0.5, 1e-5), str(X(q[4])))
    check("point on circle + diameter + point-line distance",
          near(by[circ]["r"], 0.5) and near(X(q[5])[1], 1.4)
          and near(math.hypot(X(q[2])[0] - X(q[5])[0],
                              X(q[2])[1] - X(q[5])[1]), 0.5))

    # dragging: the pulled point goes where asked, constraints hold
    doc = model.new_doc()
    sk = new_sketch(doc)
    p, ln = rect(doc, sk, 0, 0, 2, 1)
    model.add_constraint(doc, sk, "coincident", [p[0], origin(sk)])
    res = solver.solve(sk, drag={p[2]: (3.5, 2.5)})
    by = model.ents(sk)
    check("drag corner", res.ok and near(by[p[2]]["x"], 3.5, 1e-3)
          and near(by[p[2]]["y"], 2.5, 1e-3) and near(by[p[1]]["x"], by[p[2]]["x"])
          and near(by[p[1]]["y"], 0.0), f"({by[p[2]]['x']:.4f},{by[p[2]]['y']:.4f})")
    try:
        model.add_constraint(doc, sk, "parallel", [p[0], p[1]])
        check("bad selection refused", False)
    except model.SketchError as exc:
        check("bad selection refused", "two lines" in str(exc))


# ---- converter -------------------------------------------------------------------

def test_convert():
    print("convert")
    doc = model.new_doc()
    sk = new_sketch(doc)
    rect(doc, sk, 0, 0, 4, 2)
    c = model.add_point(doc, sk, 1, 1)["id"]
    model.add_circle(doc, sk, c, 0.5)
    con = model.add_point(doc, sk, 3, 1)["id"]
    model.add_circle(doc, sk, con, 0.5, con=True)          # construction: ignored
    a = model.add_point(doc, sk, 5, 0)["id"]
    b = model.add_point(doc, sk, 6, 1)["id"]
    model.add_line(doc, sk, a, b)                           # an open path
    loops, paths = convert.walk(sk, 32)
    regs = convert.regions(loops)
    check("loops and paths", len(loops) == 2 and len(paths) == 1,
          f"{len(loops)} loops, {len(paths)} paths")
    check("hole found", len(regs) == 1 and len(regs[0]["holes"]) == 1)
    check("winding", convert._area2(regs[0]["outer"]["pts"]) > 0
          and convert._area2(regs[0]["holes"][0]["pts"]) < 0)
    check("smooth flags", all(regs[0]["holes"][0]["smooth"])
          and not any(regs[0]["outer"]["smooth"]))

    # rounded slot: two lines + two arcs
    doc = model.new_doc()
    sk = new_sketch(doc)
    p = [model.add_point(doc, sk, x, y)["id"]
         for x, y in ((0, -0.5), (2, -0.5), (2, 0.5), (0, 0.5), (2, 0), (0, 0.0))]
    model.add_line(doc, sk, p[0], p[1])
    model.add_arc(doc, sk, p[4], p[1], p[2])
    model.add_line(doc, sk, p[2], p[3])
    model.add_arc(doc, sk, p[5], p[3], p[0])
    loops, paths = convert.walk(sk, 64)
    area = convert._area2(loops[0]["pts"]) / 2 if loops else 0
    check("slot closes", len(loops) == 1 and not paths)
    check("slot area", abs(area - (2.0 + math.pi * 0.25)) < 2e-3, f"{area:.5f}")
    return sk


# ---- features --------------------------------------------------------------------

def test_features():
    print("features")
    from core.solids import solid_report
    doc = model.new_doc()
    sk = new_sketch(doc)
    rect(doc, sk, 0, 0, 2, 1)
    faces, soft = features.extrude(sk, convert.profile(sk), 3.0)
    ok, vol = solid_report(host.mesh_from_faces(faces, soft))
    check("box is a solid", ok and near(vol, 6.0), f"V={vol}")
    check("box faces look outward", near(features.volume(faces), 6.0))

    c = model.add_point(doc, sk, 1, 0.5)["id"]
    model.add_circle(doc, sk, c, 0.25)
    faces, soft = features.extrude(sk, convert.profile(sk, 64), -3.0, symmetric=True)
    mesh = host.mesh_from_faces(faces, soft)
    ok, vol = solid_report(mesh)
    want = (2.0 - math.pi * 0.0625) * 3.0
    check("box with hole is a solid", ok and abs(vol - want) < 5e-3,
          f"V={vol:.5f} want≈{want:.5f}")
    check("round wall is smooth", sum(1 for e in mesh.edges if e.soft) == 64)
    zs = [v.position.z() for f in mesh.faces for v in f.loop]
    check("symmetric", near(min(zs), -1.5) and near(max(zs), 1.5))

    # loft: square 2×2 at z=0 → circle r=0.5 at z=2 → square 1×1 at z=3
    doc = model.new_doc()
    s0 = new_sketch(doc, "base")
    rect(doc, s0, -1, -1, 1, 1)
    s1 = new_sketch(doc, "mid", offset=2.0)
    model.add_circle(doc, s1, origin(s1), 0.5)
    s2 = new_sketch(doc, "top", offset=3.0)
    rect(doc, s2, -0.5, -0.5, 0.5, 0.5)
    secs = [(s, convert.profile(s, 32)[0]) for s in (s0, s1)]
    faces, soft = features.loft(secs)
    ok, vol = solid_report(host.mesh_from_faces(faces, soft))
    check("loft square→circle is a solid", ok and 3.0 < vol < 5.0, f"V={vol:.4f}")
    base = next(f for f in faces if all(near(p[2], 0.0) for p in f["loop"]))
    xs = sorted({round(abs(p[0]), 6) for p in base["loop"]} | {1.0})
    check("loft keeps the square exact", xs[-1] == 1.0
          and all(max(abs(p[0]), abs(p[1])) > 1 - 1e-9 for p in base["loop"]))
    secs.append((s2, convert.profile(s2, 32)[0]))
    faces, soft = features.loft(secs)
    ok, vol3 = solid_report(host.mesh_from_faces(faces, soft))
    check("loft through 3 sections is a solid", ok and vol3 > vol, f"V={vol3:.4f}")

    # frustum: exact volume  h/3 (A1 + A2 + sqrt(A1 A2))
    faces, soft = features.loft([(s0, convert.profile(s0)[0]),
                                 (s2, convert.profile(s2)[0])])
    ok, vol = solid_report(host.mesh_from_faces(faces, soft))
    check("frustum volume exact", ok and near(vol, 3.0 / 3 * (4 + 1 + 2), 1e-6),
          f"V={vol:.6f}")


# ---- scene sync ------------------------------------------------------------------

def test_scene():
    print("scene sync")
    from core.solids import solid_report
    app = FakeApp()
    scene = app.scene
    doc = model.new_doc()
    sk = new_sketch(doc, "Profile")
    p, ln = rect(doc, sk, 0, 0, 2, 1)
    model.add_constraint(doc, sk, "coincident", [p[0], origin(sk)])
    width = model.add_constraint(doc, sk, "distance", [ln[0]], 2.0)
    model.add_constraint(doc, sk, "distance", [ln[1]], 1.0)
    solver.solve(sk)
    feat = {"id": model.new_id(doc), "t": "extrude", "name": "Extrude",
            "sketch": sk["id"], "distance": 3.0, "symmetric": False}
    doc["features"].append(feat)
    host.commit(app, doc)
    check("group created", len(scene.groups) == 1 and not feat.get("error"),
          str(feat.get("error")))
    g1 = scene.groups[0]
    check("group is a solid", near(solid_report(g1.mesh)[1], 6.0))
    check("document stored",
          app.document_data()["features"][0]["uid"] == g1.uid)

    # nothing changed → the group is left alone
    doc = model.normalize(app.document_data())
    host.commit(app, doc)
    check("unchanged feature untouched", scene.groups[0] is g1)

    # THE point of it all: edit the sketch, the solid follows
    doc = model.normalize(app.document_data())
    sk = doc["sketches"][0]
    model.find(sk["cons"], width["id"])["v"] = 5.0
    res = solver.solve(sk)
    host.commit(app, doc)
    g2 = scene.groups[0]
    check("edit sketch → solid follows",
          res.ok and len(scene.groups) == 1 and g2 is not g1
          and near(solid_report(g2.mesh)[1], 15.0),
          f"V={solid_report(g2.mesh)[1]}")
    check("group identity kept", g2.uid == g1.uid and g2.name == g1.name)

    # one undo takes back sketch AND solid
    app.viewport.history.undo()
    back = model.normalize(app.document_data())
    check("undo restores both", scene.groups[0] is g1
          and model.find(back["sketches"][0]["cons"], width["id"])["v"] == 2.0)
    app.viewport.history.redo()
    check("redo", scene.groups[0] is g2)

    # a profile opened by mistake: the solid stays, the feature says why
    doc = model.normalize(app.document_data())
    sk = doc["sketches"][0]
    model.delete_entities(sk, [next(e["id"] for e in sk["ents"]
                                    if e["t"] == "line")])
    host.commit(app, doc)
    check("broken profile keeps old solid", scene.groups[0] is g2
          and "closed profile" in doc["features"][0].get("error", ""),
          doc["features"][0].get("error", ""))
    app.viewport.history.undo()

    # sweep along an L path, with the host's Follow Me
    doc = model.normalize(app.document_data())
    prof = new_sketch(doc, "Section", "XZ")              # in the XZ plane, at y=0
    rect(doc, prof, -0.1, -0.1, 0.1, 0.1)
    path = new_sketch(doc, "Path", "XY")
    o = origin(path)
    a = model.add_point(doc, path, 0, 2)["id"]
    b = model.add_point(doc, path, 3, 2)["id"]
    model.add_line(doc, path, o, a)
    model.add_line(doc, path, a, b)
    sweep = {"id": model.new_id(doc), "t": "sweep", "name": "Sweep",
             "profile": prof["id"], "path": path["id"]}
    doc["features"].append(sweep)
    host.commit(app, doc)
    g = next((x for x in scene.groups
              if x.ext.get(host.EXT, {}).get("feature") == sweep["id"]), None)
    ok, vol = solid_report(g.mesh) if g else (False, None)
    check("sweep is a solid", g is not None and ok and near(vol, 0.04 * 5.0, 1e-6),
          f"err={sweep.get('error')} V={vol}")

    # loft feature, then delete a feature → its group goes
    doc = model.normalize(app.document_data())
    top = new_sketch(doc, "Top", offset=4.0)
    model.add_circle(doc, top, origin(top), 0.4)
    loft = {"id": model.new_id(doc), "t": "loft", "name": "Loft",
            "sketches": [doc["sketches"][0]["id"], top["id"]]}
    doc["features"].append(loft)
    host.commit(app, doc)
    check("loft feature", len(scene.groups) == 3 and not loft.get("error"),
          str(loft.get("error")))
    doc = model.normalize(app.document_data())
    doc["features"] = [f for f in doc["features"] if f["t"] != "sweep"]
    host.commit(app, doc)
    check("deleted feature removes its group", len(scene.groups) == 2)
    app.viewport.history.undo()
    check("undo brings it back", len(scene.groups) == 3)


if __name__ == "__main__":
    test_solver()
    test_convert()
    test_features()
    test_scene()
    print(f"\nALL {len(PASSED)} CHECKS PASSED")
