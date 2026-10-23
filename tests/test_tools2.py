"""Grid, copy / paste / mirror, patterns, centre rectangle, slot, semicircle,
extend, chain select, shortcuts, and sketching on a face of a built solid.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_tools2.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import convert, host, model  # noqa: E402
from Cad_sketcher.panel import _PatternDialog  # noqa: E402
from Cad_sketcher.tool import PickFaceTool  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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
        QApplication.processEvents()

    def ents(t=None):
        return [e for e in ctrl.sketch()["ents"] if t is None or e["t"] == t]

    def pt(i):
        e = model.ents(ctrl.sketch())[i]
        return e["x"], e["y"]

    def ends(e):
        return sorted([pt(e["p1"]), pt(e["p2"])])

    def rnd(p):
        return (round(p[0], 6), round(p[1], 6))

    def key(k, mods=Qt.NoModifier):
        return tool.claims_key(k, mods) and tool.on_key(vp, k, mods)

    def group_of(feat):
        return host.feature_groups(vp.scene, ctrl.doc).get(feat["id"])

    # ---- grid ---------------------------------------------------------------------
    print("grid")
    ctrl.new_sketch("XY", name="Grid")
    check("grid off by default (tests never read the user's settings)",
          not ctrl.grid["snap"] and not panel.grid_snap.isChecked())
    panel.grid_step.setValue(0.25)
    panel.grid_snap.setChecked(True)
    panel.grid_show.setChecked(True)
    check("panel drives the grid", ctrl.grid == {"show": True, "snap": True,
                                                 "step": 0.25})
    ctrl.set_mode("line")
    tool.on_hover(U.ctx(vp, 1.07, 0.93))
    check("cursor lands on the grid", tool.cursor["uv"] == (1.0, 1.0)
          and tool.cursor.get("grid"))
    click(1.07, 0.93)
    click(2.61, 1.9)
    tool.on_cancel(vp)
    check("drawn on the grid", ends(ents("line")[0]) == [(1.0, 1.0), (2.5, 2.0)],
          str(ends(ents("line")[0])))
    ctrl.set_mode("line")
    tool.on_hover(U.ctx(vp, 1.02, 1.03))
    check("an existing point still wins over the grid",
          tool.cursor["pid"] == ents("line")[0]["p1"])
    img = U.paint(app)
    check("grid is drawn", not app.overlays[0]._failed and img.pixelColor(
        int(U.CX + 325), int(U.CY - 137)) != img.pixelColor(
        int(U.CX + 337), int(U.CY - 137)))
    check("G toggles snap", key(Qt.Key_G) and not ctrl.grid["snap"]
          and not panel.grid_snap.isChecked())
    panel.grid_show.setChecked(False)

    # ---- shortcuts belong to the sketch while it is edited -------------------------
    print("shortcuts")
    check("L is claimed from IngeTrazo's own Line tool",
          tool.claims_key(Qt.Key_L, Qt.NoModifier)
          and tool.claims_key(Qt.Key_C, Qt.ControlModifier)
          and not tool.claims_key(Qt.Key_F5, Qt.NoModifier))
    vp._value_buffer = "1"
    check("…but not while a number is being typed",
          not tool.claims_key(Qt.Key_L, Qt.NoModifier))
    vp._value_buffer = ""

    # ---- centre rectangle, slot, semicircle ----------------------------------------
    print("new draw tools")
    ctrl.new_sketch("XY", name="Shapes")
    check("B key", key(Qt.Key_B) and ctrl.mode == "crect")
    click(0, 0)                                    # the origin as centre
    tool.on_hover(U.ctx(vp, 1.5, 1.0))
    check("centre rectangle preview", len(tool.preview_uv()[0]) == 5)
    click(1.5, 1.0)
    xs = sorted({rnd(pt(e[k]))[0] for e in ents("line") if not e["con"]
                 for k in ("p1", "p2")})
    check("centre rectangle", xs == [-1.5, 1.5] and len(ents("line")) == 5
          and ctrl.result.ok, f"{xs} {ctrl.result.text}")
    side = [e for e in ents("line") if not e["con"]][0]
    ctrl.sel = [side["id"]]
    ctrl.constrain("distance", value=4.0)
    xs = sorted({rnd(pt(e[k]))[0] for e in ents("line") if not e["con"]
                 for k in ("p1", "p2")})
    check("it stays centred when dimensioned", xs == [-2.0, 2.0], str(xs))

    ctrl.new_sketch("XY", name="Slot")
    check("U key", key(Qt.Key_U) and ctrl.mode == "slot")
    click(1, 1)
    click(4, 1.02)                                 # nearly level → horizontal
    tool.on_hover(U.ctx(vp, 2.5, 1.5))
    check("slot preview", len(tool.preview_uv()) == 4)
    check("typed half-width", tool.on_value(vp, 0.4) and len(ents("arc")) == 2
          and len(ents("line")) == 2 and ctrl.result.ok, ctrl.result.text)
    loops, paths = convert.walk(ctrl.sketch(), 32)
    area = convert._area2(loops[0]["pts"]) / 2 if loops else 0
    want = 3.0 * 0.8 + 16 * math.sin(math.pi / 16) * 0.16     # 32-gon ends
    check("slot is one closed outline of the right size",
          len(loops) == 1 and not paths and near(area, want, 1e-6),
          f"area={area} want={want}")
    rad = next(c for c in ctrl.sketch()["cons"] if c["t"] == "radius")
    ctrl.set_value(rad["id"], 0.6)
    by = model.ents(ctrl.sketch())
    check("one radius drives both ends",
          all(near(model.radius(by, a), 0.6) for a in ents("arc"))
          and ctrl.result.ok)

    ctrl.new_sketch("XY", name="Semi")
    check("H key", key(Qt.Key_H) and ctrl.mode == "semi")
    click(0, 0)
    click(2, 0.01)
    arc = ents("arc")[0]
    mid_y = convert.arc_points(pt(arc["c"]), pt(arc["p1"]), pt(arc["p2"]), 4)[1][1]
    check("semicircle", near(pt(arc["c"])[0], 1.0) and near(pt(arc["c"])[1], 0.0)
          and near(mid_y, -1.0) and ctrl.result.ok,
          f"c={pt(arc['c'])} mid_y={mid_y}")

    # ---- copy / paste / mirror -------------------------------------------------------
    print("copy, paste, mirror")
    ctrl.new_sketch("XY", name="Copies")
    ctrl.set_mode("circle")
    click(1, 1)
    click(1.3, 1)
    circle = ents("circle")[0]
    ctrl.sel = [circle["id"]]
    ctrl.constrain("radius", value=0.25)
    ctrl.sel = [circle["id"]]
    check("Ctrl+C", key(Qt.Key_C, Qt.ControlModifier) and ctrl.clip is not None)
    check("Ctrl+V enters paste", key(Qt.Key_V, Qt.ControlModifier)
          and ctrl.mode == "paste")
    tool.on_hover(U.ctx(vp, 3, 2))
    prev = tool.preview_uv()
    check("paste preview follows the cursor", len(prev) == 1
          and near(sum(p[0] for p in prev[0][:-1]) / (len(prev[0]) - 1), 3.0, 1e-6))
    click(3, 2)
    pasted = ents("circle")[-1]
    check("pasted where clicked, selected, back to Select",
          len(ents("circle")) == 2 and rnd(pt(pasted["c"])) == (3.0, 2.0)
          and near(pasted["r"], 0.25) and pasted["id"] in ctrl.sel
          and ctrl.mode == "select")
    other = ctrl.new_sketch("XY", offset=1.0, name="Other")
    ctrl.set_mode("paste")
    click(0.5, 0.5)
    check("paste into another sketch", len(ents("circle")) == 1
          and ctrl.active == other["id"])
    ctrl.edit(model.find(ctrl.doc["sketches"], circle and ctrl.doc["sketches"][-2]["id"])["id"])

    ctrl.sel = [circle["id"]]
    ctrl.mirror("y")
    m = ents("circle")[-1]
    check("mirror Y", rnd(pt(m["c"])) == (-1.0, 1.0) and near(m["r"], 0.25))
    rad = next(c for c in ctrl.sketch()["cons"] if c["t"] == "radius"
               and c["e"] == [circle["id"]])
    ctrl.set_value(rad["id"], 0.4)
    check("the mirrored copy follows the original's radius", near(m["r"], 0.4)
          and ctrl.result.ok)
    ctrl.set_mode("line")
    click(-2, 3)
    click(2, 3)
    tool.on_cancel(vp)
    axis = ents("line")[-1]
    ctrl.sel = [axis["id"]]
    ctrl.toggle_flag("con")
    ctrl.sel = [circle["id"], axis["id"]]
    ctrl.mirror(None)
    s = ents("circle")[-1]
    check("mirror about a line", rnd(pt(s["c"])) == (1.0, 5.0))
    n_sym = len([c for c in ctrl.sketch()["cons"] if c["t"] == "symmetric"])
    ctrl.set_mode("move")
    click(1, 1)
    tool.on_hover(U.ctx(vp, 1.5, 0.5))
    tool.on_click(U.ctx(vp, 1.5, 0.5))
    check("…and the copy stays symmetric when the original moves",
          n_sym == 1 and near(pt(s["c"])[0], pt(circle["c"])[0], 1e-6)
          and near((pt(s["c"])[1] + pt(circle["c"])[1]) / 2,
                   pt(axis["p1"])[1], 1e-6)
          and near(pt(axis["p1"])[1], pt(axis["p2"])[1], 1e-6),
          f"{pt(circle['c'])} → {pt(s['c'])}")
    ctrl.sel = []
    ctrl.mirror("x")
    check("nothing selected is refused", "Select what to copy" in ctrl.message)

    # ---- patterns ----------------------------------------------------------------------
    print("patterns")
    ctrl.new_sketch("XY", name="Flange")
    ctrl.set_mode("circle")
    click(0, 0)
    click(3, 0)
    ctrl.set_mode("circle")
    click(2, 0)
    click(2.2, 0)
    hole = ents("circle")[-1]
    ctrl.sel = [hole["id"]]
    ctrl.constrain("radius", value=0.2)
    ctrl.sel = [hole["id"]]
    ctrl.pattern_circular(6, 360.0)
    holes = ents("circle")[1:]
    got = sorted(rnd(pt(h["c"])) for h in holes)
    want = sorted(rnd((2 * math.cos(math.radians(60 * k)),
                       2 * math.sin(math.radians(60 * k)))) for k in range(6))
    check("six holes on a circle", len(holes) == 6 and got == want
          and len(ctrl.sel) > 0 and ctrl.result.ok, str(got))
    rad = next(c for c in ctrl.sketch()["cons"] if c["t"] == "radius"
               and c["e"] == [hole["id"]])
    ctrl.set_value(rad["id"], 0.3)
    check("one radius drives every hole", all(near(h["r"], 0.3) for h in holes))
    feat = ctrl.add_feature("extrude", sketch=ctrl.active, distance=0.5)
    ok, vol = solid_report(group_of(feat).mesh)
    ngon = 16 * math.sin(math.pi / 16)
    check("flange extrudes with its holes",
          ok and near(vol, ngon * (9 - 6 * 0.09) * 0.5, 1e-5), f"V={vol}")

    ctrl.new_sketch("XY", name="Row")
    ctrl.set_mode("rect")
    click(0, 0)
    click(0.5, 0.5)
    ctrl.select_all()
    check("Ctrl+A leaves the fixed origin out", ents("point")[0]["id"] not in ctrl.sel
          and len(ctrl.sel) == 7)
    ctrl.sel = [e["id"] for e in ents("line")]
    ctrl.pattern_linear(4, 1.0, 0.0)
    loops, _p = convert.walk(ctrl.sketch(), 32)
    check("linear pattern: four squares", len(loops) == 4
          and len(ents("line")) == 16 and ctrl.result.ok, f"{len(loops)} loops")
    ctrl.sel = [ents("line")[0]["id"]]
    ctrl.pattern_linear(1, 1.0, 0.0)
    check("count 1 is refused", "count of 2" in ctrl.message)
    dlg = _PatternDialog(panel, "circular")
    check("pattern dialogs", dlg.values() == (6, 360.0)
          and _PatternDialog(panel, "linear").values() == (4, 1.0, 0.0))

    # ---- extend, chain select ---------------------------------------------------------
    print("extend, chain")
    ctrl.auto = False
    ctrl.new_sketch("XY", name="Extend")
    for a, b in (((0, 1), (2, 1)), ((3, 0), (3, 3)), ((5, 0), (5, 3))):
        ctrl.set_mode("line")
        click(*a)
        click(*b)
        tool.on_cancel(vp)
    short, wall = ents("line")[0], ents("line")[1]
    check("X key", key(Qt.Key_X) and ctrl.mode == "extend")
    click(1.8, 1)                                  # near its right end
    check("extended to the first thing in its way",
          ends(short) == [(0.0, 1.0), (3.0, 1.0)] and ctrl.result.ok,
          str(ends(short)))
    tie = ctrl.sketch()["cons"][-1]
    check("the new end is held on what stopped it",
          tie["t"] == "coincident" and tie["e"] == [short["p2"], wall["id"]])
    click(0.2, 1)                                  # the left end: nothing there
    check("nothing in the way is refused", "Nothing in the way" in ctrl.message)
    ctrl.auto = True

    ctrl.new_sketch("XY", name="Chain")
    ctrl.set_mode("rect")
    click(1, 1)
    click(2, 2)
    ctrl.set_mode("circle")
    click(4, 4)
    click(4.5, 4)
    ctrl.set_mode("select")
    tool.on_double_click(U.ctx(vp, 1.5, 1.0))
    check("double-click selects the whole outline",
          sorted(ctrl.sel) == sorted(e["id"] for e in ents("line")))
    ctrl.sel = [ents("line")[2]["id"]]
    ctrl.select_chain()
    check("Chain button does the same", len(ctrl.sel) == 4)

    # ---- sketch on a face of a built solid, then a pocket ------------------------------
    print("sketch on a solid's face")
    base = ctrl.new_sketch("XY", name="Block")
    ctrl.set_mode("rect")
    click(0, 0)
    click(4, 2)
    block = ctrl.add_feature("extrude", sketch=base["id"], distance=1.5)
    g = group_of(block)
    top = next(f for f in g.mesh.faces
               if f.normal().z() > 0.9 and near(f.centroid().z(), 1.5, 1e-6))
    side = next(f for f in g.mesh.faces if f.normal().x() > 0.9)
    vp.pick_face_placement = lambda x, y: (top, None)
    ctrl.pick_face()
    check("New ▸ On a face… hands over a picking tool",
          isinstance(vp.active_tool, PickFaceTool) and ctrl.active is None)
    vp.active_tool.on_click(U.ctx(vp, 2, 1))
    sk = ctrl.sketch()
    check("a sketch starts on the solid's top face",
          sk is not None and near(sk["o"][2], 1.5, 1e-6)
          and near(abs(model.normal(sk)[2]), 1.0) and vp.active_tool is tool,
          str(sk and sk["o"]))
    ctrl.set_mode("circle")
    c_uv = model.to_local(sk, (1.0, 1.0, 1.5))
    r_uv = model.to_local(sk, (1.5, 1.0, 1.5))
    pc, pr = model.to_world(sk, *c_uv), model.to_world(sk, *r_uv)
    click(pc[0], pc[1])
    click(pr[0], pr[1])
    # a NEW solid standing on that face
    boss = ctrl.add_feature("extrude", sketch=sk["id"],
                            distance=1.0 if model.normal(sk)[2] > 0 else -1.0)
    zs = [v.position.z() for f in group_of(boss).mesh.faces for v in f.loop]
    check("a new solid built on the face", near(min(zs), 1.5, 1e-6)
          and near(max(zs), 2.5, 1e-6) and solid_report(group_of(boss).mesh)[0],
          f"{min(zs)} … {max(zs)}")
    # a pocket: the same sketch pulled INTO the block, then IngeTrazo's Subtract
    ctrl.update_feature(boss["id"],
                        distance=-0.5 if model.normal(sk)[2] > 0 else 0.5)
    cutter = group_of(boss)
    from core.solids import SolidOperationCommand
    cmd = SolidOperationCommand("subtract", [cutter, group_of(block)])
    cmd.prepare()
    vp.history.execute(cmd)
    vp.notify_scene_changed()
    result = [x for x in vp.scene.groups if x not in (cutter, g)]
    ngon = 16 * math.sin(math.pi / 16)
    ok, vol = solid_report(result[-1].mesh)
    check("IngeTrazo's Subtract cuts the pocket",
          ok and near(vol, 4 * 2 * 1.5 - ngon * 0.25 * 0.5, 1e-5), f"V={vol}")
    n = len(vp.scene.groups)
    ctrl.set_mode("point")
    click(pc[0] + 0.1, pc[1] + 0.1)                # any further sketch edit…
    check("…and the spent solids are not built again behind your back",
          len(vp.scene.groups) == n and boss.get("detached")
          and block.get("detached"), f"{len(vp.scene.groups)} groups")
    panel.refresh()
    check("the list says so", "no longer in the model" in panel.features.item(
        panel.features.count() - 1).text())
    ctrl.update_feature(boss["id"], distance=0.3)
    check("editing a spent solid builds it afresh",
          group_of(boss) is not None and not boss.get("detached")
          and len(vp.scene.groups) == n + 1)

    vp.pick_face_placement = lambda x, y: (side, None)
    ctrl.pick_face()
    vp.active_tool.on_click(U.ctx(vp, 2, 1))
    sk = ctrl.sketch()
    check("a side face gives a vertical workplane",
          near(abs(model.normal(sk)[0]), 1.0) and near(sk["o"][0], 4.0, 1e-6))
    vp.pick_face_placement = lambda x, y: (None, None)
    ctrl.pick_face()
    vp.active_tool.on_click(U.ctx(vp, 9, 9))
    check("a click on nothing says so and keeps waiting",
          "No face there" in ctrl.message
          and isinstance(vp.active_tool, PickFaceTool))
    U.paint(app)
    check("overlay alive", not app.overlays[0]._failed)
    check("all the new tools are in the panel",
          {b.property("mode") for b in panel.modes.buttons()}
          >= {"crect", "slot", "semi", "extend"})

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
