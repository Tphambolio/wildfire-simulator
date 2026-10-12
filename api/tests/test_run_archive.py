"""Run archive, phase 1 (services/archive.py): finished runs survive deploys and restarts.

A finished run is archived in the background; GET /simulations/{id}, /arrival and /ensemble
read memory first, then the archive, and the archived body is the live one plus
``archived: true``. Retention removes runs past the maximum age or over the byte cap. A failing
archive never breaks a run.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from firesim_api.main import create_app
from firesim_api.routers import simulations as sims
from firesim_api.services import archive as arch
from firesim_api.services.archive import (
    ArchiveConfig,
    LocalBackend,
    RunArchive,
    RunIndex,
    S3Backend,
    build_archive,
)

_EDMONTON_FUEL = Path(__file__).resolve().parents[2] / "data" / "Edmonton_FBP_FuelLayer_20251105_10m.tif"

SINGLE = {"ignition_lat": 53.0, "ignition_lng": -114.0,
          "weather": {"wind_speed": 10.0, "wind_direction": 270.0}, "duration_hours": 0.5}
_DRY = {"wind_speed": 2.0, "wind_direction": 270.0, "temperature": 26.0,
        "relative_humidity": 30.0, "precipitation_24h": 0.0}
MULTIDAY = {"ignition_lat": 53.0, "ignition_lng": -114.0,
            "days": [_DRY, dict(_DRY, relative_humidity=70.0, precipitation_24h=12.0)],
            "fwi_overrides": {"ffmc": 88.0, "dmc": 30.0, "dc": 250.0}, "month": 7,
            "snapshot_interval_minutes": 120.0, "fuel_type": "C2", "cells_mode": "incremental"}


def _local_archive(tmp_path, **kw) -> RunArchive:
    cfg = ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "archive.db"), **kw)
    return build_archive(cfg)


@pytest.fixture
def archive(tmp_path):
    """A fresh local archive for the test, installed as the process's archive."""
    a = _local_archive(tmp_path)
    arch.set_archive(a)
    yield a
    a.wait_idle()
    arch.set_archive(None)


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _wait_done(client, sim_id: str, tries: int = 480) -> dict:
    for _ in range(tries):
        data = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        time.sleep(0.25)
    return data


def _wait_finished(sim_id: str, tries: int = 400) -> None:
    """Wait until the run's thread has ended and its ``on_finished`` hook has queued the
    archive job. Status turns "completed" first, so ``wait_idle`` alone can return early."""
    run = sims.runner.get(sim_id)
    if run is not None:
        run.done.wait(tries * 0.05)


def _forget(sim_id: str) -> None:
    """As after a deploy or restart: the run is no longer in the API's memory."""
    sims.runner._runs.pop(sim_id, None)


def _restart(tmp_path) -> RunArchive:
    """A new process: memory is empty and the archive is reopened from disk."""
    a = _local_archive(tmp_path)
    arch.set_archive(a)
    return a


def _without_flag(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "archived"}


# ── unit: round trip, index ─────────────────────────────────────────────────


