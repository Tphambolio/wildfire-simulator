"""Live fire weather endpoint.

Source tiers, in order (``source_tier`` in the response):

1. ``pyra`` — the codes Pyra (the team's fire-weather app, https://tphambolio.github.io/FWI/)
   shows today for the nearest Pyra station: Pyra's published CWFIS carry-over stepped one day
   with the GEM noon-LST weather, exactly as Pyra's station page does (``services/pyra.py``).
   Alberta only; off with ``FIRESIM_PYRA_SOURCE=0``.
2. ``cwfis`` / ``cwfis_archive`` — the nearest CWFIS station with FWI codes, as published.
3. ``gem_estimate`` — a one-day cold-start estimate from the GEM noon-LST forecast (ECCC GEM read
   from MSC GeoMet, ``services/geomet.py``; docs/weather-geomet.md).

The CWFIS tiers fetch fire weather observations and FWI codes from the CWFIS (Canadian Wildland
Fire Information System) public GeoServer WFS and select the nearest station that has FWI codes.

Source: Natural Resources Canada CWFIS WFS
  https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows
  Layers (names checked live with GetCapabilities on 2026-10-10):
  - ``public:firewx_stns_current``: latest daily report per station. Until about 19 UTC it holds
    the previous day's codes, and it returns no features while it is refreshed.
  - ``public:firewx_stns``: the archive of daily reports (``rep_date`` = the report date at
    12:00Z), used when the current layer is empty; codes at most two days old are used.

The FWI codes are the noon-LST daily values for ``rep_date``'s date. The response says how old
they are (``codes_date``, ``codes_status``, ``codes_label``): before noon LST today's codes do
not exist yet, so the newest codes are yesterday's.

FWI codes are only computed during the active fire season (approximately April-October). When no
station within range has codes (off-season), or CWFIS has nothing, the codes are a **cold-start
estimate**: one day stepped from the start-up values 85 / 6 / 15 (Van Wagner 1987, printed p. 14
of FTR-35; Van Wagner & Pickett 1985) with the ECCC GEM weather (MSC GeoMet, HRDPS → RDPS → GDPS)
at the latest noon LST and the 24 h rain to that noon. Until 2026-10-30 the GEM values came from
the Open-Meteo forecast API (``models=gem_seamless``), whose free tier is non-commercial.
DMC, DC and BUI from a cold start are far
too low except just after snowmelt; the response labels them as an estimate.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from firesim.fwi.calculator import FWICalculator
from firesim.fwi.classes import fwi_class

from ..services import alberta_time, cwfis_tz, geomet
from ..services import pyra as pyra_source
from ..settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])

_WFS_URL = (
    "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"
)
_LAYER = "public:firewx_stns_current"
_ARCHIVE_LAYER = "public:firewx_stns"
# Codes older than this many days are not used from the archive
_ARCHIVE_MAX_AGE_DAYS = 2
# Bounding box half-width in degrees; ~220 km — captures ≥1 station in all of Canada
_BBOX_DEG = 2.0
_MAX_FEATURES = 50
_TIMEOUT_S = 10.0

# Forecast model: ECCC GEM read from MSC GeoMet (services/geomet.py), the model family the
# ensemble's input-error climatology was measured on (scripts/validation/input_errors.py, via
# Open-Meteo's gem_seamless previous-runs archive). The frontend's hourly forecast comes from the
# same service (GET /api/v1/weather/hourly).
FORECAST_MODEL = geomet.MODEL_ID
FORECAST_MODEL_LABEL = "ECCC GEM (GeoMet)"
_START_UP = (85.0, 6.0, 15.0)

# Standard (non-daylight) UTC offset in hours used for "noon LST", by province/territory code as
# CWFIS reports it. Alberta stations report at noon fire-weather LST = 19 UTC (CWFIS tz_correct -7,
# not the legal clock, which is UTC-6 all year from 2026-11-01); the Alberta value is dated and
# configurable in ``services/alberta_time.py``. In provinces where CWFIS uses more than one
# value (SK "SA", BC, ON, QC, NL "NF", NU) the nearest CWFIS station's value is used
# (``services/cwfis_tz.py``); the table below is the fallback and the single-value provinces.
# Values are CWFIS ``tz_correct`` (cwfis_allstn2026.csv): Yukon -8 (not its legal UTC-7), and
# whole hours for Newfoundland (-3 island, -4 Labrador), as CWFIS uses hourly records.
_STD_OFFSET_H = {
    "BC": -8.0, "YT": -8.0, "AB": -7.0, "NT": -7.0, "SK": -6.0, "SA": -6.0, "MB": -6.0,
    "ON": -5.0, "QC": -5.0, "NB": -4.0, "NS": -4.0, "PE": -4.0, "NL": -3.0, "NF": -3.0,
}


class PyraDetails(BaseModel):
    """How the "pyra" tier got its codes (present only when ``source_tier == "pyra"``)."""

    # Pyra station the point maps to (nearest in Pyra's Alberta list) and its distance from the point
    station_name: str
    station_lat: float
    station_lng: float
    station_distance_km: float
    # Pyra's station page for that station (where the same numbers are shown)
    page_url: str
    # Where the carried codes came from: "cwfis_live" (CWFIS current layer, Pyra's holding cache)
    # or "pyra_cwfis_prev" (Pyra's published data/cwfis_prev.json)
    chain_source: str
    chain_station_name: str | None = None
    chain_station_id: str | None = None
    # Distance from the Pyra station to the station whose codes were carried
    chain_distance_km: float | None = None
    # Noon-LST date of the carried codes
    chain_date: str | None = None
    # How today's codes were made: "none" (codes used as published), "gem_noon_forecast"
    # (pre-noon: stepped with the GEM noon-LST forecast), "gem_noon" (after noon: GEM noon-LST
    # value), "cwfis_obs" (after noon: a weather-only CWFIS station's observation)
    step: str
    # 24 h rain (mm) to noon LST used for the step
    rain_24h: float | None = None
    # Pyra's "peak burn" indices: the same codes with the 16:00 UTC-6 (MDT / MT) GEM wind (display only)
    peak_wind_speed: float | None = None
    peak_isi: float | None = None
    peak_fwi: float | None = None
    # ``generated`` time and URL of the carry-over file that was read
    carry_over_generated: str | None = None
    carry_over_url: str | None = None
    # Pyra commit FireSim's port (station list, rules) was checked against
    pyra_commit: str
    notes: list[str] = []


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
    # Date (YYYY-MM-DD, noon LST) the FWI codes are valid for
    codes_date: str | None = None
    # "today" | "yesterday" | "older" (station codes) | "estimate" (cold-start estimate) |
    # "forecast" (pyra, before noon LST: stepped with the noon-LST forecast) |
    # "stepped" (pyra, after noon LST: carried from an earlier chain with today's noon weather)
    codes_status: str | None = None
    # Human-readable age of the codes, e.g. "Yesterday's codes (as of noon LST 2026-10-09)"
    codes_label: str | None = None
    # Model weather used for any value in this response: "eccc_gem_geomet" (ECCC GEM via MSC
    # GeoMet; "gem_seamless" = Open-Meteo before 2026-10-30) or None (station data only)
    weather_model: str | None = None
    # Which tier answered: "pyra" | "cwfis" | "cwfis_archive" | "gem_estimate" (None = unavailable)
    source_tier: str | None = None
    # Details of the "pyra" tier
    pyra: PyraDetails | None = None


def _now() -> datetime:
    """Current UTC time (a function so tests can fix the clock)."""
    return datetime.now(timezone.utc)


@router.get("/current", response_model=CurrentWeather)
async def get_current_weather(
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude")],
    lng: Annotated[float, Query(ge=-180, le=180, description="Longitude")],
) -> CurrentWeather:
    """Current fire weather and starting FWI codes for a location.

    In Alberta the "pyra" tier answers first (below). Otherwise: queries the CWFIS GeoServer WFS for fire weather stations within ±2° of the point and uses
    the closest station that reports FFMC, DMC and DC (stations without codes are skipped; their
    distance is not reported). If the current layer is empty (it is refreshed around 19 UTC),
    the archive layer's newest reports at most two days old are used.

    Returns FFMC, DMC, DC (for use as FWI overrides) plus wind/temp/RH, and how old the codes
    are. If no station has codes (off-season), or CWFIS is unreachable, the codes are a
    cold-start estimate from the ECCC GEM (GeoMet) noon-LST weather, labelled as such.

    Before all of that, in Alberta, the "pyra" tier returns the codes Pyra's station page shows
    today for the nearest Pyra station (see ``services/pyra.py``); if it has no value the reason is
    appended to the message of the tier that answers.

    Returns available=false only when neither CWFIS nor GeoMet gives anything usable.
    """
    now = _now()
    pyra_note: str | None = None
    if settings.pyra_source:
        try:
            got = await pyra_source.pyra_today(lat, lng, now)
        except Exception as exc:  # never let the Pyra tier break the endpoint
            logger.warning("Pyra tier failed: %s", exc)
            got = f"Pyra tier error ({type(exc).__name__})"
        if isinstance(got, pyra_source.PyraResult):
            return _from_pyra(lat, lng, got)
        pyra_note = f"Pyra chain not used: {got}"
    resp = await _cwfis_chain(lat, lng, now)
    if pyra_note:
        resp.message = f"{resp.message} · {pyra_note}"
    return resp


class HourlyBlock(BaseModel):
    """Hourly model weather, one entry per UTC hour (``null`` = no model run covers the hour)."""

    time: list[str]
    temperature_2m: list[float | None]
    relative_humidity_2m: list[float | None]
    wind_speed_10m: list[float | None]
    wind_direction_10m: list[float | None]
    precipitation: list[float | None]


class HourlyWeather(BaseModel):
    lat: float
    lng: float
    available: bool
    message: str
    # Hours are UTC hour stamps "YYYY-MM-DDTHH:00"; values are instantaneous at the stamp,
    # precipitation is the total of the hour ending at it (mm). Units: °C, %, km/h (10 m), deg.
    hourly: HourlyBlock | None = None
    # One character per hour: which model run supplied it (services/geomet.py SRC_LEGEND)
    src: str | None = None
    src_legend: dict[str, str] | None = None
    model_runs: dict[str, str | None] | None = None
    weather_model: str = geomet.MODEL_ID
    source: str = geomet.SOURCE_LABEL
    attribution: str = geomet.ATTRIBUTION
    licence_url: str = geomet.LICENCE_URL


def _parse_utc(value: str, name: str) -> datetime:
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}: not an ISO 8601 time") from exc
    if t.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"{name}: give a UTC offset (e.g. Z)")
    return t.astimezone(timezone.utc)


@router.get("/hourly", response_model=HourlyWeather)
async def get_hourly_weather(
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude")],
    lng: Annotated[float, Query(ge=-180, le=180, description="Longitude")],
    start: Annotated[str, Query(description="First hour, ISO 8601 with offset (floored to the UTC hour)")],
    end: Annotated[str, Query(description="Last hour, ISO 8601 with offset (inclusive, floored)")],
) -> HourlyWeather:
    """Hourly ECCC GEM weather at a point from MSC GeoMet (HRDPS → RDPS → GDPS; past hours from
    earlier runs' short leads). The frontend's hourly forecast / hindcast stream.

    Covers roughly 1.5-2 days before now (the model runs GeoMet keeps) to +240 h; other hours
    are null and the message says how many. At most ``geomet.MAX_HOURS`` hours per request.
    """
    t0, t1 = _parse_utc(start, "start"), _parse_utc(end, "end")
    if t1 < t0:
        raise HTTPException(status_code=422, detail="end is before start")
    if (t1 - t0) > timedelta(hours=geomet.MAX_HOURS - 1):
        raise HTTPException(status_code=422, detail=f"window longer than {geomet.MAX_HOURS} h")
    try:
        h = await geomet.hourly(lat, lng, t0, t1)
    except Exception as exc:
        logger.warning("GeoMet hourly failed: %s", exc)
        h = None
    if h is None:
        return HourlyWeather(lat=lat, lng=lng, available=False,
                             message="ECCC GeoMet unreachable: no model weather")
    n = len(h["time"])
    missing = sum(v is None for v in h["temperature_2m"])
    msg = f"{n - missing} of {n} h from {geomet.SOURCE_LABEL} ({geomet.src_summary(h['src'])})"
    if missing:
        msg += f"; {missing} h not covered by any model run GeoMet keeps"
    return HourlyWeather(
        lat=lat, lng=lng, available=missing < n, message=msg,
        hourly=HourlyBlock(**{k: h[k] for k in HourlyBlock.model_fields}),
        src=h["src"], src_legend=geomet.SRC_LEGEND, model_runs=h["model_runs"],
    )


def _from_pyra(lat: float, lng: float, r: "pyra_source.PyraResult") -> CurrentWeather:
    """Response from the "pyra" tier."""
    msg = f"FWI {r.fwi:.1f} — {_fwi_label(r.fwi)} · {r.codes_label}"
    if r.notes:
        msg += " · " + "; ".join(r.notes)
    logger.info(
        "Pyra tier '%s' %.1f km from (%.3f, %.3f): FWI=%.2f codes %s (%s, chain %s %s)",
        r.station_name, r.station_distance_km, lat, lng, r.fwi, r.codes_date, r.codes_status,
        r.chain_station_name, r.chain_date,
    )
    return CurrentWeather(
        lat=lat, lng=lng,
        ffmc=r.ffmc, dmc=r.dmc, dc=r.dc, isi=r.isi, bui=r.bui, fwi=r.fwi,
        wind_speed=r.wind_speed, wind_direction=r.wind_direction,
        temperature=r.temperature, relative_humidity=r.relative_humidity,
        source=r.source, available=True, message=msg,
        data_timestamp=r.data_timestamp, station_name=r.station_name,
        distance_km=r.station_distance_km,
        codes_date=r.codes_date, codes_status=r.codes_status, codes_label=r.codes_label,
        weather_model=r.weather_model, source_tier="pyra",
        pyra=PyraDetails(
            station_name=r.station_name, station_lat=r.station_lat, station_lng=r.station_lng,
            station_distance_km=r.station_distance_km, page_url=r.page_url,
            chain_source=r.chain_source, chain_station_name=r.chain_station_name,
            chain_station_id=r.chain_station_id, chain_distance_km=r.chain_distance_km,
            chain_date=r.chain_date, step=r.step, rain_24h=r.rain_24h,
            peak_wind_speed=r.peak_wind_speed, peak_isi=r.peak_isi, peak_fwi=r.peak_fwi,
            carry_over_generated=r.prev_generated, carry_over_url=r.prev_url,
            pyra_commit=pyra_source.PYRA_COMMIT, notes=r.notes,
        ),
    )


async def _cwfis_chain(lat: float, lng: float, now: datetime) -> CurrentWeather:
    """The CWFIS → archive → GEM cold-start chain (tiers 2 and 3)."""
    features: list[dict] = []
    archive = False
    cwfis_problem: str | None = None
    try:
        features = await _fetch_nearby_stations(lat, lng)
        if not features:
            features = await _fetch_archive_stations(lat, lng, now)
            archive = bool(features)
    except httpx.TimeoutException:
        logger.warning("CWFIS WFS timed out for (%.4f, %.4f)", lat, lng)
        cwfis_problem = "CWFIS request timed out"
    except Exception as exc:
        logger.warning("CWFIS fetch failed: %s", exc)
        cwfis_problem = "Could not reach CWFIS"
    if not features and cwfis_problem is None:
        cwfis_problem = "No fire weather stations found within range"

    coded = _nearest(lat, lng, features, require_codes=True) if features else None
    if coded is not None:
        return _from_station(lat, lng, coded[0], coded[1], now, archive, features)

    # No station with codes: weather from the nearest station (if any), codes estimated
    station, dist_km = _nearest(lat, lng, features) if features else (None, None)
    props = station["properties"] if station else {}
    prov = str(props.get("prov", "")).strip() or None
    estimated = await _estimate_fwi_from_gem(lat, lng, now, prov)

    temperature = _float(props.get("temp"), allow_negative=True)
    rh = _float(props.get("rh"))
    wind_speed = _float(props.get("ws"))
    wind_direction = _float(props.get("wdir"))
    station_name = _station_name(props) if station else None
    cwfis_tag = _cwfis_tag(station_name, prov, archive) if station else None

    if estimated is None or isinstance(estimated, str):
        reason = estimated if isinstance(estimated, str) else "GEM (GeoMet) unavailable"
        if station is None or (temperature is None and rh is None and wind_speed is None):
            return _unavailable(lat, lng, f"{cwfis_problem or 'No station data'}; {reason}")
        return CurrentWeather(
            lat=lat, lng=lng, ffmc=None, dmc=None, dc=None, isi=None, bui=None, fwi=None,
            wind_speed=wind_speed, wind_direction=wind_direction, temperature=temperature,
            relative_humidity=rh, source=cwfis_tag or "CWFIS / Natural Resources Canada",
            available=True,
            message=f"Weather loaded — FWI codes unavailable (off-season; no station with codes; {reason})",
            data_timestamp=_timestamp(props, now), station_name=station_name,
            distance_km=round(dist_km, 1) if dist_km is not None else None,
            source_tier="cwfis_archive" if archive else "cwfis",
        )

    result, noon = estimated
    if temperature is None:
        temperature = noon["temperature"]
    if rh is None:
        rh = noon["relative_humidity"]
    if wind_speed is None:
        wind_speed = noon["wind_speed"]
    if wind_direction is None:
        wind_direction = noon["wind_direction"]
    why = "no station with FWI codes within range (off-season)" if station else cwfis_problem
    label = (
        f"Cold-start estimate: one day from start-up codes 85/6/15 with {FORECAST_MODEL_LABEL} "
        f"noon LST weather of {noon['date']} ({noon['rain_24h']:.1f} mm in 24 h); DMC, DC and BUI "
        "are far too low except just after snowmelt — enter station codes if known"
    )
    source = (
        f"{cwfis_tag} (weather) + {FORECAST_MODEL_LABEL} (FWI cold-start estimate)"
        if cwfis_tag else f"{FORECAST_MODEL_LABEL} (FWI cold-start estimate; no CWFIS station data)"
    )
    return CurrentWeather(
        lat=lat, lng=lng,
        ffmc=result.ffmc, dmc=result.dmc, dc=result.dc,
        isi=result.isi, bui=result.bui, fwi=result.fwi,
        wind_speed=wind_speed, wind_direction=wind_direction,
        temperature=temperature, relative_humidity=rh,
        source=source, available=True,
        message=f"FWI {result.fwi:.1f} — {_fwi_label(result.fwi)} (estimated: {why}) · {label}",
        data_timestamp=_timestamp(props, now) if station else noon["time_utc"],
        station_name=station_name,
        distance_km=round(dist_km, 1) if dist_km is not None else None,
        codes_date=noon["date"], codes_status="estimate", codes_label=label,
        weather_model=FORECAST_MODEL, source_tier="gem_estimate",
    )


def _from_station(
    lat: float, lng: float, station: dict, dist_km: float, now: datetime, archive: bool,
    features: list[dict],
) -> CurrentWeather:
    """Response from a station that reports FWI codes."""
    props = station["properties"]
    fwi = _float(props.get("fwi"))
    station_name = _station_name(props)
    prov = str(props.get("prov", "")).strip() or None
    # the station's own noon hour (CWFIS tz_correct of that station)
    s_lat, s_lng = _float(props.get("lat"), allow_negative=True), _float(props.get("lon"), allow_negative=True)
    codes_date, status, label = _codes_age(
        props.get("rep_date"), now, prov, s_lng if s_lng is not None else lng,
        s_lat if s_lat is not None else lat)

    nearest_any, nearest_d = _nearest(lat, lng, features)
    skipped = ""
    if nearest_any is not station and nearest_d < dist_km - 0.05:
        n = sum(
            1 for f in features
            if not _has_codes(f["properties"]) and _dist(lat, lng, f["properties"]) < dist_km
        )
        skipped = f" · {n} nearer station{'s' if n != 1 else ''} without codes skipped"

    fwi_text = f"FWI {fwi:.1f} — {_fwi_label(fwi)}" if fwi is not None else "FWI codes loaded"
    msg = f"{fwi_text} · {label}" if label else fwi_text
    if archive:
        msg += " · CWFIS current layer empty (refreshing); codes from the archive layer"
    msg += skipped
    logger.info(
        "CWFIS station '%s' %.1f km away for (%.3f, %.3f): FWI=%s codes %s (%s)",
        station_name, dist_km, lat, lng, fwi, codes_date, status,
    )
    return CurrentWeather(
        lat=lat,
        lng=lng,
        ffmc=_float(props.get("ffmc")),
        dmc=_float(props.get("dmc")),
        dc=_float(props.get("dc")),
        isi=_float(props.get("isi")),
        bui=_float(props.get("bui")),
        fwi=fwi,
        wind_speed=_float(props.get("ws")),
        wind_direction=_float(props.get("wdir")),
        temperature=_float(props.get("temp"), allow_negative=True),
        relative_humidity=_float(props.get("rh")),
        source=_cwfis_tag(station_name, prov, archive),
        available=True,
        message=msg,
        data_timestamp=_timestamp(props, now),
        station_name=station_name,
        distance_km=round(dist_km, 1),
        codes_date=codes_date,
        codes_status=status,
        codes_label=label,
        source_tier="cwfis_archive" if archive else "cwfis",
    )


def _codes_age(
    rep_date: object, now: datetime, prov: str | None, lng: float, lat: float | None = None,
) -> tuple[str | None, str | None, str | None]:
    """(codes_date, status, label) for codes reported for ``rep_date``'s date (noon LST)."""
    if not rep_date:
        return None, None, None
    try:
        d = date.fromisoformat(str(rep_date)[:10])
    except ValueError:
        return None, None, None
    local = now + timedelta(hours=_std_offset_hours(prov, lng, at=now, lat=lat))
    age = (local.date() - d).days
    before_noon = local.hour < 12
    if age <= 0:
        return d.isoformat(), "today", f"Today's codes (noon LST {d.isoformat()})"
    if age == 1:
        when = "today's are computed after noon LST" if before_noon else "today's not yet published"
        return d.isoformat(), "yesterday", f"Yesterday's codes (as of noon LST {d.isoformat()}; {when})"
    return d.isoformat(), "older", f"Codes from noon LST {d.isoformat()} ({age} days old)"


def _std_offset_hours(
    prov: str | None, lng: float, tz_name: str | None = None, at: datetime | None = None,
    lat: float | None = None,
) -> float:
    """Standard-time UTC offset (hours) for noon LST: the nearest CWFIS station's ``tz_correct``
    in a province with more than one (needs ``lat``), else the province table, else the zone,
    else longitude.

    Alberta's fire-weather LST offset is dated (``alberta_time.fwi_lst_offset_at``; ``at`` = now
    when not given); before 2026-11-01 it is the table's -7.
    """
    if prov and prov.upper() == "AB":
        return -float(alberta_time.fwi_lst_offset_at(at or datetime.now(timezone.utc)))
    station = cwfis_tz.station_offset(prov, lat, lng)
    if station is not None:
        return float(station)
    if prov and prov.upper() in _STD_OFFSET_H:
        return _STD_OFFSET_H[prov.upper()]
    if tz_name:
        try:
            t = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(tz_name))
            off = t.utcoffset() or timedelta(0)
            dst = t.dst() or timedelta(0)
            return (off - dst).total_seconds() / 3600.0
        except Exception:  # unknown zone name
            pass
    return float(round(lng / 15.0))


