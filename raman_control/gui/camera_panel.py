"""Panel de la cámara del microscopio (Genie Nano)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from ..hardware.camera import CameraInfo
from .widgets import Led, hint, primary_button


class CameraPanel(QWidget):
    connect_clicked = Signal(bool)
    live_toggled = Signal(bool)
    exposure_changed = Signal(float)
    gain_changed = Signal(float)
    snapshot_clicked = Signal()
    crosshair_toggled = Signal(bool)

    def __init__(self, cfg: dict):
        super().__init__()
        cc = cfg["camera"]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        g = QGroupBox("Cámara del microscopio")
        self.btn_connect = QPushButton("Conectar")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self.connect_clicked)
        self.led = Led(14)
        self.lbl_info = QLabel("Desconectada")
        self.lbl_info.setWordWrap(True)
        self.btn_live = primary_button("Iniciar vídeo")
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self.live_toggled)
        self.spin_exp = QDoubleSpinBox()
        self.spin_exp.setRange(0.02, 1000.0)
        self.spin_exp.setDecimals(2)
        self.spin_exp.setSuffix(" ms")
        self.spin_exp.setValue(float(cc["exposure_ms"]))
        self.spin_exp.setKeyboardTracking(False)
        self.spin_exp.valueChanged.connect(self.exposure_changed)
        self.spin_gain = QDoubleSpinBox()
        self.spin_gain.setRange(0.0, 24.0)
        self.spin_gain.setDecimals(1)
        self.spin_gain.setSuffix(" dB")
        self.spin_gain.setValue(float(cc["gain_db"]))
        self.spin_gain.setKeyboardTracking(False)
        self.spin_gain.valueChanged.connect(self.gain_changed)
        self.chk_autolevels = QCheckBox("Contraste automático")
        self.chk_autolevels.setChecked(True)
        self.chk_cross = QCheckBox("Marcador de posición del láser")
        self.chk_cross.setChecked(True)
        self.chk_cross.toggled.connect(self.crosshair_toggled)
        self.btn_snap = QPushButton("Guardar imagen")
        self.btn_snap.clicked.connect(self.snapshot_clicked)
        self.lbl_fps = QLabel("— fps")

        grid = QGridLayout(g)
        top = QHBoxLayout()
        top.addWidget(self.btn_connect)
        top.addSpacing(8)
        top.addWidget(self.led)
        top.addWidget(self.lbl_fps, 1)
        grid.addLayout(top, 0, 0, 1, 2)
        grid.addWidget(self.lbl_info, 1, 0, 1, 2)
        grid.addWidget(self.btn_live, 2, 0, 1, 2)
        grid.addWidget(QLabel("Exposición"), 3, 0)
        grid.addWidget(self.spin_exp, 3, 1)
        grid.addWidget(QLabel("Ganancia"), 4, 0)
        grid.addWidget(self.spin_gain, 4, 1)
        grid.addWidget(self.chk_autolevels, 5, 0, 1, 2)
        grid.addWidget(self.chk_cross, 6, 0, 1, 2)
        grid.addWidget(self.btn_snap, 7, 0, 1, 2)
        grid.addWidget(hint("Arrastra el marcador verde hasta donde cae el láser: su posición "
                            "se guarda con cada imagen."), 8, 0, 1, 2)
        root.addWidget(g)
        root.addStretch(1)
        self.set_connected(False)

    def auto_levels(self) -> bool:
        return self.chk_autolevels.isChecked()

    def set_connected(self, connected: bool) -> None:
        self.btn_connect.blockSignals(True)
        self.btn_connect.setChecked(connected)
        self.btn_connect.setText("Desconectar" if connected else "Conectar")
        self.btn_connect.blockSignals(False)
        for w in (self.btn_live, self.spin_exp, self.spin_gain, self.btn_snap):
            w.setEnabled(connected)
        self.led.set_state("ok" if connected else "off")
        if not connected:
            self.lbl_info.setText("Desconectada")
            self.set_live(False)

    def set_live(self, live: bool) -> None:
        self.btn_live.blockSignals(True)
        self.btn_live.setChecked(live)
        self.btn_live.setText("Detener vídeo" if live else "Iniciar vídeo")
        self.btn_live.blockSignals(False)
        if not live:
            self.lbl_fps.setText("— fps")

    def set_fps(self, fps: float) -> None:
        self.lbl_fps.setText(f"{fps:.1f} fps" if fps > 0 else "— fps")

    def set_info(self, info: CameraInfo) -> None:
        self.lbl_info.setText(f"{info.model} · {info.width}×{info.height} · {info.pixel_format}"
                              + (f" · n.º {info.serial}" if info.serial else ""))
        lo, hi = info.exposure_range_ms
        self.spin_exp.setRange(max(lo, 0.001), hi)
        glo, ghi = info.gain_range_db
        self.spin_gain.setRange(glo, ghi)
