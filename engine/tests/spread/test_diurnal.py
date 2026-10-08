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


# ── Spin-up window and stream preparation (firesim.spread.diurnal.hourly_for_run) ──

from firesim.spread.diurnal import SPINUP_FROM_HOUR, hourly_for_run, spinup_hours  # noqa: E402


def test_spinup_hours_back_to_17h():
    assert SPINUP_FROM_HOUR == 17.0
    assert spinup_hours(6.0) == 13.0  # 06:00 start: from 17:00 the day before
    assert spinup_hours(13.5) == 20.5
    assert spinup_hours(21.0) == 4.0  # evening start: from 17:00 the same day
    assert spinup_hours(17.0) == 0.0  # the daily FFMC applies as is


def test_hourly_for_run_spinup_trims_to_17h():
    # 06:00 start; the stream reaches back 20 h (to 10:00 the day before)
    recs = [_hour(h) for h in range(-20, 6)]
    out = hourly_for_run(recs, 6.0, ffmc_spin_up=True)
    assert out[0].hours_from_start == -13.0 and len(out) == 13 + 6
    # half-hour start: the record in force at 17:00 is moved to start there
    recs = [_hour(h - 0.5) for h in range(-20, 6)]  # records at :30 relative to a 06:30 start
    out = hourly_for_run(recs, 6.0, ffmc_spin_up=True)
    assert out[0].hours_from_start == -13.0 and out[1].hours_from_start == -12.5


def test_hourly_for_run_without_spinup_drops_earlier_hours():
    recs = [_hour(h - 0.5, rh=30.0 + h) for h in range(-5, 4)]  # a record straddles the start
    out = hourly_for_run(recs, 6.0, ffmc_spin_up=False)
    assert [r.hours_from_start for r in out] == [0.0, 0.5, 1.5, 2.5]
    assert out[0].relative_humidity == 30.0  # the record in force at the start
    day = [_hour(h) for h in range(0, 4)]
    assert hourly_for_run(day, None, ffmc_spin_up=False) == tuple(day)
    assert hourly_for_run(None, None, ffmc_spin_up=False) is None


def test_hourly_for_run_spinup_errors():
    with pytest.raises(ValueError, match="needs hourly_weather"):
        hourly_for_run(None, 6.0, ffmc_spin_up=True)
    with pytest.raises(ValueError, match="start_time"):
        hourly_for_run([_hour(h) for h in range(-13, 4)], None, ffmc_spin_up=True)
    with pytest.raises(ValueError, match="17:00"):  # reaches back only to 21:00
        hourly_for_run([_hour(h) for h in range(-9, 4)], 6.0, ffmc_spin_up=True)
    with pytest.raises(ValueError, match="for the run"):  # overnight only
        hourly_for_run([_hour(h) for h in range(-13, -1)], 6.0, ffmc_spin_up=True)
    # a 17:00 start needs no spin-up hours
    assert hourly_for_run([_hour(h) for h in range(0, 3)], 17.0, ffmc_spin_up=True)[0].hours_from_start == 0.0


def test_huygens_run_waits_for_the_burning_period():
    """Uniform fuel (Huygens): a 21:00 start with a 10-20 h burning period does not spread
    until 10:00 the next morning (13 h), then grows."""
    cfg = _config(None, duration_hours=15.0, start_hour=21.0, burning_period=(10.0, 20.0),
                  snapshot_interval_minutes=60.0)
    frames = list(Simulator(cfg).run())
    area = {round(f.time_hours, 3): f.area_ha for f in frames}
    assert all(area[float(h)] == area[0.0] for h in range(0, 14))  # 21:00 .. 10:00
    assert area[15.0] > area[13.0] + 1.0  # spreads from 10:00
    off = [f for f in frames if 0 < f.time_hours < 13.0]  # at 10:00 the period has begun
    assert all(f.head_ros_m_min == 0.0 and f.max_hfi_kw_m == 0.0 for f in off)
    on = [f for f in frames if f.time_hours >= 13.0]
    assert all(f.head_ros_m_min > 0.0 for f in on)


def test_ensemble_members_inherit_the_burning_period():
    """Every ensemble member keeps the burning period (and start hour): with a 07-08 h period
    on a 06:00 start no cell beyond the ignition burns before minute 60 or after 120."""
    from firesim.spread.ensemble import EnsembleConfig, run_ensemble

    n, cell, lat0, lng0 = 40, 30.0, 55.0, -115.0
    dlat = cell / 111320.0
    dlng = cell / (111320.0 * math.cos(math.radians(lat0)))
    g = FuelGrid(fuel_types=[[FuelType.C2] * n for _ in range(n)], lat_min=lat0 - n / 2 * dlat,
                 lat_max=lat0 + n / 2 * dlat, lng_min=lng0 - n / 2 * dlng,
                 lng_max=lng0 + n / 2 * dlng, rows=n, cols=n)
    cfg = _config(None, duration_hours=3.0, start_hour=6.0, burning_period=(7.0, 8.0))
    res = run_ensemble(cfg, g, EnsembleConfig(n_members=5, seed=3))
    for q, arr in res.arrival.items():
        t = arr[arr > 0].astype(float)
        assert len(t) > 0, q
        assert t.min() >= 60.0 - 1 and t.max() <= 120.0 + 1, q


def test_point_ignition_holds_until_the_burning_period_opens():
    """A point ignition before the period opens starts spreading (and accelerating) when it
    opens: one burning hour gives the same fire as the first hour of a run without a period.
    Before this, a grid run ignited outside the period never spread."""
    n, cell, lat0, lng0 = 40, 30.0, 55.0, -115.0
    dlat = cell / 111320.0
    dlng = cell / (111320.0 * math.cos(math.radians(lat0)))
    g = FuelGrid(fuel_types=[[FuelType.C2] * n for _ in range(n)], lat_min=lat0 - n / 2 * dlat,
                 lat_max=lat0 + n / 2 * dlat, lng_min=lng0 - n / 2 * dlng,
                 lng_max=lng0 + n / 2 * dlng, rows=n, cols=n)
    plain = list(Simulator(_config(None, duration_hours=1.0, snapshot_interval_minutes=60.0), g).run())
    held = list(Simulator(_config(None, duration_hours=3.0, start_hour=6.0,
                                  burning_period=(7.0, 8.0), snapshot_interval_minutes=60.0), g).run())
    area = [f.area_ha for f in held]
    assert area[0] == 0.0 and area[1] < 0.2  # only the ignition cell by 07:00
    assert area[2] == pytest.approx(plain[-1].area_ha, rel=1e-9)
    assert area[3] == area[2]  # closed again after 08:00
    # Huygens (no fuel grid): the same hold
    plain_h = list(Simulator(_config(None, duration_hours=1.0, snapshot_interval_minutes=60.0)).run())
    held_h = list(Simulator(_config(None, duration_hours=3.0, start_hour=6.0,
                                    burning_period=(7.0, 8.0), snapshot_interval_minutes=60.0)).run())
    assert held_h[2].area_ha == pytest.approx(plain_h[-1].area_ha, rel=0.02)
