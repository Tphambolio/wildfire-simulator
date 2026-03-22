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

import math
from dataclasses import dataclass, field

from firesim.fbp.calculator import calculate_fbp
from firesim.fbp.constants import FuelType
from firesim.spread.ellipse import (
    calculate_back_ros,
    calculate_flank_ros,
    calculate_length_to_breadth_ratio,
)
from firesim.spread.slope import calculate_directional_slope_factor
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

    def get_fuel_at(self, lat: float, lng: float) -> FuelType | None:
        """Look up fuel type at a geographic coordinate.

        Returns None if outside grid or non-fuel.
        """
        if lat < self.lat_min or lat > self.lat_max:
            return None
        if lng < self.lng_min or lng > self.lng_max:
            return None

        row = int((self.lat_max - lat) / (self.lat_max - self.lat_min) * self.rows)
        col = int((lng - self.lng_min) / (self.lng_max - self.lng_min) * self.cols)

        row = max(0, min(self.rows - 1, row))
        col = max(0, min(self.cols - 1, col))

        return self.fuel_types[row][col]


@dataclass
class TerrainGrid:
    """Spatial grid of slope and aspect.

    Stores pre-computed slope (%) and aspect (degrees) for each cell.
    """

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
) -> list[FireVertex]:
    """Expand a single fire front vertex as a Huygens wavelet.

    Calculates FBP output for the local fuel type and conditions,
    then generates an elliptical wavelet centered on the vertex.

    Args:
        vertex: Fire front vertex to expand
        conditions: Weather and FWI conditions
        fuel_type: Local fuel type at this vertex
        slope_pct: Local slope (%)
        aspect_deg: Local aspect (degrees, 0=N)
        dt_minutes: Timestep duration (minutes)
        num_rays: Number of radial directions to sample

    Returns:
        List of new vertices forming the wavelet ellipse
    """
    # Calculate FBP for this fuel type (no slope — we apply directional slope per ray)
    fbp = calculate_fbp(
        fuel_type=fuel_type,
        wind_speed=conditions.wind_speed,
        ffmc=conditions.ffmc,
        dmc=conditions.dmc,
        dc=conditions.dc,
        slope=0.0,  # Slope applied directionally below
        pc=conditions.pc,
        grass_cure=conditions.grass_cure,
    )

    head_ros = fbp.ros_final * ros_modifier  # m/min, modified by WUI zone

    if head_ros <= 0.001:
        return [vertex]  # No spread

    # Fire ellipse shape
    lbr = calculate_length_to_breadth_ratio(conditions.wind_speed)
    back_ros = calculate_back_ros(head_ros, lbr)
    flank_ros = calculate_flank_ros(head_ros, lbr)

    # Fire spread direction (opposite of meteorological wind FROM)
    spread_dir = (conditions.wind_direction + 180.0) % 360.0
    spread_dir_rad = math.radians(spread_dir)

    # Coordinate conversion
    m_per_lng = _m_per_deg_lng(vertex.lat)

    wavelet_points = []
    for i in range(num_rays):
        # Ray direction (degrees, 0=N, clockwise)
        ray_deg = 360.0 * i / num_rays
        ray_rad = math.radians(ray_deg)

        # Angle between this ray and the head fire direction
        angle_from_head = ray_deg - spread_dir
        angle_from_head_rad = math.radians(angle_from_head)

        # ROS in this direction (elliptical interpolation)
        # Using the elliptical ROS formula:
        # ROS(theta) = a * b / sqrt((b*cos(theta))^2 + (a*sin(theta))^2)
        # where a = semi-major (head direction), b = semi-minor (flank)
        cos_a = math.cos(angle_from_head_rad)
        sin_a = math.sin(angle_from_head_rad)

        # Semi-axes in ROS space
        # Head fire: semi-major axis in spread direction
        a_ros = (head_ros + back_ros) / 2.0
        b_ros = flank_ros

        # Offset from center (the ellipse center is shifted from ignition)
        center_offset_ros = (head_ros - back_ros) / 2.0

        # Point on the ellipse relative to the center
        denom = math.sqrt((b_ros * cos_a) ** 2 + (a_ros * sin_a) ** 2)
        if denom < 1e-10:
            ray_ros = a_ros
        else:
            ray_ros = a_ros * b_ros / denom

        # Apply directional slope factor
        sf = calculate_directional_slope_factor(slope_pct, aspect_deg, ray_deg)
        ray_ros *= sf

        # Distance traveled in this timestep
        dist_m = ray_ros * dt_minutes

        # The actual displacement accounts for the ellipse center offset
        # For simplicity, compute the wavelet point from the vertex
        # using the offset center plus the elliptical radius
        offset_n = center_offset_ros * dt_minutes * math.cos(spread_dir_rad)
        offset_e = center_offset_ros * dt_minutes * math.sin(spread_dir_rad)

        # Ray displacement from ellipse center
        dn = dist_m * math.cos(ray_rad)
        de = dist_m * math.sin(ray_rad)

        # Total displacement from vertex
        total_dn = offset_n + dn
        total_de = offset_e + de

        # Convert to lat/lng
        new_lat = vertex.lat + total_dn / _M_PER_DEG_LAT
        new_lng = vertex.lng + total_de / m_per_lng

        wavelet_points.append(FireVertex(lat=new_lat, lng=new_lng))

    return wavelet_points


def expand_fire_front(
    front: list[FireVertex],
    conditions: SpreadConditions,
    fuel_grid: FuelGrid | None,
    terrain_grid: TerrainGrid | None,
    dt_minutes: float,
    default_fuel: FuelType = FuelType.C2,
    num_rays: int = 36,
    spread_modifier_grid: SpreadModifierGrid | None = None,
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

    Returns:
        New fire front vertices (expanded)
    """
    all_points: list[FireVertex] = []

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

        # Get WUI zone modifiers if available
        ros_mod = 1.0
        if spread_modifier_grid is not None:
            ros_mod, _, _ = spread_modifier_grid.get_modifiers_at(vertex.lat, vertex.lng)

        # Expand this vertex
        wavelet = expand_vertex(
            vertex=vertex,
            conditions=conditions,
            fuel_type=fuel,
            slope_pct=slope_pct,
            aspect_deg=aspect_deg,
            dt_minutes=dt_minutes,
            num_rays=num_rays,
            ros_modifier=ros_mod,
        )

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
    cx = sum(p.lat for p in points) / len(points)
    cy = sum(p.lng for p in points) / len(points)

    # Sort points by angle from centroid
    def angle_key(p: FireVertex) -> float:
        return math.atan2(p.lng - cy, p.lat - cx)

    sorted_points = sorted(points, key=angle_key)

    # Convex hull via Graham scan
    hull = _convex_hull(sorted_points)

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

    # Build lower hull
    lower: list[FireVertex] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper: list[FireVertex] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

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

    # Sort hull by angle from centroid
    def angle(p: FireVertex) -> float:
        return math.atan2(p.lng - cy, p.lat - cx)

    hull_sorted = sorted(hull, key=angle)

    # For each target angle, find the hull vertex closest to that angle
    # and interpolate if needed
    result = []
    for i in range(num_points):
        target_angle = -math.pi + 2.0 * math.pi * i / num_points

        # Find the two hull vertices that bracket this angle
        best = min(hull_sorted, key=lambda p: abs(angle(p) - target_angle))
        result.append(best)

    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for p in result:
        key = (round(p.lat, 8), round(p.lng, 8))
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique
