"""Diurnal burning: hourly-FFMC spin-up and the opt-in burning period.

Known answers:
- records before the start only advance FFMC: the schedule starts at minute 0 and its FFMC is
  the hourly-FFMC chain through the spin-up hours and then hour 0;
- without spin-up records or a burning period the schedule is unchanged;
- a burning period of 10-20 h on a run starting at 06:00 gives ROS x 0 for minutes 0-240,
  x 1 for 240-840 and x 0 after, splitting the hourly periods at the edges;
- in a grid run no cell outside the starting fire burns before the period opens, and spread
  resumes when it does (a zero-rate period no longer ends the run).
"""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.fwi.calculator import hourly_ffmc
from firesim.spread.diurnal import burning_period_schedule
from firesim.spread.huygens import FuelGrid, SpreadConditions
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, WeatherInput


def _config(hourly=None, **kw):
    return SimulationConfig(ignition_lat=55.0, ignition_lng=-115.0,
                            weather=WeatherInput(22.0, 30.0, 15.0, 270.0, 0.0),
                            duration_hours=kw.pop("duration_hours", 6.0), ffmc=90.0, dmc=40.0,
                            dc=300.0, hourly_weather=hourly, **kw)


def _hour(h, rh=30.0, temp=22.0, ws=15.0, wd=270.0, rain=0.0):
    return HourlyWeather(hours_from_start=float(h), temperature=temp, relative_humidity=rh,
                         wind_speed=ws, wind_direction=wd, precipitation=rain)


def test_spinup_hours_only_advance_ffmc():
    night = [_hour(h, rh=85.0, temp=8.0, ws=5.0, wd=180.0) for h in range(-13, 0)]
    day = [_hour(h, rh=35.0, temp=20.0, ws=15.0, wd=270.0) for h in range(0, 6)]
    sched = Simulator(_config(tuple(night + day))).weather_schedule()
    assert [s for s, _ in sched] == [60.0 * h for h in range(6)]
    ffmc = 90.0
    for r in night + day[:1]:
        ffmc = hourly_ffmc(r.temperature, r.relative_humidity, r.wind_speed, 0.0, ffmc, 1.0)
    assert sched[0][1].ffmc == pytest.approx(ffmc, abs=1e-12)
    assert sched[0][1].wind_direction == 270.0  # the night's wind is not used for spread
    no_spin = Simulator(_config(tuple(day))).weather_schedule()
    assert sched[0][1].ffmc < no_spin[0][1].ffmc - 3.0  # a humid night lowers morning FFMC


def test_schedule_unchanged_without_spinup_or_burning_period():
    day = tuple(_hour(h, rh=30.0 + 3 * h, ws=10.0 + h) for h in range(1, 5))  # starts after 0
    sched = Simulator(_config(day)).weather_schedule()
    base = Simulator(_config(None))._spread_conditions()
    assert sched[0] == (0.0, base)
    ffmc = 90.0
    for (start, cond), r in zip(sched[1:], day):
        ffmc = hourly_ffmc(r.temperature, r.relative_humidity, r.wind_speed, 0.0, ffmc, 1.0)
        assert start == r.hours_from_start * 60.0
        assert cond.ffmc == ffmc and cond.ros_multiplier == 1.0


def test_burning_period_multipliers():
    c = SpreadConditions(wind_speed=10.0, wind_direction=270.0, ffmc=90.0, dmc=40.0, dc=300.0)
    hourly = [(60.0 * h, c) for h in range(24)]
    out = burning_period_schedule(hourly, 6.0, (10.0, 20.0), 24 * 60.0)
    starts = [s for s, _ in out]
    assert starts == [60.0 * h for h in range(24)]  # edges fall on hour boundaries
    k = {s: cond.ros_multiplier for s, cond in out}
    assert all(k[60.0 * h] == 0.0 for h in range(0, 4))
    assert all(k[60.0 * h] == 1.0 for h in range(4, 14))
    assert all(k[60.0 * h] == 0.0 for h in range(14, 24))
    # one constant period, half-hour edges and an off factor
    out = burning_period_schedule([(0.0, c)], 6.0, (9.5, 12.5), 12 * 60.0, off_factor=0.25)
    assert [(s, cond.ros_multiplier) for s, cond in out] == [(0.0, 0.25), (210.0, 1.0),
                                                            (390.0, 0.25)]
    # over midnight, starting inside the period
    out = burning_period_schedule([(0.0, c)], 22.0, (20.0, 26.0), 10 * 60.0)
    assert [(s, cond.ros_multiplier) for s, cond in out] == [(0.0, 1.0), (240.0, 0.0)]


def test_burning_period_needs_start_hour():
    with pytest.raises(ValueError):
        Simulator(_config(None, burning_period=(10.0, 20.0))).weather_schedule()


def test_grid_run_waits_for_the_burning_period():
    n, cell, lat0, lng0 = 40, 30.0, 55.0, -115.0
    dlat = cell / 111320.0
    dlng = cell / (111320.0 * math.cos(math.radians(lat0)))
    g = FuelGrid(fuel_types=[[FuelType.C2] * n for _ in range(n)], lat_min=lat0 - n / 2 * dlat,
                 lat_max=lat0 + n / 2 * dlat, lng_min=lng0 - n / 2 * dlng,
                 lng_max=lng0 + n / 2 * dlng, rows=n, cols=n)
    burned = [(lat0 + (0.5 - r) * dlat, lng0 + (c + 0.5) * dlng) for r in range(2) for c in range(2)]
    cfg = _config(None, duration_hours=3.0, start_hour=6.0, burning_period=(7.0, 8.0),
                  snapshot_interval_minutes=180.0)
    frames = list(Simulator(cfg, g, initial_burned=burned).run())
    arr = frames[-1].arrival_raster
    assert arr is not None
    t = np.array(arr["minutes"], dtype=float)
    t = t[t >= 0]
    new = t[t > 0]
    assert len(new) > 0
    assert new.min() >= 60.0 - 1e-6 and new.max() <= 120.0 + 1e-6
