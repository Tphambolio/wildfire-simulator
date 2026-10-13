"""API: size rule for headline building counts (decision 2026-10-12, [R40]).

Footprints under 20 m² (``FIRESIM_SMALL_FOOTPRINT_M2``) are outbuildings: they carry fire but
are counted separately from the structure-spread, exposure and inside-perimeter counts;
``FIRESIM_SMALL_FOOTPRINT_M2=0`` restores the former counts."""

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


def _fc(polys, props=None) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [p]},
         "properties": (props[i] if props else {})} for i, p in enumerate(polys)]}


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("small")
    fuel = d / "fuel.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=50, width=50, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612),
                       transform=from_origin(350_000.0, 5_930_000.0, 50.0, 50.0)) as dst:
        dst.write(np.full((50, 50), 2, dtype=np.int32), 1)  # all C-2
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat_mid, lng_mid = (south + north) / 2, (west + east) / 2
    m_lat = 1 / 111320.0
    m_lng = 1 / (111320.0 * np.cos(np.radians(lat_mid)))
    # Three 12 m houses (144 m²) 6 m apart 300 m east of the ignition, and a 3 m shed (9 m²)
    # 2 m north of each of the first two
    polys = [_box(lng_mid + (300 + 18 * k) * m_lng, lat_mid - 6 * m_lat,
                  lng_mid + (312 + 18 * k) * m_lng, lat_mid + 6 * m_lat) for k in range(3)]
    polys += [_box(lng_mid + (304 + 18 * k) * m_lng, lat_mid + 8 * m_lat,
                   lng_mid + (307 + 18 * k) * m_lng, lat_mid + 11 * m_lat) for k in range(2)]
    buildings = d / "buildings.geojson"
    buildings.write_text(json.dumps(_fc(polys)))
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


def _payload(files):
    return {
        "ignition_lat": files["lat"], "ignition_lng": files["lng"],
        "weather": {"wind_speed": 30.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 45.0, "dc": 300.0},
        "duration_hours": 1.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
        "fuel_grid_path": files["fuel"], "buildings_path": files["buildings"],
        "fuel_modifiers": {"grass_cure": 60.0}, "structure_spread": True,
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


def test_setting_maps_to_the_engine_default(monkeypatch):
    from firesim.structures.units import resolve_small_footprint_m2, resolve_small_footprints_mode
    from firesim_api.settings import settings

    monkeypatch.delenv("FIRESIM_SMALL_FOOTPRINT_M2", raising=False)
    monkeypatch.delenv("FIRESIM_SMALL_FOOTPRINTS", raising=False)
    assert settings.small_footprint_m2 is None
    assert resolve_small_footprint_m2() == 20.0
    assert resolve_small_footprints_mode() == "count_separately"
    monkeypatch.setenv("FIRESIM_SMALL_FOOTPRINT_M2", "0")
    monkeypatch.setenv("FIRESIM_SMALL_FOOTPRINTS", "exclude")
    assert resolve_small_footprint_m2() == 0.0  # read at call time
    assert resolve_small_footprints_mode() == "exclude"
    monkeypatch.setenv("FIRESIM_SMALL_FOOTPRINTS", "nonsense")
    with pytest.raises(ValueError):
        resolve_small_footprints_mode()


async def test_outbuildings_are_counted_separately(client, files, monkeypatch):
    monkeypatch.delenv("FIRESIM_SMALL_FOOTPRINT_M2", raising=False)
    data = await _finish(client, _payload(files))
    assert data["status"] == "completed", data
    last = data["frames"][-1]
    s = last["structure_spread"]
    assert s["small_footprint_m2"] == 20.0 and s["small_footprints"] == "count_separately"
    assert s["units_in_run"] == 5
    assert s["units_involved_all"] == s["units_involved"] + s["units_small_involved"]
    assert s["units_involved"] <= 3
    detail = last["structure_spread_detail"]
    assert len(detail) == s["units_involved_all"]
    assert sum(1 for u in detail if u.get("small")) == s["units_small_involved"]
    e = last["building_exposure"]
    assert e["within_500m"] == 3 and e["small_within_500m"] == 2
    assert e["small_footprint_m2"] == 20
    assert len(last["building_exposure_detail"]) == 3


async def test_threshold_zero_restores_the_former_counts(client, files, monkeypatch):
    monkeypatch.setenv("FIRESIM_SMALL_FOOTPRINT_M2", "0")
    data = await _finish(client, _payload(files))
    assert data["status"] == "completed", data
    last = data["frames"][-1]
    s = last["structure_spread"]
    assert not {"small_footprint_m2", "units_small_involved", "units_involved_all"} & set(s)
    assert len(last["structure_spread_detail"]) == s["units_involved"]
    assert not any("small" in u for u in last["structure_spread_detail"])
    e = last["building_exposure"]
    assert e["within_500m"] == 5 and "small_within_500m" not in e
