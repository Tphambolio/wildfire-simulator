"""Huygens wavelet fire spread algorithm.

Implements fire spread using the Huygens wavelet principle, the same
approach used by Prometheus (Canadian standard fire growth model).

The fire front is represented as an ordered list of vertices. At each
timestep, every vertex is expanded as an elliptical wavelet whose shape
is determined by local FBP output (ROS, wind, slope). The envelope of
all wavelets forms the new fire front.

This eliminates the grid artifacts inherent in cellular automaton
approaches (V2) and produces physically accurate elliptical spread.

References:
    Tymstra, C. et al. (2010). Development and structure of Prometheus:
    the Canadian Wildland Fire Growth Simulation Model. Information
    Report NOR-X-417.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from firesim.fbp.calculator import (
    calculate_acceleration,
    calculate_fbp,
    calculate_lb_at_time,
    mean_acceleration_factor,
)
from firesim.fbp.constants import FuelType
from firesim.types import FBPResult


@dataclass
class FireVertex:
    """A single point on the fire front."""

    lat: float
    lng: float


@dataclass
class SpreadConditions:
    """Fire weather and fuel conditions for spread calculation."""

    wind_speed: float  # km/h
    wind_direction: float  # degrees, meteorological FROM
    ffmc: float
    dmc: float
    dc: float
    pc: float = 50.0  # percent conifer for M1/M2
    grass_cure: float = 60.0  # percent curing for O1a/O1b
    pdf: float = 35.0  # percent dead balsam fir for M3/M4
    gfl: float = 0.35  # grass fuel load (kg/m2) for O1a/O1b
    fmc: float = 100.0  # foliar moisture content (%)
    # Rate-of-spread error multiplier (ensemble perturbation; 1.0 = FBP as is). Grid model only.
    ros_multiplier: float = 1.0


def fbp_for_conditions(
    conditions: SpreadConditions,
    fuel_type: FuelType,
    slope_pct: float = 0.0,
    aspect_deg: float = 0.0,
    cbh: float | None = None,
    cfl: float | None = None,
) -> FBPResult:
    """Full FBP output (head, flank, back, direction) for local fuel and terrain.

    Slope enters through the net effective wind (ST-X-3 eqs 39-50); aspect is
    the upslope azimuth. ``cbh`` / ``cfl`` override the fuel type's default
    crown base height and crown fuel load (e.g. per-cell values from LiDAR).
    """
    return calculate_fbp(
        fuel_type=fuel_type,
        wind_speed=conditions.wind_speed,
        ffmc=conditions.ffmc,
        dmc=conditions.dmc,
        dc=conditions.dc,
        slope=slope_pct if slope_pct >= 1.0 else 0.0,
        pc=conditions.pc,
        grass_cure=conditions.grass_cure,
        fmc=conditions.fmc,
        wind_direction=conditions.wind_direction,
        slope_aspect=aspect_deg,
        pdf=conditions.pdf,
        gfl=conditions.gfl,
        cbh=cbh,
        cfl=cfl,
    )


@dataclass
class FuelGrid:
    """Spatial grid of fuel types.

    A simple grid representation where fuel types are stored as a 2D array.
    The grid covers a rectangular lat/lng extent.
    """

    fuel_types: list[list[FuelType | None]]  # [row][col], None = non-fuel
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    rows: int
    cols: int
    # Optional per-cell canopy layers (e.g. from LiDAR). None (whole layer or a
    # cell) means "use the fuel type's FBP default".
    cbh: list[list[float | None]] | None = None  # crown base height (m)
    cfl: list[list[float | None]] | None = None  # crown fuel load (kg/m2)

    def get_canopy_at(self, lat: float, lng: float) -> tuple[float | None, float | None]:
        """Per-cell crown base height and crown fuel load, or (None, None) for defaults."""
        if (self.cbh is None and self.cfl is None) or not (
            self.lat_min <= lat <= self.lat_max and self.lng_min <= lng <= self.lng_max
        ):
            return None, None
        row, col = self._cell_index(lat, lng)
        return (
            self.cbh[row][col] if self.cbh is not None else None,
            self.cfl[row][col] if self.cfl is not None else None,
        )

    def _cell_index(self, lat: float, lng: float) -> tuple[int, int]:
        row = int((self.lat_max - lat) / (self.lat_max - self.lat_min) * self.rows)
        col = int((lng - self.lng_min) / (self.lng_max - self.lng_min) * self.cols)
        return max(0, min(self.rows - 1, row)), max(0, min(self.cols - 1, col))

    def get_fuel_at(self, lat: float, lng: float) -> FuelType | None:
        """Look up fuel type at a geographic coordinate.

        Returns None if outside grid or non-fuel.
        """
        if lat < self.lat_min or lat > self.lat_max:
            return None
        if lng < self.lng_min or lng > self.lng_max:
            return None
        row, col = self._cell_index(lat, lng)
        return self.fuel_types[row][col]


@dataclass
class TerrainGrid:
    """Spatial grid of slope and aspect.

    Stores pre-computed slope (%) and aspect (degrees) for each cell.
    """

    # [row][col]; rows may be lists or array('d') (dem_loader: compact rows)
    slope: list[list[float]]  # percent slope [row][col]
    aspect: list[list[float]]  # degrees (0=N, 90=E) [row][col]
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    rows: int
    cols: int

    def get_slope_aspect(self, lat: float, lng: float) -> tuple[float, float]:
        """Look up slope and aspect at a coordinate.

        Returns (slope_percent, aspect_degrees). Defaults to (0, 0) if outside.
        """
        if lat < self.lat_min or lat > self.lat_max:
            return 0.0, 0.0
        if lng < self.lng_min or lng > self.lng_max:
            return 0.0, 0.0

        row = int((self.lat_max - lat) / (self.lat_max - self.lat_min) * self.rows)
        col = int((lng - self.lng_min) / (self.lng_max - self.lng_min) * self.cols)

        row = max(0, min(self.rows - 1, row))
        col = max(0, min(self.cols - 1, col))

        return self.slope[row][col], self.aspect[row][col]


@dataclass
class SpreadModifierGrid:
    """Per-cell spread rate and intensity multipliers (e.g. WUI zones).

    ros_multiplier < 1.0 slows fire (defended space).
    intensity_multiplier > 1.0 boosts intensity (anthropogenic fuels).
    ember_multiplier > 1.0 increases ember spotting probability.
    """

    ros_multiplier: list[list[float]]        # [row][col], 1.0 = no change
    intensity_multiplier: list[list[float]]   # [row][col], 1.0 = no change
    ember_multiplier: list[list[float]]       # [row][col], 1.0 = no change
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    rows: int
    cols: int

    def get_modifiers_at(
        self, lat: float, lng: float
    ) -> tuple[float, float, float]:
        """Look up modifiers at a coordinate.

        Returns (ros_mult, intensity_mult, ember_mult).
        Defaults to (1.0, 1.0, 1.0) if outside grid.
        """
        if lat < self.lat_min or lat > self.lat_max:
            return 1.0, 1.0, 1.0
        if lng < self.lng_min or lng > self.lng_max:
            return 1.0, 1.0, 1.0

        row = int((self.lat_max - lat) / (self.lat_max - self.lat_min) * self.rows)
        col = int((lng - self.lng_min) / (self.lng_max - self.lng_min) * self.cols)

        row = max(0, min(self.rows - 1, row))
        col = max(0, min(self.cols - 1, col))

        return (
            self.ros_multiplier[row][col],
            self.intensity_multiplier[row][col],
            self.ember_multiplier[row][col],
        )


_MISSING = object()

# Meters per degree of latitude (approximate)
_M_PER_DEG_LAT = 111320.0


def _m_per_deg_lng(lat: float) -> float:
    """Meters per degree of longitude at given latitude."""
    return 111320.0 * math.cos(math.radians(lat))


def expand_vertex(
    vertex: FireVertex,
    conditions: SpreadConditions,
    fuel_type: FuelType,
    slope_pct: float,
    aspect_deg: float,
    dt_minutes: float,
    num_rays: int = 36,
    ros_modifier: float = 1.0,
    cbh: float | None = None,
    cfl: float | None = None,
    accel_window: tuple[float, float] | None = None,
) -> list[FireVertex]:
    """Expand a single fire front vertex as a Huygens wavelet.

    The wavelet is the FBP fire ellipse for the local fuel, weather and
    slope: head rate ROS, back rate BROS (from the back ISI) and flank rate
    FROS = (ROS + BROS) / (2 LB), oriented along the net effective wind
    direction RAZ (ST-X-3 eqs 39-89).

    Args:
        vertex: Fire front vertex to expand
        conditions: Weather and FWI conditions
        fuel_type: Local fuel type at this vertex
        slope_pct: Local slope (%)
        aspect_deg: Local upslope azimuth (degrees, 0=N)
        dt_minutes: Timestep duration (minutes)
        num_rays: Number of radial directions to sample
        ros_modifier: Multiplier on all rates (e.g. WUI zones)
        cbh, cfl: Per-cell crown base height / crown fuel load overrides
        accel_window: (t1, t2) minutes since this front's point ignition for the
            step; rates are scaled by the mean FBP acceleration over the step and
            the ellipse uses LB(t) (ST-X-3 eqs 70-72, 81). None = equilibrium fire.

    Returns:
        List of new vertices forming the wavelet ellipse
    """
    offsets = _wavelet_offsets(
        conditions, fuel_type, slope_pct, aspect_deg, dt_minutes, num_rays, ros_modifier,
        cbh, cfl, accel_window,
    )
    return _place_wavelet(vertex, offsets)


def _place_wavelet(
    vertex: FireVertex, offsets: list[tuple[float, float]] | None
) -> list[FireVertex]:
    """The wavelet's vertices: metre offsets (north, east) from ``vertex``; None = no spread."""
    if offsets is None:
        return [vertex]
    m_per_lng = _m_per_deg_lng(vertex.lat)
    lat, lng = vertex.lat, vertex.lng
    return [
        FireVertex(lat=lat + dn / _M_PER_DEG_LAT, lng=lng + de / m_per_lng)
        for dn, de in offsets
    ]


