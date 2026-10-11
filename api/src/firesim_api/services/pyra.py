"""Starting FWI codes from Pyra's chain (the "pyra" source tier of ``/api/v1/weather/current``).

Pyra (https://tphambolio.github.io/FWI/, repo ``Tphambolio/FWI``) is the team's fire-weather app.
This module reproduces what Pyra's Alberta station page shows for "today" (``initFWI`` in Pyra's
``core/fwi-core.js`` with the Alberta tier chain ``_fetchWeatherPrimaryAB`` in ``fwi.js``, Pyra
commit ``PYRA_COMMIT``), so that the fire-weather product and the spread prediction start from the
same FFMC / DMC / DC. FireSim does not run Pyra's code: it reads the same public inputs and applies
the same rules with its own FWI calculator (identical equations, coefficient 147.2; parity is
checked in ``api/tests/test_pyra_source.py``).

What Pyra publishes (read only):

- ``data/cwfis_prev.json`` in the Pyra repository, written daily at 20:15 UTC by the GitHub Action
  ``.github/workflows/cwfis-daily.yml``: one CWFIS ``firewx_stns_current`` snapshot for every
  Alberta station with an FWI chain (``stations``) and every BC station (``bcStations``), keyed by
  the exact CWFIS station name, each ``{ffmc, dmc, dc, isi, bui, fwi, lat, lon, repDate}``, plus
  ``generated`` (UTC ISO time). Pyra itself reads it from raw.githubusercontent.com; it is also on
  GitHub Pages. Pyra ignores it when ``generated`` is more than 48 h old.
- Nothing else that holds codes: today's codes are computed in the browser.

What Pyra shows for a station today (Alberta, default single-station mode, not the IDW blend):

1. The station is the nearest one in Pyra's list (``pyra_stations.ALBERTA_STATIONS``) to the point
   (pin-drop / geolocation ``selectNearest``).
2. CWFIS ``firewx_stns_current`` is queried within ±2° of that station and the nearest station
   *with* FFMC/DC is chosen unless a weather-only station is more than 200 km nearer
   (``_selectCWFIS``). Its DC passes the spring cold-start floor (``applyDCFloor``).
3. Before noon LST (MST, UTC-7): the CWFIS codes (normally yesterday's) become the carry-over
   (Pyra's browser "holding cache"); the carry-over is the newer of that and the station's entry in
   ``cwfis_prev.json`` (by name, else the nearest entry within 10 km), at most 2 days old. It is
   stepped one day with the GEM noon-LST forecast for today (Open-Meteo ``models=gem_seamless``,
   noon LST temperature, RH, 10 m wind and the 24 h rain to noon) — Van Wagner (1987), PDF p. 13.
4. After noon LST: if CWFIS returns a station with codes, they are shown as published (whatever
   their date); if it returns only weather-only stations, the carry-over is stepped with that
   station's observation; if it returns nothing, Pyra tries MSC SWOB and then the GEM noon-LST
   value. FireSim does not query SWOB (it goes straight to GEM; see ``docs/PROJECT_RECORD.md``).
5. The ISI/BUI/FWI in the components strip are the daily values (noon wind). The "peak burn"
   ISI/FWI use the 16:00 MDT wind with the same codes; they are returned separately.

If any step has no data (outside Alberta, no carry-over within 2 days, the GEM request fails),
this tier returns a reason and the endpoint falls back to its CWFIS → archive → GEM chain.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from firesim.fwi.calculator import FWICalculator

from .pyra_stations import ALBERTA_STATIONS, PYRA_COMMIT

logger = logging.getLogger(__name__)

# ── Pyra's published carry-over file ──────────────────────────────────────────
# Pyra reads the raw URL (core/fwi-core.js loadCWFISPrev); GitHub Pages serves the same commit.
PREV_URLS = (
    "https://raw.githubusercontent.com/Tphambolio/FWI/main/data/cwfis_prev.json",
    "https://tphambolio.github.io/FWI/data/cwfis_prev.json",
)
# The file changes once a day (20:15 UTC Action, sometimes late); raw.githubusercontent.com caches
# for about 5 min. 15 min keeps FireSim at most one CDN period behind a fresh browser.
PREV_TTL_S = 15 * 60
# After a failed fetch, wait this long before trying again (a still-valid copy keeps being used).
PREV_RETRY_S = 60
# Pyra rejects a file whose ``generated`` is older than this (loadCWFISPrev).
PREV_MAX_AGE_H = 48
PREV_TIMEOUT_S = 10.0

PYRA_SITE = "https://tphambolio.github.io/FWI/"

# ── Alberta constants (Pyra fwi.js PROVINCE) ───────────────────────────────────
LST_OFFSET_H = 7          # noon LST = 19 UTC (MST)
NOON_UTC = 19
PEAK_UTC = 22             # 16:00 MDT, Pyra's peak-burn hour (wind only)
AB_BBOX = (48.8, 60.5, -120.5, -109.5)   # PROVINCE.cwfisBBox [latMin, latMax, lonMin, lonMax]
CWFIS_URL = "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"
CWFIS_BBOX_DEG = 2.0
CWFIS_COUNT = 50
CWFIS_TIMEOUT_S = 10.0
WX_ONLY_MARGIN_KM = 200   # chain station kept unless a weather-only station is > 200 km nearer
PREV_NEAR_KM = 10         # cwfis_prev coordinate fallback radius
CARRY_MAX_DAYS = 2
DC_COLDSTART_CEILING = 60

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
GEM_MODEL = "gem_seamless"
GEM_TIMEOUT_S = 12.0
_LOCAL_TZ = ZoneInfo("America/Edmonton")


# ── Cache of the carry-over file ───────────────────────────────────────────────
@dataclass
class _PrevCache:
    data: dict | None = None
    fetched_mono: float = 0.0
    failed_mono: float | None = None
    url: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_cache = _PrevCache()


def reset_cache() -> None:
    """Forget the cached carry-over file (tests)."""
    global _cache
    _cache = _PrevCache()


def _generated_age_h(data: dict, now: datetime) -> float | None:
    try:
        gen = datetime.fromisoformat(str(data["generated"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return (now - gen).total_seconds() / 3600.0


async def load_prev(now: datetime) -> tuple[dict | None, str | None]:
    """Pyra's ``cwfis_prev.json`` (cached), or (None, reason).

    A copy is reused for ``PREV_TTL_S``; when a refresh fails the last good copy is kept as long as
    it is not too old (Pyra's 48 h rule), and the next attempt waits ``PREV_RETRY_S``.
    """
    c = _cache
    async with c.lock:
        mono = time.monotonic()
        fresh = c.data is not None and mono - c.fetched_mono < PREV_TTL_S
        backoff = c.failed_mono is not None and mono - c.failed_mono < PREV_RETRY_S
        if not fresh and not backoff:
            got = None
            for url in PREV_URLS:
                try:
                    async with httpx.AsyncClient(timeout=PREV_TIMEOUT_S) as client:
                        r = await client.get(url)
                        r.raise_for_status()
                        js = r.json()
                    if isinstance(js, dict) and isinstance(js.get("stations"), dict):
                        got = (js, url)
                        break
                    logger.warning("Pyra carry-over file at %s has no 'stations' section", url)
                except Exception as exc:  # network, HTTP or JSON error: try the next URL
                    logger.warning("Pyra carry-over fetch failed (%s): %s", url, exc)
            if got:
                c.data, c.url = got
                c.fetched_mono, c.failed_mono = mono, None
            else:
                c.failed_mono = mono
        data = c.data
    if data is None:
        return None, "Pyra's carry-over file could not be fetched"
    age = _generated_age_h(data, now)
    if age is None or age > PREV_MAX_AGE_H:
        return None, f"Pyra's carry-over file is stale (generated {data.get('generated')})"
    return data, None


def prev_source_url() -> str | None:
    return _cache.url


# ── Station selection ──────────────────────────────────────────────────────────
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Pyra's ``_haversineKm`` (R = 6371 km)."""
    r = 6371.0
    dlat = (lat2 - lat1) * math.pi / 180
    dlon = (lon2 - lon1) * math.pi / 180
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1 * math.pi / 180) * math.cos(lat2 * math.pi / 180) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def in_alberta_box(lat: float, lng: float) -> bool:
    la0, la1, lo0, lo1 = AB_BBOX
    return la0 <= lat <= la1 and lo0 <= lng <= lo1


