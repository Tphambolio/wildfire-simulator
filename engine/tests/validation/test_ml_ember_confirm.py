"""Unit tests for scripts/ml_ember/confirm.py (stage-2 confirmation, docs/PREREG_ML_EMBER.md).

Checks, without any pool file: (1) the registered constants (clean arm, 13 features, C = 0.01, the three model names
and the three deciding pairs); (2) the candidate alignment halts on a cell-set mismatch and on an M8 mismatch and
passes on identical sets regardless of cell order; (3) the pair statistic reproduces v8_score's S3 machinery on a
synthetic per-fire table (sign, weights, one-sided p); (4) ``features`` and ``score`` refuse to run without a
confirmation commit; (5) ``wanted_epochs`` keeps only ranked candidate jumps with numeric intervals.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

for _m in ("pandas", "scipy", "sklearn"):
    pytest.importorskip(_m)  # CI's engine env has no pandas: skip there, as the v8 tests do
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "engine" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CF = pytest.importorskip("scripts.ml_ember.confirm")
L = pytest.importorskip("scripts.ml_ember.ladder")
V = pytest.importorskip("scripts.validation.v8_score")


def test_registered_constants():
    assert CF.ARM == "clean" and CF.C2 == 0.01 and CF.C_DIST == 1.0
    assert len(CF.R2_FEATURES) == 13
    assert "hazshare" not in CF.R2_FEATURES and "fuel_code" not in CF.R2_FEATURES and "d_nonfuel_km" not in CF.R2_FEATURES
    assert set(CF.R2_FEATURES) <= set(L.R1_FEATURES)
    assert CF.MODELS == ["M8", "B1", "DIST", "RUNG2", "HYBRID"]
    assert CF.DECIDING == {"RQ1a": ("RUNG2", "M8"), "RQ1b": ("RUNG2", "DIST"), "RQ3": ("HYBRID", "M8")}


def _scored(cells, m8):
    return pd.DataFrame(dict(fire_key="F", epoch=3, cell_id=cells, DIST=np.arange(len(cells), dtype=float),
                             RUNG2=np.arange(len(cells), dtype=float), HYBRID=np.arange(len(cells), dtype=float),
                             m8_log_frozen=np.log(m8), b1=np.ones(len(cells))))


def test_alignment_passes_on_permuted_identical_sets_and_halts_on_mismatch():
    cells = np.array([10, 11, 12, 13], dtype=np.int64); m8 = np.array([1e-3, 2e-3, 3e-3, 4e-3])
    scored = _scored(cells, m8)
    perm = np.array([3, 0, 2, 1])
    ok = lambda fk, ep: dict(cell_id=cells[perm], score_M8=m8[perm], score_B1=np.ones(4))  # noqa: E731
    cands, issues = CF.align_to_candidates(scored, ok, lambda *x: None)
    assert issues["intervals"] == 1 and not issues["cell_set_mismatch"] and not issues["m8_mismatch"]
    arr = cands[("F", "3")]
    assert np.array_equal(arr["cell_id"], cells[perm])
    assert np.array_equal(arr["score_RUNG2"], np.arange(4, dtype=float)[perm])  # our scores follow v8's cell order
    bad_set = lambda fk, ep: dict(cell_id=np.array([10, 11, 12, 99]), score_M8=m8, score_B1=np.ones(4))  # noqa: E731
    _, issues = CF.align_to_candidates(scored, bad_set, lambda *x: None)
    assert len(issues["cell_set_mismatch"]) == 1
    bad_m8 = lambda fk, ep: dict(cell_id=cells, score_M8=m8 * 2.0, score_B1=np.ones(4))  # noqa: E731
    _, issues = CF.align_to_candidates(scored, bad_m8, lambda *x: None)
    assert len(issues["m8_mismatch"]) == 1
    missing = lambda fk, ep: None  # noqa: E731
    _, issues = CF.align_to_candidates(scored, missing, lambda *x: None)
    assert issues["missing_candidate_file"] == [("F", 3)]


def test_pair_stats_matches_v8_weights_and_is_one_sided():
    rng = np.random.default_rng(0)
    pf = {}
    for i in range(30):
        K = int(rng.integers(1, 5)); reg = "CAN" if i % 2 else "US"
        rb = rng.uniform(0.3, 0.9, K); ra = np.clip(rb + 0.15, 0, 1)
        pf[f"F{i}"] = dict(region=reg, r=dict(A=list(ra), B=list(rb)))
    st = CF.pair_stats(pf, "A", "B")
    K = np.array([len(pf[f]["r"]["A"]) for f in sorted(pf)], float)
    a = np.array([np.mean(np.array(pf[f]["r"]["A"]) - np.array(pf[f]["r"]["B"])) for f in sorted(pf)])
    w = V.weights(K, "S3")
    assert st["S3"] == pytest.approx(float((w * a).sum() / w.sum()))
    assert st["p_signflip"] < 0.025 and st["share_fires_positive"] == 1.0
    rev = CF.pair_stats(pf, "B", "A")
    assert rev["S3"] == pytest.approx(-st["S3"]) and rev["p_signflip"] > 0.5
    assert set(st["by_region"]) == {"CAN", "US"}


def test_features_and_score_refuse_without_commit():
    with pytest.raises(SystemExit):
        CF.main(["features"])
    with pytest.raises(SystemExit):
        CF.main(["score"])


def test_wanted_epochs_keeps_ranked_candidate_jumps_only():
    J = pd.DataFrame(dict(fire_key=["A", "A", "A", "B"], interval=["3", "x202501010000", "5", "7"], cell_id=[12, -1, -1, 4],
                          out_final_m=[0.0, 0.0, 0.0, 0.0]))
    assert CF.wanted_epochs(J) == {"A": {3}, "B": {7}}
