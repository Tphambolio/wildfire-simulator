"""Load environment barriers (water bodies, buildings) for fuel grid masking."""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box, shape
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
) -> np.ndarray:
    """Create a boolean mask of non-fuel cells from environment layers.

    Args:
        bounds: (lat_min, lat_max, lng_min, lng_max) in WGS84.
        rows: Number of grid rows.
        cols: Number of grid columns.
        water_path: Path to water body GeoJSON (.geojson or .geojson.gz).
        buildings_path: Path to building footprint GeoJSON.

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

    if buildings_path:
        logger.info("Loading buildings from %s", buildings_path)
        features = _load_geojson(buildings_path)
        building_geoms = []
        for f in features:
            try:
                geom = shape(f["geometry"])
                if geom.is_valid and not geom.is_empty:
                    building_geoms.append(geom)
            except Exception:
                continue
        logger.info("Loaded %d building geometries", len(building_geoms))

        # Use STRtree to find only buildings that intersect the grid
        if building_geoms:
            tree = STRtree(building_geoms)
            intersecting = tree.query(grid_box)
            clipped = [building_geoms[i] for i in intersecting]
            # Small buffer (~10m / 0.00009 degrees) to account for
            # adjacent non-vegetated land around structures
            buffered = [g.buffer(0.00009) for g in clipped]
            all_geometries.extend(buffered)
            logger.info("  %d intersect grid bounds (buffered ~10m)", len(clipped))

    if not all_geometries:
        logger.info("No environment barriers found within grid bounds")
        return np.zeros((rows, cols), dtype=bool)

    # Build a spatial index for fast per-cell queries — avoids the memory-
    # intensive unary_union of potentially hundreds of thousands of polygons.
    logger.info("Building spatial index over %d barrier geometries...", len(all_geometries))
    tree = STRtree(all_geometries)

    # Generate all cell-centre coordinates as a flat array and query the tree
    # in one vectorised pass using the 'intersects' predicate (faster than
    # looping over individual Points and calling .contains on each).
    cell_lat = (lat_max - lat_min) / rows
    cell_lng = (lng_max - lng_min) / cols

    lats = lat_max - (np.arange(rows) + 0.5) * cell_lat  # shape (rows,)
    lngs = lng_min + (np.arange(cols) + 0.5) * cell_lng  # shape (cols,)

    # Build a grid of cell-centre Points in row-major order
    lng_grid, lat_grid = np.meshgrid(lngs, lats)  # both shape (rows, cols)
    cell_points = [
        Point(lng_grid[r, c], lat_grid[r, c])
        for r in range(rows)
        for c in range(cols)
    ]

    # Query which cell centres fall inside any barrier geometry.
    # tree.query returns (input_geometry_indices, tree_geometry_indices).
    # input_geometry_indices are the flat cell indices we need.
    mask = np.zeros(rows * cols, dtype=bool)
    result = tree.query(cell_points, predicate="within")
    if result.shape[1] > 0:
        mask[result[0]] = True  # result[0] = cell indices
    mask = mask.reshape(rows, cols)

    masked_count = int(mask.sum())
    total = rows * cols
    logger.info(
        "Environment mask: %d/%d cells masked (%.1f%%)",
        masked_count, total, 100.0 * masked_count / total,
    )
    return mask
