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


# ---------------------------------------------------------------------------
# Fire weather path fixes (2026-10-10): negative temperatures, nearest station with codes,
# age of the codes, archive fallback, GEM noon-LST cold-start estimate (M6-M8, M3)
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from firesim.fwi.calculator import FWICalculator  # noqa: E402
from firesim_api.routers import weather as weather_router  # noqa: E402

from firesim_api.services import geomet  # noqa: E402


def _fix_clock(monkeypatch, iso: str) -> None:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    monkeypatch.setattr(weather_router, "_now", lambda: t)


def _by_layer(current: list[dict], archive: list[dict]):
    """respx side effect: current layer vs archive layer by typeName."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        layer = request.url.params.get("typeName")
        seen.append(layer)
        return _wfs_response(current if layer == "public:firewx_stns_current" else archive)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def _gem(monkeypatch, noon_iso: str | None, *, noon: tuple = (12.0, 40.0, 15.0, 200.0),
         rain_per_hour: float = 0.25, missing: str | None = None, uncovered_before: str | None = None):
    """Replace the GeoMet sampler with a synthetic GEM series; returns the list of requested windows.

    Every hour has T 5, RH 80, wind 5 from 90 and ``rain_per_hour``; the hour ``noon_iso`` has
    ``noon``. ``noon_iso=None`` = GeoMet unreachable. Hours before ``uncovered_before`` are null.
    """
    calls: list[tuple[datetime, datetime]] = []
    t_noon = datetime.fromisoformat(noon_iso.replace("Z", "+00:00")) if noon_iso else None
    cut = datetime.fromisoformat(uncovered_before.replace("Z", "+00:00")) if uncovered_before else None

    async def fake_hourly(lat, lng, start, end, client=None):
        calls.append((start, end))
        if t_noon is None:
            return None
        hours = []
        t = geomet.floor_hour(start)
        while t <= geomet.floor_hour(end):
            hours.append(t)
            t += geomet.HOUR
        h = {"time": [x.strftime("%Y-%m-%dT%H:00") for x in hours], "temperature_2m": [],
             "relative_humidity_2m": [], "wind_speed_10m": [], "wind_direction_10m": [],
             "precipitation": []}
        for x in hours:
            vals = noon if x == t_noon else (5.0, 80.0, 5.0, 90.0)
            if cut is not None and x < cut:
                vals, rain = (None, None, None, None), None
            else:
                rain = rain_per_hour
            for k, v in zip(("temperature_2m", "relative_humidity_2m", "wind_speed_10m",
                             "wind_direction_10m"), vals):
                h[k].append(v)
            h["precipitation"].append(rain)
        if missing:
            h[missing] = [None] * len(hours)
        h.update(src="h" * len(hours), model_runs={}, requests=0, failed=0)
        return h

    monkeypatch.setattr(geomet, "hourly", fake_hourly)
    return calls


class TestWeatherPath:
    @respx.mock
    def test_negative_temperature_kept(self, client):
        """M7: CWFIS temperatures below 0 C are returned, not dropped."""
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(temp=-7.5)]))
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["temperature"] == pytest.approx(-7.5)

    def test_float_rejects_negative_codes(self):
        assert weather_router._float(-3.0) is None
        assert weather_router._float(-3.0, allow_negative=True) == -3.0
        assert weather_router._float("nan") is None

    @respx.mock
    def test_nearest_station_with_codes_preferred(self, client):
        """M6: a nearer station without codes is skipped; the distance is the used station's."""
        near_no_codes = _station(name="NEAR NO CODES", lat=53.55, lon=-113.50, ffmc=None, dmc=None,
                                 dc=None, isi=None, bui=None, fwi=None)
        far_codes = _station(name="FAR WITH CODES", lat=53.90, lon=-113.50, fwi=12.0)
        respx.get(_WFS_URL).mock(return_value=_wfs_response([near_no_codes, far_codes]))
        data = client.get("/api/v1/weather/current?lat=53.546&lng=-113.494").json()
        assert data["station_name"] == "FAR WITH CODES"
        assert data["fwi"] == pytest.approx(12.0)
        assert data["distance_km"] == pytest.approx(39.4, abs=0.5)
        assert "1 nearer station without codes skipped" in data["message"]
        assert data["weather_model"] is None

    @respx.mock
    def test_codes_before_noon_lst_are_yesterdays(self, client, monkeypatch):
        """M8: at 10:00 LST the newest codes are yesterday's, and the label says so."""
        _fix_clock(monkeypatch, "2026-07-16T17:00:00Z")  # 10:00 fire-weather LST (UTC-7)
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(rep_date="2026-07-15T12:00:00Z")]))
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["codes_status"] == "yesterday"
        assert data["codes_date"] == "2026-07-15"
        assert "Yesterday's codes (as of noon LST 2026-07-15" in data["codes_label"]
        assert "after noon LST" in data["codes_label"]
        assert data["codes_label"] in data["message"]

    @respx.mock
    def test_codes_after_noon_not_yet_published(self, client, monkeypatch):
        _fix_clock(monkeypatch, "2026-07-16T20:30:00Z")  # 13:30 LST (UTC-7), layer not refreshed yet
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(rep_date="2026-07-15T12:00:00Z")]))
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["codes_status"] == "yesterday"
        assert "not yet published" in data["codes_label"]

    @respx.mock
    def test_todays_codes(self, client, monkeypatch):
        _fix_clock(monkeypatch, "2026-07-16T23:00:00Z")
        respx.get(_WFS_URL).mock(return_value=_wfs_response([_station(rep_date="2026-07-16T12:00:00Z")]))
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["codes_status"] == "today"
        assert data["codes_label"] == "Today's codes (noon LST 2026-07-16)"

    @respx.mock
    def test_bc_station_uses_pacific_standard_time(self, client, monkeypatch):
        """07:30 UTC is 23:30 PST the previous day: codes dated that day are today's."""
        _fix_clock(monkeypatch, "2026-07-16T07:30:00Z")
        respx.get(_WFS_URL).mock(return_value=_wfs_response([
            _station(prov="BC", lat=49.28, lon=-123.12, rep_date="2026-07-15T12:00:00Z")]))
        data = client.get("/api/v1/weather/current?lat=49.28&lng=-123.12").json()
        assert data["codes_status"] == "today"

    @respx.mock
    def test_empty_current_layer_falls_back_to_archive(self, client, monkeypatch):
        """M8: during the ~19 UTC refresh the current layer is empty; the archive's newest day is used."""
        _fix_clock(monkeypatch, "2026-07-16T19:10:00Z")
        archive = [
            _station(name="ARCHIVE STN", fwi=9.0, rep_date="2026-07-15T12:00:00Z"),
            _station(name="ARCHIVE STN", fwi=30.0, rep_date="2026-07-14T12:00:00Z"),
        ]
        handler = _by_layer([], archive)
        respx.get(_WFS_URL).mock(side_effect=handler)
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert handler.seen == ["public:firewx_stns_current", "public:firewx_stns"]
        assert data["available"] is True
        assert data["fwi"] == pytest.approx(9.0)  # newest report date only
        assert data["codes_date"] == "2026-07-15"
        assert "[archive layer]" in data["source"]
        assert "archive layer" in data["message"]

    @respx.mock
    def test_archive_query_limits_age(self, client, monkeypatch):
        _fix_clock(monkeypatch, "2026-07-16T19:10:00Z")
        route = respx.get(_WFS_URL).mock(side_effect=_by_layer([], []))
        _gem(monkeypatch, None)
        client.get("/api/v1/weather/current?lat=53.5&lng=-113.5")
        cql = route.calls[-1].request.url.params["CQL_FILTER"]
        assert "rep_date >= '2026-07-14T00:00:00Z'" in cql

    @respx.mock
    def test_no_cwfis_data_gives_labelled_gem_estimate(self, client, monkeypatch):
        """M6 + M3: no station at all -> GEM noon-LST weather + 24 h rain, cold start, labelled."""
        _fix_clock(monkeypatch, "2026-07-16T21:00:00Z")  # 14:00 LST (UTC-7): today's noon (19 UTC) has passed
        respx.get(_WFS_URL).mock(side_effect=_by_layer([], []))
        calls = _gem(monkeypatch, "2026-07-16T19:00:00Z")
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        # The 24 h to noon LST (19 UTC, Alberta box without a CWFIS station), nothing more
        assert calls[-1] == (datetime.fromisoformat("2026-07-15T20:00:00+00:00"),
                             datetime.fromisoformat("2026-07-16T19:00:00+00:00"))
        expected = FWICalculator(85.0, 6.0, 15.0).calculate_daily(
            temp=12.0, rh=40.0, wind=15.0, rain=24 * 0.25, month=7)
        assert data["available"] is True
        assert data["fwi"] == pytest.approx(expected.fwi)
        assert data["dc"] == pytest.approx(expected.dc)
        assert data["temperature"] == pytest.approx(12.0)  # noon value, not another hour
        assert data["codes_status"] == "estimate"
        assert data["codes_date"] == "2026-07-16"
        assert data["weather_model"] == "eccc_gem_geomet"
        assert "cold-start estimate" in data["source"].lower()
        assert "Cold-start estimate" in data["message"]
        assert "6.0 mm in 24 h" in data["codes_label"]
        assert data["station_name"] is None

    @respx.mock
    def test_estimate_before_noon_uses_yesterdays_noon(self, client, monkeypatch):
        _fix_clock(monkeypatch, "2026-07-16T15:00:00Z")  # 08:00 LST (UTC-7)
        respx.get(_WFS_URL).mock(side_effect=_by_layer([], []))
        _gem(monkeypatch, "2026-07-15T19:00:00Z")
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["codes_date"] == "2026-07-15"
        assert data["temperature"] == pytest.approx(12.0)

    @respx.mock
    def test_off_season_station_weather_with_gem_codes(self, client, monkeypatch):
        """Off-season: station weather kept (incl. sub-zero), codes from the GEM estimate."""
        _fix_clock(monkeypatch, "2026-11-20T21:00:00Z")
        off = _station(temp=-12.0, ffmc=None, dmc=None, dc=None, isi=None, bui=None, fwi=None,
                       rep_date="2026-11-20T12:00:00Z")
        respx.get(_WFS_URL).mock(return_value=_wfs_response([off]))
        _gem(monkeypatch, "2026-11-20T19:00:00Z")
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["temperature"] == pytest.approx(-12.0)
        assert data["codes_status"] == "estimate"
        assert data["station_name"] == "TEST STATION"
        assert "(weather) + ECCC GEM (GeoMet)" in data["source"]
        assert "off-season" in data["message"]

    @respx.mock
    def test_gem_missing_variable_is_reported(self, client, monkeypatch):
        """If GEM has no value for a needed variable the response says so (no silent fallback)."""
        _fix_clock(monkeypatch, "2026-07-16T21:00:00Z")
        respx.get(_WFS_URL).mock(side_effect=_by_layer([], []))
        _gem(monkeypatch, "2026-07-16T19:00:00Z", missing="relative_humidity_2m")
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["available"] is False
        assert "has no relative_humidity_2m" in data["message"]

    @respx.mock
    def test_rain_window_beyond_geomet_is_reported(self, client, monkeypatch):
        """Hours of the 24 h rain window older than the runs GeoMet keeps: said, not filled."""
        _fix_clock(monkeypatch, "2026-07-16T18:30:00Z")  # 11:30 LST: yesterday's noon, 23.5 h ago
        respx.get(_WFS_URL).mock(side_effect=_by_layer([], []))
        _gem(monkeypatch, "2026-07-15T19:00:00Z", uncovered_before="2026-07-15T00:00:00Z")
        data = client.get("/api/v1/weather/current?lat=53.5&lng=-113.5").json()
        assert data["available"] is False
        assert "has no precipitation for 4 of the 24 h to noon LST 2026-07-15" in data["message"]


