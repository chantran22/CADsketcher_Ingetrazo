"""Trim, Offset, Revolve and the folding panel sections.

Run:  %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe tests\\test_edit.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui as U  # noqa: E402  (installs the engine, the stand-in viewport)

import Cad_sketcher  # noqa: E402
from Cad_sketcher import convert, host, model  # noqa: E402
from Cad_sketcher.panel import _FeatureDialog, _Section  # noqa: E402
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
    ctrl.auto = False                       # plain geometry: no automatic ties

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

    def line(a, b):
        ctrl.set_mode("line")
        click(*a)
        click(*b)
        tool.on_cancel(vp)
        return ents("line")[-1]

    def group_of(feat):
        return host.feature_groups(vp.scene, ctrl.doc).get(feat["id"])

    # ---- trim -------------------------------------------------------------------
    print("trim")
    ctrl.new_sketch("XY", name="Trim")
    a = line((0, 0), (4, 0))
    b = line((2, -1), (2, 1))
    check("T key", tool.on_key(vp, Qt.Key_T, Qt.NoModifier) and ctrl.mode == "trim")
    tool.on_hover(U.ctx(vp, 3, 0))
    check("hover shows what would go", ctrl.hover == ("ent", a["id"]))
    click(3, 0)                              # the stretch right of the crossing
    check("trim to a crossing", ends(a) == [(0.0, 0.0), (2.0, 0.0)]
          and len(ents("line")) == 2 and ctrl.result.ok, str(ends(a)))
    tie = ctrl.sketch()["cons"][-1]
    check("the cut end is held on the cutter",
          tie["t"] == "coincident" and tie["e"] == [a["p2"], b["id"]])
    click(2, 0.5)                            # the upper half of the vertical
    check("second trim shares the point", b["p2"] == a["p2"]
          and ends(b) == [(2.0, -1.0), (2.0, 0.0)], str(ends(b)))

    long = line((0, 3), (6, 3))
    line((2, 2.5), (2, 3.5))
    line((4, 2.5), (4, 3.5))
    dim = model.add_constraint(ctrl.doc, ctrl.sketch(), "distance",
                               [long["id"]], 6.0)
    ctrl.set_mode("trim")
    n = len(ents("line"))
    far = long["p2"]
    click(3, 3)                              # the middle, between two crossings
    new = ents("line")[-1]
    check("trim the middle: two pieces", len(ents("line")) == n + 1
          and ends(long) == [(0.0, 3.0), (2.0, 3.0)]
          and ends(new) == [(4.0, 3.0), (6.0, 3.0)] and new["p2"] == far,
          f"{ends(long)} {ends(new)}")
    check("its length dimension became the overall length",
          set(dim["e"]) == {long["p1"], far} and ctrl.result.ok, str(dim["e"]))
    n = len(ents())
    click(5, 3)                              # nothing crosses this piece now
    check("no crossing: the piece is deleted", len(ents("line")) == 5
          and len(ents()) < n)
    click(1, 1)
    check("a click on nothing is refused", "Click a line" in ctrl.message)

    # circle + chord → a closed D, the classic use of trim
    ctrl.new_sketch("XY", name="D")
    ctrl.set_mode("circle")
    click(0, 0)
    click(1, 0)
    chord = line((-2, 0), (2, 0))
    circle = ents("circle")[0]
    ctrl.set_mode("trim")
    click(0, 1)                              # the upper half of the circle
    check("circle becomes an arc", circle["t"] == "arc" and "r" not in circle
          and near(pt(circle["p1"])[0], -1.0) and near(pt(circle["p2"])[0], 1.0),
          str(circle))
    click(-1.5, 0)
    click(1.5, 0)
    loops, paths = convert.walk(ctrl.sketch(), 32)
    check("trimmed pieces close into one outline",
          len(loops) == 1 and not paths and ends(chord) == [(-1.0, 0.0), (1.0, 0.0)]
          and {chord["p1"], chord["p2"]} == {circle["p1"], circle["p2"]},
          f"{len(loops)} loops {len(paths)} paths")
    feat = ctrl.add_feature("extrude", sketch=ctrl.active, distance=1.0)
    want = 16 * math.sin(math.pi / 16) / 2.0          # half a 32-gon, r = 1
    ok, vol = solid_report(group_of(feat).mesh)
    check("…and extrude", ok and near(vol, want, 1e-6), f"V={vol} want={want}")
    ctrl.set_mode("circle")
    click(5, 5)
    click(5.5, 5)
    ctrl.set_mode("trim")
    click(5.5, 5)
    check("a circle nothing crosses is refused", "two crossings" in ctrl.message)

    # ---- offset -----------------------------------------------------------------
    print("offset")
    sk = ctrl.new_sketch("XY", name="Offset")
    ctrl.set_mode("rect")
    click(0, 0)
    click(4, 2)
    check("O key", tool.on_key(vp, Qt.Key_O, Qt.NoModifier)
          and ctrl.mode == "offset")
    click(2, 0)                              # pick the outline by its bottom side
    check("outline picked", len(tool.picks) == 1 and "eid" in tool.picks[0])
    tool.on_hover(U.ctx(vp, 2, 0.5))
    prev = tool.preview_uv()
    check("preview follows the cursor", len(prev) == 4
          and sorted(p for seg in prev for p in seg)[0] == (0.5, 0.5), str(prev[:1]))
    tool.on_click(U.ctx(vp, 2, 0.5))         # inside, 0.5 from the side
    inner = ents("line")[4:]
    xs = sorted({round(p[0], 9) for e in inner for p in ends(e)})
    ys = sorted({round(p[1], 9) for e in inner for p in ends(e)})
    check("inner outline", len(inner) == 4 and xs == [0.5, 3.5]
          and ys == [0.5, 1.5] and tool.picks == [], f"{xs} {ys}")
    par = [c for c in ctrl.sketch()["cons"] if c["t"] == "parallel"]
    check("stays parallel to its source", len(par) == 4 and ctrl.result.ok)
    regs = convert.profile(ctrl.sketch())
    check("gives a hollow profile", len(regs) == 1 and len(regs[0]["holes"]) == 1)
    feat = ctrl.add_feature("extrude", sketch=sk["id"], distance=1.0)
    ok, vol = solid_report(group_of(feat).mesh)
    check("hollow section extrudes", ok and near(vol, 8.0 - 3.0), f"V={vol}")

    click(2, 0)                              # same outline, now outward, typed
    tool.on_hover(U.ctx(vp, 2, -0.3))
    check("typed distance, side from the cursor",
          tool.on_value(vp, 0.25) and len(ents("line")) == 12)
    outer = ents("line")[8:]
    xs = sorted({round(p[0], 9) for e in outer for p in ends(e)})
    check("outer outline", xs == [-0.25, 4.25], str(xs))
    n = len(ents())
    click(0.5, 1.0)                          # the 3 × 1 inner outline…
    tool.on_hover(U.ctx(vp, 2.0, 1.0))
    tool.on_click(U.ctx(vp, 2.0, 1.0))       # …pushed 1.5 inward: impossible
    check("too large is refused", "too large" in ctrl.message.lower()
          and len(ents()) == n, ctrl.message)

    # rounded corner: the arc keeps its centre, the radius shrinks
    ctrl.new_sketch("XY", name="Round")
    ctrl.set_mode("rect")
    click(0, 0)
    click(4, 2)
    corner = ents("line")[1]["p2"]
    ctrl.bevel(corner, 0.6)
    arc = ents("arc")[0]
    ctrl.set_mode("offset")
    click(2, 0)
    tool.on_hover(U.ctx(vp, 2, 0.2))
    tool.on_click(U.ctx(vp, 2, 0.2))
    arc2 = ents("arc")[-1]
    by = model.ents(ctrl.sketch())
    check("offset of a filleted outline",
          len(ents("arc")) == 2 and arc2["c"] == arc["c"]
          and near(model.radius(by, arc2), 0.4) and ctrl.result.ok
          and len(convert.walk(ctrl.sketch())[0]) == 2,
          f"r={model.radius(by, arc2)}")
    ctrl.new_sketch("XY", name="Rings")
    ctrl.set_mode("circle")
    click(0, 0)
    click(1, 0)
    ctrl.set_mode("offset")
    click(1, 0)
    tool.on_hover(U.ctx(vp, 1.5, 0))
    check("circle preview", len(tool.preview_uv()) == 1)
    tool.on_click(U.ctx(vp, 1.5, 0))
    cs = ents("circle")
    check("concentric circle", len(cs) == 2 and cs[1]["c"] == cs[0]["c"]
          and near(cs[1]["r"], 1.5))
    U.paint(app)
    check("overlay alive", not app.overlays[0]._failed)

    # ---- revolve ----------------------------------------------------------------
    print("revolve")
    ctrl.auto = True
    sk = ctrl.new_sketch("XY", name="Ring")
    ctrl.set_mode("rect")
    click(1, 0)
    click(2, 1)
    ring = 16 * math.sin(math.pi / 16) * (4 - 1) * 1.0     # 32-gon ring, h = 1
    feat = ctrl.add_feature("revolve", sketch=sk["id"], axis="y", angle=360.0)
    ok, vol = solid_report(group_of(feat).mesh)
    check("full turn is a solid", ok and near(vol, ring, 1e-6),
          f"{feat.get('error')} V={vol} want={ring}")
    ctrl.update_feature(feat["id"], angle=90.0)
    ok, vol = solid_report(group_of(feat).mesh)
    check("quarter turn has its two cut faces", ok and near(vol, ring / 4, 1e-6),
          f"V={vol}")
    ctrl.update_feature(feat["id"], angle=-90.0)
    ok, vol = solid_report(group_of(feat).mesh)
    check("negative angle turns the other way", ok and near(vol, ring / 4, 1e-6))
    # THE point: the sketch drives it
    width = ctrl.sketch()["ents"]
    ctrl.update_feature(feat["id"], angle=360.0)
    ctrl.sel = [ents("line")[0]["id"]]
    ctrl.constrain("distance", value=2.0)                  # the ring gets wider
    xs = sorted({round(p[0], 6) for e in ents("line") for p in ends(e)})
    ok, vol = solid_report(group_of(feat).mesh)
    grown = 16 * math.sin(math.pi / 16) * (xs[1] ** 2 - xs[0] ** 2)
    check("edit the sketch → revolve follows", ok and len(xs) == 2
          and near(xs[1] - xs[0], 2.0) and near(vol, grown, 1e-5),
          f"xs={xs} V={vol} want={grown}")
    del width
    ctrl.update_feature(feat["id"], axis="x")
    check("axis through the profile's edge: still a solid",
          solid_report(group_of(feat).mesh)[0] and not feat.get("error"),
          str(feat.get("error")))

    # an axis drawn in the sketch (construction line), and a refusal
    axis = line((-1, -1), (-1, 3))
    ctrl.sel = [axis["id"]]
    ctrl.toggle_flag("con")
    ctrl.update_feature(feat["id"], axis=axis["id"], angle=360.0)
    ok, vol = solid_report(group_of(feat).mesh)
    lo, hi = xs[0] + 1.0, xs[1] + 1.0
    check("about a construction line", ok and near(
        vol, 16 * math.sin(math.pi / 16) * (hi * hi - lo * lo), 1e-5),
        f"{feat.get('error')} V={vol}")
    cut = line((xs[0] + 0.5, -1), (xs[0] + 0.5, 3))
    ctrl.update_feature(feat["id"], axis=cut["id"])
    check("axis through the profile is refused",
          "crosses the revolve axis" in feat.get("error", ""), feat.get("error", ""))
    dlg = _FeatureDialog(panel, ctrl, "revolve", feat)
    check("dialog offers the sketch's lines as axes",
          dlg.axis.count() == 2 + len(ents("line"))
          and dlg.params()["axis"] == cut["id"]
          and dlg.params()["angle"] == 360.0, str(dlg.params()))
    panel.refresh()
    check("panel lists it", any("⟳" in panel.features.item(i).text()
                                for i in range(panel.features.count())))

    # ---- folding sections --------------------------------------------------------
    print("sections")
    secs = {s.header.text(): s for s in panel.findChildren(_Section)}
    check("sections", set(secs) >= {"Sketches", "Draw", "Modify", "Constraints",
                                    "Dimensions", "Solids from sketches"},
          str(sorted(secs)))
    s = secs["Constraints"]
    before = s.is_expanded()
    s.set_expanded(True)
    check("expanded: arrow down, buttons there",
          s.header.arrowType() == Qt.DownArrow and not s.body.isHidden())
    s.header.click()
    check("click the header: folded, arrow right",
          s.header.arrowType() == Qt.RightArrow and s.body.isHidden()
          and not s.is_expanded())
    check("folding is remembered", not _Section("x", "constraints").is_expanded())
    s.header.click()
    check("click again: back", not s.body.isHidden()
          and s.header.arrowType() == Qt.DownArrow)
    s.set_expanded(before)                  # leave the user's own choice alone
    ctrl.finish()
    check("no sketch: tools greyed, headers still fold",
          not secs["Modify"].body.isEnabled() and secs["Modify"].header.isEnabled())
    check("new tools are in the panel",
          {b.property("mode") for b in panel.modes.buttons()}
          >= {"trim", "offset", "fillet", "chamfer"})

    print(f"\nALL {len(U.PASSED)} CHECKS PASSED")


if __name__ == "__main__":
    main()
