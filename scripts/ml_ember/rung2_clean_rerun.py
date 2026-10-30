#!/usr/bin/env python3
"""rung2_clean_rerun.py -- re-run of rung 2 (additive) and rung 4 (offset hybrid) for the CLEAN arm after the
2026-10-30 correction of ``ladder.fit_rung2`` (development only; never deciding).

Why
    Until 2026-10-30 ``fit_rung2`` read the module-level 14-feature list and ignored the arm, so the "clean arm" rung 2
    and rung 4 of the two ladder runs of 2026-10-29 (``ladder/clean/ladder.json``) carried ``hazshare``, which the clean
    arm drops (PREREG_ML_EMBER_DEV addendum A3, control 6). The two arms' rung-2 numbers were therefore identical (0.913).
    The carried model of stage 2 must be the clean arm as declared, so rung 2 and rung 4 are re-fitted here with the
    13-feature clean list under exactly the section-6 folds, seeds, inner C grid, monotone rule and per-fold M8 refit of
    ``ladder.py``; everything else of the ladder is unchanged and not re-run.

What is written (``ladder/clean/rung2_rerun.json``, ``ladder/clean/oof_rung2_rerun.parquet``)
    pooled out-of-fold AUC of M8, distance-only, rung 2 and rung 4 (clean), their S3 differences with fire-bootstrap
    95 % intervals and sign-flip p (development seeds), per-fold S3, the C chosen and the linearised columns per fold,
    the ALE signs per fold, and the difference between the corrected rung 2 and the stored (hazshare-carrying) rung 2.

Usage
    CAPJOB_MEM=5G capjob python -I scripts/ml_ember/rung2_clean_rerun.py
"""
from __future__ import annotations

import gc
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
from scripts.ml_ember import ladder as L  # noqa: E402
from scripts.ml_ember import paths as P  # noqa: E402

OUT = P.DATA_FOLDER / "ladder" / "clean"


