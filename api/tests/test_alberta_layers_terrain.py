"""API: Alberta critical assets / major roads by area, and provincial terrain outside Edmonton.

* ``GET /api/v1/layers/assets|roads``: box limits, ``available: false`` without the files,
  features of the box only.
* Terrain selection (docs/terrain-alberta.md): City lattice -> Edmonton DEM (unchanged);
  provincial window + bundled DEM requested + Alberta terrain installed -> window of the
  Alberta file (``terrain_source.kind`` "provincial"); without it the Edmonton DEM as before;
  ``dem_path: null`` -> no terrain. An outside-Edmonton run spreads differently with slope.
* Memory: terrain windows are cached like fuel windows (at most 2).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from httpx import ASGITransport, AsyncClient
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform

from firesim_api.main import create_app

LAT0, LNG0 = 53.5, -113.5
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def grids(tmp_path_factory):
    d = tmp_path_factory.mktemp("abterrain")
    x, y = transform("EPSG:4326", "EPSG:3400", [LNG0], [LAT0])
    x0, y0 = round(x[0] / 100) * 100 - 15_000.0, round(y[0] / 100) * 100 + 10_000.0
    ab = np.full((200, 300), 31, dtype=np.int16)  # O-1 grass everywhere in Alberta
    ab[:, :40] = 0  # western 4 km outside Alberta
    ab_path = d / "ab.tif"
    with rasterio.open(ab_path, "w", driver="GTiff", height=200, width=300, count=1,
                       dtype="int16", crs=CRS.from_epsg(3400),
                       transform=from_origin(x0, y0, 100.0, 100.0)) as dst:
        dst.write(ab, 1)
    cx, cy = transform("EPSG:4326", "EPSG:3776", [LNG0 + 0.03], [LAT0])
    city = np.full((300, 300), 31, dtype=np.int32)  # City grid: O-1a (code 31) 6 x 6 km
    city_path = d / "city.tif"
    with rasterio.open(city_path, "w", driver="GTiff", height=300, width=300, count=1,
                       dtype="int32", nodata=0, crs=CRS.from_epsg(3776),
                       transform=from_origin(cx[0] - 3000, cy[0] + 3000, 20.0, 20.0)) as dst:
        dst.write(city, 1)
    # Alberta terrain: 40 % slope rising to the north (uint16 decimetres, SCALE 0.1)
    dlat, dlng = 0.0009, 0.0015
    rows, cols = int(0.6 / dlat), int(1.2 / dlng)
    y_m = (np.arange(rows)[::-1] + 0.5) * dlat * 111_320
    elev = 500.0 + 0.40 * y_m
    terr = d / "ab_terrain.tif"
    with rasterio.open(terr, "w", driver="GTiff", height=rows, width=cols, count=1,
                       dtype="uint16", nodata=0, crs="EPSG:4326", tiled=True, blockxsize=256,
                       blockysize=256, transform=from_origin(LNG0 - 0.6, LAT0 + 0.3, dlng, dlat)) as dst:
        dst.write(np.tile(np.rint(elev * 10).astype(np.uint16)[:, None], (1, cols)), 1)
        dst.update_tags(SCALE="0.1")
    # "Edmonton DEM": a small flat UTM file standing in for /app/data/edmonton_dem.tif
    ux, uy = transform("EPSG:4326", "EPSG:26912", [LNG0 + 0.03], [LAT0])
    city_dem = d / "edmonton_dem.tif"
    with rasterio.open(city_dem, "w", driver="GTiff", height=250, width=250, count=1,
                       dtype="float32", crs=CRS.from_epsg(26912),
                       transform=from_origin(ux[0] - 3750, uy[0] + 3750, 30.0, 30.0)) as dst:
        dst.write(np.full((250, 250), 650.0, dtype=np.float32), 1)

    def point(col, row):
        lng, lat = transform("EPSG:3400", "EPSG:4326", [x0 + (col + 0.5) * 100],
                             [y0 - (row + 0.5) * 100])
        return lat[0], lng[0]

    return {"ab": str(ab_path), "city": str(city_path), "terrain": str(terr),
            "city_dem": str(city_dem), "point": point, "dir": d}


@pytest.fixture(scope="module")
def layer_files(tmp_path_factory):
    sys.path.insert(0, str(SCRIPTS))
    import build_ab_assets as A

    d = tmp_path_factory.mktemp("layers")
    feats = [{"name": f"School {i}", "category": "school", "detail": "School",
              "xy": (-117.6 + i * 0.015, 53.40), "outline": None,
              "sources": [{"source": A.SRC_OSM, "source_id": f"osm:node/{i}"}]} for i in range(10)]
    path = d / "assets.fgb"
    A.write_fgb(feats, path)
    return {"assets": str(path)}


def _env(monkeypatch, grids, terrain=True):
    monkeypatch.setenv("FIRESIM_AB_FUEL_GRID_PATH", grids["ab"])
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", grids["city"])
    monkeypatch.setenv("FIRESIM_CITY_DEM_PATH", grids["city_dem"])
    monkeypatch.setenv("FIRESIM_PROVINCIAL_WINDOW_KM", "4")
    monkeypatch.setenv("FIRESIM_CITY_MARGIN_KM", "1")
    if terrain:
        monkeypatch.setenv("FIRESIM_AB_TERRAIN_PATH", grids["terrain"])
    else:
        monkeypatch.delenv("FIRESIM_AB_TERRAIN_PATH", raising=False)


@pytest.fixture
async def client(grids, monkeypatch):
    _env(monkeypatch, grids)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        yield ac


def _payload(lat, lng, grids, **extra):
    p = {
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 5.0, "wind_direction": 270.0, "temperature": 25.0,
                    "relative_humidity": 25.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 91.0, "dmc": 60.0, "dc": 400.0},
        "duration_hours": 0.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C3",
        "fuel_grid_path": grids["city"], "dem_path": grids["city_dem"],
        "fuel_modifiers": {"grass_cure": 95.0}, "start_time": "2026-07-15T13:00:00-06:00",
    }
    p.update(extra)
    return p


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


# ── Layers by area ───────────────────────────────────────────────────────────


async def test_layers_unavailable_without_files(monkeypatch):
    monkeypatch.delenv("FIRESIM_AB_ASSETS_PATH", raising=False)
    monkeypatch.delenv("FIRESIM_AB_ROADS_PATH", raising=False)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        for kind in ("assets", "roads"):
            r = await ac.get(f"/api/v1/layers/{kind}", params=dict(south=53.3, west=-117.7,
                                                                 north=53.5, east=-117.4))
            assert r.status_code == 200
            body = r.json()
            assert body["features"] == [] and body["metadata"]["available"] is False
            assert "OpenStreetMap" in body["metadata"]["attribution"]


async def test_layers_box_limits(monkeypatch, layer_files):
    monkeypatch.setenv("FIRESIM_AB_ASSETS_PATH", layer_files["assets"])
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        too_big = await ac.get("/api/v1/layers/assets",
                               params=dict(south=52.0, west=-118.0, north=54.0, east=-117.0))
        assert too_big.status_code == 422
        inverted = await ac.get("/api/v1/layers/assets",
                                params=dict(south=53.5, west=-117.0, north=53.3, east=-117.5))
        assert inverted.status_code == 422
        r = await ac.get("/api/v1/layers/assets",
                         params=dict(south=53.39, west=-117.61, north=53.41, east=-117.52))
        body = r.json()
        assert body["metadata"]["available"] and body["metadata"]["count"] == 6
        assert {f["properties"]["category"] for f in body["features"]} == {"school"}


# ── Terrain selection ────────────────────────────────────────────────────────


async def test_outside_edmonton_uses_alberta_terrain(client, grids):
    lat, lng = grids["point"](200, 100)  # ~5 km east of the City grid's box
    body = await _run(client, _payload(lat, lng, grids))
    assert body["fuel_source"]["kind"] == "provincial"
    ts = body["terrain_source"]
    assert ts["kind"] == "provincial" and "MRDEM" in ts["label"]
    assert "Open Government Licence – Canada" in ts["attribution"]
    assert "terrain" not in body["fuel_source"]


async def test_inside_edmonton_keeps_city_dem(client, grids):
    body = await _run(client, _payload(LAT0, LNG0 + 0.03, grids))
    assert body["fuel_source"]["kind"] == "city"
    assert body["terrain_source"] == {"kind": "city_dem", "label": "Edmonton 30 m DEM",
                                      "attribution": None}


async def test_terrain_off_means_flat(client, grids):
    lat, lng = grids["point"](200, 100)
    body = await _run(client, _payload(lat, lng, grids, dem_path=None))
    assert body["terrain_source"] is None


async def test_without_alberta_terrain_behaves_as_before(grids, monkeypatch):
    _env(monkeypatch, grids, terrain=False)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        lat, lng = grids["point"](200, 100)
        body = await _run(ac, _payload(lat, lng, grids))
    assert body["terrain_source"]["kind"] == "city_dem"
    assert "flat outside Edmonton" in body["terrain_source"]["label"]


def _centroid(cells):
    a = np.array([[c["lat"], c["lng"]] for c in cells])
    return a[:, 0].mean(), a[:, 1].mean()


async def test_slope_changes_spread_outside_edmonton(client, grids):
    """40 % upslope to the north: the fire runs north and grows faster than on the flat."""
    lat, lng = grids["point"](200, 100)
    flat = await _run(client, _payload(lat, lng, grids, dem_path=None))
    slope = await _run(client, _payload(lat, lng, grids))
    fa, sa = flat["frames"][-1]["area_ha"], slope["frames"][-1]["area_ha"]
    assert sa > 1.3 * fa
    clat_s, _ = _centroid(slope["frames"][-1]["burned_cells"])
    clat_f, _ = _centroid(flat["frames"][-1]["burned_cells"])
    assert clat_s > clat_f  # burned area shifted upslope (north)


async def test_terrain_window_cache_is_bounded(client, grids):
    from firesim_api.routers import simulations

    runner = simulations.runner
    for col in (150, 200, 250):
        lat, lng = grids["point"](col, 100)
        await _run(client, _payload(lat, lng, grids, duration_hours=0.25))
    assert len(runner._terrain_cache) <= 2
    for t in runner._terrain_cache.values():
        # two rows of doubles per cell: 4 km window at 50 m is 80 x 80
        assert t.rows * t.cols * 16 < 200_000


async def test_burn_probability_uses_alberta_terrain(client, grids, monkeypatch):
    from firesim_api.routers import simulations

    seen = {}
    real = simulations.runner._window_terrain

    def spy(grid, dem):
        out = real(grid, dem)
        seen["kind"] = out[1]["kind"] if out[1] else None
        return out

    monkeypatch.setattr(simulations.runner, "_window_terrain", spy)
    lat, lng = grids["point"](200, 100)
    r = await client.post("/api/v1/simulations/burn-probability", json={
        "ignition_lat": lat, "ignition_lng": lng,
        "weather": {"wind_speed": 5.0, "wind_direction": 270.0},
        "fwi_overrides": {"ffmc": 91.0, "dmc": 60.0, "dc": 400.0},
        "duration_hours": 0.25, "n_iterations": 2, "fuel_grid_path": grids["city"],
        "dem_path": grids["city_dem"], "fuel_modifiers": {"grass_cure": 95.0},
    })
    assert r.status_code == 200, r.text
    assert seen["kind"] == "provincial"
