"""Spectrum processing, independent of the hardware (and therefore testable).

- raman_shift_cm1: nm → Raman shift.
- combine_frames: averages accumulations while rejecting cosmic rays.
- despike_single: removes cosmic rays from a single spectrum (less robust).
- suggest_exposure: auto-exposure towards a fraction of saturation.
- find_laser_line: locates the residual laser line to calibrate 0 cm-1.
"""
from __future__ import annotations

import numpy as np

READ_NOISE_ADU = 5.0


def raman_shift_cm1(wavelength_nm, laser_nm: float) -> np.ndarray:
    return 1e7 / float(laser_nm) - 1e7 / np.asarray(wavelength_nm, dtype=float)


def despike_single(spectrum, threshold: float = 7.0, max_width: int = 2) -> tuple[np.ndarray, int]:
    """Whitaker–Hayes: detects anomalous jumps in the first difference.

    Only corrects features up to `max_width` pixels wide, so as not to clip
    real Raman bands (which always span several pixels). With 3 or more
    accumulations combine_frames is preferable, as it is far more reliable.
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
            continue  # too wide: probably a real band
        left, right = run[0] - 1, run[-1] + 1
        if left < 0 or right >= y.size:
            continue
        baseline = max(y[left], y[right])
        if y[run].max() <= baseline:
            continue  # cosmic rays only ever add signal
        out[run] = np.interp(run, [left, right], [y[left], y[right]])
        fixed += run.size
    return out, fixed


def combine_frames(frames, reject_cosmics: bool = True, k: float = 5.0) -> tuple[np.ndarray, int]:
    """Mean of N accumulations. Returns (spectrum, number of rejected pixels)."""
    f = np.atleast_2d(np.asarray(frames, dtype=float))
    n = f.shape[0]
    if not reject_cosmics:
        return f.mean(axis=0), 0
    if n == 1:
        return despike_single(f[0])
    med = np.median(f, axis=0)
    # Expected noise ~ Poisson + read noise (in ADU, a conservative estimate).
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
    """Suggests an exposure that brings the highest peak to target_frac·saturation.

    Returns (suggested exposure, converged). If saturated, divides by 10.
    """
    y, _ = despike_single(spectrum)
    peak = float(np.max(y))
    baseline = float(np.percentile(y, 5))
    if peak >= 0.98 * saturation:
        return max(t_min, exposure_s / 10.0), False
    signal = peak - baseline
    if signal <= 5.0 * np.sqrt(max(baseline, 1.0)):
        new = min(t_max, exposure_s * 10.0)  # barely any signal: try much longer
        return new, new == exposure_s
    target = target_frac * saturation - baseline
    new = float(np.clip(exposure_s * target / signal, t_min, t_max))
    converged = abs(new / exposure_s - 1.0) < 0.15 or new in (t_min, t_max)
    return new, converged


def find_laser_line(wavelength_nm, counts, expected_nm: float,
                    window_nm: float = 1.5) -> float | None:
    """Centroid of the strongest peak near the expected laser line."""
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


def display_levels(img: np.ndarray, bit_depth: int, auto: bool,
                   min_span: float = 0.10) -> tuple[float, float]:
    """Black and white levels for showing a camera image.

    Without auto contrast, the camera's full range, as CamExpert shows it. With it,
    the 0.1-99.9 % percentiles, so that a few hot or noisy pixels do not set the
    scale, but never a span narrower than min_span of the full range: stretching an
    almost black image would turn its read noise into bright dots.
    """
    full = float(2 ** int(bit_depth) - 1)
    if not auto:
        return 0.0, full
    lo, hi = (float(v) for v in np.percentile(img[::4, ::4], [0.1, 99.9]))
    span = min_span * full
    if hi - lo < span:
        hi = min(lo + span, full)
        lo = hi - span
    return lo, hi


def rows_at_half_maximum(profile) -> tuple[int, int]:
    """Rows (from 1, both ends included, as Solis) where the spot along the slit stands
    above half its height, measured from the median of the profile (the floor of
    rows it does not reach): a starting point for a confocal-like track."""
    p = np.asarray(profile, float)
    floor = float(np.median(p))
    peak = int(np.argmax(p))
    half = floor + (p[peak] - floor) / 2
    lo = hi = peak
    while lo > 0 and p[lo - 1] > half:
        lo -= 1
    while hi < p.size - 1 and p[hi + 1] > half:
        hi += 1
    return lo + 1, hi + 1


def shift_to_nm(shift_cm1, laser_nm: float):
    """Wavelength of a Raman shift (Stokes for positive shifts)."""
    return 1e7 / (1e7 / laser_nm - np.asarray(shift_cm1, float))


def plan_windows(lo_nm: float, hi_nm: float, span_nm: float,
                 overlap: float = 0.10) -> list[float]:
    """Centres (nm) of the spectrograph windows, each span_nm wide, that cover lo_nm to
    hi_nm with the given overlap between neighbours, spread evenly."""
    lo_nm, hi_nm = min(lo_nm, hi_nm), max(lo_nm, hi_nm)
    if hi_nm - lo_nm <= span_nm:
        return [(lo_nm + hi_nm) / 2]
    step = span_nm * (1 - overlap)
    n = int(np.ceil((hi_nm - lo_nm - span_nm) / step)) + 1
    first, last = lo_nm + span_nm / 2, hi_nm - span_nm / 2
    return list(np.linspace(first, last, n))


def glue_windows(windows) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Joins spectrograph windows [(wavelength_nm, counts), ...] into one spectrum.

    Where two windows overlap, the result fades from the first to the second (the
    second resampled onto the first's points), so there is no step at the seam; no
    intensity is rescaled. Returns wavelength, counts and, for each seam, the ratio of
    the second window to the first over the overlap (1 when they agree; far from 1
    points to vignetting at the edges of the CCD).
    """
    pairs = sorted(((np.asarray(w, float), np.asarray(c, float)) for w, c in windows),
                   key=lambda p: p[0].min())
    wl, counts = (a.copy() for a in pairs[0])
    order = np.argsort(wl)
    wl, counts = wl[order], counts[order]
    ratios = []
    for w2, c2 in pairs[1:]:
        o = np.argsort(w2)
        w2, c2 = w2[o], c2[o]
        in_overlap = wl >= w2[0]
        if in_overlap.any():
            x = wl[in_overlap]
            other = np.interp(x, w2, c2)
            ratios.append(float(np.median(other) / np.median(counts[in_overlap]))
                          if np.median(counts[in_overlap]) else float("nan"))
            fade = (x - x[0]) / max(x[-1] - x[0], 1e-12)  # 0 → first window, 1 → second
            counts[in_overlap] = (1 - fade) * counts[in_overlap] + fade * other
        beyond = w2 > wl[-1]
        wl = np.concatenate([wl, w2[beyond]])
        counts = np.concatenate([counts, c2[beyond]])
    return wl, counts, ratios
