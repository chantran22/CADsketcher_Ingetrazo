# SPDX-License-Identifier: GPL-3.0-or-later
"""The side-tray panel: sketches, drawing tools, constraints, features."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
    QWidget,
)

from . import host, model
from .controller import fmt_len
from .model import SketchError

TOOLS = (("select", "Select", "S"), ("move", "Move", "M"),
         ("point", "Point", "P"), ("line", "Line", "L"),
         ("rect", "Rectangle", "R"), ("circle", "Circle", "C"),
         ("arc", "Arc", "A"), ("crect", "Centre rect", "B"),
         ("slot", "Slot", "U"), ("semi", "Semicircle", "H"),
         ("ref", "Reference", "E"))
MODIFY = (("fillet", "Fillet", "F"), ("chamfer", "Chamfer", "K"),
          ("trim", "Trim", "T"), ("extend", "Extend", "X"),
          ("offset", "Offset", "O"))

#: (label, type, axis, shortcut, what to select)
CONSTRAINTS = (
    ("Coincident", "coincident", None, "Shift+C"),
    ("Horizontal", "horizontal", None, "Shift+H"),
    ("Vertical", "vertical", None, "Shift+V"),
    ("Parallel", "parallel", None, "Shift+P"),
    ("Perpendicular", "perpendicular", None, "Shift+N"),
    ("Equal", "equal", None, "Shift+E"),
    ("Tangent", "tangent", None, "Shift+T"),
    ("Midpoint", "midpoint", None, "Shift+M"),
    ("Symmetric", "symmetric", None, "Shift+S"),
    ("Ratio…", "ratio", None, "Shift+R"),
)
DIMENSIONS = (
    ("Distance…", "distance", None, "Alt+D"),
    # Not «Horizontal… / Vertical…»: with the Constraints block folded they
    # were taken for the geometric Horizontal / Vertical (a distance, asked
    # for a value, instead of lining two points up).
    ("Horiz. distance…", "distance", "h", "Alt+H"),
    ("Vert. distance…", "distance", "v", "Alt+V"),
    ("Radius…", "radius", None, "Alt+R"),
    ("Diameter…", "diameter", None, "Alt+O"),
    ("Angle…", "angle", None, "Alt+A"),
)


_MEMORY: dict = {}


def load_setting(key: str, default):
    """A panel preference (folded sections, the grid). Kept in IngeTrazo's
    settings store — or only in memory when ``CAD_SKETCHER_NO_SETTINGS`` is
    set, which the tests do so they never read or write the user's own."""
    if os.environ.get("CAD_SKETCHER_NO_SETTINGS"):
        return _MEMORY.get(key, default)
    try:
        v = QSettings("IngeTrazo", "CadSketcherPlugin").value(key)
    except Exception:  # noqa: BLE001
        return default
    if v is None:
        return default
    if isinstance(default, bool):
        return str(v).lower() in ("true", "1")
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def store_setting(key: str, value) -> None:
    if os.environ.get("CAD_SKETCHER_NO_SETTINGS"):
        _MEMORY[key] = value
        return
    try:
        QSettings("IngeTrazo", "CadSketcherPlugin").setValue(key, value)
    except Exception:  # noqa: BLE001
        pass


