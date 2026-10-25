"""Statistical post-processing of an ensemble's burned-area cloud (B2, opt-in).

The 20-member FireSim ensemble over-predicts one-day burned area (rank histogram piled at
rank 0) and is too narrow (spread/skill > 1) on historical Alberta replays ([R49]). This
module corrects the members' **log10 area** with a two-parameter member-by-member (MBM)
transform, the classical shift-and-stretch post-processing of Van Schaeybroeck & Vannitsem
(2015, QJRMS 141: 807-818), restricted to an additive shift and a constant stretch:

    y_i = log10(area_i + 10 ha)              (the scoring variable of ensemble_scores)
    m   = mean_i(y_i)
    y'_i = max(1, m + shift + scale * (y_i - m))
    area'_i = 10 ** y'_i - 10 ha

``shift`` moves the whole cloud (central-tendency / bias correction, log10 units: -0.3 is a
factor of ~2 smaller); ``scale`` widens (> 1) or narrows (< 1) it about its mean. The floor
y' >= 1 is zero growth. Because scale > 0 keeps the members' order, each corrected member is
still tied to its own simulated footprint; only its area label changes.

Nothing here runs unless called: the engine, the API and the default ensemble output are
unchanged. Parameters are fitted on calibration fire-days only (``fit_*``), by grid search,
as pre-registered in ``docs/PREREG_ENSEMBLE_B2.md``.

This is statistical calibration (no machine learning): two scalars, a proper score (CRPS,
Gneiting & Raftery 2007) and a coverage target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from firesim.validation.ensemble_scores import AREA_FLOOR_HA, crps_ensemble, log_area

LOG_FLOOR = math.log10(AREA_FLOOR_HA)  # = 1.0: log10(0 ha + 10 ha)

# Pre-registered search grids (docs/PREREG_ENSEMBLE_B2.md §5)
SHIFT_GRID = np.round(np.arange(-1.50, 0.50 + 1e-9, 0.01), 2)
SCALE_GRID = np.round(np.arange(0.50, 4.00 + 1e-9, 0.05), 2)
COVERAGE_TARGET = 0.80


def transform_log_area(y, shift: float, scale: float) -> np.ndarray:
    """MBM transform of one fire-day's member log10 areas (see module docstring)."""
    y = np.asarray(y, dtype=float)
    m = float(np.mean(y))
    return np.maximum(LOG_FLOOR, m + shift + scale * (y - m))


def calibrate_member_areas(member_ha, shift: float, scale: float) -> np.ndarray:
    """Corrected member areas (ha) for one fire-day; identity at shift 0, scale 1."""
    y = transform_log_area(log_area(member_ha), shift, scale)
    return np.maximum(0.0, 10.0 ** y - AREA_FLOOR_HA)


@dataclass(frozen=True)
class DayScore:
    crps: float
    inside: bool  # observation inside the members' P10-P90 (np.percentile, as ensemble_scores)
    below: bool
    above: bool
    rank: int  # members below the observation (ties count half), 0..N


def score_day(y_members, y_obs: float) -> DayScore:
    """Area scores of one fire-day, computed exactly as ``summarize_ensemble`` does."""
    y = np.asarray(y_members, dtype=float)
    lo, hi = np.percentile(y, 10), np.percentile(y, 90)
    rank = int(np.sum(y < y_obs) + 0.5 * np.sum(y == y_obs))
    return DayScore(crps_ensemble(y, y_obs), bool(lo <= y_obs <= hi), bool(y_obs < lo),
                    bool(y_obs > hi), rank)


def day_logs(rows: list[dict], window: str = "17h") -> list[tuple[np.ndarray, float]]:
    """(member log10 areas, observed log10 area) per run-file row at ``window``."""
    out = []
    for r in rows:
        e = r["ens"][window]
        out.append((log_area(e["member_ha"]), float(log_area(e["obs_ha"]))))
    return out


def _stack(days) -> tuple[np.ndarray, np.ndarray]:
    """(D, N) member log areas and (D,) observations; every day must have N members."""
    return (np.array([np.asarray(y, dtype=float) for y, _ in days]),
            np.array([float(o) for _, o in days]))


