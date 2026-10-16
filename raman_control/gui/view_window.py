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
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .widgets import (LOAD_SVG, CCD_ROWS_SVG, CONTINUOUS_MARK, CONTINUOUS_SVG, FIT_NO_RAYLEIGH_SVG,
                      FIT_SVG, INK, LASER, LASER_SVG, LIPID_SVG, MUTED, PHOTO_SVG, PLAY_SVG,
                      POWER_SVG, SERIES, SPECTRUM_MODES, SPECTRUM_SVG, STOP_SVG, STYLESHEET,
                      TEAL, ZOOM_FULL_SVG, ZOOM_IN_SVG, ZOOM_OUT_SVG, AccumulationProgress,
                      axis_zoom_button, estop_square_button, NiceSpinBox, quick_values,
                      set_tip, SpectrumSquare, square_button, stepper, titled_box,
                      TrafficLight)

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


def _next_mode(mode: str) -> str:
    """normal → dim → hide → normal."""
    return SPECTRUM_MODES[(SPECTRUM_MODES.index(mode) + 1) % len(SPECTRUM_MODES)]


def _row(*items, stretch_at: int | None = None) -> QWidget:
    """A line of widgets; integers are spacings. With stretch_at, the item at that
    position takes the spare width; otherwise it is left at the end."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 1, 0, 1)
    for i, item in enumerate(items):
        if isinstance(item, int):
            h.addSpacing(item)
        else:
            h.addWidget(item, 1 if i == stretch_at else 0)
    if stretch_at is None:
        h.addStretch(1)
    return row


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
    saved_mode_changed = Signal(int, str)    # index in the session's list, new mode
    remove_saved_requested = Signal(object)  # indices to take off the list
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
        # Acquire; while a measurement runs, Stop (it stops that measurement only).
        self.btn_acquire = square_button(SPECTRUM_SVG, checkable=True, svg_checked=STOP_SVG)
        self.btn_ccd_rows = square_button(CCD_ROWS_SVG)
        self.btn_lipid = square_button(LIPID_SVG, checkable=True)
        set_tip(self.btn_lipid, "Lipid band: height and S/N of every spectrum, as a check of "
                                "its quality (e.g. while focusing)")
        set_tip(self.btn_ccd_rows, "CCD image: choose the rows read (vertical binning)")
        # Zoom of the spectra: modes that stay on until the plots are zoomed by hand.
        self.btn_load = square_button(LOAD_SVG)
        set_tip(self.btn_load, "Load spectra saved before, to compare them on the plot")
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
                     self.btn_ccd_rows, self.btn_lipid, 12,
                     self.btn_load, 12, self.btn_zoom_full, self.btn_fit, self.btn_fit_nr):
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

        # Laser power, exposure and accumulations, mirroring the control window's boxes
        # (they go under the spectra plot): boxes that step through round values with
        # − and +, take any value typed, and quick buttons for the usual ones. Exposure
        # and accumulations have one box for measurements (●) and one for the continuous
        # measurement (◆); the quick buttons set the measurement's.
        self.box_power = NiceSpinBox(1.0, max_power_mw, "mW", integer=True)
        self.lbl_power_measured = _muted("")
        self.box_exp = NiceSpinBox(0.5, 300.0, "s")
        self.box_exp_cont = NiceSpinBox(0.5, 300.0, "s")
        self.box_acc = NiceSpinBox(1, 100, "", integer=True)
        self.box_acc_cont = NiceSpinBox(1, 100, "", integer=True)
        self.power_step = stepper(self.box_power)
        self.power_quick = quick_values((1, 5, 10, 50, 100, 200, 500), self.box_power)
        self.exp_step = stepper(self.box_exp, "●", TEAL)
        self.exp_quick = quick_values((0.5, 1, 2, 5, 10, 30, 60), self.box_exp)
        self._value_labels = [_muted("Laser power"), _muted("Exposure"), _muted("Accumulations")]
        self._value_rows = [
            _row(self._value_labels[0], self.power_step, 12, self.power_quick, 12,
                 self.lbl_power_measured),
            _row(self._value_labels[1], self.exp_step,
                 stepper(self.box_exp_cont, "◆", CONTINUOUS_MARK), 12, self.exp_quick),
            _row(self._value_labels[2], stepper(self.box_acc, "●", TEAL),
                 stepper(self.box_acc_cont, "◆", CONTINUOUS_MARK), 12,
                 quick_values((1, 3, 5, 10, 20, 50), self.box_acc))]
        # The spectrograph window (presets and its range in cm⁻¹) and step and glue,
        # mirroring the control window (also under the spectra plot).
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
        self._first_labels = [_muted("Window"), _muted("Step and glue")]
        self._range_rows = [
            _row(self._first_labels[0], self.cmb_preset, self.btn_preset, 16, self.lbl_window),
            _row(self._first_labels[1], self.spin_glue_from, _muted("to"),
                 self.spin_glue_to, self.btn_glue, self.lbl_glue)]

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
        # Fixed spectra, controlled by squares like the saved ones: (curve, colour, pen,
        # name). Their mode lives here; that of the saved ones, in the main window.
        self._fixed = {
            "current": (self.curve, TEAL, dict(width=1.4), "Current spectrum"),
            "partial": (self.partial_curve, INK, dict(width=1.4, style=Qt.DashLine),
                        "Measurement in progress (running average)"),
            "background": (self.bg_curve, "#9aa3ad", dict(width=1, style=Qt.DashLine),
                           "Background"),
        }
        self._fixed_modes = {key: "normal" for key in self._fixed}
        # The lipid band, shaded on the plot while its button is on.
        self.lipid_region = pg.LinearRegionItem(movable=False,
                                                brush=pg.mkBrush(235, 104, 52, 35),
                                                pen=pg.mkPen(None))
        self.lipid_region.setZValue(-10)
        self.lipid_region.setVisible(False)
        self.plot.addItem(self.lipid_region)
        self._shown_fixed: tuple = ()
        self._hovered: str | None = None
        self.lbl_spec_info = _muted("No spectrum. Connect the spectrometer and press Acquire.")
        self.lbl_cursor = _muted()
        self.lbl_saved_info = _muted("Saved spectra appear here too.")
        # A square per spectrum on the plot, in its colour, in the column of the
        # intensity zoom buttons: click to dim, again to hide, again to show; hover for
        # its name (and to highlight it); right click on a saved one to remove it.
        self.squares = QWidget()
        self._squares_layout = QVBoxLayout(self.squares)
        self._squares_layout.setContentsMargins(0, 0, 0, 0)
        self._squares_layout.setSpacing(3)
        self.squares_scroll = QScrollArea()
        self.squares_scroll.setWidget(self.squares)
        self.squares_scroll.setWidgetResizable(True)
        self.squares_scroll.setFrameShape(QFrame.NoFrame)
        self.squares_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.squares_scroll.setFixedWidth(SpectrumSquare.SIZE + 10)
        self.squares_scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            f"QScrollBar::handle:vertical {{ background: #c4cad0; border-radius: 3px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }")
        self.btn_clear_saved = QPushButton("Clear")
        self.btn_clear_saved.setEnabled(False)
        self.btn_clear_saved.clicked.connect(self.clear_saved_clicked)
        spectra_box = self._plot_with_header(self.plot, self.lbl_spec_info, self.lbl_cursor)
        # Sample name; the same as in the control window: files are named after it.
        self.edit_sample = QLineEdit()
        self.edit_sample.setPlaceholderText("sample name")
        self.edit_sample.setMinimumWidth(220)
        # Under the plot, so that the plot starts level with the video: the sample and
        # the saved spectra, the sliders, and the spectral range.
        below = spectra_box.layout()
        sample_label = _muted("Sample")
        below.addWidget(_row(sample_label, self.edit_sample, 24, self.lbl_saved_info,
                             self.btn_clear_saved, stretch_at=3))
        for r in (*self._value_rows, *self._range_rows):
            below.addWidget(r)
        # The names in the first column share a width, so that what follows lines up.
        for label in [sample_label, *self._value_labels, *self._first_labels]:
            label.setFixedWidth(100)

        # Ranges are set by apply_zoom, not pyqtgraph's auto-range.
        self.plot.disableAutoRange()
        self.plot.getViewBox().sigRangeChangedManually.connect(lambda *_: self.zoom_manual.emit())
        self._mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30,
                                           slot=lambda ev: self._on_mouse(ev, self.plot,
                                                                          self.lbl_cursor))
        spectra = titled_box("Spectra", spectra_box)
        self.refresh_squares()

        # Laser power and CCD temperature over the last five minutes, under the image.
        self.trend_power = TrendPlot("Laser power", "mW", 1, LASER)
        self.trend_temp = TrendPlot("CCD temperature", "°C", 1, CCD_TEMP)
        # Lipid band height, shown while its button is on.
        self.trend_lipid = TrendPlot("Lipid band", "counts/s", 0, SERIES[1])
        self.trend_lipid.setVisible(False)
        trends = QWidget()
        row = QHBoxLayout(trends)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.trend_power)
        row.addWidget(self.trend_temp)
        row.addWidget(self.trend_lipid)
        # The x axis is "minutes ago", so it moves even when no readings arrive.
        self._trend_timer = QTimer(self, interval=1000)
        self._trend_timer.timeout.connect(self.trend_power.redraw)
        self._trend_timer.timeout.connect(self.trend_temp.redraw)
        self._trend_timer.timeout.connect(self.trend_lipid.redraw)
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
        """The plot over a line of text, with Andor-style axis zoom buttons: by the
        intensity axis, at its foot, and under the wavelength axis, at its start."""
        row = QHBoxLayout()
        row.addWidget(info, 1)
        row.addWidget(cursor)
        if button is not None:
            row.addWidget(button)
        y_buttons = QVBoxLayout()
        y_buttons.setSpacing(2)
        if plot is self.plot:
            y_buttons.addWidget(self.squares_scroll, 1, Qt.AlignHCenter)
            y_buttons.addSpacing(8)
        else:
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
        v.addLayout(grid, 1)
        v.addLayout(row)  # under the plot, so that it starts level with the video
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

    def set_saved(self, entries: list[tuple]) -> None:
        """Redraws the saved spectra: a list of (label, x, y, colour, mode, tooltip),
        oldest first; mode is normal, dim or hide."""
        for item in self._saved_items:
            self.plot.removeItem(item)
        self._saved_entries = list(entries)
        self._saved_items = []
        for label, x, y, color, mode, tip in entries:
            item = pg.PlotDataItem(x, y)
            item.setZValue(0)
            self.plot.addItem(item)
            self._saved_items.append(item)
        self.btn_clear_saved.setEnabled(bool(entries))
        if entries:
            hidden = sum(1 for e in entries if e[4] == "hide")
            self.lbl_saved_info.setText(f"{len(entries)} saved this session"
                                        + (f" · {hidden} hidden" if hidden else ""))
        else:
            self.lbl_saved_info.setText("Saved spectra appear here too.")
        self.refresh_squares()

    def refresh_squares(self) -> None:
        """Squares for the fixed spectra that have data now (current, measurement in
        progress, background) and for every saved one, newest first. Rebuilt only when
        that set changes; the curves are restyled every time."""
        fixed = tuple(k for k, (curve, *_) in self._fixed.items()
                      if curve.getData()[0] is not None and len(curve.getData()[0]))
        shown = (fixed, tuple((e[0], e[3], e[4]) for e in self._saved_entries))
        if shown != self._shown_fixed:
            self._shown_fixed = shown
            while self._squares_layout.count():
                w = self._squares_layout.takeAt(0).widget()
                if w is not None:
                    w.setParent(None)  # off the window at once, freed later
                    w.deleteLater()
            for key in fixed:
                _, color, pen, name = self._fixed[key]
                sq = SpectrumSquare(color, name, dashed="style" in pen)
                sq.set_mode(self._fixed_modes[key])
                sq.clicked.connect(lambda k=key, sq=sq: self._cycle_fixed(k, sq))
                sq.hovered.connect(lambda on, k=key: self._hover(k if on else None))
                self._squares_layout.addWidget(sq)
            for i in reversed(range(len(self._saved_entries))):
                label, _, _, color, mode, tip = self._saved_entries[i]
                sq = SpectrumSquare(color, tip, removable=True)
                sq.set_mode(mode)
                sq.clicked.connect(lambda i=i: self.saved_mode_changed.emit(
                    i, _next_mode(self._saved_entries[i][4])))
                sq.hovered.connect(lambda on, i=i: self._hover(i if on else None))
                sq.remove_requested.connect(lambda i=i: self.remove_saved_requested.emit([i]))
                self._squares_layout.addWidget(sq)
            self._squares_layout.addStretch(1)
        self._style_all()

    def _cycle_fixed(self, key: str, square) -> None:
        self._fixed_modes[key] = _next_mode(self._fixed_modes[key])
        square.set_mode(self._fixed_modes[key])
        self._style_all()

    def _hover(self, key) -> None:
        self._hovered = key
        self._style_all()

    def _style_all(self) -> None:
        """Pens and visibility of every spectrum from its mode: dimmed ones faded and
        thin; hidden ones not drawn (and so out of the zoom); the hovered one thicker."""
        def style(item, color, pen, mode, hovered):
            kw = dict(pen)
            c = pg.mkColor(color)
            if mode == "dim":
                c.setAlpha(70)
                kw["width"] = 1
            if hovered:
                kw["width"] = kw.get("width", 1.5) + 2
            item.setPen(pg.mkPen(c, **kw))
            item.setVisible(mode != "hide")
        for key, (curve, color, pen, _) in self._fixed.items():
            style(curve, color, pen, self._fixed_modes[key], self._hovered == key)
        for i, (item, entry) in enumerate(zip(self._saved_items, self._saved_entries)):
            style(item, entry[3], dict(width=1.5), entry[4], self._hovered == i)
            item.setZValue(5 if self._hovered == i else 0)

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

    def set_power_enabled(self, enabled: bool) -> None:
        self.power_step.setEnabled(enabled)
        self.power_quick.setEnabled(enabled)

    def set_exposure_auto(self, auto: bool) -> None:
        """With auto-exposure the program chooses the measurement exposure: its box only
        shows it."""
        self.exp_step.setEnabled(not auto)
        self.exp_quick.setEnabled(not auto)

    def set_measured_power(self, mw: float | None) -> None:
        if mw is None:
            self.lbl_power_measured.setText("")
        else:
            self.lbl_power_measured.setText(f"<span style='color:{LASER}'>measured "
                                            f"{mw:.1f} mW</span>")

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
        self.btn_acquire.setEnabled(connected and (measuring or not acquiring or live))
        self.btn_acquire.setChecked(connected and measuring)
        set_tip(self.btn_acquire, "Stop the measurement (Esc stops everything)" if measuring
                else "Acquire spectrum (saved automatically; during the continuous "
                     "measurement, it resumes afterwards)")
        self.btn_ccd_rows.setEnabled(connected)


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
