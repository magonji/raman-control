"""Ventana de visualización: imagen del microscopio y espectro, pensada para la segunda pantalla.

No habla con el hardware ni decide nada: la ventana principal le pasa los datos y
se conecta a sus widgets. Cerrarla solo la oculta; el programa se cierra desde la
ventana de control, que es la que sabe apagar el láser y calentar el CCD.
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

        # Barra superior: paro del láser y franja de emisión, también aquí porque es la
        # ventana que se mira mientras se mide.
        bar = QToolBar("Visualización")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.btn_estop = estop_button("Apagar láser  (F12)")
        bar.addWidget(self.btn_estop)
        bar.addSeparator()
        self.lbl_laser = QLabel()
        self.lbl_laser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(self.lbl_laser)
        self.set_laser_state(None)

        # Imagen del microscopio.
        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        self.image_view.getView().setBackgroundColor("#20252b")
        self.target = pg.TargetItem(pos=(0, 0), size=22, movable=True,
                                    pen=pg.mkPen(LASER, width=2))
        self.image_view.getView().addItem(self.target)
        self.target.hide()

        # Espectro.
        pg.setConfigOption("foreground", "#3d4650")
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "Intensidad", units="cuentas")
        self.plot.setLabel("bottom", "Desplazamiento Raman", units="cm⁻¹")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.bg_curve = self.plot.plot(pen=pg.mkPen("#9aa3ad", width=1, style=Qt.DashLine))
        self.curve = self.plot.plot(pen=pg.mkPen(TEAL, width=1.4))
        self.lbl_spec_info = QLabel("Sin espectro. Conecta el espectrómetro y pulsa Adquirir.")
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
        self.splitter.addWidget(titled_box("Microscopio", self.image_view))
        self.splitter.addWidget(titled_box("Espectro", spec_container))
        self.splitter.setSizes([500, 500])
        self.setCentralWidget(self.splitter)

    def set_laser_state(self, emitting: bool | None, power: str = "") -> None:
        if emitting:
            self.lbl_laser.setText(f"  Láser emitiendo · {power} · llevar protección ocular")
            self.lbl_laser.setStyleSheet(f"background:{LASER}; color:white; font-weight:700; "
                                         "border-radius:4px; padding:6px;")
        else:
            self.lbl_laser.setText("  Láser desconectado" if emitting is None else "  Láser sin emisión")
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
