"""The tool, the overlay and the panel, driven like a user would — headless.

A stand-in viewport (top view, 100 px per metre) carries IngeTrazo's REAL
scene and undo history; ``setup(app)`` is the plugin's real entry point.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_ui.py
"""
import copy
import math
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["CAD_SKETCHER_NO_SETTINGS"] = "1"     # never touch the user's own

import ingetrazo_env  # noqa: E402

ingetrazo_env.install()
import numpy as np  # noqa: E402
from PySide6.QtCore import QPointF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QVector3D  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

_qt = QApplication.instance() or QApplication([])

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from core.solids import solid_report  # noqa: E402

PASSED = []
SCALE, CX, CY = 100.0, 400.0, 300.0


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"{name}: {detail}")
    PASSED.append(name)
    print(f"  ok  {name} {detail}")


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


class FakeViewport(QWidget):
    """Top view: world (x, y) → pixel (CX + 100 x, CY − 100 y)."""
    sceneVersionChanged = Signal()

    def __init__(self):
        super().__init__()
        from core.history import History
        from core.scene import Scene
        self.scene = Scene()
        self.history = History(self.scene)
        self.active_tool = None
        self._value_buffer = ""
        self.flashes = []
        self.camera = SimpleNamespace(set_view=lambda name: self.flashes.append(
            f"view:{name}"))
        self.resize(800, 600)

    def world_to_pixels(self, pts):
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        return CX + SCALE * pts[:, 0], CY - SCALE * pts[:, 1], \
            np.ones(len(pts), dtype=bool)

    def _pixel_to_ray(self, x, y):
        return QVector3D((x - CX) / SCALE, (CY - y) / SCALE, 50.0), \
            QVector3D(0, 0, -1)

    def set_active_tool(self, tool):
        if self.active_tool is not None and self.active_tool is not tool:
            self.active_tool.on_deactivate(self)
        self.active_tool = tool
        tool.on_activate(self)

    def set_hover(self, entity):
        pass

    def notify_scene_changed(self):
        self.sceneVersionChanged.emit()

    def flash_status(self, text, ms=0):
        self.flashes.append(text)

    def undo(self):
        self.history.undo()
        self.notify_scene_changed()


class FakeApp:
    """The part of ``views.extension_api.ExtensionApp`` the plugin uses."""
    key = "Cad_sketcher"
    api_version = 2

    def __init__(self):
        self.viewport = FakeViewport()
        self.overlays, self.panels, self.menu = [], [], QMenu()

    @property
    def scene(self):
        return self.viewport.scene

    def document_data(self, default=None):
        return copy.deepcopy(self.scene.plugin_data.get(self.key, default))

    def add_panel(self, title, widget, name=None, **_kw):
        self.panels.append(widget)
        return widget

    def show_panel(self, dock):
        pass

    def add_menu(self, title):
        return self.menu

    def add_overlay(self, fn):
        self.overlays.append(fn)

    def on_document_changed(self, fn):
        self.viewport.sceneVersionChanged.connect(fn)


def px(u, v):
    return QPointF(CX + SCALE * u, CY - SCALE * v)


def ctx(vp, u, v):
    return SimpleNamespace(viewport=vp, screen=px(u, v), world=QVector3D(u, v, 0),
                           modifiers=Qt.NoModifier, snap=None)


