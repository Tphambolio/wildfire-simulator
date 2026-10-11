"""Fuel grids built from several rasters by windowed reads: the City grid where it has data, the
Alberta Wildfire provincial grid elsewhere, a national grid outside Alberta.

The provincial raster is large (7,856 x 15,002 cells at 100 m), so it is never read whole: each
read covers only the source window under the requested lat/lng box (``read_codes_on_lattice``),
a few hundred kilobytes for a 40 km run window.

Every source is reprojected onto one regular lat/lng lattice (``Lattice``; FuelGrid indexes cells
linearly in latitude and longitude) with the categorical rule of ``load_fuel_grid``: the majority
(mode) class when the lattice is coarser than the source (City 20 m -> 50 m), nearest neighbour
when it is finer or equal (Alberta 100 m -> 50 m: each 50 m cell takes the 100 m cell under its
centre, so no class is invented). Sources are then stacked in priority order: a cell takes the
first source that has data there (not "outside extent" / "no data"; a mapped non-fuel or water
cell is data and is kept). See docs/fuel-grid-alberta.md.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from firesim.data.raster_grid import M_PER_DEG_LAT, categorical_resampling, source_resolution_m
from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid

logger = logging.getLogger(__name__)

NODATA = -9999  # code of "no data" after ``normalize_fuel_codes``


@dataclass(frozen=True)
class Lattice:
    """A regular lat/lng grid: row 0 is the northern row, cells ``dlat`` x ``dlng`` degrees."""

    lat_max: float
    lng_min: float
    dlat: float
    dlng: float
    rows: int
    cols: int

    @property
    def lat_min(self) -> float:
        return self.lat_max - self.rows * self.dlat

    @property
    def lng_max(self) -> float:
        return self.lng_min + self.cols * self.dlng

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(lat_min, lat_max, lng_min, lng_max), the FuelGrid order."""
        return self.lat_min, self.lat_max, self.lng_min, self.lng_max

    @property
    def cell_m(self) -> float:
        return self.dlat * M_PER_DEG_LAT

    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.lng_min, self.lat_max, self.dlng, self.dlat)

    def cell_of(self, lat: float, lng: float) -> tuple[int, int] | None:
        r = int(math.floor((self.lat_max - lat) / self.dlat))
        c = int(math.floor((lng - self.lng_min) / self.dlng))
        if 0 <= r < self.rows and 0 <= c < self.cols:
            return r, c
        return None

    @classmethod
    def of_grid(cls, grid: FuelGrid) -> "Lattice":
        return cls(grid.lat_max, grid.lng_min, (grid.lat_max - grid.lat_min) / grid.rows,
                   (grid.lng_max - grid.lng_min) / grid.cols, grid.rows, grid.cols)

    @classmethod
    def around(cls, lat: float, lng: float, size_km: float, cell_m: float = 50.0,
               snap_km: float | None = None) -> "Lattice":
        """A square window of about ``size_km`` centred on the point snapped to a ``snap_km``
        lattice (default ``size_km / 8``, at most 5 km), so nearby ignitions share one cached
        window and the point stays at least ``size_km / 2 - snap_km`` from the edge (15 km for
        the 40 km default). Cells are ``cell_m`` north-south and the same ground distance
        east-west at the window's centre latitude."""
        if snap_km is None:
            snap_km = min(5.0, size_km / 8.0)
        snap_lat = snap_km * 1000.0 / M_PER_DEG_LAT
        clat = round(lat / snap_lat) * snap_lat
        snap_lng = snap_km * 1000.0 / (M_PER_DEG_LAT * math.cos(math.radians(clat)))
        clng = round(lng / snap_lng) * snap_lng
        n = max(1, int(round(size_km * 1000.0 / cell_m)))
        dlat = cell_m / M_PER_DEG_LAT
        dlng = cell_m / (M_PER_DEG_LAT * math.cos(math.radians(clat)))
        return cls(clat + n / 2 * dlat, clng - n / 2 * dlng, dlat, dlng, n, n)


