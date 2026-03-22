"""Live fire weather endpoint.

Fetches current FWI system indices from the CWFIS (Canadian Wildland Fire
Information System) forecast API for any lat/lng, returning values ready
to load directly into a simulation's FWI overrides.

Source: Natural Resources Canada CWFIS — https://cwfis.cfs.nrcan.gc.ca
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])

_CWFIS_URL = "https://cwfis.cfs.nrcan.gc.ca/api/forecast"
_TIMEOUT_S = 8.0


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


@router.get("/current", response_model=CurrentWeather)
async def get_current_weather(
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude")],
    lng: Annotated[float, Query(ge=-180, le=180, description="Longitude")],
) -> CurrentWeather:
    """Fetch current FWI indices for a location from CWFIS.

    Returns FFMC, DMC, DC (for use as FWI overrides) plus wind/temp/RH.
    During off-season or when the API is unavailable, returns
    available=false with a descriptive message — the frontend should
    show this gracefully rather than erroring.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                _CWFIS_URL,
                params={"lat": lat, "lon": lng, "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()

    except httpx.TimeoutException:
        logger.warning("CWFIS request timed out for (%.4f, %.4f)", lat, lng)
        return _unavailable(lat, lng, "CWFIS request timed out")

    except httpx.HTTPStatusError as exc:
        logger.warning("CWFIS returned HTTP %d", exc.response.status_code)
        return _unavailable(lat, lng, f"CWFIS returned HTTP {exc.response.status_code}")

    except Exception as exc:
        logger.warning("CWFIS fetch failed: %s", exc)
        return _unavailable(lat, lng, "Could not reach CWFIS")

    ffmc = _float(data.get("ffmc"))
    dmc = _float(data.get("dmc"))
    dc = _float(data.get("dc"))
    isi = _float(data.get("isi"))
    bui = _float(data.get("bui"))
    fwi = _float(data.get("fwi"))
    wind_speed = _float(data.get("ws") or data.get("wind_speed"))
    wind_direction = _float(data.get("wd") or data.get("wind_direction"))
    temperature = _float(data.get("temp") or data.get("temperature"))
    rh = _float(data.get("rh") or data.get("relative_humidity"))

    if ffmc is None and fwi is None:
        return _unavailable(
            lat, lng,
            "Fire weather data not available — CWFIS may be in off-season mode"
        )

    fwi_label = _fwi_label(fwi)
    logger.info(
        "CWFIS weather for (%.3f, %.3f): FWI=%.1f (%s)",
        lat, lng, fwi or 0, fwi_label,
    )

    # Use forecast date from response if available, otherwise current UTC time
    raw_date = data.get("rep_date") or data.get("date") or data.get("forecast_date")
    data_ts = str(raw_date) if raw_date else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        source="CWFIS / Natural Resources Canada",
        available=True,
        message=f"FWI {fwi:.1f} — {fwi_label}" if fwi is not None else "Fire weather loaded",
        data_timestamp=data_ts,
    )


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
