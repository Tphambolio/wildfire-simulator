"""Building footprints read by window for the run area (never the whole file).

Two stores sit behind one ``BuildingIndex``:

* **FlatGeobuf** (``.fgb``; the Alberta file built by ``scripts/build_ab_buildings.py``,
  1.78 M footprints): every query reads only the features whose bounds intersect a box,
  through the file's packed Hilbert R-tree (pyogrio / GDAL). Nothing per building is held
  between queries.
* **GeoJSON** (``.geojson`` / ``.geojson.gz``: the Edmonton extract, test fixtures, user
  files): read once into compact arrays (WKB bytes, bounds, id), not raw GeoJSON dicts.

Uses (``docs/buildings-alberta.md``):

1. **Edmonton neighbourhood selection** (building mask, inside-perimeter counts, exposure):
   ``nearest_neighbourhoods`` + ``building_geoms_for`` / ``building_centroids_for`` give the
   buildings of the 4 neighbourhoods nearest the ignition, assigned exactly as before
   (ring-average centroid within a neighbourhood polygon, else the nearest neighbourhood
   centroid) over the buildings whose centroid is in ``partition_box`` (the former Edmonton
   extract's box). Only a window is read: the neighbourhoods' polygons plus their Voronoi
   cells (the only places a nearest-centroid fallback building can be) clipped to the box.
2. **Buildings near an ignition elsewhere** (``near``): the buildings whose centroid is in
   the run grid's box, the ``max_buildings`` nearest the ignition when there are more.
3. **Footprint source for structure spread** (``count_in_box`` / ``count_intersecting`` /
   ``footprints_intersecting`` / ``ids_intersecting``): windowed reads of the reachable box;
   ``clipped(box)`` restricts it to buildings whose centroid is in ``box`` (Edmonton runs:
   the same buildings as the former extract).

Centroids are the mean of the first outer ring's vertices (closing vertex included), summed
in Python as before, so neighbourhood assignment and box membership are bit-for-bit those of
the former whole-file index.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

#: Box (lat_min, lat_max, lng_min, lng_max) of the former Edmonton extract
#: (data/edmonton_buildings.geojson.gz = the Microsoft footprints whose ring-average centroid
#: is in it). Neighbourhood assignment and Edmonton runs use the buildings in this box only.
EDMONTON_EXTRACT_BOX = (53.39, 53.71, -113.75, -113.25)

#: Search margin (degrees) added to every FlatGeobuf box query before the exact bounds test;
#: larger than any footprint's extent (largest in the Alberta file: 0.029 x 0.021 deg).
FGB_MARGIN_DEG = 0.05

#: Default cap on buildings selected around an ignition outside Edmonton (``near``).
DEFAULT_MAX_NEAR = 30_000


def _raw_centroid(geometry: dict) -> tuple[float, float] | None:
    """(lat, lng) mean of a GeoJSON geometry's first outer ring (None if unsupported)."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    try:
        if gtype == "Polygon":
            ring = coords[0]
        elif gtype == "MultiPolygon":
            ring = coords[0][0]
        else:
            return None
        if not ring:
            return None
        n = len(ring)
        lng = sum(c[0] for c in ring) / n
        lat = sum(c[1] for c in ring) / n
        return (lat, lng)
    except (IndexError, TypeError, ZeroDivisionError):
        return None


def _ring_centroid(geom) -> tuple[float, float] | None:
    """Same as ``_raw_centroid`` for a shapely Polygon / MultiPolygon."""
    try:
        t = geom.geom_type
        if t == "Polygon":
            poly = geom
        elif t == "MultiPolygon":
            poly = geom.geoms[0]
        else:
            return None
        ring = poly.exterior.coords[:]
        n = len(ring)
        if n == 0:
            return None
        return (sum(c[1] for c in ring) / n, sum(c[0] for c in ring) / n)
    except Exception:  # noqa: BLE001 - unreadable footprint: skipped, as before
        return None


def _in_box(lat, lng, box) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lng <= box[3]


