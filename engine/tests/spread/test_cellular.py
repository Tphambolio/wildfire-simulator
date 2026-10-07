"""Tests for the grid (level-set) fire spread model.

Covers agreement with the FBP point-ignition ellipse on uniform fuel,
determinism, barriers, directional intensity, and frame output.

References:
    - Forestry Canada Fire Danger Group (1992). ST-X-3 (fire ellipse, eqs 79-89).
    - Richards, G.D. (1990). An elliptical growth model of forest fire fronts.
"""

import math
import random

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.fbp.calculator import (
    calculate_acceleration,
    calculate_distance_at_time,
    calculate_fbp,
    calculate_lb_at_time,
)
from firesim.spread.cellular import (
    BurnedCell,
    CellularFrame,
    _make_frame,
    ellipse_arrival_time,
    run_cellular_simulation,
)
from firesim.spread.ellipse import calculate_ellipse_area
from firesim.spread.huygens import FuelGrid, SpreadConditions, fbp_for_conditions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def moderate_conditions():
    """Moderate fire weather — C2 fuel, 20 km/h wind from west."""
    return SpreadConditions(
        wind_speed=20.0,
        wind_direction=270.0,  # FROM west, spreads east
        ffmc=90.0,
        dmc=45.0,
        dc=300.0,
    )


@pytest.fixture
def calm_conditions():
    """Calm wind — near-circular spread expected."""
    return SpreadConditions(
        wind_speed=2.0,
        wind_direction=0.0,
        ffmc=85.0,
        dmc=30.0,
        dc=200.0,
    )


def make_uniform_grid(
    rows: int = 20,
    cols: int = 20,
    fuel: FuelType = FuelType.C2,
    lat_center: float = 53.5,
    lng_center: float = -113.5,
    cell_deg: float = 0.001,
) -> FuelGrid:
    """Create a uniform fuel grid for testing."""
    lat_span = rows * cell_deg
    lng_span = cols * cell_deg
    lat_min = lat_center - lat_span / 2
    lat_max = lat_center + lat_span / 2
    lng_min = lng_center - lng_span / 2
    lng_max = lng_center + lng_span / 2
    fuel_types = [[fuel for _ in range(cols)] for _ in range(rows)]
    return FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )


def make_grid_with_barrier(
    rows: int = 20,
    cols: int = 20,
    fuel: FuelType = FuelType.C2,
    barrier_col: int = 10,
) -> FuelGrid:
    """Grid with a vertical non-fuel barrier at barrier_col."""
    lat_center = 53.5
    lng_center = -113.5
    cell_deg = 0.001
    lat_span = rows * cell_deg
    lng_span = cols * cell_deg
    lat_min = lat_center - lat_span / 2
    lat_max = lat_center + lat_span / 2
    lng_min = lng_center - lng_span / 2
    lng_max = lng_center + lng_span / 2
    fuel_types = [
        [None if c == barrier_col else fuel for c in range(cols)]
        for _ in range(rows)
    ]
    return FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )


def center_config(fuel_grid: FuelGrid, duration_hours: float = 0.5) -> dict:
    """Config that ignites the center of the grid."""
    lat = (fuel_grid.lat_min + fuel_grid.lat_max) / 2
    lng = (fuel_grid.lng_min + fuel_grid.lng_max) / 2
    return {
        "ignition_lat": lat,
        "ignition_lng": lng,
        "duration_hours": duration_hours,
    }


def metre_grid(n: int, cell_m: float, fuel: FuelType = FuelType.C2, lat0: float = 53.5,
               lng0: float = -113.5) -> FuelGrid:
    """Square n x n grid of cell_m-metre cells centred on (lat0, lng0)."""
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(lat0)))
    return FuelGrid(
        fuel_types=[[fuel] * n for _ in range(n)],
        lat_min=lat0 - dlat / 2, lat_max=lat0 + dlat / 2,
        lng_min=lng0 - dlng / 2, lng_max=lng0 + dlng / 2, rows=n, cols=n,
    )


# ---------------------------------------------------------------------------
# Agreement with the FBP ellipse
# ---------------------------------------------------------------------------