def test_write_read_round_trip(tmp_path):
    a = _local_archive(tmp_path)
    response = {"simulation_id": "abc12345", "status": "completed", "frames": [{"t": 1}]}
    arrival = {"rows": 2, "cols": 2, "minutes": "AAAA"}
    row = a.write("abc12345", {"status": "completed", "kind": "single", "git_sha": "deadbee",
                               "fuel_source_kind": "city", "area_ha": 1.5, "frame_count": 1},
                  response, arrival=arrival)
    assert a.load_response("abc12345") == response
    assert a.load_arrival("abc12345") == arrival
    assert a.load_ensemble("abc12345") is None
    assert a.has("abc12345") and not a.has("nope0000")
    stored = a.index.get("abc12345")
    assert stored["status"] == "completed" and stored["git_sha"] == "deadbee"
    assert stored["fuel_source_kind"] == "city" and stored["area_ha"] == 1.5
    assert json.loads(stored["objects"]) == sorted(["response.json.gz", "arrival.json.gz", "meta.json"])
    assert stored["bytes_total"] == row["bytes_total"] > 0
    # payloads are gzip JSON on disk; meta.json lets the index be rebuilt from the store
    raw = (tmp_path / "runs" / "abc12345" / "response.json.gz").read_bytes()
    assert json.loads(gzip.decompress(raw)) == response
    meta = json.loads((tmp_path / "runs" / "abc12345" / "meta.json").read_bytes())
    assert meta["git_sha"] == "deadbee" and "response.json.gz" in meta["objects"]
    # the index is WAL (Litestream needs it)
    import sqlite3
    assert sqlite3.connect(a.index.path).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_ids_that_are_not_run_ids_are_refused(tmp_path):
    a = _local_archive(tmp_path)
    for bad in ("../etc", "a/b", "", "x" * 65, "a b"):
        assert a.load_response(bad) is None and not a.has(bad)
        with pytest.raises(ValueError):
            a.write(bad, {"status": "completed"}, {})
    with pytest.raises(ValueError):
        LocalBackend(tmp_path).put("../../escape", b"x")


def test_default_location_is_temporary_without_a_volume(monkeypatch, caplog):
    """No FIRESIM_ARCHIVE_DIR and no /data volume (Fly as deployed today): the archive still
    works, on a temporary directory, and says so at startup."""
    monkeypatch.setattr(arch.os.path, "isdir", lambda p: False if p == "/data" else os.path.isdir(p))
    cfg = ArchiveConfig.from_env({})
    assert not cfg.durable and cfg.root.endswith("firesim-archive")
    assert cfg.max_age_days == 365 and cfg.max_bytes == 0 and cfg.backend == "local"
    cfg2 = ArchiveConfig.from_env({"FIRESIM_ARCHIVE_DIR": "/x", "FIRESIM_ARCHIVE_MAX_AGE_DAYS": "30",
                                   "FIRESIM_ARCHIVE_MAX_BYTES": "1e9"})
    assert cfg2.durable and cfg2.db_path == "/x/archive.db"
    assert cfg2.max_age_days == 30 and cfg2.max_bytes == 1_000_000_000
    assert not ArchiveConfig.from_env({"FIRESIM_ARCHIVE": "0"}).enabled


def test_s3_without_credentials_falls_back_to_local(tmp_path, caplog):
    cfg = ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "a.db"), backend="s3",
                        s3_bucket="firesim-run-archive")
    with caplog.at_level("WARNING"):
        a = build_archive(cfg)
    assert a.enabled and a.backend.name == "local"
    assert "FIRESIM_ARCHIVE_S3_ACCESS_KEY_ID" in caplog.text


def test_disabled_archive_stores_nothing(tmp_path):
    a = build_archive(ArchiveConfig(enabled=False, root=str(tmp_path)))
    assert not a.enabled and a.submit("abc", lambda: {}) is None
    assert a.load_response("abc") is None and a.run_retention()["deleted_age"] == 0
    assert a.status()["enabled"] is False


# ── retention ───────────────────────────────────────────────────────────────


def _put(a: RunArchive, sim_id: str, finished_at: float, size: int = 100) -> None:
    a.write(sim_id, {"status": "completed", "finished_at": finished_at},
            {"simulation_id": sim_id, "pad": "x" * size})


