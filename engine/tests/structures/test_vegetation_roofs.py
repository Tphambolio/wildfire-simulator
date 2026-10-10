"""Structure step 3 (spec §2.1, §4.6, §6.2): building vegetation attributes, the gap woody
cover, the vegetation-bridged cutoff rule, the combustible-roof scenario (assignment
determinism, ψ* scaling) and the reachable-box build with the new options."""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import numpy as np
import pytest
import shapely

from firesim.structures import embers as E
from firesim.structures import roofs as R
from firesim.structures import vegetation as V
from firesim.structures.spread import (
    ListFootprintSource,
    WindPeriod,
    building_cell_contact_times,
    hamada_spread,
    spread_with_embers,
    structure_spread_for_grid_run,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(53.5, -113.5)
WEST = 270.0
DATA = Path(__file__).resolve().parents[3] / "data"


def _box(x0, y0, w, h):
    lat, lng = FRAME.to_latlng(np.array([x0, x0 + w]), np.array([y0, y0 + h]))
    return shapely.box(float(lng[0]), float(lat[0]), float(lng[1]), float(lat[1]))


def _merc_to_lnglat(x, y):
    return (np.degrees(np.asarray(x) / V.R_MERC),
            np.degrees(2.0 * np.arctan(np.exp(np.asarray(y) / V.R_MERC)) - np.pi / 2.0))


def _canopy(fn, x_range=(-300.0, 1600.0), y_range=(-300.0, 300.0), cell=0.5) -> V.CanopyWindow:
    """A CanopyWindow on EPSG:3857 whose share at each cell is ``fn(x_local, y_local)``."""
    lat0, lng0 = FRAME.to_latlng(np.array([x_range[0]]), np.array([y_range[1]]))
    lat1, lng1 = FRAME.to_latlng(np.array([x_range[1]]), np.array([y_range[0]]))
    x0, y0 = V.lnglat_to_3857(lng0[0], lat0[0])
    x1, y1 = V.lnglat_to_3857(lng1[0], lat1[0])
    cols, rows = int(math.ceil((x1 - x0) / cell)), int(math.ceil((y0 - y1) / cell))
    cx = x0 + (np.arange(cols) + 0.5) * cell
    cy = y0 - (np.arange(rows) + 0.5) * cell
    gx, gy = np.meshgrid(cx, cy)
    lng, lat = _merc_to_lnglat(gx, gy)
    lx, ly = FRAME.to_local(lat, lng)
    share = np.asarray(fn(lx, ly), dtype=np.float32)
    return V.CanopyWindow(share, float(x0), float(y0), cell, cell)


def _pair(gap):
    """Two 10 m squares ``gap`` metres apart along x, graph to 45 m."""
    return build_units([_box(0, 0, 10, 10), _box(10 + gap, 0, 10, 10)],
                       neighbour_cutoff_m=45.0, frame=FRAME)


# ------------------------------------------------------------------ gap corridor and cover


def test_gap_corridor_is_hull_minus_both_footprints():
    u = _pair(30.0)
    c = V.gap_corridors(u.footprints[0], u.footprints[[1]])[0]
    assert c.area == pytest.approx(30.0 * 10.0, rel=1e-3)
    assert c.intersection(u.footprints[0]).area == pytest.approx(0.0, abs=1e-6)


def test_gap_cover_full_half_and_none():
    u = _pair(30.0)
    full = _canopy(lambda x, y: np.ones_like(x))
    half = _canopy(lambda x, y: (x < 25.0).astype(float))  # corridor x 10-40: west half
    none = _canopy(lambda x, y: np.zeros_like(x))
    assert V.gap_cover(u, 0, [1], full)[0] == pytest.approx(1.0)
    assert V.gap_cover(u, 0, [1], half)[0] == pytest.approx(0.5, abs=0.04)
    assert V.gap_cover(u, 0, [1], none)[0] == pytest.approx(0.0)
    # symmetric in the pair
    assert V.gap_cover(u, 1, [0], half)[0] == pytest.approx(V.gap_cover(u, 0, [1], half)[0])


def test_other_buildings_in_the_gap_count_as_no_cover():
    """A third 6 x 6 m building in the 30 x 10 m corridor: 36 / 300 of it is structure."""
    u = build_units([_box(0, 0, 10, 10), _box(40, 0, 10, 10), _box(22, 2, 6, 6)],
                    neighbour_cutoff_m=45.0, frame=FRAME)
    full = _canopy(lambda x, y: np.ones_like(x))
    assert V.gap_cover(u, 0, [1], full)[0] == pytest.approx(1 - 36 / 300, abs=0.02)


def test_no_canopy_data_gives_nan_and_no_bridge():
    u = _pair(30.0)
    nodata = _canopy(lambda x, y: np.full_like(x, np.nan))
    assert np.isnan(V.gap_cover(u, 0, [1], nodata)[0])
    links = V.BridgedLinks(u, nodata)
    assert not links(0, np.array([1]), np.array([30.0]))[0]
    assert links.params()["bridge_canopy_data"] is False


# ------------------------------------------------------------------ cutoff rule


@pytest.mark.parametrize("gap,cover,linked", [
    (5.0, 0.0, True), (20.0, 0.0, True),  # <= 20 m: always (as now)
    (20.5, 0.0, False), (30.0, 0.19, False), (30.0, 0.20, True), (44.0, 0.5, True),
    (46.0, 1.0, False),  # beyond 45 m: never
])
def test_bridged_cutoff_rule(gap, cover, linked):
    u = _pair(min(gap, 44.0))
    links = V.BridgedLinks(u, _canopy(lambda x, y: np.full_like(x, cover)))
    got = links(0, np.array([1]), np.array([gap]))[0]
    assert bool(got) is linked


def test_bridged_links_need_a_45_m_graph_and_valid_cutoffs():
    with pytest.raises(ValueError):
        V.BridgedLinks(build_units([_box(0, 0, 10, 10)], neighbour_cutoff_m=30.0, frame=FRAME),
                       None)
    with pytest.raises(ValueError):
        V.BridgedLinks(_pair(30.0), None, base_cutoff_m=50.0)


def test_pre_registered_values():
    assert (V.BRIDGE_BASE_CUTOFF_M, V.BRIDGE_MAX_CUTOFF_M, V.BRIDGE_MIN_GAP_COVER) == (20.0, 45.0, 0.20)


def _row(gap, n=5):
    return build_units([_box((10 + gap) * i, 0, 10, 10) for i in range(n)],
                       neighbour_cutoff_m=45.0, frame=FRAME)


def test_hamada_with_bridge_spreads_only_across_wooded_gaps():
    units = _row(30.0)
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    w = [WindPeriod(0.0, 30.0, WEST)]
    plain = hamada_spread(units, tf, w, duration_min=600.0)
    bare = hamada_spread(units, tf, w, duration_min=600.0,
                         link_filter=V.BridgedLinks(units, _canopy(lambda x, y: np.zeros_like(x))))
    wooded = hamada_spread(units, tf, w, duration_min=600.0,
                           link_filter=V.BridgedLinks(units, _canopy(lambda x, y: np.ones_like(x))))
    assert np.isfinite(plain.t_min).all()
    assert np.isfinite(bare.t_min).sum() == 1  # 30 m gaps without cover: no link
    np.testing.assert_array_equal(wooded.t_min, plain.t_min)  # all links bridged
    # wooded only between units 0-1-2: spread stops at 2
    part = hamada_spread(units, tf, w, duration_min=600.0,
                         link_filter=V.BridgedLinks(units, _canopy(lambda x, y: (x < 90).astype(float))))
    assert np.isfinite(part.t_min).sum() == 3


def test_bridge_with_embers_filters_hamada_links_only():
    units = _row(30.0)
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    w = [WindPeriod(0.0, 30.0, WEST)]
    opts = E.EmberOptions(from_wildland=False)
    bare = V.BridgedLinks(units, _canopy(lambda x, y: np.zeros_like(x)))
    r = spread_with_embers(units, tf, w, duration_min=600.0, embers=opts, link_filter=bare)
    h = hamada_spread(units, tf, w, duration_min=600.0, link_filter=bare)
    assert np.all(r.t_min[np.isfinite(h.t_min)] <= h.t_min[np.isfinite(h.t_min)] + 1e-9)


# ------------------------------------------------------------------ roof scenario


def test_roof_assignment_deterministic_share_and_nested():
    keys = np.arange(20000, dtype=np.uint64)
    a = R.assign_combustible_roofs(keys, 0.15, seed=7)
    b = R.assign_combustible_roofs(keys, 0.15, seed=7)
    c = R.assign_combustible_roofs(keys, 0.15, seed=8)
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)
    for share in R.ROOF_SHARES:
        got = R.assign_combustible_roofs(keys, share, seed=7).mean()
        assert abs(got - share) < 4 * math.sqrt(max(share * (1 - share), 1e-12) / len(keys)) + 1e-12
    low = R.assign_combustible_roofs(keys, 0.05, seed=7)
    high = R.assign_combustible_roofs(keys, 0.30, seed=7)
    assert np.all(high[low]) and np.all(a[low]) and np.all(high[a])  # nested shares
    assert not R.assign_combustible_roofs(keys, 0.0, seed=7).any()
    with pytest.raises(ValueError):
        R.assign_combustible_roofs(keys, 1.5, seed=7)