def nearest_pyra_station(lat: float, lng: float) -> tuple[tuple[str, float, float], float]:
    """Nearest station in Pyra's list (first of equally near ones, as Pyra's ``selectNearest``)."""
    best, best_d = ALBERTA_STATIONS[0], math.inf
    for s in ALBERTA_STATIONS:
        d = haversine_km(lat, lng, s[1], s[2])
        if d < best_d:
            best, best_d = s, d
    return best, best_d


def regional_dc_floor(lat: float, lon: float, now: datetime) -> float:
    """Pyra ``getRegionalDCFloor``: spring (March-June, MST month) DC floor by Alberta region.

    A Pyra heuristic ("calibrated against CWFIS April 2026"), not a published method; FireSim
    applies it only to reproduce Pyra's numbers.
    """
    mo = (now - timedelta(hours=LST_OFFSET_H)).month
    if mo < 3 or mo > 6:
        return 0.0
    if lat < 48.8 or lat > 60.5 or lon < -120.5 or lon > -109.5:
        return 0.0
    if lat < 50.5 and lon > -113.5:
        return 450.0
    if lat < 50.5:
        return 300.0
    if lat < 51.5 and lon > -112.5:
        return 360.0
    if lat < 51.5:
        return 280.0
    if lat < 52.5:
        return 290.0
    if lat < 54.0:
        return 300.0
    if lat < 56.5:
        return 180.0
    return 120.0


