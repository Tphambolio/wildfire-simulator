#!/usr/bin/env python3
"""[R72] stage 1 (b): ERA5 wind vs nearby open surface stations on the C1b fire-days.

Purpose
    Check the wind FireSim ran with (ERA5 hourly via Open-Meteo at the record's weather point)
    against observed 10 m winds at the nearest open stations, and quantify gusts and the wind
    direction error toward each reached community.

Data (open, no request to any agency)
    Station observations: Iowa Environmental Mesonet (IEM) ASOS/AWOS archive of Canadian METAR
    and ECCC automatic stations (networks CA_AB_ASOS, CA_NT_ASOS, CA_BC_ASOS, CA_SK_ASOS;
    https://mesonet.agron.iastate.edu/request/download.phtml), fields drct (deg), sknt (kt),
    gust (kt). ERA5 gusts: Open-Meteo archive ``wind_gusts_10m`` at the same point and model as
    the run (CC BY 4.0; ERA5 Copernicus licence).

Method
    Per fire-day: stations within 150 km of the centroid of its reached communities' reach zones,
    nearest first (at most 3). Hourly station values = the report nearest each local clock hour
    (within 30 min; local = UTC-6 as in C1b). Hours 0..24 from 06:00 local. Speed bias = station -
    ERA5 (km/h) over hours with both; direction error = circular |station - ERA5| over hours with
    station speed >= 5 km/h; for each community the angle between the wind's "toward" direction
    and the start->community bearing (diagnose.py), ERA5 vs nearest station, burning-period hours
    (10-20 local). The nearest station with >= 16 valid hours in 0..24 gives an hourly series for
    the sensitivity variants ``station_wind`` / ``station_speed`` / ``station_dir``
    (sensitivity.py); missing hours are filled from ERA5 and counted.

Outputs
    OUT/weather_obs.json   per fire-day: stations, hourly table, metrics, per-community angles
    BIG/stations/<sid>_<date>.csv, BIG/stations/era5gust_<lat>_<lng>_<date>.json (raw caches)
Usage
    python3 scripts/validation/arrival_r72/weather_obs.py
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402

ST = K.BIG / "stations"
NETS = ("CA_AB_ASOS", "CA_NT_ASOS", "CA_BC_ASOS", "CA_SK_ASOS")
KT = 1.852
MAX_KM = 150.0


def stations() -> pd.DataFrame:
    rows = []
    for n in NETS:
        j = json.loads((ST / f"{n}.geojson").read_text())
        for f in j["features"]:
            lng, lat = f["geometry"]["coordinates"]
            rows.append({"sid": f["properties"]["sid"], "name": f["properties"]["sname"], "lat": lat,
                         "lng": lng, "net": n})
    return pd.DataFrame(rows).drop_duplicates("sid")


def hav_km(lat1, lng1, lat2, lng2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def fetch_station(sid: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    p = ST / f"{sid}_{start_utc:%Y%m%d%H}_{end_utc:%Y%m%d%H}.csv"
    if not p.exists():
        url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
               f"station={sid}&data=drct&data=sknt&data=gust&tz=Etc/UTC&format=onlycomma&latlon=no"
               f"&missing=M&trace=T&report_type=3&report_type=4"
               f"&year1={start_utc.year}&month1={start_utc.month}&day1={start_utc.day}&hour1={start_utc.hour}"
               f"&year2={end_utc.year}&month2={end_utc.month}&day2={end_utc.day}&hour2={end_utc.hour}")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=120) as r:
                    p.write_bytes(r.read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(5 * (attempt + 1))
        time.sleep(1.0)
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_csv(p, na_values=["M"])
    if d.empty:
        return d
    d["valid"] = pd.to_datetime(d["valid"])
    return d


def era5_gusts(lat, lng, date) -> dict:
    p = ST / f"era5gust_{lat:.3f}_{lng:.3f}_{date}.json"
    if not p.exists():
        d0 = (pd.Timestamp(date) - timedelta(days=1)).date()
        d1 = (pd.Timestamp(date) + timedelta(days=1)).date()
        url = ("https://archive-api.open-meteo.com/v1/archive?"
               f"latitude={lat:.4f}&longitude={lng:.4f}&start_date={d0}&end_date={d1}"
               "&hourly=wind_gusts_10m,wind_speed_10m,wind_direction_10m&timezone=America%2FEdmonton"
               "&models=era5&wind_speed_unit=kmh")
        with urllib.request.urlopen(url, timeout=120) as r:
            p.write_bytes(r.read())
        time.sleep(1.0)
    return json.loads(p.read_text())


def hourly_station(d: pd.DataFrame, t0_local: pd.Timestamp) -> list[dict]:
    """Report nearest each local hour 0..24 (within 30 min)."""
    out = []
    if d.empty:
        return [{"h": h} for h in range(25)]
    loc = d.valid + pd.Timedelta(hours=-6)
    for h in range(25):
        t = t0_local + pd.Timedelta(hours=h)
        dt = (loc - t).abs()
        k = dt.idxmin()
        if dt[k] > pd.Timedelta(minutes=30):
            out.append({"h": h})
            continue
        r = d.loc[k]
        sp = r.sknt * KT if pd.notna(r.sknt) else None
        out.append({"h": h, "ws": sp, "wd": float(r.drct) if pd.notna(r.drct) else None,
                    "gust": float(r.gust * KT) if pd.notna(r.gust) else None})
    return out


def circ(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def main():
    S = stations()
    diag = json.loads((K.OUT / "diagnose_rows.json").read_text())
    comm = [r for r in diag if not r.get("summary")]
    res = {}
    for fid, day, pop in K.all_days():
        case = K.load_case(fid, day)
        dom = case.domain
        rows = [r for r in comm if r["fire_day"] == f"{fid}:{day}"]
        import communities_c1b as CB

        reached, _ = CB.observed_intervals(case)
        cents = []
        for c in reached:
            rr, cc = np.nonzero(c["mask"])
            cents.append((dom.lat_max - (rr.mean() + 0.5) * dom.cell_lat, dom.lng_min + (cc.mean() + 0.5) * dom.cell_lng))
        clat, clng = np.mean([c[0] for c in cents]), np.mean([c[1] for c in cents])
        S["km"] = hav_km(clat, clng, S.lat.values, S.lng.values)
        near = S[S.km <= MAX_KM].sort_values("km").head(3)
        t0 = pd.Timestamp(case.start)
        t0u = t0 + pd.Timedelta(hours=6)
        m = json.loads((K.VD / "c1b/manifest.json").read_text())
        wp = next(x for x in m if x["fire_id"] == fid)["weather_point"]
        g = era5_gusts(wp[0], wp[1], case.start.date().isoformat())
        gt = pd.to_datetime(g["hourly"]["time"])
        era = []
        for h in range(25):
            t = t0 + pd.Timedelta(hours=h)
            k = np.where(gt == t)[0]
            hw = case.hourly[h] if h < len(case.hourly) else None
            era.append({"h": h, "ws": hw.wind_speed if hw else None, "wd": hw.wind_direction if hw else None,
                        "gust": g["hourly"]["wind_gusts_10m"][k[0]] if len(k) else None})
        sts = []
        for _, s in near.iterrows():
            d = fetch_station(s.sid, t0u - pd.Timedelta(hours=2), t0u + pd.Timedelta(hours=27))
            hs = hourly_station(d, t0)
            both = [(e, o) for e, o in zip(era, hs) if o.get("ws") is not None and e.get("ws") is not None and e["h"] <= 24]
            dirs = [circ(o["wd"], e["wd"]) for e, o in both if o.get("wd") is not None and o["ws"] >= 5]
            bp = [(e, o) for e, o in both if 4 <= e["h"] < 14]
            sts.append({"sid": s.sid, "name": s["name"], "km": round(float(s.km), 1), "lat": s.lat, "lng": s.lng,
                        "n_valid": len(both),
                        "ws_station_mean": float(np.mean([o["ws"] for _, o in both])) if both else None,
                        "ws_era5_mean": float(np.mean([e["ws"] for e, _ in both])) if both else None,
                        "ws_bias_station_minus_era5": float(np.mean([o["ws"] - e["ws"] for e, o in both])) if both else None,
                        "ws_bias_bp": float(np.mean([o["ws"] - e["ws"] for e, o in bp])) if bp else None,
                        "dir_err_mean_deg": float(np.mean(dirs)) if dirs else None, "n_dir": len(dirs),
                        "gust_station_max": max([o["gust"] for o in hs if o.get("gust")], default=None),
                        "ws_station_max": max([o["ws"] for o in hs if o.get("ws") is not None], default=None),
                        "hourly": hs})
        # per community angles (burning-period hours), ERA5 vs nearest station with data
        ang = []
        best = next((s for s in sts if s["n_valid"] >= 16), None)
        for r in rows:
            b = r["bearing_deg"]
            ae = [circ((e["wd"] + 180) % 360, b) for e in era if 4 <= e["h"] < 14 and e["wd"] is not None]
            ao = ([circ((o["wd"] + 180) % 360, b) for o in best["hourly"] if 4 <= o["h"] < 14 and o.get("wd") is not None
                   and o.get("ws") is not None and o["ws"] >= 5] if best else [])
            ang.append({"name": r["name"], "bearing_deg": b, "angle_era5_bp_deg": float(np.mean(ae)) if ae else None,
                        "angle_station_bp_deg": float(np.mean(ao)) if ao else None, "station": best["sid"] if best else None})
        res[f"{fid}:{day}"] = {"population": pop, "centroid": [clat, clng], "weather_point": wp, "era5": era,
                               "era5_gust_max": max([e["gust"] for e in era if e["gust"] is not None], default=None),
                               "era5_ws_max": max([e["ws"] for e in era if e["ws"] is not None], default=None),
                               "stations": sts, "series_station": best["sid"] if best else None,
                               "community_angles": ang}
        print(fid, day, [(s["sid"], s["km"], s["n_valid"], s["ws_bias_station_minus_era5"] and round(s["ws_bias_station_minus_era5"], 1),
                          s["dir_err_mean_deg"] and round(s["dir_err_mean_deg"])) for s in sts], flush=True)
    (K.OUT / "weather_obs.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