def test_roof_keys_do_not_depend_on_build_order_or_extent():
    boxes = [_box(16.0 * i, 16.0 * j, 10, 10) for i in range(12) for j in range(12)]
    u1 = build_units(boxes, frame=FRAME)
    u2 = build_units(boxes[::-1][:100], frame=FRAME)  # other order, fewer buildings
    a1 = R.assign_combustible_roofs(R.building_keys(u1), 0.3, 11)
    a2 = R.assign_combustible_roofs(R.building_keys(u2), 0.3, 11)
    pos1 = {(round(x, 3), round(y, 3)): v for x, y, v in zip(u1.x, u1.y, a1)}
    for x, y, v in zip(u2.x, u2.y, a2):
        assert pos1[(round(x, 3), round(y, 3))] == v
    # with ids the key is the id
    u3 = build_units(boxes[:5], frame=FRAME, ids=[10, 11, 12, 13, -1])
    k = R.building_keys(u3)
    assert list(k[:4]) == [10, 11, 12, 13] and k[4] >= np.uint64(1 << 63)


def test_roof_label_and_factor():
    assert R.roof_label(0.15) == "scenario: 15 % combustible roofs (not observed roofs)"
    assert R.roof_label(0.05) == "scenario: 5 % combustible roofs (not observed roofs)"
    assert R.ROOF_PSI_FACTOR == pytest.approx(0.06 / 0.16)  # DeBeer 2023 Tables 10-1, 10-4
    np.testing.assert_array_equal(R.psi_factors(np.array([True, False]), 0.375), [0.375, 1.0])
    with pytest.raises(ValueError):
        R.psi_factors(np.array([True]), 0.0)


