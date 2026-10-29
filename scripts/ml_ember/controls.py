"""PREREG_ML_EMBER_DEV section 8 controls 1-6 and 8 on the training tables (control 7 lives in beta_w_check.py).

Every control is run on the five outer folds of section 6 (whole fires held out) and reported before any result
of section 5 exists. No ladder number is computed here: the controls use a fixed detector (LightGBM 4.6 with the
rung-3 constraints, 300 rounds, no tuning) or, at interval level, the same detector on interval rows. Pass rules
are the pre-registered ones: negative controls 1-5 give S3-weighted within-interval AUC (or interval AUC) within
0.50 +- 0.03; the fuel-seam control 6 gives pooled held-out AUC <= 0.55 for "cell inside the final perimeter";
positive control 8 recovers the planted sign/shape in >= 4 of 5 folds.

Implementation notes (disclosed):
- control 2 (weather shifted +-1 day) is an interval-level control: within an interval the station weather is a
  constant per zone, so it cannot rank cells; the occurrence model (jump / no jump per interval) is where weather
  acts. The shifted weather of interval i is the weather of the same fire's interval whose start is nearest to
  t_start +- 24 h (within 3 h), else the interval is dropped.
- control 3 pairs fires by a seeded derangement (S_CTRL) and gives every row the feature vector of a random row
  of the paired fire (with replacement), keeping its label.
- control 4 (fold-level permutation) is interval-level: every fire's interval outcomes are replaced by outcomes
  drawn from the paired fire; the detector sees interval and fire-constant features.
- control 5 fits the detector on (bi, bj) only; control 6 on the seam_control fuel + texture columns only,
  Canadian fires headline, US reported.
- control 8 (re-run 2026-10-29 after the first run): rungs 1-2 use the within-interval conditional logistic of
  ladder.py (the pooled marginal logistic attenuated the planted effect about 3-fold and the spline version lost it);
  the first run's values are kept in controls.json, the re-run in c8_rerun.json. Control 8 plants, in every jump interval, as many synthetic positives as observed with probability
  proportional to exp(-bear_off_deg / 45) and, at interval level, occurrence with logit a + 0.3 u10_ms at the
  observed rate; rungs 1-3 must recover a negative bear_off effect (coefficient / ALE slope) and a positive
  u10 effect in >= 4 of 5 folds.
Output: reports/data/ml-ember-cast-2026-10-29/controls/controls.json (+ per-fold tables).
"""
from __future__ import annotations

import json
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

OUT = P.DATA_FOLDER / "controls"
CONTROL_FEATURES = cv.GEOMETRY + cv.WEATHER + cv.FIRE_BEHAVIOUR + cv.RECEPTOR + ["b1"]
INTERVAL_WEATHER = ["u10_ms", "wd_deg", "m_ff_pct", "ffmc", "temp_c", "rh_pct"]
INTERVAL_ALL = INTERVAL_WEATHER + ["gap_h", "act_len_km", "act_share", "fire_area_km2", "fire_age_h", "I_head_max_kwm", "region", "year"]
LGB = dict(objective="binary", learning_rate=0.05, max_depth=4, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
           bagging_fraction=1.0, lambda_l2=1.0, verbosity=-1, num_threads=4, seed=cv.S_MODEL)
ROUNDS = 300
TOL = 0.03


def lgb_fit(X: pd.DataFrame, y: np.ndarray, w: np.ndarray | None, fold: int):
    import lightgbm as lgb
    params = dict(LGB, seed=cv.S_MODEL + fold)
    params["min_data_in_leaf"] = min(200, max(20, int(0.02 * len(X))))
    return lgb.train(params, lgb.Dataset(X, y, weight=w, free_raw_data=False), num_boost_round=ROUNDS)


def cell_control(name, fit_df, eval_df, features, fires, folds, y_fit="y", y_eval="y", feat_eval=None):
    """Detector per outer fold on fit_df (train fires), ranks on eval_df (test fires); S3-weighted AUC."""
    cv.assert_allowed([f for f in features if f not in ("bi", "bj")] if name != "box_leak" else [])
    ranks, per_fold = [], {}
    region = fires.set_index("fire_key").region
    for k in range(cv.N_OUTER):
        tr = fit_df[fit_df.fire_key.map(folds) != k]
        te = eval_df[eval_df.fire_key.map(folds) == k]
        if te[y_eval].sum() == 0:
            continue
        m = lgb_fit(tr[features], tr[y_fit].to_numpy(), tr["w_sample"].to_numpy() if "w_sample" in tr else None, k)
        Xe = te[features] if feat_eval is None else feat_eval(te)[features]
        s = m.predict(Xe, num_threads=4)
        r = cv.interval_ranks(te.rename(columns={y_eval: "y"}) if y_eval != "y" else te, s)
        per_fold[k] = cv.auc_from_ranks(r, region)["AUC"]
        ranks.append(r)
    R = pd.concat(ranks, ignore_index=True)
    res = cv.auc_from_ranks(R, region)
    res.update(per_fold=per_fold, passed=bool(abs(res["AUC"] - 0.5) <= TOL), rule="AUC within 0.50 +- 0.03")
    return res


