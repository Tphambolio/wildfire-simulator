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


async def test_involved_units_detail_on_the_final_frame_only(client, files):
    """Map detail (owner decision 2026-10-10): only involved units, with time and mechanism."""
    data = await _finish(client, _payload(files, structure_spread=True))
    frames = data["frames"]
    assert all(f.get("structure_spread_detail") is None for f in frames[:-1])
    last = frames[-1]
    detail = last["structure_spread_detail"]
    counts = last["structure_spread"]
    assert len(detail) == counts["units_involved"] >= 1
    assert sum(u["mechanism"] == "front" for u in detail) == counts["units_front_contact"]
    assert sum(u["mechanism"] == "b2b" for u in detail) == counts["units_structure_to_structure"]
    for u in detail:
        assert set(u) == {"id", "t_h", "t_out_h", "mechanism", "source_id", "polygon"}
        assert 0 <= u["t_h"] <= 1.5
        ring = u["polygon"][0]
        assert ring[0] == ring[-1] and len(ring) >= 4
    # Units are built only around the fire, but units_in_run counts the whole run area
    assert counts["computed"] is True and counts["units_built"] <= counts["units_in_run"] == 3


async def test_too_many_buildings_returns_not_computed_instead_of_crashing(client, files, monkeypatch):
    """OOM guard: over the unit limit the run completes and says why there are no counts."""
    import firesim.structures.spread as sp

    monkeypatch.setattr(sp, "DEFAULT_MAX_UNITS", 1)
    data = await _finish(client, _payload(files, structure_spread=True))
    assert data["status"] == "completed", data
    last = data["frames"][-1]
    s = last["structure_spread"]
    assert s["computed"] is False and s["note"] == "not computed: too many buildings in run area"
    assert s["label"] == LABEL and s["units_involved"] is None and s["max_units"] == 1
    assert last["structure_spread_detail"] == []


async def test_structure_spread_off_has_no_detail(client, files):
    data = await _finish(client, _payload(files))
    assert all(f.get("structure_spread_detail") is None for f in data["frames"])


def test_building_index_footprint_source(files):
    from firesim.data.building_index import BuildingIndex

    bidx = BuildingIndex(files["buildings"], files["nbhd"])
    world = (-90.0, 90.0, -180.0, 180.0)
    assert bidx.count_in_box(world) == 3
    assert bidx.count_intersecting(world) == 3
    assert len(bidx.footprints_intersecting(world)) == 3
    # A box west of the houses holds none; a box over the first house's west edge holds one
    lat, lng = files["lat"], files["lng"]
    m_lng = 1 / (111320.0 * np.cos(np.radians(lat)))
    west = (lat - 0.001, lat + 0.001, lng, lng + 290 * m_lng)
    assert bidx.count_intersecting(west) == 0 and bidx.footprints_intersecting(west) == []
    first = (lat - 0.001, lat + 0.001, lng, lng + 301 * m_lng)
    assert bidx.count_intersecting(first) == 1
    assert bidx.count_in_box(first) == 0  # its centroid is further east


# ------------------------------------------------------------------ ember ignition (spec §6)


def test_ember_flags_default_off_and_need_structure_spread():
    from pydantic import ValidationError

    from firesim_api.schemas.simulation import SimulationCreate

    base = dict(ignition_lat=53.5, ignition_lng=-113.5,
                weather={"wind_speed": 10.0, "wind_direction": 270.0})
    req = SimulationCreate(**base)
    assert req.structure_embers is False and req.structure_design_fire_kw_m2 == 150
    with pytest.raises(ValidationError):
        SimulationCreate(**base, structure_embers=True)
    with pytest.raises(ValidationError):
        SimulationCreate(**base, structure_spread=True, structure_design_fire_kw_m2=250)
    ok = SimulationCreate(**base, structure_spread=True, structure_embers=True,
                          structure_design_fire_kw_m2=400)
    assert ok.structure_embers is True and ok.structure_design_fire_kw_m2 == 400