def eye_icon(on: bool, color: QColor) -> QIcon:
    """An eye, drawn (no font or image file needed): open when ``on``,
    dimmed and struck through when not."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    c = QColor(color)
    c.setAlpha(255 if on else 110)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(c, 2.6))
    lid = QPainterPath()
    lid.moveTo(3, 16)
    lid.quadTo(16, 2, 29, 16)
    lid.quadTo(16, 30, 3, 16)
    p.drawPath(lid)
    p.setBrush(c)
    p.drawEllipse(QPointF(16, 16), 4.5, 4.5)
    if not on:
        p.drawLine(QPointF(6, 27), QPointF(26, 5))
    p.end()
    return QIcon(pm)


class _Section(QWidget):
    """A titled block that folds: click the header (▸ / ▾) to hide or show
    what is under it. Whether it is folded is remembered between sessions."""

    def __init__(self, title: str, key: str, expanded: bool = True) -> None:
        super().__init__()
        self._key = f"sections/{key}"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setStyleSheet(
            "QToolButton { border: none; text-align: left; font-weight: bold;"
            " padding: 4px 2px; }")
        self.header.toggled.connect(self.set_expanded)
        lay.addWidget(self.header)
        self.body = QWidget()
        lay.addWidget(self.body)
        expanded = load_setting(self._key, bool(expanded))
        self.header.setChecked(expanded)
        self.set_expanded(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, on: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(bool(on))
        if self.header.isChecked() != bool(on):
            self.header.setChecked(bool(on))
        store_setting(self._key, bool(on))


class _SketchDialog(QDialog):
    def __init__(self, parent, name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("New sketch")
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Name:", self.name)
        self.plane = QComboBox()
        self.plane.addItems(["XY (plan)", "XZ (front)", "YZ (side)"])
        form.addRow("Workplane:", self.plane)
        unit, k = host.unit()
        self.k = k
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e6, 1e6)
        self.offset.setDecimals(4)
        self.offset.setSuffix(f" {unit}")
        self.offset.setToolTip("Distance of the workplane from the origin, "
                               "along its normal — the height of a loft section.")
        form.addRow("Offset:", self.offset)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)


class _PlacementDialog(QDialog):
    """Origin X, Y, Z and rotation about X, Y, Z of a sketch, with what the
    result faces shown live."""

    def __init__(self, parent, sk: dict) -> None:
        super().__init__(parent)
        self.sk = sk
        self.setWindowTitle(f"Placement of «{sk['name']}»")
        form = QFormLayout(self)
        unit, self.k = host.unit()
        origin, angles = model.placement(sk)
        self.origin, self.angles = [], []
        for axis, value in zip("XYZ", origin):
            w = QDoubleSpinBox()
            w.setRange(-1e7, 1e7)
            w.setDecimals(4)
            w.setSuffix(f" {unit}")
            w.setValue(value * self.k)
            w.setToolTip("Where the sketch's origin (its fixed purple point) "
                         "is in the model.")
            form.addRow(f"Origin {axis}:", w)
            self.origin.append(w)
        for axis, value, what in zip("XYZ", angles, (
                "red", "green", "blue")):
            w = QDoubleSpinBox()
            w.setRange(-360.0, 360.0)
            w.setDecimals(3)
            w.setSuffix(" °")
            w.setValue(value)
            w.setToolTip(f"Turn about the {what} {axis} axis of the model.")
            w.valueChanged.connect(self._show)
            form.addRow(f"Rotate about {axis} ({what}):", w)
            self.angles.append(w)
        self.facing = QLabel()
        form.addRow("Faces:", self.facing)
        note = QLabel(
            "Angles turn the plane the sketch was made on — first about X, "
            "then Y, then Z — around the sketch's origin. A sketch made on YZ "
            "faces along X: about Y tilts it up or down, about Z turns it left "
            "or right, about X spins it in its own plane. To stand a section "
            "square to a path that rises at a slope, tilt it by that slope "
            "(e.g. −30.17° about Y for 1250 up over 2150 along X).")
        note.setWordWrap(True)
        form.addRow(note)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                               | QDialogButtonBox.Reset)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.Reset).setToolTip(
            "Angles back to 0: the plane the sketch was made on.")
        box.button(QDialogButtonBox.Reset).clicked.connect(
            lambda: [w.setValue(0.0) for w in self.angles])
        form.addRow(box)
        self._show()

    def values(self) -> tuple:
        """``(origin in metres, angles in degrees)``."""
        return ([w.value() / self.k for w in self.origin],
                [w.value() for w in self.angles])

    def _show(self) -> None:
        _x, _y, n = model.placed_axes(self.sk, [w.value() for w in self.angles])
        names = [("X", n[0]), ("Y", n[1]), ("Z", n[2])]
        big = max(names, key=lambda p: abs(p[1]))
        hint = f"  (mostly {'+' if big[1] > 0 else '−'}{big[0]})" \
            if abs(big[1]) < 0.9999 else f"  (straight along {'+' if big[1] > 0 else '−'}{big[0]})"
        self.facing.setText(f"normal ({n[0]:+.4f}, {n[1]:+.4f}, {n[2]:+.4f})"
                            + hint)


class _DuplicateDialog(QDialog):
    """Name and place of a sketch's copy."""

    PLACES = (("Same plane as the original", None), ("XY (plan)", "XY"),
              ("XZ (front)", "XZ"), ("YZ (side)", "YZ"))

    def __init__(self, parent, sk: dict, name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Duplicate «{sk['name']}»")
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Name:", self.name)
        self.place = QComboBox()
        for text, _p in self.PLACES:
            self.place.addItem(text)
        self.place.setToolTip("Where the copy lies. On another plane the shape "
                              "keeps its own coordinates (its origin at that "
                              "plane's origin).")
        form.addRow("Place on:", self.place)
        unit, self.k = host.unit()
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-1e6, 1e6)
        self.offset.setDecimals(4)
        self.offset.setSuffix(f" {unit}")
        self.offset.setToolTip("Along the plane's normal: from the original "
                               "(same plane) or from the origin (XY / XZ / YZ). "
                               "0 = exactly on top of the original.")
        form.addRow("Offset:", self.offset)
        note = QLabel("The copy has all the constraints and dimensions of the "
                      "original, and is independent of it.")
        note.setWordWrap(True)
        form.addRow(note)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def values(self) -> tuple:
        """``(name, offset in metres, plane or None)``."""
        return (self.name.text().strip(), self.offset.value() / self.k,
                self.PLACES[self.place.currentIndex()][1])