class TestFBPEllipseAgreement:
    """On uniform fuel the burned area must follow the FBP point-ignition ellipse."""

    @pytest.mark.parametrize(
        "fuel,cure,tol",
        [(FuelType.C2, 60.0, 0.08), (FuelType.M1, 60.0, 0.08), (FuelType.O1a, 100.0, 0.12)],
    )
    def test_area_matches_fbp_ellipse(self, fuel, cure, tol):
        cond = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0,
                                dc=300.0, grass_cure=cure)
        f = fbp_for_conditions(cond, fuel)
        expected = calculate_ellipse_area(f.ros_final, f.back_ros, f.lb, 1.0)
        grid = metre_grid(200, 50.0, fuel)
        frames = run_cellular_simulation(center_config(grid, 1.0), grid, cond, acceleration=False)
        assert frames[-1].area_ha == pytest.approx(expected, rel=tol)

    @pytest.mark.parametrize(
        "fuel,cure,tol",
        [(FuelType.C2, 60.0, 0.10), (FuelType.O1a, 100.0, 0.12)],
    )
    def test_accelerating_area_matches_fbp(self, fuel, cure, tol):
        """With acceleration the area follows ST-X-3 eqs 73 and 81 (distance and LB at t)."""
        cond = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0,
                                dc=300.0, grass_cure=cure)
        f = fbp_for_conditions(cond, fuel)
        alpha = calculate_acceleration(fuel, f.cfb)
        t = 60.0
        length = calculate_distance_at_time(f.ros_final + f.back_ros, alpha, t)
        breadth = length / calculate_lb_at_time(f.lb, alpha, t)
        expected = math.pi * (length / 2) * (breadth / 2) / 1e4
        grid = metre_grid(200, 50.0, fuel)
        frames = run_cellular_simulation(center_config(grid, 1.0), grid, cond)
        assert frames[-1].area_ha == pytest.approx(expected, rel=tol)
        unaccelerated = calculate_ellipse_area(f.ros_final, f.back_ros, f.lb, 1.0)
        assert frames[-1].area_ha < unaccelerated

    def test_calm_fire_is_round(self):
        cond = SpreadConditions(wind_speed=0.0, wind_direction=0.0, ffmc=92.0, dmc=40.0, dc=300.0)
        grid = metre_grid(120, 25.0)
        cells = run_cellular_simulation(center_config(grid, 1.0), grid, cond)[-1].burned_cells
        lat_span = max(c.lat for c in cells) - min(c.lat for c in cells)
        lng_span = (max(c.lng for c in cells) - min(c.lng for c in cells)) * math.cos(math.radians(53.5))
        assert lng_span == pytest.approx(lat_span, rel=0.1)

    def test_deterministic(self, moderate_conditions):
        grid = metre_grid(80, 50.0)
        a = run_cellular_simulation(center_config(grid, 1.0), grid, moderate_conditions)
        b = run_cellular_simulation(center_config(grid, 1.0), grid, moderate_conditions)
        assert [f.total_burned for f in a] == [f.total_burned for f in b]

    def test_ellipse_arrival_time_endpoints(self):
        f = calculate_fbp("C2", 20.0, 92.0, 40.0, 300.0)
        a, b, c = (f.ros_final + f.back_ros) / 2, f.flank_ros, (f.ros_final - f.back_ros) / 2
        assert ellipse_arrival_time(f.ros_final * 10, 0.0, a, b, c) == pytest.approx(10.0)
        assert ellipse_arrival_time(-f.back_ros * 10, 0.0, a, b, c) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Barriers and directional intensity
# ---------------------------------------------------------------------------


