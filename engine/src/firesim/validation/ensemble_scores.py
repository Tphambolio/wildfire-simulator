"""Probabilistic scores for ensemble fire-growth forecasts against observed growth.

Per fire-day (``score_ensemble_day``) and pooled over fire-days (``summarize_ensemble``):

- **Burn-probability reliability** (cell by cell): for each member count k = 0..N, how many
  cells had burn probability k/N and how many of them burned. Pooled, this gives the
  reliability diagram and the Brier score (Brier 1950; Wilks 2011, ch. 8). The Brier score
  is computed over the cells that any member burned or that burned in reality (cells nobody
  predicted and that did not burn are trivially right and would swamp the score).
- **Percentile footprints**: the P10 / P50 / P90 footprints are the cells reached by at least
  10 / 50 / 90 % of members by the end of the window (the same cells as the P10 / P50 / P90
  arrival-time maps of ``firesim.spread.ensemble``). For each: precision, recall (hit rate
  of observed growth), F1 and area.
- **Burned area**: each member's growth area against the observed growth area: rank of the
  observation among the members (rank histogram), whether it falls inside the members'
  10th-90th percentile range (should be ~80 % of fire-days), and the CRPS of log10 area.
  CRPS in negative orientation (lower is better): ``E|X - y| - 0.5 E|X - X'|`` with X, X'
  independent draws from the forecast. Gneiting & Raftery (2007, JASA 102: 359-378) eq. 21
  (p. 367) prints the positively oriented form ``0.5 E|X - X'| - E|X - y|``; the negative form is
  the unnumbered CRPS* display that follows it on the same page. Taking X from the N members
  with equal weights (all N^2 pairs, i = j included) gives the CRPS of the members' empirical
  distribution exactly, the quantity Hersbach (2000) computes from the order statistics (G&R
  p. 367). It reduces to the absolute error for one member (G&R p. 367).
- **Spread-skill** (Fortin et al. 2014, J. Hydrometeor. 15: 1708-1713): the root-mean-square
  error of the ensemble mean over T fire-days against the ensemble spread, both in log10 area.
  Per day the spread is the unbiased member variance s_t^2 (divisor N - 1, Fortin eq. 9,
  p. 1710); the average spread is the square root of the *mean variance*, not the mean
  standard deviation (eq. 16, p. 1711: the mean sd "necessarily leads to a smaller value" and
  false diagnoses of under-dispersion). For a finite ensemble whose members and observation are
  exchangeable, ``MSE ~ (N + 1) / N * mean(s_t^2)`` (eq. 14; RMSE form eq. 15, p. 1711), so
  ``spread_skill_ratio = RMSE / sqrt((N + 1) / N * mean(s_t^2))`` is 1 for a reliable
  ensemble, > 1 under-dispersed. Bias inflates the RMSE and can also produce a false
  under-dispersion diagnosis (Fortin p. 1712), so the ratio is also reported with the mean
  error removed (``spread_skill_ratio_debiased``; MSE = error variance + bias^2, Fortin
  p. 1710).

Proper scores (Brier, CRPS) are the ranking criteria. Skill scores (``bss_*``, ``crpss_vs_det``)
are reported for orientation only: skill scores of the form of G&R eq. 8 are "generally
improper, even if the underlying scoring rule S is proper" (G&R p. 362). Scores compare
settings only on the same fire-days and cells (G&R p. 362); ``brier`` is computed on a fixed
cell set per fire-day for that reason. ``brier_near`` uses the cells any member, the
deterministic run or reality burned, a set that changes with the forecast, so it is a
diagnostic and must not be used to rank settings.

Scores are on the burn day's *growth*: the starting fire (cells burned before the burn day,
or burning at t = 0 in any member) is excluded from prediction and observation alike, as in
``harness.score_day``.
"""

from __future__ import annotations

import math

import numpy as np

from firesim.validation.metrics import overlap_scores

AREA_FLOOR_HA = 10.0  # log10(area + floor): keeps zero-growth members and days finite
FOOTPRINT_LEVELS = (10, 50, 90)


def crps_ensemble(members: np.ndarray, obs: float) -> float:
    """CRPS of an ensemble (equal weights) against a scalar observation, lower is better.

    ``E|X - y| - 0.5 E|X - X'|`` over the members' empirical distribution (all N^2 pairs):
    the negatively oriented form of Gneiting & Raftery (2007) eq. 21, p. 367. Equals the
    absolute error for a one-member (deterministic) forecast.
    """
    x = np.asarray(members, dtype=float)
    return float(np.mean(np.abs(x - obs)) - 0.5 * np.mean(np.abs(x[:, None] - x[None, :])))


def log_area(ha) -> np.ndarray:
    return np.log10(np.asarray(ha, dtype=float) + AREA_FLOOR_HA)


