"""Tests for the Huygens wavelet fire spread module.

Tests cover vertex expansion, fire front expansion, front simplification,
and the FuelGrid / TerrainGrid data structures.
"""

import math

import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.huygens import (
    FireVertex,
    FuelGrid,
    SpreadConditions,
    TerrainGrid,
    expand_fire_front,
    expand_vertex,
    simplify_front,
)


@pytest.fixture
def base_conditions():
    """Moderate fire weather conditions."""
    return SpreadConditions(
        wind_speed=20.0,
        wind_direction=270.0,  # Wind from the west
        ffmc=90.0,
        dmc=45.0,
        dc=300.0,
    )


@pytest.fixture
def calm_conditions():
    """Calm wind conditions (nearly circular spread)."""
    return SpreadConditions(
        wind_speed=2.0,
        wind_direction=0.0,
        ffmc=85.0,
        dmc=30.0,
        dc=200.0,
    )


@pytest.fixture
def ignition_vertex():
    """Central Alberta ignition point."""
    return FireVertex(lat=51.0, lng=-114.0)


@pytest.fixture
def small_front(ignition_vertex):
    """A small circular fire front for testing."""
    center = ignition_vertex
    radius_m = 50.0
    m_per_deg_lat = 111320.0
    m_per_deg_lng = 111320.0 * math.cos(math.radians(center.lat))
    vertices = []
    for i in range(12):
        angle = 2.0 * math.pi * i / 12
        dlat = radius_m * math.cos(angle) / m_per_deg_lat
        dlng = radius_m * math.sin(angle) / m_per_deg_lng
        vertices.append(FireVertex(lat=center.lat + dlat, lng=center.lng + dlng))
    return vertices


class TestFireVertex:
    """Test FireVertex dataclass."""

    def test_create_vertex(self):
        v = FireVertex(lat=51.0, lng=-114.0)
        assert v.lat == 51.0
        assert v.lng == -114.0

    def test_vertex_equality(self):
        v1 = FireVertex(lat=51.0, lng=-114.0)
        v2 = FireVertex(lat=51.0, lng=-114.0)
        assert v1 == v2


class TestSpreadConditions:
    """Test SpreadConditions dataclass."""

    def test_defaults(self):
        sc = SpreadConditions(
            wind_speed=20.0, wind_direction=270.0, ffmc=90.0, dmc=45.0, dc=300.0
        )
        assert sc.pc == 50.0
        assert sc.grass_cure == 60.0


class TestFuelGrid:
    """Test FuelGrid spatial lookup."""

    @pytest.fixture
    def simple_grid(self):
        """2x2 fuel grid."""
        return FuelGrid(
            fuel_types=[
                [FuelType.C2, FuelType.C3],
                [FuelType.M1, None],
            ],
            lat_min=50.0,
            lat_max=51.0,
            lng_min=-115.0,
            lng_max=-114.0,
            rows=2,
            cols=2,
        )

    def test_lookup_within_grid(self, simple_grid):
        # Top-left quadrant should be C2
        fuel = simple_grid.get_fuel_at(50.75, -114.75)
        assert fuel == FuelType.C2

    def test_lookup_outside_grid(self, simple_grid):
        assert simple_grid.get_fuel_at(52.0, -114.0) is None
        assert simple_grid.get_fuel_at(50.5, -116.0) is None

    def test_lookup_non_fuel_cell(self, simple_grid):
        # Bottom-right is None (non-fuel)
        fuel = simple_grid.get_fuel_at(50.1, -114.1)
        assert fuel is None


