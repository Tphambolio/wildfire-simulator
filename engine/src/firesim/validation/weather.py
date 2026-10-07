"""Hourly reanalysis weather for validation fire-days.

CFSDS gives one ERA5-derived row per fire-day (FWI codes, daily wind speed, RH) but no wind
direction or diurnal cycle. The harness therefore takes hourly 2 m temperature and RH, 10 m
wind speed and direction and precipitation from ERA5 (Hersbach et al. 2020, 0.25 deg) through
the Open-Meteo historical archive API (https://open-meteo.com/en/docs/historical-weather-api,
CC BY 4.0; ERA5 under the Copernicus licence), in local time. Bennett et al. (2026) used ERA5-Land
(9 km); Open-Meteo's ERA5-Land feed has no 10 m wind, so ERA5 is used for all variables.

Starting FWI codes (FFMC, DMC, DC) come from the CFSDS row of the previous day.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from firesim.types import HourlyWeather

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
               "wind_direction_10m", "precipitation")


@dataclass(frozen=True)
class HourRecord:
    """One local-time hour of reanalysis weather."""

    time: datetime  # local, naive
    temperature: float
    relative_humidity: float
    wind_speed: float  # km/h, 10 m
    wind_direction: float  # degrees FROM
    precipitation: float  # mm in the hour


def doy_to_date(year: int, doy: int) -> date:
    return date(year, 1, 1) + timedelta(days=doy - 1)


def fetch_era5_hourly(lat: float, lng: float, start: date, end: date,
                      cache_dir: str | Path | None = None,
                      timezone: str = "America/Edmonton", model: str = "era5") -> list[HourRecord]:
    """Hourly ERA5 weather at a point for ``start``..``end`` (inclusive, local dates)."""
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARS), "timezone": timezone, "models": model,
        "wind_speed_unit": "kmh",
    }
    cache = None
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        key = f"{model}_{lat:.3f}_{lng:.3f}_{start}_{end}.json"
        cache = Path(cache_dir) / key
    if cache is not None and cache.exists():
        data = json.loads(cache.read_text())
    else:
        url = OPEN_METEO_ARCHIVE + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = json.load(r)
        if cache is not None:
            cache.write_text(json.dumps(data))
    return parse_open_meteo(data)


def parse_open_meteo(data: dict) -> list[HourRecord]:
    """HourRecords from an Open-Meteo archive response (hours with any missing value dropped)."""
    h = data["hourly"]
    out = []
    for i, t in enumerate(h["time"]):
        vals = [h[v][i] for v in HOURLY_VARS]
        if any(v is None for v in vals):
            continue
        out.append(HourRecord(datetime.fromisoformat(t), *[float(v) for v in vals]))
    return out


def burn_hours(records: list[HourRecord], start: datetime, hours: int) -> tuple[HourlyWeather, ...]:
    """FireSim hourly stream for ``hours`` hours from local time ``start``."""
    by_time = {r.time: r for r in records}
    out = []
    for k in range(hours):
        r = by_time.get(start + timedelta(hours=k))
        if r is None:
            raise KeyError(f"no weather for {start + timedelta(hours=k)}")
        out.append(HourlyWeather(
            hours_from_start=float(k), temperature=r.temperature,
            relative_humidity=r.relative_humidity, wind_speed=r.wind_speed,
            wind_direction=r.wind_direction, precipitation=r.precipitation,
        ))
    return tuple(out)
