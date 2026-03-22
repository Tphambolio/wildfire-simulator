"""FWI calculation endpoint.

Accepts a single noon weather observation (temp, RH, wind, rain) plus
optional previous-day codes (FFMC, DMC, DC) and returns all six FWI
components. Useful for users who have a weather station reading and want
to compute the indices themselves rather than waiting for CWFIS.

Source: Van Wagner & Pickett (1985); Forestry Canada (1992).
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from firesim.fwi.calculator import FWICalculator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/fwi", tags=["fwi"])

# FWI danger thresholds (Van Wagner 1987 / CIFFC convention)
_FWI_THRESHOLDS = [
    (30.0, "Very High / Extreme"),
    (19.0, "High"),
    (10.0, "Moderate"),
    (0.0, "Low"),
]


class FWICalculateRequest(BaseModel):
    """Noon weather observation for FWI calculation."""

    temperature: float = Field(..., ge=-50, le=60, description="Noon temperature (°C)")
    relative_humidity: float = Field(..., ge=0, le=100, description="Noon relative humidity (%)")
    wind_speed: float = Field(..., ge=0, le=200, description="10-m wind speed (km/h)")
    precipitation_24h: float = Field(default=0.0, ge=0, description="24-hour precipitation (mm)")
    month: int | None = Field(
        default=None, ge=1, le=12,
        description="Month (1–12). Defaults to current UTC month. "
                    "Affects DMC and DC day-length factors.",
    )

    # Previous-day starting values — if omitted, spring startup defaults are used
    ffmc_prev: float = Field(
        default=85.0, ge=0, le=101,
        description="Previous day's FFMC (default: 85.0 spring startup)",
    )
    dmc_prev: float = Field(
        default=6.0, ge=0,
        description="Previous day's DMC (default: 6.0 spring startup)",
    )
    dc_prev: float = Field(
        default=15.0, ge=0,
        description="Previous day's DC (default: 15.0 spring startup)",
    )


class FWICalculateResponse(BaseModel):
    """Computed FWI system components."""

    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    danger_rating: str
    inputs: FWICalculateRequest


@router.post("/calculate", response_model=FWICalculateResponse)
def calculate_fwi(request: FWICalculateRequest) -> FWICalculateResponse:
    """Compute FWI system components from a noon weather observation.

    Uses the Canadian FWI system equations (Van Wagner & Pickett 1985).
    DMC and DC require a month parameter for day-length correction — if
    omitted, the current calendar month is used.

    If you have previous-day codes from a weather station, pass them as
    ``ffmc_prev``, ``dmc_prev``, ``dc_prev`` for maximum accuracy. Without
    them, spring startup defaults are used, which underestimates DMC/DC
    early in the season and overestimates them mid/late season. FFMC
    responds within a day and is accurate from any starting point.
    """
    month = request.month or datetime.datetime.now(tz=datetime.timezone.utc).month

    calc = FWICalculator(
        ffmc_prev=request.ffmc_prev,
        dmc_prev=request.dmc_prev,
        dc_prev=request.dc_prev,
    )
    result = calc.calculate_daily(
        temp=request.temperature,
        rh=request.relative_humidity,
        wind=request.wind_speed,
        rain=request.precipitation_24h,
        month=month,
    )

    danger = next(
        label for threshold, label in _FWI_THRESHOLDS if result.fwi >= threshold
    )

    logger.info(
        "FWI calculated: temp=%.1f rh=%.0f wind=%.1f → FWI=%.1f (%s)",
        request.temperature, request.relative_humidity,
        request.wind_speed, result.fwi, danger,
    )

    return FWICalculateResponse(
        ffmc=round(result.ffmc, 1),
        dmc=round(result.dmc, 1),
        dc=round(result.dc, 1),
        isi=round(result.isi, 2),
        bui=round(result.bui, 1),
        fwi=round(result.fwi, 1),
        danger_rating=danger,
        inputs=request,
    )