def _bench_row(n=8):
    return build_units([_box(20.0 * i, 0.0, 10, 10) for i in range(n)], neighbour_cutoff_m=30.0,
                       frame=FRAME)


U10_BENCH = 17.9 / E.U10_TO_U6


def test_psi_factor_scales_the_ember_threshold_exactly():
    """Pool ≥ k·need with GR' is the same event as pool ≥ need with GR'/k: identical times."""
    units = _bench_row()
    tf = np.full(len(units), np.inf)
    tf[0] = 0.0
    w = [WindPeriod(0.0, U10_BENCH * 3.6, WEST)]
    base = dict(design_fire_kw_m2=400, lateral=False, hamada=False, from_wildland=False)
    a = spread_with_embers(units, tf, w, duration_min=900.0,
                           embers=E.EmberOptions(gr_structure=10.0, **base),
                           psi_factor=np.full(len(units), 0.5))
    b = spread_with_embers(units, tf, w, duration_min=900.0,
                           embers=E.EmberOptions(gr_structure=20.0, **base))
    fin = np.isfinite(a.t_min)
    assert fin.sum() >= 3
    np.testing.assert_array_equal(fin, np.isfinite(b.t_min))
    np.testing.assert_allclose(a.t_min[fin], b.t_min[fin], atol=1e-9)
    # factor 1 everywhere is exactly no factor; a lower factor is never later
    one = spread_with_embers(units, tf, w, duration_min=900.0,
                             embers=E.EmberOptions(**base), psi_factor=np.ones(len(units)))
    none = spread_with_embers(units, tf, w, duration_min=900.0, embers=E.EmberOptions(**base))
    np.testing.assert_array_equal(one.t_min, none.t_min)
    roof = spread_with_embers(units, tf, w, duration_min=900.0, embers=E.EmberOptions(**base),
                              psi_factor=np.full(len(units), R.ROOF_PSI_FACTOR))
    f = np.isfinite(none.t_min)
    assert np.all(roof.t_min[f] <= none.t_min[f] + 1e-9)
    assert roof.t_min[1] < none.t_min[1]


