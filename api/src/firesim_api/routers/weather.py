"""Live fire weather endpoint.

Fetches current fire weather observations from the CWFIS (Canadian Wildland
Fire Information System) via their public GeoServer WFS service, selecting
the nearest station to the requested lat/lng.

Source: Natural Resources Canada CWFIS WFS
  https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows
  Layer: public:firewx_stns_current

FWI codes (FFMC, DMC, DC, ISI, BUI, FWI) are only computed during the active
fire season (approximately April–October).  Off-season observations still
return valid weather parameters (wind, temperature, RH) but with null FWI
codes.  The frontend should handle this gracefully.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])

_WFS_URL = (
    "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"
)
_LAYER = "public:firewx_stns_current"
# Bounding box half-width in degrees; ~220 km — captures ≥1 station in all of Canada
_BBOX_DEG = 2.0
_MAX_FEATURES = 50
_TIMEOUT_S = 10.0


class CurrentWeather(BaseModel):
    """Live fire weather values for a location."""

    lat: float
    lng: float
    ffmc: float | None
    dmc: float | None
    dc: float | None
    isi: float | None
    bui: float | None
    fwi: float | None
    wind_speed: float | None
    wind_direction: float | None
    temperature: float | None
    relative_humidity: float | None
    source: str
    available: bool
    message: str
    data_timestamp: str | None = None
    station_name: str | None = None
    distance_km: float | None = None


@router.get("/current", response_model=CurrentWeather)
async def get_current_weather(
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude")],
    lng: Annotated[float, Query(ge=-180, le=180, description="Longitude")],
) -> CurrentWeather:
    """Fetch current fire weather for a location from the nearest CWFIS station.

    Queries the CWFIS GeoServer WFS for all fire weather stations within
    ±2° of the requested point and returns data from the closest station.

    Returns FFMC, DMC, DC (for use as FWI overrides) plus wind/temp/RH.
    During the off-season (approx. Nov–Mar), FWI codes will be null but
    weather observations are still returned — the frontend should populate
    weather fields and leave FWI sliders at their current values.

    Returns available=false only when no station data can be retrieved at all.
    """
    try:
        features = await _fetch_nearby_stations(lat, lng)
    except httpx.TimeoutException:
        logger.warning("CWFIS WFS timed out for (%.4f, %.4f)", lat, lng)
        return _unavailable(lat, lng, "CWFIS request timed out")
    except Exception as exc:
        logger.warning("CWFIS fetch failed: %s", exc)
        return _unavailable(lat, lng, "Could not reach CWFIS")

    if not features:
        return _unavailable(
            lat, lng,
            "No fire weather stations found within range — try a different location"
        )

    station, dist_km = _nearest(lat, lng, features)
    props = station["properties"]

    ffmc = _float(props.get("ffmc"))
    dmc = _float(props.get("dmc"))
    dc = _float(props.get("dc"))
    isi = _float(props.get("isi"))
    bui = _float(props.get("bui"))
    fwi = _float(props.get("fwi"))
    wind_speed = _float(props.get("ws"))
    wind_direction = _float(props.get("wdir"))
    temperature = _float(props.get("temp"))
    rh = _float(props.get("rh"))

    station_name = str(props.get("name", "")).replace("+", " ").strip() or None
    rep_date = props.get("rep_date")
    data_ts = str(rep_date) if rep_date else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    has_weather = temperature is not None or rh is not None or wind_speed is not None
    has_fwi = fwi is not None

    if not has_weather:
        return _unavailable(lat, lng, "Station data incomplete — no weather observations")

    if has_fwi:
        fwi_label = _fwi_label(fwi)
        msg = f"FWI {fwi:.1f} — {fwi_label}"
    else:
        msg = "Weather loaded — FWI codes unavailable (off-season)"

    prov = str(props.get("prov", "")).strip()
    source_tag = f"CWFIS — {station_name}" if station_name else "CWFIS / Natural Resources Canada"
    if prov:
        source_tag += f" ({prov})"

    logger.info(
        "CWFIS station '%s' %.1f km away for (%.3f, %.3f): FWI=%s, T=%.1f°C",
        station_name, dist_km, lat, lng, fwi, temperature or 0,
    )

    return CurrentWeather(
        lat=lat,
        lng=lng,
        ffmc=ffmc,
        dmc=dmc,
        dc=dc,
        isi=isi,
        bui=bui,
        fwi=fwi,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        temperature=temperature,
        relative_humidity=rh,
        source=source_tag,
        available=True,
        message=msg,
        data_timestamp=data_ts,
        station_name=station_name,
        distance_km=round(dist_km, 1),
    )


async def _fetch_nearby_stations(lat: float, lng: float) -> list[dict]:
    """Query CWFIS WFS for fire weather stations within ±BBOX_DEG of point."""
    lat_min = lat - _BBOX_DEG
    lat_max = lat + _BBOX_DEG
    lon_min = lng - _BBOX_DEG
    lon_max = lng + _BBOX_DEG

    # CQL filter on the lat/lon property columns (available in this layer)
    cql = (
        f"lat BETWEEN {lat_min} AND {lat_max} "
        f"AND lon BETWEEN {lon_min} AND {lon_max}"
    )

    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": _LAYER,
        "outputFormat": "application/json",
        "count": str(_MAX_FEATURES),
        "CQL_FILTER": cql,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.get(_WFS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    return data.get("features", [])


def _nearest(lat: float, lng: float, features: list[dict]) -> tuple[dict, float]:
    """Return the (feature, distance_km) of the closest station."""
    best: dict | None = None
    best_d = float("inf")
    for feat in features:
        props = feat["properties"]
        try:
            slat = float(props["lat"])
            slng = float(props["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        d = _haversine_km(lat, lng, slat, slng)
        if d < best_d:
            best_d = d
            best = feat
    if best is None:
        best = features[0]
        best_d = 0.0
    return best, best_d


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _unavailable(lat: float, lng: float, reason: str) -> CurrentWeather:
    return CurrentWeather(
        lat=lat, lng=lng,
        ffmc=None, dmc=None, dc=None,
        isi=None, bui=None, fwi=None,
        wind_speed=None, wind_direction=None,
        temperature=None, relative_humidity=None,
        source="CWFIS / Natural Resources Canada",
        available=False,
        message=reason,
    )


def _float(val: object) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _fwi_label(fwi: float | None) -> str:
    if fwi is None:
        return "Unknown"
    if fwi >= 30:
        return "Very High / Extreme"
    if fwi >= 19:
        return "High"
    if fwi >= 10:
        return "Moderate"
    return "Low"
