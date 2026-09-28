"""Láser Laser Quantum (torus/gem, controlador SMD12) por RS-232, y su simulador.

El protocolo es texto ASCII: se envía un comando terminado en CR/LF y se lee
una línea de respuesta. Los comandos concretos están en config.toml para poder
ajustarlos al manual sin tocar el código.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


class LaserError(RuntimeError):
    pass


@dataclass
class LaserStatus:
    enabled: bool | None = None
    power_mw: float | None = None
    setpoint_mw: float | None = None
    laser_temp_c: float | None = None
    psu_temp_c: float | None = None
    raw_status: str = ""
    simulated: bool = False

    @property
    def emitting(self) -> bool:
        """Mejor estimación de si hay emisión (si el estado no se entiende, mira la potencia)."""
        if self.enabled is not None:
            return self.enabled
        return (self.power_mw or 0.0) > 1.0


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(text)
    return float(match.group()) if match else None


def parse_enabled(text: str | None) -> bool | None:
    if not text:
        return None
    t = text.strip().upper()
    if "DISABLED" in t:
        return False
    if "ENABLED" in t:
        return True
    if t in ("ON", "1"):
        return True
    if t in ("OFF", "0"):
        return False
    return None


def clamp_power(mw: float, max_mw: float) -> float:
    """El límite se aplica aquí, en el driver: ninguna ruta del programa lo salta."""
    return min(max(float(mw), 0.0), float(max_mw))


class LaserQuantumLaser:
    simulated = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cmds = cfg["commands"]
        self.max_power_mw = float(cfg["max_power_mw"])
        self._ser = None
        self._setpoint: float | None = None

    def connect(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise LaserError("Falta pyserial: pip install pyserial") from exc
        port = self.cfg["port"]
        try:
            self._ser = serial.Serial(
                port=port, baudrate=int(self.cfg["baudrate"]), bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=float(self.cfg["timeout_s"]), write_timeout=1.0)
        except serial.SerialException as exc:
            raise LaserError(
                f"No se puede abrir {port}: {exc}. Cierra la RemoteApp Laser Control "
                "o cualquier otro programa que use ese puerto.") from exc
        time.sleep(0.2)
        self._ser.reset_input_buffer()
        reply = self._query(self.cmds["get_power"])
        if parse_number(reply) is None:
            self.close()
            raise LaserError(
                f"El láser no responde bien en {port} (respuesta: {reply!r}). Revisa puerto, "
                "baudios, cable y los comandos de [laser.commands] en config.toml.")
        if self.cmds.get("set_power_mode"):
            self._query(self.cmds["set_power_mode"])

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def _query(self, command: str) -> str:
        if self._ser is None:
            raise LaserError("El láser no está conectado")
        self._ser.reset_input_buffer()
        self._ser.write((command + self.cfg["eol"]).encode("ascii"))
        self._ser.flush()
        return self._ser.readline().decode("ascii", errors="replace").strip()

    def enable(self) -> None:
        self._query(self.cmds["on"])

    def disable(self) -> None:
        self._query(self.cmds["off"])

    def set_power(self, mw: float) -> float:
        value = clamp_power(mw, self.max_power_mw)
        self._query(self.cmds["set_power"].format(mw=value))
        self._setpoint = value
        return value

    def get_status(self) -> LaserStatus:
        st = LaserStatus(setpoint_mw=self._setpoint)
        st.power_mw = parse_number(self._query(self.cmds["get_power"]))
        st.laser_temp_c = parse_number(self._query(self.cmds["get_laser_temp"]))
        st.psu_temp_c = parse_number(self._query(self.cmds["get_psu_temp"]))
        if self.cmds.get("get_status"):
            st.raw_status = self._query(self.cmds["get_status"])
            st.enabled = parse_enabled(st.raw_status)
        return st


class SimulatedLaser:
    """Imita el comportamiento: rampa de potencia, calentamiento de la fuente, ruido."""
    simulated = True

    def __init__(self, cfg: dict, world):
        self.cfg = cfg
        self.world = world
        self.max_power_mw = float(cfg["max_power_mw"])
        self._enabled = False
        self._setpoint = 0.0
        self._power = 0.0
        self._psu = 24.5
        self._t_last = time.monotonic()

    def connect(self) -> None:
        time.sleep(0.2)

    def close(self) -> None:
        self._enabled = False
        self._sync()

    def enable(self) -> None:
        self._enabled = True
        self._sync()

    def disable(self) -> None:
        self._enabled = False
        self._sync()

    def set_power(self, mw: float) -> float:
        self._setpoint = clamp_power(mw, self.max_power_mw)
        self._sync()
        return self._setpoint

    def _sync(self) -> None:
        now = time.monotonic()
        dt, self._t_last = now - self._t_last, now
        target = self._setpoint if self._enabled else 0.0
        step = 200.0 * dt  # rampa de ~200 mW/s
        self._power += max(-step, min(step, target - self._power))
        psu_target = 25.0 + (6.0 if self._enabled else 0.0)
        self._psu += (psu_target - self._psu) * min(1.0, dt / 20.0)
        self.world.set_laser(self._enabled, self._power)

    def get_status(self) -> LaserStatus:
        self._sync()
        power = max(0.0, self._power + random.gauss(0, 0.002 * max(self._power, 1.0)))
        return LaserStatus(
            enabled=self._enabled, power_mw=power if self._enabled else 0.0,
            setpoint_mw=self._setpoint, laser_temp_c=22.0 + random.gauss(0, 0.02),
            psu_temp_c=self._psu, raw_status="ENABLED" if self._enabled else "DISABLED",
            simulated=True)
