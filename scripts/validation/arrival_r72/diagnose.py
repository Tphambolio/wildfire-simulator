#!/usr/bin/env python3
"""[R72] stage 1: observational and baseline-model diagnostics per C1b community arrival.

Purpose
    For every reached community of the 20 C1b fire-days (14 primary, 6 Horse River calibration;
    PREREG_COMPARATOR_C1B, already scored in [R66] §12) describe why FireSim's operational run
    arrives late or not at all. Exploratory and descriptive: nothing is tuned or claimed.

What it computes (per community; times in h from 06:00 local = UTC-6 on burn day n)
    geometry   D0 = distance from FireSim's start area (stored C1b arrival <= 0) to the community's
               500 m reach zone; D0_active = from the active-edge cells (CFSDS DOB in the 2 days
               before n); bearing from the nearest start cell to the zone.
    (a) ROS    observed interval [lo, hi] (communities_c1b); observed mean approach rate bounds
               D0 / (hi + 6) .. D0 / (max(lo, -6) + 6) (the start area is CFSDS's burned area at the
               end of day n-1, i.e. at -6 h at the latest); the stored baseline's distance to the
               zone at time t, d_sim(t) (0..24 h by 15 min) and its progress at hi; the factor
               F_need = D0 / (D0 - d_sim(hi)) the approach would have to be faster; the VIIRS
               distance sequence d_obs(pass) = distance from (start area U detections up to the
               pass, inside the CFSDS final area + 2 km) to the zone.
    (b) wind   ERA5 hourly (the run's stream): mean speed and the angle between the wind's
               "toward" direction and the start->community bearing, over the burning period
               (10-20 h local) and over 0..hi. Station comparison: weather_obs.py.
    (c) fuel   FBP type mix (seasonal labels as run) in a 1 km corridor around the straight path
               nearest start cell -> nearest zone cell, for the back-cast grid used and for the CFS
               2014b national grid on the same lattice; grass curing used; share of the corridor
               the observed fire burned on day n or earlier.
    (d) codes  FFMC/DMC/DC start codes, CFSDS BUI/FWI, FireSim's hourly FFMC; share of the observed
               interval outside the 10-20 h burning period; class: 'pre-start' (hi <= 0),
               'in burning period' (interval inside 4..14 h), 'after burning period' (lo >= 14 h),
               'spans the end' (lo < 14 < hi).
    (e) jumps  [R53]/v8 jump rules (PREREG_LR8 addendum A1, scripts/validation/v8_rule/
               v8_viirs_jumps.py at 2db8c34, vendored byte-identical as v8_viirs_jumps_A1.py):
               the v8 catalogue is run per fire (final perimeter = CFSDS DOB > 0; separate-start
               mask not applied = the v8 'no_sep' sensitivity) and its jumps on day n are counted;
               a community's arrival is a jump if a primary-rule jump (1-15 km, line check, inside
               final, interval <= 12 h) at the epoch of the zone's first detection lies within
               750 m of the zone. Also the distance of that first zone detection to the v8 start
               perimeter (earlier detections, 375 m dilation + 1 km closing) and to FireSim's
               modelled front at that time.
    (f) start  number of start cells, active (burning) vs burned-out; whether the nearest start
               cell to the zone is active.

Outputs (OUT = reports/data/arrival-underspread-r72-2026-10-29/)
    diagnose_rows.json   one record per community (and per fire-day summaries, v8 jumps)
Usage
    CAPJOB_MEM=5G capjob python3 scripts/validation/arrival_r72/diagnose.py
"""
from __future__ import annotations

import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402
import communities_c1b as CB  # noqa: E402
import v8_viirs_jumps_A1 as V8  # noqa: E402

VIIRS_RAW = K.VD / "viirs/raw"
CORRIDOR_M = 1000.0
BP_RUN = (4.0, 14.0)  # burning period 10-20 h local in run hours


