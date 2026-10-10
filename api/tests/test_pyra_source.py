"""The "pyra" tier of /api/v1/weather/current: FireSim's starting codes equal Pyra's.

Parity: ``fixtures/pyra_parity.json`` holds recorded inputs (Pyra's ``cwfis_prev.json``, the CWFIS
current layer and the GEM payloads Pyra requests, captured 2026-10-10, plus marked synthetic
variants) and the results of **Pyra's own engine** on them (``scripts/pyra/run_pyra_parity.mjs``:
Pyra's ``initFWI`` in Node through Pyra's test harness, Pyra commit in ``fx["pyra"]``). Here the
FireSim endpoint gets the same inputs through respx and must give the same FFMC / DMC / DC / ISI /
BUI / FWI (within 1e-6, so equal at Pyra's 1-decimal display) for the same station.

With ``PYRA_DIR`` set (a Pyra checkout, read only) and node installed, the Pyra side is re-run
live instead of read from the fixture, so a change in Pyra shows up as a failure here.
Regenerate the fixture with ``scripts/pyra/build_parity_fixture.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from firesim_api.main import create_app
from firesim_api.routers import weather as weather_router
from firesim_api.services import pyra as pyra_source
from firesim_api.services.pyra_stations import ALBERTA_STATIONS, PYRA_COMMIT

pytestmark = pytest.mark.pyra

FIXTURE = Path(__file__).parent / "fixtures" / "pyra_parity.json"
FX = json.loads(FIXTURE.read_text())
ROOT = Path(__file__).resolve().parents[2]
PYRA_DIR = os.environ.get("PYRA_DIR")
LIVE_PYRA = bool(PYRA_DIR and Path(PYRA_DIR, "tests", "_harness.mjs").exists() and shutil.which("node"))
CODES = ("ffmc", "dmc", "dc", "isi", "bui", "fwi")
TOL = 1e-6


def _pyra_results() -> dict:
    """Pyra's results: re-run live when PYRA_DIR is set, else the recorded ones."""
    if not LIVE_PYRA:
        return FX["pyra"]
    out = subprocess.run(
        ["node", str(ROOT / "scripts" / "pyra" / "run_pyra_parity.mjs"), str(FIXTURE)],
        env={**os.environ, "PYRA_DIR": PYRA_DIR}, capture_output=True, text=True, check=True, timeout=300,
    )
    return json.loads(out.stdout)


PYRA = _pyra_results()


@pytest.fixture
def client():
    return TestClient(create_app())


def _fix_clock(monkeypatch, iso: str) -> None:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    monkeypatch.setattr(weather_router, "_now", lambda: t)


# ── Mocks serving the fixture's inputs (same rules as run_pyra_parity.mjs) ──────
def _features(case: dict) -> list[dict]:
    feats = json.loads(json.dumps(FX["feature_sets"].get(case["cwfis"], [])))
    tr = case.get("cwfis_transform")
    for f in feats:
        p = f["properties"]
        if tr == "rep_date_today" and p.get("rep_date"):
            p["rep_date"] = f"{FX['today']}T12:00:00Z"
        if tr == "strip_codes":
            for k in CODES:
                p[k] = None
    return feats


def _cwfis_handler(case: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if case["cwfis"] == "error":
            return httpx.Response(503)
        params = request.url.params
        if params.get("typeName") != "public:firewx_stns_current":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        cql = params.get("CQL_FILTER", "")
        parts = cql.replace("lat BETWEEN ", "").replace(" AND lon BETWEEN ", " AND ").split(" AND ")
        la0, la1, lo0, lo1 = (float(x) for x in parts[:4])
        feats = [f for f in _features(case)
                 if la0 <= float(f["properties"]["lat"]) <= la1 and lo0 <= float(f["properties"]["lon"]) <= lo1]
        feats = feats[: int(params.get("count", "1000000"))]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})
    return handler


def _prev_payload(case: dict) -> dict | None:
    if case["prev"] == "error":
        return None
    prev = json.loads(json.dumps(FX["prev_sets"][case["prev"]]))
    if case.get("prev_rep_date"):
        for v in prev["stations"].values():
            v["repDate"] = f"{case['prev_rep_date']}T12:00:00Z"
    return prev