def test_psi_factor_validation_and_needs_embers():
    units = _bench_row(3)
    tf = np.array([0.0, np.inf, np.inf])
    w = [WindPeriod(0.0, 40.0, WEST)]
    with pytest.raises(ValueError):
        spread_with_embers(units, tf, w, duration_min=60.0, psi_factor=np.full(3, 0.5))
    with pytest.raises(ValueError):
        spread_with_embers(units, tf, w, duration_min=60.0, embers=E.EmberOptions(),
                           psi_factor=np.full(3, 1.5))


# ------------------------------------------------------------------ reachable build + outputs

N = 60
CELL_M = 25.0
HALF_M = N * CELL_M / 2
LAT_MIN, LNG_MIN = FRAME.to_latlng(-HALF_M, -HALF_M)
LAT_MAX, LNG_MAX = FRAME.to_latlng(HALF_M, HALF_M)
BBOX = (float(LAT_MIN), float(LAT_MAX), float(LNG_MIN), float(LNG_MAX))


class _Cond:
    def __init__(self, ws, wd):
        self.wind_speed, self.wind_direction = ws, wd


def _town(spacing=36.0, side=10.0):
    out = []
    for x in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
        for y in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
            out.append(_box(x, y, side, side))
    return out


def _arrival():
    a = np.full((N, N), np.inf)
    a[27:33, 0:5] = (np.arange(5) * 3.0)[None, :]
    return a


class _FakeCanopy:
    """Stands in for CanopyCover: a wooded band 0 < y_local < 300 m, bare elsewhere."""

    def __init__(self):
        self.win = _canopy(lambda x, y: ((y > -200) & (y < 300)).astype(float),
                           x_range=(-HALF_M - 50, HALF_M + 50), y_range=(-HALF_M - 50, HALF_M + 50),
                           cell=2.0)

    def window(self, *a, **k):
        return self.win


def test_reachable_build_with_bridge_matches_the_whole_area_build():
    houses = _town()  # 26 m gaps: beyond 20 m, so only wooded gaps link
    arr = _arrival()
    sched = [(0.0, _Cond(40.0, 270.0))]
    wind = [WindPeriod(0.0, 40.0, 270.0)]
    canopy = _FakeCanopy()
    full = build_units(houses, neighbour_cutoff_m=45.0, bbox=BBOX, frame=FRAME)
    tf = building_cell_contact_times(full, arr, BBOX, 10.0)
    ref = hamada_spread(full, tf, wind, duration_min=600.0, burnout_min=66.0,
                        link_filter=V.BridgedLinks(full, canopy.win))
    res = structure_spread_for_grid_run(houses, arr, sched, 600.0, bbox=BBOX,
                                        vegetation_bridge=True, canopy=canopy)
    np.testing.assert_allclose(np.sort(res.t_min[np.isfinite(res.t_min)]),
                               np.sort(ref.t_min[np.isfinite(ref.t_min)]))
    c = res.counts_at(600.0)
    assert c["vegetation_bridged_cutoff"] is True and c["neighbour_cutoff_m"] == 20.0
    assert c["bridge_max_cutoff_m"] == 45.0 and c["links_bridged"] > 0
    assert c["units_structure_to_structure"] > 0
    assert c["links_tested"] > c["links_bridged"]  # bare gaps outside the band are not linked
    # no canopy object: the rule is the plain 20 m cutoff (no 26 m link)
    nocan = structure_spread_for_grid_run(houses, arr, sched, 600.0, bbox=BBOX,
                                          vegetation_bridge=True, canopy=None)
    cn = nocan.counts_at(600.0)
    assert cn["units_structure_to_structure"] == 0 and cn["bridge_canopy_data"] is False


