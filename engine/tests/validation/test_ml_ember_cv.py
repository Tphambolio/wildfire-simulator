"""Unit tests for scripts/ml_ember/cv.py and the ladder's conditional logistic (no data pool needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT, ROOT / "engine" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

cv = pytest.importorskip("scripts.ml_ember.cv")
ladder = pytest.importorskip("scripts.ml_ember.ladder")


def _fires(n=40, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(dict(fire_key=[f"F{i}" for i in range(n)], region=rng.integers(0, 2, n), year=rng.choice([2021, 2022, 2023], n), n_jumps=rng.poisson(1.0, n)))


def test_outer_folds_deterministic_and_whole_fire():
    f = _fires()
    a, b = cv.outer_folds(f), cv.outer_folds(f)
    assert a == b and set(a.values()) <= set(range(cv.N_OUTER)) and len(a) == len(f)
    # balanced within a stratum: counts differ by at most one
    for (r, y), g in f.groupby(["region", "year"]):
        c = pd.Series([a[k] for k in g.fire_key]).value_counts()
        assert c.max() - c.min() <= 1


def test_inner_folds_only_training_fires():
    f = _fires(); tr = f.fire_key[:25]
    inner = cv.inner_folds(f, tr, outer=0)
    assert set(inner) == set(tr) and set(inner.values()) <= set(range(cv.N_INNER))


def test_s3_against_v8_definition():
    pf = pd.DataFrame(dict(fire_key=["a", "b", "c"], a=[0.2, -0.1, 0.3], K=[1, 3, 2], region=[0, 0, 1]))
    pf["w"] = cv.V.weights(pf.K.to_numpy())
    r = cv.s3(pf, ci=False, test=False)
    w = pf.K / (1 + (pf.K - 1) * 0.30)
    assert abs(r["S3"] - (w * pf.a).sum() / w.sum()) < 1e-12


def test_interval_ranks_and_auc():
    df = pd.DataFrame(dict(fire_key=["f"] * 10, epoch=[1] * 10, y=[0] * 9 + [1]))
    s = np.arange(10, dtype=float)
    r = cv.interval_ranks(df, s)
    assert len(r) == 1 and abs(r["rank"].iloc[0] - 0.95) < 1e-12  # top score, ties-half convention
    fires_region = pd.Series({"f": 0})
    assert abs(cv.auc_from_ranks(r, fires_region)["AUC"] - 0.95) < 1e-12


def test_conditional_logistic_recovers_within_interval_effect():
    rng = np.random.default_rng(1)
    rows = []
    for i in range(300):  # intervals with one positive each; P ∝ exp(-x/45)
        x = rng.uniform(0, 180, 60)
        p = np.exp(-x / 45); p /= p.sum(); pos = rng.choice(60, p=p)
        y = np.zeros(60); y[pos] = 1
        rows.append(pd.DataFrame(dict(g=i, x=x, z=rng.normal(size=60), y=y, w=1.0)))
    d = pd.concat(rows, ignore_index=True)
    m = ladder.Logistic(C=10.0).fit(d[["x", "z"]], d.y, d.w, groups=d.g)
    expected = -d.x.std() / 45
    assert m.converged_ and abs(m.coef_[0] - expected) < 0.25 * abs(expected) and abs(m.coef_[1]) < 0.1


def test_forbidden_columns_rejected():
    with pytest.raises(ValueError):
        cv.assert_allowed(["d_act_km", "bi"])
    cv.assert_allowed(cv.PRODUCT_FEATURES)
