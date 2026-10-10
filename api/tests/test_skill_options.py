"""Spread-skill options on the API: burning period and FFMC spin-up (docs/validation.md).

Known answers:
- burning_period round-trips (object or [start, end] list) and ffmc_spin_up is echoed;
- bad hours (outside 0-24, start >= end, wrong list length), a burning period without
  start_time, and a spin-up without hourly weather back to 17:00 local are 422;
- a run starting at 21:00 local with a 10-20 h burning period spreads nothing until 10:00 the
  next day (13 h), uniform fuel (Huygens) and grid alike, then grows;
- the clock is start_time's own offset: the same instant sent in UTC is a different clock;
- multi-day runs and perimeter restarts take the burning period too.
"""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from firesim_api.main import create_app
from firesim_api.schemas.simulation import SimulationCreate

BASE = {
    "ignition_lat": 53.5, "ignition_lng": -113.5,
    "weather": {"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                "relative_humidity": 25.0},
    "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
    "fuel_type": "C2",
}


def _hours(first: int, last: int) -> list[dict]:
    return [{"hours_from_start": float(h), "temperature": 20.0, "relative_humidity": 40.0,
             "wind_speed": 15.0, "wind_direction": 270.0} for h in range(first, last)]


def _wait(tc: TestClient, sim_id: str, timeout_s: float = 120.0) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        data = tc.get(f"/api/v1/simulations/{sim_id}").json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.2)
    raise AssertionError(f"{sim_id} did not finish")


@pytest.fixture
def tc():
    with TestClient(create_app()) as client:
        yield client


def test_round_trip(tc):
    payload = dict(BASE, duration_hours=1.0, start_time="2026-07-15T06:00:00-06:00",
                   burning_period={"start_hour": 10, "end_hour": 20}, ffmc_spin_up=True,
                   hourly_weather=_hours(-13, 2))
    resp = tc.post("/api/v1/simulations", json=payload)
    assert resp.status_code == 200, resp.text
    cfg = tc.get(f"/api/v1/simulations/{resp.json()['simulation_id']}").json()["config"]
    assert cfg["burning_period"] == {"start_hour": 10.0, "end_hour": 20.0}
    assert cfg["ffmc_spin_up"] is True
    # the list form and the defaults
    req = SimulationCreate(**dict(BASE, start_time="2026-07-15T06:00:00-06:00",
                                  burning_period=[10, 20]))
    assert req.burning_period.as_tuple() == (10.0, 20.0) and req.ffmc_spin_up is False
    assert SimulationCreate(**BASE).burning_period is None


@pytest.mark.parametrize("extra, message", [
    ({"burning_period": {"start_hour": 20, "end_hour": 10}}, "before end_hour"),
    ({"burning_period": {"start_hour": 10, "end_hour": 10}}, "before end_hour"),
    ({"burning_period": {"start_hour": -1, "end_hour": 10}}, "greater than or equal"),
    ({"burning_period": {"start_hour": 10, "end_hour": 25}}, "less than or equal"),
    ({"burning_period": [10, 20, 22]}, "[start_hour, end_hour]"),
    ({"burning_period": [10, 20], "start_time": None}, "needs start_time"),
    ({"ffmc_spin_up": True}, "needs hourly_weather"),
    ({"ffmc_spin_up": True, "hourly_weather": _hours(-9, 3)}, "17:00"),  # back to 21:00 only
    ({"ffmc_spin_up": True, "hourly_weather": _hours(-13, -2)}, "for the run"),
    ({"hourly_weather": _hours(-30, 3)}, "greater than or equal"),  # more than 24 h back
])
def test_validation_errors(tc, extra, message):
    payload = {**BASE, "duration_hours": 1.0, "start_time": "2026-07-15T06:00:00-06:00", **extra}
    resp = tc.post("/api/v1/simulations", json=payload)
    assert resp.status_code == 422, resp.text
    assert message in resp.text


def _areas(frames: list[dict]) -> dict[float, float]:
    return {round(f["time_hours"], 3): f["area_ha"] for f in frames}


def _c2_raster(path) -> tuple[str, float, float]:
    """A 60 x 60 all-C2 GeoTIFF (50 m, UTM 12N near Edmonton); returns (path, lat, lng) of its centre."""
    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import transform as warp

    west, north, cell, n = 350_000.0, 5_930_000.0, 50.0, 60
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612), transform=from_origin(west, north, cell, cell)) as dst:
        dst.write(np.full((n, n), 2, dtype=np.int32), 1)  # code 2 = C-2
    xs, ys = warp("EPSG:32612", "EPSG:4326", [west + n * cell / 2], [north - n * cell / 2])
    return str(path), ys[0], xs[0]


