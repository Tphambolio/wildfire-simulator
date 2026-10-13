"""Long-range embers v5 (spec §6.7): two-mode ember transport (Sardoy 2008 short flaming mode at
the 10 m canopy-top wind + long charring mode), the Storey et al. 2020 calibration of the
long-mode weight, surface-fire brands, the dry-fuel glowing factor, receptor ignition by
accumulated brand mass (k-hit thresholds) and short contact links."""

from __future__ import annotations

import math

import numpy as np
import pytest

from firesim.structures import long_range as LR
from firesim.structures.spread import (
    ListFootprintSource,
    LongRangeResult,
    WindPeriod,
    structure_spread_for_grid_run,
)

from .test_long_range import BBOX, _Cond, _setup
from .test_long_range_v2 import _grid


def _src(n=1, cfb=1.0, hfi=30_000.0, sfc=2.0, cfc=1.0):
    return LR.EmberSourceCells(np.zeros(n), np.zeros(n), np.zeros(n), np.full(n, 60.0),
                               np.full(n, hfi), np.full(n, sfc), np.full(n, cfc), np.full(n, cfb),
                               np.full(n, "C7", dtype=object), 20.0)


def test_published_values_and_derivations():
    # 4 % fine fuel moisture on the FF-scale: m = 147.2 (101 − F) / (59.5 + F) = 4 → F = 96.75
    f = LR.V5_DRY_FFMC
    assert 147.2 * (101 - f) / (59.5 + f) == pytest.approx(4.0)
    assert f == pytest.approx(96.75, abs=0.01)
    # Sardoy p.486: 27.62 % of landed brands in the long mode; mode ~6.1 km, > 4.5 km
    assert LR.V5_LONG_FRACTION == pytest.approx(0.2762)
    mode = LR.V5_LONG_MEDIAN_M * math.exp(-LR.V5_LONG_SIGMA ** 2)
    assert 5800 < mode < 6400
    # Storey et al. 2020 p.7: 2 % of spot fires beyond 3 km fixes the long-mode weight
    assert LR.V5_LONG_IGNITION == pytest.approx(0.0535, abs=5e-4)
    # Filkov 2017 range, mass terms; Lee & Davidson p.692 link scale; threshold bounds
    assert LR.V5_F_SURFACE_RANGE[0] <= LR.V5_F_SURFACE <= LR.V5_F_SURFACE_RANGE[1]
    assert LR.V5_LINK_SCALE_M == 6.0
    assert LR.V5_THRESHOLD_BOUNDS_G[0] <= LR.V5_THRESHOLD_G <= LR.V5_THRESHOLD_BOUNDS_G[1]
    o = LR.LongRangeOptions.for_model("v5")
    assert o.model == "v5" and o.link_scale_m == 6.0 and o.f_surface == LR.V5_F_SURFACE
    assert o.params()["long_range_threshold_g"] == LR.V5_THRESHOLD_G
    assert LR.LongRangeOptions.for_model("v3").link_scale_m == LR.V3_LINK_SCALE_M
    with pytest.raises(ValueError):
        LR.LongRangeOptions(model="v5", long_fraction=1.0)


def test_two_mode_kernel_matches_storey_tail_beyond_3_km():
    for i, u in ((10.0, 6.7), (30.0, 11.17), (40.0, 17.88)):
        p = LR.two_mode_exceedance([1000.0, 3000.0, 14_000.0], i, u)
        assert p[1] == pytest.approx(0.02, abs=1e-3)  # Storey: 2 % beyond 3 km
        assert p[0] < 0.05  # documented misfit: Storey has 10 % beyond 1 km (spec §6.7)
        assert p[2] < 1e-4
        landed = LR.two_mode_exceedance([3000.0], i, u, weighted=False)
        assert landed[0] == pytest.approx(LR.V5_LONG_FRACTION, abs=1e-3)


def test_sardoy_validity_flag():
    assert not LR.sardoy_extrapolated(30.0, 11.17)
    assert LR.sardoy_extrapolated(5.0, 11.17) and LR.sardoy_extrapolated(30.0, 5.0)
    assert LR.sardoy_extrapolated(45.0, 11.17) and LR.sardoy_extrapolated(30.0, 18.5)


