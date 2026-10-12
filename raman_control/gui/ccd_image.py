"""Window for choosing the CCD rows read (vertical binning) by looking at the CCD.

Without a pinhole, the rows binned are the spatial filter: a narrow track centred on
the image of the spot leaves out light from outside the focal plane (less background,
fewer counts); a wide one collects more of everything. This window reads the whole
frame, shows it with the vertical profile of a band chosen on it, and lets the rows
be dragged over both; Suggest puts them at the half maximum of the profile, Default
goes back to the rows of config.toml, Apply makes them the measurement track.

Rows are numbered as in Solis: from 1, both ends included. On the image, row r spans
r - 1 to r (pixel edges), so a track start-end covers start - 1 to end.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
                               QSplitter, QVBoxLayout)

from .. import acquisition
from .widgets import INK, LASER, MUTED, STYLESHEET, TEAL, hint, primary_button

ROWS_PEN = pg.mkPen(LASER, width=2)
BAND_PEN = pg.mkPen("#eda100", width=2)


class CcdImageDialog(QDialog):
    image_requested = Signal(float)   # exposure, s
    apply_requested = Signal(object)  # [(start, end)] rows, as Solis

    def __init__(self, default_tracks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CCD image · rows read (vertical binning)")
        self.setStyleSheet(STYLESHEET)
        self.resize(1100, 640)
        self.default = tuple(default_tracks[0])
        self._image: np.ndarray | None = None
        self._shift: np.ndarray | None = None

        # Controls.
        self.spin_exp = QDoubleSpinBox()
        self.spin_exp.setRange(0.01, 600.0)
        self.spin_exp.setDecimals(2)
        self.spin_exp.setSuffix(" s")
        self.spin_exp.setValue(1.0)
        self.btn_read = primary_button("Read the whole CCD")
        self.btn_read.clicked.connect(lambda: self.image_requested.emit(self.spin_exp.value()))
        self.lbl_status = QLabel("Put the laser on a sample with a strong band (silicon, "
                                 "520 cm⁻¹) and read the CCD.")
        self.lbl_status.setStyleSheet(f"color:{MUTED};")
        top = QHBoxLayout()
        top.addWidget(QLabel("Exposure"))
        top.addWidget(self.spin_exp)
        top.addWidget(self.btn_read)
        top.addWidget(self.lbl_status, 1)

        # Image: columns × rows, row 1 at the bottom.
        self.img_plot = pg.PlotWidget(background="w")
        self.img_plot.setLabel("left", "CCD row")
        self.img_plot.setLabel("bottom", "CCD column")
        self.img_plot.setMenuEnabled(False)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.image_item.setColorMap(pg.colormap.get("viridis"))
        self.img_plot.addItem(self.image_item)
        self.band = pg.LinearRegionItem(orientation="vertical", pen=BAND_PEN,
                                        brush=pg.mkBrush(237, 161, 0, 30))
        self.rows_img = pg.LinearRegionItem(orientation="horizontal", pen=ROWS_PEN,
                                            brush=pg.mkBrush(47, 179, 68, 40))
        for item in (self.band, self.rows_img):
            item.setZValue(10)
            self.img_plot.addItem(item)

        # Vertical profile of the band, on the same rows.
        self.prof_plot = pg.PlotWidget(background="w")
        self.prof_plot.setLabel("bottom", "Counts in the band")
        self.prof_plot.setMenuEnabled(False)
        self.prof_plot.setYLink(self.img_plot)
        self.prof_plot.getAxis("left").setStyle(showValues=False)
        self.prof_curve = self.prof_plot.plot(pen=pg.mkPen(TEAL, width=2))
        self.rows_prof = pg.LinearRegionItem(orientation="horizontal", pen=ROWS_PEN,
                                             brush=pg.mkBrush(47, 179, 68, 40))
        self.prof_plot.addItem(self.rows_prof)

        plots = QSplitter(Qt.Horizontal)
        plots.addWidget(self.img_plot)
        plots.addWidget(self.prof_plot)
        plots.setSizes([800, 300])

        # Rows and the buttons that act on them.
        self.lbl_rows = QLabel()
        self.lbl_rows.setStyleSheet(f"color:{INK}; font-weight:600;")
        self.lbl_band = QLabel()
        self.lbl_band.setStyleSheet(f"color:{MUTED};")
        self.btn_suggest = QPushButton("Suggest (half maximum)")
        self.btn_default = QPushButton(f"Default ({self.default[0]}–{self.default[1]})")
        self.btn_apply = primary_button("Apply")
        self.btn_suggest.clicked.connect(self._suggest)
        self.btn_default.clicked.connect(lambda: self.set_rows(*self.default))
        self.btn_apply.clicked.connect(lambda: self.apply_requested.emit([self.rows()]))
        bottom = QHBoxLayout()
        bottom.addWidget(self.lbl_rows)
        bottom.addWidget(self.lbl_band, 1)
        for b in (self.btn_suggest, self.btn_default, self.btn_apply):
            bottom.addWidget(b)

        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(plots, 1)
        v.addLayout(bottom)
        v.addWidget(hint("Drag the green band (rows) and the yellow one (columns of the band "
                         "whose profile is shown). Narrow rows on the spot: more confocal, less "
                         "background, fewer counts. Apply makes them the rows of every "
                         "measurement; they are remembered between sessions."))

        self._syncing = False
        for region in (self.rows_img, self.rows_prof):
            region.sigRegionChanged.connect(lambda r: self._rows_moved(r))
        self.band.sigRegionChanged.connect(lambda _: self._update_profile())
        self.set_rows(*self.default)
        self.set_busy(False)

    # -- rows ------------------------------------------------------------------
    def rows(self) -> tuple[int, int]:
        lo, hi = self.rows_img.getRegion()
        start = int(round(lo)) + 1
        return start, max(start, int(round(hi)))

    def set_rows(self, start: int, end: int) -> None:
        for region in (self.rows_img, self.rows_prof):
            region.setRegion((start - 1, end))

    def _rows_moved(self, region) -> None:
        """Keeps both row bands on whole rows and in step."""
        if self._syncing:
            return
        self._syncing = True
        lo, hi = region.getRegion()
        lo, hi = round(lo), max(round(lo) + 1, round(hi))
        for r in (self.rows_img, self.rows_prof):
            r.setRegion((lo, hi))
        self._syncing = False
        start, end = lo + 1, hi
        self.lbl_rows.setText(f"Rows {start}–{end} ({end - start + 1} rows)")

    def _suggest(self) -> None:
        profile = self._profile()
        if profile is not None:
            self.set_rows(*acquisition.rows_at_half_maximum(profile))

    # -- image and profile --------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self.btn_read.setEnabled(not busy)
        if busy:
            self.lbl_status.setText("Reading the whole CCD…")

    def set_image(self, image: np.ndarray, shift_cm1: np.ndarray | None) -> None:
        first = self._image is None
        self._image, self._shift = image, shift_cm1
        rows, cols = image.shape
        # Contrast from percentiles: the Rayleigh line would otherwise set the scale and
        # leave the Raman bands in the dark.
        lo, hi = np.percentile(image, [1, 99.7])
        self.image_item.setImage(image, levels=(lo, max(hi, lo + 1)))
        self.image_item.setRect(0, 0, cols, rows)
        self.set_busy(False)
        self.lbl_status.setText(f"{rows} rows × {cols} columns")
        if first:
            # The band starts on the brightest column outside the Rayleigh line.
            column = image.sum(axis=0)
            if shift_cm1 is not None:
                column = np.where(np.abs(shift_cm1) < 30, column.min(), column)
            c = int(np.argmax(column))
            self.band.setRegion((max(0, c - 4), min(cols, c + 5)))
            self.img_plot.getViewBox().autoRange()
        self._update_profile()

    def _band_columns(self) -> slice | None:
        if self._image is None:
            return None
        cols = self._image.shape[1]
        lo, hi = self.band.getRegion()
        lo, hi = int(np.clip(round(lo), 0, cols - 1)), int(np.clip(round(hi), 1, cols))
        return slice(lo, max(lo + 1, hi))

    def _profile(self) -> np.ndarray | None:
        band = self._band_columns()
        return None if band is None else self._image[:, band].sum(axis=1)

    def _update_profile(self) -> None:
        profile = self._profile()
        if profile is None:
            return
        rows = np.arange(profile.size) + 0.5  # centre of each row on the image
        self.prof_curve.setData(profile, rows)
        band = self._band_columns()
        text = f"Band: columns {band.start}–{band.stop - 1}"
        if self._shift is not None:
            text += f" ({self._shift[band.start]:.0f}–{self._shift[band.stop - 1]:.0f} cm⁻¹)"
        self.lbl_band.setText(text)
