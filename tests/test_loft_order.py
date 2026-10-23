"""The loft goes through its sections in the order they are TICKED.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_loft_order.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from Cad_sketcher.panel import _FeatureDialog  # noqa: E402
from core.solids import solid_report  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

check = U.check


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance

    # three squares: Sec1 at z=0, Sec2 at z=6, Sec3 at z=3 (made out of order)
    secs = []
    for name, z, half in (("Sec1", 0.0, 1.0), ("Sec2", 6.0, 0.5), ("Sec3", 3.0, 0.8)):
        ctrl.new_sketch("XY", offset=z, name=name)
        s = ctrl.sketch()
        ids = [model.add_point(ctrl.doc, s, u * half, v * half)["id"]
               for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for i in range(4):
            model.add_line(ctrl.doc, s, ids[i], ids[(i + 1) % 4])
        ctrl.commit()
        secs.append(s)
    ctrl.finish()
    s1, s2, s3 = secs

    def tick(dlg, sid, on=True):
        lst = dlg.sections
        for i in range(lst.count()):
            if lst.item(i).data(Qt.UserRole) == sid:
                lst.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    dlg = _FeatureDialog(panel, ctrl, "loft", None)
    for s in (s1, s3, s2):
        tick(dlg, s["id"])
    texts = [dlg.sections.item(i).text().strip() for i in range(3)]
    check("ticked Sec1, Sec3, Sec2 → that is the order",
          dlg.params() == {"sketches": [s1["id"], s3["id"], s2["id"]]}, str(texts))
    check("the list numbers them and spells the run",
          texts == ["1.  Sec1", "3.  Sec2", "2.  Sec3"]
          and dlg.sequence.text() == "Loft: Sec1  →  Sec3  →  Sec2", str(texts))
    check("the list itself does not reorder", [dlg.sections.item(i).data(
        Qt.UserRole) for i in range(3)] == [s1["id"], s2["id"], s3["id"]])
    tick(dlg, s3["id"], False)
    tick(dlg, s3["id"])
    check("untick + tick moves one to the end",
          dlg.params()["sketches"] == [s1["id"], s2["id"], s3["id"]])
    tick(dlg, s2["id"], False)
    tick(dlg, s3["id"], False)
    tick(dlg, s3["id"])
    tick(dlg, s2["id"])

    loft = ctrl.add_feature("loft", **dlg.params())
    check("the feature keeps the ticked order",
          loft["sketches"] == [s1["id"], s3["id"], s2["id"]])
    panel.refresh()
    check("the solids list shows it", "Sec1 → Sec3 → Sec2" in panel.features.item(0).text(),
          panel.features.item(0).text())
    g = host.feature_groups(vp.scene, ctrl.doc)[loft["id"]]
    ok, vol = solid_report(g.mesh)
    # frustums 0→3 (2×2 → 1.6×1.6) and 3→6 (1.6 → 1): h/3 (A1 + A2 + √(A1 A2))
    want = 3 / 3 * (4 + 2.56 + 3.2) + 3 / 3 * (2.56 + 1 + 1.6)
    check("…and so does the solid: bottom → middle → top, no fold back",
          ok and abs(vol - want) < 1e-6, f"V={vol} want={want}")

    edit = _FeatureDialog(panel, ctrl, "loft", loft)
    check("editing shows the saved order", edit.params()["sketches"]
          == [s1["id"], s3["id"], s2["id"]]
          and edit.sequence.text() == "Loft: Sec1  →  Sec3  →  Sec2")

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
