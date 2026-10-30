#!/usr/bin/env python3
"""v8_viirs_jumps.py -- VIIRS jump catalogue, TRAINING MODE ONLY (pre-freeze check 3 of the v8 design).

Addendum A1 (PREREG_LR8, 2026-10-24) version. Identical to the frozen pre-freeze script
(reports/data/v8-prefreeze-checks-2026-10-18/scripts/v8_viirs_jumps.py, sha256 d0333957...1581) except:
  (1) epochs: a new epoch starts when consecutive acquisitions are >= 60 min apart, so acquisitions merge only
      when < 60 min apart as PREREG_LR8 section 4 says (the frozen code used > 60 min and merged at exactly
      60 min; [R61] section 5). The rule is the function ``new_epoch`` and is shared with the v8 runners.
  (2) paths: the frozen split is read from the pre-freeze folder (FIRESIM_V8_PREFREEZE) and outputs go to
      FIRESIM_V8_RULE_OUT (default validation-data/v8-pool/rule_A1/), so the frozen results are never
      overwritten.
The jump geometry, thresholds and flags (inside_final, out_final_m, gap) are unchanged.

Purpose
    Measure the real detected-jump rate per fire (new VIIRS 375 m detections >= 1 km beyond the
    observed perimeter, design section 8.4) on the TRAINING third of the seeded split, so that the v8
    minimum-jump rule and fire cap can be frozen at a measured rate (OD1, OD9). Decision fires are
    never processed: the script refuses any fire whose split assignment is not training.

Inputs
    results/split.csv (seeded split, v8_pool.py); NBAC 2026-05-13 polygons (training GIDs only);
    MTBS composite perimeters (training event IDs only); WFIGS 2025 perimeters (training IDs only,
    queried by ID); NASA FIRMS yearly country archives, VIIRS S-NPP (VNP14IMG) and NOAA-20 (VJ114IMG),
    Canada and United States, 2018-2025 (NOAA-21 is not in the keyless archive).
    Separate-start mask: start points of other fires that are NOT decision fires (NBAC rows whose GID is
    not a decision GID -> representative point + earliest start date; MTBS burnbndlat/lon + ig_date of
    non-decision events; WFIGS initial lat/lon of non-decision 2025 fires).

Jump rule as implemented (design section 8.4; free values marked [F] are fixed here for the freeze)
    * Detections per fire: inside the final perimeter buffered 2 km, inside the fire window
      [record start - 2 d, end], end = record end + 2 d (NBAC) or the last in-perimeter detection before a
      >= 21-day gap [F] (all regions), type == 0 (vegetation fire; FIRMS static/industrial/offshore removed).
    * Epochs: detection acquisition times merged when < 60 min apart [F] (S-NPP and NOAA-20 fly ~50 min
      apart). Interval i = (T_{i-1}, T_i], T = first acquisition of the epoch. Primary: intervals <= 12 h;
      sensitivities 14 h and 24 h.
    * Start perimeter at epoch i: all detections of earlier epochs (any confidence, any pixel size),
      each dilated by 375 m, then a 1 km morphological closing (alpha-shape surrogate [F]), on a per-fire
      125 m grid (250 m if the grid would exceed 4 M cells). Progression perimeters (CFSDS/FEDS) are NOT
      used in this pilot (both regions treated alike); see the report.
    * Clusters: all epoch-i detections outside the start perimeter, single linkage 750 m.
    * Jump: a cluster with >= 1 qualifying member (confidence n/h; scan <= 0.55 km and track <= 0.55 km [F]
      -- both pixel dimensions <= 550 m, see the VIIRS fact check), whose nearest qualifying member is
      >= 1,000 m from the start perimeter (2,000 m secondary); no other epoch-i detection outside the start
      perimeter within 750 m of the straight line from that member to the nearest perimeter cell; that
      member inside the final perimeter (375 m tolerance [F]); and >= 2 km from any separate start dated
      within [T_{i-1} - 1 d, T_i + 1 d]. A jump counts once, at its nearest qualifying member.
    * Gap class (front-run check): the mid-point of the jump-to-perimeter segment is
      'unburned' (outside the final perimeter -> strong evidence of a true jump), 'later_detected'
      (a detection within 375 m of the mid-point at a later epoch), or 'never_detected' (inside the final
      perimeter but never detected -> consistent with an undetected front run; the false-jump risk).
Outputs (training only; validation-data/v8-pool/viirs_training/ for the cached detections)
    results/jumps_training.csv (one row per jump), results/fires_training.csv (per-fire counts under the
    primary rule and sensitivities, intervals, window), results/jump_rate_summary.json.
Usage
    capjob python3 -I v8_viirs_jumps.py extract     # cache training-fire detections (per year, pickle)
    capjob python3 -I v8_viirs_jumps.py catalogue   # build the catalogue
Memory: < 3 GB (one year of FIRMS rows at a time; per-fire grids only).
"""
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
from pyproj import CRS
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.geometry import Point, box