def read_codes_on_lattice(path: str, lattice: Lattice, resampling="categorical") -> np.ndarray:
    """Integer fuel codes of ``path`` on ``lattice`` (``NODATA`` where the raster has none).

    Reads only the source window covering the lattice (plus two cells), so memory scales with
    the window, not the raster. ``resampling="categorical"``: mode when the lattice is coarser
    than the source, nearest neighbour otherwise (``categorical_resampling``).
    """
    import rasterio
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    from firesim.data.fuel_loader import normalize_fuel_codes

    dst = np.full((lattice.rows, lattice.cols), np.nan, dtype=np.float64)
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError(f"Raster {path!r} has no CRS; cannot place it on the map")
        if isinstance(resampling, str):
            if resampling != "categorical":
                raise ValueError(f"Unknown resampling {resampling!r}")
            resampling = categorical_resampling(lattice.cell_m, source_resolution_m(src))
        left, bottom, right, top = transform_bounds(
            "EPSG:4326", src.crs, lattice.lng_min, lattice.lat_min, lattice.lng_max,
            lattice.lat_max, densify_pts=21)
        win = from_bounds(left, bottom, right, top, src.transform)
        c0 = max(0, int(math.floor(win.col_off)) - 2)
        r0 = max(0, int(math.floor(win.row_off)) - 2)
        c1 = min(src.width, int(math.ceil(win.col_off + win.width)) + 2)
        r1 = min(src.height, int(math.ceil(win.row_off + win.height)) + 2)
        if c1 > c0 and r1 > r0:
            window = Window(c0, r0, c1 - c0, r1 - r0)
            data = src.read(1, window=window)
            reproject(
                source=data, destination=dst,
                src_transform=src.window_transform(window), src_crs=src.crs,
                src_nodata=src.nodata, dst_transform=lattice.transform(), dst_crs="EPSG:4326",
                dst_nodata=np.nan, resampling=resampling,
            )
            del data
    return normalize_fuel_codes(dst, None, fill=NODATA)


def nodata_codes(scheme) -> frozenset[int]:
    """Codes meaning "no data / outside the grid" (not "mapped non-fuel") for a scheme."""
    from firesim.data.fuel_loader import AB_WILDFIRE_NODATA, _NODATA_CODES

    if scheme == "ab_wildfire":
        return AB_WILDFIRE_NODATA
    return frozenset(_NODATA_CODES)


def code_lookup(codes: np.ndarray, scheme) -> tuple[np.ndarray, np.ndarray]:
    """(fuel types as an object array, percent conifer as float with NaN) for ``codes``.

    ``scheme`` is a key of ``CODE_SCHEMES`` or a code table (dict; no percent conifer)."""
    from firesim.data.fuel_loader import CODE_SCHEMES, percent_conifer_for_code

    if isinstance(scheme, dict):
        code_map, scheme = scheme, ""
    else:
        code_map = CODE_SCHEMES[scheme]
    uniq, inv = np.unique(codes, return_inverse=True)
    inv = inv.reshape(codes.shape)
    types = np.array([code_map.get(int(u)) for u in uniq] + [None], dtype=object)[:-1]
    pcs = np.array([np.nan if (p := percent_conifer_for_code(int(u), scheme)) is None else p
                    for u in uniq], dtype=np.float64)
    return types[inv], pcs[inv]


@dataclass
class StackedGrid:
    """A FuelGrid built from prioritised sources, with how much each source supplied."""

    grid: FuelGrid
    #: per source label: share of the lattice's cells it supplied (0-1)
    share: dict[str, float] = field(default_factory=dict)
    #: share of cells no source covers (outside every grid; non-fuel)
    uncovered: float = 0.0


def stack_sources(sources: list[tuple], lattice: Lattice) -> StackedGrid:
    """Combine code arrays on one lattice: each cell takes the first source with data there.

    ``sources``: (label, codes on ``lattice``, scheme name or code table), highest priority
    first. Cells no source
    covers are non-fuel. The FuelGrid carries a per-cell percent-conifer layer when any chosen
    cell has one (M-1/M-2 codes with percent conifer).
    """
    shape = (lattice.rows, lattice.cols)
    types = np.full(shape, None, dtype=object)
    pc = np.full(shape, np.nan)
    taken = np.zeros(shape, dtype=bool)
    share: dict[str, float] = {}
    n = float(lattice.rows * lattice.cols)
    for label, codes, scheme in sources:
        has = ~np.isin(codes, list(nodata_codes(scheme))) & ~taken
        if has.any():
            t, p = code_lookup(np.where(has, codes, NODATA), scheme)
            types[has] = t[has]
            pc[has] = p[has]
        taken |= has
        share[label] = float(has.sum()) / n
    pc_layer = None
    if np.isfinite(pc).any():
        pc_layer = [[None if math.isnan(v) else v for v in row] for row in pc.tolist()]
    lat_min, lat_max, lng_min, lng_max = lattice.bounds
    grid = FuelGrid(fuel_types=types.tolist(), lat_min=lat_min, lat_max=lat_max,
                    lng_min=lng_min, lng_max=lng_max, rows=lattice.rows, cols=lattice.cols,
                    pc=pc_layer)
    return StackedGrid(grid=grid, share=share, uncovered=float((~taken).sum()) / n)


