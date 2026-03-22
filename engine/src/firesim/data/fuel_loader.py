"""Load FBP fuel grids from GeoTIFF rasters."""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import rasterio
import rasterio.errors
from rasterio.warp import transform_bounds
from scipy.ndimage import zoom

from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid

logger = logging.getLogger(__name__)

# Maximum raster dimensions before refusing to process
_MAX_RASTER_CELLS = 10_000 * 10_000


def _warn_geojson_crs(path: str, context: str) -> None:
    """Warn if a GeoJSON file declares a non-WGS84 CRS.

    The GeoJSON spec (RFC 7946) mandates WGS84, but legacy files may carry a
    ``crs`` member pointing at a projected CRS. If the grid pipeline assumes
    WGS84 coordinates and the file is in something else, features will be
    misaligned.
    """
    try:
        import gzip
        import json
        from pathlib import Path

        p = Path(path)
        if p.suffix == ".gz":
            with gzip.open(p, "rt", encoding="utf-8") as f:
                top = json.load(f)
        else:
            with open(p, encoding="utf-8") as f:
                top = json.load(f)

        crs_obj = top.get("crs")
        if crs_obj is None:
            return  # No explicit CRS → assume WGS84 per RFC 7946

        props = crs_obj.get("properties", {})
        name = props.get("name", "")
        # WGS84 variants are fine
        if any(tag in name.upper() for tag in ("EPSG:4326", "CRS84", "WGS84", "WGS 84")):
            return

        logger.warning(
            "CRS mismatch: %s declares CRS '%s' which may not be WGS84. "
            "The fuel grid pipeline expects WGS84 coordinates. "
            "Context: %s",
            path, name, context,
        )
    except Exception:
        pass  # Best-effort; don't break loading over a CRS-check failure


# Edmonton FBP raster codes (Edmonton_FBP_FuelLayer_20251105_10m.tif)
# Verified from classification_statistics.csv
FBP_RASTER_CODES: dict[int, FuelType | None] = {
    -9999: None,
    0: None,
    1: FuelType.C1,
    12: FuelType.D2,
    32: FuelType.S2,
    41: FuelType.O1a,
    42: FuelType.O1b,
}

# uPLVI raster codes (Edmonton_uPLVI_FuelLayer_20251106_10m_COG.tif)
# From wildfire-self-learning/src/data/uplvi_fuel_loader_raster.py
UPLVI_RASTER_CODES: dict[int, FuelType | None] = {
    0: None,
    2: FuelType.C2,
    12: FuelType.D2,
    22: FuelType.M2,
    31: FuelType.O1a,
    32: FuelType.O1b,
}

# Broad mapping covering both raster types plus standard FBP numeric codes
ALL_CODES: dict[int, FuelType | None] = {
    -9999: None,
    0: None,
    1: FuelType.C1,
    2: FuelType.C2,
    3: FuelType.C3,
    4: FuelType.C4,
    5: FuelType.C5,
    6: FuelType.C6,
    7: FuelType.C7,
    11: FuelType.D1,
    12: FuelType.D2,
    22: FuelType.M2,
    31: FuelType.O1a,
    32: FuelType.S2,
    41: FuelType.O1a,
    42: FuelType.O1b,
    99: None,
    100: None,
}


def _detect_code_map(unique_codes: set[int]) -> dict[int, FuelType | None]:
    """Pick the right code mapping based on codes present in the raster."""
    if 42 in unique_codes and 22 not in unique_codes:
        logger.info("Detected FBP raster code scheme")
        return FBP_RASTER_CODES
    if 22 in unique_codes and 42 not in unique_codes:
        logger.info("Detected uPLVI raster code scheme")
        return UPLVI_RASTER_CODES
    logger.info("Using broad code mapping (ambiguous raster)")
    return ALL_CODES


