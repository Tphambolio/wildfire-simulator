"""PREREG_LR8S [R73] section 3-4: M8 and B1 from FireSim's simulated active edge, per v8 interval.

Purpose
    For each fire-day D (local standard time, offset round(longitude / 15) h), FireSim's operational set-up
    (PREREG_COMPARATOR_C1 section 4) is run through ``firesim.validation.harness`` from the observed start of day D
    (the PREREG_LR8 section 4 start perimeter of every detection before 06:00 D). For every valid v8 interval whose
    start t0 lies in [06:00 D, 06:00 D+1), the simulated perimeter P_sim(t0), its active edge and the simulated spread
    rate replace the observed ones as M8's sources and B1's distance; the candidate cells stay those of the observed
    start perimeter (identical to ``intervals.py``; checked against the registered interval files where they exist).

What is written (``fires/<fire>/simedge/``)
    ``<epoch>.npz``: bj, bi (candidate blocks, as ``intervals.py``), A (M8 landing statistics from the simulated
    edge), zone, u10, m_ff, hazshare, d_act / sec_act (B1 terms from the simulated active edge), d_all / sec_all (from
    the OBSERVED perimeter, as registered), in_psim (candidate block centre inside P_sim(t0)); scores are
    ``intervals.m8_score`` / ``intervals.b1_score`` on these files.
    ``_done.json``: per-day run status (ok / error / timeout / no start), run seconds, intervals written or dropped.

Fixed choices (PREREG_LR8S section 3; deviations from its text are listed in the dated addendum A1 of that file)
    - FireSim grid: lat/lng domain at 125 m nominal, nearest-neighbour from the v8 UTM grid; arrival mapped back
      nearest-neighbour; cropped by ``harness.crop_for_day`` (20 km margin).
    - Start: DOB surrogate, every cell of P_obs(D) burned (DOB 1), cells with a detection in the 48 h before 06:00 D
      DOB D-1, so ``RunOptions(ignition="active", active_days=2)`` makes exactly those the active edges.
    - O-1 curing: the harness takes one value per run; the median v8 NDVI curing of the fire's grass cells
      (``inputs.npz`` curing where code is 31/32), as a percentage.
    - Weather: the zone station holding most active-edge cells of P_obs(D); hourly from 06:00 D (24 h) and the
      spin-up hours from 17:00 D-1; start FFMC and DMC/DC = the station file's values at 17:00 D-1 local.
    - Wall-time cap 2 h per fire-day (SIGALRM); a capped or failed day drops its intervals for the sim models.

Usage (``scripts/validation``; ``PYTHONPATH=<repo>/engine/src``; under ``capjob``):
    python3 -m v8.simedge FIRE_KEY [...]     # v8 training fires during development
"""
from __future__ import annotations

import json
import math
import signal
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy import ndimage
from scipy.signal import fftconvolve

from . import common as C
from . import intervals as IV

DAY_CAP_S = 7200
MARGIN_M = 20000.0


def local_offset_h(lng: float) -> int:
    return int(round(lng / 15.0))


def close_mask(pts: np.ndarray, cs: float) -> np.ndarray:
    """PREREG_LR8 section 4 start perimeter of a point mask (375 m dilation, 1 km closing), as intervals.py."""
    dil = ndimage.distance_transform_edt(~pts) * cs <= IV.DIL
    E = ndimage.distance_transform_edt(~dil) * cs <= IV.CLOSE
    return (ndimage.distance_transform_edt(E) * cs > IV.CLOSE - cs) | dil


