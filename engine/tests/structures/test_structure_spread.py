"""Building-to-building spread over the unit graph (Hamada option; spec §3-4)."""

from __future__ import annotations

import math

import numpy as np
import pytest
import shapely

from firesim.structures.hamada import crossing_time_min
from firesim.structures.spread import (
    LABEL,
    SOURCE_FRONT,
    SOURCE_NONE,
    SOURCE_STRUCTURE,
    WindPeriod,
    building_cell_contact_times,
    contact_offsets,
    footprint_contact_times,
    hamada_spread,
)
from firesim.structures.units import LocalFrame, build_units

FRAME = LocalFrame(53.5, -113.5)
EAST = WindPeriod(0.0, 64.08, 270.0)  # from the west, 17.8 m/s: spread toward +x


def square(x0, y0, side):
    lat, lng = FRAME.to_latlng(np.array([x0, x0 + side]), np.array([y0, y0 + side]))
    return shapely.box(float(lng[0]), float(lat[0]), float(lng[1]), float(lat[1]))


def row_units(n=5, side=10.0, gap=10.0, cutoff=30.0):
    return build_units([square(i * (side + gap), 0.0, side) for i in range(n)],
                       frame=FRAME, neighbour_cutoff_m=cutoff)


def only_first_lit(n):
    t = np.full(n, np.inf)
    t[0] = 0.0
    return t


def test_row_downwind_times_are_hamada_crossings():
    units = row_units(n=5, gap=10.0, cutoff=15.0)  # only adjacent pairs
    res = hamada_spread(units, only_first_lit(5), [EAST], duration_min=600)
    step = float(crossing_time_min(10.0, 10.0, 17.8, 1.0))  # = T_d, 1.6947 min
    np.testing.assert_allclose(res.t_min, step * np.arange(5), rtol=1e-6)
    assert res.source[0] == SOURCE_FRONT and np.all(res.source[1:] == SOURCE_STRUCTURE)
    np.testing.assert_array_equal(res.parent, [-1, 0, 1, 2, 3])


def test_second_neighbour_shortcut_within_cutoff():
    # With a 30 m cutoff unit 0 also reaches unit 2 directly (26 m... here 30 m gap edge-to-edge)
    units = row_units(n=3, gap=8.0, cutoff=30.0)
    res = hamada_spread(units, only_first_lit(3), [EAST], duration_min=600)
    direct = float(crossing_time_min(10.0, 26.0, 17.8, 1.0))
    two_steps = 2 * float(crossing_time_min(10.0, 8.0, 17.8, 1.0))
    assert res.t_min[2] == pytest.approx(min(direct, two_steps), rel=1e-6)


def test_no_spread_beyond_the_cutoff():
    units = row_units(n=4, gap=40.0, cutoff=30.0)
    res = hamada_spread(units, only_first_lit(4), [EAST], duration_min=10_000)
    assert res.t_min[0] == 0.0 and np.all(np.isinf(res.t_min[1:]))
    assert np.all(res.source[1:] == SOURCE_NONE)
    counts = res.counts_at(10_000)
    assert counts["units_involved"] == 1 and counts["units_structure_to_structure"] == 0


def test_monotone_in_wind():
    units = build_units([square(i * 16.0, j * 16.0, 12.0) for i in range(12) for j in range(12)],
                        frame=FRAME)
    t0 = np.full(len(units), np.inf)
    t0[np.argmin(units.x + units.y)] = 0.0  # south-west corner
    last, involved = [], []
    for kmh in (0.0, 10.0, 25.0, 40.0, 60.0, 80.0):
        res = hamada_spread(units, t0, [WindPeriod(0.0, kmh, 225.0)], duration_min=60.0)
        involved.append(res.counts_at(60.0)["units_involved"])
        full = hamada_spread(units, t0, [WindPeriod(0.0, kmh, 225.0)], duration_min=1e6)
        last.append(full.t_min.max())
    assert involved == sorted(involved) and involved[-1] > involved[0]
    assert all(b < a for a, b in zip(last, last[1:]))


def test_upwind_slower_than_downwind():
    units = row_units(n=5, cutoff=15.0)
    t0 = np.full(5, np.inf)
    t0[2] = 0.0
    res = hamada_spread(units, t0, [EAST], duration_min=600)
    assert res.t_min[3] < res.t_min[1]
    assert res.t_min[3] == pytest.approx(float(crossing_time_min(10, 10, 17.8, 1.0)), rel=1e-6)
    assert res.t_min[1] == pytest.approx(float(crossing_time_min(10, 10, 17.8, -1.0)), rel=1e-6)


