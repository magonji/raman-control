"""Procesado de espectros, independiente del hardware (y por eso testeable).

- raman_shift_cm1: nm → desplazamiento Raman.
- combine_frames: promedia acumulaciones rechazando rayos cósmicos.
- despike_single: quita rayos cósmicos de un solo espectro (menos robusto).
- suggest_exposure: autoexposición hacia una fracción de la saturación.
- find_laser_line: localiza la línea láser residual para calibrar el 0 cm-1.
"""
from __future__ import annotations

import numpy as np

READ_NOISE_ADU = 5.0


def raman_shift_cm1(wavelength_nm, laser_nm: float) -> np.ndarray:
    return 1e7 / float(laser_nm) - 1e7 / np.asarray(wavelength_nm, dtype=float)


def despike_single(spectrum, threshold: float = 7.0, max_width: int = 2) -> tuple[np.ndarray, int]:
    """Whitaker–Hayes: detecta saltos anómalos en la primera diferencia.

    Solo corrige estructuras de hasta `max_width` píxeles, para no recortar
    bandas Raman reales (que siempre ocupan varios píxeles). Con 3 o más
    acumulaciones es preferible combine_frames, que es mucho más fiable.
    """
    y = np.asarray(spectrum, dtype=float)
    out = y.copy()
    if y.size < 8:
        return out, 0
    dy = np.diff(y)
    med = np.median(dy)
    mad = np.median(np.abs(dy - med))
    if mad <= 0:
        return out, 0
    flagged = np.abs(0.6745 * (dy - med) / mad) > threshold
    mask = np.zeros(y.size, dtype=bool)
    mask[:-1] |= flagged
    mask[1:] |= flagged
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return out, 0
    fixed = 0
    for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
        if run.size > max_width + 2:
            continue  # demasiado ancho: probablemente una banda real
        left, right = run[0] - 1, run[-1] + 1
        if left < 0 or right >= y.size:
            continue
        baseline = max(y[left], y[right])
        if y[run].max() <= baseline:
            continue  # los rayos cósmicos solo suman señal
        out[run] = np.interp(run, [left, right], [y[left], y[right]])
        fixed += run.size
    return out, fixed


def combine_frames(frames, reject_cosmics: bool = True, k: float = 5.0) -> tuple[np.ndarray, int]:
    """Media de N acumulaciones. Devuelve (espectro, nº de píxeles rechazados)."""
    f = np.atleast_2d(np.asarray(frames, dtype=float))
    n = f.shape[0]
    if not reject_cosmics:
        return f.mean(axis=0), 0
    if n == 1:
        return despike_single(f[0])
    med = np.median(f, axis=0)
    # Ruido esperado ~ Poisson + lectura (en ADU, estimación conservadora).
    sigma = np.sqrt(np.maximum(np.abs(med), 1.0)) + READ_NOISE_ADU
    if n == 2:
        a, b = f
        bad = np.abs(a - b) > k * sigma * np.sqrt(2.0)
        out = (a + b) / 2.0
        out[bad] = np.minimum(a, b)[bad]
        return out, int(bad.sum())
    bad = f > med + k * sigma
    kept = np.where(bad, np.nan, f)
    return np.nanmean(kept, axis=0), int(bad.sum())


def suggest_exposure(spectrum, exposure_s: float, target_frac: float, saturation: float,
                     t_min: float, t_max: float) -> tuple[float, bool]:
    """Propone una exposición que lleve el pico más alto a target_frac·saturación.

    Devuelve (exposición sugerida, convergido). Si hay saturación, divide por 10.
    """
    y, _ = despike_single(spectrum)
    peak = float(np.max(y))
    baseline = float(np.percentile(y, 5))
    if peak >= 0.98 * saturation:
        return max(t_min, exposure_s / 10.0), False
    signal = peak - baseline
    if signal <= 5.0 * np.sqrt(max(baseline, 1.0)):
        new = min(t_max, exposure_s * 10.0)  # apenas hay señal: probar mucho más largo
        return new, new == exposure_s
    target = target_frac * saturation - baseline
    new = float(np.clip(exposure_s * target / signal, t_min, t_max))
    converged = abs(new / exposure_s - 1.0) < 0.15 or new in (t_min, t_max)
    return new, converged


def find_laser_line(wavelength_nm, counts, expected_nm: float,
                    window_nm: float = 1.5) -> float | None:
    """Centroide del pico más intenso cerca de la línea láser esperada."""
    wl = np.asarray(wavelength_nm, dtype=float)
    y = np.asarray(counts, dtype=float)
    sel = np.flatnonzero(np.abs(wl - expected_nm) <= window_nm)
    if sel.size < 5:
        return None
    base = float(np.median(y[sel]))
    j = int(sel[np.argmax(y[sel])])
    if y[j] - base < 10.0 * np.sqrt(max(base, 1.0)):
        return None
    lo, hi = max(j - 3, 0), min(j + 4, y.size)
    w = np.clip(y[lo:hi] - base, 0, None)
    if w.sum() <= 0:
        return None
    return float(np.sum(wl[lo:hi] * w) / w.sum())
