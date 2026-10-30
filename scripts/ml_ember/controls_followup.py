"""Follow-up diagnostics to the section-8 controls (run 2026-10-29 after controls.json), to tell implementation
artefacts from real leaks before anything is reported:
  - control 2: weather-only occurrence model (no gap_h) with the fire's own weather shifted by +-1, +-3, +-7 days,
    and a fire-mean-weather model (fire-level climate component);
  - controls 3 and 4: five seeded replicates each (null distribution against the +-0.03 band);
  - control 6: fuel-seam detector by feature subset (hazard only; code + hazard + curing; texture only; all) for
    Canada and the US, pooled and within-fire AUC.
Output: controls/followup.json. No ladder number is computed.
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

from scripts.ml_ember import controls as K  # noqa: E402
from scripts.ml_ember import cv  # noqa: E402


def main() -> int:
    t0 = time.time(); log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    fires = cv.table("fires"); folds = cv.outer_folds(fires)
    iv = cv.table("intervals"); iv["y_int"] = (iv.n_jumps > 0).astype(np.int8)
    out = {}
    # control 2 follow-up
    c2 = dict(weather_only_unshifted=K.interval_control(iv, K.INTERVAL_WEATHER, fires, folds))
    for h in (24, -24, 72, -72, 168, -168):
        sh = K.shifted_weather(iv, h)
        r = K.interval_control(sh, K.INTERVAL_WEATHER, fires, folds); r["n_intervals_with_shift"] = int(len(sh)); c2[f"shift_{h:+d}h"] = r
    fm = iv.copy()
    for c in K.INTERVAL_WEATHER:
        fm[c] = fm.groupby("fire_key")[c].transform("mean")
    c2["fire_mean_weather"] = K.interval_control(fm, K.INTERVAL_WEATHER, fires, folds)
    c2["gap_h_only"] = K.interval_control(iv, ["gap_h"], fires, folds)
    out["c2"] = c2; log("c2", {k: round(v["AUC"], 3) for k, v in c2.items()})
    # controls 3 and 4 replicates
    fit = cv.table("cells_fit"); ev = cv.table("cells_eval")
    reps3 = []
    for s in range(5):
        pair = K.derangement(sorted(fit.fire_key.unique()), np.random.default_rng(cv.S_CTRL + 300 + s))
        def swap(df, src, seed):
            r = np.random.default_rng(seed); o = df.copy(); by = {k: g.index.to_numpy() for k, g in src.groupby("fire_key", sort=False)}
            for k, idx in df.groupby("fire_key", sort=False).indices.items():
                pk = pair.get(k)
                if pk in by:
                    o.iloc[idx, o.columns.get_indexer(K.CONTROL_FEATURES)] = src.loc[r.choice(by[pk], len(idx)), K.CONTROL_FEATURES].to_numpy()
            return o
        r = K.cell_control("other_fire", swap(fit, fit, 1000 + s), swap(ev, ev, 2000 + s), K.CONTROL_FEATURES, fires, folds)
        reps3.append(dict(seed=s, AUC=r["AUC"], ci95=r["ci95"])); log("c3 rep", s, round(r["AUC"], 3))
    out["c3_replicates"] = dict(reps=reps3, mean=float(np.mean([x["AUC"] for x in reps3])), sd=float(np.std([x["AUC"] for x in reps3])))
    reps4 = []
    for s in range(5):
        r4 = np.random.default_rng(cv.S_CTRL + 400 + s); pair4 = K.derangement(sorted(iv.fire_key.unique()), r4)
        i4 = iv.copy(); outcomes = {k: g.y_int.to_numpy() for k, g in iv.groupby("fire_key", sort=False)}
        for k, idx in iv.groupby("fire_key", sort=False).indices.items():
            i4.iloc[idx, i4.columns.get_loc("y_int")] = r4.choice(outcomes[pair4[k]], len(idx), replace=True)
        r = K.interval_control(i4, K.INTERVAL_ALL, fires, folds); reps4.append(dict(seed=s, AUC=r["AUC"], ci95=r["ci95"], per_fold=r["per_fold"]))
    out["c4_replicates"] = dict(reps=reps4, mean=float(np.mean([x["AUC"] for x in reps4])), sd=float(np.std([x["AUC"] for x in reps4])))
    log("c4 reps", [round(x["AUC"], 3) for x in reps4])
    # control 6 by feature subset
    from sklearn.metrics import roc_auc_score
    seam = cv.table("seam_control").set_index("row_id").loc[ev.row_id.to_numpy()].reset_index()
    seam["fire_key"] = ev.fire_key.to_numpy(); seam["region"] = ev.region.to_numpy()
    subsets = {"hazard_only": ["hazard"], "code_hazard_curing": ["fuel_code", "hazard", "curing_pct"], "texture_only": ["code_entropy_1km", "patch_size", "edge_density_1km"],
               "all": ["fuel_code", "hazard", "curing_pct", "code_entropy_1km", "patch_size", "edge_density_1km"], "fuel_code_only": ["fuel_code"]}
    c6 = {}
    for reg, name in ((0, "CAN"), (1, "US")):
        d = seam[seam.region == reg].reset_index(drop=True)
        for sname, cols in subsets.items():
            sc = np.full(len(d), np.nan)
            for k in range(cv.N_OUTER):
                trm = (d.fire_key.map(folds) != k).to_numpy(); tem = ~trm
                tr = d[trm].groupby("fire_key", group_keys=False).apply(lambda g: g.sample(min(len(g), 4000), random_state=cv.S_CTRL + k))
                if tr.in_final.nunique() < 2 or d.loc[tem, "in_final"].nunique() < 2:
                    continue
                m = K.lgb_fit(tr[cols], tr.in_final.to_numpy(), None, k); sc[tem] = m.predict(d.loc[tem, cols], num_threads=4)
            ok = ~np.isnan(sc); dd = d[ok].assign(score=sc[ok])
            pf = [roc_auc_score(g.in_final, g.score) for _, g in dd.groupby("fire_key") if g.in_final.nunique() == 2]
            c6[f"{name}_{sname}"] = dict(AUC_pooled=float(roc_auc_score(dd.in_final, dd.score)), mean_within_fire_AUC=float(np.mean(pf)), median_within_fire_AUC=float(np.median(pf)),
                                         n_fires=len(pf), share_fires_above_0_6=float(np.mean(np.array(pf) > 0.6)))
            log("c6", name, sname, {k_: round(v, 3) for k_, v in c6[f"{name}_{sname}"].items() if isinstance(v, float)})
    out["c6_subsets"] = c6
    # per-fire within-fire AUC for CAN all-features vs fuel_restored_share (does the seam scale with re-typing?)
    d = seam[seam.region == 0].reset_index(drop=True); cols = subsets["all"]; sc = np.full(len(d), np.nan)
    for k in range(cv.N_OUTER):
        trm = (d.fire_key.map(folds) != k).to_numpy(); tem = ~trm
        tr = d[trm].groupby("fire_key", group_keys=False).apply(lambda g: g.sample(min(len(g), 4000), random_state=cv.S_CTRL + k))
        m = K.lgb_fit(tr[cols], tr.in_final.to_numpy(), None, k); sc[tem] = m.predict(d.loc[tem, cols], num_threads=4)
    dd = d.assign(score=sc); frs = fires.set_index("fire_key").fuel_restored_share
    rows = [dict(fire_key=k, auc=roc_auc_score(g.in_final, g.score), fuel_restored_share=float(frs.get(k, np.nan)), n=len(g)) for k, g in dd.groupby("fire_key") if g.in_final.nunique() == 2]
    pf = pd.DataFrame(rows); from scipy.stats import spearmanr
    out["c6_CAN_auc_vs_restored_share"] = dict(spearman=float(spearmanr(pf.auc, pf.fuel_restored_share, nan_policy="omit").correlation), table=pf.round(3).to_dict("records"))
    out["finished_utc"] = pd.Timestamp.now("UTC").isoformat(); out["wall_seconds"] = time.time() - t0
    (K.OUT / "followup.json").write_text(json.dumps(out, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
