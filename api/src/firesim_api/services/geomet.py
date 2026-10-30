"""Hourly ECCC GEM model weather at a point, read from MSC GeoMet (WCS 2.0.1).

This is FireSim's only model-weather source (docs/weather-geomet.md). It replaced the free
Open-Meteo forecast API (``models=gem_seamless``, non-commercial terms) on 2026-10-30; the models
are the same family (ECCC's GEM: HRDPS 2.5 km, RDPS 10 km, GDPS 15 km), now read from Environment
and Climate Change Canada directly under the Open Government Licence – Canada.

Source: https://geo.weather.gc.ca/geomet, WCS 2.0.1 ``GetCoverage``; one request per
(layer, model run, hour) for a 0.02° box around the point, resampled by GeoMet to a single cell
(``SCALESIZE=x(1),y(1)``, nearest neighbour), returned as ESRI ASCII grid text, so no raster
library is needed. Layers and units are those Pyra uses (``Tphambolio/FWI tools/gem/lib.mjs``,
verified 2026-10-29) and were re-checked live on 2026-10-30:

=========  ==========================================  =======================================
Model      Layers (temp, rh, wind, wdir, rain)         Units as served
=========  ==========================================  =======================================
HRDPS      ``HRDPS.CONTINENTAL_{TT,HR,WSPD,WD,PR}``    °C, %, m/s, deg (from), mm since run start
RDPS       ``RDPS_10km_{AirTemp_2m,RelativeHumidity_   °C, %, m/s, deg, mm in the hour
           2m,WindSpeed_10m,WindDir_10m,Precip-
           Accum1h}``
GDPS       as RDPS with ``GDPS_15km_``; 3-hourly       as RDPS; ``Precip-Accum3h`` = mm in 3 h
           beyond +84 h (``Precip-Accum3h``)
=========  ==========================================  =======================================

Conversions: wind × 3.6 → km/h (the CFFDRS 10 m open wind); HRDPS rain = PR(t) − PR(t − 1 h) within
one run (PR at the run start is 0); GDPS 3-hourly rain = Accum3h(t1) / 3 for each hour of (t0, t1],
with temperature, RH and wind interpolated linearly between t0 and t1 (direction through its unit
vector). Every value is the instantaneous value at the hour, or the precipitation total of the
hour ending at it, exactly the convention of the Open-Meteo hourly series it replaces.

Which model supplies an hour (``src`` code, one character per hour):

``H``  HRDPS, newest run (forecast hours, to +48 h)
``h``  HRDPS, the newest earlier run with a 1-6 h lead (past hours: "history")
``r``  RDPS, the newest earlier run with a 1-6 h lead (history, when HRDPS has none)
``b``  GDPS, the newest earlier run with a 1-12 h lead (history beyond the HRDPS/RDPS runs kept)
``R``  RDPS, newest run (+48 to +84 h, or when HRDPS is unavailable)
``G``  GDPS, newest run, hourly (to +84 h)
``g``  GDPS, newest run, 3-hourly, interpolated to the hour (+84 to +240 h)
``-``  no data (before the oldest run GeoMet keeps, beyond +240 h, or a request failed)

GeoMet keeps about 30 h of HRDPS/RDPS runs (6 runs, 6-hourly) and 48 h of GDPS runs (5 runs,
12-hourly), so past hours reach back to the oldest GDPS run, roughly 48-60 h before now; earlier
hours are ``-`` (null), never filled from another hour.

Caching: model-run lists (WMS GetCapabilities) for ``CAPS_TTL_S``; every sampled value by
(layer, run, hour, point rounded to 0.001°) in a bounded in-process LRU, since a model run's
values never change.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

GEOMET_URL = "https://geo.weather.gc.ca/geomet"
ATTRIBUTION = "Data Source: Environment and Climate Change Canada"
LICENCE_URL = "https://eccc-msc.github.io/open-data/licence/readme_en/"
SOURCE_LABEL = "ECCC GEM (MSC GeoMet: HRDPS 2.5 km → RDPS 10 km → GDPS 15 km)"
# Value of ``weather_model`` in API responses
MODEL_ID = "eccc_gem_geomet"

HOUR = timedelta(hours=1)
CAPS_TTL_S = 10 * 60
REQUEST_TIMEOUT_S = 10.0
CONCURRENCY = 8
HALF_BOX_DEG = 0.01
CACHE_MAX = 200_000
# Longest window one call may ask for (the GDPS horizon plus two past days)
MAX_HOURS = 300

VARS = ("temp", "rh", "wind", "wdir")


@dataclass(frozen=True)
class Model:
    name: str
    layers: dict
    horizon_h: int        # hourly output to this lead
    history_lead_h: int   # max lead used for past hours from an earlier run


MODELS: dict[str, Model] = {
    "hrdps": Model("hrdps", {
        "temp": "HRDPS.CONTINENTAL_TT", "rh": "HRDPS.CONTINENTAL_HR",
        "wind": "HRDPS.CONTINENTAL_WSPD", "wdir": "HRDPS.CONTINENTAL_WD",
        "rain": "HRDPS.CONTINENTAL_PR",
    }, horizon_h=48, history_lead_h=6),
    "rdps": Model("rdps", {
        "temp": "RDPS_10km_AirTemp_2m", "rh": "RDPS_10km_RelativeHumidity_2m",
        "wind": "RDPS_10km_WindSpeed_10m", "wdir": "RDPS_10km_WindDir_10m",
        "rain": "RDPS_10km_Precip-Accum1h",
    }, horizon_h=84, history_lead_h=6),
    "gdps": Model("gdps", {
        "temp": "GDPS_15km_AirTemp_2m", "rh": "GDPS_15km_RelativeHumidity_2m",
        "wind": "GDPS_15km_WindSpeed_10m", "wdir": "GDPS_15km_WindDir_10m",
        "rain": "GDPS_15km_Precip-Accum1h", "rain3h": "GDPS_15km_Precip-Accum3h",
    }, horizon_h=84, history_lead_h=12),
}

SRC_LEGEND = {
    "H": "HRDPS 2.5 km, newest run",
    "h": "HRDPS 2.5 km, earlier run, lead 1-6 h (history)",
    "r": "RDPS 10 km, earlier run, lead 1-6 h (history)",
    "b": "GDPS 15 km, earlier run, lead 1-12 h (history)",
    "R": "RDPS 10 km, newest run",
    "G": "GDPS 15 km hourly, newest run",
    "g": "GDPS 15 km 3-hourly, interpolated to the hour",
    "-": "no data",
}


# ── Time ──────────────────────────────────────────────────────────────────────
def floor_hour(t: datetime) -> datetime:
    t = t.astimezone(timezone.utc)
    return t.replace(minute=0, second=0, microsecond=0)


def iso_z(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_duration(s: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", s.strip())
    if not m:
        raise ValueError(f"unsupported duration {s!r}")
    d, h, mi = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def parse_time_dimension(text: str) -> list[datetime]:
    """A WMS ``<Dimension>`` value: comma-separated instants and/or ``start/end/period`` ranges."""
    out: set[datetime] = set()
    for part in (text or "").split(","):
        p = part.strip()
        if not p:
            continue
        if "/" in p:
            a, b, d = p.split("/")
            t, end, step = _parse_iso(a), _parse_iso(b), _parse_duration(d)
            if step <= timedelta(0):
                raise ValueError(f"bad range {p!r}")
            while t <= end:
                out.add(t)
                t += step
        else:
            out.add(_parse_iso(p))
    return sorted(out)


@dataclass
class Caps:
    """Model runs GeoMet lists for one model."""

    ref: datetime                 # newest run (the layer's default reference_time)
    refs: list[datetime]          # every run kept
    times: list[datetime]         # valid times of the newest run


def parse_capabilities(xml: str, layer: str) -> Caps:
    """The time and reference_time dimensions of ``layer`` in a WMS GetCapabilities document."""
    m = re.search(r"<Layer[^>]*>\s*<Name>" + re.escape(layer) + r"</Name>.*?</Layer>", xml, re.S)
    if not m:
        raise ValueError(f"layer {layer} not in capabilities")
    block = m.group(0)

    def dim(name: str) -> tuple[list[datetime], datetime | None]:
        d = re.search(r'<Dimension name="' + name + r'"([^>]*)>([^<]*)</Dimension>', block)
        if not d:
            return [], None
        default = re.search(r'default="([^"]+)"', d.group(1))
        return parse_time_dimension(d.group(2)), (_parse_iso(default.group(1)) if default else None)

    times, _ = dim("time")
    refs, ref_default = dim("reference_time")
    if not times or not refs:
        raise ValueError(f"layer {layer}: no time/reference_time dimension")
    return Caps(ref=ref_default or refs[-1], refs=refs, times=times)


# ── Caches ────────────────────────────────────────────────────────────────────
_caps_cache: dict[str, tuple[float, Caps]] = {}
_samples: OrderedDict = OrderedDict()


def reset_cache() -> None:
    """Forget cached run lists and samples (tests)."""
    _caps_cache.clear()
    _samples.clear()


def _cache_get(key):
    if key in _samples:
        _samples.move_to_end(key)
        return True, _samples[key]
    return False, None


def _cache_put(key, value) -> None:
    _samples[key] = value
    _samples.move_to_end(key)
    while len(_samples) > CACHE_MAX:
        _samples.popitem(last=False)


async def _caps(client: httpx.AsyncClient, model: str) -> Caps | None:
    hit = _caps_cache.get(model)
    if hit and time.monotonic() - hit[0] < CAPS_TTL_S:
        return hit[1]
    layer = MODELS[model].layers["temp"]
    try:
        r = await client.get(GEOMET_URL, params={
            "service": "WMS", "version": "1.3.0", "request": "GetCapabilities", "layer": layer})
        r.raise_for_status()
        caps = parse_capabilities(r.text, layer)
    except Exception as exc:
        logger.warning("GeoMet capabilities for %s failed: %s", model, exc)
        return hit[1] if hit else None   # a stale list is better than none
    _caps_cache[model] = (time.monotonic(), caps)
    return caps


# ── One value ─────────────────────────────────────────────────────────────────
def parse_aaigrid(text: str) -> float | None:
    """First cell of an ESRI ASCII grid (GeoMet wraps it in a multipart body), or None."""
    if "ExceptionReport" in text and "ncols" not in text:
        return None
    lines = text.splitlines()
    nodata = None
    for i, line in enumerate(lines):
        if line.lower().startswith("ncols"):
            for row in lines[i + 1:]:
                parts = row.split()
                if not parts:
                    continue
                key = parts[0].lower()
                if key == "nodata_value" and len(parts) > 1:
                    nodata = float(parts[1])
                    continue
                if key[0].isalpha():
                    continue
                v = float(parts[0])
                if not math.isfinite(v) or abs(v) > 1e30 or (nodata is not None and v == nodata):
                    return None
                return v
            return None
    return None


def coverage_params(layer: str, ref: datetime, t: datetime, lat: float, lng: float) -> list:
    d = HALF_BOX_DEG
    return [
        ("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
        ("coverageId", layer), ("subsettingcrs", "EPSG:4326"),
        ("subset", f"x({lng - d:.4f},{lng + d:.4f})"), ("subset", f"y({lat - d:.4f},{lat + d:.4f})"),
        ("TIME", iso_z(t)), ("DIM_REFERENCE_TIME", iso_z(ref)),
        ("format", "image/x-aaigrid"), ("SCALESIZE", "x(1),y(1)"),
    ]


class _Sampler:
    def __init__(self, client: httpx.AsyncClient, lat: float, lng: float):
        self.client, self.lat, self.lng = client, round(lat, 3), round(lng, 3)
        self.sem = asyncio.Semaphore(CONCURRENCY)
        self.failed = 0
        self.requests = 0

    async def get(self, layer: str, ref: datetime, t: datetime) -> float | None:
        key = (layer, ref, t, self.lat, self.lng)
        hit, val = _cache_get(key)
        if hit:
            return val
        async with self.sem:
            hit, val = _cache_get(key)
            if hit:
                return val
            self.requests += 1
            try:
                r = await self.client.get(GEOMET_URL, params=coverage_params(layer, ref, t, self.lat, self.lng))
                r.raise_for_status()
                val = parse_aaigrid(r.text)
            except Exception as exc:
                self.failed += 1
                logger.debug("GeoMet %s %s run %s failed: %s", layer, iso_z(t), iso_z(ref), exc)
                return None   # not cached: a later call retries
        _cache_put(key, val)
        return val


# ── Which model supplies each hour ────────────────────────────────────────────
@dataclass(frozen=True)
class HourSource:
    model: str
    ref: datetime
    code: str
    t0: datetime | None = None   # 'g' only: bracketing 3-hourly instants
    t1: datetime | None = None


def _history(t: datetime, caps: Caps | None, max_lead_h: int) -> datetime | None:
    if caps is None:
        return None
    best = None
    for r in caps.refs:
        lead = (t - r) / HOUR
        if 1 <= lead <= max_lead_h and (best is None or r > best):
            best = r
    return best


def choose_source(t: datetime, caps: dict[str, Caps | None]) -> HourSource | None:
    """The model run that supplies hour ``t`` (see the module docstring for the order)."""
    hr, rd, gd = caps.get("hrdps"), caps.get("rdps"), caps.get("gdps")
    newest = max((c.ref for c in (hr, rd, gd) if c is not None), default=None)
    if newest is None:
        return None
    if hr is not None and hr.ref < t <= hr.ref + MODELS["hrdps"].horizon_h * HOUR:
        return HourSource("hrdps", hr.ref, "H")
    # Past hours (at or before a newest run's start): short-lead forecasts of earlier runs
    if t <= (hr.ref if hr is not None else newest):
        for model, code in (("hrdps", "h"), ("rdps", "r"), ("gdps", "b")):
            r = _history(t, caps.get(model), MODELS[model].history_lead_h)
            if r is not None:
                return HourSource(model, r, code)
        return None
    if rd is not None and rd.ref < t <= rd.ref + MODELS["rdps"].horizon_h * HOUR:
        return HourSource("rdps", rd.ref, "R")
    if gd is not None and t > gd.ref:
        listed = set(gd.times)
        if t in listed and (t - HOUR) in listed and t <= gd.ref + MODELS["gdps"].horizon_h * HOUR:
            return HourSource("gdps", gd.ref, "G")
        t0 = max((x for x in gd.times if x <= t), default=None)
        t1 = min((x for x in gd.times if x >= t), default=None)
        if t0 is not None and t1 is not None:
            if t0 == t1:   # a listed 3-hourly instant
                return HourSource("gdps", gd.ref, "g", t - 3 * HOUR, t)
            if t1 - t0 == 3 * HOUR:
                return HourSource("gdps", gd.ref, "g", t0, t1)
    return None


# ── Series ────────────────────────────────────────────────────────────────────
def _lerp(a, b, f):
    return None if a is None or b is None else a * (1 - f) + b * f


def _lerp_dir(a, b, f):
    if a is None or b is None:
        return None
    rad = math.pi / 180
    u = math.sin(a * rad) * (1 - f) + math.sin(b * rad) * f
    v = math.cos(a * rad) * (1 - f) + math.cos(b * rad) * f
    return (math.atan2(u, v) / rad + 360) % 360


async def _hour_values(s: _Sampler, t: datetime, src: HourSource) -> dict:
    m = MODELS[src.model]
    L = m.layers
    if src.code == "g":
        a, b = await asyncio.gather(
            asyncio.gather(*(s.get(L[v], src.ref, src.t0) for v in VARS)),
            asyncio.gather(*(s.get(L[v], src.ref, src.t1) for v in VARS)),
        )
        f = (t - src.t0) / (src.t1 - src.t0)
        rain3 = await s.get(L["rain3h"], src.ref, src.t1)
        vals = {
            "temp": _lerp(a[0], b[0], f), "rh": _lerp(a[1], b[1], f),
            "wind": _lerp(a[2], b[2], f), "wdir": _lerp_dir(a[3], b[3], f),
            "rain": None if rain3 is None else rain3 / 3.0,
        }
    else:
        got = await asyncio.gather(*(s.get(L[v], src.ref, t) for v in VARS))
        vals = dict(zip(VARS, got))
        if src.model == "hrdps":
            now_acc = await s.get(L["rain"], src.ref, t)
            prev_t = t - HOUR
            prev_acc = 0.0 if prev_t <= src.ref else await s.get(L["rain"], src.ref, prev_t)
            vals["rain"] = None if now_acc is None or prev_acc is None else max(0.0, now_acc - prev_acc)
        else:
            vals["rain"] = await s.get(L["rain"], src.ref, t)
    if vals["wind"] is not None:
        vals["wind"] = vals["wind"] * 3.6
    if vals["rh"] is not None:
        vals["rh"] = min(100.0, max(0.0, vals["rh"]))
    if vals["rain"] is not None:
        vals["rain"] = max(0.0, vals["rain"])
    if vals["wdir"] is not None:
        vals["wdir"] = vals["wdir"] % 360
    return vals


async def hourly(
    lat: float, lng: float, start: datetime, end: datetime,
    client: httpx.AsyncClient | None = None,
) -> dict | None:
    """Hourly GEM weather at (lat, lng) for every UTC hour from ``start`` to ``end`` (inclusive,
    floored to the hour), in the shape of an Open-Meteo ``hourly`` block:

    ``{"time": ["YYYY-MM-DDTHH:00", ...] (UTC), "temperature_2m": [°C], "relative_humidity_2m": [%],
    "wind_speed_10m": [km/h], "wind_direction_10m": [deg], "precipitation": [mm in the hour ending
    at the stamp], "src": "HHH...", "model_runs": {...}, "requests": n, "failed": n}``

    Hours no model run covers are ``None`` (``src`` ``-``). Returns None when GeoMet lists no run
    at all (unreachable).
    """
    t_start, t_end = floor_hour(start), floor_hour(end)
    if t_end < t_start:
        raise ValueError("end before start")
    n = int((t_end - t_start) / HOUR) + 1
    if n > MAX_HOURS:
        raise ValueError(f"window of {n} h exceeds {MAX_HOURS} h")
    own = client is None
    if own:
        client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)
    try:
        names = list(MODELS)
        got = await asyncio.gather(*(_caps(client, m) for m in names))
        caps = dict(zip(names, got))
        if all(c is None for c in caps.values()):
            return None
        sampler = _Sampler(client, lat, lng)
        hours = [t_start + i * HOUR for i in range(n)]
        sources = [choose_source(t, caps) for t in hours]

        async def one(t, src):
            if src is None:
                return {"temp": None, "rh": None, "wind": None, "wdir": None, "rain": None}
            return await _hour_values(sampler, t, src)

        values = await asyncio.gather(*(one(t, s) for t, s in zip(hours, sources)))
    finally:
        if own:
            await client.aclose()
    out = {
        "time": [t.strftime("%Y-%m-%dT%H:00") for t in hours],
        "temperature_2m": [v["temp"] for v in values],
        "relative_humidity_2m": [v["rh"] for v in values],
        "wind_speed_10m": [v["wind"] for v in values],
        "wind_direction_10m": [v["wdir"] for v in values],
        "precipitation": [v["rain"] for v in values],
        "src": "".join(
            (s.code if s is not None and v["temp"] is not None else "-") for s, v in zip(sources, values)),
        "model_runs": {m: (iso_z(c.ref) if c else None) for m, c in caps.items()},
        "requests": sampler.requests,
        "failed": sampler.failed,
    }
    return out


def src_summary(src: str) -> str:
    """Run-length summary of a ``src`` string, e.g. ``h30H48R6`` (as Pyra writes it)."""
    return re.sub(r"(.)\1*", lambda m: f"{m.group(1)}{len(m.group(0))}", src)