def spread_skill(members: list[np.ndarray], obs: np.ndarray) -> dict:
    """Spread-skill of T forecasts of a scalar (Fortin et al. 2014, eqs. 9, 14-16).

    ``members``: T arrays of N member values (N >= 2, the same N for every forecast);
    ``obs``: the T observations. Returns the RMSE of the ensemble mean, the average spread
    as the square root of the mean unbiased member variance (eq. 16), the finite-ensemble
    ratio ``RMSE / sqrt((N + 1) / N * mean(s_t^2))`` (eq. 15), the same ratio with the mean
    error (bias) removed from the RMSE, the mean standard deviation (the *incorrect* average
    spread of Fortin p. 1711, kept for comparison with earlier runs), the bias and the
    correlation of spread with absolute error.
    """
    obs = np.asarray(obs, dtype=float)
    n = len(members[0])
    var = np.array([np.var(m, ddof=1) for m in members])  # s_t^2, eq. 9
    err = np.array([np.mean(m) for m in members]) - obs
    rmse = float(np.sqrt(np.mean(err ** 2)))
    bias = float(np.mean(err))
    err_sd = float(np.sqrt(max(np.mean(err ** 2) - bias ** 2, 0.0)))  # MSE - bias^2
    denom = float(np.sqrt(np.mean(var) * (n + 1) / n))
    sd = np.sqrt(var)
    return {
        "rmse": rmse,
        "rms_spread": float(np.sqrt(np.mean(var))),
        "mean_sd": float(np.mean(sd)),
        "ratio": rmse / denom if denom > 0 else math.nan,
        "ratio_debiased": err_sd / denom if denom > 0 else math.nan,
        "bias": bias,
        "corr": float(np.corrcoef(sd, np.abs(err))[0, 1]) if len(obs) > 2 else math.nan,
    }


def score_ensemble_day(dob: np.ndarray, day: int, members: np.ndarray, det: np.ndarray,
                       cell_area_m2: float, window_h: float) -> dict:
    """Probabilistic scores of one fire-day at ``window_h`` hours.

    ``members``: (N, rows, cols) arrival minutes (inf = unburned); ``det`` the deterministic
    arrival; ``dob`` the CFSDS day-of-burning grid.
    """
    n = members.shape[0]
    prior = (dob > 0) & (dob < day)
    start = (members <= 0.0).any(axis=0) | (det <= 0.0)
    excluded = prior | start
    obs = (dob == day) & ~excluded
    lim = window_h * 60.0
    burned = (members <= lim) & ~excluded[None]
    k = burned.sum(axis=0)
    valid = ~excluded
    cells = np.bincount(k[valid].ravel(), minlength=n + 1)
    hits = np.bincount(k[valid & obs].ravel(), minlength=n + 1)
    cell_ha = cell_area_m2 / 1e4
    out: dict = {
        "n_members": n,
        "k_cells": cells.tolist(), "k_obs": hits.tolist(),
        "obs_ha": float(obs.sum()) * cell_ha,
        "member_ha": [float(b.sum()) * cell_ha for b in burned],
    }
    det_growth = (det <= lim) & ~excluded
    out["det"] = overlap_scores(det_growth, obs, cell_area_m2).as_dict()
    # Brier sums over the cells any member, the deterministic run or reality burned
    region = valid & ((k > 0) | det_growth | obs)
    o = obs[region].astype(float)
    out["brier_cells"] = int(region.sum())
    out["brier_sum"] = float(np.sum((k[region] / n - o) ** 2))
    out["brier_sum_det"] = float(np.sum((det_growth[region].astype(float) - o) ** 2))
    for q in FOOTPRINT_LEVELS:
        need = max(1, math.ceil(q / 100.0 * n))  # the q-th percentile arrival is finite
        out[f"p{q}"] = overlap_scores(k >= need, obs, cell_area_m2).as_dict()
    return out


def _fmt_nan(v: float) -> float:
    return float(v) if v is not None and np.isfinite(v) else math.nan


