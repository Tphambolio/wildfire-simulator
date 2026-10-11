"""API: Alberta Wildfire FBP grid as the default fuel outside the City grid.

A request naming the City grid (FIRESIM_CITY_FUEL_GRID_PATH) gets the City grid where it has
data and the Alberta grid (FIRESIM_AB_FUEL_GRID_PATH) elsewhere; ``fuel_source`` on the run says
which. Other fuel_grid_path values, provincial_fuel=false and a server without the Alberta grid
keep the behaviour before 2026-10-11.
"""

from __future__ import annotations

import asyncio
import math

import numpy as np
import pytest
import rasterio
from httpx import ASGITransport, AsyncClient
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform

from firesim_api.main import create_app

LAT0, LNG0 = 53.5, -113.5


@pytest.fixture(scope="module")
def grids(tmp_path_factory):
    d = tmp_path_factory.mktemp("provincial")
    x, y = transform("EPSG:4326", "EPSG:3400", [LNG0], [LAT0])
    x0, y0 = round(x[0] / 100) * 100 - 15_000.0, round(y[0] / 100) * 100 + 10_000.0
    ab = np.full((200, 300), 31, dtype=np.int16)  # O-1 grass
    ab[:, :80] = 0  # western 8 km outside Alberta
    ab[:, 200:] = 650  # M-1/M-2 50 PC in the east
    ab_path = d / "ab.tif"
    with rasterio.open(ab_path, "w", driver="GTiff", height=200, width=300, count=1,
                       dtype="int16", crs=CRS.from_epsg(3400),
                       transform=from_origin(x0, y0, 100.0, 100.0)) as dst:
        dst.write(ab, 1)
    cx, cy = transform("EPSG:4326", "EPSG:3776", [LNG0 + 0.03], [LAT0])
    city = np.full((300, 300), 2, dtype=np.int32)  # C-2, 6 x 6 km at 20 m
    city[:, 250:] = 0  # eastern 1 km: no data
    city_path = d / "city.tif"
    with rasterio.open(city_path, "w", driver="GTiff", height=300, width=300, count=1,
                       dtype="int32", nodata=0, crs=CRS.from_epsg(3776),
                       transform=from_origin(cx[0] - 3000, cy[0] + 3000, 20.0, 20.0)) as dst:
        dst.write(city, 1)
    other = d / "user.tif"
    with rasterio.open(other, "w", driver="GTiff", height=100, width=100, count=1,
                       dtype="int32", nodata=0, crs=CRS.from_epsg(3776),
                       transform=from_origin(cx[0] - 1000, cy[0] + 1000, 20.0, 20.0)) as dst:
        dst.write(np.full((100, 100), 2, dtype=np.int32), 1)

    def point(col, row):
        lng, lat = transform("EPSG:3400", "EPSG:4326", [x0 + (col + 0.5) * 100],
                             [y0 - (row + 0.5) * 100])
        return lat[0], lng[0]

    return {"ab": str(ab_path), "city": str(city_path), "user": str(other), "point": point}


@pytest.fixture
async def client(grids, monkeypatch):
    monkeypatch.setenv("FIRESIM_AB_FUEL_GRID_PATH", grids["ab"])
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", grids["city"])
    monkeypatch.setenv("FIRESIM_PROVINCIAL_WINDOW_KM", "4")
    monkeypatch.setenv("FIRESIM_CITY_MARGIN_KM", "1")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        yield ac


def _payload(lat, lng, path, **extra):
    return {
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 91.0, "dmc": 60.0, "dc": 400.0},
        "duration_hours": 0.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C3",
        "fuel_grid_path": path, "fuel_modifiers": {"grass_cure": 90.0},
        "start_time": "2026-07-15T13:00:00-06:00", **extra,
    }


async def _run(client, payload) -> dict:
    r = await client.post("/api/v1/simulations", json=payload)
    assert r.status_code == 200, r.text
    sid = r.json()["simulation_id"]
    for _ in range(600):
        body = (await client.get(f"/api/v1/simulations/{sid}")).json()
        if body["status"] in ("completed", "failed"):
            assert body["status"] == "completed", body.get("error")
            return body
        await asyncio.sleep(0.05)
    raise AssertionError("run did not finish")


