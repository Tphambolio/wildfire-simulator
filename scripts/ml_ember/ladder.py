"""PREREG_ML_EMBER_DEV sections 5-7: the model ladder under nested whole-fire grouped CV, with the stop rule.

Rungs (section 5), every one fitted on the section-3 subsample (weights w_sample) of the outer training fires and
scored on every candidate cell of the outer test fires (cells_eval); the only development numbers are the pooled
out-of-fold predictions:
  0  references: B1; M8 with (b0, b_w, b_L) refitted on the outer training fires by fit.py; distance-only logistic
     (d_act_km); distance + sec_act logistic.
  1  L2-penalised logistic GLM on <= 15 physics-named features, C chosen on the 3 inner folds from {0.01, 0.1, 1, 10}.
  2  additive: SplineTransformer (6 knots, degree 3, knots at training quantiles) + penalised logistic; the three
     monotone directions of section 5 are checked on the fitted ALE; a feature that violates its direction is
     re-entered linearly with the sign checked (the "monotone rule", disclosed; the spline library has no
     monotone constraint).
  3  LightGBM 4.6: max_depth 4, num_leaves 15, learning_rate 0.05, feature_fraction 0.8, min_data_in_leaf in
     {200, 500} and rounds (early stopping, patience 50, <= 2000) chosen on the inner folds; monotone constraints
     d_act_km -1, curing_pct +1, m_ff_pct -1; <= 25 features.
  4  hybrid: the base rung the stop rule selects, with m8_log (fold beta) as offset / init_score and the single
     pre-declared interaction d_act x fuel_group (interaction_constraints for the GBM base; a product term for the
     additive base).
Rungs 1-3 use measurable inputs only (no m8_log, no b1): the physics enters at rung 0 as a reference and at rung 4 as
the offset. This reading of "physics-named features" is disclosed here.

Statistics (section 6): S3 and its fire bootstrap 95 % interval (S_BOOT_DEV) on pooled out-of-fold ranks; per-fold
S3; fire-level sign-flip p (S_TEST_DEV) for every rung-to-rung difference; transfer folds (CAN->US, US->CAN,
leave-one-year-out 2023/2024/2025) for M8 and the carried rung. Stop rule (section 7) applied as written.
Interpretation: accumulated local effects (sign and shape per outer fold) and grouped permutation importance for
the carried rung and the hybrid; a finding needs the sign in >= 4 of 5 folds.

Missing values: GBM native; GLM / additive by outer-training-fold median with a missing indicator for the FBP
columns (disclosed). Weather arm: station only (section 10 primary); the ERA5 arm is not run here.
Output: reports/data/ml-ember-cast-2026-10-29/ladder/{ladder.json, oof_scores.parquet, betas.json}.
Usage: python -I scripts/ml_ember/ladder.py [--no-terrain] [--quick]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.ml_ember import cv  # noqa: E402
from scripts.ml_ember import paths as P  # noqa: E402

OUT = P.DATA_FOLDER / "ladder"
FBP = ["I_head_kwm", "ros_mmin", "cfb", "spread_prev_km"]
R1_FEATURES = ["d_act_km", "d_all_km", "bear_off_deg", "u10_ms", "m_ff_pct", "ffmc", "I_head_kwm", "ros_mmin",
               "frp_prev_mw", "hazshare", "curing_pct", "fuel_group", "slope_pct", "elev_m"]  # 14 features (fuel_group one-hot -> 16 columns); sec_act dropped: it equals 1{bear_off_deg <= 45} exactly (disclosed)
R3_FEATURES = [f for f in cv.PRODUCT_FEATURES if f not in ("m8_log", "b1")]  # 33 candidates -> trimmed to 25 below
R3_DROP = ["d_all_km", "sec_all", "sec_act", "wd_deg", "temp_c", "rh_pct", "fuel_code", "d_water_km", "aspect_deg"]  # correlated / circular / sparse duplicates
R3_FEATURES = [f for f in R3_FEATURES if f not in R3_DROP]
MONOTONE = {"d_act_km": -1, "curing_pct": +1, "m_ff_pct": -1}
LOG1P = ["I_head_kwm", "frp_prev_mw", "fire_area_km2", "act_len_km", "spread_prev_km"]
LGB_BASE = dict(objective="binary", learning_rate=0.05, max_depth=4, num_leaves=15, feature_fraction=0.8, lambda_l2=1.0,
                verbosity=-1, num_threads=4)
C_GRID = (0.01, 0.1, 1.0, 10.0)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
def design(df: pd.DataFrame, features, med: pd.Series | None, one_hot_fuel=True):
    X = df[features].copy()
    for c in LOG1P:
        if c in X:
            X[c] = np.log1p(X[c].clip(lower=0))
    if med is None:
        med = X.median(numeric_only=True)
    for c in FBP:
        if c in X:
            X[c + "_miss"] = X[c].isna().astype(np.float32)
    X = X.fillna(med)
    if one_hot_fuel and "fuel_group" in X:
        for g in (0, 1, 2):
            X[f"fuel_g{g}"] = (df.fuel_group.to_numpy() == g).astype(np.float32)
        X = X.drop(columns=["fuel_group"])
    return X.astype(np.float32), med


def inner_pick(fit_tr: pd.DataFrame, fires: pd.DataFrame, outer: int, candidates, fit_fn, score_fn):
    """Choose a hyperparameter on the 3 inner folds by pooled within-interval AUC (ranks of positives)."""
    region = fires.set_index("fire_key").region
    inner = cv.inner_folds(fires, fit_tr.fire_key.unique(), outer)
    best, best_auc, table = None, -1.0, {}
    for cand in candidates:
        ranks = []
        for j in range(cv.N_INNER):
            tr = fit_tr[fit_tr.fire_key.map(inner) != j]; va = fit_tr[fit_tr.fire_key.map(inner) == j]
            if va.y.sum() == 0 or tr.y.sum() == 0:
                continue
            m = fit_fn(tr, cand)
            ranks.append(cv.interval_ranks(va, score_fn(m, va)))
        if not ranks:
            continue
        auc = cv.auc_from_ranks(pd.concat(ranks, ignore_index=True), region)["AUC"]
        table[str(cand)] = auc
        if auc > best_auc:
            best, best_auc = cand, auc
    return best, table


def ale(predict, X: pd.DataFrame, col: str, nq: int = 12) -> dict:
    """Accumulated local effect of one column on the model's margin (12 quantile bins; sample <= 20k rows)."""
    from scipy.stats import spearmanr
    x = X[col].to_numpy(float); q = np.unique(np.nanquantile(x, np.linspace(0.02, 0.98, nq + 1)))
    if len(q) < 3:
        return dict(grid=[], ale=[], spearman=float("nan"))
    Xs = X.sample(min(len(X), 20000), random_state=cv.S_MODEL)
    eff = []
    for lo, hi in zip(q[:-1], q[1:]):
        a = Xs.copy(); b = Xs.copy(); a[col] = lo; b[col] = hi
        eff.append(float(np.mean(predict(b) - predict(a))))
    cum = np.cumsum(eff)
    return dict(grid=[float(v) for v in q[1:]], ale=[float(v) for v in cum], spearman=float(spearmanr(q[1:], cum).correlation))