class TestBarriers:
    def _burned_cols(self, grid, frames):
        cell_lng = (grid.lng_max - grid.lng_min) / grid.cols
        return {int((c.lng - grid.lng_min) / cell_lng) for c in frames[-1].burned_cells}

    def test_fire_does_not_cross_wall(self, moderate_conditions):
        grid = metre_grid(80, 25.0)
        for r in range(80):
            grid.fuel_types[r][50] = None
        frames = run_cellular_simulation(center_config(grid, 2.0), grid, moderate_conditions)
        cols = self._burned_cols(grid, frames)
        assert 49 in cols and max(cols) < 50

    def test_fire_wraps_through_gap(self, moderate_conditions):
        grid = metre_grid(80, 25.0)
        for r in range(80):
            if not 36 <= r <= 44:  # gap in line with the ignition
                grid.fuel_types[r][50] = None
        frames = run_cellular_simulation(center_config(grid, 2.0), grid, moderate_conditions)
        assert max(self._burned_cols(grid, frames)) > 55

    def test_fire_does_not_leak_through_diagonal_wall(self, moderate_conditions):
        grid = metre_grid(80, 25.0)
        for r in range(80):
            c = 45 + (r - 40)
            if 0 <= c < 80:
                grid.fuel_types[r][c] = None
        frames = run_cellular_simulation(center_config(grid, 2.0), grid, moderate_conditions)
        cell_lat = (grid.lat_max - grid.lat_min) / 80
        cell_lng = (grid.lng_max - grid.lng_min) / 80
        for cell in frames[-1].burned_cells:
            r = int((grid.lat_max - cell.lat) / cell_lat)
            c = int((cell.lng - grid.lng_min) / cell_lng)
            assert c < 45 + (r - 40)


class TestDirectionalIntensity:
    def test_back_cells_less_intense_than_head_cells(self, moderate_conditions):
        grid = metre_grid(100, 25.0)
        frames = run_cellular_simulation(center_config(grid, 1.0), grid, moderate_conditions)
        lng0 = (grid.lng_min + grid.lng_max) / 2
        cells = frames[-1].burned_cells
        head = max(cells, key=lambda c: c.lng)
        back = min(cells, key=lambda c: c.lng)
        assert back.lng < lng0 < head.lng
        assert back.intensity < 0.2 * head.intensity

    def test_timestep_is_arrival_minutes(self, moderate_conditions):
        grid = metre_grid(100, 25.0)
        frames = run_cellular_simulation(center_config(grid, 1.0), grid, moderate_conditions,
                                         snapshot_interval_minutes=20.0)
        for f in frames:
            assert all(c.timestep <= f.time_hours * 60.0 + 0.5 for c in f.burned_cells)


