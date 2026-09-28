"""Un hilo por instrumento.

La interfaz nunca llama al hardware directamente: envía órdenes con submit()
y recibe resultados por señales de Qt. Así una exposición de 60 s o un puerto
serie lento nunca congelan la ventana. Cada driver se crea y se usa siempre
dentro de su propio hilo (requisito de los SDK de Andor y GenICam).

Las órdenes van en una cola con prioridad: el apagado de emergencia del láser
(prioridad 0) se ejecuta antes que cualquier orden pendiente.
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
    log = Signal(str, str)          # nivel ("info", "ok", "warn", "error"), mensaje
    connected = Signal(bool)

    label = "dispositivo"

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

    # -- API para la interfaz (se llama desde el hilo principal) ------------
    def submit(self, command: str, *args, priority: int = 10, **kwargs) -> None:
        self._queue.put((priority, next(self._seq), command, args, kwargs))

    def stop(self) -> None:
        self._running.clear()

    # -- bucle del hilo --------------------------------------------------------
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
                self.log.emit("error", f"[{self.label}] Orden desconocida: {command}")
                continue
            self._safe(handler, *args, **kwargs)

    def _safe(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            self.log.emit("error", f"[{self.label}] {exc}")
            traceback.print_exc()
            return None

    # -- órdenes comunes -------------------------------------------------------
    def cmd_connect(self) -> None:
        if self.device is not None:
            return
        self.log.emit("info", f"[{self.label}] Conectando…")
        device = self._factory()
        device.connect()
        self.device = device
        for warning in getattr(device, "warnings", []):
            self.log.emit("warn", f"[{self.label}] {warning}")
        sim = " (simulado)" if getattr(device, "simulated", False) else ""
        self.log.emit("ok", f"[{self.label}] Conectado{sim}")
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
                self.log.emit("info", f"[{self.label}] Desconectado")

    # -- ganchos para las subclases ------------------------------------------
    def poll(self) -> None: ...
    def idle(self) -> bool: return False
    def after_connect(self) -> None: ...
    def before_disconnect(self) -> None: ...


# =============================================================================
class LaserWorker(DeviceWorker):
    status = Signal(object)
    label = "Láser"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, float(cfg["poll_interval_s"]), parent)
        self.cfg = cfg

    def poll(self) -> None:
        self.status.emit(self.device.get_status())

    def cmd_enable(self) -> None:
        self.device.enable()
        self.log.emit("warn", "[Láser] Emisión ACTIVADA")
        self._next_poll = 0.0

    def cmd_disable(self) -> None:
        self.device.disable()
        self.log.emit("info", "[Láser] Emisión desactivada")
        self._next_poll = 0.0

    def cmd_set_power(self, mw: float) -> None:
        applied = self.device.set_power(mw)
        if abs(applied - mw) > 1e-6:
            self.log.emit("warn", f"[Láser] Potencia limitada a {applied:.1f} mW "
                                  f"(pedido {mw:.1f} mW, máximo {self.device.max_power_mw:.0f})")
        else:
            self.log.emit("info", f"[Láser] Consigna de potencia: {applied:.1f} mW")
        self._next_poll = 0.0

    def cmd_emergency_off(self) -> None:
        if self.device is None:
            self.log.emit("warn", "[Láser] Paro: el láser no está conectado a este programa. "
                                  "Usa la llave o el interruptor del controlador.")
            return
        self.device.disable()
        self.log.emit("warn", "[Láser] PARO: emisión desactivada")
        self._next_poll = 0.0

    def before_disconnect(self) -> None:
        if self.cfg.get("turn_off_on_disconnect", True):
            self.device.disable()
            self.log.emit("info", "[Láser] Emisión desactivada antes de desconectar")


# =============================================================================
class SpectrometerWorker(DeviceWorker):
    status = Signal(object)
    spectrum = Signal(object)
    progress = Signal(int, int, str)
    acquiring = Signal(bool)
    exposure_suggested = Signal(float)
    warmup_done = Signal(bool)
    label = "Espectrómetro"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, float(cfg["poll_interval_s"]), parent)
        self.cfg = cfg
        self._abort = threading.Event()

    def request_abort(self) -> None:
        """Se llama directamente desde la interfaz (no pasa por la cola)."""
        self._abort.set()

    def _aborted(self) -> bool:
        return self._abort.is_set() or not self._running.is_set()

    def poll(self) -> None:
        self.status.emit(self.device.get_status())

    def cmd_set_cooler(self, on: bool) -> None:
        self.device.set_cooler(on)
        self.log.emit("info", f"[Espectrómetro] Refrigeración {'activada' if on else 'desactivada'}")

    def cmd_set_target(self, temperature_c: float) -> None:
        self.device.set_target(temperature_c)
        self.log.emit("info", f"[Espectrómetro] Temperatura objetivo: {temperature_c:.1f} °C")

    def cmd_set_grating(self, grating: int) -> None:
        self.log.emit("info", f"[Espectrómetro] Cambiando a la red {grating}…")
        self.device.set_grating(grating)
        self.log.emit("ok", f"[Espectrómetro] Red {grating} en posición")
        self._next_poll = 0.0

    def cmd_set_center(self, center_nm: float) -> None:
        self.device.set_center(center_nm)
        self.log.emit("ok", f"[Espectrómetro] Longitud de onda central: {center_nm:.2f} nm")
        self._next_poll = 0.0

    def cmd_warmup(self, then_disconnect: bool = False) -> None:
        """Apaga la refrigeración y espera a una temperatura segura antes de apagar."""
        safe = float(self.cfg["safe_shutdown_temperature_c"])
        self._abort.clear()
        self.device.set_cooler(False)
        self.log.emit("info", f"[Espectrómetro] Calentando el CCD hasta {safe:.0f} °C…")
        ok = True
        while True:
            st = self.device.get_status()
            self.status.emit(st)
            if st.temperature_c is not None and st.temperature_c >= safe:
                break
            if self._abort.is_set():
                ok = False
                self.log.emit("warn", "[Espectrómetro] Calentamiento interrumpido")
                break
            self.progress.emit(0, 0, f"Calentando: {st.temperature_c:.1f} °C")
            time.sleep(1.0)
        self.progress.emit(1, 1, "Listo" if ok else "Interrumpido")
        if ok:
            self.log.emit("ok", "[Espectrómetro] CCD a temperatura segura")
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
                f"El CCD no está estabilizado ({st.temperature_c:.1f} °C). Espera a que llegue a "
                f"{c['target_temperature_c']:.0f} °C o marca «Permitir sin CCD estable» para pruebas.")
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
                    self.log.emit("warn", "[Espectrómetro] Adquisición detenida")
                    self.progress.emit(0, 1, "Detenido")
                    return
                tag = f" · ciclo {iteration}" if s.get("continuous") else ""
                self.progress.emit(i, n_frames, f"Exposición {i + 1}/{n_frames} ({exposure:.3g} s){tag}")
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
            self.progress.emit(n_frames, n_frames, "Listo")
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
                self.log.emit("warn", "[Espectrómetro] Autoexposición detenida")
                return None
            self.progress.emit(0, 0, f"Autoexposición: prueba {step + 1} con {test:.3g} s")
            spectrum, _ = self.device.acquire(test)
            suggested, converged = acquisition.suggest_exposure(spectrum, test, target, sat, t_min, t_max)
            # Las pruebas son cortas; la exposición final puede extrapolarse por linealidad.
            if converged or suggested > max_test:
                self.log.emit("info", f"[Espectrómetro] Autoexposición: {suggested:.3g} s")
                return suggested
            test = suggested
        self.log.emit("warn", f"[Espectrómetro] La autoexposición no convergió; uso {test:.3g} s")
        return test


# =============================================================================
class CameraWorker(DeviceWorker):
    frame = Signal(object)
    snapshot = Signal(object)
    fps = Signal(float)
    info = Signal(object)
    live_changed = Signal(bool)
    label = "Cámara"

    def __init__(self, factory, cfg: dict, parent=None):
        super().__init__(factory, 5.0, parent)
        self.cfg = cfg
        self._live = False
        self._want_snapshot = False
        self._last_emit = 0.0
        self._frame_times: list[float] = []

    def after_connect(self) -> None:
        self.info.emit(self.device.info())

    def cmd_start_live(self) -> None:
        if self._live:
            return
        self.device.start()
        self._live = True
        self._frame_times.clear()
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
        self.log.emit("info", f"[Cámara] Exposición {applied:.3g} ms")

    def cmd_set_gain(self, db: float) -> None:
        applied = self.device.set_gain_db(db)
        if applied is None:
            self.log.emit("warn", "[Cámara] Esta cámara no expone el nodo Gain")
        else:
            self.log.emit("info", f"[Cámara] Ganancia {applied:.1f} dB")

    def cmd_snapshot(self) -> None:
        if self._live:
            self._want_snapshot = True  # se entrega con el siguiente fotograma
            return
        self.device.start()
        try:
            img = self.device.grab(float(self.cfg["grab_timeout_s"]) + 2.0)
        finally:
            self.device.stop()
        if img is None:
            raise RuntimeError("La cámara no entregó ningún fotograma (tiempo agotado)")
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
        # Se limita lo que se dibuja para no saturar la interfaz.
        if now - self._last_emit >= 1.0 / max(1, int(self.cfg["display_fps"])):
            self._last_emit = now
            self.frame.emit(img)
            if len(self._frame_times) > 1:
                span = self._frame_times[-1] - self._frame_times[0]
                self.fps.emit((len(self._frame_times) - 1) / span if span > 0 else 0.0)
        return True

    def before_disconnect(self) -> None:
        if self._live:
            self.cmd_stop_live()
