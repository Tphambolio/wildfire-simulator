"""Run IDs (services/run_ids.py) and the not-found backoff (middleware/run_probe_limit.py).

New runs get 32 lowercase hex characters (128 bits from ``secrets``), so ``GET
/simulations/{id}`` can stay open without runs being guessable. IDs made before 2026-10-13 are
8 hex characters and stay valid for reading, the archive, labels and delete. Unknown IDs are
slowed per IP. The passphrase here is a test value.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect

from firesim_api.main import create_app
from firesim_api.middleware import run_probe_limit as probe
from firesim_api.routers import runs as runs_router
from firesim_api.routers import simulations as sims
from firesim_api.services import archive as arch
from firesim_api.services.archive import ArchiveConfig, build_archive
from firesim_api.services.run_ids import LEGACY_ID, NEW_ID, RUN_ID_BYTES, new_run_id

TEST_PASS = "test-only-passphrase"  # not a real secret
H = {"X-FireSim-Passphrase": TEST_PASS}
URL = "/api/v1/simulations"
LEGACY = "1a2b3c4d"
SINGLE = {"ignition_lat": 53.0, "ignition_lng": -114.0,
          "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.25,
          "snapshot_interval_minutes": 15.0}
_DAY = {"wind_speed": 5.0, "wind_direction": 270.0, "temperature": 20.0,
        "relative_humidity": 50.0, "precipitation_24h": 0.0}
MULTIDAY = {"ignition_lat": 53.0, "ignition_lng": -114.0, "days": [_DAY, _DAY],
            "fwi_overrides": {"ffmc": 85.0, "dmc": 20.0, "dc": 200.0}, "month": 7,
            "snapshot_interval_minutes": 120.0, "fuel_type": "C2"}


@pytest.fixture
def archive(tmp_path):
    a = build_archive(ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "archive.db")))
    arch.set_archive(a)
    yield a
    a.wait_idle()
    arch.set_archive(None)


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("FIRESIM_RUNS_PASSPHRASE", TEST_PASS)
    runs_router.reset_rate_limit()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    runs_router.reset_rate_limit()


async def _wait_done(client, sim_id: str, tries: int = 480) -> dict:
    for _ in range(tries):
        data = (await client.get(f"{URL}/{sim_id}")).json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.25)
    return data


def _seed(archive, sim_id: str) -> None:
    t = 1_780_000_000.0
    req = {"ignition_lat": 53.5, "ignition_lng": -113.5, "start_time": "2026-07-01T13:00:00-06:00"}
    archive.write(sim_id, {"kind": "single", "status": "completed", "created_at": t,
                           "finished_at": t + 30, "request_json": json.dumps(req)},
                  {"simulation_id": sim_id, "status": "completed", "frames": []},
                  arrival={"rows": 1, "cols": 1, "minutes": "AAA="})


# ── format ──────────────────────────────────────────────────────────────────


def test_new_ids_are_128_bit_lowercase_hex():
    ids = {new_run_id() for _ in range(2000)}
    assert len(ids) == 2000
    assert RUN_ID_BYTES * 8 >= 128
    for sid in ids:
        assert len(sid) == 32 and NEW_ID.match(sid) and not LEGACY_ID.match(sid)
        assert set(sid) <= set("0123456789abcdef")
        assert arch.valid_id(sid)  # the archive accepts it as a key prefix and index row
    assert LEGACY_ID.match(LEGACY) and arch.valid_id(LEGACY)


async def test_every_kind_of_run_gets_a_long_id(client, archive):
    single = (await client.post(URL, json=SINGLE)).json()["simulation_id"]
    resp = await client.post(f"{URL}/multiday", json=MULTIDAY)
    assert resp.status_code == 200, resp.text
    multi = resp.json()["simulation_id"]
    assert (await _wait_done(client, single))["status"] == "completed"
    square = {"type": "Polygon", "coordinates": [[[-114.001, 52.999], [-113.999, 52.999],
                                                  [-113.999, 53.001], [-114.001, 53.001],
                                                  [-114.001, 52.999]]]}
    resp = await client.post(f"{URL}/perimeter-override", json={
        "simulation_id": single, "perimeter_geojson": square, "duration_hours": 0.25})
    assert resp.status_code == 200, resp.text
    override = resp.json()["simulation_id"]
    for sid in (single, multi, override):
        assert NEW_ID.match(sid), sid
    assert len({single, multi, override}) == 3
    for sid in (multi, override):
        assert (await _wait_done(client, sid))["status"] == "completed"
    for sid in (single, multi, override):
        run = sims.runner.get(sid)
        if run is not None:
            run.done.wait(20)
    assert archive.wait_idle()
    # archive key layout: runs/<id>/<payload>, one index row per run
    for sid in (single, multi, override):
        assert archive.has(sid) and archive.index.get(sid) is not None
        assert (archive.backend.root / sid / "response.json.gz").is_file()


# ── backward compatibility: 8-hex IDs ───────────────────────────────────────


async def test_legacy_8_hex_run_reads_labels_and_deletes(client, archive):
    _seed(archive, LEGACY)
    r = await client.get(f"{URL}/{LEGACY}")
    assert r.status_code == 200 and r.json()["archived"] is True
    assert r.json()["simulation_id"] == LEGACY
    assert (await client.get(f"{URL}/{LEGACY}/arrival")).status_code == 200
    ens = await client.get(f"{URL}/{LEGACY}/ensemble")
    assert ens.status_code == 404 and ens.json()["detail"] == "No ensemble requested for this run"
    r = await client.patch(f"{URL}/{LEGACY}", json={"label": "Day 1 (old ID)"}, headers=H)
    assert r.status_code == 200, r.text
    listed = (await client.get(URL, headers=H)).json()["runs"]
    assert [(x["id"], x["label"]) for x in listed if x["id"] == LEGACY] == [(LEGACY, "Day 1 (old ID)")]
    assert (await client.delete(f"{URL}/{LEGACY}", headers=H)).status_code == 200
    assert (await client.get(f"{URL}/{LEGACY}")).status_code == 404


async def test_old_and_new_ids_restore_side_by_side_after_restart(client, archive, tmp_path):
    """An incident whose Day 1 run has an old ID and Day 2 a new one ("Load Day N run")."""
    new = (await client.post(URL, json=SINGLE)).json()["simulation_id"]
    assert (await _wait_done(client, new))["status"] == "completed"
    sims.runner.get(new).done.wait(20)
    _seed(archive, LEGACY)
    assert archive.wait_idle()
    sims.runner._runs.pop(new, None)  # restart: memory empty, archive reopened
    arch.set_archive(build_archive(ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "archive.db"))))
    for sid in (LEGACY, new):
        r = await client.get(f"{URL}/{sid}")
        assert r.status_code == 200 and r.json()["archived"] is True and r.json()["simulation_id"] == sid


# ── not-found backoff ───────────────────────────────────────────────────────


async def test_unknown_ids_back_off_per_ip(client, archive):
    _seed(archive, LEGACY)
    for i in range(probe.FREE_MISSES):
        r = await client.get(f"{URL}/{i:08x}")
        assert r.status_code == 404 and r.json()["detail"] == "Simulation not found"
    # the next miss starts the lock-out; then every run lookup from the IP waits, found or not
    assert (await client.get(f"{URL}/ffffffff/arrival")).status_code == 404
    for path in (LEGACY, "eeeeeeee", f"{LEGACY}/ensemble"):
        r = await client.get(f"{URL}/{path}")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert "unknown run IDs" in r.json()["detail"]
    # another IP, and other endpoints, are not affected
    r = await client.get(f"{URL}/{LEGACY}", headers={"Fly-Client-IP": "203.0.113.9"})
    assert r.status_code == 200
    assert (await client.get("/api/v1/health")).status_code == 200
    probe.reset()
    assert (await client.get(f"{URL}/{LEGACY}")).status_code == 200


async def test_found_runs_and_no_ensemble_never_count(client, archive):
    """Normal use (the app polling its runs, a run without an ensemble) never locks out."""
    _seed(archive, LEGACY)
    for _ in range(3 * probe.FREE_MISSES):
        assert (await client.get(f"{URL}/{LEGACY}")).status_code == 200
        assert (await client.get(f"{URL}/{LEGACY}/ensemble")).status_code == 404
    assert probe.locked_for("127.0.0.1") == 0
    assert probe._misses == {}


def test_found_runs_do_not_clear_the_count_and_it_is_forgotten():
    ip = "198.51.100.7"
    t0 = 1_000_000.0
    for i in range(probe.FREE_MISSES):
        probe.record_miss(ip, now=t0 + i)
    assert probe.locked_for(ip, now=t0 + 30) == 0
    assert probe.record_miss(ip, now=t0 + 30) == probe.FREE_MISSES + 1
    assert probe.locked_for(ip, now=t0 + 30) == pytest.approx(2.0)
    # the lock doubles with each further miss, capped
    probe.record_miss(ip, now=t0 + 40)
    assert probe.locked_for(ip, now=t0 + 40) == pytest.approx(4.0)
    for k in range(20):
        probe.record_miss(ip, now=t0 + 50 + k)
    assert probe.locked_for(ip, now=t0 + 69) == pytest.approx(probe.MAX_LOCK_S)
    # no miss for FORGET_S: the count starts again
    late = t0 + 69 + probe.MAX_LOCK_S + probe.FORGET_S + 1
    assert probe.record_miss(ip, now=late) == 1 and probe.locked_for(ip, now=late) == 0


def test_websocket_unknown_id_counts_and_locked_socket_is_refused():
    with TestClient(create_app()) as tc:
        for i in range(probe.FREE_MISSES + 1):
            with pytest.raises(WebSocketDisconnect) as exc:
                with tc.websocket_connect(f"{URL}/ws/{i:08x}") as ws:
                    ws.receive_json()
            assert exc.value.code == 4004
        with pytest.raises(WebSocketDisconnect) as exc:
            with tc.websocket_connect(f"{URL}/ws/{LEGACY}") as ws:
                ws.receive_json()
        assert exc.value.code == 4029


async def test_perimeter_override_from_unknown_source_counts(client):
    square = {"type": "Polygon", "coordinates": [[[-114.001, 52.999], [-113.999, 52.999],
                                                  [-113.999, 53.001], [-114.001, 53.001],
                                                  [-114.001, 52.999]]]}
    r = await client.post(f"{URL}/perimeter-override", json={
        "simulation_id": "0badbeef", "perimeter_geojson": square})
    assert r.status_code == 422 and "not found" in r.json()["detail"]
    assert probe._misses["127.0.0.1"][0] == 1