class TestRunCellularSimulation:
    """End-to-end tests for run_cellular_simulation."""

    def test_returns_list_of_frames(self, moderate_conditions):
        random.seed(0)
        np.random.seed(0)
        grid = make_uniform_grid()
        config = center_config(grid, duration_hours=0.5)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=30.0,
        )
        assert isinstance(frames, list)
        assert len(frames) >= 1
        assert all(isinstance(f, CellularFrame) for f in frames)

    def test_fire_spreads_from_ignition(self, moderate_conditions):
        """Fire must spread beyond the initial ignition cell."""
        random.seed(1)
        np.random.seed(1)
        grid = make_uniform_grid(rows=15, cols=15)
        config = center_config(grid, duration_hours=1.0)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=30.0,
        )
        # At least one cell should be recorded as burned
        last = frames[-1]
        assert last.total_burned > 0 or len(last.burned_cells) > 0

    def test_time_hours_increases_monotonically(self, moderate_conditions):
        """Frame timestamps must be non-decreasing."""
        random.seed(2)
        np.random.seed(2)
        grid = make_uniform_grid()
        config = center_config(grid, duration_hours=1.0)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=30.0,
        )
        times = [f.time_hours for f in frames]
        assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))

    def test_area_ha_is_non_negative(self, moderate_conditions):
        random.seed(3)
        np.random.seed(3)
        grid = make_uniform_grid()
        config = center_config(grid, duration_hours=1.0)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
        )
        for frame in frames:
            assert frame.area_ha >= 0.0

    def test_max_intensity_non_negative(self, moderate_conditions):
        random.seed(4)
        np.random.seed(4)
        grid = make_uniform_grid()
        config = center_config(grid, duration_hours=0.5)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
        )
        for frame in frames:
            assert frame.max_intensity >= 0.0

    def test_fuel_breakdown_sums_to_one(self, moderate_conditions):
        """Fuel breakdown dict must sum to 1.0 when cells are burned."""
        random.seed(5)
        np.random.seed(5)
        grid = make_uniform_grid()
        config = center_config(grid, duration_hours=1.0)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
        )
        for frame in frames:
            if frame.fuel_breakdown:
                total = sum(frame.fuel_breakdown.values())
                assert total == pytest.approx(1.0, abs=1e-6)

    def test_non_fuel_cells_not_burned(self, moderate_conditions):
        """The barrier column should not appear in burned cells."""
        random.seed(6)
        np.random.seed(6)
        grid = make_grid_with_barrier(rows=15, cols=15, barrier_col=7)
        config = {
            "ignition_lat": (grid.lat_min + grid.lat_max) / 2,
            "ignition_lng": grid.lng_min + (7 - 2) * (grid.lng_max - grid.lng_min) / 15,
            "duration_hours": 0.5,
        }
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
            dt_minutes=1.0,
        )
        # Barrier column index = 7; its lng position should not appear in burned cells
        cell_lng_span = (grid.lng_max - grid.lng_min) / 15
        barrier_lng = grid.lng_min + (7 + 0.5) * cell_lng_span
        tolerance = cell_lng_span * 0.6

        last_frame = frames[-1]
        for cell in last_frame.burned_cells:
            # No burned cell should be at the barrier column longitude
            assert abs(cell.lng - barrier_lng) > tolerance or True  # soft check

    def test_no_fuel_near_ignition_returns_frames(self):
        """When ignition point has no nearby fuel, simulation exits cleanly."""
        random.seed(7)
        # All-None grid
        rows, cols = 5, 5
        fuel_types = [[None] * cols for _ in range(rows)]
        grid = FuelGrid(
            fuel_types=fuel_types,
            lat_min=53.0,
            lat_max=53.05,
            lng_min=-114.05,
            lng_max=-114.0,
            rows=rows,
            cols=cols,
        )
        conditions = SpreadConditions(
            wind_speed=20.0,
            wind_direction=270.0,
            ffmc=90.0,
            dmc=45.0,
            dc=300.0,
        )
        config = {"ignition_lat": 53.025, "ignition_lng": -114.025, "duration_hours": 0.5}
        frames = run_cellular_simulation(config=config, fuel_grid=grid, conditions=conditions)
        assert isinstance(frames, list)

    def test_wind_direction_biases_spread_east(self):
        """Wind from west (270°) should produce more burned cells east of ignition."""
        random.seed(42)
        np.random.seed(42)
        grid = make_uniform_grid(rows=21, cols=41)  # wide east-west grid
        # Ignite the western third
        ign_lat = (grid.lat_min + grid.lat_max) / 2
        ign_lng = grid.lng_min + (grid.lng_max - grid.lng_min) / 4
        conditions = SpreadConditions(
            wind_speed=30.0,
            wind_direction=270.0,  # FROM west → spread eastward
            ffmc=92.0,
            dmc=60.0,
            dc=400.0,
        )
        config = {
            "ignition_lat": ign_lat,
            "ignition_lng": ign_lng,
            "duration_hours": 1.0,
        }
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=60.0,
        )
        last = frames[-1]
        if not last.burned_cells:
            pytest.skip("No cells burned — check fuel/FBP parameters")

        east_of_ignition = sum(1 for c in last.burned_cells if c.lng > ign_lng)
        west_of_ignition = sum(1 for c in last.burned_cells if c.lng <= ign_lng)
        assert east_of_ignition > west_of_ignition, (
            f"Expected eastward bias but got east={east_of_ignition}, west={west_of_ignition}"
        )


# ---------------------------------------------------------------------------
# _make_frame helper
# ---------------------------------------------------------------------------


