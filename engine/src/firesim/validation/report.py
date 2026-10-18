"""Aggregate fire-day validation records into tables (Bennett et al. 2026 style).

Bennett et al. report the mean, standard deviation and 5th / 95th percentiles over fire-days
(their Table 1) and over fires after averaging within each fire (Table 2). Medians are added
here because the score distributions are skewed.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

METRICS = ("f1", "precision", "recall", "iou", "area_diff_norm", "hausdorff_m")
SPREAD_METRICS = ("spread_ratio", "bearing_err_deg")
# Per-window start-perimeter diagnostics (firesim.validation.start_strata; backlog A2)
START_DIAG_METRICS = ("growth_ratio", "static_share_model")

# Bennett et al. (2026) Table 1, W.I.S.E., 2,210 fires / 19,848 burn days (fire-day means)
BENNETT_WISE = {
    "S1 default (06-23 h)": {"f1": 0.259, "precision": 0.200, "recall": 0.856, "iou": 0.194,
                             "area_diff_norm": 0.544, "hausdorff_m": 2828.0},
    "S2 best duration": {"f1": 0.498, "precision": 0.451, "recall": 0.637, "iou": 0.284,
                         "area_diff_norm": -0.109, "hausdorff_m": 918.0},
    "S3 best duration + wind": {"f1": 0.539, "precision": 0.475, "recall": 0.701, "iou": 0.309,
                                "area_diff_norm": -0.061, "hausdorff_m": 891.0},
}


def growth_class(ha: float) -> str:
    """Observed day's growth size class."""
    if ha < 100:
        return "<100 ha"
    if ha < 1000:
        return "100-1,000 ha"
    if ha < 10000:
        return "1,000-10,000 ha"
    return ">10,000 ha"


def flatten(records: list[dict], member: str = "det") -> list[dict]:
    """One row per (fire-day, window) for ``member``, with the grouping fields."""
    rows = []
    for rec in records:
        m = rec["members"].get(member)
        if m is None:
            continue
        for window, s in m.items():
            if not isinstance(s, dict):
                continue
            rows.append({
                "fire_id": rec["fire_id"], "year": rec["year"], "day": rec["day"],
                "ignition": rec["ignition"], "spotting": rec["spotting"], "window": window,
                "fuel": rec["dominant_fuel"], "fwi_class": rec["fwi_class"] or "n/a",
                "growth_class": growth_class(rec["obs_growth_ha"]),
                "obs_growth_ha": rec["obs_growth_ha"], "edge_hit": m.get("edge_hit", False),
                **{k: s.get(k, math.nan) for k in METRICS + SPREAD_METRICS},
                "ros_within_35pct": s.get("ros_within_35pct"),
                # backlog A2 (descriptive): start-type stratum and start-perimeter diagnostics
                "start_type": rec.get("start_type", "n/a"),
                "static_share_obs": (rec.get("start_perimeter_obs") or {}).get(
                    "static_share", math.nan),
                **{k: s.get(k, math.nan) for k in START_DIAG_METRICS},
            })
    return rows


def _num(v) -> float:
    return math.nan if v is None else float(v)


def summarize(rows: list[dict], metrics=METRICS) -> dict:
    """n, mean, sd, median, p5, p95 per metric (NaNs ignored, n counts non-NaN values)."""
    out = {}
    for k in metrics:
        v = np.array([_num(r.get(k)) for r in rows], dtype=float)
        v = v[np.isfinite(v)]
        if len(v) == 0:
            out[k] = {"n": 0, "mean": math.nan, "sd": math.nan, "median": math.nan,
                      "p5": math.nan, "p95": math.nan}
            continue
        out[k] = {"n": int(len(v)), "mean": float(v.mean()), "sd": float(v.std(ddof=0)),
                  "median": float(np.median(v)), "p5": float(np.percentile(v, 5)),
                  "p95": float(np.percentile(v, 95))}
    return out


def spread_summary(rows: list[dict]) -> dict:
    """Head-spread diagnostics: median spread ratio, median |bearing error|, share within 35 %."""
    ratio = np.array([_num(r.get("spread_ratio")) for r in rows], dtype=float)
    ratio = ratio[np.isfinite(ratio)]
    bear = np.array([_num(r.get("bearing_err_deg")) for r in rows], dtype=float)
    bear = bear[np.isfinite(bear)]
    within = [r["ros_within_35pct"] for r in rows if r.get("ros_within_35pct") is not None]
    return {
        "n": len(ratio),
        "spread_ratio_median": float(np.median(ratio)) if len(ratio) else math.nan,
        "spread_over_share": float((ratio > 1.35).mean()) if len(ratio) else math.nan,
        "spread_under_share": float((ratio < 0.65).mean()) if len(ratio) else math.nan,
        "abs_bearing_err_median": float(np.median(np.abs(bear))) if len(bear) else math.nan,
        "bearing_err_median": float(np.median(bear)) if len(bear) else math.nan,
        "within_35pct": float(np.mean(within)) if within else math.nan,
    }


