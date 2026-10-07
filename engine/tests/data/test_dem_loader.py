"""Tests for DEM loader — slope/aspect computation and TerrainGrid creation.

Uses synthetic in-memory GeoTIFFs (via rasterio MemoryFile) so the tests
run without any external data files.
"""

from __future__ import annotations

import math
import tempfile
import os

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds

from firesim.data.dem_loader import load_terrain_grid
from firesim.spread.huygens import TerrainGrid


def _write_dem_tiff(
    path: str,
    elevation: np.ndarray,
    lat_min: float = 53.0,
    lat_max: float = 53.1,
    lng_min: float = -113.6,
    lng_max: float = -113.5,
    crs: str = "EPSG:4326",
) -> None:
    """Write a small elevation GeoTIFF to path (WGS84 by default)."""
    rows, cols = elevation.shape
    transform = from_bounds(lng_min, lat_min, lng_max, lat_max, cols, rows)
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=elevation.dtype,
        crs=CRS.from_epsg(4326),
        transform=transform,
    ) as dst:
        dst.write(elevation.astype(np.float32), 1)


def _write_utm_dem_tiff(
    path: str,
    elevation: np.ndarray,
    cell_size_m: float = 10.0,
    origin_x: float = 400000.0,
    origin_y: float = 5900000.0,
) -> None:
    """Write a small elevation GeoTIFF in UTM Zone 12N (projected CRS)."""
    rows, cols = elevation.shape
    transform = rasterio.transform.from_origin(origin_x, origin_y + rows * cell_size_m, cell_size_m, cell_size_m)
    with rasterio.open(
        path, "w",
        driver="GTiff",
        height=rows,
        width=cols,
        count=1,
        dtype=elevation.dtype,
        crs=CRS.from_epsg(32612),  # UTM Zone 12N
        transform=transform,
    ) as dst:
        dst.write(elevation.astype(np.float32), 1)