async def test_inside_city_uses_city_lattice(client, grids):
    body = await _run(client, _payload(LAT0, LNG0 + 0.03, grids["city"]))
    fs = body["fuel_source"]
    assert fs["kind"] == "city" and "Alberta Wildfire" in fs["label"]
    assert "Open Government Licence – Alberta" in fs["attribution"]
    assert fs["season"] == "leaf-on"
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"C2"}


async def test_straddling_city_edge_mosaics(client, grids):
    dlng = 2500 / (111320 * math.cos(math.radians(LAT0)))
    body = await _run(client, _payload(LAT0, LNG0 + 0.03 + dlng, grids["city"]))
    fs = body["fuel_source"]
    assert fs["kind"] == "provincial"
    assert fs["share"]["city"] > 0.1 and fs["share"]["ab_wildfire"] > 0.1
    assert "City of Edmonton" in fs["label"] and "Alberta Wildfire" in fs["label"]


async def test_outside_city_in_alberta(client, grids):
    lat, lng = grids["point"](250, 100)  # M-1/M-2 50 PC, 15 km east of the City grid
    body = await _run(client, _payload(lat, lng, grids["city"]))
    fs = body["fuel_source"]
    assert fs["kind"] == "provincial" and fs["share"] == {"ab_wildfire": 1.0}
    assert fs["label"] == "Alberta Wildfire FBP fuel types 2025, 100 m"
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"M2"}  # 15 July: leaf-on


async def test_leaf_off_date(client, grids):
    lat, lng = grids["point"](250, 100)
    body = await _run(client, _payload(lat, lng, grids["city"],
                                       start_time="2026-04-20T13:00:00-06:00"))
    assert body["fuel_source"]["season"] == "leaf-off"
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"M1"}


async def test_outside_alberta_falls_back_to_uniform(client, grids):
    lat, lng = grids["point"](20, 100)
    body = await _run(client, _payload(lat, lng, grids["city"]))
    fs = body["fuel_source"]
    assert fs["kind"] == "uniform" and "C3" in fs["label"] and "outside Alberta" in fs["label"]
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"C3"}


async def test_opt_out_keeps_city_only(client, grids):
    lat, lng = grids["point"](250, 100)
    body = await _run(client, _payload(lat, lng, grids["city"], provincial_fuel=False))
    assert body["fuel_source"]["kind"] == "city" and body["fuel_source"]["attribution"] is None


async def test_user_grid_is_used_as_given(client, grids):
    body = await _run(client, _payload(LAT0, LNG0 + 0.03, grids["user"]))
    assert body["fuel_source"]["kind"] == "user_grid"
    assert body["fuel_source"]["label"].endswith("user.tif")


async def test_uniform_fuel_type_unchanged(client, grids):
    lat, lng = grids["point"](250, 100)
    body = await _run(client, _payload(lat, lng, None))
    assert body["fuel_source"]["kind"] == "uniform"
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"C3"}


async def test_without_alberta_grid_city_only(grids, monkeypatch):
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", grids["city"])
    monkeypatch.delenv("FIRESIM_AB_FUEL_GRID_PATH", raising=False)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        body = await _run(ac, _payload(LAT0, LNG0 + 0.03, grids["city"]))
    assert body["fuel_source"]["kind"] == "city" and body["fuel_source"]["season"] is None


async def test_burn_probability_outside_city(client, grids):
    lat, lng = grids["point"](250, 100)
    r = await client.post("/api/v1/simulations/burn-probability", json={
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 91.0, "dmc": 60.0, "dc": 400.0},
        "duration_hours": 0.5, "n_iterations": 2, "fuel_grid_path": grids["city"],
        "fuel_modifiers": {"grass_cure": 90.0},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["iterations_completed"] == 2 and body["rows"] == 80


async def test_burn_probability_outside_alberta_is_422(client, grids):
    lat, lng = grids["point"](20, 100)
    r = await client.post("/api/v1/simulations/burn-probability", json={
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 20.0, "wind_direction": 270.0},
        "duration_hours": 0.5, "n_iterations": 2, "fuel_grid_path": grids["city"],
        "fuel_modifiers": {"grass_cure": 90.0},
    })
    assert r.status_code == 422 and "outside Alberta" in r.text