def _station_name(props: dict) -> str | None:
    return str(props.get("name", "")).replace("+", " ").strip() or None


def _cwfis_tag(station_name: str | None, prov: str | None, archive: bool) -> str:
    tag = f"CWFIS — {station_name}" if station_name else "CWFIS / Natural Resources Canada"
    if prov:
        tag += f" ({prov})"
    if archive:
        tag += " [archive layer]"
    return tag


def _timestamp(props: dict, now: datetime) -> str:
    rep_date = props.get("rep_date")
    return str(rep_date) if rep_date else now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _noon_utc(now: datetime, prov: str | None, lng: float, lat: float | None) -> tuple[date, datetime]:
    """The latest noon LST at or before ``now``: (its date, its UTC time)."""
    if prov is None and lat is not None and pyra_source.in_alberta_box(lat, lng):
        # No CWFIS station to name the province: Alberta's box (Pyra's) gets Alberta's rule
        prov = "AB"
    off = _std_offset_hours(prov, lng, None, at=now, lat=lat)
    local = now + timedelta(hours=off)
    noon_date = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
    noon_utc = datetime(noon_date.year, noon_date.month, noon_date.day, 12, tzinfo=timezone.utc) - timedelta(hours=off)
    return noon_date, noon_utc


async def _fetch_gem_hourly(lat: float, lng: float, now: datetime, prov: str | None) -> dict | None:
    """GEM hourly weather (GeoMet) for the 24 h to the latest noon LST, in the unixtime shape
    ``_noon_weather`` reads. None when GeoMet cannot be reached."""
    _, noon_utc = _noon_utc(now, prov, lng, lat)
    noon_hour = noon_utc.replace(minute=0, second=0, microsecond=0)
    if noon_hour < noon_utc:      # noon LST at a half-hour offset (NL): the hour after
        noon_hour += timedelta(hours=1)
    try:
        h = await geomet.hourly(lat, lng, noon_hour - timedelta(hours=23), noon_hour)
    except Exception as exc:
        logger.warning("GeoMet fetch failed: %s", exc)
        return None
    if h is None:
        return None
    times = [int(datetime.fromisoformat(t + ":00+00:00").timestamp()) for t in h["time"]]
    return {"timezone": None, "src": h["src"], "hourly": {**h, "time": times}}


