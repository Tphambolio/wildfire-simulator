#!/usr/bin/env python3
"""[R72] stage 2 (PREREG_ARRIVAL1): the fresh held-out community-arrival pool. Observations only.

Why this pool. C1b (PREREG_COMPARATOR_C1B §2) took every Alberta CFSDS fire-day 2014-2024 with
an observed community arrival, and PREREG_LR8G [R74] (e9c6e51, §3) reserves every CFSDS fire
2014-2024 in BC, SK, MB, NT and YT outside the v8 pool (its set C takes all their community-arrival
fire-days, set F draws from them). The fires no study has reserved, on the UTC-6 fire-season clock the
registered C1b metric code assumes (local = UTC-6, start 06:00, burning period 10-20 h local; the
harness's ERA5 fetch uses the America/Edmonton clock = UTC-6 in summer), are therefore the VIIRS
375 m era years before 2014: CFSDS fires of 2012 and 2013 with centroid in Alberta, Saskatchewan
or the Northwest Territories (SK: CST all year; AB, NT: MDT in summer). The v8 pool (NBAC
2018-2025) cannot overlap these years; the check is kept anyway.

No model is run by this script.

Commands
    communities  build the community reach-zone layer for SK + NT with the Alberta (C1) rules
                 transposed (communities.py): StatCan 2021 census subdivisions of settlement types
                 SETTLEMENT_CSD (cities, towns, villages, northern/resort villages, hamlets,
                 settlements, chartered communities, Indian reserves and settlements), StatCan 2021
                 designated places (all), and CGNDB populated places of POINT_TERMS as 250 m discs
                 (dropped inside a CSD/DPL polygon above, or within 1 km of a polygon of the same
                 name); every unit buffered by 500 m. Writes BIG/heldout/communities_sk_nt.geojson and the
                 merged layer communities_ho.geojson (+ FireSim's Alberta C1 zones, communities.py).
    screen       every CFSDS v1.1 fire of YEARS (2012, 2013) with its mean daily-growth centroid in AB
                 (validate.ALBERTA outline), SK or NT
                 (any size) and a DOB raster (the v8 pool, NBAC 2018-2025, and LR8G's 2014-2024 pool
                 cannot contain these years); on the native DOB raster, the
                 fire-days passing Bennett's filters whose day-n growth touches a reach zone that no
                 earlier day touched (prepare_c1b.cmd_screen, same code path). Writes
                 BIG/heldout/screen.json.
Usage
    PYTHONPATH=engine/src python3 scripts/validation/arrival_r72/heldout_pool.py communities|screen
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import sys
import unicodedata
import zipfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402

SRC = K.BIG / "communities_src"
HO = K.BIG / "heldout"
PR = {"47": "Saskatchewan", "61": "Northwest Territories"}  # + Alberta ("48") in the screen
SETTLEMENT_CSD = ("CY", "C", "T", "TV", "V", "VL", "VN", "VK", "VC", "NV", "NH", "RV", "SV", "HAM",
                  "SET", "CC", "IRI", "S-É", "IGD", "NL")
POINT_TERMS = ("Locality", "Hamlet", "Community", "Compact Rural Community", "Settlement",
               "Urban Community", "Village", "Town", "City", "Organized Hamlet", "Northern Community")
REACH_M, POINT_RADIUS_M, SAME_NAME_M = 500.0, 250.0, 1000.0
YEARS = (2012, 2013)  # VIIRS 375 m era before LR8G's 2014-2024 pool (PREREG_LR8G §3)
V8_SPLIT = Path("/home/rpas/dev/wildfire/reports/data/v8-prefreeze-checks-2026-10-18/results/split.csv")
NBAC = Path("/home/rpas/dev/wildfire/prefire-data/raw/nbac")


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).strip()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cmd_communities(_a):
    import geopandas as gpd
    import pandas as pd

    csd = pd.concat([gpd.read_file(p) for p in sorted(glob.glob(str(SRC / "csd*.geojson")))])
    csd = csd[csd.PRUID.isin(PR)].set_crs(4326, allow_override=True)
    csd["geometry"] = csd.geometry.make_valid()
    prov = csd.dissolve("PRUID")[["geometry"]].reset_index()
    prov.to_file(HO / "provinces_sk_nt.geojson", driver="GeoJSON")
    sett = csd[csd.CSDTYPE.isin(SETTLEMENT_CSD)].copy()
    sett["name"] = sett.CSDNAME.str.strip() + " (" + sett.CSDTYPE + ", " + sett.PRUID.map(PR) + ")"
    sett["level"] = "csd"
    dpl = gpd.read_file(SRC / "dpl.geojson").set_crs(4326, allow_override=True)
    dpl = dpl[dpl.PRUID.isin(PR)].copy()
    dpl["geometry"] = dpl.geometry.make_valid()
    dpl["name"] = dpl.DPLNAME.str.strip() + " (designated place, " + dpl.PRUID.map(PR) + ")"
    dpl["level"] = "dpl"
    polys = pd.concat([sett[["name", "level", "geometry"]], dpl[["name", "level", "geometry"]]]).to_crs(3978)
    polys["geometry"] = polys.buffer(0)
    union = polys.union_all()
    by_name = collections.defaultdict(list)
    for n, g in zip(polys.name, polys.geometry):
        by_name[fold(n.split(" (")[0])].append(g)
    with zipfile.ZipFile(SRC / "cgn_canada_csv_eng.zip") as z:
        c = pd.read_csv(z.open("cgn_canada_csv_eng.csv"), encoding="utf-8-sig",
                        usecols=["CGNDB ID", "Geographical Name", "Generic Term", "Generic Category",
                                 "Latitude", "Longitude", "Province - Territory"])
    c = c[(c["Generic Category"] == "Populated Place") & c["Generic Term"].isin(POINT_TERMS) &
          c["Province - Territory"].isin(PR.values())]
    c = c.rename(columns={"Geographical Name": "gname", "Generic Term": "term", "Province - Territory": "prov"})
    pts = gpd.GeoDataFrame(c, geometry=gpd.points_from_xy(c.Longitude, c.Latitude), crs=4326).to_crs(3978)
    keep, dropped = [], collections.Counter()
    seen = []
    for r in pts.itertuples():
        n = r.gname.strip()
        if union.contains(r.geometry):
            dropped["inside a CSD/DPL polygon"] += 1
            continue
        if any(g.distance(r.geometry) <= SAME_NAME_M for g in by_name.get(fold(n), [])):
            dropped["same name as a polygon within 1 km"] += 1
            continue
        if any(fold(n) == sn and r.geometry.distance(sg) <= SAME_NAME_M for sn, sg in seen):
            dropped["duplicate point within 1 km"] += 1
            continue
        seen.append((fold(n), r.geometry))
        keep.append({"name": f"{n} ({r.term.lower()}, {r.prov})", "level": "point",
                     "geometry": r.geometry.buffer(POINT_RADIUS_M)})
    p = gpd.GeoDataFrame(keep, crs=3978)
    z = pd.concat([polys, p]).reset_index(drop=True)
    # unique names
    cnt = collections.Counter(z.name)
    idx = collections.Counter()
    names = []
    for n in z.name:
        if cnt[n] > 1:
            idx[n] += 1
            n = f"{n} #{idx[n]}"
        names.append(n)
    z["name"] = names
    z["geometry"] = z.buffer(REACH_M)
    out = HO / "communities_sk_nt.geojson"
    z.to_crs(4326).to_file(out, driver="GeoJSON")
    import communities as C

    ab = C.community_zones()[["name", "geometry"]].copy()  # FireSim's Alberta layer (C1), already + 500 m
    ab["level"] = "alberta_c1"
    allz = pd.concat([z.to_crs(4326), ab.to_crs(4326)]).reset_index(drop=True)
    out2 = HO / "communities_ho.geojson"
    allz.to_file(out2, driver="GeoJSON")
    info = {"n_alberta_c1": int(len(ab)), "merged_sha256": sha(out2), "n_csd": int((z.level == "csd").sum()), "n_dpl": int((z.level == "dpl").sum()),
            "n_point": int((z.level == "point").sum()), "dropped_points": dict(dropped),
            "sha256": sha(out), "sources": {p.name: sha(p) for p in sorted(SRC.glob("*")) if p.is_file()}}
    (HO / "communities_sk_nt.json").write_text(json.dumps(info, indent=1))
    print(info)


def fire_list():
    import geopandas as gpd
    import shapely

    from firesim.validation.cfsds import read_groups

    import validate as V

    prov = gpd.read_file(HO / "provinces_sk_nt.geojson")
    geom = {r.PRUID: r.geometry for r in prov.itertuples()}
    geom["48"] = shapely.Polygon(V.ALBERTA)  # the harness's Alberta outline (validate.alberta_fires)
    out = []
    for y in YEARS:
        p = K.VD / f"cfsds/groups/Firegrowth_groups_v1_1_{y}.csv"
        for fid, days in read_groups(p).items():
            rows = list(days.values())
            lat = float(np.mean([r["lat"] for r in rows]))
            lng = float(np.mean([r["lon"] for r in rows]))
            pt = shapely.Point(lng, lat)
            for pr, g in geom.items():
                if g.contains(pt):
                    out.append({"fire_id": fid, "year": y, "pruid": pr, "area_ha": sum(r["firearea"] for r in rows),
                                "lat": lat, "lng": lng, "ndays": len(rows)})
    return out


def v8_union():
    """Union (EPSG:4326) of the v8 pool's Canadian fires (NBAC GIDs of PREREG_LR8's split)."""
    import pandas as pd
    import pyogrio
    import shapely

    s = pd.read_csv(V8_SPLIT)
    s = s[s.fire_key.str.startswith("NBAC:")]
    geoms = []
    for y, g in s.groupby("year"):
        gids = ",".join(repr(k[5:]) for k in g.fire_key)
        z = NBAC / f"NBAC_{y}_20260513.zip"
        d = pyogrio.read_dataframe(f"/vsizip/{z}/NBAC_{y}_20260513.shp", where=f"GID IN ({gids})", columns=["GID"])
        geoms += list(d.to_crs(4326).geometry)
    return shapely.union_all(geoms), len(s)


def cmd_screen(_a):
    import geopandas as gpd
    import rasterio
    import shapely
    from rasterio.features import rasterize, shapes
    from scipy import ndimage
    from shapely.geometry import box, shape

    from firesim.validation.cfsds import DOB_NODATA

    from rasterio.windows import from_bounds

    z = gpd.read_file(HO / "communities_ho.geojson")
    # v8 pool fires are NBAC 2018-2025: they cannot be 2012-2013 CFSDS fires (no spatial check needed)
    assert max(YEARS) < 2018
    n_v8 = 0
    eight = np.ones((3, 3), bool)
    out, excluded, no_raster = [], [], []
    fires = fire_list()
    print(len(fires), "fires in the pool", flush=True)
    zcache = {}
    for k, f in enumerate(fires):
        p = K.VD / f"cfsds/doy/{f['fire_id']}_krig.tif"
        if not p.exists():
            no_raster.append(f["fire_id"])
            continue
        with rasterio.open(p) as s:
            raw = s.read(1)
            a = np.where((raw == DOB_NODATA) | (raw > 400), 0, raw).astype(np.int16)
            del raw
            if not (a > 0).any():
                continue
            ck = s.crs.to_string()
            if ck not in zcache:
                zcache[ck] = z.to_crs(s.crs)
            zz = zcache[ck]
            sub = zz[zz.intersects(box(*s.bounds))]
            if sub.empty:
                continue
            masks = []  # (name, row slice, col slice, window mask): rasterised on the zone's own window only
            for n, g in zip(sub["name"], sub.geometry):
                w = from_bounds(*g.bounds, transform=s.transform).round_offsets().round_lengths()
                r0, c0 = max(int(w.row_off) - 1, 0), max(int(w.col_off) - 1, 0)
                r1, c1 = min(int(w.row_off + w.height) + 2, a.shape[0]), min(int(w.col_off + w.width) + 2, a.shape[1])
                if r1 <= r0 or c1 <= c0:
                    continue
                from rasterio.windows import Window, transform as wtr

                tw = wtr(Window(c0, r0, c1 - c0, r1 - r0), s.transform)
                mm = rasterize([(g, 1)], out_shape=(r1 - r0, c1 - c0), transform=tw, all_touched=True).astype(bool)
                if mm.any():
                    masks.append((n, slice(r0, r1), slice(c0, c1), mm))
        days = sorted(np.unique(a[a > 0]).tolist())
        if k % 20 == 0:
            print(k, f["fire_id"], a.shape, len(masks), flush=True)
        for d in days[1:]:
            init, grow = (a > 0) & (a < d), a == d
            if not init.any() or not (a == d - 1).any():
                continue
            if not (ndimage.binary_dilation(init, eight) & grow).any():
                continue
            hit = [n for n, rs, cs, mm in masks if (mm & grow[rs, cs]).any() and not (mm & init[rs, cs]).any()]
            if hit:
                out.append({"fire_id": f["fire_id"], "day": int(d), "year": f["year"], "pruid": f["pruid"],
                            "area_ha": round(f["area_ha"]), "communities": hit})
    res = {"n_fires_pool": len(fires), "n_no_raster": len(no_raster), "n_v8_pool_fires": n_v8,
           "excluded": excluded, "fire_days": out,
           "n_fire_days": len(out), "n_fires_with_days": len({o["fire_id"] for o in out})}
    (HO / "screen.json").write_text(json.dumps(res, indent=1))
    print({k: v for k, v in res.items() if k not in ("excluded", "fire_days")})
    print(collections.Counter((o["year"], o["pruid"]) for o in out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["communities", "screen"])
    a = ap.parse_args()
    HO.mkdir(parents=True, exist_ok=True)
    {"communities": cmd_communities, "screen": cmd_screen}[a.cmd](a)


if __name__ == "__main__":
    main()
