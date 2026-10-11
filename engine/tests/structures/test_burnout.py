"""Burn-out of building units (spec §4.3, §5): a unit passes fire only while its design fire
burns (66 min at 150 kW/m², 70 min at 400 kW/m² after involvement)."""

from __future__ import annotations

import math

import numpy as np
import pytest
import shapely

from firesim.structures.embers import DESIGN_FIRES, EmberOptions, coupled_spread
from firesim.structures.hamada import crossing_time_min
from firesim.structures.spread import (
    SOURCE_STRUCTURE,
    WindPeriod,
    _cross,
    burnout_minutes,
    hamada_spread,
    spread_with_embers,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(53.5, -113.5)
EAST = WindPeriod(0.0, 64.08, 270.0)  # from the west, 17.8 m/s: spread toward +x
CALM = WindPeriod(0.0, 0.0, 270.0)


def square(x0, y0, side):
    lat, lng = FRAME.to_latlng(np.array([x0, x0 + side]), np.array([y0, y0 + side]))
    return shapely.box(float(lng[0]), float(lat[0]), float(lng[1]), float(lat[1]))


def row_units(n=5, side=10.0, gap=10.0, cutoff=15.0):
    return build_units([square(i * (side + gap), 0.0, side) for i in range(n)],
                       frame=FRAME, neighbour_cutoff_m=cutoff)


def first_lit(n):
    t = np.full(n, np.inf)
    t[0] = 0.0
    return t


def test_burnout_minutes_are_the_design_fire_durations():
    # PROCI24 p.3: 5 min growth + 1 min fully developed + 60 min decay
    assert burnout_minutes(150) == pytest.approx(66.0)
    assert burnout_minutes() == pytest.approx(66.0)
    # FSJ104686 p.2: 300 s + 3600 s + 300 s
    assert burnout_minutes(400) == pytest.approx(70.0)
    for k, df in DESIGN_FIRES.items():  # same curve as the ember stage
        assert burnout_minutes(k) * 60.0 == pytest.approx(df.duration_s)
        assert float(df.hrrpua(df.duration_s)) == 0.0  # HRR is zero from burn-out on
        assert float(df.hrrpua(df.duration_s - 1.0)) > 0.0
    with pytest.raises(ValueError):
        burnout_minutes(250)


def test_crossing_longer_than_burnout_never_arrives():
    # Two 10 m houses 40 m apart in calm air: tau = 74.2 min > 66 min
    units = build_units([square(0, 0, 10), square(50, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=45.0)
    tau = float(crossing_time_min(10.0, 40.0, 0.0, 1.0))
    assert 70.0 < tau < 75.0
    off = hamada_spread(units, first_lit(2), [CALM], duration_min=600)
    assert off.t_min[1] == pytest.approx(tau, rel=1e-6)
    for kw in (150, 400):
        on = hamada_spread(units, first_lit(2), [CALM], duration_min=600,
                           burnout_min=burnout_minutes(kw))
        assert math.isinf(on.t_min[1])


def test_crossing_shorter_than_burnout_is_unchanged():
    units = build_units([square(0, 0, 10), square(40, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=45.0)
    tau = float(crossing_time_min(10.0, 30.0, 0.0, 1.0))  # 58.9 min < 66 min
    on = hamada_spread(units, first_lit(2), [CALM], duration_min=600, burnout_min=66.0)
    assert on.t_min[1] == pytest.approx(tau, rel=1e-6)
    assert on.source[1] == SOURCE_STRUCTURE


def test_burnout_stops_the_chain_exactly_at_the_crossing_time():
    units = row_units(5)
    step = float(crossing_time_min(10.0, 10.0, 17.8, 1.0))  # 1.695 min
    short = hamada_spread(units, first_lit(5), [EAST], duration_min=600, burnout_min=step * 0.99)
    assert np.isfinite(short.t_min).sum() == 1  # only the seed
    longer = hamada_spread(units, first_lit(5), [EAST], duration_min=600, burnout_min=step * 1.01)
    np.testing.assert_allclose(longer.t_min, step * np.arange(5), rtol=1e-6)
    # arriving at the burn-out minute still counts (within floating-point rounding)
    # (the footprints' sizes are 10 m only to ~1e-6, so take the run's own crossing time)
    step_run = float(longer.t_min[1])
    exact = hamada_spread(units, first_lit(5), [EAST], duration_min=600,
                          burnout_min=step_run * (1 + 1e-12))
    assert np.isfinite(exact.t_min).all()


def test_burnout_ends_a_crossing_that_spans_weather_periods():
    # Calm, then 17.8 m/s toward the neighbour: progress accumulates over the periods; the
    # source burns out at 66 min whatever the wind does afterwards.
    units = build_units([square(0, 0, 10), square(50, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=45.0)
    early = [WindPeriod(0.0, 0.0, 270.0), WindPeriod(50.0, 64.08, 270.0)]
    late = [WindPeriod(0.0, 0.0, 270.0), WindPeriod(70.0, 64.08, 270.0)]
    r = hamada_spread(units, first_lit(2), early, duration_min=600, burnout_min=66.0)
    assert 50.0 < r.t_min[1] < 66.0
    r_off = hamada_spread(units, first_lit(2), late, duration_min=600)
    assert 70.0 < r_off.t_min[1] < 80.0
    r_on = hamada_spread(units, first_lit(2), late, duration_min=600, burnout_min=66.0)
    assert math.isinf(r_on.t_min[1])


def test_cross_respects_burnout_at_directly():
    a0, sep = np.array([10.0]), np.array([10.0])
    args = (np.array([1.0]), np.array([0.0]), np.array([True]), np.array([0.0]),
            np.array([17.8]), np.array([1.0]), np.array([0.0]), 1.0)
    step = float(crossing_time_min(10.0, 10.0, 17.8, 1.0))
    assert _cross(5.0, a0, sep, *args, 600.0)[0] == pytest.approx(5.0 + step)
    assert math.isinf(_cross(5.0, a0, sep, *args, 600.0, burnout_at=5.0 + step / 2)[0])


def test_counts_split_burning_and_burnt_out():
    units = row_units(5)
    step = float(crossing_time_min(10.0, 10.0, 17.8, 1.0))
    r = hamada_spread(units, first_lit(5), [EAST], duration_min=600, burnout_min=66.0)
    np.testing.assert_allclose(r.t_out_min, step * np.arange(5) + 66.0)
    for t in (0.0, 3.0, 65.9, 66.0, 66.0 + 2.5 * step, 200.0):
        c = r.counts_at(t)
        assert c["units_burning"] + c["units_burnt_out"] == c["units_involved"]
        assert c["units_burnt_out"] == int(np.sum(step * np.arange(5) + 66.0 <= t))
        assert c["burnout"] is True and c["burnout_min"] == 66.0
    assert r.counts_at(70.0)["units_burnt_out"] == 3  # involved at 0, 1.69, 3.39 min
    assert r.counts_at(200.0)["units_burning"] == 0
    off = hamada_spread(units, first_lit(5), [EAST], duration_min=600)
    c = off.counts_at(600.0)
    assert c["burnout"] is False and c["burnout_min"] is None
    assert c["units_burnt_out"] == 0 and c["units_burning"] == c["units_involved"] == 5
    assert np.isinf(off.t_out_min).all()


def test_units_not_involved_have_no_burnout_time():
    units = build_units([square(0, 0, 10), square(200, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=30.0)
    r = hamada_spread(units, first_lit(2), [EAST], duration_min=600, burnout_min=66.0)
    assert r.t_out_min[0] == 66.0 and math.isinf(r.t_out_min[1])
    assert r.counts_at(600.0)["units_burnt_out"] == 1


# ------------------------------------------------------------------- embers and burn-out


def _ember_pair(gap=8.0):
    """Two 10 m houses on the wind axis; wind 74.1 km/h (10 m) = 17.9 m/s at 6.1 m."""
    units = build_units([square(0, 0, 10), square(10 + gap, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=5.0)  # no Hamada neighbours: embers only
    return units, [WindPeriod(0.0, 17.9 * 1.15 * 3.6, 270.0)]


def test_ember_emission_stops_when_the_design_fire_ends():
    units, wind = _ember_pair()
    opts = EmberOptions(gr_structure=0.01, from_wildland=False, hamada=False)  # no ignition
    pools = {}
    for dur in (30.0, 66.0, 120.0, 400.0):
        r = coupled_spread(units, first_lit(2), wind, duration_min=dur, fb=1.0, options=opts,
                           cross=_cross)
        assert math.isinf(r.t_min[1])
        pools[dur] = r.embers_received[1]
    assert 0 < pools[30.0] < pools[66.0]
    assert pools[120.0] == pytest.approx(pools[66.0], rel=1e-9)
    assert pools[400.0] == pytest.approx(pools[66.0], rel=1e-9)


def test_burnt_out_unit_passes_no_fire_with_embers_on():
    # Hamada with embers (inert, GR' tiny): the calm 40 m crossing (74 min) is stopped
    units = build_units([square(0, 0, 10), square(50, 0, 10)], frame=FRAME,
                        neighbour_cutoff_m=45.0)
    opts = EmberOptions(gr_structure=0.01, from_wildland=False)
    off = spread_with_embers(units, first_lit(2), [CALM], duration_min=600, embers=opts)
    assert off.t_min[1] == pytest.approx(float(crossing_time_min(10.0, 40.0, 0.0, 1.0)), rel=1e-6)
    on = spread_with_embers(units, first_lit(2), [CALM], duration_min=600, embers=opts,
                            burnout_min=66.0)
    assert math.isinf(on.t_min[1])
    assert on.counts_at(600.0)["units_burnt_out"] == 1
    with pytest.raises(ValueError):
        spread_with_embers(units, first_lit(2), [CALM], duration_min=600, embers=opts,
                           burnout_min=70.0)  # not the 150 kW/m² ember design fire
    with pytest.raises(ValueError):
        hamada_spread(units, first_lit(2), [CALM], duration_min=600, burnout_min=0.0)


def test_embers_plus_hamada_with_burnout_equals_hamada_with_burnout_when_embers_are_inert():
    units = row_units(6, gap=12.0, cutoff=30.0)
    wind = [WindPeriod(0.0, 15.0, 225.0)]
    ham = hamada_spread(units, first_lit(6), wind, duration_min=360, burnout_min=66.0)
    emb = spread_with_embers(units, first_lit(6), wind, duration_min=360, burnout_min=66.0,
                             embers=EmberOptions(gr_structure=0.01, from_wildland=False))
    np.testing.assert_allclose(emb.t_min, ham.t_min)
