"""Load WUI zone modifiers from GeoJSON.

Provenance warning: the bundled ``data/wui_zones.geojson.gz`` (425 100 m buffers around parks,
all with ros 0.7, intensity 1.2, ember 3.0) has no documented source or generating script.
The frontend leaves it off by default; do not present results that use it as measured.
"""

from __future__ import annotations

import logging

from shapely.geometry import Point, box, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

from firesim.data.environment import _load_geojson
from firesim.spread.huygens import SpreadModifierGrid

logger = logging.getLogger(__name__)


def load_wui_modifiers(
    wui_zones_path: str,
    bounds: tuple[float, float, float, float],
    rows: int,
    cols: int,
) -> SpreadModifierGrid:
    """Load WUI zones and build a per-cell modifier grid.

    Each WUI zone GeoJSON feature should have properties:
        ros_multiplier (float): ROS scaling (0.7 = 30% slower)
        intensity_multiplier (float): Intensity scaling (1.2 = 20% hotter)
        ember_multiplier (float): Ember spotting scaling (3.0 = 3x embers)

    Args:
        wui_zones_path: Path to WUI zones GeoJSON (.geojson or .geojson.gz).
        bounds: (lat_min, lat_max, lng_min, lng_max) in WGS84.
        rows: Number of grid rows.
        cols: Number of grid columns.

    Returns:
        SpreadModifierGrid with per-cell modifiers.
    """
    lat_min, lat_max, lng_min, lng_max = bounds
    grid_box = box(lng_min, lat_min, lng_max, lat_max)

    features = _load_geojson(wui_zones_path)
    logger.info("Loaded %d WUI zone features from %s", len(features), wui_zones_path)

    # Parse geometries and their modifier properties
    zones = []
    for f in features:
        try:
            geom = shape(f["geometry"])
            if not geom.is_valid or geom.is_empty:
                continue
            props = f.get("properties", {})
            zones.append({
                "geom": geom,
                "ros": float(props.get("ros_multiplier", 1.0)),
                "intensity": float(props.get("intensity_multiplier", 1.0)),
                "ember": float(props.get("ember_multiplier", 1.0)),
            })
        except Exception:
            continue

    # Build spatial index and filter to grid extent
    if not zones:
        logger.warning("No valid WUI zones found")
        return _default_grid(bounds, rows, cols)

    tree = STRtree([z["geom"] for z in zones])
    intersecting_idx = tree.query(grid_box)
    if len(intersecting_idx) == 0:
        logger.info("No WUI zones intersect grid bounds")
        return _default_grid(bounds, rows, cols)

    logger.info("  %d zones intersect grid bounds", len(intersecting_idx))

    # Build STRtree for fast per-cell lookups (O(log n) instead of O(n))
    zone_geoms = [zones[i]["geom"] for i in intersecting_idx]
    zone_data = [zones[i] for i in intersecting_idx]
    cell_tree = STRtree(zone_geoms)

    # Build grids
    ros_grid = [[1.0] * cols for _ in range(rows)]
    int_grid = [[1.0] * cols for _ in range(rows)]
    emb_grid = [[1.0] * cols for _ in range(rows)]

    cell_lat = (lat_max - lat_min) / rows
    cell_lng = (lng_max - lng_min) / cols

    modified_count = 0
    for r in range(rows):
        lat = lat_max - (r + 0.5) * cell_lat
        for c in range(cols):
            lng = lng_min + (c + 0.5) * cell_lng
            pt = Point(lng, lat)
            # STRtree query returns indices of candidate geometries
            candidates = cell_tree.query(pt)
            for idx in candidates:
                if zone_geoms[idx].contains(pt):
                    zone = zone_data[idx]
                    ros_grid[r][c] = zone["ros"]
                    int_grid[r][c] = zone["intensity"]
                    emb_grid[r][c] = zone["ember"]
                    modified_count += 1
                    break

    total = rows * cols
    logger.info(
        "WUI modifier grid %dx%d: %d/%d cells modified (%.1f%%)",
        rows, cols, modified_count, total, 100.0 * modified_count / total,
    )

    return SpreadModifierGrid(
        ros_multiplier=ros_grid,
        intensity_multiplier=int_grid,
        ember_multiplier=emb_grid,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )


def _default_grid(
    bounds: tuple[float, float, float, float], rows: int, cols: int
) -> SpreadModifierGrid:
    """Return a grid with all multipliers = 1.0 (no modification)."""
    lat_min, lat_max, lng_min, lng_max = bounds
    ones = [[1.0] * cols for _ in range(rows)]
    return SpreadModifierGrid(
        ros_multiplier=[row[:] for row in ones],
        intensity_multiplier=[row[:] for row in ones],
        ember_multiplier=[row[:] for row in ones],
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )
