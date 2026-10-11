"""API: the 20 m WUI window (mechanics decision M5) on a 20 m fuel raster with houses.

The run grid is the raster coarsened to 50 m (majority class); near buildings the run is
repeated at the raster's native 20 m on a crop around the fire. Frames say which grid was used
(``grid``); ``FIRESIM_WUI_FINE_GRID=0`` keeps every run at 50 m. No new request option.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
import rasterio
from httpx import ASGITransport, AsyncClient
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from firesim_api.main import create_app


def _fc(polys, props=None):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [p]},
         "properties": (props[i] if props else {})} for i, p in enumerate(polys)]}


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("wui20")
    fuel = d / "fuel20.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=150, width=150, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612),
                       transform=from_origin(350_000.0, 5_930_000.0, 20.0, 20.0)) as dst:
        dst.write(np.full((150, 150), 2, dtype=np.int32), 1)  # 3 km of C-2 at 20 m
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat_mid, lng_mid = (south + north) / 2, (west + east) / 2
    m_lat = 1 / 111320.0
    m_lng = 1 / (111320.0 * np.cos(np.radians(lat_mid)))
    houses = [_box(lng_mid + (250 + 18 * k) * m_lng, lat_mid - 6 * m_lat,
                   lng_mid + (262 + 18 * k) * m_lng, lat_mid + 6 * m_lat) for k in range(3)]
    buildings = d / "buildings.geojson"
    buildings.write_text(json.dumps(_fc(houses)))
    # the same houses 450 m north (crosswind): in the window, not in the fire's path
    crosswind = [_box(lng_mid + (250 + 18 * k) * m_lng, lat_mid + 444 * m_lat,
                  lng_mid + (262 + 18 * k) * m_lng, lat_mid + 456 * m_lat) for k in range(3)]
    buildings_north = d / "buildings_north.geojson"
    buildings_north.write_text(json.dumps(_fc(crosswind)))
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps(_fc([_box(west - 0.01, south - 0.01, east + 0.01, north + 0.01)],
                                   [{"neighbourhood": "TEST"}])))
    return {"fuel": str(fuel), "buildings": str(buildings), "nbhd": str(nbhd),
            "buildings_north": str(buildings_north),
            "lat": lat_mid, "lng": lng_mid}


@pytest.fixture
async def client(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


def _payload(files, **extra):
    return {
        "ignition_lat": files["lat"], "ignition_lng": files["lng"],
        "weather": {"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 45.0, "dc": 300.0},
        "duration_hours": 1.0, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
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
            data["id"] = sim_id
            return data
        time.sleep(0.25)
    raise TimeoutError(sim_id)


async def test_run_near_buildings_uses_the_20m_window(client, files):
    data = await _finish(client, _payload(files, structure_spread=True))
    assert data["status"] == "completed", data
    grid = data["frames"][-1]["grid"]
    assert grid["wui_window"] is True and grid["reason"] == "used"
    assert grid["cell_m"] == pytest.approx(20.0, abs=0.5)
    assert all(f["grid"] == grid for f in data["frames"])
    # the arrival raster is the 20 m crop
    arr = (await client.get(f"/api/v1/simulations/{data['id']}/arrival")).json()
    assert arr["rows"] * arr["cols"] == grid["window_cells"]
    # structure spread runs on the crop's arrival times, units over the run grid's box
    ss = data["frames"][-1]["structure_spread"]
    assert ss["units_in_run"] == 3 and ss["units_involved"] >= 1


async def test_window_off_keeps_50m(client, files, monkeypatch):
    monkeypatch.setenv("FIRESIM_WUI_FINE_GRID", "0")
    data = await _finish(client, _payload(files))
    assert data["status"] == "completed", data
    grid = data["frames"][-1]["grid"]
    assert grid["wui_window"] is False and grid["cell_m"] == pytest.approx(50.0, abs=0.5)


async def test_window_and_50m_areas_agree_on_uniform_fuel(client, files, monkeypatch):
    """Uniform fuel, buildings off the fire's path: 20 m and 50 m agree (the engine test
    measures +4.7 % at 1 h with acceleration). With the houses in the head's path the 50 m
    mask (every 50 m cell a footprint touches) is a larger obstacle than the 20 m one, so the
    areas differ there by design."""
    payload = _payload(files, buildings_path=files["buildings_north"])
    a = await _finish(client, payload)
    monkeypatch.setenv("FIRESIM_WUI_FINE_GRID", "0")
    b = await _finish(client, payload)
    assert a["frames"][-1]["grid"]["wui_window"] is True
    assert b["frames"][-1]["grid"]["wui_window"] is False
    assert a["frames"][-1]["area_ha"] == pytest.approx(b["frames"][-1]["area_ha"], rel=0.10)


def test_frame_schema_carries_grid():
    from firesim.types import FireType, SimulationFrame
    from firesim_api.routers.simulations import _frame_to_schema

    g = {"cell_m": 20.0, "wui_window": True, "reason": "used", "note": "x"}
    frame = SimulationFrame(
        time_hours=1.0, perimeter=[(53.5, -113.5)] * 3, area_ha=1.0, head_ros_m_min=1.0,
        max_hfi_kw_m=1.0, fire_type=FireType.SURFACE, flame_length_m=1.0, fuel_breakdown={},
        grid=g,
    )
    assert _frame_to_schema(frame).model_dump()["grid"] == g