def interval_auc(df: pd.DataFrame, score: np.ndarray, fires: pd.DataFrame) -> dict:
    from sklearn.metrics import roc_auc_score
    y = df["y_int"].to_numpy(); s = np.asarray(score)
    out = dict(AUC=float(roc_auc_score(y, s)), n_intervals=int(len(df)), n_pos=int(y.sum()))
    keys = df.fire_key.to_numpy(); uk = np.unique(keys); reg = fires.set_index("fire_key").region
    rng = np.random.default_rng(cv.S_BOOT_DEV); est = []
    by_reg = {r: uk[reg.loc[uk].to_numpy() == r] for r in np.unique(reg.loc[uk])}
    idx_of = {k: np.where(keys == k)[0] for k in uk}
    for _ in range(1000):
        sel = np.concatenate([idx_of[k] for r, ks in by_reg.items() for k in rng.choice(ks, len(ks))])
        if y[sel].min() == y[sel].max():
            continue
        est.append(roc_auc_score(y[sel], s[sel]))
    out["ci95"] = (float(np.percentile(est, 2.5)), float(np.percentile(est, 97.5)))
    for r in by_reg:
        sel = np.isin(keys, by_reg[r])
        if y[sel].min() != y[sel].max():
            out[f"AUC_region{int(r)}"] = float(roc_auc_score(y[sel], s[sel]))
    return out


def interval_control(df, features, fires, folds, label="y_int"):
    scores = np.full(len(df), np.nan); per_fold = {}
    for k in range(cv.N_OUTER):
        trm = (df.fire_key.map(folds) != k).to_numpy(); tem = ~trm
        if df.loc[tem, label].nunique() < 2 or df.loc[trm, label].nunique() < 2:
            continue
        m = lgb_fit(df.loc[trm, features], df.loc[trm, label].to_numpy(), None, k)
        scores[tem] = m.predict(df.loc[tem, features], num_threads=4)
        from sklearn.metrics import roc_auc_score
        per_fold[k] = float(roc_auc_score(df.loc[tem, label], scores[tem]))
    ok = ~np.isnan(scores)
    d = df[ok].copy(); d["y_int"] = d[label].to_numpy()
    res = interval_auc(d, scores[ok], fires)
    res.update(per_fold=per_fold, passed=bool(abs(res["AUC"] - 0.5) <= TOL), rule="AUC within 0.50 +- 0.03")
    return res


def derangement(keys, rng):
    keys = list(keys)
    while True:
        p = rng.permutation(len(keys))
        if all(p[i] != i for i in range(len(keys))):
            return {keys[i]: keys[p[i]] for i in range(len(keys))}


def shifted_weather(iv: pd.DataFrame, hours: float) -> pd.DataFrame:
    """Weather of the same fire's interval nearest to t_start + hours (within 3 h); rows without one dropped."""
    out = []
    for fk, g in iv.groupby("fire_key", sort=False):
        t = g.t_start.to_numpy().astype("datetime64[s]").astype(np.int64)
        target = t + int(hours * 3600)
        j = np.abs(t[None, :] - target[:, None]).argmin(1)
        ok = np.abs(t[j] - target) <= 3 * 3600
        h = g.iloc[np.where(ok)[0]].copy()
        src = g.iloc[j[ok]]
        for c in INTERVAL_WEATHER:
            h[c] = src[c].to_numpy()
        out.append(h)
    return pd.concat(out, ignore_index=True)


def ale_slope(model_predict, X: pd.DataFrame, col: str, grid=None) -> float:
    """Spearman correlation between the feature grid and its accumulated local effect (sign and monotonicity)."""
    from scipy.stats import spearmanr
    x = X[col].to_numpy(float); q = np.nanquantile(x, np.linspace(0.02, 0.98, 13)) if grid is None else grid
    Xs = X.sample(min(len(X), 20000), random_state=cv.S_MODEL)
    eff = []
    for lo, hi in zip(q[:-1], q[1:]):
        a = Xs.copy(); b = Xs.copy(); a[col] = lo; b[col] = hi
        eff.append(float(np.mean(model_predict(b) - model_predict(a))))
    ale = np.cumsum(eff)
    return float(spearmanr(q[1:], ale).correlation)


