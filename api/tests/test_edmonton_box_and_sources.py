"""API: the Edmonton extract box is no longer a limit (decision 2026-10-12) and runs report the
building source per footprint (municipal ODB footprints in the 2026-10 Alberta file).

docs/buildings-alberta.md §5-6.

* An Edmonton run (ignition in the box) whose fire reaches houses just south of 53.39 N: with
  the box dropped, structure spread counts them; ``FIRESIM_EDMONTON_BOX_LEGACY=1`` restores the
  former clip (they are ignored).
* A run outside Edmonton on a file with a ``source`` field reports the selected buildings per
  source, with the municipal provider in the label and its licence in the attribution.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
import rasterio
from httpx import ASGITransport, AsyncClient
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from firesim.data.building_index import EDMONTON_EXTRACT_BOX
from firesim_api.main import create_app


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


def _site(d, name, lat, lng):
    """A 50 x 50 C-2 grid (50 m, UTM 12N) centred on (lat, lng) and 20 houses (12 m, 6 m
    apart) 250-334 m east of the centre, rows 30 m south to 24 m north of it."""
    x, y = Transformer.from_crs(4326, 32612, always_xy=True).transform(lng, lat)
    fuel = d / f"{name}.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=50, width=50, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612),
                       transform=from_origin(x - 1250.0, y + 1250.0, 50.0, 50.0)) as dst:
        dst.write(np.full((50, 50), 2, dtype=np.int32), 1)
    m_lat, m_lng = 1 / 111320.0, 1 / (111320.0 * np.cos(np.radians(lat)))
    houses = [_box(lng + (250 + 18 * i) * m_lng, lat + (18 * j - 30) * m_lat,
                   lng + (262 + 18 * i) * m_lng, lat + (18 * j - 18) * m_lat)
              for i in range(5) for j in range(4)]
    return {"fuel": str(fuel), "lat": lat, "lng": lng, "houses": houses}


def _write_fgb(path, geoms, ids, sources):
    import pyogrio.raw
    import shapely

    pyogrio.raw.write(str(path), np.asarray(shapely.to_wkb(geoms), dtype=object),
                      [np.asarray(ids, dtype=np.int64), np.zeros(len(ids)),
                       np.asarray(sources, dtype=object)],
                      ["id", "area_m2", "source"], driver="FlatGeobuf",
                      geometry_type="Polygon", crs="EPSG:4326",
                      layer_options={"SPATIAL_INDEX": "YES"})


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("edm_box")
    # Ignition 1 m north of the box's south edge; the house rows straddle 53.39 N
    edge = _site(d, "edge", EDMONTON_EXTRACT_BOX[0] + 0.0001, -113.50)
    canmore = _site(d, "canmore", 51.09, -115.35)
    geoms = [Polygon(h[:-1]) for h in edge["houses"] + canmore["houses"]]
    # Edmonton-extract houses keep ids <= 346,238; the others are Microsoft (1,000,000 + n)
    # or municipal ODB (10,000,000 + fid)
    ids, sources = [], []
    for k, g in enumerate(geoms[:20]):
        south = g.exterior.coords[:][0][1] + 6 / 111320.0 < EDMONTON_EXTRACT_BOX[0]
        ids.append(1_000_000 + k if south else k + 1)
        sources.append("ms2019")
    for k in range(20):
        ids.append(10_000_000 + k + 1 if k < 15 else 1_100_000 + k)
        sources.append("odb:canmore" if k < 15 else "ms2019")
    ab = d / "ab_buildings.fgb"
    _write_fgb(ab, geoms, ids, sources)
    city = d / "edmonton_buildings.geojson"
    city.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    # One neighbourhood polygon around the edge site, inside the box only
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"neighbourhood": "EDGE"},
         "geometry": {"type": "Polygon", "coordinates": [_box(
             -113.53, EDMONTON_EXTRACT_BOX[0], -113.48, EDMONTON_EXTRACT_BOX[0] + 0.02)]}},
        {"type": "Feature", "properties": {"neighbourhood": "EDGE SOUTH"},
         "geometry": {"type": "Polygon", "coordinates": [_box(
             -113.53, EDMONTON_EXTRACT_BOX[0] - 0.02, -113.48, EDMONTON_EXTRACT_BOX[0])]}}]}))
    n_south = sum(1 for i in ids[:20] if i >= 1_000_000)
    assert 0 < n_south < 20
    return {"edge": edge, "canmore": canmore, "ab": str(ab), "city": str(city),
            "nbhd": str(nbhd), "n_south": n_south}


@pytest.fixture
def env(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    monkeypatch.setenv("FIRESIM_CITY_BUILDINGS_PATH", files["city"])
    monkeypatch.setenv("FIRESIM_AB_BUILDINGS_PATH", files["ab"])
    monkeypatch.delenv("FIRESIM_EDMONTON_BOX_LEGACY", raising=False)
    return monkeypatch


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


async def test_edmonton_run_counts_buildings_south_of_the_box(files, env):
    data = await _run(_payload(files["edge"], files["city"]))
    assert data["buildings_source"]["kind"] == "edmonton_neighbourhoods"
    # mask/exposure: the south neighbourhood's houses (outside the box, within its polygon)
    # are assigned to it, so all 20 are selected
    assert data["buildings_source"]["buildings"] == 20
    ss = data["frames"][-1]["structure_spread"]
    assert ss["units_in_run"] == 20


async def test_legacy_setting_restores_the_box(files, env):
    env.setenv("FIRESIM_EDMONTON_BOX_LEGACY", "1")
    data = await _run(_payload(files["edge"], files["city"]))
    n_in_box = 20 - files["n_south"]
    assert data["buildings_source"]["buildings"] == n_in_box
    assert data["frames"][-1]["structure_spread"]["units_in_run"] == n_in_box


async def test_run_reports_sources_and_municipal_attribution(files, env):
    data = await _run(_payload(files["canmore"], files["city"], structure_spread=False))
    src = data["buildings_source"]
    assert src["kind"] == "near_ignition" and src["buildings"] == 20
    assert src["sources"] == {"ms2019": 5, "odb:canmore": 15}
    assert "Town of Canmore" in src["label"] and "Microsoft" in src["label"]
    assert "Town of Canmore Open Data Licence" in src["attribution"]
    assert "ODbL" in src["attribution"]


def test_buildings_info_without_sources_is_unchanged():
    from firesim_api.services.runner import MS_BUILDINGS_ATTRIBUTION, _buildings_info

    info = _buildings_info("near_ignition", "/x/ab.fgb", 3, False, "run area")
    assert info["label"] == "Microsoft Canadian Building Footprints, Alberta (2019)"
    assert info["attribution"] == MS_BUILDINGS_ATTRIBUTION and "sources" not in info
    only_odb = _buildings_info("near_ignition", "/x/ab.fgb", 3, False, "run area",
                               {"odb:lethbridge": 3})
    assert only_odb["label"].startswith("City of Lethbridge") and "Microsoft" not in only_odb["label"]
    assert "ODbL" not in only_odb["attribution"]
