"""Building vegetation attributes and the opt-in vegetation-bridged neighbour cutoff.

Specification: ``docs/structure-spread-spec.md`` §2.1 and §4.6 (structure step 3, 2026-10-10).
**Illustrative — not validated in Canada.** Open data only: the Meta/WRI 1 m global canopy
height map (Tolan et al. 2024, *Remote Sens. Environ.* 300: 113888, CC BY 4.0). City of Edmonton
LiDAR products are City-owned and never used for shipped values.

1. **Attributes** (``BuildingVegetation``): per footprint, canopy cover (CHM ≥ 2 m outside
   buildings) in rings 0-5, 5-10, 10-30 and 0-10 m, the share of the roof under canopy and the
   distance to a ≥ 1 ha stand, computed offline by ``scripts/build_building_vegetation.py``.
   They are context only: no model dynamics read them.
2. **Vegetation-bridged cutoff** [H] (``BridgedLinks``): a Hamada link between two buildings
   exists if their edge-to-edge separation is ≤ 20 m, or if it is > 20 m and ≤ 45 m **and** the
   gap between them carries ≥ 20 % woody cover. 20 m and 45 m are the pre-registered cutoff
   sensitivities (owner decision D2; [R10] PREREG); 20 % is the woody-cover threshold of
   FPInnovations WF TR 2025 n.04 p.24 (block loss 2-4 × at > 20 % vs < 10 % in the 10 m zone).

**Gap woody cover** (FireSim definition [H], spec §4.6): the *gap corridor* of buildings i and j
is the convex hull of the two footprints minus the two footprints. Points on a regular lattice
(``step_m``, default 1 m) inside the corridor are classed: inside another building → 0 (a
structure, counted in the denominator as FPI's point-intercept sampling counts structures,
p.9); otherwise → the canopy share of the open ground of the canopy-raster cell the point falls
in (``scripts/build_building_vegetation.py canopy-raster``: share of non-building 1 m pixels with
CHM ≥ 2 m, ≈ 5 m cells). The gap cover is the mean over the lattice. Points with no canopy
data are dropped; a corridor with no valid point has no cover (nan) and is **not bridged**
(the rule then reduces to the 20 m cutoff). Why this corridor: it is the ground any flame or
radiant path between the two footprints crosses, it needs no direction (Hamada's wind term
already handles direction) and it is the region the step-3 data report proposed (§5.2).
"""

from __future__ import annotations

import gzip
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

VEG_FIELDS = ("cc_0_5", "cc_5_10", "cc_10_30", "cc_0_10", "overhang_frac", "dist_stand_1ha_m")
VEG_SOURCE = ("Meta/WRI 1 m canopy height (Tolan et al. 2024, CC BY 4.0); FireSim open-data "
              "method (scripts/build_building_vegetation.py)")

# Pre-registered values of the vegetation-bridged cutoff (spec §4.6). Do not tune.
BRIDGE_BASE_CUTOFF_M = 20.0  # D2 / [R10] PREREG sensitivity (lower)
BRIDGE_MAX_CUTOFF_M = 45.0  # D2 / [R10] PREREG sensitivity (upper)
BRIDGE_MIN_GAP_COVER = 0.20  # FPInnovations WF TR 2025 n.04 p.24
DEFAULT_STEP_M = 1.0
R_MERC = 6378137.0
NODATA = 255


def lnglat_to_3857(lng, lat):
    """Web Mercator (EPSG:3857) metres of (lng, lat) degrees (spherical, R = 6,378,137 m)."""
    lng = np.asarray(lng, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -85.0, 85.0)
    return (R_MERC * np.radians(lng),
            R_MERC * np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0)))


# ------------------------------------------------------------------------------ attributes


