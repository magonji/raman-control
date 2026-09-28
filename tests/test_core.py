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
from raman_control.hardware.laser import clamp_power, parse_enabled, parse_number  # noqa: E402

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


def test_parse_laser_replies():
    assert parse_number("250.0mW") == 250.0
    assert parse_number("25.03C") == 25.03
    assert parse_number("") is None
    assert parse_enabled("ENABLED") is True
    assert parse_enabled("DISABLED") is False
    assert parse_enabled("???") is None


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
    assert lit.max() > dark.max() + 500


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