def _wavelet_offsets(
    conditions: SpreadConditions,
    fuel_type: FuelType,
    slope_pct: float,
    aspect_deg: float,
    dt_minutes: float,
    num_rays: int,
    ros_modifier: float,
    cbh: float | None,
    cfl: float | None,
    accel_window: tuple[float, float] | None,
) -> list[tuple[float, float]] | None:
    """Metre offsets (north, east) of the wavelet's ray end points from the vertex, or None
    when the fire does not spread. Depends only on the local inputs, not on the vertex, so a
    front in uniform fuel and terrain computes it once per step (``expand_fire_front``)."""
    fbp = fbp_for_conditions(conditions, fuel_type, slope_pct, aspect_deg, cbh, cfl)

    head_ros = fbp.ros_final * ros_modifier
    if head_ros <= 0.001:
        return None  # No spread
    back_ros = fbp.back_ros * ros_modifier
    flank_ros = fbp.flank_ros * ros_modifier
    if accel_window is not None:
        alpha = calculate_acceleration(fuel_type, fbp.cfb)
        g = mean_acceleration_factor(alpha, *accel_window)
        lb_t = calculate_lb_at_time(fbp.lb, alpha, 0.5 * (accel_window[0] + accel_window[1]))
        head_ros *= g
        back_ros *= g
        flank_ros = (head_ros + back_ros) / (2.0 * lb_t)
        if head_ros <= 1e-9:
            return None

    # Ellipse in rate space: semi-major (head + back) / 2, semi-minor = FROS,
    # centre displaced (head - back) / 2 toward the head along RAZ.
    spread_dir_rad = math.radians(fbp.raz)
    a_ros = (head_ros + back_ros) / 2.0
    b_ros = flank_ros
    center_offset = (head_ros - back_ros) / 2.0 * dt_minutes
    offset_n = center_offset * math.cos(spread_dir_rad)
    offset_e = center_offset * math.sin(spread_dir_rad)

    offsets: list[tuple[float, float]] = []
    for i in range(num_rays):
        # Ray direction (degrees, 0=N, clockwise) measured from the ellipse centre
        ray_deg = 360.0 * i / num_rays
        ray_rad = math.radians(ray_deg)
        angle_from_head = math.radians(ray_deg - fbp.raz)
        cos_a = math.cos(angle_from_head)
        sin_a = math.sin(angle_from_head)

        # Polar radius of the ellipse from its centre
        denom = math.sqrt((b_ros * cos_a) ** 2 + (a_ros * sin_a) ** 2)
        ray_ros = a_ros if denom < 1e-10 else a_ros * b_ros / denom

        dist_m = ray_ros * dt_minutes
        total_dn = offset_n + dist_m * math.cos(ray_rad)
        total_de = offset_e + dist_m * math.sin(ray_rad)
        offsets.append((total_dn, total_de))

    return offsets


