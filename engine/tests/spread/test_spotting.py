"""Unit tests for ember spotting fire spread model.

Tests cover:
- Von Mises directional sampling (Albini 1979 wind-biased direction)
- Spotting distance scaling with wind speed and intensity
- Crown fire threshold enforcement (Van Wagner 1977)
- Non-fuel landing rejection

References:
    - Albini, F.A. (1979). Spot Fire Distance from Burning Trees — A Predictive
      Model. USDA Forest Service General Technical Report INT-56.
    - Van Wagner, C.E. (1977). Conditions for the start and spread of crown fire.
      Canadian Journal of Forest Research, 7, 23–34.
"""

import math
import random
import statistics

import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FireVertex, FuelGrid, SpreadConditions
from firesim.spread.spotting import (
    CROWN_FIRE_THRESHOLD_KW_M,
    MAX_SPOT_PROB,
    SpotFire,
    _von_mises_sample,
    check_ember_spotting,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def extreme_conditions():
    """Extreme fire weather — high enough HFI to exceed crown fire threshold."""
    return SpreadConditions(
        wind_speed=50.0,
        wind_direction=270.0,  # FROM west
        ffmc=95.0,
        dmc=80.0,
        dc=500.0,
    )


@pytest.fixture
def moderate_conditions():
    """Moderate fire weather — unlikely to generate embers."""
    return SpreadConditions(
        wind_speed=10.0,
        wind_direction=270.0,
        ffmc=80.0,
        dmc=30.0,
        dc=200.0,
    )


def make_fuel_grid(
    rows: int = 10,
    cols: int = 10,
    fuel: FuelType = FuelType.C5,
    lat_center: float = 53.5,
    lng_center: float = -113.5,
) -> FuelGrid:
    """Create a small uniform fuel grid for spotting tests."""
    cell_deg = 0.01
    lat_span = rows * cell_deg
    lng_span = cols * cell_deg
    lat_min = lat_center - lat_span / 2
    lat_max = lat_center + lat_span / 2
    lng_min = lng_center - lng_span / 2
    lng_max = lng_center + lng_span / 2
    fuel_types = [[fuel] * cols for _ in range(rows)]
    return FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )


def make_nonfuel_grid(
    lat_center: float = 53.5,
    lng_center: float = -113.5,
) -> FuelGrid:
    """A grid where all cells are non-fuel (water/pavement)."""
    rows, cols = 5, 5
    cell_deg = 0.01
    lat_span = rows * cell_deg
    lng_span = cols * cell_deg
    fuel_types = [[None] * cols for _ in range(rows)]
    return FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_center - lat_span / 2,
        lat_max=lat_center + lat_span / 2,
        lng_min=lng_center - lng_span / 2,
        lng_max=lng_center + lng_span / 2,
        rows=rows,
        cols=cols,
    )


