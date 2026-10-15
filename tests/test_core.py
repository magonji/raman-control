"""Hardware-free tests: python -m pytest tests"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raman_control import acquisition, storage  # noqa: E402
from raman_control.config import DEFAULTS, load_config  # noqa: E402
from raman_control.hardware import SimulatedLaser, SimulatedSpectrometer, SimWorld  # noqa: E402
from raman_control.hardware.laser import (LaserStatus, clamp_power, parse_enabled,  # noqa: E402
                                          parse_number)

rng = np.random.default_rng(0)


def lorentz(x, x0, w, a):
    return a * w**2 / ((x - x0) ** 2 + w**2)


# --- unit conversion ---------------------------------------------------------
def test_raman_shift():
    assert acquisition.raman_shift_cm1(532.0, 532.0) == pytest.approx(0.0)
    # 1003 cm-1 (phenylalanine) with 532 nm falls at about 561.98 nm
    assert acquisition.raman_shift_cm1(561.98, 532.0) == pytest.approx(1002.8, abs=1.0)


# --- cosmic rays -----------------------------------------------------------------
def test_despike_removes_spike_keeps_raman_band():
    x = np.arange(1024, dtype=float)
    clean = 500 + lorentz(x, 400, 4.0, 3000)
    noisy = clean + rng.normal(0, 5, x.size)
    spiked = noisy.copy()
    spiked[700] += 8000
    out, fixed = acquisition.despike_single(spiked)
    assert fixed >= 1
    assert abs(out[700] - clean[700]) < 50
    assert out[400] == pytest.approx(noisy[400], rel=0.01)  # the real band is left untouched


def test_combine_frames_rejects_cosmic_in_one_frame():
    base = 1000 + lorentz(np.arange(512.0), 256, 5, 5000)
    frames = np.array([rng.poisson(base).astype(float) for _ in range(5)])
    frames[2, 100] += 20000
    out, rejected = acquisition.combine_frames(frames, reject_cosmics=True)
    assert rejected >= 1
    assert abs(out[100] - base[100]) < 5 * np.sqrt(base[100])


def test_combine_two_frames_takes_minimum_on_spike():
    base = np.full(64, 800.0)
    a, b = base + rng.normal(0, 3, 64), base + rng.normal(0, 3, 64)
    a[10] += 5000
    out, rejected = acquisition.combine_frames([a, b])
    assert rejected == 1 and out[10] < 900


def test_combine_without_rejection_is_plain_mean():
    f = rng.normal(100, 1, (4, 32))
    out, rejected = acquisition.combine_frames(f, reject_cosmics=False)
    assert rejected == 0 and np.allclose(out, f.mean(axis=0))


# --- auto-exposure ---------------------------------------------------------------
def test_auto_exposure_saturated_divides_by_ten():
    y = np.full(256, 300.0)
    y[100:110] = 65535
    t, ok = acquisition.suggest_exposure(y, 1.0, 0.7, 65535, 0.01, 120)
    assert t == pytest.approx(0.1) and not ok


def test_auto_exposure_scales_linearly():
    x = np.arange(512.0)
    y = 300 + lorentz(x, 200, 6, 10000) + rng.normal(0, 3, x.size)
    t, _ = acquisition.suggest_exposure(y, 1.0, 0.7, 65535, 0.01, 120)
    assert t == pytest.approx((0.7 * 65535 - 300) / 10000, rel=0.05)


def test_auto_exposure_respects_max():
    y = 300 + rng.normal(0, 3, 256)
    t, _ = acquisition.suggest_exposure(y, 60.0, 0.7, 65535, 0.01, 120)
    assert t == 120


# --- calibration with the laser line -------------------------------------------------
def test_find_laser_line():
    wl = np.linspace(530.0, 540.0, 1000)
    y = 300 + 20000 * np.exp(-((wl - 532.07) / 0.03) ** 2) + rng.normal(0, 3, wl.size)
    assert acquisition.find_laser_line(wl, y, 532.0) == pytest.approx(532.07, abs=0.005)
    assert acquisition.find_laser_line(wl, 300 + rng.normal(0, 3, wl.size), 532.0) is None


# --- laser safety ------------------------------------------------------------------
def test_power_clamped_in_driver():
    assert clamp_power(900, 500) == 500
    assert clamp_power(-5, 500) == 0
    laser = SimulatedLaser(DEFAULTS["laser"], SimWorld())
    assert laser.set_power(10_000) == 500


def test_config_cannot_raise_limit(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[laser]\nmax_power_mw = 2000\n", encoding="utf-8")
    assert load_config(p)["laser"]["max_power_mw"] == 500


def test_local_config_overrides_shared_one(tmp_path):
    shared = tmp_path / "config.toml"
    shared.write_text('[laser]\nport = "COM4"\n', encoding="utf-8")
    assert load_config(shared)["laser"]["simulate"] is True
    (tmp_path / "config.local.toml").write_text("[laser]\nsimulate = false\n", encoding="utf-8")
    cfg = load_config(shared)
    assert cfg["laser"]["simulate"] is False and cfg["laser"]["port"] == "COM4"
    assert load_config(shared, force_simulation=True)["laser"]["simulate"] is True


def test_parse_laser_replies():
    assert parse_number("250.0mW") == 250.0
    assert parse_number("25.03C") == 25.03
    assert parse_number("") is None
    assert parse_enabled("ENABLED") is True
    assert parse_enabled("DISABLED") is False
    assert parse_enabled("???") is None


def test_measured_power_always_counts_as_emitting():
    assert LaserStatus(enabled=False, power_mw=50.0).emitting
    assert LaserStatus(enabled=None, power_mw=50.0).emitting
    assert LaserStatus(enabled=True, power_mw=0.0).emitting
    assert not LaserStatus(enabled=False, power_mw=0.2).emitting


# --- simulators -----------------------------------------------------------------------
def test_sim_spectrometer_signal_needs_laser():
    world = SimWorld()
    cfg = DEFAULTS["spectrometer"]
    spec = SimulatedSpectrometer(cfg, DEFAULTS["simulation"], world, 532.0)
    spec.connect()
    dark, _ = spec.acquire(0.1)
    world.set_laser(True, 200.0)
    lit, _ = spec.acquire(0.1)
    assert dark.shape == lit.shape == (1024,)
    # Medians, not maxima: a random cosmic ray in the dark spectrum must not fail the test.
    assert np.median(lit) > np.median(dark) + 50


# --- saving ------------------------------------------------------------------------------
def test_save_spectrum_roundtrip(tmp_path):
    wl = np.linspace(532, 615, 100)
    counts = rng.normal(1000, 10, 100)
    base = storage.new_base(tmp_path, "cell 03/a")
    path = storage.save_spectrum(base, counts, {"exposure_s": 1.0, "sample": "x"},
                                 wavelength_nm=wl,
                                 raman_shift_cm1=acquisition.raman_shift_cm1(wl, 532))
    data = np.loadtxt(path, delimiter=",")
    assert data.shape == (100, 3)
    assert np.allclose(data[:, 2], counts, rtol=1e-6)
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["exposure_s"] == 1.0
    assert base.name == "cell_03_a_001"
    # a second save does not overwrite: the next number
    base2 = storage.new_base(tmp_path, "cell 03/a")
    assert base2.name == "cell_03_a_002"


def test_files_go_in_a_folder_per_day_numbered_per_sample(tmp_path):
    from datetime import datetime
    day = datetime(2026, 10, 7, 11, 30)
    first = storage.new_base(tmp_path, "quartz crystal", now=day)
    assert first == tmp_path / "20261007" / "quartz_crystal_001"
    storage.save_spectrum(first, [1.0, 2.0], {})
    storage.save_image(first, np.zeros((4, 4), np.uint8), {})  # same number as its spectrum
    assert storage.new_base(tmp_path, "quartz crystal", now=day).name == "quartz_crystal_002"
    assert storage.new_base(tmp_path, "other", now=day).name == "other_001"
    # a sample whose name is the start of another one does not take its numbers
    storage.save_spectrum(storage.new_base(tmp_path, "quartz crystal_2", now=day), [1.0], {})
    assert storage.new_base(tmp_path, "quartz crystal", now=day).name == "quartz_crystal_002"
    # next day, new folder, numbers start again
    assert storage.new_base(tmp_path, "quartz crystal",
                            now=datetime(2026, 10, 8)).name == "quartz_crystal_001"


def test_save_image(tmp_path):
    img = (rng.random((50, 60)) * 4095).astype(np.uint16)
    path = storage.save_image(storage.new_base(tmp_path, "m"), img, {"exposure_ms": 20})
    import tifffile
    assert np.array_equal(tifffile.imread(path), img)


# --- laser disconnection -------------------------------------------------------------
class _StuckLaser:
    """Accepts OFF but keeps measuring power, like a laser that did not switch off."""
    def __init__(self):
        self.closed = False

    def disable(self):
        pass

    def get_status(self):
        return LaserStatus(power_mw=50.0)

    def close(self):
        self.closed = True


def _laser_worker(device):
    pytest.importorskip("PySide6")
    from raman_control.workers import LaserWorker
    worker = LaserWorker(lambda: device, DEFAULTS["laser"])
    worker.device = device
    worker.OFF_CONFIRM_S = 0.3
    return worker


def test_laser_disconnect_switches_emission_off_first():
    laser = SimulatedLaser(DEFAULTS["laser"], SimWorld())
    laser.set_power(100.0)
    laser.enable()
    worker = _laser_worker(laser)
    worker.cmd_disconnect()
    assert worker.device is None
    assert laser.get_status().power_mw == 0.0


def test_laser_stays_connected_if_emission_off_not_confirmed():
    from raman_control.workers import KeepConnected
    laser = _StuckLaser()
    worker = _laser_worker(laser)
    with pytest.raises(KeepConnected):
        worker.cmd_disconnect()
    assert worker.device is laser and not laser.closed
    worker.cmd_disconnect(force=True)  # closing the program: closed anyway
    assert worker.device is None and laser.closed


# --- camera display ------------------------------------------------------------------
def test_dark_image_is_not_stretched_into_dots():
    # As saved on the Raman PC: Mono8, almost all 0, a few pixels at 1-3.
    img = np.zeros((1088, 1456), np.uint8)
    img[rng.integers(0, 1088, 200), rng.integers(0, 1456, 200)] = 1
    img[5, 7] = img[900, 1000] = 3
    lo, hi = acquisition.display_levels(img, 8, auto=True)
    assert hi - lo >= 25 and 3 / hi < 0.15


def test_auto_contrast_ignores_a_few_hot_pixels():
    img = rng.normal(1000, 50, (400, 400)).clip(0, 4095).astype(np.uint16)
    img[rng.integers(0, 400, 20), rng.integers(0, 400, 20)] = 4095
    lo, hi = acquisition.display_levels(img, 12, auto=True)
    assert hi < 1300 and lo > 700
    assert acquisition.display_levels(img, 12, auto=False) == (0.0, 4095.0)


# --- camera network losses -------------------------------------------------------------
def test_camera_warns_only_on_severe_network_losses():
    pytest.importorskip("PySide6")
    from raman_control.hardware import SimulatedCamera
    from raman_control.workers import CameraWorker

    class Camera(SimulatedCamera):
        resend_requests = 0

        def stream_counters(self):
            return {"StreamPacketResendRequestCount": self.resend_requests,
                    "StreamPacketResendReceivedPacketCount": 8 * self.resend_requests}

    cam = Camera(DEFAULTS["camera"], DEFAULTS["simulation"], SimWorld())
    worker = CameraWorker(lambda: cam, DEFAULTS["camera"])
    messages = []
    worker.log.connect(lambda level, text: messages.append((level, text)))
    worker.device = cam
    worker.cmd_start_live()

    def ten_seconds(frames, requests):
        worker._frames_received += frames
        cam.resend_requests += requests
        worker._check_losses()

    ten_seconds(300, 2)            # as on the Raman PC with jumbo frames: quiet
    assert not messages
    ten_seconds(300, 30)           # as before jumbo frames: warns
    assert messages and messages[-1][0] == "warn" and "30 packet resend" in messages[-1][1]
    ten_seconds(300, 30)           # still bad, but within a minute: not repeated
    assert len(messages) == 1
    worker._next_loss_warning = 0.0
    cam.incomplete_frames += 1     # a frame actually lost: warns even with few resends
    ten_seconds(300, 0)
    assert len(messages) == 2 and "incomplete frames discarded 1" in messages[-1][1]
    worker.cmd_stop_live()


def test_laser_disconnect_mentions_emission_off_only_if_it_was_on():
    for emitting in (False, True):
        laser = SimulatedLaser(DEFAULTS["laser"], SimWorld())
        laser.set_power(100.0)
        if emitting:
            laser.enable()
            laser._power = 100.0  # already at full power, no ramp
        worker = _laser_worker(laser)
        messages = []
        worker.log.connect(lambda level, text: messages.append(text))
        worker.cmd_disconnect()
        assert any("Emission switched off" in m for m in messages) == emitting


# --- CCD disconnection ---------------------------------------------------------------
def _cold_ccd_worker():
    pytest.importorskip("PySide6")
    from raman_control.workers import SpectrometerWorker
    spec = SimulatedSpectrometer(DEFAULTS["spectrometer"], DEFAULTS["simulation"], SimWorld(), 532.0)
    spec.connect()
    spec._temp = DEFAULTS["spectrometer"]["safe_shutdown_temperature_c"] - 0.5  # short warm-up
    worker = SpectrometerWorker(lambda: spec, DEFAULTS["spectrometer"])
    worker.device = spec
    return worker, spec


def test_cold_ccd_warms_up_before_disconnecting():
    worker, spec = _cold_ccd_worker()
    worker.cmd_disconnect()
    assert worker.device is None
    assert spec._temp >= DEFAULTS["spectrometer"]["safe_shutdown_temperature_c"]
    assert not spec._cooler


def test_interrupted_warm_up_keeps_the_ccd_connected():
    from raman_control.workers import KeepConnected
    worker, spec = _cold_ccd_worker()
    spec._temp = -60.0
    worker.progress.connect(lambda *a: worker.request_abort())  # Esc during the warm-up
    with pytest.raises(KeepConnected):
        worker.cmd_disconnect()
    assert worker.device is spec
    assert spec._cooler  # wanted again: cooling from where it got to


def test_forced_disconnect_skips_the_warm_up():
    worker, spec = _cold_ccd_worker()
    spec._temp = -60.0
    worker.cmd_disconnect(force=True)  # "Quit without warming up"
    assert worker.device is None and spec._temp < -50.0


# --- value boxes in the image window -------------------------------------------------------
def test_nice_box_steps_through_round_values_and_takes_any_typed():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.widgets import NiceSpinBox, nice_values
    assert nice_values(0.5, 300) == [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 300]
    box = NiceSpinBox(1, 500, "mW", integer=True)
    committed = []
    box.committed.connect(committed.append)
    box.set_quiet(237)
    assert committed == []                        # shown, not set by the user
    box.stepBy(1)
    assert box.value() == 500 and committed == [500]
    box.stepBy(-2)
    assert box.value() == 100                     # 500 → 200 → 100
    box.stepBy(10)
    assert box.value() == 500                     # not beyond the range
    assert box.textFromValue(237) == "237 mW" and box.valueFromText("237 mW") == 237
    exposure = NiceSpinBox(0.5, 300, "s")
    exposure.set_quiet(0.5)
    exposure.stepBy(1)
    assert exposure.value() == 1 and exposure.textFromValue(2.5) == "2.5 s"
    from raman_control.gui.widgets import format_seconds  # used by the progress bar
    assert format_seconds(300) == "5 min" and format_seconds(90) == "1 min 30 s"


def test_image_window_boxes_mirror_the_control_panel():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.main_window import MainWindow
    w = MainWindow(load_config(None, force_simulation=True), confirm_dialogs=False)
    v, sp = w.view, w.spec_panel
    v.box_exp.setValue(20.0)
    v.box_acc_cont.stepBy(1)
    assert sp.spin_exp.value() == 20.0 and sp.spin_acc_cont.value() == 2
    sp.spin_exp_cont.setValue(0.7)
    assert v.box_exp_cont.value() == pytest.approx(0.7)
    sp.chk_auto.setChecked(True)                  # auto-exposure locks the measurement box
    assert not v.exp_step.isEnabled() and not v.exp_quick.isEnabled()


def test_continuous_settings_are_separate_and_never_auto_exposed():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.spectrometer_panel import SpectrometerPanel
    panel = SpectrometerPanel(load_config(None))
    panel.spin_exp.setValue(20.0)
    panel.spin_acc.setValue(10)
    panel.chk_auto.setChecked(True)
    measure, continuous = panel.settings(), panel.settings(continuous=True)
    assert (measure["exposure_s"], measure["accumulations"], measure["auto_exposure"]) == \
        (20.0, 10, True)
    assert (continuous["exposure_s"], continuous["accumulations"], continuous["auto_exposure"]) == \
        (DEFAULTS["spectrometer"]["continuous_exposure_s"],
         DEFAULTS["spectrometer"]["continuous_accumulations"], False)


# --- measuring during the continuous measurement -----------------------------------------
def test_measurement_interrupts_continuous_then_resumes_it(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.main_window import MainWindow
    cfg = load_config(None, force_simulation=True)
    cfg["general"]["data_dir"] = str(tmp_path)
    w = MainWindow(cfg, confirm_dialogs=False)

    class Worker:  # stands in for the spectrometer thread: records what it is asked
        device = object()

        def __init__(self):
            self.submitted, self.aborts = [], 0

        def submit(self, command, settings=None, **kw):
            self.submitted.append(settings["continuous"])

        def request_abort(self):
            self.aborts += 1

    sw = w.spec_w = Worker()
    sp = w.spec_panel

    def run(continuous, counts=None):
        """The worker starts the next acquisition and delivers a spectrum; a measurement
        then ends, while the continuous one keeps going."""
        w._on_spec_acquiring(True)
        if counts is not None:
            w._on_spectrum({"purpose": "sample", "continuous": continuous,
                            "counts": counts, "wavelength_nm": None, "saturated": False,
                            "timestamp": "2026-10-08T12:00:00", "exposure_s": 1.0,
                            "accumulations": 1, "cosmic_removal": False,
                            "cosmic_pixels_rejected": 0, "auto_exposure": False,
                            "raw_max_counts": 1.0, "grating": 1, "center_nm": 574.0,
                            "ccd_temperature_c": -65.0, "ccd_temp_status": "stabilized",
                            "simulated": True})
            if not continuous:
                w._on_spec_acquiring(False)

    w._toggle_continuous()                      # start the continuous measurement
    run(True, np.ones(4))
    assert w.last_spectrum["continuous"]
    w._acquire(sp.settings())                   # measure during it
    w._acquire(sp.settings())                   # a second click is ignored
    assert sw.aborts == 1 and sw.submitted == [True, False]
    w._on_spec_acquiring(False)                 # the continuous one stops...
    run(False, np.full(4, 7.0))                 # ...the measurement runs and ends
    assert len(w.saved) == 1 and list((tmp_path).rglob("*_spectrum.csv"))
    assert w.last_spectrum["continuous"]        # the current spectrum stays continuous
    assert sw.submitted[-1] is True             # and the continuous one resumes
    run(True)
    assert w.view.btn_continuous.isChecked() and w.view.btn_acquire.isEnabled()
    w._acquire(sp.settings())                   # measure again, then Esc
    w._on_spec_acquiring(False)
    w._on_spec_acquiring(True)
    w._stop_acquisition()
    w._on_spec_acquiring(False)
    assert sw.submitted[-1] is False and not w.view.btn_continuous.isChecked()


# --- zoom of the spectra --------------------------------------------------------------------
def test_zoom_modes_share_the_scale_and_leave_out_rayleigh():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from raman_control.gui.view_window import ViewWindow
    v = ViewWindow("t")
    v.resize(1440, 860)
    v.show()  # pyqtgraph links plots by their place on screen
    app.processEvents()
    x = np.linspace(-50, 1000, 2000)
    current = 100 + 5000 * np.exp(-(x / 2) ** 2) + 300 * np.exp(-((x - 30) / 3) ** 2)
    v.curve.setData(x, current)
    v.set_saved([("s", x, 0.5 * current, "#2a78d6", "normal", "s")])
    in_rayleigh = lambda xs: np.abs(xs) < 10  # noqa: E731
    v.apply_zoom("full")
    (x0, x1), (y0, y1) = v.plot.getViewBox().viewRange()
    assert x0 <= -50 and x1 >= 1000 and y1 >= 5100
    v.plot.setXRange(-40, 100, padding=0)
    v.apply_zoom("fit")
    assert v.plot.getViewBox().viewRange()[0] == pytest.approx([-40, 100])  # X kept
    assert v.plot.getViewBox().viewRange()[1][1] >= 5100                   # Rayleigh in
    v.apply_zoom("fit_nr", in_rayleigh)
    y_top = v.plot.getViewBox().viewRange()[1][1]
    assert 400 <= y_top < 500                                              # band at 30


def test_axis_zoom_buttons_keep_the_baseline_and_the_centre():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from raman_control.gui.view_window import ViewWindow
    v = ViewWindow("t")
    v.resize(1440, 860)
    v.show()
    app.processEvents()
    v.plot.setXRange(0, 1000, padding=0)
    v.plot.setYRange(100, 400, padding=0)
    manual = []
    v.zoom_manual.connect(lambda: manual.append(1))
    v.zoom_axis("y", 1.5)
    assert v.plot.getViewBox().viewRange()[1] == pytest.approx([100, 300])  # foot stays
    v.zoom_axis("x", 2.0)
    assert v.plot.getViewBox().viewRange()[0] == pytest.approx([250, 750])  # about centre
    assert len(manual) == 2  # a manual zoom: the zoom modes switch off


# --- rows read by the CCD (vertical binning) ----------------------------------------------
def test_rows_at_half_maximum_of_the_spot_profile():
    rows = np.arange(1, 201)
    profile = 100 + 1000 * np.exp(-0.5 * ((rows - 69.5) / 2.3) ** 2)
    assert acquisition.rows_at_half_maximum(profile) == (67, 72)


def test_sim_rows_change_signal_and_are_checked():
    from raman_control.hardware.spectrometer import SpectrometerError
    world = SimWorld()
    world.set_laser(True, 100.0)
    cfg = dict(DEFAULTS["spectrometer"], tracks=[[67, 72]])
    spec = SimulatedSpectrometer(cfg, DEFAULTS["simulation"], world, 532.0)
    spec._temp = -65.0
    narrow = spec.acquire(0.01)[0]
    spec.set_tracks([(20, 120)])
    wide = spec.acquire(0.01)[0]
    assert np.median(wide) > np.median(narrow)  # a wider track collects more
    with pytest.raises(SpectrometerError):
        spec.set_tracks([(72, 67)])
    with pytest.raises(SpectrometerError):
        spec.set_tracks([(1, 5000)])
    image = spec.acquire_image(0.01)
    assert image.shape == (DEFAULTS["simulation"]["ccd_rows"], DEFAULTS["simulation"]["ccd_pixels"])


def test_squares_dim_hide_and_show_each_spectrum():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from raman_control.gui.view_window import ViewWindow
    from raman_control.gui.widgets import SpectrumSquare
    v = ViewWindow("t")
    v.resize(1440, 860)
    v.show()
    app.processEvents()
    x = np.linspace(0, 1000, 500)
    v.curve.setData(x, np.full(x.size, 100.0))
    modes = ["normal"]
    v.saved_mode_changed.connect(lambda i, m: (modes.append(m), v.set_saved(
        [("big", x, np.full(x.size, 9000.0), "#2a78d6", m, "big\n12:00")])))
    v.set_saved([("big", x, np.full(x.size, 9000.0), "#2a78d6", "normal", "big\n12:00")])
    squares = v.squares.findChildren(SpectrumSquare)
    assert [sq.toolTip() for sq in squares] == ["Current spectrum", "big\n12:00"]
    saved, current = v._saved_items[0], squares[0]
    v.apply_zoom("full")
    assert v.plot.getViewBox().viewRange()[1][1] >= 9000
    v.squares.findChildren(SpectrumSquare)[1].clicked.emit()      # dim
    saved = v._saved_items[0]
    assert modes[-1] == "dim" and saved.isVisible() and saved.opts["pen"].color().alpha() < 255
    v.squares.findChildren(SpectrumSquare)[1].clicked.emit()      # hide
    saved = v._saved_items[0]
    assert modes[-1] == "hide" and not saved.isVisible()
    v.apply_zoom("full")  # hidden spectra are left out of the zoom
    assert v.plot.getViewBox().viewRange()[1][1] < 9000
    v.squares.findChildren(SpectrumSquare)[1].clicked.emit()      # back to normal
    assert modes[-1] == "normal" and v._saved_items[0].isVisible()
    current.clicked.emit()                                        # the current one too
    assert v._fixed_modes["current"] == "dim"
    v.partial_curve.setData(x, x)                                 # measurement in progress
    v.refresh_squares()
    tips = [sq.toolTip() for sq in v.squares.findChildren(SpectrumSquare)]
    assert tips[:2] == ["Current spectrum", "Measurement in progress (running average)"]


def test_every_saved_spectrum_stays_until_removed(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.main_window import MainWindow
    from raman_control.gui.widgets import SERIES
    w = MainWindow(load_config(None, force_simulation=True), confirm_dialogs=False)
    r = {"wavelength_nm": np.linspace(540, 600, 8), "counts_corrected": np.ones(8),
         "timestamp": "2026-10-14T12:41:05", "exposure_s": 2.0, "accumulations": 10}
    for i in range(22):
        w._add_saved(r, f"s_{i:03d}")
    assert len(w.saved) == 22  # no limit
    colours = [e["color"] for e in w.saved]
    assert colours[:20] == list(SERIES) and colours[20:] == list(SERIES[:2])  # repeat
    assert w.saved[0]["tooltip"] == "s_000\n12:41 · 2 s × 10"
    w._remove_saved([1, 4])
    assert len(w.saved) == 20 and w.saved[1]["label"] == "s_002"
    w._add_saved(r, "s_022")  # takes the first colour freed (SERIES[1] is s_021's again)
    assert w.saved[-1]["color"] == SERIES[4]
    w._set_saved_mode(0, "hide")
    assert not w.view._saved_items[0].isVisible() and len(w.saved) == 21


# --- step and glue ----------------------------------------------------------------------------
def test_plan_windows_covers_the_range_with_overlap():
    centres = acquisition.plan_windows(540.0, 700.0, span_nm=86.0, overlap=0.10)
    assert len(centres) == 2  # 540-626 and 614-700: 12 nm in common
    assert centres[0] - 43 == pytest.approx(540) and centres[-1] + 43 == pytest.approx(700)
    centres = acquisition.plan_windows(540.0, 760.0, span_nm=86.0, overlap=0.10)
    assert len(centres) == 3
    gaps = np.diff(centres)
    assert np.all(gaps <= 86 * 0.9 + 1e-9)  # neighbours overlap by at least 10 %
    assert acquisition.plan_windows(550, 600, 86.0) == [575.0]  # fits in one


def test_glue_windows_joins_without_a_step():
    truth = lambda w: 1000 + 500 * np.exp(-((w - 620) / 2) ** 2)  # noqa: E731
    a = np.linspace(540, 626, 1024)
    b = np.linspace(617, 703, 1024)
    wl, counts, ratios = acquisition.glue_windows([(b, truth(b)), (a, truth(a))])
    assert wl[0] == 540 and wl[-1] == 703 and np.all(np.diff(wl) > 0)
    assert np.allclose(counts, truth(wl), rtol=1e-3)
    assert ratios == [pytest.approx(1.0, abs=1e-3)]


def test_grating_list_in_the_file_replaces_the_default(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[spectrometer.grating_labels]\n1 = "600 l/mm, blaze 500 nm"\n', encoding="utf-8")
    assert load_config(p)["spectrometer"]["grating_labels"] == {"1": "600 l/mm, blaze 500 nm"}


def test_acquire_button_stops_the_measurement():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.main_window import MainWindow
    w = MainWindow(load_config(None, force_simulation=True), confirm_dialogs=False)

    class Worker:
        device = object()

        def __init__(self):
            self.submitted, self.aborts = [], 0

        def submit(self, command, settings=None, *a, **kw):
            self.submitted.append(settings["continuous"])

        def request_abort(self):
            self.aborts += 1

    sw = w.spec_w = Worker()
    w._refresh_spec_buttons()  # the buttons see the (simulated) spectrometer connected
    btn = w.view.btn_acquire
    # Idle: a measurement, then Stop: it stops and nothing resumes.
    btn.click()
    w._on_spec_acquiring(True)
    assert btn.isChecked() and btn.isEnabled()        # it shows Stop
    btn.click()
    assert sw.aborts == 1
    w._on_spec_acquiring(False)
    assert sw.submitted == [False] and not btn.isChecked()
    # During the continuous one: Stop stops the measurement, the continuous one resumes.
    w._toggle_continuous()
    w._on_spec_acquiring(True)
    btn.click()                                        # measure
    w._on_spec_acquiring(False)                        # the continuous one stops...
    w._on_spec_acquiring(True)                         # ...the measurement starts
    btn.click()                                        # Stop
    w._on_spec_acquiring(False)                        # it ends with no result
    assert sw.submitted[-1] is True                    # continuous again
    w._on_spec_acquiring(True)
    # Stop before the measurement starts: it is stopped as soon as it does.
    btn.click()                                        # measure (continuous still running)
    aborts = sw.aborts
    btn.click()                                        # Stop, measurement not started yet
    w._on_spec_acquiring(False)
    w._on_spec_acquiring(True)                         # the measurement starts...
    assert sw.aborts == aborts + 1                     # ...and is stopped at once
    w._on_spec_acquiring(False)
    assert sw.submitted[-1] is True                    # and the continuous one resumes


def test_a_stopped_measurement_keeps_the_accumulations_done():
    pytest.importorskip("PySide6")
    from raman_control.workers import SpectrometerWorker
    spec = SimulatedSpectrometer(DEFAULTS["spectrometer"], DEFAULTS["simulation"], SimWorld(), 532.0)
    spec.connect()
    worker = SpectrometerWorker(lambda: spec, DEFAULTS["spectrometer"])
    worker.device = spec
    results = []
    worker.spectrum.connect(results.append)
    settings = {"exposure_s": 0.01, "accumulations": 5, "cosmic": True, "allow_unstable": True,
                "purpose": "sample", "continuous": False}
    stop_at = {"index": 3}
    worker.frame_started.connect(lambda i, n, e: worker.request_abort()
                                 if i == stop_at["index"] else None)
    # Stopped during the 4th accumulation: an exposure under way cannot be cut short,
    # so it ends and is kept with the three before it.
    worker.cmd_acquire(settings)
    assert len(results) == 1
    r = results[0]
    assert r["accumulations"] == 4 and r["accumulations_planned"] == 5 and r["stopped_early"]
    stop_at["index"] = 0               # stopped during the first one: that one is kept
    worker.cmd_acquire(settings)
    assert results[-1]["accumulations"] == 1 and results[-1]["stopped_early"]
    stop_at["index"] = None            # not stopped: as before
    worker.cmd_acquire(settings)
    assert results[-1]["accumulations"] == 5 and not results[-1]["stopped_early"]


# --- lipid band ---------------------------------------------------------------------------
def test_lipid_band_height_ignores_a_sloping_background():
    shift = np.linspace(1700, 3800, 1024)
    background = 2000 - 0.3 * shift                       # sloping fluorescence
    band = 1500 * np.exp(-0.5 * ((shift - 2850) / 12) ** 2)
    counts = background + band + rng.normal(0, 10, shift.size)
    q = acquisition.band_quality(shift, counts, 2800, 2900, exposure_s=2.0)
    assert q["height_counts"] == pytest.approx(1500, rel=0.05)
    assert q["height_per_s"] == pytest.approx(750, rel=0.05)
    assert 100 < q["snr"] < 200                            # 1500 over a noise of ~10
    assert acquisition.band_quality(np.linspace(-35, 2590, 1024), counts, 2800, 2900, 2.0) is None
