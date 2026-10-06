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