def paint(app):
    img = QImage(800, 600, QImage.Format_ARGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    for fn in app.overlays:
        p.save()
        fn(app.viewport, p)
        p.restore()
    p.end()
    return img


def main():
    app = FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    check("setup", Cad_sketcher._instance is not None and len(app.panels) == 1
          and len(app.overlays) == 1 and len(app.menu.actions()) >= 5)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    overlay = app.overlays[0]
    ctrl.ask_value = lambda *a, **k: None          # never open a dialog here

    def click(u, v):
        tool.on_hover(ctx(vp, u, v))
        tool.on_click(ctx(vp, u, v))

    def ents(t=None):
        return [e for e in ctrl.sketch()["ents"] if t is None or e["t"] == t]

    def pt(i):
        e = model.ents(ctrl.sketch())[i]
        return e["x"], e["y"]

    # ---- a sketch, a rectangle from the origin --------------------------------
    print("draw")
    sk = ctrl.new_sketch("XY")
    check("new sketch is active, tool handed over",
          ctrl.active == sk["id"] and vp.active_tool is tool
          and ents("point")[0]["fixed"])
    ctrl.set_mode("rect")
    click(0.02, 0.03)                               # lands on the origin point
    tool.on_hover(ctx(vp, 1.5, 0.7))
    check("rectangle preview", len(tool.preview_uv()[0]) == 5)
    click(2.0, 1.0)
    sk = ctrl.sketch()
    origin = ents("point")[0]["id"]
    lines = ents("line")
    check("rectangle", len(ents("point")) == 4 and len(lines) == 4
          and len(sk["cons"]) == 4 and lines[0]["p1"] == origin,
          f"{len(ents('point'))} pts {len(lines)} lines {len(sk['cons'])} cons")
    check("solved, 2 dof left", ctrl.result.ok and ctrl.result.dof == 2,
          ctrl.result.text)
    check("document stored", len(app.document_data()["sketches"][0]["ents"]) == 8)

    # ---- dimensions through the selection -------------------------------------
    print("constrain")
    ctrl.set_mode("select")
    tool.on_hover(ctx(vp, 1.0, 0.0))
    check("hover a line", ctrl.hover == ("ent", lines[0]["id"]))
    click(1.0, 0.0)
    check("select a line", ctrl.sel == [lines[0]["id"]])
    ctrl.ask_value = lambda *a, **k: 3.0
    width = ctrl.constrain("distance")
    check("width dimension", width is not None and near(pt(lines[0]["p2"])[0], 3.0)
          and ctrl.sel == [])
    click(3.0, 0.5)
    height = ctrl.constrain("distance", value=1.25)
    check("fully constrained", ctrl.result.ok and ctrl.result.dof == 0
          and near(pt(lines[1]["p2"])[1], 1.25), ctrl.result.text)
    ctrl.sel = [lines[0]["id"]]
    check("wrong selection is refused, with words",
          ctrl.constrain("parallel") is None and "two lines" in ctrl.message,
          ctrl.message)
    ctrl.sel = []

    # ---- the overlay paints, and its labels can be picked ----------------------
    print("overlay / panel")
    img = paint(app)
    check("overlay paints", not overlay._failed and img.pixelColor(
        int(CX + 150), int(CY)) != img.pixelColor(5, 5))
    check("labels laid out", set(tool.labels) == {c["id"] for c in sk["cons"]})
    r = tool.labels[width["id"]].center()
    hit = tool.pick(ctrl.sketch(), r.x(), r.y())
    check("pick a dimension label", hit == ("con", width["id"]), str(hit))
    panel.refresh()
    check("panel lists constraints", panel.cons.count() == 6
          and "Fully constrained" in panel.status.text()
          and panel.edit_btn.text() == "Finish" and panel.tools_box.isEnabled())

    # ---- extrude, then edit the sketch: the solid follows ----------------------
    print("solid follows sketch")
    feat = ctrl.add_feature("extrude", sketch=sk["id"], distance=2.0,
                            symmetric=False)
    check("extrude", len(vp.scene.groups) == 1 and not feat.get("error")
          and near(solid_report(vp.scene.groups[0].mesh)[1], 3.0 * 1.25 * 2.0),
          str(feat.get("error")))
    ctrl.set_value(width["id"], 4.0)
    check("change a dimension → solid rebuilt",
          near(solid_report(vp.scene.groups[0].mesh)[1], 4.0 * 1.25 * 2.0),
          f"V={solid_report(vp.scene.groups[0].mesh)[1]}")
    vp.undo()
    check("undo: sketch and solid together",
          near(solid_report(vp.scene.groups[0].mesh)[1], 7.5)
          and near(pt(lines[0]["p2"])[0], 3.0) and ctrl.active == sk["id"])
    ctrl.update_feature(feat["id"], distance=0.5)
    check("edit the feature", near(solid_report(vp.scene.groups[0].mesh)[1],
                                   3.0 * 1.25 * 0.5))
    check("panel lists the solid", panel.features.count() == 1
          and "Extrude" in panel.features.item(0).text())
    paint(app)

    # ---- move: click, move, click ----------------------------------------------
    print("move")
    ctrl.new_sketch("XY", offset=0.0, name="Free")
    ctrl.set_mode("line")
    click(1.0, 1.0)
    tool.on_hover(ctx(vp, 2.0, 1.02))               # nearly level → horizontal
    check("level inference", tool.cursor["hv"] == "h")
    click(2.0, 1.02)
    click(2.0, 2.0)                                 # plumb
    cons = [c["t"] for c in ctrl.sketch()["cons"]]
    check("chain with automatic H / V", len(ents("line")) == 2
          and cons == ["horizontal", "vertical"], str(cons))
    tool.on_hover(ctx(vp, 1.0, 2.5))
    check("typed length", tool.on_value(vp, 0.75) is True
          and len(ents("line")) == 3
          and near(math.dist(pt(ents("line")[2]["p1"]), pt(ents("line")[2]["p2"])),
                   0.75) and ctrl.sketch()["cons"][-1]["t"] == "distance")
    first = ents("line")[0]["p1"]
    click(1.0, 1.0)                                 # back on the first point
    check("closing click ends the chain", len(ents("line")) == 4
          and ents("line")[3]["p2"] == first and tool.picks == [])
    n = len(ents())
    click(3.0, 3.0)
    tool.on_cancel(vp)
    check("Esc drops the unfinished start point", len(ents()) == n
          and tool.picks == [])

    ctrl.set_mode("move")
    corner = ents("line")[0]["p2"]
    before = pt(corner)
    click(*before)
    check("grab", tool.grab is not None and tool.grab["id"] == corner)
    tool.on_hover(ctx(vp, 2.6, 0.6))
    moved = pt(corner)
    check("drag follows, constraints hold",
          near(moved[0], 2.6, 1e-3) and near(moved[1], 0.6, 1e-3)
          and near(pt(ents("line")[0]["p1"])[1], moved[1])
          and near(pt(ents("line")[1]["p2"])[0], moved[0]), str(moved))
    tool.on_cancel(vp)
    check("Esc puts it back", pt(corner) == before and tool.grab is None)
    click(*before)
    tool.on_hover(ctx(vp, 2.6, 0.6))
    tool.on_click(ctx(vp, 2.6, 0.6))
    stored = model.ents(app.document_data()["sketches"][1])[corner]
    check("drop commits", tool.grab is None and near(stored["x"], 2.6, 1e-3))

    # ---- circle, arc, point on curve, delete, flags ----------------------------
    print("circle / arc / delete")
    ctrl.new_sketch("XY", name="Round")
    ctrl.set_mode("circle")
    click(0.0, 0.0)
    tool.on_hover(ctx(vp, 0.4, 0.0))
    check("typed radius", tool.on_value(vp, 0.5) and len(ents("circle")) == 1
          and near(ents("circle")[0]["r"], 0.5)
          and ctrl.sketch()["cons"][-1]["t"] == "radius")
    ctrl.set_mode("point")
    click(0.0, 0.52)                                # on the circle
    last = ctrl.sketch()["cons"][-1]
    check("point dropped on a curve stays on it", last["t"] == "coincident"
          and near(math.hypot(*pt(last["e"][0])), 0.5), str(pt(last["e"][0])))
    ctrl.set_mode("arc")
    click(2.0, 0.0)
    click(3.0, 0.0)
    tool.on_hover(ctx(vp, 2.0, 1.4))
    check("arc preview", len(tool.preview_uv()) == 3)
    click(2.0, 1.4)
    arc = ents("arc")[0]
    check("arc end lands on the radius", near(pt(arc["p2"])[1], 1.0)
          and near(pt(arc["p2"])[0], 2.0))
    paint(app)
    check("overlay still alive", not overlay._failed)

    ctrl.set_mode("select")
    click(3.0, 0.0)                                 # the arc's start point
    check("select point", ctrl.sel == [arc["p1"]])
    ctrl.toggle_flag("fixed")
    check("fix point", model.ents(ctrl.sketch())[arc["p1"]]["fixed"])
    n_ents = len(ents())
    check("Delete key", tool.on_key(vp, Qt.Key_Delete, Qt.NoModifier)
          and len(ents("arc")) == 0 and len(ents()) < n_ents)
    check("keys switch tools", tool.on_key(vp, Qt.Key_L, Qt.NoModifier)
          and ctrl.mode == "line"
          and not tool.on_key(vp, Qt.Key_F7, Qt.NoModifier))
    vp._value_buffer = "12"
    check("typing a number is left to the host",
          not tool.on_key(vp, Qt.Key_L, Qt.NoModifier))
    vp._value_buffer = ""

    # ---- loft and sweep through the panel's own path ---------------------------
    print("loft / sweep")
    top = ctrl.new_sketch("XY", offset=3.0, name="Top")
    ctrl.set_mode("circle")
    click(1.5, 0.6)
    click(1.9, 0.6)
    loft = ctrl.add_feature("loft", sketches=[ctrl.doc["sketches"][0]["id"],
                                              top["id"]])
    g = host.feature_groups(vp.scene, ctrl.doc).get(loft["id"])
    check("loft", g is not None and solid_report(g.mesh)[0], str(loft.get("error")))

    sec = ctrl.new_sketch("XZ", name="Section")
    check("XZ sketch offers the front view",
          (ctrl.align_view() or True) and "view:front" in vp.flashes)
    # a top view is edge-on to XZ: the tool must refuse politely
    ctrl.set_mode("point")
    click(1.0, 1.0)
    check("edge-on view refused", "edge-on" in ctrl.message)
    doc, s = ctrl.doc, ctrl.sketch()
    ids = [model.add_point(doc, s, u, v)["id"]
           for u, v in ((-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1))]
    for i in range(4):
        model.add_line(doc, s, ids[i], ids[(i + 1) % 4])
    ctrl.commit()
    path = ctrl.new_sketch("XY", name="Path", offset=0.0)
    ctrl.set_mode("line")
    click(0.0, -5.0)
    click(0.0, -8.0)
    click(2.0, -8.0)
    tool.on_cancel(vp)
    # the path must start at the profile: tie its first point to the origin
    s = ctrl.sketch()
    ctrl.sel = [ents("point")[0]["id"], ents("line")[0]["p1"]]
    ctrl.constrain("coincident")
    sweep = ctrl.add_feature("sweep", profile=sec["id"], path=path["id"])
    g = host.feature_groups(vp.scene, ctrl.doc).get(sweep["id"])
    ok, vol = solid_report(g.mesh) if g else (False, None)
    check("sweep", ok and near(vol, 0.04 * 10.0, 1e-5),
          f"{sweep.get('error')} V={vol}")

    # ---- leaving, deleting, a new document -------------------------------------
    print("lifecycle")
    ctrl.finish()
    panel.refresh()
    paint(app)
    check("finish", ctrl.active is None and not panel.tools_box.isEnabled()
          and panel.edit_btn.text() == "Edit" and not overlay._failed)
    n = len(vp.scene.groups)
    ctrl.delete_feature(sweep["id"])
    check("delete a solid", len(vp.scene.groups) == n - 1)
    ctrl.delete_sketch(top["id"])
    check("solid survives its sketch, and says so",
          len(vp.scene.groups) == n - 1
          and "deleted" in model.find(ctrl.doc["features"], loft["id"])["error"])
    vp.scene.plugin_data.clear()                    # File ▸ New
    vp.notify_scene_changed()
    check("new document", ctrl.doc["sketches"] == [] and panel.sketches.count() == 0)

    print(f"\nALL {len(PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