def expand_fire_front(
    front: list[FireVertex],
    conditions: SpreadConditions,
    fuel_grid: FuelGrid | None,
    terrain_grid: TerrainGrid | None,
    dt_minutes: float,
    default_fuel: FuelType = FuelType.C2,
    num_rays: int = 36,
    spread_modifier_grid: SpreadModifierGrid | None = None,
    accel_window: tuple[float, float] | None = None,
) -> list[FireVertex]:
    """Expand the entire fire front by one Huygens wavelet timestep.

    Each vertex on the fire front is expanded independently as an
    elliptical wavelet. The union of all wavelet points forms the
    new fire front.

    Args:
        front: Current fire front vertices
        conditions: Weather and FWI conditions
        fuel_grid: Spatial fuel type grid (or None for uniform fuel)
        terrain_grid: Slope/aspect grid (or None for flat terrain)
        dt_minutes: Timestep in minutes
        default_fuel: Fuel type to use when grid is None or lookup fails
        num_rays: Number of directional rays per wavelet
        spread_modifier_grid: Optional per-cell ROS/intensity multipliers
        accel_window: (t1, t2) minutes since the front's point ignition, or None

    Returns:
        New fire front vertices (expanded)
    """
    all_points: list[FireVertex] = []
    wavelet_cache: dict[tuple, list[tuple[float, float]] | None] = {}

    for vertex in front:
        # Look up local fuel type
        fuel = default_fuel
        if fuel_grid is not None:
            local_fuel = fuel_grid.get_fuel_at(vertex.lat, vertex.lng)
            if local_fuel is not None:
                fuel = local_fuel
            else:
                # Non-fuel: vertex stays in place (barrier edge)
                all_points.append(vertex)
                continue

        # Look up local terrain
        slope_pct, aspect_deg = 0.0, 0.0
        if terrain_grid is not None:
            slope_pct, aspect_deg = terrain_grid.get_slope_aspect(vertex.lat, vertex.lng)
        cbh, cfl = fuel_grid.get_canopy_at(vertex.lat, vertex.lng) if fuel_grid else (None, None)

        # Get WUI zone modifiers if available
        ros_mod = 1.0
        if spread_modifier_grid is not None:
            ros_mod, _, _ = spread_modifier_grid.get_modifiers_at(vertex.lat, vertex.lng)

        # Expand this vertex. The wavelet's shape depends only on the local inputs, so
        # vertices with the same fuel, terrain and modifiers share it (uniform fuel: once
        # per step); the result is the same as expanding each vertex on its own.
        key = (fuel, slope_pct, aspect_deg, ros_mod, cbh, cfl)
        offsets = wavelet_cache.get(key, _MISSING)
        if offsets is _MISSING:
            offsets = _wavelet_offsets(
                conditions, fuel, slope_pct, aspect_deg, dt_minutes, num_rays, ros_mod,
                cbh, cfl, accel_window,
            )
            wavelet_cache[key] = offsets
        wavelet = _place_wavelet(vertex, offsets)

        # Clip rays that land on non-fuel — shorten to barrier boundary
        if fuel_grid is not None:
            clipped = []
            for wp in wavelet:
                if fuel_grid.get_fuel_at(wp.lat, wp.lng) is not None:
                    clipped.append(wp)  # Endpoint has fuel, keep it
                else:
                    # Binary search for the fuel/non-fuel boundary
                    good_lat, good_lng = vertex.lat, vertex.lng
                    bad_lat, bad_lng = wp.lat, wp.lng
                    for _ in range(5):  # 5 iterations = ~3% of cell resolution
                        mid_lat = (good_lat + bad_lat) / 2
                        mid_lng = (good_lng + bad_lng) / 2
                        if fuel_grid.get_fuel_at(mid_lat, mid_lng) is not None:
                            good_lat, good_lng = mid_lat, mid_lng
                        else:
                            bad_lat, bad_lng = mid_lat, mid_lng
                    clipped.append(FireVertex(lat=good_lat, lng=good_lng))
            all_points.extend(clipped)
        else:
            all_points.extend(wavelet)

    if not all_points:
        return front  # No spread occurred

    return all_points