@lru_cache(maxsize=4)
def viirs_year(year: int) -> pd.DataFrame:
    """All VIIRS 375 m detections of a year in the Alberta box with the v8 columns; t = UTC."""
    cols = ["latitude", "longitude", "scan", "track", "acq_date", "acq_time", "satellite",
            "confidence", "daynight", "type", "frp"]
    fr = []
    for p in sorted(VIIRS_RAW.glob(f"viirs-*_{year}_Canada.csv")):
        f = pd.read_csv(p, usecols=cols, dtype={"acq_time": str})
        f = f[(f.longitude > -120.5) & (f.longitude < -109.5) & (f.latitude > 48.5)]
        fr.append(f)
    d = pd.concat(fr, ignore_index=True)
    d["t"] = pd.to_datetime(d.acq_date + " " + d.acq_time.str.zfill(4), format="%Y-%m-%d %H%M")
    d["local"] = d.t + pd.Timedelta(hours=CB.C.UTC_OFFSET_H)
    return d.drop(columns=["acq_date", "acq_time"])


def grid_rc(dom, lat, lng):
    r = np.floor((dom.lat_max - np.asarray(lat)) / dom.cell_lat).astype(int)
    c = np.floor((np.asarray(lng) - dom.lng_min) / dom.cell_lng).astype(int)
    ok = (r >= 0) & (r < dom.rows) & (c >= 0) & (c < dom.cols)
    return r, c, ok


def final_polygon(full_dom):
    """CFSDS final burned area (DOB > 0) of the whole fire as one shapely geometry (EPSG:4326)."""
    from rasterio.features import shapes
    from rasterio.transform import Affine
    from shapely.geometry import shape
    from shapely.ops import unary_union

    m = full_dom.dob > 0
    tr = Affine(full_dom.cell_lng, 0.0, full_dom.lng_min, 0.0, -full_dom.cell_lat, full_dom.lat_max)
    return unary_union([shape(g) for g, _ in shapes(m.astype(np.uint8), mask=m, transform=tr)])


@lru_cache(maxsize=16)
def v8_jumps_for_fire(fid: str) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """The v8 (A1) jump catalogue of a whole fire, from the vendored code, final = CFSDS."""
    import geopandas as gpd

    full, groups, _ = K.load_fire(fid)
    geom = final_polygon(full)
    from firesim.validation.weather import doy_to_date

    t0 = pd.Timestamp(doy_to_date(full.year, min(full.burn_days()))) - pd.Timedelta(hours=CB.C.UTC_OFFSET_H)
    t1 = pd.Timestamp(doy_to_date(full.year, max(full.burn_days()))) + pd.Timedelta(days=1) - \
        pd.Timedelta(hours=CB.C.UTC_OFFSET_H)
    v = viirs_year(full.year)
    b = geom.bounds
    v = v[(v.longitude > b[0] - 0.1) & (v.longitude < b[2] + 0.1) & (v.latitude > b[1] - 0.1) &
          (v.latitude < b[3] + 0.1)]
    utm = gpd.GeoSeries([geom], crs=4326).estimate_utm_crs()
    buf = gpd.GeoSeries([geom], crs=4326).to_crs(utm).buffer(2000).to_crs(4326).iloc[0]
    from shapely import contains_xy

    v = v[contains_xy(buf, v.longitude.values, v.latitude.values)].copy()
    # the v8 fire window (catalogue_fire keeps t >= record start - 2 d; end = record end + 2 d)
    v = v[(v.t >= t0 - pd.Timedelta(days=2)) & (v.t <= t1 + pd.Timedelta(days=2))]
    fr = pd.Series(dict(fire_key=f"CFSDS:{fid}", geometry=geom, t0=t0, t1=t1 + pd.Timedelta(days=0),
                        region="AB", year=full.year, assignment="r72_diagnostic"))
    info, jumps = V8.catalogue_fire(fr, v.drop(columns=["local"]), pd.DataFrame())
    J = pd.DataFrame(jumps)
    if len(J):
        J["t_end_local"] = J.t_end + pd.Timedelta(hours=CB.C.UTC_OFFSET_H)
        J["t_start_local"] = J.t_start + pd.Timedelta(hours=CB.C.UTC_OFFSET_H)
        ll = gpd.GeoSeries(gpd.points_from_xy(J.x_utm, J.y_utm), crs=utm).to_crs(4326)
        J["lng"], J["lat"] = ll.x.values, ll.y.values
        J["primary"] = ((J.dist_m >= 1000) & (J.dist_m <= 15000) & J.line_ok & J.inside_final &
                        (J.interval_h <= 12))
    return J, info or {}, v


