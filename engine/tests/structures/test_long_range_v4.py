"""Long-range embers v4 (spec §6.6): per-building survival from pre-fire attributes, VIIRS burn
times and FRP emission scaling, per-unit survival in the v3 member."""

from __future__ import annotations

import math

import numpy as np
import pytest

from firesim.structures import long_range as LR
from firesim.structures.spread import WindPeriod

from .test_long_range_v2 import _grid


def test_survival_v4_logit_shifts():
    assert LR.survival_v4(0.3) == 0.3
    s = LR.survival_v4(0.3, canopy_30_100=np.array([0.3, 0.8, np.nan]), b_canopy=-2.0)
    assert s[0] == pytest.approx(0.3) and s[2] == pytest.approx(0.3)
    assert s[1] == pytest.approx(1 / (1 + math.exp(-(math.log(0.3 / 0.7) - 1.0))))
    y = LR.survival_v4(0.3, year_built=np.array([1990, 2010, np.nan]), b_code=1.0)
    assert y[0] == pytest.approx(0.3) and y[2] == pytest.approx(0.3) and y[1] > 0.3


def test_viirs_burn_times_and_emit_scale():
    xs, ys = np.array([0.0, 500.0, 5000.0]), np.zeros(3)
    det_x, det_y = np.array([0.0, 100.0, 600.0]), np.zeros(3)
    t = LR.viirs_burn_times(xs, ys, det_x, det_y, np.array([30.0, 10.0, 90.0]), radius_m=750.0)
    assert t[0] == 10.0 and t[1] == 10.0 and np.isnan(t[2])
    sc = LR.viirs_emit_scale(xs, ys, det_x, det_y, np.array([100.0, 50.0, 25.0]), 1.0, frp_ref=50.0, radius_m=150.0)
    assert sc[0] == pytest.approx(1.5) and sc[2] == pytest.approx(1.0)  # no detection: median 50 MW
    assert np.all(LR.viirs_emit_scale(xs, ys, det_x, det_y, np.ones(3), 0.0) == 1.0)


def test_emit_scale_multiplies_emission():
    src = LR.EmberSourceCells(np.zeros(2), np.zeros(2), np.zeros(2), np.ones(2), np.full(2, 5e4),
                              np.full(2, 2.0), np.ones(2), np.ones(2), np.array(["C2", "C2"], dtype=object), 20.0)
    base = src.emitted(LR.LongRangeOptions())
    src.emit_scale = np.array([2.0, 0.5])
    assert np.allclose(src.emitted(LR.LongRangeOptions()), base * [2.0, 0.5])


def test_per_unit_survival_blocks_those_units():
    u = _grid(6, 1, 15.0)
    w = [WindPeriod(0.0, 40.0, 270.0)]
    tf = np.full(len(u), np.inf)
    tf[0] = 0.0
    o = LR.LongRangeOptions(model="v3", param_draws=False, ember_factor=0.0, link_scale_m=1e9)
    s_unit = np.array([0.0, 0.0, 0.999999, 0.0, 0.0, 0.0])
    t, *_ = LR.run_member_v3(u, tf, None, np.arange(0, 121, 10.0), o, w, 120.0, np.random.default_rng(0),
                             None, [(0, 40, 270)], survival_unit=s_unit)
    assert np.isinf(t[2])