@dataclass
class BuildingVegetation:
    """Per-footprint vegetation metrics keyed by building id (``VEG_FIELDS``; nan = none)."""

    ids: np.ndarray  # sorted int64
    values: np.ndarray  # (n, len(VEG_FIELDS)) float32
    source: str = VEG_SOURCE

    @classmethod
    def from_csv(cls, path: str | Path) -> "BuildingVegetation":
        """Read ``id,<VEG_FIELDS>`` (blank = not available), plain or gzipped."""
        p = Path(path)
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            cols = [header.index(k) if k in header else -1 for k in VEG_FIELDS]
            id_col = header.index("id")
            ids, rows = [], []
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) < len(header):
                    continue
                ids.append(int(float(parts[id_col])))
                rows.append([float(parts[c]) if c >= 0 and parts[c] != "" else np.nan
                             for c in cols])
        ids = np.asarray(ids, dtype=np.int64)
        vals = np.asarray(rows, dtype=np.float32).reshape(len(ids), len(VEG_FIELDS))
        order = np.argsort(ids, kind="stable")
        return cls(ids=ids[order], values=vals[order])

    def __len__(self) -> int:
        return len(self.ids)

    def lookup(self, ids) -> np.ndarray:
        """(len(ids), len(VEG_FIELDS)) values; nan rows for unknown or missing ids (-1)."""
        ids = np.asarray(ids, dtype=np.int64)
        out = np.full((len(ids), len(VEG_FIELDS)), np.nan, dtype=np.float32)
        if len(self.ids) == 0 or len(ids) == 0:
            return out
        pos = np.clip(np.searchsorted(self.ids, ids), 0, len(self.ids) - 1)
        hit = self.ids[pos] == ids
        out[hit] = self.values[pos[hit]]
        return out


def veg_dict(row: np.ndarray) -> dict | None:
    """JSON-friendly attributes of one unit (covers to 0.01, distance to 1 m), or None."""
    if row is None or not np.isfinite(row).any():
        return None
    out = {}
    for k, v in zip(VEG_FIELDS, row):
        if np.isfinite(v):
            out[k] = int(round(float(v))) if k == "dist_stand_1ha_m" else round(float(v), 2)
    return out


# ------------------------------------------------------------------------------ canopy


@dataclass
class CanopyWindow:
    """Canopy share of open ground (0-1, nan = none) on an EPSG:3857 grid (north-up)."""

    share: np.ndarray  # (rows, cols) float32
    x0: float  # west edge, EPSG:3857 m
    y0: float  # north edge
    dx: float  # cell width (EPSG:3857 m)
    dy: float  # cell height (positive)

    def sample(self, lng, lat) -> np.ndarray:
        x, y = lnglat_to_3857(lng, lat)
        c = np.floor((x - self.x0) / self.dx).astype(np.int64)
        r = np.floor((self.y0 - y) / self.dy).astype(np.int64)
        ok = (r >= 0) & (r < self.share.shape[0]) & (c >= 0) & (c < self.share.shape[1])
        out = np.full(np.shape(x), np.nan, dtype=np.float32)
        out[ok] = self.share[r[ok], c[ok]]
        return out

    @property
    def has_data(self) -> bool:
        return bool(np.isfinite(self.share).any())


