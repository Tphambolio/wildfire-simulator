"""Province-wide vector layers (critical assets, major roads) read by box from FlatGeobuf.

The files (``scripts/build_ab_assets.py``, ``scripts/build_ab_roads.py``) carry a packed
Hilbert R-tree: a box query reads the index and then only the matching features, so memory
scales with the box, not the province (docs/data-sources.md, "Alberta assets and roads").
Features come back as GeoJSON dicts in the schema of the bundled Edmonton layers
(``frontend/public/edmonton/assets.geojson`` / ``roads.geojson``).
"""

from __future__ import annotations

import json

AB_ASSETS_LABEL = "Alberta critical assets (Statistics Canada ODHF, OpenStreetMap)"
AB_ASSETS_ATTRIBUTION = ("Statistics Canada Open Database of Healthcare Facilities (Open "
                         "Government Licence – Canada) · © OpenStreetMap contributors (ODbL)")
AB_ROADS_LABEL = "Alberta major roads (OpenStreetMap)"
AB_ROADS_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"


def _read_box(path: str, bbox: tuple[float, float, float, float], limit: int):
    """(columns, wkb geometries, field arrays, truncated) of the features intersecting ``bbox``
    (west, south, east, north): at most ``limit``, in file (Hilbert) order; ``truncated``
    says when more were in the box."""
    import pyogrio

    fids, _ = pyogrio.read_bounds(path, bbox=bbox)
    truncated = len(fids) > limit
    if truncated:
        fids = fids[:limit]
    if len(fids) == 0:
        return [], [], [], truncated
    meta, _, wkb, fields = pyogrio.raw.read(path, fids=fids)
    return list(meta["fields"]), wkb, fields, truncated


def _rows(cols, fields, n):
    for i in range(n):
        yield {c: (v[i].item() if hasattr(v[i], "item") else v[i]) for c, v in zip(cols, fields)}


def read_assets(path: str, bbox: tuple[float, float, float, float],
                limit: int = 5000) -> tuple[list[dict], bool]:
    """Asset features in the box: point geometry, or the site outline when the file has one
    (``anchor`` then holds the point), with name, category, detail, source, source_id,
    licence, sources (list), verify."""
    import shapely

    cols, wkb, fields, truncated = _read_box(path, bbox, limit)
    feats = []
    for g, p in zip(wkb, _rows(cols, fields, len(wkb))):
        pt = shapely.from_wkb(g)
        props = {k: p.get(k) for k in ("name", "category", "detail", "source", "source_id",
                                       "licence")}
        props["sources"] = json.loads(p.get("sources") or "[]")
        if p.get("verify"):
            props["verify"] = p["verify"]
        if not props.get("detail"):
            props.pop("detail")
        anchor = [round(pt.x, 6), round(pt.y, 6)]
        if p.get("outline"):
            geom = json.loads(p["outline"])
            props["anchor"] = anchor
        else:
            geom = {"type": "Point", "coordinates": anchor}
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return feats, truncated


def read_roads(path: str, bbox: tuple[float, float, float, float],
               limit: int = 20000) -> tuple[list[dict], bool]:
    """Major-road pieces in the box: LineString with name (when named), highway, ref, link."""
    import shapely

    cols, wkb, fields, truncated = _read_box(path, bbox, limit)
    feats = []
    for g, p in zip(wkb, _rows(cols, fields, len(wkb))):
        geom = shapely.from_wkb(g)
        props = {"highway": p.get("highway"), "ref": p.get("ref") or ""}
        if p.get("name"):
            props["name"] = p["name"]
        if p.get("link"):
            props["link"] = 1
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(x, 5), round(y, 5)]
                                                   for x, y in geom.coords]}})
    return feats, truncated
