"""Neighbourhood-partitioned building index for fast per-ignition masking.

Loads all building footprints once, spatially joins each building to its
Edmonton neighbourhood polygon, then serves only the 3-4 nearest
neighbourhoods worth of buildings for a given ignition point.

This reduces the rasterize workload from ~334K buildings (full city) to
~5-15K per simulation — cutting startup time from 1-2 min to seconds.

Performance: the assignment loop computes centroids from raw GeoJSON
coordinates (averaging ring vertices) without constructing full shapely
polygons, which is ~20x faster than calling shape() on 346K features.
Shapely polygons are only built lazily in building_geoms_for().
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from shapely.geometry import shape
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)


def _raw_centroid(geometry: dict) -> tuple[float, float] | None:
    """Compute approximate centroid from raw GeoJSON coords.

    Averages the outer ring of the first polygon — fast because it needs
    no shapely construction, just list arithmetic over raw coordinate arrays.
    Returns (lat, lng) or None if the geometry is unsupported.
    """
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


class BuildingIndex:
    """One-time spatial join of buildings → neighbourhoods, cached per path pair.

    Args:
        buildings_path: Path to building footprints GeoJSON (.geojson or .geojson.gz).
        neighbourhoods_path: Path to neighbourhood polygons GeoJSON.
    """

    def __init__(self, buildings_path: str, neighbourhoods_path: str) -> None:
        import gzip
        import json

        def _load(path: str) -> list[dict]:
            p = Path(path)
            if p.suffix == ".gz":
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            if data.get("type") == "FeatureCollection":
                return data["features"]
            if data.get("type") == "Feature":
                return [data]
            return []

        # ── Load neighbourhood polygons ──────────────────────────────────────
        logger.info("BuildingIndex: loading neighbourhoods from %s", neighbourhoods_path)
        nbhd_features = _load(neighbourhoods_path)
        nbhd_geoms: list = []
        nbhd_keys: list[str] = []
        nbhd_centroids: list[tuple[float, float]] = []  # (lat, lng)

        for f in nbhd_features:
            try:
                geom = shape(f["geometry"])
                if not geom.is_valid or geom.is_empty:
                    continue
                key = f["properties"].get("neighbourhood") or f["properties"].get("name", "")
                nbhd_geoms.append(geom)
                nbhd_keys.append(key)
                c = geom.centroid
                nbhd_centroids.append((c.y, c.x))  # (lat, lng)
            except Exception:
                continue

        logger.info("BuildingIndex: loaded %d neighbourhood polygons", len(nbhd_geoms))

        nbhd_tree = STRtree(nbhd_geoms)

        # ── Load all buildings, assign each to a neighbourhood ───────────────
        # Store raw GeoJSON features grouped by neighbourhood.
        # Shapely polygon construction is deferred to building_geoms_for()
        # so the index build only needs cheap centroid math, not 346K shape() calls.
        logger.info("BuildingIndex: loading buildings from %s", buildings_path)
        building_features = _load(buildings_path)
        logger.info("BuildingIndex: assigning %d buildings to neighbourhoods...", len(building_features))

        # {nbhd_key: [raw_geojson_geometry_dict, ...]}
        raw_by_nbhd: dict[str, list[dict]] = {k: [] for k in nbhd_keys}
        # {nbhd_key: [(lat, lng), ...]} — approximate centroids
        centroids_by_nbhd: dict[str, list[tuple[float, float]]] = {k: [] for k in nbhd_keys}
        # {nbhd_key: [building id, ...]}: the feature's ``id`` property (-1 when absent), for
        # per-building attributes (structure step 3: vegetation, roof scenario keys)
        ids_by_nbhd: dict[str, list[int]] = {k: [] for k in nbhd_keys}

        unassigned = 0
        for f in building_features:
            try:
                geometry = f.get("geometry")
                if not geometry:
                    continue
                ctr = _raw_centroid(geometry)
                if ctr is None:
                    continue
                clat, clng = ctr

                # Query STRtree: which neighbourhood contains this centroid?
                from shapely.geometry import Point
                pt = Point(clng, clat)
                hits = nbhd_tree.query(pt, predicate="within")
                if len(hits) > 0:
                    idx = int(hits[0])
                else:
                    # Fallback: nearest neighbourhood centroid by Euclidean distance
                    best_idx = 0
                    best_dist = float("inf")
                    for i, (nlat, nlng) in enumerate(nbhd_centroids):
                        d = math.hypot(clng - nlng, clat - nlat)
                        if d < best_dist:
                            best_dist = d
                            best_idx = i
                    idx = best_idx
                    unassigned += 1

                key = nbhd_keys[idx]
                raw_by_nbhd[key].append(geometry)
                centroids_by_nbhd[key].append((clat, clng))
                try:
                    bid = int((f.get("properties") or {}).get("id", -1))
                except (TypeError, ValueError):
                    bid = -1
                ids_by_nbhd[key].append(bid)
            except Exception:
                continue

        total_assigned = sum(len(v) for v in raw_by_nbhd.values())
        logger.info(
            "BuildingIndex: %d buildings assigned (%d via nearest fallback)",
            total_assigned, unassigned,
        )

        self._nbhd_keys = nbhd_keys
        self._nbhd_centroids = nbhd_centroids  # (lat, lng) per neighbourhood
        self._raw_by_nbhd = raw_by_nbhd
        self._centroids_by_nbhd = centroids_by_nbhd
        self._ids_by_nbhd = ids_by_nbhd

    def nearest_neighbourhoods(self, lat: float, lng: float, n: int = 4) -> list[str]:
        """Return the n neighbourhood keys nearest to (lat, lng) by centroid distance."""
        distances = [
            (math.hypot(lng - nlng, lat - nlat), key)
            for (nlat, nlng), key in zip(self._nbhd_centroids, self._nbhd_keys)
        ]
        distances.sort(key=lambda x: x[0])
        return [key for _, key in distances[:n]]

    def building_geoms_for(self, nbhd_keys: list[str]) -> list:
        """Return shapely geometries for buildings in the given neighbourhoods.

        Shapely construction is deferred to here so the index build loop
        only computes cheap raw centroids rather than 346K shape() calls.
        """
        result = []
        for key in nbhd_keys:
            for geometry in self._raw_by_nbhd.get(key, []):
                try:
                    geom = shape(geometry)
                    if geom.is_valid and not geom.is_empty:
                        result.append(geom)
                except Exception:
                    continue
        return result

    def building_centroids_for(self, nbhd_keys: list[str]) -> list[tuple[float, float]]:
        """Return (lat, lng) centroids for buildings in the given neighbourhoods."""
        result = []
        for key in nbhd_keys:
            result.extend(self._centroids_by_nbhd.get(key, []))
        return result

    def _flatten(self) -> None:
        """Flat per-building lists (every neighbourhood once): raw geometry, centroid, id."""
        import numpy as np

        raw: list[dict] = []
        lat: list[float] = []
        lng: list[float] = []
        ids: list[int] = []
        ids_by = getattr(self, "_ids_by_nbhd", {})
        for key, geoms in self._raw_by_nbhd.items():  # each key once
            raw.extend(geoms)
            for clat, clng in self._centroids_by_nbhd.get(key, []):
                lat.append(clat)
                lng.append(clng)
            got = ids_by.get(key, [])
            ids.extend(got if len(got) == len(geoms) else [-1] * len(geoms))
        self._flat_geoms = raw
        self._flat_lat = np.asarray(lat, dtype=float)
        self._flat_lng = np.asarray(lng, dtype=float)
        self._flat_ids = np.asarray(ids, dtype=np.int64)

    def building_geoms_in_bbox(
        self, lat_min: float, lat_max: float, lng_min: float, lng_max: float
    ) -> list:
        """Shapely geometries of the buildings whose centroid lies in the box.

        For structure-to-structure spread, which needs every building in the run area rather
        than only the neighbourhoods nearest the ignition. Builds flat centroid arrays once.
        """
        import numpy as np

        if not hasattr(self, "_flat_geoms"):
            self._flatten()
        sel = np.nonzero(
            (self._flat_lat >= lat_min) & (self._flat_lat <= lat_max)
            & (self._flat_lng >= lng_min) & (self._flat_lng <= lng_max)
        )[0]
        result = []
        for i in sel:
            try:
                geom = shape(self._flat_geoms[i])
                if not geom.is_empty:
                    result.append(geom)
            except Exception:
                continue
        return result

    # ── Footprint source for structure spread (firesim.structures.spread) ───────────────
    # Compact per-building arrays (raw centroid + bounds, ~14 MB for 346K buildings) so a run
    # can count and select the footprints of the area its structure spread can reach, and
    # build shapely geometries only for those (not for the whole city; 2026-10-10 OOM fix).

    def _ensure_flat(self) -> None:
        import numpy as np

        if hasattr(self, "_flat_bounds"):
            return
        if not hasattr(self, "_flat_geoms"):
            self._flatten()
        b = np.full((len(self._flat_geoms), 4), np.nan)
        for i, g in enumerate(self._flat_geoms):
            try:
                c = g["coordinates"]
                rings = c if g["type"] == "Polygon" else [r for p in c for r in p]
                xs = [pt[0] for r in rings for pt in r]
                ys = [pt[1] for r in rings for pt in r]
                b[i] = (min(xs), min(ys), max(xs), max(ys))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        self._flat_bounds = b  # lng_min, lat_min, lng_max, lat_max (NaN = unusable)

    def count_in_box(self, bbox: tuple[float, float, float, float]) -> int:
        """Buildings whose (raw) centroid is inside ``bbox`` = (lat_min, lat_max, lng_min, lng_max)."""
        import numpy as np

        self._ensure_flat()
        lat_min, lat_max, lng_min, lng_max = bbox
        return int(np.sum((self._flat_lat >= lat_min) & (self._flat_lat <= lat_max)
                          & (self._flat_lng >= lng_min) & (self._flat_lng <= lng_max)))

    def _intersecting(self, bbox):
        import numpy as np

        self._ensure_flat()
        lat_min, lat_max, lng_min, lng_max = bbox
        b = self._flat_bounds
        with np.errstate(invalid="ignore"):
            return np.nonzero((b[:, 0] <= lng_max) & (b[:, 2] >= lng_min)
                              & (b[:, 1] <= lat_max) & (b[:, 3] >= lat_min))[0]

    def count_intersecting(self, bbox: tuple[float, float, float, float]) -> int:
        """Buildings whose footprint bounds intersect ``bbox``."""
        return int(len(self._intersecting(bbox)))

    def footprints_intersecting(self, bbox: tuple[float, float, float, float]) -> list:
        """Shapely footprints (lng, lat) whose bounds intersect ``bbox``; built on demand.

        An unreadable footprint is returned as an empty polygon (dropped by ``build_units``)
        so the list stays aligned with ``ids_intersecting``."""
        from shapely.geometry import Polygon

        out = []
        for i in self._intersecting(bbox):
            try:
                out.append(shape(self._flat_geoms[i]))
            except Exception:
                out.append(Polygon())
        return out

    def ids_intersecting(self, bbox: tuple[float, float, float, float]):
        """Building ids (the GeoJSON ``id`` property, -1 when absent) of
        ``footprints_intersecting(bbox)``, in the same order."""
        self._ensure_flat()
        return self._flat_ids[self._intersecting(bbox)]
