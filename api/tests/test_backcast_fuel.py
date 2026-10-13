"""API: pre-fire (back-cast) fuel for dated historical runs (docs/fuel-grid-backcast.md).

A provincial-window run dated before the end of the Alberta grid's vintage year gets the cells
NBAC records as burned since its date restored (``fuel_source.backcast.applied``); later and
undated runs are unchanged; the City lattice and servers without the data say why not.
"""

from __future__ import annotations

import asyncio
import json
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
W, H = 300, 200


@pytest.fixture(scope="module")
def grids(tmp_path_factory):
    d = tmp_path_factory.mktemp("backcast_api")
    x, y = transform("EPSG:4326", "EPSG:3400", [LNG0], [LAT0])
    x0, y0 = round(x[0] / 100) * 100 - 15_000.0, round(y[0] / 100) * 100 + 10_000.0
    tr = from_origin(x0, y0, 100.0, 100.0)

    def write(path, arr, dtype, nodata=None):
        with rasterio.open(path, "w", driver="GTiff", height=H, width=W, count=1, dtype=dtype,
                           crs=CRS.from_epsg(3400), transform=tr, nodata=nodata) as dst:
            dst.write(arr.astype(dtype), 1)

    ab = np.full((H, W), 31, np.int16)  # grass: the 2025 grid's post-fire typing
    ab[:, :80] = 0
    write(d / "ab.tif", ab, "int16")
    key = np.zeros((H, W), np.int32)
    key[:, 200:] = 2011209  # burned 2011, ended 28 July
    bc = d / "bc"
    bc.mkdir()
    write(bc / "key.tif", key, "int32", nodata=0)
    pre = np.where(key > 0, 2, 0)  # C-2 before the fire
    write(bc / "e2010.tif", pre, "int16", nodata=0)
    (bc / "backcast.json").write_text(json.dumps({
        "version": 1, "grid_vintage_year": 2025, "burns": {"file": "key.tif"},
        "epochs": {"2010": "e2010.tif"}}))
    cx, cy = transform("EPSG:4326", "EPSG:3776", [LNG0 + 0.03], [LAT0])
    city = np.full((300, 300), 2, dtype=np.int32)
    city[:, 250:] = 0
    with rasterio.open(d / "city.tif", "w", driver="GTiff", height=300, width=300, count=1,
                       dtype="int32", nodata=0, crs=CRS.from_epsg(3776),
                       transform=from_origin(cx[0] - 3000, cy[0] + 3000, 20.0, 20.0)) as dst:
        dst.write(city, 1)

    def point(col, row):
        lng, lat = transform("EPSG:3400", "EPSG:4326", [x0 + (col + 0.5) * 100],
                             [y0 - (row + 0.5) * 100])
        return lat[0], lng[0]

    return {"ab": str(d / "ab.tif"), "city": str(d / "city.tif"), "bc": str(bc),
            "point": point}


@pytest.fixture
async def client(grids, monkeypatch):
    monkeypatch.setenv("FIRESIM_AB_FUEL_GRID_PATH", grids["ab"])
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", grids["city"])
    monkeypatch.setenv("FIRESIM_AB_BACKCAST_DIR", grids["bc"])
    monkeypatch.setenv("FIRESIM_PROVINCIAL_WINDOW_KM", "4")
    monkeypatch.setenv("FIRESIM_CITY_MARGIN_KM", "1")
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        yield ac


def _payload(lat, lng, path, start):
    return {
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 91.0, "dmc": 60.0, "dc": 400.0},
        "duration_hours": 0.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C3",
        "fuel_grid_path": path, "fuel_modifiers": {"grass_cure": 90.0}, "start_time": start,
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


