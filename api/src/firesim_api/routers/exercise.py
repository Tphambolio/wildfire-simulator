"""Exercise mode: scenario packs (docs/exercise-mode-spec.md §5, §11; PR2).

All under ``/api/v1/exercise``:

| Method | Path | Passphrase | Purpose |
|---|---|---|---|
| GET | ``/examples`` | no | The repository's public example packs (full JSON) |
| GET | ``/packs`` | yes | List stored packs (id, title, start, duration, sources, created) |
| POST | ``/packs`` | yes | Store a pack (validated ``ScenarioPack``, ≤ 2 MB); a new 32-hex ID |
| GET | ``/packs/{id}`` | no | One stored pack by its 32-hex ID (players open it by link) |
| DELETE | ``/packs/{id}`` | yes | Remove a stored pack |

Facilitator passphrase (owner decisions 2026-10-30, Q4 and Q11): header
``X-FireSim-Exercise-Passphrase`` against the secret ``FIRESIM_EXERCISE_PASSPHRASE``, one per
server, separate from ``FIRESIM_RUNS_PASSPHRASE`` (neither opens the other's endpoints). Unset →
403. Compared and rate-limited exactly as the saved-runs passphrase (``services/passphrase.py``),
with its own failure count. Customer packs are private behind it: there is no listing without
it and no public library (Q3); the unauthenticated GET by 32-hex ID is kept so players can open
a pack by link. Unknown IDs on that GET back off per IP (``middleware/run_probe_limit.py``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from firesim_api.schemas.exercise import MAX_PACK_BYTES, ExamplePackList, PackList, ScenarioPack
from firesim_api.services import exercise_packs as packs
from firesim_api.services.passphrase import PassphraseGate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exercise", tags=["exercise"])

PASSPHRASE_ENV = "FIRESIM_EXERCISE_PASSPHRASE"
PASSPHRASE_HEADER = "X-FireSim-Exercise-Passphrase"
NOT_FOUND = "Pack not found"

_gate = PassphraseGate(
    PASSPHRASE_ENV, PASSPHRASE_HEADER,
    disabled_detail=f"Exercise packs are disabled on this server ({PASSPHRASE_ENV} is not set).",
    log_name="Exercise packs")


def reset_rate_limit() -> None:
    """Forget all failed attempts (tests)."""
    _gate.reset()


def require_facilitator(request: Request) -> None:
    _gate.require(request)


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/examples", response_model=ExamplePackList)
async def list_examples() -> ExamplePackList:
    """The example packs shipped in the repository (public open-data packs; owner decision
    2026-10-30, Q3). No passphrase."""
    return ExamplePackList(packs=await run_in_threadpool(packs.example_packs))


@router.get("/packs", response_model=PackList)
async def list_packs(request: Request) -> PackList:
    """Stored packs, newest first. Needs the facilitator passphrase."""
    require_facilitator(request)
    try:
        rows = await run_in_threadpool(packs.list_packs)
    except packs.PacksUnavailable as exc:
        raise _unavailable(exc) from exc
    return PackList(packs=rows, total=len(rows))


@router.post("/packs", status_code=201)
async def store_pack(request: Request) -> dict:
    """Store a pack (the JSON file as the body, ≤ 2 MB). The server assigns a new 32-hex ID
    (an ``id`` in the body is replaced). 413 when too large, 422 when not a valid pack."""
    require_facilitator(request)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_PACK_BYTES:
        raise HTTPException(status_code=413, detail=f"A pack must be at most {MAX_PACK_BYTES} bytes")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_PACK_BYTES:
            raise HTTPException(status_code=413, detail=f"A pack must be at most {MAX_PACK_BYTES} bytes")
    try:
        pack = ScenarioPack.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False,
                                                               include_input=False)) from exc
    try:
        pack_id, doc, created = await run_in_threadpool(packs.put_pack, pack)
    except packs.PacksUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"id": pack_id, "createdAt": created, "pack": doc}


@router.get("/packs/{pack_id}")
async def get_pack(pack_id: str) -> dict:
    """One stored pack by ID. No passphrase (players open it by link; IDs are 128 random bits)."""
    if not packs.valid_pack_id(pack_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        doc = await run_in_threadpool(packs.get_pack, pack_id)
    except packs.PacksUnavailable as exc:
        raise _unavailable(exc) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return doc


@router.delete("/packs/{pack_id}")
async def delete_pack(pack_id: str, request: Request) -> dict:
    """Remove a stored pack. Needs the facilitator passphrase."""
    require_facilitator(request)
    try:
        found = await run_in_threadpool(packs.delete_pack, pack_id)
    except packs.PacksUnavailable as exc:
        raise _unavailable(exc) from exc
    if not found:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    logger.info("Exercise packs: %s deleted", pack_id)
    return {"id": pack_id, "deleted": True}