W = Path("/home/rpas/dev/wildfire")
PREFREEZE_RES = Path(os.environ.get("FIRESIM_V8_PREFREEZE", W / "reports/data/v8-prefreeze-checks-2026-10-18")) / "results"
RES = Path(os.environ.get("FIRESIM_V8_RULE_OUT", W / "validation-data/v8-pool/rule_A1"))
RAW = W / "validation-data/v8-pool/raw"
CACHE = W / "validation-data/v8-pool/viirs_training"
YEARS = range(2018, 2026)
WFIGS = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters/FeatureServer/0/query"
MERGE_MIN, GAP_END_D = 60.0, 21.0
DIL, CLOSE, CL_LINK, LINE_TOL = 375.0, 1000.0, 750.0, 750.0
FINAL_TOL, SEP_START = 375.0, 2000.0


def new_epoch(t_sorted) -> np.ndarray:
    """True where an acquisition starts a new epoch: the first one, and any >= MERGE_MIN minutes after the
    previous acquisition (acquisitions merge only when < 60 min apart; PREREG_LR8 section 4, addendum A1)."""
    t = np.asarray(t_sorted, dtype="datetime64[ns]")
    return np.r_[True, np.diff(t) >= np.timedelta64(int(MERGE_MIN), "m")] if len(t) else np.zeros(0, bool)


def split():
    s = pd.read_csv(PREFREEZE_RES / "split.csv")
    return s, s[s.assignment.isin(["training", "training_legacy"])].copy(), set(s[s.assignment == "decision"].fire_key)


def sql_in(col, vals):
    return f"{col} IN ({','.join(repr(v) for v in vals)})"


def training_geoms(tr):
    """Final perimeters (EPSG:4326) and record dates of training fires only."""
    out = []
    can = tr[tr.fire_key.str.startswith("NBAC:")]
    for y, g in can.groupby("year"):
        gids = [k[5:] for k in g.fire_key]
        d = pyogrio.read_dataframe(f"/vsizip/{W}/prefire-data/raw/nbac/NBAC_{y}_20260513.zip/NBAC_{y}_20260513.shp",
                                   where=sql_in("GID", gids))
        d = d.to_crs(4326)
        for gid, r in d.groupby("GID"):
            s0 = pd.concat([r.HS_SDATE, r.AG_SDATE]).min()
            e0 = pd.concat([r.HS_EDATE, r.AG_EDATE]).max()
            out.append(dict(fire_key="NBAC:" + gid, geometry=r.geometry.union_all(), t0=s0, t1=e0))
    mt = tr[tr.fire_key.str.startswith("MTBS:")]
    if len(mt):
        d = pyogrio.read_dataframe(RAW / "mtbs/mtbs_perims_DD.shp", where=sql_in("event_id", [k[5:] for k in mt.fire_key]))
        d = d.to_crs(4326)
        for r in d.itertuples():
            out.append(dict(fire_key="MTBS:" + r.event_id, geometry=r.geometry, t0=pd.Timestamp(r.ig_date), t1=pd.NaT))
    wf = tr[tr.fire_key.str.startswith("WFIGS:")]
    if len(wf):
        import urllib.parse, urllib.request, io
        ids = [k[6:] for k in wf.fire_key]
        q = {"where": sql_in("attr_UniqueFireIdentifier", ids), "outFields": "attr_UniqueFireIdentifier,attr_FireDiscoveryDateTime,attr_FireOutDateTime,poly_GISAcres",
             "returnGeometry": "true", "outSR": 4326, "f": "geojson"}
        raw = urllib.request.urlopen(WFIGS, data=urllib.parse.urlencode(q).encode(), timeout=300).read()
        (RAW / "wfigs_training_2025.geojson").write_bytes(raw)
        d = gpd.read_file(io.BytesIO(raw)).sort_values("poly_GISAcres", ascending=False).drop_duplicates("attr_UniqueFireIdentifier")
        for r in d.itertuples():
            out.append(dict(fire_key="WFIGS:" + r.attr_UniqueFireIdentifier, geometry=r.geometry,
                            t0=pd.to_datetime(r.attr_FireDiscoveryDateTime, unit="ms"),
                            t1=pd.to_datetime(r.attr_FireOutDateTime, unit="ms") if r.attr_FireOutDateTime else pd.NaT))
    g = gpd.GeoDataFrame(out, crs=4326)
    return g.merge(tr[["fire_key", "region", "year", "assignment"]], on="fire_key")


