"""Unit tests for spatial data loaders: fuel_loader, environment, wui_loader."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from firesim.data.environment import load_environment_mask
from firesim.data.fuel_loader import (
    ALL_CODES,
    CANOPY_RASTER_CODES,
    FBP_RASTER_CODES,
    TERRA_PIPELINE_CODES,
    UPLVI_RASTER_CODES,
    _detect_code_map,
    load_fuel_grid,
)
from firesim.data.wui_loader import load_wui_modifiers
from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid, SpreadModifierGrid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Edmonton UTM 12N origin (approximate top-left corner, 10m raster)
_UTM_CRS = CRS.from_epsg(32612)
_WEST = 350_000.0   # m easting
_NORTH = 5_930_000.0  # m northing
_CELL_10M = 10.0


def _make_fuel_raster(
    path: Path, data: np.ndarray, cell_m: float = _CELL_10M, nodata: float | None = None
) -> Path:
    """Write a small GeoTIFF with UTM 12N projection."""
    rows, cols = data.shape
    transform = from_origin(_WEST, _NORTH, cell_m, cell_m)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=data.dtype,
        crs=_UTM_CRS,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


def _fuel_at_source_cells(grid: FuelGrid, rows: int, cols: int, cell_m: float) -> list[list]:
    """Fuel the grid holds at the centre of each source (UTM) cell.

    The loader reprojects onto a lat/lng grid, so source rows and columns no longer map
    one to one onto grid cells; sample at each source cell's true position instead.
    """
    from rasterio.warp import transform as warp_transform

    out = []
    for r in range(rows):
        xs = [_WEST + (c + 0.5) * cell_m for c in range(cols)]
        ys = [_NORTH - (r + 0.5) * cell_m] * cols
        lngs, lats = warp_transform(_UTM_CRS, "EPSG:4326", xs, ys)
        out.append([grid.get_fuel_at(lat, lng) for lat, lng in zip(lats, lngs)])
    return out


def _geojson_feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _polygon_feature(coords: list[list[float]], props: dict | None = None) -> dict:
    """GeoJSON polygon feature from a ring of [lng, lat] pairs."""
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": props or {},
    }


def _write_geojson(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_geojson_gz(path: Path, data: dict) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    return path


# Small test grid in WGS84
# bounds: lat 53.0–53.1, lng -113.1–-113.0  (0.1° × 0.1°)
# 10 rows × 10 cols → each cell 0.01° lat × 0.01° lng
_BOUNDS = (53.0, 53.1, -113.1, -113.0)  # lat_min, lat_max, lng_min, lng_max
_ROWS = 10
_COLS = 10


# ===========================================================================
# _detect_code_map
# ===========================================================================


class TestDetectCodeMap:
    def test_fbp_scheme_detected_when_42_present(self):
        assert _detect_code_map({1, 32, 41, 42}) is FBP_RASTER_CODES

    def test_uplvi_scheme_detected_when_22_present(self):
        assert _detect_code_map({2, 12, 22, 31}) is UPLVI_RASTER_CODES

    def test_ambiguous_falls_back_to_all_codes(self):
        # Neither 42-only nor 22-only pattern
        assert _detect_code_map({1, 2, 3}) is ALL_CODES

    def test_both_42_and_22_falls_back_to_all_codes(self):
        # Both marker codes present → ambiguous
        assert _detect_code_map({22, 42}) is ALL_CODES

    def test_empty_codes_falls_back_to_all_codes(self):
        assert _detect_code_map(set()) is ALL_CODES

    def test_edmonton_fuel_layer_codes_detect_canopy_scheme(self):
        # Codes present in Edmonton_FBP_FuelLayer_20251105_10m.tif
        assert _detect_code_map({2, 12, 14, 31, 32, 99}) is CANOPY_RASTER_CODES

    @pytest.mark.parametrize(
        "codes",
        [
            {1, 12, 13, 14, 31, 41, 42},  # cloud3_fuel20m.tif
            {1, 13, 31, 41, 42},  # cloud6_fuel20m.tif
            {0, 11, 12, 15, 41, 42},  # cloud3_fbp_fuel.tif
            {1, 12, 13, 14, 31},  # pipeline raster without mixedwood
        ],
    )
    def test_drone_pipeline_codes_detect_pipeline_scheme(self, codes):
        cmap = _detect_code_map(codes)
        assert cmap is TERRA_PIPELINE_CODES
        assert cmap[41] == FuelType.M1 and cmap[42] == FuelType.M2 and cmap[31] == FuelType.D1

    def test_ambiguous_codes_use_priority_order(self):
        # {1, 12, 41, 42} fits both the Edmonton FBP and the pipeline scheme
        assert _detect_code_map({1, 12, 41, 42}) is FBP_RASTER_CODES

    def test_explicit_scheme_overrides_detection(self):
        assert _detect_code_map({1, 12, 41, 42}, "terra_pipeline") is TERRA_PIPELINE_CODES

    def test_unknown_scheme_rejected(self):
        with pytest.raises(ValueError):
            _detect_code_map({1}, "nonsense")


# ===========================================================================
# load_fuel_grid — valid loading
# ===========================================================================


class TestLoadFuelGridValid:
    def test_pipeline_raster_mixedwood_not_grass(self, tmp_path):
        data = np.array([[41, 42], [31, 14]], dtype=np.int16)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert _fuel_at_source_cells(grid, 2, 2, 50.0) == [[FuelType.M1, FuelType.M2], [FuelType.D1, FuelType.C4]]

    def test_float_raster_with_nan_nodata(self, tmp_path):
        data = np.array([[12.0, np.nan], [41.0, 31.0]], dtype=np.float32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0, nodata=float("nan"))
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert _fuel_at_source_cells(grid, 2, 2, 50.0) == [[FuelType.C2, None], [FuelType.M1, FuelType.D1]]

    def test_returns_fuel_grid_instance(self, tmp_path):
        data = np.array([[1, 12], [32, 41]], dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data)
        grid = load_fuel_grid(str(raster), target_resolution_m=10.0)
        assert isinstance(grid, FuelGrid)

    def test_grid_cell_size_matches_input_when_no_downsampling(self, tmp_path):
        data = np.ones((4, 4), dtype=np.int32)  # all code 1 → C1
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        # a 200 m square in UTM covers 4-5 cells of 50 m once placed on a lat/lng grid
        assert 4 <= grid.rows <= 5 and 4 <= grid.cols <= 5
        cell_m = (grid.lat_max - grid.lat_min) / grid.rows * 111320.0
        assert cell_m == pytest.approx(50.0, rel=0.01)
        assert all(f == FuelType.C1 for row in _fuel_at_source_cells(grid, 4, 4, 50.0) for f in row)

    def test_fuel_type_mapping_fbp_scheme(self, tmp_path):
        # code 42 → O1b (FBP scheme), no code 22
        data = np.array([[42, 1], [32, 0]], dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert grid.fuel_types[0][0] == FuelType.O1b
        assert grid.fuel_types[0][1] == FuelType.C1
        assert grid.fuel_types[1][0] == FuelType.S2
        assert grid.fuel_types[1][1] is None  # code 0 → non-fuel

    def test_fuel_type_mapping_uplvi_scheme(self, tmp_path):
        # code 22 → M2 (uPLVI scheme), no code 42
        data = np.array([[22, 2], [31, 0]], dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert grid.fuel_types[0][0] == FuelType.M2
        assert grid.fuel_types[0][1] == FuelType.C2
        assert grid.fuel_types[1][0] == FuelType.O1a
        assert grid.fuel_types[1][1] is None

    def test_bounds_are_wgs84(self, tmp_path):
        data = np.ones((4, 4), dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data)
        grid = load_fuel_grid(str(raster), target_resolution_m=10.0)
        # Bounds should be in WGS84 degree range
        assert -180 <= grid.lng_min < grid.lng_max <= 180
        assert -90 <= grid.lat_min < grid.lat_max <= 90

    def test_nodata_becomes_non_fuel(self, tmp_path):
        data = np.array([[1, 255], [32, 255]], dtype=np.int32)
        rows, cols = data.shape
        transform = from_origin(_WEST, _NORTH, 50.0, 50.0)
        path = tmp_path / "fuel_nodata.tif"
        with rasterio.open(
            path, "w", driver="GTiff",
            height=rows, width=cols, count=1,
            dtype=data.dtype, crs=_UTM_CRS,
            transform=transform, nodata=255,
        ) as dst:
            dst.write(data, 1)
        grid = load_fuel_grid(str(path), target_resolution_m=50.0)
        # nodata cells (255) → -9999 → None in ALL_CODES
        assert grid.fuel_types[0][1] is None
        assert grid.fuel_types[1][1] is None


# ===========================================================================
# load_fuel_grid — downsampling
# ===========================================================================


class TestLoadFuelGridDownsampling:
    def test_downsampling_reduces_grid_size(self, tmp_path):
        # 20×20 at 10m → target 50m → scale=0.2 → ~4×4
        data = np.ones((20, 20), dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=10.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert grid.rows < 20
        assert grid.cols < 20

    def test_no_downsampling_when_already_at_target(self, tmp_path):
        data = np.ones((5, 5), dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert 5 <= grid.rows <= 6 and 5 <= grid.cols <= 6

    def test_cells_are_placed_at_their_true_position(self, tmp_path):
        """Regression: the loader used to stretch projected rows/columns over the lat/lng
        bounding box, misplacing cells by up to hundreds of metres."""
        data = np.zeros((40, 40), dtype=np.int32)  # 2 km x 2 km of non-fuel (code 0)
        data[30, 7] = 1  # one C-1 marker cell
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        from rasterio.warp import transform as warp_transform

        lng, lat = warp_transform(_UTM_CRS, "EPSG:4326", [_WEST + 7.5 * 50.0], [_NORTH - 30.5 * 50.0])
        assert grid.get_fuel_at(lat[0], lng[0]) == FuelType.C1
        burning = [(r, c) for r in range(grid.rows) for c in range(grid.cols) if grid.fuel_types[r][c]]
        assert len(burning) == 1


# ===========================================================================
# load_fuel_grid — environment masking
# ===========================================================================


class TestLoadFuelGridEnvironmentMask:
    def test_water_body_masks_fuel_cells(self, tmp_path):
        # 2×2 raster at 50m, all C1 (code 1)
        data = np.ones((2, 2), dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)

        # Build grid to find actual WGS84 bounds from the raster
        grid_pre = load_fuel_grid(str(raster), target_resolution_m=50.0)
        lat_min, lat_max = grid_pre.lat_min, grid_pre.lat_max
        lng_min, lng_max = grid_pre.lng_min, grid_pre.lng_max

        # Polygon that covers the entire grid extent
        water = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_min - 0.001],
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)

        grid = load_fuel_grid(
            str(raster),
            target_resolution_m=50.0,
            water_path=str(water_path),
        )
        # All cells should be masked to None
        for row in grid.fuel_types:
            for cell in row:
                assert cell is None

    def test_non_overlapping_mask_leaves_fuels_intact(self, tmp_path):
        data = np.full((2, 2), 1, dtype=np.int32)  # all C1
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)

        # Water body far away from the grid
        water = _geojson_feature_collection([
            _polygon_feature([
                [0.0, 0.0], [0.001, 0.0], [0.001, 0.001], [0.0, 0.001], [0.0, 0.0]
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)

        grid = load_fuel_grid(
            str(raster),
            target_resolution_m=50.0,
            water_path=str(water_path),
        )
        assert grid.fuel_types[0][0] == FuelType.C1

    def test_buildings_mask_fuel_cells(self, tmp_path):
        data = np.ones((2, 2), dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)

        grid_pre = load_fuel_grid(str(raster), target_resolution_m=50.0)
        lat_min, lat_max = grid_pre.lat_min, grid_pre.lat_max
        lng_min, lng_max = grid_pre.lng_min, grid_pre.lng_max

        buildings = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_min - 0.001],
                [lng_max + 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_max + 0.001],
                [lng_min - 0.001, lat_min - 0.001],
            ])
        ])
        bldg_path = _write_geojson(tmp_path / "buildings.geojson", buildings)

        grid = load_fuel_grid(
            str(raster),
            target_resolution_m=50.0,
            buildings_path=str(bldg_path),
        )
        for row in grid.fuel_types:
            for cell in row:
                assert cell is None


# ===========================================================================
# load_fuel_grid — edge cases
# ===========================================================================


class TestLoadFuelGridEdgeCases:
    def test_missing_file_raises(self):
        with pytest.raises((FileNotFoundError, rasterio.errors.RasterioIOError)):
            load_fuel_grid("/nonexistent/path/fuel.tif")

    def test_unknown_code_maps_to_none(self, tmp_path):
        # Code 99 → None in ALL_CODES
        data = np.full((2, 2), 99, dtype=np.int32)
        raster = _make_fuel_raster(tmp_path / "fuel.tif", data, cell_m=50.0)
        grid = load_fuel_grid(str(raster), target_resolution_m=50.0)
        assert all(cell is None for row in grid.fuel_types for cell in row)


# ===========================================================================
# load_environment_mask
# ===========================================================================


class TestLoadEnvironmentMask:
    def test_no_paths_returns_all_zeros(self):
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS)
        assert mask.shape == (_ROWS, _COLS)
        assert mask.dtype == bool
        assert not mask.any()

    def test_water_body_covering_full_grid_masks_all_cells(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        water = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_min - 0.01],
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS, water_path=str(water_path))
        assert mask.all()

    def test_water_body_outside_bounds_masks_nothing(self, tmp_path):
        water = _geojson_feature_collection([
            _polygon_feature([
                [0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS, water_path=str(water_path))
        assert not mask.any()

    def test_water_body_covering_single_cell(self, tmp_path):
        # Cover only cell (5, 5): center at lat=53.045, lng=-113.045
        # Cell size = 0.01°; lat = lat_max - (r+0.5)*cell_lat → row 5: 53.1 - 5.5*0.01 = 53.045
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        cell_lat = (lat_max - lat_min) / _ROWS  # 0.01
        cell_lng = (lng_max - lng_min) / _COLS  # 0.01
        r, c = 5, 5
        clat = lat_max - (r + 0.5) * cell_lat
        clng = lng_min + (c + 0.5) * cell_lng
        half = cell_lat * 0.4  # slightly smaller than half cell to hit just one
        water = _geojson_feature_collection([
            _polygon_feature([
                [clng - half, clat - half],
                [clng + half, clat - half],
                [clng + half, clat + half],
                [clng - half, clat + half],
                [clng - half, clat - half],
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS, water_path=str(water_path))
        assert mask[r, c] is np.bool_(True)
        # All other cells should remain False
        mask[r, c] = False
        assert not mask.any()

    def test_buildings_covering_full_grid_masks_all_cells(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        buildings = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_min - 0.01],
            ])
        ])
        bldg_path = _write_geojson(tmp_path / "buildings.geojson", buildings)
        mask = load_environment_mask(
            _BOUNDS, _ROWS, _COLS, buildings_path=str(bldg_path)
        )
        assert mask.all()

    def test_combined_water_and_buildings(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        cell_lat = (lat_max - lat_min) / _ROWS
        cell_lng = (lng_max - lng_min) / _COLS

        # Water covers top row (row 0), buildings cover bottom row (row 9)
        top_lat = lat_max - 0.5 * cell_lat
        bot_lat = lat_min + 0.5 * cell_lat
        half = cell_lat * 0.4

        water = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.01, top_lat - cell_lat * 0.49],
                [lng_max + 0.01, top_lat - cell_lat * 0.49],
                [lng_max + 0.01, top_lat + cell_lat * 0.49],
                [lng_min - 0.01, top_lat + cell_lat * 0.49],
                [lng_min - 0.01, top_lat - cell_lat * 0.49],
            ])
        ])
        buildings = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.01, bot_lat - half],
                [lng_max + 0.01, bot_lat - half],
                [lng_max + 0.01, bot_lat + half],
                [lng_min - 0.01, bot_lat + half],
                [lng_min - 0.01, bot_lat - half],
            ])
        ])
        water_path = _write_geojson(tmp_path / "water.geojson", water)
        bldg_path = _write_geojson(tmp_path / "buildings.geojson", buildings)

        mask = load_environment_mask(
            _BOUNDS, _ROWS, _COLS,
            water_path=str(water_path),
            buildings_path=str(bldg_path),
        )
        assert mask[0, :].all()   # top row masked by water
        assert mask[9, :].all()   # bottom row masked by buildings
        assert not mask[4, :].any()  # middle rows untouched

    def test_empty_geojson_returns_zeros(self, tmp_path):
        empty = _geojson_feature_collection([])
        water_path = _write_geojson(tmp_path / "empty.geojson", empty)
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS, water_path=str(water_path))
        assert not mask.any()

    def test_geojson_gz_loads_correctly(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        water = _geojson_feature_collection([
            _polygon_feature([
                [lng_min - 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_min - 0.01],
                [lng_max + 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_max + 0.01],
                [lng_min - 0.01, lat_min - 0.01],
            ])
        ])
        gz_path = _write_geojson_gz(tmp_path / "water.geojson.gz", water)
        mask = load_environment_mask(_BOUNDS, _ROWS, _COLS, water_path=str(gz_path))
        assert mask.all()

    def test_mask_shape_matches_rows_cols(self, tmp_path):
        mask = load_environment_mask(_BOUNDS, rows=7, cols=13)
        assert mask.shape == (7, 13)


# ===========================================================================
# load_wui_modifiers
# ===========================================================================


class TestLoadWuiModifiers:
    def test_returns_spread_modifier_grid(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        wui = _geojson_feature_collection([
            _polygon_feature(
                [
                    [lng_min - 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_min - 0.01],
                ],
                {"ros_multiplier": 0.7, "intensity_multiplier": 1.2, "ember_multiplier": 3.0},
            )
        ])
        wui_path = _write_geojson(tmp_path / "wui.geojson", wui)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)
        assert isinstance(grid, SpreadModifierGrid)

    def test_wui_zone_multipliers_propagate_to_cells(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        wui = _geojson_feature_collection([
            _polygon_feature(
                [
                    [lng_min - 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_min - 0.01],
                ],
                {"ros_multiplier": 0.5, "intensity_multiplier": 1.5, "ember_multiplier": 2.0},
            )
        ])
        wui_path = _write_geojson(tmp_path / "wui.geojson", wui)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        assert grid.ros_multiplier[0][0] == pytest.approx(0.5)
        assert grid.intensity_multiplier[0][0] == pytest.approx(1.5)
        assert grid.ember_multiplier[0][0] == pytest.approx(2.0)

    def test_missing_properties_default_to_1(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        wui = _geojson_feature_collection([
            _polygon_feature(
                [
                    [lng_min - 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_min - 0.01],
                ],
                {},  # No multiplier properties
            )
        ])
        wui_path = _write_geojson(tmp_path / "wui.geojson", wui)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        assert grid.ros_multiplier[0][0] == pytest.approx(1.0)
        assert grid.intensity_multiplier[0][0] == pytest.approx(1.0)
        assert grid.ember_multiplier[0][0] == pytest.approx(1.0)

    def test_no_zones_returns_default_grid(self, tmp_path):
        empty = _geojson_feature_collection([])
        wui_path = _write_geojson(tmp_path / "wui.geojson", empty)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        assert isinstance(grid, SpreadModifierGrid)
        for r in range(_ROWS):
            for c in range(_COLS):
                assert grid.ros_multiplier[r][c] == pytest.approx(1.0)
                assert grid.intensity_multiplier[r][c] == pytest.approx(1.0)
                assert grid.ember_multiplier[r][c] == pytest.approx(1.0)

    def test_zone_outside_bounds_returns_default_grid(self, tmp_path):
        # Zone placed far away — no cells modified
        wui = _geojson_feature_collection([
            _polygon_feature(
                [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]],
                {"ros_multiplier": 0.5, "intensity_multiplier": 2.0, "ember_multiplier": 4.0},
            )
        ])
        wui_path = _write_geojson(tmp_path / "wui.geojson", wui)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        for r in range(_ROWS):
            for c in range(_COLS):
                assert grid.ros_multiplier[r][c] == pytest.approx(1.0)

    def test_partial_coverage_modifies_only_covered_cells(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        cell_lat = (lat_max - lat_min) / _ROWS
        cell_lng = (lng_max - lng_min) / _COLS

        # Zone covering only the top-left quadrant (rows 0-4, cols 0-4)
        zone_lat_max = lat_max
        zone_lat_min = lat_max - 5 * cell_lat
        zone_lng_min = lng_min
        zone_lng_max = lng_min + 5 * cell_lng

        wui = _geojson_feature_collection([
            _polygon_feature(
                [
                    [zone_lng_min, zone_lat_min],
                    [zone_lng_max, zone_lat_min],
                    [zone_lng_max, zone_lat_max],
                    [zone_lng_min, zone_lat_max],
                    [zone_lng_min, zone_lat_min],
                ],
                {"ros_multiplier": 0.6},
            )
        ])
        wui_path = _write_geojson(tmp_path / "wui.geojson", wui)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        # Cells in top-left quadrant should have ros=0.6
        assert grid.ros_multiplier[0][0] == pytest.approx(0.6)
        assert grid.ros_multiplier[2][2] == pytest.approx(0.6)
        # Bottom-right quadrant should be unmodified
        assert grid.ros_multiplier[9][9] == pytest.approx(1.0)

    def test_grid_dimensions_match_rows_cols(self, tmp_path):
        empty = _geojson_feature_collection([])
        wui_path = _write_geojson(tmp_path / "wui.geojson", empty)
        grid = load_wui_modifiers(str(wui_path), _BOUNDS, _ROWS, _COLS)

        assert grid.rows == _ROWS
        assert grid.cols == _COLS
        assert len(grid.ros_multiplier) == _ROWS
        assert len(grid.ros_multiplier[0]) == _COLS

    def test_geojson_gz_loads_correctly(self, tmp_path):
        lat_min, lat_max, lng_min, lng_max = _BOUNDS
        wui = _geojson_feature_collection([
            _polygon_feature(
                [
                    [lng_min - 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_min - 0.01],
                    [lng_max + 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_max + 0.01],
                    [lng_min - 0.01, lat_min - 0.01],
                ],
                {"ros_multiplier": 0.8, "ember_multiplier": 2.5},
            )
        ])
        gz_path = _write_geojson_gz(tmp_path / "wui.geojson.gz", wui)
        grid = load_wui_modifiers(str(gz_path), _BOUNDS, _ROWS, _COLS)
        assert grid.ros_multiplier[0][0] == pytest.approx(0.8)
        assert grid.ember_multiplier[0][0] == pytest.approx(2.5)
