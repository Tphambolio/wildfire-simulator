"""Opt-in M8 spot-fire likelihood through the API (v8 ember model, [R70]).

- Off by default: restarts without ``ember_m8`` carry ``ember_m8: null`` and archive the same request.
- Offered only with an observed active edge: a plain ignition run, or a restart without active edges /
  hotspots / VIIRS, is rejected (422 "needs an observed active edge").
- With an active edge: relative tiers on 375 m cells 1-15 km beyond the perimeter, downwind of the
  active edge; no spot counts, probabilities or raw scores; the approved sentence and caveat.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from starlette.testclient import TestClient

from firesim.spread.embers_m8 import APPROVED_SENTENCE
from firesim_api.main import create_app
from firesim_api.schemas.simulation import PerimeterOverrideRequest
from firesim_api.services.runner import _request_record

from .test_restart_weather import _box, _wait

T = "2011-05-14T18:50:00-06:00"


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """A 36 km x 36 km C-2 grid (100 m) so the 1-15 km band is mapped."""
    d = tmp_path_factory.mktemp("m8")
    fuel = d / "fuel.tif"
    n = 360
    with rasterio.open(fuel, "w", driver="GTiff", height=n, width=n, count=1, dtype="int32",
                       crs=CRS.from_epsg(32612),
                       transform=from_origin(340_000.0, 5_940_000.0, 100.0, 100.0)) as dst:
        dst.write(np.full((n, n), 2, dtype=np.int32), 1)
    with rasterio.open(fuel) as src:
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    lat, lng = (south + north) / 2, (west + east) / 2
    m_lat, m_lng = 1 / 111320.0, 1 / (111320.0 * np.cos(np.radians(lat)))
    nbhd = d / "nbhd.geojson"
    nbhd.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"neighbourhood": "TEST"},
        "geometry": {"type": "Polygon", "coordinates": [_box(west, south, east, north)]}}]}))
    h = 500
    square = {"type": "Polygon", "coordinates": [_box(lng - h * m_lng, lat - h * m_lat,
                                                      lng + h * m_lng, lat + h * m_lat)]}
    east_edge = {"type": "LineString", "coordinates": [[lng + h * m_lng, lat - h * m_lat],
                                                        [lng + h * m_lng, lat + h * m_lat]]}
    return {"fuel": str(fuel), "nbhd": str(nbhd), "lat": lat, "lng": lng, "square": square,
            "east_edge": east_edge, "m_lng": m_lng}


@pytest.fixture
def tc(big, monkeypatch):
    monkeypatch.setenv("FIRESIM_NEIGHBOURHOODS_PATH", big["nbhd"])
    monkeypatch.setenv("FIRESIM_ARCHIVE", "0")
    with TestClient(create_app()) as client:
        yield client


def _source(tc, big, **extra):
    p = {"ignition_lat": big["lat"], "ignition_lng": big["lng"],
         "weather": {"wind_speed": 25.0, "wind_direction": 270.0, "temperature": 22.0,
                     "relative_humidity": 20.0, "precipitation_24h": 0.0},
         "fwi_overrides": {"ffmc": 91.0, "dmc": 40.0, "dc": 300.0},
         "duration_hours": 0.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
         "fuel_grid_path": big["fuel"], "start_time": "2011-05-14T17:50:00-06:00", **extra}
    r = tc.post("/api/v1/simulations", json=p)
    return r


def _restart(tc, big, src_id, **extra):
    body = {"simulation_id": src_id, "perimeter_geojson": big["square"], "duration_hours": 0.5,
            "snapshot_interval_minutes": 30.0, "start_time": T, **extra}
    return tc.post("/api/v1/simulations/perimeter-override", json=body)


def _src_id(tc, big):
    r = _source(tc, big)
    assert r.status_code == 200, r.text
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    return data["simulation_id"]


def test_plain_ignition_run_rejects_ember_m8(tc, big):
    r = _source(tc, big, ember_m8=True)
    assert r.status_code == 422
    assert "needs an observed active edge" in r.text


def test_off_by_default_and_same_archived_request(tc, big):
    src = _src_id(tc, big)
    r = _restart(tc, big, src)
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed" and data["ember_m8"] is None
    rec = _request_record(PerimeterOverrideRequest.model_validate(
        {"simulation_id": "x", "perimeter_geojson": big["square"]}))
    assert not {"ember_m8", "ember_m8_previous_perimeter_geojson", "ember_m8_previous_time"} & set(rec)


def test_restart_without_active_edge_rejects_ember_m8(tc, big):
    src = _src_id(tc, big)
    r = _restart(tc, big, src, ember_m8=True)
    assert r.status_code == 422 and "needs an observed active edge" in r.text


def test_restart_with_active_edge_gives_relative_tiers_downwind(tc, big):
    src = _src_id(tc, big)
    r = _restart(tc, big, src, ember_m8=True, active_edges=big["east_edge"])
    assert r.status_code == 200, r.text
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    m8 = data["ember_m8"]
    assert "error" not in m8, m8
    assert m8["description"] == APPROVED_SENTENCE and "not calibrated" in m8["caveat"]
    assert m8["display"] == "relative tiers only"
    assert m8["n_by_tier"]["higher"] > 0 and m8["active_source_cells"] > 0
    feats = m8["tiers"]["features"]
    assert {f["properties"]["tier"] for f in feats} <= {"higher", "middle"}
    hi = [f["geometry"]["coordinates"][0][0][0] for f in feats if f["properties"]["tier"] == "higher"]
    assert np.mean(np.array(hi) > big["lng"]) > 0.99  # wind from the west
    flat = json.dumps(m8).lower()
    for banned in ("score", "probab", "expected_", "lambda"):
        assert banned not in flat.replace("no counts or probabilities", ""), banned
    rec = data["frames"] and _request_record(PerimeterOverrideRequest.model_validate(
        {"simulation_id": "x", "perimeter_geojson": big["square"], "ember_m8": True,
         "active_edges": big["east_edge"]}))
    assert rec["ember_m8"] is True


def test_previous_perimeter_needs_time_and_order(tc, big):
    src = _src_id(tc, big)
    r = _restart(tc, big, src, ember_m8=True, active_edges=big["east_edge"],
                 ember_m8_previous_perimeter_geojson=big["square"])
    assert r.status_code == 422
    r = _restart(tc, big, src, ember_m8=True, active_edges=big["east_edge"],
                 ember_m8_previous_perimeter_geojson=big["square"],
                 ember_m8_previous_time="2011-05-14T19:50:00-06:00")
    assert r.status_code == 422 and "before the observation time" in r.text


def test_observed_perimeter_run_with_hotspots(tc, big):
    hot = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"time": T},
        "geometry": {"type": "Point", "coordinates": [big["lng"] + 520 * big["m_lng"], big["lat"]]}}]}
    body = {"perimeter_geojson": big["square"], "observation_time": T,
            "weather": {"wind_speed": 25.0, "wind_direction": 270.0, "temperature": 22.0,
                        "relative_humidity": 20.0, "precipitation_24h": 0.0},
            "fwi_overrides": {"ffmc": 91.0, "dmc": 40.0, "dc": 300.0},
            "duration_hours": 0.5, "snapshot_interval_minutes": 30.0, "fuel_type": "C2",
            "fuel_grid_path": big["fuel"], "hotspots_geojson": hot, "ember_m8": True}
    r = tc.post("/api/v1/simulations/observed-perimeter", json=body)
    assert r.status_code == 200, r.text
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    assert data["ember_m8"]["n_by_tier"]["higher"] > 0
    body.pop("hotspots_geojson")
    r = tc.post("/api/v1/simulations/observed-perimeter", json=body)
    assert r.status_code == 422 and "needs an observed active edge" in r.text
