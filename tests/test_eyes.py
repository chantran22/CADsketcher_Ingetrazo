"""The eyes: hide / show sketches and solids from their lists.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_eyes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402

import Cad_sketcher  # noqa: E402
from Cad_sketcher import host, model  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402

check = U.check


def main():
    app = U.FakeApp()
    vp = app.viewport
    Cad_sketcher.setup(app)
    ctrl, panel, _dock = Cad_sketcher._instance
    tool = ctrl.tool
    panel.resize(360, 900)
    panel.show()

    def click(u, v):
        tool.on_hover(U.ctx(vp, u, v))
        tool.on_click(U.ctx(vp, u, v))

    def eye(listw, row):
        w = listw.itemWidget(listw.item(row))
        return w.findChild(QToolButton)

    def press(listw, row):
        eye(listw, row).click()
        QApplication.processEvents()            # the toggle is deferred

    def ink(u, v):
        """Is something drawn at workplane (u, v)?"""
        img = U.paint(app)
        p = U.px(u, v)
        return img.pixelColor(int(p.x()), int(p.y())) != img.pixelColor(3, 3)

    a = ctrl.new_sketch("XY", name="A")
    ctrl.set_mode("rect")
    click(0, 0)
    click(2, 1)
    b = ctrl.new_sketch("XY", name="B")
    ctrl.set_mode("rect")
    click(0, -2)
    click(2, -1)

    # ---- sketches ----------------------------------------------------------------
    print("sketch eyes")
    check("every sketch row has an eye", panel.sketches.count() == 2
          and all(eye(panel.sketches, i) is not None for i in range(2)))
    check("idle sketch A is drawn", ink(1.0, 1.0))
    press(panel.sketches, 0)
    check("eye hides it", not model.sketch(ctrl.doc, a["id"])["visible"]
          and not ink(1.0, 1.0) and eye(panel.sketches, 0).property("eye_on") is False)
    check("…and it is in the document",
          app.document_data()["sketches"][0]["visible"] is False)
    check("the sketch being edited is always drawn", ink(1.0, -2.0))
    press(panel.sketches, 0)
    check("eye shows it again", ink(1.0, 1.0)
          and eye(panel.sketches, 0).property("eye_on") is True)
    vp.undo()
    check("undo", not ink(1.0, 1.0)
          and eye(panel.sketches, 0).property("eye_on") is False)
    press(panel.sketches, 0)

    # the row itself still works: click selects, double-click edits
    row = panel.sketches.itemWidget(panel.sketches.item(0))
    at = QPointF(20, row.height() / 2)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        QApplication.sendEvent(row, QMouseEvent(
            kind, at, QPointF(row.mapToGlobal(at.toPoint())), Qt.LeftButton,
            Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
            Qt.NoModifier))
    check("row click still selects", panel.sketches.currentRow() == 0)
    panel._edit()
    check("…and edits", ctrl.active == a["id"])

    # ---- solids ------------------------------------------------------------------
    print("solid eyes")
    fa = ctrl.add_feature("extrude", sketch=a["id"], distance=1.0)
    fb = ctrl.add_feature("extrude", sketch=b["id"], distance=2.0)
    ga = host.feature_groups(vp.scene, ctrl.doc)[fa["id"]]
    check("a sketch steps back when a solid is made from it",
          not model.sketch(ctrl.doc, a["id"])["visible"]
          and not model.sketch(ctrl.doc, b["id"])["visible"])
    check("every solid row has an eye", panel.features.count() == 2
          and eye(panel.features, 0).property("eye_on") is True)
    press(panel.features, 0)
    check("eye hides the solid", ga.hidden is True and len(vp.scene.groups) == 2
          and eye(panel.features, 0).property("eye_on") is False
          and eye(panel.features, 1).property("eye_on") is True)
    # editing the sketch rebuilds the solid: it must stay hidden
    ctrl.update_feature(fa["id"], distance=3.0)
    ga2 = host.feature_groups(vp.scene, ctrl.doc)[fa["id"]]
    check("a hidden solid stays hidden when rebuilt",
          ga2 is not ga and ga2.hidden is True
          and eye(panel.features, 0).property("eye_on") is False)
    press(panel.features, 0)
    check("eye shows it again", ga2.hidden is False
          and eye(panel.features, 0).property("eye_on") is True)
    vp.undo()
    check("hide is an undo step, and the eye follows",
          ga2.hidden is True and eye(panel.features, 0).property("eye_on") is False)
    press(panel.features, 0)

    # a solid that could not be built has nothing to hide
    bad = ctrl.add_feature("sweep", profile=a["id"], path=99999)
    check("no group: the eye is greyed", bad.get("error")
          and not eye(panel.features, 2).isEnabled(), str(bad.get("error")))
    press(panel.sketches, 1)
    check("sketch of a solid can be brought out with its eye",
          model.sketch(ctrl.doc, b["id"])["visible"])
    ctrl.finish()
    check("…and is drawn over it", ink(1.0, -2.0))

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
