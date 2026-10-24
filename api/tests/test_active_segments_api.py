"""Active-segment restart (backlog A1) through the API, and backward compatibility.

- Existing perimeter-override payloads are unchanged: same engine options, same frames as a
  payload that spells out the new fields at their defaults, and `active_segments` is null.
- RPAS hotspots / a FIRMS VIIRS CSV classify the boundary (east side active here), the run
  spreads only from it, and the response carries segments held / active, the hold and the
  modelled static share.
- `hold_hours: 0` is the explicit "all active" option (same frames as no classification).
- POST /observed-perimeter: standalone forecast from a perimeter at its observation time T
  (required), no source run; POST /classify-perimeter classifies without running.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from firesim_api.schemas.simulation import ObservedPerimeterRequest, PerimeterOverrideRequest
from firesim_api.services.runner import resolve_active_segments

from .test_restart_weather import _payload, _restart, _run, _wait, files, tc  # noqa: F401

T = "2011-05-14T18:50:00-06:00"


def _east_hotspots(files, when=T):
    m_lng = 1 / (111320.0 * np.cos(np.radians(files["lat"])))
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"time": when},
        "geometry": {"type": "Point", "coordinates": [files["lng"] + 120 * m_lng, files["lat"]]}}]}


def _cells(frame):
    return {(round(c["lat"], 7), round(c["lng"], 7)) for c in frame["burned_cells"] or []}


def test_old_payload_resolves_to_the_old_engine_options(files):
    old = {"simulation_id": "x", "perimeter_geojson": files["square"],
           "active_edges": {"type": "Point", "coordinates": [files["lng"], files["lat"]]},
           "active_edge_buffer_m": 60.0}
    req = PerimeterOverrideRequest.model_validate(old)
    kw, summary = resolve_active_segments(req, None, True)
    assert kw == {"active_edges": old["active_edges"], "active_edge_buffer_m": 60.0}
    assert summary is None
    kw, summary = resolve_active_segments(
        PerimeterOverrideRequest.model_validate({"simulation_id": "x",
                                                 "perimeter_geojson": files["square"]}),
        None, False)
    assert kw == {"active_edges": None, "active_edge_buffer_m": None} and summary is None


def test_existing_restart_unchanged_and_no_summary(tc, files):
    src = _run(tc, _payload(files))["simulation_id"]
    plain = _restart(tc, src, files)
    spelled = _restart(tc, src, files, hold_hours=None, hold_band_m=None, hotspots_geojson=None,
                       firms_csv=None)
    assert plain["active_segments"] is None and spelled["active_segments"] is None
    assert [f["area_ha"] for f in plain["frames"]] == [f["area_ha"] for f in spelled["frames"]]
    assert _cells(plain["frames"][-1]) == _cells(spelled["frames"][-1])


def test_hotspots_classify_and_hold(tc, files):
    src = _run(tc, _payload(files))["simulation_id"]
    whole = _restart(tc, src, files)
    held = _restart(tc, src, files, hotspots_geojson=_east_hotspots(files), hold_hours=24,
                    classification_radius_m=100, classification_segment_m=20)
    seg = held["active_segments"]
    assert seg["source"] == "rpas_hotspots" and seg["hold_hours"] == 24
    assert seg["n_active"] >= 1 and seg["n_held"] >= 1
    assert {f["properties"]["status"] for f in seg["segments"]["features"]} == {"active", "held"}
    assert 0.0 <= seg["static_share_model"] <= 1.0
    assert held["frames"][-1]["area_ha"] < whole["frames"][-1]["area_ha"]
    west = files["lng"] - 60 / (111320.0 * np.cos(np.radians(files["lat"])))
    assert min(c["lng"] for c in held["frames"][-1]["burned_cells"]) >= west - 1e-6
    # stale hotspots (13 h before T): nothing active, the perimeter holds
    old = (datetime.fromisoformat(T) - timedelta(hours=13)).isoformat()
    stale = _restart(tc, src, files, hotspots_geojson=_east_hotspots(files, old), hold_hours=24,
                     classification_segment_m=20)
    assert stale["active_segments"]["n_active"] == 0
    assert stale["frames"][-1]["area_ha"] == pytest.approx(stale["frames"][0]["area_ha"])


def test_hold_zero_is_all_active(tc, files):
    src = _run(tc, _payload(files))["simulation_id"]
    whole = _restart(tc, src, files)
    zero = _restart(tc, src, files, hotspots_geojson=_east_hotspots(files), hold_hours=0)
    assert zero["active_segments"]["source"] == "all_active"
    assert _cells(zero["frames"][-1]) == _cells(whole["frames"][-1])


def test_firms_csv_classification(tc, files):
    m_lng = 1 / (111320.0 * np.cos(np.radians(files["lat"])))
    csv = ("latitude,longitude,acq_date,acq_time,confidence\n"
           f"{files['lat']},{files['lng'] + 120 * m_lng},2011-05-15,0030,h\n"
           f"{files['lat']},{files['lng'] - 120 * m_lng},2011-05-15,0030,l\n")
    r = tc.post("/api/v1/simulations/classify-perimeter", json={
        "perimeter_geojson": files["square"], "observation_time": T, "firms_csv": csv,
        "classification_radius_m": 100, "classification_segment_m": 20})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["source"] == "viirs_firms" and body["summary"]["n_active"] >= 1
    assert body["active_edges"]["type"] == "MultiLineString"
    assert all(min(p[0] for p in line) > files["lng"] for line in body["active_edges"]["coordinates"])
    bad = tc.post("/api/v1/simulations/classify-perimeter", json={
        "perimeter_geojson": files["square"], "observation_time": T, "firms_csv": "a,b\n1,2\n"})
    assert bad.status_code == 422


def test_observed_perimeter_standalone(tc, files):
    body = {k: v for k, v in _payload(files).items()
            if k not in ("ignition_lat", "ignition_lng", "start_time")}
    body.update(perimeter_geojson=files["square"], observation_time=T,
                hotspots_geojson=_east_hotspots(files), hold_hours=24,
                classification_radius_m=100, classification_segment_m=20)
    r = tc.post("/api/v1/simulations/observed-perimeter", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["active_segments"]["source"] == "rpas_hotspots"
    data = _wait(tc, r.json()["simulation_id"])
    assert data["status"] == "completed", data.get("error")
    assert data["config"]["start_time"].startswith("2011-05-14T18:50:00")
    assert data["frames"][0]["time_hours"] == 0.0 and data["frames"][0]["area_ha"] > 0
    assert data["active_segments"]["static_share_model"] is not None
    # T is required, and start_time cannot disagree with it
    missing = {k: v for k, v in body.items() if k != "observation_time"}
    assert tc.post("/api/v1/simulations/observed-perimeter", json=missing).status_code == 422
    clash = dict(body, start_time="2011-05-14T12:00:00-06:00")
    assert tc.post("/api/v1/simulations/observed-perimeter", json=clash).status_code == 422
    naive = dict(body, observation_time="2011-05-14T18:50:00")
    assert tc.post("/api/v1/simulations/observed-perimeter", json=naive).status_code == 422


def test_observed_perimeter_needs_grid_for_segments():
    req = ObservedPerimeterRequest.model_validate({
        "perimeter_geojson": {"type": "Polygon", "coordinates": [[[-113.5, 53.5], [-113.49, 53.5],
                                                                  [-113.49, 53.51], [-113.5, 53.5]]]},
        "observation_time": T, "weather": {"wind_speed": 10, "wind_direction": 270},
        "hold_hours": 24})
    assert req.ignition_lat == pytest.approx(53.50333, abs=1e-4)
    with pytest.raises(ValueError, match="grid model"):
        resolve_active_segments(req, req.observation_time, False)


def test_openapi_keeps_old_fields(tc):
    schema = tc.get("/openapi.json").json()["components"]["schemas"]["PerimeterOverrideRequest"]
    props = schema["properties"]
    for k in ("simulation_id", "perimeter_geojson", "duration_hours", "active_edges",
              "active_edge_buffer_m", "start_time", "weather", "fwi_overrides"):
        assert k in props
    assert set(schema["required"]) == {"simulation_id", "perimeter_geojson"}
