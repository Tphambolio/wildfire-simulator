"""Load Digital Elevation Model (DEM) GeoTIFFs and compute slope/aspect.

Produces TerrainGrid (slope %, aspect °) consumed by the Huygens wavelet
and cellular automaton spread models for CFFDRS slope-adjusted ROS.

Slope enters FBP as a slope-equivalent wind speed added vectorially to the
wind (net effective wind speed WSV and direction RAZ, ST-X-3 eqs 39-50); see
firesim.fbp.calculator.calculate_slope_adjustment. Aspect here is the
upslope azimuth.

References:
    Forestry Canada Fire Danger Group (1992). ST-X-3 eqs 39-50 (slope factor, net effective wind).
    ESRI (1996). How Aspect Works / How Slope Works (terrain derivatives).
"""

from __future__ import annotations

import logging
import math
import os

import numpy as np
import rasterio
import rasterio.errors
from rasterio.warp import transform_bounds

from firesim.spread.huygens import TerrainGrid

logger = logging.getLogger(__name__)

# Maximum raster dimensions before refusing to process (same limit as fuel loader)
_MAX_RASTER_CELLS = 10_000 * 10_000


def load_terrain_grid(
    path: str,
    target_resolution_m: float = 50.0,
) -> TerrainGrid:
    """Load a DEM GeoTIFF and return a TerrainGrid with slope and aspect.

    Slope and aspect are derived from the elevation surface using central
    finite differences (numpy gradient), then stored as per-cell arrays.

    Slope is percent slope (tan(angle) × 100). Aspect is the upslope bearing
    in degrees clockwise from north (0 = slope faces north, 90 = faces east).
    Flat cells (slope < 0.1%) are assigned aspect 0.

    Args:
        path: Absolute path to GeoTIFF DEM (single-band, elevation in metres).
        target_resolution_m: Target cell size in metres after downsampling.
            Match or be coarser than the fuel grid resolution. Default 50 m.

    Returns:
        TerrainGrid with slope (%) and aspect (°) arrays in WGS84 extent.

    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If raster exceeds the maximum processable size.
        rasterio.errors.RasterioIOError: If the file is corrupt or unreadable.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DEM GeoTIFF not found: {path!r}. "
            "Check the path or set FIRESIM_DEM_PATH in the environment."
        )

    try:
        rasterio_ctx = rasterio.open(path)
    except rasterio.errors.RasterioIOError as exc:
        raise rasterio.errors.RasterioIOError(
            f"Cannot open DEM GeoTIFF {path!r}: {exc}. "
            "The file may be corrupt, truncated, or not a valid GeoTIFF."
        ) from exc

    with rasterio_ctx as src:
        elevation = src.read(1).astype(np.float32)  # Band 1, metres
        nodata = src.nodata
        src_crs = src.crs
        src_bounds = src.bounds  # left, bottom, right, top in source CRS
        src_res = src.res         # (x_res, y_res) in source CRS units

    # Fill nodata with the raster mean so gradients are continuous at edges
    if nodata is not None:
        nodata_mask = elevation == float(nodata)
        if nodata_mask.any():
            valid_mean = float(np.nanmean(elevation[~nodata_mask]))
            elevation[nodata_mask] = valid_mean
            logger.debug(
                "DEM nodata: filled %d cells with mean elevation %.1f m",
                int(nodata_mask.sum()), valid_mean,
            )

    # Transform bounds to WGS84 (lat/lng) for TerrainGrid storage
    lng_min, lat_min, lng_max, lat_max = transform_bounds(
        src_crs, "EPSG:4326",
        src_bounds.left, src_bounds.bottom,
        src_bounds.right, src_bounds.top,
    )

    # Determine cell size in metres for gradient scaling
    if src_crs.is_projected:
        # UTM, Lambert, etc. — res is already in metres
        cell_y_m = abs(src_res[1])  # y-resolution (positive value)
        cell_x_m = abs(src_res[0])  # x-resolution
    else:
        # Geographic CRS (degrees) — approximate metres at raster centre
        lat_centre = (lat_min + lat_max) / 2.0
        cell_y_m = abs(src_res[1]) * 111320.0
        cell_x_m = abs(src_res[0]) * 111320.0 * math.cos(math.radians(lat_centre))

    # Downsample if source resolution is finer than target
    src_res_m = min(cell_y_m, cell_x_m)
    if src_res_m < target_resolution_m:
        from scipy.ndimage import zoom

        scale = src_res_m / target_resolution_m
        elevation = zoom(elevation, scale, order=1)  # Bilinear for smoother gradients
        cell_y_m /= scale
        cell_x_m /= scale
        logger.info(
            "DEM downsampled %.0f m → %.0f m: shape now %dx%d",
            src_res_m, target_resolution_m, *elevation.shape,
        )

    rows, cols = elevation.shape

    if rows * cols > _MAX_RASTER_CELLS:
        raise ValueError(
            f"DEM too large: {rows}×{cols} = {rows * cols:,} cells "
            f"(limit {_MAX_RASTER_CELLS:,}). "
            f"Increase target_resolution_m (currently {target_resolution_m} m)."
        )

    # --- Slope and aspect via finite differences ---
    # np.gradient(f, dy, dx) → [∂f/∂y, ∂f/∂x]
    # axis-0 spacing = cell_y_m (rows increase southward in standard rasters)
    # axis-1 spacing = cell_x_m (cols increase eastward)
    dz_south, dz_east = np.gradient(elevation, cell_y_m, cell_x_m)

    # Convert to north/east components of steepest ascent
    #   going north = decreasing row → dz_north = -dz_south
    dz_north = -dz_south  # m/m (dimensionless)

    # Slope percentage: tan(slope_angle) × 100
    slope_pct = np.sqrt(dz_north ** 2 + dz_east ** 2) * 100.0

    # Aspect: bearing of steepest upslope direction, clockwise from north
    #   atan2(east_component, north_component) → angle from north, clockwise
    aspect_deg = np.degrees(np.arctan2(dz_east, dz_north)) % 360.0

    # Flat cells get aspect 0 (undefined, will have no slope effect anyway)
    aspect_deg[slope_pct < 0.1] = 0.0

    # Convert numpy arrays to Python lists (required by TerrainGrid)
    slope_list: list[list[float]] = slope_pct.tolist()
    aspect_list: list[list[float]] = aspect_deg.tolist()

    grid = TerrainGrid(
        slope=slope_list,
        aspect=aspect_list,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )

    # Summary stats
    valid_slope = slope_pct[slope_pct > 0.1]
    logger.info(
        "Loaded DEM %dx%d from %s — "
        "bounds: %.4f–%.4f N, %.4f–%.4f E — "
        "slope: mean=%.1f%%, max=%.1f%%, "
        "cells >20%%=%.1f%%",
        rows, cols, path,
        lat_min, lat_max, lng_min, lng_max,
        float(valid_slope.mean()) if len(valid_slope) else 0.0,
        float(slope_pct.max()),
        100.0 * float((slope_pct > 20.0).sum()) / (rows * cols),
    )

    return grid
