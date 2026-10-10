"""Ember ignition of building units (spec §6): equations against the papers' check values,
the FSJ104686 one-dimensional benchmark, repeatability, mechanism accounting, the wildland
(Sardoy) coupling and the reachable-box build with embers.

Check values (pages as printed):
- FSJ104686 = Qin et al. (2026), Fire Safety J. 162: 104686.
- Qin25 = Qin (2025), PhD dissertation, University of Maryland.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import shapely

from firesim.structures import embers as E
from firesim.structures.spread import (
    SOURCE_EMBER,
    StructureSpreadSkipped,
    WindPeriod,
    building_cell_contact_times,
    hamada_spread,
    spread_with_embers,
    structure_spread_for_grid_run,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(53.5, -113.5)
U6_BENCH = 17.9  # m/s at 6.1 m (40 mph), FSJ104686 p.3
U10_BENCH = U6_BENCH / E.U10_TO_U6


def _box(x0, y0, w, h):
    lat, lng = FRAME.to_latlng(np.array([x0, x0 + w]), np.array([y0, y0 + h]))
    return shapely.box(float(lng[0]), float(lat[0]), float(lng[1]), float(lat[1]))


def _row(n=12, ss=10.0, ssd=10.0):
    """FSJ104686 §3 benchmark: identical squares SS, separations SSD, along +x."""
    return build_units([_box((ss + ssd) * i, 0.0, ss, ss) for i in range(n)],
                       neighbour_cutoff_m=30.0, frame=FRAME)


def _bench_options(**kw):
    o = dict(design_fire_kw_m2=400, lateral=False, hamada=False, from_wildland=False)
    o.update(kw)
    return E.EmberOptions(**o)


WEST = 270.0  # wind FROM the west: embers carried to +x


# ------------------------------------------------------------------ equations vs check values


@pytest.mark.parametrize("hrr_mw,xmax", [(10, 65.0), (40, 84.0), (160, 109.0)])
def test_himoto_xmax_matches_fsj104686_p3(hrr_mw, xmax):
    """X_max = 65 / 84 / 109 m for 10 / 40 / 160 MW in a 17.9 m/s wind (FSJ104686 p.3, D = 10 m).
    Reproduced with B* at the 10 m wind (1.15 x 17.9 m/s, spec C17); 62 / 80 / 103 m at 17.9."""
    mu, sg = E.himoto_lognormal(hrr_mw * 1000.0, U10_BENCH, 10.0)
    assert float(E.x_max(mu, sg)) == pytest.approx(xmax, abs=0.6)
    mu6, sg6 = E.himoto_lognormal(hrr_mw * 1000.0, U6_BENCH, 10.0)
    assert float(E.x_max(mu6, sg6)) < xmax - 3.0


def test_psi_check_value_fsj104686_p5():
    """17.9 m/s -> v_air ≈ 1.1 m/s at 2 m -> ψ* ≈ 0.059 g/cm² -> ≈ 2.95e5 embers on 10 x 10 m."""
    va = float(E.v_air(U6_BENCH))
    assert va == pytest.approx(1.1, abs=0.05)
    assert float(E.psi_critical(va)) == pytest.approx(0.059, abs=0.001)
    assert float(E.critical_embers(100.0, va)) == pytest.approx(2.95e5, rel=0.015)
    # Qin25 eq 6.6 (p.157): v_air ≈ 1.14 m/s (factor 0.064), N_crit ≈ 2,928.9 pcs/m²
    assert float(E.critical_embers(1.0, 1.14)) == pytest.approx(2928.9, rel=0.01)


def test_psi_no_ignition_outside_the_wind_window():
    """ψ* has asymptotes at v_min and v_max (FSJ104686 eq 1, p.4): no ignition outside."""
    assert np.isinf(E.psi_critical(E.V_MIN))
    assert np.isinf(E.psi_critical(E.V_MAX))
    assert np.isinf(E.psi_critical(5.0))
    assert np.isfinite(E.psi_critical(0.0))
    # U-shape: the minimum is between the asymptotes
    v = np.linspace(0.0, 4.0, 401)
    assert 1.5 < v[np.argmin(E.psi_critical(v))] < 2.5


def test_sardoy_check_value_qin25_p124():
    """Grass case: 6.71 m/s at 6.1 m, I = 3,189 kW/m -> μ = 2.18, σ = 1.23, X_kmax = 150 m
    (10 m bins; Qin25 p.124)."""
    mu, sg = E.sardoy_lognormal(3.189, 6.71)
    assert float(mu) == pytest.approx(2.18, abs=0.005)
    assert float(sg) == pytest.approx(1.23, abs=0.005)
    assert round(float(E.x_max(mu, sg)) / 10.0) * 10.0 == 150.0


def test_sardoy_froude_branches_continuity_of_regimes():
    """Fr = U / sqrt(g Lc) <= 1 uses eqs 4.3-4.4, > 1 eqs 4.5-4.6 (Qin25 p.73)."""
    ib = 3.189
    lc = (1000 * ib / (1.1 * 1.0 * 300 * math.sqrt(9.81))) ** (2 / 3)
    u_crit = math.sqrt(9.81 * lc)
    lo = E.sardoy_lognormal(ib, u_crit * 0.99)
    hi = E.sardoy_lognormal(ib, u_crit * 1.01)
    assert float(lo[0]) == pytest.approx(1.47 * ib ** 0.54 * (u_crit * 0.99) ** -0.55 + 1.14)
    assert float(hi[0]) == pytest.approx(1.32 * ib ** 0.26 * (u_crit * 1.01) ** 0.11 - 0.02)


def test_design_fires():
    """400 kW/m²: 300 / 3600 / 300 s and 1.56 GJ/m² fire load (FSJ104686 p.2); emitted embers
    1.56e6 for 100 m² at 10 pcs/(MW·s) (p.5). 150 kW/m²: 5 / 1 / 60 min (PROCI24 p.3)."""
    d4 = E.DESIGN_FIRES[400]
    assert d4.duration_s == 4200.0
    assert float(d4.energy_per_area(1e9)) == pytest.approx(1.56e6)  # kJ/m²
    emitted = E.GR_STRUCTURE * float(d4.energy_per_area(4200.0)) * 100.0 / 1000.0
    assert emitted == pytest.approx(1.56e6)
    assert float(d4.hrrpua(150.0)) == pytest.approx(200.0)
    assert float(d4.hrrpua(3950.0)) == pytest.approx(400.0 * (1 - 50.0 / 300.0))
    assert float(d4.hrrpua(4200.0)) == 0.0 and float(d4.hrrpua(-1.0)) == 0.0
    d1 = E.DESIGN_FIRES[150]
    assert (d1.grow_s, d1.full_s, d1.decay_s) == (300.0, 60.0, 3600.0)
    assert float(d1.energy_per_area(1e9)) == pytest.approx(150 * (150 + 60 + 1800))
    # the integral matches a fine Riemann sum
    tt = np.linspace(0, d1.duration_s, 200001)
    assert float(d1.energy_per_area(2000.0)) == pytest.approx(
        np.trapezoid(d1.hrrpua(tt[tt <= 2000.0]), tt[tt <= 2000.0]), rel=1e-4)
    with pytest.raises(ValueError):
        E.EmberOptions(design_fire_kw_m2=250).design_fire


def test_landing_fraction_one_third_on_the_next_structure_fsj104686_p5():
    """33 % of the embers of a 40 MW structure fall on the next one downwind (SS = SSD = 10 m):
    flight distances 10-20 m from the source's downwind edge (Qin25 §6.3.1 item 3, p.170)."""
    mu, sg = E.himoto_lognormal(40e3, U10_BENCH, 10.0)
    f = float(E.landing_fraction(10.0, 20.0, -5.0, 5.0, mu, sg, None, 1.0))
    assert f == pytest.approx(0.33, abs=0.005)