def apply_dc_floor(dc: float | None, lat: float, lon: float, now: datetime) -> tuple[float | None, bool]:
    """Pyra ``applyDCFloor``: raise a spring cold-start DC (≤ 60) to the regional floor."""
    if dc is None:
        return dc, False
    floor = regional_dc_floor(lat, lon, now)
    if floor > 0 and dc <= DC_COLDSTART_CEILING and dc < floor:
        return floor, True
    return dc, False


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def clean_name(raw: object) -> str:
    return " ".join(str(raw or "").replace("+", " ").split())


@dataclass
class CwfisPick:
    """Pyra ``_selectCWFIS`` result for one point."""

    props: dict
    feature_id: str | None
    name: str
    dist_km: float
    has_codes: bool
    ffmc: float | None
    dmc: float | None
    dc: float | None
    isi: float | None
    bui: float | None
    fwi: float | None
    rep_date: str | None
    dc_floored: bool


def select_cwfis(features: list[dict], lat: float, lng: float, now: datetime) -> CwfisPick | None:
    """Pyra ``_selectCWFIS``: nearest station with FFMC and DC, unless a weather-only station is
    more than 200 km nearer; stations without temp, RH or wind are ignored."""
    fwi_near, fwi_d = None, math.inf
    wx_near, wx_d = None, math.inf
    for feat in features or []:
        p = feat.get("properties") or {}
        if p.get("temp") is None or p.get("rh") is None or p.get("ws") is None:
            continue
        try:
            d = haversine_km(lat, lng, float(p["lat"]), float(p["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d < wx_d:
            wx_d, wx_near = d, feat
        if p.get("ffmc") is not None and p.get("dc") is not None and d < fwi_d:
            fwi_d, fwi_near = d, feat
    nearest = fwi_near if (fwi_near is not None and fwi_d <= wx_d + WX_ONLY_MARGIN_KM) else wx_near
    if nearest is None:
        return None
    p = nearest["properties"]
    has = p.get("ffmc") is not None and p.get("dmc") is not None and p.get("dc") is not None
    dc, floored = (None, False)
    if has:
        dc, floored = apply_dc_floor(float(p["dc"]), float(p["lat"]), float(p["lon"]), now)
    bui = _num(p.get("bui")) if has else None
    fwi = _num(p.get("fwi")) if has else None
    if floored and p.get("isi") is not None:
        bui = FWICalculator.calculate_bui(float(p["dmc"]), dc)
        fwi = FWICalculator.calculate_fwi(float(p["isi"]), bui)
    return CwfisPick(
        props=p, feature_id=str(nearest.get("id")) if nearest.get("id") is not None else None,
        name=clean_name(p.get("name")), dist_km=fwi_d if nearest is fwi_near else wx_d,
        has_codes=has,
        ffmc=_num(p.get("ffmc")) if has else None, dmc=_num(p.get("dmc")) if has else None, dc=dc,
        isi=_num(p.get("isi")) if has else None, bui=bui, fwi=fwi,
        rep_date=str(p["rep_date"]) if p.get("rep_date") else None, dc_floored=floored,
    )


async def fetch_cwfis(lat: float, lng: float) -> list[dict] | None:
    """CWFIS ``firewx_stns_current`` within ±2° of the point (Pyra ``fetchCWFIS``); None on failure."""
    cql = (f"lat BETWEEN {lat - CWFIS_BBOX_DEG} AND {lat + CWFIS_BBOX_DEG} "
           f"AND lon BETWEEN {lng - CWFIS_BBOX_DEG} AND {lng + CWFIS_BBOX_DEG}")
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": "public:firewx_stns_current", "outputFormat": "application/json",
        "count": str(CWFIS_COUNT), "CQL_FILTER": cql,
    }
    try:
        async with httpx.AsyncClient(timeout=CWFIS_TIMEOUT_S) as client:
            r = await client.get(CWFIS_URL, params=params)
            r.raise_for_status()
            return list(r.json().get("features") or [])
    except Exception as exc:
        logger.warning("Pyra tier: CWFIS fetch failed: %s", exc)
        return None


def prev_for(section: dict, name: str, lat: float, lng: float) -> tuple[str, dict] | None:
    """Pyra ``_cwfisPrevFor``: case-insensitive name match, else nearest entry within 10 km."""
    upper = {k.upper(): (k, v) for k, v in section.items()}
    hit = upper.get((name or "").upper())
    if hit:
        return hit
    best, best_d = None, float(PREV_NEAR_KM)
    for k, v in section.items():
        if not isinstance(v, dict) or v.get("lat") is None or v.get("lon") is None:
            continue
        d = haversine_km(lat, lng, float(v["lat"]), float(v["lon"]))
        if d < best_d:
            best, best_d = (k, v), d
    return best


# ── GEM noon-LST weather (Pyra fetchWeather) ───────────────────────────────────
def lst_date(now: datetime) -> date:
    return (now - timedelta(hours=LST_OFFSET_H)).date()


def is_pre_noon(now: datetime) -> bool:
    return (now - timedelta(hours=LST_OFFSET_H)).hour < 12


def _js_num(x: float) -> str:
    """A coordinate as JavaScript prints it in a template string (Pyra builds its URL that way)."""
    s = repr(float(x))
    return s[:-2] if s.endswith(".0") else s


def gem_url(lat: float, lng: float) -> str:
    """The exact Open-Meteo URL Pyra's ``fetchWeather`` requests for a station."""
    return (
        f"{OPEN_METEO_URL}?latitude={_js_num(lat)}&longitude={_js_num(lng)}"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,"
        "thunderstorm_probability&past_days=1&forecast_days=2&timezone=UTC&models=" + GEM_MODEL
    )


async def fetch_gem(lat: float, lng: float) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=GEM_TIMEOUT_S) as client:
            r = await client.get(gem_url(lat, lng))
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        logger.warning("Pyra tier: Open-Meteo GEM fetch failed: %s", exc)
        return None


def gem_noon(data: dict, now: datetime) -> dict | str:
    """Pyra ``fetchWeather``: today's noon-LST hour (19 UTC), its 24 h rain, and the 16:00 MDT wind."""
    h = data.get("hourly") or {}
    times = list(h.get("time") or [])
    if not times:
        return "GEM returned no hours"
    d = lst_date(now)

    def iso(hour_utc: int) -> str:
        t = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=hour_utc)
        return t.strftime("%Y-%m-%dT%H:00")

    i = times.index(iso(NOON_UTC)) if iso(NOON_UTC) in times else len(times) - 1
    ip = times.index(iso(PEAK_UTC)) if iso(PEAK_UTC) in times else -1

    def col(name: str) -> list:
        return list(h.get(name) or [None] * len(times))

    temp, rh, ws, wd, pr = (col(n) for n in (
        "temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
        "precipitation"))
    if temp[i] is None or rh[i] is None or ws[i] is None:
        return f"GEM has no noon-LST value for {d.isoformat()}"
    rain = sum((v or 0.0) for v in pr[max(0, i - 23): i + 1])
    peak = None
    if ip >= 0 and ws[ip] is not None:
        peak = {"temperature": temp[ip], "relative_humidity": rh[ip], "wind_speed": ws[ip],
                "wind_direction": wd[ip]}
    return {
        "temperature": float(temp[i]), "relative_humidity": float(rh[i]),
        "wind_speed": float(ws[i]), "wind_direction": _num(wd[i]),
        "rain_24h": float(rain), "time_utc": times[i] + ":00Z", "date": d.isoformat(),
        "peak": peak,
    }


