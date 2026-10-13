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
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
                               QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .widgets import (CCD_ROWS_SVG, CONTINUOUS_SVG, FIT_NO_RAYLEIGH_SVG, FIT_SVG, INK, LASER, LASER_SVG, MUTED, PHOTO_SVG, PLAY_SVG,
                      POWER_SVG, SERIES, SPECTRUM_SVG, STOP_SVG, STYLESHEET, TEAL,
                      ZOOM_FULL_SVG, ZOOM_IN_SVG, ZOOM_OUT_SVG, axis_zoom_button,
                      AccumulationProgress, DualValueSlider, TrafficLight, ValueSlider, estop_square_button,
                      format_seconds, set_tip,
                      square_button, titled_box)

CCD_TEMP = "#8e1b1b"  # dark red, distinct from the red used for errors


def _spectrum_plot() -> pg.PlotWidget:
    plot = pg.PlotWidget(background="w")
    # Closed box: axes on all four sides, numbers only on the left and bottom.
    plot.getPlotItem().showAxes(True, showValues=(True, False, False, True))
    plot.showGrid(x=True, y=True, alpha=0.15)
    for side in ("top", "right"):  # the grid once, not again from the closing axes
        plot.getAxis(side).setGrid(False)
    plot.getAxis("right").setWidth(6)  # at zero width its line would be clipped
    plot.setLabel("left", "Intensity", units="counts")
    plot.setLabel("bottom", "Raman shift", units="cm⁻¹")
    plot.getAxis("left").enableAutoSIPrefix(False)
    plot.getAxis("bottom").enableAutoSIPrefix(False)
    return plot