def test_landing_fraction_crosswind_and_truncation():
    mu, sg = E.himoto_lognormal(40e3, U10_BENCH, 10.0)
    xm = float(E.x_max(mu, sg))
    # all of the truncated PDF lands somewhere in [0, X_max] x (-inf, inf)
    assert float(E.landing_fraction(0.0, xm, -1e3, 1e3, mu, sg, 9.2, 1.0)) == pytest.approx(1.0)
    assert float(E.landing_fraction(xm, xm + 50.0, -1e3, 1e3, mu, sg, 9.2, 1.0)) == 0.0
    # upwind (behind the source's downwind edge) gets nothing
    assert float(E.landing_fraction(-20.0, -10.0, -5, 5, mu, sg, 9.2, 1.0)) == 0.0
    # crosswind: symmetric, and less off the axis
    on = float(E.landing_fraction(10, 20, -5, 5, mu, sg, 9.2, 1.0))
    left = float(E.landing_fraction(10, 20, 5, 15, mu, sg, 9.2, 1.0))
    right = float(E.landing_fraction(10, 20, -15, -5, mu, sg, 9.2, 1.0))
    assert left == pytest.approx(right) and 0 < left < on
    # HT08 σ_Y = 0.92 D: |Y| < 5 m holds 2Φ(5/9.2) - 1 of the (untruncated) normal
    assert on / float(E.landing_fraction(10, 20, -1e3, 1e3, mu, sg, None, 1.0)) == pytest.approx(
        0.413, abs=0.01)


