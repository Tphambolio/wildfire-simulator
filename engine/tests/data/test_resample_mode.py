"""Majority (mode) resampling of categorical fuel rasters when coarsening (2026-10-10).

``load_fuel_grid`` coarsened with nearest neighbour until 2026-10-10: a point sample that
keeps the class under each target cell's centre and discards the rest (84 % of the Edmonton
20 m cells at 50 m). It now takes the class covering most of the target cell
(``firesim.data.raster_grid.categorical_resampling``); at or below the source cell size it
still copies cells (nearest neighbour).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling

from firesim.data.fuel_loader import load_fuel_grid
from firesim.data.raster_grid import categorical_resampling
from firesim.fbp.constants import FuelType

M = 111320.0
LAT_MAX, LNG_MIN = 53.6, -113.6


def _write(path, data, cell_m=10.0):
    """Geographic (EPSG:4326) raster of ``cell_m``-metre cells (canopy-LiDAR codes)."""
    rows, cols = data.shape
    cell_m *= 1.0 - 1e-7  # a hair under, so ceil(extent / target cell) is not one too many
    mid = LAT_MAX - rows * cell_m / M / 2
    dlat = cell_m / M
    dlng = cell_m / (M * math.cos(math.radians(mid)))
    with rasterio.open(path, "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="int16", crs="EPSG:4326", nodata=0,
                       transform=from_origin(LNG_MIN, LAT_MAX, dlng, dlat)) as dst:
        dst.write(data.astype(np.int16), 1)


def _blocks(n_blocks=4):
    """5 x 5 blocks of 10 m cells (one 50 m cell each): 13 grass (31) + 12 non-fuel (99) with
    non-fuel at the block centre, so the centre sample is non-fuel and the majority is grass."""
    block = np.full((5, 5), 99)
    grass = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (1, 0), (1, 4), (2, 0), (2, 4),
             (3, 0), (3, 4), (4, 0), (4, 4)]
    for r, c in grass:
        block[r, c] = 31
    assert (block == 31).sum() == 13 and block[2, 2] == 99
    return np.tile(block, (n_blocks, n_blocks))


def test_categorical_resampling_rule():
    assert categorical_resampling(50.0, 20.0) == Resampling.mode
    assert categorical_resampling(20.0, 20.0) == Resampling.nearest
    assert categorical_resampling(90.0, 250.0) == Resampling.nearest  # refining (CFSDS 250 -> 90 m)


def test_coarsening_takes_the_majority_class(tmp_path):
    path = tmp_path / "blocks.tif"
    _write(path, _blocks())
    mode = load_fuel_grid(str(path), target_resolution_m=50.0)
    near = load_fuel_grid(str(path), target_resolution_m=50.0, resampling="nearest")
    assert (mode.rows, mode.cols) == (4, 4)
    inner = [mode.fuel_types[r][c] for r in range(1, 3) for c in range(1, 3)]
    assert inner == [FuelType.O1a] * 4  # 13 of 25 source cells are grass
    assert [near.fuel_types[r][c] for r in range(1, 3) for c in range(1, 3)] == [None] * 4


def test_class_shares_are_kept_by_mode(tmp_path):
    """Random 10 m mosaic of large patches: the mode keeps each class's share within a few
    percent; it can only lose classes that are a minority in every 50 m cell."""
    rng = np.random.default_rng(3)
    patches = rng.choice([2, 12, 31, 99], size=(8, 8), p=[0.1, 0.2, 0.4, 0.3])
    data = np.kron(patches, np.ones((10, 10), dtype=int))  # 100 m patches of 10 m cells
    path = tmp_path / "patches.tif"
    _write(path, data)
    grid = load_fuel_grid(str(path), target_resolution_m=50.0)
    codes = {FuelType.C2: 2, FuelType.D2: 12, FuelType.O1a: 31, None: 99}
    out = np.array([[codes[ft] for ft in row] for row in grid.fuel_types])
    for code in (2, 12, 31, 99):
        assert np.mean(out == code) == pytest.approx(np.mean(data == code), abs=0.03)


def test_no_coarsening_at_native_size(tmp_path):
    path = tmp_path / "native.tif"
    data = _blocks(2)
    _write(path, data)
    grid = load_fuel_grid(str(path), target_resolution_m=10.0)
    assert (grid.rows, grid.cols) == data.shape
    got = np.array([[0 if ft is None else 1 for ft in row] for row in grid.fuel_types])
    assert np.array_equal(got, (data == 31).astype(int))
