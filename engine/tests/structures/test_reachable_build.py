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
    burnout_minutes,
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


def _full(footprints, arrival, wind, duration, cutoff=30.0, burnout=True):
    """Reference: units for every footprint in the grid box (the pre-fix build)."""
    units = build_units(footprints, neighbour_cutoff_m=cutoff, bbox=BBOX, frame=FRAME)
    t_front = building_cell_contact_times(units, arrival, BBOX, 10.0)
    return units, hamada_spread(units, t_front, wind, duration_min=duration,
                                burnout_min=burnout_minutes(150) if burnout else None)


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


@pytest.mark.parametrize("burnout", [True, False])
@pytest.mark.parametrize("speed,duration", [(20.0, 60.0), (40.0, 240.0), (60.0, 600.0)])
def test_reachable_build_matches_the_whole_area_build(speed, duration, burnout):
    houses = _lattice()
    arrival = _arrival()
    sched = [(0.0, _Cond(speed, 270.0))]
    wind = [WindPeriod(0.0, speed, 270.0)]
    _, ref = _full(houses, arrival, wind, duration, burnout=burnout)
    src = _CountingSource(houses)
    res = structure_spread_for_grid_run(src, arrival, sched, duration, bbox=BBOX, burnout=burnout)
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
        assert set(u) == {"id", "t_h", "t_out_h", "mechanism", "source_id", "polygon"}
        assert u["t_out_h"] == pytest.approx(u["t_h"] + 66.0 / 60.0, abs=2e-3)  # 150 kW/m² fire
        ring = u["polygon"][0]
        assert ring[0] == ring[-1] and len(ring) >= 4
        lng, lat = ring[0]
        assert LNG_MIN <= lng <= LNG_MAX and LAT_MIN <= lat <= LAT_MAX
        if u["mechanism"] == "front":
            assert u["source_id"] is None
        else:
            # The source was involved first
            assert u["source_id"] is not None and d[u["source_id"]]["t_h"] <= u["t_h"]


def test_front_contact_matches_an_independent_all_touched_raster_check():
    """Front contact time = earliest arrival over the 3x3 neighbourhood of the cells the
    footprint touches (rasterio all_touched, as the building mask), never from unburned cells
    (inf), and a unit is reached at time 0 only next to the ignition cell (2026-10-10 check of
    the house-to-house fixture)."""
    from rasterio import features
    from rasterio.transform import from_bounds

    rng = np.random.default_rng(7)
    arrival = np.full((N, N), np.inf)
    rr, cc = np.mgrid[0:N, 0:N]
    blob = np.hypot(rr - 40, cc - 30) < 9
    arrival[blob] = np.hypot(rr - 40, cc - 30)[blob] * 7.0  # ignition cell (40, 30) at 0
    houses = []
    for _ in range(400):
        x, y = rng.uniform(-HALF_M + 20, HALF_M - 40, 2)
        w, h = rng.uniform(6, 30, 2)
        la, ln = FRAME.to_latlng(np.array([y, y + h]), np.array([x, x + w]))
        houses.append(shapely.box(float(ln[0]), float(la[0]), float(ln[1]), float(la[1])))
    units = build_units(houses, bbox=BBOX, frame=FRAME)
    t_front = building_cell_contact_times(units, arrival, BBOX, 10.0)

    pad = np.pad(arrival, 1, constant_values=np.inf)
    near = np.min([pad[1 + dr:1 + dr + N, 1 + dc:1 + dc + N] for dr in (-1, 0, 1) for dc in (-1, 0, 1)], axis=0)
    tr = from_bounds(BBOX[2], BBOX[0], BBOX[3], BBOX[1], N, N)
    cent = shapely.centroid(np.asarray(houses, dtype=object))
    kept = [g for g, c in zip(houses, cent)
            if BBOX[0] <= c.y <= BBOX[1] and BBOX[2] <= c.x <= BBOX[3]]
    assert len(kept) == len(units)
    reached = 0
    for i, g in enumerate(kept):
        m = features.rasterize([(g, 1)], out_shape=(N, N), transform=tr, all_touched=True).astype(bool)
        expect = near[m].min() if m.any() else np.inf
        assert t_front[i] == pytest.approx(expect) or (np.isinf(expect) and np.isinf(t_front[i])), i
        reached += np.isfinite(expect)
        if t_front[i] == 0.0:
            rows, cols = np.nonzero(m)
            assert np.min(np.maximum(np.abs(rows - 40), np.abs(cols - 30))) <= 1
    assert reached > 10