def simplify_front(
    points: list[FireVertex],
    tolerance_m: float = 10.0,
) -> list[FireVertex]:
    """Simplify fire front using convex hull + angular sampling.

    For the Huygens wavelet approach, the fire front after expansion
    is a cloud of points. We extract the convex hull to get the
    outer boundary, then resample at regular angular intervals.

    Args:
        points: All fire front points (potentially many)
        tolerance_m: Minimum spacing between output vertices (meters)

    Returns:
        Simplified fire front as ordered vertices
    """
    if len(points) <= 3:
        return points

    # Calculate centroid
    xs = [p.lat for p in points]
    ys = [p.lng for p in points]
    cx = sum(xs) / len(points)
    cy = sum(ys) / len(points)

    # Convex hull (monotone chain; it sorts the points itself, so the input order does not
    # matter)
    hull = _convex_hull(points)

    if len(hull) < 3:
        return hull

    # Resample at regular angular intervals for a clean perimeter
    num_output = max(36, len(hull))
    resampled = _resample_angular(hull, cx, cy, num_output)

    return resampled


def _convex_hull(points: list[FireVertex]) -> list[FireVertex]:
    """Compute convex hull using Andrew's monotone chain algorithm."""
    pts = sorted(points, key=lambda p: (p.lat, p.lng))

    if len(pts) <= 2:
        return pts

    # Build lower and upper hulls. Same arithmetic as ``_cross`` on plain floats (hot loop:
    # thousands of wavelet points per step).
    xs = [p.lat for p in pts]
    ys = [p.lng for p in pts]

    def chain(order) -> list[int]:
        out: list[int] = []
        for i in order:
            bx, by = xs[i], ys[i]
            while len(out) >= 2:
                o, a = out[-2], out[-1]
                ox, oy = xs[o], ys[o]
                if (xs[a] - ox) * (by - oy) - (ys[a] - oy) * (bx - ox) <= 0:
                    out.pop()
                else:
                    break
            out.append(i)
        return out

    n = len(pts)
    lower = [pts[i] for i in chain(range(n))]
    upper = [pts[i] for i in chain(range(n - 1, -1, -1))]

    return lower[:-1] + upper[:-1]


