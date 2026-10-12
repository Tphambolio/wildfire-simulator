"""Terrain (slope, aspect) for runs outside Edmonton, read by window from a provincial
elevation file (NRCan MRDEM-30 averaged to ~100 m; ``scripts/build_ab_terrain.py``).

The file is never read whole: ``terrain_on_lattice`` reads only the source window under the
run's lat/lng lattice (plus a margin), interpolates elevation bilinearly onto the lattice and
derives slope and aspect there with the same finite differences as the Edmonton DEM
(``dem_loader.slope_aspect``), so aspect is relative to true north. A 40 km window at 50 m reads
about 450 x 270 uint16 cells (0.25 MB) and holds two 800 x 800 float rows (~10 MB).

Selection (``docs/terrain-alberta.md``): the Edmonton DEM where the run uses the City grid's
lattice, this file for provincial / national windows when the request asks for the bundled
terrain; elsewhere nothing changes.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from firesim.data.dem_loader import _float_rows, slope_aspect
from firesim.data.raster_grid import M_PER_DEG_LAT
from firesim.spread.huygens import TerrainGrid

logger = logging.getLogger(__name__)

AB_TERRAIN_LABEL = "NRCan MRDEM-30 elevation, ~100 m (Alberta)"
AB_TERRAIN_ATTRIBUTION = ("Contains information licensed under the Open Government Licence – "
                          "Canada (NRCan Medium Resolution Digital Elevation Model, MRDEM-30).")
CITY_DEM_LABEL = "Edmonton 30 m DEM"


def _scale(src) -> float:
    """Metres per stored unit: the file's SCALE tag (decimetres -> 0.1), else band scale."""
    tag = src.tags().get("SCALE")
    if tag:
        return float(tag)
    return float(src.scales[0] or 1.0)


def elevation_on_lattice(path: str, lattice, margin: int = 1) -> np.ndarray:
    """Elevation (m, float64, NaN where none) on ``lattice`` grown by ``margin`` cells on
    every side (row 0 north). Reads only the source window under it (+2 cells)."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    rows, cols = lattice.rows + 2 * margin, lattice.cols + 2 * margin
    lat_max = lattice.lat_max + margin * lattice.dlat
    lng_min = lattice.lng_min - margin * lattice.dlng
    lat_min = lat_max - rows * lattice.dlat
    lng_max = lng_min + cols * lattice.dlng
    dst = np.full((rows, cols), np.nan, dtype=np.float64)
    with rasterio.open(path) as src:
        scale = _scale(src)
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, lng_min, lat_min,
                                                    lng_max, lat_max, densify_pts=21)
        win = from_bounds(left, bottom, right, top, src.transform)
        c0 = max(0, int(math.floor(win.col_off)) - 2)
        r0 = max(0, int(math.floor(win.row_off)) - 2)
        c1 = min(src.width, int(math.ceil(win.col_off + win.width)) + 2)
        r1 = min(src.height, int(math.ceil(win.row_off + win.height)) + 2)
        if c1 > c0 and r1 > r0:
            window = Window(c0, r0, c1 - c0, r1 - r0)
            data = src.read(1, window=window).astype(np.float64)
            nod = src.nodata
            if nod is not None:
                data[data == nod] = np.nan
            data *= scale
            reproject(source=data, destination=dst,
                      src_transform=src.window_transform(window), src_crs=src.crs,
                      src_nodata=np.nan, dst_transform=from_origin(lng_min, lat_max,
                                                                   lattice.dlng, lattice.dlat),
                      dst_crs="EPSG:4326", dst_nodata=np.nan, resampling=Resampling.bilinear)
            del data
    return dst


def terrain_on_lattice(path: str, lattice) -> TerrainGrid | None:
    """Slope (%) and aspect (deg) on ``lattice`` from the elevation file, or None when the
    file has no elevation under it. Cells without elevation inside a partly covered window are
    filled with the window's mean (flat there), as ``load_terrain_grid`` does."""
    elev = elevation_on_lattice(path, lattice, margin=1)
    ok = np.isfinite(elev)
    if not ok.any():
        return None
    if not ok.all():
        elev[~ok] = float(np.nanmean(elev))
    lat_mid = lattice.lat_max - lattice.rows * lattice.dlat / 2.0
    cell_y = lattice.dlat * M_PER_DEG_LAT
    cell_x = lattice.dlng * M_PER_DEG_LAT * math.cos(math.radians(lat_mid))
    slope, aspect = slope_aspect(elev, cell_y, cell_x)
    slope, aspect = slope[1:-1, 1:-1], aspect[1:-1, 1:-1]
    lat_min, lat_max, lng_min, lng_max = lattice.bounds
    logger.info("Provincial terrain %dx%d: slope mean %.1f%%, max %.0f%%, cells > 20%%: %.1f%%",
                lattice.rows, lattice.cols, float(slope.mean()), float(slope.max()),
                100.0 * float((slope > 20).mean()))
    return TerrainGrid(slope=_float_rows(slope), aspect=_float_rows(aspect), lat_min=lat_min,
                       lat_max=lat_max, lng_min=lng_min, lng_max=lng_max, rows=lattice.rows,
                       cols=lattice.cols)


def terrain_covers(path: str, lat: float, lng: float) -> bool:
    """True when the elevation file has a value at the point."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    with rasterio.open(path) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lng], [lat])
        r, c = src.index(xs[0], ys[0])
        if not (0 <= r < src.height and 0 <= c < src.width):
            return False
        v = src.read(1, window=Window(c, r, 1, 1))[0, 0]
        return src.nodata is None or v != src.nodata