# ── The tier ───────────────────────────────────────────────────────────────────
@dataclass
class PyraResult:
    """Today's codes as Pyra shows them for one station."""

    station_name: str
    station_lat: float
    station_lng: float
    station_distance_km: float
    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float
    temperature: float | None
    relative_humidity: float | None
    wind_speed: float | None
    wind_direction: float | None
    rain_24h: float | None
    codes_date: str
    codes_status: str
    codes_label: str
    source: str
    step: str                     # none | gem_noon_forecast | gem_noon | cwfis_obs
    chain_source: str             # cwfis_live | pyra_cwfis_prev
    chain_station_name: str | None
    chain_station_id: str | None
    chain_distance_km: float | None
    chain_date: str | None
    weather_model: str | None
    data_timestamp: str | None
    peak_wind_speed: float | None = None
    peak_isi: float | None = None
    peak_fwi: float | None = None
    prev_generated: str | None = None
    prev_url: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def page_url(self) -> str:
        return f"{PYRA_SITE}station_detail/code.html?stn={quote(self.station_name)}"


def _fmt_day(d: str) -> str:
    """"Oct 9" (Pyra's en-CA short date)."""
    x = date.fromisoformat(d[:10])
    return f"{x:%b} {x.day}"


def _daily(ffmc: float, dmc: float, dc: float, wind: float) -> tuple[float, float, float]:
    isi = FWICalculator.calculate_isi(ffmc, wind)
    bui = FWICalculator.calculate_bui(dmc, dc)
    return isi, bui, FWICalculator.calculate_fwi(isi, bui)