def planted_cells(ev: pd.DataFrame, rng) -> np.ndarray:
    """Synthetic cell labels: per jump interval, as many positives as observed, P ∝ exp(-bear_off_deg/45)."""
    y = np.zeros(len(ev), np.int8); yo = ev.y.to_numpy()
    for (fk, ep), idx in ev.groupby(["fire_key", "epoch"], sort=False).indices.items():
        n = int(yo[idx].sum())
        if n == 0:
            continue
        p = np.exp(-ev.bear_off_deg.to_numpy()[idx] / 45.0); p = p / p.sum()
        y[rng.choice(idx, n, replace=False, p=p)] = 1
    return y


def subsample_like(ev: pd.DataFrame, y: np.ndarray, rng) -> pd.DataFrame:
    """Section-3 style fitting subsample on synthetic labels: positives + 200 negatives per jump interval."""
    rows = []
    for (fk, ep), idx in ev.groupby(["fire_key", "epoch"], sort=False).indices.items():
        pos = idx[y[idx] == 1]; neg = idx[y[idx] == 0]
        if len(pos) == 0:
            continue
        take = rng.choice(neg, min(200, len(neg)), replace=False)
        d = ev.iloc[np.concatenate([pos, take])].copy(); d["y"] = np.r_[np.ones(len(pos)), np.zeros(len(take))].astype(np.int8)
        d["w_sample"] = np.r_[np.ones(len(pos)), np.full(len(take), len(neg) / len(take))]
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def control8(ev, fires, folds) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import SplineTransformer, StandardScaler
    rng = np.random.default_rng(cv.S_CTRL + 8)
    ys = planted_cells(ev, rng)
    ev2 = ev.copy(); ev2["y"] = ys
    fit = subsample_like(ev2, ys, rng)
    feats = ["bear_off_deg", "d_act_km", "sec_act", "hazshare", "u10_ms"]
    res = {"rung1_sign": {}, "rung2_ale": {}, "rung3_ale": {}, "aucs": {}}
    region = fires.set_index("fire_key").region
    for k in range(cv.N_OUTER):
        tr = fit[fit.fire_key.map(folds) != k]; te = ev2[ev2.fire_key.map(folds) == k]
        if te.y.sum() == 0:
            continue
        Xtr = tr[feats].fillna(0.0); w = tr.w_sample.to_numpy()
        from scripts.ml_ember.ladder import Logistic  # within-interval conditional logistic (the pre-registered GLM form)
        grp = tr.fire_key.astype(str) + "/" + tr.epoch.astype(str)
        r1 = Logistic(1.0).fit(Xtr, tr.y, w, groups=grp)
        res["rung1_sign"][k] = float(r1.coef_[0])  # bear_off_deg coefficient (standardised)
        st = SplineTransformer(n_knots=6, degree=3, knots="quantile", include_bias=False).fit(Xtr)
        r2 = Logistic(1.0).fit(st.transform(Xtr), tr.y, w, groups=grp)
        res["rung2_ale"][k] = ale_slope(lambda X: r2.margin(st.transform(X)), Xtr, "bear_off_deg")
        m3 = lgb_fit(Xtr, tr.y.to_numpy(), w, k)
        res["rung3_ale"][k] = ale_slope(lambda X: m3.predict(X, num_threads=4), Xtr, "bear_off_deg")
        s = m3.predict(te[feats].fillna(0.0), num_threads=4)
        res["aucs"][k] = cv.auc_from_ranks(cv.interval_ranks(te, s), region)["AUC"]
    neg = lambda d: sum(v < 0 for v in d.values())  # noqa: E731
    res["folds_with_negative_bear_off"] = dict(rung1=neg(res["rung1_sign"]), rung2=neg(res["rung2_ale"]), rung3=neg(res["rung3_ale"]))
    res["passed_cell"] = all(v >= 4 for v in res["folds_with_negative_bear_off"].values())
    return res


