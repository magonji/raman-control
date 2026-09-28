"""Ventana principal: une los tres instrumentos, la seguridad y el guardado."""
from __future__ import annotations

import logging

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QLabel, QMainWindow, QMessageBox, QScrollArea, QSizePolicy,
                               QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import __version__, acquisition, storage
from ..config import grating_labels
from ..hardware import (GenieNanoCamera, LaserQuantumLaser, SimulatedCamera, SimulatedLaser,
                        SimulatedSpectrometer, SimWorld, AndorShamrockSpectrometer, TEMP_STATES_ES)
from ..workers import CameraWorker, LaserWorker, SpectrometerWorker
from .camera_panel import CameraPanel
from .laser_panel import LaserPanel
from .spectrometer_panel import SpectrometerPanel
from .view_window import ViewWindow
from .widgets import DANGER, LASER, STYLESHEET, LogView, SavePanel, estop_button, titled_box

log = logging.getLogger("raman")
LEVELS = {"info": logging.INFO, "ok": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, confirm_dialogs: bool = True):
        super().__init__()
        self.cfg = cfg
        self.confirm_dialogs = confirm_dialogs  # False solo en pruebas automáticas
        self.world = SimWorld()
        self.laser_nm = float(cfg["laser"]["wavelength_nm"])
        self.x_mode = "shift"
        self.background: dict | None = None
        self.last_spectrum: dict | None = None
        self.last_frame: np.ndarray | None = None
        self.laser_status = None
        self.spec_status = None
        self.cam_info = None
        self._laser_confirmed = False
        self._closing = None  # None | "warming" | "done"
        self._first_frame = True

        self.setStyleSheet(STYLESHEET)
        self._build_workers()
        self._build_ui()
        self._wire()
        for w in self.workers:
            w.start()

    # ------------------------------------------------------------------------
    #  Instrumentos
    # ------------------------------------------------------------------------
    def _build_workers(self) -> None:
        c, world = self.cfg, self.world

        def laser_factory():
            lc = c["laser"]
            return SimulatedLaser(lc, world) if lc["simulate"] else LaserQuantumLaser(lc)

        def spec_factory():
            sc = c["spectrometer"]
            if sc["simulate"]:
                return SimulatedSpectrometer(sc, c["simulation"], world, self.laser_nm)
            return AndorShamrockSpectrometer(sc)

        def cam_factory():
            cc = c["camera"]
            return SimulatedCamera(cc, c["simulation"], world) if cc["simulate"] else GenieNanoCamera(cc)

        self.laser_w = LaserWorker(laser_factory, c["laser"])
        self.spec_w = SpectrometerWorker(spec_factory, c["spectrometer"])
        self.cam_w = CameraWorker(cam_factory, c["camera"])
        self.workers = (self.laser_w, self.spec_w, self.cam_w)

    # ------------------------------------------------------------------------
    #  Interfaz
    # ------------------------------------------------------------------------
    def _build_ui(self) -> None:
        simulated = [n for n in ("laser", "spectrometer", "camera") if self.cfg[n]["simulate"]]
        names = {"laser": "láser", "spectrometer": "espectrómetro", "camera": "cámara"}
        suffix = "  [simulación]" if simulated else ""
        self.setWindowTitle(f"Microscopio Raman · panel de control {__version__}{suffix}")

        # Barra superior: paro del láser siempre visible.
        bar = QToolBar("Principal")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.btn_estop = estop_button("Apagar láser  (F12)")
        bar.addWidget(self.btn_estop)
        bar.addSeparator()
        self.act_connect_all = QAction("Conectar todo", self)
        bar.addAction(self.act_connect_all)
        self.act_show_view = QAction("Mostrar imagen y espectro", self)
        bar.addAction(self.act_show_view)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        if simulated:
            badge = QLabel("Simulado: " + ", ".join(names[n] for n in simulated))
            badge.setObjectName("simbadge")
            bar.addWidget(badge)

        # Columna izquierda de controles.
        self.laser_panel = LaserPanel(float(self.cfg["laser"]["max_power_mw"]))
        self.spec_panel = SpectrometerPanel(self.cfg)
        self.cam_panel = CameraPanel(self.cfg)
        self.save_panel = SavePanel(self.cfg["general"]["data_dir"])
        tabs = QTabWidget()
        tabs.addTab(self.spec_panel, "Espectrómetro")
        tabs.addTab(self.cam_panel, "Cámara")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 4, 8, 8)
        lv.addWidget(self.laser_panel)
        lv.addWidget(tabs)
        lv.addWidget(self.save_panel)
        lv.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(left.sizeHint().width() + 24)
        scroll.setMaximumWidth(520)

        # Registro a la derecha de los controles.
        self.logview = LogView()
        main = QSplitter(Qt.Horizontal)
        main.addWidget(scroll)
        main.addWidget(titled_box("Registro", self.logview))
        main.setStretchFactor(1, 1)
        self.setCentralWidget(main)

        # Imagen y espectro en su propia ventana, pensada para la segunda pantalla.
        self.view = ViewWindow(f"Microscopio Raman · imagen y espectro{suffix}")
        self.image_view = self.view.image_view
        self.target = self.view.target
        self.plot = self.view.plot
        self.curve = self.view.curve
        self.bg_curve = self.view.bg_curve
        self.lbl_spec_info = self.view.lbl_spec_info
        self.lbl_cursor = self.view.lbl_cursor
        self._mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30,
                                           slot=self._on_mouse)

        self.sb_laser = QLabel("Láser: desconectado")
        self.sb_ccd = QLabel("CCD: desconectado")
        self.sb_cam = QLabel("Cámara: desconectada")
        for label in (self.sb_laser, self.sb_ccd, self.sb_cam):
            label.setStyleSheet("padding: 0 12px;")
            self.statusBar().addPermanentWidget(label)

    # ------------------------------------------------------------------------
    #  Ventanas y pantallas
    # ------------------------------------------------------------------------
    def show_windows(self) -> None:
        """Muestra las dos ventanas donde se dejaron la última vez.

        La primera vez, con dos pantallas, pone la de control en la principal y la de
        imagen y espectro maximizada en la otra.
        """
        settings = QSettings("RamanControl", "panel")
        control = settings.value("control/geometry")
        view = settings.value("view/geometry")
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        if control is None or not self.restoreGeometry(control):
            self.setGeometry(primary.availableGeometry().adjusted(40, 40, -40, -40))
            if len(screens) < 2:
                self.resize(1000, 850)
        if view is None or not self.view.restoreGeometry(view):
            others = [s for s in screens if s is not primary]
            if others:
                self.view.setGeometry(others[0].availableGeometry())
                self.view.setWindowState(Qt.WindowMaximized)
            else:
                self.view.resize(1100, 900)
        self.show()
        self.view.show()

    def show_view(self) -> None:
        if self.view.isMinimized():
            self.view.showNormal()
        self.view.show()
        self.view.raise_()
        self.view.activateWindow()

    def _save_window_geometry(self) -> None:
        settings = QSettings("RamanControl", "panel")
        settings.setValue("control/geometry", self.saveGeometry())
        settings.setValue("view/geometry", self.view.saveGeometry())

    def _wire(self) -> None:
        for w in self.workers:
            w.log.connect(self.log)

        # Láser
        lp, lw = self.laser_panel, self.laser_w
        lp.connect_clicked.connect(lambda on: lw.submit("connect" if on else "disconnect"))
        lp.enable_clicked.connect(self._enable_laser)
        lp.disable_clicked.connect(lambda: lw.submit("disable", priority=1))
        lp.power_requested.connect(lambda mw: lw.submit("set_power", mw))
        lw.connected.connect(lp.set_connected)
        lw.connected.connect(self._laser_connected)
        lw.status.connect(self._on_laser_status)
        self.btn_estop.clicked.connect(self.emergency_stop)
        self.view.btn_estop.clicked.connect(self.emergency_stop)
        # F12 funciona con cualquier ventana del programa activa, incluso con un diálogo abierto.
        QShortcut(QKeySequence(Qt.Key_F12), self, activated=self.emergency_stop,
                  context=Qt.ApplicationShortcut)

        # Espectrómetro
        sp, sw = self.spec_panel, self.spec_w
        sp.connect_clicked.connect(lambda on: sw.submit("connect" if on else "disconnect"))
        sp.cooler_toggled.connect(lambda on: sw.submit("set_cooler", on))
        sp.target_changed.connect(lambda t: sw.submit("set_target", t))
        sp.warmup_clicked.connect(lambda: sw.submit("warmup"))
        sp.grating_selected.connect(lambda g: sw.submit("set_grating", g))
        sp.center_requested.connect(lambda nm: sw.submit("set_center", nm))
        sp.acquire_requested.connect(lambda s: sw.submit("acquire", s))
        sp.background_requested.connect(self._acquire_background)
        sp.abort_clicked.connect(sw.request_abort)
        sp.axis_changed.connect(self._set_axis)
        sp.laser_wl_changed.connect(self._set_laser_wl)
        sp.calibrate_clicked.connect(self._calibrate_zero)
        sp.show_background_toggled.connect(lambda _: self._redraw_spectrum())
        sw.connected.connect(sp.set_connected)
        sw.connected.connect(lambda on: None if on else self.sb_ccd.setText("CCD: desconectado"))
        sw.status.connect(self._on_spec_status)
        sw.spectrum.connect(self._on_spectrum)
        sw.progress.connect(sp.set_progress)
        sw.acquiring.connect(sp.set_acquiring)
        sw.exposure_suggested.connect(sp.set_exposure)
        sw.warmup_done.connect(self._on_warmup_done)
        # Esc, en cada ventana por separado para no quitársela a los diálogos.
        for window in (self, self.view):
            QShortcut(QKeySequence(Qt.Key_Escape), window, activated=sw.request_abort)

        # Cámara
        cp, cw = self.cam_panel, self.cam_w
        cp.connect_clicked.connect(lambda on: cw.submit("connect" if on else "disconnect"))
        cp.live_toggled.connect(lambda on: cw.submit("start_live" if on else "stop_live"))
        cp.exposure_changed.connect(lambda ms: cw.submit("set_exposure", ms))
        cp.gain_changed.connect(lambda db: cw.submit("set_gain", db))
        cp.snapshot_clicked.connect(lambda: cw.submit("snapshot"))
        cp.crosshair_toggled.connect(lambda on: self.target.setVisible(on and self.last_frame is not None))
        cw.connected.connect(cp.set_connected)
        cw.connected.connect(lambda on: self.sb_cam.setText("Cámara: conectada" if on else "Cámara: desconectada"))
        cw.info.connect(self._on_cam_info)
        cw.live_changed.connect(cp.set_live)
        cw.fps.connect(cp.set_fps)
        cw.frame.connect(self._on_frame)
        cw.snapshot.connect(self._on_snapshot)

        self.save_panel.save_clicked.connect(self._save_spectrum)
        self.act_connect_all.triggered.connect(self.connect_all)
        self.act_show_view.triggered.connect(self.show_view)

    # ------------------------------------------------------------------------
    #  Registro
    # ------------------------------------------------------------------------
    def log(self, level: str, message: str) -> None:
        self.logview.add(level, message)
        log.log(LEVELS.get(level, logging.INFO), message)

    def connect_all(self) -> None:
        for w in self.workers:
            w.submit("connect")

    # ------------------------------------------------------------------------
    #  Láser y seguridad
    # ------------------------------------------------------------------------
    def emergency_stop(self) -> None:
        self.laser_w.submit("emergency_off", priority=0)
        self.log("warn", "Paro del láser solicitado")

    def _enable_laser(self) -> None:
        if not self._laser_confirmed and self.confirm_dialogs:
            answer = QMessageBox.warning(
                self, "Láser de Clase 4",
                "Vas a activar la emisión del láser de 532 nm.\n\n"
                "Confirma que llevas gafas con la densidad óptica adecuada, que la sala está "
                "señalizada y que nadie sin protección puede ver el haz.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            self._laser_confirmed = True
        self.laser_w.submit("enable")

    def _laser_connected(self, connected: bool) -> None:
        if not connected:
            self.laser_status = None
            self.sb_laser.setText("Láser: desconectado")
            self.sb_laser.setStyleSheet("padding: 0 12px;")
            self.view.set_laser_state(None)

    def _on_laser_status(self, st) -> None:
        self.laser_status = st
        self.laser_panel.update_status(st)
        power = "—" if st.power_mw is None else f"{st.power_mw:.1f} mW"
        self.view.set_laser_state(bool(st.emitting), power)
        if st.emitting:
            self.sb_laser.setText(f"Láser emitiendo · {power}")
            self.sb_laser.setStyleSheet(f"padding: 0 12px; color:{LASER}; font-weight:700;")
        else:
            self.sb_laser.setText("Láser sin emisión")
            self.sb_laser.setStyleSheet("padding: 0 12px;")

    # ------------------------------------------------------------------------
    #  Espectrómetro
    # ------------------------------------------------------------------------
    def _on_spec_status(self, st) -> None:
        self.spec_status = st
        self.spec_panel.update_status(st)
        state = TEMP_STATES_ES.get(st.temp_status, st.temp_status)
        self.sb_ccd.setText(f"CCD {st.temperature_c:.1f} °C · {state.lower()}")

    def _acquire_background(self, settings: dict) -> None:
        if self.confirm_dialogs:
            answer = QMessageBox.question(
                self, "Adquirir fondo",
                "Para el fondo, bloquea el haz o apaga la emisión del láser, manteniendo las "
                "mismas condiciones (exposición, red, centro). ¿Continuar?",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return
        self.spec_w.submit("acquire", settings)

    def _background_compatible(self, result: dict) -> bool:
        bg = self.background
        return (bg is not None
                and np.size(bg["counts"]) == np.size(result["counts"])
                and abs(bg["exposure_s"] - result["exposure_s"]) <= 1e-9 * max(1.0, result["exposure_s"])
                and bg["grating"] == result["grating"]
                and (bg["center_nm"] or 0) == (result["center_nm"] or 0))

    def _on_spectrum(self, result: dict) -> None:
        if result["purpose"] == "background":
            self.background = result
            self.log("ok", f"Fondo guardado en memoria ({result['exposure_s']:.3g} s × "
                           f"{result['accumulations']}). Marca «Restar fondo» para usarlo.")
            self._redraw_spectrum()
            return
        corrected, used_bg = result["counts"], None
        if self.spec_panel.subtract_background():
            if self._background_compatible(result):
                used_bg = self.background["counts"]
                corrected = result["counts"] - used_bg
            else:
                self.log("warn", "El fondo no coincide (exposición, red o centro distintos) o no "
                                 "existe: el espectro se muestra sin restar.")
        result["counts_corrected"] = corrected
        result["background_used"] = used_bg
        result["background_subtracted"] = used_bg is not None
        self.last_spectrum = result
        self.save_panel.btn_save.setEnabled(True)
        self._redraw_spectrum()
        if result["saturated"]:
            self.log("warn", "Espectro saturado: reduce la exposición o la potencia.")
        if self.save_panel.chk_autosave.isChecked():
            self._save_spectrum()

    def _x_values(self, wl: np.ndarray | None, n: int) -> tuple[np.ndarray, str, str]:
        if wl is None:
            return np.arange(n, dtype=float), "Píxel", ""
        if self.x_mode == "shift":
            return acquisition.raman_shift_cm1(wl, self.laser_nm), "Desplazamiento Raman", "cm⁻¹"
        return wl, "Longitud de onda", "nm"

    def _redraw_spectrum(self) -> None:
        r = self.last_spectrum
        if r is not None:
            y = r["counts_corrected"]
            x, name, units = self._x_values(r["wavelength_nm"], len(y))
            self.plot.setLabel("bottom", name, units=units)
            self.curve.setData(x, y)
            wl = r["wavelength_nm"]
            if wl is not None:
                shift = acquisition.raman_shift_cm1(wl, self.laser_nm)
                self.spec_panel.set_range(f"Rango: {wl.min():.2f}–{wl.max():.2f} nm, "
                                          f"{shift.min():.0f} a {shift.max():.0f} cm⁻¹")
            parts = [f"{r['exposure_s']:.3g} s × {r['accumulations']}",
                     f"CCD {r['ccd_temperature_c']:.1f} °C"]
            if r["cosmic_pixels_rejected"]:
                parts.append(f"{r['cosmic_pixels_rejected']} píxeles con rayos cósmicos corregidos")
            if r["background_subtracted"]:
                parts.append("fondo restado")
            text = ", ".join(parts)
            if r["saturated"]:
                self.lbl_spec_info.setText(f"<b style='color:{DANGER}'>Saturado.</b> {text}")
            else:
                self.lbl_spec_info.setText(text)
        bg = self.background
        if bg is not None and self.spec_panel.chk_showbg.isChecked():
            x, _, _ = self._x_values(bg["wavelength_nm"], len(bg["counts"]))
            self.bg_curve.setData(x, bg["counts"])
        else:
            self.bg_curve.setData([], [])

    def _on_mouse(self, event) -> None:
        pos = event[0]
        if self.plot.sceneBoundingRect().contains(pos):
            p = self.plot.getPlotItem().vb.mapSceneToView(pos)
            unit = {"shift": "cm⁻¹", "nm": "nm"}[self.x_mode]
            self.lbl_cursor.setText(f"{p.x():.1f} {unit} · {p.y():.0f}")

    def _set_axis(self, mode: str) -> None:
        self.x_mode = mode
        self.plot.enableAutoRange()
        self._redraw_spectrum()

    def _set_laser_wl(self, nm: float) -> None:
        self.laser_nm = float(nm)
        self._redraw_spectrum()

    def _calibrate_zero(self) -> None:
        r = self.last_spectrum
        if r is None or r["wavelength_nm"] is None:
            self.log("warn", "Necesito un espectro con eje en longitud de onda para calibrar.")
            return
        found = acquisition.find_laser_line(r["wavelength_nm"], r["counts"], self.laser_nm)
        if found is None:
            self.log("warn", "No encuentro la línea láser a ±1,5 nm de la actual. Usa una muestra "
                             "que disperse bastante y comprueba que el rango incluye el láser.")
            return
        old = self.laser_nm
        self.spec_panel.spin_laser.setValue(found)  # dispara _set_laser_wl
        delta = 1e7 / old - 1e7 / found
        self.log("ok", f"Línea láser en {found:.4f} nm (antes {old:.4f}); el eje se desplaza "
                       f"{delta:+.2f} cm⁻¹. Anótalo en config.toml si quieres conservarlo.")

    # ------------------------------------------------------------------------
    #  Cámara
    # ------------------------------------------------------------------------
    def _on_cam_info(self, info) -> None:
        self.cam_info = info
        self.cam_panel.set_info(info)

    def _on_frame(self, img: np.ndarray) -> None:
        self.last_frame = img
        auto = self.cam_panel.auto_levels() or self._first_frame
        self.image_view.setImage(img, autoLevels=auto, autoRange=self._first_frame,
                                 autoHistogramRange=auto)
        if self._first_frame:
            self._first_frame = False
            self.target.setPos(img.shape[1] / 2, img.shape[0] / 2)
            self.target.setVisible(self.cam_panel.chk_cross.isChecked())

    def _image_metadata(self) -> dict:
        info = self.cam_info
        pos = self.target.pos()
        md = {
            "software": f"raman_control {__version__}",
            "sample": self.save_panel.sample(),
            "camera_model": info.model if info else None,
            "camera_serial": info.serial if info else None,
            "pixel_format": info.pixel_format if info else None,
            "exposure_ms": self.cam_panel.spin_exp.value(),
            "gain_db": self.cam_panel.spin_gain.value(),
            "laser_marker_xy_px": [round(pos.x(), 1), round(pos.y(), 1)],
            "simulated": bool(self.cfg["camera"]["simulate"]),
        }
        md.update(self._laser_metadata())
        return md

    def _on_snapshot(self, img: np.ndarray) -> None:
        try:
            base = storage.new_base(self.save_panel.folder(), self.save_panel.sample())
            path = storage.save_image(base, img, self._image_metadata())
            self.log("ok", f"Imagen guardada: {path}")
        except Exception as exc:
            self.log("error", f"No se pudo guardar la imagen: {exc}")

    # ------------------------------------------------------------------------
    #  Guardado de espectros
    # ------------------------------------------------------------------------
    def _laser_metadata(self) -> dict:
        st = self.laser_status
        return {
            "laser_wavelength_nm": self.laser_nm,
            "laser_emitting": st.emitting if st else None,
            "laser_power_setpoint_mw": st.setpoint_mw if st else None,
            "laser_power_measured_mw": st.power_mw if st else None,
            "laser_head_temp_c": st.laser_temp_c if st else None,
        }

    def _spectrum_metadata(self, r: dict) -> dict:
        labels = grating_labels(self.cfg)
        sc = self.cfg["spectrometer"]
        md = {
            "software": f"raman_control {__version__}",
            "timestamp": r["timestamp"],
            "sample": self.save_panel.sample(),
            "exposure_s": r["exposure_s"],
            "accumulations": r["accumulations"],
            "combination": "media con rechazo de rayos cósmicos" if r["cosmic_removal"] else "media",
            "cosmic_pixels_rejected": r["cosmic_pixels_rejected"],
            "auto_exposure": r["auto_exposure"],
            "saturated": r["saturated"],
            "raw_max_counts": r["raw_max_counts"],
            "grating": r["grating"],
            "grating_label": labels.get(r["grating"]) if r["grating"] else None,
            "center_wavelength_nm": r["center_nm"],
            "read_mode": sc["read_mode"],
            "ccd_temperature_c": r["ccd_temperature_c"],
            "ccd_temp_status": r["ccd_temp_status"],
            "background_subtracted": r["background_subtracted"],
            "simulated": r["simulated"],
        }
        md.update(self._laser_metadata())
        return md

    def _save_spectrum(self) -> None:
        r = self.last_spectrum
        if r is None:
            return
        try:
            base = storage.new_base(self.save_panel.folder(), self.save_panel.sample())
            wl = r["wavelength_nm"]
            shift = acquisition.raman_shift_cm1(wl, self.laser_nm) if wl is not None else None
            path = storage.save_spectrum(base, r["counts_corrected"], self._spectrum_metadata(r),
                                         wavelength_nm=wl, raman_shift_cm1=shift,
                                         background=r["background_used"])
            self.log("ok", f"Espectro guardado: {path}")
            if self.save_panel.chk_attach.isChecked() and self.last_frame is not None:
                img_path = storage.save_image(base, self.last_frame, self._image_metadata())
                self.log("ok", f"Imagen asociada: {img_path.name}")
        except Exception as exc:
            self.log("error", f"No se pudo guardar el espectro: {exc}")

    # ------------------------------------------------------------------------
    #  Cierre ordenado
    # ------------------------------------------------------------------------
    def _ccd_is_cold(self) -> bool:
        st = self.spec_status
        safe = float(self.cfg["spectrometer"]["safe_shutdown_temperature_c"])
        return (self.spec_w.device is not None and st is not None
                and st.temperature_c is not None and st.temperature_c < safe)

    def closeEvent(self, event) -> None:
        if self._closing == "done":
            self._finish_close()
            event.accept()
            return
        if self._closing == "warming":
            if self._ask("Calentando el CCD", "El CCD todavía se está calentando. ¿Salir ya?",
                         default_yes=False):
                self._closing = "done"
                self.close()
            event.ignore()
            return
        self.spec_w.request_abort()
        self.laser_w.submit("emergency_off", priority=0)
        if self._ccd_is_cold() and self.confirm_dialogs:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Cerrar el programa")
            box.setText(f"El CCD está a {self.spec_status.temperature_c:.1f} °C. Andor recomienda "
                        "calentarlo por encima de "
                        f"{self.cfg['spectrometer']['safe_shutdown_temperature_c']:.0f} °C antes de "
                        "desconectarlo.")
            warm = box.addButton("Calentar y salir", QMessageBox.AcceptRole)
            box.addButton("Salir sin calentar", QMessageBox.DestructiveRole)
            cancel = box.addButton("Cancelar", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is cancel:
                event.ignore()
                return
            if box.clickedButton() is warm:
                self._closing = "warming"
                self.cam_w.submit("stop_live")
                self.spec_w.submit("warmup", then_disconnect=True)
                self.log("info", "Calentando el CCD antes de salir; la ventana se cerrará sola.")
                event.ignore()
                return
        self._finish_close()
        event.accept()

    def _finish_close(self) -> None:
        self._save_window_geometry()
        self.view.close_for_real()
        self._stop_workers()

    def _on_warmup_done(self, ok: bool) -> None:
        if self._closing == "warming":
            self._closing = "done"
            self.close()

    def _ask(self, title: str, text: str, default_yes: bool = True) -> bool:
        if not self.confirm_dialogs:
            return default_yes
        default = QMessageBox.Yes if default_yes else QMessageBox.No
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                    default) == QMessageBox.Yes

    def _stop_workers(self) -> None:
        for w in self.workers:
            w.stop()
        for w in self.workers:
            w.wait(15000)
