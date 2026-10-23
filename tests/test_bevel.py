"""Fillet and chamfer: editable corners, and the solid follows them.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_bevel.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402  (installs the engine, the stand-in viewport)

import Cad_sketcher  # noqa: E402
from Cad_sketcher import model  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

check, near = U.check, U.near
W, H, T = 3.0, 1.25, 2.0


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    asked = []

    def click(u, v):
        tool.on_hover(U.ctx(vp, u, v))
        tool.on_click(U.ctx(vp, u, v))
        QApplication.processEvents()            # the dialog is asked for later

    def volume():
        return solid_report(vp.scene.groups[0].mesh)[1]

    def pt(i):
        e = model.ents(ctrl.sketch())[i]
        return e["x"], e["y"]

    # a fully dimensioned W × H rectangle from the origin, extruded T
    sk = ctrl.new_sketch("XY")
    ctrl.set_mode("rect")
    click(0, 0)
    click(2.0, 1.0)
    lines = [e for e in ctrl.sketch()["ents"] if e["t"] == "line"]
    ctrl.sel = [lines[0]["id"]]
    ctrl.constrain("distance", value=W)
    ctrl.sel = [lines[1]["id"]]
    ctrl.constrain("distance", value=H)
    ctrl.add_feature("extrude", sketch=sk["id"], distance=T, symmetric=False)
    check("box", ctrl.result.dof == 0 and near(volume(), W * H * T))

    # ---- fillet the far corner (W, H) with the tool -----------------------------
    print("fillet")
    corner = lines[1]["p2"]
    ctrl.ask_value = lambda title, default, **k: asked.append(title) or 0.4
    check("tool and key", tool.on_key(vp, Qt.Key_F, Qt.NoModifier)
          and ctrl.mode == "fillet")
    click(W, H)
    arcs = [e for e in ctrl.sketch()["ents"] if e["t"] == "arc"]
    check("asked for the radius, arc made",
          asked == ["Fillet radius"] and len(arcs) == 1, str(asked))
    arc = arcs[0]
    r = 0.4
    check("still fully constrained", ctrl.result.ok and ctrl.result.dof == 0,
          ctrl.result.text)
    ends = sorted([pt(arc["p1"]), pt(arc["p2"])])
    check("tangent points exact",
          near(ends[0][0], W - r) and near(ends[0][1], H)
          and near(ends[1][0], W) and near(ends[1][1], H - r)
          and near(pt(arc["c"])[0], W - r) and near(pt(arc["c"])[1], H - r),
          str(ends))
    check("corner point kept, as construction",
          near(pt(corner)[0], W) and near(pt(corner)[1], H)
          and model.ents(ctrl.sketch())[corner]["con"])

    def want(rad):
        # the quarter circle is 8 of the document's 32 segments
        quarter = 4.0 * rad * rad * math.sin(math.pi / 16.0)
        return (W * H - rad * rad + quarter) * T
    check("solid got the round corner", abs(volume() - want(r)) < 1e-6,
          f"V={volume():.5f} want≈{want(r):.5f}")

    # the overall size is still what the dimensions say, and still editable
    width = next(c for c in ctrl.sketch()["cons"] if c.get("v") == W)
    height = next(c for c in ctrl.sketch()["cons"] if c.get("v") == H)
    check("the shortened side's dimension moved to the corner",
          set(height["e"]) == {lines[1]["p1"], corner}, str(height["e"]))
    ctrl.set_value(width["id"], 4.0)
    check("width still drives the sketch", ctrl.result.ok
          and near(pt(corner)[0], 4.0) and near(pt(arc["c"])[0], 4.0 - r))
    ctrl.set_value(width["id"], W)

    # ---- THE request: change the radius, the 3D object follows ------------------
    rad = next(c for c in ctrl.sketch()["cons"] if c["t"] == "radius")
    ctrl.set_value(rad["id"], 0.8)
    check("edit radius → sketch", ctrl.result.ok
          and near(pt(arc["c"])[0], W - 0.8) and near(pt(arc["c"])[1], H - 0.8))
    check("edit radius → solid", abs(volume() - want(0.8)) < 1e-6,
          f"V={volume():.5f} want≈{want(0.8):.5f}")
    vp.undo()
    check("undo", abs(volume() - want(0.4)) < 1e-6)
    U.paint(app)
    check("overlay shows it", not app.overlays[0]._failed
          and rad["id"] in tool.labels)

    # ---- chamfer the corner (W, 0) -----------------------------------------------
    print("chamfer")
    ctrl.ask_value = lambda title, default, **k: asked.append(title) or 0.3
    ctrl.set_mode("chamfer")
    n_lines = len([e for e in ctrl.sketch()["ents"] if e["t"] == "line"])
    click(W, 0.0)
    now = [e for e in ctrl.sketch()["ents"] if e["t"] == "line"]
    cut = now[-1]
    check("chamfer line made", asked[-1] == "Chamfer distance"
          and len(now) == n_lines + 1 and ctrl.result.ok and ctrl.result.dof == 0,
          ctrl.result.text)
    e = sorted([pt(cut["p1"]), pt(cut["p2"])])
    check("chamfer ends exact", near(e[0][0], W - 0.3) and near(e[0][1], 0.0)
          and near(e[1][0], W) and near(e[1][1], 0.3), str(e))
    base = want(0.4)
    check("solid got the cut corner",
          abs(volume() - (base - 0.3 * 0.3 / 2 * T)) < 1e-6, f"V={volume():.5f}")
    d = [c for c in ctrl.sketch()["cons"] if c["t"] == "distance"
         and c.get("v") == 0.3]
    ctrl.set_value(d[0]["id"], 0.6)
    check("edit one chamfer distance → solid",
          abs(volume() - (base - 0.6 * 0.3 / 2 * T)) < 1e-6, f"V={volume():.5f}")

    # ---- refusals ----------------------------------------------------------------
    print("refusals")
    ctrl.ask_value = lambda *a, **k: 50.0
    ctrl.set_mode("fillet")
    n = len(ctrl.sketch()["ents"])
    click(0.0, H)
    check("too large is refused, nothing changes",
          "Too large" in ctrl.message and len(ctrl.sketch()["ents"]) == n,
          ctrl.message)
    click(*pt(arc["c"]))
    check("not a corner is refused", "exactly two lines" in ctrl.message
          and len(ctrl.sketch()["ents"]) == n, ctrl.message)
    ctrl.ask_value = lambda *a, **k: None
    click(0.0, H)
    check("cancel does nothing", len(ctrl.sketch()["ents"]) == n)
    check("panel has the buttons",
          {b.property("mode") for b in panel.modes.buttons()}
          >= {"fillet", "chamfer"})

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