class _PatternDialog(QDialog):
    """Count and spacing of a linear or a circular pattern."""

    def __init__(self, parent, kind: str) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(f"{kind.capitalize()} pattern")
        form = QFormLayout(self)
        unit, self.k = host.unit()
        self.count = QSpinBox()
        self.count.setRange(2, 500)
        self.count.setValue(4 if kind == "linear" else 6)
        self.count.setToolTip("How many in all, the original included.")
        form.addRow("Count:", self.count)
        if kind == "linear":
            self.du, self.dv = QDoubleSpinBox(), QDoubleSpinBox()
            for w, label, value in ((self.du, "Spacing along X:", 1.0),
                                    (self.dv, "Spacing along Y:", 0.0)):
                w.setRange(-1e6, 1e6)
                w.setDecimals(4)
                w.setSuffix(f" {unit}")
                w.setValue(value * self.k)
                form.addRow(label, w)
        else:
            self.angle = QDoubleSpinBox()
            self.angle.setRange(-360.0, 360.0)
            self.angle.setDecimals(3)
            self.angle.setSuffix(" °")
            self.angle.setValue(360.0)
            self.angle.setToolTip("360 = evenly all the way round.")
            form.addRow("Total angle:", self.angle)
            note = QLabel("Centre: the point selected last, else the sketch "
                          "origin.")
            note.setWordWrap(True)
            form.addRow(note)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def values(self) -> tuple:
        if self.kind == "linear":
            return (self.count.value(), self.du.value() / self.k,
                    self.dv.value() / self.k)
        return (self.count.value(), self.angle.value())


class _FeatureDialog(QDialog):
    """Parameters of one feature — new, or being edited."""

    def __init__(self, parent, ctrl, kind: str, feat: dict | None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(("Edit " if feat else "New ") + kind)
        feat = feat or {}
        sketches = ctrl.doc["sketches"]
        form = QFormLayout(self)
        unit, self.k = host.unit()

        def combo(current):
            box = QComboBox()
            for sk in sketches:
                box.addItem(sk["name"], sk["id"])
            i = box.findData(current if current is not None else ctrl.active)
            box.setCurrentIndex(max(i, 0))
            return box

        if kind in ("extrude", "face", "revolve"):
            self.sketch = combo(feat.get("sketch"))
            form.addRow("Sketch:", self.sketch)
        if kind == "revolve":
            self.axis = QComboBox()
            self.axis.setToolTip("The workplane's own axes (the red / green "
                                 "dashes from the sketch origin), or a line of "
                                 "the sketch — mark it Construction.")
            form.addRow("Axis:", self.axis)

            def fill_axes() -> None:
                want = self.axis.currentData() if self.axis.count() \
                    else feat.get("axis", "x")
                self.axis.clear()
                self.axis.addItem("Sketch X axis", "x")
                self.axis.addItem("Sketch Y axis", "y")
                sk = model.sketch(ctrl.doc, self.sketch.currentData())
                n = 0
                for e in (sk or {}).get("ents", []):
                    if e["t"] == "line":
                        n += 1
                        self.axis.addItem(
                            f"Line {n}" + (" (construction)" if e.get("con")
                                           else ""), e["id"])
                self.axis.setCurrentIndex(max(self.axis.findData(want), 0))
            fill_axes()
            self.sketch.currentIndexChanged.connect(lambda _i: fill_axes())
            self.angle = QDoubleSpinBox()
            self.angle.setRange(-360.0, 360.0)
            self.angle.setDecimals(3)
            self.angle.setSuffix(" °")
            self.angle.setValue(feat.get("angle", 360.0))
            form.addRow("Angle:", self.angle)
            note = QLabel("The profile must stay on one side of the axis "
                          "(it may touch it).")
            note.setWordWrap(True)
            form.addRow(note)
        if kind == "extrude":
            self.distance = QDoubleSpinBox()
            self.distance.setRange(-1e6, 1e6)
            self.distance.setDecimals(4)
            self.distance.setSuffix(f" {unit}")
            self.distance.setValue(feat.get("distance", 1.0) * self.k)
            self.distance.setToolTip("Along the workplane normal; negative = "
                                     "the other way.")
            form.addRow("Distance:", self.distance)
            self.symmetric = QCheckBox("Half to each side of the sketch")
            self.symmetric.setChecked(bool(feat.get("symmetric")))
            form.addRow(self.symmetric)
        if kind == "sweep":
            self.profile = combo(feat.get("profile"))
            form.addRow("Profile (closed):", self.profile)
            self.path = combo(feat.get("path"))
            form.addRow("Path:", self.path)
            note = QLabel("Draw the profile on a plane ACROSS the start of "
                          "the path (e.g. profile on XZ, path on XY).")
            note.setWordWrap(True)
            form.addRow(note)
        if kind == "loft":
            # The loft runs through the sections in the order they are TICKED
            # (numbered in the list): tick Sec1, Sec3, Sec2 and that is the
            # loft. The list itself keeps the sketches' order, so nothing
            # jumps around under the mouse.
            self.sections = QListWidget()
            known = {s["id"] for s in sketches}
            self.order = [sid for sid in feat.get("sketches", []) if sid in known]
            self.names = {s["id"]: s["name"] for s in sketches}
            for sk in sketches:
                it = QListWidgetItem(sk["name"])
                it.setData(Qt.UserRole, sk["id"])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if sk["id"] in self.order
                                 else Qt.Unchecked)
                self.sections.addItem(it)
            self.sequence = QLabel()
            self.sequence.setWordWrap(True)
            self._number()
            self.sections.itemChanged.connect(self._ticked)
            form.addRow(QLabel("Sections — tick them in the order the loft "
                               "goes through them (untick and tick again to "
                               "move one to the end):"))
            form.addRow(self.sections)
            form.addRow(self.sequence)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def params(self) -> dict:
        if self.kind == "face":
            return {"sketch": self.sketch.currentData()}
        if self.kind == "extrude":
            return {"sketch": self.sketch.currentData(),
                    "distance": self.distance.value() / self.k,
                    "symmetric": self.symmetric.isChecked()}
        if self.kind == "revolve":
            return {"sketch": self.sketch.currentData(),
                    "axis": self.axis.currentData(),
                    "angle": self.angle.value()}
        if self.kind == "sweep":
            return {"profile": self.profile.currentData(),
                    "path": self.path.currentData()}
        return {"sketches": list(self.order)}

    def _ticked(self, it) -> None:
        sid = it.data(Qt.UserRole)
        on = it.checkState() == Qt.Checked
        if on and sid not in self.order:
            self.order.append(sid)
        elif not on and sid in self.order:
            self.order.remove(sid)
        self._number()

    def _number(self) -> None:
        """«1. Sec1», «2. Sec3»… in the list, and the whole run spelt out."""
        self.sections.blockSignals(True)
        for i in range(self.sections.count()):
            it = self.sections.item(i)
            sid = it.data(Qt.UserRole)
            n = self.order.index(sid) + 1 if sid in self.order else None
            it.setText(f"{n}.  {self.names[sid]}" if n else
                       f"     {self.names[sid]}")
        self.sections.blockSignals(False)
        self.sequence.setText(
            "Loft: " + "  →  ".join(self.names[s] for s in self.order)
            if self.order else "Tick two or more sketches.")