# ── Stores ────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Batch:
    """Footprints from one box query, in id order (FlatGeobuf) or file order (GeoJSON)."""

    ids: np.ndarray      # int64 (-1 = no id)
    bounds: np.ndarray   # (n, 4) lng_min, lat_min, lng_max, lat_max
    wkb: np.ndarray      # object array of WKB bytes

    def __len__(self) -> int:
        return len(self.ids)

    def take(self, sel) -> "_Batch":
        return _Batch(self.ids[sel], self.bounds[sel], self.wkb[sel])


def _hit(bounds: np.ndarray, bbox) -> np.ndarray:
    lat_min, lat_max, lng_min, lng_max = bbox
    b = bounds
    with np.errstate(invalid="ignore"):
        return (b[:, 0] <= lng_max) & (b[:, 2] >= lng_min) & (b[:, 1] <= lat_max) & (b[:, 3] >= lat_min)


class _GeoJSONStore:
    """A GeoJSON file read once into compact arrays (WKB + bounds + id)."""

    def __init__(self, path: str) -> None:
        import gzip
        import json

        p = Path(path)
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
        feats = (data.get("features", []) if data.get("type") == "FeatureCollection"
                 else [data] if data.get("type") == "Feature" else [])
        del data
        ids, wkb, bounds = [], [], []
        for f in feats:
            geometry = f.get("geometry")
            if not geometry or _raw_centroid(geometry) is None:
                continue
            try:
                g = shape(geometry)
            except Exception:  # noqa: BLE001
                continue
            try:
                bid = int((f.get("properties") or {}).get("id", -1))
            except (TypeError, ValueError):
                bid = -1
            ids.append(bid)
            wkb.append(shapely.to_wkb(g))
            bounds.append(g.bounds if not g.is_empty else (np.nan,) * 4)
        del feats
        self._ids = np.asarray(ids, dtype=np.int64)
        self._wkb = np.asarray(wkb, dtype=object) if wkb else np.zeros(0, dtype=object)
        self._bounds = np.asarray(bounds, dtype=float).reshape(-1, 4)

    def __len__(self) -> int:
        return len(self._ids)

    def bounds_in(self, bbox):
        """(ids, bounds, row) of footprints whose bounds intersect ``bbox``."""
        sel = np.nonzero(_hit(self._bounds, bbox))[0]
        return self._ids[sel], self._bounds[sel], sel

    def read(self, bbox, rows=None) -> _Batch:
        sel = rows if rows is not None else np.nonzero(_hit(self._bounds, bbox))[0]
        return _Batch(self._ids[sel], self._bounds[sel], self._wkb[sel])


class _FlatGeobufStore:
    """A FlatGeobuf file with a spatial index, read by box only (pyogrio)."""

    def __init__(self, path: str) -> None:
        import pyogrio

        self.path = str(path)
        info = pyogrio.read_info(self.path)
        self._n = int(info.get("features") or 0)
        fields = list(info.get("fields", []))
        self._id_col = "id" if "id" in fields else None

    def __len__(self) -> int:
        return self._n

    @staticmethod
    def _grown(bbox, m=FGB_MARGIN_DEG):
        lat_min, lat_max, lng_min, lng_max = bbox
        return (lng_min - m, lat_min - m, lng_max + m, lat_max + m)

    def bounds_in(self, bbox):
        """(fids, bounds, fids) of footprints whose bounds intersect ``bbox`` (bounds only)."""
        import pyogrio

        fids, b = pyogrio.read_bounds(self.path, bbox=self._grown(bbox))
        b = np.asarray(b, dtype=float).T.reshape(-1, 4)
        sel = _hit(b, bbox)
        return np.asarray(fids)[sel], b[sel], np.asarray(fids)[sel]

    def read(self, bbox, rows=None) -> _Batch:
        """Footprints whose bounds intersect ``bbox`` (with ``rows``: those fids among them),
        sorted by id. The bounds test runs on the index-filtered bounds; only the selected
        features' geometries are read (by fid)."""
        import pyogrio

        fids, b, _ = self.bounds_in(bbox)
        if rows is not None:
            keep = np.isin(fids, rows)
            fids, b = fids[keep], b[keep]
        if len(fids) == 0:
            return _Batch(np.zeros(0, np.int64), np.zeros((0, 4)), np.zeros(0, dtype=object))
        order = np.argsort(fids)
        fids, b = fids[order], b[order]
        cols = [self._id_col] if self._id_col else []
        _, _, wkb, fields = pyogrio.raw.read(self.path, fids=fids, columns=cols)
        ids = (np.asarray(fields[0], dtype=np.int64) if self._id_col
               else np.full(len(wkb), -1, dtype=np.int64))
        idx = np.argsort(ids, kind="stable")
        return _Batch(ids[idx], b[idx], np.asarray(wkb, dtype=object)[idx])


