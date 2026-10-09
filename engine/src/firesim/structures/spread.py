"""Building-to-building spread over the unit graph, driven by the FBP grid run (Hamada option).

Specification: ``docs/structure-spread-spec.md`` §3-4. **Illustrative — not validated in
Canada.** Outputs are modelled involvement times, not predictions of which buildings burn.

1. Each unit is reached by the wildland front at ``t_front``: the earliest arrival of a burned
   grid cell whose edge comes within ``wildland_contact_m`` of its footprint (FireSim heuristic;
   10 m is the flame-contact band of docs/building-exposure.md).
2. From every unit reached, fire passes to neighbours within the graph cutoff in the Hamada
   crossing time (a0 + d) / rate(theta), the wind taken from the run's weather periods; a
   crossing that spans periods accumulates progress period by period (FireSim heuristic).
3. First involvement times over the graph by Dijkstra (non-negative, first-in-first-out
   crossings). No burnout: Hamada has none (Purnomo et al. 2026, Fire Safety J. 161: 104651,
   pp.3, 17).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from firesim.structures.hamada import DEFAULT_COMBUSTIBLE_FRACTION, rate_toward
from firesim.structures.units import StructureUnits

LABEL = "illustrative — not validated in Canada"
MODEL = "hamada"
DEFAULT_WILDLAND_CONTACT_M = 10.0  # spec §3: FireSim heuristic (flame-contact band)

SOURCE_NONE = 0
SOURCE_FRONT = 1  # reached by the wildland front
SOURCE_STRUCTURE = 2  # building-to-building (Hamada)


@dataclass(frozen=True)
class WindPeriod:
    """Wind from ``start_min`` until the next period: 10 m open wind (km/h, FROM degrees)."""

    start_min: float
    wind_speed_kmh: float
    wind_direction_deg: float


@dataclass
class StructureSpreadResult:
    """First modelled involvement time of each unit (minutes from the start; inf = not)."""

    t_min: np.ndarray
    source: np.ndarray  # SOURCE_* per unit
    parent: np.ndarray  # unit that passed the fire on (-1 for front or none)
    t_front_min: np.ndarray
    params: dict = field(default_factory=dict)

    def counts_at(self, t_min: float) -> dict:
        """Counts of units involved by ``t_min`` (JSON-friendly, with the label)."""
        by = self.t_min <= t_min
        front = int(np.sum(by & (self.source == SOURCE_FRONT)))
        structure = int(np.sum(by & (self.source == SOURCE_STRUCTURE)))
        return {
            "model": MODEL,
            "label": LABEL,
            "units_in_run": int(len(self.t_min)),
            "units_front_contact": front,
            "units_structure_to_structure": structure,
            "units_involved": front + structure,
            **self.params,
        }


def front_contact_times(
    units: StructureUnits,
    cell_x: np.ndarray,
    cell_y: np.ndarray,
    cell_arrival_min: np.ndarray,
    cell_size_m: float,
    contact_m: float = DEFAULT_WILDLAND_CONTACT_M,
) -> np.ndarray:
    """Earliest arrival (minutes) of a burned cell within ``contact_m`` of each footprint.

    Cells are given by their centres in the units' local frame (metres). Distance is from the
    footprint to the cell centre minus half a cell, as ``firesim.exposure`` measures it.
    Units never reached get inf.
    """
    import shapely
    from scipy.spatial import cKDTree

    n = len(units)
    out = np.full(n, np.inf)
    cell_arrival_min = np.asarray(cell_arrival_min, dtype=float)
    ok = np.isfinite(cell_arrival_min)
    if n == 0 or not ok.any():
        return out
    pts = np.column_stack([np.asarray(cell_x, float)[ok], np.asarray(cell_y, float)[ok]])
    arr = cell_arrival_min[ok]
    half = cell_size_m / 2.0
    b = shapely.bounds(units.footprints)
    reach = np.hypot(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) / 2.0
    radius = reach + contact_m + half * math.sqrt(2.0)
    tree = cKDTree(pts)
    hits = tree.query_ball_point(np.column_stack([units.x, units.y]), radius)
    for i, idx in enumerate(hits):
        if not idx:
            continue
        idx = np.asarray(idx, dtype=int)
        dist = shapely.distance(units.footprints[i], shapely.points(pts[idx])) - half
        near = dist <= contact_m
        if near.any():
            out[i] = float(arr[idx[near]].min())
    return out


def hamada_spread(
    units: StructureUnits,
    t_front_min: np.ndarray,
    wind: list[WindPeriod],
    *,
    duration_min: float,
    fb: float = DEFAULT_COMBUSTIBLE_FRACTION,
) -> StructureSpreadResult:
    """First involvement time of every unit: front contact, then Hamada building-to-building.

    Args:
        units: building units with their neighbour graph (the cutoff limits spread, spec §4.4).
        t_front_min: minutes each unit is reached by the wildland front (inf = not).
        wind: weather periods in time order; the first applies from its start back to 0 and
            the last indefinitely.
        duration_min: end of the run; nothing is involved after it.
        fb: Hamada combustible fraction.
    """
    if not wind:
        raise ValueError("wind needs at least one period")
    n = len(units)
    t = np.asarray(t_front_min, dtype=float).copy()
    t[t > duration_min] = np.inf
    source = np.where(np.isfinite(t), SOURCE_FRONT, SOURCE_NONE).astype(np.int8)
    parent = np.full(n, -1, dtype=np.int64)
    starts = np.array([w.start_min for w in wind], dtype=float)
    speed = np.array([w.wind_speed_kmh / 3.6 for w in wind])
    to = np.radians(np.array([w.wind_direction_deg for w in wind]) + 180.0)
    sx, sy = np.sin(to), np.cos(to)  # downwind unit vector (x east, y north)

    heap = [(float(t[i]), int(i)) for i in np.nonzero(np.isfinite(t))[0]]
    heapq.heapify(heap)
    done = np.zeros(n, dtype=bool)
    while heap:
        ti, i = heapq.heappop(heap)
        if done[i] or ti > t[i]:
            continue
        done[i] = True
        nb, sep = units.neighbours(i)
        if len(nb) == 0:
            continue
        keep = ~done[nb]
        nb, sep = nb[keep], sep[keep]
        if len(nb) == 0:
            continue
        a0 = (units.size_m[i] + units.size_m[nb]) / 2.0
        dx, dy = units.x[nb] - units.x[i], units.y[nb] - units.y[i]
        dist = np.hypot(dx, dy)
        safe = np.where(dist > 0, dist, 1.0)
        arrive = _cross(ti, a0, sep, dx / safe, dy / safe, dist > 0, starts, speed, sx, sy, fb,
                        duration_min)
        better = arrive < t[nb]
        for j, tj in zip(nb[better], arrive[better]):
            t[j] = tj
            source[j] = SOURCE_STRUCTURE
            parent[j] = i
            heapq.heappush(heap, (float(tj), int(j)))
    return StructureSpreadResult(t_min=t, source=source, parent=parent,
                                 t_front_min=np.asarray(t_front_min, dtype=float),
                                 params={"combustible_fraction": fb,
                                         "neighbour_cutoff_m": units.neighbour_cutoff_m})


def _cross(t0, a0, sep, ux, uy, has_dir, starts, speed, sx, sy, fb, duration_min):
    """Arrival minutes of a crossing that starts at ``t0`` toward each neighbour, accumulating
    progress (a0 + d) at the Hamada rate of each weather period in turn."""
    length = a0 + sep
    remaining = np.ones(len(sep))
    arrive = np.full(len(sep), np.inf)
    k = max(int(np.searchsorted(starts, t0, side="right")) - 1, 0)
    t = t0
    active = np.ones(len(sep), dtype=bool)
    while active.any() and t <= duration_min:
        end = starts[k + 1] if k + 1 < len(starts) else math.inf
        cos_t = np.where(has_dir, ux * sx[k] + uy * sy[k], 1.0)
        rate = rate_toward(a0, sep, speed[k], cos_t, fb)  # m/min
        need = np.where(rate > 0, remaining * length / np.where(rate > 0, rate, 1.0), np.inf)
        fin = active & (t + need <= end)
        arrive[fin] = t + need[fin]
        active &= ~fin
        if not math.isfinite(end):
            break
        remaining = np.where(active, remaining - rate * (end - t) / length, remaining)
        t = end
        k += 1
    arrive[arrive > duration_min] = np.inf
    return arrive


def structure_spread_for_grid_run(
    footprints,
    emitters,
    schedule,
    duration_min: float,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    neighbour_cutoff_m: float | None = None,
    contact_m: float = DEFAULT_WILDLAND_CONTACT_M,
    fb: float = DEFAULT_COMBUSTIBLE_FRACTION,
) -> StructureSpreadResult | None:
    """Hamada structure spread coupled to a finished grid run.

    Args:
        footprints: shapely footprints (lng, lat) of the buildings in the run area.
        emitters: the grid run's flame panels (``firesim.exposure.Emitters``): burned cell
            centres and arrival minutes.
        schedule: the run's weather periods, ``[(start_min, SpreadConditions), ...]``.
        duration_min: run length.
        bbox: run area ``(lat_min, lat_max, lng_min, lng_max)``.
    """
    from firesim.structures.units import DEFAULT_NEIGHBOUR_CUTOFF_M, build_units

    if emitters is None or not footprints:
        return None
    units = build_units(
        footprints, bbox=bbox,
        neighbour_cutoff_m=DEFAULT_NEIGHBOUR_CUTOFF_M if neighbour_cutoff_m is None else neighbour_cutoff_m,
    )
    if len(units) == 0:
        return None
    lat = emitters.lat0 + np.asarray(emitters.y) / emitters.m_per_deg_lat
    lng = emitters.lng0 + np.asarray(emitters.x) / emitters.m_per_deg_lng
    cx, cy = units.frame.to_local(lat, lng)
    t_front = front_contact_times(units, cx, cy, emitters.start_min, emitters.cell_size, contact_m)
    wind = [WindPeriod(float(s), float(c.wind_speed), float(c.wind_direction)) for s, c in schedule]
    result = hamada_spread(units, t_front, wind, duration_min=duration_min, fb=fb)
    result.params["wildland_contact_m"] = contact_m
    return result