def _gem_handler(case: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if p.get("past_days") != "1" or p.get("forecast_days") != "2":
            return httpx.Response(404)
        payload = FX["gem_sets"].get(case["gem"], {}).get(f"{p.get('latitude')},{p.get('longitude')}")
        return httpx.Response(200, json=payload) if payload else httpx.Response(404)
    return handler


def _mock_inputs(case: dict) -> None:
    prev = _prev_payload(case)
    respx.get(pyra_source.PREV_URLS[0]).mock(
        return_value=httpx.Response(200, json=prev) if prev else httpx.Response(500))
    respx.get(pyra_source.PREV_URLS[1]).mock(return_value=httpx.Response(404))
    respx.get(url__startswith=pyra_source.CWFIS_URL).mock(side_effect=_cwfis_handler(case))
    respx.get(url__startswith=pyra_source.OPEN_METEO_URL).mock(side_effect=_gem_handler(case))


# ── Parity with Pyra's engine ──────────────────────────────────────────────────
CASES = {c["id"]: c for c in FX["cases"]}


@pytest.mark.parametrize("case_id", list(CASES))
@respx.mock
def test_matches_pyra_station_page(case_id, client, monkeypatch):
    case = CASES[case_id]
    want = PYRA["cases"][case_id]
    _fix_clock(monkeypatch, case["now"])
    _mock_inputs(case)
    data = client.get(f"/api/v1/weather/current?lat={case['lat']}&lng={case['lng']}").json()
    if want["inactive"]:
        # Pyra shows no codes → FireSim falls back to its CWFIS chain and says why
        assert data["source_tier"] != "pyra"
        assert "Pyra chain not used" in data["message"]
        return
    assert data["source_tier"] == "pyra", data["message"]
    assert data["pyra"]["station_name"] == want["station"]["name"]
    for k in CODES:
        assert data[k] == pytest.approx(want[k], abs=TOL), k
        assert f"{data[k]:.1f}" == f"{want[k]:.1f}", k          # Pyra's display (toFixed(1))
    if want["peak"]:
        assert data["pyra"]["peak_wind_speed"] == pytest.approx(want["peak"]["wind"], abs=TOL)
        assert data["pyra"]["peak_isi"] == pytest.approx(want["peak"]["isi"], abs=TOL)
        assert data["pyra"]["peak_fwi"] == pytest.approx(want["peak"]["fwi"], abs=TOL)
    else:
        assert data["pyra"]["peak_isi"] is None
    if want["carry"]:
        assert data["pyra"]["chain_date"] == want["carry"]["obsDate"]
        assert data["pyra"]["chain_source"] == (
            "cwfis_live" if want["carry"]["src"] == "holding" else "pyra_cwfis_prev")
        if want["carry"]["stationName"]:
            assert data["pyra"]["chain_station_name"] == want["carry"]["stationName"]
    # Weather used for the day = Pyra's (noon LST) weather
    assert data["temperature"] == pytest.approx(want["weather"]["temp"], abs=TOL)
    assert data["relative_humidity"] == pytest.approx(want["weather"]["rh"], abs=TOL)
    assert data["wind_speed"] == pytest.approx(want["weather"]["wind"], abs=TOL)


@pytest.mark.parametrize("eq", FX["equations"], ids=lambda e: e["id"])
def test_daily_step_equations_match_pyra(eq):
    want = next(e for e in PYRA["equations"] if e["id"] == eq["id"])
    p = eq["prev"]
    r = pyra_source._step((p["ffmc"], p["dmc"], p["dc"]),
                          {"temperature": eq["temp"], "relative_humidity": eq["rh"],
                           "wind_speed": eq["wind"], "rain_24h": eq["rain"]}, eq["month"])
    for k in CODES:
        assert getattr(r, k) == pytest.approx(want[k], abs=TOL), k


def test_fixture_provenance():
    assert FX["pyra"]["pyra_commit"].startswith(PYRA_COMMIT[:7])
    assert FX["captured_at"].startswith("2026-10-10")


@pytest.mark.skipif(not LIVE_PYRA, reason="needs PYRA_DIR (a Pyra checkout) and node")
def test_station_list_matches_pyra_checkout():
    script = (
        "import { makeContext } from '" + Path(PYRA_DIR, "tests", "_harness.mjs").as_uri() + "';"
        "const { run } = makeContext('" + str(Path(PYRA_DIR, "fwi.js")) + "', { now: Date.now() });"
        "process.stdout.write(run('JSON.stringify(ALBERTA_STATIONS.map(s => [s.name, s.lat, s.lng]))'));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                         check=True, timeout=60)
    assert [tuple(s) for s in json.loads(out.stdout)] == list(ALBERTA_STATIONS)


# ── Behaviour of the tier ──────────────────────────────────────────────────────
BASE = CASES["prenoon_live_edmonton"]


@respx.mock
def test_labels_before_noon_as_pyra_does(client, monkeypatch):
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs(BASE)
    d = client.get(f"/api/v1/weather/current?lat={BASE['lat']}&lng={BASE['lng']}").json()
    assert d["source"].startswith("Pyra · Edmonton Blatchford · as of noon LST forecast 2026-10-10")
    assert d["codes_status"] == "forecast"
    assert d["codes_date"] == "2026-10-10"
    assert "Noon LST Forecast · today (pre-noon)" in d["codes_label"]
    assert "from the Oct 9 chain" in d["codes_label"]
    assert d["weather_model"] == "gem_seamless"
    assert d["pyra"]["step"] == "gem_noon_forecast"
    assert d["pyra"]["page_url"].endswith("station_detail/code.html?stn=Edmonton%20Blatchford")
    assert d["pyra"]["pyra_commit"] == PYRA_COMMIT
    assert d["pyra"]["carry_over_generated"] == FX["prev_sets"]["captured"]["generated"]


@respx.mock
def test_outside_alberta_uses_cwfis_chain(client, monkeypatch):
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs(BASE)
    d = client.get("/api/v1/weather/current?lat=52.13&lng=-106.67").json()   # Saskatoon
    assert d["source_tier"] != "pyra"
    assert "outside Pyra's Alberta coverage" in d["message"]


@respx.mock
def test_everything_down_falls_back(client, monkeypatch):
    case = {**BASE, "cwfis": "error", "prev": "error"}
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs(case)
    d = client.get(f"/api/v1/weather/current?lat={BASE['lat']}&lng={BASE['lng']}").json()
    assert d["source_tier"] != "pyra"
    assert "Pyra chain not used: no Pyra carry-over for Edmonton Blatchford" in d["message"]


@respx.mock
def test_gem_down_falls_back(client, monkeypatch):
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs({**BASE, "gem": "none"})          # Open-Meteo answers 404
    d = client.get(f"/api/v1/weather/current?lat={BASE['lat']}&lng={BASE['lng']}").json()
    assert d["source_tier"] != "pyra"
    assert "GEM forecast unavailable" in d["message"]


@respx.mock
def test_disabled_by_setting(client, monkeypatch):
    monkeypatch.setenv("FIRESIM_PYRA_SOURCE", "0")
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs(BASE)
    d = client.get(f"/api/v1/weather/current?lat={BASE['lat']}&lng={BASE['lng']}").json()
    assert d["source_tier"] != "pyra"
    assert "Pyra" not in d["message"]


@respx.mock
def test_carry_over_file_is_cached(client, monkeypatch):
    _fix_clock(monkeypatch, BASE["now"])
    _mock_inputs(BASE)
    route = respx.routes[0]
    for _ in range(3):
        assert client.get(f"/api/v1/weather/current?lat={BASE['lat']}&lng={BASE['lng']}").json()[
            "source_tier"] == "pyra"
    assert route.call_count == 1


@respx.mock
async def test_pages_mirror_used_when_raw_fails():
    now = datetime.fromisoformat(BASE["now"].replace("Z", "+00:00"))
    respx.get(pyra_source.PREV_URLS[0]).mock(return_value=httpx.Response(500))
    respx.get(pyra_source.PREV_URLS[1]).mock(return_value=httpx.Response(200, json=FX["prev_sets"]["captured"]))
    data, why = await pyra_source.load_prev(now)
    assert why is None and data["generated"] == FX["prev_sets"]["captured"]["generated"]
    assert pyra_source.prev_source_url() == pyra_source.PREV_URLS[1]


@respx.mock
async def test_last_good_copy_kept_when_refresh_fails(monkeypatch):
    now = datetime.fromisoformat(BASE["now"].replace("Z", "+00:00"))
    raw = respx.get(pyra_source.PREV_URLS[0]).mock(return_value=httpx.Response(200, json=FX["prev_sets"]["captured"]))
    respx.get(pyra_source.PREV_URLS[1]).mock(return_value=httpx.Response(500))
    assert (await pyra_source.load_prev(now))[0] is not None
    raw.mock(return_value=httpx.Response(500))
    monkeypatch.setattr(pyra_source, "PREV_TTL_S", 0)                 # force a refresh
    data, why = await pyra_source.load_prev(now)
    assert data is not None and why is None
    assert raw.call_count == 2


async def test_stale_file_rejected():
    now = datetime.fromisoformat("2026-10-13T15:00:00+00:00")
    pyra_source._cache.data = FX["prev_sets"]["captured"]
    pyra_source._cache.fetched_mono = __import__("time").monotonic()
    data, why = await pyra_source.load_prev(now)
    assert data is None and "stale" in why


def test_station_choice_is_pyras_nearest():
    (name, lat, lng), d = pyra_source.nearest_pyra_station(53.5461, -113.4938)
    assert name == "Edmonton Blatchford" and d < 3.0
    # Equal coordinates: the first in Pyra's sorted list wins, as in Pyra's selectNearest
    (name, _, _), _ = pyra_source.nearest_pyra_station(58.767, -111.117)
    assert name == "Fort Chipewyan"


def test_dc_floor_spring_only():
    may = datetime.fromisoformat("2026-05-12T16:00:00+00:00")
    oct_ = datetime.fromisoformat("2026-10-10T16:00:00+00:00")
    assert pyra_source.apply_dc_floor(15.0, 53.567, -113.517, may) == (300.0, True)
    assert pyra_source.apply_dc_floor(15.0, 53.567, -113.517, oct_) == (15.0, False)
    assert pyra_source.apply_dc_floor(120.0, 53.567, -113.517, may) == (120.0, False)


def test_gem_url_is_pyras():
    assert pyra_source.gem_url(53.567, -113.517) == (
        "https://api.open-meteo.com/v1/forecast?latitude=53.567&longitude=-113.517"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,"
        "thunderstorm_probability&past_days=1&forecast_days=2&timezone=UTC&models=gem_seamless")
    assert pyra_source.gem_url(56.65, -111.0) == (
        "https://api.open-meteo.com/v1/forecast?latitude=56.65&longitude=-111"
        "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,"
        "thunderstorm_probability&past_days=1&forecast_days=2&timezone=UTC&models=gem_seamless")