def open_store(path: str):
    """The store for a buildings file: FlatGeobuf by suffix, else GeoJSON."""
    if str(path).lower().endswith(".fgb"):
        return _FlatGeobufStore(path)
    return _GeoJSONStore(path)


# ── Index ─────────────────────────────────────────────────────────────────────────────────


@dataclass
class NearBuildings:
    """Buildings selected for a run outside Edmonton (``BuildingIndex.near``)."""

    geoms: list
    centroids: list  # (lat, lng)
    ids: np.ndarray
    in_box: int      # buildings with ring centroid in the box (before the cap)
    capped: bool


class BuildingIndex:
    """Windowed building reader + Edmonton neighbourhood selection.

    Args:
        buildings_path: FlatGeobuf (``.fgb``, read by window) or GeoJSON (``.geojson[.gz]``).
        neighbourhoods_path: neighbourhood polygons GeoJSON (Edmonton), or None.
        partition_box: buildings considered for neighbourhood assignment (centroid inside);
            None = every building in the file (the former behaviour for a GeoJSON extract).
    """

    def __init__(self, buildings_path: str, neighbourhoods_path: str | None = None,
                 partition_box: tuple[float, float, float, float] | None = None) -> None:
        self.buildings_path = str(buildings_path)
        self._store = open_store(buildings_path)
        self.partition_box = tuple(partition_box) if partition_box else None
        self._nbhd_keys: list[str] = []
        self._nbhd_geoms: list = []
        self._nbhd_centroids: list[tuple[float, float]] = []
        if neighbourhoods_path:
            self._load_neighbourhoods(neighbourhoods_path)
        self._nbhd_tree = STRtree(self._nbhd_geoms) if self._nbhd_geoms else None
        self._voronoi = None
        self._sel_cache: tuple | None = None
        logger.info("BuildingIndex: %s (%d footprints, %s), %d neighbourhoods",
                    buildings_path, len(self._store), type(self._store).__name__,
                    len(self._nbhd_keys))

    def _load_neighbourhoods(self, path: str) -> None:
        import json

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        feats = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
        for f in feats:
            try:
                geom = shape(f["geometry"])
                if not geom.is_valid or geom.is_empty:
                    continue
                key = f["properties"].get("neighbourhood") or f["properties"].get("name", "")
                self._nbhd_geoms.append(geom)
                self._nbhd_keys.append(key)
                c = geom.centroid
                self._nbhd_centroids.append((c.y, c.x))
            except Exception:  # noqa: BLE001
                continue

    # ── Edmonton neighbourhoods ──────────────────────────────────────────────────────────

    @property
    def has_neighbourhoods(self) -> bool:
        return bool(self._nbhd_keys)

    def in_partition(self, lat: float, lng: float) -> bool:
        """The point is where the neighbourhood selection applies (inside ``partition_box``,
        or anywhere when there is no box) and neighbourhoods are loaded."""
        if not self._nbhd_keys:
            return False
        return self.partition_box is None or _in_box(lat, lng, self.partition_box)

    def nearest_neighbourhoods(self, lat: float, lng: float, n: int = 4) -> list[str]:
        """Return the n neighbourhood keys nearest to (lat, lng) by centroid distance."""
        distances = [
            (math.hypot(lng - nlng, lat - nlat), key)
            for (nlat, nlng), key in zip(self._nbhd_centroids, self._nbhd_keys)
        ]
        distances.sort(key=lambda x: x[0])
        return [key for _, key in distances[:n]]

    def _assign(self, clat: float, clng: float) -> int:
        """Neighbourhood index of a building centroid (within, else nearest centroid)."""
        hits = self._nbhd_tree.query(Point(clng, clat), predicate="within")
        if len(hits) > 0:
            return int(hits[0])
        best_idx, best_dist = 0, float("inf")
        for i, (nlat, nlng) in enumerate(self._nbhd_centroids):
            d = math.hypot(clng - nlng, clat - nlat)
            if d < best_dist:
                best_dist, best_idx = d, i
        return best_idx

    def _window_for(self, idx: list[int]):
        """Box holding every building that can be assigned to neighbourhoods ``idx``:
        their polygons, and their Voronoi cells (nearest-centroid fallback) within the
        partition box (or the file bounds)."""
        from shapely.geometry import MultiPoint, box as sbox

        if self._voronoi is None:
            pts = [Point(lng, lat) for lat, lng in self._nbhd_centroids]
            extent = (sbox(self.partition_box[2], self.partition_box[0],
                           self.partition_box[3], self.partition_box[1])
                      if self.partition_box else None)
            if extent is None:
                lng0, lat0, lng1, lat1 = MultiPoint(pts).bounds
                extent = sbox(lng0 - 5, lat0 - 5, lng1 + 5, lat1 + 5)
            cells = shapely.voronoi_polygons(MultiPoint(pts), extend_to=extent).geoms
            # voronoi_polygons does not keep input order: match each cell to its point
            tree = STRtree(list(cells))
            by_point = [None] * len(pts)
            for i, p in enumerate(pts):
                for j in tree.query(p, predicate="intersects"):
                    by_point[i] = cells[int(j)] if by_point[i] is None else by_point[i].union(cells[int(j)])
            self._voronoi = (by_point, extent)
        cells, extent = self._voronoi
        parts = [self._nbhd_geoms[i] for i in idx]
        parts += [c.intersection(extent) for i in idx if (c := cells[i]) is not None]
        lng0, lat0, lng1, lat1 = shapely.union_all(parts).bounds
        eps = 1e-6
        return (lat0 - eps, lat1 + eps, lng0 - eps, lng1 + eps)

    def _select(self, nbhd_keys: list[str]):
        key = tuple(nbhd_keys)
        if self._sel_cache is not None and self._sel_cache[0] == key:
            return self._sel_cache[1]
        pos = {k: i for i, k in enumerate(self._nbhd_keys)}
        idx = sorted({pos[k] for k in nbhd_keys if k in pos})
        want = {i: [] for i in idx}
        if idx:
            batch = self._store.read(self._window_for(idx))
            for j in range(len(batch)):
                g = shapely.from_wkb(batch.wkb[j])
                c = _ring_centroid(g)
                if c is None:
                    continue
                if self.partition_box is not None and not _in_box(c[0], c[1], self.partition_box):
                    continue
                a = self._assign(*c)
                if a in want:
                    want[a].append((g, c, int(batch.ids[j])))
        # Output order: the requested neighbourhoods in their given order, buildings in file
        # (id) order within each, as the former index returned them.
        out = []
        for k in nbhd_keys:
            i = pos.get(k)
            if i is not None and i in want:
                out.extend(want.pop(i))
        self._sel_cache = (key, out)
        return out

    def building_geoms_for(self, nbhd_keys: list[str]) -> list:
        """Shapely footprints of the buildings assigned to the given neighbourhoods
        (valid, non-empty only)."""
        result = []
        for g, _, _ in self._select(nbhd_keys):
            try:
                if g.is_valid and not g.is_empty:
                    result.append(g)
            except Exception:  # noqa: BLE001
                continue
        return result

    def building_centroids_for(self, nbhd_keys: list[str]) -> list[tuple[float, float]]:
        """(lat, lng) ring-average centroids of the buildings in the given neighbourhoods."""
        return [c for _, c, _ in self._select(nbhd_keys)]

    def building_ids_for(self, nbhd_keys: list[str]) -> list[int]:
        return [i for _, _, i in self._select(nbhd_keys)]

    # ── Outside Edmonton ─────────────────────────────────────────────────────────────────

    def near(self, lat: float, lng: float, bbox,
             max_buildings: int = DEFAULT_MAX_NEAR) -> NearBuildings:
        """Buildings whose ring centroid is in ``bbox`` (lat_min, lat_max, lng_min, lng_max);
        when there are more than ``max_buildings``, the ``max_buildings`` whose footprint
        bounds centre is nearest (lat, lng) (ties by file position). Returned in id order;
        valid, non-empty footprints only. Reads bounds for the box and geometries only for
        the selected buildings (and those straddling the box edge)."""
        _, b, rows = self._store.bounds_in(bbox)
        if len(rows) == 0:
            return NearBuildings([], [], np.zeros(0, np.int64), 0, False)
        keep = self._centroid_in(rows, b, bbox)
        rows, b = np.asarray(rows)[keep], b[keep]
        n_in = int(len(rows))
        capped = n_in > max_buildings
        if capped:
            kx = math.cos(math.radians(lat))
            d2 = (((b[:, 0] + b[:, 2]) / 2.0 - lng) * kx) ** 2 + ((b[:, 1] + b[:, 3]) / 2.0 - lat) ** 2
            order = np.lexsort((rows, d2))[:max_buildings]
            rows, b = rows[order], b[order]
        batch = self._store.read((float(np.nanmin(b[:, 1])), float(np.nanmax(b[:, 3])),
                                  float(np.nanmin(b[:, 0])), float(np.nanmax(b[:, 2]))),
                                 rows=rows)
        geoms, cents, ids = [], [], []
        for j in range(len(batch)):
            g = shapely.from_wkb(batch.wkb[j])
            c = _ring_centroid(g)
            if c is None or not g.is_valid or g.is_empty:
                continue
            geoms.append(g)
            cents.append(c)
            ids.append(int(batch.ids[j]))
        return NearBuildings(geoms, cents, np.asarray(ids, dtype=np.int64), n_in, capped)

    # ── Footprint source for structure spread (firesim.structures.spread) ────────────────

    def clipped(self, box) -> "ClippedSource":
        """This index as a footprint source restricted to buildings whose centroid is in
        ``box`` (Edmonton runs: the former extract's buildings exactly)."""
        return ClippedSource(self, box)

    def _centroids_of(self, batch: _Batch):
        lat = np.full(len(batch), np.nan)
        lng = np.full(len(batch), np.nan)
        for j in range(len(batch)):
            c = _ring_centroid(shapely.from_wkb(batch.wkb[j]))
            if c is not None:
                lat[j], lng[j] = c
        return lat, lng

    def _intersecting(self, bbox, clip=None) -> _Batch:
        """Footprints whose bounds intersect ``bbox`` (and centroid in ``clip``), with a
        valid ring centroid (as the former index's flat arrays)."""
        batch = self._store.read(bbox)
        lat, lng = self._centroids_of(batch)
        ok = np.isfinite(lat)
        if clip is not None:
            ok &= (lat >= clip[0]) & (lat <= clip[1]) & (lng >= clip[2]) & (lng <= clip[3])
        return batch.take(np.nonzero(ok)[0])

    def _centroid_in(self, rows, b, box) -> np.ndarray:
        """Mask: ring centroid in ``box`` for the footprints ``rows`` with bounds ``b``.
        Decided from the bounds where they lie inside or outside ``box``; geometries are read
        only for footprints whose bounds straddle an edge of ``box``."""
        lat_min, lat_max, lng_min, lng_max = box
        e = 1e-9  # a ring mean lies within its bounds up to rounding
        inside = ((b[:, 0] >= lng_min + e) & (b[:, 2] <= lng_max - e)
                  & (b[:, 1] >= lat_min + e) & (b[:, 3] <= lat_max - e))
        outside = ((b[:, 2] < lng_min) | (b[:, 0] > lng_max)
                   | (b[:, 3] < lat_min) | (b[:, 1] > lat_max))
        keep = inside.copy()
        edge_idx = np.nonzero(~inside & ~outside)[0]
        if len(edge_idx):
            wkb = self._wkb_for_rows(np.asarray(rows)[edge_idx])
            for k in edge_idx:
                c = _ring_centroid(shapely.from_wkb(wkb[int(rows[k])]))
                keep[k] = c is not None and _in_box(c[0], c[1], box)
        return keep

    def _wkb_for_rows(self, rows) -> dict:
        """{row: wkb} for store rows (fids for FlatGeobuf, positions for GeoJSON)."""
        st = self._store
        if isinstance(st, _GeoJSONStore):
            return {int(r): st._wkb[int(r)] for r in rows}
        import pyogrio

        rows = np.sort(np.asarray(rows))
        _, _, wkb, _ = pyogrio.raw.read(st.path, fids=rows, columns=[])
        return {int(r): w for r, w in zip(rows, wkb)}

    def count_in_box(self, bbox, clip=None) -> int:
        """Buildings whose ring centroid is inside ``bbox`` (lat_min, lat_max, lng_min,
        lng_max) (and ``clip``). Reads bounds, plus geometries along the box edges only."""
        box = bbox if clip is None else (max(bbox[0], clip[0]), min(bbox[1], clip[1]),
                                         max(bbox[2], clip[2]), min(bbox[3], clip[3]))
        if box[0] > box[1] or box[2] > box[3]:
            return 0
        _, b, rows = self._store.bounds_in(box)
        if len(rows) == 0:
            return 0
        return int(self._centroid_in(rows, b, box).sum())

    def count_intersecting(self, bbox, clip=None) -> int:
        """Buildings whose footprint bounds intersect ``bbox`` (and ring centroid in
        ``clip``)."""
        _, b, rows = self._store.bounds_in(bbox)
        if clip is None or len(rows) == 0:
            return int(len(rows))
        return int(self._centroid_in(rows, b, clip).sum())

    def footprints_intersecting(self, bbox, clip=None) -> list:
        """Shapely footprints (lng, lat) whose bounds intersect ``bbox``; an unreadable one
        is an empty polygon so the list stays aligned with ``ids_intersecting``."""
        from shapely.geometry import Polygon

        out = []
        for w in self._intersecting(bbox, clip).wkb:
            try:
                out.append(shapely.from_wkb(w))
            except Exception:  # noqa: BLE001
                out.append(Polygon())
        return out

    def ids_intersecting(self, bbox, clip=None) -> np.ndarray:
        """Building ids (-1 when absent) of ``footprints_intersecting(bbox)``, same order."""
        return self._intersecting(bbox, clip).ids

    def building_geoms_in_bbox(self, lat_min: float, lat_max: float, lng_min: float,
                               lng_max: float) -> list:
        """Shapely footprints whose ring centroid lies in the box."""
        batch = self._store.read((lat_min, lat_max, lng_min, lng_max))
        lat, lng = self._centroids_of(batch)
        out = []
        for j in range(len(batch)):
            if np.isfinite(lat[j]) and lat_min <= lat[j] <= lat_max and lng_min <= lng[j] <= lng_max:
                g = shapely.from_wkb(batch.wkb[j])
                if not g.is_empty:
                    out.append(g)
        return out


