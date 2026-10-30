"""Unit tests for the M8-C1 kernel (scripts/validation/v8/score_c1.py), PREREG_M8_C1 section 4 [R76].

(a) f reproduces the section-3 table at the nodes and the registered check points; score_C1 is non-increasing in d for
    fixed m_ff; the shape file's nodes equal the PREREG's printed nodes and the floor is round(-1.0284, 2).
(b) p_e equals M8's own establishment factor (intervals.m8_score with beta_0 = 0).
(c) on five TRAINING fires the C1 ranks of the training jumps computed from the interval npz equal those computed from
    the stage-2 feature table (cells_eval.parquet, training split), with bit-for-bit candidate sets.
M8 unchanged: m8_score on a training fire is identical before and after importing score_c1, and the registered files
keep their sha256. Only training-split data is read; no sealed, held-out or pool A/E path is opened.
"""
import hashlib
import importlib
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

for _m in ("pandas", "geopandas", "pyogrio", "rasterio", "scipy"):
    pytest.importorskip(_m)  # the runners need the geo stack (not installed in CI): skip there

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
IV = importlib.import_module("scripts.validation.v8.intervals")
C = importlib.import_module("scripts.validation.v8.common")
V8S = importlib.import_module("scripts.validation.v8_score")

PREREG_NODES = [(1.5, 2.46), (2.5, 1.22), (3.5, 0.63), (4.5, 0.43), (5.5, 0.06), (7.0, -0.17), (8.0, -1.03)]
CHECK_POINTS = {2.0: 1.84, 3.0: 0.925, 5.0: 0.245, 6.25: -0.055, 7.5: -0.60, 12.0: -1.03}
A2_BETA = dict(b0=-22.670, b_w=0.0, b_L=-4.675)
REG_SHA = {"v8/intervals.py": "4303e544736968f29f28dd827f662b0f6dfed9178109154ed309d7a5b4724dbe",
           "v8/common.py": "8eef29c54a294136fda7aec231e4c3dd32a7082ce86f4361087c805c33608d92",
           "v8_score.py": "623233f687eb97d83a5cd3d81806accb28f78a6ef699f941249247c38ebad880"}
TABLE = Path(os.environ.get("FIRESIM_ML_EMBER_TABLE", Path.home() / "dev/wildfire/reports/data/"
                            "ml-ember-cast-2026-10-29/table/cells_eval.parquet"))


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def S():
    mod = importlib.import_module("scripts.validation.v8.score_c1")
    if not mod.SHAPE_PATH.exists():
        pytest.skip("frozen decay-shape file not present")
    return mod


def test_registered_files_unchanged():
    base = ROOT / "scripts" / "validation"
    for f, h in REG_SHA.items():
        assert _sha(base / f) == h, f
    H = importlib.import_module("scripts.validation.v8.heldout")
    assert H.INTERVALS_SHA == REG_SHA["v8/intervals.py"]


def test_shape_file_pinned_and_nodes_match_prereg(S):
    assert _sha(S.SHAPE_PATH) == S.SHAPE_SHA256 == "79aa8e1861a99f46f7e4dc86242f8cd7ecfc8811b54caaedf4ec6299971251d1"
    s = S.load_shape()
    assert list(zip(s["x"].tolist(), s["y"].tolist())) == PREREG_NODES
    floor = (-1.00 * 9 - 0.79 * 12 - 1.12 * 14 - 1.12 * 20) / 55
    assert s["floor"] == round(floor, 2) == -1.03
    assert s["floor_from_km"] == 8.0 and s["below"] == 2.46


def test_shape_sha_mismatch_refused(S, tmp_path):
    p = tmp_path / "shape.json"
    p.write_bytes(S.SHAPE_PATH.read_bytes() + b"\n")
    with pytest.raises(RuntimeError):
        S.load_shape(p)


def test_a_f_reproduces_table_and_check_points(S):
    for d, v in PREREG_NODES:
        assert S.f_of_d(d) == pytest.approx(v, abs=1e-12)
    for d, v in CHECK_POINTS.items():
        assert S.f_of_d(d) == pytest.approx(v, abs=1e-12)
    for d, v in S.load_shape()["check_points"].items():  # the file's own check points too
        assert S.f_of_d(d) == pytest.approx(v, abs=1e-12)
    assert np.all(S.f_of_d([0.0, 0.5, 1.0, 1.49]) == 2.46)
    assert np.all(S.f_of_d([8.0, 8.01, 15.0, 58.0, 500.0]) == -1.03)
    # score at the nodes = exp(log ratio) x p_e
    m = 9.5
    for d, v in PREREG_NODES:
        assert S.score_c1_arrays(d, m) == pytest.approx(math.exp(v) * S.p_e(m), rel=1e-12)


def test_a_non_increasing_in_d(S):
    d = np.linspace(0.0, 60.0, 60001)
    for m in (3.0, 12.0, 21.0, 35.0):
        sc = S.score_c1_arrays(d, np.full_like(d, m))
        assert np.all(np.diff(sc) <= 0)
        assert np.all(sc > 0)