def test_long_ignition_factor_rises_when_dry():
    o = LR.LongRangeOptions.for_model("v5")
    assert LR.long_ignition_factor(o, 93.0) == pytest.approx(LR.V5_LONG_IGNITION)
    assert LR.long_ignition_factor(o, 97.0) == pytest.approx(3.0 * LR.V5_LONG_IGNITION)
    assert LR.long_ignition_factor(LR.LongRangeOptions.for_model("v5", dry_factor=1e3), 97.0) == 1.0


def test_surface_cells_emit_f_s_and_no_long_mode():
    o = LR.LongRangeOptions.for_model("v5", f_surface=0.01)
    surf = _src(cfb=0.0, sfc=2.0, cfc=0.5)
    e = surf.emitted(o)
    assert e[0] == pytest.approx(o.n_per_kg * 0.01 * 2.0 * 400.0)  # CFC ignored below CFB 0.1
    assert surf.emitted_long(o)[0] == 0.0
    crown = _src(cfb=1.0, sfc=2.0, cfc=1.0)
    assert crown.emitted(o)[0] == pytest.approx(o.n_per_kg * (0.01 * 2.0 + 1.0) * 400.0)
    assert crown.emitted_long(o)[0] == pytest.approx(o.long_fraction * o.n_per_kg * 1.0 * 400.0)
    assert np.all(_src(cfb=0.0).emitted(LR.LongRangeOptions.for_model("v5", f_surface=0.0)) == 0)


def test_landing_field_v5_two_modes():
    o = LR.LongRangeOptions.for_model("v5", raster_m=20.0, bin_min=60.0, step_min=60.0)
    src = _src(n=1, hfi=30_000.0)
    fld = LR.landing_field(src, [(0.0, 11.17 * 3.6, 270.0)], 60.0, o)  # wind from W → east
    assert fld.long_density is not None and fld.long_emitted > 0
    short_landed = fld.landed_total
    assert short_landed == pytest.approx(fld.emitted_total, rel=0.03)  # truncated at q99.9
    assert fld.emitted_total == pytest.approx(src.emitted(o)[0] - src.emitted_long(o)[0], rel=1e-6)
    lr_ = fld.long_raster
    long_landed = float(fld.long_density.sum(dtype=np.float64)) * lr_.h ** 2
    assert long_landed == pytest.approx(fld.long_emitted, rel=0.05)
    # the long mode lands 4-11 km downwind (east), the short mode within ~1.3 km
    rows, cols = np.nonzero(fld.long_density[0] > 1e-6 * fld.long_density[0].max())
    xs = lr_.x0 + (cols + 0.5) * lr_.h
    w = fld.long_density[0][rows, cols]
    assert 5500 < np.average(xs, weights=w) < 7200
    assert np.average(xs < 3000, weights=w) < 0.01
    r, c = np.nonzero(fld.density[0] > 1e-6 * fld.density[0].max())  # above FFT round-off
    assert (fld.raster.x0 + (c.max() + 0.5) * fld.raster.h) < 1500
    assert not fld.classes[0]["sardoy_extrapolated"] and fld.extrapolated_share == 0.0
    slow = LR.landing_field(src, [(0.0, 4.0 * 3.6, 270.0)], 60.0, o)  # 4 m/s: outside 6.7-17.9
    assert slow.extrapolated_share == pytest.approx(1.0)


def test_threshold_receptor_is_k_hit():
    o = LR.LongRangeOptions.for_model("v5", threshold_g=4.0)  # n* = 20 model embers
    thr = LR.ignition_thresholds_v5(o, np.random.default_rng(0), 20_000)
    assert thr.mean() == pytest.approx(20.0, rel=0.02)
    # P(ignite) = P(Poisson(Λ) >= 20): ~0 at Λ = 5, ~1 at Λ = 50 (single-hit would give 1 − e^-Λq)
    assert np.mean(thr <= 5.0) < 1e-3 and np.mean(thr <= 50.0) > 0.999


