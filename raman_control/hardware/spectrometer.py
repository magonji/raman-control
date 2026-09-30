"""Andor CCD (SDK2) + Shamrock spectrograph via pylablib, and their simulator.

pylablib uses the DLLs installed by Andor Solis (atmcd64d.dll, ShamrockCIF64.dll...).
Solis and this program cannot use the camera at the same time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np


class SpectrometerError(RuntimeError):
    pass


TEMP_STATES = {
    "off": "Cooling off",
    "not_reached": "Cooling",
    "not_stabilized": "Stabilising",
    "drifted": "Temperature drift",
    "stabilized": "Stable",
}


@dataclass
class SpectrometerStatus:
    temperature_c: float | None = None
    temp_status: str = "off"
    cooler_on: bool = False
    target_c: float | None = None
    grating: int | None = None
    n_gratings: int | None = None
    center_nm: float | None = None
    has_spectrograph: bool = False
    simulated: bool = False


class AndorShamrockSpectrometer:
    simulated = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cam = None
        self.spec = None
        self.warnings: list[str] = []
        self._target = float(cfg["target_temperature_c"])
        self._exposure: float | None = None
        self._grating: int | None = None
        self._n_gratings: int | None = None
        self._center_nm: float | None = None

    # -- connection ----------------------------------------------------------
    def connect(self) -> None:
        try:
            import pylablib as pll
        except ImportError as exc:
            raise SpectrometerError("pylablib is missing: pip install pylablib") from exc
        dll_dir = self.cfg.get("dll_dir") or ""
        if dll_dir:
            pll.par["devices/dlls/andor_sdk2"] = dll_dir
            pll.par["devices/dlls/andor_shamrock"] = dll_dir
        from pylablib.devices import Andor
        try:
            self.cam = Andor.AndorSDK2Camera(temperature=self._target,
                                             fan_mode=self.cfg.get("fan_mode", "full"))
        except Exception as exc:
            raise SpectrometerError(
                f"Cannot open the Andor camera ({exc}). Is Andor Solis closed? "
                "If it persists, set the DLL folder in dll_dir.") from exc
        try:
            self._apply_read_mode()
            try:
                # The Shamrock is opened after the camera (it may sit on the camera's I2C bus).
                self.spec = Andor.ShamrockSpectrograph()
                self.spec.setup_pixels_from_camera(self.cam)
                self._n_gratings = int(self.spec.get_gratings_number())
                self._refresh_spectrograph()
            except Exception as exc:
                self.spec = None
                self.warnings.append(
                    f"Shamrock spectrograph not available ({exc}); the axis will be in pixels.")
        except Exception:
            self.close()
            raise

    def _apply_read_mode(self) -> None:
        mode = self.cfg.get("read_mode", "random_track")
        if mode == "random_track":
            # Rows as Solis shows them: from 1, both ends included. pylablib wants them
            # from 0 with the end excluded, and passes them on to the SDK in its own terms.
            height = int(self.cam.get_detector_size()[1])
            tracks = []
            for start, end in self.cfg["tracks"]:
                start, end = int(start), int(end)
                if not 1 <= start <= end <= height:
                    raise SpectrometerError(
                        f"Track rows {start}-{end} are not valid: they must go from 1 to "
                        f"{height}, with the start row not after the end row.")
                tracks.append((start - 1, end))
            self.cam.setup_random_track_mode(tracks)
        elif mode == "multi_track":
            self.cam.set_read_mode("multi_track")
            self.cam.setup_multi_track_mode(number=int(self.cfg["mt_number"]),
                                            height=int(self.cfg["mt_height"]),
                                            offset=int(self.cfg["mt_offset"]))
        elif mode == "fvb":
            self.cam.set_read_mode("fvb")
        else:
            raise SpectrometerError(
                f"Unknown read_mode: {mode!r} (use random_track, multi_track or fvb)")

    def _refresh_spectrograph(self) -> None:
        self._grating = int(self.spec.get_grating())
        self._center_nm = float(self.spec.get_wavelength()) * 1e9  # pylablib works in metres

    def close(self) -> None:
        for dev in (self.spec, self.cam):
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        self.spec = self.cam = None

    # -- status and control ----------------------------------------------------
    def get_status(self) -> SpectrometerStatus:
        return SpectrometerStatus(
            temperature_c=float(self.cam.get_temperature()),
            temp_status=str(self.cam.get_temperature_status()),
            cooler_on=bool(self.cam.is_cooler_on()),
            target_c=self._target, grating=self._grating, n_gratings=self._n_gratings,
            center_nm=self._center_nm, has_spectrograph=self.spec is not None)

    def set_cooler(self, on: bool) -> None:
        if on:
            self.cam.set_temperature(self._target, enable_cooler=True)
        else:
            self.cam.set_cooler(False)

    def set_target(self, temperature_c: float) -> None:
        self._target = float(temperature_c)
        self.cam.set_temperature(self._target, enable_cooler=True)

    def set_grating(self, grating: int) -> None:
        if self.spec is None:
            raise SpectrometerError("No spectrograph connected")
        self.spec.set_grating(int(grating))
        self._refresh_spectrograph()

    def set_center(self, wavelength_nm: float) -> None:
        if self.spec is None:
            raise SpectrometerError("No spectrograph connected")
        self.spec.set_wavelength(float(wavelength_nm) * 1e-9)
        self._refresh_spectrograph()

    def wavelengths_nm(self) -> np.ndarray | None:
        if self.spec is None:
            return None
        return np.asarray(self.spec.get_calibration(), dtype=float) * 1e9

    def acquire(self, exposure_s: float) -> tuple[np.ndarray, float]:
        """One exposure. Returns (1D spectrum, maximum value of the raw frame)."""
        if self._exposure != exposure_s:
            self.cam.set_exposure(float(exposure_s))
            self._exposure = exposure_s
        frame = np.atleast_2d(np.asarray(self.cam.snap(timeout=exposure_s + 30.0), dtype=float))
        spectrum = frame.sum(axis=0) if frame.shape[0] > 1 else frame[0]
        return spectrum, float(frame.max())


# =============================================================================
#  Simulator
# =============================================================================
# (position cm-1, relative amplitude, half-width cm-1): low-frequency and
# fingerprint-region bands, so that the whole workflow can be practised.
SIM_PEAKS = [
    (12, 0.45, 2.5), (27, 0.90, 3.0), (51, 0.55, 4.0), (86, 0.35, 5.0),
    (465, 0.70, 6.0), (645, 0.25, 7.0), (797, 0.45, 6.0), (858, 0.35, 7.0),
    (1003, 1.00, 4.0), (1168, 0.45, 6.0), (1236, 0.30, 8.0), (1328, 0.30, 8.0),
    (1450, 0.40, 10.0), (1583, 0.35, 8.0), (1610, 0.55, 7.0), (1655, 0.45, 10.0),
]
SIM_DISPERSION = {1: 1.0, 2: 0.47, 3: 0.25}  # relative spectral width per grating


def sim_raman_profile(shift: np.ndarray) -> np.ndarray:
    prof = np.zeros_like(shift)
    boltzmann = 1.4388 / 295.0  # hc/kT in cm (room temperature)
    for pos, amp, w in SIM_PEAKS:
        prof += amp * w**2 / ((shift - pos) ** 2 + w**2)                       # Stokes
        prof += amp * np.exp(-pos * boltzmann) * w**2 / ((shift + pos) ** 2 + w**2)  # anti-Stokes
    prof += 0.18 * np.exp(-((shift - 1700.0) / 1400.0) ** 2)  # fluorescence background
    notch = 1.0 - np.exp(-(np.abs(shift) / 6.0) ** 4)          # OptiGrate filters
    leak = 3.0 * np.exp(-(shift / 1.2) ** 2)                   # residual Rayleigh
    return prof * notch + leak


class SimulatedSpectrometer:
    simulated = True

    def __init__(self, cfg: dict, sim_cfg: dict, world, laser_nm: float):
        self.cfg = cfg
        self.sim = sim_cfg
        self.world = world
        self.laser_nm = laser_nm
        self.warnings: list[str] = []
        self._target = float(cfg["target_temperature_c"])
        self._temp = 20.0
        self._cooler = False
        self._stable_since: float | None = None
        self._t_last = time.monotonic()
        self._grating = 1
        self._center = float(cfg["default_center_nm"])
        self._n = int(sim_cfg["ccd_pixels"])
        self._rng = np.random.default_rng()

    def connect(self) -> None:
        time.sleep(0.3)
        self._cooler = True  # like the Andor: starts cooling on connection

    def close(self) -> None:
        pass

    def _update(self) -> None:
        now = time.monotonic()
        dt, self._t_last = now - self._t_last, now
        target = self._target if self._cooler else 20.0
        step = float(self.sim["ccd_cooling_rate_c_per_s"]) * (1.0 if self._cooler else 0.5) * dt
        self._temp += float(np.clip(target - self._temp, -step, step))
        if self._cooler and abs(self._temp - self._target) < 0.5:
            self._stable_since = self._stable_since or now
        else:
            self._stable_since = None

    def _state(self) -> str:
        if not self._cooler:
            return "off"
        if self._stable_since is None:
            return "not_reached"
        return "stabilized" if time.monotonic() - self._stable_since > 8.0 else "not_stabilized"

    def get_status(self) -> SpectrometerStatus:
        self._update()
        return SpectrometerStatus(
            temperature_c=self._temp + float(self._rng.normal(0, 0.02)),
            temp_status=self._state(), cooler_on=self._cooler, target_c=self._target,
            grating=self._grating, n_gratings=3, center_nm=self._center,
            has_spectrograph=True, simulated=True)

    def set_cooler(self, on: bool) -> None:
        self._update()
        self._cooler = bool(on)

    def set_target(self, temperature_c: float) -> None:
        self._update()
        self._target = float(temperature_c)
        self._cooler = True

    def set_grating(self, grating: int) -> None:
        if int(grating) not in SIM_DISPERSION:
            raise SpectrometerError(f"The simulated turret only has gratings 1-3 (requested {grating})")
        time.sleep(1.0)  # changing grating takes time
        self._grating = int(grating)

    def set_center(self, wavelength_nm: float) -> None:
        time.sleep(0.5)
        self._center = float(wavelength_nm)

    def wavelengths_nm(self) -> np.ndarray:
        span = float(self.sim["span_nm"]) * SIM_DISPERSION[self._grating]
        return np.linspace(self._center - span / 2, self._center + span / 2, self._n)

    def acquire(self, exposure_s: float) -> tuple[np.ndarray, float]:
        self._update()
        time.sleep(exposure_s)
        wl = self.wavelengths_nm()
        shift = 1e7 / self.laser_nm - 1e7 / wl
        signal = sim_raman_profile(shift) * self.world.laser_power() * exposure_s * 45.0
        dark = 0.05 * 2 ** ((self._temp + 65.0) / 6.0) * exposure_s  # grows if the CCD is warm
        counts = self._rng.poisson(np.clip(signal + dark, 0, None)).astype(float)
        counts += 300.0 + self._rng.normal(0, 4.5, self._n)       # bias + read noise
        for _ in range(self._rng.poisson(0.08 * exposure_s + 0.02)):  # cosmic rays
            i = int(self._rng.integers(0, self._n))
            amp = self._rng.uniform(300, 15000)
            counts[i] += amp
            if i + 1 < self._n:
                counts[i + 1] += amp * self._rng.uniform(0, 0.5)
        sat = float(self.cfg["saturation_counts"])
        raw_max = float(min(counts.max(), sat))
        return np.minimum(counts, sat), raw_max
