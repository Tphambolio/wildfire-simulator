"""20 m WUI window (mechanics decision M5): native-grid crop, window run, guards, convergence.

The window repeats the grid run at the fuel raster's native cell size on a crop around the
50 m fire when buildings are near (``firesim.spread.wui_window``). These tests check:

- the crop: native cells, edges on native cell edges, building mask at the native size;
- the window logic: used near buildings, kept at 50 m without buildings, guards (too many
  cells, too many burned cells), growth when the fire reaches the crop edge, fallback;
- convergence: on uniform fuel the 20 m and 50 m runs agree with each other and with the FBP
  ellipse (the level set is unchanged; only the cell size differs);
- the memory bound per cell that sizes ``WindowOptions.max_cells``.
"""

from __future__ import annotations

import math
import tracemalloc

import numpy as np
import pytest
import shapely

from firesim.data.fine_grid import FineFuelSource
from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.ellipse import calculate_ellipse_area
from firesim.spread.huygens import FuelGrid, SpreadConditions, fbp_for_conditions
from firesim.spread.simulator import Simulator
from firesim.spread.wui_window import (
    NOTE_USED,
    WindowOptions,
    grow_box,
    reaches_edge,
    run_with_window,
)
from firesim.types import SimulationConfig, WeatherInput

M = 111320.0
LAT0, LNG0 = 53.5, -113.5


def metre_grid(n_rows: int, n_cols: int, cell_m: float, fuel=FuelType.C2) -> FuelGrid:
    """n_rows x n_cols grid of cell_m-metre cells centred on (LAT0, LNG0)."""
    dlat = n_rows * cell_m / M
    dlng = n_cols * cell_m / (M * math.cos(math.radians(LAT0)))
    return FuelGrid(fuel_types=[[fuel] * n_cols for _ in range(n_rows)],
                    lat_min=LAT0 - dlat / 2, lat_max=LAT0 + dlat / 2,
                    lng_min=LNG0 - dlng / 2, lng_max=LNG0 + dlng / 2, rows=n_rows, cols=n_cols)


def same_box(grid: FuelGrid, cell_m: float, fuel=FuelType.C2) -> FuelGrid:
    """Grid over ``grid``'s box with cells of about ``cell_m`` (an exact divisor)."""
    _, _, h, w = _sizes(grid)
    rows = round(grid.rows * h / cell_m)
    cols = round(grid.cols * w / cell_m)
    return FuelGrid(fuel_types=[[fuel] * cols for _ in range(rows)], lat_min=grid.lat_min,
                    lat_max=grid.lat_max, lng_min=grid.lng_min, lng_max=grid.lng_max,
                    rows=rows, cols=cols)


def _sizes(g):
    cl = (g.lat_max - g.lat_min) / g.rows
    cg = (g.lng_max - g.lng_min) / g.cols
    return cl, cg, cl * M, cg * M * math.cos(math.radians((g.lat_max + g.lat_min) / 2))


def square(lat, lng, half_m=8.0):
    """A small square footprint (lng/lat) centred on (lat, lng)."""
    dlat = half_m / M
    dlng = half_m / (M * math.cos(math.radians(lat)))
    return shapely.box(lng - dlng, lat - dlat, lng + dlng, lat + dlat)


def config(hours=1.0, wind=20.0, direction=270.0, lat=LAT0, lng=LNG0, fuel_kw=None):
    return SimulationConfig(
        ignition_lat=lat, ignition_lng=lng,
        weather=WeatherInput(temperature=25.0, relative_humidity=30.0, wind_speed=wind,
                             wind_direction=direction, precipitation_24h=0.0),
        duration_hours=hours, snapshot_interval_minutes=30.0, ffmc=92.0, dmc=40.0, dc=300.0,
        seed=7, **(fuel_kw or {}))


COND = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0, dc=300.0)


# ---------------------------------------------------------------------------
# Native grid and crop
# ---------------------------------------------------------------------------


