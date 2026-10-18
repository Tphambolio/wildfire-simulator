"""G3 kernel-integrity test for PREREG_LR8 (scripts/validation/v8_gate.py)."""
import importlib.util
from pathlib import Path

P = Path(__file__).resolve().parents[3] / "scripts" / "validation" / "v8_gate.py"
spec = importlib.util.spec_from_file_location("v8_gate", P)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def test_g3_kernel_bounds():
    r = g.g3()
    assert r["median_max_m"] <= 138.0
    assert r["p999_max_m"] <= 4200.0
    assert abs(r["fig9_mu"] - 4.15) < 0.02 and abs(r["fig9_sigma"] - 1.08) < 0.02
    assert r["pass"]


def test_clamp_is_identity_inside_domain():
    import numpy as np
    from firesim.structures.embers import sardoy_lognormal
    a = sardoy_lognormal(np.array([15.0, 30.0]), np.array([8.0, 12.0]))
    b = g.sardoy_clamped(np.array([15.0, 30.0]), np.array([8.0, 12.0]))
    assert np.allclose(a, b)
