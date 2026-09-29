"""Loads the configuration (TOML) on top of safe default values."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "general": {"data_dir": "raman_data", "simulate_all": False,
                "log_file": "raman_control.log"},
    "laser": {
        "simulate": True, "port": "COM4", "baudrate": 9600, "timeout_s": 0.5,
        "eol": "\r", "max_power_mw": 500.0, "wavelength_nm": 532.0,
        "poll_interval_s": 1.0, "turn_off_on_disconnect": True,
        "commands": {
            "on": "ON", "off": "OFF", "set_power": "POWER={mw:.0f}",
            "get_power": "POWER?", "get_laser_temp": "LASTEMP?",
            "get_psu_temp": "PSUTEMP?", "get_status": "STATUS?",
            "set_power_mode": "CONTROL=POWER",
        },
    },
    "spectrometer": {
        "simulate": True, "dll_dir": "", "target_temperature_c": -65.0,
        "safe_shutdown_temperature_c": -20.0, "fan_mode": "full",
        "read_mode": "multi_track", "mt_number": 1, "mt_height": 20, "mt_offset": 0,
        "default_exposure_s": 1.0, "saturation_counts": 65535,
        "poll_interval_s": 1.0, "default_center_nm": 574.0,
        "auto_exposure_target": 0.70, "auto_exposure_min_s": 0.01,
        "auto_exposure_max_s": 120.0, "auto_exposure_max_test_s": 2.0,
        "grating_labels": {"1": "600 l/mm, blaze 500 nm",
                           "2": "1200 l/mm, blaze 500 nm",
                           "3": "2160 l/mm, blaze 500 nm"},
    },
    "camera": {
        "simulate": True, "cti_path": "", "device_index": 0, "exposure_ms": 20.0,
        "gain_db": 0.0, "packet_size": 0, "display_fps": 25, "grab_timeout_s": 1.0,
    },
    "simulation": {
        "ccd_cooling_rate_c_per_s": 4.0, "ccd_pixels": 1024, "span_nm": 86.0,
        "camera_width": 960, "camera_height": 720,
    },
}

INSTRUMENTS = ("laser", "spectrometer", "camera")
LOCAL_CONFIG = "config.local.toml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, force_simulation: bool = False) -> dict:
    """Returns the merged configuration. Without a file, everything stays simulated.

    Next to the given file, an optional config.local.toml (ignored by git) holds what
    belongs to one computer only, such as simulate = false on the Raman PC, and is
    applied on top.
    """
    cfg = copy.deepcopy(DEFAULTS)
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Configuration file not found: {p}")
        for file in (p, p.with_name(LOCAL_CONFIG)):
            if file.is_file():
                with file.open("rb") as fh:
                    cfg = _deep_merge(cfg, tomllib.load(fh))
    if force_simulation or cfg["general"].get("simulate_all"):
        for name in INSTRUMENTS:
            cfg[name]["simulate"] = True
    # The maximum power can never exceed 500 mW, even if the file is edited.
    cfg["laser"]["max_power_mw"] = min(float(cfg["laser"]["max_power_mw"]), 500.0)
    return cfg


def grating_labels(cfg: dict) -> dict[int, str]:
    labels = cfg["spectrometer"].get("grating_labels", {})
    return {int(k): str(v) for k, v in labels.items()}
