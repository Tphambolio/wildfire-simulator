"""Active / inactive edges of an observed starting fire (grid model).

An RPAS thermal flight shows which parts of a perimeter are still active. With
``active_edges`` only starting cells near them spread; the rest of the starting fire is burned
out. Known answers on uniform C-2 with a west wind (spread to the east):

- marking the whole perimeter active reproduces the default run exactly;
- marking only the east (head) edge active stops the backing fire on the west side, while the
  head run east is the same as with the whole perimeter active;
- active edges that miss the starting fire leave nothing burning.
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import active_mask, run_cellular_simulation
from firesim.spread.huygens import FireVertex, FuelGrid, SpreadConditions
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

N, CELL = 80, 30.0
LAT0, LNG0 = 55.0, -115.0
DLAT = CELL / 111320.0
DLNG = CELL / (111320.0 * math.cos(math.radians(LAT0)))
COND = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=91.0, dmc=40.0, dc=300.0)


def _grid():
    lat_max = LAT0 + N / 2 * DLAT
    lng_min = LNG0 - N / 2 * DLNG
    return FuelGrid(fuel_types=[[FuelType.C2] * N for _ in range(N)], lat_min=lat_max - N * DLAT,
                    lat_max=lat_max, lng_min=lng_min, lng_max=lng_min + N * DLNG, rows=N, cols=N)


def _cell_lng(col):  # west edge of column ``col``
    return LNG0 - N / 2 * DLNG + col * DLNG


def _cell_lat(row):  # north edge of row ``row``
    return LAT0 + N / 2 * DLAT - row * DLAT


# Starting fire: rows 30-49, columns 20-39 (a 600 m square)
R0, R1, C0, C1 = 30, 50, 20, 40
SQUARE = [(_cell_lat(R0), _cell_lng(C0)), (_cell_lat(R0), _cell_lng(C1)),
          (_cell_lat(R1), _cell_lng(C1)), (_cell_lat(R1), _cell_lng(C0)),
          (_cell_lat(R0), _cell_lng(C0))]
EAST_EDGE = {"type": "LineString",
             "coordinates": [[_cell_lng(C1), _cell_lat(R0)], [_cell_lng(C1), _cell_lat(R1)]]}


def _run(active=None, buffer_m=None, minutes=40.0):
    g = _grid()
    frames = run_cellular_simulation(
        {"ignition_lat": LAT0, "ignition_lng": LNG0, "duration_hours": minutes / 60.0}, g, COND,
        initial_perimeter=SQUARE, snapshot_interval_minutes=minutes, compute_perimeter=False,
        active_edges=active, active_edge_buffer_m=buffer_m,
    )
    return frames[-1].arrival


def test_whole_perimeter_active_is_the_default():
    ring = [[lng, lat] for lat, lng in SQUARE]
    base = _run()
    same = _run({"type": "Polygon", "coordinates": [ring]}, buffer_m=0.0)
    np.testing.assert_array_equal(base, same)


def test_inactive_back_edge_does_not_spread():
    base = _run()
    head_only = _run(EAST_EDGE)
    start = np.zeros((N, N), bool)
    start[R0:R1, C0:C1] = True
    # starting cells all count as burned at t = 0
    assert np.all(head_only[start] == 0.0)
    west = np.zeros((N, N), bool)
    west[R0:R1, :C0] = True
    assert np.isfinite(base[west]).sum() > 0  # the default run backs into the west fuel
    assert np.isfinite(head_only[west]).sum() == 0  # the inactive west edge holds
    # the head run east is unchanged (within one cell)
    def east_reach(a):
        cols = np.nonzero(np.isfinite(a[R0:R1]).any(axis=0))[0]
        return cols.max()
    assert abs(east_reach(head_only) - east_reach(base)) <= 1
    assert east_reach(base) > C1 + 3


def test_active_edges_missing_the_fire_leave_nothing_burning():
    far = {"type": "Point", "coordinates": [_cell_lng(2), _cell_lat(2)]}
    a = _run(far)
    burned = np.isfinite(a)
    assert burned.sum() == (R1 - R0) * (C1 - C0)  # only the starting fire
    assert np.all(a[burned] == 0.0)


def test_active_mask_buffer_is_metric():
    pt = {"type": "Point", "coordinates": [_cell_lng(40) + DLNG / 2, _cell_lat(40) - DLAT / 2]}
    lat_max = LAT0 + N / 2 * DLAT
    lng_min = LNG0 - N / 2 * DLNG
    dx = DLNG * 111320.0 * math.cos(math.radians(LAT0))
    m0 = active_mask(pt, 0.0, N, N, lat_max, lng_min, DLAT, DLNG, dx, CELL)
    assert m0.sum() == 1 and m0[40, 40]
    m = active_mask(pt, 100.0, N, N, lat_max, lng_min, DLAT, DLNG, dx, CELL)
    rr, cc = np.nonzero(m)
    d = np.hypot((rr - 40) * CELL, (cc - 40) * dx)
    assert d.max() <= 100.0 + 1e-6
    assert m.sum() == int((np.hypot(*np.mgrid[-5:6, -5:6] * CELL) <= 100.0).sum())


def test_simulator_passes_active_edges():
    g = _grid()
    cfg = SimulationConfig(ignition_lat=LAT0, ignition_lng=LNG0,
                           weather=WeatherInput(20.0, 30.0, 20.0, 270.0, 0.0),
                           duration_hours=40.0 / 60.0, snapshot_interval_minutes=40.0,
                           ffmc=91.0, dmc=40.0, dc=300.0)
    front = [FireVertex(lat=a, lng=b) for a, b in SQUARE]
    full = list(Simulator(cfg, g, initial_front=front).run())[-1]
    head = list(Simulator(cfg, g, initial_front=front, active_edges=EAST_EDGE).run())[-1]
    assert head.area_ha < full.area_ha
    west_lng = _cell_lng(C0)
    assert min(c["lng"] for c in full.burned_cells) < west_lng
    assert min(c["lng"] for c in head.burned_cells) > west_lng
