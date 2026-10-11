"""Stand objects and the stand-level conifer model of the v2 key (2.1.0).

* **Stand objects.** In Edmonton the City's uPLVI 2025 polygons (open data) are the stands; forest
  cells outside them, and all forest elsewhere, are split into connected regions of one conifer
  class cut by 200 m blocks (``class_objects``). Typing per object keeps pure stands from being
  averaged with their neighbours. (SLIC was tried first: with a forest mask, scikit-image's SLIC
  used ~13 GB and returned a few huge segments, so it was dropped.)
* **Stand model (Edmonton / St. Albert).** Labels are uPLVI percent conifer per polygon (photo-
  interpreted, treed components weighted by their share; larch counted deciduous as CFS 2018),
  not the City LiDAR crowns. Features are the v1 open features averaged over the polygon. A
  LightGBM classifier (C >= 75 / M / D <= 25 % conifer, class-balanced) decides the type and a
  LightGBM regressor gives percent conifer for M-1/M-2. Spatial hold-out: polygons are grouped by
  the 2 km blocks of v1 (``model.block_folds``), so Edmonton's map is out of fold everywhere.
* **Elsewhere** (northern test areas): no labels; SCANFI conifer share averaged per segment, no
  re-mapping.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from . import config as C
    from .grid import Grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import Grid  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.stands")
UPLVI = C.REFERENCE_LAYERS / "edmonton_uplvi/uplvi_2025.geojson"
CLASSES = ("D", "M", "C")


# ── segmentation ─────────────────────────────────────────────────────────────
def class_objects(share: np.ndarray, mask: np.ndarray, block_cells: int = C.PARAMS_V2["object_block_cells"],
                  start: int = 1, p: dict = C.PARAMS_V2) -> np.ndarray:
    """Stand objects: connected regions (8-neighbour) of one conifer class (D / M / C of the 3 x 3
    mean conifer share over the mask cells), cut by square blocks of `block_cells` (200 m: <= 4 ha).

    Deterministic, memory-light, and homogeneous by construction, so a pure stand is not averaged
    with a different neighbour. 0 outside the mask; ids from `start`."""
    from scipy import ndimage as ndi
    out = np.zeros(mask.shape, np.int32)
    if not mask.any():
        return out
    a = np.where(mask, np.nan_to_num(np.asarray(share, np.float32)), 0)
    w = ndi.uniform_filter(mask.astype(np.float32), 3)
    with np.errstate(invalid="ignore", divide="ignore"):
        sm = np.where(w > 0, ndi.uniform_filter(a, 3) / w, 0)
    cls = np.ones(mask.shape, np.int8)
    cls[sm <= p["conifer_d"]] = 0
    cls[sm >= p["conifer_c"]] = 2
    comp = np.zeros(mask.shape, np.int64)
    off = 0
    for k in (0, 1, 2):
        lab, n = ndi.label(mask & (cls == k), structure=np.ones((3, 3), bool))
        comp[lab > 0] = lab[lab > 0] + off
        off += n
    r, c = np.indices(mask.shape)
    block = (r // block_cells) * ((mask.shape[1] + block_cells - 1) // block_cells) + c // block_cells
    key = np.where(mask, comp * (block.max() + 1) + block, -1)
    u, inv = np.unique(key, return_inverse=True)
    ids = inv.reshape(mask.shape).astype(np.int32)  # 0 is the -1 key (outside) when present
    if u[0] != -1:
        ids += 1
    out[mask] = ids[mask] + (start - 1)
    return out


def relabel(ids: np.ndarray) -> np.ndarray:
    """Consecutive ids 1..n (0 stays 0)."""
    u, inv = np.unique(ids, return_inverse=True)
    out = inv.reshape(ids.shape).astype(np.int32)
    return out if u[0] == 0 else out + 1


def segment_means(stack: np.ndarray, ids: np.ndarray, n: int, mask: np.ndarray | None = None) -> np.ndarray:
    """(n + 1, bands) mean of each band over the cells of each id (row 0 unused; NaN-aware)."""
    flat = ids.ravel()
    m = np.ones(flat.shape, bool) if mask is None else mask.ravel()
    out = np.full((n + 1, stack.shape[0]), np.nan, np.float32)
    for b in range(stack.shape[0]):
        v = stack[b].ravel()
        ok = m & np.isfinite(v)
        cnt = np.bincount(flat[ok], minlength=n + 1)
        s = np.bincount(flat[ok], weights=v[ok], minlength=n + 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:, b] = np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)
    return out


# ── uPLVI labels ─────────────────────────────────────────────────────────────
def uplvi_percent_conifer(df) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(percent conifer of the treed components, treed share 0-1, all-treed flag) per polygon.

    CON1-3 = percent of trees that are conifer; STYPEPER1-3 = component share; components whose
    stand type is Non Forest (or empty) are not treed. Same definition as the reference-layer
    scorer (`edmonton_uplvi.py`)."""
    import pandas as pd
    tw = np.zeros(len(df))
    cw = np.zeros(len(df))
    pw = np.zeros(len(df))
    allt = np.ones(len(df), bool)
    for i in (1, 2, 3):
        stn = df[f"stnd{i}"].fillna("").astype(str)
        per = pd.to_numeric(df[f"stypeper{i}"], errors="coerce").fillna(0).to_numpy()
        con = pd.to_numeric(df[f"con{i}"], errors="coerce").fillna(0).to_numpy()
        t = ((stn != "") & ~stn.str.startswith("Non Forest")).to_numpy()
        allt &= ~((per > 0) & ~t)
        tw += per * t
        cw += per * t * con
        pw += per
    with np.errstate(invalid="ignore", divide="ignore"):
        pc = np.where(tw > 0, cw / np.maximum(tw, 1e-9), np.nan)
        share = np.where(pw > 0, tw / np.maximum(pw, 1e-9), 0.0)
    return pc, share, allt & (tw > 0)


