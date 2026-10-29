"""Offline unit tests for the v8 runners (scripts/validation/v8): fuel typing, kernels, scores, guard, the seeded
ELMFIRE subset, the establishment fit, the draw stop counts and the A1 jumps.csv column."""
import importlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

for _m in ("pandas", "geopandas", "pyogrio", "rasterio", "scipy"):
    pytest.importorskip(_m)  # the runners need the geo stack (not installed in CI): skip there

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
I = importlib.import_module("scripts.validation.v8.intervals")


def test_fbp_of_code_season():
    code = np.array([2, 13, 650, 32, 101, 13])
    tid, pc = I.fbp_of_code(code, green=True)
    names = [I.TYPES[t] for t in tid]
    assert names == ["C2", "D2", "M2", "O1b", "", "D2"]
    assert pc[2] == 50
    tid, _ = I.fbp_of_code(code, green=False)
    assert [I.TYPES[t] for t in tid][1:3] == ["D1", "M1"]


def test_long_kernel_mass_and_direction():
    k = I.long_kernel(125.0, math.radians(90.0))  # blowing to the east
    assert 0.95 < k.sum() <= 1.0001
    n = k.shape[0] // 2
    assert k[:, n + 1:].sum() > 0.99 * k.sum()  # all mass downwind (east)


def test_short_kernel_bounded():
    k = I.short_kernel(125.0, 0.0, 300000.0, 1.0)  # far outside Sardoy's domain: clamped
    n = k.shape[0] // 2
    assert n * 125.0 <= 4200.0 + 2 * 125.0
    assert abs(k.sum() - 0.999) < 1e-3


def test_scores_shapes_and_monotonic():
    npz = dict(A=np.array([[[1.0, 2.0], [0, 0], [0, 0]], [[1.0, 0.0], [0, 0], [0, 0]]], np.float32),
               zone=np.array([0, 0], np.int16), u10=np.array([5.0], np.float32), m_ff=np.array([10.0], np.float32),
               hazshare=np.array([0.5, 0.5], np.float32), d_act=np.array([1000.0, 3000.0], np.float32),
               sec_act=np.array([True, True]), d_all=np.array([1000.0, 3000.0], np.float32), sec_all=np.array([True, False]))
    beta = dict(b0=-5.0, b_w=1.0, b_L=-0.7, b_m=-0.2441)
    s = I.m8_score(npz, beta)
    assert s.shape == (2,) and np.all(s > 0)
    b = I.b1_score(npz)
    assert b[0] > b[1]
    assert I.b1_score(npz, active=False)[1] == 0.0


def test_guard_sets_limit():
    import resource
    C = importlib.import_module("scripts.validation.v8.common")
    old = resource.getrlimit(resource.RLIMIT_AS)
    try:
        C.guard()
        assert resource.getrlimit(resource.RLIMIT_AS)[0] <= C.AS_BACKSTOP_BYTES
    finally:
        resource.setrlimit(resource.RLIMIT_AS, old)


def test_elmfire_subset_seeded():
    E = importlib.import_module("scripts.validation.v8.elmfire")
    keys = [f"NBAC:2023_{i}" for i in range(200)]
    a, b = E.subset(keys), E.subset(list(reversed(keys)))
    assert a == b and len(a) == 150 and len(set(a)) == 150  # order of input irrelevant (sorted, then permuted)
    assert sorted(E.subset(keys[:40])) == sorted(keys[:40])


def test_fit_b0_only_is_poisson_mle():
    F = importlib.import_module("scripts.validation.v8.fit")
    rng = np.random.default_rng(3)
    S_rows = [dict(S=rng.uniform(0, 2, size=(1, 2, 3)), u10=np.array([4.0]), m=np.array([12.0])) for _ in range(30)]
    pos = [dict(A=rng.uniform(0, 1, size=(2, 3)), u10=4.0, m=12.0) for _ in range(12)]
    beta, res = F.fit(S_rows, pos, full=False)
    tot = sum(F._terms(1.0, math.log(0.5), r["S"][0], 4.0) * math.exp(F.B_M * (12.0 - 21.0)) for r in S_rows)
    assert abs(beta["b0"] - math.log(12 / tot)) < 1e-4
    assert beta["b_w"] == 1.0 and abs(beta["b_L"] - math.log(0.5)) < 1e-12 and abs(beta["b_m"] - F.B_M) < 1e-12


def test_fit_full_respects_constraints():
    F = importlib.import_module("scripts.validation.v8.fit")
    rng = np.random.default_rng(4)
    S_rows = [dict(S=rng.uniform(0, 2, size=(1, 2, 3)), u10=np.array([rng.uniform(1, 10)]), m=np.array([10.0])) for _ in range(40)]
    pos = [dict(A=rng.uniform(0, 1, size=(2, 3)), u10=float(rng.uniform(1, 10)), m=10.0) for _ in range(150)]
    beta, _ = F.fit(S_rows, pos, full=True)
    assert beta["b_w"] >= 0.0 and beta["b_L"] <= 0.0


def test_draw_status_and_stop_rule_counts():
    D = importlib.import_module("scripts.validation.v8.draw")
    import pandas as pd
    c = pd.DataFrame(dict(region=["CAN", "CAN", "US", "US"], rank=[1, 2, 1, 2], fire_key=list("abcd"),
                          eligible=[True, False, True, True], K=[100, 0, 30, 31], n_int_12h=[5, 0, 3, 3]))
    tot, reg, nel = D.status(c)
    assert tot == 161 and reg == {"CAN": 100, "US": 61} and nel == {"CAN": 1, "US": 2}
    assert tot >= D.K_MIN and all(v >= D.K_REG for v in reg.values())


def test_score_guard_used_by_unseal_columns():
    # jumps.csv written by assemble.unseal must carry out_final_m (addendum A1); v8_score refuses otherwise
    src = (ROOT / "scripts" / "validation" / "v8" / "assemble.py").read_text()
    assert '"out_final_m"' in src and "inside_final &" not in src.split("def unseal")[1].split("el = drawn_eligible")[0]
