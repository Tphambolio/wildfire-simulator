"""Canadian Fire Spread Dataset (CFSDS) inputs for the validation harness.

CFSDS v1.1 (Barber et al. 2024, Scientific Data 11:764, doi:10.1038/s41597-024-03436-4;
data doi:10.17605/OSF.IO/F48RY, CC BY 4.0) reconstructs the daily progression of Canadian
fires from MODIS/VIIRS hotspots kriged inside the NBAC final perimeter. Per fire it gives a
day-of-burning (DOB, day of year) raster at 90 m (``<year>_<NFIREID>_krig.tif``, Lambert
conformal conic, NAD83), and per fire-day an ERA5-derived summary row
(``Firegrowth_groups_v1_1_<year>.csv``: ffmc, dmc, dc, isi, bui, fwi, ws, rh, tmax, ...).

``FireDomain`` holds one fire on a regular latitude/longitude grid (FireSim's ``FuelGrid``
convention): DOB, national FBP fuel codes and elevation, resampled to the same cells.
Domains are prepared once from the big rasters (``prepare_domain``) and cached as ``.npz``.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

M_PER_DEG = 111320.0
DOB_NODATA = 4294967295


@dataclass
class FireDomain:
    """One fire on a regular lat/lng grid (row 0 = north)."""

    fire_id: str  # CFSDS ID, "<year>_<NFIREID>"
    year: int
    dob: np.ndarray  # int32 day of year burned, 0 = not burned
    fuel: np.ndarray  # int32 fuel codes of ``fuel_scheme``
    elevation: np.ndarray | None  # float32 metres, or None for flat
    lat_max: float
    lng_min: float
    cell_lat: float  # degrees per row
    cell_lng: float  # degrees per column
    fuel_scheme: str = "cfs_national_2014"
    meta: dict = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return int(self.dob.shape[0])

    @property
    def cols(self) -> int:
        return int(self.dob.shape[1])

    @property
    def lat_min(self) -> float:
        return self.lat_max - self.rows * self.cell_lat

    @property
    def lng_max(self) -> float:
        return self.lng_min + self.cols * self.cell_lng

    @property
    def dy(self) -> float:
        """Metres per row (FireSim's spherical approximation, as in the grid model)."""
        return self.cell_lat * M_PER_DEG

    @property
    def dx(self) -> float:
        mid = 0.5 * (self.lat_max + self.lat_min)
        return self.cell_lng * M_PER_DEG * math.cos(math.radians(mid))

    @property
    def cell_area_m2(self) -> float:
        return self.dx * self.dy

    def burn_days(self) -> list[int]:
        """Days of year with any burning, ascending."""
        return [int(d) for d in np.unique(self.dob[self.dob > 0])]

    def crop(self, r0: int, r1: int, c0: int, c1: int) -> "FireDomain":
        """Sub-domain rows r0:r1, cols c0:c1."""
        return FireDomain(
            fire_id=self.fire_id, year=self.year,
            dob=self.dob[r0:r1, c0:c1], fuel=self.fuel[r0:r1, c0:c1],
            elevation=None if self.elevation is None else self.elevation[r0:r1, c0:c1],
            lat_max=self.lat_max - r0 * self.cell_lat, lng_min=self.lng_min + c0 * self.cell_lng,
            cell_lat=self.cell_lat, cell_lng=self.cell_lng, fuel_scheme=self.fuel_scheme,
            meta=dict(self.meta),
        )

    def save(self, path: str | Path) -> None:
        arrays = {"dob": self.dob.astype(np.int32), "fuel": self.fuel.astype(np.int32)}
        if self.elevation is not None:
            arrays["elevation"] = self.elevation.astype(np.float32)
        header = {
            "fire_id": self.fire_id, "year": self.year, "lat_max": self.lat_max,
            "lng_min": self.lng_min, "cell_lat": self.cell_lat, "cell_lng": self.cell_lng,
            "fuel_scheme": self.fuel_scheme, "meta": self.meta,
        }
        np.savez_compressed(path, header=np.array(json.dumps(header)), **arrays)

    @classmethod
    def load(cls, path: str | Path) -> "FireDomain":
        with np.load(path, allow_pickle=False) as z:
            header = json.loads(str(z["header"]))
            return cls(
                fire_id=header["fire_id"], year=int(header["year"]),
                dob=z["dob"].astype(np.int32), fuel=z["fuel"].astype(np.int32),
                elevation=z["elevation"].astype(np.float32) if "elevation" in z else None,
                lat_max=header["lat_max"], lng_min=header["lng_min"],
                cell_lat=header["cell_lat"], cell_lng=header["cell_lng"],
                fuel_scheme=header.get("fuel_scheme", "cfs_national_2014"),
                meta=header.get("meta", {}),
            )


def read_groups(csv_path: str | Path) -> dict[str, dict[int, dict]]:
    """CFSDS daily summaries: {fire ID: {DOB: row}} with numeric fields as floats."""
    out: dict[str, dict[int, dict]] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            rec: dict = {}
            for k, v in row.items():
                if k == "ID":
                    rec[k] = v
                    continue
                try:
                    rec[k] = float(v)
                except (TypeError, ValueError):
                    rec[k] = math.nan
            out.setdefault(row["ID"], {})[int(rec["DOB"])] = rec
    return out


@dataclass(frozen=True)
class FireDayPair:
    """A burn day ``day`` initialised from everything burned before it (CFSDS DOB < day)."""

    day: int  # burn day (day of year), Bennett's "day n"
    initial_cells: int  # cells burned before ``day``
    growth_cells: int  # cells with DOB == day
    previous_day_cells: int  # cells with DOB == day - 1
    adjacent: bool  # growth touches the earlier burned area (8-neighbour)


def fire_day_pairs(domain: FireDomain, require_previous_day: bool = True,
                   require_adjacent: bool = True, min_growth_cells: int = 1) -> list[FireDayPair]:
    """Candidate fire-days in the style of Bennett et al. (2026).

    Bennett kept consecutive day n-1 / day n pairs (both days with burning) whose areas touch.
    ``require_previous_day`` and ``require_adjacent`` apply those filters.
    """
    pairs = []
    dob = domain.dob
    days = domain.burn_days()
    eight = np.ones((3, 3), dtype=bool)
    for day in days[1:]:
        initial = (dob > 0) & (dob < day)
        growth = dob == day
        prev = dob == day - 1
        n_init, n_grow, n_prev = int(initial.sum()), int(growth.sum()), int(prev.sum())
        if n_init == 0 or n_grow < min_growth_cells:
            continue
        if require_previous_day and n_prev == 0:
            continue
        adjacent = bool((ndimage.binary_dilation(initial, structure=eight) & growth).any())
        if require_adjacent and not adjacent:
            continue
        pairs.append(FireDayPair(day, n_init, n_grow, n_prev, adjacent))
    return pairs


def domain_grid(lat_min: float, lat_max: float, lng_min: float, lng_max: float,
                res_m: float) -> tuple[int, int, float, float]:
    """(rows, cols, cell_lat, cell_lng) of a ``res_m`` lat/lng grid covering the box."""
    mid = 0.5 * (lat_min + lat_max)
    cell_lat = res_m / M_PER_DEG
    cell_lng = res_m / (M_PER_DEG * math.cos(math.radians(mid)))
    rows = int(math.ceil((lat_max - lat_min) / cell_lat))
    cols = int(math.ceil((lng_max - lng_min) / cell_lng))
    return rows, cols, cell_lat, cell_lng


def prepare_domain(fire_id: str, dob_path: str | Path, fuel_path: str | Path,
                   dem_path: str | None = None, res_m: float = 90.0, buffer_m: float = 15000.0,
                   fuel_scheme: str = "cfs_national_2014",
                   dem_overview_level: int | None = 0) -> FireDomain:
    """Resample a CFSDS DOB raster, a national fuel grid and a DEM onto one lat/lng grid.

    The grid covers the burned cells plus ``buffer_m`` on each side, at ``res_m`` metres.
    DOB and fuel use nearest-neighbour resampling; elevation bilinear. ``dem_path`` may be a
    GDAL path such as ``/vsicurl/https://.../mrdem-30-dtm.tif`` (read at overview
    ``dem_overview_level``; 0 = first overview, None = full resolution).
    """
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds

    with rasterio.open(dob_path) as src:
        dob_src = src.read(1)
        valid = (dob_src != DOB_NODATA) & (dob_src > 0) & (dob_src < 400)
        rr, cc = np.nonzero(valid)
        if len(rr) == 0:
            raise ValueError(f"{dob_path}: no burned cells")
        t = src.transform
        x0, x1 = t.c + cc.min() * t.a, t.c + (cc.max() + 1) * t.a
        y1, y0 = t.f + rr.min() * t.e, t.f + (rr.max() + 1) * t.e
        lng_min, lat_min, lng_max, lat_max = transform_bounds(src.crs, "EPSG:4326", x0, y0, x1, y1)
        mid = 0.5 * (lat_min + lat_max)
        blat = buffer_m / M_PER_DEG
        blng = buffer_m / (M_PER_DEG * math.cos(math.radians(mid)))
        lat_min, lat_max, lng_min, lng_max = lat_min - blat, lat_max + blat, lng_min - blng, lng_max + blng
        rows, cols, cell_lat, cell_lng = domain_grid(lat_min, lat_max, lng_min, lng_max, res_m)
        dst_t = from_origin(lng_min, lat_max, cell_lng, cell_lat)
        dob = np.zeros((rows, cols), dtype=np.float64)
        src_data = np.where(valid, dob_src, 0).astype(np.float64)
        reproject(src_data, dob, src_transform=src.transform, src_crs=src.crs, src_nodata=0,
                  dst_transform=dst_t, dst_crs="EPSG:4326", dst_nodata=0,
                  resampling=Resampling.nearest)

    fuel = np.zeros((rows, cols), dtype=np.int32)
    with rasterio.open(fuel_path) as src:
        reproject(rasterio.band(src, 1), fuel, dst_transform=dst_t, dst_crs="EPSG:4326",
                  dst_nodata=0, resampling=Resampling.nearest)

    elevation = None
    if dem_path:
        kw = {} if dem_overview_level is None else {"overview_level": dem_overview_level}
        elevation = np.full((rows, cols), np.nan, dtype=np.float32)
        with rasterio.open(dem_path, **kw) as src:
            reproject(rasterio.band(src, 1), elevation, dst_transform=dst_t, dst_crs="EPSG:4326",
                      dst_nodata=np.nan, resampling=Resampling.bilinear)
        if np.isnan(elevation).all():
            elevation = None
        elif np.isnan(elevation).any():
            elevation = np.where(np.isnan(elevation), np.nanmean(elevation), elevation)

    year = int(fire_id.split("_")[0])
    return FireDomain(
        fire_id=fire_id, year=year, dob=dob.astype(np.int32), fuel=fuel, elevation=elevation,
        lat_max=lat_max, lng_min=lng_min, cell_lat=cell_lat, cell_lng=cell_lng,
        fuel_scheme=fuel_scheme,
        meta={"res_m": res_m, "buffer_m": buffer_m, "dob_source": str(Path(dob_path).name),
              "fuel_source": str(Path(fuel_path).name),
              "dem_source": str(dem_path) if dem_path else None},
    )
