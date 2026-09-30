"""Main window: brings together the three instruments, safety and saving."""
from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox, QScrollArea,
                               QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget)

from .. import __version__, acquisition, storage
from ..config import grating_labels
from ..hardware import (GenieNanoCamera, LaserQuantumLaser, SimulatedCamera, SimulatedLaser,
                        SimulatedSpectrometer, SimWorld, AndorShamrockSpectrometer, TEMP_STATES)
from ..workers import CameraWorker, LaserWorker, SpectrometerWorker
from .camera_panel import CameraPanel
from .laser_panel import LaserPanel
from .spectrometer_panel import SpectrometerPanel
from .view_window import ViewWindow
from .widgets import (DANGER, LASER, SERIES, STYLESHEET, LogView, SavePanel, estop_button,
                      titled_box)

log = logging.getLogger("raman")
LEVELS = {"info": logging.INFO, "ok": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}
# Where the window positions are remembered. Change the group when the default
# layout changes, so that positions saved with the old layout are ignored once.
# They are kept separately for each number of usable screens, so that a change of
# monitors does not bring back positions saved for another arrangement.
GEOMETRY_GROUP = "windows_v4"


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, confirm_dialogs: bool = True):
        super().__init__()
        self.cfg = cfg
        self.confirm_dialogs = confirm_dialogs  # False only in automated tests
        self.world = SimWorld()
        self.laser_nm = float(cfg["laser"]["wavelength_nm"])
        self.x_mode = "shift"
        self.background: dict | None = None
        self.last_spectrum: dict | None = None
        self.saved: list[dict] = []  # spectra saved this session, oldest first
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
    #  Instruments
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
    #  Interface
    # ------------------------------------------------------------------------
    def _build_ui(self) -> None:
        simulated = [n for n in ("laser", "spectrometer", "camera") if self.cfg[n]["simulate"]]
        names = {"laser": "laser", "spectrometer": "spectrometer", "camera": "camera"}
        suffix = "  [simulation]" if simulated else ""
        self.setWindowTitle(f"Raman microscope · control panel {__version__}{suffix}")

        # Top bar: laser stop always visible.
        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.btn_estop = estop_button("Laser off  (F12)")
        bar.addWidget(self.btn_estop)
        bar.addSeparator()
        self.act_connect_all = QAction("Connect all", self)
        bar.addAction(self.act_connect_all)
        self.act_show_view = QAction("Show image and spectrum", self)
        bar.addAction(self.act_show_view)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        if simulated:
            badge = QLabel("Simulated: " + ", ".join(names[n] for n in simulated))
            badge.setObjectName("simbadge")
            bar.addWidget(badge)

        # Every control visible at once, in three columns.
        self.laser_panel = LaserPanel(float(self.cfg["laser"]["max_power_mw"]))
        self.spec_panel = SpectrometerPanel(self.cfg)
        self.cam_panel = CameraPanel(self.cfg)
        self.save_panel = SavePanel(self.cfg["general"]["data_dir"])
        sp = self.spec_panel
        columns = (
            (self.laser_panel, self.cam_panel),
            (sp.box_ccd, sp.box_spectrograph, sp.box_axis),
            (sp.box_acquisition, self.save_panel),
        )
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(12)
        for widgets in columns:
            col = QVBoxLayout()
            for widget in widgets:
                col.addWidget(widget)
            col.addStretch(1)
            row.addLayout(col, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)

        # Log underneath, across the whole width.
        self.logview = LogView()
        main = QSplitter(Qt.Vertical)
        main.addWidget(scroll)
        main.addWidget(titled_box("Log", self.logview))
        main.setCollapsible(0, False)
        main.setStretchFactor(1, 1)
        main.setSizes([controls.sizeHint().height() + 8, 250])
        self.setCentralWidget(main)

        # Image and spectrum in their own window, intended for the second screen.
        self.view = ViewWindow(f"Raman microscope · image and spectrum{suffix}")
        self.image_view = self.view.image_view
        self.target = self.view.target
        self.plot = self.view.plot
        self.curve = self.view.curve
        self.bg_curve = self.view.bg_curve
        self.lbl_spec_info = self.view.lbl_spec_info
        self.lbl_cursor = self.view.lbl_cursor

        self.sb_laser = QLabel("Laser: disconnected")
        self.sb_ccd = QLabel("CCD: disconnected")
        self.sb_cam = QLabel("Camera: disconnected")
        for label in (self.sb_laser, self.sb_ccd, self.sb_cam):
            label.setStyleSheet("padding: 0 12px;")
            self.statusBar().addPermanentWidget(label)

    # ------------------------------------------------------------------------
    #  Windows and screens
    # ------------------------------------------------------------------------
    def show_windows(self) -> None:
        """Shows both windows where they were left last time.

        Screens listed in [windows] ignore_screens (such as the spatial light
        modulator, which Windows sees as one more screen) are never used. With two
        usable screens the control window goes maximised on the main screen and the
        image and spectrum window on the other one, the first time and whenever the
        saved positions leave a window off the usable screens or both windows on the
        same screen. With a single screen they overlap, and the control window goes
        on top.
        """
        screens = self._usable_screens()
        log.info("Screens: %s", ", ".join(
            f"{s.name()} {s.geometry().getRect()} x{s.devicePixelRatio()}"
            + ("" if s in screens else " (ignored)") for s in QGuiApplication.screens()))
        settings = QSettings("RamanControl", "panel")
        control = settings.value(self._geometry_key("control"))
        view = settings.value(self._geometry_key("view"))
        restored = (control is not None and self.restoreGeometry(control)
                    and view is not None and self.view.restoreGeometry(view))
        on_screen = restored and None not in (self._screen_of(self), self._screen_of(self.view))
        if len(screens) > 1:
            if not on_screen or self._screen_of(self) is self._screen_of(self.view):
                control_screen = self._control_screen()
                view_screen = next(s for s in screens if s is not control_screen)
                self._show_maximised_on(self.view, view_screen)
                self._show_maximised_on(self, control_screen)
            else:
                self.view.show()
                self.show()
        else:
            if not on_screen:
                area = screens[0].availableGeometry()
                self.view.setGeometry(area.adjusted(0, 30, -300, 0))
                self.setGeometry(area.adjusted(300, 30, 0, 0))
            self.view.show()
            self.show()
        # The windows appear asynchronously, so raising the control window right
        # away can be undone when the image window finishes appearing on top of it.
        # Raise it once the event loop is running, and again a moment later, when it
        # is also checked that the control window really is on a usable screen.
        QTimer.singleShot(0, self._bring_to_front)
        QTimer.singleShot(500, self._check_control_on_screen)

    def _usable_screens(self) -> list:
        """Screens that are real monitors, left to right."""
        ignored = [text.lower() for text in self.cfg["windows"]["ignore_screens"]]
        screens = [s for s in QGuiApplication.screens()
                   if not any(text in s.name().lower() for text in ignored)]
        if not screens:  # everything ignored by mistake: better any screen than none
            screens = [QGuiApplication.primaryScreen()]
        return sorted(screens, key=lambda s: s.geometry().x())

    def _control_screen(self):
        """The main screen, unless it is ignored; then the right-most usable one."""
        screens = self._usable_screens()
        primary = QGuiApplication.primaryScreen()
        return primary if primary in screens else screens[-1]

    def _geometry_key(self, window: str) -> str:
        return f"{GEOMETRY_GROUP}/{len(self._usable_screens())}_screens/{window}"

    def _screen_of(self, window: QMainWindow):
        """The usable screen holding the centre of the window, or None if there is none."""
        screen = QGuiApplication.screenAt(window.frameGeometry().center())
        return screen if screen in self._usable_screens() else None

    @staticmethod
    def _show_maximised_on(window: QMainWindow, screen) -> None:
        # Setting the geometry and then the maximised state is not enough on Windows:
        # the window can still be maximised on the main screen. Tie the native window
        # to the screen first, show it there at normal size, then maximise it.
        window.setWindowState(Qt.WindowNoState)
        window.winId()
        window.windowHandle().setScreen(screen)
        area = screen.availableGeometry()
        window.setGeometry(area.adjusted(40, 40, -40, -40))
        window.show()
        window.showMaximized()

    def _check_control_on_screen(self) -> None:
        """Last resort: brings the control window onto the main screen if it is not on a usable one."""
        log.info("Control window at %s, image window at %s",
                 self.frameGeometry().getRect(), self.view.frameGeometry().getRect())
        if self._screen_of(self) is None:
            log.warning("The control window was not on a usable screen; moving it to the main screen.")
            self._show_maximised_on(self, self._control_screen())
        self._bring_to_front()

    def _bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_view(self) -> None:
        if self.view.isMinimized():
            self.view.showNormal()
        self.view.show()
        self.view.raise_()
        self.view.activateWindow()

    def _save_window_geometry(self) -> None:
        settings = QSettings("RamanControl", "panel")
        settings.setValue(self._geometry_key("control"), self.saveGeometry())
        settings.setValue(self._geometry_key("view"), self.view.saveGeometry())

    def _wire(self) -> None:
        for w in self.workers:
            w.log.connect(self.log)

        # Laser
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
        # F12 works with any of the program's windows active, even with a dialogue open.
        QShortcut(QKeySequence(Qt.Key_F12), self, activated=self.emergency_stop,
                  context=Qt.ApplicationShortcut)

        # Spectrometer
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
        sw.connected.connect(lambda on: None if on else self.sb_ccd.setText("CCD: disconnected"))
        sw.status.connect(self._on_spec_status)
        sw.spectrum.connect(self._on_spectrum)
        sw.progress.connect(sp.set_progress)
        sw.acquiring.connect(sp.set_acquiring)
        sw.exposure_suggested.connect(sp.set_exposure)
        sw.warmup_done.connect(self._on_warmup_done)
        # Esc, on each window separately so as not to take it away from dialogues.
        for window in (self, self.view):
            QShortcut(QKeySequence(Qt.Key_Escape), window, activated=sw.request_abort)

        # Camera
        cp, cw = self.cam_panel, self.cam_w
        cp.connect_clicked.connect(lambda on: cw.submit("connect" if on else "disconnect"))
        cp.live_toggled.connect(lambda on: cw.submit("start_live" if on else "stop_live"))
        cp.exposure_changed.connect(lambda ms: cw.submit("set_exposure", ms))
        cp.gain_changed.connect(lambda db: cw.submit("set_gain", db))
        cp.snapshot_clicked.connect(lambda: cw.submit("snapshot"))
        cp.crosshair_toggled.connect(lambda on: self.target.setVisible(on and self.last_frame is not None))
        cw.connected.connect(cp.set_connected)
        cw.connected.connect(lambda on: self.sb_cam.setText("Camera: connected" if on else "Camera: disconnected"))
        cw.info.connect(self._on_cam_info)
        cw.live_changed.connect(cp.set_live)
        cw.fps.connect(cp.set_fps)
        cw.frame.connect(self._on_frame)
        cw.snapshot.connect(self._on_snapshot)

        self.save_panel.save_clicked.connect(self._save_spectrum)
        self.view.clear_saved_clicked.connect(self._clear_saved)
        self.act_connect_all.triggered.connect(self.connect_all)
        self.act_show_view.triggered.connect(self.show_view)

    # ------------------------------------------------------------------------
    #  Log
    # ------------------------------------------------------------------------
    def log(self, level: str, message: str) -> None:
        self.logview.add(level, message)
        log.log(LEVELS.get(level, logging.INFO), message)

    def connect_all(self) -> None:
        for w in self.workers:
            w.submit("connect")

    # ------------------------------------------------------------------------
    #  Laser and safety
    # ------------------------------------------------------------------------
    def emergency_stop(self) -> None:
        self.laser_w.submit("emergency_off", priority=0)
        self.log("warn", "Laser stop requested")

    def _enable_laser(self) -> None:
        if not self._laser_confirmed and self.confirm_dialogs:
            answer = QMessageBox.warning(
                self, "Class 4 laser",
                "You are about to switch on emission of the 532 nm laser.\n\n"
                "Confirm that you are wearing goggles of adequate optical density, that the room "
                "is signposted and that nobody without protection can see the beam.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            self._laser_confirmed = True
        self.laser_w.submit("enable")

    def _laser_connected(self, connected: bool) -> None:
        if not connected:
            self.laser_status = None
            self.sb_laser.setText("Laser: disconnected")
            self.sb_laser.setStyleSheet("padding: 0 12px;")
            self.view.set_laser_state(None)

    def _on_laser_status(self, st) -> None:
        self.laser_status = st
        self.laser_panel.update_status(st)
        power = "—" if st.power_mw is None else f"{st.power_mw:.1f} mW"
        self.view.set_laser_state(bool(st.emitting), power)
        if st.emitting:
            self.sb_laser.setText(f"Laser emitting · {power}")
            self.sb_laser.setStyleSheet(f"padding: 0 12px; color:{LASER}; font-weight:700;")
        else:
            self.sb_laser.setText("Laser not emitting")
            self.sb_laser.setStyleSheet("padding: 0 12px;")

    # ------------------------------------------------------------------------
    #  Spectrometer
    # ------------------------------------------------------------------------
    def _on_spec_status(self, st) -> None:
        self.spec_status = st
        self.spec_panel.update_status(st)
        state = TEMP_STATES.get(st.temp_status, st.temp_status)
        self.sb_ccd.setText(f"CCD {st.temperature_c:.1f} °C · {state.lower()}")

    def _acquire_background(self, settings: dict) -> None:
        if self.confirm_dialogs:
            answer = QMessageBox.question(
                self, "Acquire background",
                "For the background, block the beam or switch off laser emission, keeping the "
                "same conditions (exposure, grating, centre). Continue?",
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
            self.log("ok", f"Background stored in memory ({result['exposure_s']:.3g} s × "
                           f"{result['accumulations']}). Tick 'Subtract background' to use it.")
            self._redraw_spectrum()
            return
        corrected, used_bg = result["counts"], None
        if self.spec_panel.subtract_background():
            if self._background_compatible(result):
                used_bg = self.background["counts"]
                corrected = result["counts"] - used_bg
            else:
                self.log("warn", "The background does not match (different exposure, grating or centre) or "
                                 "does not exist: the spectrum is shown without subtraction.")
        result["counts_corrected"] = corrected
        result["background_used"] = used_bg
        result["background_subtracted"] = used_bg is not None
        self.last_spectrum = result
        self.save_panel.btn_save.setEnabled(True)
        self._redraw_spectrum()
        if result["saturated"]:
            self.log("warn", "Spectrum saturated: reduce the exposure or the power.")
        if self.save_panel.chk_autosave.isChecked():
            self._save_spectrum()

    def _x_values(self, wl: np.ndarray | None, n: int) -> tuple[np.ndarray, str, str]:
        if wl is None:
            return np.arange(n, dtype=float), "Pixel", ""
        if self.x_mode == "shift":
            return acquisition.raman_shift_cm1(wl, self.laser_nm), "Raman shift", "cm⁻¹"
        return wl, "Wavelength", "nm"

    def _redraw_spectrum(self) -> None:
        r = self.last_spectrum
        if r is not None:
            y = r["counts_corrected"]
            x, name, units = self._x_values(r["wavelength_nm"], len(y))
            self.view.set_axis(name, units)
            self.curve.setData(x, y)
            wl = r["wavelength_nm"]
            if wl is not None:
                shift = acquisition.raman_shift_cm1(wl, self.laser_nm)
                self.spec_panel.set_range(f"Range: {wl.min():.2f}–{wl.max():.2f} nm, "
                                          f"{shift.min():.0f} to {shift.max():.0f} cm⁻¹")
            parts = [f"{r['exposure_s']:.3g} s × {r['accumulations']}",
                     f"CCD {r['ccd_temperature_c']:.1f} °C"]
            if r["cosmic_pixels_rejected"]:
                parts.append(f"{r['cosmic_pixels_rejected']} cosmic-ray pixels corrected")
            if r["background_subtracted"]:
                parts.append("background subtracted")
            text = ", ".join(parts)
            if r["saturated"]:
                self.lbl_spec_info.setText(f"<b style='color:{DANGER}'>Saturated.</b> {text}")
            else:
                self.lbl_spec_info.setText(text)
        bg = self.background
        if bg is not None and self.spec_panel.chk_showbg.isChecked():
            x, _, _ = self._x_values(bg["wavelength_nm"], len(bg["counts"]))
            self.bg_curve.setData(x, bg["counts"])
        else:
            self.bg_curve.setData([], [])
        self._redraw_saved()

    def _redraw_saved(self) -> None:
        entries = []
        for e in self.saved:
            x, _, _ = self._x_values(e["wavelength_nm"], len(e["counts"]))
            entries.append((e["label"], x, e["counts"], e["color"]))
        self.view.set_saved(entries)

    def _add_saved(self, r: dict) -> None:
        """Adds a saved spectrum to the overlay, keeping the latest len(SERIES).

        Each spectrum keeps its colour while it stays; a newcomer takes the first
        colour left free, so the others are never repainted.
        """
        if len(self.saved) >= len(SERIES):
            self.saved.pop(0)
        used = {e["color"] for e in self.saved}
        color = next(c for c in SERIES if c not in used)
        self.saved.append({"label": f"{r['timestamp'][11:19]} · {self.save_panel.sample()}",
                           "wavelength_nm": r["wavelength_nm"],
                           "counts": r["counts_corrected"], "color": color})
        self._redraw_saved()

    def _clear_saved(self) -> None:
        self.saved.clear()
        self._redraw_saved()

    def _set_axis(self, mode: str) -> None:
        self.x_mode = mode
        self.plot.enableAutoRange()
        self.view.saved_plot.enableAutoRange()
        self._redraw_spectrum()

    def _set_laser_wl(self, nm: float) -> None:
        self.laser_nm = float(nm)
        self._redraw_spectrum()

    def _calibrate_zero(self) -> None:
        r = self.last_spectrum
        if r is None or r["wavelength_nm"] is None:
            self.log("warn", "A spectrum with a wavelength axis is needed for calibration.")
            return
        found = acquisition.find_laser_line(r["wavelength_nm"], r["counts"], self.laser_nm)
        if found is None:
            self.log("warn", "Cannot find the laser line within ±1.5 nm of the current one. Use a strongly "
                             "scattering sample and check that the range includes the laser.")
            return
        old = self.laser_nm
        self.spec_panel.spin_laser.setValue(found)  # triggers _set_laser_wl
        delta = 1e7 / old - 1e7 / found
        self.log("ok", f"Laser line at {found:.4f} nm (previously {old:.4f}); the axis shifts by "
                       f"{delta:+.2f} cm⁻¹. Note it in config.toml if you want to keep it.")

    # ------------------------------------------------------------------------
    #  Camera
    # ------------------------------------------------------------------------
    def _on_cam_info(self, info) -> None:
        self.cam_info = info
        self.cam_panel.set_info(info)

    def _on_frame(self, img: np.ndarray) -> None:
        try:
            self.last_frame = img
            auto = self.cam_panel.auto_levels() or self._first_frame
            self.image_view.setImage(img, autoLevels=auto, autoRange=self._first_frame,
                                     autoHistogramRange=auto)
            if self._first_frame:
                self._first_frame = False
                self.target.setPos(img.shape[1] / 2, img.shape[0] / 2)
                self.target.setVisible(self.cam_panel.chk_cross.isChecked())
        finally:
            # The image is painted after this returns; ask for the next frame only once
            # the events already waiting (that painting, and any clicks) have been handled.
            QTimer.singleShot(0, self.cam_w.frame_drawn)

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
            self.log("ok", f"Image saved: {path}")
        except Exception as exc:
            self.log("error", f"Could not save the image: {exc}")

    # ------------------------------------------------------------------------
    #  Saving spectra
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
            "combination": "mean with cosmic-ray rejection" if r["cosmic_removal"] else "mean",
            "cosmic_pixels_rejected": r["cosmic_pixels_rejected"],
            "auto_exposure": r["auto_exposure"],
            "saturated": r["saturated"],
            "raw_max_counts": r["raw_max_counts"],
            "grating": r["grating"],
            "grating_label": labels.get(r["grating"]) if r["grating"] else None,
            "center_wavelength_nm": r["center_nm"],
            "read_mode": sc["read_mode"],
            "tracks_rows": sc["tracks"] if sc["read_mode"] == "random_track" else None,
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
            self.log("ok", f"Spectrum saved: {path}")
            self._add_saved(r)
            if self.save_panel.chk_attach.isChecked() and self.last_frame is not None:
                img_path = storage.save_image(base, self.last_frame, self._image_metadata())
                self.log("ok", f"Associated image: {img_path.name}")
        except Exception as exc:
            self.log("error", f"Could not save the spectrum: {exc}")

    # ------------------------------------------------------------------------
    #  Orderly shutdown
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
            if self._ask("Warming up the CCD", "The CCD is still warming up. Quit now?",
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
            box.setWindowTitle("Close the program")
            box.setText(f"The CCD is at {self.spec_status.temperature_c:.1f} °C. Andor recommends "
                        "warming it above "
                        f"{self.cfg['spectrometer']['safe_shutdown_temperature_c']:.0f} °C before "
                        "disconnecting it.")
            warm = box.addButton("Warm up and quit", QMessageBox.AcceptRole)
            box.addButton("Quit without warming up", QMessageBox.DestructiveRole)
            cancel = box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is cancel:
                event.ignore()
                return
            if box.clickedButton() is warm:
                self._closing = "warming"
                self.cam_w.submit("stop_live")
                self.spec_w.submit("warmup", then_disconnect=True)
                self.log("info", "Warming up the CCD before quitting; the window will close by itself.")
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
