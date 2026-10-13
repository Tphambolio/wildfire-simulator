"""Manual weather schedule → hourly stream (firesim.spread.diurnal.schedule_hourly)."""

import pytest

from firesim.spread.diurnal import schedule_hourly
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, WeatherInput

from .test_grid_continuation import LAT0, LNG0, grid


def _w(t, ws, rh=15.0, rain=0.0):
    return HourlyWeather(hours_from_start=t, temperature=20.0, relative_humidity=rh,
                         wind_speed=ws, wind_direction=135.0, precipitation=rain)


def test_records_at_every_hour_and_at_each_change():
    recs = schedule_hourly(_w(0, 47.0), [_w(2 + 10 / 60, 20.0)], 4.0)
    times = [round(r.hours_from_start, 4) for r in recs]
    assert times == [0.0, 1.0, 2.0, 2.1667, 3.0]
    assert [r.wind_speed for r in recs] == [47.0, 47.0, 47.0, 20.0, 20.0]


def test_rain_is_a_rate_scaled_to_each_period():
    recs = schedule_hourly(_w(0, 10.0), [_w(1.5, 10.0, rain=2.0)], 3.0)
    by_t = {round(r.hours_from_start, 2): r.precipitation for r in recs}
    assert by_t == {0.0: 0.0, 1.0: 0.0, 1.5: 1.0, 2.0: 2.0}


@pytest.mark.parametrize("bad", [[_w(-0.5, 10.0)], [_w(1.0, 10.0), _w(1.0, 20.0)]])
def test_bad_schedules_rejected(bad):
    with pytest.raises(ValueError):
        schedule_hourly(_w(0, 10.0), bad, 3.0)


def _config(hourly, hours=3.0):
    return SimulationConfig(LAT0, LNG0, WeatherInput(20.0, 15.0, 47.0, 270.0, 0.0), hours,
                            snapshot_interval_minutes=30.0, ffmc=90.0, dmc=15.0, dc=193.0,
                            hourly_weather=hourly)


def test_ffmc_recomputed_hourly_from_the_starting_code():
    """Hot dry weather dries fine fuel: FFMC rises hour by hour from the starting 90."""
    sim = Simulator(_config(schedule_hourly(_w(0, 47.0), [_w(2.0, 20.0)], 3.0)), fuel_grid=grid())
    sched = sim.weather_schedule()
    ffmc = [c.ffmc for _, c in sched]
    assert ffmc[0] > 90.0 and all(b >= a for a, b in zip(ffmc, ffmc[1:]))
    assert [c.wind_speed for _, c in sched] == [47.0, 47.0, 20.0]


def test_wind_drop_slows_the_run():
    g = grid(n=400, cell_m=25.0)  # 10 km: the fire stays off the grid edge
    const = list(Simulator(_config(schedule_hourly(_w(0, 30.0), [], 3.0)), fuel_grid=g).run())
    drop = list(Simulator(_config(schedule_hourly(_w(0, 30.0), [_w(1.0, 5.0)], 3.0)), fuel_grid=g).run())
    by = lambda fr, t: next(f for f in fr if abs(f.time_hours - t) < 1e-6)  # noqa: E731
    assert by(drop, 1.0).area_ha == pytest.approx(by(const, 1.0).area_ha)
    assert drop[-1].area_ha < 0.7 * const[-1].area_ha