def v8_start_perimeter_distance(prev_xy: np.ndarray, q_xy: np.ndarray, cs: float = 125.0) -> np.ndarray:
    """Distance (m) of points ``q_xy`` to the v8 start perimeter built from earlier detections
    ``prev_xy`` (375 m dilation + 1 km closing on a 125 m grid, as V8.catalogue_fire)."""
    if len(prev_xy) == 0:
        return np.full(len(q_xy), np.inf)
    allxy = np.r_[prev_xy, q_xy]
    mg = 6000.0
    minx, maxy = allxy[:, 0].min() - mg, allxy[:, 1].max() + mg
    nx = int(math.ceil((allxy[:, 0].max() + mg - minx) / cs))
    ny = int(math.ceil((maxy - (allxy[:, 1].min() - mg)) / cs))
    if nx * ny > 16e6:
        cs = 250.0
        nx, ny = int(math.ceil(nx * 125 / cs)), int(math.ceil(ny * 125 / cs))
    pts = np.zeros((ny, nx), bool)
    pi = np.clip(((prev_xy[:, 0] - minx) / cs).astype(int), 0, nx - 1)
    pj = np.clip(((maxy - prev_xy[:, 1]) / cs).astype(int), 0, ny - 1)
    pts[pj, pi] = True
    dil = ndimage.distance_transform_edt(~pts) * cs <= V8.DIL
    E = ndimage.distance_transform_edt(~dil) * cs <= V8.CLOSE
    closed = (ndimage.distance_transform_edt(E) * cs > V8.CLOSE - cs) | dil
    dist = ndimage.distance_transform_edt(~closed) * cs
    qi = np.clip(((q_xy[:, 0] - minx) / cs).astype(int), 0, nx - 1)
    qj = np.clip(((maxy - q_xy[:, 1]) / cs).astype(int), 0, ny - 1)
    return dist[qj, qi]


def seg_mask(dom, p, q, width_m):
    """Cells within ``width_m`` of the segment p -> q (row, col) on the domain grid."""
    X, Y = K.cell_xy(dom)
    a = np.array([(p[1] + 0.5) * dom.dx, (p[0] + 0.5) * dom.dy])
    b = np.array([(q[1] + 0.5) * dom.dx, (q[0] + 0.5) * dom.dy])
    pts = np.c_[X.ravel(), Y.ravel()]
    return (V8.seg_dist(pts, a, b) <= width_m).reshape(dom.rows, dom.cols)


def fuel_mix(types: np.ndarray, mask: np.ndarray) -> dict:
    vals = [("NF" if t is None else t.value) for t in types[mask]]
    if not vals:
        return {}
    u, n = np.unique(vals, return_counts=True)
    return {k: round(float(c) / len(vals), 4) for k, c in zip(u, n)}


def fuel_class(mix: dict) -> dict:
    g = lambda keys: round(sum(v for k, v in mix.items() if k in keys), 4)  # noqa: E731
    return {"conifer": g({"C1", "C2", "C3", "C4", "C5", "C6", "C7"}),
            "mixed": g({"M1", "M2", "M3", "M4"}), "deciduous": g({"D1", "D2"}),
            "grass": g({"O1a", "O1b"}), "slash": g({"S1", "S2", "S3"}), "nonfuel": g({"NF"})}


