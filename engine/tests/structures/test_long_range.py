"""Long-range ember ignition (spec §6.3, ``firesim.structures.long_range``): published values,
the untruncated capped kernel, mass conservation of the landing field, the Poisson ignition
draw, and the ensemble coupled to the reachable-box structure build."""

from __future__ import annotations

import math

import numpy as np
import pytest
import shapely
from scipy.special import ndtr

from firesim.exposure import Emitters
from firesim.structures import long_range as LR
from firesim.structures.spread import (
    LongRangeResult,
    ListFootprintSource,
    structure_spread_for_grid_run,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(53.5, -113.5)


def test_beverly_wotton_grass_summer():
    # eq 2.02: P = 0.5 at DFFMC = 23.3755 / 0.2895
    assert LR.p_sustained_flaming(23.3755 / 0.2895) == pytest.approx(0.5)
    assert LR.p_sustained_flaming(95.0) > 0.98
    assert LR.p_sustained_flaming(60.0) < 0.01


@pytest.mark.parametrize("hc,r,ui", [(15.0, 1.0, 2), (10.0, 2.0, 4), (25.0, 2.0, 5), (5.0, 1.0, 0)])
def test_albini2012_reproduces_table1(hc, r, ui):
    uc = LR.A2012_UC[ui]
    a = 2.94 * r
    hfi = 0.34 * uc * hc * (1 + a) * math.log1p(a) / LR.A2012_FLAME_A * 1000.0  # eq 18 inverse
    assert LR.free_flame_height(hfi, uc, hc) == pytest.approx(r * hc, rel=1e-6)
    i = list(LR.A2012_HC).index(hc)
    expected = LR.A2012_TABLE[i, int(r) - 1, ui] * 1000.0
    assert LR.albini2012_max_distance(hfi, uc / LR.A2012_UC_FROM_U10, hc) == pytest.approx(expected, rel=1e-6)


def test_albini2012_held_at_table_edges():
    # weak flame (Hf < Hc) is held at Hf = Hc; very strong wind at 16.7 m/s
    assert LR.albini2012_max_distance(1.0, 100.0, 15.0) == pytest.approx(1700.0)


@pytest.mark.parametrize("tail", ["cap", "q99", "x2"])
def test_kernel_mass(tail):
    mu, sg, cap = math.log(300.0), 1.0, 1500.0
    k = LR.kernel(mu, sg, 18.4, cap, 20.0, 90.0, tail)
    assert k.shape[0] % 2 == 1 and k.shape[1] % 2 == 1
    cdf_cap = float(ndtr((math.log(cap) - mu) / sg))
    if tail == "cap":
        assert k.sum() == pytest.approx(cdf_cap, abs=0.01)  # beyond-cap embers dropped
    elif tail == "q99":
        assert k.sum() == pytest.approx(1.0, abs=0.01)  # Qin truncation, renormalised
    else:
        # beyond the median distances are doubled and the cap doubled: kept mass = F(cap + med/2)
        kept = float(ndtr((math.log(cap + 150.0) - mu) / sg))
        assert k.sum() == pytest.approx(kept, abs=0.01) and k.sum() > cdf_cap


@pytest.mark.parametrize("to_deg", [0.0, 45.0, 90.0, 200.0])
def test_kernel_crop_is_exact_on_the_grid(to_deg):
    # a kernel longer than the grid: cropping to ±(R-1, C-1) leaves a mode="same" convolution
    # unchanged, and return_total gives the uncropped sum (2026-10-17 memory fix)
    from scipy.signal import fftconvolve

    mu, sg, cap, h, shape = math.log(400.0), 1.0, 3000.0, 30.0, (21, 15)
    full = LR.kernel(mu, sg, 40.0, cap, h, to_deg, subsample=2)
    crop, total = LR.kernel(mu, sg, 40.0, cap, h, to_deg, subsample=2, crop=(shape[0] - 1, shape[1] - 1),
                            return_total=True)
    assert crop.shape[0] < full.shape[0] or crop.shape[1] < full.shape[1]
    assert crop.shape[0] % 2 == 1 and crop.shape[1] % 2 == 1
    assert total == pytest.approx(full.sum(), rel=1e-12)
    g = np.random.default_rng(1).random(shape)
    a, b = fftconvolve(g, full, mode="same"), fftconvolve(g, crop, mode="same")
    assert np.allclose(a, b, rtol=0, atol=1e-12 * max(abs(a).max(), 1.0))


def _dense_kernel(mu, sigma, sigma_y, cap_m, h, to_deg, subsample):
    # the pre-2026-10-17 algorithm ('cap' tail): every cell of the bounding box evaluated
    to = math.radians(to_deg)
    ux, uy = math.sin(to), math.cos(to)
    hw = 4.0 * sigma_y + h
    nc = int(math.ceil((abs(ux) * cap_m + abs(uy) * hw + h) / h))
    nr = int(math.ceil((abs(uy) * cap_m + abs(ux) * hw + h) / h))
    out = np.zeros((2 * nr + 1, 2 * nc + 1))
    cols = np.arange(-nc, nc + 1)
    q = (np.arange(subsample) + 0.5) / subsample - 0.5
    for r_off in range(-nr, nr + 1):
        acc = np.zeros(len(cols))
        for qy in q:
            py = (-r_off + qy) * h
            for qx in q:
                px = (cols + qx) * h
                a, c = px * ux + py * uy - 0.0, -px * uy + py * ux
                with np.errstate(divide="ignore", invalid="ignore"):
                    fx = np.exp(-((np.log(np.maximum(a, 1e-9)) - mu) ** 2) / (2 * sigma * sigma)) / (
                        np.maximum(a, 1e-9) * sigma * math.sqrt(2 * math.pi))
                fx = np.where((a > 0) & (a <= cap_m), fx * 1.0, 0.0)
                fy = np.exp(-c * c / (2 * sigma_y * sigma_y)) / (sigma_y * math.sqrt(2 * math.pi))
                acc += fx * fy
        out[r_off + nr] = acc * h * h / subsample ** 2
    return out


@pytest.mark.parametrize("to_deg", [0.0, 37.0, 90.0, 180.0, 300.0])
def test_kernel_strip_matches_dense_evaluation(to_deg):
    # only the crosswind strip where exp(-c^2 / 2 sy^2) > 0 is evaluated: same cells bit for bit
    args = (math.log(2000.0), 0.7, 25.0, 12000.0, 30.0, to_deg)
    k, total = LR.kernel(*args, subsample=2, return_total=True)
    ref = _dense_kernel(*args, subsample=2)
    assert np.array_equal(k, ref)
    assert total == pytest.approx(ref.sum(), rel=1e-12)


def test_kernel_analytic_total_beyond_the_exact_extent(monkeypatch):
    # above KERNEL_EXACT_CELLS a cropped 'cap' kernel keeps its cells and takes its sum from the
    # lognormal mass Phi((ln cap - mu) / sigma); for a long kernel that equals the cell sum closely
    args = (math.log(80_000.0), 0.66, 166.0, 615_000.0, 180.0, 37.0)
    exact, t_exact = LR.kernel(*args, subsample=2, crop=(60, 50), return_total=True)
    monkeypatch.setattr(LR, "KERNEL_EXACT_CELLS", 1000)
    fast, t_fast = LR.kernel(*args, subsample=2, crop=(60, 50), return_total=True)
    assert np.array_equal(exact, fast)
    assert t_fast == pytest.approx(float(ndtr((math.log(615_000.0) - args[0]) / 0.66)))
    assert t_fast == pytest.approx(t_exact, rel=1e-6)


def test_kernel_points_downwind():
    k = LR.kernel(math.log(200.0), 0.8, 18.4, 1000.0, 20.0, 90.0)  # blowing toward the east
    rc, cc = k.shape[0] // 2, k.shape[1] // 2
    assert k[:, cc + 1:].sum() > 0.95 * k.sum()
    assert k[:, :cc].sum() == 0.0
    assert k[rc].sum() > k[rc + 3].sum()  # concentrated on the wind axis


def _sources(n=1, cfb=1.0, hfi=50_000.0, x=0.0, y=0.0, cell=20.0, fuel="C2"):
    return LR.EmberSourceCells(np.full(n, x), np.full(n, y), np.zeros(n), np.full(n, 1.0),
                               np.full(n, hfi), np.full(n, 3.0), np.full(n, 1.0 * cfb),
                               np.full(n, cfb), np.full(n, fuel, dtype=object), cell)


def test_generation_per_kg_and_sources():
    opts = LR.LongRangeOptions()
    assert _sources(cfb=1.0).emitted(opts)[0] == pytest.approx(500.0 * 1.0 * 400.0)  # CFC 1 kg/m2
    assert _sources(cfb=0.05).emitted(opts)[0] == 0.0  # surface fire: no long-range source
    assert _sources(cfb=0.05).emitted(LR.LongRangeOptions(f_surface=1.0))[0] > 0.0


def test_landing_field_conserves_the_kernel_share():
    src = _sources()
    opts = LR.LongRangeOptions(members=1)
    fld = LR.landing_field(src, [(0.0, 40.0, 270.0)], 60.0, opts)
    landed = float(fld.density.sum(dtype=np.float64)) * fld.raster.h ** 2
    share = fld.classes[0]["kernel_share"]
    assert fld.emitted_total == pytest.approx(src.emitted(opts)[0])
    assert landed == pytest.approx(share * fld.emitted_total, rel=0.01)
    assert 0.0 < share < 1.0


def test_draw_ignition_times():
    edges = np.array([0.0, 30.0, 60.0, 90.0])
    hz = np.zeros((3, 4))
    hz[1, 1] = 50.0  # certain, in the second bin
    hz[:, 2] = 0.3  # P = 1 - exp(-0.9)
    rng = np.random.default_rng(0)
    t = LR.draw_ignition_times(hz, edges, rng)
    assert np.isinf(t[0]) and np.isinf(t[3])
    assert 30.0 <= t[1] <= 31.0
    big = np.repeat(hz[:, 2:3], 20000, axis=1)
    p = np.isfinite(LR.draw_ignition_times(big, edges, np.random.default_rng(1))).mean()
    assert p == pytest.approx(1 - math.exp(-0.9), abs=0.015)


def test_hazard_roof_factor_and_cap():
    geoms = [shapely.box(-113.5, 53.5, -113.4999, 53.5001)] * 2
    units = build_units(geoms, neighbour_cutoff_m=30.0, frame=FRAME)
    raster = LR.Raster.around(units.x, units.y, 100.0, 20.0)
    fld = LR.LandingField(raster, np.array([0.0, 60.0]),
                          np.full((1, raster.rows, raster.cols), 0.01, dtype=np.float32))
    opts = LR.LongRangeOptions()
    h = LR.building_hazard(units, fld, opts, 95.0, combustible_roof=np.array([False, True]))
    assert h[0, 1] / h[0, 0] == pytest.approx(LR.ROOF_FACTOR)
    a_r = LR.receptive_area(units)
    assert np.all(a_r > units.area_m2)
    p_e = LR.p_sustained_flaming(95.0)
    assert h[0, 0] == pytest.approx(0.01 * a_r[0] * p_e * 0.5 * 0.1, rel=1e-5)


# ------------------------------------------------------------------ coupled to the build

N, CELL = 60, 50.0
HALF = N * CELL / 2
LAT_MIN, LNG_MIN = FRAME.to_latlng(-HALF, -HALF)
LAT_MAX, LNG_MAX = FRAME.to_latlng(HALF, HALF)
BBOX = (float(LAT_MIN), float(LAT_MAX), float(LNG_MIN), float(LNG_MAX))


class _Cond:
    def __init__(self, ws, wd, ffmc=93.0):
        self.wind_speed, self.wind_direction, self.ffmc = ws, wd, ffmc


def _setup():
    """A crowning strip on the west edge (cols 0-3) and houses east of it, wind from the west."""
    arrival = np.full((N, N), np.inf)
    arrival[:, :4] = 5.0
    r, c = np.nonzero(np.isfinite(arrival))
    x = (c + 0.5) * CELL
    y = -(r + 0.5) * CELL
    n = len(r)
    em = Emitters(x=x, y=y, start_min=np.full(n, 5.0), end_min=np.full(n, 6.0),
                  flame_m=np.full(n, 20.0), normal_x=np.ones(n), normal_y=np.zeros(n),
                  cell_size=CELL, intensity_kw_m=np.full(n, 40_000.0), cfb=np.ones(n),
                  sfc=np.full(n, 2.0), cfc=np.full(n, 1.0), fuel=np.full(n, "C2", dtype=object))
    em.lat0, em.lng0 = BBOX[1], BBOX[2]
    em.m_per_deg_lat = 111320.0
    em.m_per_deg_lng = FRAME.m_per_deg_lng
    houses = []
    for xm in np.arange(-HALF + 400, HALF - 50, 120.0):
        for ym in np.arange(-HALF + 300, HALF - 300, 120.0):
            la, ln = FRAME.to_latlng(np.array([ym, ym + 12]), np.array([xm, xm + 12]))
            houses.append(shapely.box(float(ln[0]), float(la[0]), float(ln[1]), float(la[1])))
    return arrival, em, houses


def _run(**kw):
    arrival, em, houses = _setup()
    opts = LR.LongRangeOptions(members=kw.pop("members", 8), p_building=kw.pop("p_b", 0.001),
                               model=kw.pop("model", "v1"))
    return structure_spread_for_grid_run(ListFootprintSource(houses), arrival,
                                         [(0.0, _Cond(40.0, 270.0))], 120.0, bbox=BBOX,
                                         emitters=em, long_range=opts, ffmc=93.0, seed=7, **kw)


def test_long_range_ensemble_gives_probabilities():
    res = _run()
    assert isinstance(res, LongRangeResult)
    p = res.p_involved()
    assert res.t_members.shape[0] == 8
    assert p.max() > 0.5
    built = ListFootprintSource(_setup()[2])
    assert res.units_in_run == built.count_in_box(BBOX)
    # probability falls with distance downwind (east) of the burning strip
    units_x = np.array([shapely.centroid(g).x for g in res.footprints if g is not None])
    assert units_x.size > 0
    near = res.p_direct[np.argsort(res.t_front_min)][:1]
    assert np.all(near >= 0)
    c = res.counts_at(120.0)
    assert c["long_range_embers"] is True and c["long_range"]["members"] == 8
    assert c["units_involved"] == round(c["units_involved_expected"])
    d = res.involved_detail()
    assert d and all(0 < e["p_involved"] <= 1 for e in d)
    assert all("polygon" in e for e in d)


def test_long_range_is_repeatable_and_seeded():
    a, b = _run(), _run()
    assert np.array_equal(a.t_members, b.t_members)


def test_long_range_probability_falls_with_distance():
    res = _run(members=4)
    inv = np.array([g is not None for g in res.footprints])
    xs = np.array([shapely.centroid(g).x for g in res.footprints[inv]])
    pd = res.p_direct[inv]
    assert np.corrcoef(xs, pd)[0, 1] < -0.5  # east = farther downwind = less likely


def test_roof_share_allowed_with_long_range_only():
    res = _run(combustible_roof_share=0.30)
    assert res.roof_scenario["combustible_roof_share"] == 0.30
    assert "roof_scenario" in res.counts_at(120.0)
