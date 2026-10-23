"""Duplicate a whole sketch: shape, constraints, dimensions - independent copy.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_duplicate.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from Cad_sketcher.panel import _DuplicateDialog  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

check, near = U.check, U.near


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    ctrl.ask_value = lambda *a, **k: 0.05

    def click(u, v):
        tool.on_hover(U.ctx(vp, u, v))
        tool.on_click(U.ctx(vp, u, v))
        QApplication.processEvents()

    # an L-shaped, fully dimensioned profile with a fillet, like the user's
    src = ctrl.new_sketch("XY", name="Profile")
    ctrl.set_mode("line")
    # (the user's angle at x10, so the stand-in view can click its corners)
    for u, v in ((0, 0), (5, 0), (5, 1), (1, 1), (1, 4), (0, 4), (0, 0)):
        click(u, v)
    lines = [e for e in ctrl.sketch()["ents"] if e["t"] == "line"]
    ctrl.set_mode("fillet")
    click(1, 1)
    for ln, val in ((lines[0], 5.2), (lines[1], 0.1), (lines[4], 1.07),
                    (lines[3], 0.97)):
        ctrl.sel = [ln["id"]]
        ctrl.constrain("distance", value=val)
    dof = ctrl.result.dof
    check("source profile", ctrl.result.ok
          and len([e for e in ctrl.sketch()["ents"] if e["t"] == "arc"]) == 1,
          ctrl.result.text)
    feat = ctrl.add_feature("extrude", sketch=src["id"], distance=2.0)
    v_src = solid_report(host.feature_groups(vp.scene, ctrl.doc)[feat["id"]].mesh)[1]
    src_now = model.sketch(ctrl.doc, src["id"])
    n_ents, n_cons = len(src_now["ents"]), len(src_now["cons"])

    # ---- same plane, moved 3 m along the normal ---------------------------------
    print("duplicate")
    panel.sketches.setCurrentRow(0)
    dlg = _DuplicateDialog(panel, src_now, "Profile copy")
    dlg.offset.setValue(3.0)
    name, off, plane = dlg.values()
    check("dialog", (name, off, plane) == ("Profile copy", 3.0, None))
    copy_ = ctrl.duplicate_sketch(src["id"], name, off, plane)
    check("the copy is being edited, right after the original in the list",
          ctrl.active == copy_["id"] and ctrl.doc["sketches"][1] is copy_
          and panel.sketches.item(1).text().strip().endswith("Profile copy"))
    check("everything copied", len(copy_["ents"]) == n_ents
          and len(copy_["cons"]) == n_cons)
    old_ids = {e["id"] for e in src_now["ents"]} | {c["id"] for c in src_now["cons"]}
    new_ids = {e["id"] for e in copy_["ents"]} | {c["id"] for c in copy_["cons"]}
    refs = {i for e in copy_["ents"] for i in model.point_refs(e)} \
        | {i for c in copy_["cons"] for i in c["e"]}
    check("fresh ids, all pointing inside the copy",
          not (old_ids & new_ids) and refs <= {e["id"] for e in copy_["ents"]})
    check("solved as it was: same degrees of freedom", ctrl.result.ok
          and ctrl.result.dof == dof, ctrl.result.text)
    check("3 m above the original", near(copy_["o"][2], 3.0)
          and copy_["x"] == src_now["x"])
    # independent: change the copy, the original and its solid do not move
    width = next(c for c in copy_["cons"] if c.get("v") == 5.2)
    ctrl.set_value(width["id"], 8.0)
    g = host.feature_groups(vp.scene, ctrl.doc)[feat["id"]]
    xs = [e["x"] for e in model.sketch(ctrl.doc, src["id"])["ents"]
          if e["t"] == "point"]
    check("editing the copy leaves the original alone",
          near(max(xs), 5.2) and near(solid_report(g.mesh)[1], v_src))
    rad = next(c for c in copy_["cons"] if c["t"] == "radius")
    ctrl.set_value(rad["id"], 0.03)
    check("...its fillet too", ctrl.result.ok)
    vp.undo()
    check("undo", near(model.find(model.sketch(ctrl.doc, copy_["id"])["cons"],
                                  rad["id"])["v"], 0.05))

    # ---- onto another plane: a profile to sweep, a loft section ------------------
    side = ctrl.duplicate_sketch(src["id"], "", 0.0, "XZ")
    check("onto XZ, keeping its shape",
          side["name"] == "Profile copy 2" and side["x"] == [1.0, 0.0, 0.0]
          and side["y"] == [0.0, 0.0, 1.0] and near(abs(model.normal(side)[1]), 1.0)
          and ctrl.result.ok and ctrl.result.dof == dof)
    loft = ctrl.add_feature("loft", sketches=[src["id"], copy_["id"]])
    g = host.feature_groups(vp.scene, ctrl.doc).get(loft["id"])
    check("original + copy loft into a solid", g is not None
          and solid_report(g.mesh)[0], str(loft.get("error")))
    check("the original is still the original",
          len(model.sketch(ctrl.doc, src["id"])["ents"]) == n_ents)
    ctrl.finish()
    panel.sketches.setCurrentRow(-1)
    panel._duplicate()
    check("nothing chosen: says what to do", "Select the sketch" in ctrl.message)

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()