class TestFineFuelSource:
    def test_crop_is_native_cells_on_native_edges(self):
        coarse = metre_grid(40, 40, 50.0)
        fine_grid = same_box(coarse, 20.0)
        fine_grid.fuel_types[10][13] = None
        fine_grid.fuel_types[11][13] = FuelType.O1a
        src = FineFuelSource.from_fuel_grid(fine_grid)
        assert src.cell_m == pytest.approx(20.0, rel=1e-6)
        cl, cg = src.cell_lat, src.cell_lng
        # a box that cuts through cells: the crop covers it with whole native cells
        box = (src.lat_max - 12.3 * cl, src.lat_max - 9.6 * cl,
               src.lng_min + 12.2 * cg, src.lng_min + 15.5 * cg)
        crop = src.crop(box)
        assert (crop.rows, crop.cols) == (4, 4)  # rows 9-12, cols 12-15
        assert crop.lat_max == pytest.approx(src.lat_max - 9 * cl)
        assert crop.lng_min == pytest.approx(src.lng_min + 12 * cg)
        assert crop.fuel_types[1][1] is None and crop.fuel_types[2][1] == FuelType.O1a
        assert src.cells_in(box) == 16

    def test_crop_masks_buildings_at_native_size(self):
        coarse = metre_grid(20, 20, 50.0)
        src = FineFuelSource.from_fuel_grid(same_box(coarse, 20.0))
        bldg = square(LAT0, LNG0, half_m=6.0)  # 12 m footprint: 1-4 native cells
        crop = src.crop((LAT0 - 0.002, LAT0 + 0.002, LNG0 - 0.003, LNG0 + 0.003), [bldg])
        masked = sum(ft is None for row in crop.fuel_types for ft in row)
        assert 1 <= masked <= 4  # at 50 m the same footprint masks 1-4 cells of 2,500 m²
        # the source itself is never changed
        assert int((src.idx == 0).sum()) == 0

    def test_from_raster_edmonton_native_20m(self):
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"
        if not path.exists():
            pytest.skip("Edmonton fuel grid not present")
        src = FineFuelSource.from_raster(str(path))
        assert src.cell_m == pytest.approx(20.0)
        assert src.nbytes() < 5_000_000  # one byte per cell
        assert set(src.types[1:]) == {FuelType.C2, FuelType.D2, FuelType.M2, FuelType.O1a,
                                      FuelType.O1b}


# ---------------------------------------------------------------------------
# Window helpers and logic
# ---------------------------------------------------------------------------


def test_grow_box_clips_to_outer():
    outer = (53.0, 54.0, -114.0, -113.0)
    b = grow_box((53.5, 53.6, -113.5, -113.4), 1000.0, outer)
    assert b[0] == pytest.approx(53.5 - 1000 / M)
    assert b[3] == pytest.approx(-113.4 + 1000 / (M * math.cos(math.radians(53.5))))
    assert grow_box((53.0005, 53.9995, -113.9, -113.1), 1000.0, outer)[:2] == (53.0, 54.0)


def test_reaches_edge_only_on_open_sides():
    crop = metre_grid(30, 30, 20.0)
    arr = np.full((30, 30), np.inf)
    arr[2, 15] = 10.0  # within 6 cells of the north side
    inner = (crop.lat_min - 1, crop.lat_max + 1, crop.lng_min - 1, crop.lng_max + 1)
    assert reaches_edge(arr, 60.0, crop, inner, 6)
    on_grid_edge = (crop.lat_min - 1, crop.lat_max, crop.lng_min - 1, crop.lng_max + 1)
    assert not reaches_edge(arr, 60.0, crop, on_grid_edge, 6)  # north side is the grid's edge
    assert not reaches_edge(arr, 5.0, crop, inner, 6)  # not burned by 5 min
    arr[2, 15] = np.inf
    arr[15, 15] = 1.0
    assert not reaches_edge(arr, 60.0, crop, inner, 6)


