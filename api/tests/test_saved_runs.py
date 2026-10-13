"""Saved runs (run archive phase 2, routers/runs.py): the passphrase-gated list of runs.

GET /api/v1/simulations lists archived runs plus in-memory runs not archived yet, newest
first, paginated and filtered by time, box around the ignition and kind; PATCH sets a label;
DELETE removes a run. All need X-FireSim-Passphrase matching FIRESIM_RUNS_PASSPHRASE; unset
→ 403; failed attempts are rate-limited per IP. The passphrase here is a test value.
"""

from __future__ import annotations

import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from firesim_api.main import create_app
from firesim_api.routers import runs as runs_router
from firesim_api.routers import simulations as sims
from firesim_api.services import archive as arch
from firesim_api.services.archive import ArchiveConfig, RunIndex, build_archive

TEST_PASS = "test-only-passphrase"  # not a real secret
H = {"X-FireSim-Passphrase": TEST_PASS}
URL = "/api/v1/simulations"


def _meta(i: int, *, lat=53.5, lng=-113.5, kind="single", created=None, label=None,
          ensemble=None, start="2026-07-01T13:00:00-06:00") -> dict:
    req: dict = {"ignition_lat": lat + 0.000123, "ignition_lng": lng - 0.000456,
                 "start_time": start, "label": label}
    if ensemble:
        req["ensemble"] = {"n_members": ensemble}
    if kind == "perimeter_override":
        req = {"source_config": req, "perimeter_override": {"label": None, "start_time": None}}
    t = created if created is not None else 1_780_000_000.0 + i * 60
    return {"kind": kind, "status": "completed", "created_at": t, "finished_at": t + 30,
            "request_json": json.dumps(req), "git_sha": "abc", "fuel_source_kind": "city",
            "fuel_source_label": "City of Edmonton fuel grid", "buildings_source_kind": "ab",
            "buildings_source_label": "Microsoft footprints", "area_ha": 10.0 + i,
            "duration_hours": 4.0, "frame_count": 9, "days": None,
            "ensemble_status": "completed" if ensemble else None}


@pytest.fixture
def archive(tmp_path):
    a = build_archive(ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "archive.db")))
    arch.set_archive(a)
    yield a
    a.wait_idle()
    arch.set_archive(None)


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    monkeypatch.setenv("FIRESIM_RUNS_PASSPHRASE", TEST_PASS)
    runs_router.reset_rate_limit()
    # runs other tests left in the shared runner's memory are not this test's
    before = set(sims.runner._runs) if sims.runner is not None else set()
    real = runs_router._memory_runs
    monkeypatch.setattr(runs_router, "_memory_runs",
                        lambda: [r for r in real() if r.id not in before])
    yield
    runs_router.reset_rate_limit()


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _seed(archive, n: int = 5, **kw) -> list[str]:
    ids = []
    for i in range(n):
        sid = f"run{i:04d}"
        archive.write(sid, _meta(i, **kw), {"simulation_id": sid, "status": "completed", "frames": []})
        ids.append(sid)
    return ids


# ── gate ──────────────────────────────────────────────────────────────────────


async def test_unset_passphrase_disables_list(client, archive, monkeypatch):
    monkeypatch.delenv("FIRESIM_RUNS_PASSPHRASE", raising=False)
    r = await client.get(URL, headers=H)
    assert r.status_code == 403
    assert "FIRESIM_RUNS_PASSPHRASE" in r.json()["detail"]
    assert (await client.patch(f"{URL}/x1", json={"label": "a"}, headers=H)).status_code == 403
    assert (await client.delete(f"{URL}/x1", headers=H)).status_code == 403