def grouped_permutation(predict, te: pd.DataFrame, X: pd.DataFrame, groups: dict, fires_region, seed: int) -> dict:
    base = cv.auc_from_ranks(cv.interval_ranks(te, predict(X)), fires_region)["AUC"]
    rng = np.random.default_rng(seed); out = {}
    for g, cols in groups.items():
        cols = [c for c in cols if c in X]
        if not cols:
            continue
        Xp = X.copy(); perm = rng.permutation(len(Xp))
        Xp[cols] = X[cols].to_numpy()[perm]
        out[g] = base - cv.auc_from_ranks(cv.interval_ranks(te, predict(Xp)), fires_region)["AUC"]
    out["_base_auc"] = base
    return out


# ── rungs ────────────────────────────────────────────────────────────────────────────────────────
class Logistic:
    """Within-interval (conditional) L2 logistic: the interval effects cancel in a softmax over the interval's
    sampled cells, so the model has no intercept and ranks cells within an interval, which is what section 3 scores.
    Subsampled negatives carry importance weights w (1 / sampling fraction) inside the denominator; every positive
    of an interval contributes its own term. An optional fixed offset (the hybrid's m8_log) enters the margin.
    Fitted by L-BFGS with analytic gradients; features standardised on the training rows.
    (The pooled marginal logistic attenuated a planted within-interval effect by a factor of about 3 on the
    section-8 control 8 data, 2026-10-29; the conditional form is the pre-registered "within-interval" GLM.)"""

    def __init__(self, C=1.0):
        self.C = C; self.coef_ = None; self.intercept_ = 0.0; self.mean_ = None; self.std_ = None

    def fit(self, X, y, w, offset=None, groups=None):
        from scipy.optimize import minimize
        Xa = np.asarray(X, float); self.mean_ = Xa.mean(0); self.std_ = Xa.std(0) + 1e-9
        Z = (Xa - self.mean_) / self.std_; y = np.asarray(y, float); w = np.asarray(w, float)
        off = np.zeros(len(y)) if offset is None else np.asarray(offset, float)
        g = np.zeros(len(y), int) if groups is None else pd.factorize(pd.Series(groups).astype(str))[0]
        ng = g.max() + 1; npos = max(y.sum(), 1.0); lam = 1.0 / (self.C * npos)
        pos_g = np.bincount(g, weights=y, minlength=ng)  # positives per interval
        def f(v):
            eta = off + Z @ v
            emax = np.zeros(ng); np.maximum.at(emax, g, eta)
            ew = w * np.exp(eta - emax[g])
            den = np.bincount(g, weights=ew, minlength=ng)
            logden = np.log(den) + emax
            ll = (y * eta).sum() - (pos_g * logden).sum()
            # gradient: sum_pos z - sum_g pos_g * (sum_c ew_c z_c / den_g)
            r = (ew / den[g]) * pos_g[g]
            grad = (y[:, None] * Z).sum(0) - (r[:, None] * Z).sum(0)
            return -(ll / npos) + lam * (v @ v), -(grad / npos) + 2 * lam * v
        r = minimize(f, np.zeros(Z.shape[1]), jac=True, method="L-BFGS-B")
        self.coef_ = r.x; self.converged_ = bool(r.success)
        return self

    def margin(self, X, offset=None):
        Z = (np.asarray(X, float) - self.mean_) / self.std_
        return Z @ self.coef_ + (0 if offset is None else np.asarray(offset, float))


