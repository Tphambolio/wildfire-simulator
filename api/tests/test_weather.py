"""Tests for the /api/v1/weather/current endpoint.

Uses httpx/respx mock to avoid hitting the real CWFIS GeoServer in CI.
The endpoint now queries the CWFIS WFS layer public:firewx_stns_current
and selects the nearest station.
"""

from __future__ import annotations

import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from firesim_api.main import create_app

_WFS_URL = "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"


def _wfs_response(features: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={
        "type": "FeatureCollection",
        "features": features,
        "totalFeatures": len(features),
        "numberMatched": len(features),
        "numberReturned": len(features),
    })


def _station(
    name: str = "TEST STATION",
    prov: str = "AB",
    lat: float = 53.56,
    lon: float = -113.49,
    temp: float = 20.0,
    rh: float = 35.0,
    ws: float = 18.0,
    wdir: float = 270.0,
    ffmc: float | None = 88.5,
    dmc: float | None = 40.0,
    dc: float | None = 260.0,
    isi: float | None = 10.2,
    bui: float | None = 58.0,
    fwi: float | None = 15.5,
    rep_date: str = "2026-07-15T12:00:00Z",
) -> dict:
    return {
        "type": "Feature",
        "id": "firewx_stns_current.1",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "geometry_name": "the_geom",
        "properties": {
            "name": name,
            "prov": prov,
            "lat": lat,
            "lon": lon,
            "temp": temp,
            "rh": rh,
            "ws": ws,
            "wdir": wdir,
            "precip": 0,
            "ffmc": ffmc,
            "dmc": dmc,
            "dc": dc,
            "isi": isi,
            "bui": bui,
            "fwi": fwi,
            "dsr": None,
            "rep_date": rep_date,
        },
    }


@pytest.fixture
def client():
    return TestClient(create_app())


class TestWeatherEndpoint:
    """Contract tests for GET /api/v1/weather/current."""

    @respx.mock
    def test_returns_200_with_data(self, client):
        """When CWFIS WFS returns a station with FWI, endpoint returns available=True."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station()]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["ffmc"] == pytest.approx(88.5)
        assert data["dmc"] == pytest.approx(40.0)
        assert data["dc"] == pytest.approx(260.0)
        assert data["fwi"] == pytest.approx(15.5)

    @respx.mock
    def test_weather_fields_populated(self, client):
        """Wind speed, direction, temp, RH should all be returned."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station()]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        data = resp.json()
        assert data["wind_speed"] == pytest.approx(18.0)
        assert data["wind_direction"] == pytest.approx(270.0)
        assert data["temperature"] == pytest.approx(20.0)
        assert data["relative_humidity"] == pytest.approx(35.0)

    @respx.mock
    def test_station_name_and_distance_returned(self, client):
        """Nearest station name and distance_km should be in response."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([
            _station(name="EDMONTON BLATCHFORD", lat=53.567, lon=-113.517)
        ]))
        resp = client.get("/api/v1/weather/current?lat=53.546&lng=-113.494")
        data = resp.json()
        assert data["station_name"] == "EDMONTON BLATCHFORD"
        assert data["distance_km"] is not None
        assert data["distance_km"] < 10.0  # should be a few km

    @respx.mock
    def test_nearest_station_selected(self, client):
        """When multiple stations returned, the closest one is used."""
        far = _station(name="FAR STATION", lat=55.0, lon=-113.5, fwi=5.0)
        near = _station(name="NEAR STATION", lat=53.56, lon=-113.50, fwi=20.0)
        respx.get(_WFS_URL).mock(return_value=_wfs_response([far, near]))
        resp = client.get("/api/v1/weather/current?lat=53.546&lng=-113.494")
        data = resp.json()
        assert data["station_name"] == "NEAR STATION"
        assert data["fwi"] == pytest.approx(20.0)

    @respx.mock
    def test_plus_signs_stripped_from_station_name(self, client):
        """Station names with + URL-encoding artifacts should have spaces."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([
            _station(name="EDMONTON+BLATCHFORD")
        ]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        data = resp.json()
        assert "+" not in (data["station_name"] or "")
        assert data["station_name"] == "EDMONTON BLATCHFORD"

    @respx.mock
    def test_off_season_no_fwi_still_available(self, client):
        """Off-season: weather obs present but FWI codes null → available=True, message explains."""
        off_season = _station(ffmc=None, dmc=None, dc=None, isi=None, bui=None, fwi=None)
        respx.get(_WFS_URL).mock(return_value=_wfs_response([off_season]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["ffmc"] is None
        assert data["fwi"] is None
        assert "off-season" in data["message"].lower() or "unavailable" in data["message"].lower()

    @respx.mock
    def test_no_stations_returns_unavailable(self, client):
        """Empty feature collection → available=False."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False

    @respx.mock
    def test_cwfis_timeout_returns_unavailable(self, client):
        """Network timeout → available=False, not 500."""
        respx.get(_WFS_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert data["ffmc"] is None

    @respx.mock
    def test_cwfis_http_error_returns_unavailable(self, client):
        """Non-200 from CWFIS WFS → available=False, not 500."""
        respx.get(_WFS_URL).mock(return_value=httpx.Response(503))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False

    def test_missing_lat_returns_422(self, client):
        """Missing required lat param → 422 validation error."""
        resp = client.get("/api/v1/weather/current?lng=-113.5")
        assert resp.status_code == 422

    def test_invalid_lat_returns_422(self, client):
        """Out-of-range lat should be rejected."""
        resp = client.get("/api/v1/weather/current?lat=999&lng=-113.5")
        assert resp.status_code == 422

    @respx.mock
    def test_message_contains_fwi_label(self, client):
        """Available response message should include FWI value and danger label."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(fwi=15.5)]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        data = resp.json()
        assert "15.5" in data["message"] or "Moderate" in data["message"]

    @respx.mock
    def test_high_fwi_label(self, client):
        """FWI ≥ 19 → message includes 'High'."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(fwi=25.0)]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        data = resp.json()
        assert data["available"] is True
        assert "High" in data["message"]

    @respx.mock
    def test_coords_echoed_in_response(self, client):
        """Lat/lng should be echoed back in the response."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station()]))
        resp = client.get("/api/v1/weather/current?lat=51.2&lng=-114.8")
        data = resp.json()
        assert data["lat"] == pytest.approx(51.2)
        assert data["lng"] == pytest.approx(-114.8)

    @respx.mock
    def test_source_includes_station_name(self, client):
        """Source field should mention station name when available."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([
            _station(name="ELK ISLAND NAT PARK", prov="AB")
        ]))
        resp = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        data = resp.json()
        assert "ELK ISLAND NAT PARK" in data["source"]
        assert "AB" in data["source"]
