"""Fillet / chamfer on every corner of a CENTRE rectangle (its construction
diagonal ends on two of the corners), drawn the way the user did.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_bevel2.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import convert, host, model  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

check, near = U.check, U.near
W, H = 7.5, 4.25


def build(ctrl, tool, vp, name):
    def click(u, v):
        tool.on_hover(U.ctx(vp, u, v))
        tool.on_click(U.ctx(vp, u, v))
        QApplication.processEvents()

    sk = ctrl.new_sketch("XY", name=name)
    ctrl.set_mode("crect")
    click(0, 0)
    click(3, 2)
    sides = [e for e in ctrl.sketch()["ents"] if e["t"] == "line" and not e["con"]]
    ctrl.sel = [sides[0]["id"]]
    ctrl.constrain("distance", value=W)
    ctrl.sel = [sides[1]["id"]]
    ctrl.constrain("distance", value=H)
    return sk, click


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    corners = [(-W / 2, -H / 2), (W / 2, -H / 2), (W / 2, H / 2), (-W / 2, H / 2)]
    ngon_quarter = 4.0 * math.sin(math.pi / 16.0)        # 8 of 32 segments

    for kind, size, key in (("fillet", 0.15, "radius"), ("chamfer", 0.2, "distance")):
        print(kind)
        sk, click = build(ctrl, tool, vp, kind)
        check(f"{kind}: rectangle {W} × {H} fully constrained",
              ctrl.result.ok and ctrl.result.dof == 0, ctrl.result.text)
        ctrl.ask_value = lambda *a, **k: size
        ctrl.set_mode(kind)
        for n, (u, v) in enumerate(corners, start=1):
            click(u, v)
            made = [e for e in ctrl.sketch()["ents"]
                    if e["t"] == ("arc" if kind == "fillet" else "line")
                    and not e.get("con")]
            want = n if kind == "fillet" else 4 + n
            check(f"{kind}: corner {n} of 4 ({u:+}, {v:+})",
                  len(made) == want and ctrl.result.ok and ctrl.result.dof == 0,
                  f"{len(made)} made, {ctrl.message!r}, {ctrl.result.text}")
        loops, paths = convert.walk(ctrl.sketch(), 32)
        area = convert._area2(loops[0]["pts"]) / 2 if loops else 0.0
        cut = (size * size * (1 - ngon_quarter)) if kind == "fillet" \
            else size * size / 2
        check(f"{kind}: still one closed outline, the right size",
              len(loops) == 1 and not paths and near(area, W * H - 4 * cut, 1e-6),
              f"{len(loops)} loops, area={area}")
        by = model.ents(ctrl.sketch())
        centre = ctrl.sketch()["ents"][0]
        xs = [by[i]["x"] for e in loops and ctrl.sketch()["ents"]
              if e["t"] == "line" and not e.get("con") for i in (e["p1"], e["p2"])]
        check(f"{kind}: still centred on the origin",
              near(min(xs), -W / 2) and near(max(xs), W / 2)
              and centre["x"] == 0.0)
        # the overall size and the corner size are still editable
        feat = ctrl.add_feature("extrude", sketch=sk["id"], distance=1.0)
        dims = [c for c in ctrl.sketch()["cons"] if c["t"] == key
                and near(c.get("v", 0), size)]
        check(f"{kind}: {len(dims)} editable corner dimensions",
              len(dims) == (4 if kind == "fillet" else 8))
        ctrl.set_value(dims[0]["id"], size * 2)
        big = (4 * size * size * (1 - ngon_quarter)) if kind == "fillet" \
            else (2 * size) * size / 2
        g = host.feature_groups(vp.scene, ctrl.doc)[feat["id"]]
        ok, vol = solid_report(g.mesh)
        check(f"{kind}: change one corner → the solid follows",
              ok and near(vol, W * H - 3 * cut - big, 1e-5), f"V={vol}")
        width = next(c for c in ctrl.sketch()["cons"] if near(c.get("v", 0), W))
        ctrl.set_value(width["id"], 9.0)
        ok, vol = solid_report(host.feature_groups(vp.scene, ctrl.doc)[feat["id"]].mesh)
        check(f"{kind}: the overall width still drives it",
              ctrl.result.ok and ok and near(vol, 9.0 * H - 3 * cut - big, 1e-5),
              f"{ctrl.result.text} V={vol}")

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
