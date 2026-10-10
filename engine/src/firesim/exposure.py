"""Building exposure to a modelled wildland fire front: distance, timing and radiant heat.

These are **exposure** quantities with published thresholds, not ignition predictions. The
radiant model reproduces Cohen's Structure Ignition Assessment Model (SIAM) assumptions, which
are deliberately worst-case and known to overestimate measured flux (Cohen 2000, J. For. 98(3)
p.19). Radiation from burning buildings, yard fuels and embers is not modelled; in WUI disasters
those usually dominate, so low values here do not mean a building is safe.

Sources (see docs/building-exposure.md):
- Cohen, J.D. (2004). Relating flame radiation to home ignition using modeling and experimental
  crown fires. Can. J. For. Res. 34: 1616-1626. Radiant flux q = F eps sigma T^4 with T = 1200 K
  and eps = 1 (eq 1); flux-time ignition criterion FTP = integral (q - 13.1)^1.828 dt >= 11,501
  (eqs 2-4, after Tran et al. 1992).
- NRC (2021). National Guide for Wildland-Urban Interface Fires. 12.5 / 25 kW/m2 construction-class
  bands (p.xvii); thick crown-fire flames radiate around 150-200 kW/m2 (p.27); 30 m and 100 m
  radiant-influence distances (pp.27, 33).
- View factor of a differential element facing a parallel rectangle (standard catalogue result).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

STEFAN_BOLTZMANN = 5.670374419e-8  # W m-2 K-4
COHEN_FLAME_TEMPERATURE_K = 1200.0
# Emissive power scenarios (kW/m2): Cohen's 1200 K blackbody, and the top of the NRC
# 150-200 kW/m2 band for thick crown-fire flames.
EMISSIVE_POWER_COHEN = STEFAN_BOLTZMANN * COHEN_FLAME_TEMPERATURE_K**4 / 1000.0  # 117.6
EMISSIVE_POWER_HIGH = 200.0

FTP_CRITICAL_FLUX = 13.1  # kW/m2 (Cohen 2004 eq 2)
FTP_EXPONENT = 1.828
FTP_IGNITION = 11501.0  # (kW/m2)^1.828 s
NRC_FLUX_LOW = 12.5  # kW/m2: <= CC3-equivalent exposure; piloted ignition of wood
NRC_FLUX_HIGH = 25.0  # kW/m2: > CC1-equivalent exposure

DEFAULT_RESIDENCE_S = 60.0  # flaming residence; NRC p.27 ~30 s, Cohen 2000 50-70 s, Westhaver 60-90 s
DISTANCE_BANDS_M = (10.0, 30.0, 100.0, 500.0)
RADIANT_RANGE_M = 150.0  # emitters farther than this are ignored (well below 12.5 kW/m2)
MAX_WALL_POINTS = 16  # target points sampled around a footprint


def corner_view_factor(a: np.ndarray | float, b: np.ndarray | float, d: np.ndarray | float):
    """View factor from a differential element to a parallel a x b rectangle with one corner on
    the element's normal, at distance d."""
    x = np.asarray(a, dtype=float) / d
    y = np.asarray(b, dtype=float) / d
    sx = np.sqrt(1.0 + x * x)
    sy = np.sqrt(1.0 + y * y)
    return (x / sx * np.arctan(y / sx) + y / sy * np.arctan(x / sy)) / (2.0 * math.pi)


def _signed_corner(x, y, d):
    return np.sign(x) * np.sign(y) * corner_view_factor(np.abs(x), np.abs(y), d)


def rectangle_view_factor(x1, x2, y1, y2, d):
    """View factor from a differential element to the parallel rectangle [x1, x2] x [y1, y2]
    (coordinates in the rectangle's plane measured from the foot of the element's normal),
    at perpendicular distance d > 0. Superposition of corner rectangles."""
    return (
        _signed_corner(x2, y2, d) - _signed_corner(x1, y2, d)
        - _signed_corner(x2, y1, d) + _signed_corner(x1, y1, d)
    )


def centred_flame_view_factor(width: float, height: float, distance: float) -> float:
    """SIAM geometry: a target facing the centre of a width x height flame at ``distance``."""
    return float(4.0 * corner_view_factor(width / 2.0, height / 2.0, distance))


def time_to_ignition_s(flux: float) -> float:
    """Constant-flux time to reach the FTP ignition criterion (Cohen 2004 eq 3); inf below 13.1."""
    if flux <= FTP_CRITICAL_FLUX:
        return math.inf
    return FTP_IGNITION / (flux - FTP_CRITICAL_FLUX) ** FTP_EXPONENT


