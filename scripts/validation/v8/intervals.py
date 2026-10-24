"""v8 interval engine: start perimeters, active segments, M8 landing fields and B1 per candidate cell.

PREREG_LR8 sections 4-6 and addendum A2. For every valid interval (<= 12 h) of a fire this writes one
``intervals/<epoch>.npz`` with, per 375 m candidate cell (1-15 km from the start perimeter, unburned,
burnable): the M8 landing sufficient statistics ``A[mode, waf_class]`` (embers landed x grass curing, by
short/long mode and bed-wind class), the zone index, and the B1 terms (hazard share within 500 m, distance
to the nearest active segment and to the perimeter, downwind-sector flags). The M8 score for a given
establishment fit beta is (``m8_score``)

    lambda = exp(b0 + b_m (m_ff - 21)) * sum_w exp(b_w ln(1 + U10 * WAF_w)) * (A_short,w + exp(b_L) A_long,w)

with m_ff (fine-fuel moisture %, FF scale from the zone's hourly FFMC averaged over the interval) and U10
(m/s, the zone's interval-mean 10 m wind). kappa_g = 0 for every burnable group and grass receptivity is
multiplied by curing (addendum A2, A2.3). This is the log-linear (rare-ignition) form of the pre-registered
logistic, exact as P -> 0; beta_0 absorbs ember yield and VIIRS detection and is constant within a fire,
so it cancels in the ranks.

Physics (design section 4, PREREG section 5; [H] = FireSim hypothesis fixed here):
- Sources: start-perimeter edge cells with a nominal/high detection within 1 km in the 12 h before t_start.
- Spread rate: the cell's distance to the previous start perimeter / the previous interval (m/min);
  floor 0.5 m/min for an active cell [H]; first interval of a fire: the floor.
- FBP (ST-X-3; firesim.fbp): SFC at the zone's FFMC and BUI; CSI from the type's default CBH and the
  date's foliar moisture; CFB from the observed ROS vs RSO; TFC = SFC + CFL x CFB; I = 300 TFC ROS.
- Emission: n = 540 embers/kg x TFC x ROS x interval x cell edge (mass yield 27 g/kg / 0.05 g).
- Long-mode share 0.2762 (Sardoy p.486) x plume factor 1-2 rising with log10 Byram N_c from 2 to 10 [H].
- Short mode: Sardoy correlation at I_eff = min(I, 40 MW/m), U_eff = clip(U6.1, 6.70, 17.88) (v8_gate),
  crosswind sigma 0.92 x cell; long mode: lognormal median 733 m, sigma_ln 0.99, cut 15.6 km, crosswind
  sigma tan 5 deg x distance; both by FFT convolution per zone and intensity class.
- Bed-wind classes (Andrews 2012 WAF) [H]: unsheltered grass / US shrub 0.4; D, M 0.2; C, US timber 0.15.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy import ndimage
from scipy.signal import fftconvolve
from scipy.special import ndtri

from . import common as C

WAF = np.array([0.4, 0.2, 0.15], np.float32)
N_PER_KG = 27.0 / 0.05          # 540 embers per kg consumed
LONG_SHARE = 0.2762
LONG_MED, LONG_SIG, LONG_CUT = 733.0, 0.99, 15600.0
ROS_FLOOR = 0.5                 # m/min
ACTIVE_R, DIL, CLOSE = 1000.0, 375.0, 1000.0
BAND = (1000.0, 15000.0)
I_BINS = np.array([0, 500, 2000, 5000, 10000, 20000, 40000, np.inf])  # kW/m classes for the short kernel


# ── fuels ─────────────────────────────────────────────────────────────────────────────────────

TYPES = ["", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "D1", "D2", "M1", "M2", "M3", "M4", "O1a", "O1b",
         "S1", "S2", "S3"]
TI = {t: k for k, t in enumerate(TYPES)}


def fbp_of_code(code: np.ndarray, green: bool):
    """(FBP type index into TYPES, 0 = non-fuel; percent conifer / dead fir) from CIFFC national codes;
    season by FireSim's leaf rule (green D-2 / M-2 / M-4 from day of year 150 to 258)."""
    tid = np.zeros(code.shape, np.int8)
    pc = np.zeros(code.shape, np.float32)
    for v, t in ((1, "C1"), (2, "C2"), (3, "C3"), (4, "C4"), (5, "C5"), (6, "C6"), (7, "C7"),
                 (11, "D1"), (12, "D2"), (21, "S1"), (22, "S2"), (23, "S3"), (31, "O1a"), (32, "O1b")):
        tid[code == v] = TI[t]
    tid[code == 13] = TI["D2" if green else "D1"]
    for base, a, b in ((400, "M1", "M1"), (500, "M2", "M2"), (600, "M1", "M2"), (700, "M3", "M3"),
                       (800, "M4", "M4"), (900, "M3", "M4")):
        m = (code > base) & (code < base + 100)
        tid[m] = TI[b if green else a]
        pc[m] = code[m] - base
    for v, a, b in ((40, "M1", "M1"), (50, "M2", "M2"), (60, "M1", "M2"), (70, "M3", "M3"), (80, "M4", "M4"),
                    (90, "M3", "M4")):
        tid[code == v] = TI[b if green else a]
        pc[code == v] = 50.0
    return tid, pc


