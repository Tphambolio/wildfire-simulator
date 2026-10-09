#!/usr/bin/env python3
"""Input-error climatology for the ensemble perturbation sizes (Alberta fire seasons).

Following Fox-Hughes et al. (2024), who sized ensemble weather perturbations from an
"error climatology" of forecast grids against nearby weather-station observations, this
compares, at ECCC hourly stations in Alberta's forested regions, May-September 2024-2025:

- the **GEM day-1 forecast** (Open-Meteo previous-runs API, ``*_previous_day1``: the value
  forecast the day before, i.e. 24-47 h lead time) - what an operational run is driven by;
- **ERA5** (Open-Meteo archive) - what the validation harness is driven by;

against the station observations (ECCC MSC GeoMet ``climate-hourly``). Reported:

- Wind direction error of the burning-period (10:00-20:00 MDT) vector-mean wind, on days
  whose observed mean wind is >= 10 km/h: the ensemble applies one direction offset per
  member to every hour, so it represents the day's systematic error, not hourly noise.
  The hourly error is also given.
- Wind speed: sd of log(forecast / observed) of the burning-period mean speed.
- FWI codes: FFMC, DMC and DC computed with ``FWICalculator`` from noon-LST weather and
  noon-to-noon rain, from a 1 May start-up, once with station weather and once with the
  gridded weather; reported as the sd of the FFMC difference and of log(DMC), log(DC)
  ratios through the season (June-September, after the start-up has worn off).

Usage (needs network; caches under $FIRESIM_VALIDATION_DATA/input_errors):

    PYTHONPATH=engine/src python scripts/validation/input_errors.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
CACHE = ROOT / "input_errors"
# ECCC hourly stations in the boreal and foothills forest (not the prairie)
STATIONS = {
    "3015523": "Rocky Mtn House", "3026KNQ": "Sundre A", "3053536": "Jasper Warden",
    "3057376": "Whitecourt A", "3062246": "Edson Climate", "3062696": "Fort McMurray CS",
    "3065995": "Slave Lake", "3072659": "Fort Chipewyan RCS", "3072921": "Grande Prairie A",
    "3073148": "High Level", "3075041": "Peace River A", "3075488": "Red Earth",
    "3081680": "Cold Lake A", "301A001": "Stony Plain CS",
}
SEASONS = ((date(2024, 5, 1), date(2024, 9, 30)), (date(2025, 5, 1), date(2025, 9, 30)))
VARS = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
        "precipitation")
BURN_UTC = range(16, 27)  # 10:00-20:00 MDT = 16-02 UTC (hours >= 24 are the next UTC day)
MIN_WS = 10.0


def _get(url: str, cache: Path):
    if cache.exists():
        return json.loads(cache.read_text())
    with urllib.request.urlopen(url, timeout=120) as r:
        data = json.loads(r.read())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data


def station_hourly(cid: str, d0: date, d1: date) -> tuple[dict, tuple[float, float]]:
    """UTC hour -> (T, RH, ws km/h, wd deg, precip mm) and the station (lat, lng)."""
    q = {"f": "json", "CLIMATE_IDENTIFIER": cid, "limit": 10000,
         "datetime": f"{d0}T00:00:00Z/{d1 + timedelta(days=1)}T23:00:00Z",
         "properties": "UTC_DATE,TEMP,RELATIVE_HUMIDITY,WIND_SPEED,WIND_DIRECTION,PRECIP_AMOUNT"}
    url = "https://api.weather.gc.ca/collections/climate-hourly/items?" + urllib.parse.urlencode(q)
    data = _get(url, CACHE / f"eccc_{cid}_{d0}_{d1}.json")
    out = {}
    lat = lng = math.nan
    for f in data["features"]:
        p = f["properties"]
        lng, lat = f["geometry"]["coordinates"]
        t = datetime.fromisoformat(p["UTC_DATE"])
        wd = p["WIND_DIRECTION"]
        out[t] = (p["TEMP"], p["RELATIVE_HUMIDITY"], p["WIND_SPEED"],
                  None if wd is None else (wd * 10.0) % 360.0, p["PRECIP_AMOUNT"])
    return out, (lat, lng)


def grid_hourly(lat: float, lng: float, d0: date, d1: date, source: str) -> dict:
    """UTC hour -> (T, RH, ws, wd, precip) from ERA5 or the GEM day-1 forecast."""
    if source == "era5":
        base = "https://archive-api.open-meteo.com/v1/archive"
        names = VARS
        model = "era5"
    else:
        base = "https://previous-runs-api.open-meteo.com/v1/forecast"
        names = tuple(f"{v}_previous_day1" for v in VARS)
        model = "gem_seamless"
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}", "start_date": str(d0),
         "end_date": str(d1 + timedelta(days=1)), "hourly": ",".join(names), "models": model,
         "wind_speed_unit": "kmh", "timezone": "GMT"}
    data = _get(base + "?" + urllib.parse.urlencode(q),
                CACHE / f"{source}_{lat:.3f}_{lng:.3f}_{d0}_{d1}.json")
    h = data["hourly"]
    out = {}
    for i, t in enumerate(h["time"]):
        out[datetime.fromisoformat(t)] = tuple(h[n][i] for n in names)
    return out


def circ_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def vector_mean(ws, wd):
    u = np.mean([s * math.sin(math.radians(d)) for s, d in zip(ws, wd)])
    v = np.mean([s * math.cos(math.radians(d)) for s, d in zip(ws, wd)])
    return math.degrees(math.atan2(u, v)) % 360.0, float(np.mean(ws))


def daily_burn_wind(hourly: dict, day: date):
    """(vector-mean direction, mean speed) over 10-20 MDT of ``day`` or None if incomplete."""
    ws, wd = [], []
    for h in BURN_UTC:
        t = datetime(day.year, day.month, day.day) + timedelta(hours=h)
        r = hourly.get(t)
        if r is None or r[2] is None or r[3] is None:
            return None
        ws.append(r[2])
        wd.append(r[3])
    return vector_mean(ws, wd)


def fwi_series(hourly: dict, d0: date, d1: date) -> dict:
    """date -> (FFMC, DMC, DC) from noon-LST (19 UTC) weather and noon-to-noon rain."""
    from firesim.fwi.calculator import FWICalculator

    calc = FWICalculator(ffmc_prev=85.0, dmc_prev=6.0, dc_prev=15.0)
    out = {}
    d = d0 + timedelta(days=1)  # the first day's rain window starts before the data
    while d <= d1:
        noon = datetime(d.year, d.month, d.day, 19)
        r = next((hourly[t] for t in (noon, noon - timedelta(hours=1), noon + timedelta(hours=1))
                  if hourly.get(t) is not None and None not in hourly[t][:3]), None)
        rain = [hourly.get(noon - timedelta(hours=k)) for k in range(24)]
        rain = [None if x is None else x[4] for x in rain]
        if r is None or sum(x is None for x in rain) > 3:
            return out  # stop at the first real gap: later codes would not be comparable
        res = calc.calculate_daily(temp=r[0], rh=min(100.0, r[1]), wind=r[2],
                                   rain=sum(x for x in rain if x is not None), month=d.month)
        out[d] = (res.ffmc, res.dmc, res.dc)
        d += timedelta(days=1)
    return out


def robust_sd(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def main() -> None:
    acc: dict = defaultdict(lambda: defaultdict(list))
    for cid, name in STATIONS.items():
        for d0, d1 in SEASONS:
            try:
                obs, (lat, lng) = station_hourly(cid, d0, d1)
            except Exception as exc:  # noqa: BLE001 - a missing station is skipped, not fatal
                print(f"{name}: {exc!r}", file=sys.stderr)
                continue
            if not obs:
                continue
            grids = {s: grid_hourly(lat, lng, d0, d1, s) for s in ("gem_day1", "era5")}
            d = d0
            while d <= d1:
                o = daily_burn_wind(obs, d)
                for s, g in grids.items():
                    f = daily_burn_wind(g, d) if o else None
                    if not f:
                        continue
                    if o[1] >= MIN_WS:  # conditioned on the observed wind
                        acc[s]["wd_day"].append(circ_diff(f[0], o[0]))
                        acc[s]["ws_day_log"].append(math.log(max(f[1], 1.0) / o[1]))
                    if f[1] >= MIN_WS:  # conditioned on the forecast (what a run knows)
                        acc[s]["wd_day_given_fc"].append(circ_diff(f[0], o[0]))
                        acc[s]["ws_day_log_given_fc"].append(math.log(f[1] / max(o[1], 1.0)))
                for h in BURN_UTC:
                    t = datetime(d.year, d.month, d.day) + timedelta(hours=h)
                    r = obs.get(t)
                    if r and r[2] is not None and r[3] is not None and r[2] >= MIN_WS:
                        for s, g in grids.items():
                            f = g.get(t)
                            if f and f[3] is not None:
                                acc[s]["wd_hour"].append(circ_diff(f[3], r[3]))
                d += timedelta(days=1)
            so = fwi_series(obs, d0, d1)
            for s, g in grids.items():
                sg = fwi_series(g, d0, d1)
                for day in sorted(set(so) & set(sg)):
                    if day.month < 6:
                        continue
                    (fo, mo, co), (fg, mg, cg) = so[day], sg[day]
                    acc[s]["ffmc"].append(fg - fo)
                    acc[s]["dmc_log"].append(math.log((mg + 1.0) / (mo + 1.0)))
                    acc[s]["dc_log"].append(math.log((cg + 1.0) / (co + 1.0)))
                    if fo >= 85.0:  # fire weather days
                        acc[s]["ffmc_ge85"].append(fg - fo)
            print(f"{name} {d0.year}: {len(so)} station FWI days", file=sys.stderr)
    out = {}
    for s, a in acc.items():
        out[s] = {k: {"n": len(v), "mean": float(np.mean(v)), "sd": float(np.std(v, ddof=1)),
                      "robust_sd": robust_sd(v),
                      "p10": float(np.percentile(v, 10)), "p90": float(np.percentile(v, 90))}
                  for k, v in a.items()}
    text = json.dumps(out, indent=1)
    print(text)
    (CACHE / "summary.json").write_text(text)


if __name__ == "__main__":
    main()
