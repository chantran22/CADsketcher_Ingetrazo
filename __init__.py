# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD Sketcher for IngeTrazo — constraint-based sketches that drive solids.

A port of the idea of the Blender add-on CAD_Sketcher (hlorus): draw points,
lines, arcs and circles on a workplane, tie them down with geometric and
dimensional constraints, and let a solver hold the drawing to them. Here the
sketch then becomes an Extrude, a Loft or a Sweep — and stays its source:
edit the sketch (change one dimension) and the solid is rebuilt.

Entry point: ``setup(app)`` (IngeTrazo extension API 2). Open it from
Extensions ▸ CAD Sketcher, or the «CAD Sketcher» tab of the side tray.

    model.py       the document data (JSON in the .igz)
    solver.py      the constraint solver (NumPy)
    convert.py     sketch → closed profiles / paths
    features.py    extrude and loft geometry
    host.py        IngeTrazo's engine: groups, undo, Follow Me sweep
    controller.py  state + operations      tool.py   viewport tool + overlay
    panel.py       the side panel
"""
from __future__ import annotations

import logging

__version__ = "0.1.0"

log = logging.getLogger("ingetrazo.plugins.cad_sketcher")

_instance = None


def setup(app) -> None:
    """Called once by IngeTrazo when the main window is built."""
    global _instance
    try:
        from .controller import Controller
        from .panel import SketcherPanel
        from .tool import Overlay, SketchTool

        ctrl = Controller(app)
        SketchTool(ctrl)                                  # registers as ctrl.tool
        panel = SketcherPanel(ctrl)
        dock = app.add_panel("CAD Sketcher", panel)
        app.add_overlay(Overlay(ctrl))
        app.on_document_changed(ctrl.reload)

        def show() -> None:
            if dock is not None:
                app.show_panel(dock)

        def new_sketch(plane: str) -> None:
            show()
            ctrl.new_sketch(plane)

        menu = app.add_menu("CAD Sketcher")
        if menu is not None:
            menu.addAction("Open panel", show)
            menu.addSeparator()
            for plane, label in (("XY", "New sketch on XY (plan)"),
                                 ("XZ", "New sketch on XZ (front)"),
                                 ("YZ", "New sketch on YZ (side)")):
                menu.addAction(label, lambda p=plane: new_sketch(p))
            menu.addAction("Finish sketch", ctrl.finish)
        _instance = (ctrl, panel, dock)
        log.info("CAD Sketcher %s ready", __version__)
    except Exception:  # noqa: BLE001 — never keep the host from starting
        log.exception("CAD Sketcher failed to start")