def flux_time_product(flux: np.ndarray, duration_s: np.ndarray) -> float:
    """FTP of a piecewise-constant flux history (kW/m2 over durations in s)."""
    excess = np.clip(np.asarray(flux, dtype=float) - FTP_CRITICAL_FLUX, 0.0, None)
    return float(np.sum(excess**FTP_EXPONENT * np.asarray(duration_s, dtype=float)))


def flame_length_m(intensity_kw_m: np.ndarray, crowning: np.ndarray) -> np.ndarray:
    """Byram (1959) for surface fire, Thomas (1963) when CFB >= 0.1 (as suggested by Rothermel
    1991 for crown fires, via Alexander & Cruz 2012 p.99; approximate for crown fires)."""
    i = np.clip(np.asarray(intensity_kw_m, dtype=float), 0.0, None)
    return np.where(crowning, 0.0266 * i ** (2.0 / 3.0), 0.0775 * i**0.46)


@dataclass
class Emitters:
    """Burned cells as flame panels, in a local metric frame (x east, y north, metres).

    Each cell is a vertical panel of width ``cell_size`` and height = flame length, facing its
    spread direction, present from its arrival until ``end_min``.
    """

    x: np.ndarray
    y: np.ndarray
    start_min: np.ndarray
    end_min: np.ndarray
    flame_m: np.ndarray
    normal_x: np.ndarray  # unit spread direction
    normal_y: np.ndarray
    cell_size: float
    # Local frame: x = (lng - lng0) * m_per_deg_lng, y = (lat - lat0) * m_per_deg_lat
    lat0: float = 0.0
    lng0: float = 0.0
    m_per_deg_lat: float = 111320.0
    m_per_deg_lng: float = 111320.0

    def to_local(self, lat, lng):
        return (np.asarray(lng) - self.lng0) * self.m_per_deg_lng, (np.asarray(lat) - self.lat0) * self.m_per_deg_lat


@dataclass
class BuildingExposure:
    """Exposure of one building over the simulated period."""

    lat: float
    lng: float
    min_distance_m: float  # footprint (or centroid) to the nearest burned cell edge
    first_within_min: dict[float, float] = field(default_factory=dict)  # band (m) -> minutes
    peak_flux: float = 0.0  # kW/m2, Cohen emissive power
    minutes_over_12_5: float = 0.0
    first_over_12_5_min: float = math.inf
    first_over_25_min: float = math.inf
    ftp_index: float = 0.0  # FTP / 11,501, Cohen emissive power
    ftp_index_high: float = 0.0  # same with 200 kW/m2
    first_ftp_reached_min: float = math.inf

    @property
    def band(self) -> str:
        d = self.min_distance_m
        if d <= 10.0:
            return "flame_contact"
        if d <= 30.0:
            return "radiant"
        if d <= 100.0:
            return "short_range_ember"
        if d <= 500.0:
            return "long_range_ember"
        return "none"


def building_exposure(
    targets: list,
    emitters: Emitters,
    *,
    duration_min: float,
    use_footprints: bool,
) -> list[BuildingExposure]:
    """Distance, timing and radiant exposure for each target.

    Args:
        targets: per building a tuple (lat, lng, geometry_in_local_metres) where the geometry is
            a shapely footprint (``use_footprints``) or a shapely Point at the centroid.
        emitters: burned cells as flame panels (see ``Emitters``).
        duration_min: end of the simulation; flux after it is not counted.
    """
    import shapely
    from scipy.spatial import cKDTree

    out: list[BuildingExposure] = []
    if len(emitters.x) == 0:
        return [BuildingExposure(lat, lng, math.inf) for lat, lng, _ in targets]
    pts = np.column_stack([emitters.x, emitters.y])
    tree = cKDTree(pts)
    half = emitters.cell_size / 2.0
    for lat, lng, geom in targets:
        c = geom.centroid
        reach = 0.0
        if use_footprints:
            minx, miny, maxx, maxy = geom.bounds
            reach = math.hypot(maxx - minx, maxy - miny) / 2.0
        idx = np.asarray(
            tree.query_ball_point([c.x, c.y], DISTANCE_BANDS_M[-1] + reach + half), dtype=int
        )
        rec = BuildingExposure(lat, lng, math.inf)
        out.append(rec)
        if len(idx) == 0:
            continue
        cell_pts = shapely.points(pts[idx])
        dist = np.clip(shapely.distance(geom, cell_pts) - half, 0.0, None)
        rec.min_distance_m = float(dist.min())
        starts = emitters.start_min[idx]
        for band in DISTANCE_BANDS_M:
            within = dist <= band
            if within.any():
                rec.first_within_min[band] = float(starts[within].min())

        near = dist <= RADIANT_RANGE_M
        if not near.any():
            continue
        sel = idx[near]
        # Target points: the centroid, or points every ~3 m (at most 16) around the footprint wall. Each
        # point faces every panel (worst-case orientation); the building takes the worst point.
        if use_footprints:
            ring = shapely.segmentize(geom.exterior if hasattr(geom, "exterior") else geom.boundary, 3.0)
            wall = shapely.get_coordinates(ring)[:-1]
            if len(wall) > MAX_WALL_POINTS:
                wall = wall[np.linspace(0, len(wall) - 1, MAX_WALL_POINTS).astype(int)]
        else:
            wall = np.array([[c.x, c.y]])
        nx, ny = emitters.normal_x[sel], emitters.normal_y[sel]
        h = emitters.flame_m[sel]
        vx = wall[:, :1] - emitters.x[sel]  # (points, emitters)
        vy = wall[:, 1:] - emitters.y[sel]
        d = np.abs(vx * nx + vy * ny)  # flames radiate from both faces
        s = vx * ny - vy * nx  # target offset along the panel
        d = np.maximum(d, half / 2.0)  # a target inside the cell is in flame contact
        # Target facing the panel at the flame's mid-height (SIAM's centred geometry)
        f = rectangle_view_factor(-s - half, -s + half, -h / 2.0, h / 2.0, d)
        _accumulate(rec, f, emitters.start_min[sel], emitters.end_min[sel], duration_min)
    return out


