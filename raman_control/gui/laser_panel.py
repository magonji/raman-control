"""Panel for the 532 nm Raman laser."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton)

from ..hardware.laser import LaserStatus
from .widgets import DANGER, LASER, MUTED, Led, hint, reading


class LaserPanel(QGroupBox):
    connect_clicked = Signal(bool)
    enable_clicked = Signal()
    disable_clicked = Signal()
    power_requested = Signal(float)

    def __init__(self, max_power_mw: float):
        super().__init__("532 nm Raman laser")
        self.max_power_mw = max_power_mw

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self.connect_clicked)
        self.led = Led(14)
        self.lbl_state = QLabel("Disconnected")

        self.banner = QLabel("")
        self.banner.setMinimumHeight(26)

        self.btn_on = QPushButton("Emission on")
        self.btn_off = QPushButton("Emission off")
        self.btn_on.clicked.connect(self.enable_clicked)
        self.btn_off.clicked.connect(self.disable_clicked)

        self.spin_power = QDoubleSpinBox()
        self.spin_power.setRange(0.0, max_power_mw)
        self.spin_power.setDecimals(0)  # the smd12 takes whole mW
        self.spin_power.setSingleStep(5.0)
        self.spin_power.setSuffix(" mW")
        self.spin_power.setValue(10.0)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(lambda: self.power_requested.emit(self.spin_power.value()))

        self.lbl_power = reading("— mW")
        self.lbl_tlaser = QLabel("—")
        self.lbl_tpsu = QLabel("—")

        grid = QGridLayout(self)
        top = QHBoxLayout()
        top.addWidget(self.btn_connect)
        top.addSpacing(8)
        top.addWidget(self.led)
        top.addWidget(self.lbl_state, 1)
        grid.addLayout(top, 0, 0, 1, 3)
        grid.addWidget(self.banner, 1, 0, 1, 3)
        grid.addWidget(self.btn_on, 2, 0, 1, 2)
        grid.addWidget(self.btn_off, 2, 2)
        grid.addWidget(QLabel("Setpoint"), 3, 0)
        grid.addWidget(self.spin_power, 3, 1)
        grid.addWidget(self.btn_apply, 3, 2)
        grid.addWidget(QLabel("Measured power"), 4, 0)
        grid.addWidget(self.lbl_power, 4, 1, 1, 2)
        grid.addWidget(QLabel("Head / PSU"), 5, 0)
        temps = QHBoxLayout()
        temps.addWidget(self.lbl_tlaser)
        temps.addWidget(QLabel("/"))
        temps.addWidget(self.lbl_tpsu)
        temps.addStretch(1)
        grid.addLayout(temps, 5, 1, 1, 2)
        grid.addWidget(hint(f"Software limit: {max_power_mw:.0f} mW. "
                            "The hardware interlock is still mandatory."), 6, 0, 1, 3)
        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        self.btn_connect.blockSignals(True)
        self.btn_connect.setChecked(connected)
        self.btn_connect.setText("Disconnect" if connected else "Connect")
        self.btn_connect.blockSignals(False)
        for w in (self.btn_on, self.btn_off, self.spin_power, self.btn_apply):
            w.setEnabled(connected)
        if not connected:
            self.led.set_state("off")
            self.lbl_state.setText("Disconnected")
            self.lbl_power.setText("— mW")
            self.lbl_tlaser.setText("—")
            self.lbl_tpsu.setText("—")
            self._set_banner(False)

    def _set_banner(self, emitting: bool) -> None:
        if emitting:
            self.banner.setText("  Emitting · wear eye protection")
            self.banner.setStyleSheet(f"background:{LASER}; color:white; font-weight:700; border-radius:4px;")
        else:
            self.banner.setText("")
            self.banner.setStyleSheet("background:transparent;")

    def update_status(self, st: LaserStatus) -> None:
        emitting = st.emitting
        self.led.set_state("laser" if emitting else "ok")
        if st.enabled is None:
            self.lbl_state.setText("Status not recognised (check get_status)")
            self.lbl_state.setStyleSheet(f"color:{DANGER};")
        else:
            self.lbl_state.setText("Emitting" if emitting else "Connected, no emission")
            self.lbl_state.setStyleSheet("" if emitting else f"color:{MUTED};")
        self._set_banner(emitting)
        self.lbl_power.setText("— mW" if st.power_mw is None else f"{st.power_mw:.1f} mW")
        fmt = lambda t: "—" if t is None else f"{t:.2f} °C"
        self.lbl_tlaser.setText(fmt(st.laser_temp_c))
        self.lbl_tpsu.setText(fmt(st.psu_temp_c))