def test_wind_change_mid_crossing_accumulates_progress():
    units = row_units(n=2, cutoff=15.0)
    fast = float(crossing_time_min(10, 10, 17.8, 1.0))
    slow = float(crossing_time_min(10, 10, 17.8, -1.0))  # wind reverses: now upwind
    switch = fast / 2.0
    wind = [EAST, WindPeriod(switch, 64.08, 90.0)]
    res = hamada_spread(units, only_first_lit(2), wind, duration_min=600)
    # half the crossing at the downwind rate, the other half at the upwind rate
    assert res.t_min[1] == pytest.approx(switch + 0.5 * slow, rel=1e-6)


def test_nothing_after_the_run_ends():
    units = row_units(n=5, cutoff=15.0)
    step = float(crossing_time_min(10, 10, 17.8, 1.0))
    res = hamada_spread(units, only_first_lit(5), [EAST], duration_min=2.5 * step)
    assert np.isfinite(res.t_min[:3]).all() and np.isinf(res.t_min[3:]).all()
    t0 = np.array([0.0, np.inf, np.inf, np.inf, 999.0])
    res = hamada_spread(units, t0, [EAST], duration_min=10.0)
    assert res.source[4] != SOURCE_FRONT


def test_counts_over_time_and_label():
    units = row_units(n=5, cutoff=15.0)
    res = hamada_spread(units, only_first_lit(5), [EAST], duration_min=600)
    step = float(crossing_time_min(10, 10, 17.8, 1.0))
    c = res.counts_at(1.5 * step)
    assert c["label"] == LABEL == "illustrative — not validated in Canada"
    assert c["model"] == "hamada"
    assert (c["units_in_run"], c["units_front_contact"], c["units_structure_to_structure"],
            c["units_involved"]) == (5, 1, 1, 2)
    assert c["neighbour_cutoff_m"] == 15.0 and c["combustible_fraction"] == 1.0


def test_footprint_contact_times_diagnostic():
    units = row_units(n=3, gap=20.0)  # footprints x 0-10, 30-40, 60-70; y 0-10
    # 20 m cells; centres at x = 15 (edge at 5 m from unit 0, 5 m from unit 1), x = 95
    cx = np.array([15.0, 95.0, -500.0])
    cy = np.array([5.0, 5.0, 5.0])
    arr = np.array([12.0, 30.0, 1.0])
    t = footprint_contact_times(units, cx, cy, arr, 20.0, contact_m=10.0)
    # cell edge 15-10=5 m beyond... unit 0: distance 15-10 = 5, minus half cell 10 -> <= 10
    assert t[0] == 12.0 and t[1] == 12.0
    # unit 2 (60-70): cell at 95 is 25 m away, minus 10 = 15 > 10
    assert math.isinf(t[2])
    t = footprint_contact_times(units, cx, cy, arr, 20.0, contact_m=20.0)
    assert t[2] == 30.0
    t = footprint_contact_times(units, cx, cy, np.full(3, np.inf), 20.0)
    assert np.all(np.isinf(t))


# --- Front contact from the building's own grid cells (spec §3, 2026-10-09) -----------------

CELL = 50.0  # engine grid cell, metres (in FRAME)


def _grid(rows=10, cols=10):
    """Bounds of a rows x cols grid of 50 m cells in FRAME, x 0..cols*50 east, y 0..rows*50
    north; row 0 is the north row."""
    lat, lng = FRAME.to_latlng(np.array([0.0, cols * CELL]), np.array([0.0, rows * CELL]))
    return (float(lat[0]), float(lat[1]), float(lng[0]), float(lng[1]))


def _cell_xy(r, c, rows=10):
    """South-west corner (x, y) of cell (row r, col c)."""
    return c * CELL, (rows - 1 - r) * CELL


def _arrival_west_burned(rows=10, cols=10, burned_cols=4):
    """Columns 0..burned_cols-1 burned at 10 * col + row minutes; the rest unburned."""
    r, c = np.mgrid[0:rows, 0:cols]
    return np.where(c < burned_cols, 10.0 * c + r, np.inf)


def _units(*boxes):
    return build_units([square(x, y, s) for x, y, s in boxes], frame=FRAME)