class SketcherPanel(QWidget):
    def __init__(self, ctrl) -> None:
        super().__init__()
        self.ctrl = ctrl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)

        # ---- sketches
        box = _Section("Sketches", "sketches")
        v = QVBoxLayout(box.body)
        self.sketches = QListWidget()
        self.sketches.setMaximumHeight(110)
        self.sketches.itemDoubleClicked.connect(lambda _i: self._edit())
        self.sketches.setToolTip("Double-click a sketch to edit it.")
        v.addWidget(self.sketches)
        row = QHBoxLayout()
        self.new_btn = QToolButton()
        self.new_btn.setText("New ▾")
        self.new_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.new_btn)
        menu.addAction("On a standard plane…", self._new)
        menu.addAction("On a face of the model — click it…", ctrl.pick_face)
        menu.addAction("On the selected face", self._new_on_face)
        self.new_btn.setMenu(menu)
        row.addWidget(self.new_btn)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.clicked.connect(self._edit_or_finish)
        row.addWidget(self.edit_btn)
        for text, slot, tip in (
                ("Duplicate…", self._duplicate,
                 "A copy of the selected sketch with all its constraints and "
                 "dimensions — on the same plane, moved along it, or on "
                 "another plane."),
                ("Rename", self._rename, ""), ("Delete", self._delete, "")):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if tip:
                b.setToolTip(tip)
            row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        self.view_btn = QPushButton("Align view")
        self.view_btn.setToolTip("Look straight at the sketch's plane.")
        self.view_btn.clicked.connect(ctrl.align_view)
        row.addWidget(self.view_btn)
        self.place_btn = QPushButton("Placement…")
        self.place_btn.setToolTip(
            "Where the sketch is and how it is turned: origin X, Y, Z and "
            "rotation about X, Y, Z. Its solids follow.")
        self.place_btn.clicked.connect(self._placement)
        row.addWidget(self.place_btn)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(box)

        # ---- drawing tools
        checked = "QToolButton:checked { background: #2f6fd0; color: white; }"
        sec = _Section("Draw", "draw")
        self.tools_box = sec.body
        self.tools_box.setStyleSheet(checked)
        grid = QGridLayout(self.tools_box)
        self.modes = QButtonGroup(self)
        self.modes.setExclusive(True)
        for i, (mode, text, key) in enumerate(TOOLS):
            grid.addWidget(self._mode_button(mode, text, key), i // 4, i % 4)
        self.auto = QCheckBox("Automatic constraints")
        self.auto.setChecked(ctrl.auto)
        self.auto.setToolTip("While drawing: keep level/plumb lines horizontal/"
                             "vertical and points dropped on a curve on it.")
        self.auto.toggled.connect(lambda on: setattr(ctrl, "auto", bool(on)))
        grid.addWidget(self.auto, 3, 0, 1, 4)
        row = QHBoxLayout()
        self.grid_show = QCheckBox("Grid")
        self.grid_show.setToolTip("Draw a grid on the sketch plane.")
        self.grid_snap = QCheckBox("Snap")
        self.grid_snap.setToolTip("Clicks land on the grid (G). Existing points "
                                  "and curves still win when you click on them.")
        unit, self._k = host.unit()
        self.grid_step = QDoubleSpinBox()
        self.grid_step.setDecimals(4)
        self.grid_step.setRange(0.0001, 1e6)
        self.grid_step.setSuffix(f" {unit}")
        self.grid_step.setToolTip("Grid spacing.")
        self._load_grid()
        self.grid_show.toggled.connect(lambda _on: self._grid_changed())
        self.grid_snap.toggled.connect(lambda _on: self._grid_changed())
        self.grid_step.editingFinished.connect(self._grid_changed)
        for w in (self.grid_show, self.grid_snap, self.grid_step):
            row.addWidget(w)
        grid.addLayout(row, 4, 0, 1, 4)
        lay.addWidget(sec)

        # ---- modify tools
        sec = _Section("Modify", "modify")
        sec.body.setStyleSheet(checked)
        grid = QGridLayout(sec.body)
        for i, (mode, text, key) in enumerate(MODIFY):
            grid.addWidget(self._mode_button(mode, text, key), 0, i)
        for r, items in enumerate((
                (("Copy", "Copy the selection (Ctrl+C).", ctrl.copy),
                 ("Paste", "Paste, then click where it goes (Ctrl+V).",
                  self._paste),
                 ("Mirror X", "A mirrored copy of the selection across the "
                  "sketch X axis.", lambda: ctrl.mirror("x")),
                 ("Mirror Y", "A mirrored copy of the selection across the "
                  "sketch Y axis.", lambda: ctrl.mirror("y")),
                 ("Mirror line", "Mirror across the line selected LAST; the "
                  "copy stays symmetric when the sketch is edited.",
                  lambda: ctrl.mirror(None))),
                (("Linear pattern…", "Repeat the selection in a row.",
                  lambda: self._pattern("linear")),
                 ("Circular pattern…", "Repeat the selection around a point: "
                  "the point selected LAST, else the sketch origin.",
                  lambda: self._pattern("circular")))), start=1):
            row = QHBoxLayout()
            for text, tip, slot in items:
                b = QPushButton(text)
                b.setToolTip(tip)
                b.clicked.connect(lambda _c=False, f=slot: f())
                row.addWidget(b)
            grid.addLayout(row, r, 0, 1, len(MODIFY))
        lay.addWidget(sec)
        self._sketch_only = [self.tools_box, sec.body]

        # ---- constraints
        for title, key_, items in (("Constraints", "constraints", CONSTRAINTS),
                                   ("Dimensions", "dimensions", DIMENSIONS)):
            sec = _Section(title, key_)
            sec.setToolTip("Select entities in the sketch, then click.")
            grid = QGridLayout(sec.body)
            for i, (text, ctype, axis, key) in enumerate(items):
                b = QPushButton(text)
                b.setToolTip(f"{model._NEEDS[ctype].capitalize()}  ({key})")
                b.clicked.connect(lambda _c=False, t=ctype, a=axis:
                                  ctrl.constrain(t, a))
                grid.addWidget(b, i // 2, i % 2)
            lay.addWidget(sec)
            self._sketch_only.append(sec.body)
        sec = _Section("Selection and constraint list", "selection")
        self.cons_box = sec.body
        self._sketch_only.append(sec.body)
        grid = QGridLayout(self.cons_box)
        row = QHBoxLayout()
        for text, tip, slot in (
                ("Construction", "Solved, but left out of the solid.",
                 lambda: ctrl.toggle_flag("con")),
                ("Fix point", "A fixed point cannot move.",
                 lambda: ctrl.toggle_flag("fixed")),
                ("Chain", "Select the whole connected outline of the last "
                 "selected line or arc (or double-click it).",
                 lambda: ctrl.select_chain()),
                ("Delete", "Delete the selection (Del).", ctrl.delete_selected)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        grid.addLayout(row, grid.rowCount(), 0, 1, 2)
        self.cons = QListWidget()
        self.cons.setMaximumHeight(120)
        self.cons.setToolTip("Click to select a constraint; double-click a "
                             "dimension to change its value.")
        self.cons.itemClicked.connect(self._con_clicked)
        self.cons.itemDoubleClicked.connect(
            lambda it: ctrl.edit_value(it.data(Qt.UserRole)))
        grid.addWidget(self.cons, grid.rowCount(), 0, 1, 2)
        lay.addWidget(sec)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        # ---- features
        box = _Section("Solids from sketches", "solids")
        v = QVBoxLayout(box.body)
        row = QHBoxLayout()
        for kind, text, tip in (
                ("extrude", "Extrude", "Pull a closed sketch along its normal."),
                ("revolve", "Revolve", "Turn a closed sketch about an axis."),
                ("loft", "Loft", "A skin through two or more closed sketches."),
                ("sweep", "Sweep", "A closed sketch along the path of another."),
                ("face", "Face", "The sketch as a flat face.")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=kind: self._feature(k))
            row.addWidget(b)
        v.addLayout(row)
        self.features = QListWidget()
        self.features.setMaximumHeight(110)
        self.features.setToolTip("Double-click a solid to change it.")
        self.features.itemDoubleClicked.connect(lambda _i: self._feature_edit())
        v.addWidget(self.features)
        row = QHBoxLayout()
        for text, slot in (("Edit", self._feature_edit),
                           ("Delete", self._feature_delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addWidget(QLabel("Circle segments:"))
        self.segments = QSpinBox()
        self.segments.setRange(8, 256)
        self.segments.setSingleStep(8)
        self.segments.editingFinished.connect(
            lambda: ctrl.set_segments(self.segments.value()))
        row.addWidget(self.segments)
        v.addLayout(row)
        lay.addWidget(box)

        self.hint = QLabel(
            "Edit a sketch, draw, select entities and constrain them. Solids "
            "follow their sketch: change a dimension and the model updates. "
            "Undo takes back both.")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        lay.addStretch(1)

        # A click on a panel button must not take the keyboard from the 3D
        # view: the sketch shortcuts (L, C, G, Ctrl+C…) only reach our tool
        # while the view has the focus — elsewhere IngeTrazo's own fire.
        # (Qt hands a click's focus to the nearest ancestor that takes it,
        # and a scroll area does — so the containers and lists opt out too.)
        for w in [self, scroll, scroll.viewport(), body,
                  *self.findChildren(QAbstractButton),
                  *self.findChildren(QListWidget),
                  *self.findChildren(_Section)]:
            w.setFocusPolicy(Qt.NoFocus)
        for sec_ in self.findChildren(_Section):
            sec_.body.setFocusPolicy(Qt.NoFocus)

        ctrl.changed.connect(self.refresh)
        self.refresh()

    def _mode_button(self, mode: str, text: str, key: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setCheckable(True)
        b.setToolTip(f"{text} ({key})")
        b.setProperty("mode", mode)
        b.setSizePolicy(self.edit_btn.sizePolicy())
        b.clicked.connect(lambda _c=False, m=mode: self.ctrl.set_mode(m))
        self.modes.addButton(b)
        return b

    def _eye(self, listw, item, on: bool, toggle, tip: str,
             enabled: bool = True) -> QToolButton:
        """An eye at the right end of a list row (as in IngeTrazo's Levels):
        open = shown, struck through = hidden. The rest of the row still
        selects and double-clicks as before."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.addStretch(1)
        b = QToolButton()
        b.setAutoRaise(True)
        b.setIcon(eye_icon(on, self.palette().color(self.foregroundRole())))
        b.setIconSize(QSize(16, 16))
        b.setFixedSize(22, 18)
        b.setStyleSheet("QToolButton { border: none; background: transparent; }")
        b.setToolTip(tip)
        b.setEnabled(enabled)
        b.setProperty("eye_on", bool(on))
        # Deferred: the handler rebuilds this list, button included.
        b.clicked.connect(lambda _c=False: QTimer.singleShot(0, toggle))
        lay.addWidget(b)
        listw.setItemWidget(item, row)
        return b

    # ---- refresh ----------------------------------------------------------------
    def refresh(self) -> None:
        ctrl = self.ctrl
        doc = ctrl.doc
        sk = ctrl.sketch()
        current = self._sketch_id()
        self.sketches.blockSignals(True)
        self.sketches.clear()
        for s in doc["sketches"]:
            it = QListWidgetItem(("✎ " if s["id"] == ctrl.active else "   ")
                                 + s["name"])
            it.setData(Qt.UserRole, s["id"])
            self.sketches.addItem(it)
            on = bool(s.get("visible", True))
            self._eye(self.sketches, it, on,
                      lambda sid=s["id"], on=on: ctrl.set_visible(sid, not on),
                      "Shown while it is not being edited — click to hide."
                      if on else "Hidden while it is not being edited — click "
                      "to show.")
            if s["id"] == (ctrl.active if ctrl.active is not None else current):
                self.sketches.setCurrentItem(it)
        self.sketches.blockSignals(False)
        self.edit_btn.setText("Finish" if sk is not None else "Edit")
        self._show_grid()                       # G toggles it from the viewport
        self.save_grid()
        for w in self._sketch_only:
            w.setEnabled(sk is not None)
        self.view_btn.setEnabled(sk is not None)
        for b in self.modes.buttons():
            b.setChecked(sk is not None and b.property("mode") == ctrl.mode)

        # Rows are updated in place while the constraints are the same ones:
        # rebuilding the list on the first click of a double-click would
        # destroy the item the second click is meant for.
        cons = sk["cons"] if sk is not None else []
        failed = set(ctrl.result.failed) if ctrl.result else set()
        shown = [self.cons.item(i).data(Qt.UserRole)
                 for i in range(self.cons.count())]
        if shown != [c["id"] for c in cons]:
            self.cons.clear()
            for c in cons:
                it = QListWidgetItem()
                it.setData(Qt.UserRole, c["id"])
                self.cons.addItem(it)
        normal = self.status.palette().windowText()
        for i, c in enumerate(cons):
            it = self.cons.item(i)
            it.setText(("⚠ " if c["id"] in failed else "")
                       + model.describe(sk, c, fmt_len))
            it.setForeground(Qt.red if c["id"] in failed else normal)
            it.setSelected(c["id"] in ctrl.sel_cons)
        if sk is None:
            self.status.setText("")
        else:
            res = ctrl.result
            text = res.text if res else ""
            n = len(ctrl.sel) + len(ctrl.sel_cons)
            if n:
                text += f"  ·  {n} selected"
            color = "#c62828" if res and not res.ok else \
                "#2e7d32" if res and res.dof == 0 else "#1565c0"
            self.status.setText(f'<span style="color:{color}">{text}</span>')

        keep = self._feature_id()
        self.features.clear()
        for f in doc["features"]:
            it = QListWidgetItem(self._feature_text(f))
            it.setData(Qt.UserRole, f["id"])
            if f.get("error") or f.get("warn"):
                it.setForeground(Qt.red if f.get("error") else Qt.darkYellow)
                it.setToolTip(f.get("error") or f["warn"])
            self.features.addItem(it)
            on = ctrl.solid_visible(f["id"])
            self._eye(self.features, it, bool(on),
                      lambda fid=f["id"], on=on:
                      ctrl.set_solid_visible(fid, not on),
                      "This solid is not in the model (not built, deleted, "
                      "or used up by a solid operation). Edit it to build "
                      "it again." if on is None
                      else "Shown in the model — click to hide." if on
                      else "Hidden in the model — click to show.",
                      enabled=on is not None)
            if f["id"] == keep:
                self.features.setCurrentItem(it)
        self.segments.blockSignals(True)
        self.segments.setValue(int(doc.get("segments", 32)))
        self.segments.blockSignals(False)

    def _feature_text(self, f: dict) -> str:
        doc = self.ctrl.doc

        def nm(sid):
            sk = model.sketch(doc, sid)
            return sk["name"] if sk else "?"
        if f["t"] == "extrude":
            what = f"{nm(f.get('sketch'))} × {fmt_len(f.get('distance', 0))}"
        elif f["t"] == "revolve":
            axis = {"x": "X", "y": "Y"}.get(f.get("axis"), "line")
            what = f"{nm(f.get('sketch'))} ⟳ {f.get('angle', 360):g}° about {axis}"
        elif f["t"] == "loft":
            what = " → ".join(nm(s) for s in f.get("sketches", []))
        elif f["t"] == "sweep":
            what = f"{nm(f.get('profile'))} along {nm(f.get('path'))}"
        else:
            what = nm(f.get("sketch"))
        return ("⚠ " if f.get("error") or f.get("warn") else "") \
            + f"{f['name']}:  {what}" \
            + ("   (no longer in the model)" if f.get("detached") else "")

    # ---- sketches ---------------------------------------------------------------
    def _sketch_id(self):
        it = self.sketches.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _feature_id(self):
        it = self.features.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _new(self) -> None:
        ctrl = self.ctrl
        dlg = _SketchDialog(self, ctrl._unique("Sketch", ctrl.doc["sketches"]))
        if dlg.exec() != QDialog.Accepted:
            return
        plane = ("XY", "XZ", "YZ")[dlg.plane.currentIndex()]
        ctrl.new_sketch(plane, dlg.offset.value() / dlg.k,
                        dlg.name.text().strip() or None)

    # ---- grid, paste, patterns ----------------------------------------------------
    def _load_grid(self) -> None:
        g = self.ctrl.grid
        g["show"] = load_setting("grid/show", False)
        g["snap"] = load_setting("grid/snap", False)
        g["step"] = max(1e-6, load_setting("grid/step", float(g["step"])))
        self._grid_saved = dict(g)
        self._show_grid()

    def _show_grid(self) -> None:
        g = self.ctrl.grid
        for w, v in ((self.grid_show, g["show"]), (self.grid_snap, g["snap"])):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        if not self.grid_step.hasFocus():
            self.grid_step.blockSignals(True)
            self.grid_step.setValue(g["step"] * self._k)
            self.grid_step.blockSignals(False)

    def _grid_changed(self) -> None:
        g = self.ctrl.grid
        g["show"] = self.grid_show.isChecked()
        g["snap"] = self.grid_snap.isChecked()
        g["step"] = max(1e-6, self.grid_step.value() / self._k)
        self.save_grid()
        self.ctrl.viewport.update()

    def save_grid(self) -> None:
        if self.ctrl.grid != self._grid_saved:
            for k, v in self.ctrl.grid.items():
                store_setting(f"grid/{k}", v)
            self._grid_saved = dict(self.ctrl.grid)

    def _paste(self) -> None:
        if self.ctrl.clip is None:
            self.ctrl.say("Nothing copied yet — select, then Copy.")
        else:
            self.ctrl.set_mode("paste")

    def _pattern(self, kind: str) -> None:
        ctrl = self.ctrl
        if not ctrl.sel:
            ctrl.say("Select what to repeat first.")
            return
        dlg = _PatternDialog(self, kind)
        if dlg.exec() != QDialog.Accepted:
            return
        if kind == "linear":
            ctrl.pattern_linear(*dlg.values())
        else:
            ctrl.pattern_circular(*dlg.values())

    def _new_on_face(self) -> None:
        try:
            self.ctrl.new_sketch_on_face()
        except SketchError as exc:
            self.ctrl.say(str(exc))

    def _edit(self) -> None:
        sid = self._sketch_id()
        if sid is not None:
            self.ctrl.edit(sid)

    def _edit_or_finish(self) -> None:
        if self.ctrl.sketch() is not None:
            self.ctrl.finish()
        else:
            self._edit()

    def _placement(self) -> None:
        ctrl = self.ctrl
        sk = model.sketch(ctrl.doc, ctrl.active if ctrl.active is not None
                          else self._sketch_id())
        if sk is None:
            ctrl.say("Select the sketch to place in the list first.")
            return
        dlg = _PlacementDialog(self, sk)
        if dlg.exec() == QDialog.Accepted:
            ctrl.place_sketch(sk["id"], *dlg.values())

    def _duplicate(self) -> None:
        ctrl = self.ctrl
        sk = model.sketch(ctrl.doc, ctrl.active if ctrl.active is not None
                          else self._sketch_id())
        if sk is None:
            ctrl.say("Select the sketch to duplicate in the list first.")
            return
        dlg = _DuplicateDialog(self, sk, ctrl._unique(f"{sk['name']} copy",
                                                      ctrl.doc["sketches"]))
        if dlg.exec() == QDialog.Accepted:
            ctrl.duplicate_sketch(sk["id"], *dlg.values())

    def _rename(self) -> None:
        sk = model.sketch(self.ctrl.doc, self._sketch_id())
        if sk is None:
            return
        name, ok = QInputDialog.getText(self, "Rename sketch", "Name:",
                                        text=sk["name"])
        if ok:
            self.ctrl.rename_sketch(sk["id"], name)

    def _delete(self) -> None:
        sid = self._sketch_id()
        if sid is not None:
            self.ctrl.delete_sketch(sid)

    def _con_clicked(self, it) -> None:
        self.ctrl.toggle("con", it.data(Qt.UserRole))

    # ---- features ---------------------------------------------------------------
    def _feature(self, kind: str, feat: dict | None = None) -> None:
        ctrl = self.ctrl
        if not ctrl.doc["sketches"]:
            ctrl.say("Draw a sketch first.")
            return
        dlg = _FeatureDialog(self, ctrl, kind, feat)
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.params()
        if kind == "loft" and len(params["sketches"]) < 2:
            ctrl.say("A loft needs two or more sketches.")
            return
        if feat is None:
            ctrl.add_feature(kind, **params)
        else:
            ctrl.update_feature(feat["id"], **params)

    def _feature_edit(self) -> None:
        feat = model.find(self.ctrl.doc["features"], self._feature_id())
        if feat is not None:
            self._feature(feat["t"], feat)

    def _feature_delete(self) -> None:
        fid = self._feature_id()
        if fid is not None:
            self.ctrl.delete_feature(fid)
