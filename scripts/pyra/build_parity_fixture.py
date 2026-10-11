#!/usr/bin/env python3
"""Build api/tests/fixtures/pyra_parity.json: recorded inputs + Pyra's own results.

1. Captures real inputs once (read only): Pyra's published ``data/cwfis_prev.json``, the CWFIS
   ``firewx_stns_current`` layer for Alberta, and the GEM hourly payload Pyra's ``fetchWeather``
   requests for each case's Pyra station (same URL as Pyra).
2. Adds synthetic variants of those inputs for the branches real data cannot show on the capture
   day (CWFIS with today's codes, empty, weather-only, 2-day-old and today's carry-over, the spring
   DC floor, a stale file).
3. Runs Pyra's engine (``scripts/pyra/run_pyra_parity.mjs`` with node and the read-only Pyra
   checkout ``PYRA_DIR``) on every case and stores its results with the Pyra commit, so the API
   test can compare FireSim with Pyra without node or Pyra present.

    PYRA_DIR=/home/rpas/dev/FWI python3 scripts/pyra/build_parity_fixture.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api" / "src"))
sys.path.insert(0, str(ROOT / "engine" / "src"))
from firesim_api.services import pyra  # noqa: E402

OUT = ROOT / "api" / "tests" / "fixtures" / "pyra_parity.json"
PYRA_DIR = os.environ.get("PYRA_DIR", "/home/rpas/dev/FWI")
KEEP = ("name", "prov", "lat", "lon", "elev", "temp", "rh", "ws", "wdir", "precip",
        "ffmc", "dmc", "dc", "isi", "bui", "fwi", "rep_date")

IGNITIONS = {
    "edmonton": (53.5461, -113.4938),
    "calgary": (51.0447, -114.0719),
    "grande_prairie": (55.1707, -118.7947),
    "fort_mcmurray": (56.7267, -111.3810),
}


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "FireSim parity fixture builder"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def capture() -> tuple[dict, list[dict], dict]:
    prev = get_json(pyra.PREV_URLS[0])
    prev = {"generated": prev["generated"], "stations": prev["stations"]}   # AB section only
    la0, la1, lo0, lo1 = pyra.AB_BBOX
    q = urllib.parse.urlencode({
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": "public:firewx_stns_current", "outputFormat": "application/json", "count": "2000",
        "CQL_FILTER": f"lat BETWEEN {la0 - 2} AND {la1 + 2} AND lon BETWEEN {lo0 - 2} AND {lo1 + 2}",
    })
    feats = get_json(f"{pyra.CWFIS_URL}?{q}")["features"]
    feats = [{"type": "Feature", "id": f.get("id"),
              "properties": {k: f["properties"].get(k) for k in KEEP}} for f in feats]
    gem = {}
    for lat, lng in IGNITIONS.values():
        (_, s_lat, s_lng), _ = pyra.nearest_pyra_station(lat, lng)
        key = f"{pyra._js_num(s_lat)},{pyra._js_num(s_lng)}"
        if key not in gem:
            gem[key] = get_json(pyra.gem_url(s_lat, s_lng))
    return prev, feats, gem


def synthetic_gem(day: str, past: int = 1, fc: int = 2) -> dict:
    """A smooth diurnal GEM-like payload (timezone=UTC) around ``day`` for the spring case."""
    d0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(days=past)
    times, t, rh, ws, wd, pr = [], [], [], [], [], []
    for i in range(24 * (past + fc)):
        ts = d0 + timedelta(hours=i)
        loc = (ts.hour - 7) % 24
        times.append(ts.strftime("%Y-%m-%dT%H:00"))
        t.append(round(12 + 9 * math.sin((loc - 9) / 24 * 2 * math.pi), 1))
        rh.append(round(55 - 25 * math.sin((loc - 9) / 24 * 2 * math.pi)))
        ws.append(round(12 + 6 * math.sin((loc - 10) / 24 * 2 * math.pi), 1))
        wd.append(250)
        pr.append(0.4 if ts.date().isoformat() == day and 2 <= ts.hour <= 6 else 0.0)  # 2 mm before noon
    return {"hourly": {"time": times, "temperature_2m": t, "relative_humidity_2m": rh,
                       "wind_speed_10m": ws, "wind_direction_10m": wd, "precipitation": pr,
                       "thunderstorm_probability": [0] * len(times)}}


def dump(fx: dict) -> str:
    """Readable cases and results; the bulky recorded inputs on one line each."""
    bulky = ("feature_sets", "prev_sets", "gem_sets")
    head = {k: v for k, v in fx.items() if k not in bulky}
    text = json.dumps(head, indent=1)[:-2]
    for k in bulky:
        if k in fx:
            text += f',\n "{k}": ' + json.dumps(fx[k], separators=(",", ":"))
    return text + "\n}\n"


def main() -> None:
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev, feats, gem = capture()
    rep_dates = sorted({str(f["properties"].get("rep_date"))[:10] for f in feats if f["properties"].get("rep_date")})
    obs = rep_dates[-1]                                   # newest CWFIS report date (yesterday)
    today = (datetime.fromisoformat(obs) + timedelta(days=1)).date().isoformat()

    # Keep only the features the cases' CWFIS queries (±2° around each Pyra station) can return
    boxes = []
    for lat, lng in IGNITIONS.values():
        (_, s_lat, s_lng), _ = pyra.nearest_pyra_station(lat, lng)
        boxes.append((s_lat - 2, s_lat + 2, s_lng - 2, s_lng + 2))
    feats = [f for f in feats if any(b[0] <= float(f["properties"]["lat"]) <= b[1]
                                     and b[2] <= float(f["properties"]["lon"]) <= b[3] for b in boxes)]

    spring = {"generated": "2026-05-11T20:15:00Z", "stations": {
        "EDMONTON BLATCHFORD": {"ffmc": 87.1, "dmc": 9.4, "dc": 15.0, "isi": 5.2, "bui": 9.4, "fwi": 4.9,
                                "lat": 53.5667, "lon": -113.5167, "repDate": "2026-05-11T12:00:00Z"}}}
    spring_key = "53.567,-113.517"

    t = datetime.fromisoformat(today)
    at = lambda h, m=0: (t + timedelta(hours=h, minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    cases = []
    for name, (lat, lng) in IGNITIONS.items():
        cases.append(dict(id=f"prenoon_live_{name}", lat=lat, lng=lng, now=at(15), cwfis="live",
                          prev="captured", gem="captured",
                          what="real inputs; 08:00 MST: CWFIS holds yesterday's codes, stepped with the GEM noon forecast"))
    for name in ("edmonton", "calgary"):
        lat, lng = IGNITIONS[name]
        cases.append(dict(id=f"postnoon_cwfis_today_{name}", lat=lat, lng=lng, now=at(21), cwfis="live", cwfis_transform="rep_date_today",
                          prev="captured", gem="captured",
                          what="synthetic rep_date=today; 14:00 MST: CWFIS codes shown as published"))
        cases.append(dict(id=f"postnoon_cwfis_yesterday_{name}", lat=lat, lng=lng, now=at(19, 30), cwfis="live",
                          prev="captured", gem="captured",
                          what="real inputs; 12:30 MST before the CWFIS refresh: yesterday's codes shown as published"))
    for name in ("edmonton", "grande_prairie"):
        lat, lng = IGNITIONS[name]
        cases.append(dict(id=f"postnoon_cwfis_empty_{name}", lat=lat, lng=lng, now=at(19, 30), cwfis="empty",
                          prev="captured", gem="captured",
                          what="CWFIS empty (refresh window), SWOB empty: cwfis_prev stepped with GEM noon"))
    lat, lng = IGNITIONS["edmonton"]
    cases += [
        dict(id="postnoon_wx_only_edmonton", lat=lat, lng=lng, now=at(20), cwfis="live", cwfis_transform="strip_codes", prev="captured",
             gem="captured", what="synthetic: CWFIS weather-only stations; cwfis_prev stepped with the CWFIS obs"),
        dict(id="prenoon_cwfis_down_2day_edmonton", lat=lat, lng=lng, now=at(16), cwfis="error",
             prev="captured", prev_rep_date=(t - timedelta(days=2)).date().isoformat(), gem="captured", what="synthetic: carry-over 2 days old, stepped once"),
        dict(id="prenoon_cwfis_down_final_edmonton", lat=lat, lng=lng, now=at(17), cwfis="error",
             prev="captured", prev_rep_date=today, gem="captured", what="synthetic: carry-over already today's (final), not stepped"),
        dict(id="spring_dc_floor_edmonton", lat=lat, lng=lng, now="2026-05-12T16:00:00Z", cwfis="error",
             prev="spring", gem="spring", what="synthetic: May, cold-start DC 15 raised to the regional floor 300"),
        dict(id="stale_file_edmonton", lat=lat, lng=lng, now=at(24 * 3 + 15), cwfis="error", prev="captured",
             gem="captured", what="file generated > 48 h before: Pyra shows no codes (inactive)"),
    ]
    equations = [
        dict(id="ftr33_day1", prev=[85.0, 6.0, 15.0], temp=17, rh=42, wind=25, rain=0, month=4),
        dict(id="hot_dry_windy", prev=[90.0, 60.0, 400.0], temp=32, rh=12, wind=40, rain=0, month=7),
        dict(id="heavy_rain", prev=[92.0, 80.0, 500.0], temp=14, rh=85, wind=10, rain=25, month=8),
        dict(id="light_rain", prev=[88.0, 30.0, 250.0], temp=20, rh=50, wind=15, rain=0.6, month=6),
        dict(id="rh100_calm", prev=[80.0, 20.0, 200.0], temp=8, rh=100, wind=0, rain=0, month=9),
        dict(id="cold_day_may", prev=[85.0, 10.0, 200.0], temp=-5, rh=60, wind=10, rain=0, month=5),
        dict(id="cold_day_july", prev=[85.0, 10.0, 300.0], temp=-3, rh=60, wind=10, rain=0, month=7),
        dict(id="clamp_rh_over_100", prev=[86.0, 25.0, 280.0], temp=5, rh=104, wind=5, rain=2.0, month=10),
    ]
    for e in equations:
        e["prev"] = dict(zip(("ffmc", "dmc", "dc"), e["prev"]))

    fx = {
        "about": ("FireSim vs Pyra parity fixture. Inputs captured read-only at captured_at; synthetic "
                  "variants are marked in each case's 'what'. 'pyra' holds the results of Pyra's own "
                  "engine (run_pyra_parity.mjs) at pyra_commit; FireSim must reproduce them."),
        "captured_at": captured_at,
        "sources": {
            "cwfis_prev": pyra.PREV_URLS[0],
            "cwfis": f"{pyra.CWFIS_URL} public:firewx_stns_current (Alberta box ±2°)",
            "gem": "Open-Meteo forecast API, Pyra fetchWeather URL (models=gem_seamless)",
        },
        "today": today,
        "transforms": {
            "cwfis_transform": {"rep_date_today": "every feature's rep_date set to today's date (T12:00:00Z)",
                                "strip_codes": "ffmc/dmc/dc/isi/bui/fwi set to null (weather-only stations)"},
            "prev_rep_date": "every carry-over entry's repDate set to this date (T12:00:00Z)",
        },
        "feature_sets": {"live": feats, "empty": []},
        "prev_sets": {"captured": prev, "spring": spring},
        "gem_sets": {"captured": gem, "spring": {spring_key: synthetic_gem("2026-05-12")}},
        "cases": cases,
        "equations": equations,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(dump(fx))
    res = subprocess.run(["node", str(ROOT / "scripts" / "pyra" / "run_pyra_parity.mjs"), str(OUT)],
                         env={**os.environ, "PYRA_DIR": PYRA_DIR}, capture_output=True, text=True, check=True)
    fx["pyra"] = json.loads(res.stdout)
    OUT.write_text(dump(fx))
    print(f"wrote {OUT} ({len(cases)} cases, {len(equations)} equation cases, Pyra {fx['pyra']['pyra_commit'][:7]})")


if __name__ == "__main__":
    main()
