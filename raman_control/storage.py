"""Saving of spectra (CSV + JSON) and images (TIFF + JSON).

Each measurement shares a base name with date and sample, for example:
    20260925_143012_cell03_spectrum.csv / .json
    20260925_143012_cell03_image.tif    / .json
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", (text or "").strip()).strip("_") or "sample"


def new_base(folder: str | Path, sample: str) -> Path:
    """Unique base name within the folder (creates the folder if it does not exist)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now():%Y%m%d_%H%M%S}_{safe_name(sample)}"
    candidate, i = base, 1
    while any(folder.glob(candidate + "_*")):
        candidate, i = f"{base}_{i:02d}", i + 1
    return folder / candidate


def _json_default(obj):
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def _write_json(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False, default=_json_default),
                    encoding="utf-8")


def save_spectrum(base: Path, counts, metadata: dict, wavelength_nm=None,
                  raman_shift_cm1=None, background=None) -> Path:
    columns, names = [], []
    if wavelength_nm is not None:
        columns.append(np.asarray(wavelength_nm, float)); names.append("wavelength_nm")
    if raman_shift_cm1 is not None:
        columns.append(np.asarray(raman_shift_cm1, float)); names.append("raman_shift_cm-1")
    columns.append(np.asarray(counts, float)); names.append("counts")
    if background is not None:
        columns.append(np.asarray(background, float)); names.append("background")
    header = "\n".join(f"{k}: {v}" for k, v in metadata.items()) + "\n" + ",".join(names)
    csv_path = base.with_name(base.name + "_spectrum.csv")
    np.savetxt(csv_path, np.column_stack(columns), delimiter=",", header=header,
               comments="# ", fmt="%.8g")
    _write_json(base.with_name(base.name + "_spectrum.json"), metadata)
    return csv_path


def save_image(base: Path, image, metadata: dict) -> Path:
    image = np.asarray(image)
    try:
        import tifffile
        path = base.with_name(base.name + "_image.tif")
        tifffile.imwrite(path, image, description=json.dumps(metadata, default=_json_default))
    except ImportError:
        path = base.with_name(base.name + "_image.npy")
        np.save(path, image)
    _write_json(base.with_name(base.name + "_image.json"), metadata)
    return path