def test_retention_by_age_and_total_bytes(tmp_path):
    a = _local_archive(tmp_path)
    now = time.time()
    day = 86400.0
    _put(a, "old00001", now - 400 * day)
    _put(a, "mid00002", now - 100 * day, size=20_000)
    _put(a, "new00003", now - 1 * day, size=20_000)
    out = a.run_retention(now=now)  # default 365 days
    assert out["deleted_age"] == 1
    assert a.load_response("old00001") is None and not (tmp_path / "runs" / "old00001").exists()
    assert a.load_response("mid00002") is not None

    # byte cap below the two remaining runs: the oldest goes
    total = a.index.stats()["bytes"]
    out = a.run_retention(now=now, max_bytes=total - 1)
    assert out["deleted_bytes"] == 1
    assert a.load_response("mid00002") is None and a.load_response("new00003") is not None
    assert a.index.stats()["runs"] == 1

    # a shorter maximum age (configurable) removes the rest
    assert a.run_retention(now=now, max_age_days=0.5)["deleted_age"] == 1
    assert a.index.stats() == {"runs": 0, "bytes": 0}


def test_retention_sweeps_payloads_left_by_a_lost_index(tmp_path):
    """Payloads whose index row is gone (an index on a temporary disk) are read by GET and
    removed by age from the store's own timestamps."""
    a = _local_archive(tmp_path)
    _put(a, "orph0001", time.time())
    a.index.delete("orph0001")  # the index was lost
    assert a.load_response("orph0001") is not None and a.has("orph0001")
    assert a.run_retention()["deleted_orphans"] == 0  # still young
    old = time.time() - 400 * 86400
    for p in (tmp_path / "runs" / "orph0001").iterdir():
        os.utime(p, (old, old))
    assert a.run_retention()["deleted_orphans"] == 2
    assert a.load_response("orph0001") is None