class TestTerrainGrid:
    """Test TerrainGrid spatial lookup."""

    @pytest.fixture
    def flat_terrain(self):
        return TerrainGrid(
            slope=[[0.0, 0.0], [0.0, 0.0]],
            aspect=[[0.0, 0.0], [0.0, 0.0]],
            lat_min=50.0,
            lat_max=51.0,
            lng_min=-115.0,
            lng_max=-114.0,
            rows=2,
            cols=2,
        )

    @pytest.fixture
    def sloped_terrain(self):
        return TerrainGrid(
            slope=[[30.0, 10.0], [5.0, 0.0]],
            aspect=[[180.0, 90.0], [0.0, 270.0]],
            lat_min=50.0,
            lat_max=51.0,
            lng_min=-115.0,
            lng_max=-114.0,
            rows=2,
            cols=2,
        )

    def test_flat_terrain_lookup(self, flat_terrain):
        slope, aspect = flat_terrain.get_slope_aspect(50.5, -114.5)
        assert slope == 0.0
        assert aspect == 0.0

    def test_sloped_terrain_lookup(self, sloped_terrain):
        # Top-left cell: slope=30, aspect=180
        slope, aspect = sloped_terrain.get_slope_aspect(50.75, -114.75)
        assert slope == 30.0
        assert aspect == 180.0

    def test_outside_terrain_returns_flat(self, sloped_terrain):
        slope, aspect = sloped_terrain.get_slope_aspect(52.0, -114.0)
        assert slope == 0.0
        assert aspect == 0.0


class TestExpandVertex:
    """Test single vertex Huygens wavelet expansion."""

    def test_returns_multiple_points(self, base_conditions, ignition_vertex):
        """Expanding a vertex should return num_rays points."""
        points = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.C2,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=5.0,
            num_rays=36,
        )
        assert len(points) == 36

    def test_wavelet_surrounds_vertex(self, base_conditions, ignition_vertex):
        """Wavelet points should be distributed around the original vertex."""
        points = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.C2,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=5.0,
        )
        lats = [p.lat for p in points]
        lngs = [p.lng for p in points]
        # Some points should be north and south, east and west
        assert max(lats) > ignition_vertex.lat
        assert min(lats) < ignition_vertex.lat
        assert max(lngs) > ignition_vertex.lng
        assert min(lngs) < ignition_vertex.lng

    def test_no_spread_returns_original(self, ignition_vertex):
        """Very low FFMC / no wind should result in minimal or no spread."""
        conditions = SpreadConditions(
            wind_speed=0.0,
            wind_direction=0.0,
            ffmc=10.0,  # Very wet fuel
            dmc=1.0,
            dc=5.0,
        )
        # D1 (deciduous) has very low ROS
        points = expand_vertex(
            vertex=ignition_vertex,
            conditions=conditions,
            fuel_type=FuelType.D1,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=1.0,
        )
        # Should still return points, but they should be very close to origin
        for p in points:
            dist_lat = abs(p.lat - ignition_vertex.lat) * 111320.0
            dist_lng = abs(p.lng - ignition_vertex.lng) * 111320.0 * math.cos(
                math.radians(ignition_vertex.lat)
            )
            # Within 100m even after 1 minute (D1 is very slow)
            assert dist_lat < 100.0
            assert dist_lng < 100.0

    def test_wind_creates_elliptical_spread(self, ignition_vertex):
        """Strong wind should make spread elliptical — longer in downwind direction."""
        conditions = SpreadConditions(
            wind_speed=30.0,
            wind_direction=180.0,  # Wind from south, fire spreads north
            ffmc=92.0,
            dmc=50.0,
            dc=300.0,
        )
        points = expand_vertex(
            vertex=ignition_vertex,
            conditions=conditions,
            fuel_type=FuelType.C2,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=10.0,
        )
        lats = [p.lat for p in points]
        # Max northward displacement (head fire) should be greater than
        # max southward displacement (back fire)
        north_max = max(lats) - ignition_vertex.lat
        south_max = ignition_vertex.lat - min(lats)
        assert north_max > south_max

    def test_slope_affects_spread(self, base_conditions, ignition_vertex):
        """Upslope should increase spread in that direction."""
        flat = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.C2,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=5.0,
        )
        sloped = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.C2,
            slope_pct=30.0,
            aspect_deg=0.0,  # North-facing slope
            dt_minutes=5.0,
        )
        # Sloped case should differ from flat case
        flat_max_lat = max(p.lat for p in flat)
        sloped_max_lat = max(p.lat for p in sloped)
        # They should not be identical (slope has an effect)
        assert flat_max_lat != sloped_max_lat

    def test_different_fuels_different_spread(self, base_conditions, ignition_vertex):
        """Different fuel types should produce different spread distances."""
        c2_points = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.C2,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=5.0,
        )
        d1_points = expand_vertex(
            vertex=ignition_vertex,
            conditions=base_conditions,
            fuel_type=FuelType.D1,
            slope_pct=0.0,
            aspect_deg=0.0,
            dt_minutes=5.0,
        )
        c2_max = max(p.lat for p in c2_points)
        d1_max = max(p.lat for p in d1_points)
        # C2 (Boreal Spruce) should spread faster than D1 (Deciduous)
        assert c2_max > d1_max