def test_wind_conversions():
    assert float(E.u10_to_u6(11.5)) == pytest.approx(10.0)  # Andrews 2009 p.58
    assert float(E.v_air(17.88)) == pytest.approx(1.144, abs=1e-3)  # Qin25 p.157


# ------------------------------------------------------------------ the 1-D benchmark


def test_benchmark_second_structure_ignition_time_fsj104686_p5():
    """τ = TOA + t_ign,small + t_ign,large ≈ 1.1 + 2404 + 300 = 2705 s (FSJ104686 p.5)."""
    units = _row()
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    r = spread_with_embers(units, tf, [WindPeriod(0.0, U10_BENCH * 3.6, WEST)],
                           duration_min=600.0, embers=_bench_options())
    assert r.t_min[1] * 60.0 == pytest.approx(2705.0, rel=0.01)
    assert r.source[1] == SOURCE_EMBER and r.parent[1] == 0
    # strictly downwind order, all by embers
    assert np.all(np.diff(r.t_min[np.isfinite(r.t_min)]) > 0)
    assert np.all(r.source[1:][np.isfinite(r.t_min[1:])] == SOURCE_EMBER)
    # FSJ104686 p.6: time-averaged ROS ≈ 0.01 m/s (about 5 h for 200 m)
    fin = np.nonzero(np.isfinite(r.t_min))[0]
    ros = (units.x[fin[-1]] - units.x[fin[1]]) / ((r.t_min[fin[-1]] - r.t_min[fin[1]]) * 60.0)
    assert 0.007 < ros < 0.013


def test_benchmark_is_converged_in_the_accumulation_step():
    units = _row(6)
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    w = [WindPeriod(0.0, U10_BENCH * 3.6, WEST)]
    a = spread_with_embers(units, tf, w, duration_min=400.0, embers=_bench_options(dt_min=0.5))
    b = spread_with_embers(units, tf, w, duration_min=400.0, embers=_bench_options(dt_min=0.1))
    fin = np.isfinite(a.t_min)
    assert np.array_equal(fin, np.isfinite(b.t_min)) and fin.sum() >= 3
    np.testing.assert_allclose(a.t_min[fin], b.t_min[fin], atol=0.05)  # minutes


def test_no_ember_spread_at_zero_wind_or_wide_gaps():
    units = _row(4)
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    r0 = spread_with_embers(units, tf, [WindPeriod(0.0, 0.0, WEST)], duration_min=600.0,
                            embers=_bench_options())
    assert np.isfinite(r0.t_min).sum() == 1
    wide = _row(4, ssd=40.0)  # FSJ104686 Fig. 6a: no spread at large SSD with GR' = 10
    r1 = spread_with_embers(wide, tf, [WindPeriod(0.0, U10_BENCH * 3.6, WEST)],
                            duration_min=600.0, embers=_bench_options())
    assert np.isfinite(r1.t_min).sum() == 1


def test_upwind_structure_gets_no_embers():
    units = _row(3)
    tf = np.full(len(units), np.inf)
    tf[2] = 0.0  # the downwind-most burns; wind still from the west
    r = spread_with_embers(units, tf, [WindPeriod(0.0, U10_BENCH * 3.6, WEST)],
                           duration_min=600.0, embers=_bench_options())
    assert np.isfinite(r.t_min).sum() == 1