def extract():
    CACHE.mkdir(parents=True, exist_ok=True)
    s, tr, dec = split()
    gp = CACHE / "training_perimeters.gpkg"
    if gp.exists():
        g = gpd.read_file(gp)
    else:
        g = training_geoms(tr)
        g.to_file(gp, driver="GPKG")
    cols = ["latitude", "longitude", "scan", "track", "acq_date", "acq_time", "satellite", "confidence", "daynight", "type", "frp"]
    for y in YEARS:
        gy = g[g.year == y]
        if not len(gy) or (CACHE / f"viirs_training_{y}.pkl.gz").exists():
            continue
        bufs = []
        for r in gy.itertuples():
            utm = gpd.GeoSeries([r.geometry], crs=4326).estimate_utm_crs()
            bufs.append((r.fire_key, gpd.GeoSeries([r.geometry], crs=4326).to_crs(utm).simplify(50)
                         .buffer(2000, resolution=4).simplify(100).to_crs(4326).iloc[0]))
        keep = []
        for sat in ("viirs-snpp", "viirs-jpss1"):
            for c in ("Canada", "United_States"):
                for ch in pd.read_csv(RAW / f"firms/{sat}_{y}_{c}.csv", usecols=cols, chunksize=200_000,
                                      dtype={"acq_time": str}):
                    # keep only points inside a training fire's 2 km buffer; all other points (incl. any
                    # decision-fire points) are discarded chunk by chunk and never written
                    for fk, buf in bufs:
                        bx = buf.bounds
                        sel = ((ch.longitude >= bx[0]) & (ch.longitude <= bx[2]) & (ch.latitude >= bx[1]) & (ch.latitude <= bx[3])).values
                        if not sel.any():
                            continue
                        sub = ch[sel]
                        ins = contains_xy(buf, sub.longitude.values, sub.latitude.values)
                        if ins.any():
                            keep.append(sub[ins].assign(fire_key=fk))
        d = pd.concat(keep, ignore_index=True)
        d["t"] = pd.to_datetime(d.acq_date + " " + d.acq_time.str.zfill(4), format="%Y-%m-%d %H%M")
        d = d.drop(columns=["acq_date", "acq_time"])
        d.to_pickle(CACHE / f"viirs_training_{y}.pkl.gz")
        print(y, len(d), "training detections cached", flush=True)