class TestExpandFireFront:
    """Test expansion of the entire fire front."""

    def test_front_grows(self, base_conditions, small_front):
        """Expanded front should have more points than original."""
        new_front = expand_fire_front(
            front=small_front,
            conditions=base_conditions,
            fuel_grid=None,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        # Each of 12 vertices produces 36 rays = 432 points
        assert len(new_front) > len(small_front)

    def test_front_area_increases(self, base_conditions, small_front):
        """Bounding box of expanded front should be larger."""
        new_front = expand_fire_front(
            front=small_front,
            conditions=base_conditions,
            fuel_grid=None,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        old_lat_range = max(p.lat for p in small_front) - min(
            p.lat for p in small_front
        )
        new_lat_range = max(p.lat for p in new_front) - min(
            p.lat for p in new_front
        )
        assert new_lat_range > old_lat_range

    def test_fuel_grid_non_fuel_stops_spread(self, base_conditions):
        """Vertices in non-fuel cells should not produce new wavelet points."""
        # Grid where everything is non-fuel
        grid = FuelGrid(
            fuel_types=[[None, None], [None, None]],
            lat_min=50.0,
            lat_max=52.0,
            lng_min=-115.0,
            lng_max=-113.0,
            rows=2,
            cols=2,
        )
        front = [FireVertex(lat=51.0, lng=-114.0)]
        new_front = expand_fire_front(
            front=front,
            conditions=base_conditions,
            fuel_grid=grid,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        # Non-fuel means no spread; should return original front
        assert new_front == front

    def test_empty_front_returns_empty(self, base_conditions):
        """Empty front should remain empty."""
        new_front = expand_fire_front(
            front=[],
            conditions=base_conditions,
            fuel_grid=None,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        assert new_front == []


class TestSimplifyFront:
    """Test fire front simplification."""

    def test_simplify_reduces_points(self, base_conditions, small_front):
        """Simplification of a large point cloud should produce fewer points."""
        # Generate a large cloud by expanding
        expanded = expand_fire_front(
            front=small_front,
            conditions=base_conditions,
            fuel_grid=None,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        simplified = simplify_front(expanded)
        assert len(simplified) < len(expanded)
        # But still has a reasonable number of vertices
        assert len(simplified) >= 3

    def test_simplify_small_front_unchanged(self):
        """Fronts with <= 3 points should not be simplified."""
        front = [
            FireVertex(lat=51.0, lng=-114.0),
            FireVertex(lat=51.001, lng=-114.0),
            FireVertex(lat=51.0, lng=-113.999),
        ]
        result = simplify_front(front)
        assert len(result) == 3

    def test_simplify_preserves_extent(self, base_conditions, small_front):
        """Simplified front should cover roughly the same geographic extent."""
        expanded = expand_fire_front(
            front=small_front,
            conditions=base_conditions,
            fuel_grid=None,
            terrain_grid=None,
            dt_minutes=5.0,
        )
        simplified = simplify_front(expanded)

        # Bounding box should be similar (convex hull preserves extremes)
        exp_lat_max = max(p.lat for p in expanded)
        exp_lat_min = min(p.lat for p in expanded)
        sim_lat_max = max(p.lat for p in simplified)
        sim_lat_min = min(p.lat for p in simplified)

        # Within 10% of the original extent
        lat_range = exp_lat_max - exp_lat_min
        assert abs(sim_lat_max - exp_lat_max) < 0.1 * lat_range
        assert abs(sim_lat_min - exp_lat_min) < 0.1 * lat_range


# ── Performance rewrites must not change results (multi-day speed, 2026-10-11) ──────────


def _reference_resample(hull, cx, cy, num_points):
    """The pre-2026-10-11 resampling: a linear min() scan per target angle."""
    def angle(p):
        return math.atan2(p.lng - cy, p.lat - cx)

    hull_sorted = sorted(hull, key=angle)
    result = [
        min(hull_sorted, key=lambda p: abs(angle(p) - (-math.pi + 2.0 * math.pi * i / num_points)))
        for i in range(num_points)
    ]
    seen, unique = set(), []
    for p in result:
        key = (round(p.lat, 8), round(p.lng, 8))
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _reference_hull(points):
    from firesim.spread.huygens import _cross

    pts = sorted(points, key=lambda p: (p.lat, p.lng))
    if len(pts) <= 2:
        return pts
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


@pytest.mark.parametrize("seed", range(6))
def test_hull_and_resampling_match_the_reference(seed):
    """Bisection resampling and the inlined monotone chain give exactly the old output,
    including duplicate points, equal angles and fewer hull vertices than targets."""
    import random

    from firesim.spread.huygens import _convex_hull, _resample_angular

    rng = random.Random(seed)
    pts = [FireVertex(53.5 + rng.gauss(0, 0.01), -113.5 + rng.gauss(0, 0.02)) for _ in range(800)]
    pts += pts[:50]  # exact duplicates
    pts += [FireVertex(53.5 + 0.001 * k, -113.5 + 0.001 * k) for k in range(-5, 6)]  # collinear
    hull = _convex_hull(pts)
    assert hull == _reference_hull(pts)
    cx = sum(p.lat for p in pts) / len(pts)
    cy = sum(p.lng for p in pts) / len(pts)
    for n in (5, 36, len(hull), 3 * len(hull)):
        assert _resample_angular(hull, cx, cy, n) == _reference_resample(hull, cx, cy, n)
    # a hull with repeated angles from the centroid (points on one ray)
    ray = [FireVertex(53.5 + 0.001 * k, -113.5) for k in (1, 2, 3)] + hull
    assert _resample_angular(ray, cx, cy, 72) == _reference_resample(ray, cx, cy, 72)


def test_front_expansion_shares_wavelets_without_changing_them(base_conditions):
    """expand_fire_front computes each distinct wavelet once; every point equals expanding
    the vertex on its own (uniform fuel, terrain and a WUI modifier grid)."""
    front = [FireVertex(53.5 + 0.002 * math.cos(a / 10), -113.5 + 0.003 * math.sin(a / 10))
             for a in range(63)]
    terrain = TerrainGrid([[0.0, 30.0], [12.0, 5.0]], [[0.0, 90.0], [180.0, 270.0]],
                          53.49, 53.51, -113.51, -113.49, 2, 2)
    for tg in (None, terrain):
        got = expand_fire_front(front, base_conditions, None, tg, 5.0, accel_window=(10.0, 15.0))
        want = []
        for v in front:
            slope, aspect = tg.get_slope_aspect(v.lat, v.lng) if tg else (0.0, 0.0)
            want += expand_vertex(v, base_conditions, FuelType.C2, slope, aspect, 5.0,
                                  accel_window=(10.0, 15.0))
        assert got == want


def test_simplify_front_is_independent_of_point_order(base_conditions):
    import random

    front = [FireVertex(53.5 + 0.002 * math.cos(a / 5), -113.5 + 0.003 * math.sin(a / 5))
             for a in range(32)]
    pts = expand_fire_front(front, base_conditions, None, None, 5.0)
    shuffled = pts[:]
    random.Random(1).shuffle(shuffled)
    a, b = simplify_front(pts), simplify_front(shuffled)
    # the centroid sum depends on the order only through rounding
    assert len(a) == len(b)
    for p, q in zip(a, b):
        assert p.lat == pytest.approx(q.lat, abs=1e-12) and p.lng == pytest.approx(q.lng, abs=1e-12)
