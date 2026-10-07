"""Read a raster onto a regular latitude/longitude grid.

FuelGrid and TerrainGrid index cells linearly in latitude and longitude, so every raster
must be reprojected onto such a grid before use. Taking a projected raster's bounding box
in lat/lng and keeping its rows and columns (what the loaders did before 2026-10-07)
misplaces cells by up to hundreds of metres for the Edmonton fuel grid (EPSG:3776) and
kilometres for a UTM DEM, because projected rows and columns are not lines of constant
latitude and longitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

M_PER_DEG_LAT = 111_320.0


@dataclass
class LatLngRaster:
    data: np.ndarray  # (rows, cols), row 0 = north
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    cell_m: float  # nominal cell size (metres) used to build the grid


def source_resolution_m(src) -> float:
    """Approximate source cell size in metres (projected or geographic CRS)."""
    rx, ry = abs(src.res[0]), abs(src.res[1])
    if src.crs is not None and src.crs.is_projected:
        return min(rx, ry)
    lng_min, lat_min, lng_max, lat_max = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    mid = math.radians((lat_min + lat_max) / 2.0)
    return min(ry * M_PER_DEG_LAT, rx * M_PER_DEG_LAT * math.cos(mid))


def read_to_latlng(
    path: str,
    target_resolution_m: float,
    resampling: Resampling,
    dtype: str,
    dst_nodata: float,
) -> LatLngRaster:
    """Reproject band 1 of ``path`` onto a lat/lng grid of about ``target_resolution_m`` cells.

    The grid is never finer than the source (no upsampling). Cells outside the source
    footprint get ``dst_nodata``.
    """
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(f"Raster {path!r} has no CRS; cannot place it on the map")
        cell_m = max(float(target_resolution_m), source_resolution_m(src))
        lng_min, lat_min, lng_max, lat_max = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        mid = math.radians((lat_min + lat_max) / 2.0)
        dlat = cell_m / M_PER_DEG_LAT
        dlng = cell_m / (M_PER_DEG_LAT * math.cos(mid))
        cols = max(1, math.ceil((lng_max - lng_min) / dlng))
        rows = max(1, math.ceil((lat_max - lat_min) / dlat))
        dst = np.full((rows, cols), dst_nodata, dtype=dtype)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=from_origin(lng_min, lat_max, dlng, dlat),
            dst_crs="EPSG:4326",
            dst_nodata=dst_nodata,
            resampling=resampling,
        )
    return LatLngRaster(
        data=dst, lat_min=lat_max - rows * dlat, lat_max=lat_max,
        lng_min=lng_min, lng_max=lng_min + cols * dlng, cell_m=cell_m,
    )