def separate_starts(dec):
    """Start points of fires that are not decision fires (lon, lat, start date)."""
    rows = []
    dgid = [k[5:] for k in dec if k.startswith("NBAC:")]
    for y in YEARS:
        d = pyogrio.read_dataframe(f"/vsizip/{W}/prefire-data/raw/nbac/NBAC_{y}_20260513.zip/NBAC_{y}_20260513.shp",
                                   where=f"NOT ({sql_in('GID', [x for x in dgid if x.startswith(str(y))] or ['none'])})",
                                   columns=["GID", "HS_SDATE", "AG_SDATE"])
        p = d.to_crs(4326).geometry.representative_point()
        st = pd.concat([d.HS_SDATE, d.AG_SDATE], axis=1).min(axis=1)
        rows.append(pd.DataFrame(dict(src="NBAC:" + d.GID, lon=p.x, lat=p.y, t=st)))
    m = pyogrio.read_dataframe(RAW / "mtbs/mtbs_perims_DD.shp", read_geometry=False,
                               columns=["event_id", "burnbndlat", "burnbndlon", "ig_date"])
    m = m[~("MTBS:" + m.event_id).isin(dec)]
    m = m[m.ig_date.astype(str).str[:4].astype(int).between(2018, 2025)]
    rows.append(pd.DataFrame(dict(src="MTBS:" + m.event_id, lon=m.burnbndlon.astype(float), lat=m.burnbndlat.astype(float),
                                  t=pd.to_datetime(m.ig_date))))
    j = json.load(open(RAW / "wfigs_ge5000ha_2018_2025_attrs.json"))
    w = pd.DataFrame([f["attributes"] for f in j["features"]])
    w = w[~("WFIGS:" + w.attr_UniqueFireIdentifier).isin(dec) & w.attr_InitialLongitude.notna()]
    rows.append(pd.DataFrame(dict(src="WFIGS:" + w.attr_UniqueFireIdentifier, lon=w.attr_InitialLongitude,
                                  lat=w.attr_InitialLatitude, t=pd.to_datetime(w.attr_FireDiscoveryDateTime, unit="ms"))))
    return pd.concat(rows, ignore_index=True).dropna(subset=["lon", "lat", "t"])


def single_link(xy, r):
    if len(xy) == 0:
        return np.zeros(0, int)
    pairs = cKDTree(xy).query_pairs(r, output_type="ndarray")
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = len(xy)
    A = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n))
    return connected_components(A, directed=False)[1]


def seg_dist(p, a, b):
    """Distance of points p (n,2) to segment a-b."""
    ab = b - a
    L2 = float(ab @ ab)
    if L2 == 0:
        return np.hypot(*(p - a).T)
    t = np.clip(((p - a) @ ab) / L2, 0, 1)
    q = a + t[:, None] * ab
    return np.hypot(*(p - q).T)