def test_lower_generation_rate_and_smaller_design_fire_are_slower():
    units = _row(4)
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    w = [WindPeriod(0.0, U10_BENCH * 3.6, WEST)]
    base = spread_with_embers(units, tf, w, duration_min=900.0, embers=_bench_options())
    low = spread_with_embers(units, tf, w, duration_min=900.0,
                             embers=_bench_options(gr_structure=E.GR_STRUCTURE_REFERENCE))
    small = spread_with_embers(units, tf, w, duration_min=900.0,
                               embers=_bench_options(design_fire_kw_m2=150))
    assert np.isfinite(base.t_min[1])
    assert not (low.t_min[1] < base.t_min[1])
    assert not (small.t_min[1] < base.t_min[1])


# ------------------------------------------------------------------ coupling with Hamada


def _block(n=8, spacing=16.0, side=10.0):
    return build_units([_box(spacing * i, spacing * j, side, side)
                        for i in range(n) for j in range(n)], neighbour_cutoff_m=30.0,
                       frame=FRAME)


def test_embers_off_is_exactly_hamada():
    units = _block()
    tf = np.full(len(units), np.inf)
    tf[:3] = 0.0
    w = [WindPeriod(0.0, 40.0, WEST)]
    a = spread_with_embers(units, tf, w, duration_min=240.0)
    b = hamada_spread(units, tf, w, duration_min=240.0)
    np.testing.assert_array_equal(a.t_min, b.t_min)
    assert "units_ember" not in a.counts_at(240.0)


def test_embers_and_hamada_mechanism_accounting_and_repeatability():
    units = _block(n=10, spacing=14.0)
    tf = np.full(len(units), np.inf)
    tf[:2] = 0.0
    w = [WindPeriod(0.0, 75.0, WEST), WindPeriod(90.0, 75.0, 250.0)]
    opts = E.EmberOptions(design_fire_kw_m2=400)
    r1 = spread_with_embers(units, tf, w, duration_min=360.0, embers=opts)
    r2 = spread_with_embers(units, tf, w, duration_min=360.0, embers=opts)
    np.testing.assert_array_equal(r1.t_min, r2.t_min)
    np.testing.assert_array_equal(r1.source, r2.source)
    for tm in (60.0, 180.0, 360.0):
        c = r1.counts_at(tm)
        assert c["units_involved"] == (c["units_front_contact"] + c["units_structure_to_structure"]
                                       + c["units_ember"]) == int(np.sum(r1.t_min <= tm))
        assert c["embers"] is True and c["design_fire_kw_m2"] == 400
    # Never later than Hamada alone: embers only add ways to be reached
    h = hamada_spread(units, tf, w, duration_min=360.0)
    fin = np.isfinite(h.t_min)
    assert np.all(r1.t_min[fin] <= h.t_min[fin] + 1e-9)


def test_ember_parents_burned_long_enough_before():
    """An ember-ignited unit's main source was involved at least t_ign,small + t_ign,large
    (342 s) earlier."""
    units = _block(n=10, spacing=13.0)
    tf = np.full(len(units), np.inf)
    tf[:3] = 0.0
    r = spread_with_embers(units, tf, [WindPeriod(0.0, 75.0, WEST)], duration_min=480.0,
                           embers=E.EmberOptions(design_fire_kw_m2=400, hamada=False))
    em = np.nonzero(r.source == SOURCE_EMBER)[0]
    assert len(em) >= 3
    for j in em:
        p = r.parent[j]
        assert p >= 0 and r.t_min[p] + 342.0 / 60.0 <= r.t_min[j] + 1e-9
        assert units.x[j] > units.x[p] - 1e-6  # downwind of its main source
    d = r.counts_at(480.0)
    assert d["units_ember"] == len(em) and d["units_ember_from_wildland"] == 0


# ------------------------------------------------------------------ wildland (Sardoy) embers


def _line_of_cells(x=-40.0, ys=np.arange(-60.0, 61.0, 20.0), inten=20000.0, start=0.0, end=30.0):
    n = len(ys)
    return E.WildlandSources(x=np.full(n, x), y=np.asarray(ys, float),
                             start_min=np.full(n, start), end_min=np.full(n, end),
                             intensity_kw_m=np.full(n, inten), cell_size_m=20.0)


