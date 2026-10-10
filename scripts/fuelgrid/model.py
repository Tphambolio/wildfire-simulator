"""Conifer-fraction model: open-data features -> share of conifer crowns in a 20 m cell.

Trained on Edmonton only, with labels from the City LiDAR crowns (``labels.py``). Transfer
error is estimated by spatial-block cross-validation: Edmonton is cut into square blocks
(``PARAMS['block_m']``), blocks are dealt to folds at random, and every cell's prediction for
the Edmonton grid comes from a model that never saw its block (out-of-fold). St. Albert uses a
model fitted on all Edmonton labels.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from . import config as C
    from .grid import REGION, Grid, band_names, read_tif
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from grid import REGION, Grid, band_names, read_tif  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.model")


def _nd(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (a - b) / (a + b)


def features(grid: Grid) -> tuple[np.ndarray, list[str]]:
    """(n_features, rows, cols) float32 feature stack for a grid window of the region."""
    from scipy import ndimage as ndi
    feats, names = [], []

    def add(name, arr):
        feats.append(np.asarray(arr, np.float32))
        names.append(name)

    idx = {}
    for season in C.S2_SEASONS:
        s2 = read_tif(C.REGION_DIR / f"s2_{season}.tif", grid)
        b = dict(zip(band_names(C.REGION_DIR / f"s2_{season}.tif"), s2))
        for k in C.S2_BANDS:
            add(f"{season}_{k}", b[k])
        idx[season] = {
            "ndvi": _nd(b["nir"], b["red"]),
            "ndmi": _nd(b["nir"], b["swir16"]),
            "ndre": _nd(b["nir"], b["rededge1"]),
            "nbr": _nd(b["nir"], b["swir22"]),
            "ndsi": _nd(b["green"], b["swir16"]),
        }
        for k, v in idx[season].items():
            add(f"{season}_{k}", v)
            add(f"{season}_{k}_3x3", ndi.uniform_filter(np.nan_to_num(v), 3))
    for k in ("ndvi", "ndmi", "ndre"):
        add(f"d_{k}_summer_spring", idx["summer"][k] - idx["spring"][k])
        add(f"d_{k}_summer_autumn", idx["summer"][k] - idx["autumn"][k])
        add(f"d_{k}_summer_winter", idx["summer"][k] - idx["winter"][k])
    sc = read_tif(C.REGION_DIR / "scanfi.tif", grid)
    sb = dict(zip(band_names(C.REGION_DIR / "scanfi.tif"), sc))
    con = sum(np.nan_to_num(sb[f"v2_{n}"]) for n in
              ("spsCC_blackSpruce", "spsCC_otherConiferous", "spsCC_balsamFir", "spsCC_jackPine",
               "spsCC_lodgepolePine", "spsCC_tamarack", "spsCC_whiteRedPine", "spsCC_douglasFir",
               "spsCC_ponderosaPine"))
    for k in ("v2_att_closure", "v2_att_height", "v2_spsCC_broadleaf", "v3_treed_coniferous",
              "v3_treed_broadleaf", "v3_nonTreed_tallShrubs"):
        add(k, sb[k])
    add("v2_conifer_cc", con)
    with np.errstate(invalid="ignore", divide="ignore"):
        add("v2_conifer_share", con / (con + np.nan_to_num(sb["v2_spsCC_broadleaf"])))
        v3c, v3b = sb["v3_treed_coniferous"], sb["v3_treed_broadleaf"]
        add("v3_conifer_share", v3c / (v3c + v3b))
    chm = read_tif(C.REGION_DIR / "meta_chm.tif", grid)
    add("chm_cover5", chm[0])
    add("chm_height5", chm[3])
    return np.stack(feats), names


def scanfi_rule_conifer(grid: Grid) -> np.ndarray:
    """Untrained baseline: SCANFI v2 conifer share of crown closure (no Edmonton labels used)."""
    f, names = features(grid)
    return np.nan_to_num(f[names.index("v2_conifer_share")], nan=0.0)


def block_folds(grid: Grid, p: dict = C.PARAMS) -> np.ndarray:
    """Fold id per cell from square spatial blocks on the region grid (stable across AOIs)."""
    rs, cs = REGION.slices(grid)
    rows = np.arange(REGION.height)[rs]
    cols = np.arange(REGION.width)[cs]
    nb = int(p["block_m"] // C.CELL)
    br = rows[:, None] // nb
    bc = cols[None, :] // nb
    nbc = REGION.width // nb + 1
    bid = br * nbc + bc
    nblocks = (REGION.height // nb + 1) * nbc
    rng = np.random.default_rng(p["seed"])
    fold_of_block = rng.permutation(nblocks) % p["n_folds"]
    return fold_of_block[bid]


def _lgbm(seed: int):
    import lightgbm as lgb
    return lgb.LGBMRegressor(objective="cross_entropy", n_estimators=500, learning_rate=0.03,
                             num_leaves=31, min_child_samples=40, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.7, reg_lambda=1.0, random_state=seed, verbose=-1)


def train_mask(lab: dict, domain: np.ndarray) -> np.ndarray:
    """Training cells: >= 2 LiDAR crowns (the City grid's own forest rule) within the domain."""
    return (lab["cnt"] >= 2) & np.isfinite(lab["conifer_frac"]) & domain


class QuantileMapped:
    """LightGBM regressor whose output is quantile-mapped onto the training label distribution.

    A regression toward the mean almost never predicts >= 0.75 conifer, so C-2 would vanish.
    Mapping each prediction through F_label^-1(F_pred(p)), both CDFs from the training cells only,
    restores the label distribution (prior matching at every threshold) before the fixed
    C-2 / M-2 / D-2 cuts are applied. Selected by spatial-block CV on the LiDAR labels
    (C/M/D kappa 0.24 with plain thresholds vs 0.28 mapped); see docs/fuel-grid-open-data.md.
    """

    def __init__(self, seed: int):
        self.reg = _lgbm(seed)

    def fit(self, X, y):
        self.reg.fit(X, y)
        q = np.linspace(0, 1, 1001)
        self.xp = np.quantile(self.reg.predict(X), q)
        self.yp = np.quantile(y, q)
        return self

    def predict(self, X, mapped: bool = True):
        p = self.reg.predict(X)
        return np.interp(p, self.xp, self.yp) if mapped else p


def cv_predict(grid: Grid, X: np.ndarray, y: np.ndarray, train: np.ndarray, p: dict = C.PARAMS):
    """Out-of-fold conifer fraction for every cell of `grid`, plus held-out metrics per fold."""
    folds = block_folds(grid, p)
    Xf = X.reshape(X.shape[0], -1).T
    yf, tf, ff = y.ravel(), train.ravel(), folds.ravel()
    pred = np.full(yf.shape, np.nan, np.float32)
    raw = np.full(yf.shape, np.nan, np.float32)
    per_fold = []
    importances = np.zeros(X.shape[0])
    for k in range(p["n_folds"]):
        tr = tf & (ff != k)
        m = QuantileMapped(p["seed"] + k).fit(Xf[tr], yf[tr])
        importances += m.reg.booster_.feature_importance("gain")
        te_all = ff == k
        pred[te_all] = m.predict(Xf[te_all])
        raw[te_all] = m.predict(Xf[te_all], mapped=False)
        te = tf & te_all
        per_fold.append({"fold": k, "n_train": int(tr.sum()), "n_test": int(te.sum()),
                         **regression_metrics(yf[te], pred[te], p),
                         "unmapped": regression_metrics(yf[te], raw[te], p)})
        log.info("fold %d: n=%d mae=%.3f kappa=%.3f", k, int(te.sum()), per_fold[-1]["mae"], per_fold[-1]["cmd_kappa"])
    return pred.reshape(grid.shape), raw.reshape(grid.shape), per_fold, importances / p["n_folds"]


def fit_full(X: np.ndarray, y: np.ndarray, train: np.ndarray, p: dict = C.PARAMS):
    Xf = X.reshape(X.shape[0], -1).T
    return QuantileMapped(p["seed"]).fit(Xf[train.ravel()], y.ravel()[train.ravel()])


def predict(model, X: np.ndarray) -> np.ndarray:
    return model.predict(X.reshape(X.shape[0], -1).T).reshape(X.shape[1:]).astype(np.float32)


def regression_metrics(y: np.ndarray, yhat: np.ndarray, p: dict = C.PARAMS) -> dict:
    """MAE, RMSE, R^2 and C/M/D class agreement (thresholds of the decision key)."""
    try:
        from .rules import agreement, confusion, forest_type
    except ImportError:  # pragma: no cover
        from rules import agreement, confusion, forest_type  # type: ignore[no-redef]
    if len(y) == 0:
        return {}
    err = yhat - y
    ss = ((y - y.mean()) ** 2).sum()
    cm = confusion(forest_type(y, p), forest_type(yhat, p), [C.C2, C.M2, C.D2])
    a = agreement(cm)
    return {"mae": float(np.abs(err).mean()), "rmse": float(np.sqrt((err ** 2).mean())),
            "r2": float(1 - (err ** 2).sum() / ss) if ss > 0 else float("nan"),
            "cmd_oa": a["oa"], "cmd_kappa": a["kappa"], "cmd_confusion": cm.tolist(),
            "cmd_recall": a["recall"], "cmd_precision": a["precision"]}