def waf_class(tid: np.ndarray, us_group: np.ndarray | None) -> np.ndarray:
    """0 = unsheltered (O, US grass/shrub/urban), 1 = D/M, 2 = C, S, US timber; -1 = non-fuel."""
    cls = np.full(tid.shape, -1, np.int8)
    cls[(tid >= TI["O1a"]) & (tid <= TI["O1b"])] = 0
    cls[(tid >= TI["D1"]) & (tid <= TI["M4"])] = 1
    cls[((tid >= TI["C1"]) & (tid <= TI["C7"])) | (tid >= TI["S1"])] = 2
    if us_group is not None:
        cls[np.isin(us_group, ["G", "S", "U"])] = 0
        cls[us_group == "T"] = 2
    return cls


def tfc_intensity(names, pcs, ros, ffmc, bui, fmc):
    """Vector FBP: TFC (kg/m2) and Byram intensity (kW/m) at observed ROS (m/min), per source cell."""
    from firesim.fbp.calculator import calculate_sfc
    from firesim.fbp.constants import FUEL_TYPES, FuelType
    tfc = np.zeros(len(names)); inten = np.zeros(len(names))
    for k in np.unique(names):
        if k == 0:
            continue
        t = TYPES[int(k)]
        m = names == k
        spec = FUEL_TYPES[FuelType(t)]
        for pc in np.unique(pcs[m]):
            mm = m & (pcs == pc)
            sfc = calculate_sfc(spec, ffmc, bui, pc=float(pc) if pc > 0 else 50.0)
            cfl, cbh = float(spec.cfl), float(spec.cbh)
            r = ros[mm]
            cfb = np.zeros_like(r)
            if cfl > 0 and cbh > 0 and sfc > 0:
                csi = 0.001 * cbh ** 1.5 * (460.0 + 25.9 * fmc) ** 1.5
                rso = csi / (300.0 * sfc)
                cfb = np.where(r > rso, 1.0 - np.exp(-0.23 * (r - rso)), 0.0)
                if t in ("M1", "M2"):
                    cfb = cfb * pc / 100.0
            tfc[mm] = sfc + cfl * cfb
            inten[mm] = 300.0 * tfc[mm] * r
    return tfc, inten


# ── kernels ───────────────────────────────────────────────────────────────────────────────────

def _rot(n, cell, to_rad):
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1].astype(np.float64) * cell
    yy = -yy  # row index grows southward
    along = xx * math.sin(to_rad) + yy * math.cos(to_rad)
    cross = xx * math.cos(to_rad) - yy * math.sin(to_rad)
    return along, cross


