"""Build harness inputs for the PREREG_US_TRANSFER1 held-out fires (run only after the main session says go).

Purpose: the same input pipeline as the R71 development fires (``build_dev.py``), applied blind to the ``test``
subset of ``validation-data/us-transfer-heldout/heldout_us_2017.csv`` (sha256-checked first), so that
``sens_run.py`` can run ``base``, ``fix1``, ``ens_base`` and ``ens_fix1`` on them with ``FIRESIM_R71_DATA`` pointing at
the output root. Every choice is frozen in docs/PREREG_US_TRANSFER1.md §4:

- perimeter: the MTBS burned-area boundary of the draw (``heldout_us_2017_perimeters.geojson``);
- DOB: VIIRS 375 m S-NPP (FIRMS yearly archive ``viirs-snpp_2017_United_States.csv``, all confidence classes),
  detections inside the perimeter + 375 m, burn day = date of (local solar time - 12 h), from ignition date - 1 d to
  ignition date + 120 d; nearest detection fills every perimeter cell (Parks 2014);
- grid, ERA5 hourly and daily FWI rows, elevation (LF2020 Elev): exactly as ``build_dev.py``;
- fuel: LANDFIRE LF2016 FBFM40 CONUS (LFPS; disturbances through 2016, pre-fire for 2017), nearest onto the grid,
  crosswalked by ``common.crosswalk(.., 'base')`` (the v7/R67 crosswalk); the raw codes go to the sidecar for FIX1;
- fire-days: ``fire_day_pairs`` harness defaults, at most 3 per fire, ``random.Random(f"2026102912:{fid}")``.
Fire IDs are anonymous ``2017_H<order>``.

Output root ``~/dev/wildfire/validation-data/us-transfer-heldout/build/`` (same layout as the development root).
Usage: ``PYTHONPATH=engine/src python3 -m scripts.validation.us_transfer.heldout_inputs --go`` (``--go`` is required).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import common as C
from . import build_dev as B

HO = C.W / "validation-data/us-transfer-heldout"
ROOT = HO / "build"
FIRMS = "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2017/viirs-snpp_2017_United_States.csv"
FBFM_SVC = "Landfire_LF2016/LF2016_FBFM40_CONUS"
DAY_SEED = 2026102912


def records() -> list[dict]:
    import geopandas as gpd

    about = json.loads((HO / "_about.json").read_text())
    for name in ("heldout_us_2017.csv", "heldout_us_2017_perimeters.geojson"):
        if C.sha256(HO / name) != about["sha256"][name]:
            raise SystemExit(f"{name}: sha256 differs from the draw record")
    lst = pd.read_csv(HO / "heldout_us_2017.csv")
    lst = lst[lst.subset == "test"].sort_values("order")
    g = gpd.read_file(HO / "heldout_us_2017_perimeters.geojson").set_index("fire_key")
    return [{"fire_id": f"2017_H{int(r.order):02d}", "fire_key": r.fire_key, "year": 2017,
             "t0": pd.Timestamp(r.ig_date), "geometry": g.loc[r.fire_key].geometry} for r in lst.itertuples()]


def firms_points() -> pd.DataFrame:
    p = ROOT / "raw" / "viirs-snpp_2017_United_States.csv"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(C.http(FIRMS, timeout=1200))
    df = pd.read_csv(p, usecols=["latitude", "longitude", "acq_date", "acq_time"])
    hh = df.acq_time.astype(int).astype(str).str.zfill(4)
    df["t"] = pd.to_datetime(df.acq_date + " " + hh.str[:2] + ":" + hh.str[2:])
    return df


def build_fire(rec: dict, pts: pd.DataFrame) -> dict:
    from firesim.validation.cfsds import FireDomain, fire_day_pairs
    from firesim.validation.start_strata import start_type
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    t_start = time.time()
    fid = rec["fire_id"]
    g = B.make_grid(rec["geometry"], 5070)
    info = {"fire_id": fid, "fire_key": rec["fire_key"], "region": "US", "year": 2017, "grid": g, "t0": str(rec["t0"])}
    lng_min, lat_min, lng_max, lat_max = rec["geometry"].bounds
    p = pts[(pts.longitude >= lng_min - .05) & (pts.longitude <= lng_max + .05) &
            (pts.latitude >= lat_min - .05) & (pts.latitude <= lat_max + .05)]
    bd, by = B.burn_day_solar(p.t, p.longitude)
    d_lo, d_hi = (rec["t0"] - pd.Timedelta(days=1)).dayofyear, min((rec["t0"] + pd.Timedelta(days=120)).dayofyear, 365)
    sel = (by == 2017) & (bd >= d_lo) & (bd <= d_hi)
    dob, det, st = B.nn_dob(rec["geometry"], g, p.longitude.to_numpy()[sel], p.latitude.to_numpy()[sel], bd[sel],
                            det_radius_m=187.5)
    info["dob"] = st
    if not (dob > 0).any():
        info["status"] = "no VIIRS detection inside the perimeter"
        return {"info": info}
    fb = B.lfps_export(FBFM_SVC, g, "RSP_NearestNeighbor")
    fbi = np.where(np.isfinite(fb), fb, 0).astype(np.int32)
    fuel = C.crosswalk(fbi, "base")
    elev = B.lfps_export(B.ELEV_SVC, g, "RSP_BilinearInterpolation")
    if np.isnan(elev).all():
        elev = None
    elif np.isnan(elev).any():
        elev = np.where(np.isnan(elev), np.nanmean(elev), elev).astype(np.float32)
    clat = g["lat_max"] - 0.5 * g["rows"] * g["cell_lat"]
    clng = g["lng_min"] + 0.5 * g["cols"] * g["cell_lng"]
    tz = B.openmeteo(clat, clng, date(2017, 7, 1), date(2017, 7, 1), "auto", ROOT / "inputs" / f"{fid}_tz.json")["timezone"]
    dom = FireDomain(fire_id=fid, year=2017, dob=dob, fuel=fuel, elevation=elev, lat_max=g["lat_max"], lng_min=g["lng_min"],
                     cell_lat=g["cell_lat"], cell_lng=g["cell_lng"], fuel_scheme="cfs_national",
                     meta={"res_m": g["res_m"], "timezone": tz, "fire_key": rec["fire_key"],
                           "dob_source": "FIRMS VIIRS S-NPP 2017, nearest detection in the MTBS perimeter",
                           "fuel_source": "LANDFIRE LF2016 FBFM40 -> FBP (v7 crosswalk)"})
    (ROOT / "domains").mkdir(parents=True, exist_ok=True)
    dpath = ROOT / "domains" / f"{fid}.npz"
    dom.save(dpath)
    (ROOT / "sidecar").mkdir(exist_ok=True)
    np.savez_compressed(ROOT / "sidecar" / f"{fid}.npz", fbfm=fbi, dob_det=det)
    pairs = fire_day_pairs(dom)
    days = [q.day for q in pairs]
    if len(days) > 3:
        days = sorted(random.Random(f"{DAY_SEED}:{fid}").sample(days, 3))
    burn_days = dom.burn_days()
    w0, w1 = doy_to_date(2017, burn_days[0]) - timedelta(days=1), doy_to_date(2017, burn_days[-1]) + timedelta(days=2)
    fetch_era5_hourly(clat, clng, w0, w1, ROOT / "weather", timezone=tz)
    t0d = rec["t0"].date()
    f_start = min(date(2017, 4, 1), t0d - timedelta(days=30))
    f_end = doy_to_date(2017, burn_days[-1]) + timedelta(days=1)
    e5 = B.era5_utc_frame(B.openmeteo(clat, clng, f_start, f_end + timedelta(days=2), "GMT",
                                      ROOT / "inputs" / f"{fid}_era5_utc.json"))
    (ROOT / "groups").mkdir(exist_ok=True)
    (ROOT / "groups" / f"{fid}_era5.json").write_text(json.dumps(B.daily_fwi(e5, tz, f_start, f_end, fid, clat, clng)))
    tags = {d: start_type(dom.dob, d, dom.cell_area_m2)["start_type"] for d in days}
    info.update(status="ok", eligible_pairs=len(pairs), days=days, start_types=tags, timezone=tz,
                domain_sha256=C.sha256(dpath), seconds=round(time.time() - t_start, 1))
    man = {"fire_id": fid, "fire_key": rec["fire_key"], "region": "US", "year": 2017,
           "area_ha": float((dob > 0).sum()) * dom.cell_area_m2 / 1e4, "res_m": g["res_m"], "grid": [dom.rows, dom.cols],
           "days": days, "weather_point": [clat, clng], "weather_range": [int(burn_days[0]), int(burn_days[-1])],
           "timezone": tz, "station": None, "ndvi_curing": None, "t0": str(t0d)}
    return {"info": info, "manifest": man}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true", help="required: held-out scoring authorised by the main session")
    a = ap.parse_args()
    if not a.go:
        raise SystemExit("refusing: pass --go only after the main session authorises held-out scoring")
    (ROOT / "inputs").mkdir(parents=True, exist_ok=True)
    pts = firms_points()
    manifest = []
    for rec in records():
        try:
            out = build_fire(rec, pts)
        except Exception as e:  # recorded; contributes nothing
            out = {"info": {"fire_id": rec["fire_id"], "status": f"error {e!r}"[:400]}}
        (ROOT / "inputs" / f"{rec['fire_id']}.json").write_text(json.dumps(out["info"], indent=1, default=str))
        if "manifest" in out and out["manifest"]["days"]:
            manifest.append(out["manifest"])
        print(rec["fire_id"], out["info"].get("status"), out["info"].get("days"), flush=True)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print("fires with days:", len(manifest), "fire-days:", sum(len(m["days"]) for m in manifest))


if __name__ == "__main__":
    sys.exit(main())
