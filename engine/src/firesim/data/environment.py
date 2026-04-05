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

    if not all_geometries:
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
    burned = rasterize(
        ((geom, 1) for geom in all_geometries),
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    )
    mask = burned > 0

    masked_count = int(mask.sum())
    total = rows * cols
    logger.info(
        "Environment mask: %d/%d cells masked (%.1f%%)",
        masked_count, total, 100.0 * masked_count / total,
    )
    return mask
