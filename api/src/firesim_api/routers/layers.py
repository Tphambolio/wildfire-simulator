"""Province-wide critical assets and major roads, served by area (docs/api-reference.md).

The app bundles Edmonton's layers (``frontend/public/edmonton/``); elsewhere in Alberta it asks
these endpoints for the box around the ignition, so the province is never sent to the browser.
Both read FlatGeobuf files by box (``firesim.data.vector_layers``); without the files they
answer ``available: false`` with no features (the app then keeps the Edmonton layers only).
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/layers", tags=["layers"])


def _box(south: float, west: float, north: float, east: float) -> tuple[float, float, float, float]:
    from firesim_api.settings import settings

    if not (south < north and west < east):
        raise HTTPException(status_code=422, detail="Need south < north and west < east")
    side = settings.layers_max_box_deg
    if north - south > side or east - west > 2 * side:
        raise HTTPException(status_code=422, detail=(
            f"Box too large: at most {side:g} degrees of latitude and {2 * side:g} of longitude"))
    return west, south, east, north


async def _serve(path: str | None, reader, label: str, attribution: str, bbox) -> dict:
    if not path or not os.path.exists(path):
        return {"type": "FeatureCollection", "features": [],
                "metadata": {"available": False, "label": label, "attribution": attribution,
                             "truncated": False}}
    feats, truncated = await asyncio.get_event_loop().run_in_executor(None, reader, path, bbox)
    return {"type": "FeatureCollection", "features": feats,
            "metadata": {"available": True, "label": label, "attribution": attribution,
                         "bbox": list(bbox), "count": len(feats), "truncated": truncated}}


@router.get("/assets")
async def assets_in_box(
    south: float = Query(..., ge=-90, le=90), west: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90), east: float = Query(..., ge=-180, le=180),
) -> dict:
    """Critical assets (hospitals and care facilities, schools, fire / police / EMS stations,
    recreation and seniors centres, water and wastewater plants, power plants and substations,
    LRT stations) in the box, as GeoJSON in the bundled Edmonton layer's schema."""
    from firesim.data.vector_layers import AB_ASSETS_ATTRIBUTION, AB_ASSETS_LABEL, read_assets
    from firesim_api.settings import settings

    return await _serve(settings.ab_assets_path, read_assets, AB_ASSETS_LABEL,
                        AB_ASSETS_ATTRIBUTION, _box(south, west, north, east))


@router.get("/roads")
async def roads_in_box(
    south: float = Query(..., ge=-90, le=90), west: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90), east: float = Query(..., ge=-180, le=180),
) -> dict:
    """Major roads (OSM motorway, trunk, primary, secondary and their ramps) in the box, as
    GeoJSON line pieces of at most 5 km, in the bundled Edmonton layer's schema."""
    from firesim.data.vector_layers import AB_ROADS_ATTRIBUTION, AB_ROADS_LABEL, read_roads
    from firesim_api.settings import settings

    return await _serve(settings.ab_roads_path, read_roads, AB_ROADS_LABEL,
                        AB_ROADS_ATTRIBUTION, _box(south, west, north, east))