class TestDEMLoaderBasic:
    """Basic loading and return type tests."""

    def test_returns_terrain_grid(self, tmp_path):
        """load_terrain_grid returns a TerrainGrid instance."""
        elev = np.ones((20, 20), dtype=np.float32) * 700.0
        dem_path = str(tmp_path / "flat.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path)
        assert isinstance(grid, TerrainGrid)

    def test_grid_dimensions(self, tmp_path):
        """Grid rows/cols match (downsampled) raster dimensions."""
        elev = np.ones((20, 20), dtype=np.float32) * 700.0
        dem_path = str(tmp_path / "flat.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        assert grid.rows > 0
        assert grid.cols > 0

    def test_bounds_in_wgs84(self, tmp_path):
        """Grid bounds are in WGS84 (roughly sensible lat/lng values)."""
        elev = np.ones((20, 20), dtype=np.float32) * 700.0
        dem_path = str(tmp_path / "flat.tif")
        _write_dem_tiff(dem_path, elev, lat_min=53.0, lat_max=53.1,
                        lng_min=-113.6, lng_max=-113.5)
        grid = load_terrain_grid(dem_path)
        assert 50.0 < grid.lat_min < 60.0
        assert 50.0 < grid.lat_max < 60.0
        assert -120.0 < grid.lng_min < -110.0
        assert -120.0 < grid.lng_max < -110.0
        assert grid.lat_min < grid.lat_max
        assert grid.lng_min < grid.lng_max

    def test_file_not_found(self):
        """Raises FileNotFoundError for a non-existent path."""
        with pytest.raises(FileNotFoundError):
            load_terrain_grid("/nonexistent/path/dem.tif")


class TestFlatTerrain:
    """Flat DEM should give slope ≈ 0 everywhere."""

    def test_flat_slope_is_zero(self, tmp_path):
        """Flat terrain produces ~0% slope for all cells."""
        elev = np.full((30, 30), 700.0, dtype=np.float32)
        dem_path = str(tmp_path / "flat.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        for row in grid.slope:
            for val in row:
                assert val < 0.5, f"Flat terrain cell has slope {val:.2f}%"

    def test_flat_aspect_is_zero(self, tmp_path):
        """Flat terrain cells get aspect = 0 (undefined, set to default)."""
        elev = np.full((20, 20), 700.0, dtype=np.float32)
        dem_path = str(tmp_path / "flat.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        for row in grid.aspect:
            for val in row:
                assert val == 0.0


class TestSlopedTerrain:
    """Synthetic sloped DEMs should produce expected slope/aspect."""

    def test_north_facing_slope_aspect(self, tmp_path):
        """Slope rising toward north should have aspect ~0° (faces north)."""
        rows, cols = 30, 30
        # Elevation increases going north (decreasing row index)
        elev = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            elev[r, :] = (rows - r) * 10.0  # 10 m per cell going north
        dem_path = str(tmp_path / "north_slope.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        # Interior cells (avoid boundary effects)
        mid_r = rows // 2
        mid_c = cols // 2
        # Slope should be >0
        assert grid.slope[mid_r][mid_c] > 1.0, "North-facing slope should have >1% slope"
        # Aspect should be near 0° (north) — allow ±30°
        asp = grid.aspect[mid_r][mid_c]
        assert asp < 30.0 or asp > 330.0, f"North-facing slope aspect {asp:.1f}° should be ~0°"

    def test_east_facing_slope_aspect(self, tmp_path):
        """Slope rising toward east should have aspect ~90°."""
        rows, cols = 30, 30
        # Elevation increases going east (increasing col index)
        elev = np.zeros((rows, cols), dtype=np.float32)
        for c in range(cols):
            elev[:, c] = c * 10.0
        dem_path = str(tmp_path / "east_slope.tif")
        _write_dem_tiff(dem_path, elev)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        mid_r = rows // 2
        mid_c = cols // 2
        asp = grid.aspect[mid_r][mid_c]
        assert 60.0 < asp < 120.0, f"East-facing slope aspect {asp:.1f}° should be ~90°"

    def test_slope_magnitude_reasonable(self, tmp_path):
        """A 10 m/cell rise over ~111 m (1 arc-minute) should give ~9% slope."""
        rows, cols = 30, 30
        cell_size_deg = 0.001  # ~111 m
        lat_min, lat_max = 53.0, 53.0 + rows * cell_size_deg
        lng_min, lng_max = -113.6, -113.6 + cols * cell_size_deg
        # 10 m rise per row (northward)
        elev = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            elev[r, :] = (rows - r) * 10.0
        dem_path = str(tmp_path / "calibrated_slope.tif")
        _write_dem_tiff(dem_path, elev, lat_min=lat_min, lat_max=lat_max,
                        lng_min=lng_min, lng_max=lng_max)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        mid_r = rows // 2
        mid_c = cols // 2
        # cell_size_y ≈ 111 m, rise = 10 m → slope ≈ 9%
        # Allow ±50% tolerance for the geographic approximation
        slope = grid.slope[mid_r][mid_c]
        assert 3.0 < slope < 20.0, f"Expected ~9% slope, got {slope:.1f}%"

    def test_utm_crs_loads_correctly(self, tmp_path):
        """DEM in UTM CRS (projected metres) should load without error."""
        rows, cols = 30, 30
        elev = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            elev[r, :] = (rows - r) * 5.0  # 5 m rise per 10 m cell → 50% slope
        dem_path = str(tmp_path / "utm_dem.tif")
        _write_utm_dem_tiff(dem_path, elev, cell_size_m=10.0)
        grid = load_terrain_grid(dem_path, target_resolution_m=10.0)
        assert isinstance(grid, TerrainGrid)
        assert grid.rows > 0

    def test_utm_slope_magnitude(self, tmp_path):
        """UTM DEM: 5 m rise per 10 m cell → 50% slope in interior cells."""
        rows, cols = 30, 30
        elev = np.zeros((rows, cols), dtype=np.float32)
        for r in range(rows):
            elev[r, :] = (rows - r) * 5.0
        dem_path = str(tmp_path / "utm_steep.tif")
        _write_utm_dem_tiff(dem_path, elev, cell_size_m=10.0)
        grid = load_terrain_grid(dem_path, target_resolution_m=10.0)
        mid_r = rows // 2
        mid_c = cols // 2
        # Interior: 5 m / 10 m = 50% slope
        slope = grid.slope[mid_r][mid_c]
        assert 40.0 < slope < 65.0, f"Expected ~50% slope, got {slope:.1f}%"


class TestNodata:
    """Nodata cells should be filled without crashing."""

    def test_nodata_filled(self, tmp_path):
        """Nodata cells in DEM are filled and don't cause extreme slopes."""
        elev = np.full((20, 20), 700.0, dtype=np.float32)
        elev[5, 5] = -9999.0  # nodata cell
        dem_path = str(tmp_path / "nodata.tif")
        # Write with nodata value set
        rows, cols = elev.shape
        transform = from_bounds(-113.6, 53.0, -113.5, 53.1, cols, rows)
        with rasterio.open(
            dem_path, "w", driver="GTiff", height=rows, width=cols,
            count=1, dtype="float32",
            crs=CRS.from_epsg(4326), transform=transform,
            nodata=-9999.0,
        ) as dst:
            dst.write(elev, 1)
        grid = load_terrain_grid(dem_path, target_resolution_m=1.0)
        # No catastrophic slopes from the nodata fill
        max_slope = max(v for row in grid.slope for v in row)
        assert max_slope < 500.0, f"Nodata fill produced extreme slope: {max_slope:.0f}%"


def test_dem_is_placed_and_aspect_is_relative_to_true_north(tmp_path):
    """Regression: the DEM loader used to stretch projected rows/columns over the lat/lng
    bounding box (misplacing cells by kilometres for a UTM DEM) and took aspect from the
    projection's grid north. A plane rising toward UTM grid north at 113.5 W (2.5 degrees
    west of the zone 12 central meridian) must come out facing the true bearing of grid
    north there (about 2 degrees east of north)."""
    import math

    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import transform as warp_transform

    from firesim.data.dem_loader import load_terrain_grid

    crs = CRS.from_epsg(32612)
    west, north, cell = 333_000.0, 5_930_000.0, 50.0
    n = 60
    rows = np.arange(n, dtype=np.float32)[:, None]
    elev = np.repeat(700.0 + (n - rows) * cell * 0.10, n, axis=1)  # 10 % rise toward grid north
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32",
                       crs=crs, transform=from_origin(west, north, cell, cell)) as dst:
        dst.write(elev, 1)
    terrain = load_terrain_grid(str(path), target_resolution_m=50.0)

    x0, y0 = west + 30 * cell, north - 30 * cell
    (lng0, lng1), (lat0, lat1) = warp_transform(crs, "EPSG:4326", [x0, x0], [y0, y0 + 1000.0])
    expected = math.degrees(math.atan2((lng1 - lng0) * math.cos(math.radians(lat0)), lat1 - lat0)) % 360
    r = int((terrain.lat_max - lat0) / (terrain.lat_max - terrain.lat_min) * terrain.rows)
    c = int((lng0 - terrain.lng_min) / (terrain.lng_max - terrain.lng_min) * terrain.cols)
    assert terrain.slope[r][c] == pytest.approx(10.0, rel=0.05)
    assert abs((terrain.aspect[r][c] - expected + 180) % 360 - 180) < 0.5
    assert abs(expected) > 1.0  # the test only means something if grid and true north differ
