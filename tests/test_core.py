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
    assert "/" not in base.name and " " not in base.name
    # a second save within the same second does not overwrite
    base2 = storage.new_base(tmp_path, "cell 03/a")
    assert base2 != base


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
    spec._temp = -20.5  # just below the safe -20 °C, so the warm-up is short
    worker = SpectrometerWorker(lambda: spec, DEFAULTS["spectrometer"])
    worker.device = spec
    return worker, spec


def test_cold_ccd_warms_up_before_disconnecting():
    worker, spec = _cold_ccd_worker()
    worker.cmd_disconnect()
    assert worker.device is None
    assert spec._temp >= -20.0 and not spec._cooler


def test_interrupted_warm_up_keeps_the_ccd_connected():
    from raman_control.workers import KeepConnected
    worker, spec = _cold_ccd_worker()
    spec._temp = -60.0
    worker.progress.connect(lambda *a: worker.request_abort())  # Esc during the warm-up
    with pytest.raises(KeepConnected):
        worker.cmd_disconnect()
    assert worker.device is spec


def test_forced_disconnect_skips_the_warm_up():
    worker, spec = _cold_ccd_worker()
    spec._temp = -60.0
    worker.cmd_disconnect(force=True)  # "Quit without warming up"
    assert worker.device is None and spec._temp < -50.0


# --- sliders in the image window ---------------------------------------------------------
def test_value_sliders_cover_their_ranges():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from raman_control.gui.widgets import ValueSlider, format_seconds
    power = ValueSlider("Laser power", 1.0, 500.0, str, log=True, step=1.0)
    exposure = ValueSlider("Exposure", 0.5, 300.0, format_seconds, log=True)
    for slider, lo, hi in ((power, 1.0, 500.0), (exposure, 0.5, 300.0)):
        assert slider._to_value(0) == lo and slider._to_value(slider.TICKS) == hi
    # Logarithmic: half way is the geometric mean, so the low end gets half the travel.
    assert power._to_value(500) == pytest.approx(22, abs=1)
    assert power._to_value(137) == round(power._to_value(137))  # whole mW
    exposure.set_value(2.5)
    assert exposure.value() == 2.5 and exposure.lbl_value.text() == "2.5 s"
    assert format_seconds(300) == "5 min" and format_seconds(90) == "1 min 30 s"


def test_default_setpoint_sent_on_connecting_unless_already_emitting():
    for emitting in (False, True):
        laser = SimulatedLaser(DEFAULTS["laser"], SimWorld())
        laser.set_power(20.0)
        if emitting:
            laser.enable()
            laser._power = 20.0
        worker = _laser_worker(laser)
        messages = []
        worker.log.connect(lambda level, text: messages.append(text))
        worker.after_connect()
        assert laser.get_status().setpoint_mw == (20.0 if emitting else 500.0)
        assert any("not sent" in m for m in messages) == emitting
