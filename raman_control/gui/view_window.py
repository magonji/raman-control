"""Display window: microscope image and spectra, intended for the second screen.

The image sits on the left, where its roughly square shape uses the height of the
screen; on the right, the spectra saved during the session sit above the spectrum
being measured, with linked wavelength axes so that they can be compared. Under
the image, two small charts follow the measured laser power and the CCD
temperature over the last few minutes.

It neither talks to the hardware nor decides anything: the main window passes it
the data and connects to its widgets. Closing it asks the control window to close
the program, which knows how to switch the laser off and warm up the CCD (and may
be cancelled, leaving both windows open).
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
                               QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .widgets import (CONTINUOUS_SVG, INK, LASER, LASER_SVG, MUTED, PHOTO_SVG, PLAY_SVG,
                      POWER_SVG, SERIES, SPECTRUM_SVG, STOP_SVG, STYLESHEET, TEAL,
                      DualValueSlider, TrafficLight, ValueSlider, estop_square_button,
                      format_seconds, set_tip,
                      square_button, titled_box)

CCD_TEMP = "#8e1b1b"  # dark red, distinct from the red used for errors


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


class TrendPlot(QWidget):
    """Small chart of one quantity over the last few minutes, newest on the right."""

    WINDOW_S = 300.0
    GAP_S = 5.0  # longer without readings (disconnected) breaks the line

    def __init__(self, name: str, units: str, decimals: int, color: str):
        super().__init__()
        self.name, self.units, self.decimals = name, units, decimals
        self._points: deque[tuple[float, float]] = deque()
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "Minutes ago")
        self.plot.setLabel("left", units)
        self.plot.setXRange(-self.WINDOW_S / 60, 0, padding=0)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.setMinimumHeight(110)
        # Closed box: axes on all four sides, numbers only on the left and bottom.
        self.plot.getPlotItem().showAxes(True, showValues=(True, False, False, True))
        self.curve = self.plot.plot(pen=pg.mkPen(color, width=2), connect="finite")
        self.lbl_value = QLabel(f"{name}: —")
        self.lbl_value.setStyleSheet(f"color:{INK}; font-weight:600;")
        self.lbl_cursor = _muted()
        self._proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30,
                                     slot=self._on_mouse)
        row = QHBoxLayout()
        row.addWidget(self.lbl_value, 1)
        row.addWidget(self.lbl_cursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(row)
        v.addWidget(self.plot)

    def add(self, value: float | None) -> None:
        if value is None:
            return
        self._points.append((time.monotonic(), float(value)))
        self.lbl_value.setText(f"{self.name}: {value:.{self.decimals}f} {self.units}")
        self.redraw()

    def redraw(self) -> None:
        now = time.monotonic()
        while self._points and now - self._points[0][0] > self.WINDOW_S:
            self._points.popleft()
        if not self._points:
            self.curve.setData([], [])
            return
        t, y = (np.array(c, dtype=float) for c in zip(*self._points))
        gaps = np.flatnonzero(np.diff(t) > self.GAP_S) + 1
        t = np.insert(t, gaps, np.nan)
        y = np.insert(y, gaps, np.nan)
        self.curve.setData((t - now) / 60.0, y)

    def _on_mouse(self, event) -> None:
        pos = event[0]
        if not self.plot.sceneBoundingRect().contains(pos) or not self._points:
            self.lbl_cursor.setText("")
            return
        minutes = self.plot.getPlotItem().vb.mapSceneToView(pos).x()
        now = time.monotonic()
        t, value = min(self._points, key=lambda p: abs((p[0] - now) / 60.0 - minutes))
        ago = now - t
        self.lbl_cursor.setText(f"{ago / 60:.0f} min {ago % 60:02.0f} s ago · "
                                f"{value:.{self.decimals}f} {self.units}")


class ViewWindow(QMainWindow):
    clear_saved_clicked = Signal()
    close_requested = Signal()

    def __init__(self, title: str, max_power_mw: float = 500.0):
        super().__init__()
        self.setWindowTitle(title)
        self.setStyleSheet(STYLESHEET)
        self._allow_close = False
        self._x_units = "cm⁻¹"

        # Top bar: instrument buttons on the left; the laser state and the emergency stop
        # on the right, here too because this is the window people look at while measuring.
        bar = QToolBar("Display")
        bar.setMovable(False)
        self.addToolBar(bar)
        # Button groups 12 px apart; the emergency stop sits alone at the far right.
        self.btn_power = square_button(POWER_SVG, checkable=True)
        bar.addWidget(self.btn_power)
        bar.addWidget(self._gap(12))
        self.btn_emission = square_button(LASER_SVG, checkable=True, laser=True)
        bar.addWidget(self.btn_emission)
        bar.addWidget(self._gap(12))
        self.btn_video = square_button(PLAY_SVG, checkable=True, svg_checked=STOP_SVG)
        bar.addWidget(self.btn_video)
        self.btn_snapshot = square_button(PHOTO_SVG)
        set_tip(self.btn_snapshot, "Save image")
        bar.addWidget(self.btn_snapshot)
        bar.addWidget(self._gap(12))
        self.btn_continuous = square_button(CONTINUOUS_SVG, checkable=True, svg_checked=STOP_SVG)
        bar.addWidget(self.btn_continuous)
        self.btn_acquire = square_button(SPECTRUM_SVG)
        bar.addWidget(self.btn_acquire)
        self.set_all_connected(False)
        self.set_emission_state(None)
        self.set_camera_state(False, False)
        self.set_spectrometer_state(False, False, False, False)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.laser_light = TrafficLight()
        bar.addWidget(self.laser_light)
        bar.addWidget(self._gap(12))
        self.btn_estop = estop_square_button("Laser emergency stop (F12)")
        bar.addWidget(self.btn_estop)
        self.set_laser_state(None)

        # Second row: laser power, exposure and accumulations, mirroring the control
        # window's boxes. Power and exposure are logarithmic: fine at the low end.
        self.addToolBarBreak()
        sliders = QToolBar("Settings")
        sliders.setMovable(False)
        self.addToolBar(sliders)
        self.sld_power = ValueSlider("Laser power", 1.0, max_power_mw,
                                     lambda v: f"{v:.0f} mW", log=True, step=1.0)
        # Exposure and accumulations: a handle for measurements (●) and one for the
        # continuous measurement (◆).
        self.sld_exposure = DualValueSlider("Exposure", 0.5, 300.0, format_seconds, log=True)
        self.sld_accumulations = DualValueSlider("Accumulations", 1, 100,
                                                 lambda v: f"{v:.0f}", step=1.0)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(36)
        for slider in (self.sld_power, self.sld_exposure, self.sld_accumulations):
            h.addWidget(slider, 1)
        sliders.addWidget(row)

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
        # A measurement in progress: its running average, dashed, over the spectrum shown
        # when it started.
        self.partial_curve = self.plot.plot(pen=pg.mkPen(INK, width=1.4, style=Qt.DashLine))
        self.lbl_spec_info = _muted("No spectrum. Connect the spectrometer and press Acquire.")
        self.lbl_cursor = _muted()
        live_box = self._plot_with_header(self.plot, self.lbl_spec_info, self.lbl_cursor)
        # Sample name, on the title line; the same as in the control window: files are
        # named after it.
        self.edit_sample = QLineEdit()
        self.edit_sample.setPlaceholderText("sample name")
        self.edit_sample.setMinimumWidth(260)
        sample = QWidget()
        sample_row = QHBoxLayout(sample)
        sample_row.setContentsMargins(0, 0, 0, 0)
        sample_row.addWidget(_muted("Sample"))
        sample_row.addWidget(self.edit_sample)

        # Zooming or panning either plot moves both along the wavelength axis.
        self.saved_plot.setXLink(self.plot)
        self._mouse_proxies = [
            pg.SignalProxy(p.scene().sigMouseMoved, rateLimit=30,
                           slot=lambda ev, p=p, lbl=lbl: self._on_mouse(ev, p, lbl))
            for p, lbl in ((self.plot, self.lbl_cursor), (self.saved_plot, self.lbl_saved_cursor))]

        spectra = QSplitter(Qt.Vertical)
        spectra.addWidget(titled_box("Saved spectra", saved_box))
        spectra.addWidget(titled_box("Current spectrum", live_box, sample))
        spectra.setSizes([500, 500])

        # Laser power and CCD temperature over the last five minutes, under the image.
        self.trend_power = TrendPlot("Laser power", "mW", 1, LASER)
        self.trend_temp = TrendPlot("CCD temperature", "°C", 1, CCD_TEMP)
        trends = QWidget()
        row = QHBoxLayout(trends)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.trend_power)
        row.addWidget(self.trend_temp)
        # The x axis is "minutes ago", so it moves even when no readings arrive.
        self._trend_timer = QTimer(self, interval=1000)
        self._trend_timer.timeout.connect(self.trend_power.redraw)
        self._trend_timer.timeout.connect(self.trend_temp.redraw)
        self._trend_timer.start()

        left = QSplitter(Qt.Vertical)
        left.addWidget(titled_box("Microscope", self.image_view))
        left.addWidget(titled_box("Last 5 minutes", trends))
        left.setStretchFactor(0, 1)
        left.setSizes([700, 200])

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
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

    @staticmethod
    def _gap(width: int) -> QWidget:
        gap = QWidget()
        gap.setFixedWidth(width)
        return gap

    def set_all_connected(self, any_connected: bool) -> None:
        """The power button shows whether anything is connected, and what a click does."""
        self.btn_power.setChecked(any_connected)
        set_tip(self.btn_power,
                "Disconnect all instruments" if any_connected else "Connect all instruments")

    def set_emission_state(self, emitting: bool | None) -> None:
        """Emission button: usable with the laser connected (emitting not None), green
        while it emits, and its tooltip says what the next click will do."""
        self.btn_emission.setEnabled(emitting is not None)
        self.btn_emission.setChecked(bool(emitting))
        set_tip(self.btn_emission, "Switch laser emission off" if emitting
                else "Switch laser emission on")

    def set_camera_state(self, connected: bool, live: bool) -> None:
        """Video and image buttons: usable with the camera connected; the video one
        shows whether the video is running, and what a click does."""
        self.btn_video.setEnabled(connected)
        self.btn_snapshot.setEnabled(connected)
        self.btn_video.setChecked(connected and live)
        set_tip(self.btn_video, "Stop video" if connected and live else "Start video")

    def set_spectrometer_state(self, connected: bool, acquiring: bool, live: bool,
                               measuring: bool) -> None:
        """Continuous and single-spectrum buttons.

        live: the continuous measurement is on (also while a measurement interrupts
        it, to resume it afterwards). measuring: a measurement is running. While live,
        a spectrum can still be taken; its button is off only during a measurement."""
        live = connected and live
        self.btn_continuous.setEnabled(connected and (not acquiring or live))
        self.btn_continuous.setChecked(live)
        set_tip(self.btn_continuous, "Stop continuous measurement" if live
                else "Start continuous measurement (spectra not saved automatically)")
        self.btn_acquire.setEnabled(connected and not measuring and (not acquiring or live))
        set_tip(self.btn_acquire, "Acquire spectrum (saved automatically; during the "
                                  "continuous measurement, it resumes afterwards)")

    def set_laser_state(self, emitting: bool | None, power: str = "") -> None:
        self.set_emission_state(emitting)
        if emitting is None:
            self.laser_light.set_state("disconnected", "Laser disconnected")
        elif emitting:
            self.laser_light.set_state("emitting",
                                       f"Laser emitting · {power} · wear eye protection")
        else:
            self.laser_light.set_state("idle", "Laser not emitting")

    def close_for_real(self) -> None:
        self._allow_close = True
        self.close()

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
        else:
            # The control window closes the program, and this window with it. Asked once
            # this event is over: Qt ignores closing a window from inside its own close.
            event.ignore()
            QTimer.singleShot(0, self.close_requested.emit)