def test_v5_member_wildland_threshold_and_links():
    u = _grid(5, 1, 18.0)  # 12 m houses 6 m apart in a row
    w = [WindPeriod(0.0, 40.0, 270.0)]
    edges = np.arange(0.0, 121.0, 10.0)
    tf = np.full(len(u), np.inf)
    # wildland embers: 100 effective embers on house 0 only in the first bin
    wild = np.zeros((len(edges) - 1, len(u)))
    wild[0, 0] = 100.0
    o = LR.LongRangeOptions.for_model("v5", threshold_g=4.0, survival=0.0, param_draws=False,
                                      link_scale_m=1e9, urban_source=False)
    t, *_ = LR.run_member_v5(u, tf, wild, edges, o, w, 120.0, np.random.default_rng(1), None,
                             [(0, 40, 270)])
    assert np.isfinite(t[0]) and t[0] >= o.delay_s / 60.0
    assert np.all(np.isfinite(t))  # certain links: Hamada carries it along the row
    o_hi = LR.LongRangeOptions.for_model("v5", threshold_g=91.0, survival=0.0, param_draws=False,
                                         urban_source=False)
    t2, *_ = LR.run_member_v5(u, tf, wild, edges, o_hi, w, 120.0, np.random.default_rng(1), None,
                              [(0, 40, 270)])
    assert np.all(np.isinf(t2))  # 100 embers < n* = 455


def test_short_links_reproduce_lee_davidson_no_spread_share():
    """Lee & Davidson (2010) Application p.692: 17 of 50 runs at 6 m spacing never left the
    first house. With λ 6 m and s 0.3 the analytic share is Π (1 − p_j (1 − s))."""
    u = _grid(5, 5, 18.0)
    w = [WindPeriod(0.0, 25.0, 0.0)]  # 7 m/s
    i0 = 12  # centre house
    tf = np.full(len(u), np.inf)
    tf[i0] = 0.0
    o = LR.LongRangeOptions.for_model("v5", survival=0.3, param_draws=False, urban_source=False)
    nb, sep = u.neighbours(i0)
    p_j = np.exp(-np.asarray(sep) / o.link_scale_m) * (np.asarray(sep) <= o.link_cutoff_m)
    analytic = float(np.prod(1.0 - p_j * (1.0 - 0.3)))
    stuck = 0
    m = 400
    for k in range(m):
        t, *_ = LR.run_member_v5(u, tf, None, np.arange(0.0, 121.0, 10.0), o, w, 120.0,
                                 np.random.default_rng([3, k]), None, [(0, 25, 0)])
        stuck += int(np.isfinite(t).sum() == 1)
    assert stuck / m == pytest.approx(analytic, abs=0.06)
    assert 0.1 < analytic < 0.4  # same order as 17/50 = 0.34


def test_grid_run_v5():
    arrival, em, houses = _setup()

    def run(members):
        opts = LR.LongRangeOptions.for_model("v5", members=members, threshold_g=3.5)
        return structure_spread_for_grid_run(ListFootprintSource(houses), arrival,
                                             [(0.0, _Cond(40.0, 270.0))], 120.0, bbox=BBOX,
                                             emitters=em, long_range=opts, ffmc=93.0, seed=7)

    res = run(3)
    assert isinstance(res, LongRangeResult)
    assert res.params["long_range_model"] == "v5"
    assert res.params["neighbour_cutoff_m"] == LR.V5_LINK_CUTOFF_M
    assert "long_range_sardoy_extrapolated_share" in res.params
    assert res.counts_at(120.0)["long_range"]["members"] == 3
    a, b = run(2), run(2)
    assert np.array_equal(a.t_members, b.t_members)


def test_storey_located_variant_reproduces_storey_points():
    o = LR.LongRangeOptions.for_model("v5", **LR.V5_STOREY_LONG)
    p = LR.two_mode_exceedance([100.0, 1000.0, 3000.0, 13_900.0], 30.0, 11.17, o)
    assert p[0] == pytest.approx(0.5, abs=0.01)
    assert p[1] == pytest.approx(0.10, abs=0.005)
    assert p[2] == pytest.approx(0.02, abs=0.002)
    assert p[3] < 1e-3
