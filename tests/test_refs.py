"""References between sketches: lining up loft sections on different levels.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_refs.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

check, near = U.check, U.near


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    ctrl.ask_value = lambda *a, **k: None

    def click(u, v):
        tool.on_hover(U.ctx(vp, u, v))
        tool.on_click(U.ctx(vp, u, v))

    def ents(t=None):
        return [e for e in ctrl.sketch()["ents"] if t is None or e["t"] == t]

    def pt(i, sk=None):
        e = model.ents(sk or ctrl.sketch())[i]
        return e["x"], e["y"]

    # bottom: a 2 × 1 rectangle away from the origin; top (3 m up): a circle
    base = ctrl.new_sketch("XY", name="Base")
    ctrl.set_mode("rect")
    click(1, 1)
    click(3, 2)
    bottom_side = ents("line")[0]
    top = ctrl.new_sketch("XY", offset=3.0, name="Top")
    ctrl.set_mode("circle")
    click(0.5, 0.5)
    click(1.05, 0.5)
    circle = ents("circle")[0]
    ctrl.sel = [circle["id"]]
    ctrl.constrain("diameter", value=1.1)

    # ---- case 1: the circle's centre over the rectangle's centre -----------------
    print("case 1: centre over centre")
    check("E key", tool.on_key(vp, Qt.Key_E, Qt.NoModifier) and ctrl.mode == "ref")
    tool.on_hover(U.ctx(vp, 2.0, 1.5))
    check("the other sketch's centre is offered",
          tool.ref_hit is not None and tool.ref_hit["kind"] == "bbox"
          and tool.ref_hit["sk"] == base["id"], str(tool.ref_hit))
    img = U.paint(app)
    check("overlay marks the spots", not app.overlays[0]._failed
          and img.pixelColor(int(U.CX + 200), int(U.CY - 150))
          != img.pixelColor(3, 3))
    click(2.0, 1.5)
    ref = ents("point")[-1]
    check("reference point made: fixed, construction, in place",
          ref.get("ref", {}).get("kind") == "bbox" and ref["fixed"] and ref["con"]
          and pt(ref["id"]) == (2.0, 1.5) and ctrl.sel == [ref["id"]])
    n = len(ents())
    click(2.0, 1.5)
    check("the same spot twice gives the same point", len(ents()) == n)
    ctrl.sel = [ref["id"], circle["c"]]
    ctrl.constrain("coincident")
    check("two picks and one constraint: centred",
          ctrl.result.ok and ctrl.result.dof == 0
          and near(pt(circle["c"])[0], 2.0) and near(pt(circle["c"])[1], 1.5),
          f"{pt(circle['c'])} {ctrl.result.text}")
    loft = ctrl.add_feature("loft", sketches=[base["id"], top["id"]])
    g = host.feature_groups(vp.scene, ctrl.doc)[loft["id"]]
    ok, vol = solid_report(g.mesh)
    check("loft", ok and vol > 0 and not loft.get("error"), str(loft.get("error")))

    # the link is alive: move the base, the top follows, the loft is rebuilt
    ctrl.edit(base["id"])
    ctrl.sel = [bottom_side["id"]]
    ctrl.constrain("distance", value=4.0)            # the rectangle grows
    xs = sorted({round(pt(e[k])[0], 9) for e in ents("line") for k in ("p1", "p2")})
    cx = (xs[0] + xs[-1]) / 2
    top_now = model.sketch(ctrl.doc, top["id"])
    check("edit the base → the top sketch follows",
          near(xs[-1] - xs[0], 4.0) and near(pt(ref["id"], top_now)[0], cx)
          and near(pt(circle["c"], top_now)[0], cx),
          f"base centre x={cx} circle x={pt(circle['c'], top_now)[0]}")
    g2 = host.feature_groups(vp.scene, ctrl.doc)[loft["id"]]
    check("…and the loft with it", g2 is not g and solid_report(g2.mesh)[0])
    vp.undo()
    top_now = model.sketch(ctrl.doc, top["id"])
    check("one undo takes all of it back", near(pt(circle["c"], top_now)[0], 2.0)
          and host.feature_groups(vp.scene, ctrl.doc)[loft["id"]] is g)

    # ---- case 2: a point of the circle over the middle of a side ----------------
    print("case 2: quadrant over a side's midpoint")
    ctrl.edit(top["id"])
    ctrl.set_mode("ref")
    tool.on_hover(U.ctx(vp, 2.0, 1.0))
    check("a side's midpoint is offered", tool.ref_hit["kind"] == "mid"
          and tool.ref_hit["e"] == bottom_side["id"], str(tool.ref_hit))
    click(2.0, 1.0)
    mid = ents("point")[-1]
    ctrl.set_mode("point")
    click(2.0 + 0.55 * math.cos(-1.2), 1.5 + 0.55 * math.sin(-1.2))   # on the circle
    on_circle = ents("point")[-1]
    ctrl.sel = [mid["id"], on_circle["id"]]
    ctrl.constrain("vertical")
    check("the circle's point sits over the side's midpoint",
          ctrl.result.ok and near(pt(on_circle["id"])[0], 2.0)
          and near(pt(on_circle["id"])[1], 1.5 - 0.55),
          f"{pt(on_circle['id'])} {ctrl.result.text}")

    # the other way round: a circle's quadrant, used from the base sketch
    ctrl.edit(base["id"])
    ctrl.set_mode("ref")
    tool.on_hover(U.ctx(vp, 2.0 + 0.55, 1.5))
    check("a circle's quadrant is offered", tool.ref_hit["kind"] == "quad"
          and tool.ref_hit["q"] == 0, str(tool.ref_hit))
    click(2.0 + 0.55, 1.5)
    quad = ents("point")[-1]
    check("projected down onto the base plane",
          near(pt(quad["id"])[0], 2.55) and near(pt(quad["id"])[1], 1.5))
    world = model.to_world(ctrl.sketch(), *pt(quad["id"]))
    check("…at the base's level", near(world[2], 0.0))

    # ---- robustness --------------------------------------------------------------
    print("robustness")
    ctrl.set_mode("move")
    click(*pt(quad["id"]))
    check("a reference cannot be dragged", tool.grab is None
          and "fixed" in ctrl.message)
    ctrl.set_mode("ref")
    click(9, 9)
    check("a click on nothing says what to do", "marked spots" in ctrl.message)
    ctrl.finish()
    U.paint(app)
    ctrl.delete_sketch(base["id"])
    top_now = model.sketch(ctrl.doc, top["id"])
    check("source deleted: references stay where they were, nothing breaks",
          near(pt(ref["id"], top_now)[0], 2.0) and not app.overlays[0]._failed)
    check("panel has the tool",
          "ref" in {b.property("mode") for b in panel.modes.buttons()})

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
