"""Building-to-building spread over the unit graph, driven by the FBP grid run (Hamada option).

Specification: ``docs/structure-spread-spec.md`` §3-4. **Illustrative — not validated in
Canada.** Outputs are modelled involvement times, not predictions of which buildings burn.

1. Each unit is reached by the wildland front at ``t_front``: the earliest arrival of a burned
   grid cell whose square lies within ``wildland_contact_m`` of the unit's *building cells*, the
   grid cells its footprint touches (the cells the all-touched building mask makes non-fuel).
   On the 50 m engine grid with the 10 m default this is: the front reaches one of the 8
   neighbours of a building cell (or a building cell itself, where it is not masked). The
   outcome depends only on which cells a footprint touches, not on where it sits inside them
   (FireSim heuristic [H], spec §3; 10 m is the flame-contact band of docs/building-exposure.md).
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
FRONT_CONTACT_RULE = "building_cells"
DETAIL_SIMPLIFY_M = 0.5  # map footprints: Douglas-Peucker tolerance, metres (display only)  # spec §3 [H]: measured from the footprint's grid cells

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
    """First modelled involvement time of each unit (minutes from the start; inf = not).

    ``units_in_run`` is the number of buildings in the run area (the fuel grid's box); the
    units actually built (``len(t_min)``) can be fewer: only the area the spread can reach is
    built (``structure_spread_for_grid_run``).
    """

    t_min: np.ndarray
    source: np.ndarray  # SOURCE_* per unit
    parent: np.ndarray  # unit that passed the fire on (-1 for front or none)
    t_front_min: np.ndarray
    params: dict = field(default_factory=dict)
    units_in_run: int | None = None
    footprints: np.ndarray | None = None  # local-metre footprints of the built units
    frame: object = None  # LocalFrame of ``footprints``

    def counts_at(self, t_min: float) -> dict:
        """Counts of units involved by ``t_min`` (JSON-friendly, with the label)."""
        by = self.t_min <= t_min
        front = int(np.sum(by & (self.source == SOURCE_FRONT)))
        structure = int(np.sum(by & (self.source == SOURCE_STRUCTURE)))
        return {
            "model": MODEL,
            "label": LABEL,
            "computed": True,
            "units_in_run": int(len(self.t_min) if self.units_in_run is None else self.units_in_run),
            "units_built": int(len(self.t_min)),
            "units_front_contact": front,
            "units_structure_to_structure": structure,
            "units_involved": front + structure,
            **self.params,
        }

    def involved_detail(self, simplify_m: float = DETAIL_SIMPLIFY_M) -> list[dict]:
        """The involved units for the map (spec §9; owner decision 2026-10-10, D3 reversed).

        One entry per involved unit, in involvement order: ``id`` (0.. in that order),
        ``t_h`` (hours from the start), ``mechanism`` ("front" = wildland front contact,
        "b2b" = building to building), ``source_id`` (the ``id`` of the unit that passed the
        fire on, b2b only) and ``polygon`` (footprint rings in (lng, lat), simplified by
        ``simplify_m`` and rounded to 6 decimals, ~0.1 m). Only involved units; no attributes
        beyond these. Illustrative — not validated in Canada.
        """
        import shapely

        inv = np.nonzero(np.isfinite(self.t_min))[0]
        if len(inv) == 0 or self.footprints is None or self.frame is None:
            return []
        inv = inv[np.lexsort((inv, self.t_min[inv]))]
        new_id = {int(u): k for k, u in enumerate(inv)}
        frame = self.frame
        mlng = frame.m_per_deg_lng

        def _to_ll(coords):
            return np.column_stack([np.round(frame.lng0 + coords[:, 0] / mlng, 6),
                                    np.round(frame.lat0 + coords[:, 1] / 111320.0, 6)])

        geoms = shapely.simplify(self.footprints[inv], simplify_m, preserve_topology=True)
        geoms = shapely.transform(geoms, _to_ll)
        out = []
        for k, (u, g) in enumerate(zip(inv, geoms)):
            if g.geom_type == "MultiPolygon":  # keep the largest part (map display only)
                g = max(g.geoms, key=lambda p: p.area)
            mech = "front" if self.source[u] == SOURCE_FRONT else "b2b"
            parent = int(self.parent[u])
            out.append({
                "id": k,
                "t_h": round(float(self.t_min[u]) / 60.0, 3),
                "mechanism": mech,
                "source_id": new_id.get(parent) if mech == "b2b" and parent >= 0 else None,
                "polygon": [[[float(x), float(y)] for x, y in g.exterior.coords]],
            })
        return out


def contact_offsets(contact_m: float, cell_w_m: float, cell_h_m: float) -> np.ndarray:
    """(d_row, d_col) offsets of the cells whose square lies within ``contact_m`` of a cell's
    square (edge-to-edge; 0 for cells sharing an edge or a corner). Always includes the cell
    and its 8 neighbours; more only when ``contact_m`` reaches a whole cell."""
    k = int(math.ceil(max(contact_m, 0.0) / min(cell_w_m, cell_h_m))) + 1
    d = np.arange(-k, k + 1)
    dr, dc = np.meshgrid(d, d, indexing="ij")
    gap_x = np.maximum(np.abs(dc) - 1, 0) * cell_w_m
    gap_y = np.maximum(np.abs(dr) - 1, 0) * cell_h_m
    keep = np.hypot(gap_x, gap_y) <= max(contact_m, 0.0) + 1e-9
    return np.column_stack([dr[keep], dc[keep]])


def building_cell_contact_times(
    units: StructureUnits,
    arrival_min: np.ndarray,
    bounds: tuple[float, float, float, float],
    contact_m: float = DEFAULT_WILDLAND_CONTACT_M,
) -> np.ndarray:
    """Minutes each unit is first reached by the wildland front (inf = not reached).

    Spec §3 [H]. A unit's *building cells* are the grid cells its footprint touches, the same
    cells the all-touched building mask makes non-fuel (``firesim.data.environment``). The
    unit is reached at the earliest arrival of a burned cell whose square is within
    ``contact_m`` of the square of one of its building cells. Cell-to-cell distances on a
    regular grid are 0 (shared edge or corner) or at least one cell, so for any ``contact_m``
    below the cell size (10 m on the 50 m engine grid) the rule is: the front has reached a
    building cell or one of its 8 neighbours. The result depends only on which cells a
    footprint touches, not on its position inside them. A burned cell two or more cells away
    (e.g. across a non-fuel road or river at least one cell wide that the footprint does not
    touch) does not reach the unit.

    Args:
        units: building units (local metres in ``units.frame``).
        arrival_min: (rows, cols) arrival minutes of the grid run, row 0 = north, inf where
            unburned (``CAFrame.arrival``).
        bounds: grid bounds ``(lat_min, lat_max, lng_min, lng_max)``.
        contact_m: wildland contact distance, metres.
    """
    import shapely
    from scipy import ndimage

    n = len(units)
    out = np.full(n, np.inf)
    arrival_min = np.asarray(arrival_min, dtype=float)
    if n == 0 or arrival_min.size == 0 or not np.isfinite(arrival_min).any():
        return out
    rows, cols = arrival_min.shape
    lat_min, lat_max, lng_min, lng_max = bounds
    x_left, y_top = (float(v) for v in units.frame.to_local(lat_max, lng_min))
    x_right, y_bot = (float(v) for v in units.frame.to_local(lat_min, lng_max))
    cw, ch = (x_right - x_left) / cols, (y_top - y_bot) / rows

    # Earliest arrival within contact_m of every cell (a min filter over the offsets)
    off = contact_offsets(contact_m, cw, ch)
    k = int(np.abs(off).max())
    struct = np.zeros((2 * k + 1, 2 * k + 1), dtype=bool)
    struct[off[:, 0] + k, off[:, 1] + k] = True
    near = ndimage.minimum_filter(arrival_min, footprint=struct, mode="constant", cval=np.inf)

    b = shapely.bounds(units.footprints)
    c0 = np.floor((b[:, 0] - x_left) / cw).astype(np.int64)
    c1 = np.floor((b[:, 2] - x_left) / cw).astype(np.int64)
    r0 = np.floor((y_top - b[:, 3]) / ch).astype(np.int64)
    r1 = np.floor((y_top - b[:, 1]) / ch).astype(np.int64)
    inside = (c1 >= 0) & (c0 < cols) & (r1 >= 0) & (r0 < rows)
    c0, c1 = np.clip(c0, 0, cols - 1), np.clip(c1, 0, cols - 1)
    r0, r1 = np.clip(r0, 0, rows - 1), np.clip(r1, 0, rows - 1)

    # Footprint inside one cell (most buildings on a 50 m grid): that cell is its building cell
    single = inside & (c0 == c1) & (r0 == r1)
    out[single] = near[r0[single], c0[single]]
    # Footprints over several cells: the cells their geometry touches (all-touched)
    for i in np.nonzero(inside & ~single)[0]:
        rr, cc = np.mgrid[r0[i]:r1[i] + 1, c0[i]:c1[i] + 1]
        rr, cc = rr.ravel(), cc.ravel()
        t = near[rr, cc]
        fin = np.isfinite(t)
        if not fin.any():
            continue
        rr, cc, t = rr[fin], cc[fin], t[fin]
        boxes = shapely.box(x_left + cc * cw, y_top - (rr + 1) * ch,
                            x_left + (cc + 1) * cw, y_top - rr * ch)
        hit = shapely.intersects(units.footprints[i], boxes)
        if hit.any():
            out[i] = float(t[hit].min())
    return out


def footprint_contact_times(
    units: StructureUnits,
    cell_x: np.ndarray,
    cell_y: np.ndarray,
    cell_arrival_min: np.ndarray,
    cell_size_m: float,
    contact_m: float = DEFAULT_WILDLAND_CONTACT_M,
) -> np.ndarray:
    """Earliest arrival (minutes) of a burned cell within ``contact_m`` of each footprint.

    **Not the coupling rule** since 2026-10-09 (see ``building_cell_contact_times``). This was
    the first front-contact rule; it is kept as a diagnostic (distance bands, report R7/R8).
    On a grid coarser than the contact distance its outcome depends on where a footprint sits
    inside its (masked) cell.

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


class ListFootprintSource:
    """Footprint source over an in-memory list of shapely footprints (lng, lat).

    ``structure_spread_for_grid_run`` asks a source for the footprints that intersect a box;
    ``firesim.data.building_index.BuildingIndex`` is the other implementation (it builds
    shapely geometries only for the footprints asked for)."""

    def __init__(self, footprints) -> None:
        import shapely

        self._geoms = np.asarray(list(footprints), dtype=object)
        if len(self._geoms):
            ok = ~(shapely.is_missing(self._geoms) | shapely.is_empty(self._geoms))
            self._geoms = self._geoms[ok]
        b = shapely.bounds(self._geoms) if len(self._geoms) else np.zeros((0, 4))
        self._b = b  # lng_min, lat_min, lng_max, lat_max
        c = shapely.centroid(self._geoms) if len(self._geoms) else np.zeros(0)
        self._clng = shapely.get_x(c) if len(self._geoms) else np.zeros(0)
        self._clat = shapely.get_y(c) if len(self._geoms) else np.zeros(0)

    def _hit(self, bbox):
        lat_min, lat_max, lng_min, lng_max = bbox
        b = self._b
        return (b[:, 0] <= lng_max) & (b[:, 2] >= lng_min) & (b[:, 1] <= lat_max) & (b[:, 3] >= lat_min)

    def count_in_box(self, bbox) -> int:
        """Footprints whose centroid is inside ``bbox`` (lat_min, lat_max, lng_min, lng_max)."""
        lat_min, lat_max, lng_min, lng_max = bbox
        return int(np.sum((self._clat >= lat_min) & (self._clat <= lat_max)
                          & (self._clng >= lng_min) & (self._clng <= lng_max)))

    def count_intersecting(self, bbox) -> int:
        return int(self._hit(bbox).sum())

    def footprints_intersecting(self, bbox) -> list:
        """Footprints whose bounds intersect ``bbox``, in input order."""
        return list(self._geoms[self._hit(bbox)])


DEFAULT_MAX_UNITS = 60_000  # OOM guard (2 GB API machine; ~1.5 kB per unit, see report)
NOT_COMPUTED_NOTE = "not computed: too many buildings in run area"


def not_computed(units_in_run: int, units_needed: int, max_units: int, params: dict) -> dict:
    """Frame payload when the guard stops the units build (labelled, no counts)."""
    return {"model": MODEL, "label": LABEL, "computed": False, "note": NOT_COMPUTED_NOTE,
            "units_in_run": int(units_in_run), "units_needed": int(units_needed),
            "max_units": int(max_units), "units_front_contact": None,
            "units_structure_to_structure": None, "units_involved": None, **params}


@dataclass
class StructureSpreadSkipped:
    """The guard's outcome: structure spread was not computed (frame payload is constant)."""

    payload: dict

    def counts_at(self, t_min: float) -> dict:
        return dict(self.payload)

    def involved_detail(self, simplify_m: float = 0.0) -> list[dict]:
        return []


def structure_spread_for_grid_run(
    footprints,
    arrival_min: np.ndarray,
    schedule,
    duration_min: float,
    *,
    bbox: tuple[float, float, float, float],
    neighbour_cutoff_m: float | None = None,
    contact_m: float = DEFAULT_WILDLAND_CONTACT_M,
    fb: float = DEFAULT_COMBUSTIBLE_FRACTION,
    max_units: int = DEFAULT_MAX_UNITS,
):
    """Hamada structure spread coupled to a finished grid run, built only where it can reach.

    Units are built only for the footprints that intersect a *reachable box* R: the box of the
    cells the front burned, grown by the front-contact reach (``contact_offsets`` radius + 2
    cells) and by a spread margin. After the spread is run on those units, the result is
    exact for the whole run area if no involved unit lies within ``neighbour_cutoff_m`` (plus
    a 1 m guard) of R's edge: a footprint not built lies wholly outside R, so it is farther
    than the cutoff from every involved unit (not a graph neighbour) and too far from the
    burned cells for front contact, hence never involved, and cannot change any built unit's
    time. Otherwise the margin is doubled and the build repeated. Sides of R on the fuel
    grid's edge need no check (units are only the footprints centred in the grid box). Every
    unit is in a fixed frame at the grid box centre, so results do not depend on R.

    If R would hold more than ``max_units`` footprints, nothing is built and the result is a
    ``StructureSpreadSkipped`` whose frames say ``NOT_COMPUTED_NOTE`` (OOM guard).

    Args:
        footprints: a footprint source (``count_in_box`` / ``count_intersecting`` /
            ``footprints_intersecting``, e.g. ``BuildingIndex``) or a list of shapely
            footprints (lng, lat).
        arrival_min: the grid run's arrival minutes per cell, (rows, cols), row 0 = north,
            inf where unburned (``CAFrame.arrival``).
        schedule: the run's weather periods, ``[(start_min, SpreadConditions), ...]``.
        duration_min: run length.
        bbox: the fuel grid's bounds ``(lat_min, lat_max, lng_min, lng_max)``: the run area
            and the frame of ``arrival_min``.
    """
    from firesim.structures.units import (
        DEFAULT_NEIGHBOUR_CUTOFF_M,
        M_PER_DEG_LAT,
        LocalFrame,
        build_units,
    )

    if arrival_min is None or footprints is None:
        return None
    source = footprints if hasattr(footprints, "footprints_intersecting") else ListFootprintSource(footprints)
    lat_min, lat_max, lng_min, lng_max = bbox
    units_in_run = source.count_in_box(bbox)
    if units_in_run == 0:
        return None
    cutoff = DEFAULT_NEIGHBOUR_CUTOFF_M if neighbour_cutoff_m is None else float(neighbour_cutoff_m)
    frame = LocalFrame((lat_min + lat_max) / 2.0, (lng_min + lng_max) / 2.0)
    params = {"combustible_fraction": fb, "neighbour_cutoff_m": cutoff,
              "wildland_contact_m": contact_m, "front_contact_rule": FRONT_CONTACT_RULE}
    wind = [WindPeriod(float(s), float(c.wind_speed), float(c.wind_direction)) for s, c in schedule]

    arrival_min = np.asarray(arrival_min, dtype=float)
    rows, cols = arrival_min.shape
    burned = np.isfinite(arrival_min) & (arrival_min <= duration_min)
    if not burned.any():
        res = StructureSpreadResult(t_min=np.zeros(0), source=np.zeros(0, dtype=np.int8),
                                    parent=np.zeros(0, dtype=np.int64), t_front_min=np.zeros(0),
                                    params=params, units_in_run=units_in_run, frame=frame)
        return res
    dlat, dlng = (lat_max - lat_min) / rows, (lng_max - lng_min) / cols
    r_idx, c_idx = np.nonzero(burned.any(axis=1))[0], np.nonzero(burned.any(axis=0))[0]
    b_lat = (lat_max - (r_idx.max() + 1) * dlat, lat_max - r_idx.min() * dlat)
    b_lng = (lng_min + c_idx.min() * dlng, lng_min + (c_idx.max() + 1) * dlng)
    cw, ch = dlng * frame.m_per_deg_lng, dlat * M_PER_DEG_LAT
    k = int(np.abs(contact_offsets(contact_m, cw, ch)).max())
    contact_reach_m = (k + 2) * max(cw, ch)
    margin_m = contact_reach_m + 2.0 * cutoff + 250.0  # first guess; grown until exact

    while True:
        mlat, mlng = margin_m / M_PER_DEG_LAT, margin_m / frame.m_per_deg_lng
        reach = (max(lat_min, b_lat[0] - mlat), min(lat_max, b_lat[1] + mlat),
                 max(lng_min, b_lng[0] - mlng), min(lng_max, b_lng[1] + mlng))
        whole_grid = reach == (lat_min, lat_max, lng_min, lng_max)
        needed = source.count_intersecting(reach)
        if needed > max_units:
            return StructureSpreadSkipped(not_computed(units_in_run, needed, max_units, params))
        units = build_units(source.footprints_intersecting(reach), neighbour_cutoff_m=cutoff,
                            bbox=bbox, frame=frame)
        t_front = building_cell_contact_times(units, arrival_min, bbox, contact_m)
        result = hamada_spread(units, t_front, wind, duration_min=duration_min, fb=fb)
        if whole_grid or _inside_reach(units, result, reach, frame, cutoff + 1.0, bbox):
            break
        margin_m *= 2.0
    result.params.update(params)
    result.units_in_run = units_in_run
    inv = np.isfinite(result.t_min)
    result.footprints = np.where(inv, units.footprints, None)  # keep only involved geometry
    result.frame = frame
    return result


def _inside_reach(units, result, reach, frame, guard_m, grid_bbox) -> bool:
    """True when every involved unit's footprint, grown by ``guard_m``, stays inside ``reach``
    on every side of ``reach`` that is not on the grid box's edge."""
    import shapely

    inv = np.isfinite(result.t_min)
    if not inv.any():
        return True
    b = shapely.bounds(units.footprints[inv])  # local metres
    r_lat0, r_lat1, r_lng0, r_lng1 = reach
    g_lat0, g_lat1, g_lng0, g_lng1 = grid_bbox
    x0, y0 = (float(v) for v in frame.to_local(r_lat0, r_lng0))
    x1, y1 = (float(v) for v in frame.to_local(r_lat1, r_lng1))
    ok = True
    if r_lng0 > g_lng0:
        ok &= bool(np.all(b[:, 0] - guard_m > x0))
    if r_lng1 < g_lng1:
        ok &= bool(np.all(b[:, 2] + guard_m < x1))
    if r_lat0 > g_lat0:
        ok &= bool(np.all(b[:, 1] - guard_m > y0))
    if r_lat1 < g_lat1:
        ok &= bool(np.all(b[:, 3] + guard_m < y1))
    return ok