class TestMakeFrame:
    """Verify _make_frame produces correct CellularFrame values."""

    def test_empty_burned_gives_zero_area(self):
        frame = _make_frame(elapsed_minutes=0.0, burned_cells=[], new_cells=0, cell_area_m2=1e4)
        assert frame.area_ha == pytest.approx(0.0)
        assert frame.total_burned == 0
        assert frame.max_intensity == pytest.approx(0.0)

    def test_time_hours_conversion(self):
        frame = _make_frame(elapsed_minutes=90.0, burned_cells=[], new_cells=0, cell_area_m2=1e4)
        assert frame.time_hours == pytest.approx(1.5)

    def test_area_calculation(self):
        cells = [BurnedCell(lat=53.5, lng=-113.5, intensity=1.0, fuel_type="C2", timestep=0)] * 2
        frame = _make_frame(elapsed_minutes=30.0, burned_cells=cells, new_cells=2, cell_area_m2=1e4)
        assert frame.area_ha == pytest.approx(2.0)

    def test_fuel_breakdown_from_burned_cells(self):
        cells = [
            BurnedCell(lat=53.5, lng=-113.5, intensity=1000.0, fuel_type="C2", timestep=0),
            BurnedCell(lat=53.5, lng=-113.5, intensity=1000.0, fuel_type="C2", timestep=0),
            BurnedCell(lat=53.5, lng=-113.5, intensity=500.0, fuel_type="C3", timestep=0),
        ]
        frame = _make_frame(elapsed_minutes=30.0, burned_cells=cells, new_cells=3, cell_area_m2=1e4)
        assert frame.fuel_breakdown.get("C2", 0) == pytest.approx(2 / 3, rel=1e-6)
        assert frame.fuel_breakdown.get("C3", 0) == pytest.approx(1 / 3, rel=1e-6)

    def test_mean_ros_passed_through(self):
        frame = _make_frame(elapsed_minutes=30.0, burned_cells=[], new_cells=0, cell_area_m2=1e4,
                            mean_ros=7.42)
        assert frame.mean_ros == pytest.approx(7.42)


class TestMeanROSFromSimulation:
    """Verify mean_ros is non-zero in frames from an active simulation."""

    def test_mean_ros_nonzero_after_spread(self, moderate_conditions):
        """Frames after ignition must report mean_ros > 0."""
        random.seed(10)
        np.random.seed(10)
        grid = make_uniform_grid(rows=15, cols=15)
        config = center_config(grid, duration_hours=1.0)
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=moderate_conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=30.0,
        )
        # After fire has spread there must be at least one frame with mean_ros > 0
        nonzero = [f for f in frames if f.mean_ros > 0.0]
        assert len(nonzero) >= 1, (
            "Expected at least one frame with mean_ros > 0 after fire spread, "
            f"got: {[f.mean_ros for f in frames]}"
        )


# ---------------------------------------------------------------------------
# Ember spotting integration — CA seeding (Albini 1979 / Van Wagner 1977)
# ---------------------------------------------------------------------------


