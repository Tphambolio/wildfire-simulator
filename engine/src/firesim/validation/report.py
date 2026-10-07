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