def catalogue_fire(fr, det, seps):
    utm = gpd.GeoSeries([fr.geometry], crs=4326).estimate_utm_crs()
    per = gpd.GeoSeries([fr.geometry], crs=4326).to_crs(utm).iloc[0]
    d = det.copy()
    xy = gpd.GeoSeries(gpd.points_from_xy(d.longitude, d.latitude), crs=4326).to_crs(utm)
    d["x"], d["y"] = xy.x.values, xy.y.values
    d = d[d.type == 0]
    t0 = (fr.t0 if pd.notna(fr.t0) else d.t.min()) - pd.Timedelta(days=2)
    d = d[d.t >= t0]
    inper = contains_xy(per.buffer(FINAL_TOL), d.x.values, d.y.values)
    # end of fire: record end + 2 d if known; and the last in-perimeter detection before a >= 21-day gap
    ti = np.sort(d.t[inper].values)
    if len(ti) == 0:
        return None, []
    gaps = np.where(np.diff(ti) > np.timedelta64(int(GAP_END_D * 24), "h"))[0]
    t_end = pd.Timestamp(ti[gaps[0]] if len(gaps) else ti[-1]) + pd.Timedelta(hours=1)
    if pd.notna(fr.t1):
        t_end = min(t_end, fr.t1 + pd.Timedelta(days=2))
    d = d[d.t <= t_end].sort_values("t").reset_index(drop=True)
    if len(d) == 0:
        return None, []
    # epochs
    tt = d.t.values
    new = new_epoch(tt)  # addendum A1: >= 60 min starts an epoch (frozen code: > 60 min)
    d["ep"] = np.cumsum(new) - 1
    T = d.groupby("ep").t.min().values
    d["qual"] = d.confidence.isin(["n", "h"]) & (d.scan <= 0.55) & (d.track <= 0.55)
    # grid
    minx, miny, maxx, maxy = per.bounds
    minx, miny, maxx, maxy = minx - 3000, miny - 3000, maxx + 3000, maxy + 3000
    cs = 125.0
    if (maxx - minx) * (maxy - miny) / cs ** 2 > 4e6:
        cs = 250.0
    nx, ny = int(math.ceil((maxx - minx) / cs)), int(math.ceil((maxy - miny) / cs))
    ci = np.clip(((d.x - minx) / cs).astype(int), 0, nx - 1).values
    cj = np.clip(((maxy - d.y) / cs).astype(int), 0, ny - 1).values
    first_ep = np.full((ny, nx), np.iinfo(np.int32).max, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    X, Y = d.x.values, d.y.values
    tree_all = cKDTree(np.c_[X, Y])
    per_buf = per.buffer(FINAL_TOL)
    sep_xy = None
    if len(seps):
        s = seps[(seps.lon > fr.geometry.bounds[0] - 0.2) & (seps.lon < fr.geometry.bounds[2] + 0.2)
                 & (seps.lat > fr.geometry.bounds[1] - 0.2) & (seps.lat < fr.geometry.bounds[3] + 0.2)]
        s = s[s.src != fr.fire_key]
        if len(s):
            sp = gpd.GeoSeries(gpd.points_from_xy(s.lon, s.lat), crs=4326).to_crs(utm)
            sep_xy = (np.c_[sp.x.values, sp.y.values], s.t.values)
    jumps = []
    n_int = {12: 0, 14: 0, 24: 0}
    ep_arr = d.ep.values
    for i in range(1, len(T)):
        L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
        for h in n_int:
            n_int[h] += L <= h
        if L > 24:
            continue
        cur = np.where(ep_arr == i)[0]
        prev = np.where(ep_arr < i)[0]
        # prefilter: distance to nearest earlier detection must exceed 1,375 m for any candidate
        dd, _ = cKDTree(np.c_[X[prev], Y[prev]]).query(np.c_[X[cur], Y[cur]])
        if not np.any((dd > DIL + CLOSE) & d.qual.values[cur]):
            continue
        # local windows: candidate points (> 375 m from every earlier detection) are clustered; each cluster
        # with a qualifying member > 1,375 m out is evaluated on a window = its bbox + (max distance to an
        # earlier detection + 3 km), which contains its nearest perimeter cell, every detection within 750 m of
        # the line to it and every earlier detection that can affect the 1 km closing there.
        sub = cur[dd > DIL - cs]
        dsub = dd[dd > DIL - cs]
        lab0 = single_link(np.c_[X[sub], Y[sub]], CL_LINK)
        done = set()
        for c0 in np.unique(lab0):
            m0 = lab0 == c0
            if not np.any(m0 & d.qual.values[sub] & (dsub > DIL + CLOSE)):
                continue
            mg = float(dsub[m0].max()) + 3000.0
            wx0, wx1 = X[sub[m0]].min() - mg, X[sub[m0]].max() + mg
            wy0, wy1 = Y[sub[m0]].min() - mg, Y[sub[m0]].max() + mg
            i0, i1 = max(0, int((wx0 - minx) / cs)), min(nx, int((wx1 - minx) / cs) + 1)
            j0, j1 = max(0, int((maxy - wy1) / cs)), min(ny, int((maxy - wy0) / cs) + 1)
            pts = first_ep[j0:j1, i0:i1] < i
            dil = ndimage.distance_transform_edt(~pts) * cs <= DIL
            E = ndimage.distance_transform_edt(~dil) * cs <= CLOSE  # dilation by 1 km
            closed = (ndimage.distance_transform_edt(E) * cs > CLOSE - cs) | dil  # erosion = closing
            dist, (ii, jj) = ndimage.distance_transform_edt(~closed, return_indices=True)
            dist = dist * cs
            inw = cur[(ci[cur] >= i0) & (ci[cur] < i1) & (cj[cur] >= j0) & (cj[cur] < j1)]
            dc = dist[cj[inw] - j0, ci[inw] - i0]
            out = inw[dc > 0]
            if len(out) == 0:
                continue
            lab = single_link(np.c_[X[out], Y[out]], CL_LINK)
            seed = set(sub[m0].tolist())
            for c in np.unique(lab):
                mem = out[lab == c]
                if not seed.intersection(mem.tolist()):
                    continue
                q = mem[d.qual.values[mem]]
                if len(q) == 0:
                    continue
                dq = dist[cj[q] - j0, ci[q] - i0]
                k = int(q[np.argmin(dq)])
                dmin = float(dq.min())
                if dmin < 1000 or k in done:
                    continue
                done.add(k)
                pj, pi = ii[cj[k] - j0, ci[k] - i0] + j0, jj[cj[k] - j0, ci[k] - i0] + i0
                px, py = minx + (pi + 0.5) * cs, maxy - (pj + 0.5) * cs
                a, b = np.array([X[k], Y[k]]), np.array([px, py])
                others = np.setdiff1d(out, mem)
                line_ok = not (len(others) and np.any(seg_dist(np.c_[X[others], Y[others]], a, b) <= LINE_TOL))
                inside = bool(contains_xy(per_buf, X[k], Y[k]))
                sep_ok = True
                if sep_xy is not None:
                    lo, hi = T[i - 1] - np.timedelta64(1, "D"), T[i] + np.timedelta64(1, "D")
                    sel = (sep_xy[1] >= lo) & (sep_xy[1] <= hi)
                    if sel.any():
                        sep_ok = bool(np.min(np.hypot(*(sep_xy[0][sel] - a).T)) >= SEP_START)
                mid = (a + b) / 2
                if not contains_xy(per, mid[0], mid[1]):
                    gap = "unburned"
                else:
                    near = tree_all.query_ball_point(mid, 375.0)
                    gap = "later_detected" if any(ep_arr[n] >= i for n in near) else "never_detected"
                jumps.append(dict(fire_key=fr.fire_key, region=fr.region, year=fr.year, assignment=fr.assignment,
                                  epoch=i, t_start=pd.Timestamp(T[i - 1]), t_end=pd.Timestamp(T[i]), interval_h=L,
                                  dist_m=dmin, n_members=len(mem), n_qual=len(q), satellite=d.satellite.values[k],
                                  daynight=d.daynight.values[k], frp=float(d.frp.values[k]), line_ok=line_ok,
                                  inside_final=inside, out_final_m=float(per.distance(Point(X[k], Y[k]))) if not inside else 0.0,
                                  sep_ok=sep_ok, gap=gap, x_utm=float(X[k]), y_utm=float(Y[k])))
    info = dict(fire_key=fr.fire_key, region=fr.region, year=fr.year, assignment=fr.assignment,
                n_det=len(d), n_epochs=len(T), n_int_12h=int(n_int[12]), n_int_14h=int(n_int[14]), n_int_24h=int(n_int[24]),
                t_first=str(d.t.min()), t_last=str(d.t.max()), cell_m=cs, area_ha=per.area / 1e4)
    return info, jumps


def catalogue():
    s, tr, dec = split()
    g = gpd.read_file(CACHE / "training_perimeters.gpkg")
    g["t0"] = pd.to_datetime(g.t0)
    g["t1"] = pd.to_datetime(g.t1)
    assert set(g.assignment) <= {"training", "training_legacy"}, "decision fire in training mode"
    seps = separate_starts(dec)
    infos, jumps = [], []
    t0 = time.time()
    for y in YEARS:
        p = CACHE / f"viirs_training_{y}.pkl.gz"
        if not p.exists():
            continue
        det = pd.read_pickle(p)
        for fr in g[g.year == y].itertuples():
            dd = det[det.fire_key == fr.fire_key]
            if len(dd) == 0:
                infos.append(dict(fire_key=fr.fire_key, region=fr.region, year=fr.year, assignment=fr.assignment, n_det=0))
                continue
            info, js = catalogue_fire(fr, dd, seps)
            infos.append(info or dict(fire_key=fr.fire_key, region=fr.region, year=fr.year, assignment=fr.assignment, n_det=0))
            jumps += js
        print(y, "done", len(jumps), "candidate clusters", f"{time.time()-t0:.0f}s", flush=True)
        RES.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(jumps).to_csv(RES / "jumps_training_partial.csv", index=False)
    RES.mkdir(parents=True, exist_ok=True)
    J = pd.DataFrame(jumps)
    J.to_csv(RES / "jumps_training.csv", index=False)
    F = pd.DataFrame(infos)
    F.to_csv(RES / "fires_training_raw.csv", index=False)
    print("written", len(J), len(F))


if __name__ == "__main__":
    {"extract": extract, "catalogue": catalogue}[sys.argv[1]]()