def test_wildland_embers_ignite_a_house_downwind_of_an_intense_front():
    units = build_units([_box(0.0, -5.0, 10.0, 10.0)], neighbour_cutoff_m=30.0, frame=FRAME)
    tf = np.full(1, np.inf)
    w = [WindPeriod(0.0, 60.0, WEST)]
    on = spread_with_embers(units, tf, w, duration_min=120.0, embers=E.EmberOptions(),
                            wildland=_line_of_cells())
    assert np.isfinite(on.t_min[0]) and on.source[0] == SOURCE_EMBER
    assert on.ember_from_wildland[0] and on.parent[0] == -1
    assert on.t_min[0] >= 342.0 / 60.0
    c = on.counts_at(120.0)
    assert c["units_ember"] == c["units_ember_from_wildland"] == 1
    off = spread_with_embers(units, tf, w, duration_min=120.0,
                             embers=E.EmberOptions(from_wildland=False),
                             wildland=_line_of_cells())
    assert np.isinf(off.t_min[0])
    # a weak surface front does not do it
    weak = spread_with_embers(units, tf, w, duration_min=120.0, embers=E.EmberOptions(),
                              wildland=_line_of_cells(inten=300.0))
    assert np.isinf(weak.t_min[0])
    # nor does a front downwind of the house
    behind = spread_with_embers(units, tf, w, duration_min=120.0, embers=E.EmberOptions(),
                                wildland=_line_of_cells(x=60.0))
    assert np.isinf(behind.t_min[0])


def test_involved_detail_marks_ember_units():
    units = build_units([_box(0.0, -5.0, 10.0, 10.0)], neighbour_cutoff_m=30.0, frame=FRAME)
    r = spread_with_embers(units, np.full(1, np.inf), [WindPeriod(0.0, 60.0, WEST)],
                           duration_min=120.0, embers=E.EmberOptions(), wildland=_line_of_cells())
    r.footprints, r.frame = units.footprints, FRAME
    d = r.involved_detail()
    assert len(d) == 1 and d[0]["mechanism"] == "ember" and d[0]["source_id"] is None


# ------------------------------------------------------------------ reachable box with embers

N = 60
CELL_M = 25.0
HALF_M = N * CELL_M / 2
LAT_MIN, LNG_MIN = FRAME.to_latlng(-HALF_M, -HALF_M)
LAT_MAX, LNG_MAX = FRAME.to_latlng(HALF_M, HALF_M)
BBOX = (float(LAT_MIN), float(LAT_MAX), float(LNG_MIN), float(LNG_MAX))


class _Cond:
    def __init__(self, ws, wd):
        self.wind_speed, self.wind_direction = ws, wd


def _dense_town(spacing=15.0, side=10.0):
    out = []
    for x in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
        for y in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
            out.append(_box(x, y, side, side))
    return out


def _arrival():
    a = np.full((N, N), np.inf)
    a[27:33, 0:5] = (np.arange(5) * 3.0)[None, :]
    return a


@pytest.mark.parametrize("hamada,duration", [(True, 40.0), (False, 300.0)])
def test_reachable_build_with_embers_matches_the_whole_area_build(hamada, duration):
    houses = _dense_town()
    arr = _arrival()
    opts = E.EmberOptions(design_fire_kw_m2=400, hamada=hamada, from_wildland=False)
    sched = [(0.0, _Cond(75.0, 270.0))]
    wind = [WindPeriod(0.0, 75.0, 270.0)]
    full = build_units(houses, neighbour_cutoff_m=30.0, bbox=BBOX, frame=FRAME)
    tf = building_cell_contact_times(full, arr, BBOX, 10.0)
    ref = spread_with_embers(full, tf, wind, duration_min=duration, embers=opts)
    res = structure_spread_for_grid_run(houses, arr, sched, duration, bbox=BBOX, embers=opts)
    assert res.counts_at(duration)["units_built"] < len(full)
    np.testing.assert_allclose(np.sort(res.t_min[np.isfinite(res.t_min)]),
                               np.sort(ref.t_min[np.isfinite(ref.t_min)]))
    c = res.counts_at(duration)
    assert c["units_ember"] == int(np.sum(ref.source == SOURCE_EMBER))
    if not hamada:
        assert c["units_ember"] > 0


