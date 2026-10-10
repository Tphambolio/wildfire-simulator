"""Region / AOI grid helpers (EPSG:3776, 20 m) and raster I/O with provenance tags."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

try:
    from . import config as C
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float  # top
    width: int
    height: int
    cell: float = C.CELL
    crs: str = C.CRS

    @property
    def transform(self):
        return from_origin(self.x0, self.y0, self.cell, self.cell)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0 - self.height * self.cell, self.x0 + self.width * self.cell, self.y0)

    @classmethod
    def from_bounds(cls, b: tuple[float, float, float, float], cell: float = C.CELL) -> "Grid":
        x0 = np.floor(b[0] / cell) * cell
        y1 = np.ceil(b[3] / cell) * cell
        x1 = np.ceil(b[2] / cell) * cell
        y0 = np.floor(b[1] / cell) * cell
        return cls(float(x0), float(y1), int(round((x1 - x0) / cell)), int(round((y1 - y0) / cell)), cell)

    def window_of(self, sub: "Grid") -> Window:
        """Window of `sub` inside this grid (both must share cell size and alignment)."""
        col = (sub.x0 - self.x0) / self.cell
        row = (self.y0 - sub.y0) / self.cell
        if abs(col - round(col)) > 1e-6 or abs(row - round(row)) > 1e-6:
            raise ValueError("grids are not aligned")
        col, row = int(round(col)), int(round(row))
        if col < 0 or row < 0 or col + sub.width > self.width or row + sub.height > self.height:
            raise ValueError("sub-grid extends outside the region grid")
        return Window(col, row, sub.width, sub.height)

    def slices(self, sub: "Grid") -> tuple[slice, slice]:
        w = self.window_of(sub)
        return (slice(w.row_off, w.row_off + w.height), slice(w.col_off, w.col_off + w.width))


REGION = Grid.from_bounds(C.REGION_BOUNDS)


def write_tif(path: Path, data: np.ndarray, grid: Grid, nodata=None, tags: dict | None = None,
              band_names: list[str] | None = None, band_tags: list[dict] | None = None) -> None:
    """Write a (bands, rows, cols) or (rows, cols) array as a tiled, deflated GeoTIFF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = data if data.ndim == 3 else data[None]
    prof = {"driver": "GTiff", "width": grid.width, "height": grid.height, "count": arr.shape[0],
            "dtype": arr.dtype.name, "crs": grid.crs, "transform": grid.transform, "nodata": nodata,
            "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256}
    if arr.dtype.kind == "f":
        prof["predictor"] = 3
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr)
        if tags:
            dst.update_tags(**{k: (v if isinstance(v, str) else json.dumps(v)) for k, v in tags.items()})
        for i in range(arr.shape[0]):
            if band_names:
                dst.set_band_description(i + 1, band_names[i])
            if band_tags and band_tags[i]:
                dst.update_tags(i + 1, **band_tags[i])


def read_tif(path: Path, window_grid: Grid | None = None, region: Grid = REGION) -> np.ndarray:
    """Read all bands of a region raster, optionally the window of an AOI grid."""
    with rasterio.open(path) as src:
        w = region.window_of(window_grid) if window_grid is not None else None
        a = src.read(window=w)
    return a[0] if a.shape[0] == 1 else a


def band_names(path: Path) -> list[str]:
    with rasterio.open(path) as src:
        return list(src.descriptions)


def provenance_path() -> Path:
    return C.DATA / "provenance.json"


def record_provenance(key: str, info: dict) -> None:
    """Merge one source's retrieval record into DATA/provenance.json."""
    p = provenance_path()
    prov = json.loads(p.read_text()) if p.exists() else {}
    prov[key] = {**C.SOURCES.get(key, {}), **info, "recorded": date.today().isoformat()}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prov, indent=2, sort_keys=True))
