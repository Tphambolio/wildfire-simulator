"""Grid model: starting from an existing fire (RPAS perimeter, multi-day) and frame outlines."""

import math

import shapely

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FireVertex, FuelGrid, SpreadConditions
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5
COND = SpreadConditions(wind_speed=20.0, wind_direction=270.0, ffmc=92.0, dmc=40.0, dc=300.0)


def grid(n=100, cell_m=25.0):
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(LAT0)))
    return FuelGrid([[FuelType.C2] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                    LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


def square(lat, lng, half_m):
    dlat = half_m / 111320.0
    dlng = half_m / (111320.0 * math.cos(math.radians(lat)))
    return [(lat - dlat, lng - dlng), (lat - dlat, lng + dlng), (lat + dlat, lng + dlng),
            (lat + dlat, lng - dlng)]


def test_starts_from_initial_perimeter_not_ignition():
    g = grid()
    # observed fire 300 m north of the configured ignition point
    perim = square(LAT0 + 300 / 111320.0, LNG0, 100.0)
    frames = run_cellular_simulation(
        dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=0.5), g, COND,
        initial_perimeter=perim, snapshot_interval_minutes=15.0,
    )
    first = frames[0]
    assert 3.0 < first.area_ha < 5.5  # the 200 m x 200 m square (4 ha) on 25 m cells
    assert all(c.lat > LAT0 + 100 / 111320.0 for c in first.burned_cells)
    assert frames[-1].area_ha > first.area_ha


def test_outline_encloses_burned_area():
    g = grid()
    frames = run_cellular_simulation(dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=1.0),
                                     g, COND)
    last = frames[-1]
    poly = shapely.Polygon([(lng, lat) for lat, lng in last.perimeter])
    assert poly.is_valid
    inside = sum(poly.contains(shapely.Point(c.lng, c.lat)) for c in last.burned_cells)
    assert inside == len(last.burned_cells)
    # polygon area (degrees -> m2 approx) matches the burned-cell area within 10 %
    m2 = poly.area * 111320.0 ** 2 * math.cos(math.radians(LAT0))
    assert abs(m2 / 1e4 - last.area_ha) / last.area_ha < 0.1


def _config(hours):
    return SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), hours,
                            snapshot_interval_minutes=30.0, ffmc=92.0, dmc=40.0, dc=300.0)


def test_simulator_perimeter_override_uses_observed_perimeter():
    perim = square(LAT0 + 300 / 111320.0, LNG0, 100.0)
    front = [FireVertex(lat, lng) for lat, lng in perim]
    frames = list(Simulator(_config(0.5), fuel_grid=grid(), initial_front=front).run())
    assert 3.0 < frames[0].area_ha < 5.5
    assert len(frames[0].perimeter) >= 4


def test_multiday_continuation_keeps_burned_area():
    day1 = list(Simulator(_config(1.0), fuel_grid=grid()).run())[-1]
    burned = [(c["lat"], c["lng"]) for c in day1.burned_cells]
    day2 = list(Simulator(_config(1.0), fuel_grid=grid(), initial_burned=burned).run())
    assert day2[0].area_ha == day1.area_ha
    assert day2[-1].area_ha > day1.area_ha