def code_at(path: str, lat: float, lng: float) -> int | None:
    """The raster's code at a point (one-cell read), or None outside the raster."""
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    with rasterio.open(path) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lng], [lat])
        r, c = src.index(xs[0], ys[0])
        if not (0 <= r < src.height and 0 <= c < src.width):
            return None
        v = src.read(1, window=Window(c, r, 1, 1))[0, 0]
        if src.nodata is not None and v == src.nodata:
            return None
        return int(v)


def covers(path: str, lat: float, lng: float, scheme: str) -> bool:
    """True when the raster has data (any mapped class, including non-fuel) at the point."""
    code = code_at(path, lat, lng)
    return code is not None and code not in nodata_codes(scheme)


def apply_water_mask(stacked: StackedGrid, water_path: str | None) -> StackedGrid:
    """Water bodies (GeoJSON) as non-fuel on a stacked grid, as ``load_fuel_grid`` does."""
    if not water_path:
        return stacked
    from firesim.data.environment import load_environment_mask

    g = stacked.grid
    mask = load_environment_mask(bounds=(g.lat_min, g.lat_max, g.lng_min, g.lng_max),
                                 rows=g.rows, cols=g.cols, water_path=water_path)
    for r, c in zip(*np.nonzero(mask)):
        g.fuel_types[r][c] = None
    return stacked


def fuel_counts(grid: FuelGrid) -> dict[str, int]:
    """Cells per fuel type (non-fuel under "non-fuel")."""
    out: dict[str, int] = {}
    for row in grid.fuel_types:
        for ft in row:
            k = ft.value if isinstance(ft, FuelType) else "non-fuel"
            out[k] = out.get(k, 0) + 1
    return out


# ── Source selection (docs/fuel-grid-alberta.md section 4) ────────────────────────────────────

AB_LABEL = "Alberta Wildfire FBP fuel types 2025, 100 m"
AB_ATTRIBUTION = ("Contains information licensed under the Open Government Licence – Alberta "
                  "(Alberta Wildfire FBP Fuel Type, © Government of Alberta).")
CITY_LABEL = "City of Edmonton LiDAR fuel grid"
NATIONAL_LABEL = "CFS national FBP fuel grid"


@dataclass(frozen=True)
class FuelSources:
    """Where the default fuel comes from.

    ``ab_path``: the Alberta Wildfire grid (COG). ``city_path``: the City grid, kept wherever it
    has data. ``national_path`` / ``national_scheme``: optional national grid outside Alberta.
    Runs whose ignition is inside the City grid's box, at least ``city_margin_km`` from its
    edge, use the City grid's own 50 m lattice (identical cells to the City-only grid) with the
    Alberta grid filling only its no-data cells; other runs get a ``window_km`` square window
    centred on the ignition at ``cell_m``.
    """

    ab_path: str | None
    city_path: str | None = None
    national_path: str | None = None
    national_scheme: str = "cfs_national"
    window_km: float = 40.0
    cell_m: float = 50.0
    city_margin_km: float = 5.0


@dataclass(frozen=True)
class FuelChoice:
    """The fuel source chosen for a run.

    ``kind``: "city" (City lattice, Alberta fill), "provincial" (window: City where mapped,
    Alberta elsewhere, national outside Alberta if configured), "national" (window, ignition
    outside Alberta) or "uniform" (no grid covers the ignition: the request's uniform fuel).
    """

    kind: str
    label: str
    attribution: str | None = None