def uplvi_raster(grid: Grid):
    """uPLVI polygon id raster (1..n, 0 = none) on the grid and the polygon table (with pc)."""
    import geopandas as gpd
    from rasterio.features import rasterize
    df = gpd.read_file(UPLVI).to_crs(grid.crs).reset_index(drop=True)
    df["pc"], df["treed_share"], df["all_treed"] = uplvi_percent_conifer(df)
    df["pid"] = np.arange(1, len(df) + 1)
    pid = rasterize(((g, int(p)) for g, p in zip(df.geometry, df.pid) if g is not None), out_shape=grid.shape,
                    transform=grid.transform, fill=0, dtype="int32")
    return pid, df


# ── stand model ──────────────────────────────────────────────────────────────
def cmd_class(pc: np.ndarray, p: dict = C.PARAMS_V2) -> np.ndarray:
    """0 = D (<= conifer_d), 1 = M, 2 = C (>= conifer_c); pc as a 0-1 fraction."""
    out = np.ones(np.shape(pc), np.int8)
    out[np.asarray(pc) <= p["conifer_d"]] = 0
    out[np.asarray(pc) >= p["conifer_c"]] = 2
    return out


def _clf(seed: int):
    import lightgbm as lgb
    return lgb.LGBMClassifier(objective="multiclass", n_estimators=300, learning_rate=0.03, num_leaves=15,
                              min_child_samples=10, subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
                              class_weight="balanced", random_state=seed, verbose=-1)


def _reg(seed: int):
    import lightgbm as lgb
    return lgb.LGBMRegressor(objective="cross_entropy", n_estimators=300, learning_rate=0.03, num_leaves=15,
                             min_child_samples=10, subsample=0.8, subsample_freq=1, colsample_bytree=0.7,
                             random_state=seed, verbose=-1)


