"""Build FireSim harness inputs for the R71 development fires (v8 training split, US and Canada).

Purpose: put the open v8 *training* fires (PREREG_LR8 §3: "may train", never decide; 105 US and 61 Canadian
training fires with built v8 inputs) through the same input pipeline the sealed 2026 confirm test used
([R67], ``scripts/validation/sealed2026/confirm_inputs.py`` on ``feat/sealed-confirm-h1h2``), so that the US
over-prediction can be decomposed on fires that are not held out. No sealed or decision fire is read.

Per fire (``<fid>`` = ``<year>_U<nnn>`` US, ``<year>_K<nnn>`` Canada; the key map is in ``manifest.json``):

- **Grid**: regular lat/lng, final-perimeter bounds + 15 km, 90 m or the first of 180/270/360 m keeping
  <= 1.2 M cells, else 450 m (the ``validate.choose_res`` rule, as R67).
- **Day of burning (DOB)**: VIIRS 375 m active fires (FIRMS archive, S-NPP + NOAA-20; the v8 training
  extraction ``viirs_training_<year>.pkl.gz``) inside the final perimeter buffered 375 m. Burn day = date of
  (local solar time - 12 h), so the ~13:30 day pass of day D and the ~01:30 night pass after it are both day D
  (the FEDS step rule of R67 applied to acquisition times). Every cell whose centre is inside the final
  perimeter takes the burn day of the nearest kept detection (Parks 2014). Sidecars: ``dob_det`` (only cells
  within 187.5 m of a detection; scoring-geometry check) and, for US fires of 2018-2021, ``dob_feds`` (NASA
  FEDS archive new-fire pixels, Chen et al. 2022, with exactly the R67 rule).
- **Fuel**: the v8 per-fire grid (125 m UTM; US = LANDFIRE FBFM40 of the latest release before the fire,
  crosswalked as v7/R67; Canada = CFS FBP 2024 back-cast), nearest onto the grid; sidecar ``fbfm`` keeps the
  raw FBFM40 code (US) for the crosswalk variants of ``common.CROSSWALKS``.
- **Curing**: sidecar scalar ``ndvi_curing`` = median of the v8 NDVI curing (MOD13Q1, July-August of the year
  before) over grass cells inside the perimeter (percent); used only by the curing variant.
- **Elevation**: US LANDFIRE LF2020 Elev (LFPS, bilinear, as R67); Canada NRCan MRDEM-30 (as validate.py).
- **Weather**: ERA5 hourly at the grid centre (Open-Meteo archive, local clock time of the point, as R67) and
  daily FWI rows at local standard noon from 1 April (FFMC 85, DMC 6, DC 15). Station alternative: the v8
  station (IEM ASOS, nearest eligible, hourly T/RH/wind; ERA5 precipitation) in local time, ERA5 filling hours
  the station lacks, with its own daily FWI rows computed the same way.
- **Burn-day window**: detections with burn day from discovery (MTBS/NBAC start date) - 1 d to the end of the v8
  detection window + 1 d (R67: discovery - 1 d to end + 1 d).
- **Fire-days**: ``fire_day_pairs`` harness defaults; at most 3 per fire, ``random.Random(f"2026102901:{fid}")``.
- **Suppression attributes** (US): the WFIGS 2018-2025 record matched to the MTBS start (discovery within 5 d,
  initial point within 10 km): discovery, containment, control, out times.

Output root ``$FIRESIM_R71_DATA`` (default ``~/dev/wildfire/validation-data/us-transfer-r71``): ``domains/``,
``sidecar/<fid>.npz``, ``weather/`` (ERA5 cache), ``station/<fid>.json``, ``groups/<fid>_{era5,stn}.json``,
``inputs/<fid>.json`` (build record), ``manifest.json``.

Usage: ``PYTHONPATH=engine/src python3 -m scripts.validation.us_transfer.build_dev [--region US|CAN] [--limit N]``
(run from the repository root, under capjob).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import pickle
import random
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import common as C

LFPS = "https://lfps.usgs.gov/arcgis/rest/services/{svc}/ImageServer/exportImage"
ELEV_SVC = "Landfire_Topo/LF2020_Elev_CONUS"
MRDEM = "/vsicurl/https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"
FEDS_ARCH = "https://openveda.cloud/api/features/collections/public.eis_fire_lf_newfirepix_archive/items"
OPENMETEO = "https://archive-api.open-meteo.com/v1/archive"
BUFFER_M, PIX_BUFFER_M, MAX_CELLS, MAX_DAYS, DAY_SEED = 15000.0, 375.0, 1_200_000, 3, 2026102901


# ── fires ──────────────────────────────────────────────────────────────────────────────────────

def training_fires(region: str) -> list[dict]:
    """Training (not legacy) fires of the v8 split with built v8 inputs, sorted by key."""
    import geopandas as gpd

    s = pd.read_csv(C.SPLIT)
    s = s[(s.assignment == "training") & (s.region == region)]
    per = gpd.read_file(C.V8 / "viirs_training/training_perimeters.gpkg")
    per = per.set_index("fire_key")
    out = []
    for k in sorted(s.fire_key):
        d = C.V8 / "fires" / k.replace(":", "_")
        if not (d / "inputs.npz").exists() or k not in per.index:
            continue
        p = per.loc[k]
        out.append({"fire_key": k, "region": region, "year": int(p.year), "t0": pd.Timestamp(p.t0),
                    "t1": None if pd.isna(p.t1) else pd.Timestamp(p.t1), "geometry": p.geometry,
                    "v8_dir": d})
    return out


def viirs_points(key: str, year: int) -> pd.DataFrame:
    df = pickle.load(gzip.open(C.V8 / f"viirs_training/viirs_training_{year}.pkl.gz"))
    return df[df.fire_key == key]


# ── grid and rasters ───────────────────────────────────────────────────────────────────────────

def make_grid(geom, proj: int) -> dict:
    import geopandas as gpd
    from firesim.validation.cfsds import M_PER_DEG, domain_grid

    b = gpd.GeoSeries([geom], crs=4326).to_crs(proj).total_bounds
    h, w = (b[3] - b[1]) + 2 * BUFFER_M, (b[2] - b[0]) + 2 * BUFFER_M
    res = next((r for r in (90.0, 180.0, 270.0, 360.0) if (h / r) * (w / r) <= MAX_CELLS), 450.0)
    lng_min, lat_min, lng_max, lat_max = geom.bounds
    mid = 0.5 * (lat_min + lat_max)
    blat = BUFFER_M / M_PER_DEG
    blng = BUFFER_M / (M_PER_DEG * math.cos(math.radians(mid)))
    lat_min, lat_max, lng_min, lng_max = lat_min - blat, lat_max + blat, lng_min - blng, lng_max + blng
    rows, cols, cell_lat, cell_lng = domain_grid(lat_min, lat_max, lng_min, lng_max, res)
    return dict(res_m=res, rows=rows, cols=cols, cell_lat=cell_lat, cell_lng=cell_lng,
                lat_max=lat_max, lng_min=lng_min)


def dst_transform(g: dict):
    from rasterio.transform import from_origin

    return from_origin(g["lng_min"], g["lat_max"], g["cell_lng"], g["cell_lat"])


def from_v8(arr: np.ndarray, meta: dict, g: dict, nodata) -> np.ndarray:
    """A v8 125 m UTM grid onto the lat/lng grid (nearest)."""
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject

    gr = meta["grid"]
    out = np.full((g["rows"], g["cols"]), nodata, dtype=arr.dtype)
    reproject(arr, out, src_transform=from_origin(gr["x0"], gr["y1"], gr["cell"], gr["cell"]),
              src_crs=gr["crs"], src_nodata=None, dst_transform=dst_transform(g), dst_crs="EPSG:4326",
              dst_nodata=nodata, resampling=Resampling.nearest)
    return out


def lfps_export(svc: str, g: dict, interp: str, step: int = 2000) -> np.ndarray:
    """A LANDFIRE image-service layer on the lat/lng grid (EPSG:4326 export, tiled; from R67)."""
    import rasterio

    out = np.zeros((g["rows"], g["cols"]), np.float32)
    for j0 in range(0, g["rows"], step):
        for i0 in range(0, g["cols"], step):
            h, w = min(step, g["rows"] - j0), min(step, g["cols"] - i0)
            x0 = g["lng_min"] + i0 * g["cell_lng"]
            y1 = g["lat_max"] - j0 * g["cell_lat"]
            bb = (x0, y1 - h * g["cell_lat"], x0 + w * g["cell_lng"], y1)
            q = {"bbox": ",".join(f"{v:.8f}" for v in bb), "bboxSR": 4326, "imageSR": 4326,
                 "size": f"{w},{h}", "format": "tiff", "pixelType": "F32", "interpolation": interp,
                 "f": "image"}
            data = C.http(LFPS.format(svc=svc) + "?" + urllib.parse.urlencode(q))
            with rasterio.MemoryFile(data) as mf, mf.open() as ds:
                a = ds.read(1)
                nod = ds.nodata
            if nod is not None:
                a = np.where(a == nod, np.nan, a)
            out[j0:j0 + h, i0:i0 + w] = a
    return out


def mrdem(g: dict) -> np.ndarray:
    import rasterio
    from rasterio.warp import Resampling, reproject

    out = np.full((g["rows"], g["cols"]), np.nan, np.float32)
    with rasterio.open(MRDEM, overview_level=0) as src:
        reproject(rasterio.band(src, 1), out, dst_transform=dst_transform(g), dst_crs="EPSG:4326",
                  dst_nodata=np.nan, resampling=Resampling.bilinear)
    return out


def cell_centres(g: dict):
    lat = g["lat_max"] - (np.arange(g["rows"]) + 0.5) * g["cell_lat"]
    lng = g["lng_min"] + (np.arange(g["cols"]) + 0.5) * g["cell_lng"]
    return lat, lng


# ── DOB ────────────────────────────────────────────────────────────────────────────────────────

def burn_day_solar(t_utc: pd.Series, lon: pd.Series) -> np.ndarray:
    """Burn day (day of year) = date of (local solar time - 12 h)."""
    loc = pd.to_datetime(t_utc) + pd.to_timedelta(lon / 15.0, unit="h") - pd.Timedelta(hours=12)
    return loc.dt.dayofyear.to_numpy(np.int32), loc.dt.year.to_numpy(np.int32)


def nn_dob(geom, g: dict, x: np.ndarray, y: np.ndarray, d: np.ndarray, det_radius_m: float | None = None):
    """Nearest-detection DOB on cells inside ``geom`` (and, optionally, detected-only cells)."""
    import geopandas as gpd
    from rasterio.features import rasterize
    from scipy.spatial import cKDTree
    from shapely import contains_xy
    from firesim.validation.cfsds import M_PER_DEG

    buf = gpd.GeoSeries([geom], crs=4326).to_crs(5070).buffer(PIX_BUFFER_M).to_crs(4326).iloc[0]
    keep = contains_xy(buf, x, y)
    x, y, d = x[keep], y[keep], d[keep]
    inside = rasterize([(geom, 1)], out_shape=(g["rows"], g["cols"]), transform=dst_transform(g), fill=0,
                       all_touched=False, dtype="uint8").astype(bool)
    dob = np.zeros((g["rows"], g["cols"]), np.int32)
    det = np.zeros_like(dob)
    st = {"pixels_kept": int(len(x)), "perimeter_cells": int(inside.sum())}
    if len(x) == 0 or not inside.any():
        return dob, det, st
    coslat = math.cos(math.radians(g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]))
    tree = cKDTree(np.column_stack([x * coslat * M_PER_DEG, y * M_PER_DEG]))
    lat, lng = cell_centres(g)
    rr, cc = np.nonzero(inside)
    q = np.column_stack([lng[cc] * coslat * M_PER_DEG, lat[rr] * M_PER_DEG])
    dist, idx = tree.query(q)
    dob[rr, cc] = d[idx]
    st["nearest_pixel_m"] = {"median": float(np.median(dist)), "p95": float(np.percentile(dist, 95))}
    if det_radius_m:
        # detected-only: earliest burn day among detections within the radius (cells anywhere)
        lat2, lng2 = np.meshgrid(lat, lng, indexing="ij")
        allq = np.column_stack([lng2.ravel() * coslat * M_PER_DEG, lat2.ravel() * M_PER_DEG])
        near = tree.query_ball_point(allq, det_radius_m)
        flat = det.ravel()
        for k, lst in enumerate(near):
            if lst:
                flat[k] = int(d[lst].min())
        det = flat.reshape(det.shape)
    return dob, det, st


def feds_dob(geom, g: dict, d0: date, d1: date, cache: Path):
    """FEDS archive new-fire pixels with the R67 rule (burn day = date of t - 12 h), NN in perimeter."""
    if cache.exists():
        pts = json.loads(cache.read_text())
    else:
        lng_min, lat_min, lng_max, lat_max = geom.bounds
        q = {"bbox": f"{lng_min - .05:.5f},{lat_min - .05:.5f},{lng_max + .05:.5f},{lat_max + .05:.5f}",
             "datetime": f"{d0 - timedelta(days=2)}T00:00:00Z/{d1 + timedelta(days=3)}T00:00:00Z", "limit": 500}
        url = FEDS_ARCH + "?" + urllib.parse.urlencode(q)
        pts = []
        while url:
            dd = json.loads(C.http(url))
            for f in dd.get("features", []):
                p, geo = f["properties"], f["geometry"]
                coords = geo["coordinates"] if geo["type"] == "MultiPoint" else [geo["coordinates"]]
                pts += [(float(a), float(b), p["t"]) for a, b in coords]
            url = next((ln["href"] for ln in dd.get("links", []) if ln.get("rel") == "next"), None)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(pts))
    if not pts:
        return np.zeros((g["rows"], g["cols"]), np.int32), {"feds_pixels": 0}
    x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
    bd = [(datetime.fromisoformat(p[2]) - timedelta(hours=12)).date() for p in pts]
    ok = np.array([d0 - timedelta(days=1) <= b <= d1 + timedelta(days=1) for b in bd])
    doy = np.array([b.timetuple().tm_yday for b in bd], np.int32)
    dob, _, st = nn_dob(geom, g, x[ok], y[ok], doy[ok])
    st["feds_pixels"] = len(pts)
    return dob, st


# ── weather ────────────────────────────────────────────────────────────────────────────────────

def openmeteo(lat, lng, start: date, end: date, tz: str, cache: Path) -> dict:
    if cache.exists():
        return json.loads(cache.read_text())
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lng:.4f}", "start_date": start.isoformat(),
         "end_date": end.isoformat(), "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,"
         "wind_direction_10m,precipitation", "timezone": tz, "models": "era5", "wind_speed_unit": "kmh"}
    d = json.loads(C.http(OPENMETEO + "?" + urllib.parse.urlencode(q)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d))
    return d


def daily_fwi(df_utc: pd.DataFrame, tz: str, start: date, end: date, fid: str, lat, lng) -> list[dict]:
    """Daily FWI rows at local standard noon (24 h rain to noon) from an hourly UTC frame
    (columns temp, rh, ws, rain), FWICalculator from ``start`` with FFMC 85, DMC 6, DC 15."""
    from firesim.fwi.calculator import FWICalculator

    z = ZoneInfo(tz)
    calc = FWICalculator()
    rows = []
    day = start
    while day <= end:
        loc = datetime(day.year, day.month, day.day, 12, tzinfo=z)
        std_off = loc.utcoffset() - (loc.dst() or timedelta(0))
        noon = pd.Timestamp(datetime(day.year, day.month, day.day, 12) - std_off)
        if noon not in df_utc.index:
            raise RuntimeError(f"{fid}: no weather at {noon}")
        r0 = df_utc.loc[noon]
        rain = float(df_utc.loc[noon - pd.Timedelta(hours=23):noon, "rain"].fillna(0).sum())
        r = calc.calculate_daily(float(r0.temp), float(r0.rh), float(r0.ws), rain, day.month)
        rows.append({"ID": fid, "DOB": day.timetuple().tm_yday, "date": day.isoformat(), "ffmc": r.ffmc,
                     "dmc": r.dmc, "dc": r.dc, "isi": r.isi, "bui": r.bui, "fwi": r.fwi,
                     "temp_noon": float(r0.temp), "rh_noon": float(r0.rh), "ws_noon": float(r0.ws),
                     "rain24": rain, "lat": lat, "lon": lng})
        day += timedelta(days=1)
    return rows


def era5_utc_frame(d: dict) -> pd.DataFrame:
    h = d["hourly"]
    df = pd.DataFrame({"temp": h["temperature_2m"], "rh": h["relative_humidity_2m"], "ws": h["wind_speed_10m"],
                       "wd": h["wind_direction_10m"], "rain": h["precipitation"]},
                      index=pd.to_datetime(h["time"]))
    return df.astype(float)


def station_frame(v8dir: Path, meta: dict) -> tuple[pd.DataFrame | None, str | None]:
    sid = (meta.get("zone_station") or [None])[0]
    if not sid or not (v8dir / f"wx_{sid}.csv").exists():
        return None, None
    s = pd.read_csv(v8dir / f"wx_{sid}.csv", parse_dates=["utc"]).set_index("utc")
    return s[["temp", "rh", "ws", "wd", "rain"]].astype(float), sid


def to_local_records(df_utc: pd.DataFrame, tz: str) -> dict:
    """Open-Meteo-shaped local-time hourly dict from a UTC frame (for ``parse_open_meteo``)."""
    loc = df_utc.copy()
    loc.index = loc.index.tz_localize("UTC").tz_convert(tz).tz_localize(None)
    loc = loc[~loc.index.duplicated(keep="first")]  # DST fall-back hour
    return {"hourly": {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in loc.index],
                       "temperature_2m": loc.temp.tolist(), "relative_humidity_2m": loc.rh.tolist(),
                       "wind_speed_10m": loc.ws.tolist(), "wind_direction_10m": loc.wd.tolist(),
                       "precipitation": loc.rain.tolist()}}


# ── WFIGS suppression attributes ───────────────────────────────────────────────────────────────

def wfigs_index() -> list[dict]:
    d = json.loads((C.V8 / "raw/wfigs_ge5000ha_2018_2025_attrs.json").read_text())
    return [f["attributes"] for f in d["features"]]


def wfigs_match(key: str, t0: pd.Timestamp, recs: list[dict]) -> dict | None:
    """WFIGS record for an MTBS key (start point and date in the key; WFIGS:<id> keys match by id)."""
    def ms(v):
        return None if v in (None, "") else datetime.fromtimestamp(v / 1000, timezone.utc).replace(tzinfo=None)
    if key.startswith("WFIGS:"):
        uid = key.split(":", 1)[1].lower()
        hit = [r for r in recs if str(r.get("attr_UniqueFireIdentifier", "")).lower() == uid]
    else:
        s = key.split(":", 1)[1]
        lat, lon = float(s[2:7]) / 1000, -float(s[7:13]) / 1000
        hit = []
        for r in recs:
            la, lo, dd = r.get("attr_InitialLatitude"), r.get("attr_InitialLongitude"), ms(r.get("attr_FireDiscoveryDateTime"))
            if la is None or lo is None or dd is None:
                continue
            dkm = math.hypot((la - lat) * 111.2, (lo - lon) * 111.2 * math.cos(math.radians(lat)))
            if dkm <= 10 and abs((dd - t0.to_pydatetime()).days) <= 5:
                hit.append((dkm, r))
        hit = [r for _, r in sorted(hit, key=lambda x: x[0])]
    if not hit:
        return None
    r = hit[0]
    return {k: (ms(r.get(k)).isoformat() if ms(r.get(k)) else None) for k in
            ("attr_FireDiscoveryDateTime", "attr_ContainmentDateTime", "attr_ControlDateTime",
             "attr_FireOutDateTime")} | {"acres": r.get("poly_GISAcres"),
                                         "pct_contained": r.get("attr_PercentContained")}


# ── build ──────────────────────────────────────────────────────────────────────────────────────

def build_fire(rec: dict, fid: str, root: Path, wf: list[dict]) -> dict:
    from firesim.validation.cfsds import FireDomain, fire_day_pairs
    from firesim.validation.start_strata import start_type
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    t_start = time.time()
    meta = json.loads((rec["v8_dir"] / "meta.json").read_text())
    v8 = np.load(rec["v8_dir"] / "inputs.npz")
    us = rec["region"] == "US"
    g = make_grid(rec["geometry"], 5070 if us else 3978)
    info = {"fire_id": fid, "fire_key": rec["fire_key"], "region": rec["region"], "year": rec["year"],
            "grid": g, "t0": str(rec["t0"]), "t1": str(rec["t1"])}
    pts = viirs_points(rec["fire_key"], rec["year"])
    bd, by = burn_day_solar(pts.t, pts.longitude)
    # burn days from discovery - 1 d to the end of the v8 detection window + 1 d (as R67: discovery - 1 to end + 1)
    d_lo = (rec["t0"] - pd.Timedelta(days=1)).dayofyear
    w_end = pd.Timestamp(meta["detection_window"][1]) if meta.get("detection_window") else None
    d_hi = (w_end + pd.Timedelta(days=1)).dayofyear if w_end is not None and w_end.year == rec["year"] else 366
    sel = (by == rec["year"]) & (bd >= d_lo) & (bd <= d_hi)
    info["dob_window_doy"] = [int(d_lo), int(d_hi)]
    dob, det, st = nn_dob(rec["geometry"], g, pts.longitude.to_numpy()[sel], pts.latitude.to_numpy()[sel],
                          bd[sel], det_radius_m=187.5)
    info["dob"] = st
    if not (dob > 0).any():
        info["status"] = "no VIIRS detection inside the perimeter"
        return {"info": info}
    fuel = from_v8(v8["code"].astype(np.int32), meta, g, C.NONFUEL)
    side = {"dob_det": det}
    if us:
        fb = from_v8(v8["fbfm40"].astype(np.int32), meta, g, 0)
        side["fbfm"] = fb
        grass = np.isin(v8["fbfm40"], C.GR + C.GS)
    else:
        grass = np.isin(v8["code"], [C.O1A, C.O1B])
    cur = v8["curing"]
    # grass cells of the v8 grid inside the final perimeter
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    import geopandas as gpd

    gr = meta["grid"]
    pin = rasterize([(gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(gr["crs"]).iloc[0], 1)],
                    out_shape=cur.shape, transform=from_origin(gr["x0"], gr["y1"], gr["cell"], gr["cell"]),
                    fill=0, dtype="uint8").astype(bool)
    m = grass & pin & (cur > 0)
    info["ndvi_curing"] = float(np.median(cur[m]) * 100) if m.sum() >= 10 else None
    info["ndvi_curing_n"] = int(m.sum())
    elev = lfps_export(ELEV_SVC, g, "RSP_BilinearInterpolation") if us else mrdem(g)
    if np.isnan(elev).all():
        elev = None
    elif np.isnan(elev).any():
        elev = np.where(np.isnan(elev), np.nanmean(elev), elev).astype(np.float32)
    clat = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
    clng = g["lng_min"] + 0.5 * g["cols"] * g["cell_lng"]
    tz = openmeteo(clat, clng, date(rec["year"], 7, 1), date(rec["year"], 7, 1), "auto",
                   root / "inputs" / f"{fid}_tz.json")["timezone"]
    info["timezone"] = tz
    burned = dob > 0
    u, n = np.unique(fuel[burned], return_counts=True)
    info["fuel_share_in_perimeter"] = {str(int(a)): float(b) / burned.sum() for a, b in zip(u, n)}
    dom = FireDomain(fire_id=fid, year=rec["year"], dob=dob, fuel=fuel, elevation=elev, lat_max=g["lat_max"],
                     lng_min=g["lng_min"], cell_lat=g["cell_lat"], cell_lng=g["cell_lng"],
                     fuel_scheme="cfs_national",
                     meta={"res_m": g["res_m"], "buffer_m": BUFFER_M, "timezone": tz,
                           "dob_source": "FIRMS VIIRS 375 m (v8 training extraction), nearest detection in the final perimeter",
                           "fuel_source": meta.get("fuel_source", ""), "fire_key": rec["fire_key"]})
    (root / "domains").mkdir(parents=True, exist_ok=True)
    dpath = root / "domains" / f"{fid}.npz"
    dom.save(dpath)
    pairs = fire_day_pairs(dom)
    days = [p.day for p in pairs]
    if len(days) > MAX_DAYS:
        days = sorted(random.Random(f"{DAY_SEED}:{fid}").sample(days, MAX_DAYS))
    burn_days = dom.burn_days()
    w0, w1 = doy_to_date(rec["year"], burn_days[0]) - timedelta(days=1), doy_to_date(rec["year"], burn_days[-1]) + timedelta(days=2)
    fetch_era5_hourly(clat, clng, w0, w1, root / "weather", timezone=tz)
    t0d = rec["t0"].date()
    f_start = min(date(rec["year"], 4, 1), t0d - timedelta(days=30))
    f_end = doy_to_date(rec["year"], burn_days[-1]) + timedelta(days=1)
    e5 = era5_utc_frame(openmeteo(clat, clng, f_start, f_end + timedelta(days=2), "GMT",
                                  root / "inputs" / f"{fid}_era5_utc.json"))
    rows_e = daily_fwi(e5, tz, f_start, f_end, fid, clat, clng)
    (root / "groups").mkdir(exist_ok=True)
    (root / "groups" / f"{fid}_era5.json").write_text(json.dumps(rows_e))
    stn, sid = station_frame(rec["v8_dir"], meta)
    info["station"] = sid
    if stn is not None:
        stn = stn[~stn.index.duplicated()]
        merged = e5.copy()
        common = merged.index.intersection(stn.index)
        have = stn.loc[common, ["temp", "rh", "ws", "wd"]].notna().all(axis=1)
        cidx = common[have.to_numpy()]
        merged.loc[cidx, ["temp", "rh", "ws", "wd"]] = stn.loc[cidx, ["temp", "rh", "ws", "wd"]].to_numpy()
        merged["stn_obs"] = merged.index.isin(cidx)
        win = merged.loc[pd.Timestamp(w0):pd.Timestamp(w1)]
        info["station_hour_share"] = float(win.stn_obs.mean()) if len(win) else 0.0
        info["station_km"] = next((s["dist_km"] for s in meta.get("stations", []) if s["sid"] == sid), None)
        rows_s = daily_fwi(merged, tz, f_start, f_end, fid, clat, clng)
        (root / "groups" / f"{fid}_stn.json").write_text(json.dumps(rows_s))
        (root / "station").mkdir(exist_ok=True)
        (root / "station" / f"{fid}.json").write_text(json.dumps(to_local_records(merged.loc[pd.Timestamp(w0) - pd.Timedelta(days=1):pd.Timestamp(w1) + pd.Timedelta(days=1)], tz)))
    if us and rec["year"] <= 2021:
        d1 = (rec["t1"].date() if rec["t1"] is not None else doy_to_date(rec["year"], burn_days[-1]))
        fd, fst = feds_dob(rec["geometry"], g, t0d, max(d1, doy_to_date(rec["year"], burn_days[-1])),
                           root / "inputs" / f"{fid}_feds.json")
        side["dob_feds"] = fd
        info["feds"] = fst
    if us:
        info["wfigs"] = wfigs_match(rec["fire_key"], rec["t0"], wf)
    (root / "sidecar").mkdir(exist_ok=True)
    np.savez_compressed(root / "sidecar" / f"{fid}.npz", **side)
    tags = {d: start_type(dom.dob, d, dom.cell_area_m2)["start_type"] for d in days}
    info.update(status="ok", eligible_pairs=len(pairs), days=days, start_types=tags,
                burn_days=[int(burn_days[0]), int(burn_days[-1])], domain_sha256=C.sha256(dpath),
                seconds=round(time.time() - t_start, 1))
    man = {"fire_id": fid, "fire_key": rec["fire_key"], "region": rec["region"], "year": rec["year"],
           "area_ha": float(burned.sum()) * dom.cell_area_m2 / 1e4, "res_m": g["res_m"],
           "grid": [dom.rows, dom.cols], "days": days, "weather_point": [clat, clng],
           "weather_range": [int(burn_days[0]), int(burn_days[-1])], "timezone": tz,
           "station": sid, "ndvi_curing": info["ndvi_curing"], "t0": str(rec["t0"].date())}
    return {"info": info, "manifest": man}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", choices=("US", "CAN"), nargs="+", default=["US", "CAN"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    root = C.DATA
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    mpath = root / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else []
    done = {m["fire_key"] for m in manifest}
    tried = {json.loads(p.read_text()).get("fire_key") for p in (root / "inputs").glob("*_?[0-9][0-9][0-9].json")}
    wf = wfigs_index()
    for region in a.region:
        recs = training_fires(region)
        tag = "U" if region == "US" else "K"
        for i, rec in enumerate(recs[: a.limit or None], 1):
            fid = f"{rec['year']}_{tag}{i:03d}"
            if rec["fire_key"] in done or rec["fire_key"] in tried:
                continue
            try:
                out = build_fire(rec, fid, root, wf)
            except Exception as e:  # recorded; a fire whose inputs cannot be built contributes nothing
                out = {"info": {"fire_id": fid, "fire_key": rec["fire_key"], "status": f"error {e!r}"[:400]}}
            (root / "inputs" / f"{fid}.json").write_text(json.dumps(out["info"], indent=1, default=str))
            if "manifest" in out and out["manifest"]["days"]:
                manifest.append(out["manifest"])
                mpath.write_text(json.dumps(manifest, indent=1))
            print(fid, out["info"].get("status"), out["info"].get("days"), flush=True)
    print("fires with days:", len(manifest), "fire-days:", sum(len(m["days"]) for m in manifest))


if __name__ == "__main__":
    sys.exit(main())