def burning_class(lo: float, hi: float) -> tuple[str, float]:
    a, b = BP_RUN
    w = max(hi - lo, 1e-9)
    inside = max(0.0, min(hi, b) - max(lo, a))
    share_out = 1.0 - inside / w if hi > lo else (0.0 if a <= lo <= b else 1.0)
    if hi <= 0:
        return "pre-start", share_out
    if lo >= b:
        return "after burning period", share_out
    if lo >= a - 1e-9 and hi <= b:
        return "in burning period", share_out
    if lo < b < hi:
        return "spans the end", share_out
    return "before burning period", share_out


def arrival_jump(zdet, vfire, J, dom, prior, base, t0) -> dict:
    """Classify a community's observed arrival with the v8 jump rule (A1 code, vendored).

    The epoch of the zone's first VIIRS detection on day n is found in the fire's own epoch
    sequence (V8.new_epoch: acquisitions < 60 min apart merge). Detections of that epoch outside
    the v8 start perimeter (earlier detections, 375 m dilation + 1 km closing) are linked at
    750 m (V8.single_link); the cluster(s) holding the zone's detections are looked up in the v8
    catalogue of the same epoch (V8.catalogue_fire output, matched by the jump member's
    coordinates). Classes: 'v8 jump' (a catalogue jump in the zone's cluster; ``primary`` adds
    1-15 km, line check, inside final, interval <= 12 h; ``int14`` / ``int24`` the v8
    sensitivities), 'front run' (zone cluster outside the start perimeter, not a catalogue
    jump: new detections contiguous with the earlier ones, or nearest member < 1 km out),
    'within start perimeter' (the zone's first detections touch the v8 start perimeter),
    'no zone detection on day n' (arrival timed by CFSDS only).
    """
    import geopandas as gpd

    if len(zdet) == 0:
        return {"class": "no zone detection on day n"}
    vf = vfire[vfire.type == 0].sort_values("t").reset_index(drop=True)
    ep = np.cumsum(V8.new_epoch(vf.t.values)) - 1
    T = vf.groupby(ep).t.min().values
    tz = zdet.t.min()
    i = int(ep[np.searchsorted(vf.t.values, np.datetime64(tz), side="left")])
    utm = gpd.GeoSeries(gpd.points_from_xy(vf.longitude, vf.latitude), crs=4326).estimate_utm_crs()
    xy = gpd.GeoSeries(gpd.points_from_xy(vf.longitude, vf.latitude), crs=4326).to_crs(utm)
    X, Y = xy.x.values, xy.y.values
    prev, cur = np.where(ep < i)[0], np.where(ep == i)[0]
    d_cur = v8_start_perimeter_distance(np.c_[X[prev], Y[prev]], np.c_[X[cur], Y[cur]])
    zr, zc, zok = grid_rc(dom, vf.latitude.values[cur], vf.longitude.values[cur])
    zone_cell = np.zeros(len(cur), bool)
    zmask = np.zeros((dom.rows, dom.cols), bool)
    zmask[zdet.r.values, zdet.c.values] = True
    zone_cell[zok] = zmask[zr[zok], zc[zok]]
    interval_h = float((T[i] - T[i - 1]) / np.timedelta64(1, "h")) if i > 0 else math.inf
    # with the CFSDS start area as extra earlier "detections"
    rr, cc = np.nonzero(prior)
    sxy = gpd.GeoSeries(gpd.points_from_xy(dom.lng_min + (cc + 0.5) * dom.cell_lng,
                                           dom.lat_max - (rr + 0.5) * dom.cell_lat), crs=4326).to_crs(utm)
    d_cur2 = v8_start_perimeter_distance(np.r_[np.c_[X[prev], Y[prev]], np.c_[sxy.x, sxy.y]],
                                         np.c_[X[cur][zone_cell], Y[cur][zone_cell]])
    hz = float((pd.Timestamp(tz) + pd.Timedelta(hours=CB.C.UTC_OFFSET_H) - t0) / pd.Timedelta(hours=1))
    front = base <= hz * 60.0
    dfront = math.nan
    if front.any():
        Df = ndimage.distance_transform_edt(~front, sampling=(dom.dy, dom.dx))
        dfront = float(np.min(Df[zr[zok & zone_cell], zc[zok & zone_cell]])) if (zok & zone_cell).any() else math.nan
    out = {"epoch_h": round(hz, 2), "interval_h": round(interval_h, 2), "n_zone_det": int(zone_cell.sum()),
           "zone_dist_to_v8_perimeter_m": float(d_cur[zone_cell].min()),
           "zone_dist_to_v8_perimeter_plus_cfsds_m": float(d_cur2.min()) if len(d_cur2) else math.nan,
           "zone_dist_to_model_front_m": dfront}
    outside = d_cur > 0
    if not (zone_cell & outside).any():
        out["class"] = "within start perimeter"
        return out
    o_idx = np.where(outside)[0]
    lab = V8.single_link(np.c_[X[cur][o_idx], Y[cur][o_idx]], V8.CL_LINK)
    zl = set(lab[zone_cell[o_idx]].tolist())
    members = o_idx[np.isin(lab, list(zl))]
    mxy = np.c_[X[cur][members], Y[cur][members]]
    jj = J[J.t_end == pd.Timestamp(T[i])] if len(J) else J
    hit = None
    for _, j in (jj.iterrows() if len(jj) else []):
        if np.min(np.hypot(mxy[:, 0] - j.x_utm, mxy[:, 1] - j.y_utm)) < 1.0:
            hit = j if hit is None or j.dist_m > hit.dist_m else hit
    if hit is None:
        out["class"] = "front run"
        out["cluster_n"] = int(len(members))
        return out
    base_ok = bool((hit.dist_m >= 1000) & (hit.dist_m <= 15000) & hit.line_ok & hit.inside_final)
    out.update({"class": "v8 jump", "jump_dist_m": float(hit.dist_m), "line_ok": bool(hit.line_ok),
                "gap": hit.gap, "primary": base_ok and interval_h <= 12, "int14": base_ok and interval_h <= 14,
                "int24": base_ok and interval_h <= 24, "strict": base_ok and interval_h <= 12 and hit.gap != "never_detected",
                "cluster_n": int(len(members))})
    return out


