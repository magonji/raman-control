"""Cámara del microscopio Teledyne DALSA Genie Nano (GigE Vision / GenICam).

Se usa Harvester, que carga un "GenTL producer" (archivo .cti) y entrega cada
fotograma como array de NumPy. Sapera LT instala un producer para sus cámaras;
cualquier producer GigE Vision genérico también sirve.
"""
from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass

import numpy as np


class CameraError(RuntimeError):
    pass


@dataclass
class CameraInfo:
    model: str = ""
    serial: str = ""
    width: int = 0
    height: int = 0
    pixel_format: str = ""
    bit_depth: int = 8
    exposure_range_ms: tuple[float, float] = (0.01, 1000.0)
    gain_range_db: tuple[float, float] = (0.0, 24.0)
    simulated: bool = False


def find_cti_files() -> list[str]:
    """Busca producers GenTL registrados en las variables de entorno estándar."""
    found: list[str] = []
    for var in ("GENICAM_GENTL64_PATH", "GENICAM_GENTL32_PATH"):
        for folder in os.environ.get(var, "").split(os.pathsep):
            if folder and os.path.isdir(folder):
                found += glob.glob(os.path.join(folder, "*.cti"))
    return sorted(set(found))


def bit_depth_from_format(pixel_format: str) -> int:
    for depth in (16, 14, 12, 10):
        if str(depth) in pixel_format:
            return depth
    return 8


class GenieNanoCamera:
    simulated = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.h = None
        self.ia = None
        self._nm = None
        self.warnings: list[str] = []

    def connect(self) -> None:
        try:
            from harvesters.core import Harvester
        except ImportError as exc:
            raise CameraError("Falta harvesters: pip install harvesters") from exc
        cti = self.cfg.get("cti_path") or ""
        candidates = [cti] if cti else find_cti_files()
        if not candidates:
            raise CameraError(
                "No se encuentra ningún GenTL producer (.cti). Indica su ruta en "
                "[camera] cti_path (ver README).")
        self.h = Harvester()
        for path in candidates:
            self.h.add_file(path)
        self.h.update()
        if not self.h.device_info_list:
            self.h.reset()
            self.h = None
            raise CameraError(
                "No se detecta ninguna cámara GigE. Comprueba que CamExpert está cerrado, "
                "que la cámara tiene IP en la subred de la tarjeta y que el firewall "
                "permite a python.exe usar esa red.")
        create = getattr(self.h, "create", None) or getattr(self.h, "create_image_acquirer")
        self.ia = create(int(self.cfg.get("device_index", 0)))
        self._nm = self.ia.remote_device.node_map
        packet = int(self.cfg.get("packet_size") or 0)
        if packet:
            if not self._set_node("GevSCPSPacketSize", packet):
                self.warnings.append("No se pudo fijar el tamaño de paquete (GevSCPSPacketSize).")
        self.set_exposure_ms(float(self.cfg["exposure_ms"]))
        self.set_gain_db(float(self.cfg["gain_db"]))

    def _node(self, name: str):
        try:
            return getattr(self._nm, name)
        except Exception:
            return None

    def _set_node(self, name: str, value) -> bool:
        node = self._node(name)
        if node is None:
            return False
        try:
            node.value = value
            return True
        except Exception:
            return False

    def _read(self, name: str, default=""):
        node = self._node(name)
        try:
            return node.value if node is not None else default
        except Exception:
            return default

    def _range(self, node, scale: float, default: tuple[float, float]) -> tuple[float, float]:
        try:
            return float(node.min) * scale, float(node.max) * scale
        except Exception:
            return default

    def info(self) -> CameraInfo:
        fmt = str(self._read("PixelFormat", ""))
        exp_node = self._node("ExposureTime")
        gain_node = self._node("Gain")
        return CameraInfo(
            model=str(self._read("DeviceModelName", "Genie Nano")),
            serial=str(self._read("DeviceSerialNumber", "")),
            width=int(self._read("Width", 0) or 0), height=int(self._read("Height", 0) or 0),
            pixel_format=fmt, bit_depth=bit_depth_from_format(fmt),
            exposure_range_ms=self._range(exp_node, 1e-3, (0.01, 1000.0)),
            gain_range_db=self._range(gain_node, 1.0, (0.0, 24.0)))

    def set_exposure_ms(self, ms: float) -> float:
        node = self._node("ExposureTime") or self._node("ExposureTimeAbs")
        if node is None:
            raise CameraError("La cámara no expone ExposureTime")
        us = float(ms) * 1000.0  # GenICam SFNC: microsegundos
        try:
            us = min(max(us, float(node.min)), float(node.max))
        except Exception:
            pass
        node.value = us
        return float(node.value) / 1000.0

    def set_gain_db(self, db: float) -> float | None:
        node = self._node("Gain")
        if node is None:
            return None
        value = float(db)
        try:
            value = min(max(value, float(node.min)), float(node.max))
        except Exception:
            pass
        node.value = value
        return float(node.value)

    def start(self) -> None:
        (getattr(self.ia, "start", None) or self.ia.start_acquisition)()

    def stop(self) -> None:
        (getattr(self.ia, "stop", None) or self.ia.stop_acquisition)()

    def grab(self, timeout_s: float) -> np.ndarray | None:
        fetch = getattr(self.ia, "fetch", None) or self.ia.fetch_buffer
        try:
            with fetch(timeout=timeout_s) as buffer:
                comp = buffer.payload.components[0]
                return np.array(comp.data, copy=True).reshape(comp.height, comp.width)
        except Exception as exc:
            if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower():
                return None
            raise

    def close(self) -> None:
        if self.ia is not None:
            try:
                self.stop()
            except Exception:
                pass
            try:
                self.ia.destroy()
            except Exception:
                pass
            self.ia = None
        if self.h is not None:
            try:
                self.h.reset()
            except Exception:
                pass
            self.h = None


