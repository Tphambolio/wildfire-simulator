#!/usr/bin/env python3
"""C1b: build the community-arrival fire-day set (PREREG_COMPARATOR_C1B §3). Observations only.

1. ``screen``: every Alberta CFSDS fire 2014-2024 (centroid in Alberta, ``validate.alberta_fires``,
   any size) with a DOB raster; on the native DOB raster, fire-days passing Bennett's filters
   (day n-1 burned, day n growth touches the earlier area) whose day-n growth touches a C1
   community reach zone (unit + 500 m) that no earlier day touched.
2. ``domains``: for the screened fires, harness domains exactly as ``validate.py prepare``
   (90 m or coarser to stay <= 1.2 M cells, fire + 15 km; CFS 2014b fuel, MRDEM-30), then the fuel
   replaced by the pre-fire back-cast of the Alberta Wildfire 2025 grid at the fire's start
   (``backcast_fire_test.backcast_codes`` logic, 20 m EPSG:3400 area grid, mode to the domain),
   ERA5 hourly weather fetched, and a ``manifest.json`` written. Horse River (2016_255) reuses
   its existing back-cast domain.
3. ``finalise``: re-applies the rule on the harness domain (``communities.observed_arrivals`` on
   each fire-day's working area); writes the fire-day set file and its sha256.

No model is run here.

    PYTHONPATH=engine/src:. python3 scripts/validation/c1/prepare_c1b.py screen|domains|finalise
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "engine/src"), str(HERE)]

VALDATA = Path(os.environ.get("C1_BASE_DATA", "/home/rpas/dev/wildfire/validation-data"))  # the record root
# (run finalise with FIRESIM_VALIDATION_DATA=$VALDATA/c1b so validate.py reads the C1b manifest)
C1B = VALDATA / "c1b"
BACKCAST_DOMAINS = VALDATA / "backcast/roots/ab2025_backcast/domains"
SET_FILE = HERE / "sets/community_c1b.txt"
YEARS = range(2014, 2025)


def cmd_screen(_a) -> None:
    import rasterio
    from rasterio.features import rasterize
    from scipy import ndimage
    from shapely.geometry import box

    import communities as C
    import validate as V
    from firesim.validation.cfsds import DOB_NODATA

    z = C.community_zones()
    eight = np.ones((3, 3), bool)
    out = []
    for f in V.alberta_fires(YEARS, 0.0):
        p = VALDATA / f"cfsds/doy/{f['fire_id']}_krig.tif"
        if not p.exists():
            continue
        with rasterio.open(p) as s:
            a = s.read(1).astype(int)
            a[a == DOB_NODATA] = 0
            zz = z.to_crs(s.crs)
            sub = zz[zz.intersects(box(*s.bounds))]
            if sub.empty:
                continue
            masks = [(n, rasterize([(g, 1)], out_shape=a.shape, transform=s.transform,
                                   all_touched=True).astype(bool))
                     for n, g in zip(sub["name"], sub.geometry)]
        days = sorted(set(a[a > 0].ravel().tolist()))
        for d in days[1:]:
            init, grow = (a > 0) & (a < d), a == d
            if not init.any() or not (a == d - 1).any():
                continue
            if not (ndimage.binary_dilation(init, eight) & grow).any():
                continue
            hit = [n for n, m in masks if (m & grow).any() and not (m & init).any()]
            if hit:
                out.append({"fire_id": f["fire_id"], "day": int(d), "year": f["year"],
                            "area_ha": round(f["area_ha"]), "communities": hit})
    C1B.mkdir(parents=True, exist_ok=True)
    (C1B / "screen.json").write_text(json.dumps(out, indent=1))
    print(len(out), "fire-days on", len({o["fire_id"] for o in out}), "fires")


def _backcast(dom, days: list[int]):
    """Back-cast Alberta 2025 codes on the domain grid (backcast_fire_test.backcast_codes)."""
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, transform_bounds

    from firesim.data.backcast import BackcastData, compose
    from firesim.validation.harness import crop_for_day
    from scripts.fuelgrid import fire_areas as FA
    from scripts.validation import backcast_fire_test as B

    boxes = [crop_for_day(dom, d, 20000.0) for d in days]
    ll = (min(c.lng_min for c in boxes), min(c.lat_min for c in boxes),
          max(c.lng_max for c in boxes), max(c.lat_max for c in boxes))
    g0 = FA.Grid.from_bounds(transform_bounds("EPSG:4326", FA.CRS, *ll, densify_pts=41), FA.CELL)
    g = FA.Grid(g0.x0, g0.y0, g0.width, g0.height, FA.CELL, FA.CRS)  # as fire_areas.area_grid
    skey = B.fire_start(dom)
    data = BackcastData.load(str(B.PREFIRE))
    epoch, epath = data.epoch_for(dom.year)
    base = FA._warp_remote(str(B._odt().AB2025), g, Resampling.nearest, dtype=np.int32)
    key = FA._warp_remote(data.burn_key, g, Resampling.nearest, dtype=np.int32)
    pre = FA._warp_remote(epath, g, Resampling.nearest, dtype=np.int32)
    out, restored, unrestored = compose(base, key, pre, skey)
    codes = B.ab_to_harness(out)
    dst = np.zeros(dom.dob.shape, np.int32)
    reproject(codes.astype(np.int32), dst, src_transform=g.transform, src_crs=g.crs,
              dst_transform=from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat),
              dst_crs="EPSG:4326", resampling=Resampling.mode, src_nodata=0, dst_nodata=0)
    st = {"scenario": skey, "epoch": epoch, "source": f"SCANFI v2 {epoch}",
          "restored_share": float(restored.mean()), "unrestored_share": float(unrestored.mean())}
    return dst, st


def cmd_domains(_a) -> None:
    import shutil

    import validate as V
    from firesim.validation.cfsds import FireDomain, fire_day_pairs, prepare_domain
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    scr = json.loads((C1B / "screen.json").read_text())
    by_fire: dict[str, list[int]] = {}
    for o in scr:
        by_fire.setdefault(o["fire_id"], []).append(o["day"])
    (C1B / "domains").mkdir(parents=True, exist_ok=True)
    (C1B / "domains2014b").mkdir(parents=True, exist_ok=True)
    for name in ("cfsds", "weather"):
        if not (C1B / name).exists():
            (C1B / name).symlink_to(VALDATA / name)
    fires = {f["fire_id"]: f for f in V.alberta_fires(YEARS, 0.0)}
    manifest, report = [], {}
    for fid, days in sorted(by_fire.items()):
        dpath = C1B / f"domains/{fid}.npz"
        if fid == V.HORSE_RIVER:
            if not dpath.exists():
                shutil.copy(BACKCAST_DOMAINS / f"{fid}.npz", dpath)
            dom = FireDomain.load(dpath)
        elif dpath.exists():
            dom = FireDomain.load(dpath)
        else:
            dob_path = VALDATA / f"cfsds/doy/{fid}_krig.tif"
            res = V.choose_res(dob_path, 15000.0)
            d14 = prepare_domain(fid, dob_path, V.FUEL_2014B, V.MRDEM, res_m=res, buffer_m=15000.0,
                                 dem_overview_level=0 if res < 120 else 1)
            d14.save(C1B / f"domains2014b/{fid}.npz")
            fuel, st = _backcast(d14, days)
            meta = dict(d14.meta, fuel_source="back-cast test grid 'ab2025_backcast' "
                        "(scripts/validation/c1/prepare_c1b.py, as backcast_fire_test.py)",
                        fuel_resampling="mode from 20 m EPSG:3400", backcast=st)
            dom = FireDomain(fire_id=d14.fire_id, year=d14.year, dob=d14.dob, fuel=fuel,
                             elevation=d14.elevation, lat_max=d14.lat_max, lng_min=d14.lng_min,
                             cell_lat=d14.cell_lat, cell_lng=d14.cell_lng,
                             fuel_scheme="cfs_national", meta=meta)
            dom.save(dpath)
            report[fid] = st
        pairs = {p.day for p in fire_day_pairs(dom)}
        bd = dom.burn_days()
        lat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
        lng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
        fetch_era5_hourly(lat, lng, doy_to_date(dom.year, bd[0]) - timedelta(days=1),
                          doy_to_date(dom.year, bd[-1]) + timedelta(days=2), VALDATA / "weather")
        f = fires.get(fid, {"fire_id": fid, "year": dom.year})
        manifest.append({**f, "res_m": dom.meta.get("res_m"), "grid": [dom.rows, dom.cols],
                         "days": sorted(d for d in days if d in pairs),
                         "screen_days": sorted(days), "weather_point": [lat, lng],
                         "weather_range": [bd[0], bd[-1]]})
        print(fid, dom.rows, dom.cols, report.get(fid, "existing"), flush=True)
    (C1B / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (C1B / "domains_report.json").write_text(json.dumps(report, indent=1))


def cmd_finalise(_a) -> None:
    import communities as C
    import validate as V
    from firesim.validation.harness import build_case
    from firesim.validation.report import split_fires

    os.environ["FIRESIM_VALIDATION_DOMAINS"] = str(C1B / "domains")
    man = json.loads((C1B / "manifest.json").read_text())
    split = split_fires({m["fire_id"] for m in json.loads((VALDATA / "manifest.json").read_text())})
    rows, keep = [], []
    for m in man:
        dom, groups, rec = V._load_fire(m["fire_id"])
        for d in m["screen_days"]:
            ok = d in m["days"]
            reached = []
            if ok:
                case = build_case(dom, d, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
                reached = [c["name"] for c in C.observed_arrivals(case, 17.0) if c["reached"]]
            stratum = {"calibration": "calibration", "test": "held-out (record split)"}.get(
                split.get(m["fire_id"]), "new (not in the 32-fire record)")
            rows.append({"fire_id": m["fire_id"], "day": d, "bennett_on_domain": ok,
                         "reached": reached, "stratum": stratum})
            if ok and reached:
                keep.append(f"{m['fire_id']}:{d}")
    (C1B / "finalise.json").write_text(json.dumps(rows, indent=1))
    SET_FILE.write_text("".join(f"{k}\n" for k in sorted(keep)))
    print(len(keep), "fire-days kept;", hashlib.sha256(SET_FILE.read_bytes()).hexdigest())
    for r in rows:
        print(r)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("screen", "domains", "finalise"):
        sp.add_parser(n)
    a = ap.parse_args()
    {"screen": cmd_screen, "domains": cmd_domains, "finalise": cmd_finalise}[a.cmd](a)


if __name__ == "__main__":
    main()