_BOX_CACHE: dict[str, tuple[float, float, float, float]] = {}


def raster_box(path: str) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lng_min, lng_max) of a raster's footprint (cached per path)."""
    if path not in _BOX_CACHE:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(path) as src:
            lng_min, lat_min, lng_max, lat_max = transform_bounds(src.crs, "EPSG:4326",
                                                                  *src.bounds)
        _BOX_CACHE[path] = (lat_min, lat_max, lng_min, lng_max)
    return _BOX_CACHE[path]


def _inside_box(box, lat: float, lng: float, margin_km: float) -> bool:
    lat_min, lat_max, lng_min, lng_max = box
    mlat = margin_km * 1000.0 / M_PER_DEG_LAT
    mlng = margin_km * 1000.0 / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lat_min + mlat <= lat <= lat_max - mlat) and (lng_min + mlng <= lng <= lng_max - mlng)


def in_alberta_grid(path: str, lat: float, lng: float) -> bool:
    """True when the point lies inside the Alberta grid's service extent (code not 0 or -1; a
    32767 "no data" cell inside the province still counts as Alberta)."""
    code = code_at(path, lat, lng)
    return code is not None and code not in (0, -1)


def choose_fuel(src: FuelSources, lat: float, lng: float,
                uniform_fuel: str = "C2") -> FuelChoice:
    """Pick the run's fuel source from the ignition point (see ``FuelSources``)."""
    if src.city_path and _inside_box(raster_box(src.city_path), lat, lng, src.city_margin_km):
        return FuelChoice("city", f"{CITY_LABEL} (20 m, 2025); {AB_LABEL} where the City grid "
                                  "has no data", AB_ATTRIBUTION if src.ab_path else None)
    if src.ab_path and in_alberta_grid(src.ab_path, lat, lng):
        return FuelChoice("provincial", AB_LABEL, AB_ATTRIBUTION)
    if src.national_path and covers(src.national_path, lat, lng, src.national_scheme):
        return FuelChoice("national", f"{NATIONAL_LABEL} ({src.national_scheme})")
    return FuelChoice("uniform", f"Uniform {uniform_fuel}: no fuel grid covers the ignition "
                                 "(outside Alberta)")


def scheme_of(path: str, code_scheme: str = "auto") -> str | dict:
    """Code scheme name of a raster (detected from a decimated read when "auto")."""
    import rasterio

    from firesim.data.fuel_loader import CODE_SCHEMES, _detect_code_map

    if code_scheme != "auto":
        return code_scheme
    with rasterio.open(path) as src:
        step = max(1, max(src.width, src.height) // 1024)
        data = src.read(1, out_shape=(max(1, src.height // step), max(1, src.width // step)))
    code_map = _detect_code_map(set(np.unique(data).tolist()))
    return next((n for n, m in CODE_SCHEMES.items() if m is code_map), code_map)


def provincial_window(src: FuelSources, lat: float, lng: float,
                      water_path: str | None = None) -> StackedGrid:
    """The ``window_km`` window around the point: City grid where mapped, then the Alberta grid,
    then the national grid (if configured). Season is not applied (``firesim.fbp.season``)."""
    lattice = Lattice.around(lat, lng, src.window_km, src.cell_m)
    sources: list[tuple] = []
    if src.city_path:
        lat_min, lat_max, lng_min, lng_max = raster_box(src.city_path)
        b = lattice.bounds
        if lat_min < b[1] and lat_max > b[0] and lng_min < b[3] and lng_max > b[2]:
            sources.append(("city", read_codes_on_lattice(src.city_path, lattice),
                            scheme_of(src.city_path)))
    if src.ab_path:
        sources.append(("ab_wildfire", read_codes_on_lattice(src.ab_path, lattice), "ab_wildfire"))
    if src.national_path:
        sources.append(("national", read_codes_on_lattice(src.national_path, lattice),
                        src.national_scheme))
    stacked = stack_sources(sources, lattice)
    logger.info("Provincial fuel window %dx%d at %.0f m around (%.4f, %.4f): %s, uncovered %.3f",
                lattice.rows, lattice.cols, lattice.cell_m, lat, lng,
                {k: round(v, 3) for k, v in stacked.share.items()}, stacked.uncovered)
    return apply_water_mask(stacked, water_path)