def elevation(fire_key: str, meta: dict, rec: dict) -> np.ndarray:
    """Open terrain on the 125 m run grid (A2.11 sources), cached per fire."""
    from rasterio.enums import Resampling
    from . import inputs as I
    from .elmfire import MRDEM
    p = C.fire_dir(fire_key) / "simedge" / "elev.npy"
    if p.exists():
        return np.load(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    g = meta["grid"]
    if rec["region"] == "US":
        e = I.lf_export("LF2020", "Elev", g, g["cell"]).astype(np.float32)
    else:
        e = I.warp_to_grid(MRDEM, g, Resampling.bilinear).astype(np.float32)
    e = np.where(np.isfinite(e) & (e > -500), e, np.nan)
    if np.isnan(e).all():
        e = np.zeros_like(e)
    e = np.where(np.isnan(e), np.nanmean(e), e).astype(np.float32)
    np.save(p, e)
    return e


def latlng_frame(g: dict):
    """(dst transform, shape, lat_max, lng_min, cell_lat, cell_lng) of a lat/lng grid covering the UTM grid."""
    from rasterio.warp import transform_bounds
    from rasterio.transform import from_origin
    cs = g["cell"]
    l, b, r, t = transform_bounds(g["crs"], "EPSG:4326", g["x0"], g["y1"] - g["ny"] * cs, g["x0"] + g["nx"] * cs, g["y1"])
    mid = 0.5 * (b + t)
    clat = cs / 111320.0
    clng = cs / (111320.0 * math.cos(math.radians(mid)))
    nr, nc = int(math.ceil((t - b) / clat)), int(math.ceil((r - l) / clng))
    return from_origin(l, t, clng, clat), (nr, nc), t, l, clat, clng


def to_latlng(a, g, frame, nearest=True):
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    tr, shape = frame[0], frame[1]
    out = np.zeros(shape, a.dtype)
    reproject(a, out, src_transform=IV_grid_transform(g), src_crs=g["crs"], dst_transform=tr, dst_crs="EPSG:4326",
              resampling=Resampling.nearest if nearest else Resampling.bilinear)
    return out


def IV_grid_transform(g):
    from . import inputs as I
    return I.grid_transform(g)


def to_utm(a, g, tr):
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    big = 1e9
    src = np.where(np.isfinite(a), a, big).astype(np.float32)
    out = np.full((g["ny"], g["nx"]), big, np.float32)
    reproject(src, out, src_transform=tr, src_crs="EPSG:4326", dst_transform=IV_grid_transform(g),
              dst_crs=g["crs"], resampling=Resampling.nearest, src_nodata=big, dst_nodata=big)
    return np.where(out >= big, np.inf, out)


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


def simulate_day(fire_id, year, doy, start_local: datetime, wx: pd.DataFrame, off_h: int, fuel_ll, elev_ll, dob_ll,
                 frame, cure_pct: float):
    """One harness run of FireSim's operational set-up; returns (arrival minutes on the cropped domain, its domain)."""
    from firesim.validation.cfsds import FireDomain
    from firesim.validation import harness as H
    from firesim.types import HourlyWeather
    dom = FireDomain(fire_id=fire_id, year=year, dob=dob_ll.astype(np.int32), fuel=fuel_ll.astype(np.int32),
                     elevation=elev_ll.astype(np.float32), lat_max=frame[2], lng_min=frame[3], cell_lat=frame[4],
                     cell_lng=frame[5], fuel_scheme="cfs_national")
    dom = H.crop_for_day(dom, doy, MARGIN_M)
    s_utc = pd.Timestamp(start_local) - pd.Timedelta(hours=off_h)
    w = wx.copy()
    w.index = pd.to_datetime(w.index)
    w = w[~w.index.duplicated()].asfreq("h").ffill()

    def hours(t0, n, base):
        out = []
        for k in range(n):
            r = w.loc[t0 + pd.Timedelta(hours=k)]
            out.append(HourlyWeather(hours_from_start=float(k + base), temperature=float(r.temp),
                                     relative_humidity=float(r.rh), wind_speed=float(r.ws),
                                     wind_direction=float(r.wd) % 360.0, precipitation=float(r.rain)))
        return tuple(out)
    sp0 = s_utc - pd.Timedelta(hours=13)  # 17:00 local the day before
    case = H.FireDayCase(domain=dom, day=doy, start=start_local, hourly=hours(s_utc, 24, 0),
                         ffmc=float(w.loc[sp0].ffmc), dmc=float(w.loc[sp0].dmc), dc=float(w.loc[sp0].dc),
                         spinup=hours(sp0, 13, -13))
    opts = H.RunOptions(windows_h=(24.0,), ignition="active", active_days=2, ffmc_spinup=True,
                        burning_period=(10.0, 20.0), enable_spotting=False, grass_cure_green=cure_pct,
                        grass_cure_dormant=cure_pct, grass_cure_spring=cure_pct)
    r = H.simulate(case, opts)
    return r["arrival"], dom, r["run_s"]


def run_fire(fire_key: str, assignment: str, force: bool = False, days: list | None = None, mode: str = "sim") -> dict:
    """mode 'obs' (development check only): the same code path with the OBSERVED perimeter, active edge and
    spread rate; its files must equal the registered ``intervals/`` files (equivalence of the M8/B1 code path)."""
    from . import inputs as I
    from firesim.fbp.calculator import calculate_foliar_moisture
    from rasterio.transform import from_origin
    fd = C.fire_dir(fire_key)
    out_dir = fd / ("simedge" if mode == "sim" else "simedge_obscheck")
    done_p = out_dir / "_done.json"
    if done_p.exists() and not force:
        return json.loads(done_p.read_text())
    meta = json.loads((fd / "meta.json").read_text())
    if not meta.get("eligible_station"):
        return dict(fire_key=fire_key, status="ineligible")
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    rec = I.fire_record(fire_key)
    g = meta["grid"]; ny, nx, cs = g["ny"], g["nx"], g["cell"]
    z = np.load(fd / "inputs.npz")
    code, curing, zone = z["code"], z["curing"], z["zone"]
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"]).iloc[0]
    det = IV.detections(fire_key, rec["year"], assignment)
    d = IV.epochs(rec, det, g["crs"], per)
    if not len(d):
        return dict(fire_key=fire_key, status="no_detections")
    ci = np.clip(((d.x - g["x0"]) / cs).astype(int), 0, nx - 1).values
    cj = np.clip(((g["y1"] - d.y) / cs).astype(int), 0, ny - 1).values
    first_ep = np.full((ny, nx), 1 << 30, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    T = d.groupby("ep").t.min().values
    wx = {s: pd.read_csv(fd / f"wx_{s}.csv", index_col=0, parse_dates=True) for s in set(meta["zone_station"])}
    lat_c = float(rec["geometry"].centroid.y); lng_c = float(rec["geometry"].centroid.x)
    off = local_offset_h(lng_c)
    us_group = None
    if rec["region"] == "US":
        fb = z["fbfm40"]
        us_group = np.full(fb.shape, "N", object)
        for c_, gg in I.GROUP_OF_FBFM.items():
            us_group[fb == c_] = gg
    f = C.CAND_F
    bny, bnx = ny // f, nx // f
    burnable_cell = ~np.isin(code, [0, 100, 101, 102, 103, 104, 105, 106]) & (code > 0)
    burn_blk = burnable_cell.reshape(bny, f, bnx, f).mean(axis=(1, 3)) >= 0.5
    haz = z["hazard"].astype(np.float32)
    k500 = np.hypot(*np.mgrid[-4:5, -4:5]) * cs <= 500.0
    hazshare = fftconvolve(haz, k500.astype(np.float32) / k500.sum(), mode="same")
    cy, cx = np.arange(bny) * f + 1, np.arange(bnx) * f + 1
    season = {gr: IV.fbp_of_code(code, gr) for gr in (False, True)}
    season = {gr: (tid, pc, IV.waf_class(tid, us_group)) for gr, (tid, pc) in season.items()}
    grass = np.isin(code, [31, 32])
    cure_pct = float(np.clip(np.median(curing[grass]) * 100.0, 0, 100)) if grass.any() else 60.0
    elev = elevation(fire_key, meta, rec)
    frame = latlng_frame(g)
    fuel_ll = to_latlng(code.astype(np.int32), g, frame)
    elev_ll = to_latlng(elev.astype(np.float32), g, frame, nearest=False)
    # detection time per row, for the day starts
    dt_local = d.t + pd.Timedelta(hours=off)

    # valid intervals exactly as intervals.run_fire
    valid = []
    closed_by_i = {}
    for i in range(1, len(T)):
        L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
        closed = close_mask(first_ep < i, cs)
        if L > 12.0 or not closed.any():
            continue
        valid.append(i)
    day_of = {}
    for i in valid:
        t0l = pd.Timestamp(T[i - 1]) + pd.Timedelta(hours=off)
        dday = (t0l - pd.Timedelta(hours=6)).normalize()
        day_of.setdefault(dday, []).append(i)
    res_days, n_written, n_dropped = [], 0, 0
    for dday, ivs in sorted(day_of.items()):
        if days is not None and str(dday.date()) not in days:
            continue
        t06l = dday + pd.Timedelta(hours=6)
        t06 = t06l - pd.Timedelta(hours=off)  # UTC
        before = (d.t < t06).values
        info = dict(day=str(dday.date()), intervals=[int(i) for i in ivs])
        if not before.any():
            info["status"] = "no_start"; n_dropped += len(ivs); res_days.append(info); continue
        pts0 = np.zeros((ny, nx), bool); pts0[cj[before], ci[before]] = True
        P0 = close_mask(pts0, cs)
        rec48 = ((d.t >= t06 - pd.Timedelta(hours=48)) & (d.t < t06)).values
        recent = np.zeros((ny, nx), bool); recent[cj[rec48], ci[rec48]] = True
        recent &= P0
        doy = int(t06l.dayofyear)
        dob = np.where(P0, 1, 0).astype(np.int32)
        dob[recent] = doy - 1
        dob_ll = to_latlng(dob, g, frame)
        # station: the zone holding most active-edge cells of P0
        edge0 = P0 & ~ndimage.binary_erosion(P0)
        act0 = edge0 & (ndimage.distance_transform_edt(~recent) * cs <= C.CELL_M) if recent.any() else edge0
        zs = meta["zone_station"]
        zi_main = int(np.bincount(zone[act0].astype(int), minlength=len(zs)).argmax()) if len(zs) > 1 and act0.any() else 0
        t = time.time()
        signal.signal(signal.SIGALRM, _alarm); signal.alarm(DAY_CAP_S)
        try:
            arr_c, dom, run_s = simulate_day(fire_key, rec["year"], doy, t06l.to_pydatetime(), wx[zs[zi_main]], off,
                                             fuel_ll, elev_ll, dob_ll, frame, cure_pct)
            signal.alarm(0)
        except _Timeout:
            info["status"] = "timeout"; n_dropped += len(ivs); res_days.append(info); continue
        except Exception as e:
            signal.alarm(0)
            info["status"] = f"error {e!r}"[:200]; n_dropped += len(ivs); res_days.append(info); continue
        info.update(status="ok", run_s=round(time.time() - t, 1), station=zs[zi_main], cure_pct=cure_pct)
        tr = from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat)
        arr = to_utm(np.where(np.isfinite(arr_c), arr_c, np.inf), g, tr)  # minutes after 06:00 D
        arr[P0] = np.minimum(arr[P0], -1.0)

        def psim(tt):
            m = (tt - t06) / np.timedelta64(1, "m")
            return close_mask(P0 | (arr <= m), cs) if m > 0 else P0

        for i in ivs:
            t0, t1 = pd.Timestamp(T[i - 1]), pd.Timestamp(T[i])
            L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
            closed_obs = close_mask(first_ep < i, cs)
            Ps = psim(np.datetime64(T[i - 1])) if mode == "sim" else closed_obs
            edge = Ps & ~ndimage.binary_erosion(Ps)
            m0 = (np.datetime64(T[i - 1]) - t06) / np.timedelta64(1, "m")
            burning = (arr > m0 - 720.0) & (arr <= m0) & (arr >= 0)
            lo = pd.Timestamp(T[i - 1]) - pd.Timedelta(hours=12)
            if lo < t06:
                sel = ((d.t >= lo) & (d.t < min(t06, t0)) & d.nh).values
                burning[cj[sel], ci[sel]] = True
            act = edge & (ndimage.distance_transform_edt(~burning) * cs <= IV.ACTIVE_R) if burning.any() else np.zeros_like(edge)
            if mode == "obs":
                rd = ((d.t <= t0) & (d.t >= t0 - pd.Timedelta(hours=12)) & d.nh).values
                burning = np.zeros((ny, nx), bool); burning[cj[rd], ci[rd]] = True
                act = edge & (ndimage.distance_transform_edt(~burning) * cs <= IV.ACTIVE_R) if burning.any() else np.zeros_like(edge)
            # simulated spread rate over the previous interval
            if i >= 2 and pd.Timestamp(T[i - 2]) >= t06:
                tprev, Pprev = np.datetime64(T[i - 2]), psim(np.datetime64(T[i - 2]))
            else:
                tprev, Pprev = np.datetime64(t06), P0
            dtp = max((np.datetime64(T[i - 1]) - tprev) / np.timedelta64(1, "m"), 60.0)
            first_obs = False
            if mode == "obs":
                if i >= 2:
                    Pprev, dtp = close_mask(first_ep < i - 1, cs), max((T[i - 1] - T[i - 2]) / np.timedelta64(1, "m"), 60.0)
                else:
                    first_obs = True
            names, pcs, wcls = season[150 <= t0.dayofyear <= 258]
            dist_all, idx_all = ndimage.distance_transform_edt(~closed_obs, return_indices=True)
            dist_all *= cs
            A = np.zeros((2, 3, ny, nx), np.float32)
            zinfo = []
            aj, ai = np.nonzero(act)
            dprev_all = ndimage.distance_transform_edt(~Pprev) * cs
            fmc = calculate_foliar_moisture(lat_c, lng_c, None, t0.dayofyear)
            for zi, sid in enumerate(meta["zone_station"]):
                zw = IV.zone_weather(wx[sid], t0, t1)
                zinfo.append(zw)
                sel = zone[aj, ai] == zi if len(meta["zone_station"]) > 1 else np.ones(len(aj), bool)
                if not sel.any():
                    continue
                sj, si = aj[sel], ai[sel]
                ros = np.full(len(sj), IV.ROS_FLOOR) if first_obs else np.maximum(dprev_all[sj, si] / dtp, IV.ROS_FLOOR)
                nm = names[sj, si]; ok = nm != 0
                sj, si, ros, nm, pc = sj[ok], si[ok], ros[ok], nm[ok], pcs[sj[ok], si[ok]]
                if not len(sj):
                    continue
                tfc, inten = IV.tfc_intensity(nm, pc, ros, zw["ffmc"], zw["bui"], fmc)
                tfc, inten = np.nan_to_num(tfc), np.nan_to_num(inten)
                n_emb = IV.N_PER_KG * tfc * (ros / 60.0) * (L * 3600.0) * cs
                ur = max(zw["u10"] - 0.0, 0.5)
                nc = 2 * 9.81 * inten * 1000.0 / (1.2 * 1005.0 * 300.0 * np.maximum(ur - ros / 60.0, 0.5) ** 3)
                phi = 1.0 + np.clip((np.log10(np.maximum(nc, 1e-9)) - math.log10(2)) / (math.log10(10) - math.log10(2)), 0, 1)
                fl = np.minimum(IV.LONG_SHARE * phi, 0.95)
                src_long = np.zeros((ny, nx), np.float32)
                np.add.at(src_long, (sj, si), (n_emb * fl).astype(np.float32))
                land_long = fftconvolve(src_long, IV.long_kernel(cs, zw["to_rad"]), mode="same")
                land_short = np.zeros((ny, nx), np.float32)
                cls = np.digitize(inten, IV.I_BINS) - 1
                for k in np.unique(cls):
                    m = cls == k
                    src = np.zeros((ny, nx), np.float32)
                    np.add.at(src, (sj[m], si[m]), (n_emb[m] * (1 - fl[m])).astype(np.float32))
                    land_short += fftconvolve(src, IV.short_kernel(cs, zw["to_rad"], float(np.median(inten[m])), zw["u10"]), mode="same")
                land_long = np.maximum(land_long, 0); land_short = np.maximum(land_short, 0)
                gcur = np.where(wcls == 0, curing, 1.0) if us_group is None else np.where(np.isin(us_group, ["G", "U"]), curing, 1.0)
                for w in range(3):
                    mw = (wcls == w)
                    A[0, w] += np.where(mw, land_short * gcur, 0)
                    A[1, w] += np.where(mw, land_long * gcur, 0)
            dcen = dist_all[np.ix_(cy, cx)]
            incl = closed_obs.reshape(bny, f, bnx, f).any(axis=(1, 3))
            cand = (dcen >= IV.BAND[0]) & (dcen <= IV.BAND[1]) & ~incl & burn_blk
            bj, bi = np.nonzero(cand)
            Ab = A.reshape(2, 3, bny, f, bnx, f).sum(axis=(3, 5))[:, :, bj, bi]
            jj, ii_ = cy[bj], cx[bi]
            dps, idx_s = ndimage.distance_transform_edt(~Ps, return_indices=True)
            if act.any():
                dact, (aj2, ai2) = ndimage.distance_transform_edt(~act, return_indices=True)
                d_act = dact[jj, ii_] * cs
                vy, vx = -(jj - aj2[jj, ii_]) * cs, (ii_ - ai2[jj, ii_]) * cs
            else:
                d_act = dps[jj, ii_] * cs
                vy, vx = -(jj - idx_s[0][jj, ii_]) * cs, (ii_ - idx_s[1][jj, ii_]) * cs
            pvy, pvx = -(jj - idx_all[0][jj, ii_]) * cs, (ii_ - idx_all[1][jj, ii_]) * cs
            zb = zone[jj, ii_] if len(meta["zone_station"]) > 1 else np.zeros(len(jj), np.int16)
            to = np.array([zz["to_rad"] for zz in zinfo])[zb]
            sec_act = np.abs(((np.arctan2(vx, vy) - to + np.pi) % (2 * np.pi)) - np.pi) <= np.radians(45)
            sec_all = np.abs(((np.arctan2(pvx, pvy) - to + np.pi) % (2 * np.pi)) - np.pi) <= np.radians(45)
            np.savez_compressed(out_dir / f"{i:05d}.npz", bj=bj.astype(np.int32), bi=bi.astype(np.int32),
                                A=Ab.astype(np.float32), zone=zb.astype(np.int16),
                                u10=np.array([zz["u10"] for zz in zinfo], np.float32),
                                m_ff=np.array([zz["m_ff"] for zz in zinfo], np.float32),
                                hazshare=hazshare[jj, ii_].astype(np.float32), d_act=d_act.astype(np.float32),
                                sec_act=sec_act, d_all=dcen[bj, bi].astype(np.float32), sec_all=sec_all,
                                in_psim=Ps[jj, ii_], n_active=np.int32(act.sum()))
            n_written += 1
        res_days.append(info)
    res = dict(fire_key=fire_key, status="ok", n_written=n_written, n_dropped=n_dropped, days=res_days,
               seconds=time.time() - t_start, local_offset_h=off)
    if days is None:
        done_p.write_text(json.dumps(res, indent=1, default=str))
    C.log_run("simedge", fire_key, "ok", res["seconds"], f"{n_written} written, {n_dropped} dropped")
    return res


if __name__ == "__main__":
    C.guard()
    split = C.split_table().set_index("fire_key").assignment
    days = None
    args = sys.argv[1:]
    if "--days" in args:
        days = args[args.index("--days") + 1].split(","); args = args[:args.index("--days")]
    args = [a for a in args if a != "--obs"]
    for k in args:
        mode = "obs" if "--obs" in sys.argv else "sim"
        r = run_fire(k, split.get(k, "heldout"), force=True, days=days, mode=mode)
        print(k, r["status"], r.get("n_written"), r.get("n_dropped"), f"{r.get('seconds', 0):.0f}s", flush=True)
