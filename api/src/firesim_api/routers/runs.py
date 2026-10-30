"""Saved runs (run archive phase 2): the list of runs, behind a shared passphrase.

``GET /api/v1/simulations`` lists the runs in the run archive (``services/archive.py``) plus the
runs still in the API's memory that are not archived yet, newest first, with optional filters
(time range, a box around the ignition point, kind) and ``limit``/``offset`` paging.
``PATCH /api/v1/simulations/{id}`` sets or clears a run's label and ``DELETE
/api/v1/simulations/{id}`` removes a finished run from the archive and memory.

All three need the header ``X-FireSim-Passphrase``. It is compared (``hmac.compare_digest`` of
SHA-256 digests) with the environment variable ``FIRESIM_RUNS_PASSPHRASE``; with the variable
unset the endpoints answer 403, so nothing is listed until the owner sets it. The passphrase
is never logged. Failed attempts are rate-limited per client IP (in memory): after
``_FREE_FAILURES`` failures each further one locks the IP out for 2, 4, 8 ... seconds (at most
``_MAX_LOCK_S``), answered 429 with ``Retry-After``; a success clears the count.

``GET /api/v1/simulations/{id}`` (routers/simulations.py) stays open: new run IDs are 128 random
bits (services/run_ids.py), unknown IDs back off per IP (middleware/run_probe_limit.py), and the
EOC console reloads its periods' runs by ID. The list carries no address and no
per-building data: ignition point rounded to 3 decimals (about 100 m), times, kind, summary
and source labels. See docs/run-archive.md (phase 2).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from firesim_api.routers import simulations as sims
from firesim_api.services import archive as run_archive
from firesim_api.services.passphrase import FREE_FAILURES, MAX_LOCK_S, PassphraseGate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulations", tags=["saved runs"])

PASSPHRASE_ENV = "FIRESIM_RUNS_PASSPHRASE"
PASSPHRASE_HEADER = "X-FireSim-Passphrase"
LABEL_MAX = 80
KINDS = ("single", "multiday", "perimeter_override")

# ---------------------------------------------------------------------------
# Passphrase gate and failed-attempt backoff (services/passphrase.py; the exercise
# facilitator passphrase uses the same gate with its own secret and its own count)
# ---------------------------------------------------------------------------

_FREE_FAILURES = FREE_FAILURES  # failures allowed before the backoff starts
_MAX_LOCK_S = MAX_LOCK_S        # longest lock-out (s)

_gate = PassphraseGate(
    PASSPHRASE_ENV, PASSPHRASE_HEADER,
    disabled_detail=f"The saved-runs list is disabled on this server ({PASSPHRASE_ENV} is not set).",
    log_name="Saved runs")


def reset_rate_limit() -> None:
    """Forget all failed attempts (tests)."""
    _gate.reset()


def require_passphrase(request: Request) -> None:
    """Raise 403 (list disabled), 429 (locked out), 401 (missing or wrong passphrase)."""
    _gate.require(request)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


def clean_label(value: Any) -> str | None:
    """A label as plain text: control and direction characters removed, whitespace collapsed,
    at most 80 characters; empty → None."""
    if not isinstance(value, str):
        return None
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text[:LABEL_MAX] or None


class SavedRun(BaseModel):
    id: str
    label: str | None = Field(None, description="Run label (renamed, else the one given at run time)")
    kind: str | None = Field(None, description="single, multiday or perimeter_override")
    status: str
    archived: bool = Field(description="In the run archive (false: in memory only, not archived yet)")
    created_at: str | None = Field(None, description="Run submitted (UTC, ISO 8601)")
    finished_at: str | None = Field(None, description="Run ended (UTC, ISO 8601)")
    start_time: str | None = Field(None, description="Scenario start (as requested, with its offset)")
    ignition_lat: float | None = Field(None, description="Ignition latitude, 3 decimals")
    ignition_lng: float | None = Field(None, description="Ignition longitude, 3 decimals")
    area_ha: float | None = None
    duration_hours: float | None = None
    frame_count: int | None = None
    days: int | None = None
    fuel_source: str | None = Field(None, description="Fuel source label")
    buildings_source: str | None = Field(None, description="Buildings source label")
    ensemble: bool = Field(False, description="The run has an ensemble (range of outcomes)")
    ensemble_status: str | None = None
    ensemble_members: int | None = None


class SavedRunList(BaseModel):
    runs: list[SavedRun]
    total: int = Field(description="Runs matching the filters (all pages)")
    limit: int
    offset: int
    archive_enabled: bool


class LabelUpdate(BaseModel):
    label: str | None = Field(None, max_length=LABEL_MAX,
                              description="New label; null or empty clears it")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _round3(v: Any) -> float | None:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _request_fields(request: dict | None) -> dict:
    """Ignition point, scenario start, run-time label and ensemble size from a stored request
    (a perimeter override stores ``{"source_config": ..., "perimeter_override": ...}``)."""
    if not isinstance(request, dict):
        return {}
    src = request
    over: dict = {}
    if "perimeter_override" in request:
        src = request.get("source_config") or {}
        over = request.get("perimeter_override") or {}
    ens = src.get("ensemble") if isinstance(src, dict) else None
    members = ens.get("n_members") if isinstance(ens, dict) and not over else None
    return {
        "ignition_lat": _round3(src.get("ignition_lat")),
        "ignition_lng": _round3(src.get("ignition_lng")),
        "start_time": over.get("start_time") or src.get("start_time"),
        "label": clean_label(over.get("label")) or clean_label(src.get("label")),
        "ensemble_members": members if isinstance(members, int) and members > 0 else None,
    }


def _status(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v)


def _archived_rows(archive) -> tuple[list[dict], dict[str, str | None]]:
    if not archive.enabled:
        return [], {}
    return archive.index.list_rows(), archive.index.labels()


def _row_from_index(r: dict) -> dict:
    try:
        request = json.loads(r.get("request_json") or "null")
    except ValueError:
        request = None
    req = _request_fields(request)
    renamed = r.get("label")
    ens = r.get("ensemble_status")
    return {
        "id": r["id"],
        "label": clean_label(renamed) if renamed is not None else req.get("label"),
        "kind": r.get("kind"), "status": r.get("status") or "unknown", "archived": True,
        "_t": r.get("created_at") or r.get("finished_at") or 0.0,
        "created_at": _iso(r.get("created_at")), "finished_at": _iso(r.get("finished_at")),
        "start_time": req.get("start_time"),
        "ignition_lat": req.get("ignition_lat"), "ignition_lng": req.get("ignition_lng"),
        "area_ha": r.get("area_ha"), "duration_hours": r.get("duration_hours"),
        "frame_count": r.get("frame_count"), "days": r.get("days"),
        "fuel_source": r.get("fuel_source_label"), "buildings_source": r.get("buildings_source_label"),
        "ensemble": bool(ens) and ens not in ("none", "cancelled"), "ensemble_status": ens,
        "ensemble_members": req.get("ensemble_members"),
    }


def _row_from_memory(run, labels: dict[str, str | None]) -> dict:
    cfg = run.config
    request = cfg.model_dump(mode="json") if cfg is not None else None
    if getattr(run, "override_request", None) is not None:
        request = {"source_config": request, "perimeter_override": run.override_request}
    req = _request_fields(request)
    frames = run.get_frames()
    last = frames[-1] if frames else None
    days = {d for d, _ in run.frame_meta(len(frames)) if d is not None} if frames else set()
    fuel = run.fuel_source or {}
    bld = getattr(run, "buildings_source", None) or {}
    ens = run.ensemble_status
    return {
        "id": run.id,
        "label": clean_label(labels[run.id]) if run.id in labels else req.get("label"),
        "kind": run.kind, "status": _status(run.status), "archived": False,
        "_t": run.created_at,
        "created_at": _iso(run.created_at), "finished_at": _iso(run.finished_at),
        "start_time": req.get("start_time"),
        "ignition_lat": req.get("ignition_lat"), "ignition_lng": req.get("ignition_lng"),
        "area_ha": getattr(last, "area_ha", None), "duration_hours": getattr(last, "time_hours", None),
        "frame_count": len(frames), "days": len(days) or None,
        "fuel_source": fuel.get("label"), "buildings_source": bld.get("label"),
        "ensemble": bool(ens) and ens not in ("none", "cancelled"), "ensemble_status": ens,
        "ensemble_members": req.get("ensemble_members"),
    }


def _memory_runs() -> list:
    runner = sims.runner
    if runner is None:
        return []
    with runner._lock:  # the runner keeps its runs in insertion order (oldest first)
        return list(runner._runs.values())


def _all_rows() -> tuple[list[dict], bool]:
    archive = run_archive.get_archive()
    try:
        archived, labels = _archived_rows(archive)
    except Exception:  # noqa: BLE001 - an unreadable index lists memory only
        logger.warning("Saved runs: could not read the run index", exc_info=True)
        archived, labels = [], {}
    rows = [_row_from_index(r) for r in archived]
    seen = {r["id"] for r in rows}
    for run in _memory_runs():
        if run.id not in seen:
            try:
                rows.append(_row_from_memory(run, labels))
            except Exception:  # noqa: BLE001 - a run mid-update: skip it this time
                logger.debug("Saved runs: skipped in-memory run %s", run.id, exc_info=True)
    rows.sort(key=lambda r: r["_t"] or 0.0, reverse=True)
    return rows, archive.enabled


def _parse_time(value: str | None, name: str) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}: not an ISO 8601 time") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=SavedRunList)
async def list_runs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    since: str | None = Query(None, description="Runs created at or after (ISO 8601; no offset = UTC)"),
    until: str | None = Query(None, description="Runs created before (ISO 8601; no offset = UTC)"),
    south: float | None = Query(None, ge=-90, le=90),
    west: float | None = Query(None, ge=-180, le=180),
    north: float | None = Query(None, ge=-90, le=90),
    east: float | None = Query(None, ge=-180, le=180),
    kind: str | None = Query(None, description="single, multiday or perimeter_override"),
) -> SavedRunList:
    """Saved runs, newest first (archive plus runs in memory not archived yet). Needs the
    ``X-FireSim-Passphrase`` header."""
    require_passphrase(request)
    t0, t1 = _parse_time(since, "since"), _parse_time(until, "until")
    box = (south, west, north, east)
    if any(v is not None for v in box) and any(v is None for v in box):
        raise HTTPException(status_code=422, detail="the box needs south, west, north and east")
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(KINDS)}")

    rows, enabled = await run_in_threadpool(_all_rows)

    def keep(r: dict) -> bool:
        if kind is not None and r["kind"] != kind:
            return False
        t = r["_t"] or 0.0
        if t0 is not None and t < t0:
            return False
        if t1 is not None and t >= t1:
            return False
        if south is not None:
            lat, lng = r["ignition_lat"], r["ignition_lng"]
            if lat is None or lng is None or not (south <= lat <= north):
                return False
            # a box across the antimeridian has west > east
            if (west <= east and not (west <= lng <= east)) or (west > east and east < lng < west):
                return False
        return True

    matched = [r for r in rows if keep(r)]
    page = matched[offset:offset + limit]
    return SavedRunList(
        runs=[SavedRun(**{k: v for k, v in r.items() if k != "_t"}) for r in page],
        total=len(matched), limit=limit, offset=offset, archive_enabled=enabled)


def _known(sim_id: str) -> tuple[bool, bool]:
    """(in memory, in archive)."""
    in_memory = sims.runner is not None and sims.runner.get(sim_id) is not None
    archive = run_archive.get_archive()
    try:
        in_archive = archive.has(sim_id)
    except Exception:  # noqa: BLE001
        in_archive = False
    return in_memory, in_archive


@router.patch("/{sim_id}")
async def rename_run(sim_id: str, body: LabelUpdate, request: Request) -> dict:
    """Set or clear a run's label (archived or in memory). Needs the passphrase."""
    require_passphrase(request)
    if not run_archive.valid_id(sim_id):
        raise HTTPException(status_code=404, detail="Simulation not found")
    archive = run_archive.get_archive()
    if not archive.enabled:
        raise HTTPException(status_code=409, detail="Labels need the run archive (it is off)")
    in_memory, in_archive = await run_in_threadpool(_known, sim_id)
    if not (in_memory or in_archive):
        raise HTTPException(status_code=404, detail="Simulation not found")
    label = clean_label(body.label)
    await run_in_threadpool(archive.index.set_label, sim_id, label)
    return {"id": sim_id, "label": label}


@router.delete("/{sim_id}")
async def delete_run(sim_id: str, request: Request) -> dict:
    """Delete a finished run from the archive and memory. Needs the passphrase. A run still
    running is refused (409)."""
    require_passphrase(request)
    if not run_archive.valid_id(sim_id):
        raise HTTPException(status_code=404, detail="Simulation not found")
    run = sims.runner.get(sim_id) if sims.runner is not None else None
    # ``done`` is set once the run's thread (and its ensemble) ended and its archive job was
    # queued, so the delete below cannot be followed by a late archive write
    if run is not None and not run.done.is_set():
        raise HTTPException(status_code=409, detail="The run is still running; stop it first")
    archive = run_archive.get_archive()
    removed = False
    if archive.enabled:
        removed = await run_in_threadpool(archive.delete_run, sim_id)
    if run is not None:
        with sims.runner._lock:
            sims.runner._runs.pop(sim_id, None)
        removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="Simulation not found")
    logger.info("Saved runs: %s deleted", sim_id)
    return {"id": sim_id, "deleted": True}
