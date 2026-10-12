"""Long-range embers v2 (spec §6.4): urban ember source, per-ember building ignition after
Himoto & Tanaka (2008), parameter draws, the incremental structure stage and the coupled
wildland → town → town iteration."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from firesim.structures import long_range as LR
from firesim.structures.embers import DESIGN_FIRES
from firesim.structures.spread import (
    SOURCE_EMBER,
    LongRangeResult,
    WindPeriod,
    burnout_minutes,
    hamada_spread,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(34.0, -118.0)
MX = FRAME.m_per_deg_lng


def _grid(nx, ny, spacing, size=12.0):
    """Square houses on a lattice (local metres → lng/lat), x east, y north."""
    out = []
    for i in range(nx):
        for j in range(ny):
            x, y = i * spacing, j * spacing
            la, ln = FRAME.to_latlng(np.array([x - size / 2, x + size / 2]),
                                     np.array([y - size / 2, y + size / 2]))
            out.append(shapely.box(float(ln[0]), float(la[0]), float(ln[1]), float(la[1])))
    return build_units(out, neighbour_cutoff_m=30.0, frame=FRAME)


def test_published_values():
    # HT08 Table 3 α_B·β_B = 1e-6 per kJ, Qin GR' = 10 per MJ = 0.01 per kJ → α = 1e-4
    assert LR.BETA_B_PER_KJ == pytest.approx(0.01)
    assert LR.ALPHA_PER_EMBER == pytest.approx(1e-4)
    assert LR.ALPHA_BETA_RANGE == (0.0, 2e-6)
    assert LR.N_PER_KG_RANGE == (250.0, 640.0)
    o = LR.LongRangeOptions()
    assert o.model == "v2" and o.urban_source and o.param_draws and o.field_bin_min == 10.0
    assert o.receptive == "bed" and LR.BED_AREA_M2 == pytest.approx(1.44)
    # dry mulch, Suzuki/Manzello 2015: 7.36 brands m⁻² s⁻¹ × 1.44 m² × 172 s to sustained flaming
    assert LR.P_BED_MULCH == pytest.approx(1.0 / (7.36 * 1.44 * 172.0), rel=0.01)
    assert LR.MASS_FACTOR == pytest.approx(0.2 / 0.05)
    assert LR.LongRangeOptions(model="v1").field_bin_min == 30.0
    with pytest.raises(ValueError):
        LR.LongRangeOptions(model="v3")


def test_member_draws_cover_the_published_ranges():
    o = LR.LongRangeOptions()
    rng = np.random.default_rng(0)
    d = [LR.member_draw(o, rng, 50) for _ in range(500)]
    q = np.concatenate([x.q for x in d])
    pb = np.array([x.p_b for x in d])
    n = np.array([x.n_scale for x in d]) * o.n_per_kg
    assert q.min() >= 4 * 2e-4 * 0.1 * 0.999 and q.max() <= 4 * 7e-3 * 1.0 * 1.001
    assert pb.min() >= 0.1 and pb.max() <= 1.0
    assert n.min() >= 250 and n.max() <= 640
    # p_bed varies between buildings within a member; p_b is one value per member
    assert np.unique(d[0].q).size == 50
    ht = LR.LongRangeOptions(receptive="footprint")
    a = np.array([LR.member_draw(ht, rng, 1).alpha for _ in range(2000)])
    assert a.min() >= 0 and a.max() <= 2e-4 and a.mean() == pytest.approx(1e-4, rel=0.05)
    fixed = LR.member_draw(LR.LongRangeOptions(param_draws=False), rng, 3)
    assert np.allclose(fixed.q, 4 * LR.P_BED_MULCH * 0.5) and fixed.n_scale == 1.0
    fixed = LR.member_draw(LR.LongRangeOptions(param_draws=False, receptive="footprint", alpha=3e-5), rng, 3)
    assert np.allclose(fixed.q, 3e-5)


def test_receptive_areas():
    u = _grid(2, 1, 50.0)
    assert np.allclose(LR.receptive_m2(u, LR.LongRangeOptions()), 1.44)
    assert np.allclose(LR.receptive_m2(u, LR.LongRangeOptions(receptive="footprint")), u.area_m2)
    assert np.all(LR.receptive_m2(u, LR.LongRangeOptions(receptive="ring")) > u.area_m2)


def test_weighted_hrr_and_emission_total():
    df = DESIGN_FIRES[150]
    q = LR.weighted_hrrpua(df)
    assert 75.0 < q < 150.0  # between the decay-phase mean and the peak
    u = _grid(1, 1, 50.0)
    urb = LR.UrbanSource.build(u, [(0.0, 36.0, 270.0)], LR.LongRangeOptions())
    # whole design fire: GR' (10 per MJ) × ∫HRR dt (Qin 2025 eq 3.1)
    total = urb.emitted(np.array([0.0]), 0.0, df.duration_s / 60.0 + 1)[0]
    e_mj = float(df.energy_per_area(df.duration_s)) * u.area_m2[0] / 1000.0
    assert total == pytest.approx(10.0 * e_mj, rel=1e-9)
    # split over steps adds up
    parts = sum(urb.emitted(np.array([0.0]), a, a + 10.0)[0] for a in np.arange(0.0, 80.0, 10.0))
    assert parts == pytest.approx(total, rel=1e-9)
    assert urb.emitted(np.array([np.inf]), 0.0, 10.0)[0] == 0.0


def test_urban_embers_land_downwind_not_on_the_source():
    u = _grid(9, 9, 20.0)  # dense 9 × 9 block, wind from the west (toward east)
    urb = LR.UrbanSource.build(u, [(0.0, 13 * 3.6, 270.0)], LR.LongRangeOptions())
    src = int(np.argmin(np.hypot(u.x - 80.0, u.y - 80.0)))  # the centre house
    cnt = np.zeros(len(u))
    cnt[src] = 1.0e5
    dens = urb.density_at_units(cnt, 0)
    assert dens[src] == 0.0  # nothing lands on the emitting footprint
    east = u.x > u.x[src] + 1
    west = u.x < u.x[src] - 1
    assert dens[east].sum() > 0 and dens[west].sum() == 0.0
    # the nearest downwind neighbour gets the most
    on_axis = east & (np.abs(u.y - u.y[src]) < 1)
    order = np.argsort(u.x[on_axis])
    assert np.all(np.diff(dens[on_axis][order]) <= 1e-12)


def test_incremental_hamada_equals_hamada_spread():
    u = _grid(12, 6, 22.0)
    w = [WindPeriod(0.0, 40.0, 250.0), WindPeriod(60.0, 25.0, 300.0)]
    tf = np.full(len(u), np.inf)
    tf[:3] = [0.0, 5.0, 12.0]
    ref = hamada_spread(u, tf, w, duration_min=240.0, burnout_min=burnout_minutes(150))
    st = LR.IncrementalHamada(u, w, duration_min=240.0, fb=1.0, burnout_min=burnout_minutes(150))
    st.reset(tf)
    for e in np.arange(10.0, 241.0, 10.0):
        st.advance(e)
    t, s = st.final()
    assert np.array_equal(np.isfinite(t), np.isfinite(ref.t_min))
    assert np.allclose(t[np.isfinite(t)], ref.t_min[np.isfinite(t)])
    assert np.array_equal(s, ref.source)


def test_no_embers_reduces_to_hamada():
    u = _grid(10, 3, 22.0)
    w = [WindPeriod(0.0, 40.0, 270.0)]
    tf = np.full(len(u), np.inf)
    tf[0] = 0.0
    edges = np.arange(0.0, 121.0, 10.0)
    o = LR.LongRangeOptions(param_draws=False, alpha=0.0, receptive="footprint")
    urb = LR.UrbanSource.build(u, [(0.0, 40.0, 270.0)], o)
    st = LR.IncrementalHamada(u, w, duration_min=120.0, fb=1.0, burnout_min=burnout_minutes(150))
    t, s, lr, landed, _ = LR.run_member_v2(u, tf, None, edges, o, st, 120.0,
                                           np.random.default_rng(1), urb, [(0.0, 40.0, 270.0)])
    ref = hamada_spread(u, tf, w, duration_min=120.0, burnout_min=burnout_minutes(150))
    assert np.allclose(np.where(np.isfinite(t), t, -1), np.where(np.isfinite(ref.t_min), ref.t_min, -1))
    assert not lr.any() and landed.sum() > 0  # embers landed, none ignited (α = 0)


def test_wildland_embers_ignite_with_delay():
    u = _grid(3, 1, 200.0)  # isolated houses, no Hamada links
    edges = np.array([0.0, 10.0, 20.0])
    landed = np.zeros((2, len(u)))
    landed[0, 1] = 1.0e9  # certain ignition of house 1 in the first bin
    o = LR.LongRangeOptions(param_draws=False, alpha=1e-4, urban_source=False, receptive="footprint")
    st = LR.IncrementalHamada(u, [WindPeriod(0.0, 30.0, 270.0)], duration_min=20.0, fb=1.0)
    t, s, lr, _, _ = LR.run_member_v2(u, np.full(len(u), np.inf), landed, edges, o, st, 20.0,
                                      np.random.default_rng(0), None, [(0.0, 30.0, 270.0)])
    assert lr.tolist() == [False, True, False]
    assert s[1] == SOURCE_EMBER
    assert t[1] == pytest.approx(o.delay_s / 60.0, abs=1e-3)  # crossed at once + 42 s + 300 s


def test_urban_source_carries_fire_downwind_beyond_the_hamada_cutoff():
    # a row of houses 40 m apart (28 m gaps, Hamada at 30 m still links them) is too easy:
    # use 45 m (33 m gaps): Hamada cannot cross, only embers from burning houses can
    u = _grid(8, 1, 45.0)
    wind = [(0.0, 15 * 3.6, 270.0)]
    w = [WindPeriod(0.0, 15 * 3.6, 270.0)]
    tf = np.full(len(u), np.inf)
    tf[np.argmin(u.x)] = 0.0  # west end burns first
    edges = np.arange(0.0, 361.0, 10.0)
    res = {}
    for urban_on in (False, True):
        o = LR.LongRangeOptions(param_draws=False, alpha=2e-4, urban_source=urban_on,
                                receptive="footprint")
        urb = LR.UrbanSource.build(u, wind, o)
        st = LR.IncrementalHamada(u, w, duration_min=360.0, fb=1.0, burnout_min=burnout_minutes(150))
        n_inv = []
        for m in range(10):
            t, *_ = LR.run_member_v2(u, tf, None, edges, o, st, 360.0, np.random.default_rng([3, m]),
                                     urb, wind)
            n_inv.append(int(np.isfinite(t).sum()))
        res[urban_on] = np.mean(n_inv)
    assert res[False] == 1.0  # only the seed: no wildland embers, no Hamada link
    assert res[True] > 1.0  # town → town by embers


def test_grid_run_v2_and_v1_both_work():
    from .test_long_range import _run  # the crowning strip + houses fixture

    v2 = _run(model="v2", members=4)
    v1 = _run(model="v1", members=4)
    assert isinstance(v2, LongRangeResult) and isinstance(v1, LongRangeResult)
    assert v2.params["long_range_model"] == "v2" and v1.params["long_range_model"] == "v1"
    assert v2.params["long_range_urban_source"] is True
    c = v2.counts_at(120.0)
    assert c["long_range"]["members"] == 4
    assert all(0 < e["p_involved"] <= 1 for e in v2.involved_detail())
    a, b = _run(model="v2", members=3), _run(model="v2", members=3)
    assert np.array_equal(a.t_members, b.t_members)  # seeded