def load_fuel_grid(
    path: str,
    target_resolution_m: float = 50.0,
    water_path: str | None = None,
    buildings_path: str | None = None,
) -> FuelGrid:
    """Load a GeoTIFF fuel raster and return a FuelGrid.

    Args:
        path: Path to GeoTIFF with integer FBP fuel codes.
        target_resolution_m: Target cell size in meters for downsampling.
            Smaller = more detail but more memory. Default 50m.
        water_path: Optional path to water body GeoJSON for masking.
        buildings_path: Optional path to building footprint GeoJSON for masking.

    Returns:
        FuelGrid ready for use with Simulator.

    Raises:
        FileNotFoundError: If the GeoTIFF path does not exist.
        ValueError: If the raster exceeds the maximum processable size.
        rasterio.errors.RasterioIOError: If the file is corrupt or unreadable.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Fuel grid GeoTIFF not found: {path!r}. "
            "Check that the path is correct and the file has been uploaded."
        )

    # Warn about potential CRS mismatches in overlay files before loading
    if water_path and os.path.exists(water_path):
        _warn_geojson_crs(water_path, "water bodies")
    if buildings_path and os.path.exists(buildings_path):
        _warn_geojson_crs(buildings_path, "buildings")

    try:
        rasterio_ctx = rasterio.open(path)
    except rasterio.errors.RasterioIOError as exc:
        raise rasterio.errors.RasterioIOError(
            f"Cannot open fuel grid GeoTIFF {path!r}: {exc}. "
            "The file may be corrupt, truncated, or not a valid GeoTIFF."
        ) from exc

    with rasterio_ctx as src:
        data = src.read(1)  # Band 1, int array
        nodata = src.nodata
        src_crs = src.crs
        src_bounds = src.bounds  # left, bottom, right, top
        src_res = src.res  # (x_res, y_res) in source CRS units

    # Transform bounds to WGS84
    lng_min, lat_min, lng_max, lat_max = transform_bounds(
        src_crs, "EPSG:4326",
        src_bounds.left, src_bounds.bottom,
        src_bounds.right, src_bounds.top,
    )

    # Replace nodata with -9999 for consistent mapping
    if nodata is not None:
        data[data == int(nodata)] = -9999

    # Downsample if source resolution is finer than target
    src_res_m = abs(src_res[0])  # Approximate meters (works for UTM)
    if src_res_m < target_resolution_m:
        scale = src_res_m / target_resolution_m
        data = zoom(data, scale, order=0)  # Nearest-neighbor
        logger.info(
            "Downsampled from %.0fm to %.0fm: %dx%d → %dx%d",
            src_res_m, target_resolution_m,
            *reversed(data.shape), data.shape[1], data.shape[0],
        )

    rows, cols = data.shape

    # Refuse to process rasters that would exceed memory/time budgets
    if rows * cols > _MAX_RASTER_CELLS:
        raise ValueError(
            f"Raster too large to process: {rows}x{cols} = {rows * cols:,} cells "
            f"(limit {_MAX_RASTER_CELLS:,}). Increase target_resolution_m "
            f"(currently {target_resolution_m}m) to reduce dimensions."
        )

    # Detect code mapping from unique values
    unique_codes = set(np.unique(data).tolist())
    code_map = _detect_code_map(unique_codes)

    # Map integer codes to FuelType enums
    fuel_types: list[list[FuelType | None]] = []
    for r in range(rows):
        row_list: list[FuelType | None] = []
        for c in range(cols):
            code = int(data[r, c])
            row_list.append(code_map.get(code))
        fuel_types.append(row_list)

    # Apply environment mask (water bodies, buildings → non-fuel)
    if water_path or buildings_path:
        from firesim.data.environment import load_environment_mask

        mask = load_environment_mask(
            bounds=(lat_min, lat_max, lng_min, lng_max),
            rows=rows,
            cols=cols,
            water_path=water_path,
            buildings_path=buildings_path,
        )
        masked_count = 0
        for r in range(rows):
            for c in range(cols):
                if mask[r, c] and fuel_types[r][c] is not None:
                    fuel_types[r][c] = None
                    masked_count += 1
        logger.info("Environment mask applied: %d fuel cells → non-fuel", masked_count)

    grid = FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )

    # Log summary and memory estimate
    fuel_counts: dict[str, int] = {}
    non_fuel = 0
    for row in fuel_types:
        for ft in row:
            if ft is None:
                non_fuel += 1
            else:
                fuel_counts[ft.value] = fuel_counts.get(ft.value, 0) + 1
    total = rows * cols
    # Estimate memory: numpy array (int32 = 4 bytes/cell) + Python list overhead
    # (~56 bytes/object for enum refs). Report the numpy portion as a lower bound.
    mem_numpy_mb = (data.nbytes) / (1024 * 1024)
    mem_list_mb = (total * sys.getsizeof(None)) / (1024 * 1024)
    logger.info(
        "Loaded fuel grid %dx%d from %s — "
        "bounds: %.4f-%.4fN, %.4f-%.4fW — "
        "%d fuel cells, %d non-fuel (%.1f%%) — "
        "raw raster ~%.1f MB, grid list ~%.1f MB",
        rows, cols, path,
        lat_min, lat_max, lng_min, lng_max,
        total - non_fuel, non_fuel, 100.0 * non_fuel / total,
        mem_numpy_mb, mem_list_mb,
    )
    for code, count in sorted(fuel_counts.items(), key=lambda x: -x[1]):
        logger.info("  %s: %d (%.1f%%)", code, count, 100.0 * count / total)

    return grid