def summarize_ensemble(recs: list[dict], window: str = "17h") -> dict:
    """Pooled probabilistic scores over fire-day records (``rec["ens"][window]``)."""
    rows = [r["ens"][window] for r in recs if window in r.get("ens", {})]
    if not rows:
        return {}
    n = rows[0]["n_members"]
    cells = np.sum([r["k_cells"] for r in rows], axis=0).astype(float)
    hits = np.sum([r["k_obs"] for r in rows], axis=0).astype(float)
    p = np.arange(n + 1) / n
    # Brier over every scored cell of the working areas (fixed per fire-day, so comparable
    # between ensemble settings; small because most cells are far from the fire), the
    # deterministic run's on the same cells, and the Brier skill score against it
    n_all = cells.sum()
    bs = float(np.sum(hits * (1 - p) ** 2 + (cells - hits) * p ** 2) / n_all)
    # cell area of each fire-day from its observed cells (records keep areas, not counts)
    cell_m2 = [r["obs_ha"] * 1e4 / sum(r["k_obs"]) if sum(r["k_obs"]) else math.nan
               for r in rows]
    det_wrong = sum((r["det"]["fp"] + r["det"]["fn"]) / c for r, c in zip(rows, cell_m2)
                    if np.isfinite(c))
    det_bs = float(det_wrong / n_all)
    # and over the cells any member, the deterministic run or reality burned (pooled)
    n_bs = float(sum(r["brier_cells"] for r in rows))
    bs_near = float(sum(r["brier_sum"] for r in rows) / n_bs)
    det_bs_near = float(sum(r["brier_sum_det"] for r in rows) / n_bs)
    clim = hits.sum() / n_all  # base rate
    bs_ref = float(clim * (1 - clim))
    # reliability diagram in 5 probability bins (k = 0 shown separately)
    rel = [{"bin": "0", "n_cells": int(cells[0]), "obs_freq": _fmt_nan(hits[0] / cells[0])
            if cells[0] else math.nan, "mean_p": 0.0}]
    for lo, hi in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        sel = (p > 0) & (p >= lo - 1e-9) & (p < hi - 1e-9)
        c, h = cells[sel].sum(), hits[sel].sum()
        rel.append({"bin": f"{lo:.1f}-{min(hi, 1):.1f}", "n_cells": int(c),
                    "obs_freq": float(h / c) if c else math.nan,
                    "mean_p": float((cells[sel] * p[sel]).sum() / c) if c else math.nan})
    # observed growth captured at probability >= x
    frac_obs_ge = {f"{x:.1f}": float(hits[p >= x - 1e-9].sum() / hits.sum()) if hits.sum() else
                   math.nan for x in (0.1, 0.5, 0.9)}

    obs_l = np.array([log_area(r["obs_ha"]) for r in rows])
    mem_l = [log_area(r["member_ha"]) for r in rows]
    det_l = np.array([log_area(r["det"]["area_pred"] / 1e4) for r in rows])
    crps = np.array([crps_ensemble(m, o) for m, o in zip(mem_l, obs_l)])
    crps_det = np.abs(det_l - obs_l)
    ranks = np.array([int(np.sum(m < o) + 0.5 * np.sum(m == o)) for m, o in zip(mem_l, obs_l)])
    lo = np.array([np.percentile(m, 10) for m in mem_l])
    hi = np.array([np.percentile(m, 90) for m in mem_l])
    inside = (obs_l >= lo) & (obs_l <= hi)
    ss = spread_skill(mem_l, obs_l)

    def fp_mean(level: str, key: str) -> float:
        v = np.array([np.nan_to_num(r[level][key], nan=0.0) if key == "f1" else
                      _fmt_nan(r[level][key]) for r in rows], dtype=float)
        return float(np.nanmean(v)) if np.isfinite(v).any() else math.nan

    def pooled(level: str) -> dict:
        tp = sum(r[level]["tp"] for r in rows)
        fp = sum(r[level]["fp"] for r in rows)
        fn = sum(r[level]["fn"] for r in rows)
        return {"precision": tp / (tp + fp) if tp + fp else math.nan,
                "recall": tp / (tp + fn) if tp + fn else math.nan}

    footprints = {}
    for level in ("det", "p10", "p50", "p90"):
        ad = np.array([r[level]["area_diff_norm"] for r in rows], dtype=float)
        footprints[level] = {
            "f1": fp_mean(level, "f1"), "precision": fp_mean(level, "precision"),
            "recall": fp_mean(level, "recall"),
            "area_diff": float(np.nanmean(ad)), "over_pred": float(np.mean(np.nan_to_num(ad) > 0)),
            "recall_ge_0.9": float(np.mean([np.nan_to_num(r[level]["recall"], nan=0.0) >= 0.9
                                            for r in rows])),
            "pooled": pooled(level),
        }
    return {
        "n_fire_days": len(rows), "n_members": n,
        "brier": bs, "brier_det": det_bs, "brier_base_rate": bs_ref,
        "bss_vs_det": 1.0 - bs / det_bs if det_bs else math.nan,
        "bss_vs_base_rate": 1.0 - bs / bs_ref if bs_ref else math.nan,
        "brier_near": bs_near, "brier_near_det": det_bs_near,
        "reliability": rel, "frac_obs_at_p_ge": frac_obs_ge,
        "crps_log10_area": float(np.mean(crps)), "mae_log10_area_det": float(np.mean(crps_det)),
        "crpss_vs_det": float(1.0 - np.mean(crps) / np.mean(crps_det)) if np.mean(crps_det) > 0
        else math.nan,
        "coverage_p10_p90": float(np.mean(inside)),
        "below_p10": float(np.mean(obs_l < lo)), "above_p90": float(np.mean(obs_l > hi)),
        "rank_hist": np.bincount(np.clip(ranks, 0, n), minlength=n + 1).tolist(),
        "spread_skill_ratio": ss["ratio"], "spread_skill_ratio_debiased": ss["ratio_debiased"],
        "spread_error_corr": ss["corr"],
        "rmse_log10_ens_mean": ss["rmse"],
        "rms_spread_log10": ss["rms_spread"],  # sqrt(mean variance): Fortin eq. 16
        "mean_spread_log10": ss["mean_sd"],  # mean sd (biased low; Fortin p. 1711)
        "ens_mean_bias_log10": ss["bias"],
        "footprints": footprints,
    }
