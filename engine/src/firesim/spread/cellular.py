"""Grid fire spread: level-set front driven by the FBP fire ellipse.

Used when a spatial fuel grid is provided (urban/WUI scenarios): the front
lives on the fuel grid, so fire wraps around non-fuel obstacles (water,
roads, buildings). The Huygens wavelet model is used for uniform fuel.

The fire front is the zero contour of a level-set function phi (burned where
phi < 0). Each fuel cell carries its FBP fire ellipse (head, flank and back
rates and spread direction from ST-X-3, including slope and per-cell canopy).
By Huygens' principle the front moves with velocity U = dH/dp, where
H(p) = c (p.h) + sqrt(a^2 (p.h)^2 + b^2 (p.k)^2) is the support function of
the elliptical wavelet (a = (ROS + BROS)/2, b = FROS, c = (ROS - BROS)/2, h the
head direction, k across it). phi is advected along U with upwind differences
(second-order ENO, first-order next to non-fuel), the approach of ELMFIRE.
Until the head has run a few cells the front is the exact FBP point-ignition
ellipse of the ignition cell, restricted to cells connected to the ignition
through fuel. On uniform fuel the burned area reproduces the FBP ellipse to
within a few percent at 25-50 m cells (``engine/tests/spread/test_cellular.py``).

Each burned cell's intensity and fire type use the front's normal speed when it
crossed the cell (head, flank or back rate as appropriate; ST-X-3 eqs 58, 69),
so flanks and backs are not labelled with head-fire intensity. Ember spotting
(opt-in) is evaluated on the newly burned cells every ``dt_minutes`` and seeds
new ignitions that then spread by the same rule.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from firesim.exposure import DEFAULT_RESIDENCE_S, Emitters, flame_length_m
from firesim.fbp.calculator import calculate_acceleration
from firesim.fbp.constants import FuelType
from firesim.fbp.crown_fire import calculate_crown_fraction_burned, classify_fire_type
from firesim.spread.huygens import (
    FireVertex,
    FuelGrid,
    SpreadConditions,
    SpreadModifierGrid,
    TerrainGrid,
    fbp_for_conditions,
)
from firesim.spread.spotting import SpotFire, check_ember_spotting

logger = logging.getLogger(__name__)

ADJACENT = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]

# Search radius for a fuel cell when the ignition lands on non-fuel (about 5 km at 50 m)
_MAX_SNAP_CELLS = 100
# Low heat of combustion over 60 s/min: I = 300 * TFC * ROS (ST-X-3 eq 69)
_INTENSITY_FACTOR = 300.0
# Head run (in cells) covered by the exact point-ignition ellipse before the level set takes over
START_CELLS = 5.0
# Courant number for the second-order scheme
CFL = 0.2
# Cells of margin around the burned area in which phi is advanced each step
BAND_CELLS = 6


@dataclass
class BurnedCell:
    """A single burned cell with location and intensity."""
    lat: float
    lng: float
    intensity: float  # kW/m, at the rate the front crossed the cell
    fuel_type: str
    timestep: int  # arrival time (minutes after ignition, rounded)
    fire_type: str = "surface"  # FireType value: surface, surface_with_torching, passive_crown, active_crown


@dataclass
class CellularFrame:
    """Output frame from the grid spread simulation."""
    time_hours: float
    burned_cells: list[BurnedCell]
    total_burned: int
    new_cells: int
    area_ha: float
    max_intensity: float
    mean_ros: float
    fuel_breakdown: dict[str, float]
    spot_fires: list[SpotFire] | None = None
    num_fronts: int = 1
    ignition_snapped_m: float = 0.0  # >0 if ignition was moved to nearest fuel cell
    perimeter: list[tuple[float, float]] | None = None  # (lat, lng) outline of the largest burned area
    emitters: Emitters | None = None  # burned cells as flame panels (last frame only)


def wavelet_normal_speed(a, b, c, nh, nk):
    """Normal speed of the elliptical wavelet front for unit normal (nh, nk) in the head frame."""
    return c * nh + np.sqrt(a * a * nh * nh + b * b * nk * nk)


def ellipse_arrival_time(u, v, a, b, c):
    """Time for the FBP point-ignition ellipse to reach (u along head, v across), in minutes.

    Solves ((u - c t)/(a t))^2 + (v/(b t))^2 = 1 for the positive t.
    """
    big_a = (u / a) ** 2 + (v / b) ** 2
    big_b = -2.0 * u * c / (a * a)
    k = 1.0 - (c / a) ** 2
    return (big_b + np.sqrt(big_b * big_b + 4.0 * k * big_a)) / (2.0 * k)


def run_cellular_simulation(
    config: dict,
    fuel_grid: FuelGrid,
    conditions: SpreadConditions,
    default_fuel: FuelType = FuelType.C2,
    spread_modifier_grid: SpreadModifierGrid | None = None,
    terrain_grid: TerrainGrid | None = None,
    dt_minutes: float = 1.0,
    snapshot_interval_minutes: float = 30.0,
    enable_spotting: bool = False,
    spotting_intensity: float = 1.0,
    acceleration: bool = True,
    initial_perimeter: list[tuple[float, float]] | None = None,
    initial_burned: list[tuple[float, float]] | None = None,
    compute_perimeter: bool = True,
    weather_schedule: list[tuple[float, SpreadConditions]] | None = None,
) -> list[CellularFrame]:
    """Run grid fire spread with a level-set front.

    Args:
        config: Dict with ignition_lat, ignition_lng, duration_hours.
        fuel_grid: Spatial fuel grid (required); optional per-cell cbh/cfl layers.
        conditions: Weather/FWI conditions.
        default_fuel: Passed to the spotting model for off-grid lookups.
        spread_modifier_grid: Optional WUI modifiers (ROS and intensity multipliers).
        terrain_grid: Optional slope/aspect grid. Slope enters FBP through the
            net effective wind (ST-X-3 eqs 39-50), which changes the head, flank
            and back rates and the spread direction per cell.
        dt_minutes: Interval at which ember spotting is evaluated. The front's own
            time step is set by the Courant condition.
        snapshot_interval_minutes: How often to yield frames.
        enable_spotting: When True, apply Albini (1979) ember spotting from newly
            burned cells and spread from the landing cells.
        spotting_intensity: Multiplier on spot fire probability (1.0 = baseline;
            0 = disabled). Has no effect when enable_spotting is False.
        acceleration: Apply FBP point-ignition acceleration (ST-X-3 eqs 70-72, 81):
            rates build up as 1 - exp(-alpha t) and the ellipse elongates as LB(t),
            with alpha per cell from its crown fraction burned.
        initial_perimeter: (lat, lng) polygon of an existing fire (e.g. an RPAS-observed
            perimeter). Fuel cells whose centres fall inside start burned.
        initial_burned: (lat, lng) points of already-burned cells (e.g. the previous day of a
            multi-day run). With either, the fire is treated as established (no
            acceleration) and spreads from that area instead of the ignition point.
        compute_perimeter: Build each frame's outline polygon (skip for ensembles).
        weather_schedule: (start minute, conditions) periods, e.g. from an hourly weather
            stream; FBP rates are recomputed at each change. Default: ``conditions`` throughout.

    Returns:
        List of CellularFrame snapshots at t = 0, every snapshot interval, and the end.
    """
    rows, cols = fuel_grid.rows, fuel_grid.cols
    lat_max, lng_min = fuel_grid.lat_max, fuel_grid.lng_min
    cell_lat = (fuel_grid.lat_max - fuel_grid.lat_min) / rows
    cell_lng = (fuel_grid.lng_max - fuel_grid.lng_min) / cols
    mid_lat = (fuel_grid.lat_max + fuel_grid.lat_min) / 2.0
    dy = cell_lat * 111320.0  # metres per row (north-south)
    dx = cell_lng * 111320.0 * math.cos(math.radians(mid_lat))  # metres per column
    h_min = min(dx, dy)
    cell_area_m2 = dx * dy
    duration = config["duration_hours"] * 60.0

    def center(r: int, c: int) -> tuple[float, float]:
        return lat_max - (r + 0.5) * cell_lat, lng_min + (c + 0.5) * cell_lng

    schedule = sorted(weather_schedule, key=lambda e: e[0]) if weather_schedule else [(0.0, conditions)]
    conditions = schedule[0][1]
    cell_keys = _cell_keys(fuel_grid, spread_modifier_grid, terrain_grid, center)
    params = _CellParams.evaluate(cell_keys, conditions)
    period = 0
    fuel = params.fuel

    arrival = np.full((rows, cols), np.inf)
    cross_ros = np.zeros((rows, cols))
    spot_events: list[tuple[float, SpotFire]] = []

    start = _initial_region(fuel_grid, fuel, initial_perimeter, initial_burned, cell_lat, cell_lng)
    snapped_m = 0.0
    phi = None
    if start is not None:
        acceleration = False  # an existing fire is already at equilibrium spread
        arrival[start] = 0.0
        phi = _signed_distance(start, dx, dy)
        t = 0.0
        ign_row = ign_col = 0
    else:
        ign_row, ign_col, snapped_m = _snap_ignition(
            fuel_grid, config["ignition_lat"], config["ignition_lng"], cell_lat, cell_lng, dy
        )
        if ign_row is not None and params.head[ign_row, ign_col] > 1e-6:
            phi, t = _initial_front(params, ign_row, ign_col, dx, dy, duration, arrival, cross_ros,
                                    acceleration)

    if phi is not None:
        near_nonfuel = ndimage.binary_dilation(~fuel, iterations=2)
        in_band = np.zeros((rows, cols), dtype=bool)
        in_band[_window(arrival < np.inf, BAND_CELLS)] = True
        slice_len = dt_minutes if enable_spotting and spotting_intensity > 0.0 else duration
        next_slice = min(t + slice_len, duration)
        slice_start = t

        def next_change() -> float:
            return schedule[period + 1][0] if period + 1 < len(schedule) else math.inf

        while t < duration - 1e-9:
            if t >= next_change() - 1e-9:  # weather period changes: new FBP rates everywhere
                while t >= next_change() - 1e-9:
                    period += 1
                conditions = schedule[period][1]
                params = _CellParams.evaluate(cell_keys, conditions)
            win = _window(arrival < np.inf, BAND_CELLS)
            entering = ~in_band[win]
            if entering.any():
                # Cells joining the computational window still hold their starting phi,
                # which has not evolved with the front; reset them to their distance from
                # the current burned area so the front does not lag behind.
                dist = ndimage.distance_transform_edt(phi[win] >= 0, sampling=(dy, dx))
                sub = phi[win]
                sub[entering] = np.maximum(dist[entering] - 0.5 * h_min, 0.5 * h_min)
                phi[win] = sub
                in_band[win] = True
            speed = (params.head[win] + params.b[win]).max()
            if speed <= 1e-9:
                break
            step = min(CFL * h_min / speed, next_slice - t, next_change() - t)
            _advance(phi, win, params, near_nonfuel[win], dx, dy, step, t, arrival, cross_ros,
                     acceleration)
            t += step
            if t >= next_slice - 1e-9:
                if enable_spotting and spotting_intensity > 0.0:
                    newly = np.argwhere((arrival > slice_start) & (arrival <= t))
                    for spot in _spot_from_front(
                        newly, center, conditions, fuel_grid, spread_modifier_grid,
                        default_fuel, t - slice_start, spotting_intensity,
                    ):
                        r = int((lat_max - spot.lat) / cell_lat)
                        c = int((spot.lng - lng_min) / cell_lng)
                        if 0 <= r < rows and 0 <= c < cols and fuel[r, c] and arrival[r, c] == np.inf:
                            phi[r, c] = -0.5 * h_min
                            arrival[r, c] = t
                            cross_ros[r, c] = params.head[r, c]
                            spot_events.append((t, spot))
                slice_start = t
                next_slice = min(t + slice_len, duration)

    frames = _frames(
        arrival, cross_ros, params, fuel_grid, center, duration, snapshot_interval_minutes,
        cell_area_m2, spot_events, snapped_m, cell_lat, cell_lng, compute_perimeter,
    )
    if compute_perimeter and frames:
        frames[-1].emitters = flame_emitters(arrival, cross_ros, params, duration, dx, dy)
        frames[-1].emitters.lat0, frames[-1].emitters.lng0 = lat_max, lng_min
        frames[-1].emitters.m_per_deg_lat = dy / cell_lat
        frames[-1].emitters.m_per_deg_lng = dx / cell_lng
    return frames


def flame_emitters(arrival, cross_ros, p, duration, dx, dy,
                   residence_s: float = DEFAULT_RESIDENCE_S) -> Emitters:
    """Burned cells as vertical flame panels for the radiant exposure model.

    Each panel faces the local spread direction (the arrival-time gradient, from earlier-burned
    neighbours; the cell's head direction where it has none). It is present while the front
    crosses the cell (cell size / normal speed), so one panel represents the moving flame face
    and panels are not stacked behind it. Cells where the front stops (no later-burned
    neighbour: a barrier, the fuel edge or the end of the run) keep flaming for the flaming
    residence time. Flame height from the cell intensity: Byram, or Thomas when CFB >= 0.1.
    Positions in metres, x east and y north of the grid's north-west corner.
    """
    rows, cols = arrival.shape
    t = np.where(arrival <= duration, arrival, np.inf)
    pad = np.pad(t, 1, constant_values=np.inf)
    gx = np.zeros((rows, cols))
    gy = np.zeros((rows, cols))
    later = np.zeros((rows, cols), dtype=bool)
    for dr, dc in ADJACENT:
        nb = pad[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
        dist = math.hypot(dc * dx, dr * dy)
        earlier = np.isfinite(nb) & np.isfinite(t) & (nb < t)
        w = np.where(earlier, t - np.where(earlier, nb, 0.0), 0.0) / dist
        gx += w * (-dc * dx) / dist  # unit vector from the neighbour to this cell
        gy += w * (dr * dy) / dist
        later |= np.isfinite(nb) & (nb > t) & np.isfinite(t)
    burned = np.isfinite(t)
    r, c = np.nonzero(burned)
    nx, ny = gx[r, c], gy[r, c]
    norm = np.hypot(nx, ny)
    none = norm < 1e-12
    nx = np.where(none, p.hx[r, c], nx / np.where(none, 1.0, norm))
    ny = np.where(none, p.hy[r, c], ny / np.where(none, 1.0, norm))
    norm = np.hypot(nx, ny)
    nx, ny = nx / np.where(norm > 0, norm, 1.0), ny / np.where(norm > 0, norm, 1.0)

    ros = cross_ros[r, c]
    rso = p.rso[r, c]
    cfb = np.where((p.cfl[r, c] > 0.0) & (ros > rso), 1.0 - np.exp(-0.23 * (ros - rso)), 0.0)
    intensity = _INTENSITY_FACTOR * (p.sfc[r, c] + p.cfl[r, c] * cfb) * ros * p.imult[r, c]
    size = math.sqrt(dx * dy)
    crossing = size / np.maximum(ros, 1e-3)
    end = t[r, c] + crossing + np.where(later[r, c], 0.0, residence_s / 60.0)
    return Emitters(
        x=(c + 0.5) * dx, y=-(r + 0.5) * dy, start_min=t[r, c], end_min=end,
        flame_m=flame_length_m(intensity, cfb >= 0.1), normal_x=nx, normal_y=ny, cell_size=size,
    )


def _window(burned: np.ndarray, margin: int) -> tuple[slice, slice]:
    """Bounding box of the burned cells plus ``margin`` cells on each side."""
    rows, cols = burned.shape
    r_idx = np.flatnonzero(burned.any(axis=1))
    c_idx = np.flatnonzero(burned.any(axis=0))
    return (slice(max(r_idx[0] - margin, 0), min(r_idx[-1] + margin + 1, rows)),
            slice(max(c_idx[0] - margin, 0), min(c_idx[-1] + margin + 1, cols)))


def _initial_region(fuel_grid, fuel, perimeter, burned_points, cell_lat, cell_lng):
    """Boolean mask of fuel cells burned at t = 0, or None for a point ignition."""
    if not perimeter and not burned_points:
        return None
    rows, cols = fuel.shape
    mask = np.zeros((rows, cols), dtype=bool)
    if perimeter and len(perimeter) >= 3:
        import shapely

        poly = shapely.Polygon([(lng, lat) for lat, lng in perimeter])
        if not poly.is_valid:
            poly = poly.buffer(0)
        rr, cc = np.mgrid[0:rows, 0:cols]
        lats = fuel_grid.lat_max - (rr + 0.5) * cell_lat
        lngs = fuel_grid.lng_min + (cc + 0.5) * cell_lng
        mask |= shapely.contains_xy(poly, lngs, lats)
    for lat, lng in burned_points or []:
        r = int((fuel_grid.lat_max - lat) / cell_lat)
        c = int((lng - fuel_grid.lng_min) / cell_lng)
        if 0 <= r < rows and 0 <= c < cols:
            mask[r, c] = True
    mask &= fuel
    return mask if mask.any() else None


def _signed_distance(region, dx, dy):
    """Signed distance (m) to the edge of ``region``: negative inside, positive outside."""
    outside = ndimage.distance_transform_edt(~region, sampling=(dy, dx))
    inside = ndimage.distance_transform_edt(region, sampling=(dy, dx))
    return np.where(region, -inside, outside) + np.where(region, 0.5, -0.5) * min(dx, dy)


def burned_outline(mask: np.ndarray, lat_max: float, lng_min: float, cell_lat: float,
                   cell_lng: float) -> list[tuple[float, float]]:
    """(lat, lng) exterior ring of the largest connected burned area in ``mask``."""
    if not mask.any():
        return []
    from rasterio.features import shapes
    from rasterio.transform import Affine

    transform = Affine(cell_lng, 0.0, lng_min, 0.0, -cell_lat, lat_max)
    best, best_area = None, -1.0
    for geom, value in shapes(mask.astype(np.uint8), mask=mask, transform=transform):
        ring = geom["coordinates"][0]
        xs, ys = np.array([p[0] for p in ring]), np.array([p[1] for p in ring])
        area = 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))
        if area > best_area:
            best, best_area = ring, area
    return [(lat, lng) for lng, lat in best]


@dataclass
class _CellParams:
    """Per-cell FBP ellipse parameters and the quantities needed for intensity."""
    fuel: np.ndarray  # bool
    head: np.ndarray
    a: np.ndarray
    b: np.ndarray
    c: np.ndarray
    hx: np.ndarray  # head direction, east component
    hy: np.ndarray  # head direction, north component
    sfc: np.ndarray
    cfl: np.ndarray
    rso: np.ndarray
    imult: np.ndarray
    alpha: np.ndarray  # FBP acceleration parameter (per minute)
    lb: np.ndarray  # equilibrium length-to-breadth ratio

    @classmethod
    def build(cls, fuel_grid, conditions, spread_modifier_grid, terrain_grid, center) -> "_CellParams":
        return cls.evaluate(_cell_keys(fuel_grid, spread_modifier_grid, terrain_grid, center), conditions)

    @classmethod
    def evaluate(cls, cell_keys, conditions) -> "_CellParams":
        """FBP for each distinct cell type under ``conditions``, spread back onto the grid."""
        fuel, index, keys = cell_keys
        table = np.zeros((max(len(keys), 1), 10))
        for k, (ft, slope, aspect, cbh, cfl, rm, im) in enumerate(keys):
            f = fbp_for_conditions(conditions, ft, float(slope), float(aspect), cbh, cfl)
            table[k] = (f.ros_final * rm, f.back_ros * rm, f.flank_ros * rm, f.raz, f.sfc, f.cfl,
                        f.rso if math.isfinite(f.rso) else 1e12, im,
                        calculate_acceleration(ft, f.cfb), f.lb)
        vals = np.where(fuel[..., None], table[np.maximum(index, 0)], 0.0)
        head, back, flank, raz_deg, sfc, cfl, rso, imult, alpha, lb = np.moveaxis(vals, -1, 0)
        raz = np.radians(raz_deg)
        return cls(
            fuel=fuel, head=head, a=(head + back) / 2.0, b=flank, c=(head - back) / 2.0,
            hx=np.sin(raz), hy=np.cos(raz), sfc=sfc, cfl=cfl, rso=rso, imult=imult,
            alpha=np.where(fuel, alpha, 0.115), lb=np.maximum(lb, 1.0),
        )


def _cell_keys(fuel_grid, spread_modifier_grid, terrain_grid, center):
    """(fuel mask, per-cell index into the distinct cell types, the distinct types).

    A cell type is (fuel, slope % rounded, upslope azimuth rounded, CBH, CFL, ROS and
    intensity multipliers): everything FBP needs apart from the weather, so FBP runs once
    per type and weather period instead of once per cell.
    """
    rows, cols = fuel_grid.rows, fuel_grid.cols
    fuel = np.zeros((rows, cols), dtype=bool)
    index = np.full((rows, cols), -1, dtype=np.int32)
    lookup: dict[tuple, int] = {}
    has_canopy = fuel_grid.cbh is not None or fuel_grid.cfl is not None
    for r in range(rows):
        for c in range(cols):
            ft = fuel_grid.fuel_types[r][c]
            if ft is None:
                continue
            fuel[r, c] = True
            slope, aspect, cbh, cfl, rm, im = 0.0, 0.0, None, None, 1.0, 1.0
            if terrain_grid is not None or has_canopy or spread_modifier_grid is not None:
                lat, lng = center(r, c)
                if terrain_grid is not None:
                    slope, aspect = terrain_grid.get_slope_aspect(lat, lng)
                    if slope < 1.0:
                        slope, aspect = 0.0, 0.0
                if has_canopy:
                    cbh, cfl = fuel_grid.get_canopy_at(lat, lng)
                if spread_modifier_grid is not None:
                    rm, im, _ = spread_modifier_grid.get_modifiers_at(lat, lng)
            key = (ft, round(slope), round(aspect) % 360, cbh, cfl, rm, im)
            index[r, c] = lookup.setdefault(key, len(lookup))
    return fuel, index, list(lookup)


def _initial_front(params, r0, c0, dx, dy, duration, arrival, cross_ros, acceleration=True):
    """Exact FBP ellipse of the ignition cell until its head has run START_CELLS cells.

    With acceleration the ellipse at t0 has the head and back distances of the
    accelerating fire (ST-X-3 eq 73) and breadth from LB(t0) (eq 81); inside it,
    arrival times use the mean speed over [0, t0].
    """
    rows, cols = params.fuel.shape
    a, b, c = params.a[r0, c0], params.b[r0, c0], params.c[r0, c0]
    hx, hy, head = params.hx[r0, c0], params.hy[r0, c0], params.head[r0, c0]
    run = START_CELLS * min(dx, dy)
    if acceleration:
        alpha, lb = params.alpha[r0, c0], params.lb[r0, c0]

        def head_dist(t):
            return head * (t + math.exp(-alpha * t) / alpha - 1.0 / alpha)

        lo, hi = 0.0, run / head + 5.0 / alpha
        for _ in range(60):  # bisection for the time the head has run `run` metres
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if head_dist(mid) < run else (lo, mid)
        t0 = min(hi, 0.5 * duration)
        g0 = head_dist(t0) / (head * t0) if t0 > 0 else 0.0
        lb_t0 = (lb - 1.0) * (1.0 - math.exp(-alpha * t0)) + 1.0
        a, c = a * g0, c * g0
        b = a / lb_t0
        head = head * g0
    else:
        t0 = min(run / head, 0.5 * duration)
    rr, cc = np.mgrid[0:rows, 0:cols]
    x = (cc - c0) * dx
    y = (r0 - rr) * dy
    u = x * hx + y * hy
    v = -x * hy + y * hx
    tg = ellipse_arrival_time(u, v, a, b, c)
    inside = (tg <= t0) & params.fuel
    labels, _ = ndimage.label(inside)
    burned = labels == labels[r0, c0]
    phi = head * (tg - t0)
    # cells inside the ellipse but cut off from the ignition by non-fuel start unburned
    phi[inside & ~burned] = 0.5 * min(dx, dy)
    phi[~params.fuel] = np.maximum(phi[~params.fuel], 0.5 * min(dx, dy))
    arrival[burned] = tg[burned]
    dist = np.hypot(u, v)
    with np.errstate(invalid="ignore", divide="ignore"):
        nh = np.where(dist > 0, u / dist, 1.0)
        nk = np.where(dist > 0, v / dist, 0.0)
    cross_ros[burned] = wavelet_normal_speed(a, b, c, nh, nk)[burned]
    cross_ros[r0, c0] = head
    return phi, t0


def _advance(phi, win, p, near_nonfuel, dx, dy, step, t, arrival, cross_ros, acceleration=True):
    """Advance phi by one time step inside the window ``win`` (in place)."""
    sub = phi[win]
    pad = np.pad(sub, 2, mode="edge")
    C = pad[2:-2, 2:-2]
    W1, W2, E1, E2 = pad[2:-2, 1:-3], pad[2:-2, :-4], pad[2:-2, 3:-1], pad[2:-2, 4:]
    S1, S2, N1, N2 = pad[3:-1, 2:-2], pad[4:, 2:-2], pad[1:-3, 2:-2], pad[:-4, 2:-2]  # row+1 is south
    # Barrier boundary: a non-fuel neighbour takes the cell's own value (zero gradient across
    # it), so the front slides along walls instead of being turned by their frozen phi.
    fpad = np.pad(p.fuel[win], 1, mode="edge")
    W1 = np.where(fpad[1:-1, :-2], W1, C)
    E1 = np.where(fpad[1:-1, 2:], E1, C)
    S1 = np.where(fpad[2:, 1:-1], S1, C)
    N1 = np.where(fpad[:-2, 1:-1], N1, C)
    dxm, dxp = (C - W1) / dx, (E1 - C) / dx
    dym, dyp = (C - S1) / dy, (N1 - C) / dy

    # ENO2 correction (smaller second difference), first order next to non-fuel
    def mm(u, v):
        return np.where(np.abs(u) < np.abs(v), u, v)

    eno = ~near_nonfuel
    dxm = dxm + eno * 0.5 * mm(C - 2 * W1 + W2, E1 - 2 * C + W1) / dx
    dxp = dxp - eno * 0.5 * mm(E1 - 2 * C + W1, E2 - 2 * E1 + C) / dx
    dym = dym + eno * 0.5 * mm(C - 2 * S1 + S2, N1 - 2 * C + S1) / dy
    dyp = dyp - eno * 0.5 * mm(N1 - 2 * C + S1, N2 - 2 * N1 + C) / dy

    a, b, c = p.a[win], p.b[win], p.c[win]
    hx, hy, fuel = p.hx[win], p.hy[win], p.fuel[win]
    if acceleration:  # mean FBP acceleration over the step and LB(t) at its midpoint
        alpha = p.alpha[win]
        g = 1.0 - (np.exp(-alpha * t) - np.exp(-alpha * (t + step))) / (alpha * step)
        lb_t = (p.lb[win] - 1.0) * (1.0 - np.exp(-alpha * (t + 0.5 * step))) + 1.0
        a, c = a * g, c * g
        b = a / lb_t
    gx, gy = 0.5 * (dxm + dxp), 0.5 * (dym + dyp)
    g = np.hypot(gx, gy) + 1e-12
    nh = (gx * hx + gy * hy) / g
    nk = (-gx * hy + gy * hx) / g
    root = np.sqrt(a * a * nh * nh + b * b * nk * nk) + 1e-12
    uh = c + a * a * nh / root
    uk = b * b * nk / root
    ux = uh * hx - uk * hy
    uy = uh * hy + uk * hx
    rate = np.where(ux > 0, ux * dxm, ux * dxp) + np.where(uy > 0, uy * dym, uy * dyp)
    new = np.where(fuel, C - step * rate, np.maximum(C, 0.5 * min(dx, dy)))

    crossed = fuel & (C >= 0) & (new < 0)
    if crossed.any():
        frac = C[crossed] / np.maximum(C[crossed] - new[crossed], 1e-12)
        arr_win = arrival[win]
        ros_win = cross_ros[win]
        arr_win[crossed] = t + frac * step
        ros_win[crossed] = (c * nh + root)[crossed]
    phi[win] = new


def _frames(arrival, cross_ros, p, fuel_grid, center, duration, snapshot_interval, cell_area_m2,
            spot_events, snapped_m, cell_lat, cell_lng, compute_perimeter=True) -> list[CellularFrame]:
    """Build cumulative frames from per-cell arrival times."""
    burned_idx = np.argwhere(arrival <= duration)
    order = np.argsort(arrival[burned_idx[:, 0], burned_idx[:, 1]], kind="stable")
    burned_idx = burned_idx[order]

    cells: list[BurnedCell] = []
    for r, c in burned_idx:
        r, c = int(r), int(c)
        ros = float(cross_ros[r, c])
        cfb = calculate_crown_fraction_burned(ros, p.rso[r, c]) if p.cfl[r, c] > 0.0 else 0.0
        intensity = _INTENSITY_FACTOR * (p.sfc[r, c] + p.cfl[r, c] * cfb) * ros * p.imult[r, c]
        lat, lng = center(r, c)
        cells.append(BurnedCell(
            lat=lat, lng=lng, intensity=float(intensity), fuel_type=fuel_grid.fuel_types[r][c].value,
            timestep=int(round(arrival[r, c])), fire_type=classify_fire_type(cfb).value,
        ))
    times = arrival[burned_idx[:, 0], burned_idx[:, 1]] if len(burned_idx) else np.array([])
    heads = p.head[burned_idx[:, 0], burned_idx[:, 1]] if len(burned_idx) else np.array([])

    frames: list[CellularFrame] = []
    snapshot_times = list(np.arange(0.0, duration, snapshot_interval)) + [duration]
    prev_t, prev_n, mean_ros = -1.0, 0, 0.0
    for t_snap in snapshot_times:
        n = int(np.searchsorted(times, t_snap, side="right"))
        if n > prev_n:  # mean head ROS of the cells reached since the last frame
            mean_ros = float(np.mean(heads[prev_n:n]))
        spots = [s for ts, s in spot_events if prev_t < ts <= t_snap]
        frame = _make_frame(
            float(t_snap), cells[:n], n - prev_n, cell_area_m2, mean_ros=mean_ros,
            spot_fires=spots or None, ignition_snapped_m=snapped_m if not frames else 0.0,
        )
        if compute_perimeter:
            frame.perimeter = burned_outline(
                arrival <= t_snap, fuel_grid.lat_max, fuel_grid.lng_min, cell_lat, cell_lng
            )
        frames.append(frame)
        prev_t, prev_n = t_snap, n

    logger.info(
        "Grid spread complete: %.1fh, %d cells burned (%.1f ha), %d spot fires",
        duration / 60.0, len(cells), len(cells) * cell_area_m2 / 10000.0, len(spot_events),
    )
    return frames


def _snap_ignition(
    fuel_grid: FuelGrid, lat: float, lng: float, cell_lat: float, cell_lng: float, dy: float
) -> tuple[int | None, int | None, float]:
    """Ignition cell, moved to the nearest fuel cell (BFS) if it falls on non-fuel."""
    rows, cols = fuel_grid.rows, fuel_grid.cols
    r0 = max(0, min(rows - 1, int((fuel_grid.lat_max - lat) / cell_lat)))
    c0 = max(0, min(cols - 1, int((lng - fuel_grid.lng_min) / cell_lng)))
    if fuel_grid.fuel_types[r0][c0] is not None:
        return r0, c0, 0.0
    visited = {(r0, c0)}
    q: deque[tuple[int, int, int]] = deque([(r0, c0, 0)])
    while q:
        r, c, dist = q.popleft()
        if dist >= _MAX_SNAP_CELLS:
            break
        for dr, dc in ADJACENT:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols) or (nr, nc) in visited:
                continue
            visited.add((nr, nc))
            if fuel_grid.fuel_types[nr][nc] is not None:
                snapped = (dist + 1) * dy
                logger.warning(
                    "Ignition in non-fuel zone — snapped %.0fm to nearest fuel cell (%d,%d) fuel=%s",
                    snapped, nr, nc, fuel_grid.fuel_types[nr][nc].value,
                )
                return nr, nc, snapped
            q.append((nr, nc, dist + 1))
    logger.warning("No fuel within %.0fm of ignition point — simulation will be empty", _MAX_SNAP_CELLS * dy)
    return None, None, 0.0


def _spot_from_front(
    front: np.ndarray,
    center,
    conditions: SpreadConditions,
    fuel_grid: FuelGrid,
    spread_modifier_grid: SpreadModifierGrid | None,
    default_fuel: FuelType,
    dt_minutes: float,
    spotting_intensity: float,
) -> list[SpotFire]:
    """Ember spotting (Albini 1979) from cells the front reached in the last interval."""
    if len(front) == 0:
        return []
    # Cap the sample for performance — every 3rd front cell
    vertices = [FireVertex(*center(int(r), int(c))) for r, c in front[::3]]
    return check_ember_spotting(
        front=vertices,
        conditions=conditions,
        fuel_grid=fuel_grid,
        spread_modifier_grid=spread_modifier_grid,
        default_fuel=default_fuel,
        dt_minutes=dt_minutes,
        check_interval=1,
        intensity_multiplier=spotting_intensity,
    )


def _make_frame(
    elapsed_minutes: float,
    burned_cells: list[BurnedCell],
    new_cells: int,
    cell_area_m2: float,
    mean_ros: float = 0.0,
    spot_fires: list[SpotFire] | None = None,
    ignition_snapped_m: float = 0.0,
) -> CellularFrame:
    """Create a frame snapshot with all cells burned by ``elapsed_minutes``."""
    total = len(burned_cells)
    fuel_counts: dict[str, int] = {}
    for cell in burned_cells:
        fuel_counts[cell.fuel_type] = fuel_counts.get(cell.fuel_type, 0) + 1
    fuel_breakdown = {k: v / total for k, v in fuel_counts.items()} if total else {}
    return CellularFrame(
        time_hours=elapsed_minutes / 60.0,
        burned_cells=list(burned_cells),
        total_burned=total,
        new_cells=new_cells,
        area_ha=total * cell_area_m2 / 10000.0,
        max_intensity=max((c.intensity for c in burned_cells), default=0.0),
        mean_ros=mean_ros,
        fuel_breakdown=fuel_breakdown,
        spot_fires=spot_fires,
        ignition_snapped_m=ignition_snapped_m,
    )