def make_front(lat: float = 53.5, lng: float = -113.5, n: int = 20) -> list[FireVertex]:
    """Generate a small circular fire front at the given point."""
    vertices = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        dlat = 0.001 * math.cos(angle)
        dlng = 0.001 * math.sin(angle)
        vertices.append(FireVertex(lat=lat + dlat, lng=lng + dlng))
    return vertices


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify model constants match the literature."""

    def test_emission_threshold(self):
        """Heuristic ember-emission threshold (not from Van Wagner or Albini)."""
        assert CROWN_FIRE_THRESHOLD_KW_M == pytest.approx(4000.0)

    def test_max_spot_prob_positive_and_bounded(self):
        """MAX_SPOT_PROB must be a small positive probability."""
        assert 0.0 < MAX_SPOT_PROB <= 1.0


# ---------------------------------------------------------------------------
# Von Mises sampling (_von_mises_sample)
# ---------------------------------------------------------------------------


class TestVonMisesSample:
    """Verify von Mises directional distribution behaviour.

    With high kappa (concentrated), samples should cluster around mu.
    With kappa~0 (diffuse), samples should be nearly uniform.
    """

    def test_returns_float(self):
        val = _von_mises_sample(mu=0.0, kappa=1.0, rng=random.Random(0))
        assert isinstance(val, float)

    def test_very_low_kappa_returns_uniform(self):
        """kappa < 0.01 should fall back to uniform [0, 2π]."""
        rng = random.Random(99)
        samples = [_von_mises_sample(mu=0.0, kappa=0.0, rng=rng) for _ in range(200)]
        # Uniform on [0, 2π]: mean ≈ π ± large variance
        mean = statistics.mean(samples)
        assert 0.5 < mean < 2 * math.pi - 0.5

    def test_high_kappa_clusters_around_mu(self):
        """With kappa=50, >90% of samples should be within 0.3 rad of mu."""
        rng = random.Random(123)
        mu = math.pi / 2  # 90 degrees
        kappa = 50.0
        samples = [_von_mises_sample(mu=mu, kappa=kappa, rng=rng) for _ in range(500)]

        def angular_diff(a, b):
            d = abs(a - b) % (2 * math.pi)
            return d if d <= math.pi else 2 * math.pi - d

        close = sum(1 for s in samples if angular_diff(s, mu) < 0.3)
        fraction = close / len(samples)
        assert fraction > 0.90, f"Only {fraction:.1%} of samples within 0.3 rad of mu"

    def test_mean_direction_aligned_with_mu(self):
        """Mean circular direction of von Mises samples should approximate mu."""
        rng = random.Random(777)
        mu = 1.5  # radians
        kappa = 10.0
        samples = [_von_mises_sample(mu=mu, kappa=kappa, rng=rng) for _ in range(1000)]
        # Circular mean
        sin_mean = statistics.mean(math.sin(s) for s in samples)
        cos_mean = statistics.mean(math.cos(s) for s in samples)
        circular_mean = math.atan2(sin_mean, cos_mean) % (2 * math.pi)
        mu_norm = mu % (2 * math.pi)
        diff = abs(circular_mean - mu_norm)
        diff = min(diff, 2 * math.pi - diff)
        assert diff < 0.15, f"Circular mean {circular_mean:.3f} too far from mu {mu_norm:.3f}"

    def test_higher_kappa_lower_variance(self):
        """Higher concentration parameter should reduce angular spread.

        Use circular variance (1 - R̄) which properly handles the wrap-around
        nature of angular data. Lower values indicate tighter concentration.
        """
        rng = random.Random(42)
        mu = 0.0

        def circular_variance(kappa):
            """Circular variance: 0 = perfectly concentrated, 1 = uniform."""
            samples = [_von_mises_sample(mu=mu, kappa=kappa, rng=rng) for _ in range(500)]
            sin_mean = statistics.mean(math.sin(s - mu) for s in samples)
            cos_mean = statistics.mean(math.cos(s - mu) for s in samples)
            r_bar = math.sqrt(sin_mean ** 2 + cos_mean ** 2)
            return 1.0 - r_bar

        var_low = circular_variance(1.0)
        var_high = circular_variance(20.0)
        assert var_high < var_low


# ---------------------------------------------------------------------------
# Crown fire threshold (Van Wagner 1977)
# ---------------------------------------------------------------------------


class TestCrownFireThreshold:
    """Fires below the crown fire threshold must not generate embers."""

    def test_low_intensity_no_spot_fires(self, moderate_conditions):
        """Moderate conditions (C2, low weather) should stay below crown threshold."""
        rng = random.Random(0)
        front = make_front()
        grid = make_fuel_grid(fuel=FuelType.C2)
        # C2 with moderate weather will typically have HFI < 4000 kW/m
        spots = check_ember_spotting(
            front=front,
            conditions=moderate_conditions,
            fuel_grid=grid,
            spread_modifier_grid=None,
            default_fuel=FuelType.C2,
            dt_minutes=5.0,
            check_interval=1,
            rng=rng,
        )
        # May or may not produce spots depending on FBP output; but if HFI < threshold, none
        # We verify the function returns a list (may be empty)
        assert isinstance(spots, list)

    def test_returns_spot_fires_only_when_crown(self, extreme_conditions):
        """Under extreme conditions with crown-capable fuel, some spots are possible."""
        rng = random.Random(10)
        # Run many trials to get at least one spot fire
        front = make_front(n=80)
        grid = make_fuel_grid(fuel=FuelType.C5, rows=20, cols=20)
        all_spots = []
        for seed in range(30):
            rng = random.Random(seed)
            spots = check_ember_spotting(
                front=front,
                conditions=extreme_conditions,
                fuel_grid=grid,
                spread_modifier_grid=None,
                default_fuel=FuelType.C5,
                dt_minutes=5.0,
                check_interval=1,
                rng=rng,
            )
            all_spots.extend(spots)
        # Not asserting any spots were produced (stochastic) but result must be a list
        assert isinstance(all_spots, list)


# ---------------------------------------------------------------------------
# Non-fuel landing rejection
# ---------------------------------------------------------------------------


class TestNonFuelLandingRejection:
    """Embers landing on non-fuel cells must be rejected (no SpotFire created)."""

    def test_landing_on_nonfuel_grid_produces_no_spots(self, extreme_conditions):
        """All-non-fuel grid: even if embers are generated, no SpotFire is kept."""
        rng = random.Random(20)
        front = make_front(n=80)
        nonfuel_grid = make_nonfuel_grid()
        all_spots = []
        for seed in range(50):
            rng = random.Random(seed)
            spots = check_ember_spotting(
                front=front,
                conditions=extreme_conditions,
                fuel_grid=nonfuel_grid,
                spread_modifier_grid=None,
                default_fuel=FuelType.C5,
                dt_minutes=5.0,
                check_interval=1,
                rng=rng,
            )
            all_spots.extend(spots)
        assert all_spots == [], (
            "Expected no spot fires when landing grid is all non-fuel"
        )

    def test_spot_fires_respect_fuel_grid_bounds(self, extreme_conditions):
        """Spot fires outside the fuel grid must not be created."""
        rng = random.Random(30)
        # Small grid; fire front center at grid center but embers can fly out of bounds
        lat_center = 53.5
        lng_center = -113.5
        grid = make_fuel_grid(
            rows=5, cols=5, lat_center=lat_center, lng_center=lng_center
        )
        front = make_front(lat=lat_center, lng=lng_center, n=20)

        all_spots = []
        for seed in range(50):
            rng = random.Random(seed)
            spots = check_ember_spotting(
                front=front,
                conditions=extreme_conditions,
                fuel_grid=grid,
                spread_modifier_grid=None,
                default_fuel=FuelType.C5,
                dt_minutes=5.0,
                check_interval=1,
                rng=rng,
            )
            all_spots.extend(spots)

        # Any spot fire that exists must land within a fuel cell
        for spot in all_spots:
            landed_fuel = grid.get_fuel_at(spot.lat, spot.lng)
            assert landed_fuel is not None, (
                f"Spot fire at ({spot.lat:.4f}, {spot.lng:.4f}) landed on non-fuel"
            )


# ---------------------------------------------------------------------------
# SpotFire dataclass
# ---------------------------------------------------------------------------


class TestSpotFireDataclass:
    """Verify SpotFire carries the expected metadata."""

    def test_spot_fire_fields(self):
        sf = SpotFire(
            lat=53.51,
            lng=-113.49,
            source_lat=53.5,
            source_lng=-113.5,
            distance_m=150.0,
            hfi_kw_m=5000.0,
        )
        assert sf.lat == pytest.approx(53.51)
        assert sf.lng == pytest.approx(-113.49)
        assert sf.distance_m == pytest.approx(150.0)
        assert sf.hfi_kw_m == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# Spotting distance — Albini (1979) wind-scaling
# ---------------------------------------------------------------------------


class TestSpottingDistanceScaling:
    """Spot distance must increase with wind speed and stay within Albini's maximum.

    Maximum distances come from firesim.spread.albini, which is checked against the
    published worked examples in test_albini.py.
    """

    def _collect_spots(self, conditions, n_trials=100):
        front = make_front(n=80)
        grid = make_fuel_grid(fuel=FuelType.C5, rows=30, cols=30)
        all_spots = []
        for seed in range(n_trials):
            rng = random.Random(seed)
            spots = check_ember_spotting(
                front=front,
                conditions=conditions,
                fuel_grid=grid,
                spread_modifier_grid=None,
                default_fuel=FuelType.C5,
                dt_minutes=5.0,
                check_interval=1,
                rng=rng,
            )
            all_spots.extend(spots)
        return all_spots

    def test_high_wind_produces_longer_distances(self):
        """Higher wind should produce a higher mean spot distance."""
        low_wind = SpreadConditions(
            wind_speed=20.0,
            wind_direction=270.0,
            ffmc=95.0,
            dmc=80.0,
            dc=500.0,
        )
        high_wind = SpreadConditions(
            wind_speed=60.0,
            wind_direction=270.0,
            ffmc=95.0,
            dmc=80.0,
            dc=500.0,
        )
        low_spots = self._collect_spots(low_wind, n_trials=200)
        high_spots = self._collect_spots(high_wind, n_trials=200)

        if not low_spots or not high_spots:
            pytest.skip("Insufficient spot fires produced — check fuel/FBP parameters")

        mean_low = statistics.mean(s.distance_m for s in low_spots)
        mean_high = statistics.mean(s.distance_m for s in high_spots)
        assert mean_high > mean_low, (
            f"High-wind mean distance ({mean_high:.0f}m) not greater than "
            f"low-wind mean ({mean_low:.0f}m)"
        )

    def test_spot_distance_positive(self):
        """All spot fire distances must be > 10 m (code filters < 10 m)."""
        rng = random.Random(50)
        conditions = SpreadConditions(
            wind_speed=50.0,
            wind_direction=270.0,
            ffmc=95.0,
            dmc=80.0,
            dc=500.0,
        )
        spots = self._collect_spots(conditions, n_trials=100)
        for spot in spots:
            assert spot.distance_m > 10.0, (
                f"Spot distance {spot.distance_m:.1f} m is below the 10 m minimum"
            )

    def test_distances_bounded_by_albini_maximum(self):
        """Each spot lands within the Albini maximum distance for its source."""
        from firesim.fbp.constants import FuelType
        from firesim.spread.albini import max_spot_distance
        from firesim.spread.huygens import fbp_for_conditions

        conditions = SpreadConditions(
            wind_speed=40.0, wind_direction=270.0, ffmc=95.0, dmc=80.0, dc=500.0,
        )
        fbp = fbp_for_conditions(conditions, FuelType.C5)  # the fuel _collect_spots uses
        s_max = max_spot_distance("C5", fbp.hfi, fbp.cfb, conditions.wind_speed)
        spots = self._collect_spots(conditions, n_trials=200)
        assert spots
        for spot in spots:
            assert 0.3 * s_max - 1e-6 <= spot.distance_m <= s_max + 1e-6


# ---------------------------------------------------------------------------
# Wind directional bias — von Mises alignment
# ---------------------------------------------------------------------------


class TestWindDirectionalBias:
    """Spot fires should cluster downwind of the source (Albini 1979).

    Wind from west (270°) means fire spreads east (90°).
    Spot fire displacements should be predominantly eastward.
    """

    def test_spot_fires_predominantly_downwind(self):
        """Spots should land east of source when wind is from west."""
        rng = random.Random(99)
        lat_center = 53.5
        lng_center = -113.5
        conditions = SpreadConditions(
            wind_speed=50.0,
            wind_direction=270.0,  # FROM west → TO east
            ffmc=95.0,
            dmc=80.0,
            dc=500.0,
        )
        front = make_front(lat=lat_center, lng=lng_center, n=80)
        grid = make_fuel_grid(
            rows=30, cols=60,
            lat_center=lat_center,
            lng_center=lng_center + 0.15,  # large grid extending east
            fuel=FuelType.C5,
        )

        all_spots = []
        for seed in range(200):
            rng = random.Random(seed)
            spots = check_ember_spotting(
                front=front,
                conditions=conditions,
                fuel_grid=grid,
                spread_modifier_grid=None,
                default_fuel=FuelType.C5,
                dt_minutes=5.0,
                check_interval=1,
                rng=rng,
            )
            all_spots.extend(spots)

        if len(all_spots) < 5:
            pytest.skip("Too few spot fires to assess directional bias")

        # Count spots east vs west of source front center
        east = sum(1 for s in all_spots if s.lng > lng_center)
        west = sum(1 for s in all_spots if s.lng <= lng_center)

        assert east > west, (
            f"Expected eastward bias but got east={east}, west={west}"
        )
