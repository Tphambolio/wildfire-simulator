"""Zero-burn runs and failing ensemble members (production bug of 2026-10-10).

An Edmonton ensemble failed with ``IndexError: index 0 is out of bounds`` in ``_window``:
members whose ignition cell could not carry fire (D-2 below BUI 80 after the DMC/DC
perturbation) had FBP's 1e-6 m/min "no spread" floor scaled by a ROS multiplier above 1. The
ignition hold accepted that rate (> 1e-6) but the starting ellipse rejected it (> 1e-5), so
the run had a starting fire with no burned cell. docs/PROJECT_RECORD.md has the record.
"""

import math
from dataclasses import replace

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread import ensemble as ens_mod
from firesim.spread.cellular import CARRY_ROS, _window, run_cellular_simulation
from firesim.spread.ensemble import EnsembleConfig, EnsembleFailedError, run_ensemble
from firesim.spread.huygens import FuelGrid, SpreadConditions, fbp_for_conditions
from firesim.types import SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5


def _grid(n=40, cell=25.0, fuel=FuelType.D2):
    dlat = n * cell / 111320
    dlng = n * cell / (111320 * math.cos(math.radians(LAT0)))
    return FuelGrid([[fuel] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                    LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


def _run(grid, cond, hours=1.0):
    return run_cellular_simulation(
        dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=hours), grid, cond,
        snapshot_interval_minutes=30.0, compute_perimeter=True,
    )


def _assert_zero_burn(frames):
    assert frames, "a zero-burn run still returns frames"
    for f in frames:
        assert f.area_ha == 0.0 and f.total_burned == 0 and f.burned_cells == []
    assert not np.isfinite(frames[-1].arrival).any()
    assert len(frames[-1].emitters.x) == 0


# D-2 below BUI 80 has no spread in FBP (cffdrs floors the ROS at 1e-6 m/min)
D2_NO_SPREAD = SpreadConditions(wind_speed=20, wind_direction=270, ffmc=90, dmc=20, dc=100)


@pytest.mark.parametrize("multiplier", [1.0, 2.0, 6.0, 20.0])
def test_ignition_that_cannot_spread_burns_nothing_without_error(multiplier):
    """The crash: a ROS multiplier above 1 on FBP's no-spread floor raised IndexError (2.0,
    6.0); a large one (20) burned the ignition cell of a fuel that does not spread."""
    frames = _run(_grid(), replace(D2_NO_SPREAD, ros_multiplier=multiplier))
    _assert_zero_burn(frames)


def test_rate_between_the_old_thresholds_does_not_leave_an_empty_fire():
    """A real (not floored) head rate just at the carry threshold: the ignition hold and the
    starting ellipse use one threshold, so the run is either a fire or nothing, never a
    starting fire without cells."""
    cond = SpreadConditions(wind_speed=20, wind_direction=270, ffmc=90, dmc=40, dc=300)
    head = fbp_for_conditions(cond, FuelType.C2).ros_final
    for target in (0.5 * CARRY_ROS, 0.99 * CARRY_ROS, 5e-6):
        frames = _run(_grid(fuel=FuelType.C2), replace(cond, ros_multiplier=target / head))
        _assert_zero_burn(frames)
    frames = _run(_grid(fuel=FuelType.C2), replace(cond, ros_multiplier=2.0 * CARRY_ROS / head))
    assert frames[-1].total_burned >= 1  # above the threshold the ignition cell burns


def test_window_of_an_empty_mask_is_empty():
    win = _window(np.zeros((5, 7), dtype=bool), 3)
    assert np.zeros((5, 7))[win].size == 0
    m = np.zeros((5, 7), dtype=bool)
    m[2, 3] = True
    assert _window(m, 1) == (slice(1, 4), slice(2, 5))


def _config(hours=1.0, **kw):
    return SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), hours,
                            ffmc=90.0, **kw)


