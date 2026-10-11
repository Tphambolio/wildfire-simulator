"""API: buildings for all of Alberta (windowed FlatGeobuf; docs/buildings-alberta.md).

* Edmonton (ignition in the former extract's box): the Alberta file gives the same results as
  the Edmonton GeoJSON (building exposure, inside-perimeter counts, structure spread).
* Elsewhere (a Hinton ignition): buildings near the ignition are read from the Alberta file,
  so exposure and structure counts are produced (the Edmonton file has none there).
* The request still names the Edmonton file; the server substitutes the Alberta file when it
  is installed, and runs without it as before.
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
from shapely.geometry import shape

from firesim.data.building_index import write_buildings_fgb
from firesim_api.main import create_app


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


def _site(d, name, epsg, x0, y0):
    """A 50 x 50 C-2 grid (50 m) and a block of 12 m houses 6 m apart east of its centre."""
    fuel = d / f"{name}.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=50, width=50, count=1, dtype="int32",
                       crs=CRS.from_epsg(epsg), transform=from_origin(x0, y0, 50.0, 50.0)) as dst:
        dst.write(np.full((50, 50), 2, dtype=np.int32), 1)
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat, lng = (south + north) / 2, (west + east) / 2
    m_lat, m_lng = 1 / 111320.0, 1 / (111320.0 * np.cos(np.radians(lat)))
    houses = [_box(lng + (250 + 18 * i) * m_lng, lat + (18 * j - 30) * m_lat,
                   lng + (262 + 18 * i) * m_lng, lat + (18 * j - 18) * m_lat)
              for i in range(5) for j in range(4)]
    return {"fuel": str(fuel), "lat": lat, "lng": lng, "houses": houses,
            "bounds": (west, south, east, north)}


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("ab_buildings")
    edm = _site(d, "edm", 32612, 340_000.0, 5_930_000.0)      # ~53.49 N 113.41 W (Edmonton box)
    hin = _site(d, "hinton", 32611, 461_200.0, 5_918_700.0)   # ~53.40 N 117.57 W (Hinton)
    # The "Edmonton extract": Edmonton houses only (ids 1..20)
    feats = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [h]},
              "properties": {"id": i + 1}} for i, h in enumerate(edm["houses"])]
    city = d / "edmonton_buildings.geojson"
    city.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    # The "Alberta file": the same Edmonton houses and ids, plus Hinton houses
    geoms = [shape({"type": "Polygon", "coordinates": [h]}) for h in edm["houses"] + hin["houses"]]
    ids = list(range(1, 21)) + [1_000_000 + k for k in range(1, 21)]
    ab = d / "ab_buildings.fgb"
    write_buildings_fgb(str(ab), geoms, ids)
    w, s, e, n = edm["bounds"]
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"neighbourhood": "TEST"},
         "geometry": {"type": "Polygon", "coordinates": [_box(w - 0.01, s - 0.01, e + 0.01, n + 0.01)]}}]}))
    return {"edm": edm, "hin": hin, "city": str(city), "ab": str(ab), "nbhd": str(nbhd)}


def _payload(site, buildings_path, **extra):
    return {
        "ignition_lat": site["lat"], "ignition_lng": site["lng"],
        "weather": {"wind_speed": 30.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 45.0, "dc": 300.0},
        "duration_hours": 1.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
        "fuel_grid_path": site["fuel"], "buildings_path": buildings_path,
        "fuel_modifiers": {"grass_cure": 60.0}, "structure_spread": True, **extra,
    }


async def _run(payload) -> dict:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        resp = await ac.post("/api/v1/simulations", json=payload)
        assert resp.status_code == 200, resp.text
        sim_id = resp.json()["simulation_id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            data = (await ac.get(f"/api/v1/simulations/{sim_id}")).json()
            if data["status"] in ("completed", "failed"):
                assert data["status"] == "completed", data.get("error")
                return data
            time.sleep(0.25)
    raise TimeoutError(sim_id)


def _summary(data):
    out = []
    for f in data["frames"]:
        ss = f.get("structure_spread") or {}
        out.append((f.get("buildings_at_risk"), json.dumps(f.get("building_exposure"), sort_keys=True),
                    ss.get("units_involved"), ss.get("units_front_contact"),
                    ss.get("units_structure_to_structure"), ss.get("units_in_run")))
    return out


@pytest.fixture
def env(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    monkeypatch.setenv("FIRESIM_CITY_BUILDINGS_PATH", files["city"])
    monkeypatch.delenv("FIRESIM_AB_BUILDINGS_PATH", raising=False)
    return monkeypatch


async def test_edmonton_run_same_with_alberta_file(files, env):
    before = await _run(_payload(files["edm"], files["city"]))
    assert before["buildings_source"]["kind"] == "edmonton_neighbourhoods"
    env.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"])
    after = await _run(_payload(files["edm"], files["city"]))
    src = after["buildings_source"]
    assert src["kind"] == "edmonton_neighbourhoods" and src["buildings"] == 20
    assert "Alberta" in src["label"] and "ODbL" in src["attribution"]
    assert _summary(after) == _summary(before)
    last = after["frames"][-1]
    assert last["structure_spread"]["units_involved"] > 0
    # the Hinton houses (in the Alberta file only) never enter an Edmonton run
    assert last["structure_spread"]["units_in_run"] == 20


async def test_hinton_run_gets_buildings_from_alberta_file(files, env):
    without = await _run(_payload(files["hin"], files["city"]))
    last0 = without["frames"][-1]
    assert (last0.get("building_exposure") or {}).get("within_500m", 0) == 0
    env.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"])
    data = await _run(_payload(files["hin"], files["city"]))
    src = data["buildings_source"]
    assert src["kind"] == "near_ignition" and src["buildings"] == 20 and not src["capped"]
    last = data["frames"][-1]
    assert last["building_exposure"]["within_500m"] == 20
    ss = last["structure_spread"]
    assert ss["computed"] and ss["units_in_run"] == 20 and ss["units_involved"] > 0


async def test_cap_keeps_nearest_buildings(files, env):
    env.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"])
    env.setenv("FIRESIM_BUILDINGS_MAX_NEAR", "5")
    data = await _run(_payload(files["hin"], files["city"], structure_spread=False))
    src = data["buildings_source"]
    assert src["capped"] and src["buildings"] == 5


def test_resolve_buildings_path(files, env):
    from firesim_api.services.runner import SimulationRunner

    r = SimulationRunner.resolve_buildings_path
    assert r(files["city"]) == files["city"]  # Alberta file not installed
    env.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"])
    assert r(files["city"]) == files["ab"]
    assert r(None) is None                    # no buildings asked for: none
    assert r("/elsewhere/user.geojson") == "/elsewhere/user.geojson"
    env.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"] + ".missing")
    assert r(files["city"]) == files["city"]