def _accumulate(rec: BuildingExposure, f, start, end, duration_min):
    """Sweep the piecewise-constant view-factor history of each target point (rows of ``f``,
    one column per panel) and keep, in ``rec``, the worst value of each metric over points."""
    start = np.minimum(start, duration_min)
    end = np.minimum(end, duration_min)
    keep = (end > start) & (f.max(axis=0) > 0.0)
    if not keep.any():
        return
    f, start, end = f[:, keep], start[keep], end[keep]
    times = np.unique(np.concatenate([start, end]))
    delta = np.zeros((len(times), f.shape[0]))
    np.add.at(delta, np.searchsorted(times, start), f.T)
    np.add.at(delta, np.searchsorted(times, end), -f.T)
    # Total view factor on [times[i], times[i+1]) for each point. Each panel is seen face-on
    # (worst case), so a target surrounded by fire could sum past 1; a surface element cannot
    # see more than its hemisphere, so cap at 1 (flux at most the emissive power).
    vf = np.clip(np.cumsum(delta, axis=0)[:-1], 0.0, 1.0)
    dur_s = (np.diff(times) * 60.0)[:, None]
    t0 = times[:-1]
    q = vf * EMISSIVE_POWER_COHEN
    rec.peak_flux = float(q.max())
    over = q >= NRC_FLUX_LOW
    rec.minutes_over_12_5 = float((over * dur_s).sum(axis=0).max() / 60.0)
    if over.any():
        rec.first_over_12_5_min = float(t0[over.any(axis=1)][0])
    over25 = q >= NRC_FLUX_HIGH
    if over25.any():
        rec.first_over_25_min = float(t0[over25.any(axis=1)][0])
    excess = np.clip(q - FTP_CRITICAL_FLUX, 0.0, None) ** FTP_EXPONENT * dur_s
    cum = np.cumsum(excess, axis=0)
    rec.ftp_index = float(cum[-1].max() / FTP_IGNITION)
    for j in np.nonzero(cum[-1] >= FTP_IGNITION)[0]:
        i = int(np.searchsorted(cum[:, j], FTP_IGNITION))
        before = cum[i - 1, j] if i > 0 else 0.0
        rate = excess[i, j] / dur_s[i, 0]
        reached = float(times[i] + (FTP_IGNITION - before) / rate / 60.0)
        rec.first_ftp_reached_min = min(rec.first_ftp_reached_min, reached)
    high = np.clip(vf * EMISSIVE_POWER_HIGH - FTP_CRITICAL_FLUX, 0.0, None) ** FTP_EXPONENT * dur_s
    rec.ftp_index_high = float(high.sum(axis=0).max() / FTP_IGNITION)


def summarize(records: list[BuildingExposure], t_min: float, inside: int = 0) -> dict:
    """Counts of buildings by exposure reached by time ``t_min``."""
    summary = {"inside_perimeter": inside}
    for band in DISTANCE_BANDS_M:
        summary[f"within_{int(band)}m"] = sum(
            1 for r in records if r.first_within_min.get(band, math.inf) <= t_min
        )
    summary["flux_over_12_5"] = sum(1 for r in records if r.first_over_12_5_min <= t_min)
    summary["flux_over_25"] = sum(1 for r in records if r.first_over_25_min <= t_min)
    summary["ftp_reached"] = sum(1 for r in records if r.first_ftp_reached_min <= t_min)
    return summary