def spline_design(X: pd.DataFrame, fitted=None, linear_cols=()):
    from sklearn.preprocessing import SplineTransformer
    cols = [c for c in X.columns if not c.endswith("_miss") and not c.startswith("fuel_g") and c not in linear_cols and X[c].nunique() > 6]
    if fitted is None:
        st = SplineTransformer(n_knots=6, degree=3, knots="quantile", include_bias=False).fit(X[cols])
        fitted = (st, cols)
    st, cols = fitted
    S = pd.DataFrame(st.transform(X[cols]), index=X.index, columns=[f"{c}__s{i}" for c in cols for i in range(st.n_features_out_ // len(cols))])
    rest = X.drop(columns=cols)
    return pd.concat([S, rest], axis=1), fitted


def fit_rung1(tr, C, med=None, offset=None, features=R1_FEATURES):
    X, med = design(tr, features, med)
    m = Logistic(C).fit(X, tr.y, tr.w_sample, offset, groups=tr.fire_key.astype(str) + "/" + tr.epoch.astype(str))
    return dict(model=m, med=med, cols=list(X.columns), features=features)


def score_rung1(fm, df, offset=None):
    X, _ = design(df, fm["features"], fm["med"])
    return fm["model"].margin(X[fm["cols"]], offset)


def fit_rung2(tr, C, med=None, linear_cols=(), offset=None):
    X, med = design(tr, R1_FEATURES, med)
    if offset is not None:  # pre-declared interaction for the additive hybrid base
        X["dact_x_fuel1"] = X["d_act_km"] * X["fuel_g1"]; X["dact_x_fuel2"] = X["d_act_km"] * X["fuel_g2"]
    S, fitted = spline_design(X, None, linear_cols)
    m = Logistic(C).fit(S, tr.y, tr.w_sample, offset, groups=tr.fire_key.astype(str) + "/" + tr.epoch.astype(str))
    return dict(model=m, med=med, spl=fitted, cols=list(S.columns), linear=tuple(linear_cols), hybrid=offset is not None)


def score_rung2(fm, df, offset=None):
    X, _ = design(df, R1_FEATURES, fm["med"])
    if fm.get("hybrid"):
        X["dact_x_fuel1"] = X["d_act_km"] * X["fuel_g1"]; X["dact_x_fuel2"] = X["d_act_km"] * X["fuel_g2"]
    S, _ = spline_design(X, fm["spl"], fm["linear"])
    return fm["model"].margin(S[fm["cols"]], offset)


def fit_rung3(tr, cand, features=R3_FEATURES, rounds=None, inner=None, offset=None, interactions=None, seed=0):
    import lightgbm as lgb
    mdl, _ = cand if isinstance(cand, tuple) else (cand, None)
    params = dict(LGB_BASE, min_data_in_leaf=int(mdl), seed=cv.S_MODEL + seed,
                  monotone_constraints=[MONOTONE.get(f, 0) for f in features])
    if interactions is not None:
        params["interaction_constraints"] = interactions
    ds = lgb.Dataset(tr[features], tr.y.to_numpy(), weight=tr.w_sample.to_numpy(), init_score=offset, free_raw_data=False)
    if inner is not None:  # (validation df, validation offset) -> early stopping
        va, va_off = inner
        dv = lgb.Dataset(va[features], va.y.to_numpy(), weight=va.w_sample.to_numpy(), init_score=va_off, reference=ds, free_raw_data=False)
        m = lgb.train(params, ds, num_boost_round=2000, valid_sets=[dv], callbacks=[lgb.early_stopping(50, verbose=False)])
        return dict(model=m, features=features, best_iter=int(m.best_iteration or m.current_iteration()))
    m = lgb.train(params, ds, num_boost_round=int(rounds))
    return dict(model=m, features=features, best_iter=int(rounds))


def score_rung3(fm, df, offset=None):
    s = fm["model"].predict(df[fm["features"]], raw_score=True, num_threads=4, num_iteration=fm.get("best_iter"))
    return s + (0 if offset is None else np.asarray(offset))


# ── main ─────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--no-terrain", action="store_true"); ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    log = lambda *x: print(f"[{time.time() - t0:6.0f}s]", *x, flush=True)  # noqa: E731
    fires = cv.table("fires"); folds = cv.outer_folds(fires); region = fires.set_index("fire_key").region
    fit = cv.table("cells_fit"); ev = cv.table("cells_eval")
    terr = cv.suffstats_path().parent / "terrain.parquet"
    if not a.no_terrain and terr.exists():
        T = pd.read_parquet(terr).set_index("row_id")[cv.TERRAIN]
        fit = fit.join(T, on="row_id"); ev = ev.join(T, on="row_id"); terrain_used = True
    else:
        for c in cv.TERRAIN:
            fit[c] = np.nan; ev[c] = np.nan
        terrain_used = False
    ss = pd.read_parquet(cv.suffstats_path())
    pos = fit[fit.y == 1]
    r1f = [f for f in R1_FEATURES if terrain_used or f not in cv.TERRAIN]
    r3f = [f for f in R3_FEATURES if terrain_used or f not in cv.TERRAIN]
    assert len(r1f) <= 15 and len(r3f) <= 25, (len(r1f), len(r3f))
    cv.assert_allowed(r1f + r3f)
    if a.quick:
        keep = fires.fire_key.isin(fires.sample(60, random_state=1).fire_key) | fires.n_jumps.gt(0)
        ks = set(fires.fire_key[keep]); fit = fit[fit.fire_key.isin(ks)]; ev = ev[ev.fire_key.isin(ks)]
    res = dict(started_utc=pd.Timestamp.now("UTC").isoformat(), terrain_used=terrain_used, features=dict(rung1=r1f, rung3=r3f),
               folds={k: int(v) for k, v in folds.items()}, lightgbm=__import__("lightgbm").__version__, choices={}, betas={}, ale={}, importance={})
    MODELS = ["b1", "m8", "dist", "dist_sec", "rung1", "rung2", "rung3"]
    oof = ev[["row_id", "fire_key", "epoch", "y", "region", "year", "gap_h"]].copy()
    for m in MODELS + ["rung4"]:
        oof[m] = np.nan
    rung2_rule = {}
    for k in range(cv.N_OUTER):
        trm = fit.fire_key.map(folds) != k; tem = (ev.fire_key.map(folds) == k).to_numpy()
        tr, te = fit[trm], ev[tem]
        if te.y.sum() == 0:
            continue
        beta = cv.refit_beta(ss, pos[trm[pos.index]], fires.fire_key[fires.fire_key.map(folds) != k]); res["betas"][k] = beta
        tr = tr.assign(m8_log=cv.m8_log(tr, beta)); te = te.assign(m8_log=cv.m8_log(te, beta))
        log(f"fold {k}: train fires {tr.fire_key.nunique()} pos {int(tr.y.sum())}; test fires {te.fire_key.nunique()} pos {int(te.y.sum())}; beta {beta}")
        oof.loc[tem, "b1"] = te.b1.to_numpy(); oof.loc[tem, "m8"] = te.m8_log.to_numpy()
        fd = fit_rung1(tr, 1.0, features=["d_act_km"]); oof.loc[tem, "dist"] = score_rung1(fd, te)
        fd = fit_rung1(tr, 1.0, features=["d_act_km", "sec_act"]); oof.loc[tem, "dist_sec"] = score_rung1(fd, te)
        # rung 1
        C1, tab = inner_pick(tr, fires, k, C_GRID, lambda d, c: fit_rung1(d, c, features=r1f), lambda m, d: score_rung1(m, d))
        f1 = fit_rung1(tr, C1, features=r1f); oof.loc[tem, "rung1"] = score_rung1(f1, te); res["choices"][f"rung1_C_fold{k}"] = dict(chosen=C1, inner_auc=tab)
        X1, _ = design(tr, r1f, f1["med"])
        res["ale"].setdefault("rung1", {})[k] = {c: float(f1["model"].coef_[i]) for i, c in enumerate(f1["cols"])}  # standardised conditional-logit coefficients
        # rung 2 (+ monotone rule)
        C2, tab = inner_pick(tr, fires, k, C_GRID, lambda d, c: fit_rung2(d, c), lambda m, d: score_rung2(m, d))
        f2 = fit_rung2(tr, C2); lin = []
        X2, _ = design(tr, R1_FEATURES, f2["med"])
        for col, sgn in MONOTONE.items():
            if col in X2 and X2[col].nunique() > 6:
                sp = ale(lambda X: score_rung2(f2, X.assign(fuel_group=0)) if False else f2["model"].margin(spline_design(X, f2["spl"], f2["linear"])[0][f2["cols"]]), X2, col)["spearman"]
                if np.isfinite(sp) and np.sign(sp) != sgn:
                    lin.append(col)
        if lin:
            f2 = fit_rung2(tr, C2, linear_cols=tuple(lin))
        rung2_rule[k] = lin
        oof.loc[tem, "rung2"] = score_rung2(f2, te); res["choices"][f"rung2_C_fold{k}"] = dict(chosen=C2, inner_auc=tab, linearised=lin)
        res["ale"].setdefault("rung2", {})[k] = {c: ale(lambda X: f2["model"].margin(spline_design(X, f2["spl"], f2["linear"])[0][f2["cols"]]), X2, c)["spearman"] for c in ["d_act_km", "bear_off_deg", "u10_ms", "m_ff_pct", "curing_pct", "hazshare", "I_head_kwm", "slope_pct"] if c in X2}
        # rung 3: inner early stopping + min_data_in_leaf
        inner = cv.inner_folds(fires, tr.fire_key.unique(), k); pick = {}
        for mdl in (200, 500):
            its, ranks = [], []
            for j in range(cv.N_INNER):
                itr = tr[tr.fire_key.map(inner) != j]; iva = tr[tr.fire_key.map(inner) == j]
                if iva.y.sum() == 0:
                    continue
                m = fit_rung3(itr, mdl, r3f, inner=(iva, None), seed=k)
                its.append(m["best_iter"]); ranks.append(cv.interval_ranks(iva, score_rung3(m, iva)))
            pick[mdl] = dict(rounds=int(np.median(its)), inner_auc=cv.auc_from_ranks(pd.concat(ranks), region)["AUC"])
        mdl3 = max(pick, key=lambda m_: pick[m_]["inner_auc"]); rounds3 = pick[mdl3]["rounds"]
        f3 = fit_rung3(tr, mdl3, r3f, rounds=rounds3, seed=k); oof.loc[tem, "rung3"] = score_rung3(f3, te)
        res["choices"][f"rung3_fold{k}"] = dict(min_data_in_leaf=mdl3, rounds=rounds3, inner=pick)
        res["ale"].setdefault("rung3", {})[k] = {c: ale(lambda X: f3["model"].predict(X, raw_score=True, num_threads=4), tr[r3f], c)["spearman"] for c in ["d_act_km", "bear_off_deg", "u10_ms", "m_ff_pct", "curing_pct", "hazshare", "I_head_kwm", "frp_prev_mw", "slope_pct", "elev_m"] if c in r3f}
        groups = dict(geometry=cv.GEOMETRY, weather=cv.WEATHER, fire_behaviour=cv.FIRE_BEHAVIOUR, receptor=cv.RECEPTOR, terrain=cv.TERRAIN)
        res["importance"].setdefault("rung3", {})[k] = grouped_permutation(lambda X: f3["model"].predict(X, raw_score=True, num_threads=4), te, te[r3f], groups, region, cv.S_MODEL + k)
        res.setdefault("fold_models", {})[k] = dict(f3_rounds=rounds3, f3_mdl=mdl3, C1=C1, C2=C2)
        log(f"fold {k} rungs 0-3 done")
    # ── pooled statistics and stop rule ───────────────────────────────────────────────────────
    valid = oof.m8.notna()
    ranks = {m: cv.interval_ranks(oof[valid], oof.loc[valid, m].to_numpy()) for m in MODELS}
    res["auc"] = {m: cv.auc_from_ranks(r, region) for m, r in ranks.items()}
    def diff(a, b):
        pf = cv.per_fire_a(ranks[a], ranks[b], region); d = cv.s3(pf)
        d["per_fold"] = {int(k): float((pf[pf.fire_key.map(folds) == k].w * pf[pf.fire_key.map(folds) == k].a).sum() / max(pf[pf.fire_key.map(folds) == k].w.sum(), 1e-12)) for k in range(cv.N_OUTER)}
        return d
    res["s3"] = {f"{a}-{b}": diff(a, b) for a, b in [("m8", "b1"), ("dist", "b1"), ("dist_sec", "dist"), ("rung1", "dist_sec"), ("rung1", "m8"), ("rung2", "rung1"), ("rung3", "rung2"), ("rung3", "rung1"), ("rung2", "m8"), ("rung3", "m8")]}
    def signs_hold(r, n=4):
        if r not in res["ale"]:
            return True
        tab = pd.DataFrame(res["ale"][r]).T
        return bool((np.sign(tab).apply(lambda s: s.value_counts().max(), axis=0) >= n).all())
    def climb(step):
        d = res["s3"][step]
        return bool(d["S3"] >= 0.02 and d["ci95"][0] > -0.01 and signs_hold(step.split("-")[0]))
    stop = dict(rung1_to_2=climb("rung2-rung1"), rung2_to_3=climb("rung3-rung2"))
    reached = "rung1"
    if stop["rung1_to_2"]:
        reached = "rung2"
        if stop["rung2_to_3"]:
            reached = "rung3"
    aucs = {r: res["auc"][r]["AUC"] for r in ("rung1", "rung2", "rung3")}
    best = max(aucs, key=aucs.get); within = [r for r in ("rung1", "rung2", "rung3") if aucs[best] - aucs[r] <= 0.01]
    carried = within[0]  # simplest within +0.01 of the best
    res["stop_rule"] = dict(steps=stop, reached=reached, aucs=aucs, carried=carried, rule="climb if S3 >= +0.02, CI lower > -0.01, signs in >= 4/5 folds; carry the simplest rung within +0.01 of the best")
    log("stop rule", res["stop_rule"])
    # ── rung 4 hybrid on the carried base ─────────────────────────────────────────────────────
    base = "rung3" if carried == "rung3" else "rung2"
    for k in range(cv.N_OUTER):
        trm = fit.fire_key.map(folds) != k; tem = (ev.fire_key.map(folds) == k).to_numpy()
        tr, te = fit[trm], ev[tem]
        if te.y.sum() == 0:
            continue
        beta = res["betas"][k]; tr = tr.assign(m8_log=cv.m8_log(tr, beta)); te = te.assign(m8_log=cv.m8_log(te, beta))
        off_tr, off_te = tr.m8_log.to_numpy(), te.m8_log.to_numpy()
        if base == "rung3":
            fm = res["fold_models"][k]; idx = {f: i for i, f in enumerate(r3f)}
            inter = [[idx["d_act_km"], idx["fuel_group"]]] + [[i] for f, i in idx.items() if f not in ("d_act_km", "fuel_group")]
            f4 = fit_rung3(tr, fm["f3_mdl"], r3f, rounds=fm["f3_rounds"], offset=off_tr, interactions=inter, seed=k)
            oof.loc[tem, "rung4"] = score_rung3(f4, te, offset=off_te)
            res["ale"].setdefault("rung4", {})[k] = {c: ale(lambda X: f4["model"].predict(X, raw_score=True, num_threads=4), tr[r3f], c)["spearman"] for c in ["d_act_km", "bear_off_deg", "u10_ms", "curing_pct", "hazshare", "I_head_kwm", "slope_pct"] if c in r3f}
        else:
            f4 = fit_rung2(tr, res["fold_models"][k]["C2"], linear_cols=tuple(rung2_rule.get(k, [])), offset=off_tr)
            oof.loc[tem, "rung4"] = score_rung2(f4, te, offset=off_te)
        log(f"fold {k} rung 4 ({base} + m8 offset) done")
    ranks["rung4"] = cv.interval_ranks(oof[valid], oof.loc[valid, "rung4"].to_numpy())
    res["auc"]["rung4"] = cv.auc_from_ranks(ranks["rung4"], region)
    res["s3"]["rung4-m8"] = diff("rung4", "m8"); res["s3"][f"rung4-{carried}"] = diff("rung4", carried); res["s3"][f"{carried}-m8"] = diff(carried, "m8")
    res["hybrid_base"] = base
    # ── transfer folds (RQ4, descriptive): M8 and the carried rung ───────────────────────────
    transfer = {}
    def fit_carried(tr, name, seed):
        if carried == "rung1":
            return ("r1", fit_rung1(tr, 1.0, features=r1f))
        if carried == "rung2":
            return ("r2", fit_rung2(tr, 1.0))
        return ("r3", fit_rung3(tr, 200, r3f, rounds=int(np.median([res["fold_models"][k]["f3_rounds"] for k in res["fold_models"]])), seed=seed))
    def score_carried(fm, te):
        kind, m = fm
        return {"r1": score_rung1, "r2": score_rung2, "r3": score_rung3}[kind](m, te)
    splits = {"CAN->US": (fires.region == 0, fires.region == 1), "US->CAN": (fires.region == 1, fires.region == 0)}
    for yr in (2023, 2024, 2025):
        splits[f"LOYO-{yr}"] = (fires.year != yr, fires.year == yr)
    for name, (trm_f, tem_f) in splits.items():
        trk, tek = set(fires.fire_key[trm_f]), set(fires.fire_key[tem_f])
        tr, te = fit[fit.fire_key.isin(trk)], ev[ev.fire_key.isin(tek)]
        if te.y.sum() < 5 or tr.y.sum() < 20:
            transfer[name] = dict(skipped=True, pos_train=int(tr.y.sum()), pos_test=int(te.y.sum())); continue
        beta = cv.refit_beta(ss, pos[pos.fire_key.isin(trk)], sorted(trk)); te = te.assign(m8_log=cv.m8_log(te, beta))
        fm = fit_carried(tr, name, 99)
        rk_m8 = cv.interval_ranks(te, te.m8_log.to_numpy()); rk_c = cv.interval_ranks(te, score_carried(fm, te)); rk_b1 = cv.interval_ranks(te, te.b1.to_numpy())
        transfer[name] = dict(beta=beta, pos_test=int(te.y.sum()), fires_test=int(te.fire_key.nunique()),
                              auc_m8=cv.auc_from_ranks(rk_m8, region)["AUC"], auc_b1=cv.auc_from_ranks(rk_b1, region)["AUC"],
                              auc_carried=cv.auc_from_ranks(rk_c, region)["AUC"], s3_carried_minus_m8=cv.s3(cv.per_fire_a(rk_c, rk_m8, region)))
        log("transfer", name, {k_: v for k_, v in transfer[name].items() if k_.startswith("auc")})
    res["transfer"] = transfer
    res["rung2_monotone_rule"] = rung2_rule
    res["finished_utc"] = pd.Timestamp.now("UTC").isoformat(); res["wall_seconds"] = time.time() - t0
    oof.to_parquet(OUT / "oof_scores.parquet", index=False)
    (OUT / "ladder.json").write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log("done", {m: round(v["AUC"], 3) for m, v in res["auc"].items()}, {k_: round(v["S3"], 3) for k_, v in res["s3"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
