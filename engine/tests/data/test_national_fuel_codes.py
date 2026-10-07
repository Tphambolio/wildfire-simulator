"""National FBP fuel grid code schemes (CFS / CWFIS)."""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from firesim.data.fuel_loader import (
    CFS_NATIONAL_2014_CODES,
    CFS_NATIONAL_CODES,
    CODE_SCHEMES,
    TERRA_PIPELINE_CODES,
    UPLVI_RASTER_CODES,
    _detect_code_map,
    cfs_national_modifier,
    load_fuel_grid,
)
from firesim.fbp.constants import FuelType


class TestCfsNational:
    """CIFFC standard codes of the 2019 / 2024 national grids."""

    @pytest.mark.parametrize("code,fuel", [
        (1, FuelType.C1), (2, FuelType.C2), (3, FuelType.C3), (4, FuelType.C4),
        (5, FuelType.C5), (6, FuelType.C6), (7, FuelType.C7),
        (11, FuelType.D1), (12, FuelType.D2), (13, FuelType.D1),
        (21, FuelType.S1), (22, FuelType.S2), (23, FuelType.S3),
        (31, FuelType.O1a), (32, FuelType.O1b),
        (40, FuelType.M1), (50, FuelType.M2), (60, FuelType.M1),
        (70, FuelType.M3), (80, FuelType.M4), (90, FuelType.M3),
        (425, FuelType.M1), (550, FuelType.M2), (650, FuelType.M1), (675, FuelType.M1),
        (705, FuelType.M3), (835, FuelType.M4), (995, FuelType.M3),
    ])
    def test_fuel_codes(self, code, fuel):
        assert CFS_NATIONAL_CODES[code] is fuel

    @pytest.mark.parametrize("code", [100, 101, 102, 103, 104, 105, 106, 0, -9999])
    def test_non_fuel_codes(self, code):
        assert CFS_NATIONAL_CODES[code] is None

    def test_every_mixedwood_percentage_mapped(self):
        for group in range(4, 10):
            for pct in range(5, 100, 5):
                assert CFS_NATIONAL_CODES[group * 100 + pct] is not None

    def test_modifier_decoding(self):
        assert cfs_national_modifier(650) == ("pc", 50.0)
        assert cfs_national_modifier(405) == ("pc", 5.0)
        assert cfs_national_modifier(595) == ("pc", 95.0)
        assert cfs_national_modifier(835) == ("pdf", 35.0)
        assert cfs_national_modifier(905) == ("pdf", 5.0)
        assert cfs_national_modifier(60) is None
        assert cfs_national_modifier(2) is None
        assert cfs_national_modifier(101) is None

    def test_not_auto_detected(self):
        # Low CIFFC codes collide with local schemes (22 = S-2 here, M-2 in uPLVI)
        assert _detect_code_map({2, 12, 22, 31}) is UPLVI_RASTER_CODES
        assert _detect_code_map({1, 12, 13, 14, 31}) is TERRA_PIPELINE_CODES

    def test_explicit_scheme(self):
        assert _detect_code_map({2, 22, 650}, "cfs_national") is CFS_NATIONAL_CODES


class TestCfsNational2014:
    """nat_fbpfuels_2014b codes (101-122)."""

    @pytest.mark.parametrize("code,fuel", [
        (101, FuelType.C1), (102, FuelType.C2), (103, FuelType.C3), (104, FuelType.C4),
        (105, FuelType.C5), (106, FuelType.C6), (107, FuelType.C7), (108, FuelType.D1),
        (109, FuelType.M1), (110, FuelType.M2), (111, FuelType.M3), (112, FuelType.M4),
        (113, FuelType.S1), (114, FuelType.S2), (115, FuelType.S3),
        (116, FuelType.O1a), (117, FuelType.O1b),
    ])
    def test_fuel_codes(self, code, fuel):
        assert CFS_NATIONAL_2014_CODES[code] is fuel

    @pytest.mark.parametrize("code", [118, 119, 120, 121, 122])
    def test_non_fuel_codes(self, code):
        assert CFS_NATIONAL_2014_CODES[code] is None

    def test_auto_detected(self):
        assert _detect_code_map({101, 102, 108, 109, 118, 119, 120, 122}) is CFS_NATIONAL_2014_CODES

    def test_registered(self):
        assert CODE_SCHEMES["cfs_national_2014"] is CFS_NATIONAL_2014_CODES
        assert CODE_SCHEMES["cfs_national"] is CFS_NATIONAL_CODES

    def test_load_fuel_grid(self, tmp_path):
        data = np.array([[102, 108], [116, 118]], dtype=np.uint8)
        path = tmp_path / "nat.tif"
        with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1, dtype="uint8",
                           crs=CRS.from_epsg(32612), transform=from_origin(350000, 5930000, 50, 50),
                           nodata=0) as dst:
            dst.write(data, 1)
        grid = load_fuel_grid(str(path), target_resolution_m=50.0)
        assert grid.fuel_types == [[FuelType.C2, FuelType.D1], [FuelType.O1a, None]]