def diag_day(fid: str, day: int, pop: str, run_row: dict) -> list[dict]:
    from firesim.validation.cfsds import FireDomain
    from firesim.validation.harness import crop_for_day, fuel_grid_for

    case = K.load_case(fid, day)
    dom = case.domain
    base = K.stored_baseline(fid, day)
    start = base <= 0
    prior = (dom.dob > 0) & (dom.dob < day)
    active = (dom.dob >= day - 2) & (dom.dob < day) & (dom.dob > 0)
    obs = dom.dob == day
    reached, unreached = CB.observed_intervals(case)
    opts = K.opts_operational()
    types = np.array(fuel_grid_for(dom, day, opts).fuel_types, dtype=object).reshape(dom.rows, dom.cols)
    d14 = K.VD / "c1b/domains2014b" / f"{fid}.npz"
    types14 = None
    if d14.exists():
        f14 = FireDomain.load(d14)
        if f14.dob.shape == K.load_fire(fid)[0].dob.shape:
            c14 = crop_for_day(f14, day, 20000.0)
            types14 = np.array(fuel_grid_for(c14, day, opts).fuel_types, dtype=object).reshape(dom.rows, dom.cols)
    from firesim.validation.harness import grass_cure_for

    cure = grass_cure_for(day, opts)
    hff = {int(k): v for k, v in (run_row.get("wise_meta", {}).get("hourly_ffmc") or {}).items()}
    # VIIRS on this domain from -6 h to +24 h, inside the CFSDS final area + 2 km
    v = viirs_year(dom.year)
    t0 = pd.Timestamp(case.start)
    vd = v[(v.local >= t0 - pd.Timedelta(hours=6)) & (v.local <= t0 + pd.Timedelta(hours=24))]
    r, c, ok = grid_rc(dom, vd.latitude.values, vd.longitude.values)
    vd = vd[ok].assign(r=r[ok], c=c[ok])
    near_final = ndimage.distance_transform_edt(~(dom.dob > 0), sampling=(dom.dy, dom.dx)) <= 2000
    vd = vd[near_final[vd.r.values, vd.c.values]]
    vd["h"] = (vd.local - t0) / pd.Timedelta(hours=1)
    passes = CB._passes(vd.h.values * 60.0)
    J, finfo, vfire = v8_jumps_for_fire(fid)
    import geopandas as gpd

    utm = None
    out = []
    hourly = case.hourly
    t_grid = np.arange(0, 24.01, 0.25)
    for comm in reached:
        m = comm["mask"]
        if (m & start).any():
            continue
        Dz = ndimage.distance_transform_edt(~m, sampling=(dom.dy, dom.dx))
        D0 = float(Dz[start].min())
        D0a = float(Dz[active].min()) if active.any() else math.nan
        p = np.unravel_index(int(np.argmin(np.where(start, Dz, np.inf))), Dz.shape)
        zr, zc = np.nonzero(m)
        k = int(np.argmin(np.hypot((zr - p[0]) * dom.dy, (zc - p[1]) * dom.dx)))
        q = (int(zr[k]), int(zc[k]))
        bearing = K.bearing_deg((q[1] - p[1]) * dom.dx, (p[0] - q[0]) * dom.dy)
        lo, hi = comm["lo"], comm["hi"]
        # (a) model distance curve and progress
        dsim = np.array([float(Dz[base <= t * 60.0].min()) if (base <= t * 60.0).any() else D0
                         for t in t_grid])
        a_sim = float(np.min(base[m])) / 60.0
        a_sim = 24.0 if not math.isfinite(a_sim) else min(a_sim, 24.0)
        d_at_hi = float(np.interp(max(hi, 0.0), t_grid, dsim))
        prog = (D0 - d_at_hi) / D0 if D0 > 0 else 1.0
        f_need = D0 / (D0 - d_at_hi) if D0 - d_at_hi > 1e-6 else math.inf
        # VIIRS distance sequence (cumulative)
        seq = []
        cum = np.inf
        for ps in passes:
            h0 = ps / 60.0
            sel = vd[(vd.h >= h0) & (vd.h < h0 + 20 / 60.0 + 1.0)]
            sel = sel[sel.h < h0 + 1.0]
            if len(sel):
                cum = min(cum, float(Dz[sel.r.values, sel.c.values].min()))
            seq.append([round(h0, 3), round(min(D0, cum), 1)])
        r_obs_lo = D0 / (hi + 6.0) / 60 if hi > -6 else math.nan  # m/min
        r_obs_hi = D0 / (max(lo, -6.0) + 6.0) / 60 if lo > -6 else math.inf
        # (b) wind
        def wind_stats(h0, h1):
            hs = [h for h in hourly if h0 <= h.hours_from_start < h1]
            if not hs:
                return {"n": 0}
            toward = [(h.wind_direction + 180.0) % 360.0 for h in hs]
            ang = [K.angdiff(t, bearing) for t in toward]
            return {"n": len(hs), "ws_mean_kmh": round(float(np.mean([h.wind_speed for h in hs])), 2),
                    "ws_max_kmh": round(float(np.max([h.wind_speed for h in hs])), 2),
                    "angle_mean_deg": round(float(np.mean(ang)), 1),
                    "angle_ws_weighted_deg": round(float(np.average(ang, weights=[h.wind_speed + 1e-6 for h in hs])), 1),
                    "rh_min": round(float(np.min([h.relative_humidity for h in hs])), 1),
                    "t_max": round(float(np.max([h.temperature for h in hs])), 1)}
        # (c) fuel corridor
        cor = seg_mask(dom, p, q, CORRIDOR_M) & ~start
        mix = fuel_mix(types, cor)
        mix14 = fuel_mix(types14, cor) if types14 is not None else {}
        # (d)
        bclass, share_out = burning_class(lo, hi)
        # (e) jump at arrival (v8 rule applied at the epoch of the zone's first detection)
        jump = arrival_jump(vd[m[vd.r.values, vd.c.values]], vfire, J, dom, prior, base, t0)
        jump["first_zone_detection_h"] = comm.get("viirs_first_h")
        out.append({
            "fire_day": f"{fid}:{day}", "population": pop, "name": comm["name"],
            "lo": lo, "hi": hi, "model_h": a_sim, "err_h": CB.interval_error(a_sim, lo, hi),
            "D0_m": D0, "D0_active_m": D0a, "nearest_start_active": bool(active[p]),
            "bearing_deg": round(bearing, 1),
            "obs_rate_m_min": [r_obs_lo, r_obs_hi],
            "model_dist_at_hi_m": d_at_hi, "model_progress_at_hi": prog, "F_need": f_need,
            "model_dist_24h_m": float(dsim[-1]),
            "model_mean_rate_m_min_0_24": (D0 - dsim[-1]) / (24 * 60),
            "dsim_curve": [[float(t), round(float(d), 1)] for t, d in zip(t_grid[::2], dsim[::2])],
            "viirs_seq": seq,
            "wind_bp": wind_stats(*BP_RUN), "wind_to_hi": wind_stats(0.0, max(hi, 1.0)),
            "wind_0_24": wind_stats(0.0, 24.0),
            "fuel_mix": mix, "fuel_class": fuel_class(mix), "fuel_mix_2014b": mix14,
            "fuel_class_2014b": fuel_class(mix14) if mix14 else {},
            "corridor_obs_burned_share": round(float(((dom.dob > 0) & (dom.dob <= day))[cor].mean()) if cor.any() else math.nan, 3),
            "corridor_cells": int(cor.sum()),
            "grass_cure": cure, "doy": day, "date": case.start.date().isoformat(),
            "ffmc0": case.ffmc, "dmc0": case.dmc, "dc0": case.dc, "bui": case.bui, "fwi": case.fwi,
            "hourly_ffmc": hff,
            "burning_class": bclass, "interval_share_outside_bp": round(share_out, 3),
            "jump": jump,
        })
    # fire-day summary of v8 jumps on day n (-6..24 h local)
    jd = J[(J.t_end_local >= t0 - pd.Timedelta(hours=6)) & (J.t_end_local <= t0 + pd.Timedelta(hours=24))] if len(J) else J
    summ = {"fire_day": f"{fid}:{day}", "population": pop, "summary": True,
            "n_start": int(start.sum()), "n_active_cells": int((start & active).sum()),
            "obs_growth_ha": float(obs.sum() * dom.cell_area_m2 / 1e4),
            "n_passes": len(passes), "passes_h": [round(x / 60, 2) for x in passes],
            "v8_jumps_day": int(len(jd)), "v8_primary_jumps_day": int(jd.primary.sum()) if len(jd) else 0,
            "v8_jumps_day_rows": jd.drop(columns=["t_start", "t_end", "t_start_local", "t_end_local"], errors="ignore")
            .assign(t_end_h=((jd.t_end_local - t0) / pd.Timedelta(hours=1)) if len(jd) else [])
            .to_dict("records") if len(jd) else []}
    return out + [summ]


def main():
    rows_c1b = {}
    for line in (K.C1B_DATA / "c1b_runs.jsonl").read_text().splitlines():
        r = json.loads(line)
        rows_c1b[(r["fire_id"], r["day"])] = r
    res = []
    for fid, day, pop in K.all_days():
        print(fid, day, flush=True)
        res += diag_day(fid, day, pop, rows_c1b[(fid, day)])
    K.OUT.mkdir(parents=True, exist_ok=True)
    (K.OUT / "diagnose_rows.json").write_text(json.dumps(res, indent=1, default=lambda o: o if not isinstance(o, (np.generic,)) else o.item()))
    print("written", len(res))


if __name__ == "__main__":
    main()
