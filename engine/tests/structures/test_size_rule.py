"""Size rule for headline building counts (decision 2026-10-12, [R40]; spec §2.2).

Footprints under ``SMALL_FOOTPRINT_M2`` (20 m²) are outbuildings: they stay in the unit graph
and carry fire, but are left out of the headline counts (structure spread, exposure,
inside-perimeter) and reported separately. ``small_footprint_m2=0`` restores the former
payload exactly."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from firesim.structures import units as units_mod
from firesim.structures.spread import structure_spread_for_grid_run
from firesim.structures.units import (
    SMALL_FOOTPRINT_M2,
    LocalFrame,
    build_units,
    footprint_areas_m2,
    resolve_small_footprint_m2,
)

N = 60
CELL_M = 25.0
LAT0, LNG0 = 53.5, -113.5
FRAME = LocalFrame(LAT0, LNG0)
HALF_M = N * CELL_M / 2
LAT_MIN, LNG_MIN = FRAME.to_latlng(-HALF_M, -HALF_M)
LAT_MAX, LNG_MAX = FRAME.to_latlng(HALF_M, HALF_M)
BBOX = (float(LAT_MIN), float(LAT_MAX), float(LNG_MIN), float(LNG_MAX))


class _Cond:
    def __init__(self, ws, wd):
        self.wind_speed, self.wind_direction = ws, wd


def _box(x, y, side):
    la, ln = FRAME.to_latlng(np.array([x, x + side]), np.array([y, y + side]))
    return shapely.box(float(ln[0]), float(la[0]), float(ln[1]), float(la[1]))


def _street(n_houses=12, gap_m=14.0, house=12.0, shed=3.5):
    """A west-east row of 12 m houses (144 m²) 14 m apart with a 3.5 m shed (12.25 m²) in
    the middle of each gap (5.25 m from each house)."""
    houses, sheds = [], []
    x = -HALF_M + 60.0
    for _ in range(n_houses):
        houses.append(_box(x, 0.0, house))
        sx = x + house + (gap_m - shed) / 2
        sheds.append(_box(sx, (house - shed) / 2, shed))
        x += house + gap_m
    return houses, sheds[:-1]


def _arrival():
    """Front burns the west end of the grid up to the first house."""
    a = np.full((N, N), np.inf)
    rows = slice(N // 2 - 3, N // 2 + 3)
    a[rows, 0:4] = np.arange(4)[None, :] * 3.0
    return a


def _run(footprints, **kw):
    schedule = [(0.0, _Cond(30.0, 270.0))]
    return structure_spread_for_grid_run(footprints, _arrival(), schedule, 240.0, bbox=BBOX, **kw)


def test_default_threshold_and_resolution(monkeypatch):
    assert SMALL_FOOTPRINT_M2 == 20.0
    assert resolve_small_footprint_m2(None) == 20.0
    assert resolve_small_footprint_m2(0) == 0.0
    monkeypatch.setattr(units_mod, "SMALL_FOOTPRINT_M2", 40.0)  # read at call time
    assert resolve_small_footprint_m2(None) == 40.0
    with pytest.raises(ValueError):
        resolve_small_footprint_m2(-1)


def test_footprint_areas_are_square_metres():
    a = footprint_areas_m2([_box(0, 0, 10.0), _box(50, 50, 4.0), shapely.Polygon()])
    assert a[0] == pytest.approx(100.0, rel=1e-3)
    assert a[1] == pytest.approx(16.0, rel=1e-3)
    assert a[2] == 0.0


def test_outbuildings_carry_fire_but_are_counted_separately():
    houses, sheds = _street()
    fps = houses + sheds
    legacy = _run(fps, small_footprint_m2=0)
    rule = _run(fps)  # default 20 m²: count_separately
    # Same spread: the rule only changes what is counted.
    np.testing.assert_array_equal(legacy.t_min, rule.t_min)
    c0, c1 = legacy.counts_at(240.0), rule.counts_at(240.0)
    assert "units_small_involved" not in c0 and "small_footprint_m2" not in c0
    n_small = int(np.sum(np.isfinite(rule.t_min) & rule.small))
    assert n_small > 0
    assert c1["units_small_involved"] == n_small
    assert c1["units_involved"] == c0["units_involved"] - n_small
    assert c1["units_involved_all"] == c0["units_involved"]
    assert c1["units_front_contact"] + c1["units_structure_to_structure"] == c1["units_involved"]
    assert c1["units_burning"] + c1["units_burnt_out"] == c1["units_involved"]
    assert c1["small_footprint_m2"] == 20.0 and c1["small_footprints"] == "count_separately"
    # The map detail keeps every involved unit; outbuildings are flagged.
    det = rule.involved_detail()
    assert len(det) == c1["units_involved_all"]
    assert sum(1 for d in det if d.get("small")) == n_small
    assert not any("small" in d for d in legacy.involved_detail())


def test_sheds_bridge_gaps_houses_alone_do_not():
    """36 m gaps between houses (beyond the 30 m cutoff), a shed in each gap 16 m from both:
    with the sheds in the graph fire passes along the row; with them dropped (the 'exclude'
    sensitivity) it stops at the houses the front reaches — outbuildings carry fire."""
    houses, sheds = _street(gap_m=36.0)
    keep = _run(houses + sheds).counts_at(240.0)
    excl = _run(houses + sheds, small_footprints="exclude").counts_at(240.0)
    only_houses = _run(houses, small_footprint_m2=0).counts_at(240.0)
    assert excl["units_involved"] == only_houses["units_involved"]
    assert excl.get("units_small_involved", 0) == 0
    assert excl["units_structure_to_structure"] == 0
    assert keep["units_involved"] > excl["units_involved"]
    assert keep["units_small_involved"] > 0
    assert keep["units_involved"] <= len(houses)


def test_exclude_mode_drops_small_units_before_the_graph():
    houses, sheds = _street()
    u_all = build_units(houses + sheds, bbox=BBOX, frame=FRAME)
    u_big = build_units(houses + sheds, bbox=BBOX, frame=FRAME, min_area_m2=20.0)
    assert len(u_all) == len(houses) + len(sheds)
    assert len(u_big) == len(houses)
    assert u_big.area_m2.min() >= 20.0
    with pytest.raises(ValueError):
        _run(houses, small_footprints="drop")


def test_no_rule_payload_is_unchanged_for_large_buildings():
    """Every footprint ≥ 20 m²: the rule adds only its own keys."""
    houses, _ = _street()
    c0 = _run(houses, small_footprint_m2=0).counts_at(240.0)
    c1 = _run(houses).counts_at(240.0)
    extra = {"small_footprint_m2", "small_footprints", "units_small_involved", "units_involved_all"}
    assert set(c1) - set(c0) == extra
    assert {k: v for k, v in c1.items() if k not in extra} == c0
    assert c1["units_small_involved"] == 0


def test_simulator_inside_and_exposure_counts_skip_outbuildings():
    from firesim.spread.simulator import Simulator
    from firesim.types import SimulationConfig, WeatherInput

    houses, sheds = _street(n_houses=3)
    fps = houses + sheds
    cfg = SimulationConfig(ignition_lat=LAT0, ignition_lng=LNG0,
                           weather=WeatherInput(wind_speed=10, wind_direction=270,
                                                temperature=20, relative_humidity=30,
                                                precipitation_24h=0.0),
                           duration_hours=1)
    sim = Simulator(cfg, building_footprints=fps)
    sim0 = Simulator(cfg, building_footprints=fps, small_footprint_m2=0)
    assert int(sim._small_building.sum()) == len(sheds)
    assert sim0._small_building is None
    ring = [(LAT_MIN, LNG_MIN), (LAT_MIN, LNG_MAX), (LAT_MAX, LNG_MAX), (LAT_MAX, LNG_MIN)]
    assert sim0._buildings_inside(ring) == len(fps)
    assert sim._buildings_inside(ring) == len(houses)
    # centroids longer than footprints (invalid footprints dropped upstream): subtract by
    # the outbuildings' own centroids
    cents = [(g.centroid.y, g.centroid.x) for g in fps] + [(LAT0, LNG0)]
    sim_c = Simulator(cfg, building_footprints=fps, building_centroids=cents)
    assert sim_c._buildings_inside(ring) == len(houses) + 1

    from firesim.exposure import BuildingExposure

    recs = [BuildingExposure(0.0, 0.0, 50.0, first_within_min={500.0: 1.0, 100.0: 1.0})
            for _ in fps]
    s1 = sim._exposure_summary(recs, 10.0, 0)
    s0 = sim0._exposure_summary(recs, 10.0, 0)
    assert s0["within_500m"] == len(fps) and "small_within_500m" not in s0
    assert s1["within_500m"] == len(houses)
    assert s1["small_within_500m"] == len(sheds)
    assert s1["small_footprint_m2"] == 20
    assert len(sim._counted(recs)) == len(houses)
