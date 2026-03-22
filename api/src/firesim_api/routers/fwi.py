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


# ---------------------------------------------------------------------------
# Multi-day accumulation
# ---------------------------------------------------------------------------


class DailyObservation(BaseModel):
    """One noon weather observation in a multi-day sequence."""

    temperature: float = Field(..., ge=-50, le=60, description="Noon temperature (°C)")
    relative_humidity: float = Field(..., ge=0, le=100, description="Noon RH (%)")
    wind_speed: float = Field(..., ge=0, le=200, description="10-m wind speed (km/h)")
    precipitation_24h: float = Field(default=0.0, ge=0, description="24-h rain (mm)")
    month: int = Field(..., ge=1, le=12, description="Calendar month (1–12)")


class FWIMultiDayRequest(BaseModel):
    """Multi-day FWI accumulation request.

    Provide starting codes (spring startup defaults if unknown) and an
    ordered list of daily noon weather observations. The FWI system is
    chained so each day's output codes become the next day's inputs.
    """

    ffmc_start: float = Field(default=85.0, ge=0, le=101, description="Starting FFMC")
    dmc_start: float = Field(default=6.0, ge=0, description="Starting DMC")
    dc_start: float = Field(default=15.0, ge=0, description="Starting DC")
    observations: list[DailyObservation] = Field(
        ..., min_length=1, max_length=366,
        description="Ordered daily noon weather observations (1–366 days)",
    )


class DailyFWIResult(BaseModel):
    """FWI system output for a single day in a multi-day sequence."""

    day: int  # 1-based index
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    danger_rating: str
    temperature: float
    relative_humidity: float
    wind_speed: float
    precipitation_24h: float
    month: int


class FWIMultiDayResponse(BaseModel):
    """Multi-day FWI accumulation response."""

    days: list[DailyFWIResult]
    peak_fwi_day: int  # 1-based day index of highest FWI
    peak_fwi: float
    peak_danger_rating: str


def _danger(fwi_val: float) -> str:
    return next(label for threshold, label in _FWI_THRESHOLDS if fwi_val >= threshold)


@router.post("/multi-day", response_model=FWIMultiDayResponse)
def calculate_fwi_multi_day(request: FWIMultiDayRequest) -> FWIMultiDayResponse:
    """Chain FWI calculations across a sequence of daily weather observations.

    Starts from the provided (or default spring startup) codes and feeds
    each day's output FFMC/DMC/DC into the next day's calculation. Returns
    the full time series plus peak FWI day.

    Typical use: paste a week of station readings from a fire weather station
    to compute the drought code buildup trajectory.

    Source: Van Wagner & Pickett (1985) PS-X-58.
    """
    ffmc_prev = request.ffmc_start
    dmc_prev = request.dmc_start
    dc_prev = request.dc_start

    results: list[DailyFWIResult] = []

    for i, obs in enumerate(request.observations):
        calc = FWICalculator(
            ffmc_prev=ffmc_prev,
            dmc_prev=dmc_prev,
            dc_prev=dc_prev,
        )
        result = calc.calculate_daily(
            temp=obs.temperature,
            rh=obs.relative_humidity,
            wind=obs.wind_speed,
            rain=obs.precipitation_24h,
            month=obs.month,
        )

        results.append(DailyFWIResult(
            day=i + 1,
            ffmc=round(result.ffmc, 1),
            dmc=round(result.dmc, 1),
            dc=round(result.dc, 1),
            isi=round(result.isi, 2),
            bui=round(result.bui, 1),
            fwi=round(result.fwi, 1),
            danger_rating=_danger(result.fwi),
            temperature=obs.temperature,
            relative_humidity=obs.relative_humidity,
            wind_speed=obs.wind_speed,
            precipitation_24h=obs.precipitation_24h,
            month=obs.month,
        ))

        # Carry forward
        ffmc_prev = result.ffmc
        dmc_prev = result.dmc
        dc_prev = result.dc

    peak = max(results, key=lambda r: r.fwi)

    logger.info(
        "Multi-day FWI: %d days, peak FWI=%.1f on day %d (%s)",
        len(results), peak.fwi, peak.day, peak.danger_rating,
    )

    return FWIMultiDayResponse(
        days=results,
        peak_fwi_day=peak.day,
        peak_fwi=peak.fwi,
        peak_danger_rating=peak.danger_rating,
    )
