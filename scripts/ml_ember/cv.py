"""Shared development machinery for the ML ember-cast ladder, controls and β_w check.

Implements PREREG_ML_EMBER_DEV section 6 (nested whole-fire grouped CV), the S3 statistic of PREREG_LR8
section 6 as the v8 scorer computes it (imported from ``scripts/validation/v8_score.py``: percentile rank with
half ties, S3 weights with rho 0.30, fire bootstrap, fire-level sign-flip test) with the development seeds of
section 6, and the per-fold refit of M8's coefficients with the frozen ``fit.py`` (section 4 ``m8_log``).

Nothing here opens a pool file except through ``loader.Guard``. Nothing here reads a decision fire.

Seeds (section 6): S_FOLD = 2026102502 (outer folds; inner folds use S_FOLD + 1000 * (outer + 1), disclosed
here because section 6 fixed only the outer stream), S_MODEL = 2026102503, S_BOOT_DEV = 2026102504,
S_TEST_DEV = 2026102505, S_CTRL = 2026102506 (section 8).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.ml_ember import paths as P
from scripts.validation import v8_score as V
from scripts.validation.v8 import fit as F

S_FOLD, S_MODEL, S_BOOT_DEV, S_TEST_DEV, S_CTRL = 2026102502, 2026102503, 2026102504, 2026102505, 2026102506
N_OUTER, N_INNER = 5, 3

GEOMETRY = ["d_act_km", "d_all_km", "sec_act", "sec_all", "bear_off_deg", "act_len_km", "act_share",
            "fire_area_km2", "fire_age_h", "gap_h", "hour_local"]
WEATHER = ["u10_ms", "wd_deg", "m_ff_pct", "ffmc", "temp_c", "rh_pct"]
FIRE_BEHAVIOUR = ["I_head_kwm", "ros_mmin", "cfb", "spread_prev_km", "frp_prev_mw"]
RECEPTOR = ["fuel_code", "fuel_group", "hazard", "hazshare", "curing_pct", "d_nonfuel_km", "d_water_km"]
TERRAIN = ["elev_m", "slope_pct", "aspect_deg", "tpi_1km"]
PHYSICS = ["m8_log", "b1"]
DIAGNOSTIC = ["stn_dist_km", "region", "year", "sat_era", "fuel_restored_share"]  # never product features
PRODUCT_FEATURES = GEOMETRY + WEATHER + FIRE_BEHAVIOUR + RECEPTOR + TERRAIN + PHYSICS
FORBIDDEN = {"bi", "bj", "row", "col", "in_final", "code_entropy_1km", "patch_size", "edge_density_1km",
             "gap_class", "n_jumps", "fire_key", "epoch", "row_id", "y", "w_sample", "m8_log_frozen"}


def table(name: str, columns=None) -> pd.DataFrame:
    import pyarrow.parquet as pq
    return pq.read_table(P.TABLE_DIR / f"{name}.parquet", columns=columns).to_pandas()


def assert_allowed(columns) -> None:
    bad = sorted(set(columns) & FORBIDDEN)
    if bad:
        raise ValueError(f"forbidden feature columns: {bad}")


# ── folds (section 6) ────────────────────────────────────────────────────────────────────────────
def grouped_folds(fires: pd.DataFrame, n_folds: int, seed: int) -> dict[str, int]:
    """Fire -> fold. Strata = region x year; keys sorted within a stratum, permuted with
    default_rng(seed + stratum_index) (strata in sorted order), dealt round robin."""
    out: dict[str, int] = {}
    strata = sorted(set(zip(fires.region.astype(int), fires.year.astype(int))))
    for si, (r, y) in enumerate(strata):
        keys = sorted(fires.fire_key[(fires.region == r) & (fires.year == y)])
        rng = np.random.default_rng(seed + si)
        for j, k in enumerate(rng.permutation(keys)):
            out[str(k)] = j % n_folds
    return out


def outer_folds(fires: pd.DataFrame) -> dict[str, int]:
    return grouped_folds(fires, N_OUTER, S_FOLD)


def inner_folds(fires: pd.DataFrame, train_keys, outer: int) -> dict[str, int]:
    return grouped_folds(fires[fires.fire_key.isin(train_keys)], N_INNER, S_FOLD + 1000 * (outer + 1))


# ── S3 statistic (PREREG_LR8 section 6, as v8_score) ─────────────────────────────────────────────
def interval_ranks(df: pd.DataFrame, score: np.ndarray) -> pd.DataFrame:
    """Percentile rank (ties half) of every positive among all candidate cells of its interval.
    df needs fire_key, epoch, y (and region); returns one row per positive."""
    rows = []
    s = np.asarray(score, float)
    y = df.y.to_numpy()
    g = df.groupby(["fire_key", "epoch"], sort=False).indices
    for (fk, ep), idx in g.items():
        pos = idx[y[idx] == 1]
        if len(pos) == 0:
            continue
        r = V.pct_rank(s[idx], s[pos])
        rows.append(pd.DataFrame(dict(fire_key=fk, epoch=ep, rank=r)))
    return pd.concat(rows, ignore_index=True)


def per_fire_a(ranks_a: pd.DataFrame, ranks_b: pd.DataFrame | None, fires_region: pd.Series) -> pd.DataFrame:
    """a_f = mean over the fire's jumps of (rank_a - rank_b) (rank_b = 0.5 when b is None, i.e. AUC - 0.5),
    K_f = number of ranked jumps; S3 weights as v8_score.weights."""
    a = ranks_a.copy()
    a["diff"] = a["rank"] - (0.5 if ranks_b is None else ranks_b["rank"].to_numpy())
    pf = a.groupby("fire_key").agg(a=("diff", "mean"), K=("diff", "size")).reset_index()
    pf["w"] = V.weights(pf.K.to_numpy(), "S3")
    pf["region"] = pf.fire_key.map(fires_region).to_numpy()
    return pf


def s3(pf: pd.DataFrame, ci: bool = True, test: bool = True) -> dict:
    a, w, reg = pf.a.to_numpy(float), pf.w.to_numpy(float), pf.region.to_numpy()
    out = dict(S3=float((w * a).sum() / w.sum()), n_fires=int(len(pf)), K=int(pf.K.sum()))
    if ci:
        out["ci95"] = V.boot_ci(a, w, reg, B=V.N_BOOT, seed=S_BOOT_DEV)
    if test:
        out["p_signflip"] = V.signflip_p(a, w, reg, flips=V.N_FLIPS, seed=S_TEST_DEV)
    return out


def auc_from_ranks(ranks: pd.DataFrame, fires_region: pd.Series) -> dict:
    """S3-weighted mean percentile rank = within-interval AUC (point, CI, by region)."""
    pf = per_fire_a(ranks, None, fires_region)
    res = s3(pf, ci=True, test=False)
    out = dict(AUC=res["S3"] + 0.5, ci95=(res["ci95"][0] + 0.5, res["ci95"][1] + 0.5), n_fires=res["n_fires"], K=res["K"])
    for r in sorted(pf.region.unique()):
        q = pf[pf.region == r]
        out[f"AUC_region{int(r)}"] = float((q.w * q.a).sum() / q.w.sum() + 0.5)
    return out


# ── M8 coefficients per fold (section 4 m8_log; section 6) ──────────────────────────────────────
def suffstats_path() -> Path:
    return P.TABLE_DIR / "m8_suffstats.parquet"


def build_suffstats(guard, fire_keys, fuel_group_of_cell) -> pd.DataFrame:
    """Per (fire, epoch, zone, receptor fuel group): the summed landing statistics S[mode, w] over the
    interval's candidate cells, with the zone's u10 and m_ff, hours and n_active. Reads only training
    fires' intervals through the guard. ``fuel_group_of_cell(fire_key, npz) -> int8 array`` supplies the
    receptor group of every candidate cell (the §4 ``fuel_group``)."""
    from scripts.validation.v8 import common as C
    rows = []
    for k in fire_keys:
        guard.assert_training(k)
        fd = C.FIRES / k.replace(":", "_")
        done = guard.read_json(fd / "intervals" / "_done.json")
        if done.get("status") != "ok":
            continue
        for it in done["intervals"]:
            z = guard.load_npz(fd / "intervals" / f"{it['epoch']:05d}.npz")
            g = fuel_group_of_cell(k, z)
            A = z["A"].astype(np.float64)
            for zi in range(len(z["u10"])):
                inz = z["zone"] == zi
                for grp in (-1, 0, 1, 2):
                    sel = inz & (g == grp)
                    if not sel.any():
                        continue
                    S = A[:, :, sel].sum(axis=-1)
                    rows.append(dict(fire_key=k, epoch=int(it["epoch"]), t_end=pd.Timestamp(it["t_end"]), hours=float(it["hours"]),
                                     n_active=int(it["n_active"]), zone=zi, fuel_group=grp, n_cells=int(sel.sum()),
                                     u10=float(z["u10"][zi]), m_ff=float(z["m_ff"][zi]),
                                     S_s0=S[0, 0], S_s1=S[0, 1], S_s2=S[0, 2], S_l0=S[1, 0], S_l1=S[1, 1], S_l2=S[1, 2]))
    return pd.DataFrame(rows)


def to_fit_inputs(ss: pd.DataFrame, pos: pd.DataFrame):
    """fit.py's S_rows (one per interval, S[zone] summed over groups) and pos (A, u10, m per positive)."""
    S_rows = []
    for (fk, ep), g in ss.groupby(["fire_key", "epoch"], sort=False):
        nz = int(g.zone.max()) + 1
        Sz = np.zeros((nz, 2, 3)); u = np.zeros(nz); m = np.zeros(nz)
        for zi, gz in g.groupby("zone"):
            Sz[zi, 0] = gz[["S_s0", "S_s1", "S_s2"]].sum().to_numpy(); Sz[zi, 1] = gz[["S_l0", "S_l1", "S_l2"]].sum().to_numpy()
            u[zi] = gz.u10.iloc[0]; m[zi] = gz.m_ff.iloc[0]
        S_rows.append(dict(fire_key=fk, epoch=ep, S=Sz, u10=u, m=m))
    P_ = [dict(fire_key=r.fire_key, epoch=r.epoch, A=np.array([[r.A_s0, r.A_s1, r.A_s2], [r.A_l0, r.A_l1, r.A_l2]], float),
               u10=float(r.u10_ms), m=float(r.m_ff_pct)) for r in pos.itertuples()]
    return S_rows, P_