class TestWindowRun:
    """Window logic on uniform fuel: 50 m run grid, 20 m native grid over the same box."""

    @pytest.fixture
    def setup(self):
        coarse = metre_grid(100, 100, 50.0)  # 5 km
        fine = FineFuelSource.from_fuel_grid(same_box(coarse, 20.0))
        return coarse, fine

    def _sim(self, coarse, fine, buildings, **kw):
        return Simulator(config(**kw.pop("cfg", {})), fuel_grid=coarse, building_footprints=buildings,
                         fine_fuel=fine, **kw)

    def test_used_near_buildings(self, setup):
        coarse, fine = setup
        frames = list(self._sim(coarse, fine, [square(LAT0, LNG0 + 0.004)]).run())
        g = frames[-1].grid
        assert g["wui_window"] is True and g["cell_m"] == pytest.approx(20.0, abs=0.05)
        assert g["note"] == NOTE_USED and g["window_cells"] > 0
        assert all(f.grid == g for f in frames)
        raster = frames[-1].arrival_raster
        assert raster["rows"] * raster["cols"] == g["window_cells"]

    def test_stays_50m_without_buildings(self, setup):
        coarse, fine = setup
        far = square(LAT0 + 0.02, LNG0 + 0.03)  # ~2-3 km away, outside the window
        frames = list(self._sim(coarse, fine, [far]).run())
        g = frames[-1].grid
        assert g["wui_window"] is False and g["reason"] == "no_buildings"
        assert g["cell_m"] == pytest.approx(50.0, abs=0.05)
        assert frames[-1].arrival_raster["rows"] == 100

    def test_no_native_grid_is_the_old_run(self, setup):
        coarse, _ = setup
        a = list(Simulator(config(), fuel_grid=coarse).run())
        assert a[-1].grid["reason"] == "no_native_grid" and a[-1].grid["wui_window"] is False

    def test_cell_guard_falls_back(self, setup):
        coarse, fine = setup
        sim = self._sim(coarse, fine, [square(LAT0, LNG0 + 0.004)],
                        wui_window=WindowOptions(max_cells=1000))
        frames = list(sim.run())
        assert frames[-1].grid["reason"] == "too_large"
        base = list(Simulator(config(), fuel_grid=coarse).run())
        assert frames[-1].area_ha == pytest.approx(base[-1].area_ha)  # the 50 m run itself

    def test_burned_cell_guard_falls_back(self, setup):
        coarse, fine = setup
        sim = self._sim(coarse, fine, [square(LAT0, LNG0 + 0.004)],
                        wui_window=WindowOptions(max_burned_cells=50))
        assert list(sim.run())[-1].grid["reason"] == "too_large"

    def test_window_grows_when_fire_reaches_edge(self, setup):
        coarse, fine = setup
        opts = WindowOptions(min_margin_m=60.0, margin_fraction=0.0)
        frames = list(self._sim(coarse, fine, [square(LAT0, LNG0 + 0.004)],
                                wui_window=opts).run())
        g = frames[-1].grid
        assert g["wui_window"] is True
        # with a 60 m margin the 20 m fire (6-cell band) reaches the edge at least once
        sim = self._sim(coarse, fine, [square(LAT0, LNG0 + 0.004)],
                        wui_window=WindowOptions(min_margin_m=60.0, margin_fraction=0.0,
                                                 max_attempts=1))
        assert list(sim.run())[-1].grid["reason"] == "edge"

    def test_continuation_runs_stay_at_run_grid(self, setup):
        coarse, fine = setup
        sim = Simulator(config(), fuel_grid=coarse, building_footprints=[square(LAT0, LNG0)],
                        fine_fuel=fine, initial_burned=[(LAT0, LNG0)])
        assert sim._fine_window_applies() is False

    def test_run_with_window_direct(self, setup):
        """The helper with a stub runner: buildings test and the crop handed to the runner."""
        coarse, fine = setup
        coarse_frames = run_cellular_simulation(
            {"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": 0.5}, coarse, COND)
        seen = []

        def run_fn(grid):
            seen.append((grid.rows, grid.cols))
            return run_cellular_simulation(
                {"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": 0.5}, grid, COND)

        res = run_with_window(run_fn, coarse, coarse_frames, 30.0, fine, ignition=(LAT0, LNG0),
                              has_buildings=lambda b: True)
        assert res.used and len(seen) == res.attempts == 1
        assert res.grid.rows * res.grid.cols == res.window_cells
        res = run_with_window(run_fn, coarse, coarse_frames, 30.0, fine, ignition=(LAT0, LNG0),
                              has_buildings=lambda b: False)
        assert not res.used and res.grid is coarse and len(seen) == 1


def test_structure_units_beyond_the_window_are_kept():
    """Building-to-building spread is not cut at the crop edge: units come from the run grid's
    box (``area_bbox``), front contact from the crop's arrival raster."""
    from firesim.structures.spread import structure_spread_for_grid_run

    area_grid = metre_grid(60, 60, 50.0)  # 3 km run grid
    area = (area_grid.lat_min, area_grid.lat_max, area_grid.lng_min, area_grid.lng_max)
    crop_grid = metre_grid(20, 20, 20.0)  # 400 m crop at the centre
    crop = (crop_grid.lat_min, crop_grid.lat_max, crop_grid.lng_min, crop_grid.lng_max)
    arrival = np.full((20, 20), np.inf)
    arrival[10, 10] = 0.0  # one burned cell at the centre
    # a row of 16 m houses 6 m apart from the burned cell eastward, 1.1 km long
    dlng = 22.0 / (M * math.cos(math.radians(LAT0)))
    houses = [square(LAT0 - 10.0 / M, LNG0 + 0.0002 + k * dlng) for k in range(50)]
    schedule = [(0.0, COND)]
    full = structure_spread_for_grid_run(houses, arrival, schedule, 900.0, bbox=crop,
                                         area_bbox=area)
    cut = structure_spread_for_grid_run(houses, arrival, schedule, 900.0, bbox=crop)
    east = np.array([h.centroid.x > crop[3] for h in houses])
    assert east.sum() > 20
    assert full.units_in_run == len(houses) and cut.units_in_run == int((~east).sum())
    reached = np.isfinite(full.t_min)
    assert reached[0] and full.t_front_min[0] == 0.0
    # beyond the crop, houses are involved building to building only with area_bbox
    assert reached.sum() > len(cut.t_min) >= int(np.isfinite(cut.t_min).sum())


