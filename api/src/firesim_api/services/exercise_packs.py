"""Exercise scenario pack storage (docs/exercise-mode-spec.md §5, §11.3; PR2).

Packs live beside the run archive (``services/archive.py``): the JSON under
``exercise/packs/<id>.json`` in the archive's object store (``RunArchive.pack_backend``) and one
row per pack in the archive's SQLite index (table ``exercise_packs``). IDs are 32 hex characters
from ``services/run_ids.py`` (128 bits from ``secrets``), so the unauthenticated
``GET /exercise/packs/{id}`` cannot practically be guessed.

Packs are exempt from age and byte pruning (owner decision 2026-10-30, Q9): ``run_retention``
never reads this table or these objects. A pack is removed only by ``DELETE``.

Example packs (``data/exercise-packs/*.json``) are public and read from the repository
(``FIRESIM_EXERCISE_EXAMPLES_DIR``; api/Dockerfile copies ``data/`` to ``/app/data``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from firesim_api.schemas.exercise import ScenarioPack
from firesim_api.services import archive as run_archive
from firesim_api.services.run_ids import NEW_ID, new_run_id

logger = logging.getLogger(__name__)

EXAMPLES_ENV = "FIRESIM_EXERCISE_EXAMPLES_DIR"


class PacksUnavailable(RuntimeError):
    """The run archive (and so pack storage) is off on this server."""


def _store():
    archive = run_archive.get_archive()
    if not archive.enabled or archive.pack_backend is None:
        raise PacksUnavailable("Pack storage needs the run archive (it is off on this server)")
    return archive


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def valid_pack_id(pack_id: str) -> bool:
    return bool(NEW_ID.match(pack_id or ""))


def put_pack(pack: ScenarioPack) -> tuple[str, dict, str]:
    """Store a validated pack under a new 32-hex ID; returns (id, stored JSON, created ISO)."""
    archive = _store()
    pack_id = new_run_id()
    doc = pack.to_json_dict()
    doc["id"] = pack_id
    data = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    archive.pack_backend.put(f"{pack_id}.json", data)
    now = time.time()
    with archive.index._lock, archive.index._connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO exercise_packs (id, title, start_iso, duration_hours, "
            "sources_json, created_at, bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack_id, doc["title"], doc["startIso"], doc["durationHours"],
             json.dumps(doc["sources"]), now, len(data)))
    logger.info("Exercise packs: %s stored (%d bytes)", pack_id, len(data))
    return pack_id, doc, _iso(now)


def get_pack(pack_id: str) -> dict | None:
    if not valid_pack_id(pack_id):
        return None
    archive = _store()
    data = archive.pack_backend.get(f"{pack_id}.json")
    return json.loads(data.decode("utf-8")) if data is not None else None


def list_packs() -> list[dict]:
    archive = _store()
    with archive.index._connect() as db:
        rows = db.execute("SELECT id, title, start_iso, duration_hours, sources_json, created_at "
                          "FROM exercise_packs ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        try:
            sources = json.loads(r["sources_json"] or "[]")
        except ValueError:
            sources = []
        out.append({"id": r["id"], "title": r["title"], "startIso": r["start_iso"],
                    "durationHours": r["duration_hours"], "sources": sources,
                    "createdAt": _iso(r["created_at"])})
    return out


def delete_pack(pack_id: str) -> bool:
    if not valid_pack_id(pack_id):
        return False
    archive = _store()
    with archive.index._lock, archive.index._connect() as db:
        n = db.execute("DELETE FROM exercise_packs WHERE id = ?", (pack_id,)).rowcount
    found = n > 0 or archive.pack_backend.get(f"{pack_id}.json") is not None
    archive.pack_backend.delete(f"{pack_id}.json")
    return found


# ── Example packs (public, in the repository) ────────────────────────────────


def examples_dir() -> Path | None:
    env = os.environ.get(EXAMPLES_ENV)
    candidates = [Path(env)] if env else []
    candidates += [Path("/app/data/exercise-packs"),
                   Path(__file__).resolve().parents[4] / "data" / "exercise-packs"]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def example_packs() -> list[dict]:
    """The repository's example packs, validated (an invalid file is skipped with a warning)."""
    d = examples_dir()
    if d is None:
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            pack = ScenarioPack.model_validate_json(f.read_bytes())
        except Exception:  # noqa: BLE001 - one bad file must not hide the others
            logger.warning("Exercise packs: example %s is not a valid pack", f.name, exc_info=True)
            continue
        out.append(pack.to_json_dict())
    return out
