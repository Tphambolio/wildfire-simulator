"""Structure units built only where the spread can reach (2026-10-10 OOM fix), the OOM guard
and the involved-unit detail for the map (spec §2, §9)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from firesim.structures.spread import (
    LABEL,
    NOT_COMPUTED_NOTE,
    ListFootprintSource,
    StructureSpreadSkipped,
    WindPeriod,
    building_cell_contact_times,
    hamada_spread,
    structure_spread_for_grid_run,
)
from firesim.structures.units import LocalFrame, build_units

N = 80  # cells per side
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


def _lattice(spacing=35.0, side=10.0):
    """Houses on a regular lattice over the whole grid (local metres -> lng/lat boxes)."""
    out = []
    for x in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
        for y in np.arange(-HALF_M + 5, HALF_M - side - 5, spacing):
            la, ln = FRAME.to_latlng(np.array([y, y + side]), np.array([x, x + side]))
            out.append(shapely.box(float(ln[0]), float(la[0]), float(ln[1]), float(la[1])))
    return out


def _arrival(rows=slice(36, 44), cols=slice(0, 8)):
    """A small burned block on the west side, arrival rising eastward."""
    a = np.full((N, N), np.inf)
    cc = np.arange(N)[cols]
    a[rows, cols] = (cc * 3.0)[None, :]
    return a


def _full(footprints, arrival, wind, duration, cutoff=30.0):
    """Reference: units for every footprint in the grid box (the pre-fix build)."""
    units = build_units(footprints, neighbour_cutoff_m=cutoff, bbox=BBOX, frame=FRAME)
    t_front = building_cell_contact_times(units, arrival, BBOX, 10.0)
    return units, hamada_spread(units, t_front, wind, duration_min=duration)


class _CountingSource(ListFootprintSource):
    def __init__(self, footprints):
        super().__init__(footprints)
        self.max_requested = 0
        self.calls = 0

    def footprints_intersecting(self, bbox):
        out = super().footprints_intersecting(bbox)
        self.max_requested = max(self.max_requested, len(out))
        self.calls += 1
        return out


@pytest.mark.parametrize("speed,duration", [(20.0, 60.0), (40.0, 240.0), (60.0, 600.0)])
def test_reachable_build_matches_the_whole_area_build(speed, duration):
    houses = _lattice()
    arrival = _arrival()
    sched = [(0.0, _Cond(speed, 270.0))]
    wind = [WindPeriod(0.0, speed, 270.0)]
    _, ref = _full(houses, arrival, wind, duration)
    src = _CountingSource(houses)
    res = structure_spread_for_grid_run(src, arrival, sched, duration, bbox=BBOX)
    for t in (duration / 4, duration / 2, duration):
        assert res.counts_at(t)["units_involved"] == int(np.sum(ref.t_min <= t))
        assert res.counts_at(t)["units_structure_to_structure"] == int(
            np.sum((ref.t_min <= t) & (ref.source == 2)))
    # Same involvement times (as a multiset: unit ids differ between the builds)
    np.testing.assert_allclose(np.sort(res.t_min[np.isfinite(res.t_min)]),
                               np.sort(ref.t_min[np.isfinite(ref.t_min)]))
    c = res.counts_at(duration)
    assert c["units_in_run"] == len(houses) and c["computed"] is True


def test_short_run_builds_far_fewer_units_than_the_run_area():
    """Regression bound for the OOM: a small fire must not build units for the whole area."""
    houses = _lattice()
    src = _CountingSource(houses)
    res = structure_spread_for_grid_run(src, _arrival(), [(0.0, _Cond(20.0, 270.0))], 60.0,
                                        bbox=BBOX)
    c = res.counts_at(60.0)
    assert c["units_in_run"] == len(houses) > 2500
    assert c["units_built"] == src.max_requested
    assert src.max_requested < 0.15 * len(houses)


def test_expansion_when_spread_reaches_the_first_margin():
    houses = _lattice()
    src = _CountingSource(houses)
    res = structure_spread_for_grid_run(src, _arrival(), [(0.0, _Cond(60.0, 270.0))], 600.0,
                                        bbox=BBOX)
    assert src.calls >= 2  # the first box was too small and was grown
    assert res.counts_at(600.0)["units_involved"] > 0


def test_guard_returns_a_labelled_not_computed_result():
    houses = _lattice()
    res = structure_spread_for_grid_run(houses, _arrival(), [(0.0, _Cond(20.0, 270.0))], 60.0,
                                        bbox=BBOX, max_units=10)
    assert isinstance(res, StructureSpreadSkipped)
    c = res.counts_at(60.0)
    assert c["computed"] is False and c["note"] == NOT_COMPUTED_NOTE
    assert c["label"] == LABEL and c["units_involved"] is None
    assert c["units_needed"] > c["max_units"] == 10
    assert res.involved_detail() == []


def test_no_burned_cells_builds_nothing():
    houses = _lattice()
    src = _CountingSource(houses)
    res = structure_spread_for_grid_run(src, np.full((N, N), np.inf), [(0.0, _Cond(20.0, 270.0))],
                                        60.0, bbox=BBOX)
    assert src.calls == 0
    c = res.counts_at(60.0)
    assert c["units_involved"] == 0 and c["units_in_run"] == len(houses)
    assert res.involved_detail() == []


def test_involved_detail_only_involved_units_with_time_and_mechanism():
    houses = _lattice()
    res = structure_spread_for_grid_run(houses, _arrival(), [(0.0, _Cond(40.0, 270.0))], 120.0,
                                        bbox=BBOX)
    c = res.counts_at(120.0)
    d = res.involved_detail()
    assert len(d) == c["units_involved"] > c["units_front_contact"] > 0
    assert sum(u["mechanism"] == "front" for u in d) == c["units_front_contact"]
    assert sum(u["mechanism"] == "b2b" for u in d) == c["units_structure_to_structure"]
    assert [u["id"] for u in d] == list(range(len(d)))
    times = [u["t_h"] for u in d]
    assert times == sorted(times) and times[-1] <= 2.0
    for u in d:
        assert set(u) == {"id", "t_h", "mechanism", "source_id", "polygon"}
        ring = u["polygon"][0]
        assert ring[0] == ring[-1] and len(ring) >= 4
        lng, lat = ring[0]
        assert LNG_MIN <= lng <= LNG_MAX and LAT_MIN <= lat <= LAT_MAX
        if u["mechanism"] == "front":
            assert u["source_id"] is None
        else:
            # The source was involved first
            assert u["source_id"] is not None and d[u["source_id"]]["t_h"] <= u["t_h"]
