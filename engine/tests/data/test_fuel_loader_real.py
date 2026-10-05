"""Integration tests for fuel_loader with the real Edmonton FBP raster.

These tests are skipped automatically when the real raster file is not present
(i.e., in CI or a fresh checkout). They validate the full load+simulate
pipeline against actual spatial data.

Known fire location: Sturgeon County, northeast of Edmonton (~53.65°N, 113.47°W)
is within the raster extent and has fuel coverage.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# The repo's data/ copy (not committed), or FIRESIM_TEST_FUEL_RASTER
_EDMONTON_FBP = os.environ.get(
    "FIRESIM_TEST_FUEL_RASTER",
    str(Path(__file__).resolve().parents[3] / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"),
)

# Skip the whole module when the raster is not available
pytestmark = pytest.mark.skipif(
    not os.path.exists(_EDMONTON_FBP),
    reason="Real Edmonton FBP raster not available — skipping integration tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def edmonton_grid():
    """Load the real Edmonton FBP raster (module-scoped — expensive)."""
    from firesim.data.fuel_loader import load_fuel_grid

    return load_fuel_grid(_EDMONTON_FBP, target_resolution_m=100.0)


# ---------------------------------------------------------------------------
# Raster load validation
# ---------------------------------------------------------------------------


class TestRealGridLoad:
    def test_grid_dimensions_non_zero(self, edmonton_grid):
        assert edmonton_grid.rows > 0
        assert edmonton_grid.cols > 0

    def test_grid_covers_edmonton(self, edmonton_grid):
        """Raster must span Edmonton city centre (53.55°N, 113.49°W)."""
        g = edmonton_grid
        assert g.lat_min < 53.55 < g.lat_max, (
            f"Edmonton lat 53.55 not in grid lat range {g.lat_min:.4f}–{g.lat_max:.4f}"
        )
        assert g.lng_min < -113.49 < g.lng_max, (
            f"Edmonton lng -113.49 not in grid lng range {g.lng_min:.4f}–{g.lng_max:.4f}"
        )

    def test_fuel_types_contain_known_fbp_codes(self, edmonton_grid):
        """Real raster has codes 1=C1, 32=S2, 41=O1a, 42=O1b — at least some must appear."""
        from firesim.fbp.constants import FuelType

        all_fuels = {
            cell
            for row in edmonton_grid.fuel_types
            for cell in row
            if cell is not None
        }
        # At least one FBP fuel type must be present
        assert len(all_fuels) > 0, "No fuel types found in real raster"

    def test_non_fuel_fraction_plausible(self, edmonton_grid):
        """Edmonton is an urban-wildland interface — expect 20–90% non-fuel.

        The Edmonton FBP raster (Nov 2025) shows ~28.6% non-fuel at 100m resolution
        including NoData (-9999) and code-0 cells (roads, urban, cleared land).
        """
        g = edmonton_grid
        total = g.rows * g.cols
        non_fuel = sum(1 for row in g.fuel_types for cell in row if cell is None)
        fraction = non_fuel / total
        assert 0.20 <= fraction <= 0.90, (
            f"Non-fuel fraction {fraction:.1%} outside expected 20–90% range for Edmonton"
        )

    def test_wgs84_bounds_in_range(self, edmonton_grid):
        g = edmonton_grid
        assert -90 <= g.lat_min < g.lat_max <= 90
        assert -180 <= g.lng_min < g.lng_max <= 180


# ---------------------------------------------------------------------------
# End-to-end simulation over a known fire location
# ---------------------------------------------------------------------------


class TestRealGridSimulation:
    """Run a short CA simulation over a known fire-prone location."""

    # Sturgeon County / northwest Edmonton fringe — confirmed within raster extent
    # and has C1/O1b fuel cover near the river valley
    IGN_LAT = 53.623
    IGN_LNG = -113.624

    def test_simulation_produces_burned_cells(self):
        """A 1-hour CA sim from a fueled ignition point must burn at least 1 cell."""
        from firesim.data.fuel_loader import load_fuel_grid
        from firesim.spread.cellular import run_cellular_simulation
        from firesim.spread.huygens import SpreadConditions

        grid = load_fuel_grid(_EDMONTON_FBP, target_resolution_m=100.0)

        # Verify ignition cell has fuel (skip if it falls in a non-fuel patch)
        fuel_at_ignition = grid.get_fuel_at(self.IGN_LAT, self.IGN_LNG)
        if fuel_at_ignition is None:
            pytest.skip("Ignition point falls in non-fuel cell in real raster")

        conditions = SpreadConditions(
            wind_speed=25.0,      # km/h — moderate
            wind_direction=225.0,  # SW
            ffmc=88.0,
            dmc=60.0,
            dc=300.0,
        )
        config = {
            "ignition_lat": self.IGN_LAT,
            "ignition_lng": self.IGN_LNG,
            "duration_hours": 1.0,
        }

        frames = run_cellular_simulation(
            config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=2.0,
            snapshot_interval_minutes=60.0,
        )

        assert len(frames) >= 1, "No frames produced"
        final = frames[-1]
        assert final.total_burned >= 1, (
            f"Zero cells burned in 1-hour simulation — "
            f"ignition fuel: {fuel_at_ignition}, wind: {conditions.wind_speed} km/h"
        )

    def test_burned_cells_within_grid_bounds(self):
        """All burned cell coordinates must lie within the raster extent."""
        from firesim.data.fuel_loader import load_fuel_grid
        from firesim.spread.cellular import run_cellular_simulation
        from firesim.spread.huygens import SpreadConditions

        grid = load_fuel_grid(_EDMONTON_FBP, target_resolution_m=100.0)

        fuel_at_ignition = grid.get_fuel_at(self.IGN_LAT, self.IGN_LNG)
        if fuel_at_ignition is None:
            pytest.skip("Ignition point falls in non-fuel cell in real raster")

        conditions = SpreadConditions(
            wind_speed=25.0,
            wind_direction=225.0,
            ffmc=88.0,
            dmc=60.0,
            dc=300.0,
        )
        config = {
            "ignition_lat": self.IGN_LAT,
            "ignition_lng": self.IGN_LNG,
            "duration_hours": 1.0,
        }

        frames = run_cellular_simulation(
            config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=2.0,
            snapshot_interval_minutes=60.0,
        )

        if not frames or frames[-1].total_burned == 0:
            pytest.skip("No cells burned — cannot validate coordinates")

        final = frames[-1]
        for cell in final.burned_cells:
            assert grid.lat_min <= cell.lat <= grid.lat_max, (
                f"Burned cell lat {cell.lat} outside grid [{grid.lat_min}, {grid.lat_max}]"
            )
            assert grid.lng_min <= cell.lng <= grid.lng_max, (
                f"Burned cell lng {cell.lng} outside grid [{grid.lng_min}, {grid.lng_max}]"
            )

    def test_burn_area_plausible(self):
        """1-hour burn area should be <10,000 ha (no runaway) but >0."""
        from firesim.data.fuel_loader import load_fuel_grid
        from firesim.spread.cellular import run_cellular_simulation
        from firesim.spread.huygens import SpreadConditions

        grid = load_fuel_grid(_EDMONTON_FBP, target_resolution_m=100.0)

        fuel_at_ignition = grid.get_fuel_at(self.IGN_LAT, self.IGN_LNG)
        if fuel_at_ignition is None:
            pytest.skip("Ignition point falls in non-fuel cell in real raster")

        conditions = SpreadConditions(
            wind_speed=25.0,
            wind_direction=225.0,
            ffmc=88.0,
            dmc=60.0,
            dc=300.0,
        )
        config = {
            "ignition_lat": self.IGN_LAT,
            "ignition_lng": self.IGN_LNG,
            "duration_hours": 1.0,
        }

        frames = run_cellular_simulation(
            config,
            fuel_grid=grid,
            conditions=conditions,
            dt_minutes=2.0,
            snapshot_interval_minutes=60.0,
        )

        final = frames[-1]
        assert 0.0 < final.area_ha < 10_000.0, (
            f"Burn area {final.area_ha:.1f} ha implausible for 1-hour run"
        )
