"""grass_cure on the API (decision M1, 2026-10-10): 95 % by default in the pre-green-up window,
no silent default outside it when O-1 grass can burn (422), explicit values always used."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from firesim_api.main import create_app
from firesim_api.schemas.simulation import (
    BurnProbabilityRequest,
    MultiDaySimulationCreate,
    SimulationCreate,
)

BASE = {
    "ignition_lat": 53.5, "ignition_lng": -113.5,
    "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
    "duration_hours": 0.5, "snapshot_interval_minutes": 30.0,
}
SPRING = "2026-04-20T13:00:00-06:00"  # day of year 110
SUMMER = "2026-07-15T13:00:00-06:00"  # day of year 196


def test_spring_grid_run_defaults_to_95():
    req = SimulationCreate(**BASE, fuel_grid_path="x.tif", start_time=SPRING)
    assert req.fuel_modifiers.grass_cure == 95.0
    assert req.fuel_config_kwargs()["grass_cure"] == 95.0


def test_spring_default_from_day_of_year_without_start_time():
    req = SimulationCreate(**BASE, fuel_type="O1a", fuel_modifiers={"day_of_year": 100})
    assert req.fuel_modifiers.grass_cure == 95.0


def test_day_of_year_takes_precedence_over_start_time():
    with pytest.raises(ValidationError, match="day of year 200"):
        SimulationCreate(**BASE, fuel_type="O1b", start_time=SPRING,
                         fuel_modifiers={"day_of_year": 200})


@pytest.mark.parametrize("extra", [
    {"fuel_grid_path": "x.tif"}, {"use_ca_mode": True}, {"fuel_type": "O1a"}, {"fuel_type": "O1b"},
])
def test_summer_without_curing_is_rejected_when_grass_can_burn(extra):
    with pytest.raises(ValidationError, match="grass_cure is required"):
        SimulationCreate(**BASE, start_time=SUMMER, **extra)


def test_no_date_without_curing_is_rejected_when_grass_can_burn():
    with pytest.raises(ValidationError, match="no date"):
        SimulationCreate(**BASE, fuel_type="O1a")


def test_explicit_value_is_used_in_any_season():
    for when in (SPRING, SUMMER):
        req = SimulationCreate(**BASE, fuel_grid_path="x.tif", start_time=when,
                               fuel_modifiers={"grass_cure": 60.0})
        assert req.fuel_config_kwargs()["grass_cure"] == 60.0


def test_uniform_non_grass_needs_no_curing():
    req = SimulationCreate(**BASE, fuel_type="C2", start_time=SUMMER)
    assert req.fuel_modifiers.grass_cure is None
    assert "grass_cure" not in req.fuel_config_kwargs()


def test_multiday_and_burn_probability_follow_the_rule():
    md = {"ignition_lat": 53.5, "ignition_lng": -113.5, "fuel_type": "O1a",
          "days": [{"wind_speed": 10.0, "wind_direction": 270.0}]}
    assert MultiDaySimulationCreate(**md, start_time=SPRING).fuel_modifiers.grass_cure == 95.0
    with pytest.raises(ValidationError, match="grass_cure is required"):
        MultiDaySimulationCreate(**md, start_time=SUMMER)
    bp = {"ignition_lat": 53.5, "ignition_lng": -113.5,
          "weather": {"wind_speed": 10.0, "wind_direction": 270.0}}
    assert BurnProbabilityRequest(**bp, fuel_modifiers={"day_of_year": 120}).fuel_modifiers.grass_cure == 95.0
    with pytest.raises(ValidationError, match="grass_cure is required"):
        BurnProbabilityRequest(**bp)
    assert BurnProbabilityRequest(**bp, fuel_modifiers={"grass_cure": 70.0}).fuel_modifiers.grass_cure == 70.0


def test_endpoint_returns_422_with_the_reason_and_echoes_the_default():
    with TestClient(create_app()) as tc:
        resp = tc.post("/api/v1/simulations", json={**BASE, "fuel_type": "O1a", "start_time": SUMMER})
        assert resp.status_code == 422
        assert "grass_cure is required" in resp.text and "1 March to 29 May" in resp.text
        resp = tc.post("/api/v1/simulations", json={**BASE, "fuel_type": "O1a", "start_time": SPRING})
        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["fuel_modifiers"]["grass_cure"] == 95.0