def _step(prev: tuple[float, float, float], wx: dict, month: int):
    """Pyra ``calculateFWI`` (non-CWFIS branch): clamp RH to 0-100, wind and rain ≥ 0, one day."""
    rh = min(100.0, max(0.0, wx["relative_humidity"]))
    wind = max(0.0, wx["wind_speed"])
    rain = max(0.0, wx.get("rain_24h") or 0.0)
    return FWICalculator(*prev).calculate_daily(temp=wx["temperature"], rh=rh, wind=wind, rain=rain,
                                                month=month)


async def pyra_today(lat: float, lng: float, now: datetime) -> PyraResult | str:
    """Today's codes for the point as Pyra's Alberta station page shows them, or a reason string."""
    if not in_alberta_box(lat, lng):
        return "outside Pyra's Alberta coverage"
    (s_name, s_lat, s_lng), s_dist = nearest_pyra_station(lat, lng)
    pre_noon = is_pre_noon(now)
    today = lst_date(now)
    month = now.astimezone(_LOCAL_TZ).month   # Pyra: the browser's local month (Alberta viewer)

    features, (prev, prev_reason) = await asyncio.gather(fetch_cwfis(s_lat, s_lng), load_prev(now))
    pick = select_cwfis(features, s_lat, s_lng, now) if features else None
    base = dict(station_name=s_name, station_lat=s_lat, station_lng=s_lng,
                station_distance_km=round(s_dist, 1),
                prev_generated=(prev or {}).get("generated"), prev_url=prev_source_url())
    tag = f"Pyra · {s_name}"

    # After noon LST a CWFIS station with codes is shown as published (Pyra post-noon tier).
    if not pre_noon and pick is not None and pick.has_codes:
        isi = pick.isi if pick.isi is not None else FWICalculator.calculate_isi(pick.ffmc, _num(pick.props.get("ws")) or 0.0)
        bui = pick.bui if pick.bui is not None else FWICalculator.calculate_bui(pick.dmc or 0.0, pick.dc or 0.0)
        fwi = pick.fwi if pick.fwi is not None else FWICalculator.calculate_fwi(isi, bui)
        cd = (pick.rep_date or today.isoformat())[:10]
        is_today = cd == today.isoformat()
        age = (today - date.fromisoformat(cd)).days
        status = "today" if is_today else ("yesterday" if age == 1 else "older")
        label = ("Noon LST · today" if is_today
                 else f"Noon LST · {_fmt_day(cd)} (not today) — CWFIS has not published today's codes yet")
        return PyraResult(
            **base, ffmc=pick.ffmc, dmc=pick.dmc, dc=pick.dc, isi=isi, bui=bui, fwi=fwi,
            temperature=_num(pick.props.get("temp")), relative_humidity=_num(pick.props.get("rh")),
            wind_speed=_num(pick.props.get("ws")), wind_direction=_num(pick.props.get("wdir")),
            rain_24h=_num(pick.props.get("precip")),
            codes_date=cd, codes_status=status, codes_label=f"Pyra: {label} · CWFIS {pick.name}",
            source=f"{tag} · as of noon LST {cd} (CWFIS {pick.name}, {round(pick.dist_km)} km)",
            step="none", chain_source="cwfis_live", chain_station_name=pick.name,
            chain_station_id=pick.feature_id, chain_distance_km=round(pick.dist_km, 1), chain_date=cd,
            weather_model=None, data_timestamp=pick.rep_date,
        )

    # Otherwise step the newest carry-over (≤ 2 days) once with today's noon weather.
    cands: list[dict] = []
    if pick is not None and pick.has_codes and pick.rep_date:      # Pyra's holding cache
        cands.append(dict(ffmc=pick.ffmc, dmc=pick.dmc, dc=pick.dc, rep=pick.rep_date,
                          name=pick.name, id=pick.feature_id, dist=pick.dist_km, src="cwfis_live"))
    if prev is not None:
        hit = prev_for(prev["stations"], s_name, s_lat, s_lng)
        if hit:
            k, v = hit
            if all(v.get(x) is not None for x in ("ffmc", "dmc", "dc")) and v.get("repDate"):
                dist = (haversine_km(s_lat, s_lng, float(v["lat"]), float(v["lon"]))
                        if v.get("lat") is not None else None)
                cands.append(dict(ffmc=float(v["ffmc"]), dmc=float(v["dmc"]), dc=float(v["dc"]),
                                  rep=str(v["repDate"]), name=k, id=None, dist=dist,
                                  src="pyra_cwfis_prev"))
    best = None
    for c in cands:
        c["obs"] = c["rep"][:10]
        if best is None or c["obs"] > best["obs"]:
            best = c
    if best is None:
        why = [r for r in (prev_reason, None if features else "CWFIS returned no stations") if r]
        return "no Pyra carry-over for " + s_name + (f" ({'; '.join(why)})" if why else "")
    age_days = (today - date.fromisoformat(best["obs"])).days
    if not 0 <= age_days <= CARRY_MAX_DAYS:
        return f"Pyra carry-over for {s_name} is from {best['obs']} (more than {CARRY_MAX_DAYS} days old)"
    dc0, _ = apply_dc_floor(best["dc"], s_lat, s_lng, now)
    chain_name = best["name"]

    # Today's weather: pre-noon the GEM noon-LST forecast; after noon a weather-only CWFIS
    # station's observation if CWFIS returned one, else GEM's noon-LST value (Pyra: SWOB first).
    wx: dict | None = None
    step, model = "gem_noon_forecast" if pre_noon else "gem_noon", GEM_MODEL
    notes: list[str] = []
    if not pre_noon and pick is not None and not pick.has_codes:
        p = pick.props
        wx = {"temperature": float(p["temp"]), "relative_humidity": float(p["rh"]),
              "wind_speed": float(p["ws"]), "wind_direction": _num(p.get("wdir")),
              "rain_24h": _num(p.get("precip")) or 0.0,
              "time_utc": pick.rep_date, "date": today.isoformat(), "peak": None}
        step, model = "cwfis_obs", None
    else:
        gem = await fetch_gem(s_lat, s_lng)
        if gem is None:
            return "Open-Meteo GEM forecast unavailable"
        got = gem_noon(gem, now)
        if isinstance(got, str):
            return got
        wx = got
        if not pre_noon:
            notes.append("after noon with CWFIS empty Pyra tries MSC SWOB before GEM; FireSim uses GEM")

    if age_days == 0:           # the carry-over already holds today's noon codes
        ffmc, dmc, dc = best["ffmc"], best["dmc"], dc0
        isi, bui, fwi = _daily(ffmc, dmc, dc, wx["wind_speed"] if wx["wind_speed"] is not None else 0.0)
        step = "none"
        status, label = "today", f"Noon LST · today (carry-over {chain_name})"
    else:
        r = _step((best["ffmc"], best["dmc"], dc0), wx, month)
        ffmc, dmc, dc, isi, bui, fwi = r.ffmc, r.dmc, r.dc, r.isi, r.bui, r.fwi
        t = _fmt_day(today.isoformat())
        if age_days == 2:
            notes.append("carry-over is 2 days old; Pyra steps it one day only")
        if step == "gem_noon_forecast":
            status = "forecast"
            label = (f"Noon LST Forecast · today (pre-noon) — CFFDRS daily FWI · noon LST {t} · stepped "
                     f"with the noon-LST model forecast (GEM) from the {_fmt_day(best['obs'])} chain "
                     f"({chain_name})")
        else:
            status = "stepped"
            with_what = "GEM noon-LST weather" if step == "gem_noon" else f"the CWFIS {pick.name} observation"
            label = (f"CFFDRS daily FWI · noon LST {t} · carried from the {_fmt_day(best['obs'])} chain "
                     f"({chain_name}), stepped with {with_what}")
    peak = wx.get("peak")
    peak_isi = peak_fwi = peak_w = None
    if peak is not None and peak.get("wind_speed") is not None:
        peak_w = max(0.0, float(peak["wind_speed"]))
        peak_isi, _, peak_fwi = _daily(ffmc, dmc, dc, peak_w)
    when = "noon LST forecast" if status == "forecast" else "noon LST"
    return PyraResult(
        **base, ffmc=ffmc, dmc=dmc, dc=dc, isi=isi, bui=bui, fwi=fwi,
        temperature=wx["temperature"], relative_humidity=wx["relative_humidity"],
        wind_speed=wx["wind_speed"], wind_direction=wx.get("wind_direction"),
        rain_24h=wx.get("rain_24h"),
        codes_date=today.isoformat(), codes_status=status, codes_label=f"Pyra: {label}",
        source=f"{tag} · as of {when} {today.isoformat()} (chain {chain_name} {best['obs']})",
        step=step, chain_source=best["src"], chain_station_name=chain_name,
        chain_station_id=best["id"],
        chain_distance_km=round(best["dist"], 1) if best["dist"] is not None else None,
        chain_date=best["obs"], weather_model=model, data_timestamp=wx.get("time_utc"),
        peak_wind_speed=peak_w, peak_isi=peak_isi, peak_fwi=peak_fwi, notes=notes,
    )


__all__ = ["PYRA_COMMIT", "PyraResult", "pyra_today", "reset_cache", "load_prev"]