async def test_embers_without_structure_spread_is_422(client, files):
    resp = await client.post("/api/v1/simulations", json=_payload(files, structure_embers=True))
    assert resp.status_code == 422


@pytest.mark.parametrize("design_fire", [150, 400])
async def test_structure_embers_on_reports_ember_counts_and_detail(client, files, design_fire):
    data = await _finish(client, _payload(files, structure_spread=True, structure_embers=True,
                                          structure_design_fire_kw_m2=design_fire))
    assert data["status"] == "completed", data
    assert data["config"]["structure_embers"] is True
    last = data["frames"][-1]
    c = last["structure_spread"]
    assert c["label"] == LABEL and c["embers"] is True
    assert c["design_fire_kw_m2"] == design_fire
    assert c["ember_generation_pcs_per_mw_s"] == 10.0 and c["embers_from_wildland"] is True
    assert c["units_involved"] == (c["units_front_contact"] + c["units_structure_to_structure"]
                                   + c["units_ember"])
    assert 0 <= c["units_ember_from_wildland"] <= c["units_ember"]
    detail = last["structure_spread_detail"]
    assert len(detail) == c["units_involved"]
    assert sum(u["mechanism"] == "ember" for u in detail) == c["units_ember"]
    assert {u["mechanism"] for u in detail} <= {"front", "b2b", "ember"}
    for f in data["frames"]:
        assert "units_ember" in f["structure_spread"]


async def test_structure_spread_without_embers_has_no_ember_fields(client, files):
    data = await _finish(client, _payload(files, structure_spread=True))
    c = data["frames"][-1]["structure_spread"]
    assert "units_ember" not in c and "embers" not in c


# ------------------------------------------------------------------ burn-out (spec §4.3)


def test_burnout_defaults_on():
    from firesim_api.schemas.simulation import SimulationCreate

    req = SimulationCreate(ignition_lat=53.5, ignition_lng=-113.5,
                           weather={"wind_speed": 10.0, "wind_direction": 270.0},
                           structure_spread=True)
    assert req.structure_burnout is True and req.structure_design_fire_kw_m2 == 150


@pytest.mark.parametrize("design_fire,burnout_min", [(150, 66.0), (400, 70.0)])
async def test_burnout_counts_and_detail(client, files, design_fire, burnout_min):
    data = await _finish(client, _payload(files, structure_spread=True, duration_hours=3.0,
                                          structure_design_fire_kw_m2=design_fire))
    assert data["status"] == "completed", data
    assert data["config"]["structure_burnout"] is True
    for f in data["frames"]:
        c = f["structure_spread"]
        assert c["burnout"] is True and c["burnout_min"] == pytest.approx(burnout_min)
        assert c["design_fire_kw_m2"] == design_fire
        assert c["units_burning"] + c["units_burnt_out"] == c["units_involved"]
    last = data["frames"][-1]
    detail = last["structure_spread_detail"]
    for u in detail:
        assert u["t_out_h"] == pytest.approx(u["t_h"] + burnout_min / 60.0, abs=2e-3)
    t_end = last["time_hours"]
    assert last["structure_spread"]["units_burnt_out"] == sum(u["t_out_h"] <= t_end + 1e-6
                                                             for u in detail)
    assert last["structure_spread"]["units_burnt_out"] >= 1  # 3 h run: early units burnt out


async def test_burnout_off_is_the_published_hamada(client, files):
    data = await _finish(client, _payload(files, structure_spread=True, duration_hours=3.0,
                                          structure_burnout=False))
    assert data["status"] == "completed", data
    last = data["frames"][-1]
    c = last["structure_spread"]
    assert c["burnout"] is False and c["burnout_min"] is None
    assert c["units_burnt_out"] == 0 and c["units_burning"] == c["units_involved"]
    assert all(u["t_out_h"] is None for u in last["structure_spread_detail"])
