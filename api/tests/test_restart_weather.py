"""Perimeter restart fixes and the manual weather schedule (owner field tests 2026-10-12).

- A1: a restart uses the request's weather and FWI codes (the app sends Setup's), the
  source run's only when none is sent; ``weather_used`` says which.
- A2: Predict N h runs N h, and the WebSocket delivers every frame, in order, before
  ``simulation.completed`` (which carries ``n_frames``).
- A4: restarts load buildings like normal runs: building mask, exposure, house-to-house.
- B: ``weather_schedule`` (validation, effect, restart re-basing).
- Fuel-grid vintage warning (issue 6).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from starlette.testclient import TestClient

from firesim_api.main import create_app
from firesim_api.schemas.simulation import PerimeterOverrideRequest, SimulationCreate
from firesim_api.services.runner import fuel_vintage_warning, fuel_vintage_year, restart_config

START = "2011-05-14T17:50:00-06:00"


def _box(lng0, lat0, lng1, lat1):
    return [[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("restart")
    fuel = d / "fuel.tif"
    with rasterio.open(fuel, "w", driver="GTiff", height=60, width=60, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612),
                       transform=from_origin(350_000.0, 5_930_000.0, 50.0, 50.0)) as dst:
        dst.write(np.full((60, 60), 2, dtype=np.int32), 1)  # all C-2
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat, lng = (south + north) / 2, (west + east) / 2
    m_lat = 1 / 111320.0
    m_lng = 1 / (111320.0 * np.cos(np.radians(lat)))
    houses = [_box(lng + (250 + 18 * k) * m_lng, lat - 6 * m_lat,
                   lng + (262 + 18 * k) * m_lng, lat + 6 * m_lat) for k in range(3)]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [h]}, "properties": {}}
        for h in houses]}
    buildings = d / "buildings.geojson"
    buildings.write_text(json.dumps(fc))
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"neighbourhood": "TEST"},
        "geometry": {"type": "Polygon", "coordinates": [
            _box(west - 0.01, south - 0.01, east + 0.01, north + 0.01)]}}]}))
    square = {"type": "Polygon", "coordinates": [_box(lng - 60 * m_lng, lat - 60 * m_lat,
                                                      lng + 60 * m_lng, lat + 60 * m_lat)]}
    return {"fuel": str(fuel), "buildings": str(buildings), "nbhd": str(nbhd), "lat": lat,
            "lng": lng, "square": square}


@pytest.fixture
def tc(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    monkeypatch.setenv("FIRESIM_ARCHIVE", "0")
    with TestClient(create_app()) as client:
        yield client


def _payload(files, **extra):
    p = {
        "ignition_lat": files["lat"], "ignition_lng": files["lng"],
        "weather": {"wind_speed": 30.0, "wind_direction": 270.0, "temperature": 20.0,
                    "relative_humidity": 15.0, "precipitation_24h": 0.0},
        "fwi_overrides": {"ffmc": 90.0, "dmc": 15.0, "dc": 193.0},
        "duration_hours": 1.0, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
        "fuel_grid_path": files["fuel"], "buildings_path": files["buildings"],
        "fuel_modifiers": {"grass_cure": 90.0}, "start_time": START,
    }
    p.update(extra)
    return p


def _wait(tc, sim_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = tc.get(f"/api/v1/simulations/{sim_id}").json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.2)
    raise TimeoutError(sim_id)


def _run(tc, payload):
    r = tc.post("/api/v1/simulations", json=payload)
    assert r.status_code == 200, r.text
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    return data


def _restart(tc, src_id, files, **extra):
    body = {"simulation_id": src_id, "perimeter_geojson": files["square"],
            "duration_hours": 1.0, "snapshot_interval_minutes": 30.0,
            "start_time": "2011-05-14T18:50:00-06:00", **extra}
    r = tc.post("/api/v1/simulations/perimeter-override", json=body)
    assert r.status_code == 200, r.text
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    return data


# ── A1: restart weather ─────────────────────────────────────────────────────────

def test_restart_uses_request_weather_else_source_run(tc, files):
    src = _run(tc, _payload(files))["simulation_id"]
    inherited = _restart(tc, src, files)
    assert inherited["weather_used"]["source"] == "source_run"
    assert inherited["weather_used"]["weather"]["wind_speed"] == 30.0
    calm = _restart(tc, src, files,
                    weather={"wind_speed": 5.0, "wind_direction": 270.0, "temperature": 20.0,
                             "relative_humidity": 15.0, "precipitation_24h": 0.0},
                    fwi_overrides={"ffmc": 88.0, "dmc": 15.0, "dc": 193.0})
    used = calm["weather_used"]
    assert used["source"] == "request" and used["weather"]["wind_speed"] == 5.0
    assert used["fwi_overrides"]["ffmc"] == 88.0
    assert calm["config"]["weather"]["wind_speed"] == 5.0  # the request it ran as
    assert calm["frames"][-1]["area_ha"] < inherited["frames"][-1]["area_ha"]
    assert calm["frames"][-1]["head_ros_m_min"] < inherited["frames"][-1]["head_ros_m_min"]


# ── A2: full duration, every frame over the WebSocket ───────────────────────────

def test_restart_runs_full_predict_and_ws_delivers_every_frame(tc, files):
    src = _run(tc, _payload(files))["simulation_id"]
    r = tc.post("/api/v1/simulations/perimeter-override", json={
        "simulation_id": src, "perimeter_geojson": files["square"], "duration_hours": 3.0,
        "snapshot_interval_minutes": 30.0, "start_time": "2011-05-14T18:50:00-06:00"})
    sim_id = r.json()["simulation_id"]
    times, completed = [], None
    with tc.websocket_connect(f"/api/v1/simulations/ws/{sim_id}") as ws:
        for _ in range(500):
            msg = ws.receive_json()
            if msg["type"] == "simulation.frame":
                times.append(msg["frame"]["time_hours"])
            elif msg["type"] == "simulation.completed":
                completed = msg
                break
            elif msg["type"] == "simulation.error":
                pytest.fail(msg["error"])
    assert completed is not None
    assert times == [0.5 * k for k in range(7)]  # 0 .. 3 h, each once, in order
    assert completed["n_frames"] == len(times)


def test_late_ws_join_gets_all_frames_then_completed(tc, files):
    data = _run(tc, _payload(files))
    with tc.websocket_connect(f"/api/v1/simulations/ws/{data['simulation_id']}") as ws:
        msgs = [ws.receive_json() for _ in range(len(data["frames"]) + 1)]
    assert [m["type"] for m in msgs] == ["simulation.frame"] * len(data["frames"]) + [
        "simulation.completed"]
    assert msgs[-1]["n_frames"] == len(data["frames"])


# ── A4: buildings on restarts ────────────────────────────────────────────────────

def test_restart_has_building_exposure_and_house_to_house(tc, files):
    src = _run(tc, _payload(files, structure_spread=True))["simulation_id"]
    data = _restart(tc, src, files, duration_hours=2.0)
    last = data["frames"][-1]
    assert data["buildings_source"] is not None
    assert last["building_exposure"] is not None
    assert last["structure_spread"] is not None and last["structure_spread"]["units_in_run"] == 3
    assert last["buildings_at_risk"] >= 1  # the fire reaches the houses 250 m downwind


# ── B: weather schedule ──────────────────────────────────────────────────────────

def _step(h, ws):
    return {"hours_from_start": h, "wind_speed": ws, "wind_direction": 270.0,
            "temperature": 20.0, "relative_humidity": 15.0}


def test_schedule_validation(tc, files):
    hourly = [{"hours_from_start": 0, "temperature": 20, "relative_humidity": 15,
               "wind_speed": 10, "wind_direction": 270}]
    for extra in ({"weather_schedule": [_step(0.5, 5.0)], "hourly_weather": hourly},
                  {"weather_schedule": [_step(1.0, 5.0)]},  # at the end of a 1 h run
                  {"weather_schedule": [_step(0.5, 5.0), _step(0.5, 9.0)]}):
        r = tc.post("/api/v1/simulations", json=_payload(files, **extra))
        assert r.status_code == 422, extra


def test_schedule_wind_drop_slows_spread_and_is_reported(tc, files):
    const = _run(tc, _payload(files, duration_hours=2.0))
    drop = _run(tc, _payload(files, duration_hours=2.0, weather_schedule=[_step(0.5, 0.0)]))
    # Before the change the scheduled run is at least as fast: its FFMC is recomputed hourly
    # from 90 and dries in 20 C / 15 % RH (the constant run keeps the daily 90)
    assert drop["frames"][1]["area_ha"] >= const["frames"][1]["area_ha"]
    assert drop["frames"][-1]["area_ha"] < const["frames"][-1]["area_ha"]
    assert drop["weather_used"]["weather_schedule"][0]["wind_speed"] == 0.0
    assert drop["config"]["weather_schedule"][0]["hours_from_start"] == 0.5


def test_restart_config_rebases_the_source_schedule():
    src = SimulationCreate(ignition_lat=53.5, ignition_lng=-113.5, start_time=START,
                           weather={"wind_speed": 47.0, "wind_direction": 135.0},
                           duration_hours=6.0, fuel_type="C2",
                           weather_schedule=[_step(2.0 + 10 / 60, 20.0), _step(4.0, 10.0)])
    t0 = datetime.fromisoformat(START)
    req = PerimeterOverrideRequest(simulation_id="x", perimeter_geojson={"type": "Polygon"},
                                   duration_hours=4.0, start_time=t0 + timedelta(hours=3))
    params, source = restart_config(req, src)
    assert source == "source_run"
    assert params.weather.wind_speed == 20.0  # the step in force at the restart
    assert [(round(c.hours_from_start, 3), c.wind_speed) for c in params.weather_schedule] == [
        (1.0, 10.0)]
    # Sent weather wins, and without a schedule none is inherited
    req2 = req.model_copy(update={"weather": src.weather})
    params2, source2 = restart_config(req2, src)
    assert source2 == "request" and params2.weather_schedule is None


# ── Fuel-grid vintage (issue 6) ──────────────────────────────────────────────────

def test_fuel_vintage_year_by_source():
    assert fuel_vintage_year({"kind": "city"}) == 2025
    assert fuel_vintage_year({"kind": "provincial", "share": {"ab_wildfire": 1.0}}) == 2025
    assert fuel_vintage_year({"kind": "national"}, "cfs_national_2014") == 2011
    assert fuel_vintage_year({"kind": "national"}, "cfs_national") == 2025
    assert fuel_vintage_year({"kind": "provincial", "share": {"national": 1.0}},
                             "cfs_national_2014") == 2011
    for kind in ("uniform", "synthetic", "user_grid"):
        assert fuel_vintage_year({"kind": kind}) is None


def test_fuel_vintage_warning_text():
    tz = timezone(timedelta(hours=-6))
    msg = fuel_vintage_warning(2025, datetime(2011, 5, 14, tzinfo=tz))
    assert msg.startswith("Fuel grid represents 2025. Areas burned since 2011")
    assert fuel_vintage_warning(2025, datetime(2025, 5, 14, tzinfo=tz)) is None
    assert fuel_vintage_warning(None, datetime(2011, 5, 14, tzinfo=tz)) is None


def test_run_on_the_city_grid_dated_2011_carries_the_flag(files, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", files["nbhd"])
    monkeypatch.setenv("FIRESIM_CITY_FUEL_GRID_PATH", files["fuel"])  # treat it as the City grid
    monkeypatch.delenv("FIRESIM_AB_FUEL_GRID_PATH", raising=False)
    with TestClient(create_app()) as client:
        old = _run(client, _payload(files))
        new = _run(client, _payload(files, start_time="2026-05-14T17:50:00-06:00"))
    assert old["fuel_source"]["vintage_year"] == 2025
    assert "Areas burned since 2011" in old["fuel_vintage_warning"]
    assert new["fuel_vintage_warning"] is None
