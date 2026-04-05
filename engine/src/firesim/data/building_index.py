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
