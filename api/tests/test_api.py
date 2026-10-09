"""Integration tests for the FastAPI application."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from firesim_api.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    async def test_health_check(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "3.0.0"


class TestSimulations:
    async def test_create_simulation(self, client):
        payload = {
            "ignition_lat": 51.0,
            "ignition_lng": -114.0,
            "weather": {
                "wind_speed": 20.0,
                "wind_direction": 270.0,
            },
            "duration_hours": 0.5,
            "snapshot_interval_minutes": 15.0,
        }
        resp = await client.post("/api/v1/simulations", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "simulation_id" in data
        assert data["status"] == "running"

    async def test_get_simulation_not_found(self, client):
        resp = await client.get("/api/v1/simulations/nonexistent")
        assert resp.status_code == 404

    async def test_create_and_wait_for_results(self, client):
        payload = {
            "ignition_lat": 51.0,
            "ignition_lng": -114.0,
            "weather": {
                "wind_speed": 15.0,
                "wind_direction": 180.0,
            },
            "duration_hours": 0.5,
            "snapshot_interval_minutes": 15.0,
            "fuel_type": "C2",
        }
        resp = await client.post("/api/v1/simulations", json=payload)
        sim_id = resp.json()["simulation_id"]

        # Wait for completion (short sim should be fast)
        for _ in range(30):
            resp = await client.get(f"/api/v1/simulations/{sim_id}")
            data = resp.json()
            if data["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)

        assert data["status"] == "completed"
        assert len(data["frames"]) > 0

        # Check first frame
        first = data["frames"][0]
        assert first["time_hours"] == 0.0
        assert first["area_ha"] >= 0.0
        assert len(first["perimeter"]) > 0

        # Check last frame
        last = data["frames"][-1]
        assert last["area_ha"] > first["area_ha"]
        assert last["head_ros_m_min"] > 0

    async def test_create_with_fwi_overrides(self, client):
        payload = {
            "ignition_lat": 51.0,
            "ignition_lng": -114.0,
            "weather": {
                "wind_speed": 25.0,
                "wind_direction": 270.0,
            },
            "fwi_overrides": {
                "ffmc": 92.0,
                "dmc": 60.0,
                "dc": 400.0,
            },
            "duration_hours": 0.5,
            "snapshot_interval_minutes": 15.0,
        }
        resp = await client.post("/api/v1/simulations", json=payload)
        assert resp.status_code == 200

    async def test_validation_rejects_invalid(self, client):
        payload = {
            "ignition_lat": 200.0,  # Invalid latitude
            "ignition_lng": -114.0,
            "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
        }
        resp = await client.post("/api/v1/simulations", json=payload)
        assert resp.status_code == 422


def test_frame_schema_carries_buildings_and_snap():
    """Engine frame fields the frontend displays must survive the API schema."""
    from firesim.types import FireType, SimulationFrame
    from firesim_api.routers.simulations import _frame_to_schema

    frame = SimulationFrame(
        time_hours=1.0, perimeter=[(53.5, -113.5)] * 3, area_ha=1.0, head_ros_m_min=1.0,
        max_hfi_kw_m=1.0, fire_type=FireType.SURFACE, flame_length_m=1.0, fuel_breakdown={},
        buildings_at_risk=7, ignition_snapped_m=402.0,
        building_exposure={"inside_perimeter": 7, "within_30m": 9},
        building_exposure_detail=[{"lat": 53.5, "lng": -113.5, "band": "radiant"}],
    )
    out = _frame_to_schema(frame).model_dump()
    assert out["buildings_at_risk"] == 7
    assert out["ignition_snapped_m"] == 402.0
    assert out["building_exposure"]["within_30m"] == 9
    assert out["building_exposure_detail"][0]["band"] == "radiant"


async def test_fuel_grid_image_has_legend_without_fire_colours(client):
    """The fuel overlay lists the fuels it draws, in colours that are not fire hues."""
    import pathlib

    tif = pathlib.Path(__file__).resolve().parents[2] / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"
    if not tif.exists():
        pytest.skip("Edmonton fuel grid not present")
    resp = await client.get("/api/v1/simulations/fuel-grid-image", params={"fuel_grid_path": str(tif)})
    assert resp.status_code == 200
    legend = resp.json()["legend"]
    fuels = {e["fuel"] for e in legend}
    assert {"C2", "D2", "O1a"} <= fuels
    for e in legend:
        r, g, b = (int(e["color"][i:i + 2], 16) for i in (1, 3, 5))
        assert not (r > 200 and g < 180 and b < 100), e  # no orange/red


_EDMONTON_FUEL = (
    __import__("pathlib").Path(__file__).resolve().parents[2]
    / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"
)


async def _run_grid(client, cells_mode: str) -> dict:
    if not _EDMONTON_FUEL.exists():
        pytest.skip("Edmonton fuel grid not present")
    payload = {
        # grass in north-east Edmonton, where the fire spreads freely
        "ignition_lat": 53.6778, "ignition_lng": -113.3631,
        "weather": {"wind_speed": 25.0, "wind_direction": 270.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
        "duration_hours": 2.0, "snapshot_interval_minutes": 10.0,
        "fuel_grid_path": str(_EDMONTON_FUEL), "cells_mode": cells_mode,
    }
    resp = await client.post("/api/v1/simulations", json=payload)
    sim_id = resp.json()["simulation_id"]
    for _ in range(120):
        data = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
        if data["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert data["status"] == "completed", data.get("error")
    return data


async def test_incremental_frames_rebuild_cumulative(client):
    """Incremental frames (only new cells) rebuild exactly the cumulative frames, smaller."""
    import json

    full = await _run_grid(client, "cumulative")
    inc = await _run_grid(client, "incremental")
    assert len(full["frames"]) == len(inc["frames"])
    cells: list[dict] = []
    for f_full, f_inc in zip(full["frames"], inc["frames"]):
        assert f_inc["cells_offset"] == len(cells)
        cells = cells[: f_inc["cells_offset"]] + (f_inc["burned_cells"] or [])
        assert cells == (f_full["burned_cells"] or [])
        assert f_full["cells_offset"] == 0
    assert full["frames"][-1]["area_ha"] > 5  # the fire spread
    assert len(json.dumps(inc)) < 0.6 * len(json.dumps(full))
    last = inc["frames"][-1]
    assert {"ros", "part"} <= last["burned_cells"][0].keys() if last["burned_cells"] else True
    heads = [f["head"] for f in inc["frames"] if f["head"]]
    assert heads and all(h["ros"] > 0 and 0 <= h["raz"] < 360 for h in heads)

    # Arrival grid for the finished run
    arr = (await client.get(f"/api/v1/simulations/{inc['simulation_id']}/arrival")).json()
    import base64

    import numpy as np

    minutes = np.frombuffer(base64.b64decode(arr["minutes"]), dtype="<i2").reshape(arr["rows"], arr["cols"])
    assert int((minutes >= 0).sum()) == cells.__len__()
    assert 0 <= minutes[minutes >= 0].max() <= 120


async def test_arrival_404_for_unknown_run(client):
    resp = await client.get("/api/v1/simulations/nope/arrival")
    assert resp.status_code == 404


async def test_version_endpoint(client):
    data = (await client.get("/api/v1/version")).json()
    assert data["version"] == "3.0.0" and "git_sha" in data


async def test_start_time_round_trips_and_sets_day_of_year(client):
    payload = {
        "ignition_lat": 53.5, "ignition_lng": -113.5,
        "weather": {"wind_speed": 10.0, "wind_direction": 270.0},
        "duration_hours": 0.5, "snapshot_interval_minutes": 15.0,
        "start_time": "2026-04-28T13:40:00-06:00",
    }
    resp = await client.post("/api/v1/simulations", json=payload)
    assert resp.status_code in (200, 201)
    sim_id = resp.json()["simulation_id"]
    data = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
    from datetime import datetime

    assert datetime.fromisoformat(data["config"]["start_time"]) == datetime.fromisoformat(payload["start_time"])

    from firesim_api.schemas.simulation import SimulationCreate

    req = SimulationCreate(**payload)
    assert req.fuel_config_kwargs()["day_of_year"] == 118  # 28 April
    explicit = SimulationCreate(**payload, fuel_modifiers={"day_of_year": 200})
    assert explicit.fuel_config_kwargs()["day_of_year"] == 200


async def test_seed_round_trips(client):
    """Optional spotting seed: stored with the config; default None (derived from the inputs)."""
    from firesim_api.schemas.simulation import SimulationCreate

    base = {
        "ignition_lat": 53.5, "ignition_lng": -113.5,
        "weather": {"wind_speed": 10.0, "wind_direction": 270.0},
        "duration_hours": 0.5, "snapshot_interval_minutes": 15.0,
    }
    assert SimulationCreate(**base).seed is None
    resp = await client.post("/api/v1/simulations", json={**base, "seed": 7})
    assert resp.status_code in (200, 201)
    sim_id = resp.json()["simulation_id"]
    data = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
    assert data["config"]["seed"] == 7


async def test_start_time_needs_an_offset(client):
    payload = {
        "ignition_lat": 53.5, "ignition_lng": -113.5,
        "weather": {"wind_speed": 10.0, "wind_direction": 270.0},
        "start_time": "2026-04-28T13:40:00",
    }
    resp = await client.post("/api/v1/simulations", json=payload)
    assert resp.status_code == 422


async def test_ensemble_after_grid_run(client):
    """A grid run with `ensemble` serves P10/P50/P90 arrival and burn probability."""
    import base64

    import numpy as np

    if not _EDMONTON_FUEL.exists():
        pytest.skip("Edmonton fuel grid not present")
    payload = {
        "ignition_lat": 53.6778, "ignition_lng": -113.3631,
        "weather": {"wind_speed": 25.0, "wind_direction": 270.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
        "duration_hours": 1.0, "snapshot_interval_minutes": 30.0,
        "fuel_grid_path": str(_EDMONTON_FUEL),
        "ensemble": {"n_members": 6, "seed": 2},
    }
    sim_id = (await client.post("/api/v1/simulations", json=payload)).json()["simulation_id"]
    for _ in range(240):
        ens = (await client.get(f"/api/v1/simulations/{sim_id}/ensemble")).json()
        if ens["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert ens["status"] == "completed", ens
    shape = (ens["rows"], ens["cols"])
    grids = {k: np.frombuffer(base64.b64decode(v), dtype="<i2").reshape(shape).astype(float)
             for k, v in ens["arrival"].items()}
    for g in grids.values():
        g[g < 0] = np.inf
    assert np.all(grids["p10"] <= grids["p50"]) and np.all(grids["p50"] <= grids["p90"])
    bp = np.frombuffer(base64.b64decode(ens["burn_probability"]), dtype=np.uint8).reshape(shape)
    assert bp.max() == 100 and len(ens["members"]) == 6
    assert ens["area_ha"]["min"] <= ens["area_ha"]["p50"] <= ens["area_ha"]["max"]


async def test_ensemble_404_when_not_requested(client):
    payload = {"ignition_lat": 53.5, "ignition_lng": -113.5,
               "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.5}
    sim_id = (await client.post("/api/v1/simulations", json=payload)).json()["simulation_id"]
    resp = await client.get(f"/api/v1/simulations/{sim_id}/ensemble")
    assert resp.status_code == 404


def test_ensemble_params_defaults_are_the_engine_calibration():
    """The API defaults are the calibrated engine defaults (firesim.spread.ensemble)."""
    from firesim.spread.ensemble import DEFAULT_SIGMAS
    from firesim_api.schemas.simulation import EnsembleParams

    p = EnsembleParams()
    for k, v in DEFAULT_SIGMAS.items():
        assert getattr(p, k) == v, k