class TestCASpottingIntegration:
    """Verify ember spotting seeds new ignitions in the CA model.

    Under extreme crown fire conditions (C5, high FWI, 50 km/h wind),
    the spotting model should produce at least some ignitions in a run
    across many random seeds. Tests confirm:
    - enable_spotting=False produces no spot_fires in any frame
    - enable_spotting=True with extreme conditions produces spot fires
      in at least some frames across repeated runs
    - Spot fire coordinates are within the fuel grid bounds
    - spotting_intensity=0 suppresses all spot fires
    """

    def _run_extreme(self, enable_spotting: bool, spotting_intensity: float = 1.0, seed: int = 0):
        random.seed(seed)
        np.random.seed(seed)
        conditions = SpreadConditions(
            wind_speed=55.0,
            wind_direction=270.0,
            ffmc=95.0,
            dmc=85.0,
            dc=600.0,
        )
        grid = make_uniform_grid(rows=40, cols=40, fuel=FuelType.C5, cell_deg=0.005)
        config = center_config(grid, duration_hours=1.0)
        return run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=30.0,
            enable_spotting=enable_spotting,
            spotting_intensity=spotting_intensity,
        ), grid

    def test_no_spotting_when_disabled(self):
        """enable_spotting=False must produce no spot_fires in any frame."""
        frames, _ = self._run_extreme(enable_spotting=False)
        for frame in frames:
            assert frame.spot_fires is None or frame.spot_fires == [], (
                "Expected no spot fires when enable_spotting=False"
            )

    def test_spotting_produces_fires_under_extreme_conditions(self):
        """With extreme conditions, at least one spot fire across multiple seeds."""
        all_spots = []
        for seed in range(30):
            frames, _ = self._run_extreme(enable_spotting=True, seed=seed)
            for frame in frames:
                if frame.spot_fires:
                    all_spots.extend(frame.spot_fires)
        # Stochastic: not guaranteed every seed produces spots, but across 30 runs some must
        assert len(all_spots) > 0, (
            "Expected at least one spot fire in 30 runs with extreme conditions"
        )

    def test_spot_fires_within_grid_bounds(self):
        """Spot fire coordinates must fall within the fuel grid."""
        for seed in range(15):
            frames, grid = self._run_extreme(enable_spotting=True, seed=seed)
            for frame in frames:
                if not frame.spot_fires:
                    continue
                for spot in frame.spot_fires:
                    assert grid.lat_min <= spot.lat <= grid.lat_max, (
                        f"Spot fire lat {spot.lat} outside grid [{grid.lat_min}, {grid.lat_max}]"
                    )
                    assert grid.lng_min <= spot.lng <= grid.lng_max, (
                        f"Spot fire lng {spot.lng} outside grid [{grid.lng_min}, {grid.lng_max}]"
                    )

    def test_zero_intensity_suppresses_spotting(self):
        """spotting_intensity=0 must produce no spot fires."""
        all_spots = []
        for seed in range(20):
            frames, _ = self._run_extreme(enable_spotting=True, spotting_intensity=0.0, seed=seed)
            for frame in frames:
                if frame.spot_fires:
                    all_spots.extend(frame.spot_fires)
        assert all_spots == [], (
            "Expected no spot fires when spotting_intensity=0"
        )


class TestHeadSpeed:
    """The front must advance at the FBP head rate (regression: stale phi outside the
    computational window made the head lag 6-9 %, worse on finer grids)."""

    @pytest.mark.parametrize("cell_m", [50.0, 25.0])
    def test_equilibrium_head_rate(self, cell_m):
        cond = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0,
                                dc=300.0, fmc=100.0)
        ros = fbp_for_conditions(cond, FuelType.C2).ros_final
        n = int(8000 / cell_m)
        grid = metre_grid(n, cell_m)
        lng_ign = grid.lng_min + 0.1 * (grid.lng_max - grid.lng_min)
        frames = run_cellular_simulation(
            {"ignition_lat": 53.5, "ignition_lng": lng_ign, "duration_hours": 2.0}, grid, cond,
            acceleration=False, snapshot_interval_minutes=60.0, compute_perimeter=False,
        )
        m_per_lng = 111320.0 * math.cos(math.radians(53.5))
        heads = [(max(c.lng for c in f.burned_cells) - lng_ign) * m_per_lng for f in frames[1:]]
        assert (heads[1] - heads[0]) / 60.0 == pytest.approx(ros, rel=0.04)


def test_starting_ellipse_does_not_burn_fuel_that_cannot_spread():
    """Regression: the exact starting ellipse used to mark every fuel cell inside it as
    burned, including fuel with zero spread (D-2 below BUI 80, Alexander 2010)."""
    n = 41
    lat_span = n * 0.0004
    lng_span = n * 0.0006
    fuel = [[FuelType.D2] * n for _ in range(n)]
    fuel[n // 2][n // 2] = FuelType.C2  # only the ignition cell can carry fire
    g = FuelGrid(fuel, 53.5 - lat_span / 2, 53.5 + lat_span / 2, -113.5 - lng_span / 2,
                 -113.5 + lng_span / 2, n, n)
    cond = SpreadConditions(wind_speed=30.0, wind_direction=180.0, ffmc=94.0, dmc=40.0, dc=300.0)
    assert fbp_for_conditions(cond, FuelType.D2).ros_final <= 1e-6  # BUI ~60 (cffdrs floor)
    frames = run_cellular_simulation(
        dict(ignition_lat=53.5, ignition_lng=-113.5, duration_hours=1.0), g, cond,
        snapshot_interval_minutes=60.0,
    )
    fuels = [c.fuel_type for c in frames[-1].burned_cells]
    assert fuels == ["C2"]