class CanopyCover:
    """The canopy-share GeoTIFF (``canopy-raster`` output: uint8 percent, 255 = none, EPSG:3857).

    Only the window a run needs is read (``window``)."""

    def __init__(self, path: str | Path) -> None:
        import rasterio

        self.path = str(path)
        with rasterio.open(self.path) as s:
            if s.crs is None or s.crs.to_epsg() != 3857:
                raise ValueError("canopy raster must be on EPSG:3857 (the Meta CHM grid)")
            self.transform = s.transform
            self.shape = (s.height, s.width)
            self.nodata = s.nodata if s.nodata is not None else NODATA

    def window(self, lat_min: float, lat_max: float, lng_min: float, lng_max: float,
               pad_m: float = 100.0) -> CanopyWindow:
        """Read the cells covering a (lat, lng) box, padded by ``pad_m`` (Mercator metres)."""
        import rasterio
        from rasterio.windows import Window

        x0, y0 = lnglat_to_3857(lng_min, lat_min)
        x1, y1 = lnglat_to_3857(lng_max, lat_max)
        inv = ~self.transform
        ca, ra = inv * (float(x0) - pad_m, float(y1) + pad_m)
        cb, rb = inv * (float(x1) + pad_m, float(y0) - pad_m)
        c0, r0 = max(int(math.floor(ca)), 0), max(int(math.floor(ra)), 0)
        c1, r1 = min(int(math.ceil(cb)), self.shape[1]), min(int(math.ceil(rb)), self.shape[0])
        t = self.transform
        if c1 <= c0 or r1 <= r0:
            return CanopyWindow(np.full((1, 1), np.nan, np.float32), t.c, t.f, t.a, -t.e)
        with rasterio.open(self.path) as s:
            a = s.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        share = np.where(a == self.nodata, np.nan, a.astype(np.float32) / 100.0)
        wx0, wy0 = t * (c0, r0)
        return CanopyWindow(share.astype(np.float32), float(wx0), float(wy0), t.a, -t.e)