def control8_interval(iv, fires, folds) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    rng = np.random.default_rng(cv.S_CTRL + 80)
    base = np.log(iv.y_int.mean() / (1 - iv.y_int.mean()))
    u = iv.u10_ms.to_numpy(float)
    logit = base + 0.3 * (u - u.mean())
    ys = (rng.random(len(iv)) < 1 / (1 + np.exp(-logit))).astype(np.int8)
    d = iv.copy(); d["y_syn"] = ys
    feats = ["u10_ms", "m_ff_pct", "gap_h", "fire_area_km2"]
    signs = {}
    for k in range(cv.N_OUTER):
        tr = d[d.fire_key.map(folds) != k]
        sc = StandardScaler().fit(tr[feats])
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(tr[feats]), tr.y_syn)
        signs[k] = float(lr.coef_[0][0])
    return dict(u10_coef_by_fold=signs, folds_positive=sum(v > 0 for v in signs.values()), planted_rate=float(ys.mean()),
                passed=sum(v > 0 for v in signs.values()) >= 4)


def main() -> int:
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    fires = cv.table("fires"); folds = cv.outer_folds(fires)
    iv = cv.table("intervals"); iv["y_int"] = (iv.n_jumps > 0).astype(np.int8)
    fit = cv.table("cells_fit"); ev = cv.table("cells_eval")
    cv.assert_allowed(CONTROL_FEATURES)
    results = dict(started_utc=pd.Timestamp.utcnow().isoformat(), folds={k: int(v) for k, v in folds.items()},
                   fold_sizes=pd.Series(folds).value_counts().sort_index().to_dict(), detector=dict(LGB, rounds=ROUNDS),
                   n_fit=int(len(fit)), n_eval=int(len(ev)), n_intervals=int(len(iv)))
    rng = np.random.default_rng(cv.S_CTRL)

    # 1 labels shuffled within each interval
    log("control 1")
    f1 = fit.copy(); f1["y"] = f1.groupby(["fire_key", "epoch"], sort=False)["y"].transform(lambda s: rng.permutation(s.to_numpy()))
    e1 = ev.copy(); e1["y"] = e1.groupby(["fire_key", "epoch"], sort=False)["y"].transform(lambda s: rng.permutation(s.to_numpy()))
    results["c1_shuffle_within_interval"] = cell_control("shuffle", f1, e1, CONTROL_FEATURES, fires, folds)
    log("c1", results["c1_shuffle_within_interval"]["AUC"])

    # 2 weather shifted +-1 day (interval level)
    log("control 2")
    ref = interval_control(iv, INTERVAL_WEATHER + ["gap_h"], fires, folds)
    results["c2_weather_shift"] = {"unshifted_reference_weather_only": {k: v for k, v in ref.items() if k != "passed"}}
    for h in (+24, -24):
        sh = shifted_weather(iv, h)
        r = interval_control(sh, INTERVAL_WEATHER + ["gap_h"], fires, folds)
        r["n_intervals_with_shift"] = int(len(sh))
        results["c2_weather_shift"][f"shift_{h:+d}h"] = r
    results["c2_weather_shift"]["passed"] = all(results["c2_weather_shift"][f"shift_{h:+d}h"]["passed"] for h in (24, -24))
    log("c2", {k: v.get("AUC") for k, v in results["c2_weather_shift"].items() if isinstance(v, dict)})

    # 3 features of another fire
    log("control 3")
    pair = derangement(sorted(fit.fire_key.unique()), np.random.default_rng(cv.S_CTRL + 3))
    def swap(df, src, seed):
        r = np.random.default_rng(seed); out = df.copy()
        by = {k: g.index.to_numpy() for k, g in src.groupby("fire_key", sort=False)}
        for k, idx in df.groupby("fire_key", sort=False).indices.items():
            pk = pair.get(k)
            if pk not in by:
                continue
            take = r.choice(by[pk], len(idx), replace=True)
            out.iloc[idx, out.columns.get_indexer(CONTROL_FEATURES)] = src.loc[take, CONTROL_FEATURES].to_numpy()
        return out
    f3 = swap(fit, fit, cv.S_CTRL + 31)
    e3 = swap(ev, ev, cv.S_CTRL + 32)
    results["c3_other_fire_features"] = cell_control("other_fire", f3, e3, CONTROL_FEATURES, fires, folds)
    results["c3_other_fire_features"]["pairs"] = pair
    log("c3", results["c3_other_fire_features"]["AUC"])

    # 4 fire-level label permutation (interval level, with fire constants)
    log("control 4")
    r4 = np.random.default_rng(cv.S_CTRL + 4); pair4 = derangement(sorted(iv.fire_key.unique()), r4)
    i4 = iv.copy(); outcomes = {k: g.y_int.to_numpy() for k, g in iv.groupby("fire_key", sort=False)}
    for k, idx in iv.groupby("fire_key", sort=False).indices.items():
        i4.iloc[idx, i4.columns.get_loc("y_int")] = r4.choice(outcomes[pair4[k]], len(idx), replace=True)
    results["c4_fire_label_permutation"] = interval_control(i4, INTERVAL_ALL, fires, folds)
    log("c4", results["c4_fire_label_permutation"]["AUC"])

    # 5 box-leak: (bi, bj) only
    log("control 5")
    xy = cv.table("coords_control", ["row_id", "bi", "bj"]).set_index("row_id")
    f5 = fit.join(xy, on="row_id"); e5 = ev.join(xy, on="row_id")
    results["c5_box_coordinates"] = cell_control("box_leak", f5, e5, ["bi", "bj"], fires, folds)
    results["c5_box_coordinates"]["note"] = ("Skill here means the run box geometry carries position information; bi/bj are forbidden in "
                                             "every rung (section 4) and asserted absent from the cell tables at build time.")
    log("c5", results["c5_box_coordinates"]["AUC"])
    del xy, f5, e5

    # 6 fuel-seam: fuel + texture -> inside final perimeter
    log("control 6")
    seam = cv.table("seam_control").set_index("row_id").loc[ev.row_id.to_numpy()].reset_index()
    seam["fire_key"] = ev.fire_key.to_numpy(); seam["region"] = ev.region.to_numpy()
    SEAM_F = ["fuel_code", "hazard", "curing_pct", "code_entropy_1km", "patch_size", "edge_density_1km"]
    from sklearn.metrics import roc_auc_score
    c6 = {}
    for reg, name in ((0, "CAN"), (1, "US")):
        d = seam[seam.region == reg]
        if d.empty:
            continue
        sc = np.full(len(d), np.nan); pf = {}
        for k in range(cv.N_OUTER):
            trm = (d.fire_key.map(folds) != k).to_numpy(); tem = ~trm
            tr = d[trm].groupby("fire_key", group_keys=False).apply(lambda g: g.sample(min(len(g), 4000), random_state=cv.S_CTRL + k))
            if tr.in_final.nunique() < 2 or d.loc[tem, "in_final"].nunique() < 2:
                continue
            m = lgb_fit(tr[SEAM_F], tr.in_final.to_numpy(), None, k)
            sc[tem] = m.predict(d.loc[tem, SEAM_F], num_threads=4)
            pf[k] = float(roc_auc_score(d.loc[tem, "in_final"], sc[tem]))
        ok = ~np.isnan(sc)
        per_fire = [roc_auc_score(g.in_final, sc[ok][g.index]) for _, g in d[ok].reset_index(drop=True).groupby("fire_key") if g.in_final.nunique() == 2]
        c6[name] = dict(AUC_pooled=float(roc_auc_score(d.in_final[ok], sc[ok])), per_fold=pf, mean_per_fire_AUC=float(np.mean(per_fire)),
                        n_fires_scored=len(per_fire), base_rate_in_final=float(d.in_final.mean()), n_cells=int(ok.sum()),
                        passed=bool(roc_auc_score(d.in_final[ok], sc[ok]) <= 0.55))
    c6["rule"] = "pooled held-out AUC <= 0.55 (Canadian fires decide; US reported)"
    c6["passed"] = c6.get("CAN", {}).get("passed", False)
    results["c6_fuel_seam"] = c6
    log("c6", {k: v.get("AUC_pooled") for k, v in c6.items() if isinstance(v, dict)})
    del seam

    # 8 planted ML signal
    log("control 8")
    results["c8_planted_signal"] = dict(cell_level=control8(ev, fires, folds), interval_level=control8_interval(iv, fires, folds))
    results["c8_planted_signal"]["passed"] = bool(results["c8_planted_signal"]["cell_level"]["passed_cell"] and results["c8_planted_signal"]["interval_level"]["passed"])
    log("c8", results["c8_planted_signal"]["cell_level"]["folds_with_negative_bear_off"], results["c8_planted_signal"]["interval_level"]["folds_positive"])

    results["summary"] = {k: v.get("passed") for k, v in results.items() if isinstance(v, dict) and "passed" in v}
    results["all_negative_controls_passed"] = all(results[k]["passed"] for k in ("c1_shuffle_within_interval", "c2_weather_shift", "c3_other_fire_features", "c4_fire_label_permutation", "c5_box_coordinates", "c6_fuel_seam"))
    results["finished_utc"] = pd.Timestamp.utcnow().isoformat(); results["wall_seconds"] = time.time() - t0
    (OUT / "controls.json").write_text(json.dumps(results, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log("done", results["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
