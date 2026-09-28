"""Display window: microscope image and spectrum, intended for the second screen.

It neither talks to the hardware nor decides anything: the main window passes it
the data and connects to its widgets. Closing it only hides it; the program is
closed from the control window, which knows how to switch the laser off and warm
up the CCD.
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QSizePolicy, QSplitter,
                               QToolBar, QVBoxLayout, QWidget)

from .widgets import LASER, MUTED, STYLESHEET, TEAL, estop_button, titled_box


class ViewWindow(QMainWindow):
    def __init__(self, title: str):
        super().__init__()
        self.setWindowTitle(title)
        self.setStyleSheet(STYLESHEET)
        self._allow_close = False

        # Top bar: laser stop and emission strip, here too because this is the window
        # people look at while measuring.
        bar = QToolBar("Display")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.btn_estop = estop_button("Laser off  (F12)")
        bar.addWidget(self.btn_estop)
        bar.addSeparator()
        self.lbl_laser = QLabel()
        self.lbl_laser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(self.lbl_laser)
        self.set_laser_state(None)

        # Microscope image.
        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.image_view.getView().setBackgroundColor("#20252b")
        self.target = pg.TargetItem(pos=(0, 0), size=22, movable=True,
                                    pen=pg.mkPen(LASER, width=2))
        self.image_view.getView().addItem(self.target)
        self.target.hide()

        # Spectrum.
        pg.setConfigOption("foreground", "#3d4650")
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "Intensity", units="counts")
        self.plot.setLabel("bottom", "Raman shift", units="cm⁻¹")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.bg_curve = self.plot.plot(pen=pg.mkPen("#9aa3ad", width=1, style=Qt.DashLine))
        self.curve = self.plot.plot(pen=pg.mkPen(TEAL, width=1.4))
        self.lbl_spec_info = QLabel("No spectrum. Connect the spectrometer and press Acquire.")
        self.lbl_spec_info.setStyleSheet(f"color:{MUTED};")
        self.lbl_cursor = QLabel("")
        self.lbl_cursor.setStyleSheet(f"color:{MUTED};")
        info_row = QHBoxLayout()
        info_row.addWidget(self.lbl_spec_info, 1)
        info_row.addWidget(self.lbl_cursor)
        spec_container = QWidget()
        sv = QVBoxLayout(spec_container)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addLayout(info_row)
        sv.addWidget(self.plot)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.addWidget(titled_box("Microscope", self.image_view))
        self.splitter.addWidget(titled_box("Spectrum", spec_container))
        self.splitter.setSizes([500, 500])
        self.setCentralWidget(self.splitter)

    def set_laser_state(self, emitting: bool | None, power: str = "") -> None:
        if emitting:
            self.lbl_laser.setText(f"  Laser emitting · {power} · wear eye protection")
            self.lbl_laser.setStyleSheet(f"background:{LASER}; color:white; font-weight:700; "
                                         "border-radius:4px; padding:6px;")
        else:
            self.lbl_laser.setText("  Laser disconnected" if emitting is None else "  Laser not emitting")
            self.lbl_laser.setStyleSheet(f"color:{MUTED}; padding:6px;")

    def close_for_real(self) -> None:
        self._allow_close = True
        self.close()

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
        else:
            self.hide()
            event.ignore()