def _emitters(arr, intensity):
    """Grid cells of ``arr`` as the grid run's Emitters (x east / y north of the NW corner)."""
    from firesim.exposure import Emitters

    rr, cc = np.nonzero(np.isfinite(arr))
    dlat, dlng = (BBOX[1] - BBOX[0]) / N, (BBOX[3] - BBOX[2]) / N
    m_lat, m_lng = CELL_M / dlat, CELL_M / dlng
    k = len(rr)
    return Emitters(x=(cc + 0.5) * CELL_M, y=-(rr + 0.5) * CELL_M, start_min=arr[rr, cc],
                    end_min=arr[rr, cc] + 20.0, flame_m=np.full(k, 10.0), normal_x=np.ones(k),
                    normal_y=np.zeros(k), cell_size=CELL_M, lat0=BBOX[1], lng0=BBOX[2],
                    m_per_deg_lat=m_lat, m_per_deg_lng=m_lng, intensity_kw_m=np.full(k, intensity))


def test_reachable_build_with_wildland_embers_matches_the_whole_area_build():
    """Crown-fire intensity: Sardoy X_max is kilometres, but the safe wildland margin keeps the
    box small and the result equals the whole-area build."""
    houses = _dense_town(spacing=45.0)  # gaps of 35 m: beyond the Hamada cutoff
    arr = _arrival()
    opts = E.EmberOptions(design_fire_kw_m2=150)
    sched = [(0.0, _Cond(50.0, 270.0))]
    wind = [WindPeriod(0.0, 50.0, 270.0)]
    em = _emitters(arr, 30000.0)
    mu, sg = E.sardoy_lognormal(30.0, float(E.u10_to_u6(50 / 3.6)))
    assert float(E.x_max(mu, sg)) > 700.0  # what a plain X_max margin would have to cover
    full = build_units(houses, neighbour_cutoff_m=30.0, bbox=BBOX, frame=FRAME)
    tf = building_cell_contact_times(full, arr, BBOX, 10.0)
    ref = spread_with_embers(full, tf, wind, duration_min=60.0, embers=opts,
                             wildland=E.WildlandSources.from_emitters(em, FRAME))
    res = structure_spread_for_grid_run(houses, arr, sched, 60.0, bbox=BBOX, embers=opts,
                                        emitters=em)
    c = res.counts_at(60.0)
    assert c["units_built"] < len(full)
    assert c["units_ember_from_wildland"] == int(np.sum(ref.ember_from_wildland)) > 0
    np.testing.assert_allclose(np.sort(res.t_min[np.isfinite(res.t_min)]),
                               np.sort(ref.t_min[np.isfinite(ref.t_min)]))


def test_safe_wildland_reach_bound():
    ws = _line_of_cells(inten=30000.0)
    u6 = float(E.u10_to_u6(50 / 3.6))
    r = ws.safe_reach_m([u6], 30.0, footprint_allowance_m=0.0)
    # beyond r no point can collect ψ*_min even with every cell's whole emission
    assert 0 < r < float(E.x_max(*E.sardoy_lognormal(30.0, u6)))
    assert ws.safe_reach_m([0.0], 30.0) == 0.0
    weak = _line_of_cells(inten=50.0)
    assert weak.safe_reach_m([u6], 30.0, footprint_allowance_m=0.0) <= r


def test_ember_reach_bounds_every_landing():
    """structure_ember_reach covers X_max at the design-fire peak + crosswind truncation."""
    units = _row(2)
    reach = E.structure_ember_reach(units, E.EmberOptions(design_fire_kw_m2=400), U10_BENCH)
    mu, sg = E.himoto_lognormal(400 * 100.0, U10_BENCH, 10.0)
    assert np.all(reach >= float(E.x_max(mu, sg)) + 5.0)
    assert np.all(reach < 200.0)


def test_guard_still_applies_with_embers():
    res = structure_spread_for_grid_run(_dense_town(), _arrival(), [(0.0, _Cond(40.0, 270.0))],
                                        60.0, bbox=BBOX, max_units=10, embers=E.EmberOptions())
    assert isinstance(res, StructureSpreadSkipped)
    assert res.counts_at(60.0)["embers"] is True