@pytest.mark.parametrize("grid", [False, True])
def test_burning_period_from_21h_waits_until_10h(tc, grid, tmp_path):
    payload = dict(BASE, duration_hours=15.0, snapshot_interval_minutes=60.0,
                   start_time="2026-07-15T21:00:00-06:00", burning_period=[10, 20])
    if grid:
        path, lat, lng = _c2_raster(tmp_path / "c2.tif")
        payload.update(fuel_grid_path=path, ignition_lat=lat, ignition_lng=lng,
                       fuel_modifiers={"grass_cure": 60.0})
    data = _wait(tc, tc.post("/api/v1/simulations", json=payload).json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    area = _areas(data["frames"])
    night = [area[float(h)] for h in range(0, 13)]  # 21:00 .. 09:00
    assert max(night) <= 0.5  # nothing beyond the ignition (cell)
    assert area[15.0] > night[-1] + 1.0  # spreads from 10:00
    # without a burning period the same fire grows from the start
    plain = dict(payload, duration_hours=2.0)
    plain.pop("burning_period")
    data = _wait(tc, tc.post("/api/v1/simulations", json=plain).json()["simulation_id"])
    assert _areas(data["frames"])[2.0] == pytest.approx(area[15.0], rel=0.05)


def test_clock_is_start_time_offset(tc):
    """The same instant in UTC (03:00Z = 21:00-06:00) is 03:00 on its own clock: the
    10-20 h period is then 7 h away, not 13 h."""
    payload = dict(BASE, duration_hours=8.0, snapshot_interval_minutes=60.0,
                   start_time="2026-07-16T03:00:00Z", burning_period=[10, 20])
    area = _areas(_wait(tc, tc.post("/api/v1/simulations", json=payload).json()["simulation_id"])["frames"])
    assert area[7.0] == area[0.0] and area[8.0] > area[7.0]


def test_spin_up_lowers_morning_spread(tc):
    """Spin-up from 17:00 through a humid night: the 06:00 run spreads less than one that
    starts from the afternoon FFMC."""
    night = [{"hours_from_start": float(h), "temperature": 8.0, "relative_humidity": 90.0,
              "wind_speed": 5.0, "wind_direction": 270.0} for h in range(-13, 0)]
    day = _hours(0, 3)
    areas = []
    for spin in (False, True):
        payload = dict(BASE, duration_hours=2.0, start_time="2026-07-15T06:00:00-06:00",
                       hourly_weather=night + day, ffmc_spin_up=spin)
        data = _wait(tc, tc.post("/api/v1/simulations", json=payload).json()["simulation_id"])
        assert data["status"] == "completed", data.get("error")
        areas.append(data["frames"][-1]["area_ha"])
    assert areas[1] < 0.8 * areas[0]


def test_multiday_burning_period(tc):
    payload = {
        "ignition_lat": 53.5, "ignition_lng": -113.5,
        "days": [{"wind_speed": 20.0, "wind_direction": 270.0, "temperature": 25.0,
                  "relative_humidity": 25.0}],
        "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
        "snapshot_interval_minutes": 60.0, "burning_period": [10, 20],
        "start_time": "2026-07-15T21:00:00-06:00",
    }
    resp = tc.post("/api/v1/simulations/multiday", json=payload)
    assert resp.status_code == 200, resp.text
    data = _wait(tc, resp.json()["simulation_id"])
    area = _areas(data["frames"])
    assert area[13.0] <= area[1.0] + 0.1 and area[23.0] > area[13.0] + 1.0
    no_clock = dict(payload)
    no_clock.pop("start_time")
    assert tc.post("/api/v1/simulations/multiday", json=no_clock).status_code == 422


def test_perimeter_override_burning_period_and_spin_up(tc):
    src_payload = dict(BASE, duration_hours=0.5, start_time="2026-07-15T13:00:00-06:00",
                       hourly_weather=_hours(0, 8))  # 13:00-20:00
    src = tc.post("/api/v1/simulations", json=src_payload).json()["simulation_id"]
    _wait(tc, src)
    square = {"type": "Polygon", "coordinates": [[[-113.501, 53.499], [-113.499, 53.499],
                                                  [-113.499, 53.501], [-113.501, 53.501],
                                                  [-113.501, 53.499]]]}
    # Observed at 21:00: nothing spreads in the next 2 h with a 10-20 h burning period
    resp = tc.post("/api/v1/simulations/perimeter-override", json={
        "simulation_id": src, "perimeter_geojson": square, "duration_hours": 2.0,
        "snapshot_interval_minutes": 60.0, "start_time": "2026-07-15T21:00:00-06:00",
        "burning_period": {"start_hour": 10, "end_hour": 20}})
    assert resp.status_code == 200, resp.text
    frames = _wait(tc, resp.json()["simulation_id"])["frames"]
    assert frames[-1]["area_ha"] == pytest.approx(frames[0]["area_ha"], rel=1e-6)
    # ... and spreads without it
    resp = tc.post("/api/v1/simulations/perimeter-override", json={
        "simulation_id": src, "perimeter_geojson": square, "duration_hours": 2.0,
        "snapshot_interval_minutes": 60.0, "start_time": "2026-07-15T21:00:00-06:00"})
    frames = _wait(tc, resp.json()["simulation_id"])["frames"]
    assert frames[-1]["area_ha"] > frames[0]["area_ha"] + 1.0
    # The source stream starts at 13:00, so a 14:00 restart cannot spin up from 17:00 the day before
    resp = tc.post("/api/v1/simulations/perimeter-override", json={
        "simulation_id": src, "perimeter_geojson": square,
        "start_time": "2026-07-15T14:00:00-06:00", "ffmc_spin_up": True})
    assert resp.status_code == 422 and "17:00" in resp.text
    # An 18:00 restart can (17:00 is in the re-based source stream)
    resp = tc.post("/api/v1/simulations/perimeter-override", json={
        "simulation_id": src, "perimeter_geojson": square, "duration_hours": 0.5,
        "start_time": "2026-07-15T18:00:00-06:00", "ffmc_spin_up": True})
    assert resp.status_code == 200, resp.text
    assert _wait(tc, resp.json()["simulation_id"])["status"] == "completed"
