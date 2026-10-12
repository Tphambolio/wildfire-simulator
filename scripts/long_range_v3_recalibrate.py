#!/usr/bin/env python3
"""v3 structural fix candidates, evaluated by leave-one-fire-out CV on the training fires.

The v3 ensemble probability P is mapped to a calibrated probability by a logistic layer
fitted on the other training fires (illustrative — not validated):

- R1 "platt":   logit P' = a + b logit P                      (forecast recalibration)
- R2 "hybrid":  R1 + c log1p(buildings within 100 m) + d log1p(edge distance / 100 m)
                (exposure-weighted: housing density is the strongest pre-fire predictor of
                loss in Knapp et al. 2021; spacing in Zamanialaei et al. 2025; Syphard 2012)
- R0 "covariates only": R2 without P (control: does the physics add anything?)

Inputs: ``units_lr3_<fire>.npz`` from ``long_range_v3_validation.py score`` on the training
fires. Output: JSON with per-fold Brier, BSS vs the fold's observed base rate, AUC and the
coefficients fitted on all training fires (the values a held-out test would use).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

FIRES = ("eaton", "palisades", "camp")
MODELS = {"R0_covariates": ("dens", "edge"), "R1_platt": ("lp",), "R2_hybrid": ("lp", "dens", "edge")}


def features(d):
    sm = d["scored"]
    p = np.clip(d["P_V3"][sm], 0.01, 0.99)
    return {"lp": np.log(p / (1 - p)), "dens": np.log1p(d["density100"][sm].astype(float)),
            "edge": np.log1p(d["d_edge"][sm] / 100.0)}, d["obs"][sm].astype(int)


def auc(p, o):
    from scipy.stats import rankdata

    r = rankdata(p)
    n1, n0 = int(o.sum()), int((1 - o).sum())
    return float((r[o == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def fit_predict(train, test, cols, weight_fires=True):
    X = np.vstack([np.column_stack([f[c] for c in cols]) for f, _ in train])
    y = np.concatenate([o for _, o in train])
    # each training fire weighs the same (fires differ 2x in size)
    w = np.concatenate([np.full(len(o), 1.0 / len(o)) for _, o in train]) if weight_fires else None
    m = LogisticRegression(C=1e6, max_iter=1000).fit(X, y, sample_weight=w)
    Xt = np.column_stack([test[0][c] for c in cols])
    return m.predict_proba(Xt)[:, 1], m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = {f: features(dict(np.load(a.results / f"units_lr3_{f}.npz"))) for f in FIRES}
    out = {"folds": {}, "final": {}}
    for name, cols in MODELS.items():
        out["folds"][name] = {}
        for f in FIRES:
            train = [data[g] for g in FIRES if g != f]
            p, _ = fit_predict(train, data[f], cols)
            o = data[f][1]
            clim = o.mean()
            br = float(np.mean((p - o) ** 2))
            raw = 1 / (1 + np.exp(-data[f][0]["lp"]))
            out["folds"][name][f] = {"brier": br, "bss_vs_base_rate": 1 - br / float(np.mean((clim - o) ** 2)),
                                     "auc": auc(p, o), "mean_p": float(p.mean()), "base_rate": float(clim),
                                     "raw_v3_brier": float(np.mean((raw - o) ** 2)), "raw_v3_auc": auc(raw, o)}
        _, m = fit_predict([data[g] for g in FIRES], data[FIRES[0]], cols)
        out["final"][name] = {"columns": list(cols), "intercept": float(m.intercept_[0]),
                              "coef": [float(c) for c in m.coef_[0]]}
    a.out.write_text(json.dumps(out, indent=1))
    for name in MODELS:
        print(name, {f: (round(v["brier"], 4), round(v["bss_vs_base_rate"], 3), round(v["auc"], 3))
                     for f, v in out["folds"][name].items()})


if __name__ == "__main__":
    main()