def test_b_p_e_equals_m8_factor(S):
    fit = importlib.import_module("scripts.validation.v8.fit")
    assert S.B_M == fit.B_M == pytest.approx(-0.2441, abs=1e-4)
    rng = np.random.default_rng(7)
    n = 50
    m_ff = np.array([4.0, 13.0, 21.0, 31.0], np.float32)
    zone = rng.integers(0, 4, n).astype(np.int16)
    A = np.zeros((2, 3, n), np.float32)
    A[0, 0] = 1.0  # unit short-mode landing in WAF class 0: m8_score = exp(b0 + b_m (m - 21)) x (1 + U WAF)^b_w
    npz = dict(A=A, u10=np.full(4, 5.0, np.float32), m_ff=m_ff, zone=zone,
               d_act=rng.uniform(1000, 30000, n).astype(np.float32))
    m8 = IV.m8_score(npz, dict(b0=0.0, b_w=0.0, b_L=-4.675, b_m=fit.B_M))
    np.testing.assert_array_equal(S.p_e(m_ff[zone].astype(np.float64)), m8)
    # and score_c1 uses the per-zone m_ff exactly as m8_score
    np.testing.assert_array_equal(S.score_c1(npz),
                                  np.exp(S.f_of_d(npz["d_act"].astype(np.float64) / 1000.0)) * m8)
    np.testing.assert_array_equal(S.score(npz, kernel="c1"), S.score_c1(npz))
    with pytest.raises(ValueError):
        S.score(npz, kernel="dist")


# ── training-fire tests (skip where the v8 pool / stage-2 table are absent, e.g. CI) ───────────────────────────────

def _training_jump_intervals(n_fires=5):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    if not TABLE.exists():
        pytest.skip("stage-2 training table not present")
    fires = pd.read_parquet(TABLE.parent / "fires.parquet")
    split = C.split_table().set_index("fire_key").assignment if (C.PREFREEZE / "results" / "split.csv").exists() else None
    out = []
    for k in sorted(fires.loc[fires.n_jumps > 0, "fire_key"]):
        if split is not None:
            assert split[k] == "training", k  # never a decision / held-out fire
        fdir = C.FIRES / k.replace(":", "_") / "intervals"
        if not fdir.exists():
            continue
        t = pd.read_parquet(TABLE, columns=["row_id", "fire_key", "epoch", "d_act_km", "m_ff_pct", "zone", "y"],
                            filters=[("fire_key", "==", k)])
        for ep, g in t.groupby("epoch"):
            if g.y.sum() == 0 or not (fdir / f"{int(ep):05d}.npz").exists():
                continue
            out.append((k, int(ep), g.sort_values("row_id"), fdir / f"{int(ep):05d}.npz"))
        if len({o[0] for o in out}) >= n_fires:
            break
    if len({o[0] for o in out}) < n_fires:
        pytest.skip("fewer than five training fires with interval files")
    return out


def test_c_ranks_equal_stage2_table_on_five_training_fires(S):
    rows = _training_jump_intervals(5)
    n_pos = 0
    for k, ep, g, p in rows:
        z = np.load(p)
        cand_idx = (g.row_id.values & ((1 << 24) - 1)).astype(np.int64)
        assert np.array_equal(cand_idx, np.arange(len(z["d_act"]))), (k, ep)  # bit-for-bit candidate set
        assert np.array_equal(g.zone.values.astype(np.int64), z["zone"].astype(np.int64)), (k, ep)
        assert np.array_equal(g.d_act_km.values, (z["d_act"] / 1000.0).astype(np.float32)), (k, ep)
        assert np.array_equal(g.m_ff_pct.values, z["m_ff"][z["zone"]]), (k, ep)
        s_npz = S.score_c1(z)
        s_tab = S.score_c1_arrays(g.d_act_km.values.astype(np.float64), g.m_ff_pct.values.astype(np.float64))
        pos = np.nonzero(g.y.values == 1)[0]
        r_npz = V8S.pct_rank(s_npz, s_npz[pos])
        r_tab = V8S.pct_rank(s_tab, s_tab[pos])
        np.testing.assert_array_equal(r_npz, r_tab, err_msg=f"{k} epoch {ep}")
        n_pos += len(pos)
    assert n_pos >= 5


def test_m8_scores_unchanged_by_importing_score_c1():
    rows = _training_jump_intervals(1)
    k, ep, g, p = rows[0]
    beta = dict(A2_BETA, b_m=importlib.import_module("scripts.validation.v8.fit").B_M)
    z = dict(np.load(p))
    before = IV.m8_score(z, beta)
    importlib.import_module("scripts.validation.v8.score_c1")
    after = IV.m8_score(z, beta)
    np.testing.assert_array_equal(before, after)
    np.testing.assert_array_equal(importlib.import_module("scripts.validation.v8.score_c1").score(z, "m8", beta), before)
    assert _sha(IV.__file__) == REG_SHA["v8/intervals.py"]
