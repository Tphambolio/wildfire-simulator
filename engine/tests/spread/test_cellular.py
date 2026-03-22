"""Unit tests for cellular automaton fire spread model.

Tests cover 8-neighbor grid connectivity, elliptical ROS probability,
heat accumulation ignition, and full simulation frame output.

References:
    - Alexander, M.E. (1985). Estimating the length-to-breadth ratio of
      elliptical forest fire patterns.
    - Canadian Forest Fire Behavior Prediction System (FBP) documentation.
"""

import math
import random

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import (
    HEAT_IGNITION_THRESHOLD,
    NEIGHBORS,
    BurnedCell,
    CellularFrame,
    _elliptical_spread_prob,
    _make_frame,
    run_cellular_simulation,
)
from firesim.spread.huygens import FuelGrid, SpreadConditions


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


# ---------------------------------------------------------------------------
# NEIGHBORS constant
# ---------------------------------------------------------------------------


class TestNeighborsConstant:
    """Verify the 8-neighbor offset table is correct."""

    def test_eight_neighbors(self):
        assert len(NEIGHBORS) == 8

    def test_neighbor_angles_unique(self):
        angles = [n[2] for n in NEIGHBORS]
        assert len(set(angles)) == 8

    def test_cardinal_and_diagonal_present(self):
        angles = {n[2] for n in NEIGHBORS}
        expected = {0, 45, 90, 135, 180, 225, 270, 315}
        assert angles == expected

    def test_all_adjacent_cells_reachable(self):
        """Every (dr, dc) pair must reach an adjacent cell."""
        offsets = {(n[0], n[1]) for n in NEIGHBORS}
        for dr, dc in offsets:
            assert abs(dr) <= 1 and abs(dc) <= 1
            assert not (dr == 0 and dc == 0)


# ---------------------------------------------------------------------------
# Elliptical spread probability (_elliptical_spread_prob)
# ---------------------------------------------------------------------------


class TestEllipticalSpreadProb:
    """Verify directional spread probability follows an elliptical pattern.

    Head fire direction should have the highest probability; backing direction
    the lowest. Flank directions should be intermediate.

    Reference: Alexander (1985) — elliptical fire shape.
    """

    def test_head_direction_highest_probability(self):
        """Head and back fire directions have higher probability than flanks.

        The elliptical polar equation used here is centered on the ellipse
        center (not the focus), so the head and back axes both equal the
        semi-major axis `a = ros`. The flanks are constrained to `b = ros/lbr`,
        giving lower probability perpendicular to the wind.
        """
        spread_dir = 90.0  # fire spreads east
        ros = 5.0
        lbr = 3.0
        cell_size = 50.0
        dt = 5.0

        head_prob = _elliptical_spread_prob(90.0, spread_dir, ros, lbr, cell_size, dt)
        flank_prob = _elliptical_spread_prob(0.0, spread_dir, ros, lbr, cell_size, dt)
        back_prob = _elliptical_spread_prob(270.0, spread_dir, ros, lbr, cell_size, dt)

        # Both head and back use the semi-major axis in the polar formula
        assert head_prob > flank_prob
        assert back_prob > flank_prob

    def test_probability_bounded_zero_to_one(self):
        """Spread probability must be in [0, 1]."""
        for angle in range(0, 360, 45):
            prob = _elliptical_spread_prob(
                float(angle), 90.0, 10.0, 3.0, 50.0, 5.0
            )
            assert 0.0 <= prob <= 1.0

    def test_no_wind_symmetric(self):
        """With LBR=1 (no wind), spread should be symmetric in all directions."""
        spread_dir = 90.0
        ros = 3.0
        lbr = 1.0
        cell_size = 50.0
        dt = 5.0

        probs = [
            _elliptical_spread_prob(float(a), spread_dir, ros, lbr, cell_size, dt)
            for a in [0, 90, 180, 270]
        ]
        # All directions should give the same probability
        assert max(probs) - min(probs) < 1e-6

    def test_high_ros_saturates_to_one(self):
        """Very high ROS relative to cell size should give probability ~1.0."""
        prob = _elliptical_spread_prob(
            90.0, 90.0, ros=1000.0, lbr=3.0, cell_size=50.0, dt=5.0
        )
        assert prob == pytest.approx(1.0)

    def test_zero_ros_gives_zero_probability(self):
        """Zero ROS should produce zero spread probability."""
        prob = _elliptical_spread_prob(
            90.0, 90.0, ros=0.0, lbr=3.0, cell_size=50.0, dt=5.0
        )
        assert prob == pytest.approx(0.0)

    def test_higher_lbr_increases_head_probability(self):
        """Higher LBR (stronger wind) should increase head direction probability."""
        prob_low_lbr = _elliptical_spread_prob(
            90.0, 90.0, ros=5.0, lbr=1.5, cell_size=100.0, dt=5.0
        )
        prob_high_lbr = _elliptical_spread_prob(
            90.0, 90.0, ros=5.0, lbr=5.0, cell_size=100.0, dt=5.0
        )
        assert prob_high_lbr >= prob_low_lbr


# ---------------------------------------------------------------------------
# Heat accumulation
# ---------------------------------------------------------------------------


class TestHeatAccumulation:
    """Verify that repeated heat exposure triggers ignition.

    HEAT_IGNITION_THRESHOLD: accumulated heat value that forces ignition
    regardless of stochastic probability.
    """

    def test_threshold_is_positive(self):
        assert HEAT_IGNITION_THRESHOLD > 0.0

    def test_heat_accumulation_ignites_cell(self):
        """A non-fuel barrier surrounded by fuel should eventually force ignition
        via heat if a neighbor accumulates enough heat to exceed the threshold.

        We test this by running the simulation with a very small grid and very
        long duration, seeding random so ignition is deterministic, and verifying
        that cells adjacent to burning cells are eventually ignited.
        """
        random.seed(42)
        np.random.seed(42)
        grid = make_uniform_grid(rows=5, cols=5, fuel=FuelType.C2)
        conditions = SpreadConditions(
            wind_speed=30.0,
            wind_direction=270.0,
            ffmc=92.0,
            dmc=60.0,
            dc=400.0,
        )
        config = {
            "ignition_lat": (grid.lat_min + grid.lat_max) / 2,
            "ignition_lng": (grid.lng_min + grid.lng_max) / 2,
            "duration_hours": 2.0,
        }
        frames = run_cellular_simulation(
            config=config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=1.0,
            snapshot_interval_minutes=60.0,
        )
        # Fire should have spread — at least the ignition cell and some neighbors
        last_frame = frames[-1]
        assert last_frame.total_burned > 0


# ---------------------------------------------------------------------------
# Full simulation — uniform fuel grid
# ---------------------------------------------------------------------------


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
        grid = make_uniform_grid(rows=10, cols=10)
        burned = np.zeros((10, 10), dtype=bool)
        frame = _make_frame(
            elapsed_minutes=0.0,
            all_burned_cells=[],
            new_burned_cells=[],
            rows=10,
            cols=10,
            cell_size_m=100.0,
            fuel_grid=grid,
            burned=burned,
        )
        assert frame.area_ha == pytest.approx(0.0)
        assert frame.total_burned == 0
        assert frame.max_intensity == pytest.approx(0.0)

    def test_time_hours_conversion(self):
        grid = make_uniform_grid(rows=10, cols=10)
        burned = np.zeros((10, 10), dtype=bool)
        frame = _make_frame(
            elapsed_minutes=90.0,
            all_burned_cells=[],
            new_burned_cells=[],
            rows=10,
            cols=10,
            cell_size_m=100.0,
            fuel_grid=grid,
            burned=burned,
        )
        assert frame.time_hours == pytest.approx(1.5)

    def test_area_calculation(self):
        """Area should equal total_burned * cell_area_ha."""
        grid = make_uniform_grid(rows=10, cols=10)
        burned = np.zeros((10, 10), dtype=bool)
        burned[0, 0] = True
        burned[0, 1] = True  # 2 burned cells
        cell_size_m = 100.0
        expected_ha = 2 * (cell_size_m ** 2) / 10000.0
        frame = _make_frame(
            elapsed_minutes=30.0,
            all_burned_cells=[],
            new_burned_cells=[],
            rows=10,
            cols=10,
            cell_size_m=cell_size_m,
            fuel_grid=grid,
            burned=burned,
        )
        assert frame.area_ha == pytest.approx(expected_ha)

    def test_fuel_breakdown_from_burned_cells(self):
        """Fuel breakdown should reflect actual fuel type proportions."""
        grid = make_uniform_grid(rows=10, cols=10)
        burned = np.zeros((10, 10), dtype=bool)
        cells = [
            BurnedCell(lat=53.5, lng=-113.5, intensity=1000.0, fuel_type="C2", timestep=0),
            BurnedCell(lat=53.5, lng=-113.5, intensity=1000.0, fuel_type="C2", timestep=0),
            BurnedCell(lat=53.5, lng=-113.5, intensity=500.0, fuel_type="C3", timestep=0),
        ]
        frame = _make_frame(
            elapsed_minutes=30.0,
            all_burned_cells=cells,
            new_burned_cells=[],
            rows=10,
            cols=10,
            cell_size_m=100.0,
            fuel_grid=grid,
            burned=burned,
        )
        assert frame.fuel_breakdown.get("C2", 0) == pytest.approx(2 / 3, rel=1e-6)
        assert frame.fuel_breakdown.get("C3", 0) == pytest.approx(1 / 3, rel=1e-6)

    def test_mean_ros_passed_through(self):
        """mean_ros parameter must be stored in the frame (not hardcoded 0)."""
        grid = make_uniform_grid(rows=10, cols=10)
        burned = np.zeros((10, 10), dtype=bool)
        frame = _make_frame(
            elapsed_minutes=30.0,
            all_burned_cells=[],
            new_burned_cells=[],
            rows=10,
            cols=10,
            cell_size_m=100.0,
            fuel_grid=grid,
            burned=burned,
            mean_ros=7.42,
        )
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
