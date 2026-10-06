"""One thread per instrument.

The interface never calls the hardware directly: it sends commands with submit()
and receives results through Qt signals. That way a 60 s exposure or a slow
serial port never freezes the window. Each driver is always created and used
inside its own thread (a requirement of the Andor and GenICam SDKs).

Commands go into a priority queue: the laser emergency stop (priority 0) runs
before any pending command.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
import traceback
from datetime import datetime

import numpy as np
from PySide6.QtCore import QThread, Signal

from . import acquisition


class DeviceWorker(QThread):
    log = Signal(str, str)          # level ("info", "ok", "warn", "error"), message
    connected = Signal(bool)

    label = "device"

    def __init__(self, factory, poll_interval: float = 1.0, parent=None):
        super().__init__(parent)
        self._factory = factory
        self.device = None
        self.poll_interval = poll_interval
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._running = threading.Event()
        self._running.set()
        self._next_poll = 0.0

    # -- API for the interface (called from the main thread) ----------------
    def submit(self, command: str, *args, priority: int = 10, **kwargs) -> None:
        self._queue.put((priority, next(self._seq), command, args, kwargs))

    def stop(self) -> None:
        self._running.clear()

    # -- thread loop -----------------------------------------------------------
    def run(self) -> None:
        while self._running.is_set():
            self._drain()
            if self.device is not None and time.monotonic() >= self._next_poll:
                self._safe(self.poll)
                self._next_poll = time.monotonic() + self.poll_interval
            busy = self._safe(self.idle) if self.device is not None else False
            if not busy:
                time.sleep(0.02)
        self._safe(self.cmd_disconnect)

    def _drain(self) -> None:
        while True:
            try:
                _, _, command, args, kwargs = self._queue.get_nowait()
            except queue.Empty:
                return
            handler = getattr(self, f"cmd_{command}", None)
            if handler is None:
                self.log.emit("error", f"[{self.label}] Unknown command: {command}")
                continue
            self._safe(handler, *args, **kwargs)

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self.log.emit("error", f"[{self.label}] {exc}")
            traceback.print_exc()
            return None

    # -- common commands -------------------------------------------------------
    def cmd_connect(self) -> None:
        if self.device is not None:
            return
        self.log.emit("info", f"[{self.label}] Connecting…")
        device = self._factory()
        device.connect()
        self.device = device
        for warning in getattr(device, "warnings", []):
            self.log.emit("warn", f"[{self.label}] {warning}")
        sim = " (simulated)" if getattr(device, "simulated", False) else ""
        self.log.emit("ok", f"[{self.label}] Connected{sim}")
        self._next_poll = 0.0
        self.after_connect()
        self.connected.emit(True)

    def cmd_disconnect(self) -> None:
        if self.device is None:
            return
        try:
            self.before_disconnect()
        finally:
            try:
                self.device.close()
            finally:
                self.device = None
                self.connected.emit(False)
                self.log.emit("info", f"[{self.label}] Disconnected")

    # -- hooks for subclasses ---------------------------------------------------
    def poll(self) -> None: ...
    def idle(self) -> bool: return False
    def after_connect(self) -> None: ...
    def before_disconnect(self) -> None: ...


# =============================================================================
class LaserWorker(DeviceWorker):
    status = Signal(object)
    label = "Laser"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, float(cfg["poll_interval_s"]), parent)
        self.cfg = cfg

    def poll(self) -> None:
        self.status.emit(self.device.get_status())

    def cmd_enable(self) -> None:
        self.device.enable()
        self.log.emit("warn", "[Laser] Emission ON")
        self._next_poll = 0.0

    def cmd_disable(self) -> None:
        self.device.disable()
        self.log.emit("info", "[Laser] Emission off")
        self._next_poll = 0.0

    def cmd_set_power(self, mw: float) -> None:
        applied = self.device.set_power(mw)
        if abs(applied - mw) > 1e-6:
            self.log.emit("warn", f"[Laser] Power limited to {applied:.1f} mW "
                                  f"(requested {mw:.1f} mW, maximum {self.device.max_power_mw:.0f})")
        else:
            self.log.emit("info", f"[Laser] Power setpoint: {applied:.1f} mW")
        self._next_poll = 0.0

    def cmd_emergency_off(self) -> None:
        if self.device is None:
            self.log.emit("warn", "[Laser] Stop: the laser is not connected to this program. "
                                  "Use the key or the switch on the controller.")
            return
        self.device.disable()
        self.log.emit("warn", "[Laser] STOP: emission off")
        self._next_poll = 0.0

    def before_disconnect(self) -> None:
        if self.cfg.get("turn_off_on_disconnect", True):
            self.device.disable()
            self.log.emit("info", "[Laser] Emission switched off before disconnecting")


# =============================================================================
class SpectrometerWorker(DeviceWorker):
    status = Signal(object)
    spectrum = Signal(object)
    progress = Signal(int, int, str)
    acquiring = Signal(bool)
    exposure_suggested = Signal(float)
    warmup_done = Signal(bool)
    label = "Spectrometer"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, float(cfg["poll_interval_s"]), parent)
        self.cfg = cfg
        self._abort = threading.Event()

    def request_abort(self) -> None:
        """Called directly from the interface (does not go through the queue)."""
        self._abort.set()

    def _aborted(self) -> bool:
        return self._abort.is_set() or not self._running.is_set()

    def poll(self) -> None:
        self.status.emit(self.device.get_status())

    def after_connect(self) -> None:
        st = self.device.get_status()
        if st.cooler_on:
            self.log.emit("info", f"[{self.label}] Cooling the CCD to {st.target_c:.0f} °C")
        else:
            self.log.emit("warn", f"[{self.label}] The CCD cooler did not switch on; "
                                  "tick \"Cooling on\".")

    def cmd_set_cooler(self, on: bool) -> None:
        self.device.set_cooler(on)
        self.log.emit("info", f"[Spectrometer] Cooling {'on' if on else 'off'}")

    def cmd_set_target(self, temperature_c: float) -> None:
        self.device.set_target(temperature_c)
        self.log.emit("info", f"[Spectrometer] Target temperature: {temperature_c:.1f} °C")

    def cmd_set_grating(self, grating: int) -> None:
        self.log.emit("info", f"[Spectrometer] Moving to grating {grating}…")
        self.device.set_grating(grating)
        self.log.emit("ok", f"[Spectrometer] Grating {grating} in position")
        self._next_poll = 0.0

    def cmd_set_center(self, center_nm: float) -> None:
        self.device.set_center(center_nm)
        self.log.emit("ok", f"[Spectrometer] Centre wavelength: {center_nm:.2f} nm")
        self._next_poll = 0.0

    def cmd_warmup(self, then_disconnect: bool = False) -> None:
        """Switches cooling off and waits for a safe temperature before shutting down."""
        safe = float(self.cfg["safe_shutdown_temperature_c"])
        self._abort.clear()
        self.device.set_cooler(False)
        self.log.emit("info", f"[Spectrometer] Warming the CCD up to {safe:.0f} °C…")
        ok = True
        while True:
            st = self.device.get_status()
            self.status.emit(st)
            if st.temperature_c is not None and st.temperature_c >= safe:
                break
            if self._abort.is_set():
                ok = False
                self.log.emit("warn", "[Spectrometer] Warm-up interrupted")
                break
            self.progress.emit(0, 0, f"Warming up: {st.temperature_c:.1f} °C")
            time.sleep(1.0)
        self.progress.emit(1, 1, "Done" if ok else "Interrupted")
        if ok:
            self.log.emit("ok", "[Spectrometer] CCD at a safe temperature")
            if then_disconnect:
                self.cmd_disconnect()
        self.warmup_done.emit(ok)

    def cmd_acquire(self, settings: dict) -> None:
        self._abort.clear()
        self.acquiring.emit(True)
        try:
            self._acquire(settings)
        finally:
            self.acquiring.emit(False)

    def _acquire(self, s: dict) -> None:
        dev, c = self.device, self.cfg
        st = dev.get_status()
        self.status.emit(st)
        if st.temp_status != "stabilized" and not s.get("allow_unstable"):
            raise RuntimeError(
                f"The CCD is not stabilised ({st.temperature_c:.1f} °C). Wait until it reaches "
                f"{c['target_temperature_c']:.0f} °C, or tick 'Allow without stable CCD' for testing.")
        purpose = s.get("purpose", "sample")
        exposure = float(s["exposure_s"])
        n_frames = max(1, int(s["accumulations"]))
        sat = float(c["saturation_counts"])
        iteration = 0
        while True:
            iteration += 1
            if s.get("auto_exposure") and purpose == "sample":
                exposure = self._auto_exposure(exposure, s)
                if exposure is None:
                    return
                self.exposure_suggested.emit(exposure)
            frames, raw_max = [], 0.0
            for i in range(n_frames):
                if self._aborted():
                    self.log.emit("warn", "[Spectrometer] Acquisition stopped")
                    self.progress.emit(0, 1, "Stopped")
                    return
                tag = f" · cycle {iteration}" if s.get("continuous") else ""
                self.progress.emit(i, n_frames, f"Exposure {i + 1}/{n_frames} ({exposure:.3g} s){tag}")
                spectrum, frame_max = dev.acquire(exposure)
                frames.append(spectrum)
                raw_max = max(raw_max, frame_max)
            combined, rejected = acquisition.combine_frames(np.array(frames), bool(s.get("cosmic")))
            st = dev.get_status()
            self.status.emit(st)
            self.spectrum.emit({
                "purpose": purpose,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "wavelength_nm": dev.wavelengths_nm(),
                "counts": combined,
                "exposure_s": exposure,
                "accumulations": n_frames,
                "cosmic_removal": bool(s.get("cosmic")),
                "cosmic_pixels_rejected": rejected,
                "auto_exposure": bool(s.get("auto_exposure")) and purpose == "sample",
                "saturated": raw_max >= 0.98 * sat,
                "raw_max_counts": raw_max,
                "grating": st.grating,
                "center_nm": st.center_nm,
                "ccd_temperature_c": st.temperature_c,
                "ccd_temp_status": st.temp_status,
                "simulated": dev.simulated,
            })
            self.progress.emit(n_frames, n_frames, "Done")
            if not s.get("continuous") or self._aborted():
                return

    def _auto_exposure(self, exposure: float, s: dict) -> float | None:
        c = self.cfg
        target = float(s.get("auto_target", c["auto_exposure_target"]))
        t_min, t_max = float(c["auto_exposure_min_s"]), float(c["auto_exposure_max_s"])
        max_test = float(c["auto_exposure_max_test_s"])
        sat = float(c["saturation_counts"])
        test = min(exposure, max_test)
        for step in range(5):
            if self._aborted():
                self.log.emit("warn", "[Spectrometer] Auto-exposure stopped")
                return None
            self.progress.emit(0, 0, f"Auto-exposure: test {step + 1} at {test:.3g} s")
            spectrum, _ = self.device.acquire(test)
            suggested, converged = acquisition.suggest_exposure(spectrum, test, target, sat, t_min, t_max)
            # Test shots are short; the final exposure can be extrapolated linearly.
            if converged or suggested > max_test:
                self.log.emit("info", f"[Spectrometer] Auto-exposure: {suggested:.3g} s")
                return suggested
            test = suggested
        self.log.emit("warn", f"[Spectrometer] Auto-exposure did not converge; using {test:.3g} s")
        return test


# =============================================================================
class CameraWorker(DeviceWorker):
    frame = Signal(object)
    snapshot = Signal(object)
    fps = Signal(float)
    info = Signal(object)
    live_changed = Signal(bool)
    label = "Camera"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, 5.0, parent)
        self.cfg = cfg
        self._live = False
        self._want_snapshot = False
        self._last_emit = 0.0
        self._frame_times: list[float] = []
        # Set by the interface once it has drawn the last frame sent. Without it, frames
        # that take longer to draw than to arrive pile up in the event queue: the image
        # lags further and further behind and the window stops responding.
        self._frame_drawn = threading.Event()
        self._frame_drawn.set()

    def frame_drawn(self) -> None:
        """Called from the interface when it is ready for the next frame."""
        self._frame_drawn.set()

    def after_connect(self) -> None:
        self.info.emit(self.device.info())

    def cmd_start_live(self) -> None:
        if self._live:
            return
        self.device.start()
        self._live = True
        self._frame_times.clear()
        self._frame_drawn.set()
        self.live_changed.emit(True)

    def cmd_stop_live(self) -> None:
        if not self._live:
            return
        self.device.stop()
        self._live = False
        self.live_changed.emit(False)
        self.fps.emit(0.0)

    def cmd_set_exposure(self, ms: float) -> None:
        applied = self.device.set_exposure_ms(ms)
        self.log.emit("info", f"[Camera] Exposure {applied:.3g} ms")

    def cmd_set_gain(self, db: float) -> None:
        applied = self.device.set_gain_db(db)
        if applied is None:
            self.log.emit("warn", "[Camera] This camera does not expose the Gain node")
        else:
            self.log.emit("info", f"[Camera] Gain {applied:.1f} dB")

    def cmd_snapshot(self) -> None:
        if self._live:
            self._want_snapshot = True  # delivered with the next frame
            return
        self.device.start()
        try:
            img = self.device.grab(float(self.cfg["grab_timeout_s"]) + 2.0)
        finally:
            self.device.stop()
        if img is None:
            raise RuntimeError("The camera delivered no frame (timed out)")
        self.frame.emit(img)
        self.snapshot.emit(img)

    def idle(self) -> bool:
        if not self._live:
            return False
        img = self.device.grab(float(self.cfg["grab_timeout_s"]))
        if img is None:
            return True
        now = time.monotonic()
        self._frame_times = [t for t in self._frame_times if now - t < 2.0] + [now]
        if self._want_snapshot:
            self._want_snapshot = False
            self.snapshot.emit(img)
        # Limit what gets drawn so as not to overload the interface: at most display_fps,
        # and never while the previous frame is still waiting to be drawn.
        if (self._frame_drawn.is_set()
                and now - self._last_emit >= 1.0 / max(1, int(self.cfg["display_fps"]))):
            self._last_emit = now
            self._frame_drawn.clear()
            self.frame.emit(img)
            if len(self._frame_times) > 1:
                span = self._frame_times[-1] - self._frame_times[0]
                self.fps.emit((len(self._frame_times) - 1) / span if span > 0 else 0.0)
        return True

    def before_disconnect(self) -> None:
        if self._live:
            self.cmd_stop_live()
