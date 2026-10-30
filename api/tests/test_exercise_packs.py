"""Exercise mode PR2 (docs/exercise-mode-spec.md §5, §11, §12): scenario packs API.

/api/v1/exercise/packs behind the facilitator passphrase FIRESIM_EXERCISE_PASSPHRASE (header
X-FireSim-Exercise-Passphrase; unset → 403, wrong → 401, per-IP backoff → 429), separate from
the saved-runs passphrase; GET /packs/{id} open by 32-hex ID; 413 over 2 MB; 422 invalid;
packs exempt from archive retention (owner decisions 2026-10-30, Q3/Q4/Q9/Q11/Q12). The
passphrases here are test values.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from firesim_api.main import create_app
from firesim_api.routers import exercise as ex_router
from firesim_api.routers import runs as runs_router
from firesim_api.schemas.exercise import MAX_PACK_BYTES, ScenarioPack
from firesim_api.services import archive as arch
from firesim_api.services.archive import ArchiveConfig, build_archive
from firesim_api.services.passphrase import FREE_FAILURES

PASS = "test-only-exercise-passphrase"  # not a real secret
RUNS_PASS = "test-only-runs-passphrase"
H = {"X-FireSim-Exercise-Passphrase": PASS}
URL = "/api/v1/exercise/packs"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
EXAMPLES = Path(__file__).resolve().parents[2] / "data" / "exercise-packs"


def _pack() -> dict:
    return json.loads((EXAMPLES / "edmonton-terwillegar.json").read_text())


@pytest.fixture
def archive(tmp_path):
    a = build_archive(ArchiveConfig(root=str(tmp_path), db_path=str(tmp_path / "archive.db")))
    arch.set_archive(a)
    yield a
    a.wait_idle()
    arch.set_archive(None)


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    monkeypatch.setenv("FIRESIM_EXERCISE_PASSPHRASE", PASS)
    monkeypatch.setenv("FIRESIM_RUNS_PASSPHRASE", RUNS_PASS)
    ex_router.reset_rate_limit()
    runs_router.reset_rate_limit()
    yield
    ex_router.reset_rate_limit()
    runs_router.reset_rate_limit()


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Auth ──────────────────────────────────────────────────────────────────────


async def test_unset_secret_answers_403(client, archive, monkeypatch):
    monkeypatch.delenv("FIRESIM_EXERCISE_PASSPHRASE", raising=False)
    r = await client.get(URL, headers=H)
    assert r.status_code == 403 and "FIRESIM_EXERCISE_PASSPHRASE" in r.json()["detail"]
    assert (await client.post(URL, json=_pack(), headers=H)).status_code == 403
    assert (await client.delete(f"{URL}/{'a' * 32}", headers=H)).status_code == 403


async def test_missing_and_wrong_passphrase_401(client, archive):
    r = await client.get(URL)
    assert r.status_code == 401 and r.json()["detail"] == "Passphrase required"
    r = await client.get(URL, headers={"X-FireSim-Exercise-Passphrase": "nope"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong passphrase"
    assert (await client.get(URL, headers=H)).status_code == 200


async def test_passphrases_do_not_cross(client, archive):
    # the saved-runs passphrase does not open exercise packs, and the reverse
    r = await client.get(URL, headers={"X-FireSim-Exercise-Passphrase": RUNS_PASS})
    assert r.status_code == 401
    r = await client.get(URL, headers={"X-FireSim-Passphrase": RUNS_PASS})
    assert r.status_code == 401
    r = await client.get("/api/v1/simulations", headers={"X-FireSim-Passphrase": PASS})
    assert r.status_code == 401


async def test_backoff_429_then_reset(client, archive):
    bad = {"X-FireSim-Exercise-Passphrase": "wrong"}
    for _ in range(FREE_FAILURES):
        assert (await client.get(URL, headers=bad)).status_code == 401
    assert (await client.get(URL, headers=bad)).status_code == 401  # starts the lock-out
    r = await client.get(URL, headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # its own count: the saved-runs list is not locked for this IP
    r = await client.get("/api/v1/simulations", headers={"X-FireSim-Passphrase": RUNS_PASS})
    assert r.status_code == 200
    # another IP is not affected
    assert (await client.get(URL, headers={**H, "Fly-Client-IP": "203.0.113.7"})).status_code == 200
    ex_router.reset_rate_limit()
    assert (await client.get(URL, headers=H)).status_code == 200


async def test_passphrase_never_logged(client, archive, caplog):
    caplog.set_level("DEBUG")
    await client.get(URL, headers={"X-FireSim-Exercise-Passphrase": "guess-456"})
    await client.post(URL, json=_pack(), headers=H)
    assert "guess-456" not in caplog.text and PASS not in caplog.text


# ── Round trip, IDs ───────────────────────────────────────────────────────────


async def test_round_trip_list_get_delete(client, archive):
    pack = _pack()
    r = await client.post(URL, json=pack, headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    assert HEX32.match(pid) and body["pack"]["id"] == pid
    # GET by ID needs no passphrase and returns the same pack (with the new ID)
    r = await client.get(f"{URL}/{pid}")
    assert r.status_code == 200
    got = r.json()
    expected = ScenarioPack.model_validate(pack).to_json_dict()
    expected["id"] = pid
    assert got == expected
    assert got["marking"] == "EXERCISE"
    # listed (facilitator only), no pack body in the list
    r = await client.get(URL, headers=H)
    rows = r.json()["packs"]
    assert r.json()["total"] == 1 and rows[0]["id"] == pid
    assert rows[0]["title"] == pack["title"] and rows[0]["durationHours"] == 24
    assert rows[0]["startIso"] == "2026-05-15T14:00:00-06:00" and rows[0]["sources"] == pack["sources"]
    assert "weatherScript" not in rows[0]
    # a stored pack posted again is a new copy with another ID
    r2 = await client.post(URL, json=got, headers=H)
    assert r2.status_code == 201 and r2.json()["id"] != pid and HEX32.match(r2.json()["id"])
    # delete needs the passphrase
    assert (await client.delete(f"{URL}/{pid}")).status_code == 401
    r = await client.delete(f"{URL}/{pid}", headers=H)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert (await client.get(f"{URL}/{pid}")).status_code == 404
    assert (await client.delete(f"{URL}/{pid}", headers=H)).status_code == 404


async def test_ids_are_unique_32_hex(client, archive):
    ids = set()
    for _ in range(5):
        r = await client.post(URL, json=_pack(), headers=H)
        ids.add(r.json()["id"])
    assert len(ids) == 5 and all(HEX32.match(i) for i in ids)


async def test_get_rejects_non_hex_ids(client, archive):
    for bad in ("example-edmonton-terwillegar", "A" * 32, "a" * 31, "..%2f..%2farchive.db"):
        assert (await client.get(f"{URL}/{bad}")).status_code == 404


async def test_unknown_ids_back_off(client, archive):
    from firesim_api.middleware import run_probe_limit
    for i in range(run_probe_limit.FREE_MISSES):
        assert (await client.get(f"{URL}/{i:032x}")).status_code == 404
    r = await client.get(f"{URL}/{'f' * 32}")
    assert r.status_code == 404
    r = await client.get(f"{URL}/{'e' * 32}")
    assert r.status_code == 429


# ── Size and validation ───────────────────────────────────────────────────────


async def test_oversize_413(client, archive):
    pack = _pack()
    pack["notesForFacilitator"] = "x" * 100
    body = json.dumps(pack).encode()
    big = body[:-1] + b"," + b'"pad":"' + b"y" * (MAX_PACK_BYTES + 10) + b'"}'
    r = await client.post(URL, content=big, headers={**H, "content-type": "application/json"})
    assert r.status_code == 413


async def test_oversize_without_content_length_413(client, archive):
    async def gen():
        yield b'{"schema":"firesim.exercise.pack/1","pad":"'
        for _ in range(3):
            yield b"z" * (MAX_PACK_BYTES // 2)
        yield b'"}'
    r = await client.post(URL, content=gen(), headers={**H, "content-type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema="firesim.exercise.pack/2"),
    lambda p: p.update(durationHours=73),                       # owner limit ≤ 72 h
    lambda p: p.update(startIso="2026-07-15T14:00:00"),         # no offset
    lambda p: p.update(marking="LIVE"),
    lambda p: p.update(evacuationTier="Order"),                 # no tier field, anywhere
    lambda p: p["scheduledInjects"][1]["payload"].update(tier="Alert"),
    lambda p: p.update(address="10111 104 Ave NW"),             # no unknown fields (addresses)
    lambda p: p["scheduledInjects"][0].update(kind="evacuation"),
    lambda p: p["scheduledInjects"][0].update(kind="note"),     # payload kind mismatch
    lambda p: p["weatherScript"][0].update(hoursFromStart=1),   # must start at 0
    lambda p: p["weatherScript"][1].update(hoursFromStart=30),  # after the end
    lambda p: p["periods"][1].update(startIso="2026-05-15T19:00:00-06:00"),  # overlap
    lambda p: p["periods"][1].update(number=3),
    lambda p: p["ignitions"][0].update(hoursFromStart=2),       # no ignition at 0
    lambda p: p.update(fuel={"source": "uniform"}),             # uniform needs a type
    lambda p: p.update(title="bad‮title"),                 # direction character
    lambda p: p.update(sources=[]),
])
async def test_invalid_pack_422(client, archive, mutate):
    pack = _pack()
    mutate(pack)
    r = await client.post(URL, json=pack, headers=H)
    assert r.status_code == 422, r.text


async def test_not_json_422(client, archive):
    r = await client.post(URL, content=b"not json", headers={**H, "content-type": "application/json"})
    assert r.status_code == 422


async def test_drawn_perimeter_needs_polygon(client, archive):
    pack = _pack()
    pack["scheduledInjects"][0]["payload"] = {"kind": "perimeter", "perimeterSource": "drawn"}
    assert (await client.post(URL, json=pack, headers=H)).status_code == 422
    pack["scheduledInjects"][0]["payload"]["perimeterGeojson"] = {
        "type": "Polygon", "coordinates": [[[-113.66, 53.46], [-113.65, 53.46], [-113.65, 53.47], [-113.66, 53.46]]]}
    assert (await client.post(URL, json=pack, headers=H)).status_code == 201


# ── Examples, storage, retention ──────────────────────────────────────────────


async def test_examples_public_and_valid(client):
    r = await client.get("/api/v1/exercise/examples")
    assert r.status_code == 200
    packs = {p["id"]: p for p in r.json()["packs"]}
    assert set(packs) == {"example-edmonton-terwillegar", "example-ab-ridgewater"}
    t, w = packs["example-edmonton-terwillegar"], packs["example-ab-ridgewater"]
    # owner's values (decisions 2026-10-30)
    assert t["durationHours"] == 24 and len(t["periods"]) == 3
    assert t["startIso"] == "2026-05-15T14:00:00-06:00"
    assert [p["startIso"][11:16] for p in t["periods"]] == ["14:00", "20:00", "08:00"]
    assert t["ignitions"][0] == {"lat": 53.4606, "lng": -113.6596, "hoursFromStart": 0.0}
    assert t["startingCodes"] == {"ffmc": 92.0, "dmc": 40.0, "dc": 300.0} and t["grassCuringPct"] == 80
    assert w["durationHours"] == 36 and len(w["periods"]) == 3
    assert w["startingCodes"] == {"ffmc": 90.0, "dmc": 25.0, "dc": 150.0}
    assert [j["hoursFromStart"] for j in w["scheduledInjects"]] == [24, 26, 30]
    assert w["weatherScript"][1]["hoursFromStart"] == 24
    for p in (t, w):
        assert p["marking"] == "EXERCISE" and p["title"].startswith("EXERCISE")
        assert p["autoRun"] is False and p["forecastHorizonHours"] == 36
        text = json.dumps(p).lower()
        assert "sfoc" not in text and "evacuation order" not in text
    # the fictional community name is used; the real one appears only in the alias and the
    # facilitator note
    assert "Wandering River" not in w["title"] + (w["summary"] or "")
    assert w["placeAliases"] == [{"place": "Wandering River", "shownAs": "Hamlet of Ridgewater (fictional)"}]


def test_example_files_are_canonical():
    # every field of the repository files is set explicitly (no silent schema defaults), and
    # the schema round-trip is stable
    files = list(EXAMPLES.glob("*.json"))
    assert len(files) == 2
    for f in files:
        raw = json.loads(f.read_text())
        assert set(ScenarioPack.model_validate(raw).to_json_dict()) == set(raw)
        again = ScenarioPack.model_validate(ScenarioPack.model_validate(raw).to_json_dict())
        assert again.to_json_dict() == ScenarioPack.model_validate(raw).to_json_dict()


async def test_packs_survive_retention(client, archive, tmp_path):
    r = await client.post(URL, json=_pack(), headers=H)
    pid = r.json()["id"]
    pack_file = tmp_path / "exercise" / "packs" / f"{pid}.json"
    assert pack_file.is_file()
    # a far-future "now", zero age and a 1-byte cap: every run would go, packs stay (Q9)
    out = archive.run_retention(now=4_000_000_000.0, max_age_days=0.0, max_bytes=1)
    assert out["deleted_orphans"] == 0
    assert pack_file.is_file()
    assert (await client.get(f"{URL}/{pid}")).status_code == 200


def test_s3_orphan_sweep_skips_exercise_keys():
    class FakeS3:
        def __init__(self):
            self.objects = {}

        def put_object(self, Bucket, Key, Body):
            self.objects[Key] = Body

        def get_object(self, Bucket, Key):
            if Key not in self.objects:
                raise type("NoSuchKey", (Exception,), {})()
            body = self.objects[Key]
            return {"Body": type("B", (), {"read": lambda self: body})()}

        def delete_object(self, Bucket, Key):
            self.objects.pop(Key, None)

        def list_objects_v2(self, Bucket, Prefix, **kw):
            return {"Contents": [{"Key": k, "Size": len(v), "LastModified": 0.0}
                                 for k, v in self.objects.items() if k.startswith(Prefix)]}

    import tempfile
    d = tempfile.mkdtemp()
    s3 = FakeS3()
    cfg = ArchiveConfig(root=d, db_path=f"{d}/a.db", backend="s3", s3_bucket="b", s3_prefix="")
    a = build_archive(cfg, s3_client=s3)
    assert a.backend.name == "s3" and a.pack_backend.prefix == "exercise/packs/"
    a.pack_backend.put("p.json", b"{}")
    a.backend.put("orphanrun/meta.json", b"{}")
    a.run_retention(now=4_000_000_000.0)
    assert "exercise/packs/p.json" in s3.objects and "orphanrun/meta.json" not in s3.objects
    # the default runs/ prefix puts packs at exercise/packs/ beside it
    b = build_archive(ArchiveConfig(root=d, db_path=f"{d}/b.db", backend="s3", s3_bucket="b"), s3_client=s3)
    assert b.pack_backend.prefix == "exercise/packs/"


async def test_archive_off_answers_409(client, monkeypatch):
    arch.set_archive(build_archive(ArchiveConfig(enabled=False)))
    try:
        r = await client.get(URL, headers=H)
        assert r.status_code == 409
    finally:
        arch.set_archive(None)


def test_schema_defaults_owner_q12():
    p = _pack()
    for k in ("forecastHorizonHours", "autoRun"):
        p.pop(k)
    m = ScenarioPack.model_validate(p)
    assert m.forecastHorizonHours == 36 and m.autoRun is False
    big = copy.deepcopy(_pack())
    big["durationHours"] = 72
    big["periods"][-1]["endIso"] = "2026-05-18T14:00:00-06:00"
    assert ScenarioPack.model_validate(big).durationHours == 72
