"""Optional progress callback of the grid model (display only): it reports the spread fraction
and phases, and never changes the result."""

import pytest

from firesim.data.synthetic_grid import generate_synthetic_fuel_grid
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, WeatherInput


def _config():
    return SimulationConfig(
        ignition_lat=53.5,
        ignition_lng=-113.5,
        weather=WeatherInput(temperature=25.0, relative_humidity=30.0, wind_speed=20.0,
                             wind_direction=270.0, precipitation_24h=0.0),
        duration_hours=1.0,
        snapshot_interval_minutes=30.0,
        ffmc=90.0,
        dmc=45.0,
        dc=300.0,
    )


def _grid():
    return generate_synthetic_fuel_grid(53.5, -113.5, radius_km=2.0, cell_size_m=50.0, seed=7)


def test_grid_model_reports_spread_fraction_then_finishing():
    events = []
    frames = list(Simulator(_config(), fuel_grid=_grid(), progress=lambda ph, f: events.append((ph, f))).run())
    assert frames
    spread = [f for ph, f in events if ph == "spread"]
    assert len(spread) >= 2
    assert spread == sorted(spread) and 0.0 <= spread[0] and spread[-1] < 1.0
    assert events[-1] == ("finishing", None)
    assert "structures" not in [ph for ph, _ in events]  # house-to-house spread is off


def test_progress_callback_does_not_change_frames():
    a = list(Simulator(_config(), fuel_grid=_grid()).run())
    b = list(Simulator(_config(), fuel_grid=_grid(), progress=lambda *_: None).run())
    assert [f.area_ha for f in a] == [f.area_ha for f in b]
    assert [len(f.burned_cells or []) for f in a] == [len(f.burned_cells or []) for f in b]


def test_an_exception_from_the_callback_stops_the_run():
    class Stop(Exception):
        pass

    def cb(phase, f):
        if phase == "spread" and f and f > 0.2:
            raise Stop()

    with pytest.raises(Stop):
        list(Simulator(_config(), fuel_grid=_grid(), progress=cb).run())
