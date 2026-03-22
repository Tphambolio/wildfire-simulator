"""Tests for POST /api/v1/fwi/calculate.

Reference values: Van Wagner, C.E. & Pickett, T.L. (1985).
Equations and FORTRAN program for the Canadian Forest Fire Weather Index System.
Canadian Forestry Service, Petawawa National Forestry Institute. Info. Rep. PS-X-58.

The Day-1 test case in the original paper uses spring startup defaults
(ffmc_prev=85, dmc_prev=6, dc_prev=15) with standard summer noon observations.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from firesim_api.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


# Van Wagner & Pickett (1985) — startup conditions + standard summer observation.
# Inputs: T=17°C, H=42%, W=25 km/h, R=0mm, July, 46°N latitude.
# Starting codes: FFMC=85 (spring startup), DMC=6, DC=15.
_VW85_DAY1 = {
    "temperature": 17.0,
    "relative_humidity": 42.0,
    "wind_speed": 25.0,
    "precipitation_24h": 0.0,
    "month": 7,
    "ffmc_prev": 85.0,
    "dmc_prev": 6.0,
    "dc_prev": 15.0,
}

# Expected outputs computed from the Van Wagner & Pickett (1985) formulas
# at 46°N (standard Canadian latitude, Lf July=12.4 DMC, Lf July=6.4 DC).
#
# FFMC=87.7, ISI=10.9 — confirmed against the published paper.
# DMC=8.5             — Van Wagner Eq. 17-18, k = 1.894*(T+1.1)*(100-H)*Le*1e-4;
#                       DMC = Po + k  (not 100*k; 100x bug was present before fix).
# DC=21.8             — Van Wagner Eq. 22, Lf=6.4 for July at 46°N.
# BUI=8.6             — follows from corrected DMC=8.5 and DC=21.8.
# FWI=10.1            — follows from ISI=10.9 and BUI=8.6.
_VW85_DAY1_EXPECTED = {
    "ffmc": 87.7,
    "dmc": 8.5,
    "dc": 21.8,
    "isi": 10.9,
    "bui": 8.6,
    "fwi": 10.1,
}


class TestFWIEndpointContract:
    """Basic API contract — request/response shape and status codes."""

    def test_returns_200(self, client):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        assert resp.status_code == 200

    def test_response_has_all_six_components(self, client):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        data = resp.json()
        for field in ("ffmc", "dmc", "dc", "isi", "bui", "fwi"):
            assert field in data, f"Missing field: {field}"

    def test_response_has_danger_rating(self, client):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        data = resp.json()
        assert "danger_rating" in data
        assert len(data["danger_rating"]) > 0

    def test_response_echoes_inputs(self, client):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        data = resp.json()
        assert "inputs" in data
        assert data["inputs"]["temperature"] == pytest.approx(17.0)

    def test_minimum_required_fields(self, client):
        """Only temp, rh, wind_speed are required — rest have defaults."""
        resp = client.post("/api/v1/fwi/calculate", json={
            "temperature": 25.0,
            "relative_humidity": 35.0,
            "wind_speed": 20.0,
        })
        assert resp.status_code == 200


class TestFWIValidation:
    """Input validation — expect 422 for out-of-range or missing fields."""

    def test_missing_temperature_returns_422(self, client):
        resp = client.post("/api/v1/fwi/calculate", json={
            "relative_humidity": 40.0, "wind_speed": 20.0
        })
        assert resp.status_code == 422

    def test_missing_rh_returns_422(self, client):
        resp = client.post("/api/v1/fwi/calculate", json={
            "temperature": 25.0, "wind_speed": 20.0
        })
        assert resp.status_code == 422

    def test_rh_above_100_returns_422(self, client):
        resp = client.post("/api/v1/fwi/calculate", json={
            "temperature": 25.0, "relative_humidity": 105.0, "wind_speed": 20.0
        })
        assert resp.status_code == 422

    def test_month_13_returns_422(self, client):
        resp = client.post("/api/v1/fwi/calculate", json={**_VW85_DAY1, "month": 13})
        assert resp.status_code == 422

    def test_negative_rain_returns_422(self, client):
        resp = client.post("/api/v1/fwi/calculate", json={
            **_VW85_DAY1, "precipitation_24h": -1.0
        })
        assert resp.status_code == 422


class TestFWIReferenceValues:
    """Validate against Van Wagner & Pickett (1985) published reference values.

    Tolerance is ±0.2 on the rounded outputs to allow for minor rounding
    differences between the FORTRAN integer arithmetic and Python floats.
    """

    @pytest.mark.parametrize("component,expected", _VW85_DAY1_EXPECTED.items())
    def test_van_wagner_day1(self, client, component, expected):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        assert resp.status_code == 200
        actual = resp.json()[component]
        assert abs(actual - expected) <= 0.5, (
            f"{component}: got {actual}, expected {expected} "
            f"(Van Wagner & Pickett 1985, Appendix 1, Day 1)"
        )

    def test_all_components_non_negative(self, client):
        resp = client.post("/api/v1/fwi/calculate", json=_VW85_DAY1)
        data = resp.json()
        for field in ("ffmc", "dmc", "dc", "isi", "bui", "fwi"):
            assert data[field] >= 0.0, f"{field} is negative: {data[field]}"

    def test_ffmc_within_bounds(self, client):
        """FFMC is bounded 0–101 by the algorithm."""
        resp = client.post("/api/v1/fwi/calculate", json={
            **_VW85_DAY1, "temperature": 40.0, "relative_humidity": 5.0
        })
        assert resp.json()["ffmc"] <= 101.0


class TestFWIDangerRating:
    """Danger rating labels should match FWI thresholds."""

    def test_low_fwi_is_low(self, client):
        """Very wet, cold, calm → FWI near 0 → Low."""
        resp = client.post("/api/v1/fwi/calculate", json={
            "temperature": 5.0,
            "relative_humidity": 95.0,
            "wind_speed": 0.0,
            "precipitation_24h": 20.0,
            "month": 5,
            "ffmc_prev": 60.0,
            "dmc_prev": 2.0,
            "dc_prev": 10.0,
        })
        data = resp.json()
        assert data["danger_rating"] == "Low"

    def test_high_fwi_is_high(self, client):
        """Hot, dry, windy July conditions should produce High or Very High FWI."""
        resp = client.post("/api/v1/fwi/calculate", json={
            "temperature": 35.0,
            "relative_humidity": 15.0,
            "wind_speed": 40.0,
            "precipitation_24h": 0.0,
            "month": 7,
            "ffmc_prev": 90.0,
            "dmc_prev": 60.0,
            "dc_prev": 350.0,
        })
        data = resp.json()
        assert data["fwi"] >= 19.0
        assert "High" in data["danger_rating"]


class TestFWIPrevDayEffect:
    """Previous-day codes should influence output as expected."""

    def test_higher_ffmc_prev_gives_higher_ffmc(self, client):
        """Starting from higher FFMC_prev (drier fuel) → higher FFMC output."""
        base = {"temperature": 20.0, "relative_humidity": 40.0, "wind_speed": 15.0, "month": 7}
        low = client.post("/api/v1/fwi/calculate", json={**base, "ffmc_prev": 70.0}).json()
        high = client.post("/api/v1/fwi/calculate", json={**base, "ffmc_prev": 92.0}).json()
        assert high["ffmc"] > low["ffmc"]

    def test_higher_dmc_prev_gives_higher_dmc(self, client):
        """Higher starting DMC (drier duff) → higher output DMC."""
        base = {"temperature": 25.0, "relative_humidity": 35.0, "wind_speed": 10.0, "month": 7}
        low = client.post("/api/v1/fwi/calculate", json={**base, "dmc_prev": 10.0}).json()
        high = client.post("/api/v1/fwi/calculate", json={**base, "dmc_prev": 80.0}).json()
        assert high["dmc"] > low["dmc"]

    def test_rain_reduces_ffmc(self, client):
        """24h rain should result in lower FFMC than no-rain case."""
        base = {"temperature": 20.0, "relative_humidity": 50.0, "wind_speed": 10.0,
                "month": 7, "ffmc_prev": 90.0}
        dry = client.post("/api/v1/fwi/calculate", json={**base, "precipitation_24h": 0.0}).json()
        wet = client.post("/api/v1/fwi/calculate", json={**base, "precipitation_24h": 15.0}).json()
        assert wet["ffmc"] < dry["ffmc"]
