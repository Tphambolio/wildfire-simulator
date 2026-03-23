"""GeoJSON perimeter ↔ FireVertex conversions.

Supports importing drone-observed fire perimeters (M4TD orthomosaic trace or
manually drawn) as a list of FireVertex objects for use as an initial_front in
the Huygens simulator.

GeoJSON coordinates are [lng, lat] per RFC 7946; FireVertex uses (lat, lng).
"""

from __future__ import annotations

from firesim.spread.huygens import FireVertex


def geojson_to_fire_vertices(geometry: dict) -> list[FireVertex]:
    """Convert a GeoJSON Polygon or MultiPolygon geometry to FireVertex list.

    Uses the exterior ring of the first polygon.  Closed rings (where the
    last coordinate repeats the first) are de-duplicated automatically.

    Args:
        geometry: GeoJSON geometry object with ``type`` and ``coordinates``.
                  Coordinates must be ``[lng, lat]`` (GeoJSON standard).

    Returns:
        Ordered list of FireVertex objects in (lat, lng) order.

    Raises:
        ValueError: Unsupported geometry type, or fewer than 3 unique vertices.
    """
    geom_type = geometry.get("type")

    if geom_type == "Polygon":
        # coordinates = [exterior_ring, *holes]
        ring: list[list[float]] = geometry["coordinates"][0]
    elif geom_type == "MultiPolygon":
        # coordinates = [[exterior_ring, *holes], ...]  — use first polygon
        ring = geometry["coordinates"][0][0]
    else:
        raise ValueError(
            f"Unsupported geometry type '{geom_type}'. "
            "Expected 'Polygon' or 'MultiPolygon'."
        )

    vertices: list[FireVertex] = []
    for coord in ring:
        lng, lat = float(coord[0]), float(coord[1])
        vertices.append(FireVertex(lat=lat, lng=lng))

    # Drop closing duplicate (GeoJSON rings close on themselves)
    if len(vertices) >= 2:
        first, last = vertices[0], vertices[-1]
        if first.lat == last.lat and first.lng == last.lng:
            vertices = vertices[:-1]

    if len(vertices) < 3:
        raise ValueError(
            f"Perimeter must have at least 3 unique vertices, got {len(vertices)}."
        )

    return vertices
