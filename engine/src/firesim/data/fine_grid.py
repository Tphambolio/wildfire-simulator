"""The fuel raster at its native resolution, cropped on demand for the 20 m WUI window.

The API's run grid is the fuel raster coarsened to 50 m (``load_fuel_grid``, majority class).
For the WUI window (``firesim.spread.wui_window``, mechanics decision M5) the run is repeated at
the raster's own resolution (20 m for the Edmonton grid) on a crop around the fire. This module
keeps the native grid compactly (one byte per cell: an index into a small table of fuel
types, 3 MB for Edmonton) and builds a ``FuelGrid`` for a requested box. No resampling is done
beyond the reprojection onto the lat/lng grid at the source cell size (nearest neighbour, as the
cells are the same size); the crop's cells are the native cells.
"""

from __future__ import annotations

import logging
import math
import os

import numpy as np

from firesim.data.raster_grid import M_PER_DEG_LAT, read_to_latlng, source_resolution_m
from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid

logger = logging.getLogger(__name__)


class FineFuelSource:
    """A fuel raster on a lat/lng grid at its native cell size, cropped per run.

    Built with ``from_raster`` (the API) or ``from_fuel_grid`` (tests).
    """

    def __init__(self, idx: np.ndarray, types: list, bounds: tuple[float, float, float, float],
                 cell_m: float | None = None):
        """``idx`` (rows, cols) uint8, row 0 = north: 0 = non-fuel, k = ``types[k]``;
        ``bounds`` = (lat_min, lat_max, lng_min, lng_max)."""
        self.idx = np.asarray(idx, dtype=np.uint8)
        self.types: list[FuelType | None] = list(types)
        self.lat_min, self.lat_max, self.lng_min, self.lng_max = (float(v) for v in bounds)
        self.rows, self.cols = self.idx.shape
        self.cell_lat = (self.lat_max - self.lat_min) / self.rows
        self.cell_lng = (self.lng_max - self.lng_min) / self.cols
        mid = math.radians((self.lat_max + self.lat_min) / 2.0)
        self.cell_m = float(cell_m) if cell_m is not None else min(
            self.cell_lat * M_PER_DEG_LAT, self.cell_lng * M_PER_DEG_LAT * math.cos(mid))

    @classmethod
    def from_fuel_grid(cls, grid: FuelGrid) -> "FineFuelSource":
        """Compact copy of a ``FuelGrid`` (tests, synthetic landscapes)."""
        types: list = [None]
        idx = np.zeros((grid.rows, grid.cols), dtype=np.uint8)
        for r, row in enumerate(grid.fuel_types):
            for c, ft in enumerate(row):
                if ft is not None:
                    if ft not in types:
                        types.append(ft)
                    idx[r, c] = types.index(ft)
        return cls(idx, types, (grid.lat_min, grid.lat_max, grid.lng_min, grid.lng_max))

    @classmethod
    def from_raster(cls, path: str, water_path: str | None = None,
                    code_scheme: str = "auto") -> "FineFuelSource":
        """Load ``path`` at its native resolution.

        Args:
            path: GeoTIFF with integer fuel codes (the file the run grid is made from).
            water_path: optional water GeoJSON, masked as non-fuel once at load (as
                ``load_fuel_grid`` does for the run grid).
            code_scheme: as ``load_fuel_grid``; "auto" detects it from the whole raster, so
                every crop uses the run grid's scheme.
        """
        import rasterio

        from firesim.data.fuel_loader import _detect_code_map, normalize_fuel_codes

        if not os.path.exists(path):
            raise FileNotFoundError(f"Fuel grid GeoTIFF not found: {path!r}")
        with rasterio.open(path) as src:
            native_m = source_resolution_m(src)
        grid = read_to_latlng(path, native_m, "categorical", "float64", np.nan)
        codes = normalize_fuel_codes(grid.data, None)
        grid.data = None
        present = np.unique(codes).tolist()
        code_map = _detect_code_map(set(present), code_scheme)
        types: list[FuelType | None] = [None]
        idx = np.zeros(codes.shape, dtype=np.uint8)
        for code in present:
            ft = code_map.get(int(code))
            if ft is None:
                continue
            if ft not in types:
                types.append(ft)
            idx[codes == code] = types.index(ft)
        if len(types) > 255:
            raise ValueError("too many fuel types for the compact native grid")
        del codes
        bounds = (grid.lat_min, grid.lat_max, grid.lng_min, grid.lng_max)
        if water_path:
            from firesim.data.environment import load_environment_mask

            water = load_environment_mask(bounds=bounds, rows=idx.shape[0], cols=idx.shape[1],
                                          water_path=water_path)
            idx[water] = 0
        src = cls(idx, types, bounds, cell_m=grid.cell_m)
        logger.info("Native fuel grid %s: %dx%d at %.0f m (%.1f MB)", os.path.basename(path),
                    src.rows, src.cols, src.cell_m, idx.nbytes / 1e6)
        return src

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.lat_min, self.lat_max, self.lng_min, self.lng_max

    def cell_range(self, bounds) -> tuple[int, int, int, int]:
        """(r0, r1, c0, c1), end-exclusive, of the native cells covering ``bounds``
        (lat_min, lat_max, lng_min, lng_max), clipped to the grid."""
        lat_min, lat_max, lng_min, lng_max = bounds
        r0 = max(0, int(math.floor((self.lat_max - lat_max) / self.cell_lat + 1e-9)))
        r1 = min(self.rows, int(math.ceil((self.lat_max - lat_min) / self.cell_lat - 1e-9)))
        c0 = max(0, int(math.floor((lng_min - self.lng_min) / self.cell_lng + 1e-9)))
        c1 = min(self.cols, int(math.ceil((lng_max - self.lng_min) / self.cell_lng - 1e-9)))
        return r0, max(r1, r0 + 1), c0, max(c1, c0 + 1)

    def cells_in(self, bounds) -> int:
        """Number of native cells a crop of ``bounds`` would hold."""
        r0, r1, c0, c1 = self.cell_range(bounds)
        return (r1 - r0) * (c1 - c0)

    def crop(self, bounds, building_geoms: list | None = None) -> FuelGrid:
        """``FuelGrid`` of the native cells covering ``bounds``, its edges on native cell edges.

        ``building_geoms`` (shapely, lng/lat) are masked as non-fuel on the crop with the same
        all-touched rule as the run grid's building mask (``load_environment_mask``), so at
        20 m a footprint makes non-fuel only the 20 m cells it touches.
        """
        r0, r1, c0, c1 = self.cell_range(bounds)
        sub = self.idx[r0:r1, c0:c1].copy()
        lat_max = self.lat_max - r0 * self.cell_lat
        lat_min = self.lat_max - r1 * self.cell_lat
        lng_min = self.lng_min + c0 * self.cell_lng
        lng_max = self.lng_min + c1 * self.cell_lng
        rows, cols = sub.shape
        if building_geoms:
            import shapely

            from firesim.data.environment import load_environment_mask

            geoms = np.asarray(building_geoms, dtype=object)
            hit = shapely.intersects(geoms, shapely.box(lng_min, lat_min, lng_max, lat_max))
            if hit.any():
                mask = load_environment_mask(bounds=(lat_min, lat_max, lng_min, lng_max),
                                             rows=rows, cols=cols,
                                             building_geoms=list(geoms[hit]))
                sub[mask] = 0
        types = self.types
        fuel_types = [[types[v] for v in row] for row in sub.tolist()]
        return FuelGrid(fuel_types=fuel_types, lat_min=lat_min, lat_max=lat_max,
                        lng_min=lng_min, lng_max=lng_max, rows=rows, cols=cols)

    def nbytes(self) -> int:
        return int(self.idx.nbytes)

