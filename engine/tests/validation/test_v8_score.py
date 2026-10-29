"""Unit tests for scripts/validation/v8_score.py (PREREG_LR8 section 9): rank/ties vs sklearn AUC,
sign-flip null calibration, S3 weights, and the decision logic on toy data."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

P = Path(__file__).resolve().parents[3] / "scripts" / "validation" / "v8_score.py"
spec = importlib.util.spec_from_file_location("v8_score", P)
v8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v8)


def test_pct_rank_ties_half():
    s = np.array([1.0, 2.0, 2.0, 3.0])
    assert v8.pct_rank(s, np.array([2.0]))[0] == pytest.approx((1 + 0.5 * 2) / 4)


def test_weights_s3():
    assert np.allclose(v8.weights(np.array([0, 1, 4])), [0, 1, 4 / 1.9])


def test_selftest_suite():
    pytest.importorskip("sklearn")
    v8.selftest()
