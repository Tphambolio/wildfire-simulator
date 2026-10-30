"""Deterministic feature builder for the ML ember-cast training tables (PREREG_ML_EMBER_DEV section 4).

Purpose
    For one training fire, rebuild every valid interval's candidate set with the frozen v8 primitives
    (``scripts/validation/v8/intervals.py``: ``detections``, ``epochs``, ``zone_weather``, ``tfc_intensity``,
    ``fbp_of_code``, ``waf_class``, ``block_of``, ``m8_score``, ``b1_score``; ``inputs.fire_record``) and
    compute EXACTLY the section-4 feature columns at every candidate cell. Fields the v8 run already wrote to
    ``intervals/<epoch>.npz`` (``A``, ``zone``, ``u10``, ``m_ff``, ``hazshare``, ``d_act``, ``sec_act``,
    ``d_all``, ``sec_all``) are taken from the npz; the same quantities are recomputed here and compared
    (``IntervalProducts.verify``) so that the sibling path is proven identical to ``run_fire``.
Inputs (through the guarded loader only)
    ``v8-pool/fires/<key>/{meta.json, inputs.npz, wx_<station>.csv, intervals/*.npz}``,
    ``v8-pool/viirs_training/viirs_training_<year>.pkl.gz``, the raw final-perimeter records the v8 code reads
    (NBAC zip / MTBS shapefile / cached WFIGS GeoJSON, via ``inputs.fire_record``), ``v8-pool/fit/fit_beta.json``.
Outputs
    ``IntervalProducts`` per valid interval: candidate-level feature columns (section 4 names and units),
    interval-level scalars, control-only columns (box coordinates ``bi``/``bj``; ``in_final``; the deliberately
    forbidden fuel-texture statistics for the section-8 fuel-seam control) kept apart from the features, and the
    npz verification diffs. No file is written here.
Information rule (section 4)
    Everything is computable at t_start from epochs <= i-1 plus the interval's observed station weather
    (disclosed). Nothing uses epoch i detections, the end-of-interval or final perimeter (the final perimeter
    enters only the control column ``in_final`` and the v8 detection window, exactly as ``run_fire``).
Deviations recorded for the professor (conservative readings)
    - ``elev_m``, ``slope_pct``, ``aspect_deg``, ``tpi_1km`` are NOT produced: ``inputs.npz`` ``elev`` is all
      zero in every v8 fire (``inputs.build`` writes ``np.zeros``); a constant column would be noise and a new
      DEM source is not in the contract.
    - ``d_act_x_fuel_group`` is derived by the ladder from its two factors; not stored.
    - ``perim_agree_flag`` was struck by PREREG_LR8 A1.4 and does not exist; ``gap_class`` is a jump attribute
      (forbidden as a feature) and stays in the jump catalogue.
    - ``m8_log`` (beta per outer fold) is computed by the ladder from ``A_*``, ``u10_ms``, ``m_ff_pct``, ``zone``;
      ``m8_log_frozen`` (fit_beta.json) is stored for reference only.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.signal import fftconvolve

from . import paths as P
from .loader import Guard
from scripts.validation.v8 import common as C
from scripts.validation.v8 import intervals as Iv

CELL_FEATURES = ("d_act_km", "d_all_km", "sec_act", "sec_all", "bear_off_deg", "act_len_km", "act_share",
                 "fire_area_km2", "fire_age_h", "gap_h", "hour_local", "u10_ms", "wd_deg", "m_ff_pct", "ffmc",
                 "temp_c", "rh_pct", "stn_dist_km", "I_head_kwm", "ros_mmin", "cfb", "spread_prev_km", "frp_prev_mw",
                 "fuel_code", "fuel_group", "hazard", "hazshare", "curing_pct", "d_nonfuel_km", "d_water_km",
                 "A_s0", "A_s1", "A_s2", "A_l0", "A_l1", "A_l2", "zone", "m8_log_frozen", "b1",
                 "region", "year", "sat_era", "fuel_restored_share")
INT_COLUMNS = {"sec_act": np.int8, "sec_all": np.int8, "fuel_code": np.int16, "fuel_group": np.int8, "hazard": np.int8,
               "zone": np.int16, "region": np.int8, "year": np.int16, "sat_era": np.int8}
REGION_CODE = {"CAN": 0, "US": 1}
NPZ_FIELDS = ("bj", "bi", "d_act", "d_all", "sec_act", "sec_all", "hazshare", "u10", "m_ff", "zone")
TEXTURE_R = 8  # 1 km window = 8 cells of 125 m on each side (17 x 17), control only


# ── sibling primitives (same equations as intervals.py; verified against it) ─────────────────

def fbp_at_sources(names, pcs, ros, ffmc, bui, fmc):
    """``intervals.tfc_intensity`` with the crown fraction returned as well: TFC (kg/m2), I (kW/m), CFB."""
    from firesim.fbp.calculator import calculate_sfc
    from firesim.fbp.constants import FUEL_TYPES, FuelType
    tfc = np.zeros(len(names)); inten = np.zeros(len(names)); cfb_out = np.zeros(len(names))
    for k in np.unique(names):
        if k == 0:
            continue
        t = Iv.TYPES[int(k)]
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
            cfb_out[mm] = cfb
    return tfc, inten, cfb_out


def interval_weather(wx: pd.DataFrame, t0, t1) -> dict:
    """``intervals.zone_weather`` (frozen) plus the interval-mean temperature and RH over the same rows."""
    zw = Iv.zone_weather(wx, t0, t1)
    w = wx.loc[t0:t1]
    w = w[w.obs.astype(bool)] if len(w) else w
    if not len(w):
        w = wx.loc[:t1][wx.loc[:t1].obs.astype(bool)].tail(6)
    zw["temp"] = float(np.nanmean(w.temp.values)) if len(w) and np.isfinite(w.temp.values).any() else float("nan")
    zw["rh"] = float(np.nanmean(w.rh.values)) if len(w) and np.isfinite(w.rh.values).any() else float("nan")
    return zw


def start_perimeter(first_ep: np.ndarray, i: int, cs: float):
    """Start perimeter at epoch i exactly as ``run_fire``: detections of epochs < i dilated 375 m, closed 1 km."""
    pts = first_ep < i
    dil = ndimage.distance_transform_edt(~pts) * cs <= Iv.DIL
    E = ndimage.distance_transform_edt(~dil) * cs <= Iv.CLOSE
    closed = (ndimage.distance_transform_edt(E) * cs > Iv.CLOSE - cs) | dil
    return closed


def window_sum(a: np.ndarray, r: int) -> np.ndarray:
    """Sum of ``a`` over the (2r+1)^2 window around every cell (clipped at the array edge)."""
    S = np.zeros((a.shape[0] + 1, a.shape[1] + 1), np.float64)
    S[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    ny, nx = a.shape
    j = np.arange(ny)[:, None]; i = np.arange(nx)[None, :]
    j0 = np.clip(j - r, 0, ny); j1 = np.clip(j + r + 1, 0, ny)
    i0 = np.clip(i - r, 0, nx); i1 = np.clip(i + r + 1, 0, nx)
    return S[j1, i1] - S[j0, i1] - S[j1, i0] + S[j0, i0]


def texture_layers(code: np.ndarray, cs: float, r: int = TEXTURE_R) -> dict[str, np.ndarray]:
    """Fuel-texture statistics, FORBIDDEN as features (section 4); built only for the section-8 fuel-seam control.
    code_entropy_1km: Shannon entropy (nats) of the code within the 1 km window; patch_size: area (km2) of the
    8-connected same-code patch holding the cell; edge_density_1km: share of 4-neighbour pairs in the window
    whose codes differ."""
    n_win = window_sum(np.ones(code.shape, np.float32), r)
    ent = np.zeros(code.shape, np.float64)
    patch = np.zeros(code.shape, np.float32)
    for v in np.unique(code):
        ind = code == v
        cnt = window_sum(ind.astype(np.float32), r)
        p = cnt / n_win
        ent -= np.where(p > 0, p * np.log(np.where(p > 0, p, 1.0)), 0.0)
        lab, _ = ndimage.label(ind, structure=np.ones((3, 3)))
        sizes = np.bincount(lab.ravel())
        patch[ind] = sizes[lab[ind]]
    dh = np.zeros(code.shape, np.float32); dh[:, 1:] = (code[:, 1:] != code[:, :-1])
    dv = np.zeros(code.shape, np.float32); dv[1:, :] = (code[1:, :] != code[:-1, :])
    nh = np.zeros(code.shape, np.float32); nh[:, 1:] = 1
    nv = np.zeros(code.shape, np.float32); nv[1:, :] = 1
    pairs = window_sum(nh, r) + window_sum(nv, r)
    edge = (window_sum(dh, r) + window_sum(dv, r)) / np.maximum(pairs, 1)
    return dict(code_entropy_1km=ent.astype(np.float32), patch_size=(patch * cs * cs / 1e6).astype(np.float32),
                edge_density_1km=edge.astype(np.float32))


# ── per-fire context ──────────────────────────────────────────────────────────────────────────

@dataclass
class FireContext:
    fire_key: str
    meta: dict
    rec: dict
    code: np.ndarray
    curing: np.ndarray
    zone: np.ndarray
    hazard: np.ndarray
    us_group: np.ndarray | None
    d: pd.DataFrame          # detections with epochs (frozen rule), UTM x/y
    ci: np.ndarray
    cj: np.ndarray
    first_ep: np.ndarray
    T: np.ndarray            # first acquisition per epoch
    t_first_inper: pd.Timestamp
    wx: dict
    season: dict
    lat_c: float
    lng_c: float
    lng_grid: float
    burn_blk: np.ndarray
    hazshare: np.ndarray
    d_nonfuel: np.ndarray
    d_water: np.ndarray
    in_final_blk: np.ndarray
    texture: dict
    cy: np.ndarray
    cx: np.ndarray
    stn_dist: dict
    beta: dict | None = None
    region_code: int = 0

    @property
    def grid(self) -> dict:
        return self.meta["grid"]


def load_beta(guard: Guard) -> dict:
    p = P.POOL / "fit" / "fit_beta.json"
    if C.sha256(guard.check(p)) != P.FIT_BETA_SHA256:
        raise RuntimeError("fit_beta.json does not match the frozen sha256 (PREREG_ML_EMBER_DEV section 2)")
    return guard.read_json(p)["beta"]


def load_fire(fire_key: str, guard: Guard, beta: dict | None = None, with_texture: bool = True,
              assignment: str = "training") -> FireContext | None:
    """Everything ``run_fire`` derives before its interval loop, for a training fire. None when ineligible.
    ``assignment`` "decision" (stage-2 confirmation only, PREREG_ML_EMBER section 4) reads the fire's sealed
    ``viirs_decision`` detections through the guard, which refuses them without the registered confirmation commit."""
    import geopandas as gpd
    import shapely
    from pyproj import Transformer
    from scripts.validation.v8 import inputs as I
    if assignment == "training":
        guard.assert_training(fire_key)
    elif assignment != "decision":
        raise ValueError(f"assignment must be 'training' or 'decision', not {assignment!r}")
    fd = P.POOL / "fires" / fire_key.replace(":", "_")
    meta = guard.read_json(fd / "meta.json")
    if not meta.get("eligible_station"):
        return None
    g = meta["grid"]; ny, nx, cs = g["ny"], g["nx"], g["cell"]
    z = guard.load_npz(fd / "inputs.npz")
    code, curing, zone, hazard = z["code"], z["curing"], z["zone"], z["hazard"]
    guard.check(P.POOL / "raw")  # fire_record reads the MTBS shapefile / WFIGS cache below v8-pool/raw
    rec = I.fire_record(fire_key)
    per = gpd.GeoSeries([rec["geometry"]], crs=4326).to_crs(g["crs"]).iloc[0]
    if assignment == "training":
        guard.check(P.POOL / "viirs_training" / f"viirs_training_{rec['year']}.pkl.gz")
    else:
        guard.check(P.POOL / "viirs_decision" / f"{fire_key.replace(':', '_')}.pkl.gz")
    det = Iv.detections(fire_key, rec["year"], assignment)
    d = Iv.epochs(rec, det, g["crs"], per)
    if not len(d):
        return None
    wx = {s: guard.read_csv(fd / f"wx_{s}.csv", index_col=0, parse_dates=True) for s in set(meta["zone_station"])}
    return make_context(fire_key, meta, rec, z, d, per, wx, beta=beta, with_texture=with_texture)


def make_context(fire_key: str, meta: dict, rec: dict, z: dict, d: pd.DataFrame, per, wx: dict, beta: dict | None = None,
                 with_texture: bool = True, final_tol: float | None = None) -> FireContext:
    """Pure part of ``load_fire`` (no I/O): the static per-fire layers of ``run_fire`` from the input arrays
    ``z`` (``code, curing, zone, hazard[, fbfm40]``), the epoch-tagged detections ``d`` (``x, y, t, ep, nh, frp``),
    the final perimeter ``per`` (grid CRS) and the station tables ``wx``. Tests build synthetic fires here."""
    import shapely
    from pyproj import Transformer
    from scripts.validation.v8 import inputs as I
    g = meta["grid"]; ny, nx, cs = g["ny"], g["nx"], g["cell"]
    code, curing, zone, hazard = z["code"], z["curing"], z["zone"], z["hazard"]
    tol = C.frozen_jumps().FINAL_TOL if final_tol is None else final_tol
    inper = shapely.dwithin(per, shapely.points(d.x.values, d.y.values), tol)
    t_first = pd.Timestamp(d.t[inper].min()) if inper.any() else pd.Timestamp(d.t.min())
    ci = np.clip(((d.x - g["x0"]) / cs).astype(int), 0, nx - 1).values
    cj = np.clip(((g["y1"] - d.y) / cs).astype(int), 0, ny - 1).values
    first_ep = np.full((ny, nx), 1 << 30, np.int32)
    np.minimum.at(first_ep, (cj, ci), d.ep.values.astype(np.int32))
    T = d.groupby("ep").t.min().values
    lat_c = float(rec["geometry"].centroid.y); lng_c = float(rec["geometry"].centroid.x)
    tr = Transformer.from_crs(g["crs"], 4326, always_xy=True)
    lng_grid = float(tr.transform(g["x0"] + nx * cs / 2, g["y1"] - ny * cs / 2)[0])
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
    haz = hazard.astype(np.float32)
    k500 = np.hypot(*np.mgrid[-4:5, -4:5]) * cs <= 500.0
    hazshare = fftconvolve(haz, k500.astype(np.float32) / k500.sum(), mode="same")
    cy, cx = np.arange(bny) * f + 1, np.arange(bnx) * f + 1
    season = {gr: Iv.fbp_of_code(code, gr) for gr in (False, True)}
    season = {gr: (tid, pc, Iv.waf_class(tid, us_group)) for gr, (tid, pc) in season.items()}
    # receptor distances (section 4): to the nearest non-fuel cell and to the nearest water cell (code 102)
    d_nonfuel = ndimage.distance_transform_edt(burnable_cell) * cs if (~burnable_cell).any() else np.full(code.shape, np.nan)
    water = code == 102
    d_water = ndimage.distance_transform_edt(~water) * cs if water.any() else np.full(code.shape, np.nan)
    # control only: block centre inside the final perimeter
    X = g["x0"] + (cx + 0.5) * cs; Y = g["y1"] - (cy + 0.5) * cs
    XX, YY = np.meshgrid(X, Y)
    in_final_blk = shapely.contains_xy(per, XX.ravel(), YY.ravel()).reshape(bny, bnx)
    texture = texture_layers(code, cs) if with_texture else {}
    stn_dist = {s["sid"]: float(s["dist_km"]) for s in meta["stations"]}
    return FireContext(fire_key, meta, rec, code, curing, zone, hazard, us_group, d, ci, cj, first_ep, T, t_first, wx,
                       season, lat_c, lng_c, lng_grid, burn_blk, hazshare, d_nonfuel, d_water, in_final_blk, texture,
                       cy, cx, stn_dist, beta, REGION_CODE[rec["region"]])


# ── per-interval products ─────────────────────────────────────────────────────────────────────

@dataclass
class IntervalProducts:
    fire_key: str
    epoch: int
    t_start: pd.Timestamp
    t_end: pd.Timestamp
    bj: np.ndarray
    bi: np.ndarray
    cells: dict = field(default_factory=dict)       # section-4 columns, candidate-level
    interval: dict = field(default_factory=dict)    # interval-level scalars
    control: dict = field(default_factory=dict)     # bi, bj, in_final, texture (never features)
    recomputed: dict = field(default_factory=dict)  # npz-equivalent fields from the sibling path
    npz: dict | None = None

    @property
    def n(self) -> int:
        return len(self.bj)

    def verify(self) -> dict[str, float]:
        """Max absolute difference between the recomputed npz fields and the v8 npz (0.0 = identical)."""
        out = {}
        if self.npz is None:
            return out
        for k in NPZ_FIELDS:
            a, b = np.asarray(self.recomputed[k]), np.asarray(self.npz[k])
            if a.shape != b.shape:
                out[k] = float("inf"); continue
            out[k] = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64)))) if a.size else 0.0
        return out


def iter_intervals(ctx: FireContext, guard: Guard | None = None, det_override: pd.DataFrame | None = None):
    """Yield ``IntervalProducts`` for every valid interval (<= 12 h, non-empty start perimeter), replicating the
    loop of ``intervals.run_fire`` step for step (perimeters, active segments, previous-interval spread, FBP at
    the sources) and adding the section-4 features. ``det_override`` lets a test perturb the detection table
    (epoch i FRP / positions) to prove that no feature depends on epoch i."""
    from firesim.fbp.calculator import calculate_foliar_moisture
    g = ctx.grid; ny, nx, cs = g["ny"], g["nx"], g["cell"]
    d = ctx.d if det_override is None else det_override
    T, first_ep = ctx.T, ctx.first_ep
    f = C.CAND_F
    bny, bnx = ny // f, nx // f
    cy, cx = ctx.cy, ctx.cx
    zst = ctx.meta["zone_station"]
    fd = P.POOL / "fires" / ctx.fire_key.replace(":", "_")
    prev_closed, prev_T = None, None
    for i in range(1, len(T)):
        L = (T[i] - T[i - 1]) / np.timedelta64(1, "h")
        closed = start_perimeter(first_ep, i, cs)
        if L > 12.0 or not closed.any():
            prev_closed, prev_T = closed, T[i - 1]
            continue
        t0, t1 = pd.Timestamp(T[i - 1]), pd.Timestamp(T[i])
        doy = t0.dayofyear
        names, pcs, wcls = ctx.season[150 <= doy <= 258]
        edge = closed & ~ndimage.binary_erosion(closed)
        rec_det = (d.t <= t0) & (d.t >= t0 - pd.Timedelta(hours=12)) & d.nh
        recent = np.zeros((ny, nx), bool)
        recent[ctx.cj[rec_det.values], ctx.ci[rec_det.values]] = True
        act = edge & (ndimage.distance_transform_edt(~recent) * cs <= Iv.ACTIVE_R) if recent.any() else np.zeros_like(edge)
        dist_all, idx_all = ndimage.distance_transform_edt(~closed, return_indices=True)
        dist_all *= cs
        aj, ai = np.nonzero(act)
        n_src = len(aj)
        # previous-interval spread and FBP at every active cell (run_fire's per-zone source loop)
        ros_g = np.full((ny, nx), np.nan, np.float64); dprev_g = np.full((ny, nx), np.nan, np.float64)
        inten_g = np.zeros((ny, nx), np.float64); cfb_g = np.zeros((ny, nx), np.float64)
        zinfo = []
        fmc = calculate_foliar_moisture(ctx.lat_c, ctx.lng_c, None, doy)
        for zi, sid in enumerate(zst):
            zw = interval_weather(ctx.wx[sid], t0, t1)
            zinfo.append(zw)
            sel = ctx.zone[aj, ai] == zi if len(zst) > 1 else np.ones(len(aj), bool)
            if not sel.any():
                continue
            sj, si = aj[sel], ai[sel]
            if prev_closed is not None and prev_T is not None:
                dprev = ndimage.distance_transform_edt(~prev_closed)[sj, si] * cs
                dtp = max((T[i - 1] - prev_T) / np.timedelta64(1, "m"), 60.0)
                ros = np.maximum(dprev / dtp, Iv.ROS_FLOOR)
                dprev_g[sj, si] = dprev
            else:
                ros = np.full(len(sj), Iv.ROS_FLOOR)
            ros_g[sj, si] = ros
            nm = names[sj, si]; ok = nm != 0
            sj2, si2, ros2, nm2 = sj[ok], si[ok], ros[ok], nm[ok]
            if len(sj2):
                tfc, inten, cfb = fbp_at_sources(nm2, pcs[sj2, si2], ros2, zw["ffmc"], zw["bui"], fmc)
                inten_g[sj2, si2] = np.nan_to_num(inten); cfb_g[sj2, si2] = np.nan_to_num(cfb)
        # candidate blocks (identical selection to run_fire)
        dcen = dist_all[np.ix_(cy, cx)]
        incl = closed.reshape(bny, f, bnx, f).any(axis=(1, 3))
        cand = (dcen >= Iv.BAND[0]) & (dcen <= Iv.BAND[1]) & ~incl & ctx.burn_blk
        bj, bi = np.nonzero(cand)
        jj, ii_ = cy[bj], cx[bi]
        if act.any():
            dact, (aj2, ai2) = ndimage.distance_transform_edt(~act, return_indices=True)
            d_act = dact[jj, ii_] * cs
            nj, ni = aj2[jj, ii_], ai2[jj, ii_]
            vy, vx = -(jj - nj) * cs, (ii_ - ni) * cs
        else:
            d_act = dcen[bj, bi]
            nj, ni = idx_all[0][jj, ii_], idx_all[1][jj, ii_]
            vy, vx = -(jj - nj) * cs, (ii_ - ni) * cs
        pvy, pvx = -(jj - idx_all[0][jj, ii_]) * cs, (ii_ - idx_all[1][jj, ii_]) * cs
        zb = ctx.zone[jj, ii_] if len(zst) > 1 else np.zeros(len(jj), np.int16)
        to = np.array([zi["to_rad"] for zi in zinfo])[zb]
        off_act = np.abs(((np.arctan2(vx, vy) - to + np.pi) % (2 * np.pi)) - np.pi)
        off_all = np.abs(((np.arctan2(pvx, pvy) - to + np.pi) % (2 * np.pi)) - np.pi)
        sec_act = off_act <= np.radians(45)
        sec_all = off_all <= np.radians(45)
        ip = IntervalProducts(ctx.fire_key, i, t0, t1, bj.astype(np.int32), bi.astype(np.int32))
        ip.recomputed = dict(bj=bj.astype(np.int32), bi=bi.astype(np.int32), d_act=d_act.astype(np.float32),
                             d_all=dcen[bj, bi].astype(np.float32), sec_act=sec_act, sec_all=sec_all,
                             hazshare=ctx.hazshare[jj, ii_].astype(np.float32),
                             u10=np.array([zz["u10"] for zz in zinfo], np.float32),
                             m_ff=np.array([zz["m_ff"] for zz in zinfo], np.float32), zone=zb.astype(np.int16))
        # the v8 npz of this interval (A, zone, u10, m_ff, hazshare, distances, sectors)
        npz = guard.load_npz(fd / "intervals" / f"{i:05d}.npz") if guard is not None else None
        ip.npz = npz
        src = npz if npz is not None else dict(A=np.zeros((2, 3, len(bj)), np.float32), **ip.recomputed)
        # FRP at epoch i-1 only: detections of epoch i-1 within 1 km of an active cell
        if act.any():
            prev_det = (d.ep == i - 1).values
            dact_full = ndimage.distance_transform_edt(~act) * cs
            near = dact_full[ctx.cj[prev_det], ctx.ci[prev_det]] <= Iv.ACTIVE_R
            frp_prev = float(np.nansum(d.frp.values[prev_det][near]))
        else:
            frp_prev = 0.0
        n_edge = int(edge.sum())
        area_km2 = float(closed.sum()) * cs * cs / 1e6
        age_h = (t0 - ctx.t_first_inper) / pd.Timedelta(hours=1)
        hour_local = (t0.hour + t0.minute / 60.0 + t0.second / 3600.0 + ctx.lng_grid / 15.0) % 24.0
        def zcol(key):
            return np.array([zw[key] for zw in zinfo], np.float64)[zb]
        n = len(bj)
        A = src["A"].astype(np.float32)
        lam = Iv.m8_score(src, ctx.beta) if ctx.beta is not None else np.full(n, np.nan)
        cells = dict(
            d_act_km=src["d_act"].astype(np.float64) / 1000.0, d_all_km=src["d_all"].astype(np.float64) / 1000.0,
            sec_act=src["sec_act"].astype(np.int8), sec_all=src["sec_all"].astype(np.int8),
            bear_off_deg=np.degrees(off_act), act_len_km=np.full(n, n_src * cs / 1000.0),
            act_share=np.full(n, n_src / n_edge if n_edge else np.nan), fire_area_km2=np.full(n, area_km2),
            fire_age_h=np.full(n, age_h), gap_h=np.full(n, float(L)), hour_local=np.full(n, hour_local),
            u10_ms=zcol("u10"), wd_deg=zcol("wd_from"), m_ff_pct=zcol("m_ff"), ffmc=zcol("ffmc"),
            temp_c=zcol("temp"), rh_pct=zcol("rh"),
            stn_dist_km=np.array([ctx.stn_dist.get(zst[int(z)], np.nan) for z in zb], np.float64),
            I_head_kwm=inten_g[nj, ni] if act.any() else np.full(n, np.nan),
            ros_mmin=ros_g[nj, ni] if act.any() else np.full(n, np.nan),
            cfb=cfb_g[nj, ni] if act.any() else np.full(n, np.nan),
            spread_prev_km=(dprev_g[nj, ni] / 1000.0) if act.any() else np.full(n, np.nan),
            frp_prev_mw=np.full(n, frp_prev),
            fuel_code=ctx.code[jj, ii_].astype(np.int16), fuel_group=wcls[jj, ii_].astype(np.int8),
            hazard=ctx.hazard[jj, ii_].astype(np.int8), hazshare=src["hazshare"].astype(np.float64),
            curing_pct=ctx.curing[jj, ii_].astype(np.float64) * 100.0,
            d_nonfuel_km=ctx.d_nonfuel[jj, ii_] / 1000.0, d_water_km=ctx.d_water[jj, ii_] / 1000.0,
            A_s0=A[0, 0], A_s1=A[0, 1], A_s2=A[0, 2], A_l0=A[1, 0], A_l1=A[1, 1], A_l2=A[1, 2],
            zone=src["zone"].astype(np.int16), m8_log_frozen=np.log(np.maximum(lam, 1e-300)),
            b1=Iv.b1_score(src, True).astype(np.float64),
            region=np.full(n, ctx.region_code, np.int8), year=np.full(n, int(ctx.rec["year"]), np.int16),
            sat_era=np.full(n, 1 if t0 >= pd.Timestamp("2018-04-01") else 0, np.int8),
            fuel_restored_share=np.full(n, float(ctx.meta.get("fuel_restored_share") or 0.0)),
        )
        assert set(cells) == set(CELL_FEATURES), set(cells) ^ set(CELL_FEATURES)
        ip.cells = cells
        z0 = zinfo[0]
        ip.interval = dict(fire_key=ctx.fire_key, epoch=i, t_start=t0, t_end=t1, gap_h=float(L), n_candidates=n,
                           region=ctx.region_code, year=int(ctx.rec["year"]), u10_ms=z0["u10"], wd_deg=z0["wd_from"],
                           m_ff_pct=z0["m_ff"], ffmc=z0["ffmc"], temp_c=z0["temp"], rh_pct=z0["rh"],
                           act_len_km=n_src * cs / 1000.0, act_share=n_src / n_edge if n_edge else float("nan"),
                           fire_area_km2=area_km2, fire_age_h=age_h,
                           I_head_max_kwm=float(inten_g[aj, ai].max()) if n_src else 0.0)
        ip.control = dict(bi=bi.astype(np.int32), bj=bj.astype(np.int32), in_final=ctx.in_final_blk[bj, bi].astype(np.int8),
                          fuel_code=cells["fuel_code"], hazard=cells["hazard"], curing_pct=cells["curing_pct"],
                          **{k: v[jj, ii_] for k, v in ctx.texture.items()})
        yield ip
        prev_closed, prev_T = closed, T[i - 1]


def verify_fire(fire_key: str, guard: Guard) -> dict:
    """Recompute every valid interval of a training fire and compare with the v8 npz: per field, the maximum
    absolute difference over all intervals (0.0 = bit-identical). Also compares the candidate count with
    ``_done.json``."""
    ctx = load_fire(fire_key, guard, beta=None, with_texture=False)
    if ctx is None:
        return {"status": "ineligible"}
    fd = P.POOL / "fires" / fire_key.replace(":", "_")
    done = guard.read_json(fd / "intervals" / "_done.json")
    by_epoch = {it["epoch"]: it for it in done["intervals"]}
    worst = {k: 0.0 for k in NPZ_FIELDS}
    seen = []
    for ip in iter_intervals(ctx, guard):
        seen.append(ip.epoch)
        for k, v in ip.verify().items():
            worst[k] = max(worst[k], v)
        it = by_epoch.get(ip.epoch)
        worst["n_cand_vs_done"] = max(worst.get("n_cand_vs_done", 0.0), abs(ip.n - it["n_cand"]) if it else float("inf"))
        worst["n_active_vs_done"] = max(worst.get("n_active_vs_done", 0.0),
                                        abs(ip.interval["act_len_km"] * 1000 / ctx.grid["cell"] - it["n_active"]) if it else float("inf"))
    worst["epochs_match_done"] = 0.0 if sorted(seen) == sorted(by_epoch) else float("inf")
    return dict(status="ok", n_intervals=len(seen), max_abs_diff=worst)