def test_reachable_build_with_roof_scenario_matches_the_whole_area_build():
    houses = _town(spacing=15.0)
    arr = _arrival()
    opts = E.EmberOptions(design_fire_kw_m2=400, hamada=False, from_wildland=False)
    sched = [(0.0, _Cond(75.0, 270.0))]
    wind = [WindPeriod(0.0, 75.0, 270.0)]
    full = build_units(houses, neighbour_cutoff_m=30.0, bbox=BBOX, frame=FRAME)
    tf = building_cell_contact_times(full, arr, BBOX, 10.0)
    roof = R.assign_combustible_roofs(R.building_keys(full), 0.30, 99)
    ref = spread_with_embers(full, tf, wind, duration_min=300.0, embers=opts,
                             burnout_min=70.0, psi_factor=R.psi_factors(roof))
    res = structure_spread_for_grid_run(houses, arr, sched, 300.0, bbox=BBOX, embers=opts,
                                        combustible_roof_share=0.30, seed=99)
    np.testing.assert_allclose(np.sort(res.t_min[np.isfinite(res.t_min)]),
                               np.sort(ref.t_min[np.isfinite(ref.t_min)]))
    c = res.counts_at(300.0)
    rs = c["roof_scenario"]
    assert rs["label"] == "scenario: 30 % combustible roofs (not observed roofs)"
    assert rs["psi_factor"] == R.ROOF_PSI_FACTOR and rs["seed"] == 99
    assert rs["units_involved_combustible_roof"] <= rs["units_combustible_roof_built"]
    # counts by mechanism unchanged in form
    assert c["units_involved"] == (c["units_front_contact"] + c["units_structure_to_structure"]
                                   + c["units_ember"])
    d = res.involved_detail()
    flagged = sum(1 for e in d if e.get("combustible_roof_scenario"))
    assert flagged == rs["units_involved_combustible_roof"]
    # no roof scenario: no key, no flag
    base = structure_spread_for_grid_run(houses, arr, sched, 300.0, bbox=BBOX, embers=opts)
    assert "roof_scenario" not in base.counts_at(300.0)
    assert not any("combustible_roof_scenario" in e for e in base.involved_detail())
    assert c["units_involved"] >= base.counts_at(300.0)["units_involved"]


def test_roof_scenario_needs_embers():
    with pytest.raises(ValueError):
        structure_spread_for_grid_run(_town(), _arrival(), [(0.0, _Cond(40.0, 270.0))], 60.0,
                                      bbox=BBOX, combustible_roof_share=0.15)


# ------------------------------------------------------------------ attributes


