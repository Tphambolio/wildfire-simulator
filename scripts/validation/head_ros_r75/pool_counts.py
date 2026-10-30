#!/usr/bin/env python3
"""[R75] pool plan: COUNT the unreserved fire-days for a held-out head-ROS test. Counts only.

Nothing about any day-n outcome is used except what every harness set uses to define a fire-day
(CFSDS DOB of day n-1 and day n exist); no model, no score, no growth area of day n, no hotspot.
No fire ID is written anywhere (counts only); the scan's ID list is a temporary file deleted at the end.

Pools counted (CFSDS v1.1 fire-days: burning on day n-1 and on day n)
  A   Alberta CFSDS fires 2012-2024 (centroid in validate.ALBERTA), minus: the record's 32 fires
      (validation-data/manifest.json; C1/H79 are its 16 test fires), the C1b fires (sets/community_c1b*),
      any fire whose burned-cell convex hull intersects a v8 pool fire (PREREG_LR8 split, NBAC 2018-2025),
      and any fire whose ID is named in a text file of reports/, validation-data/ (except sealed2026/
      and this study's own folders) or the repository's docs/ (the LR8G / R71 / SEALED rule-8 scan). Raw
      source catalogues (validation-data/cfsds/, the alberta_fires.csv screen) do not name a fire, and an
      NBAC GID ('NBAC:2021_1655', same YYYY_N form) is not a CFSDS ID. Per-directory hit counts are written
      to OUT/named_hits_by_directory.csv.
      UTC-6, back-cast fuel method as C1b: no transfer.
  A0  SK and NT CFSDS fires 2012-2013 (outside LR8G's 2014-2024 pool), same exclusions. UTC-6.
  E   ON, QC, NL, NB, NS, PE, NU CFSDS fires 2014-2024, minus the v8 pool and LR8L/S pool E (NBAC
      >= 5,000 ha 2018-2025 east: excluded by the same hull test against NBAC east fires >= 5,000 ha),
      same text scan. UTC-4/-5: needs a per-fire time-zone offset (disclosed).
  US  MTBS Wildfire boundaries 2012-2016, >= 5,000 ha, the 11 western states (outside v8 2018-2024, R71's
      2017 seal and SEALED_2026): fire counts only (no CFSDS; day of burning would come from VIIRS as R71).
Major-run candidates per fire-day (forecast-time, from DOB before day n): previous-day growth >= 1,000 ha
and >= 10 % of the start area; held days: R71 rule (start age >= 2 d and previous-day share < 5 %).

    CAPJOB_MEM=5G capjob python3 scripts/validation/head_ros_r75/pool_counts.py
Writes OUT/pool_counts.json and OUT/named_hits_by_directory.csv (counts only), The hull test uses
the unsimplified NBAC polygons.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VD = Path("/home/rpas/dev/wildfire/validation-data")
OUT = Path("/home/rpas/dev/wildfire/reports/data/head-ros-r75-pool-plan-2026-10-29")
BIG = VD / "head-ros-r75"
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "engine/src")]
R72SRC = VD / "arrival-underspread-r72/communities_src"
NBAC = Path("/home/rpas/dev/wildfire/prefire-data/raw/nbac")
V8_SPLIT = Path("/home/rpas/dev/wildfire/reports/data/v8-prefreeze-checks-2026-10-18/results/split.csv")
EAST = {"35", "24", "10", "13", "12", "11", "62"}
WEST_US = ("AZ", "CA", "CO", "ID", "MT", "NV", "NM", "OR", "UT", "WA", "WY")


def provinces():
    """Census subdivisions (R72's CSD download) with PRUID, for point-in-province lookup. No dissolve: a point
    inside a CSD is inside its province (the dissolve + make_valid of the old version took over an hour)."""
    import geopandas as gpd
    import pandas as pd

    os.environ["OGR_GEOJSON_MAX_OBJ_SIZE"] = "0"
    return pd.concat([gpd.read_file(p, columns=["PRUID"]) for p in sorted(R72SRC.glob("csd*.geojson"))]
                     ).set_crs(4326, allow_override=True).reset_index(drop=True)


def excluded_chunks():
    """Yield (year, raw geometries in EPSG:4326) for the v8 pool fires (split.csv NBAC GIDs) and the NBAC east
    fires >= 5,000 ha 2018-2025 (LR8L/S pool E), read 50 GIDs at a time to keep memory low."""
    import pandas as pd
    import pyogrio

    s = pd.read_csv(V8_SPLIT)
    s = s[s.fire_key.str.startswith("NBAC:")]
    for y in range(2018, 2026):
        shp = f"/vsizip/{NBAC}/NBAC_{y}_20260513.zip/NBAC_{y}_20260513.shp"
        a = pyogrio.read_dataframe(shp, read_geometry=False, columns=["GID", "ADMIN_NAME", "POLY_HA"])
        east = a[a.ADMIN_NAME.isin(["ON", "QC", "NL", "NB", "NS", "PE", "NU"])].groupby("GID").POLY_HA.sum()
        gids = sorted(set(k[5:] for k in s[s.year == y].fire_key) | set(east[east >= 5000].index))
        for i in range(0, len(gids), 50):
            chunk = gids[i:i + 50]
            d = pyogrio.read_dataframe(shp, where=f"GID IN ({','.join(repr(g) for g in chunk)})", columns=["GID"])
            yield y, [g for g in d.to_crs(4326).geometry if g is not None]


def excluded_union():
    """STRtree of the excluded NBAC geometries, unsimplified and prepared (exact hull test). An earlier version
    simplified them by 0.005 deg to save memory; topology-preserving simplify of the 2023 megafires took
    many minutes per chunk and the exact geometries fit in memory, so the test is exact and the
    simplification effect is zero by construction."""
    import shapely

    geoms = []
    for y, gs in excluded_chunks():
        geoms += gs
    shapely.prepare(geoms)
    print("excluded geometries", len(geoms), flush=True)
    return shapely.STRtree(geoms), geoms


# Raw source catalogues: a listing of every fire in a download (or a screen of it) does not "name" a fire.
# A file counts as naming a fire only if it records that fire being selected, run or scored.
RAW_CATALOGUES = (
    str(VD / "cfsds") + "/",                  # CFSDS v1.1 downloads (groups/, raw/): every CFSDS fire
    str(VD / "alberta_fires.csv"),            # validate.py prepare screen: all AB CFSDS fires >= min area with
                                              # area/ndays/FWI; the 32 run fires are manifest.json/fire_days.csv
)
SCAN_ROOTS = ["/home/rpas/dev/wildfire/reports", str(VD), str(REPO / "docs")]
SCAN_EXCLUDE_DIRS = {"sealed2026", "head-ros-r75", "arrival-underspread-r72", "head-ros-r75-pool-plan-2026-10-29",
                     "arrival-underspread-r72-2026-10-29"}
SCAN_EXT = (".md", ".json", ".csv", ".txt", ".jsonl", ".tsv", ".log")
ID_RX = re.compile(rb"(?<![\w])(\d{4}_\d+|[A-Z]{2}\d{19})(?!\w)")


def named_ids(ids: list[str]) -> dict[str, set[str]]:
    """{id: files naming it} over text files of reports/, validation-data/ (minus sealed2026 and this study's
    / R72's folders) and docs/, word-bounded. Skips RAW_CATALOGUES, and skips a CFSDS-form match preceded
    within 6 bytes by 'NBAC' (NBAC GIDs share the YYYY_N form but are a different fire namespace; v8/NBAC
    fires are excluded spatially by the hull test instead)."""
    want = set(ids)
    hits: dict[str, set[str]] = {}
    for root in SCAN_ROOTS:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SCAN_EXCLUDE_DIRS]
            for f in fn:
                p = os.path.join(dp, f)
                if not f.endswith(SCAN_EXT) or p.startswith(RAW_CATALOGUES):
                    continue
                try:
                    b = Path(p).read_bytes()
                except OSError:
                    continue
                for m in ID_RX.finditer(b):
                    i = m.group(1).decode()
                    if i in want and not (i[4] == "_" and b"NBAC" in b[max(0, m.start() - 6):m.start()]):
                        hits.setdefault(i, set()).add(p)
    return hits


def top_dir(p: str) -> str:
    for r in SCAN_ROOTS:
        if p.startswith(r + "/"):
            rest = p[len(r) + 1:].split("/")
            if rest[0] == "data" and len(rest) > 2:  # reports/data/<study>: one level deeper
                rest = ["data/" + rest[1]] + rest[2:]
            return Path(r).name + "/" + (rest[0] if len(rest) > 1 else "(top-level files)")
    return p


def named_table(hits: dict[str, set[str]], label: str) -> list[dict]:
    """Rows: top-level directory -> number of distinct named IDs and files (counts only, no IDs)."""
    by: dict[str, set] = {}
    files: dict[str, set] = {}
    for i, ps in hits.items():
        for p in ps:
            by.setdefault(top_dir(p), set()).add(i)
            files.setdefault(top_dir(p), set()).add(p)
    return [{"id_set": label, "directory": d, "distinct_ids": len(v), "files": len(files[d])}
            for d, v in sorted(by.items(), key=lambda x: (-len(x[1]), x[0]))]


def cmd_main():
    import geopandas as gpd
    import rasterio
    import shapely

    import validate as V
    from firesim.validation.cfsds import DOB_NODATA, read_groups

    BIG.mkdir(parents=True, exist_ok=True)
    prov = provinces()
    ptree = shapely.STRtree(prov.geometry.values)
    alberta = shapely.Polygon(V.ALBERTA)

    def province_of(pt):
        """Alberta = validate.ALBERTA (as the record and C1b); other provinces from the CSDs. A point in more
        than one takes the lowest PRUID, the same order as the previous dissolved-province lookup."""
        c = {str(prov.PRUID.iat[i]) for i in ptree.query(pt, predicate="intersects")} - {"48"}
        if alberta.contains(pt):
            c.add("48")
        return min(c) if c else None

    record = {m["fire_id"] for m in json.loads((VD / "manifest.json").read_text())}
    c1b = {x.split(":")[0] for p in (REPO / "scripts/validation/c1/sets").glob("community_c1b*.txt")
           for x in p.read_text().split() if x}
    exu = excluded_union()
    fires = []
    for y in range(2012, 2025):
        p = VD / f"cfsds/groups/Firegrowth_groups_v1_1_{y}.csv"
        if not p.exists():
            continue
        for fid, days in read_groups(p).items():
            rows = list(days.values())
            pt = shapely.Point(float(np.mean([r["lon"] for r in rows])), float(np.mean([r["lat"] for r in rows])))
            pr = province_of(pt)
            if pr is None:
                continue
            if pr == "48":
                pool = "A"
            elif pr in ("47", "61") and y <= 2013:
                pool = "A0"
            elif pr in EAST:
                pool = "E"
            else:
                continue  # BC/SK/MB/NT/YT 2014-2024 = LR8G pool; BC/MB/YT 2012-13 not on UTC-6: not counted
            fires.append((fid, y, pr, pool))
    print(len(fires), "pool fires", flush=True)
    named = named_ids([f[0] for f in fires])
    print(len(named), "named in the record", flush=True)
    table = named_table({i: ps for i, ps in named.items()}, "CFSDS pool A/A0/E")
    res = {p: {"fires_pool": 0, "excl_record": 0, "excl_c1b": 0, "excl_v8_or_poolE": 0, "excl_named": 0,
               "no_raster": 0, "fires_kept": 0, "fire_days": 0, "major_run_days": 0, "held_days": 0,
               "fires_with_major_run": 0, "by_year": {}} for p in ("A", "A0", "E")}
    cand = {p: [] for p in res}
    for fid, y, pr, pool in fires:
        R = res[pool]
        R["fires_pool"] += 1
        if fid in record:
            R["excl_record"] += 1
            continue
        if fid in c1b:
            R["excl_c1b"] += 1
            continue
        if fid in named:
            R["excl_named"] += 1
            continue
        p = VD / f"cfsds/doy/{fid}_krig.tif"
        if not p.exists():
            R["no_raster"] += 1
            continue
        with rasterio.open(p) as s:
            raw = s.read(1)
            a = np.where((raw == DOB_NODATA) | (raw > 400), 0, raw).astype(np.int16)
            del raw
            rr, cc = np.nonzero(a > 0)
            if not len(rr):
                R["no_raster"] += 1
                continue
            step = max(1, len(rr) // 5000)
            xs, ys = rasterio.transform.xy(s.transform, rr[::step], cc[::step])
            hull = gpd.GeoSeries([shapely.MultiPoint(list(zip(xs, ys))).convex_hull], crs=s.crs).to_crs(4326).iloc[0]
            tree, eg = exu
            excl = any(eg[i].intersects(hull) for i in tree.query(hull))
            if excl:
                R["excl_v8_or_poolE"] += 1
                continue
            cell_ha = abs(s.res[0] * s.res[1]) / 1e4
        R["fires_kept"] += 1
        days = np.unique(a[a > 0])
        n_days = n_run = n_held = 0
        for d in days[1:]:
            d = int(d)
            if not (a == d - 1).any():
                continue
            prior = (a > 0) & (a < d)
            age = d - int(a[prior].min())
            prev = (a == d - 1).sum()
            share = prev / prior.sum()
            n_days += 1
            if prev * cell_ha >= 1000 and share >= 0.10:
                n_run += 1
            if age >= 2 and share < 0.05:
                n_held += 1
        R["fire_days"] += n_days
        R["major_run_days"] += n_run
        R["held_days"] += n_held
        R["fires_with_major_run"] += int(n_run > 0)
        R["by_year"][str(y)] = R["by_year"].get(str(y), 0) + n_run
        cand[pool].append({"fire_id": fid, "year": y, "pruid": pr, "fire_days": n_days, "major_run_days": n_run})
    # US: MTBS fire counts (attributes only)
    import pyogrio

    m = pyogrio.read_dataframe(VD / "v8-pool/raw/mtbs/mtbs_perims_DD.shp", read_geometry=False,
                               columns=["event_id", "incid_type", "burnbndac", "ig_date", "incid_name"])
    yr = m.ig_date.astype(str).str[:4].astype(int)
    us = m[(m.incid_type == "Wildfire") & m.event_id.str[:2].isin(WEST_US) & (m.burnbndac >= 12355.3) &
           yr.between(2012, 2016) & ~m.incid_name.str.upper().str.contains("COMPLEX")]
    us_named = named_ids(list(us.event_id))
    table += named_table(us_named, "MTBS US 2012-2016")
    with open(OUT / "named_hits_by_directory.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, ["id_set", "directory", "distinct_ids", "files"])
        w.writeheader()
        w.writerows(table)
    res["hull_test"] = {"geometry": "NBAC polygons unsimplified (exact)", "excluded_geometries": len(exu[1])}
    res["US"] = {"mtbs_fires_2012_2016_ge_5000ha": int(len(us)), "named_in_record": len(us_named),
                 "by_year": {str(k): int(v) for k, v in yr[us.index].value_counts().sort_index().items()}}
    (OUT / "pool_counts.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    cmd_main()