SAVED_MODES = ("normal", "dim", "hide")
SAVED_MODE_TEXT = {"normal": ("Normal", "Saved spectra in full colour"),
                   "dim": ("Dimmed", "Saved spectra faded, behind the current one"),
                   "hide": ("Hidden", "Saved spectra hidden, and left out of the zoom")}


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
        # Closed box: axes on all four sides, numbers only on the left and bottom. The
        # right axis gets a few pixels: at zero width its line falls on the widget's
        # edge and is clipped.
        self.plot.getPlotItem().showAxes(True, showValues=(True, False, False, True))
        self.plot.getAxis("right").setWidth(6)
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
    saved_visibility_changed = Signal(int, bool)  # index in the session's list, shown
    remove_saved_requested = Signal(object)       # indices to take off the list
    zoom_manual = Signal()  # a plot was zoomed or panned with the mouse
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
        # Button groups 12 px apart on the left; the laser state and the emergency stop on
        # the right; between them, centred, the progress of a measurement. Both sides get
        # the same width so that the progress bar is centred in the window.
        left = QWidget()
        lrow = QHBoxLayout(left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(2)
        self.btn_power = square_button(POWER_SVG, checkable=True)
        self.btn_emission = square_button(LASER_SVG, checkable=True, laser=True)
        self.btn_video = square_button(PLAY_SVG, checkable=True, svg_checked=STOP_SVG)
        self.btn_snapshot = square_button(PHOTO_SVG)
        set_tip(self.btn_snapshot, "Save image")
        self.btn_continuous = square_button(CONTINUOUS_SVG, checkable=True, svg_checked=STOP_SVG)
        self.btn_acquire = square_button(SPECTRUM_SVG)
        self.btn_ccd_rows = square_button(CCD_ROWS_SVG)
        set_tip(self.btn_ccd_rows, "CCD image: choose the rows read (vertical binning)")
        # Zoom of the spectra: modes that stay on until the plots are zoomed by hand.
        self.btn_zoom_full = square_button(ZOOM_FULL_SVG, checkable=True)
        set_tip(self.btn_zoom_full, "Full view: every spectrum whole (follows new data)")
        self.btn_fit = square_button(FIT_SVG, checkable=True)
        set_tip(self.btn_fit, "Rescale the intensity to what is visible (follows new data)")
        self.btn_fit_nr = square_button(FIT_NO_RAYLEIGH_SVG, checkable=True)
        set_tip(self.btn_fit_nr, "Rescale the intensity to what is visible, leaving out the "
                                 "Rayleigh line (follows new data)")
        self.zoom_buttons = {"full": self.btn_zoom_full, "fit": self.btn_fit,
                             "fit_nr": self.btn_fit_nr}
        for item in (self.btn_power, 12, self.btn_emission, 12, self.btn_video,
                     self.btn_snapshot, 12, self.btn_continuous, self.btn_acquire,
                     self.btn_ccd_rows, 12,
                     self.btn_zoom_full, self.btn_fit, self.btn_fit_nr):
            if isinstance(item, int):
                lrow.addSpacing(item)
            else:
                lrow.addWidget(item)
        lrow.addStretch(1)
        right = QWidget()
        rrow = QHBoxLayout(right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.addStretch(1)
        self.laser_light = TrafficLight()
        rrow.addWidget(self.laser_light)
        rrow.addSpacing(12)
        self.btn_estop = estop_square_button("Laser emergency stop (F12)")
        rrow.addWidget(self.btn_estop)
        side = max(left.sizeHint().width(), right.sizeHint().width())
        left.setFixedWidth(side)
        right.setFixedWidth(side)
        self.progress = AccumulationProgress()
        bar.addWidget(left)
        for widget in (QWidget(), self.progress, QWidget()):
            if widget is not self.progress:
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            bar.addWidget(widget)
        bar.addWidget(right)
        self.set_all_connected(False)
        self.set_emission_state(None)
        self.set_camera_state(False, False)
        self.set_spectrometer_state(False, False, False, False)
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

        # Third row: the spectrograph window (presets and its range in cm⁻¹) and step
        # and glue, mirroring the control window.
        self.addToolBarBreak()
        window_bar = QToolBar("Spectral range")
        window_bar.setMovable(False)
        self.addToolBar(window_bar)
        self.cmb_preset = QComboBox()
        self.cmb_preset.setMinimumWidth(180)
        self.btn_preset = QPushButton("Go")
        self.lbl_window = QLabel("Range: —")
        self.lbl_window.setStyleSheet(f"color:{INK}; font-weight:600;")
        self.spin_glue_from = QDoubleSpinBox()
        self.spin_glue_to = QDoubleSpinBox()
        for spin in (self.spin_glue_from, self.spin_glue_to):
            spin.setRange(-5000.0, 10000.0)
            spin.setDecimals(0)
            spin.setSuffix(" cm⁻¹")
        self.btn_glue = QPushButton("Measure range")
        self.lbl_glue = _muted("")
        wrow = QWidget()
        wh = QHBoxLayout(wrow)
        wh.setContentsMargins(4, 2, 4, 2)
        for item in (_muted("Window"), self.cmb_preset, self.btn_preset, 16, self.lbl_window,
                     36, _muted("Step and glue"), self.spin_glue_from, _muted("to"),
                     self.spin_glue_to, self.btn_glue, self.lbl_glue):
            if isinstance(item, int):
                wh.addSpacing(item)
            else:
                wh.addWidget(item)
        wh.addStretch(1)
        window_bar.addWidget(wrow)

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

        # One plot for every spectrum: those saved during the session, in their colours,
        # under the current one; they can be dimmed or hidden to see the current one.
        self.plot = _spectrum_plot()
        self.bg_curve = self.plot.plot(pen=pg.mkPen("#9aa3ad", width=1, style=Qt.DashLine))
        self.curve = self.plot.plot(pen=pg.mkPen(TEAL, width=1.4))
        # A measurement in progress: its running average, dashed, over the spectrum shown
        # when it started.
        self.partial_curve = self.plot.plot(pen=pg.mkPen(INK, width=1.4, style=Qt.DashLine))
        for item in (self.bg_curve, self.curve, self.partial_curve):
            item.setZValue(10)  # above the saved spectra
        self._saved_items: list[pg.PlotDataItem] = []
        self._saved_entries: list[tuple] = []
        self._saved_mode = "normal"
        self.lbl_spec_info = _muted("No spectrum. Connect the spectrometer and press Acquire.")
        self.lbl_cursor = _muted()
        self.lbl_saved_info = _muted("Saved spectra appear here too.")
        # Every spectrum saved this session, with its colour: tick to show it, select
        # and remove the ones not wanted. (It stands in for a legend, which would cover
        # the plot with many spectra.)
        self.saved_list = QListWidget()
        self.saved_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.saved_list.itemChanged.connect(
            lambda item: self.saved_visibility_changed.emit(
                self.saved_list.row(item), item.checkState() == Qt.Checked))
        self.btn_remove_saved = QPushButton("Remove selected")
        self.btn_remove_saved.setEnabled(False)
        self.btn_remove_saved.clicked.connect(lambda: self.remove_saved_requested.emit(
            sorted(self.saved_list.row(i) for i in self.saved_list.selectedItems())))
        self.saved_list.itemSelectionChanged.connect(
            lambda: self.btn_remove_saved.setEnabled(bool(self.saved_list.selectedItems())))
        # One button that steps through normal → dim → hide → normal.
        self.btn_saved_mode = QPushButton()
        self.btn_saved_mode.setMinimumWidth(130)
        self.btn_saved_mode.clicked.connect(
            lambda: self.set_saved_mode(SAVED_MODES[(SAVED_MODES.index(self._saved_mode) + 1)
                                                    % len(SAVED_MODES)]))
        self.btn_clear_saved = QPushButton("Clear")
        self.btn_clear_saved.setEnabled(False)
        self.btn_clear_saved.clicked.connect(self.clear_saved_clicked)
        spectra_box = self._plot_with_header(self.plot, self.lbl_spec_info, self.lbl_cursor)
        saved_row = QHBoxLayout()
        saved_row.addWidget(self.lbl_saved_info, 1)
        saved_row.addWidget(self.btn_saved_mode)
        saved_row.addSpacing(8)
        saved_row.addWidget(self.btn_clear_saved)
        spectra_box.layout().insertLayout(1, saved_row)
        self.set_saved_mode("normal")
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

        # Ranges are set by apply_zoom, not pyqtgraph's auto-range.
        self.plot.disableAutoRange()
        self.plot.getViewBox().sigRangeChangedManually.connect(lambda *_: self.zoom_manual.emit())
        self._mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30,
                                           slot=lambda ev: self._on_mouse(ev, self.plot,
                                                                          self.lbl_cursor))
        # The list goes under the plot, its spectra side by side in rows to save height.
        self.saved_list.setFlow(QListView.LeftToRight)
        self.saved_list.setWrapping(True)
        self.saved_list.setResizeMode(QListView.Adjust)
        self.saved_list.setSpacing(2)
        list_panel = QWidget()
        lp = QVBoxLayout(list_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<span style='color:{TEAL}'>━━</span> current spectrum"))
        head.addStretch(1)
        head.addWidget(self.btn_remove_saved)
        lp.addLayout(head)
        lp.addWidget(self.saved_list, 1)
        list_panel.setMinimumHeight(70)
        spectra_split = QSplitter(Qt.Vertical)
        spectra_split.addWidget(spectra_box)
        spectra_split.addWidget(list_panel)
        spectra_split.setStretchFactor(0, 1)
        spectra_split.setSizes([700, 110])
        spectra = titled_box("Spectra", spectra_split, sample)

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

    def _plot_with_header(self, plot: pg.PlotWidget, info: QLabel, cursor: QLabel,
                          button: QPushButton | None = None) -> QWidget:
        """The plot under a line of text, with Andor-style axis zoom buttons: by the
        intensity axis, at its foot, and under the wavelength axis, at its start."""
        row = QHBoxLayout()
        row.addWidget(info, 1)
        row.addWidget(cursor)
        if button is not None:
            row.addWidget(button)
        y_buttons = QVBoxLayout()
        y_buttons.setSpacing(2)
        y_buttons.addStretch(1)
        x_buttons = QHBoxLayout()
        x_buttons.setSpacing(2)
        for axis, layout, vertical in (("y", y_buttons, True), ("x", x_buttons, False)):
            name = "vertically" if vertical else "horizontally"
            for svg, factor, what in ((ZOOM_IN_SVG, 1.5, "in"), (ZOOM_OUT_SVG, 1 / 1.5, "out")):
                b = axis_zoom_button(svg, vertical, f"Zoom {what} {name} (or drag the axis "
                                                    "to move along it, wheel on it to zoom)")
                b.clicked.connect(lambda _=False, a=axis, f=factor: self.zoom_axis(a, f))
                layout.addWidget(b)
        y_buttons.addSpacing(34)  # level with the foot of the intensity axis
        x_buttons.addStretch(1)
        grid = QGridLayout()
        grid.setSpacing(2)
        grid.addLayout(y_buttons, 0, 0)
        grid.addWidget(plot, 0, 1)
        grid.addLayout(x_buttons, 1, 1)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(row)
        v.addLayout(grid, 1)
        return box

    def zoom_axis(self, axis: str, factor: float) -> None:
        """Zooms one axis of the spectra plot by factor (>1 in, <1 out): the
        wavelength about the centre of the view, the intensity keeping its foot, so
        that the baseline stays put and the peaks grow. Counts as a manual zoom."""
        vb = self.plot.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        if axis == "x":
            centre, half = (x0 + x1) / 2, (x1 - x0) / 2 / factor
            vb.setXRange(centre - half, centre + half, padding=0)
        else:
            vb.setYRange(y0, y0 + (y1 - y0) / factor, padding=0)
        self.zoom_manual.emit()

    def set_axis(self, name: str, units: str) -> None:
        self._x_units = units
        self.plot.setLabel("bottom", name, units=units)

    def set_zoom_mode(self, mode: str | None) -> None:
        """Checks the button of the zoom mode in force (None: zoomed by hand)."""
        for key, button in self.zoom_buttons.items():
            button.setChecked(key == mode)

    def apply_zoom(self, mode: str | None, exclude=None) -> None:
        """Sets the range of the spectra plot, from the spectra it shows.

        "full": every spectrum whole. "fit" and "fit_nr": the wavelength window stays,
        and the intensity spans what is visible in it; for "fit_nr", leaving out the
        points where exclude(x) is True (the Rayleigh line). Hidden saved spectra do not
        count.
        """
        if mode is None:
            return
        xs, ys = [], []
        for plot in (self.plot,):
            for item in plot.getPlotItem().listDataItems():
                if not item.isVisible():
                    continue
                x, y = item.getData()
                if x is None or y is None or len(x) == 0:
                    continue
                x, y = np.asarray(x, float), np.asarray(y, float)
                ok = np.isfinite(x) & np.isfinite(y)
                xs.append(x[ok])
                ys.append(y[ok])
        if not xs:
            return
        x, y = np.concatenate(xs), np.concatenate(ys)
        if mode == "full":
            self.plot.setXRange(x.min(), x.max(), padding=0.01)
        else:
            lo, hi = self.plot.getViewBox().viewRange()[0]
            keep = (x >= lo) & (x <= hi)
            if mode == "fit_nr" and exclude is not None:
                keep &= ~exclude(x)
            y = y[keep]
            if y.size == 0:
                return
        lo, hi = float(y.min()), float(y.max())
        if hi == lo:  # a flat line: a small range around it
            lo, hi = lo - max(1.0, abs(lo) * 0.05), hi + max(1.0, abs(hi) * 0.05)
        self.plot.setYRange(lo, hi, padding=0.04)

    def set_saved(self, entries: list[tuple[str, object, object, str, bool]]) -> None:
        """Redraws the saved spectra: a list of (label, x, y, colour, shown), oldest
        first, and the list under the plot."""
        for item in self._saved_items:
            self.plot.removeItem(item)
        self._saved_entries = list(entries)
        self._saved_items = []
        self.saved_list.blockSignals(True)
        self.saved_list.clear()
        for label, x, y, color, shown in entries:
            item = pg.PlotDataItem(x, y)
            item.setZValue(0)
            self.plot.addItem(item)
            self._saved_items.append(item)
            swatch = QPixmap(14, 14)
            swatch.fill(QColor(color))
            row = QListWidgetItem(QIcon(swatch), label)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if shown else Qt.Unchecked)
            self.saved_list.addItem(row)
        self.saved_list.blockSignals(False)
        self.btn_remove_saved.setEnabled(False)
        self.btn_clear_saved.setEnabled(bool(entries))
        if entries:
            shown = sum(1 for e in entries if e[4])
            self.lbl_saved_info.setText(f"{len(entries)} saved this session · {shown} shown")
        else:
            self.lbl_saved_info.setText("Saved spectra appear here too.")
        self._style_saved()

    def set_saved_mode(self, mode: str) -> None:
        """normal: saved spectra in full colour; dim: faded and thin, behind the current
        one; hide: not drawn and left out of the zoom."""
        self._saved_mode = mode
        following = SAVED_MODES[(SAVED_MODES.index(mode) + 1) % len(SAVED_MODES)]
        self.btn_saved_mode.setText(f"Saved: {SAVED_MODE_TEXT[mode][0]}")
        self.btn_saved_mode.setToolTip(f"{SAVED_MODE_TEXT[mode][1]}. Click: "
                                       f"{SAVED_MODE_TEXT[following][0].lower()}.")
        self._style_saved()

    def _style_saved(self) -> None:
        for item, (label, _, _, color, shown) in zip(self._saved_items, self._saved_entries):
            if self._saved_mode == "dim":
                c = pg.mkColor(color)
                c.setAlpha(80)
                item.setPen(pg.mkPen(c, width=1))
            else:
                item.setPen(pg.mkPen(color, width=1.5))
            item.setVisible(shown and self._saved_mode != "hide")

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
        self.btn_ccd_rows.setEnabled(connected)
        set_tip(self.btn_acquire, "Acquire spectrum (saved automatically; during the "
                                  "continuous measurement, it resumes afterwards)")

    def set_laser_state(self, emitting: bool | None, power: str = "") -> None:
        self.set_emission_state(emitting)
        if emitting is None:
            self.laser_light.set_state("disconnected", "Laser disconnected")
        elif emitting:
            self.laser_light.set_state("emitting",
                                       f"Laser emitting · {power}")
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