def test_contact_offsets_are_the_8_neighbourhood_below_one_cell():
    for contact in (0.0, 5.0, 10.0, 20.0, 49.9):
        off = contact_offsets(contact, CELL, CELL)
        assert len(off) == 9 and np.abs(off).max() == 1
    off = {tuple(o) for o in contact_offsets(60.0, CELL, CELL)}
    assert (0, 2) in off and (2, 1) in off and (2, 2) not in off  # gap 50 / 50 / 70.7 m


def test_contact_is_independent_of_the_position_inside_the_cell():
    # Building cell (row 5, col 4), masked (unburned); the front stops at col 3 (x <= 200 m)
    bounds, arr = _grid(), _arrival_west_burned()
    x0, y0 = _cell_xy(5, 4)
    offsets = [(1.0, 1.0), (19.0, 20.0), (39.0, 39.0), (1.0, 39.0), (39.0, 1.0)]
    units = _units(*[(x0 + dx, y0 + dy, 10.0) for dx, dy in offsets])
    t = building_cell_contact_times(units, arr, bounds, contact_m=10.0)
    # earliest burned neighbour: col 3, rows 4-6 -> 10 * 3 + 4 = 34 min, for every offset
    np.testing.assert_array_equal(t, np.full(len(offsets), 34.0))
    # The previous rule (nearest burned cell edge within 10 m of the footprint) depended on
    # the offset: the house 1 m from the cell edge is reached, the one 39 m from it is not
    r, c = np.nonzero(np.isfinite(arr))
    cx, cy = (c + 0.5) * CELL, (9 - r + 0.5) * CELL
    old = footprint_contact_times(units, cx, cy, arr[r, c], CELL, contact_m=10.0)
    assert np.isfinite(old[0]) and np.isinf(old[2])


def test_contact_distance_below_one_cell_gives_one_outcome():
    bounds, arr = _grid(), _arrival_west_burned()
    x0, y0 = _cell_xy(5, 4)
    units = _units((x0 + 30.0, y0 + 30.0, 12.0))
    out = [building_cell_contact_times(units, arr, bounds, contact_m=m)[0]
           for m in (0.0, 5.0, 10.0, 20.0)]
    assert out == [34.0] * 4


def test_unit_far_from_burned_cells_is_not_reached():
    bounds, arr = _grid(), _arrival_west_burned()
    units = _units((*_cell_xy(5, 8), 10.0), (_cell_xy(5, 5)[0] + 2.0, _cell_xy(5, 5)[1] + 2.0, 10.0))
    t = building_cell_contact_times(units, arr, bounds, contact_m=10.0)
    assert np.all(np.isinf(t))  # col 8, and col 5 (one unburned cell between)
    assert np.all(np.isinf(building_cell_contact_times(units, np.full((10, 10), np.inf), bounds)))


def test_no_contact_across_non_fuel_one_or_more_cells_wide():
    # Burned cols 0-3, non-fuel road (col 4) or river (cols 4-5); houses hug the far bank
    bounds = _grid()
    for width in (1, 2):
        arr = _arrival_west_burned()
        xb = (4 + width) * CELL + 0.5  # 0.5 m from the non-fuel strip
        units = _units((xb, _cell_xy(5, 0)[1] + 20.0, 10.0))
        assert np.isinf(building_cell_contact_times(units, arr, bounds, contact_m=10.0)[0])


def test_footprint_over_several_cells_uses_only_the_cells_it_touches():
    bounds, arr = _grid(), _arrival_west_burned()
    # 20 m house straddling cols 4 / 5 in row 5: its col 4 cell neighbours the burned col 3
    x0, y0 = _cell_xy(5, 4)
    units = _units((x0 + 40.0, y0 + 10.0, 20.0))
    assert building_cell_contact_times(units, arr, bounds)[0] == 34.0
    # L-shaped house whose bounding box covers cells (5,5), (5,6), (6,5), (6,6) but whose
    # outline does not touch (5,6); only cell (4,7) is burned, a neighbour of (5,6) alone
    xs, ys = _cell_xy(6, 5)  # SW corner of cell (6, 5)
    ell = shapely.Polygon([(xs + 40, ys + 40), (xs + 60, ys + 40), (xs + 60, ys + 45),
                           (xs + 45, ys + 45), (xs + 45, ys + 60), (xs + 40, ys + 60)])
    lat, lng = FRAME.to_latlng(*np.asarray(ell.exterior.coords).T)
    units = build_units([shapely.Polygon(np.column_stack([lng, lat]))], frame=FRAME)
    one = np.full((10, 10), np.inf)
    one[4, 7] = 12.0
    assert np.isinf(building_cell_contact_times(units, one, bounds)[0])
    one[4, 6] = 15.0  # neighbour of (5, 5), which the L touches
    assert building_cell_contact_times(units, one, bounds)[0] == 15.0