async def test_missing_and_wrong_passphrase(client, archive):
    _seed(archive, 1)
    r = await client.get(URL)
    assert r.status_code == 401 and r.json()["detail"] == "Passphrase required"
    r = await client.get(URL, headers={"X-FireSim-Passphrase": "nope"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong passphrase"
    r = await client.get(URL, headers=H)
    assert r.status_code == 200 and r.json()["total"] == 1


async def test_passphrase_never_logged(client, archive, caplog):
    caplog.set_level("DEBUG")
    await client.get(URL, headers={"X-FireSim-Passphrase": "guess-123"})
    await client.get(URL, headers=H)
    assert "guess-123" not in caplog.text and TEST_PASS not in caplog.text


async def test_rate_limit_after_failures(client, archive):
    bad = {"X-FireSim-Passphrase": "wrong"}
    for _ in range(runs_router._FREE_FAILURES):
        assert (await client.get(URL, headers=bad)).status_code == 401
    # the next failure starts the lock-out; then even the right passphrase waits
    assert (await client.get(URL, headers=bad)).status_code == 401
    r = await client.get(URL, headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # another IP is not affected
    r = await client.get(URL, headers={**H, "Fly-Client-IP": "203.0.113.9"})
    assert r.status_code == 200
    runs_router.reset_rate_limit()
    assert (await client.get(URL, headers=H)).status_code == 200


async def test_success_clears_failures(client, archive):
    bad = {"X-FireSim-Passphrase": "wrong"}
    for _ in range(runs_router._FREE_FAILURES):
        await client.get(URL, headers=bad)
    assert (await client.get(URL, headers=H)).status_code == 200
    assert (await client.get(URL, headers=bad)).status_code == 401  # count restarted, no lock
    assert (await client.get(URL, headers=H)).status_code == 200


async def test_get_by_id_stays_open(client, archive):
    _seed(archive, 1)
    r = await client.get(f"{URL}/run0000")
    assert r.status_code == 200 and r.json()["archived"] is True


# ── list ──────────────────────────────────────────────────────────────────────


async def test_list_fields_newest_first(client, archive):
    _seed(archive, 3, label="Ridge  test\u0007", ensemble=30)
    body = (await client.get(URL, headers=H)).json()
    assert body["total"] == 3 and body["archive_enabled"] is True
    assert [r["id"] for r in body["runs"]] == ["run0002", "run0001", "run0000"]
    r = body["runs"][0]
    assert r["ignition_lat"] == 53.5 and r["ignition_lng"] == -113.5  # rounded to 3 decimals
    assert r["label"] == "Ridge test"  # plain text: control character dropped, spaces collapsed
    assert r["kind"] == "single" and r["status"] == "completed" and r["archived"] is True
    assert r["start_time"] == "2026-07-01T13:00:00-06:00"
    assert r["created_at"].endswith("Z") and r["finished_at"].endswith("Z")
    assert r["area_ha"] == 12.0 and r["duration_hours"] == 4.0 and r["frame_count"] == 9
    assert r["fuel_source"] == "City of Edmonton fuel grid"
    assert r["buildings_source"] == "Microsoft footprints"
    assert r["ensemble"] is True and r["ensemble_members"] == 30
    # no address, no request body, no per-building data
    assert "request_json" not in r and "address" not in json.dumps(body).lower()


async def test_pagination(client, archive):
    _seed(archive, 7)
    p1 = (await client.get(URL, params={"limit": 3}, headers=H)).json()
    p3 = (await client.get(URL, params={"limit": 3, "offset": 6}, headers=H)).json()
    assert p1["total"] == p3["total"] == 7
    assert [r["id"] for r in p1["runs"]] == ["run0006", "run0005", "run0004"]
    assert [r["id"] for r in p3["runs"]] == ["run0000"]
    assert (await client.get(URL, params={"limit": 0}, headers=H)).status_code == 422
    assert (await client.get(URL, params={"limit": 500}, headers=H)).status_code == 422


async def test_filters(client, archive):
    archive.write("near1", _meta(1, lat=53.5, lng=-113.5, created=1_780_000_000.0), {"frames": []})
    archive.write("far01", _meta(2, lat=51.0, lng=-114.0, created=1_780_086_400.0), {"frames": []})
    archive.write("ovr01", _meta(3, lat=53.52, lng=-113.48, kind="perimeter_override",
                                 created=1_780_172_800.0), {"frames": []})

    async def ids(**params):
        r = await client.get(URL, params=params, headers=H)
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["runs"]]

    box = {"south": 53.4, "west": -113.7, "north": 53.7, "east": -113.3}
    assert await ids(**box) == ["ovr01", "near1"]
    assert await ids(kind="perimeter_override") == ["ovr01"]
    assert await ids(since="2026-05-29T00:00:00Z") == ["ovr01", "far01"]
    assert await ids(until="2026-05-29T00:00:00Z") == ["near1"]
    assert await ids(since="2026-05-29T00:00:00Z", **box) == ["ovr01"]
    # the override's ignition comes from its source run
    r = (await client.get(URL, params={"kind": "perimeter_override"}, headers=H)).json()["runs"][0]
    assert r["ignition_lat"] == 53.52
    assert (await client.get(URL, params={"south": 53.4}, headers=H)).status_code == 422
    assert (await client.get(URL, params={"kind": "bogus"}, headers=H)).status_code == 422
    assert (await client.get(URL, params={"since": "yesterday"}, headers=H)).status_code == 422


async def test_in_memory_run_listed_and_not_duplicated(client, archive):
    body = {"ignition_lat": 53.0, "ignition_lng": -114.0, "label": "Live run",
            "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.5}
    sim_id = (await client.post(URL, json=body)).json()["simulation_id"]
    listed = (await client.get(URL, headers=H)).json()["runs"]
    mine = [r for r in listed if r["id"] == sim_id]
    assert len(mine) == 1 and mine[0]["label"] == "Live run"
    assert mine[0]["ignition_lat"] == 53.0
    run = sims.runner.get(sim_id)
    run.done.wait(60)
    archive.wait_idle()
    listed = (await client.get(URL, headers=H)).json()["runs"]
    mine = [r for r in listed if r["id"] == sim_id]
    assert len(mine) == 1 and mine[0]["archived"] is True and mine[0]["label"] == "Live run"
    assert mine[0]["status"] == "completed" and mine[0]["frame_count"] >= 1


async def test_label_over_80_rejected_at_run_time(client):
    body = {"ignition_lat": 53.0, "ignition_lng": -114.0, "label": "x" * 81,
            "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.5}
    assert (await client.post(URL, json=body)).status_code == 422


# ── label PATCH ───────────────────────────────────────────────────────────────


async def test_label_patch(client, archive):
    _seed(archive, 2, label="Original")
    url = f"{URL}/run0001"
    assert (await client.patch(url, json={"label": "New"})).status_code == 401
    r = await client.patch(url, json={"label": "  Day 2 \n west flank  "}, headers=H)
    assert r.status_code == 200 and r.json() == {"id": "run0001", "label": "Day 2 west flank"}
    runs = {x["id"]: x for x in (await client.get(URL, headers=H)).json()["runs"]}
    assert runs["run0001"]["label"] == "Day 2 west flank"
    assert runs["run0000"]["label"] == "Original"
    # cleared: empty label (a rename to nothing hides the run-time label too)
    await client.patch(url, json={"label": ""}, headers=H)
    runs = {x["id"]: x for x in (await client.get(URL, headers=H)).json()["runs"]}
    assert runs["run0001"]["label"] is None
    assert (await client.patch(url, json={"label": "y" * 81}, headers=H)).status_code == 422
    assert (await client.patch(f"{URL}/nosuch", json={"label": "a"}, headers=H)).status_code == 404
    # re-archiving the run keeps the label set later
    await client.patch(url, json={"label": "Kept"}, headers=H)
    archive.write("run0001", _meta(1, label="Original"), {"frames": []})
    runs = {x["id"]: x for x in (await client.get(URL, headers=H)).json()["runs"]}
    assert runs["run0001"]["label"] == "Kept"


def test_label_table_migration(tmp_path):
    """An index made before phase 2 (no run_labels table) gains it on open; rows are kept."""
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(arch._SCHEMA.split("CREATE TABLE IF NOT EXISTS run_labels")[0])
    db.execute("INSERT INTO runs (id, status, finished_at, archived_at, objects) "
               "VALUES ('old1', 'completed', 1, 1, '[]')")
    db.commit()
    db.close()
    idx = RunIndex(path)
    assert [r["id"] for r in idx.list_rows()] == ["old1"]
    idx.set_label("old1", "Renamed")
    assert idx.list_rows()[0]["label"] == "Renamed"


# ── delete ────────────────────────────────────────────────────────────────────


async def test_delete(client, archive, tmp_path):
    _seed(archive, 2)
    await client.patch(f"{URL}/run0000", json={"label": "Gone soon"}, headers=H)
    assert (await client.delete(f"{URL}/run0000")).status_code == 401
    r = await client.delete(f"{URL}/run0000", headers=H)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert [x["id"] for x in (await client.get(URL, headers=H)).json()["runs"]] == ["run0001"]
    assert (await client.get(f"{URL}/run0000")).status_code == 404
    assert not (tmp_path / "runs" / "run0000").exists()
    assert "run0000" not in archive.index.labels()
    assert (await client.delete(f"{URL}/run0000", headers=H)).status_code == 404


async def test_delete_refuses_running_run(client, archive):
    body = {"ignition_lat": 53.0, "ignition_lng": -114.0,
            "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.5}
    sim_id = (await client.post(URL, json=body)).json()["simulation_id"]
    run = sims.runner.get(sim_id)
    if not run.done.is_set():
        assert (await client.delete(f"{URL}/{sim_id}", headers=H)).status_code == 409
    run.done.wait(60)
    archive.wait_idle()
    assert (await client.delete(f"{URL}/{sim_id}", headers=H)).status_code == 200
    assert sims.runner.get(sim_id) is None
    assert (await client.get(f"{URL}/{sim_id}")).status_code == 404


async def test_list_with_archive_off(client, monkeypatch):
    off = build_archive(ArchiveConfig(enabled=False))
    arch.set_archive(off)
    try:
        r = await client.get(URL, headers=H)
        assert r.status_code == 200 and r.json()["archive_enabled"] is False
    finally:
        arch.set_archive(None)


def test_time_parse_naive_is_utc():
    assert runs_router._parse_time("2026-01-01T00:00:00", "since") == runs_router._parse_time(
        "2026-01-01T00:00:00Z", "since")
    assert time.time() > 0