# ---------------------------------------------------------------------------
# Convergence: 20 m vs 50 m on uniform fuel
# ---------------------------------------------------------------------------


class TestConvergence:
    """The level set is the same at any cell size, so on uniform fuel the 20 m and 50 m runs
    must agree with each other and with the FBP ellipse (ST-X-3 eqs 79-89)."""

    @pytest.mark.parametrize("fuel,cure,direction", [
        (FuelType.C2, 60.0, 270.0),
        (FuelType.C2, 60.0, 225.0),  # wind on the grid diagonal
        (FuelType.O1a, 100.0, 270.0),
        (FuelType.O1a, 100.0, 200.0),
    ])
    def test_area_and_head_agree(self, fuel, cure, direction):
        cond = SpreadConditions(wind_speed=20.0, wind_direction=direction, ffmc=92.0, dmc=40.0,
                                dc=300.0, grass_cure=cure)
        f = fbp_for_conditions(cond, fuel)
        expected = calculate_ellipse_area(f.ros_final, f.back_ros, f.lb, 1.0)
        out = {}
        for cell in (50.0, 20.0):
            n = int(round(8000.0 / cell))
            grid = metre_grid(n, n, cell, fuel)
            fr = run_cellular_simulation(
                {"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": 1.0}, grid, cond,
                acceleration=False)
            cells = fr[-1].burned_cells
            # head run: farthest burned cell centre along the spread direction
            sx, sy = math.sin(math.radians(direction + 180.0)), math.cos(math.radians(direction + 180.0))
            k = math.cos(math.radians(LAT0))
            head = max(((c.lng - LNG0) * M * k) * sx + ((c.lat - LAT0) * M) * sy for c in cells)
            out[cell] = (fr[-1].area_ha, head)
        a50, h50 = out[50.0]
        a20, h20 = out[20.0]
        # Measured 2026-10-10 (area % of the ellipse, 50 m / 20 m): C-2 -4.4/-4.3 (W wind),
        # -6.3/-3.8 (SW); O-1a -9.5/-6.5 (W), -10.1/-7.8 (SSW): the error shrinks with the cell
        assert a20 == pytest.approx(expected, rel=0.08)
        assert a50 == pytest.approx(expected, rel=0.12)
        assert abs(a20 - expected) <= abs(a50 - expected) + 0.01 * expected
        assert a20 == pytest.approx(a50, rel=0.06)
        head = f.ros_final * 60.0
        assert h20 == pytest.approx(head, rel=0.04)
        assert abs(h20 - head) <= abs(h50 - head) + 30.0  # 20 m no worse, to 1.5 cells

    def test_window_run_matches_uniform_50m(self):
        """Through the Simulator: the 20 m window on uniform fuel gives the 50 m area."""
        coarse = metre_grid(120, 120, 50.0)
        fine = FineFuelSource.from_fuel_grid(same_box(coarse, 20.0))
        bldg = [square(LAT0 + 0.003, LNG0)]
        a = list(Simulator(config(hours=1.0), fuel_grid=coarse).run())[-1]
        b = list(Simulator(config(hours=1.0), fuel_grid=coarse, building_footprints=bldg,
                           fine_fuel=fine).run())[-1]
        assert b.grid["wui_window"] is True
        assert b.area_ha == pytest.approx(a.area_ha, rel=0.10)


# ---------------------------------------------------------------------------
# Memory per cell (sizes WindowOptions.max_cells)
# ---------------------------------------------------------------------------


def test_memory_per_cell_bounds_the_window():
    """Peak traced memory of the grid run per cell, on a 20 m grid (measured ~265 B/cell).

    ``WindowOptions.max_cells`` x this bound must stay near the 50 m whole-Edmonton run
    (502,090 cells), so the window adds little to the 2 GB server's peak.
    """
    cond = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0,
                            dc=300.0, grass_cure=100.0)
    grid = metre_grid(500, 500, 20.0, FuelType.O1a)
    tracemalloc.start()
    run_cellular_simulation({"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": 0.5},
                            grid, cond)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    per_cell = peak / (grid.rows * grid.cols)
    assert per_cell < 320.0
    assert WindowOptions().max_cells * 320.0 < 200e6  # < 200 MB for the largest crop
    assert WindowOptions().max_cells <= 1.25 * 502_090