async def test_historical_run_is_backcast(client, grids):
    lat, lng = grids["point"](250, 100)  # inside the 2011 burn
    body = await _run(client, _payload(lat, lng, grids["city"], "2011-05-14T17:50:00-06:00"))
    fs = body["fuel_source"]
    bc = fs["backcast"]
    assert fs["kind"] == "provincial" and bc["applied"] is True
    assert bc["as_of"] == "2011-05-14" and bc["epoch"] == 2010
    assert bc["restored_share"] == pytest.approx(1.0, abs=0.01)
    assert bc["restored_before"] == {"O-1": 1.0} and bc["restored_after"] == {"C": 1.0}
    assert "back-cast to 2011-05-14" in fs["label"] and "SCANFI" in bc["note"]
    assert "Open Government Licence - Canada" in bc["attribution"] and bc["heuristic"]
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"C2"}
    # the fuel-vintage warning says the back-cast was applied instead of warning of burn scars
    assert body["fuel_vintage_warning"] == bc["note"]
    assert body["fuel_vintage_warning"].startswith("Pre-fire fuel applied: areas burned since "
                                                   "2011-05-14")


async def test_after_the_fire_and_present_day_are_unchanged(client, grids):
    lat, lng = grids["point"](250, 100)
    body = await _run(client, _payload(lat, lng, grids["city"], "2011-08-01T13:00:00-06:00"))
    bc = body["fuel_source"]["backcast"]
    assert bc["applied"] is True and bc["restored_share"] == 0.0  # the fire had ended
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"O1b"}
    body = await _run(client, _payload(lat, lng, grids["city"], "2026-07-15T13:00:00-06:00"))
    assert body["fuel_source"]["backcast"] is None
    assert body["fuel_source"]["label"] == "Alberta Wildfire FBP fuel types 2025, 100 m"
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"O1b"}


async def test_city_lattice_is_not_backcast(client, grids):
    body = await _run(client, _payload(LAT0, LNG0 + 0.03, grids["city"],
                                       "2011-05-14T17:50:00-06:00"))
    bc = body["fuel_source"]["backcast"]
    assert body["fuel_source"]["kind"] == "city" and bc["applied"] is False
    assert "City of Edmonton" in bc["reason"]


async def test_no_epoch_before_the_scenario(client, grids):
    lat, lng = grids["point"](250, 100)
    body = await _run(client, _payload(lat, lng, grids["city"], "2005-06-01T13:00:00-06:00"))
    bc = body["fuel_source"]["backcast"]
    assert bc["applied"] is False and "first SCANFI epoch 2010" in bc["reason"]
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"O1b"}


async def test_server_without_backcast_data(grids, monkeypatch):
    monkeypatch.setenv("FIRESIM_AB_FUEL_GRID_PATH", grids["ab"])
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", grids["city"])
    monkeypatch.delenv("FIRESIM_AB_BACKCAST_DIR", raising=False)
    monkeypatch.setenv("FIRESIM_PROVINCIAL_WINDOW_KM", "4")
    lat, lng = grids["point"](250, 100)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        body = await _run(ac, _payload(lat, lng, grids["city"], "2011-05-14T17:50:00-06:00"))
    bc = body["fuel_source"]["backcast"]
    assert bc["applied"] is False and "not installed" in bc["reason"]
    assert "Areas burned since 2011" in body["fuel_vintage_warning"]
    assert body["fuel_vintage_warning"].endswith(bc["note"])
    assert set(body["frames"][-1]["fuel_breakdown"]) == {"O1b"}


async def test_window_cache_separates_dates(client, grids):
    lat, lng = grids["point"](250, 100)
    a = await _run(client, _payload(lat, lng, grids["city"], "2011-05-14T17:50:00-06:00"))
    b = await _run(client, _payload(lat, lng, grids["city"], "2026-05-14T17:50:00-06:00"))
    c = await _run(client, _payload(lat, lng, grids["city"], "2011-05-14T17:50:00-06:00"))
    assert set(a["frames"][-1]["fuel_breakdown"]) == {"C2"}
    assert set(b["frames"][-1]["fuel_breakdown"]) == {"O1b"}
    assert set(c["frames"][-1]["fuel_breakdown"]) == {"C2"}
    assert math.isclose(a["frames"][-1]["area_ha"], c["frames"][-1]["area_ha"])