class StandModel:
    """Class-balanced C/M/D classifier + percent-conifer regressor on stand-mean features."""

    def __init__(self, seed: int):
        self.seed = seed

    def fit(self, X: np.ndarray, pc: np.ndarray) -> "StandModel":
        y = cmd_class(pc)
        self.clf = _clf(self.seed).fit(X, y)
        w = np.ones(len(y))
        for k in np.unique(y):  # balance the regressor too, so pure deciduous does not dominate
            w[y == k] = len(y) / (len(np.unique(y)) * (y == k).sum())
        self.reg = _reg(self.seed).fit(X, pc, sample_weight=w)
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(class 0/1/2, unit conifer 0-1 consistent with the class)."""
        k = self.clf.classes_[np.argmax(self.clf.predict_proba(X), 1)]
        pc = np.clip(self.reg.predict(X), 0, 1)
        return k, consistent_pc(k, pc)


def consistent_pc(k: np.ndarray, pc: np.ndarray, p: dict = C.PARAMS_V2) -> np.ndarray:
    """Percent conifer that the key's cuts turn back into the predicted class: D <= conifer_d,
    C >= conifer_c, M kept strictly between (clipped to [conifer_d + 0.05, conifer_c - 0.05])."""
    out = np.asarray(pc, np.float64).copy()
    out[k == 0] = np.minimum(out[k == 0], p["conifer_d"])
    out[k == 2] = np.maximum(out[k == 2], p["conifer_c"])
    m = k == 1
    out[m] = np.clip(out[m], p["conifer_d"] + 0.05, p["conifer_c"] - 0.05)
    return out.astype(np.float32)


def balanced_accuracy(true_pc: np.ndarray, pred_class: np.ndarray, pure: float = 0.2) -> dict:
    """Pure-stand scores as in the reference-layer report: share of pure conifer (>= 1 - pure)
    predicted C and pure deciduous (<= pure) predicted D; BA = their mean (M counted wrong)."""
    pc_ = np.asarray(true_pc)
    con, dec = pc_ >= 1 - pure, pc_ <= pure
    c = float((pred_class[con] == 2).mean()) if con.any() else float("nan")
    d = float((pred_class[dec] == 0).mean()) if dec.any() else float("nan")
    opp = float(((pred_class[con] == 0).sum() + (pred_class[dec] == 2).sum()) / max(1, con.sum() + dec.sum()))
    return {"pure_conifer_n": int(con.sum()), "pure_deciduous_n": int(dec.sum()), "conifer_as_C": round(c, 3),
            "deciduous_as_D": round(d, 3), "balanced_accuracy": round((c + d) / 2, 3), "opposite_type": round(opp, 3)}


# ── transfer features (available everywhere: SCANFI + canopy model) ─────────
TRANSFER = ["v2_att_closure", "v2_att_height", "v2_spsCC_broadleaf", "v3_treed_coniferous", "v3_treed_broadleaf",
            "v3_nonTreed_tallShrubs", "v2_conifer_cc", "v2_conifer_share", "v3_conifer_share", "chm_cover5",
            "chm_height5", "chm_height5_sd3x3"]


def transfer_features(sc: dict, v3: dict, chm: np.ndarray) -> np.ndarray:
    """The TRANSFER features from SCANFI v2 (species crown closure, percent), SCANFI v3 fractions
    and the Meta canopy bands, defined exactly as in ``model.features`` (+ height texture)."""
    from scipy import ndimage as ndi
    g = {k: np.nan_to_num(np.asarray(v, np.float32)) for k, v in sc.items()}
    con = sum(g[n] for n in ("blackSpruce", "otherConiferous", "balsamFir", "jackPine", "lodgepolePine",
                             "tamarack", "whiteRedPine", "douglasFir", "ponderosaPine"))
    with np.errstate(invalid="ignore", divide="ignore"):
        share = con / (con + g["broadleaf"])
        v3c, v3b = np.asarray(v3["treed_coniferous"], np.float32), np.asarray(v3["treed_broadleaf"], np.float32)
        v3s = v3c / (v3c + v3b)
    h = np.nan_to_num(np.asarray(chm[3], np.float32))
    mu = ndi.uniform_filter(h, 3)
    sd = np.sqrt(np.maximum(ndi.uniform_filter(h * h, 3) - mu * mu, 0))
    out = [sc["att_closure"], sc["att_height"], sc["broadleaf"], v3c, v3b, v3["nonTreed_tallShrubs"], con, share, v3s,
           chm[0], chm[3], sd]
    return np.stack([np.asarray(a, np.float32) for a in out])