def per_fire_means(rows: list[dict], metrics=METRICS) -> list[dict]:
    """Average each metric within each fire first (Bennett Table 2 style)."""
    by_fire: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_fire[r["fire_id"]].append(r)
    out = []
    for fid, rs in by_fire.items():
        s = summarize(rs, metrics)
        out.append({"fire_id": fid, **{k: s[k]["mean"] for k in metrics}})
    return out


SPLIT_SEED = "firesim-skill-2026"


def split_fires(fire_ids, seed: str = SPLIT_SEED) -> dict[str, str]:
    """Calibration / held-out test split BY FIRE (never by day).

    Fires are ranked by a seeded SHA-256 hash of their id and the first half (rounded down)
    is ``"calibration"``, the rest ``"test"``. The split depends only on the fire ids and the
    seed, so it is fixed before any result is seen and stable as runs are added. All
    fire-days of a fire fall on the same side, so held-out scores are for fires the
    parameters never saw.
    """
    import hashlib

    ids = sorted(set(fire_ids))
    ranked = sorted(ids, key=lambda f: hashlib.sha256(f"{seed}:{f}".encode()).hexdigest())
    n_cal = len(ranked) // 2
    return {f: ("calibration" if k < n_cal else "test") for k, f in enumerate(ranked)}


def group_by(rows: list[dict], key: str, metrics=("f1", "iou", "area_diff_norm")) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r[key])].append(r)
    return {g: {"n": len(rs), **{k: summarize(rs, (k,))[k] for k in metrics}}
            for g, rs in sorted(groups.items())}


def fmt(v: float, metric: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if metric == "hausdorff_m":
        return f"{v:,.0f}"
    return f"{v:.3f}"


STRATUM_ORDER = ("point", "fresh", "mature", "n/a")


def start_strata_summary(rows: list[dict]) -> dict:
    """Scores and start-perimeter diagnostics per start-type stratum and pooled ("all").

    ``rows`` are :func:`flatten` rows of ONE window. Per group: fire-days, fires, mean and
    median F1 (NaN F1 counted as 0, as ``validate.py compare``), mean precision / recall /
    IoU / normalised area difference, the observed and modelled static share of the start
    perimeter (median and mean over fire-days), and the growth ratio (modelled ÷ observed
    growth beyond the start perimeter: median, geometric mean, share of days > 1).
    Descriptive only: the pooled scores are the same rows ``summarize`` sees.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get("start_type", "n/a"))].append(r)
        groups["all"].append(r)
    keys = [k for k in STRATUM_ORDER if k in groups] + ["all"]
    out = {}
    for k in keys:
        rs = groups[k]
        f1 = np.array([np.nan_to_num(_num(r.get("f1")), nan=0.0) for r in rs])
        s = summarize(rs, ("precision", "recall", "iou", "area_diff_norm"))

        def col(name, vals=rs):
            v = np.array([_num(r.get(name)) for r in vals], dtype=float)
            return v[np.isfinite(v)]
        so, sm, gr = col("static_share_obs"), col("static_share_model"), col("growth_ratio")
        grp = gr[gr > 0]
        out[k] = {
            "n_fire_days": len(rs), "n_fires": len({r["fire_id"] for r in rs}),
            "f1_mean": float(f1.mean()), "f1_median": float(np.median(f1)),
            **{f"{m}_mean": s[m]["mean"] for m in ("precision", "recall", "iou",
                                                  "area_diff_norm")},
            "static_share_obs_median": float(np.median(so)) if len(so) else math.nan,
            "static_share_obs_mean": float(so.mean()) if len(so) else math.nan,
            "static_share_model_median": float(np.median(sm)) if len(sm) else math.nan,
            "static_share_model_mean": float(sm.mean()) if len(sm) else math.nan,
            "n_static_diag": int(len(sm)),
            "growth_ratio_median": float(np.median(gr)) if len(gr) else math.nan,
            "growth_ratio_geomean": float(np.exp(np.mean(np.log(grp)))) if len(grp) else math.nan,
            "growth_ratio_share_gt1": float(np.mean(gr > 1.0)) if len(gr) else math.nan,
            "n_growth_ratio_zero": int(np.sum(gr == 0)),
        }
    return out