def main() -> int:
    t0 = time.time()
    log = lambda *x: print(f"[{time.time() - t0:6.0f}s]", *x, flush=True)  # noqa: E731
    fires = cv.table("fires"); folds = cv.outer_folds(fires); region = fires.set_index("fire_key").region
    NEED = ["row_id", "fire_key", "epoch", "y", "region", "year", "gap_h", "b1", "u10_ms", "m_ff_pct", "fuel_group",
            "A_s0", "A_s1", "A_s2", "A_l0", "A_l1", "A_l2"] + [f for f in L.R1_FEATURES if f not in cv.TERRAIN]
    NEED = list(dict.fromkeys(NEED))
    fit = cv.table("cells_fit", NEED + ["w_sample"]); ev = cv.table("cells_eval", NEED)
    T = pd.read_parquet(cv.suffstats_path().parent / "terrain.parquet").set_index("row_id")[cv.TERRAIN]
    fit = fit.join(T, on="row_id"); ev = ev.join(T, on="row_id"); del T
    ss = pd.read_parquet(cv.suffstats_path()); pos = fit[fit.y == 1]
    r1f = [f for f in L.R1_FEATURES if f not in L.ARM_DROP["clean"]]
    assert "hazshare" not in r1f and len(r1f) == 13, r1f
    cv.assert_allowed(r1f)
    res = dict(started_utc=pd.Timestamp.now("UTC").isoformat(), arm="clean", features_rung2=r1f, choices={}, betas={}, ale={}, linearised={})
    oof = ev[["row_id", "fire_key", "epoch", "y", "region"]].copy()
    for m in ("m8", "dist", "rung2", "rung4"):
        oof[m] = np.nan
    for k in range(cv.N_OUTER):
        trm = fit.fire_key.map(folds) != k; tem = (ev.fire_key.map(folds) == k).to_numpy()
        tr, te = fit[trm], ev[tem]
        if te.y.sum() == 0:
            continue
        beta = cv.refit_beta(ss, pos[trm[pos.index]], fires.fire_key[fires.fire_key.map(folds) != k]); res["betas"][k] = beta
        tr = tr.assign(m8_log=cv.m8_log(tr, beta)); te = te.assign(m8_log=cv.m8_log(te, beta))
        oof.loc[tem, "m8"] = te.m8_log.to_numpy()
        fd = L.fit_rung1(tr, 1.0, features=["d_act_km"]); oof.loc[tem, "dist"] = L.score_rung1(fd, te)
        C2, tab = L.inner_pick(tr, fires, k, L.C_GRID, lambda d, c: L.fit_rung2(d, c, features=r1f), lambda m, d: L.score_rung2(m, d))
        f2 = L.fit_rung2(tr, C2, features=r1f); lin = []
        X2, _ = L.design(tr, r1f, f2["med"])
        for col, sgn in L.MONOTONE.items():
            if col in X2 and X2[col].nunique() > 6:
                sp = L.ale(lambda X: f2["model"].margin(L.spline_design(X, f2["spl"], f2["linear"])[0][f2["cols"]]), X2, col)["spearman"]
                if np.isfinite(sp) and np.sign(sp) != sgn:
                    lin.append(col)
        if lin:
            f2 = L.fit_rung2(tr, C2, linear_cols=tuple(lin), features=r1f)
        res["linearised"][k] = lin; res["choices"][k] = dict(C2=C2, inner_auc=tab)
        oof.loc[tem, "rung2"] = L.score_rung2(f2, te)
        res["ale"][k] = {c: L.ale(lambda X: f2["model"].margin(L.spline_design(X, f2["spl"], f2["linear"])[0][f2["cols"]]), X2, c)["spearman"]
                         for c in ["d_act_km", "bear_off_deg", "u10_ms", "m_ff_pct", "curing_pct", "I_head_kwm", "slope_pct"] if c in X2}
        f4 = L.fit_rung2(tr, C2, linear_cols=tuple(lin), offset=tr.m8_log.to_numpy(), features=r1f)
        oof.loc[tem, "rung4"] = L.score_rung2(f4, te, offset=te.m8_log.to_numpy())
        del tr, te, X2, f2, f4; gc.collect()
        log(f"fold {k} done: C2 {C2}, linearised {lin}")
    valid = oof.m8.notna()
    ranks = {m: cv.interval_ranks(oof[valid], oof.loc[valid, m].to_numpy()) for m in ("m8", "dist", "rung2", "rung4")}
    res["auc"] = {m: cv.auc_from_ranks(r, region) for m, r in ranks.items()}

    def diff(a, b):
        pf = cv.per_fire_a(ranks[a], ranks[b], region); d = cv.s3(pf)
        d["per_fold"] = {int(k): float((pf[pf.fire_key.map(folds) == k].w * pf[pf.fire_key.map(folds) == k].a).sum() / max(pf[pf.fire_key.map(folds) == k].w.sum(), 1e-12)) for k in range(cv.N_OUTER)}
        return d
    res["s3"] = {f"{a}-{b}": diff(a, b) for a, b in [("rung2", "m8"), ("rung2", "dist"), ("rung4", "m8"), ("rung4", "rung2"), ("dist", "m8")]}
    # comparison with the stored (hazshare-carrying) rung 2 of the 2026-10-29 run
    old = pd.read_parquet(OUT / "oof_scores.parquet", columns=["row_id", "rung2", "rung4"]).set_index("row_id")
    o = oof[valid].join(old, on="row_id", rsuffix="_stored")
    ranks["rung2_stored"] = cv.interval_ranks(o, o.rung2_stored.to_numpy()); ranks["rung4_stored"] = cv.interval_ranks(o, o.rung4_stored.to_numpy())
    res["s3"]["rung2-rung2_stored"] = diff("rung2", "rung2_stored"); res["s3"]["rung4-rung4_stored"] = diff("rung4", "rung4_stored")
    res["auc"]["rung2_stored"] = cv.auc_from_ranks(ranks["rung2_stored"], region); res["auc"]["rung4_stored"] = cv.auc_from_ranks(ranks["rung4_stored"], region)
    res["finished_utc"] = pd.Timestamp.now("UTC").isoformat(); res["wall_seconds"] = time.time() - t0
    oof.to_parquet(OUT / "oof_rung2_rerun.parquet", index=False)
    (OUT / "rung2_rerun.json").write_text(json.dumps(res, indent=1, default=float))
    log("AUC", {m: round(v["AUC"], 4) for m, v in res["auc"].items()})
    log("S3", {k: (round(v["S3"], 4), [round(x, 4) for x in v["ci95"]]) for k, v in res["s3"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