def test_ensemble_with_zero_burn_members_completes_and_counts_them():
    """Production case: ignition in D-2 with BUI ~87 (above 80), so the deterministic run
    spreads, but the DMC/DC perturbation takes some members below BUI 80 (no spread). Those
    members burn nothing; the ensemble completes and counts them."""
    g = _grid()
    res = run_ensemble(_config(dmc=60.0, dc=400.0), g, EnsembleConfig(n_members=30, seed=1))
    assert res.failed == [] and len(res.members) == 30
    areas = [m["area_ha"] for m in res.members]
    zero = sum(a == 0.0 for a in areas)
    assert 0 < zero < 30
    # the ignition cell's burn probability is the share of members that burned anything
    r = int((g.lat_max - LAT0) / ((g.lat_max - g.lat_min) / g.rows))
    c = int((LNG0 - g.lng_min) / ((g.lng_max - g.lng_min) / g.cols))
    assert res.burn_probability[r, c] == pytest.approx((30 - zero) / 30)


def _fake_members(outcomes, rows=4, cols=4):
    """Stand-in for ``_run_member``: per member an arrival raster, None, or an exception."""
    it = iter(outcomes)

    def run(*args, **kwargs):
        o = next(it)
        if isinstance(o, Exception):
            raise o
        a = np.full((rows, cols), np.inf)
        if o is not None:
            a[1, 1] = o
        return a
    return run


def test_zero_burn_members_count_in_percentiles_and_probability(monkeypatch):
    """6 of 10 members reach cell (1, 1) at 10 min, 4 burn nothing: probability 0.6, P10 and
    P50 reached (the 1st and 5th earliest), P90 not (the 9th earliest is a zero-burn member).
    Dropping the zero-burn members would give probability 1 and a P90 of 10 min."""
    g = _grid(n=4)
    monkeypatch.setattr(ens_mod, "_run_member", _fake_members([10.0] * 6 + [np.inf] * 4))
    res = run_ensemble(_config(), g, EnsembleConfig(n_members=10))
    assert res.burn_probability[1, 1] == pytest.approx(0.6)
    assert res.arrival[10][1, 1] == 10 and res.arrival[50][1, 1] == 10
    assert res.arrival[90][1, 1] == -1
    assert sorted(m["area_ha"] for m in res.members)[:4] == [0.0] * 4


def test_failing_member_is_left_out_and_reported(monkeypatch, caplog):
    g = _grid(n=4)
    outcomes = [10.0, RuntimeError("boom"), 20.0, 30.0, None, 40.0]
    monkeypatch.setattr(ens_mod, "_run_member", _fake_members(outcomes))
    with caplog.at_level("ERROR"):
        res = run_ensemble(_config(), g, EnsembleConfig(n_members=6, seed=7))
    assert res.failed == [{"member": 1, "error": "RuntimeError: boom"}]
    assert [m["member"] for m in res.members] == [0, 2, 3, 4, 5]
    assert res.n_requested == 6
    # statistics over the 5 finished members (one of them, member 4, burned nothing)
    assert res.burn_probability[1, 1] == pytest.approx(4 / 5)
    assert res.arrival[50][1, 1] == 30  # 3rd earliest of 10, 20, 30, 40, never
    assert "member 1 (seed 7) failed" in caplog.text


def test_ensemble_fails_when_fewer_than_half_the_members_finish(monkeypatch):
    g = _grid(n=4)
    outcomes = [10.0, 10.0] + [ValueError("x")] * 3
    monkeypatch.setattr(ens_mod, "_run_member", _fake_members(outcomes))
    with pytest.raises(EnsembleFailedError, match="3 of 5 members failed"):
        run_ensemble(_config(), g, EnsembleConfig(n_members=5))
    # exactly half finishing is enough
    monkeypatch.setattr(ens_mod, "_run_member", _fake_members([10.0] * 3 + [ValueError("x")] * 3))
    res = run_ensemble(_config(), g, EnsembleConfig(n_members=6))
    assert len(res.members) == 3 and len(res.failed) == 3