class ClippedSource:
    """A ``BuildingIndex`` footprint source limited to buildings with centroid in a box."""

    def __init__(self, index: BuildingIndex, box) -> None:
        self.index, self.box = index, tuple(box)

    def count_in_box(self, bbox) -> int:
        return self.index.count_in_box(bbox, clip=self.box)

    def count_intersecting(self, bbox) -> int:
        return self.index.count_intersecting(bbox, clip=self.box)

    def footprints_intersecting(self, bbox) -> list:
        return self.index.footprints_intersecting(bbox, clip=self.box)

    def ids_intersecting(self, bbox) -> np.ndarray:
        return self.index.ids_intersecting(bbox, clip=self.box)


def write_buildings_fgb(path: str, geoms, ids, area_m2=None) -> None:
    """Write footprints (shapely, lng/lat) as a FlatGeobuf with a packed R-tree, fields ``id``
    and ``area_m2`` (the Alberta file's layout; for tests and small extracts — the Alberta
    file itself is written by ``scripts/build_ab_buildings.py``)."""
    import pyogrio.raw

    geoms = list(geoms)
    ids = np.asarray(list(ids), dtype=np.int64)
    area = (np.asarray(list(area_m2), dtype=float) if area_m2 is not None
            else np.zeros(len(geoms)))
    pyogrio.raw.write(path, np.asarray(shapely.to_wkb(geoms), dtype=object), [ids, area],
                      ["id", "area_m2"], driver="FlatGeobuf", geometry_type="Polygon",
                      crs="EPSG:4326", layer_options={"SPATIAL_INDEX": "YES"})