def _scores_grid(y: np.ndarray, o: np.ndarray, shifts: np.ndarray, scale: float) -> dict:
    """Vectorised day scores for several shifts at one scale: arrays of shape (S, D).

    Identical, element by element, to ``score_day(transform_log_area(...))``."""
    m = y.mean(axis=1, keepdims=True)
    t = np.maximum(LOG_FLOOR, m[None] + shifts[:, None, None] + scale * (y - m)[None])
    oo = o[None, :, None]
    # E|X - X'| over all N^2 pairs from the order statistics (the transform keeps order):
    # (2 / N^2) sum_i (2i - N - 1) x_(i), i = 1..N
    n = y.shape[1]
    ts = np.sort(t, axis=2)
    w = (2.0 * np.arange(1, n + 1) - n - 1) * 2.0 / n ** 2
    crps = np.mean(np.abs(t - oo), axis=2) - 0.5 * (ts @ w)
    lo = np.percentile(t, 10, axis=2)
    hi = np.percentile(t, 90, axis=2)
    ob = o[None, :]
    return {"crps": crps, "inside": (ob >= lo) & (ob <= hi), "below": ob < lo, "above": ob > hi}


def mean_scores(days, shift: float, scale: float) -> dict:
    """Mean CRPS and coverage over fire-days after the transform."""
    y, o = _stack(days)
    g = _scores_grid(y, o, np.array([float(shift)]), scale)
    return {"crps": float(g["crps"][0].mean()), "coverage": float(g["inside"][0].mean()),
            "below_p10": float(g["below"][0].mean()), "above_p90": float(g["above"][0].mean())}


def best_shift(days, scale: float, grid=SHIFT_GRID) -> float:
    """Shift on ``grid`` minimising mean CRPS at a fixed scale (first minimum on ties)."""
    y, o = _stack(days)
    crps = _scores_grid(y, o, np.asarray(grid, dtype=float), scale)["crps"].mean(axis=1)
    return float(grid[int(np.argmin(crps))])


def fit_coverage_target(days, target: float = COVERAGE_TARGET, scale_grid=SCALE_GRID,
                        shift_grid=SHIFT_GRID) -> dict:
    """Primary rule (PREREG §5, arm P1): smallest scale whose calibration coverage reaches
    ``target`` with the CRPS-optimal shift at that scale. Falls back to the largest scale."""
    path = []
    for c in scale_grid:
        d = best_shift(days, float(c), shift_grid)
        sc = mean_scores(days, d, float(c))
        path.append({"scale": float(c), "shift": d, **sc})
        if sc["coverage"] >= target - 1e-12:
            return {"shift": d, "scale": float(c), "reached_target": True, **sc, "path": path}
    last = path[-1]
    return {"shift": last["shift"], "scale": last["scale"], "reached_target": False,
            **{k: last[k] for k in ("crps", "coverage", "below_p10", "above_p90")},
            "path": path}


def fit_min_crps(days, scale_grid=SCALE_GRID, shift_grid=SHIFT_GRID) -> dict:
    """Secondary rule (arm P2, descriptive): joint grid minimum of mean CRPS."""
    best = None
    for c in scale_grid:
        d = best_shift(days, float(c), shift_grid)
        sc = mean_scores(days, d, float(c))
        if best is None or sc["crps"] < best["crps"] - 1e-15:
            best = {"shift": d, "scale": float(c), **sc}
    return best


def footprint_for_area(k_cells, k_obs, cell_ha: float, target_ha: float) -> dict:
    """Burn-probability footprint (cells reached by >= k members) whose area is closest to
    ``target_ha``, k >= 1; its area and its recall of the observed growth.

    Descriptive bridge from a corrected area to a map: the footprints themselves are the
    simulated ones, only the member-count threshold changes."""
    k_cells = np.asarray(k_cells, dtype=float)
    k_obs = np.asarray(k_obs, dtype=float)
    n = len(k_cells) - 1
    areas = np.array([k_cells[k:].sum() * cell_ha for k in range(1, n + 1)])
    j = int(np.argmin(np.abs(areas - target_ha)))
    tot = k_obs[1:].sum() + k_obs[0]
    rec = float(k_obs[j + 1:].sum() / tot) if tot > 0 else math.nan
    return {"k": j + 1, "area_ha": float(areas[j]), "recall": rec}