def _cross(o: FireVertex, a: FireVertex, b: FireVertex) -> float:
    """2D cross product of vectors OA and OB."""
    return (a.lat - o.lat) * (b.lng - o.lng) - (a.lng - o.lng) * (b.lat - o.lat)


def _resample_angular(
    hull: list[FireVertex],
    cx: float,
    cy: float,
    num_points: int,
) -> list[FireVertex]:
    """Resample hull vertices at regular angular intervals from centroid.

    This produces a clean, evenly-spaced perimeter.
    """
    if not hull:
        return hull

    # Sort hull by angle from centroid (stable: equal angles keep the hull order)
    angles = [math.atan2(p.lng - cy, p.lat - cx) for p in hull]
    order = sorted(range(len(hull)), key=angles.__getitem__)
    hull_sorted = [hull[i] for i in order]
    a_sorted = [angles[i] for i in order]
    n = len(a_sorted)

    # For each target angle, the hull vertex closest in angle: the first of the sorted
    # vertices with the smallest |angle - target| (what min() over the sorted list returns),
    # found by bisection instead of a scan
    result = []
    for i in range(num_points):
        target_angle = -math.pi + 2.0 * math.pi * i / num_points
        j = bisect.bisect_left(a_sorted, target_angle)
        if j == 0:
            best = 0
        else:
            below = abs(a_sorted[j - 1] - target_angle)
            if j < n and abs(a_sorted[j] - target_angle) < below:
                best = j
            else:
                # the first vertex at that distance (equal angles, or rounding ties)
                best = j - 1
                while best > 0 and abs(a_sorted[best - 1] - target_angle) == below:
                    best -= 1
        result.append(hull_sorted[best])

    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for p in result:
        key = (round(p.lat, 8), round(p.lng, 8))
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique
