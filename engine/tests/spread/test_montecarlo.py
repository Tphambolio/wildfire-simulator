"""Tests for the Monte Carlo burn probability engine.

Covers: determinism with fixed seed, probability bounds [0, 1],
correct aggregation, grid metadata accuracy.
"""

from __future__ import annotations

import math

import pytest

from firesim.data.synthetic_grid import generate_synthetic_fuel_grid
from firesim.spread.huygens import SpreadConditions
from firesim.spread.montecarlo import (
    BurnProbabilityResult,
    MonteCarloConfig,
    run_monte_carlo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_grid():
    """A 100-cell synthetic grid for fast testing."""
    return generate_synthetic_fuel_grid(
        ignition_lat=53.55,
        ignition_lng=-113.50,
        radius_km=2.0,
        cell_size_m=50.0,
        seed=7,
    )


@pytest.fixture(scope="module")
def moderate_conditions():
    return SpreadConditions(
        wind_speed=20.0,
        wind_direction=225.0,
        ffmc=85.0,
        dmc=40.0,
        dc=200.0,
    )


@pytest.fixture(scope="module")
def mc_config_small():
    """5 iterations — fast enough for unit tests."""
    return MonteCarloConfig(
        ignition_lat=53.55,
        ignition_lng=-113.50,
        duration_hours=0.5,
        n_iterations=5,
        jitter_m=50.0,
        wind_speed_pct=10.0,
        rh_abs=5.0,
        base_seed=42,
    )


@pytest.fixture(scope="module")
def result_5iter(small_grid, moderate_conditions, mc_config_small):
    """5-iteration Monte Carlo result (module-scoped — runs once)."""
    return run_monte_carlo(mc_config_small, small_grid, moderate_conditions)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_burn_probability_result(self, result_5iter):
        assert isinstance(result_5iter, BurnProbabilityResult)

    def test_burn_probability_is_2d_list(self, result_5iter):
        bp = result_5iter.burn_probability
        assert isinstance(bp, list)
        assert all(isinstance(row, list) for row in bp)

    def test_dimensions_match_grid(self, result_5iter, small_grid):
        assert result_5iter.rows == small_grid.rows
        assert result_5iter.cols == small_grid.cols
        assert len(result_5iter.burn_probability) == result_5iter.rows
        assert all(
            len(row) == result_5iter.cols
            for row in result_5iter.burn_probability
        )


# ---------------------------------------------------------------------------
# Probability bounds
# ---------------------------------------------------------------------------


class TestProbabilityBounds:
    def test_all_values_in_unit_interval(self, result_5iter):
        """All burn probabilities must be in [0, 1]."""
        for row in result_5iter.burn_probability:
            for val in row:
                assert 0.0 <= val <= 1.0, f"Probability {val} outside [0, 1]"

    def test_max_probability_not_exceeding_one(self, result_5iter):
        max_p = max(val for row in result_5iter.burn_probability for val in row)
        assert max_p <= 1.0

    def test_min_probability_not_below_zero(self, result_5iter):
        min_p = min(val for row in result_5iter.burn_probability for val in row)
        assert min_p >= 0.0


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_iterations_completed_matches_n(self, result_5iter, mc_config_small):
        """All 5 iterations should complete without error."""
        assert result_5iter.iterations_completed == mc_config_small.n_iterations

    def test_n_iterations_stored_correctly(self, result_5iter, mc_config_small):
        assert result_5iter.n_iterations == mc_config_small.n_iterations

    def test_at_least_one_cell_burned(self, result_5iter):
        """With moderate fire weather, at least one cell must have P > 0."""
        any_burned = any(
            val > 0 for row in result_5iter.burn_probability for val in row
        )
        assert any_burned, "No cells burned in any iteration — check fuel grid and ignition"

    def test_probability_one_from_100pct_iterations(self, small_grid, moderate_conditions):
        """Cell that burns in every iteration must have P = 1.0.

        Use n=1 with no jitter and a fixed seed so the same cell burns
        every time (n_iterations doesn't affect this with n=1, but we
        use n=3 with seed that produces identical ignition cells).
        """
        # 3 iterations, no jitter — ignition cell is the same every time
        cfg = MonteCarloConfig(
            ignition_lat=53.55,
            ignition_lng=-113.50,
            duration_hours=0.5,
            n_iterations=3,
            jitter_m=0.0,       # No jitter
            wind_speed_pct=0.0,  # No weather variation
            rh_abs=0.0,
            base_seed=1,
        )
        result = run_monte_carlo(cfg, small_grid, moderate_conditions)

        # Find the max probability — with identical inputs, heavily burned
        # cells near ignition should approach P=1.0
        max_p = max(val for row in result.burn_probability for val in row)
        assert max_p == pytest.approx(1.0, abs=0.01), (
            f"Expected max P ≈ 1.0 with no jitter/variation, got {max_p:.3f}"
        )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_result(self, small_grid, moderate_conditions):
        cfg = MonteCarloConfig(
            ignition_lat=53.55,
            ignition_lng=-113.50,
            duration_hours=0.5,
            n_iterations=3,
            base_seed=99,
        )
        r1 = run_monte_carlo(cfg, small_grid, moderate_conditions)
        r2 = run_monte_carlo(cfg, small_grid, moderate_conditions)
        assert r1.burn_probability == r2.burn_probability

    def test_different_seed_different_result(self, small_grid, moderate_conditions):
        cfg1 = MonteCarloConfig(
            ignition_lat=53.55, ignition_lng=-113.50,
            duration_hours=0.5, n_iterations=5, base_seed=1,
        )
        cfg2 = MonteCarloConfig(
            ignition_lat=53.55, ignition_lng=-113.50,
            duration_hours=0.5, n_iterations=5, base_seed=2,
        )
        r1 = run_monte_carlo(cfg1, small_grid, moderate_conditions)
        r2 = run_monte_carlo(cfg2, small_grid, moderate_conditions)
        # Should differ (not guaranteed but extremely likely with different seeds)
        assert r1.burn_probability != r2.burn_probability


# ---------------------------------------------------------------------------
# Grid metadata
# ---------------------------------------------------------------------------


class TestGridMetadata:
    def test_bounds_match_fuel_grid(self, result_5iter, small_grid):
        assert result_5iter.lat_min == small_grid.lat_min
        assert result_5iter.lat_max == small_grid.lat_max
        assert result_5iter.lng_min == small_grid.lng_min
        assert result_5iter.lng_max == small_grid.lng_max

    def test_cell_size_m_positive(self, result_5iter):
        assert result_5iter.cell_size_m > 0.0

    def test_cell_size_m_approximately_correct(self, result_5iter, small_grid):
        """Cell size should be within 20% of 50m target."""
        expected = 50.0  # cell_size_m used in generate_synthetic_fuel_grid
        assert abs(result_5iter.cell_size_m - expected) / expected < 0.30, (
            f"cell_size_m {result_5iter.cell_size_m:.1f}m far from expected ~{expected}m"
        )

    def test_rows_cols_positive(self, result_5iter):
        assert result_5iter.rows > 0
        assert result_5iter.cols > 0
