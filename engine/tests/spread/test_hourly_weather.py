"""Hourly weather stream: schedule construction and its effect on both spread models."""

import math

import pytest

from firesim.fbp.constants import FuelType
from firesim.fwi.calculator import hourly_ffmc
from firesim.spread.huygens import FuelGrid
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5


def config(hourly, hours=3.0):
    return SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), hours,
                            snapshot_interval_minutes=60.0, ffmc=90.0, dmc=40.0, dc=300.0,
                            hourly_weather=hourly)


def grid(n=300, cell_m=25.0):
    dlat = n * cell_m / 111320.0
    dlng = n * cell_m / (111320.0 * math.cos(math.radians(LAT0)))
    return FuelGrid([[FuelType.C2] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                    LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


def extent(frame):
    pts = [(c["lat"], c["lng"]) for c in frame.burned_cells] if frame.burned_cells else frame.perimeter
    north = max(p[0] for p in pts) - LAT0
    east = max(p[1] for p in pts) - LNG0
    return north * 111320.0, east * 111320.0 * math.cos(math.radians(LAT0))


def test_schedule_follows_hourly_ffmc():
    hourly = (HourlyWeather(0, 30, 15, 20, 270), HourlyWeather(1, 15, 80, 10, 270, 3.0))
    sim = Simulator(config(hourly))
    sched = sim.weather_schedule()
    assert [s for s, _ in sched] == [0.0, 60.0]
    f1 = hourly_ffmc(30, 15, 20, 0.0, 90.0)
    assert sched[0][1].ffmc == pytest.approx(f1)
    assert sched[1][1].ffmc == pytest.approx(hourly_ffmc(15, 80, 10, 3.0, f1))
    assert sched[1][1].ffmc < sched[0][1].ffmc  # rain and humid night lower FFMC
    assert sim.conditions_at(90.0).wind_speed == 10


def test_without_stream_conditions_are_constant():
    sim = Simulator(config(None))
    assert len(sim.weather_schedule()) == 1


@pytest.mark.parametrize("use_grid", [False, True])
def test_wind_shift_turns_the_fire(use_grid):
    """Wind from the west for 1 h, then from the south: the fire turns north."""
    west = tuple(HourlyWeather(h, 25, 25, 20, 270) for h in range(3))
    shift = (HourlyWeather(0, 25, 25, 20, 270),) + tuple(HourlyWeather(h, 25, 25, 20, 180) for h in (1, 2))
    fg = grid() if use_grid else None
    n_w, e_w = extent(list(Simulator(config(west), fuel_grid=fg, default_fuel=FuelType.C2).run())[-1])
    n_s, e_s = extent(list(Simulator(config(shift), fuel_grid=fg, default_fuel=FuelType.C2).run())[-1])
    assert n_s > 2 * n_w
    assert e_s < e_w


@pytest.mark.parametrize("use_grid", [False, True])
def test_wet_hours_slow_spread(use_grid):
    dry = tuple(HourlyWeather(h, 30, 15, 20, 270) for h in range(3))
    wet = (HourlyWeather(0, 30, 15, 20, 270),) + tuple(HourlyWeather(h, 12, 90, 20, 270, 4.0) for h in (1, 2))
    fg = grid() if use_grid else None
    a_dry = list(Simulator(config(dry), fuel_grid=fg, default_fuel=FuelType.C2).run())[-1].area_ha
    a_wet = list(Simulator(config(wet), fuel_grid=fg, default_fuel=FuelType.C2).run())[-1].area_ha
    assert a_wet < 0.6 * a_dry