def _noon_weather(
    data: dict, now: datetime, prov: str | None, lng: float, lat: float | None = None,
) -> dict | str:
    """Weather at the latest noon LST at or before ``now`` and the 24 h rain to it.

    Hourly precipitation is the total over the preceding hour, so the 24 h rain is the
    sum of the 24 records stamped noon - 23 h to noon, i.e. (noon - 24 h, noon] (noon to noon, the
    FWI System's rain period).
    Returns a reason string if GEM has no value for a needed variable.
    """
    h = data.get("hourly") or {}
    times = h.get("time") or []
    if not times:
        return f"{FORECAST_MODEL_LABEL} returned no hours"
    noon_date, noon_utc = _noon_utc(now, prov, lng, lat)
    t_noon = int(noon_utc.timestamp())
    # Hours are on the hour in UTC; noon LST at a half-hour offset (NL) uses the hour after
    idx = next((i for i, t in enumerate(times) if int(t) >= t_noon), None)
    if idx is None or idx < 23 or int(times[idx]) - t_noon >= 3600:
        return f"{FORECAST_MODEL_LABEL} does not cover noon LST {noon_date.isoformat()}"
    names = {
        "temperature": "temperature_2m", "relative_humidity": "relative_humidity_2m",
        "wind_speed": "wind_speed_10m", "wind_direction": "wind_direction_10m",
    }
    out: dict = {}
    for key, var in names.items():
        val = (h.get(var) or [None] * len(times))[idx]
        if val is None:
            return f"{FORECAST_MODEL_LABEL} has no {var} for noon LST {noon_date.isoformat()}"
        out[key] = float(val)
    rain = (h.get("precipitation") or [None] * len(times))[idx - 23: idx + 1]
    if any(v is None for v in rain):
        n_missing = sum(v is None for v in rain)
        return (f"{FORECAST_MODEL_LABEL} has no precipitation for {n_missing} of the 24 h to noon LST "
                f"{noon_date.isoformat()} (GeoMet keeps about 1.5-2 days of model runs)")
    out["rain_24h"] = float(sum(rain))
    out["date"] = noon_date.isoformat()
    out["month"] = noon_date.month
    out["time_utc"] = datetime.fromtimestamp(int(times[idx]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


async def _estimate_fwi_from_gem(
    lat: float, lng: float, now: datetime, prov: str | None,
) -> tuple | str | None:
    """Cold-start FWI estimate from the GEM noon-LST weather and 24 h rain.

    Returns (FWIResult, noon weather dict), a reason string when GEM lacks a value, or None when
    GeoMet cannot be reached. Start-up codes 85 / 6 / 15 (Van Wagner 1987; Van Wagner &
    Pickett 1985); one day only.
    """
    data = await _fetch_gem_hourly(lat, lng, now, prov)
    if not data:
        return None
    noon = _noon_weather(data, now, prov, lng, lat)
    if isinstance(noon, str):
        logger.warning("GEM estimate not possible: %s", noon)
        return noon
    try:
        calc = FWICalculator(*_START_UP)
        result = calc.calculate_daily(
            temp=noon["temperature"],
            rh=min(100.0, max(0.0, noon["relative_humidity"])),
            wind=max(0.0, noon["wind_speed"]),
            rain=max(0.0, noon["rain_24h"]),
            month=noon["month"],
        )
    except Exception as exc:
        logger.warning("FWI calculation failed: %s", exc)
        return None
    logger.info(
        "GEM noon-LST FWI estimate for (%.3f, %.3f) on %s: FWI=%.1f (cold-start)",
        lat, lng, noon["date"], result.fwi,
    )
    return result, noon


def _bbox_cql(lat: float, lng: float) -> str:
    return (
        f"lat BETWEEN {lat - _BBOX_DEG} AND {lat + _BBOX_DEG} "
        f"AND lon BETWEEN {lng - _BBOX_DEG} AND {lng + _BBOX_DEG}"
    )


async def _wfs_get(type_name: str, cql: str, count: int) -> list[dict]:
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": type_name,
        "outputFormat": "application/json",
        "count": str(count),
        "CQL_FILTER": cql,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        resp = await client.get(_WFS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    return data.get("features", [])


async def _fetch_nearby_stations(lat: float, lng: float) -> list[dict]:
    """Query the CWFIS current layer for fire weather stations within ±BBOX_DEG of point."""
    return await _wfs_get(_LAYER, _bbox_cql(lat, lng), _MAX_FEATURES)


async def _fetch_archive_stations(lat: float, lng: float, now: datetime) -> list[dict]:
    """Newest archive reports (at most ``_ARCHIVE_MAX_AGE_DAYS`` old) near the point.

    Used when the current layer is empty (it is refreshed around 19 UTC). Returns the features of
    the newest report date only, so every station's codes are from the same day.
    """
    since = (now.date() - timedelta(days=_ARCHIVE_MAX_AGE_DAYS)).isoformat()
    cql = f"{_bbox_cql(lat, lng)} AND rep_date >= '{since}T00:00:00Z'"
    feats = await _wfs_get(_ARCHIVE_LAYER, cql, _MAX_FEATURES * (_ARCHIVE_MAX_AGE_DAYS + 1))
    dates = [str(f["properties"].get("rep_date") or "")[:10] for f in feats]
    if not any(dates):
        return []
    newest = max(dates)
    return [f for f, d in zip(feats, dates) if d == newest]


def _has_codes(props: dict) -> bool:
    return all(_float(props.get(k)) is not None for k in ("ffmc", "dmc", "dc"))


def _dist(lat: float, lng: float, props: dict) -> float:
    try:
        return _haversine_km(lat, lng, float(props["lat"]), float(props["lon"]))
    except (KeyError, TypeError, ValueError):
        return float("inf")


def _nearest(
    lat: float, lng: float, features: list[dict], require_codes: bool = False,
) -> tuple[dict, float] | None:
    """(feature, distance_km) of the closest station; with ``require_codes`` only stations that
    report FFMC, DMC and DC count, and None is returned if there is none."""
    best: dict | None = None
    best_d = float("inf")
    for feat in features:
        props = feat["properties"]
        if require_codes and not _has_codes(props):
            continue
        d = _dist(lat, lng, props)
        if d < best_d:
            best_d = d
            best = feat
    if best is None or best_d == float("inf"):
        if require_codes:
            return None
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


def _float(val: object, allow_negative: bool = False) -> float | None:
    """Parse a numeric property; negatives are invalid except where allowed (temperature)."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f if (allow_negative or f >= 0) else None


def _fwi_label(fwi: float | None) -> str:
    if fwi is None:
        return "Unknown"
    return fwi_class(fwi)