def long_kernel(cell, to_rad):
    n = int(LONG_CUT // cell) + 1
    a, c = _rot(n, cell, to_rad)
    ap = np.maximum(a, 1e-6)
    f = np.exp(-(np.log(ap / LONG_MED) ** 2) / (2 * LONG_SIG ** 2)) / (ap * LONG_SIG * math.sqrt(2 * math.pi))
    sy = np.maximum(math.tan(math.radians(5.0)) * ap, cell / 2)
    f = f * np.exp(-c ** 2 / (2 * sy ** 2)) / (math.sqrt(2 * math.pi) * sy) * cell * cell
    f[(a <= 0) | (np.hypot(a, c) > LONG_CUT)] = 0.0
    return (f / f.sum() * 0.999).astype(np.float32)  # renormalised to the in-cut share of the lognormal


def short_kernel(cell, to_rad, i_kw, u10):
    import sys
    from pathlib import Path as P
    sys.path.insert(0, str(P(__file__).resolve().parents[1]))
    from v8_gate import sardoy_clamped
    from firesim.structures.embers import u10_to_u6
    i_kw = float(i_kw) if np.isfinite(i_kw) else 1.0
    u10 = float(u10) if np.isfinite(u10) else 0.0
    mu, sg = sardoy_clamped(max(i_kw, 1.0) / 1000.0, float(u10_to_u6(u10)))
    mu, sg = float(mu), float(sg)
    reach = math.exp(mu + sg * float(ndtri(0.999)))
    n = max(2, int(reach // cell) + 2)
    a, c = _rot(n, cell, to_rad)
    ap = np.maximum(a, 1e-6)
    f = np.exp(-(np.log(ap) - mu) ** 2 / (2 * sg ** 2)) / (ap * sg * math.sqrt(2 * math.pi))
    sy = 0.92 * cell
    f = f * np.exp(-c ** 2 / (2 * sy ** 2)) / (math.sqrt(2 * math.pi) * sy) * cell * cell
    f[(a <= 0) | (a > reach)] = 0.0
    s = f.sum()
    return (f / s * 0.999).astype(np.float32) if s > 0 else f.astype(np.float32)


# ── weather ───────────────────────────────────────────────────────────────────────────────────

def zone_weather(wx: pd.DataFrame, t0, t1):
    w = wx.loc[t0:t1]
    w = w[w.obs.astype(bool)] if len(w) else w
    if not len(w):  # no observation in the interval: the last 6 observed hours before its end
        w = wx.loc[:t1][wx.loc[:t1].obs.astype(bool)].tail(6)
    r = np.radians(w.wd.fillna(0).values)
    u = (w.ws.values / 3.6)
    ue, un = np.nanmean(u * np.sin(r)), np.nanmean(u * np.cos(r))
    ue, un = float(np.nan_to_num(ue)), float(np.nan_to_num(un))
    frm = math.degrees(math.atan2(ue, un)) % 360
    spd = float(np.nan_to_num(np.nanmean(u))) if len(u) else 0.0
    ffmc = float(np.nanmean(w.ffmc.values)) if len(w) else float(wx.loc[:t1].ffmc.iloc[-1])
    bui = float(wx.loc[:t0].bui.dropna().iloc[-1]) if wx.loc[:t0].bui.notna().any() else 60.0
    return dict(u10=spd, wd_from=frm, to_rad=math.radians((frm + 180) % 360), ffmc=ffmc, bui=bui,
                m_ff=147.2 * (101 - ffmc) / (59.5 + ffmc))


# ── main loop ─────────────────────────────────────────────────────────────────────────────────

def detections(fire_key: str, year: int, assignment: str) -> pd.DataFrame:
    if assignment.startswith("training"):
        d = pd.read_pickle(C.DATA / "viirs_training" / f"viirs_training_{year}.pkl.gz")
        return d[d.fire_key == fire_key]
    return pd.read_pickle(C.DATA / "viirs_decision" / f"{fire_key.replace(':', '_')}.pkl.gz")


def epochs(fr, det: pd.DataFrame, utm: str, per) -> pd.DataFrame:
    """Detections and epochs exactly as the registered jump rule (v8_rule/v8_viirs_jumps.catalogue_fire)."""
    import shapely
    V = C.frozen_jumps()
    d = det.copy()
    xy = gpd.GeoSeries(gpd.points_from_xy(d.longitude, d.latitude), crs=4326).to_crs(utm)
    d["x"], d["y"] = xy.x.values, xy.y.values
    d = d[d.type == 0]
    t0 = (fr["t0"] if pd.notna(fr["t0"]) else d.t.min()) - pd.Timedelta(days=2)
    d = d[d.t >= t0]
    # same test as the frozen rule's contains(perimeter buffered 375 m), without building the buffer
    inper = shapely.dwithin(per, shapely.points(d.x.values, d.y.values), V.FINAL_TOL)
    ti = np.sort(d.t[inper].values)
    if len(ti) == 0:
        return d.iloc[:0]
    gaps = np.where(np.diff(ti) > np.timedelta64(int(V.GAP_END_D * 24), "h"))[0]
    t_end = pd.Timestamp(ti[gaps[0]] if len(gaps) else ti[-1]) + pd.Timedelta(hours=1)
    if pd.notna(fr["t1"]):
        t_end = min(t_end, fr["t1"] + pd.Timedelta(days=2))
    d = d[d.t <= t_end].sort_values("t").reset_index(drop=True)
    new = V.new_epoch(d.t.values)  # addendum A1: acquisitions merge only when < 60 min apart
    d["ep"] = np.cumsum(new) - 1
    d["nh"] = d.confidence.isin(["n", "h"])
    return d


def run_fire(fire_key: str, assignment: str, force: bool = False) -> dict:
    from . import inputs as I
    from firesim.fbp.calculator import calculate_foliar_moisture
    fd = C.fire_dir(fire_key)
    meta = json.loads((fd / "meta.json").read_text())
    if not meta.get("eligible_station"):
        return dict(fire_key=fire_key, status="ineligible")
    done = fd / "intervals" / "_done.json"
    if done.exists() and not force:
        return json.loads(done.read_text())
    t_start = time.time()
    (fd / "intervals").mkdir(exist_ok=True)
    rec = I.fire_record(fire_key)
    g = meta["grid"]
    ny, nx, cs = g["ny"], g["nx"], g["cell"]
    z = np.load(fd / "inputs.npz")
    code, curing, zone = z["code"], z["curing"], z["zone"]
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"]).iloc[0]
    det = detections(fire_key, rec["year"], assignment)
    d = epochs(rec, det, g["crs"], per)
    if not len(d):
        return dict(fire_key=fire_key, status="no_detections")
    ci = np.clip(((d.x - g["x0"]) / cs).astype(int), 0, nx - 1).values
    cj = np.clip(((g["y1"] - d.y) / cs).astype(int), 0, ny - 1).values
    first_ep = np.full((ny, nx), 1 << 30, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    T = d.groupby("ep").t.min().values
    wx = {s: pd.read_csv(fd / f"wx_{s}.csv", index_col=0, parse_dates=True) for s in set(meta["zone_station"])}
    lat_c = float(rec["geometry"].centroid.y); lng_c = float(rec["geometry"].centroid.x)
    us_group = None
    if rec["region"] == "US":
        fb = z["fbfm40"]
        us_group = np.full(fb.shape, "N", object)
        for c_, gg in I.GROUP_OF_FBFM.items():
            us_group[fb == c_] = gg
    # static per-fire layers
    f = C.CAND_F
    bny, bnx = ny // f, nx // f
    burnable_cell = ~np.isin(code, [0, 100, 101, 102, 103, 104, 105, 106]) & (code > 0)
    burn_blk = burnable_cell.reshape(bny, f, bnx, f).mean(axis=(1, 3)) >= 0.5
    haz = z["hazard"].astype(np.float32)
    k500 = np.hypot(*np.mgrid[-4:5, -4:5]) * cs <= 500.0
    hazshare = fftconvolve(haz, k500.astype(np.float32) / k500.sum(), mode="same")
    cy, cx = np.arange(bny) * f + 1, np.arange(bnx) * f + 1
    season = {gr: fbp_of_code(code, gr) for gr in (False, True)}
    season = {gr: (tid, pc, waf_class(tid, us_group)) for gr, (tid, pc) in season.items()}
    out_meta = []
    prev_closed, prev_T = None, None
    for i in range(1, len(T)):
        L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
        pts = first_ep < i
        dil = ndimage.distance_transform_edt(~pts) * cs <= DIL
        E = ndimage.distance_transform_edt(~dil) * cs <= CLOSE
        closed = (ndimage.distance_transform_edt(E) * cs > CLOSE - cs) | dil
        if L > 12.0 or not closed.any():
            prev_closed, prev_T = closed, T[i - 1]
            continue
        t0, t1 = pd.Timestamp(T[i - 1]), pd.Timestamp(T[i])
        doy = t0.dayofyear
        names, pcs, wcls = season[150 <= doy <= 258]
        # active segments
        edge = closed & ~ndimage.binary_erosion(closed)
        rec_det = (d.t <= t0) & (d.t >= t0 - pd.Timedelta(hours=12)) & d.nh
        recent = np.zeros((ny, nx), bool)
        recent[cj[rec_det.values], ci[rec_det.values]] = True
        act = edge & (ndimage.distance_transform_edt(~recent) * cs <= ACTIVE_R) if recent.any() else np.zeros_like(edge)
        # active-segment rule test (addendum A2, A2.5): did the fire advance from this edge cell within 24 h?
        fut = (d.t > t0) & (d.t <= t0 + pd.Timedelta(hours=24))
        fmask = np.zeros((ny, nx), bool)
        fmask[cj[fut.values], ci[fut.values]] = True
        fmask &= ~closed
        adv = edge & (ndimage.distance_transform_edt(~fmask) * cs <= ACTIVE_R) if fmask.any() else np.zeros_like(edge)
        # distance fields
        dist_all, idx_all = ndimage.distance_transform_edt(~closed, return_indices=True)
        dist_all *= cs
        # M8 landing, per zone
        A = np.zeros((2, 3, ny, nx), np.float32)
        zinfo = []
        aj, ai = np.nonzero(act)
        n_src = len(aj)
        emis_tot = 0.0
        for zi, sid in enumerate(meta["zone_station"]):
            zw = zone_weather(wx[sid], t0, t1)
            zinfo.append(zw)
            sel = zone[aj, ai] == zi if len(meta["zone_station"]) > 1 else np.ones(len(aj), bool)
            if not sel.any():
                continue
            sj, si = aj[sel], ai[sel]
            if prev_closed is not None and prev_T is not None:
                dprev = ndimage.distance_transform_edt(~prev_closed)[sj, si] * cs
                dtp = max((T[i - 1] - prev_T) / np.timedelta64(1, "m"), 60.0)
                ros = np.maximum(dprev / dtp, ROS_FLOOR)
            else:
                ros = np.full(len(sj), ROS_FLOOR)
            fmc = calculate_foliar_moisture(lat_c, lng_c, None, doy)
            nm = names[sj, si]; ok = nm != 0
            sj, si, ros, nm, pc = sj[ok], si[ok], ros[ok], nm[ok], pcs[sj[ok], si[ok]]
            if not len(sj):
                continue
            tfc, inten = tfc_intensity(nm, pc, ros, zw["ffmc"], zw["bui"], fmc)
            tfc, inten = np.nan_to_num(tfc), np.nan_to_num(inten)
            n_emb = N_PER_KG * tfc * (ros / 60.0) * (L * 3600.0) * cs
            emis_tot += float(n_emb.sum())
            # Byram convection number -> long-mode share
            ur = max(zw["u10"] - 0.0, 0.5)
            nc = 2 * 9.81 * inten * 1000.0 / (1.2 * 1005.0 * 300.0 * np.maximum(ur - ros / 60.0, 0.5) ** 3)
            phi = 1.0 + np.clip((np.log10(np.maximum(nc, 1e-9)) - math.log10(2)) / (math.log10(10) - math.log10(2)), 0, 1)
            fl = np.minimum(LONG_SHARE * phi, 0.95)
            src_long = np.zeros((ny, nx), np.float32)
            np.add.at(src_long, (sj, si), (n_emb * fl).astype(np.float32))
            land_long = fftconvolve(src_long, long_kernel(cs, zw["to_rad"]), mode="same")
            land_short = np.zeros((ny, nx), np.float32)
            cls = np.digitize(inten, I_BINS) - 1
            for k in np.unique(cls):
                m = cls == k
                src = np.zeros((ny, nx), np.float32)
                np.add.at(src, (sj[m], si[m]), (n_emb[m] * (1 - fl[m])).astype(np.float32))
                i_rep = float(np.median(inten[m]))
                land_short += fftconvolve(src, short_kernel(cs, zw["to_rad"], i_rep, zw["u10"]), mode="same")
            land_long = np.maximum(land_long, 0); land_short = np.maximum(land_short, 0)
            gcur = np.where(wcls == 0, curing, 1.0) if us_group is None else np.where(np.isin(us_group, ["G", "U"]), curing, 1.0)
            for w in range(3):
                mw = (wcls == w)
                A[0, w] += np.where(mw, land_short * gcur, 0)
                A[1, w] += np.where(mw, land_long * gcur, 0)
        # candidate blocks
        dcen = dist_all[np.ix_(cy, cx)]
        incl = closed.reshape(bny, f, bnx, f).any(axis=(1, 3))
        cand = (dcen >= BAND[0]) & (dcen <= BAND[1]) & ~incl & burn_blk
        bj, bi = np.nonzero(cand)
        Ab = A.reshape(2, 3, bny, f, bnx, f).sum(axis=(3, 5))[:, :, bj, bi]
        # B1 terms at block centres
        jj, ii_ = cy[bj], cx[bi]
        if act.any():
            dact, (aj2, ai2) = ndimage.distance_transform_edt(~act, return_indices=True)
            d_act = dact[jj, ii_] * cs
            vy, vx = -(jj - aj2[jj, ii_]) * cs, (ii_ - ai2[jj, ii_]) * cs
        else:
            d_act = dcen[bj, bi]
            vy, vx = -(jj - idx_all[0][jj, ii_]) * cs, (ii_ - idx_all[1][jj, ii_]) * cs
        pvy, pvx = -(jj - idx_all[0][jj, ii_]) * cs, (ii_ - idx_all[1][jj, ii_]) * cs
        zb = zone[jj, ii_] if len(meta["zone_station"]) > 1 else np.zeros(len(jj), np.int16)
        to = np.array([zi["to_rad"] for zi in zinfo])[zb]
        sec_act = np.abs(((np.arctan2(vx, vy) - to + np.pi) % (2 * np.pi)) - np.pi) <= np.radians(45)
        sec_all = np.abs(((np.arctan2(pvx, pvy) - to + np.pi) % (2 * np.pi)) - np.pi) <= np.radians(45)
        np.savez_compressed(fd / "intervals" / f"{i:05d}.npz", bj=bj.astype(np.int32), bi=bi.astype(np.int32),
                            A=Ab.astype(np.float32), zone=zb.astype(np.int16),
                            u10=np.array([zz["u10"] for zz in zinfo], np.float32),
                            m_ff=np.array([zz["m_ff"] for zz in zinfo], np.float32),
                            hazshare=hazshare[jj, ii_].astype(np.float32), d_act=d_act.astype(np.float32),
                            sec_act=sec_act, d_all=dcen[bj, bi].astype(np.float32), sec_all=sec_all)
        out_meta.append(dict(epoch=i, t_start=str(t0), t_end=str(t1), hours=float(L), n_cand=int(len(bj)),
                             n_active=int(n_src), edge_cells=int(edge.sum()), emission=emis_tot,
                             edge_adv24=int(adv.sum()), edge_active_and_adv=int((adv & act).sum()),
                             zones=[{k: v for k, v in zz.items() if k != "to_rad"} for zz in zinfo]))
        prev_closed, prev_T = closed, T[i - 1]
    res = dict(fire_key=fire_key, status="ok", n_intervals=len(out_meta), seconds=time.time() - t_start,
               block_shape=[bny, bnx], intervals=out_meta)
    done.write_text(json.dumps(res, indent=1, default=float))
    C.log_run("intervals", fire_key, "ok", res["seconds"], f"{len(out_meta)} intervals")
    return res


def block_of(meta: dict, x: float, y: float) -> tuple[int, int]:
    g = meta["grid"]
    return int((g["y1"] - y) // (g["cell"] * C.CAND_F)), int((x - g["x0"]) // (g["cell"] * C.CAND_F))


def m8_score(npz, beta: dict) -> np.ndarray:
    A = npz["A"].astype(np.float64)
    u = npz["u10"][npz["zone"]].astype(np.float64)
    m = npz["m_ff"][npz["zone"]].astype(np.float64)
    lam = np.zeros(A.shape[-1])
    for w in range(3):
        lam += np.exp(beta["b_w"] * np.log1p(u * WAF[w])) * (A[0, w] + math.exp(beta["b_L"]) * A[1, w])
    return lam * np.exp(beta["b0"] + beta["b_m"] * (m - 21.0))


def b1_score(npz, active: bool = True) -> np.ndarray:
    d = npz["d_act"] if active else npz["d_all"]
    sec = npz["sec_act"] if active else npz["sec_all"]
    return npz["hazshare"] * np.exp(-(d - 1000.0) / 1240.0) * sec


if __name__ == "__main__":
    import sys
    C.guard()
    split = C.split_table().set_index("fire_key").assignment
    for k in sys.argv[1:]:
        try:
            r = run_fire(k, split[k])
            print(k, r["status"], r.get("n_intervals"), f"{r.get('seconds', 0):.0f}s", flush=True)
        except Exception as e:
            import traceback; traceback.print_exc()
            C.log_run("intervals", k, f"error {e!r}"[:200], 0)
            ed = C.fire_dir(k) / "intervals"; ed.mkdir(exist_ok=True)
            (ed / "_error.txt").write_text(repr(e))
            print(k, "ERROR", repr(e), flush=True)
