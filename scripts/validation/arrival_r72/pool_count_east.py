#!/usr/bin/env python3
"""[R72] power check for PREREG_ARRIVAL1: how many community-arrival fire-days are left east of LR8G?

After LR8G [R74] (BC, SK, MB, NT, YT 2014-2024, outside the v8 pool) and C1b (all Alberta 2014-2024),
the only CFSDS 2014-2024 fires no study reserves are in ON, QC, NL, NB, NS, PE, NU (outside the v8
pool). Their summer clock is UTC-4 / UTC-5 (ON west of 90 W: UTC-5), so the frozen C1b metric code
(UTC-6) would need a per-fire offset: this script only COUNTS candidate fire-days (observations only,
native DOB raster, C1b screen rule, the national community rules of heldout_pool.py) to size that
option; no domain is built and no model is run.

v8 exclusion here is conservative: a fire is dropped if the convex hull of its burned cells intersects
any v8 pool fire (NBAC 2018-2025 GIDs of PREREG_LR8's split).

    CAPJOB_MEM=4G capjob python3 scripts/validation/arrival_r72/pool_count_east.py
Writes BIG/pool_east/{communities_east.geojson, count.json}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import heldout_pool as P  # noqa: E402

EAST = {"35": "Ontario", "24": "Quebec", "10": "Newfoundland and Labrador", "13": "New Brunswick",
        "12": "Nova Scotia", "11": "Prince Edward Island", "62": "Nunavut"}


def main():
    import geopandas as gpd
    import rasterio
    import shapely
    from rasterio.features import rasterize
    from rasterio.windows import Window, from_bounds, transform as wtr
    from scipy import ndimage
    from shapely.geometry import box

    from firesim.validation.cfsds import DOB_NODATA, read_groups

    P.PR = EAST
    P.HO = P.K.BIG / "pool_east"
    P.HO.mkdir(parents=True, exist_ok=True)
    if not (P.HO / "communities_sk_nt.geojson").exists():
        try:
            P.cmd_communities(None)  # writes communities_sk_nt.geojson (east here) + merged with AB
        except Exception as exc:  # noqa: BLE001
            print("communities:", exc)
    z = gpd.read_file(P.HO / "communities_sk_nt.geojson")
    prov = gpd.read_file(P.HO / "provinces_sk_nt.geojson")
    geom = {r.PRUID: r.geometry for r in prov.itertuples()}
    v8u, n_v8 = P.v8_union()
    fires = []
    for y in range(2014, 2025):
        for fid, days in read_groups(P.K.VD / f"cfsds/groups/Firegrowth_groups_v1_1_{y}.csv").items():
            rows = list(days.values())
            pt = shapely.Point(float(np.mean([r["lon"] for r in rows])), float(np.mean([r["lat"] for r in rows])))
            for pr, g in geom.items():
                if g.contains(pt):
                    fires.append((fid, y, pr))
    eight = np.ones((3, 3), bool)
    out, n_v8x, zc = [], 0, {}
    for fid, y, pr in fires:
        p = P.K.VD / f"cfsds/doy/{fid}_krig.tif"
        if not p.exists():
            continue
        with rasterio.open(p) as s:
            raw = s.read(1)
            a = np.where((raw == DOB_NODATA) | (raw > 400), 0, raw).astype(np.int16)
            del raw
            rr, cc = np.nonzero(a > 0)
            if not len(rr):
                continue
            xs, ys = rasterio.transform.xy(s.transform, rr[:: max(1, len(rr) // 5000)], cc[:: max(1, len(cc) // 5000)])
            hull = shapely.MultiPoint(list(zip(xs, ys))).convex_hull
            if gpd.GeoSeries([hull], crs=s.crs).to_crs(4326).iloc[0].intersects(v8u):
                n_v8x += 1
                continue
            ck = s.crs.to_string()
            zc.setdefault(ck, z.to_crs(s.crs))
            sub = zc[ck][zc[ck].intersects(box(*s.bounds))]
            masks = []
            for n, g in zip(sub["name"], sub.geometry):
                w = from_bounds(*g.bounds, transform=s.transform).round_offsets().round_lengths()
                r0, c0 = max(int(w.row_off) - 1, 0), max(int(w.col_off) - 1, 0)
                r1, c1 = min(int(w.row_off + w.height) + 2, a.shape[0]), min(int(w.col_off + w.width) + 2, a.shape[1])
                if r1 <= r0 or c1 <= c0:
                    continue
                mm = rasterize([(g, 1)], out_shape=(r1 - r0, c1 - c0),
                               transform=wtr(Window(c0, r0, c1 - c0, r1 - r0), s.transform), all_touched=True).astype(bool)
                if mm.any():
                    masks.append((n, slice(r0, r1), slice(c0, c1), mm))
        if not masks:
            continue
        for d in sorted(np.unique(a[a > 0]).tolist())[1:]:
            init, grow = (a > 0) & (a < d), a == d
            if not (a == d - 1).any() or not (ndimage.binary_dilation(init, eight) & grow).any():
                continue
            if any((mm & grow[r, c]).any() and not (mm & init[r, c]).any() for _, r, c, mm in masks):
                out.append({"fire": fid, "year": y, "pruid": pr, "day": int(d)})
    res = {"n_fires_pool": len(fires), "n_v8_excluded": n_v8x, "n_fire_days": len(out),
           "n_fires_with_days": len({o["fire"] for o in out}),
           "by_province": {pr: sum(o["pruid"] == pr for o in out) for pr in EAST}}
    (P.HO / "count.json").write_text(json.dumps(res, indent=1))
    print(res)


if __name__ == "__main__":
    main()
