"""Sketch placement: origin X, Y, Z + rotation about X, Y, Z — a section
stood square to an inclined path, the sweep following.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_placement.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from Cad_sketcher.panel import _PlacementDialog  # noqa: E402
from core.solids import solid_report  # noqa: E402

check, near = U.check, U.near
SLOPE = math.degrees(math.atan2(1.25, 2.15))           # 30.17°
LENGTH = math.hypot(2.15, 1.25)


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance

    def group_of(feat):
        return host.feature_groups(vp.scene, ctrl.doc).get(feat["id"])

    # the path: one straight run, 2150 along X rising 1250, drawn on XZ
    path = ctrl.new_sketch("XZ", name="Path")
    end = model.add_point(ctrl.doc, ctrl.sketch(), 2.15, 1.25)
    model.add_line(ctrl.doc, ctrl.sketch(), ctrl.sketch()["ents"][0]["id"], end["id"])
    ctrl.commit()
    # the section: a 0.2 × 0.2 square around its origin, drawn on YZ
    sec = ctrl.new_sketch("YZ", name="Section")
    s = ctrl.sketch()
    ids = [model.add_point(ctrl.doc, s, u, v)["id"]
           for u, v in ((-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1))]
    for i in range(4):
        model.add_line(ctrl.doc, s, ids[i], ids[(i + 1) % 4])
    ctrl.commit()
    ctrl.finish()
    sweep = ctrl.add_feature("sweep", profile=sec["id"], path=path["id"])
    ok, v_flat = solid_report(group_of(sweep).mesh)
    check("unplaced (vertical) section sweeps distorted",
          ok and not near(v_flat, 0.04 * LENGTH, 1e-4),
          f"V={v_flat:.5f} vs true {0.04 * LENGTH:.5f}")

    # ---- the dialog --------------------------------------------------------------
    print("placement")
    sk = model.sketch(ctrl.doc, sec["id"])
    dlg = _PlacementDialog(panel, sk)
    check("dialog starts at the sketch's place",
          dlg.values() == ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
          and "straight along +X" in dlg.facing.text(), dlg.facing.text())
    dlg.angles[1].setValue(-SLOPE)
    check("live readout of where it faces",
          "+0.8645" in dlg.facing.text() and "+0.5026" in dlg.facing.text(),
          dlg.facing.text())
    origin, angles = dlg.values()
    check("dialog values", origin == [0.0, 0.0, 0.0] and near(angles[1], -SLOPE, 5e-4))
    ctrl.place_sketch(sec["id"], origin, [0.0, -SLOPE, 0.0])   # full precision
    sk = model.sketch(ctrl.doc, sec["id"])
    n = model.normal(sk)
    check("tilted square to the path",
          near(n[0], 2.15 / LENGTH, 1e-9) and near(n[2], 1.25 / LENGTH, 1e-9)
          and near(n[1], 0.0), str(n))
    ok, vol = solid_report(group_of(sweep).mesh)
    check("…and the sweep is now exact: area × length",
          ok and near(vol, 0.04 * LENGTH, 1e-6), f"V={vol} want={0.04 * LENGTH}")
    again = _PlacementDialog(panel, sk)
    check("the window shows back what was typed",
          near(again.values()[1][1], -SLOPE, 5e-4))

    # origin: move the whole thing, drawing coordinates unchanged
    ctrl.place_sketch(sec["id"], [5.0, 1.0, 2.0], [0.0, -SLOPE, 0.0])
    sk = model.sketch(ctrl.doc, sec["id"])
    check("origin moves the sketch, its drawing stays",
          sk["o"] == [5.0, 1.0, 2.0]
          and [(e["x"], e["y"]) for e in sk["ents"] if e["t"] == "point"][1]
          == (-0.1, -0.1))
    vp.undo()
    check("undo", model.sketch(ctrl.doc, sec["id"])["o"] == [0.0, 0.0, 0.0])

    # heading: turned 90° about Z it faces along +Y
    ctrl.place_sketch(sec["id"], [0, 0, 0], [0.0, 0.0, 90.0])
    n = model.normal(model.sketch(ctrl.doc, sec["id"]))
    check("about Z turns it left / right", near(n[1], 1.0) and near(n[0], 0.0))
    ctrl.place_sketch(sec["id"], [0, 0, 0], [0.0, 0.0, 0.0])
    sk = model.sketch(ctrl.doc, sec["id"])
    check("reset to 0: back on YZ, Align view works again",
          sk.get("plane") == "YZ" and sk["x"] == [0.0, 1.0, 0.0])

    # a sketch from before placements existed
    old = model.sketch(ctrl.doc, path["id"])
    old.pop("base", None)
    old.pop("rot", None)
    check("old sketches open at 0°", model.placement(old)[1] == [0.0, 0.0, 0.0])
    ctrl.place_sketch(path["id"], old["o"], [0.0, 0.0, 45.0])
    n = model.normal(model.sketch(ctrl.doc, path["id"]))
    check("…and can be placed", near(n[0], math.sqrt(0.5))
          and near(n[1], -math.sqrt(0.5)), str(n))
    dup = ctrl.duplicate_sketch(sec["id"], "", 0.0, "XY")
    check("a duplicate onto a standard plane starts at 0°",
          model.placement(dup)[1] == [0.0, 0.0, 0.0])
    ctrl.finish()
    panel.sketches.setCurrentRow(-1)
    panel._placement()
    check("nothing chosen: says what to do", "Select the sketch" in ctrl.message)
    check("panel button", panel.place_btn.text() == "Placement…")

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
