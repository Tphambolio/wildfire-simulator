"""Stand objects and the stand-level conifer model of the v2.1 key (scripts/fuelgrid/stands.py).

Synthetic data only; no downloads, no City data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.fuelgrid import config as C  # noqa: E402
from scripts.fuelgrid import key2 as K  # noqa: E402
from scripts.fuelgrid import stands as S  # noqa: E402


def test_class_objects_never_mix_conifer_and_deciduous_and_respect_blocks():
    share = np.zeros((40, 40), np.float32)
    share[:, 20:] = 1.0                   # deciduous west half, conifer east half
    mask = np.ones((40, 40), bool)
    ids = S.class_objects(share, mask, block_cells=10)
    for u in np.unique(ids[ids > 0]):
        vals = share[ids == u]
        assert vals.min() == vals.max() or (vals.max() - vals.min()) <= 1.0  # one class per object
        rr, cc = np.nonzero(ids == u)
        assert rr.max() // 10 == rr.min() // 10 and cc.max() // 10 == cc.min() // 10  # inside one 200 m block
    # the 3 x 3 smoothing makes a mixed seam at the boundary; away from it, objects are pure
    assert len(np.unique(share[ids == ids[5, 5]])) == 1 and len(np.unique(share[ids == ids[5, 35]])) == 1


def test_class_objects_outside_mask_is_zero_and_ids_start_where_asked():
    share = np.full((20, 20), 0.5, np.float32)
    mask = np.zeros((20, 20), bool)
    mask[2:8, 2:8] = True
    ids = S.class_objects(share, mask, block_cells=10, start=101)
    assert (ids[~mask] == 0).all() and ids[mask].min() == 101


def test_class_objects_are_deterministic():
    rng = np.random.default_rng(5)
    share = rng.random((50, 60)).astype(np.float32)
    mask = rng.random((50, 60)) > 0.3
    assert np.array_equal(S.class_objects(share, mask), S.class_objects(share, mask))


def test_segment_means_are_nan_aware():
    stack = np.array([[[1, 3], [np.nan, 5]]], np.float32)
    ids = np.array([[1, 1], [1, 2]])
    m = S.segment_means(stack, ids, 2)
    assert m[1, 0] == pytest.approx(2.0) and m[2, 0] == pytest.approx(5.0)


def test_uplvi_percent_conifer_weights_treed_components_and_flags_non_forest():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({
        "stnd1": ["White Spruce (SW)", "Trembling Aspen (AW)", "Non Forest (NF)"],
        "stypeper1": [60, 100, 100], "con1": [100, 0, 0],
        "stnd2": ["Trembling Aspen (AW)", None, None], "stypeper2": [40, None, None], "con2": [10, None, None],
        "stnd3": [None, None, None], "stypeper3": [None, None, None], "con3": [None, None, None]})
    pc, share, allt = S.uplvi_percent_conifer(df)
    assert pc[0] == pytest.approx(64.0) and pc[1] == 0 and np.isnan(pc[2])
    assert allt.tolist() == [True, True, False] and share[2] == 0


def test_consistent_pc_reproduces_the_class_through_the_key_cuts():
    k = np.array([0, 1, 1, 2])
    pc = np.array([0.6, 0.1, 0.9, 0.3])
    out = S.consistent_pc(k, pc)
    assert (S.cmd_class(out) == k).all()


def test_stand_model_separates_obvious_classes():
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(0)
    n = 300
    pc = np.r_[np.zeros(120), np.full(60, 0.5), np.ones(120)]
    X = np.c_[pc + rng.normal(0, 0.05, n), rng.normal(0, 1, n)]
    m = S.StandModel(1).fit(X, pc)
    k, out = m.predict(X)
    assert (k == S.cmd_class(pc)).mean() > 0.9
    assert (S.cmd_class(out) == k).all()


def test_balanced_accuracy_counts_mixed_as_wrong_and_reports_opposite_type():
    true = np.array([1.0, 1.0, 0.0, 0.0])
    pred = np.array([2, 1, 0, 2])
    r = S.balanced_accuracy(true, pred)
    assert r["balanced_accuracy"] == pytest.approx(0.5) and r["opposite_type"] == pytest.approx(0.25)


def test_key_types_each_stand_object_as_a_whole():
    from tests.fuelgrid.test_fuelgrid_key2 import N, _inputs
    x = _inputs(conifer=0.9)
    cf = np.full((N, N), 0.9, np.float32)
    cf[5:15, 10:15] = 0.0                       # deciduous half of the stand
    x.base.conifer_frac = cf
    units = np.zeros((N, N), np.int32)
    units[5:15, 5:10] = 1
    units[5:15, 10:15] = 2
    x.units = units
    cls, _, _, _ = K.decision_key_v2(x)
    assert (cls[6:14, 6:10] == C.C2).all() and (cls[6:14, 10:14] == C.D2).all()  # no mixing across objects
