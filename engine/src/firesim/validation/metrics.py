"""Spatial agreement metrics for predicted vs observed burned areas.

Follows Bennett et al. (2026, Int. J. Wildland Fire 35(8), WF26072), who scored W.I.S.E. against
the Canadian Fire Spread Dataset on rasterised burned areas:

- normalised area difference  (A_pred - A_obs) / (A_pred + A_obs)   (>0 overprediction)
- precision  TP / (TP + FP),  recall  TP / (TP + FN)
- F1 = 2 P R / (P + R)  (= Sorensen / Dice coefficient)
- IoU = |A n B| / |A u B|  (= Jaccard / threat score)
- Hausdorff distance: the largest distance from a point of one area to the nearest point of
  the other, in metres.

plus head-fire bearing and forward-spread error (Fox-Hughes et al. 2024, "An evaluation of wildland fire simulators used operationally in
Australia", IJWF 33(4), WF23028) and the
+/-35 % rate-of-spread band of Cruz & Alexander (2013, Env. Modelling & Software 47:16-28).

Raster metrics take boolean masks on the same grid with cell size ``dx`` (east-west) by ``dy``
(north-south) metres, row 0 at the north edge. Polygon variants take shapely geometries in a
projected (metre) CRS.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage

CRUZ_ALEXANDER_TOLERANCE = 0.35


@dataclass(frozen=True)
class OverlapScores:
    """Confusion counts (as areas, m2) and the derived Bennett et al. (2026) scores.

    Scores that are undefined (e.g. precision with nothing predicted) are NaN, never 0, so
    aggregates can tell "no prediction" from "wrong prediction".
    """

    tp: float
    fp: float
    fn: float
    area_pred: float
    area_obs: float
    precision: float
    recall: float
    f1: float
    iou: float
    area_diff_norm: float

    def as_dict(self) -> dict:
        return asdict(self)


def _scores(tp: float, fp: float, fn: float) -> OverlapScores:
    a_pred, a_obs = tp + fp, tp + fn
    precision = tp / a_pred if a_pred > 0 else math.nan
    recall = tp / a_obs if a_obs > 0 else math.nan
    f1 = 2.0 * tp / (a_pred + a_obs) if a_pred + a_obs > 0 else math.nan
    union = tp + fp + fn
    iou = tp / union if union > 0 else math.nan
    area_diff = (a_pred - a_obs) / (a_pred + a_obs) if a_pred + a_obs > 0 else math.nan
    return OverlapScores(tp, fp, fn, a_pred, a_obs, precision, recall, f1, iou, area_diff)


def overlap_scores(pred: np.ndarray, obs: np.ndarray, cell_area_m2: float = 1.0) -> OverlapScores:
    """Overlap scores of two boolean masks on the same grid."""
    pred = np.asarray(pred, dtype=bool)
    obs = np.asarray(obs, dtype=bool)
    if pred.shape != obs.shape:
        raise ValueError(f"mask shapes differ: {pred.shape} vs {obs.shape}")
    tp = float(np.count_nonzero(pred & obs)) * cell_area_m2
    fp = float(np.count_nonzero(pred & ~obs)) * cell_area_m2
    fn = float(np.count_nonzero(~pred & obs)) * cell_area_m2
    return _scores(tp, fp, fn)


def polygon_overlap_scores(pred, obs) -> OverlapScores:
    """Overlap scores of two shapely (Multi)Polygons in a metre CRS (areas in m2)."""
    tp = pred.intersection(obs).area
    return _scores(tp, pred.area - tp, obs.area - tp)


def hausdorff_m(a: np.ndarray, b: np.ndarray, dx: float, dy: float) -> float:
    """Hausdorff distance (m) between two cell sets, measured between cell centres.

    max( max_{p in A} d(p, B), max_{q in B} d(q, A) ) with Euclidean distance on the
    anisotropic grid. Exact for the rasterised areas (via the Euclidean distance transform).
    NaN if either set is empty.
    """
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    if not a.any() or not b.any():
        return math.nan
    d_to_b = ndimage.distance_transform_edt(~b, sampling=(dy, dx))
    d_to_a = ndimage.distance_transform_edt(~a, sampling=(dy, dx))
    return float(max(d_to_b[a].max(), d_to_a[b].max()))


def polygon_hausdorff_m(a, b, densify: float | None = 0.05) -> float:
    """Hausdorff distance between two shapely geometries (metre CRS).

    Shapely measures between the geometries' vertices; ``densify`` (fraction of segment
    length) adds vertices along edges so long straight edges are not under-sampled.
    Note this is a boundary distance: for one polygon nested deep inside another it can
    differ from the filled-area distance of :func:`hausdorff_m`.
    """
    import shapely

    if a.is_empty or b.is_empty:
        return math.nan
    return float(shapely.hausdorff_distance(a, b, densify=densify))


@dataclass(frozen=True)
class HeadSpread:
    """The farthest-travelled point of a day's growth from the starting burned area."""

    distance_m: float  # forward spread distance: farthest growth cell from the initial area
    bearing_deg: float  # direction of that spread (0 = N, 90 = E), from the nearest initial cell
    row: int
    col: int


def head_spread(initial: np.ndarray, growth: np.ndarray, dx: float, dy: float) -> HeadSpread | None:
    """Forward spread distance and bearing of ``growth`` away from ``initial`` (masks).

    The head is taken as the growth cell farthest from the initial burned area; its bearing is
    measured from the initial cell nearest to it. None if either mask is empty.
    """
    initial = np.asarray(initial, dtype=bool)
    growth = np.asarray(growth, dtype=bool) & ~initial
    if not initial.any() or not growth.any():
        return None
    dist, (ri, ci) = ndimage.distance_transform_edt(~initial, sampling=(dy, dx),
                                                    return_indices=True)
    d = np.where(growth, dist, -1.0)
    r, c = np.unravel_index(int(np.argmax(d)), d.shape)
    north = (ri[r, c] - r) * dy  # rows increase southward
    east = (c - ci[r, c]) * dx
    bearing = math.degrees(math.atan2(east, north)) % 360.0
    return HeadSpread(float(dist[r, c]), bearing, int(r), int(c))


def bearing_error_deg(predicted: float, observed: float) -> float:
    """Signed angular difference predicted - observed, in [-180, 180) degrees."""
    return (predicted - observed + 180.0) % 360.0 - 180.0


def within_cruz_alexander(predicted: float, observed: float,
                          tolerance: float = CRUZ_ALEXANDER_TOLERANCE) -> bool | None:
    """True if ``predicted`` is within +/-35 % of ``observed`` (None if observed <= 0)."""
    if observed <= 0:
        return None
    return abs(predicted - observed) / observed <= tolerance
