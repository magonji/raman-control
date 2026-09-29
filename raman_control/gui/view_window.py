"""Display window: microscope image and spectra, intended for the second screen.

The image sits on the left, where its roughly square shape uses the height of the
screen; on the right, the spectra saved during the session sit above the spectrum
being measured, with linked wavelength axes so that they can be compared.

It neither talks to the hardware nor decides anything: the main window passes it
the data and connects to its widgets. Closing it only hides it; the program is
closed from the control window, which knows how to switch the laser off and warm
up the CCD.
"""
from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy,
                               QSplitter, QToolBar, QVBoxLayout, QWidget)

from .widgets import INK, LASER, MUTED, SERIES, STYLESHEET, TEAL, estop_button, titled_box


def _spectrum_plot() -> pg.PlotWidget:
    plot = pg.PlotWidget(background="w")
    plot.showGrid(x=True, y=True, alpha=0.15)
    plot.setLabel("left", "Intensity", units="counts")
    plot.setLabel("bottom", "Raman shift", units="cm⁻¹")
    plot.getAxis("left").enableAutoSIPrefix(False)
    plot.getAxis("bottom").enableAutoSIPrefix(False)
    return plot


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"color:{MUTED};")
    return label


class ViewWindow(QMainWindow):
    clear_saved_clicked = Signal()

    def __init__(self, title: str):
        super().__init__()
        self.setWindowTitle(title)
        self.setStyleSheet(STYLESHEET)
        self._allow_close = False
        self._x_units = "cm⁻¹"

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

        pg.setConfigOption("foreground", "#3d4650")

        # Spectra saved during the session.
        self.saved_plot = _spectrum_plot()
        self.saved_legend = self.saved_plot.addLegend(offset=(-10, 10), labelTextColor=INK,
                                                      brush=pg.mkBrush(255, 255, 255, 220))
        self.lbl_saved_info = _muted("Spectra appear here as they are saved.")
        self.lbl_saved_cursor = _muted()
        self.btn_clear_saved = QPushButton("Clear")
        self.btn_clear_saved.setEnabled(False)
        self.btn_clear_saved.clicked.connect(self.clear_saved_clicked)
        saved_box = self._plot_with_header(self.saved_plot, self.lbl_saved_info,
                                           self.lbl_saved_cursor, self.btn_clear_saved)

        # Spectrum being measured.
        self.plot = _spectrum_plot()
        self.bg_curve = self.plot.plot(pen=pg.mkPen("#9aa3ad", width=1, style=Qt.DashLine))
        self.curve = self.plot.plot(pen=pg.mkPen(TEAL, width=1.4))
        self.lbl_spec_info = _muted("No spectrum. Connect the spectrometer and press Acquire.")
        self.lbl_cursor = _muted()
        live_box = self._plot_with_header(self.plot, self.lbl_spec_info, self.lbl_cursor)

        # Zooming or panning either plot moves both along the wavelength axis.
        self.saved_plot.setXLink(self.plot)
        self._mouse_proxies = [
            pg.SignalProxy(p.scene().sigMouseMoved, rateLimit=30,
                           slot=lambda ev, p=p, lbl=lbl: self._on_mouse(ev, p, lbl))
            for p, lbl in ((self.plot, self.lbl_cursor), (self.saved_plot, self.lbl_saved_cursor))]

        spectra = QSplitter(Qt.Vertical)
        spectra.addWidget(titled_box("Saved spectra", saved_box))
        spectra.addWidget(titled_box("Current spectrum", live_box))
        spectra.setSizes([500, 500])

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(titled_box("Microscope", self.image_view))
        self.splitter.addWidget(spectra)
        self.splitter.setSizes([900, 1000])
        self.setCentralWidget(self.splitter)

    @staticmethod
    def _plot_with_header(plot: pg.PlotWidget, info: QLabel, cursor: QLabel,
                          button: QPushButton | None = None) -> QWidget:
        row = QHBoxLayout()
        row.addWidget(info, 1)
        row.addWidget(cursor)
        if button is not None:
            row.addWidget(button)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(row)
        v.addWidget(plot)
        return box

    def set_axis(self, name: str, units: str) -> None:
        self._x_units = units
        for plot in (self.plot, self.saved_plot):
            plot.setLabel("bottom", name, units=units)

    def set_saved(self, entries: list[tuple[str, object, object, str]]) -> None:
        """Redraws the saved spectra: a list of (label, x, y, colour), oldest first."""
        self.saved_plot.clear()
        self.saved_legend.clear()
        for label, x, y, color in entries:
            self.saved_plot.plot(x, y, pen=pg.mkPen(color, width=1.5), name=label)
        self.btn_clear_saved.setEnabled(bool(entries))
        if entries:
            self.lbl_saved_info.setText(f"{len(entries)} shown · the latest {len(SERIES)} are kept")
        else:
            self.lbl_saved_info.setText("Spectra appear here as they are saved.")

    def _on_mouse(self, event, plot: pg.PlotWidget, label: QLabel) -> None:
        pos = event[0]
        if plot.sceneBoundingRect().contains(pos):
            p = plot.getPlotItem().vb.mapSceneToView(pos)
            label.setText(f"{p.x():.1f} {self._x_units} · {p.y():.0f}")

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