class TestHourlyRoute:
    def test_hourly_window_and_shape(self, client, monkeypatch):
        calls = _gem(monkeypatch, "2026-07-16T19:00:00Z")
        r = client.get("/api/v1/weather/hourly?lat=53.5&lng=-113.5"
                       "&start=2026-07-16T17:30:00Z&end=2026-07-16T20:00:00-00:00")
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is True
        assert d["hourly"]["time"] == ["2026-07-16T17:00", "2026-07-16T18:00", "2026-07-16T19:00",
                                       "2026-07-16T20:00"]
        assert d["hourly"]["temperature_2m"][2] == 12.0
        assert d["weather_model"] == "eccc_gem_geomet"
        assert "Environment and Climate Change Canada" in d["attribution"]
        assert calls[0][0].isoformat() == "2026-07-16T17:30:00+00:00"

    def test_hourly_reports_uncovered_hours(self, client, monkeypatch):
        _gem(monkeypatch, "2026-07-16T19:00:00Z", uncovered_before="2026-07-16T18:00:00Z")
        d = client.get("/api/v1/weather/hourly?lat=53.5&lng=-113.5"
                       "&start=2026-07-16T16:00:00Z&end=2026-07-16T19:00:00Z").json()
        assert d["hourly"]["temperature_2m"][:2] == [None, None]
        assert "2 h not covered" in d["message"]

    def test_hourly_geomet_down(self, client, monkeypatch):
        _gem(monkeypatch, None)
        d = client.get("/api/v1/weather/hourly?lat=53.5&lng=-113.5"
                       "&start=2026-07-16T16:00:00Z&end=2026-07-16T19:00:00Z").json()
        assert d["available"] is False and d["hourly"] is None

    @pytest.mark.parametrize("q", [
        "start=2026-07-16T16:00:00&end=2026-07-16T19:00:00Z",        # no offset
        "start=2026-07-16T19:00:00Z&end=2026-07-16T16:00:00Z",       # reversed
        "start=2026-07-01T00:00:00Z&end=2026-07-16T19:00:00Z",       # too long
        "start=yesterday&end=2026-07-16T19:00:00Z",
    ])
    def test_hourly_rejects_bad_windows(self, client, q):
        assert client.get(f"/api/v1/weather/hourly?lat=53.5&lng=-113.5&{q}").status_code == 422