# ── S3 backend (stub client; moto when installed) ──────────────────────────


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    """The four S3 calls the backend uses, in memory, with 2-key pages."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, float]] = {}
        self.calls: list[str] = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append("put")
        self.objects[(Bucket, Key)] = (bytes(Body), time.time())

    def get_object(self, Bucket, Key):
        self.calls.append("get")
        if (Bucket, Key) not in self.objects:
            raise _NoSuchKey()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def delete_object(self, Bucket, Key):
        self.calls.append("delete")
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        from datetime import datetime, timezone
        out = {"Contents": [{"Key": k, "Size": len(self.objects[(Bucket, k)][0]),
                             "LastModified": datetime.fromtimestamp(self.objects[(Bucket, k)][1],
                                                                    tz=timezone.utc)}
                            for k in page],
               "IsTruncated": start + 2 < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + 2)
        return out


def test_s3_backend_round_trip_and_retention(tmp_path):
    s3 = FakeS3()
    cfg = ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "a.db"), backend="s3",
                        s3_bucket="firesim-run-archive", s3_prefix="runs")
    a = build_archive(cfg, s3_client=s3)
    assert a.backend.name == "s3" and a.backend.prefix == "runs/"
    _put(a, "s3run001", time.time())
    _put(a, "s3run002", time.time() - 400 * 86400)
    assert ("firesim-run-archive", "runs/s3run001/response.json.gz") in s3.objects
    assert a.load_response("s3run001")["simulation_id"] == "s3run001"
    assert a.load_arrival("s3run001") is None  # NoSuchKey reads as absent
    assert len(list(a.backend.list())) == 4  # paged listing (2 keys a page)
    assert a.run_retention()["deleted_age"] == 1
    assert {k for _, k in s3.objects} == {"runs/s3run001/response.json.gz", "runs/s3run001/meta.json"}


def test_s3_backend_against_moto(tmp_path):
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="firesim-run-archive")
        a = RunArchive(RunIndex(tmp_path / "a.db"), S3Backend("firesim-run-archive", client=client),
                       ArchiveConfig(root=str(tmp_path)))
        _put(a, "moto0001", time.time())
        assert a.load_response("moto0001")["simulation_id"] == "moto0001"
        assert a.load_response("moto0002") is None


# ── API: archived GET is the live GET ──────────────────────────────────────


async def test_restart_get_serves_the_archived_run_shape_identical(client, archive, tmp_path):
    sim_id = (await client.post("/api/v1/simulations", json=SINGLE)).json()["simulation_id"]
    live = await _wait_done(client, sim_id)
    assert live["status"] == "completed" and live["archived"] is False
    live_arrival = await client.get(f"/api/v1/simulations/{sim_id}/arrival")
    _wait_finished(sim_id)
    assert archive.wait_idle()

    row = archive.index.get(sim_id)
    assert row["status"] == "completed" and row["kind"] == "single"
    assert row["frame_count"] == len(live["frames"])
    assert row["area_ha"] == live["frames"][-1]["area_ha"]
    assert json.loads(row["request_json"])["ignition_lat"] == 53.0
    assert row["git_sha"] and row["finished_at"] >= row["created_at"]

    _forget(sim_id)
    _restart(tmp_path)
    resp = await client.get(f"/api/v1/simulations/{sim_id}")
    assert resp.status_code == 200
    stored = resp.json()
    assert stored["archived"] is True
    assert _without_flag(stored) == _without_flag(live)
    # arrival: the same answer as before the restart (a body, or the same 404)
    after = await client.get(f"/api/v1/simulations/{sim_id}/arrival")
    assert after.status_code == live_arrival.status_code
    if live_arrival.status_code == 200:
        assert after.json() == live_arrival.json()
    else:
        assert after.json()["detail"].startswith("No arrival grid")
    ens = await client.get(f"/api/v1/simulations/{sim_id}/ensemble")
    assert ens.status_code == 404 and ens.json()["detail"] == "No ensemble requested for this run"
    health = (await client.get("/api/v1/health")).json()
    assert health["archive"]["enabled"] and health["archive"]["runs"] >= 1


async def test_multiday_run_restored_with_day_and_day_fwi(client, archive, tmp_path):
    """A multi-day run (incremental cells) comes back from the archive with each frame's
    ``day``, ``day_fwi`` and ``cells_offset`` exactly as the live GET gave them."""
    sim_id = (await client.post("/api/v1/simulations/multiday", json=MULTIDAY)).json()["simulation_id"]
    live = await _wait_done(client, sim_id)
    assert live["status"] == "completed", live.get("error")
    _wait_finished(sim_id)
    assert archive.wait_idle()
    row = archive.index.get(sim_id)
    assert row["kind"] == "multiday" and row["days"] == 2 and row["duration_hours"] == 48

    _forget(sim_id)
    _restart(tmp_path)
    stored = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
    assert stored["archived"] is True and _without_flag(stored) == _without_flag(live)
    assert [f["day"] for f in stored["frames"]] == [1] * 13 + [2] * 12
    assert stored["frames"][-1]["day_fwi"] == live["frames"][-1]["day_fwi"] is not None


async def test_unknown_run_is_still_404(client, archive):
    for path in ("nope0000", "nope0000/arrival", "nope0000/ensemble"):
        resp = await client.get(f"/api/v1/simulations/{path}")
        assert resp.status_code == 404 and resp.json()["detail"] == "Simulation not found"


async def test_cancelled_run_is_archived_as_cancelled(client, archive):
    long = dict(SINGLE, duration_hours=24.0, snapshot_interval_minutes=5.0)
    sim_id = (await client.post("/api/v1/simulations", json=long)).json()["simulation_id"]
    sims.runner.get(sim_id).cancel()
    for _ in range(200):
        if archive.index.get(sim_id) is not None:
            break
        time.sleep(0.05)
    archive.wait_idle()
    assert archive.index.get(sim_id)["status"] == "cancelled"
    _forget(sim_id)
    assert (await client.get(f"/api/v1/simulations/{sim_id}")).json()["status"] == "cancelled"


async def test_a_failing_archive_never_breaks_a_run(client, tmp_path, caplog):
    class Broken(LocalBackend):
        def put(self, key, data):
            raise OSError("disk full")

    a = RunArchive(RunIndex(tmp_path / "a.db"), Broken(tmp_path), ArchiveConfig(root=str(tmp_path)))
    arch.set_archive(a)
    try:
        with caplog.at_level("ERROR"):
            sim_id = (await client.post("/api/v1/simulations", json=SINGLE)).json()["simulation_id"]
            live = await _wait_done(client, sim_id)
            _wait_finished(sim_id)
            assert a.wait_idle()
        assert live["status"] == "completed" and live["frames"]
        assert a.failures == 1 and "could not archive" in caplog.text
        assert (await client.get(f"/api/v1/simulations/{sim_id}")).json()["status"] == "completed"
        _forget(sim_id)
        assert (await client.get(f"/api/v1/simulations/{sim_id}")).status_code == 404

        # an archive whose reads fail answers 404, never 500
        class Unreadable(LocalBackend):
            def get(self, key):
                raise OSError("store unreachable")

        arch.set_archive(RunArchive(RunIndex(tmp_path / "b.db"), Unreadable(tmp_path),
                                    ArchiveConfig(root=str(tmp_path))))
        assert (await client.get(f"/api/v1/simulations/{sim_id}")).status_code == 404
    finally:
        arch.set_archive(None)


async def test_a_raising_finish_hook_never_breaks_a_run(client, archive):
    def boom(run):
        raise RuntimeError("hook failed")

    saved = sims.runner.on_finished
    sims.runner.on_finished = boom
    try:
        sim_id = (await client.post("/api/v1/simulations", json=SINGLE)).json()["simulation_id"]
        assert (await _wait_done(client, sim_id))["status"] == "completed"
    finally:
        sims.runner.on_finished = saved


async def test_archive_off_keeps_the_old_behaviour(client, tmp_path):
    arch.set_archive(build_archive(ArchiveConfig(enabled=False, root=str(tmp_path))))
    try:
        sim_id = (await client.post("/api/v1/simulations", json=SINGLE)).json()["simulation_id"]
        assert (await _wait_done(client, sim_id))["status"] == "completed"
        _forget(sim_id)
        assert (await client.get(f"/api/v1/simulations/{sim_id}")).status_code == 404
        assert not list(tmp_path.glob("runs/*"))
    finally:
        arch.set_archive(None)


async def test_grid_run_with_ensemble_restores_arrival_and_ensemble(client, archive, tmp_path):
    if not _EDMONTON_FUEL.exists():
        pytest.skip("Edmonton fuel grid not present")
    payload = {
        "ignition_lat": 53.6778, "ignition_lng": -113.3631,
        "weather": {"wind_speed": 25.0, "wind_direction": 270.0},
        "fwi_overrides": {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0},
        "duration_hours": 1.0, "snapshot_interval_minutes": 30.0,
        "fuel_grid_path": str(_EDMONTON_FUEL), "fuel_modifiers": {"grass_cure": 60.0},
        "ensemble": {"n_members": 6, "seed": 2}, "cells_mode": "incremental",
    }
    sim_id = (await client.post("/api/v1/simulations", json=payload)).json()["simulation_id"]
    for _ in range(480):
        ens = (await client.get(f"/api/v1/simulations/{sim_id}/ensemble")).json()
        if ens["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert ens["status"] == "completed", ens
    live = (await client.get(f"/api/v1/simulations/{sim_id}")).json()
    arrival = (await client.get(f"/api/v1/simulations/{sim_id}/arrival")).json()
    # the run is archived once its thread ends, i.e. after the ensemble
    for _ in range(200):
        if archive.index.get(sim_id) is not None:
            break
        time.sleep(0.05)
    assert archive.wait_idle()
    row = archive.index.get(sim_id)
    assert row["ensemble_status"] == "completed" and row["fuel_source_kind"]
    assert "ensemble.json.gz" in json.loads(row["objects"])

    _forget(sim_id)
    _restart(tmp_path)
    assert _without_flag((await client.get(f"/api/v1/simulations/{sim_id}")).json()) == _without_flag(live)
    assert (await client.get(f"/api/v1/simulations/{sim_id}/arrival")).json() == arrival
    assert (await client.get(f"/api/v1/simulations/{sim_id}/ensemble")).json() == ens
