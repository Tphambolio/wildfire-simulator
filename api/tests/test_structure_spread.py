"""API: opt-in structure-to-structure spread (`structure_spread`, Hamada; illustrative)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio
from httpx import ASGITransport, AsyncClient
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from firesim_api.main import create_app

LABEL = "illustrative — not validated in Canada"


def _fc(polys: list[list[list[float]]], props: list[dict] | None = None) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [p]},
         "properties": (props[i] if props else {})} for i, p in enumerate(polys)]}


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("structures")
    fuel = d / "fuel.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=50, width=50, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612), transform=from_origin(350_000.0, 5_930_000.0, 50.0, 50.0)) as dst:
        dst.write(np.full((50, 50), 2, dtype=np.int32), 1)  # all C-2
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat_mid, lng_mid = (south + north) / 2, (west + east) / 2
    m_lat = 1 / 111320.0
    m_lng = 1 / (111320.0 * np.cos(np.radians(lat_mid)))
    # Three 12 m houses 6 m apart, 300 m east of the ignition (wind from the west)
    houses = [_box(lng_mid + (300 + 18 * k) * m_lng, lat_mid - 6 * m_lat,
                   lng_mid + (312 + 18 * k) * m_lng, lat_mid + 6 * m_lat) for k in range(3)]
    buildings = d / "buildings.geojson"
    buildings.write_text(json.dumps(_fc(houses)))
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps(_fc([_box(west - 0.01, south - 0.01, east + 0.01, north + 0.01)],
                                   [{"neighbourhood": "TEST"}])))
    return {"fuel": str(fuel), "buildings": str(buildings), "nbhd": str(nbhd),
            "lat": lat_mid, "lng": lng_mid}


@pytest.fixture
async def client(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


def _payload(files, **extra):
    return {
        "ignition_lat": files["lat"], "ignition_lng": files["lng"],
        "weather": {"wind_speed": 30.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 45.0, "dc": 300.0},
        "duration_hours": 1.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
        "fuel_grid_path": files["fuel"], "buildings_path": files["buildings"],
        "fuel_modifiers": {"grass_cure": 60.0}, **extra,
    }


async def _finish(client, payload) -> dict:
    resp = await client.post("/api/v1/simulations", json=payload)
    assert resp.status_code == 200, resp.text
    sim_id = resp.json()["simulation_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        data = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.25)
    raise TimeoutError(sim_id)


def test_flag_defaults_off():
    from firesim_api.schemas.simulation import SimulationCreate

    req = SimulationCreate(ignition_lat=53.5, ignition_lng=-113.5,
                           weather={"wind_speed": 10.0, "wind_direction": 270.0})
    assert req.structure_spread is False


def test_frame_schema_carries_structure_spread():
    from firesim.types import FireType, SimulationFrame
    from firesim_api.routers.simulations import _frame_to_schema

    counts = {"model": "hamada", "label": LABEL, "units_in_run": 3, "units_involved": 2}
    frame = SimulationFrame(
        time_hours=1.0, perimeter=[(53.5, -113.5)] * 3, area_ha=1.0, head_ros_m_min=1.0,
        max_hfi_kw_m=1.0, fire_type=FireType.SURFACE, flame_length_m=1.0, fuel_breakdown={},
        structure_spread=counts,
    )
    assert _frame_to_schema(frame).model_dump()["structure_spread"] == counts


async def test_structure_spread_off_by_default(client, files):
    data = await _finish(client, _payload(files))
    assert data["status"] == "completed", data
    assert all(f.get("structure_spread") is None for f in data["frames"])
    assert data["config"]["structure_spread"] is False


async def test_structure_spread_on_reports_labelled_counts(client, files):
    data = await _finish(client, _payload(files, structure_spread=True))
    assert data["status"] == "completed", data
    assert data["config"]["structure_spread"] is True
    last = data["frames"][-1]["structure_spread"]
    assert last["label"] == LABEL and last["model"] == "hamada"
    assert last["units_in_run"] == 3
    assert last["units_involved"] >= 1
    assert data["frames"][0]["structure_spread"]["units_involved"] == 0
