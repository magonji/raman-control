"""Main window: brings together the three instruments, safety and saving."""
from __future__ import annotations

import json
import logging
from collections import deque

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QScrollArea, QSizePolicy, QSpinBox, QSplitter, QToolBar,
                               QVBoxLayout, QWidget)

from .. import __version__, acquisition, storage
from ..config import grating_labels
from ..hardware import (GenieNanoCamera, LaserQuantumLaser, SimulatedCamera, SimulatedLaser,
                        SimulatedSpectrometer, SimWorld, AndorShamrockSpectrometer, TEMP_STATES)
from ..workers import CameraWorker, LaserWorker, SpectrometerWorker
from .camera_panel import CameraPanel
from .ccd_image import CcdImageDialog
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
GEOMETRY_GROUP = "windows_v5"


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
        self._colour_turn = 0        # next colour to repeat, once all are in use
        self.last_frame: np.ndarray | None = None
        self.laser_status = None
        self.spec_status = None
        self.cam_info = None
        self._laser_confirmed = False
        self._closing = None  # None | "disconnecting" | "done"
        self._first_frame = True
        # Rows read by the CCD: config.toml holds the default; the rows last applied are
        # remembered between sessions (not in config.toml, which a git pull rewrites).
        sc = cfg["spectrometer"]
        self._default_tracks = [list(t) for t in sc["tracks"]]
        saved_tracks = self._saved_tracks()
        if saved_tracks and sc.get("read_mode", "random_track") == "random_track":
            sc["tracks"] = saved_tracks
        self.ccd_dialog = None

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
        self.laser_panel = LaserPanel(float(self.cfg["laser"]["max_power_mw"]),
                                      float(self.cfg["laser"]["default_setpoint_mw"]))
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
        self.view = ViewWindow(f"Raman microscope · image and spectrum{suffix}",
                               float(self.cfg["laser"]["max_power_mw"]))
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
        usable screens the image and spectrum window goes maximised on the main screen
        and the control window on the other one, the first time and whenever the
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
                self._show_maximised_on(self.view, self._main_screen())
                self._show_maximised_on(self, self._control_screen())
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

    def _main_screen(self):
        """The main screen, unless it is ignored; then the right-most usable one."""
        screens = self._usable_screens()
        primary = QGuiApplication.primaryScreen()
        return primary if primary in screens else screens[-1]

    def _control_screen(self):
        """A usable screen other than the main one, or the main one if there is no other."""
        main = self._main_screen()
        return next((s for s in self._usable_screens() if s is not main), main)

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
        """Last resort: brings the control window onto its screen if it is not on a usable one."""
        log.info("Control window at %s, image window at %s",
                 self.frameGeometry().getRect(), self.view.frameGeometry().getRect())
        if self._screen_of(self) is None:
            log.warning("The control window was not on a usable screen; moving it to its screen.")
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
        self.view.btn_emission.clicked.connect(self._toggle_emission)
        # Power box in the image window: shows the setpoint last applied, and applies a
        # new one when set (−/+, a quick value, or Enter after typing).
        box = self.view.box_power
        box.set_quiet(lp.spin_power.value())
        self.view.set_power_enabled(False)
        lp.power_requested.connect(box.set_quiet)
        lw.connected.connect(self.view.set_power_enabled)
        box.committed.connect(self._apply_power_from_view)
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
        sp.keep_cooling_clicked.connect(sw.request_abort)
        sw.warming.connect(sp.set_warming)
        sp.grating_selected.connect(lambda g: sw.submit("set_grating", g))
        sp.center_requested.connect(lambda nm: sw.submit("set_center", nm))
        # Spectral range: presets, the range of the window, step and glue.
        self._window_nm: tuple[float, float] | None = None
        self._refresh_presets()
        v = self.view
        sp.preset_selected.connect(self._go_preset)
        v.btn_preset.clicked.connect(lambda: self._go_preset(v.cmb_preset.currentData()))
        sp.save_preset_clicked.connect(self._save_preset)
        sw.window_changed.connect(self._on_window_changed)
        sp.glue_requested.connect(self._step_glue)
        v.btn_glue.clicked.connect(lambda: self._step_glue(v.spin_glue_from.value(),
                                                           v.spin_glue_to.value()))
        for a, b in ((sp.spin_glue_from, v.spin_glue_from), (sp.spin_glue_to, v.spin_glue_to)):
            b.setValue(a.value())
            a.valueChanged.connect(lambda x, b=b: b.setValue(x) if b.value() != x else None)
            b.valueChanged.connect(lambda x, a=a: a.setValue(x) if a.value() != x else None)
        sp.spin_glue_from.valueChanged.connect(lambda _: self._mirror_glue_plan())
        sp.spin_glue_to.valueChanged.connect(lambda _: self._mirror_glue_plan())
        sp.acquire_requested.connect(self._acquire)
        sp.background_requested.connect(self._acquire_background)
        sp.abort_clicked.connect(self._stop_acquisition)
        sp.axis_changed.connect(self._set_axis)
        sp.laser_wl_changed.connect(self._set_laser_wl)
        sp.calibrate_clicked.connect(self._calibrate_zero)
        sp.show_background_toggled.connect(lambda _: self._redraw_spectrum())
        sw.connected.connect(sp.set_connected)
        sw.connected.connect(lambda on: None if on else self.sb_ccd.setText("CCD: disconnected"))
        sw.status.connect(self._on_spec_status)
        sw.spectrum.connect(self._on_spectrum)
        sw.partial.connect(self._on_partial)
        # Rows read (vertical binning).
        sp.set_rows(self.cfg["spectrometer"]["tracks"])
        sp.rows_requested.connect(self._set_tracks)
        sp.rows_default_clicked.connect(lambda: self._set_tracks(self._default_tracks))
        sp.ccd_image_clicked.connect(self._open_ccd_image)
        self.view.btn_ccd_rows.clicked.connect(self._open_ccd_image)
        sw.tracks_changed.connect(self._on_tracks_changed)
        sw.ccd_image.connect(self._on_ccd_image)
        sw.frame_started.connect(self._on_frame_started)
        sw.progress.connect(sp.set_progress)
        # Exposure and accumulation boxes in the image window mirror the panel's: one
        # for measurements, one for the continuous measurement.
        v = self.view
        for view_box, panel_box in ((v.box_exp, sp.spin_exp), (v.box_exp_cont, sp.spin_exp_cont),
                                    (v.box_acc, sp.spin_acc), (v.box_acc_cont, sp.spin_acc_cont)):
            view_box.set_quiet(panel_box.value())
            panel_box.valueChanged.connect(view_box.set_quiet)
            view_box.committed.connect(
                lambda x, p=panel_box: p.setValue(int(round(x)) if isinstance(p, QSpinBox) else x))
        # With auto-exposure the program chooses the measurement exposure: that box only
        # shows it. The continuous one never uses auto-exposure.
        sp.chk_auto.toggled.connect(v.set_exposure_auto)
        v.set_exposure_auto(sp.chk_auto.isChecked())
        # Continuous and single-spectrum buttons in the image window. _spec_continuous:
        # the acquisition running is the continuous one; _resume_live: a measurement
        # has interrupted it and it starts again when the measurement ends.
        self._spec_acquiring = self._spec_continuous = self._resume_live = False
        self._stop_when_started = False
        # Kind (continuous or not) of each acquisition submitted, read when it starts:
        # the worker reports a result before it reports the end of that acquisition.
        self._queued_kinds: deque[bool] = deque()
        sw.connected.connect(self._on_spec_connected)
        sw.acquiring.connect(self._on_spec_acquiring)
        self.view.btn_continuous.clicked.connect(self._toggle_continuous)
        self.view.btn_acquire.clicked.connect(self._acquire_or_stop)
        sw.exposure_suggested.connect(sp.set_exposure)
        # Esc, on each window separately so as not to take it away from dialogues.
        for window in (self, self.view):
            QShortcut(QKeySequence(Qt.Key_Escape), window, activated=self._stop_acquisition)

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
        # Video and image buttons in the image window, the same as the camera panel's.
        self._cam_live = False
        cw.connected.connect(self._on_cam_connected)
        cw.live_changed.connect(self._on_cam_live)
        self.view.btn_video.clicked.connect(self._toggle_video)
        self.view.btn_snapshot.clicked.connect(lambda: cw.submit("snapshot"))
        cw.fps.connect(cp.set_fps)
        cw.frame.connect(self._on_frame)
        cw.snapshot.connect(self._on_snapshot)

        self.save_panel.save_clicked.connect(self._save_spectrum)
        # Sample name in the image window, mirroring the control window's box.
        sample_boxes = (self.save_panel.edit_sample, self.view.edit_sample)
        self.view.edit_sample.setText(self.save_panel.edit_sample.text())
        for box, other in (sample_boxes, sample_boxes[::-1]):
            box.textChanged.connect(lambda text, other=other: other.setText(text)
                                    if other.text() != text else None)
        self.view.clear_saved_clicked.connect(self._clear_saved)
        self.view.remove_saved_requested.connect(self._remove_saved)
        self.view.saved_mode_changed.connect(self._set_saved_mode)
        self.view.close_requested.connect(self.close)
        # Zoom of the spectra: a mode that follows new data until zoomed by hand.
        self._zoom_mode: str | None = "full"
        # Lipid band, as a check of spectrum quality.
        self._lipid_on = False
        self._lipid_band = tuple(float(v) for v in self.cfg["spectrometer"]["lipid_band_cm1"])
        self.view.btn_lipid.toggled.connect(self._set_lipid)
        self.view.set_zoom_mode("full")
        for mode, button in self.view.zoom_buttons.items():
            button.clicked.connect(lambda _=False, m=mode: self._set_zoom_mode(m))
        self.view.zoom_manual.connect(lambda: self._set_zoom_mode(None))
        self.act_connect_all.triggered.connect(self.connect_all)
        # Power button in the image window: connects everything, or disconnects it all.
        self._connected = {w: False for w in self.workers}
        for w in self.workers:
            w.connected.connect(lambda on, w=w: self._on_connected(w, on))
            w.disconnect_refused.connect(lambda w=w: self._closing_refused(w))
        self.view.btn_power.clicked.connect(self._toggle_all)
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

    def disconnect_all(self) -> None:
        """Disconnects every instrument. The laser switches emission off first, and a
        cold CCD warms up before it disconnects (Esc or Stop interrupts the warm-up
        and keeps it connected)."""
        self.spec_w.request_abort()
        for w in self.workers:
            w.submit("disconnect")

    def _toggle_all(self) -> None:
        if any(self._connected.values()):
            self.disconnect_all()
        else:
            self.connect_all()
        # The button follows the instruments, not the click.
        self.view.set_all_connected(any(self._connected.values()))

    def _on_connected(self, worker, connected: bool) -> None:
        self._connected[worker] = connected
        self.view.set_all_connected(any(self._connected.values()))
        self._closing_progress()

    # ------------------------------------------------------------------------
    #  Laser and safety
    # ------------------------------------------------------------------------
    def emergency_stop(self) -> None:
        self.laser_w.submit("emergency_off", priority=0)
        self.log("warn", "Laser stop requested")

    def _enable_laser(self, parent: QWidget | None = None) -> None:
        """parent: the window the confirmation opens over (the one clicked on)."""
        if not self._laser_confirmed and self.confirm_dialogs:
            answer = QMessageBox.warning(
                parent or self, "Class 4 laser",
                "You are about to switch on emission of the 532 nm laser.\n\n"
                "Confirm that you are wearing goggles of adequate optical density, that the room "
                "is signposted and that nobody without protection can see the beam.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
            self._laser_confirmed = True
        self.laser_w.submit("enable")

    def _apply_power_from_view(self, mw: float) -> None:
        if self.laser_w.device is None:
            return
        self.laser_panel.spin_power.setValue(mw)
        self.laser_panel.power_requested.emit(self.laser_panel.spin_power.value())

    def _toggle_emission(self) -> None:
        emitting = self.laser_status is not None and self.laser_status.emitting
        if emitting:
            self.laser_w.submit("disable", priority=1)
        else:
            self._enable_laser(parent=self.view)
        # The button follows the laser's status, not the click.
        self.view.set_emission_state(emitting if self.laser_w.device is not None else None)

    def _laser_connected(self, connected: bool) -> None:
        if not connected:
            self.laser_status = None
            self.sb_laser.setText("Laser: disconnected")
            self.sb_laser.setStyleSheet("padding: 0 12px;")
            self.view.set_laser_state(None)
            self.view.set_measured_power(None)

    def _on_laser_status(self, st) -> None:
        self.laser_status = st
        self.laser_panel.update_status(st)
        self.view.trend_power.add(st.power_mw)
        # The measured power beside the power box (none if not emitting).
        self.view.set_measured_power(st.power_mw if st.emitting else None)
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
        self.view.trend_temp.add(st.temperature_c)
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
        self._queued_kinds.append(False)
        self.spec_w.submit("acquire", settings)

    def _background_compatible(self, result: dict) -> bool:
        bg = self.background
        return (bg is not None
                and np.size(bg["counts"]) == np.size(result["counts"])
                and abs(bg["exposure_s"] - result["exposure_s"]) <= 1e-9 * max(1.0, result["exposure_s"])
                and bg["grating"] == result["grating"]
                and bg.get("tracks") == result.get("tracks")
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
        result["lipid_band"] = self._lipid_quality(result)
        if self._lipid_on and result["lipid_band"]:
            self.view.trend_lipid.add(result["lipid_band"]["height_per_s"])
        if result["saturated"]:
            self.log("warn", "Spectrum saturated: reduce the exposure or the power.")
        if result.get("continuous"):
            # A live preview: shown as the current spectrum, saved only by hand.
            self.last_spectrum = result
            self.save_panel.btn_save.setEnabled(True)
            self._redraw_spectrum()
            return
        # A measurement: always saved, and it appears among the saved spectra. If it
        # interrupted the continuous measurement, that one keeps the current spectrum
        # and starts again; otherwise the measurement is the current spectrum too.
        self._save_spectrum(result)
        if self._resume_live:
            self._resume_live = False
            self._acquire(self.spec_panel.settings(continuous=True))
        else:
            self.last_spectrum = result
            self._redraw_spectrum()

    # ------------------------------------------------------------------------
    #  Rows read by the CCD (vertical binning)
    # ------------------------------------------------------------------------
    @staticmethod
    def _saved_tracks() -> list[list[int]] | None:
        try:
            tracks = json.loads(QSettings("RamanControl", "panel").value("ccd/tracks", "") or "null")
            if tracks and all(len(t) == 2 and 1 <= int(t[0]) <= int(t[1]) for t in tracks):
                return [[int(a), int(b)] for a, b in tracks]
        except (ValueError, TypeError):
            pass
        return None

    def _set_tracks(self, tracks) -> None:
        if self.spec_w.device is not None:
            self.spec_w.submit("set_tracks", [tuple(t) for t in tracks])
        else:  # applied on connecting
            self.cfg["spectrometer"]["tracks"] = [list(t) for t in tracks]
            self._on_tracks_changed(tracks)

    def _on_tracks_changed(self, tracks) -> None:
        tracks = [list(t) for t in tracks]
        self.spec_panel.set_rows(tracks)
        QSettings("RamanControl", "panel").setValue("ccd/tracks", json.dumps(tracks))
        if self.ccd_dialog is not None:
            self.ccd_dialog.set_rows(*tracks[0])

    def _open_ccd_image(self) -> None:
        if self.ccd_dialog is None:
            self.ccd_dialog = CcdImageDialog(self._default_tracks, self.view)
            self.ccd_dialog.image_requested.connect(self._read_ccd_image)
            self.ccd_dialog.apply_requested.connect(self._set_tracks)
        self.ccd_dialog.set_rows(*self.cfg["spectrometer"]["tracks"][0])
        self.ccd_dialog.show()
        self.ccd_dialog.raise_()
        self.ccd_dialog.activateWindow()

    def _read_ccd_image(self, exposure_s: float) -> None:
        if self.spec_w.device is None:
            self.ccd_dialog.lbl_status.setText("Connect the spectrometer first.")
            return
        if self._spec_acquiring or self._resume_live:
            self.ccd_dialog.lbl_status.setText("Stop the measurement first (Esc).")
            return
        self.ccd_dialog.set_busy(True)
        self.spec_w.submit("ccd_image", exposure_s)

    def _on_ccd_image(self, result: dict) -> None:
        if self.ccd_dialog is None:
            return
        if "error" in result:
            self.ccd_dialog.set_busy(False)
            self.ccd_dialog.lbl_status.setText(f"Could not read the CCD: {result['error']}")
            return
        wl = result["wavelength_nm"]
        shift = acquisition.raman_shift_cm1(wl, self.laser_nm) if wl is not None else None
        self.ccd_dialog.set_image(result["image"], shift)

    def _on_frame_started(self, index: int, total: int, exposure_s: float) -> None:
        """Progress bar in the image window, for measurements (not the continuous one)."""
        if self._spec_acquiring and not self._spec_continuous:
            self.view.progress.start_frame(index, total, exposure_s)

    # ------------------------------------------------------------------------
    #  Spectral range: presets, the window's range, step and glue
    # ------------------------------------------------------------------------
    def _presets(self) -> list[dict]:
        """Presets of config.toml, then those saved on this computer."""
        own = []
        try:
            own = json.loads(QSettings("RamanControl", "panel").value("spectrograph/presets", "")
                             or "[]")
        except ValueError:
            pass
        return list(self.cfg["spectrometer"].get("window_presets", [])) + own

    def _refresh_presets(self) -> None:
        presets = self._presets()
        self.spec_panel.set_presets(presets)
        combo = self.view.cmb_preset
        current = combo.currentText()
        combo.clear()
        for p in presets:
            combo.addItem(p["name"], p)
        if combo.findText(current) >= 0:
            combo.setCurrentIndex(combo.findText(current))

    def _go_preset(self, preset: dict | None) -> None:
        if not preset or self.spec_w.device is None:
            return
        st = self.spec_status
        if st is None or st.grating != int(preset["grating"]):
            self.spec_w.submit("set_grating", int(preset["grating"]))
        self.spec_w.submit("set_center", float(preset["center_nm"]))
        self.spec_panel.spin_center.setValue(float(preset["center_nm"]))
        self.log("info", f"Window: {preset['name']}")

    def _save_preset(self) -> None:
        st = self.spec_status
        if st is None or st.center_nm is None or st.grating is None:
            self.log("warn", "Connect the spectrometer to save its window as a preset.")
            return
        label = grating_labels(self.cfg).get(st.grating, f"grating {st.grating}")
        suggested = f"{label.split(',')[0]} · {st.center_nm:.0f} nm"
        name, ok = QInputDialog.getText(self, "Save window as preset", "Name:", text=suggested)
        if not ok or not name.strip():
            return
        settings = QSettings("RamanControl", "panel")
        try:
            own = json.loads(settings.value("spectrograph/presets", "") or "[]")
        except ValueError:
            own = []
        own = [p for p in own if p["name"] != name.strip()]
        own.append({"name": name.strip(), "grating": int(st.grating),
                    "center_nm": round(float(st.center_nm), 2)})
        settings.setValue("spectrograph/presets", json.dumps(own))
        self._refresh_presets()
        self.log("ok", f"Preset saved: {name.strip()}")

    def _on_window_changed(self, lo_nm: float, hi_nm: float) -> None:
        self._window_nm = (lo_nm, hi_nm)
        self.spec_panel.set_window(lo_nm, hi_nm)
        a, b = (acquisition.raman_shift_cm1(np.array([lo_nm, hi_nm]), self.laser_nm))
        text = f"Range: {a:.0f} to {b:.0f} cm⁻¹ ({lo_nm:.1f}–{hi_nm:.1f} nm)"
        self.view.lbl_window.setText(text)
        self.spec_panel.set_range(text)
        self._mirror_glue_plan()

    def _mirror_glue_plan(self) -> None:
        plan = self.spec_panel.glue_plan()
        self.view.lbl_glue.setText("" if plan is None else
                                   f"{len(plan)} window{'s' if len(plan) > 1 else ''}")

    def _step_glue(self, from_cm1: float, to_cm1: float) -> None:
        """Measures a range wider than one window by stepping the spectrograph."""
        if self.spec_w.device is None:
            self.log("warn", "Connect the spectrometer first.")
            return
        if self._spec_acquiring or self._resume_live:
            self.log("warn", "Stop the measurement before measuring a range (Esc).")
            return
        plan = self.spec_panel.glue_plan()
        if plan is None:
            self.log("warn", "The width of the window is not known yet: move the spectrograph once.")
            return
        settings = self.spec_panel.settings()
        if settings["auto_exposure"]:
            self.log("warn", "Step and glue uses the fixed exposure, not auto-exposure.")
            settings["auto_exposure"] = False
        self.log("info", f"Step and glue from {from_cm1:.0f} to {to_cm1:.0f} cm⁻¹: "
                         f"{len(plan)} windows at {', '.join(f'{c:.1f}' for c in plan)} nm")
        self._queued_kinds.append(False)
        self.spec_w.submit("step_glue", settings, plan)

    def _on_partial(self, p: dict) -> None:
        """Running average of a measurement, drawn dashed over the spectrum shown when it
        started, with the same background subtraction the result will have. In step and
        glue, the spectrum glued so far."""
        counts = p["counts"]
        if self.spec_panel.subtract_background() and self._background_compatible(p):
            counts = counts - self.background["counts"]
        x, _, _ = self._x_values(p["wavelength_nm"], len(counts))
        self.view.partial_curve.setData(x, counts)
        self._apply_zoom()
        if p.get("windows"):
            self.lbl_spec_info.setText(f"Step and glue: {p['done']}/{p['total']} windows "
                                       "measured (dashed)")
            return
        self.lbl_spec_info.setText(
            f"Measuring: average of {p['done']}/{p['total']} exposures of "
            f"{p['exposure_s']:.3g} s (dashed)"
            + (", then the continuous measurement resumes" if self._resume_live else ""))

    def _acquire(self, settings: dict) -> None:
        """Starts an acquisition. A measurement asked for during the continuous one stops
        it (once the exposure in progress ends), runs, and the continuous one resumes."""
        continuous = bool(settings.get("continuous"))
        if not continuous and self._spec_acquiring:
            if not self._spec_continuous or self._resume_live:
                return  # a measurement is already running or waiting to start
            self._resume_live = True
            self.spec_w.request_abort()  # the measurement waits in the queue behind it
        self._queued_kinds.append(continuous)
        self.spec_w.submit("acquire", settings)
        self._refresh_spec_buttons()

    def _on_spec_connected(self, connected: bool) -> None:
        if not connected:
            self._queued_kinds.clear()
            self._resume_live = False
        self._refresh_spec_buttons()

    def _acquire_or_stop(self) -> None:
        """The image window's Acquire button: a measurement, or Stop while one runs."""
        if self._spec_acquiring and not self._spec_continuous:
            self._stop_measurement()
        elif self._resume_live:
            # Waiting for the continuous exposure in progress to end: the measurement has
            # not started yet, so it is stopped as soon as it does.
            self._stop_when_started = True
        else:
            self._acquire(self.spec_panel.settings())
        self._refresh_spec_buttons()

    def _stop_measurement(self) -> None:
        """Stops the measurement running; a continuous one it had interrupted resumes
        (see _on_spec_acquiring)."""
        self.spec_w.request_abort()

    def _stop_acquisition(self) -> None:
        """Esc or Stop: stops whatever is running, and does not resume the continuous one."""
        self._resume_live = False
        self.spec_w.request_abort()
        self._refresh_spec_buttons()

    def _toggle_continuous(self) -> None:
        if self._resume_live:
            # Interrupted by a measurement: let the measurement finish, without resuming.
            self._resume_live = False
        elif self._spec_acquiring and self._spec_continuous:
            self._stop_acquisition()
        elif not self._spec_acquiring:
            self._acquire(self.spec_panel.settings(continuous=True))
        # The button follows the measurement, not the click.
        self._refresh_spec_buttons()

    def _on_spec_acquiring(self, acquiring: bool) -> None:
        if not acquiring:
            self.view.partial_curve.setData([], [])
            self.view.progress.stop()
            self.view.refresh_squares()
            # A measurement that interrupted the continuous one ended without a result
            # (stopped): the continuous one resumes all the same. (With a result, it
            # has resumed already, in _on_spectrum.)
            if self._spec_acquiring and not self._spec_continuous and self._resume_live:
                self._resume_live = False
                self._spec_acquiring = False
                self._acquire(self.spec_panel.settings(continuous=True))
                return
        self._spec_acquiring = acquiring
        if acquiring:
            self._spec_continuous = self._queued_kinds.popleft() if self._queued_kinds else False
            if not self._spec_continuous and self._stop_when_started:
                self._stop_when_started = False
                self._stop_measurement()
            if not self._spec_continuous and self._resume_live:
                self.lbl_spec_info.setText(
                    "Measuring: the result goes to Saved spectra, then the continuous "
                    "measurement resumes.")
        self._refresh_spec_buttons()

    def _refresh_spec_buttons(self) -> None:
        live = self._resume_live or (self._spec_acquiring and self._spec_continuous)
        measuring = self._resume_live or (self._spec_acquiring and not self._spec_continuous)
        self.view.set_spectrometer_state(self.spec_w.device is not None, self._spec_acquiring,
                                         live, measuring)
        self.spec_panel.set_acquiring(self._spec_acquiring, live and not measuring)
        idle = self.spec_w.device is not None and not self._spec_acquiring and not self._resume_live
        for b in (self.view.btn_preset, self.view.btn_glue):
            b.setEnabled(idle)

    def _x_values(self, wl: np.ndarray | None, n: int) -> tuple[np.ndarray, str, str]:
        if wl is None:
            return np.arange(n, dtype=float), "Pixel", ""
        if self.x_mode == "shift":
            return acquisition.raman_shift_cm1(wl, self.laser_nm), "Raman shift", "cm⁻¹"
        return wl, "Wavelength", "nm"

    # ------------------------------------------------------------------------
    #  Lipid band: a quick check of spectrum quality
    # ------------------------------------------------------------------------
    def _lipid_quality(self, r: dict) -> dict | None:
        """Height (counts/s) and S/N of the lipid band in a spectrum, if it covers it."""
        if r.get("wavelength_nm") is None:
            return None
        shift = acquisition.raman_shift_cm1(r["wavelength_nm"], self.laser_nm)
        lo, hi = self._lipid_band
        return acquisition.band_quality(shift, r["counts_corrected"], lo, hi, r["exposure_s"])

    def _set_lipid(self, on: bool) -> None:
        self._lipid_on = on
        self.view.trend_lipid.setVisible(on)
        if on and self.last_spectrum is not None:
            self.last_spectrum["lipid_band"] = self._lipid_quality(self.last_spectrum)
            if self.last_spectrum["lipid_band"]:
                self.view.trend_lipid.add(self.last_spectrum["lipid_band"]["height_per_s"])
        self._redraw_spectrum()

    def _place_lipid_band(self) -> None:
        """The shaded band, in the units of the x axis."""
        region = self.view.lipid_region
        region.setVisible(self._lipid_on and self.x_mode in ("shift", "nm"))
        lo, hi = self._lipid_band
        if self.x_mode == "nm":
            lo, hi = sorted(float(acquisition.shift_to_nm(v, self.laser_nm)) for v in (lo, hi))
        region.setRegion((lo, hi))

    def _redraw_spectrum(self) -> None:
        self._place_lipid_band()
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
            parts = [f"{r['exposure_s']:.3g} s × {r['accumulations']}"
                     + (f" (stopped after {r['accumulations']} of {r['accumulations_planned']})"
                        if r.get("stopped_early") else ""),
                     f"CCD {r['ccd_temperature_c']:.1f} °C"]
            if r["cosmic_pixels_rejected"]:
                parts.append(f"{r['cosmic_pixels_rejected']} cosmic-ray pixels corrected")
            if r["background_subtracted"]:
                parts.append("background subtracted")
            if self._lipid_on:
                q = r.get("lipid_band")
                lo, hi = self._lipid_band
                parts.append(f"lipid band {q['height_per_s']:.0f} counts/s · S/N {q['snr']:.0f}"
                             if q else f"lipid band ({lo:.0f}–{hi:.0f} cm⁻¹) outside the window")
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
            entries.append((e["label"], x, e["counts"], e["color"], e["mode"], e["tooltip"]))
        self.view.set_saved(entries)
        self._apply_zoom()

    def _set_zoom_mode(self, mode: str | None) -> None:
        self._zoom_mode = mode
        self.view.set_zoom_mode(mode)
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        # The squares first: hidden spectra are left out of the zoom.
        self.view.refresh_squares()
        self.view.apply_zoom(self._zoom_mode, self._in_rayleigh)

    def _in_rayleigh(self, x: np.ndarray) -> np.ndarray:
        """Points of the x axis (as drawn) within the Rayleigh band, |shift| below
        rayleigh_exclude_cm1; none when the axis is in pixels."""
        limit = float(self.cfg["spectrometer"]["rayleigh_exclude_cm1"])
        if self.x_mode == "shift":
            shift = x
        elif self.last_spectrum is not None and self.last_spectrum["wavelength_nm"] is not None:
            shift = acquisition.raman_shift_cm1(x, self.laser_nm)
        else:
            return np.zeros_like(x, dtype=bool)
        return np.abs(shift) < limit

    def _add_saved(self, r: dict, name: str) -> None:
        """Adds a saved spectrum to the plot; every one of the session stays until
        removed.

        Each spectrum keeps its colour while it stays; a newcomer takes the first of the
        len(SERIES) colours left free (removing spectra frees theirs), so the others are
        never repainted. With all in use, the colours repeat in order.
        """
        used = {e["color"] for e in self.saved}
        color = next((c for c in SERIES if c not in used), None)
        if color is None:
            color = SERIES[self._colour_turn % len(SERIES)]
            self._colour_turn += 1
        time = r.get("timestamp", "")[11:16]
        exposure = (f"{r['exposure_s']:.3g} s × {r['accumulations']}"
                    + (f" (of {r['accumulations_planned']}, stopped)" if r.get("stopped_early")
                       else "")
                    if "exposure_s" in r else "")
        tooltip = name + ("\n" + " · ".join(t for t in (time, exposure) if t)
                          if time or exposure else "")
        self.saved.append({"label": name, "mode": "normal", "tooltip": tooltip,
                           "wavelength_nm": r["wavelength_nm"],
                           "counts": r["counts_corrected"], "color": color})
        self._redraw_saved()

    def _remove_saved(self, indices) -> None:
        names = ", ".join(self.saved[i]["label"] for i in indices)
        if self.confirm_dialogs and QMessageBox.question(
                self.view, "Remove spectrum", f"Remove {names} from the plot? "
                "(The files stay saved.)") != QMessageBox.Yes:
            return
        for i in sorted(indices, reverse=True):
            del self.saved[i]
        self._redraw_saved()

    def _set_saved_mode(self, index: int, mode: str) -> None:
        self.saved[index]["mode"] = mode
        self._redraw_saved()

    def _clear_saved(self) -> None:
        self.saved.clear()
        self._redraw_saved()

    def _set_axis(self, mode: str) -> None:
        self.x_mode = mode
        self._redraw_spectrum()
        self._set_zoom_mode("full")  # the old range means nothing in the new units

    def _set_laser_wl(self, nm: float) -> None:
        self.laser_nm = float(nm)
        self._redraw_spectrum()

    def _calibrate_zero(self) -> None:
        r = self.last_spectrum
        if r is None or r["wavelength_nm"] is None:
            self.log("warn", "A spectrum with a wavelength axis is needed for calibration.")
            return
        wl = r["wavelength_nm"]
        if not wl.min() <= self.laser_nm <= wl.max():
            self.log("warn", f"The latest spectrum ({wl.min():.1f}–{wl.max():.1f} nm) does not "
                             f"include the laser line ({self.laser_nm:.2f} nm): move the "
                             "spectrograph to a window that contains 0 cm⁻¹ to calibrate.")
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
    def _on_cam_connected(self, connected: bool) -> None:
        if not connected:
            self._cam_live = False
        self.view.set_camera_state(connected, self._cam_live)

    def _on_cam_live(self, live: bool) -> None:
        self._cam_live = live
        self.view.set_camera_state(self.cam_w.device is not None, live)

    def _toggle_video(self) -> None:
        self.cam_w.submit("stop_live" if self._cam_live else "start_live")
        # The button follows the camera, not the click.
        self.view.set_camera_state(self.cam_w.device is not None, self._cam_live)

    def _on_cam_info(self, info) -> None:
        self.cam_info = info
        self.cam_panel.set_info(info)

    def _on_frame(self, img: np.ndarray) -> None:
        try:
            self.last_frame = img
            if self.cam_info is not None:
                bits = self.cam_info.bit_depth
            else:
                bits = 8 if img.dtype == np.uint8 else 16
            levels = acquisition.display_levels(img, bits, self.cam_panel.auto_levels())
            self.image_view.setImage(img, autoLevels=False, levels=levels,
                                     autoRange=self._first_frame, autoHistogramRange=False)
            if self._first_frame:
                self.image_view.ui.histogram.setHistogramRange(0, 2 ** bits - 1)
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
            "accumulations_planned": r.get("accumulations_planned", r["accumulations"]),
            "stopped_early": r.get("stopped_early", False),
            "combination": "mean with cosmic-ray rejection" if r["cosmic_removal"] else "mean",
            "cosmic_pixels_rejected": r["cosmic_pixels_rejected"],
            "auto_exposure": r["auto_exposure"],
            "saturated": r["saturated"],
            "raw_max_counts": r["raw_max_counts"],
            "grating": r["grating"],
            "grating_label": labels.get(r["grating"]) if r["grating"] else None,
            "center_wavelength_nm": r["center_nm"],
            "glued_centres_nm": r.get("glued_centres_nm"),
            "glue_seam_ratios": r.get("glue_seam_ratios"),
            "lipid_band_cm1": list(self._lipid_band),
            "lipid_band_height_counts_per_s": (r.get("lipid_band") or {}).get("height_per_s"),
            "lipid_band_snr": (r.get("lipid_band") or {}).get("snr"),
            "read_mode": sc["read_mode"],
            "tracks_rows": r.get("tracks") if sc["read_mode"] == "random_track" else None,
            "ccd_temperature_c": r["ccd_temperature_c"],
            "ccd_temp_status": r["ccd_temp_status"],
            "background_subtracted": r["background_subtracted"],
            "simulated": r["simulated"],
        }
        md.update(self._laser_metadata())
        return md

    def _save_spectrum(self, r: dict | None = None) -> None:
        """Saves r, or the latest spectrum shown (Save latest continuous spectrum)."""
        r = r if r is not None else self.last_spectrum
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
            self._add_saved(r, base.name)
            if self.save_panel.chk_attach.isChecked() and self.last_frame is not None:
                img_path = storage.save_image(base, self.last_frame, self._image_metadata())
                self.log("ok", f"Associated image: {img_path.name}")
        except Exception as exc:
            self.log("error", f"Could not save the spectrum: {exc}")

    # ------------------------------------------------------------------------
    #  Orderly shutdown
    # ------------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """The program closes only once every instrument is disconnected.

        The first close disconnects them all in order (laser off and confirmed, camera,
        CCD warmed up first) and the windows stay open meanwhile; they close by
        themselves at the end. If an instrument refuses to disconnect, closing is
        cancelled. Closing again while waiting offers to quit without waiting.
        """
        if self._closing == "done" or not any(self._connected.values()):
            self._finish_close()
            event.accept()
            return
        event.ignore()
        if self._closing == "disconnecting":
            waiting = ", ".join(w.label for w, on in self._connected.items() if on)
            if self._ask("Close the program",
                         f"Still disconnecting: {waiting}. Quit now without waiting?\n\n"
                         "The laser has been told to switch off, but a cold CCD would be "
                         "shut down without warming up.", default_yes=False):
                self._closing = "done"
                self.close()
            return
        self._closing = "disconnecting"
        self.laser_w.submit("emergency_off", priority=0)
        self.log("info", "Closing: disconnecting every instrument first (the CCD warms up "
                         "if it is cold). The windows will close by themselves.")
        self.disconnect_all()

    def _closing_progress(self) -> None:
        """Closes the program once everything is disconnected during closing."""
        if self._closing == "disconnecting" and not any(self._connected.values()):
            self._closing = "done"
            QTimer.singleShot(0, self.close)

    def _closing_refused(self, worker) -> None:
        if self._closing == "disconnecting":
            self._closing = None
            self.log("warn", f"Closing cancelled: the {worker.label.lower()} could not be "
                             "disconnected (see above). The program stays open.")

    def _finish_close(self) -> None:
        self._save_window_geometry()
        self.view.close_for_real()
        self._stop_workers()

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
