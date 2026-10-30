"""Unit tests for the v8 follow-up scripts (fuel-seam surrogate, simulated-edge helpers)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "validation"))


def test_surrogate_code_keeps_only_hazard_class():
    from v8.fuel_seam import surrogate_code
    code = np.array([2, 650, 13, 31, 101, 0, 22, 4])
    haz = np.array([1, 1, 0, 0, 0, 0, 0, 1])
    out = surrogate_code(code, haz, 2, 13)
    assert out.tolist() == [2, 2, 13, 13, 101, 0, 13, 2]


def test_local_offset_from_longitude():
    from v8.simedge import local_offset_h
    assert local_offset_h(-113.5) == -8 and local_offset_h(-105.0) == -7 and local_offset_h(-71.0) == -5


def test_close_mask_matches_intervals_rule():
    """Same operations as the registered intervals.run_fire start perimeter (375 m dilation, 1 km closing)."""
    from scipy import ndimage
    from v8.simedge import close_mask
    rng = np.random.default_rng(1)
    pts = rng.random((80, 80)) > 0.995
    cs = 125.0
    dil = ndimage.distance_transform_edt(~pts) * cs <= 375.0
    E = ndimage.distance_transform_edt(~dil) * cs <= 1000.0
    ref = (ndimage.distance_transform_edt(E) * cs > 1000.0 - cs) | dil
    assert np.array_equal(close_mask(pts, cs), ref)