def canopy_window_from_chm(chm: np.ndarray, transform, footprints_3857=None,
                           block: int = 1, threshold_m: float = 2.0) -> CanopyWindow:
    """A ``CanopyWindow`` straight from a CHM array on an EPSG:3857 grid (validation scripts):
    blocks of ``block`` pixels hold the share of their non-building pixels with CHM ≥
    ``threshold_m`` (``footprints_3857`` rasterised as building), as the offline raster."""
    from rasterio.features import rasterize

    chm = np.asarray(chm)
    if footprints_3857 is not None and len(footprints_3857):
        bmask = rasterize(((g, 1) for g in footprints_3857), out_shape=chm.shape,
                          transform=transform, fill=0, dtype="uint8").astype(bool)
    else:
        bmask = np.zeros(chm.shape, dtype=bool)
    rows, cols = (chm.shape[0] // block) * block, (chm.shape[1] // block) * block
    open_ = ~bmask[:rows, :cols]
    can = (chm[:rows, :cols] >= threshold_m) & open_
    shp = (rows // block, block, cols // block, block)
    n_open = open_.reshape(shp).sum(axis=(1, 3))
    n_can = can.reshape(shp).sum(axis=(1, 3))
    share = np.where(n_open > 0, n_can / np.maximum(n_open, 1), np.nan).astype(np.float32)
    return CanopyWindow(share, float(transform.c), float(transform.f),
                        float(transform.a) * block, float(-transform.e) * block)


# ------------------------------------------------------------------------------ gap cover


def gap_corridors(fp_i, fp_js):
    """Gap corridors (local metres): convex hull of footprint i with each j, minus both."""
    import shapely

    fp_js = np.asarray(fp_js, dtype=object)
    pair = shapely.union(fp_i, fp_js)
    return shapely.difference(shapely.convex_hull(pair), pair)


def gap_cover(units, i: int, js, canopy: CanopyWindow, *, step_m: float = DEFAULT_STEP_M,
              tree=None) -> np.ndarray:
    """Gap woody cover (0-1, nan = no data) between unit ``i`` and each unit in ``js``.

    See the module docstring for the definition. ``tree``: an ``STRtree`` over
    ``units.footprints`` (built when ``None``)."""
    import shapely

    js = np.asarray(js, dtype=np.int64)
    out = np.full(len(js), np.nan)
    if len(js) == 0:
        return out
    if tree is None:
        tree = shapely.STRtree(units.footprints)
    corr = gap_corridors(units.footprints[i], units.footprints[js])
    frame = units.frame
    for k, (j, poly) in enumerate(zip(js, corr)):
        if poly is None or poly.is_empty:
            continue
        x0, y0, x1, y1 = poly.bounds
        xs = np.arange(x0 + step_m / 2.0, x1, step_m)
        ys = np.arange(y0 + step_m / 2.0, y1, step_m)
        if len(xs) == 0 or len(ys) == 0:
            continue
        gx, gy = np.meshgrid(xs, ys)
        gx, gy = gx.ravel(), gy.ravel()
        inside = shapely.contains_xy(poly, gx, gy)
        gx, gy = gx[inside], gy[inside]
        if len(gx) == 0:
            continue
        lat, lng = frame.to_latlng(gx, gy)
        val = canopy.sample(lng, lat).astype(float)
        others = tree.query(poly)
        others = others[(others != i) & (others != j)]
        for o in others:  # other buildings in the gap: structure, cover 0
            val[shapely.contains_xy(units.footprints[o], gx, gy)] = 0.0
        ok = np.isfinite(val)
        if ok.any():
            out[k] = float(val[ok].mean())
    return out


@dataclass
class BridgedLinks:
    """Link filter for ``hamada_spread`` / ``coupled_spread``: the vegetation-bridged cutoff.

    ``__call__(i, nb, sep)`` returns which of unit ``i``'s neighbours ``nb`` (at edge-to-edge
    separations ``sep``) are linked. Gap covers are computed only for the links a run asks for
    and cached (symmetric)."""

    units: object
    canopy: CanopyWindow | None
    base_cutoff_m: float = BRIDGE_BASE_CUTOFF_M
    max_cutoff_m: float = BRIDGE_MAX_CUTOFF_M
    min_gap_cover: float = BRIDGE_MIN_GAP_COVER
    step_m: float = DEFAULT_STEP_M
    _cache: dict = field(default_factory=dict)
    _tree: object = None

    def __post_init__(self) -> None:
        if not 0 <= self.base_cutoff_m <= self.max_cutoff_m:
            raise ValueError("need 0 <= base_cutoff_m <= max_cutoff_m")
        if self.max_cutoff_m > getattr(self.units, "neighbour_cutoff_m", math.inf) + 1e-9:
            raise ValueError("the units' neighbour graph must reach max_cutoff_m")

    def cover(self, i: int, js) -> np.ndarray:
        js = np.asarray(js, dtype=np.int64)
        out = np.full(len(js), np.nan)
        todo = []
        for k, j in enumerate(js):
            key = (min(i, int(j)), max(i, int(j)))
            if key in self._cache:
                out[k] = self._cache[key]
            else:
                todo.append(k)
        if todo and self.canopy is not None and self.canopy.has_data:
            if self._tree is None:
                import shapely

                self._tree = shapely.STRtree(self.units.footprints)
            got = gap_cover(self.units, i, js[todo], self.canopy, step_m=self.step_m,
                            tree=self._tree)
            for k, v in zip(todo, got):
                out[k] = v
        for k in todo:
            j = int(js[k])
            self._cache[(min(i, j), max(i, j))] = out[k]
        return out

    def __call__(self, i: int, nb: np.ndarray, sep: np.ndarray) -> np.ndarray:
        sep = np.asarray(sep, dtype=float)
        keep = sep <= self.base_cutoff_m
        long_ = (sep > self.base_cutoff_m) & (sep <= self.max_cutoff_m)
        if long_.any():
            c = self.cover(int(i), np.asarray(nb)[long_])
            keep[long_] = np.nan_to_num(c, nan=-1.0) >= self.min_gap_cover
        return keep

    def stats(self) -> dict:
        v = np.asarray(list(self._cache.values()), dtype=float)
        return {"links_tested": int(len(v)),
                "links_no_canopy_data": int(np.sum(~np.isfinite(v))),
                "links_bridged": int(np.sum(np.nan_to_num(v, nan=-1.0) >= self.min_gap_cover))}

    def params(self) -> dict:
        return {"vegetation_bridged_cutoff": True,
                "bridge_base_cutoff_m": self.base_cutoff_m,
                "bridge_max_cutoff_m": self.max_cutoff_m,
                "bridge_min_gap_cover": self.min_gap_cover,
                "bridge_canopy_data": bool(self.canopy is not None and self.canopy.has_data)}
