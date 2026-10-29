"""Start-type strata and start-perimeter diagnostics for fire-day validation (backlog A2).

Descriptive infrastructure only: nothing here changes how a fire-day is simulated or scored.

**Start type.** Every CFSDS fire-day starts from the area burned before the burn day (CFSDS
day of burning ``DOB < day``; ``firesim.validation.harness.initial_state``). The tag is
derived from that start area alone, so it uses no information from the burn day:

- ``age_days`` = burn day − the earliest DOB in the start area (days since the fire's first
  mapped day of burning);
- ``mature`` when ``age_days >= 2``: at least one full burn day before the previous one, so
  parts of the start perimeter may have been held or burned out;
- otherwise (the whole start area burned on the previous burn day) ``point`` when the start
  area is at most :data:`POINT_MAX_HA` (100 ha, the area of a circle of about 560 m radius:
  at the scale of a day's growth the model starts from what is in effect an ignition point),
  else ``fresh``: a perimeter that is all less than a day old. CFSDS DOB is daily, so
  "fresh" cells burned between about 6 and 30 h before the 06:00 run start; it is the
  harness's nearest analogue of a "< 24 h" perimeter;
- a fire-day with no start area at all (not produced by ``fire_day_pairs``) is ``point``.

The rule was fixed from the distribution of start age and start area over the 143 record
fire-days (2026-10-18), before any per-stratum score was computed.

**Static share of the start perimeter** ([R53] Fig. 4 method, adapted to the raster).
The start area's boundary cells (start cells with a 4-neighbour outside it) are thinned to one
sample per ``sample_m`` × ``sample_m`` block (default 500 m, the [R53] spacing; the cell
nearest the block centre). Every cell that burned outside the start area is assigned to its
nearest boundary sample; a sample's *advance* is the largest distance from the start area
among the cells assigned to it (0 if none). The static share is the share of samples whose
advance is at most ``static_m`` (default 1 km). It is computed for the observed growth and for
the modelled growth at each scoring window, against the same start area.

**Growth ratio.** Modelled growth area ÷ observed growth area, both outside the start area
(the scored "growth" masks); > 1 is over-spread. NaN when nothing was observed to grow.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

POINT_MAX_HA = 100.0  # largest one-day-old start area still treated as a point start
STATIC_M = 1000.0  # a start-perimeter sample that advanced at most this far is "static"
SAMPLE_M = 500.0  # spacing of the start-perimeter samples ([R53] Fig. 4)
START_TYPES = ("point", "fresh", "mature")
_CROSS = ndimage.generate_binary_structure(2, 1)


def start_type(dob: np.ndarray, day: int, cell_area_m2: float,
               point_max_ha: float = POINT_MAX_HA) -> dict:
    """Start-type tag of burn day ``day`` from the CFSDS DOB grid (see module docstring).

    Returns ``start_type``, ``start_age_days``, ``start_ha`` and ``start_prev_day_share``
    (share of the start area that burned on ``day - 1``).
    """
    prior = (dob > 0) & (dob < day)
    n = int(prior.sum())
    if n == 0:
        return {"start_type": "point", "start_age_days": 0, "start_ha": 0.0,
                "start_prev_day_share": math.nan}
    age = int(day - int(dob[prior].min()))
    ha = n * cell_area_m2 / 1e4
    tag = "mature" if age >= 2 else ("point" if ha <= point_max_ha else "fresh")
    return {"start_type": tag, "start_age_days": age, "start_ha": float(ha),
            "start_prev_day_share": float(((dob == day - 1) & prior).sum()) / n}


def boundary_samples(start: np.ndarray, dx: float, dy: float,
                     sample_m: float = SAMPLE_M) -> tuple[np.ndarray, np.ndarray]:
    """(rows, cols) of the start boundary thinned to one cell per ``sample_m`` block."""
    edge = start & ~ndimage.binary_erosion(start, structure=_CROSS, border_value=0)
    rr, cc = np.nonzero(edge)
    if len(rr) == 0:
        return rr, cc
    y, x = (rr + 0.5) * dy, (cc + 0.5) * dx
    by, bx = np.floor(y / sample_m), np.floor(x / sample_m)
    d2 = (y - (by + 0.5) * sample_m) ** 2 + (x - (bx + 0.5) * sample_m) ** 2
    order = np.lexsort((cc, rr, d2, bx, by))  # per block: nearest the centre first (ties by cell)
    keys = np.stack([by[order], bx[order]], axis=1)
    first = np.ones(len(order), dtype=bool)
    first[1:] = np.any(keys[1:] != keys[:-1], axis=1)
    keep = np.sort(order[first])
    return rr[keep], cc[keep]


class StartPerimeterDiagnostic:
    """Static share of a start area's perimeter for any number of growth masks.

    ``start`` is the start area (bool grid); ``reach`` bounds the cells that any later growth
    mask may contain (e.g. observed growth | modelled growth at the longest window), so the
    nearest-sample assignment is computed once.
    """

    def __init__(self, start: np.ndarray, reach: np.ndarray, dx: float, dy: float,
                 sample_m: float = SAMPLE_M, static_m: float = STATIC_M):
        self.start = np.asarray(start, dtype=bool)
        self.static_m = static_m
        self.sample_m = sample_m
        sr, sc = boundary_samples(self.start, dx, dy, sample_m)
        self.n_samples = int(len(sr))
        cells = np.asarray(reach, dtype=bool) & ~self.start
        self._idx = np.flatnonzero(cells)
        self._near = np.zeros(0, dtype=np.int64)
        self._dist = np.zeros(0)
        if self.n_samples == 0 or len(self._idx) == 0:
            return
        dist = ndimage.distance_transform_edt(~self.start, sampling=(dy, dx))
        self._dist = dist.ravel()[self._idx]
        del dist
        r, c = np.divmod(self._idx, self.start.shape[1])
        tree = cKDTree(np.column_stack([(sr + 0.5) * dy, (sc + 0.5) * dx]))
        _, self._near = tree.query(np.column_stack([(r + 0.5) * dy, (c + 0.5) * dx]))

    def advance_m(self, growth: np.ndarray) -> np.ndarray:
        """Advance (m) of each boundary sample under ``growth`` (cells outside ``reach`` are
        ignored)."""
        adv = np.zeros(self.n_samples)
        if self.n_samples == 0 or len(self._idx) == 0:
            return adv
        sel = np.asarray(growth, dtype=bool).ravel()[self._idx]
        np.maximum.at(adv, self._near[sel], self._dist[sel])
        return adv

    def summary(self, growth: np.ndarray) -> dict:
        """``static_share`` (advance ≤ static_m), ``share_adv_gt_3km``, ``adv_median_km``."""
        adv = self.advance_m(growth)
        if self.n_samples == 0:
            return {"static_share": math.nan, "share_adv_gt_3km": math.nan,
                    "adv_median_km": math.nan}
        return {"static_share": float(np.mean(adv <= self.static_m)),
                "share_adv_gt_3km": float(np.mean(adv > 3000.0)),
                "adv_median_km": float(np.median(adv)) / 1000.0}


def growth_ratio(area_pred: float, area_obs: float) -> float:
    """Modelled ÷ observed growth area beyond the start perimeter (NaN if nothing observed)."""
    return float(area_pred) / float(area_obs) if area_obs > 0 else math.nan
