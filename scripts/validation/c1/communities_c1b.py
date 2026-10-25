"""C1b community arrival metric (PREREG_COMPARATOR_C1B §5): interval-censored arrival error.

Times are hours from the run start (06:00 local, UTC-6, on burn day n).

Observed arrival interval [lo, hi] of a community whose 500 m reach zone the observed fire reached
on day n (CFSDS DOB = n in the zone, no earlier DOB in it):

- CFSDS bounds it to the local day: [-6, 18].
- hi = min(18, first VIIRS 375 m detection in the zone at or after -6 h); a detection after 18 h
  (the night pass) or none leaves hi = 18.
- lo = max(-6, the last VIIRS overpass before hi in which the zone had no detection). Overpasses
  are the clusters (gaps > 20 min) of all VIIRS detection times inside the fire-day's working area
  from -6 h on; a pass counts if it is before hi.

Model arrival a = earliest arrival minute in the zone / 60; not reached within the run = 24 h.
Error e = 0 if lo <= a <= hi, else the distance from a to the interval.
"""

from __future__ import annotations

import math

import numpy as np

import communities as C

DAY_LO, DAY_HI, NOT_REACHED_H = -6.0, 18.0, 24.0
PASS_GAP_MIN = 20.0


def _detections(dom, start):
    """(minutes from start, row, col) of VIIRS detections on the grid, -6 h .. +24 h."""
    import pandas as pd

    v = C._viirs_year(dom.year)
    if v is None:
        return np.empty(0), np.empty(0, int), np.empty(0, int)
    t0 = pd.Timestamp(start)
    sel = v[(v["local"] >= t0 + pd.Timedelta(hours=DAY_LO)) & (v["local"] <= t0 + pd.Timedelta(hours=24))]
    r = np.floor((dom.lat_max - sel["latitude"].to_numpy()) / dom.cell_lat).astype(int)
    c = np.floor((sel["longitude"].to_numpy() - dom.lng_min) / dom.cell_lng).astype(int)
    ok = (r >= 0) & (r < dom.rows) & (c >= 0) & (c < dom.cols)
    m = ((sel["local"].to_numpy() - np.datetime64(t0)) / np.timedelta64(1, "m"))[ok]
    return m.astype(float), r[ok], c[ok]


def _passes(minutes: np.ndarray) -> list[float]:
    """Start minute of each overpass (detection-time clusters separated by > 20 min)."""
    if minutes.size == 0:
        return []
    s = np.sort(minutes)
    out = [s[0]]
    for a, b in zip(s[:-1], s[1:]):
        if b - a > PASS_GAP_MIN:
            out.append(b)
    return [float(x) for x in out]


def observed_intervals(case) -> tuple[list[dict], list[dict]]:
    """(reached communities with [lo, hi] hours, eligible unreached communities)."""
    dom = case.domain
    prior = (dom.dob > 0) & (dom.dob < case.day)
    obs = dom.dob == case.day
    minutes, rr, cc = _detections(dom, case.start)
    passes = _passes(minutes)
    reached, unreached = [], []
    for name, m in C.zone_masks(dom):
        if (m & prior).any():
            continue
        if not (m & obs).any():
            unreached.append({"name": name, "mask": m})
            continue
        inz = m[rr, cc] if minutes.size else np.zeros(0, bool)
        first = float(minutes[inz].min()) / 60.0 if inz.any() else math.inf
        hi = min(DAY_HI, first)
        before = [p / 60.0 for p in passes if p / 60.0 < hi]
        # a pass "without detection in the zone": every detection of the zone is later
        lo = max([DAY_LO] + [b for b in before if b < first])
        if lo > hi:
            lo = hi
        reached.append({"name": name, "mask": m, "lo": lo, "hi": hi,
                        "viirs_first_h": first if math.isfinite(first) else None})
    return reached, unreached


def interval_error(a_h: float, lo: float, hi: float) -> float:
    if a_h < lo:
        return lo - a_h
    if a_h > hi:
        return a_h - hi
    return 0.0


def model_errors(reached: list[dict], unreached: list[dict], arrival: np.ndarray,
                 start_mask: np.ndarray) -> dict:
    rows = []
    for c in reached:
        if (c["mask"] & start_mask).any():
            continue
        a = float(np.min(arrival[c["mask"]])) / 60.0
        a = NOT_REACHED_H if not math.isfinite(a) else min(a, NOT_REACHED_H)
        rows.append({"name": c["name"], "lo": c["lo"], "hi": c["hi"], "model_h": a,
                     "err_h": interval_error(a, c["lo"], c["hi"]), "hit": a <= DAY_HI})
    false = 0
    for c in unreached:
        if (c["mask"] & start_mask).any():
            continue
        if float(np.min(arrival[c["mask"]])) <= DAY_HI * 60.0:
            false += 1
    return {"n": len(rows), "mae_h": float(np.mean([r["err_h"] for r in rows])) if rows else math.nan,
            "hits": sum(r["hit"] for r in rows), "false_arrivals": false,
            "n_unreached_eligible": len(unreached), "rows": rows}