def test_unmasked_building_cell_that_burns_reaches_the_unit():
    # Outside the masked neighbourhoods a building cell can itself burn
    bounds = _grid()
    arr = np.full((10, 10), np.inf)
    arr[5, 4] = 50.0
    x0, y0 = _cell_xy(5, 4)
    units = _units((x0 + 20.0, y0 + 20.0, 10.0))
    assert building_cell_contact_times(units, arr, bounds)[0] == 50.0


def test_contact_of_a_cell_or_more_reaches_further():
    bounds, arr = _grid(), _arrival_west_burned()
    x0, y0 = _cell_xy(5, 5)  # col 5: one unburned cell (col 4) from the front
    units = _units((x0 + 20.0, y0 + 20.0, 10.0))
    assert np.isinf(building_cell_contact_times(units, arr, bounds, contact_m=49.0)[0])
    assert building_cell_contact_times(units, arr, bounds, contact_m=50.0)[0] == 34.0


def test_empty_wind_rejected():
    with pytest.raises(ValueError):
        hamada_spread(row_units(2), only_first_lit(2), [], duration_min=10)


def _grid_and_houses(n=80, cell_m=25.0):
    """C-2 grid, 25 m cells, wind from the west. Three 20 m houses in an east-west row at
    rows 40-41: cols 58-59 (in the fire's path), then 8 m and 16 m gaps further east."""
    from firesim.fbp.constants import FuelType
    from firesim.spread.huygens import FuelGrid

    lat0, lng0 = 53.5, -113.5
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(lat0)))
    fuel = [[FuelType.C2] * n for _ in range(n)]
    g = FuelGrid(fuel, lat0 - dlat / 2, lat0 + dlat / 2, lng0 - dlng / 2, lng0 + dlng / 2, n, n)
    cell_lat, cell_lng = dlat / n, dlng / n
    m_lng = cell_lng / cell_m
    houses = []
    x = g.lng_min + 58 * cell_lng
    for gap in (0.0, 8.0, 16.0):
        x += gap * m_lng
        houses.append(shapely.box(x, g.lat_max - 42 * cell_lat, x + 2 * cell_lng, g.lat_max - 40 * cell_lat))
        x += 2 * cell_lng
    for r in (40, 41):
        for c in range(58, min(n, 66)):
            fuel[r][c] = None  # buildings are non-fuel on the grid, as with the building mask
    return g, houses


def test_simulator_structure_spread_is_opt_in_and_labelled():
    from firesim.spread.simulator import Simulator
    from firesim.types import SimulationConfig, WeatherInput

    g, houses = _grid_and_houses()
    config = SimulationConfig(53.5, -113.5, WeatherInput(25.0, 25.0, 30.0, 270.0, 0.0), 2.0,
                              snapshot_interval_minutes=30.0, ffmc=92.0, dmc=40.0, dc=300.0)
    off = list(Simulator(config, fuel_grid=g, building_footprints=houses).run())
    assert all(f.structure_spread is None for f in off)

    on = list(Simulator(config, fuel_grid=g, building_footprints=houses,
                        structure_spread=True).run())
    last = on[-1].structure_spread
    assert last["label"] == "illustrative — not validated in Canada"
    assert last["units_in_run"] == 3
    assert last["units_front_contact"] >= 1
    assert last["units_involved"] == 3
    assert on[0].structure_spread["units_involved"] == 0
    involved = [f.structure_spread["units_involved"] for f in on]
    assert involved == sorted(involved)
    # Exposure is unchanged by the option
    assert [f.building_exposure for f in on] == [f.building_exposure for f in off]


def test_simulator_without_buildings_reports_none():
    from firesim.spread.simulator import Simulator
    from firesim.types import SimulationConfig, WeatherInput

    g, _ = _grid_and_houses()
    config = SimulationConfig(53.5, -113.5, WeatherInput(25.0, 25.0, 30.0, 270.0, 0.0), 0.5,
                              ffmc=92.0, dmc=40.0, dc=300.0)
    frames = list(Simulator(config, fuel_grid=g, structure_spread=True).run())
    assert all(f.structure_spread is None for f in frames)
