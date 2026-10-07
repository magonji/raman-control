"""Panel for the Andor CCD and the Shamrock spectrograph.

It is not a widget itself: it builds four group boxes (box_ccd, box_spectrograph,
box_acquisition, box_axis) that the main window lays out wherever they fit best.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox)

from ..config import grating_labels
from ..hardware.spectrometer import TEMP_STATES, SpectrometerStatus
from .widgets import DANGER, MUTED, OK, WARN, Led, hint, primary_button, reading

STATE_COLORS = {"stabilized": OK, "not_stabilized": WARN, "not_reached": WARN,
                "drifted": DANGER, "off": MUTED}


class SpectrometerPanel(QObject):
    connect_clicked = Signal(bool)
    cooler_toggled = Signal(bool)
    target_changed = Signal(float)
    warmup_clicked = Signal()
    grating_selected = Signal(int)
    center_requested = Signal(float)
    acquire_requested = Signal(dict)
    background_requested = Signal(dict)
    abort_clicked = Signal()
    axis_changed = Signal(str)
    laser_wl_changed = Signal(float)
    calibrate_clicked = Signal()
    show_background_toggled = Signal(bool)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        sc = cfg["spectrometer"]
        self._acquiring = False
        self._connected = False

        # --- CCD and cooling ------------------------------------------------
        g = QGroupBox("CCD detector and cooling")
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self.connect_clicked)
        self.led = Led(14)
        self.lbl_temp = reading("— °C")
        self.lbl_tstate = QLabel("Disconnected")
        self.spin_target = QDoubleSpinBox()
        self.spin_target.setRange(-100.0, 20.0)
        self.spin_target.setDecimals(1)
        self.spin_target.setSuffix(" °C")
        self.spin_target.setValue(float(sc["target_temperature_c"]))
        self.spin_target.setKeyboardTracking(False)
        self.spin_target.valueChanged.connect(self.target_changed)
        self.chk_cooler = QCheckBox("Cooling on")
        self.chk_cooler.toggled.connect(self.cooler_toggled)
        self.btn_warm = QPushButton("Warm up for shutdown")
        self.btn_warm.clicked.connect(self.warmup_clicked)
        grid = QGridLayout(g)
        top = QHBoxLayout()
        top.addWidget(self.btn_connect)
        top.addSpacing(8)
        top.addWidget(self.led)
        top.addWidget(self.lbl_tstate, 1)
        grid.addLayout(top, 0, 0, 1, 2)
        grid.addWidget(self.lbl_temp, 1, 0)
        grid.addWidget(self.chk_cooler, 1, 1)
        grid.addWidget(QLabel("Target"), 2, 0)
        grid.addWidget(self.spin_target, 2, 1)
        grid.addWidget(self.btn_warm, 3, 0, 1, 2)
        self.box_ccd = g

        # --- Spectrograph -----------------------------------------------------
        g = QGroupBox("Shamrock 500i spectrograph")
        self.cmb_grating = QComboBox()
        for idx, label in sorted(grating_labels(cfg).items()):
            self.cmb_grating.addItem(f"{idx}: {label}", idx)
        self.btn_grating = QPushButton("Change grating")
        self.btn_grating.clicked.connect(
            lambda: self.grating_selected.emit(int(self.cmb_grating.currentData())))
        self.spin_center = QDoubleSpinBox()
        self.spin_center.setRange(200.0, 1200.0)
        self.spin_center.setDecimals(2)
        self.spin_center.setSuffix(" nm")
        self.spin_center.setValue(float(sc["default_center_nm"]))
        self.btn_move = QPushButton("Move")
        self.btn_move.clicked.connect(lambda: self.center_requested.emit(self.spin_center.value()))
        self.lbl_range = hint("Range: —")
        grid = QGridLayout(g)
        grid.addWidget(QLabel("Grating"), 0, 0)
        grid.addWidget(self.cmb_grating, 0, 1)
        grid.addWidget(self.btn_grating, 0, 2)
        grid.addWidget(QLabel("Centre"), 1, 0)
        grid.addWidget(self.spin_center, 1, 1)
        grid.addWidget(self.btn_move, 1, 2)
        grid.addWidget(self.lbl_range, 2, 0, 1, 3)
        self.box_spectrograph = g

        # --- Acquisition ------------------------------------------------------
        g = QGroupBox("Acquisition")
        self.spin_exp = QDoubleSpinBox()
        self.spin_exp.setRange(0.001, 600.0)
        self.spin_exp.setDecimals(3)
        self.spin_exp.setSuffix(" s")
        self.spin_exp.setValue(float(sc["default_exposure_s"]))
        self.spin_acc = QSpinBox()
        self.spin_acc.setRange(1, 1000)
        self.spin_acc.setValue(3)
        # The continuous measurement is a live preview with its own settings, so that
        # it does not change the ones used to measure.
        self.spin_exp_cont = QDoubleSpinBox()
        self.spin_exp_cont.setRange(0.001, 600.0)
        self.spin_exp_cont.setDecimals(3)
        self.spin_exp_cont.setSuffix(" s")
        self.spin_exp_cont.setValue(float(sc["continuous_exposure_s"]))
        self.spin_acc_cont = QSpinBox()
        self.spin_acc_cont.setRange(1, 1000)
        self.spin_acc_cont.setValue(int(sc["continuous_accumulations"]))
        self.chk_auto = QCheckBox("Auto-exposure to")
        self.spin_auto = QSpinBox()
        self.spin_auto.setRange(10, 95)
        self.spin_auto.setSuffix(" % of saturation")
        self.spin_auto.setValue(int(round(float(sc["auto_exposure_target"]) * 100)))
        self.chk_cosmic = QCheckBox("Remove cosmic rays")
        self.chk_cosmic.setChecked(True)
        self.chk_bg = QCheckBox("Subtract background")
        self.chk_unstable = QCheckBox("Allow without stable CCD (testing only)")
        self.btn_acquire = primary_button("Acquire")
        self.btn_cont = QPushButton("Continuous")
        self.btn_abort = QPushButton("Stop")
        self.btn_bg = QPushButton("Acquire background")
        self.btn_acquire.clicked.connect(lambda: self.acquire_requested.emit(self.settings()))
        self.btn_cont.clicked.connect(lambda: self.acquire_requested.emit(self.settings(continuous=True)))
        self.btn_bg.clicked.connect(lambda: self.background_requested.emit(self.settings(purpose="background")))
        self.btn_abort.clicked.connect(self.abort_clicked)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.lbl_progress = hint("")
        grid = QGridLayout(g)
        grid.addWidget(hint("Measurement"), 0, 1)
        grid.addWidget(hint("Continuous"), 0, 2)
        grid.addWidget(QLabel("Exposure"), 1, 0)
        grid.addWidget(self.spin_exp, 1, 1)
        grid.addWidget(self.spin_exp_cont, 1, 2)
        grid.addWidget(QLabel("Accumulations"), 2, 0)
        grid.addWidget(self.spin_acc, 2, 1)
        grid.addWidget(self.spin_acc_cont, 2, 2)
        grid.addWidget(self.chk_auto, 3, 0)
        grid.addWidget(self.spin_auto, 3, 1, 1, 2)
        grid.addWidget(self.chk_cosmic, 4, 0, 1, 3)
        grid.addWidget(self.chk_bg, 5, 0, 1, 3)
        grid.addWidget(self.chk_unstable, 6, 0, 1, 3)
        grid.addWidget(hint("With 3 or more accumulations, cosmic rays are removed by comparing "
                            "exposures; with just one, by their shape (less reliable). "
                            "Auto-exposure applies to measurements only."), 7, 0, 1, 3)
        buttons = QGridLayout()
        buttons.addWidget(self.btn_acquire, 0, 0)
        buttons.addWidget(self.btn_cont, 0, 1)
        buttons.addWidget(self.btn_bg, 1, 0)
        buttons.addWidget(self.btn_abort, 1, 1)
        grid.addLayout(buttons, 8, 0, 1, 3)
        grid.addWidget(self.progress, 9, 0, 1, 3)
        grid.addWidget(self.lbl_progress, 10, 0, 1, 3)
        self.box_acquisition = g

        # --- Axis and calibration ---------------------------------------------
        g = QGroupBox("Axis and calibration")
        self.rb_shift = QRadioButton("Raman shift (cm⁻¹)")
        self.rb_nm = QRadioButton("Wavelength (nm)")
        self.rb_shift.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.rb_shift)
        group.addButton(self.rb_nm)
        self.rb_shift.toggled.connect(lambda on: self.axis_changed.emit("shift" if on else "nm"))
        self.spin_laser = QDoubleSpinBox()
        self.spin_laser.setRange(200.0, 1100.0)
        self.spin_laser.setDecimals(4)
        self.spin_laser.setSuffix(" nm")
        self.spin_laser.setValue(float(cfg["laser"]["wavelength_nm"]))
        self.spin_laser.setKeyboardTracking(False)
        self.spin_laser.valueChanged.connect(self.laser_wl_changed)
        self.btn_cal = QPushButton("Calibrate 0 cm⁻¹ with the laser line")
        self.btn_cal.clicked.connect(self.calibrate_clicked)
        self.chk_showbg = QCheckBox("Overlay background")
        self.chk_showbg.toggled.connect(self.show_background_toggled)
        grid = QGridLayout(g)
        grid.addWidget(self.rb_shift, 0, 0, 1, 2)
        grid.addWidget(self.rb_nm, 1, 0, 1, 2)
        grid.addWidget(QLabel("Laser line"), 2, 0)
        grid.addWidget(self.spin_laser, 2, 1)
        grid.addWidget(self.btn_cal, 3, 0, 1, 2)
        grid.addWidget(self.chk_showbg, 4, 0, 1, 2)
        grid.addWidget(hint("Calibration looks for the residual Rayleigh light let through by the "
                            "notch filters in the latest spectrum."), 5, 0, 1, 2)
        self.box_axis = g
        self.set_connected(False)

    # ------------------------------------------------------------------------
    def settings(self, continuous: bool = False, purpose: str = "sample") -> dict:
        """Acquisition settings; a continuous measurement takes its own exposure and
        accumulations, and never auto-exposure."""
        return {
            "exposure_s": (self.spin_exp_cont if continuous else self.spin_exp).value(),
            "accumulations": (self.spin_acc_cont if continuous else self.spin_acc).value(),
            "auto_exposure": self.chk_auto.isChecked() and not continuous,
            "auto_target": self.spin_auto.value() / 100.0,
            "cosmic": self.chk_cosmic.isChecked(),
            "allow_unstable": self.chk_unstable.isChecked(),
            "continuous": continuous,
            "purpose": purpose,
        }

    def subtract_background(self) -> bool:
        return self.chk_bg.isChecked()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.btn_connect.blockSignals(True)
        self.btn_connect.setChecked(connected)
        self.btn_connect.setText("Disconnect" if connected else "Connect")
        self.btn_connect.blockSignals(False)
        if not connected:
            self.led.set_state("off")
            self.lbl_temp.setText("— °C")
            self.lbl_tstate.setText("Disconnected")
            self.lbl_tstate.setStyleSheet(f"color:{MUTED};")
        self._refresh_enabled()

    def set_acquiring(self, acquiring: bool) -> None:
        self._acquiring = acquiring
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        idle = self._connected and not self._acquiring
        for w in (self.btn_acquire, self.btn_cont, self.btn_bg, self.btn_grating,
                  self.btn_move, self.chk_cooler, self.spin_target, self.btn_warm):
            w.setEnabled(idle)
        self.btn_abort.setEnabled(self._connected)
        self.btn_connect.setEnabled(not self._acquiring)

    def set_progress(self, done: int, total: int, text: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)  # indeterminate animation
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.lbl_progress.setText(text)

    def set_exposure(self, seconds: float) -> None:
        self.spin_exp.setValue(seconds)

    def update_status(self, st: SpectrometerStatus) -> None:
        if st.temperature_c is not None:
            self.lbl_temp.setText(f"{st.temperature_c:.1f} °C")
        color = STATE_COLORS.get(st.temp_status, MUTED)
        self.lbl_tstate.setText(TEMP_STATES.get(st.temp_status, st.temp_status))
        self.lbl_tstate.setStyleSheet(f"color:{color}; font-weight:600;")
        self.led.set_state({"stabilized": "ok", "drifted": "error", "off": "off"}.get(st.temp_status, "warn"))
        self.chk_cooler.blockSignals(True)
        self.chk_cooler.setChecked(st.cooler_on)
        self.chk_cooler.blockSignals(False)
        if st.grating is not None and not self.cmb_grating.hasFocus():
            i = self.cmb_grating.findData(st.grating)
            if i >= 0:
                self.cmb_grating.setCurrentIndex(i)

    def set_range(self, text: str) -> None:
        self.lbl_range.setText(text)
