"""Tests for long-range ember spot fires in the grid model (v6, spec §6.8) and the spot seeding fix.

References:
    - Sardoy et al. (2008) Combust. Flame 154:478-488 (short mode, Fig. 10 p.488).
    - Storey et al. (2020) Fire 3:10 p.7 (pooled spot distances; the long mode's location).
    - Beverly & Wotton (2007) AMS preprint eq 2.02 (P sustained flaming).
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.ember_spotting import LongRangeSpotter
from firesim.spread.huygens import FuelGrid, SpreadConditions

H = 90.0
LAT0 = 39.7


def _grid(n=70, fuel=FuelType.C4):
    cl = H / 111320.0
    cg = H / (111320.0 * math.cos(math.radians(LAT0)))
    return FuelGrid([[fuel] * n for _ in range(n)], LAT0 - n * cl, LAT0, -121.5, -121.5 + n * cg, n, n), cl, cg


def _cfg(cl, cg, r=55, c=15, hours=2.0):
    return {"ignition_lat": LAT0 - (r + 0.5) * cl, "ignition_lng": -121.5 + (c + 0.5) * cg, "duration_hours": hours}


COND = SpreadConditions(wind_speed=40.0, wind_direction=225.0, ffmc=94.0, dmc=100.0, dc=800.0)  # from SW


def test_eta_zero_no_spots_and_matches_plain_run():
    fg, cl, cg = _grid()
    hook = LongRangeSpotter(H, lambda t: (40.0, 225.0), 94.0, eta=0.0)
    a = run_cellular_simulation(_cfg(cl, cg), fg, COND, compute_perimeter=False, seed=1, dt_minutes=10)[-1].arrival
    b = run_cellular_simulation(_cfg(cl, cg), fg, COND, compute_perimeter=False, seed=1, dt_minutes=10,
                                spot_hook=hook)[-1].arrival
    assert hook.events == []
    na, nb = np.isfinite(a).sum(), np.isfinite(b).sum()
    assert abs(na - nb) <= 0.03 * na


def test_spots_land_downwind_and_log_distance():
    fg, cl, cg = _grid()
    hook = LongRangeSpotter(H, lambda t: (40.0, 225.0), 94.0, eta=1e-6, seed=3)
    arr = run_cellular_simulation(_cfg(cl, cg), fg, COND, compute_perimeter=False, seed=1, dt_minutes=10,
                                  spot_hook=hook)[-1].arrival
    assert len(hook.events) > 0
    # wind from SW (225 deg) -> embers to the NE: spot rows above (north of) and columns east of the ignition
    rows = np.array([e.row for e in hook.events])
    cols = np.array([e.col for e in hook.events])
    assert np.mean(rows < 55) > 0.7 and np.mean(cols > 15) > 0.7
    assert all(e.dist_to_fire_m >= 0 for e in hook.events)
    assert hook.landed is not None and hook.landed.max() > 0
    assert hook.emitted > 0
    for e in hook.events:  # every spot cell burned at (or after, if overrun earlier) its event time
        assert np.isfinite(arr[e.row, e.col])


def test_more_eta_more_spots():
    fg, cl, cg = _grid()
    n = []
    for eta in (1e-8, 1e-6):
        hook = LongRangeSpotter(H, lambda t: (40.0, 225.0), 94.0, eta=eta, seed=5)
        run_cellular_simulation(_cfg(cl, cg, hours=1.5), fg, COND, compute_perimeter=False, seed=1, dt_minutes=10,
                                spot_hook=hook)
        n.append(len(hook.events))
    assert n[1] > n[0]


def test_far_spot_outside_window_grows():
    """Regression (2026-10-13): a spot ignited outside the burned area's box used to be reset to
    unburned as an 'entering' cell and never spread. It must now grow."""
    fg, cl, cg = _grid()
    fired = []

    def hook(newly, t0, t1, params, arrival, cross_ros):
        if not fired and t1 >= 10.0:
            fired.append(t1)
            return np.array([[15, 55]])  # far NE of the ignition at (55, 15)
        return np.empty((0, 2), dtype=np.int64)

    arr = run_cellular_simulation(_cfg(cl, cg, hours=1.0), fg, COND, compute_perimeter=False, seed=1, dt_minutes=10,
                                  spot_hook=hook)[-1].arrival
    assert np.isfinite(arr[15, 55])
    nb = arr[10:21, 50:61]
    assert np.isfinite(nb).sum() > 5  # the spot spread to its neighbours


def test_albini_spotting_spots_spread():
    """FireSim's short-range spotting: with the seeding fix, spotting adds burned area."""
    fg, cl, cg = _grid(90)
    cond = SpreadConditions(wind_speed=50.0, wind_direction=225.0, ffmc=95.0, dmc=120.0, dc=800.0)
    cfg = _cfg(cl, cg, r=75, c=15, hours=2.0)
    a = run_cellular_simulation(cfg, fg, cond, compute_perimeter=False, seed=2, dt_minutes=10)
    b = run_cellular_simulation(cfg, fg, cond, compute_perimeter=False, seed=2, dt_minutes=10, enable_spotting=True)
    n_spots = sum(len(f.spot_fires or []) for f in b)
    if n_spots == 0:
        pytest.skip("no spot drawn at this seed")
    assert np.isfinite(b[-1].arrival).sum() > np.isfinite(a[-1].arrival).sum()


def test_options_validation():
    with pytest.raises(ValueError):
        LongRangeSpotter(H, lambda t: (10.0, 0.0), 90.0, long="bogus")
    with pytest.raises(ValueError):
        LongRangeSpotter(H, lambda t: (10.0, 0.0), 90.0, eta=-1.0)
    s = LongRangeSpotter(H, lambda t: (10.0, 0.0), 97.0)
    assert s.k_long > LongRangeSpotter(H, lambda t: (10.0, 0.0), 90.0).k_long  # dry factor (Cruz 2012 p.281)
    assert 0.0 < s.p_e <= 1.0
