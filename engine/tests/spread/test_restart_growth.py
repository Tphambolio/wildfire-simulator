"""A run restarted from its own mid-run perimeter, under identical weather, grows like the
continuous run (owner field test 2026-10-12, item A5: a restart with the whole perimeter
active reached 1,307 ha against 900 ha for one continuous 6 h run).

The engine restart is not the cause: flanks and back spread at their FBP ellipse rates in
both (the level set has no "head front" per edge) and an established fire has no
acceleration to lose. What remains is the restart's front being rebuilt at burned-cell edges
(a perimeter carries no sub-cell position): +1-3 % on uniform fuel, more where fuel varies
(decision row and numbers in docs/PROJECT_RECORD.md). The API-side cause (no building mask
on restarts) is tested in api/tests/test_restart_weather.py.
"""

import math

import pytest

from firesim.data.synthetic_grid import generate_synthetic_fuel_grid
from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FireVertex, FuelGrid
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

LAT0, LNG0 = 55.0, -114.0


def _uniform(fuel, n=240, cell=50.0):
    dlat = n * cell / 111320.0
    dlng = n * cell / (111320.0 * math.cos(math.radians(LAT0)))
    return FuelGrid([[fuel] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                    LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


def _cfg(hours, ws):
    return SimulationConfig(LAT0, LNG0, WeatherInput(20.0, 15.0, ws, 135.0, 0.0), hours,
                            snapshot_interval_minutes=30.0, ffmc=90.0, dmc=15.0, dc=193.0,
                            grass_cure=90.0, percent_conifer=50.0)


def _compare(g, ws, total=4.0, mid=1.5):
    cont = list(Simulator(_cfg(total, ws), fuel_grid=g).run())
    at_mid = next(f for f in cont if abs(f.time_hours - mid) < 1e-6)
    front = [FireVertex(lat, lng) for lat, lng in at_mid.perimeter]
    rest = list(Simulator(_cfg(total - mid, ws), fuel_grid=g, initial_front=front).run())
    return at_mid, cont[-1], rest


@pytest.mark.parametrize("fuel,ws", [(FuelType.C2, 20.0), (FuelType.C2, 47.0)])
def test_uniform_fuel_restart_matches_continuous(fuel, ws):
    at_mid, cont_end, rest = _compare(_uniform(fuel), ws)
    assert rest[0].area_ha == pytest.approx(at_mid.area_ha, rel=0.02)
    ratio = rest[-1].area_ha / cont_end.area_ha
    assert 0.97 < ratio < 1.04, ratio  # measured +1.1 % (20 km/h), +2.2 % (47 km/h)
    # same head rate after the restart (no lost or doubled acceleration)
    assert rest[-1].head_ros_m_min == pytest.approx(cont_end.head_ros_m_min, rel=0.02)


def test_mixed_fuel_restart_within_ten_percent():
    g = generate_synthetic_fuel_grid(ignition_lat=LAT0, ignition_lng=LNG0, radius_km=6.0,
                                     cell_size_m=50.0, seed=7)
    _, cont_end, rest = _compare(g, 47.0)
    ratio = rest[-1].area_ha / cont_end.area_ha
    assert 0.95 < ratio < 1.12, ratio  # measured +6.8 %: front rebuilt at cell edges