def refit_beta(ss: pd.DataFrame, pos: pd.DataFrame, fire_keys) -> dict:
    """M8's b0, b_w, b_L on the given fires with the frozen fit.py (same priors, bounds, >= 100 jumps rule)."""
    keys = set(fire_keys)
    S_rows, P_ = to_fit_inputs(ss[ss.fire_key.isin(keys)], pos[pos.fire_key.isin(keys)])
    b, res = F.fit(S_rows, P_, full=len(P_) >= 100)
    b["n_pos"] = len(P_); b["n_intervals"] = len(S_rows); b["converged"] = bool(res.success)
    return b


def m8_log(cells: pd.DataFrame, beta: dict) -> np.ndarray:
    from scripts.validation.v8.intervals import WAF
    A_s = cells[["A_s0", "A_s1", "A_s2"]].to_numpy(float); A_l = cells[["A_l0", "A_l1", "A_l2"]].to_numpy(float)
    u = cells.u10_ms.to_numpy(float)[:, None]; m = cells.m_ff_pct.to_numpy(float)
    lam = (np.exp(beta["b_w"] * np.log1p(u * WAF[None, :])) * (A_s + math.exp(beta["b_L"]) * A_l)).sum(1)
    return np.log(np.maximum(lam * np.exp(beta["b0"] + beta["b_m"] * (m - 21.0)), 1e-300))
