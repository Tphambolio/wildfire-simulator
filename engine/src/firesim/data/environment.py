"""Load environment barriers (water bodies, buildings) for fuel grid masking."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely.geometry import box, shape
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)


def _load_geojson(path: str) -> list[dict]:
    """Load GeoJSON features from .geojson or .geojson.gz file."""
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


def _rasterize_fgb(path: str, bounds, out: np.ndarray, transform, tile_deg: float = 0.05) -> int:
    """Burn the valid footprints of a FlatGeobuf that intersect ``bounds`` into ``out``,
    reading one tile of ``tile_deg`` degrees at a time (bounded memory for any grid size)."""
    import shapely

    from firesim.data.building_index import open_store

    store = open_store(path)
    lat_min, lat_max, lng_min, lng_max = bounds
    n = 0
    seen: set[int] = set()
    for la in np.arange(lat_min, lat_max, tile_deg):
        for lo in np.arange(lng_min, lng_max, tile_deg):
            tile = (la, min(la + tile_deg, lat_max), lo, min(lo + tile_deg, lng_max))
            batch = store.read(tile)
            geoms = []
            for bid, w in zip(batch.ids, batch.wkb):
                if bid >= 0:
                    if int(bid) in seen:
                        continue
                    seen.add(int(bid))
                g = shapely.from_wkb(w)
                if g.is_valid and not g.is_empty:
                    geoms.append(g)
            if geoms:
                rasterize(((g, 1) for g in geoms), out=out, transform=transform,
                          all_touched=True)
                n += len(geoms)
    return n


def load_environment_mask(
    bounds: tuple[float, float, float, float],
    rows: int,
    cols: int,
    water_path: str | None = None,
    buildings_path: str | None = None,
    building_geoms: list | None = None,
) -> np.ndarray:
    """Create a boolean mask of non-fuel cells from environment layers.

    Args:
        bounds: (lat_min, lat_max, lng_min, lng_max) in WGS84.
        rows: Number of grid rows.
        cols: Number of grid columns.
        water_path: Path to water body GeoJSON (.geojson or .geojson.gz).
        buildings_path: Path to building footprint GeoJSON. Ignored when
            building_geoms is provided.
        building_geoms: Pre-filtered list of shapely geometries (e.g. from
            BuildingIndex). When provided, buildings_path is skipped entirely.

    Returns:
        Boolean array [rows, cols] where True = non-fuel (barrier).
    """
    lat_min, lat_max, lng_min, lng_max = bounds
    grid_box = box(lng_min, lat_min, lng_max, lat_max)

    # Collect all barrier geometries
    all_geometries = []

    if water_path:
        logger.info("Loading water bodies from %s", water_path)
        features = _load_geojson(water_path)
        water_geoms = []
        for f in features:
            try:
                geom = shape(f["geometry"])
                if geom.is_valid and not geom.is_empty:
                    water_geoms.append(geom)
            except Exception:
                continue
        logger.info("Loaded %d water body geometries", len(water_geoms))

        # Use STRtree to find only water bodies that intersect the grid
        if water_geoms:
            tree = STRtree(water_geoms)
            intersecting = tree.query(grid_box)
            clipped = [water_geoms[i] for i in intersecting]
            all_geometries.extend(clipped)
            logger.info("  %d intersect grid bounds", len(clipped))

    if building_geoms is not None:
        # Pre-filtered geometries from BuildingIndex — skip GeoJSON load entirely
        logger.info("Using %d pre-filtered building geometries", len(building_geoms))
        all_geometries.extend(building_geoms)
    elif buildings_path and str(buildings_path).lower().endswith(".fgb"):
        # Spatially indexed file (e.g. all of Alberta): rasterized tile by tile below,
        # never loaded whole
        pass
    elif buildings_path:
        logger.info("Loading buildings from %s", buildings_path)
        features = _load_geojson(buildings_path)
        loaded_geoms = []
        for f in features:
            try:
                geom = shape(f["geometry"])
                if geom.is_valid and not geom.is_empty:
                    loaded_geoms.append(geom)
            except Exception:
                continue
        logger.info("Loaded %d building geometries", len(loaded_geoms))

        # Use STRtree to find only buildings that intersect the grid
        if loaded_geoms:
            tree = STRtree(loaded_geoms)
            intersecting = tree.query(grid_box)
            clipped = [loaded_geoms[i] for i in intersecting]
            all_geometries.extend(clipped)
            logger.info("  %d intersect grid bounds", len(clipped))

    fgb = (building_geoms is None and buildings_path
           and str(buildings_path).lower().endswith(".fgb"))
    if not all_geometries and not fgb:
        logger.info("No environment barriers found within grid bounds")
        return np.zeros((rows, cols), dtype=bool)

    # Rasterize all barrier geometries directly onto the grid using rasterio.
    # This burns polygon footprints into a uint8 raster in C — orders of
    # magnitude faster than per-cell point-in-polygon queries.
    #
    # from_bounds(west, south, east, north, width, height) places the
    # upper-left corner at (lng_min, lat_max), matching row-0 = lat_max.
    logger.info("Rasterizing %d barrier geometries onto %dx%d grid...", len(all_geometries), rows, cols)
    transform = from_bounds(lng_min, lat_min, lng_max, lat_max, cols, rows)
    # all_touched=True marks any cell the geometry touches (not just contains),
    # giving a natural ~1-cell buffer around buildings at no extra cost.
    burned = np.zeros((rows, cols), dtype="uint8")
    if all_geometries:
        rasterize(
            ((geom, 1) for geom in all_geometries),
            out=burned,
            transform=transform,
            all_touched=True,
        )
    if fgb:
        n = _rasterize_fgb(buildings_path, bounds, burned, transform)
        logger.info("Rasterized %d buildings from %s (tiled windowed reads)", n, buildings_path)
    mask = burned > 0

    masked_count = int(mask.sum())
    total = rows * cols
    logger.info(
        "Environment mask: %d/%d cells masked (%.1f%%)",
        masked_count, total, 100.0 * masked_count / total,
    )
    return mask
