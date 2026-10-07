"""Ensemble arrival percentiles and burn probability (spread/ensemble.py)."""

import math

import numpy as np
import pytest

from firesim.fbp.constants import FuelType
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.ensemble import EnsembleConfig, run_ensemble
from firesim.spread.huygens import FuelGrid, SpreadConditions
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, WeatherInput

LAT0, LNG0 = 53.5, -113.5


def _grid(n=80, cell=25.0, fuel=FuelType.C2):
    dlat = n * cell / 111320
    dlng = n * cell / (111320 * math.cos(math.radians(LAT0)))
    return FuelGrid([[fuel] * n for _ in range(n)], LAT0 - dlat / 2, LAT0 + dlat / 2,
                    LNG0 - dlng / 2, LNG0 + dlng / 2, n, n)


def _config(hours=1.0, **kw):
    return SimulationConfig(LAT0, LNG0, WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0), hours,
                            ffmc=92.0, dmc=40.0, dc=300.0, **kw)


ZERO = dict(wind_dir_sd_deg=0, wind_speed_log_sd=0, ffmc_sd=0, dmc_dc_log_sd=0, curing_sd=0,
            fmc_sd=0, ros_log_sd=0)


def test_zero_perturbation_reproduces_the_deterministic_run():
    g = _grid()
    res = run_ensemble(_config(), g, EnsembleConfig(n_members=3, **ZERO))
    sim = Simulator(_config(), fuel_grid=g)
    frames = list(sim.run())
    det = frames[-1].arrival_raster["minutes"]
    for q in (10, 50, 90):
        assert np.array_equal(res.arrival[q], det)
    assert set(np.unique(res.burn_probability)) <= {0.0, 1.0}
    assert res.members[0]["ros_multiplier"] == 1.0


def test_percentiles_are_ordered_and_probability_matches_reach():
    g = _grid()
    res = run_ensemble(_config(), g, EnsembleConfig(n_members=20, seed=3))
    p10, p50, p90 = (res.arrival[q].astype(float) for q in (10, 50, 90))
    for a in (p10, p50, p90):
        a[a < 0] = np.inf
    assert np.all(p10 <= p50) and np.all(p50 <= p90)
    # a cell reached at P90 was reached by at least 90 % of members, and so on
    assert np.all(res.burn_probability[np.isfinite(p90)] >= 0.9 - 1e-6)
    assert np.all(res.burn_probability[np.isfinite(p10)] >= 0.1 - 1e-6)
    assert np.all(~np.isfinite(p10)[res.burn_probability < 0.1 - 1e-6])
    # the worst-credible (P10) footprint is larger than the median one
    assert np.isfinite(p10).sum() > np.isfinite(p50).sum()
    assert len({round(m["wind_dir_offset_deg"], 3) for m in res.members}) == 20


def test_ros_multiplier_scales_arrival_times():
    """On uniform fuel without acceleration, doubling ROS halves arrival times."""
    g = _grid(n=60)
    cond = SpreadConditions(wind_speed=20, wind_direction=270, ffmc=92, dmc=40, dc=300)
    from dataclasses import replace

    def arrival(m):
        f = run_cellular_simulation(
            dict(ignition_lat=LAT0, ignition_lng=LNG0, duration_hours=1.0), g,
            replace(cond, ros_multiplier=m), acceleration=False, snapshot_interval_minutes=60,
            compute_perimeter=False,
        )
        return f[-1].arrival

    a1, a2 = arrival(1.0), arrival(2.0)
    both = np.isfinite(a1) & np.isfinite(a2) & (a1 > 10)
    assert both.sum() > 20
    assert np.median(a2[both] / a1[both]) == pytest.approx(0.5, rel=0.08)


def test_wind_direction_offset_applies_to_every_hourly_record():
    from firesim.spread.ensemble import perturb_config
    import random

    hourly = tuple(HourlyWeather(h, 25.0, 25.0, 20.0, 270.0) for h in (0.0, 1.0, 2.0))
    cfg = _config(hours=3.0, hourly_weather=hourly)
    member, k, rec = perturb_config(cfg, EnsembleConfig(), random.Random(5), 100.0)
    offs = {round((h.wind_direction - 270.0) % 360.0, 6) for h in member.hourly_weather}
    assert len(offs) == 1 and offs.pop() == pytest.approx(rec["wind_dir_offset_deg"] % 360.0, abs=0.01)
    assert member.weather.wind_direction == pytest.approx((270.0 + rec["wind_dir_offset_deg"]) % 360.0, abs=0.01)