class SimulatedCamera:
    """Campo de microscopio sintético: partículas con movimiento browniano y el
    punto del láser cuando está encendido. Monocromo 12 bits, como una Mono12."""
    simulated = True

    def __init__(self, cfg: dict, sim_cfg: dict, world):
        self.cfg = cfg
        self.world = world
        self.warnings: list[str] = []
        self.W, self.H = int(sim_cfg["camera_width"]), int(sim_cfg["camera_height"])
        self._exp = float(cfg["exposure_ms"])
        self._gain = float(cfg["gain_db"])
        self._rng = np.random.default_rng()
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        self._yy, self._xx = yy, xx
        r2 = ((xx - self.W / 2) / (self.W / 2)) ** 2 + ((yy - self.H / 2) / (self.H / 2)) ** 2
        self._bg = (0.55 * (1.0 - 0.35 * r2)).astype(np.float32)  # viñeteado
        self._particles = [
            {"x": float(self._rng.uniform(40, self.W - 40)),
             "y": float(self._rng.uniform(40, self.H - 40)),
             "r": float(self._rng.uniform(7, 15))} for _ in range(10)]
        self._last = 0.0

    def connect(self) -> None:
        time.sleep(0.2)

    def info(self) -> CameraInfo:
        return CameraInfo(model="Genie Nano M1450 (simulada)", serial="SIM-0001",
                          width=self.W, height=self.H, pixel_format="Mono12", bit_depth=12,
                          exposure_range_ms=(0.02, 1000.0), gain_range_db=(0.0, 24.0),
                          simulated=True)

    def set_exposure_ms(self, ms: float) -> float:
        self._exp = min(max(float(ms), 0.02), 1000.0)
        return self._exp

    def set_gain_db(self, db: float) -> float:
        self._gain = min(max(float(db), 0.0), 24.0)
        return self._gain

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _blob(self, img, x, y, radius, fn) -> None:
        R = int(3 * radius) + 1
        ys = slice(max(0, int(y) - R), min(self.H, int(y) + R))
        xs = slice(max(0, int(x) - R), min(self.W, int(x) + R))
        d2 = ((self._xx[ys, xs] - x) ** 2 + (self._yy[ys, xs] - y) ** 2) / radius**2
        img[ys, xs] += fn(d2)

    def grab(self, timeout_s: float) -> np.ndarray:
        period = max(self._exp / 1000.0, 1 / 40)
        wait = self._last + period - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        img = self._bg.copy()
        for p in self._particles:
            p["x"] = float(np.clip(p["x"] + self._rng.normal(0, 0.7), 20, self.W - 20))
            p["y"] = float(np.clip(p["y"] + self._rng.normal(0, 0.7), 20, self.H - 20))
            self._blob(img, p["x"], p["y"], p["r"],
                       lambda d2: -0.28 * np.exp(-d2 * 1.2) + 0.22 * np.exp(-d2 * 6.0))
        power = self.world.laser_power()
        if power > 0:
            amp = 1.4 * (power / 500.0) ** 0.5
            self._blob(img, self.W / 2, self.H / 2, 5.0, lambda d2: amp * np.exp(-d2))
        scale = 3000.0 * (self._exp / 20.0) * 10 ** (self._gain / 20.0)
        counts = img * scale
        counts += self._rng.normal(0, 1, counts.shape).astype(np.float32) * (np.sqrt(np.abs(counts)) + 3)
        return np.clip(counts, 0, 4095).astype(np.uint16)
