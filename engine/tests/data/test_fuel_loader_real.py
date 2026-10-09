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


# West Edmonton, south-west Edmonton and north-east fringe grassland (O-1a)
_IGNITIONS = [(53.45586, -113.67106), (53.38669, -113.62875), (53.67684, -113.41265)]
_CONDITIONS = dict(wind_speed=25.0, wind_direction=270.0, ffmc=88.0, dmc=60.0, dc=300.0)


@pytest.fixture(scope="module")
def runs(edmonton_grid):
    """One-hour grid-model runs from each of ``_IGNITIONS``."""
    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.huygens import SpreadConditions

    cond = SpreadConditions(**_CONDITIONS)
    return [
        run_cellular_simulation(
            {"ignition_lat": lat, "ignition_lng": lng, "duration_hours": 1.0},
            fuel_grid=edmonton_grid, conditions=cond, snapshot_interval_minutes=60.0,
        )
        for lat, lng in _IGNITIONS
    ]


class TestRealGridSimulation:
    """Run a short grid-model simulation from known fuel cells of the real raster.

    The ignition points were picked (2026-10-08) from the 100 m grid as produced by the
    reprojecting loader: each is O-1a with O-1a in the 3 x 13 block of cells around it
    (one row either side, 2 cells upwind, 10 cells downwind), so a west wind can carry the
    fire about 1 km before it meets other fuel. ``test_ignitions_are_on_fuel`` guards that
    choice; if the raster or the loader changes, re-pick the points rather than skip.
    The wind is due west, chosen (2026-10-08) while point ignitions in narrow (grass)
    ellipses were badly under-spread with the wind off the grid axes; fixed 2026-10-09 (see
    ``engine/tests/spread/test_cellular.py::TestRotationalInvariance``).
    """

    IGNITIONS = _IGNITIONS
    CONDITIONS = _CONDITIONS

    def test_ignitions_are_on_fuel(self, edmonton_grid):
        from firesim.fbp.constants import FuelType

        for lat, lng in self.IGNITIONS:
            assert edmonton_grid.get_fuel_at(lat, lng) == FuelType.O1a, (lat, lng)

    def test_fire_spreads_from_the_ignition_cell(self, runs):
        """No snapping, and the fire leaves the ignition cell within the hour."""
        for frames in runs:
            final = frames[-1]
            assert final.ignition_snapped_m == 0.0
            assert final.total_burned >= 3
            assert final.area_ha == pytest.approx(final.total_burned * 1.0, rel=0.01)  # 1 ha cells

    def test_burned_cells_are_fuel_inside_the_grid(self, runs, edmonton_grid):
        g = edmonton_grid
        for frames in runs:
            for cell in frames[-1].burned_cells:
                assert g.lat_min <= cell.lat <= g.lat_max
                assert g.lng_min <= cell.lng <= g.lng_max
                fuel = g.get_fuel_at(cell.lat, cell.lng)
                assert fuel is not None and fuel.value == cell.fuel_type

    def test_head_runs_downwind_at_the_fbp_rate(self, runs):
        """West wind: the fire runs east, no further than the FBP head rate allows (O-1a,
        ST-X-3) and at least half of it, and backs west by less than one cell."""
        import math

        from firesim.fbp.constants import FuelType
        from firesim.spread.huygens import SpreadConditions, fbp_for_conditions

        ros = fbp_for_conditions(SpreadConditions(**self.CONDITIONS), FuelType.O1a).ros_final
        for (lat, lng), frames in zip(self.IGNITIONS, runs):
            m_per_lng = 111320.0 * math.cos(math.radians(lat))
            east = max((c.lng - lng) * m_per_lng for c in frames[-1].burned_cells)
            west = max((lng - c.lng) * m_per_lng for c in frames[-1].burned_cells)
            assert 0.5 * ros * 60.0 <= east <= ros * 60.0, (east, ros)
            assert west < 100.0
