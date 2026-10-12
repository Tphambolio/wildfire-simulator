"""Long-range embers v3 (spec §6.5): probabilistic building-to-building links, building
survival, ember factor and the wind-event end of the run."""

from __future__ import annotations

import math

import numpy as np
import pytest

from firesim.structures import long_range as LR
from firesim.structures.spread import WindPeriod, burnout_minutes, hamada_spread

from .test_long_range_v2 import _grid


def test_wind_event_end_rule():
    t = np.arange(0, 600, 60.0)
    v = np.array([40, 40, 40, 40, 15, 15, 15, 40, 40, 40], float)
    assert LR.wind_event_end(t, v, 20.0) == 240.0  # below 20 km/h for 2 h from 240 min
    assert LR.wind_event_end(t, v, 10.0) == math.inf  # never below
    v2 = np.array([10, 10, 10, 40, 40, 40, 40, 40, 40, 40], float)
    assert LR.wind_event_end(t, v2, 20.0) == math.inf  # calm only before 3 h
    assert LR.wind_event_end(t, v, None) == math.inf


def test_link_probability_decays_with_separation():
    rng = np.random.default_rng(0)
    f = LR.link_filter_v3(rng, 10.0, 50.0)
    for sep, expect in ((0.0, 1.0), (10.0, math.exp(-1)), (30.0, math.exp(-3)), (60.0, 0.0)):
        got = f(0, np.zeros(20000, int), np.full(20000, sep)).mean()
        assert got == pytest.approx(expect, abs=0.01)


def test_v3_reduces_to_hamada_without_survival_and_with_certain_links():
    u = _grid(10, 3, 22.0)
    w = [WindPeriod(0.0, 40.0, 270.0)]
    tf = np.full(len(u), np.inf)
    tf[0] = 0.0
    edges = np.arange(0.0, 121.0, 10.0)
    o = LR.LongRangeOptions(model="v3", survival=0.0, link_scale_m=1e9, param_draws=False,
                            ember_factor=0.0, link_cutoff_m=30.0)
    t, *_ = LR.run_member_v3(u, tf, None, edges, o, w, 120.0, np.random.default_rng(1), None,
                             [(0.0, 40.0, 270.0)], burnout_min=burnout_minutes(150))
    ref = hamada_spread(u, tf, w, duration_min=120.0, burnout_min=burnout_minutes(150))
    assert np.array_equal(np.isfinite(t), np.isfinite(ref.t_min))


def test_survival_thins_involvement_and_stop_ends_the_run():
    u = _grid(12, 12, 15.0)
    w = [WindPeriod(0.0, 40.0, 270.0)]
    tf = np.full(len(u), np.inf)
    tf[u.x < u.x.min() + 1] = 0.0
    edges = np.arange(0.0, 241.0, 10.0)
    kw = dict(param_draws=False, ember_factor=0.0, link_scale_m=1e9)
    full = LR.LongRangeOptions(model="v3", survival=0.0, **kw)
    half = LR.LongRangeOptions(model="v3", survival=0.5, **kw)
    args = (u, tf, None, edges)
    t0, *_ = LR.run_member_v3(*args, full, w, 240.0, np.random.default_rng(2), None, [(0, 40, 270)])
    t1, *_ = LR.run_member_v3(*args, half, w, 240.0, np.random.default_rng(2), None, [(0, 40, 270)])
    assert np.isfinite(t1).sum() < 0.6 * np.isfinite(t0).sum()
    ts, *_ = LR.run_member_v3(*args, full, w, 240.0, np.random.default_rng(2), None, [(0, 40, 270)],
                              stop_min=5.0)
    assert np.all(ts[np.isfinite(ts)] <= 5.0)
    assert np.isfinite(ts).sum() < np.isfinite(t0).sum()


def test_v3_options_and_ember_factor():
    o = LR.LongRangeOptions(model="v3", ember_factor=3.0, param_draws=False)
    d = LR.member_draw(o, np.random.default_rng(0), 4)
    assert np.allclose(d.q, 3.0 * 4 * LR.P_BED_MULCH * LR.P_B_FIXED)
    assert o.params()["long_range_model"] == "v3" and "long_range_survival" in o.params()
    with pytest.raises(ValueError):
        LR.LongRangeOptions(model="v3", survival=1.0)


def test_grid_run_v3():
    from .test_long_range import _run

    res = _run(model="v3", members=3)
    assert res.params["long_range_model"] == "v3"
    assert res.params["neighbour_cutoff_m"] == LR.V3_LINK_CUTOFF_M
    assert "long_range_stop_min" in res.params
    c = res.counts_at(120.0)
    assert c["long_range"]["members"] == 3
    a = _run(model="v3", members=2)
    b = _run(model="v3", members=2)
    assert np.array_equal(a.t_members, b.t_members)
