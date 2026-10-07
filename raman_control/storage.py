"""Saving of spectra (CSV + JSON) and images (TIFF + JSON).

Everything goes into a folder per day (yyyymmdd) inside the data folder, named after
the sample with a consecutive number per sample and day, for example:
    C:/Datos_Raman/20261007/quartz_crystal_001_spectrum.csv / .json
    C:/Datos_Raman/20261007/quartz_crystal_001_image.tif    / .json
A spectrum saved together with the camera image shares its number. The date and time
of each measurement are in its JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np


def safe_name(text: str) -> str:
    """The sample name as a file name: spaces, and characters Windows does not allow
    in file names, become underscores. Accented letters are kept."""
    name = re.sub(r'[\s<>:"/\\|?*\x00-\x1f]+', "_", (text or "").strip())
    return re.sub(r"_+", "_", name).strip("_.") or "sample"


def new_base(folder: str | Path, sample: str, now: datetime | None = None,
             create: bool = True) -> Path:
    """Next base name for the sample: <folder>/<yyyymmdd>/<sample>_<nnn>, numbered on
    from the files already there, so the count survives restarting the program.
    Creates the day's folder if it does not exist (unless create is False, to show
    the name only)."""
    day = Path(folder) / f"{now or datetime.now():%Y%m%d}"
    if create:
        day.mkdir(parents=True, exist_ok=True)
    name = safe_name(sample)
    # The number must be followed by the file's kind, so that "a_2_001_spectrum" counts
    # for sample "a_2" and not as number 2 of sample "a".
    pattern = re.compile(re.escape(name) + r"_(\d+)_(?:spectrum|image)\.")
    numbers = [int(m.group(1)) for p in day.glob(name + "_*")
               if (m := pattern.match(p.name))]
    return day / f"{name}_{max(numbers, default=0) + 1:03d}"


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
