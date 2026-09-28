"""Estado compartido entre instrumentos simulados: así el láser simulado
ilumina de verdad la muestra del espectrómetro y de la cámara simulados."""
import threading


class SimWorld:
    def __init__(self):
        self._lock = threading.Lock()
        self._laser_on = False
        self._laser_power_mw = 0.0

    def set_laser(self, on: bool, power_mw: float) -> None:
        with self._lock:
            self._laser_on = bool(on)
            self._laser_power_mw = float(power_mw)

    def laser_power(self) -> float:
        with self._lock:
            return self._laser_power_mw if self._laser_on else 0.0