def test_building_vegetation_table_and_detail(tmp_path):
    p = tmp_path / "veg.csv.gz"
    with gzip.open(p, "wt") as f:
        f.write("id," + ",".join(V.VEG_FIELDS) + "\n")
        f.write("7,0.1,0.2,0.3,0.15,0,120\n")
        f.write("3,,,,,,\n")
    veg = V.BuildingVegetation.from_csv(p)
    got = veg.lookup([7, 3, 99, -1])
    np.testing.assert_allclose(got[0], [0.1, 0.2, 0.3, 0.15, 0.0, 120.0], rtol=1e-6)
    assert np.isnan(got[1:]).all()
    assert V.veg_dict(got[0]) == {"cc_0_5": 0.1, "cc_5_10": 0.2, "cc_10_30": 0.3,
                                  "cc_0_10": 0.15, "overhang_frac": 0.0, "dist_stand_1ha_m": 120}
    assert V.veg_dict(got[1]) is None

    houses = [_box(-HALF_M + 20, 0, 10, 10), _box(-HALF_M + 35, 0, 10, 10)]
    src = ListFootprintSource(houses, ids=[7, 3])
    arr = np.full((N, N), np.inf)
    arr[29:31, 0:4] = 0.0
    res = structure_spread_for_grid_run(src, arr, [(0.0, _Cond(40.0, 270.0))], 120.0, bbox=BBOX,
                                        vegetation=veg)
    d = res.involved_detail()
    assert res.counts_at(120.0)["vegetation_attributes"] is True
    with_veg = [e for e in d if "veg" in e]
    assert len(with_veg) == 1 and with_veg[0]["veg"]["cc_10_30"] == 0.3


def test_building_index_ids_align_with_footprints(tmp_path):
    from shapely.geometry import mapping

    from firesim.data.building_index import BuildingIndex

    boxes = [_box(20.0 * i, 0, 10, 10) for i in range(5)]
    nb = [_box(-50, -50, 70, 120), _box(20, -50, 120, 120)]

    def fc(geoms, props):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": mapping(g), "properties": p}
            for g, p in zip(geoms, props)]}

    bp, npth = tmp_path / "b.geojson", tmp_path / "n.geojson"
    bp.write_text(json.dumps(fc(boxes, [{"id": 100 + i} for i in range(5)])))
    npth.write_text(json.dumps(fc(nb, [{"neighbourhood": "A"}, {"neighbourhood": "B"}])))
    idx = BuildingIndex(str(bp), str(npth))
    lat, lng = FRAME.to_latlng(np.array([-5.0, 85.0]), np.array([-5.0, 15.0]))
    b = (float(lat[0]), float(lat[1]), float(lng[0]), float(lng[1]))
    geoms, ids = idx.footprints_intersecting(b), idx.ids_intersecting(b)
    assert len(geoms) == len(ids) == 5  # bounds intersecting x -5..85 (the last box 80-90)
    for g, i in zip(geoms, ids):
        x, _ = FRAME.to_local(g.centroid.y, g.centroid.x)
        assert int(i) == 100 + int(round((float(x) - 5.0) / 20.0))


# ------------------------------------------------------------------ shipped data


@pytest.mark.skipif(not (DATA / "edmonton_building_vegetation.csv.gz").exists(),
                    reason="vegetation table not present")
def test_shipped_edmonton_vegetation_table():
    veg = V.BuildingVegetation.from_csv(DATA / "edmonton_building_vegetation.csv.gz")
    assert len(veg) == 346_238
    covers = veg.values[:, :5]
    ok = covers[np.isfinite(covers)]
    assert ok.min() >= 0.0 and ok.max() <= 1.0
    assert np.nanmedian(veg.values[:, V.VEG_FIELDS.index("cc_0_10")]) == pytest.approx(0.05, abs=0.01)


@pytest.mark.skipif(not (DATA / "edmonton_canopy_5m.tif").exists(), reason="canopy raster not present")
def test_shipped_edmonton_canopy_raster_reads_a_run_window():
    cc = V.CanopyCover(DATA / "edmonton_canopy_5m.tif")
    w = cc.window(53.45, 53.47, -113.58, -113.55)  # a south-west Edmonton residential area
    assert w.has_data and w.share.shape[0] > 100
    ok = w.share[np.isfinite(w.share)]
    assert 0.0 <= ok.min() and ok.max() <= 1.0 and 0.02 < ok.mean() < 0.6
    far = cc.window(10.0, 10.01, 10.0, 10.01)
    assert not far.has_data
