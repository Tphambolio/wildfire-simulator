"""Simulation REST endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from firesim_api.schemas.simulation import (
    BurnProbabilityRequest,
    BurnProbabilityResponse,
    ClassifyPerimeterRequest,
    MultiDaySimulationCreate,
    ObservedPerimeterRequest,
    PerimeterOverrideRequest,
    SimulationCreate,
    SimulationFrame as FrameSchema,
    SimulationResponse,
    SimulationStatus,
    WeatherUsed,
)
from firesim_api.services import archive as run_archive
from firesim_api.services.runner import SimulationRun, SimulationRunner
from firesim_api.ws.manager import ConnectionManager
from firesim.types import SimulationFrame

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])

# Shared state — injected from main app
runner: SimulationRunner | None = None
ws_manager: ConnectionManager | None = None


def _incremental(run) -> bool:
    """Whether the run streams only newly burned cells (grid runs that ask)."""
    return (isinstance(run.config, (SimulationCreate, MultiDaySimulationCreate))
            and run.config.cells_mode == "incremental")


def _offset(run, prev: SimulationFrame | None, frame: SimulationFrame) -> int:
    """How many earlier cells to leave out of ``frame``: the previous frame's cell count.
    Single runs: always (one grid run, cells ordered by arrival). Multi-day runs chain one
    run per day, so the offset is used only where the previous frame's cells are the start
    of this frame's; otherwise the frame is sent whole (0)."""
    if prev is None or not _incremental(run):
        return 0
    before = prev.burned_cells or []
    if isinstance(run.config, MultiDaySimulationCreate):
        cells = frame.burned_cells or []
        n = len(before)
        if n == 0 or len(cells) < n or cells[:n] != before:
            return 0
    return len(before)


def _cell_offsets(run, frames: list[SimulationFrame]) -> list[int]:
    """Per frame, how many earlier cells to leave out (the previous frame's cell count)."""
    return [_offset(run, frames[i - 1] if i else None, f) for i, f in enumerate(frames)]


def _frame_to_schema(
    frame: SimulationFrame, day: int | None = None, offset: int = 0,
    day_fwi: dict[str, float] | None = None,
) -> FrameSchema:
    """Convert engine SimulationFrame to API schema.

    ``offset`` > 0 sends only the cells after the first ``offset`` (frames' cells are
    ordered by arrival, so each frame's cells extend the previous frame's).
    """
    return FrameSchema(
        time_hours=frame.time_hours,
        perimeter=[[lat, lng] for lat, lng in frame.perimeter],
        area_ha=round(frame.area_ha, 2),
        head_ros_m_min=round(frame.head_ros_m_min, 2),
        max_hfi_kw_m=round(frame.max_hfi_kw_m, 1),
        fire_type=frame.fire_type.value,
        flame_length_m=round(frame.flame_length_m, 2),
        fuel_breakdown=frame.fuel_breakdown,
        spot_fires=frame.spot_fires,
        num_fronts=frame.num_fronts,
        burned_cells=frame.burned_cells[offset:] if offset and frame.burned_cells else frame.burned_cells,
        cells_offset=offset if frame.burned_cells else 0,
        head=frame.head,
        day=day,
        day_fwi=day_fwi,
        buildings_at_risk=frame.buildings_at_risk,
        ignition_snapped_m=round(frame.ignition_snapped_m, 1),
        building_exposure=frame.building_exposure,
        building_exposure_detail=frame.building_exposure_detail,
        structure_spread=getattr(frame, "structure_spread", None),
        structure_spread_detail=getattr(frame, "structure_spread_detail", None),
        grid=getattr(frame, "grid", None),
    )


def _on_frame(sim_id: str, frame: SimulationFrame) -> None:
    """Callback from the simulation thread: the run has a new frame. The WebSocket handler
    sends it (in order, from the run; ws/manager.py), so nothing is serialised here."""
    if ws_manager is not None:
        ws_manager.frames_added_from_thread(sim_id)


def _status_event(sim_id: str, phase: str, progress: float | None,
                  day: int | None = None, n_days: int | None = None) -> dict:
    """Small progress message (display only): phase in RUN_PHASES, progress 0-1 or None;
    multi-day runs add the day being computed and the number of days."""
    event = {
        "type": "simulation.status",
        "simulation_id": sim_id,
        "phase": phase,
        "progress": None if progress is None else round(progress, 3),
    }
    if n_days is not None:
        event["day"] = day
        event["n_days"] = n_days
    return event


def _run_status_event(run: SimulationRun) -> dict:
    """The run's current status message (late join, heartbeat)."""
    return _status_event(run.id, run.phase or "loading",
                         run.progress if run.phase == "spread" else None, run.day, run.n_days)


def _on_status(sim_id: str, phase: str, progress: float | None) -> None:
    """Callback from the simulation thread: broadcast the run's phase and progress."""
    if ws_manager is None:
        return
    run = runner.get(sim_id) if runner is not None else None
    day, n_days = (run.day, run.n_days) if run is not None else (None, None)
    ws_manager.broadcast_from_thread(sim_id, _status_event(sim_id, phase, progress, day, n_days))


def _on_multiday_frame(sim_id: str, frame: SimulationFrame, day: int) -> None:
    """Callback from the multi-day thread: new frame (sent by the WebSocket handler with its
    day tag and the day's FWI codes)."""
    if ws_manager is not None:
        ws_manager.frames_added_from_thread(sim_id)


@router.post("", response_model=SimulationResponse)
async def create_simulation(params: SimulationCreate) -> SimulationResponse:
    """Start a new fire spread simulation."""
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    sim_id = runner.create(params, on_frame=_on_frame, on_status=_on_status)

    return SimulationResponse(
        simulation_id=sim_id,
        status=SimulationStatus.RUNNING,
        config=params,
    )


@router.get("/fuel-grid-image")
async def fuel_grid_image(fuel_grid_path: str) -> dict:
    """Return a base64-encoded PNG of the fuel grid with WGS84 bounds.

    Used by the frontend to render a raster overlay on the map showing
    which FBP fuel types are present and where.

    Returns JSON with: image (base64 PNG), bounds [west, south, east, north]
    """
    import base64
    import io
    import os

    import numpy as np
    from fastapi.responses import JSONResponse
    from firesim_api.settings import settings

    path = fuel_grid_path or settings.fuel_grid_path
    if not path:
        raise HTTPException(status_code=422, detail="No fuel_grid_path supplied")
    if not os.path.exists(path):
        raise HTTPException(status_code=422, detail=f"Fuel grid not found: {path!r}")

    try:
        import rasterio
        from rasterio.warp import transform_bounds
        from PIL import Image as PILImage

        from firesim.data.fuel_loader import ALL_CODES, _detect_code_map, normalize_fuel_codes

        with rasterio.open(path) as src:
            # Decimated read for large rasters (e.g. the provincial grid, 7856 x 15002 cells):
            # the image is at most 512 px anyway, and a full read would need ~240 MB
            step = max(1, max(src.width, src.height) // 2048)
            data = src.read(1, out_shape=(max(1, src.height // step), max(1, src.width // step)))
            nodata = src.nodata
            src_crs = src.crs
            src_bounds = src.bounds

        lng_min, lat_min, lng_max, lat_max = transform_bounds(
            src_crs, "EPSG:4326",
            src_bounds.left, src_bounds.bottom,
            src_bounds.right, src_bounds.top,
        )

        data = normalize_fuel_codes(data, nodata, fill=0)

        # Downsample to max 512px on longest side for a lightweight overlay
        rows, cols = data.shape
        scale = min(512 / max(rows, cols), 1.0)
        if scale < 1.0:
            from scipy.ndimage import zoom as ndimage_zoom
            data = ndimage_zoom(data, scale, order=0)
            rows, cols = data.shape

        unique_codes = set(np.unique(data).tolist())
        code_map = _detect_code_map(unique_codes)

        rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
        present: set[str] = set()
        for r_code, fuel_type in code_map.items():
            if fuel_type is None:
                continue
            colour = _FUEL_COLOURS.get(fuel_type.value)
            if colour is None:
                continue
            mask = data == r_code
            if mask.any():
                present.add(fuel_type.value)
            rgba[mask, 0] = colour[0]
            rgba[mask, 1] = colour[1]
            rgba[mask, 2] = colour[2]
            rgba[mask, 3] = 200  # Semi-transparent

        img = PILImage.fromarray(rgba, "RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode()

        return JSONResponse({
            "image": f"data:image/png;base64,{encoded}",
            "bounds": [lng_min, lat_min, lng_max, lat_max],
            "fuel_grid_path": path,
            # Fuel types drawn in this image, in table order, for the map legend
            "legend": [
                {"fuel": code, "color": "#%02x%02x%02x" % rgb}
                for code, rgb in _FUEL_COLOURS.items() if code in present
            ],
        })

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to render fuel grid: {exc}") from exc


def _response_for(run: SimulationRun) -> SimulationResponse:
    """The GET body of an in-memory run (also what the run archive stores)."""
    # Read the status before the frames: the run thread adds its last frames and then sets
    # the terminal status, so a "completed" read first guarantees every frame is in the list.
    # (Read after, a run finishing while the frames are serialised was reported "completed"
    # with frames missing; the client then stops polling. Seen in CI 2026-10-11.)
    status, error, phase, progress = run.status, run.error, run.phase, run.progress
    engine_frames = run.get_frames()
    # Multi-day runs: each frame's day and that day's FWI codes (were dropped here, so a
    # polling client lost the day tags; QA 2026-10-11)
    frames = [
        _frame_to_schema(f, offset=o, day=d, day_fwi=fwi)
        for f, o, (d, fwi) in zip(engine_frames, _cell_offsets(run, engine_frames),
                                  run.frame_meta(len(engine_frames)))
    ]

    # Only include config in response for single-day simulations (a restart: the request it
    # ran as, with its own weather)
    config_out = run.config if isinstance(run.config, SimulationCreate) else None

    return SimulationResponse(
        simulation_id=run.id,
        status=status,
        config=config_out,
        frames=frames,
        error=error,
        phase=phase,
        progress=progress,
        day=run.day if run.n_days is not None else None,
        n_days=run.n_days,
        fuel_source=run.fuel_source,
        buildings_source=getattr(run, "buildings_source", None),
        terrain_source=getattr(run, "terrain_source", None),
        weather_used=_weather_used(run),
        fuel_vintage_warning=_vintage_warning(run),
        active_segments=getattr(run, "active_segments", None),
    )


def _vintage_warning(run: SimulationRun) -> str | None:
    """Fuel-grid vintage flag of a run (owner field test 2026-10-12, issue 6)."""
    from firesim_api.services.runner import fuel_vintage_warning

    cfg = run.config
    start = getattr(cfg, "start_time", None)
    fs = run.fuel_source if isinstance(run.fuel_source, dict) else {}
    warning = fuel_vintage_warning(fs.get("vintage_year"), start)
    bc = fs.get("backcast") if isinstance(fs.get("backcast"), dict) else None
    if bc and bc.get("applied"):
        # Pre-fire back-cast applied (docs/fuel-grid-backcast.md): say so instead
        return bc.get("note") or warning
    if warning and bc and bc.get("note"):
        return f"{warning} {bc['note']}"
    return warning


def _weather_used(run: SimulationRun) -> WeatherUsed | None:
    """The weather a single run or restart used (None for multi-day runs)."""
    cfg = run.config
    if not isinstance(cfg, SimulationCreate):
        return None
    return WeatherUsed(
        source=getattr(run, "weather_source", "request"),
        weather=cfg.weather,
        fwi_overrides=cfg.fwi_overrides,
        weather_schedule=cfg.weather_schedule,
        hourly_weather=bool(cfg.hourly_weather),
        start_time=cfg.start_time,
    )


def _archived(load: str, sim_id: str):
    """Read one payload from the run archive (None when absent, disabled or unreadable)."""
    try:
        return getattr(run_archive.get_archive(), load)(sim_id)
    except Exception:  # noqa: BLE001 - an archive outage reads as "not found"
        logger.warning("Run archive: could not read %s for %s", load, sim_id, exc_info=True)
        return None


@router.get("/{sim_id}", response_model=SimulationResponse)
async def get_simulation(sim_id: str) -> SimulationResponse:
    """Get simulation status and results: from memory, else from the run archive
    (``archived: true``; the body is the one the live GET gave when the run finished)."""
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    run = runner.get(sim_id)
    if run is not None:
        return _response_for(run)
    stored = await run_in_threadpool(_archived, "load_response", sim_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    resp = SimulationResponse.model_validate(stored)
    resp.archived = True
    return resp


def _arrival_for(run: SimulationRun) -> dict | None:
    """``/arrival`` body of an in-memory run, or None when it has no arrival grid."""
    import base64

    import numpy as np

    frames = run.get_frames()
    raster = next((f.arrival_raster for f in reversed(frames) if f.arrival_raster), None)
    if raster is None:
        return None
    minutes = np.asarray(raster["minutes"], dtype="<i2")
    return {
        "rows": raster["rows"], "cols": raster["cols"],
        "lat_min": raster["lat_min"], "lat_max": raster["lat_max"],
        "lng_min": raster["lng_min"], "lng_max": raster["lng_max"],
        "encoding": "int16-le-base64",
        "minutes": base64.b64encode(minutes.tobytes()).decode(),
    }


@router.get("/{sim_id}/arrival")
async def get_arrival(sim_id: str) -> dict:
    """Arrival time of the fire at each fuel-grid cell (grid runs, once complete).

    ``minutes`` is base64 of little-endian int16, row-major from the north-west corner,
    whole minutes after ignition, -1 where the cell did not burn. Runs no longer in memory
    are read from the run archive.
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")
    run = runner.get(sim_id)
    if run is None:
        out = await run_in_threadpool(_archived, "load_arrival", sim_id)
        if out is None:
            if await run_in_threadpool(_archived, "has", sim_id):
                raise HTTPException(status_code=404, detail=(
                    "No arrival grid: the run is not finished or did not use a fuel grid"))
            raise HTTPException(status_code=404, detail="Simulation not found")
        return out
    out = _arrival_for(run)
    if out is None:
        raise HTTPException(
            status_code=404,
            detail="No arrival grid: the run is not finished or did not use a fuel grid",
        )
    return out


def _ensemble_for(run: SimulationRun) -> dict | None:
    """``/ensemble`` body of an in-memory run; None when it requested no ensemble."""
    import base64

    import numpy as np

    if run.ensemble_status is None:
        cfg = run.config if isinstance(run.config, SimulationCreate) else None
        if cfg is None or cfg.ensemble is None:
            return None
        return {"status": "pending", "done": 0, "total": cfg.ensemble.n_members}
    done, total = run.ensemble_progress
    out: dict = {"status": run.ensemble_status, "done": done, "total": total,
                 "error": run.ensemble_error}
    res = run.ensemble
    if run.ensemble_status == "completed" and res is not None:
        areas = sorted(m["area_ha"] for m in res.members)
        out.update({
            "rows": res.rows, "cols": res.cols, "lat_min": res.lat_min, "lat_max": res.lat_max,
            "lng_min": res.lng_min, "lng_max": res.lng_max, "duration_minutes": res.duration_minutes,
            "encoding": "int16-le-base64",
            "arrival": {
                f"p{q}": base64.b64encode(np.asarray(a, dtype="<i2").tobytes()).decode()
                for q, a in res.arrival.items()
            },
            "burn_probability": base64.b64encode(
                np.rint(res.burn_probability * 100).astype(np.uint8).tobytes()
            ).decode(),
            "area_ha": {
                "min": areas[0], "p50": areas[len(areas) // 2], "max": areas[-1],
            },
            "members": res.members,
            "members_ok": len(res.members),
            "members_failed": len(res.failed),
            "failed": res.failed,
            "note": "Perturbation sizes are defaults until calibrated on observed fires.",
        })
    return out


@router.get("/{sim_id}/ensemble")
async def get_ensemble(sim_id: str) -> dict:
    """Ensemble status and, once complete, arrival percentiles and burn probability.

    ``arrival`` holds P10/P50/P90 rasters: base64 little-endian int16, row-major from the
    north-west corner, whole minutes after ignition, -1 where fewer than that share of
    members reached the cell. P10 is the early end of the modelled range (reached by 1 in 10
    members), not a worst case (docs/validation.md "Ensemble calibration").
    ``burn_probability`` is base64 uint8 percent (0-100).
    Statistics are over the members that finished (``members_ok``); members that raised are
    left out and listed in ``failed`` (count ``members_failed``). Members that burned nothing
    are finished members with 0 ha and count in every statistic. The ensemble fails when
    fewer than half the members finish.
    Runs no longer in memory are read from the run archive.
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")
    run = runner.get(sim_id)
    if run is None:
        out = await run_in_threadpool(_archived, "load_ensemble", sim_id)
        if out is None:
            if await run_in_threadpool(_archived, "has", sim_id):
                raise HTTPException(status_code=404, detail="No ensemble requested for this run")
            raise HTTPException(status_code=404, detail="Simulation not found")
        return out
    out = _ensemble_for(run)
    if out is None:
        raise HTTPException(status_code=404, detail="No ensemble requested for this run")
    return out


@router.delete("/{sim_id}/ensemble")
async def cancel_ensemble(sim_id: str) -> dict:
    """Stop a run's ensemble: before its next member, or skip it if it has not started.

    The app calls this when it starts a new run (or clears the results) while the previous
    run's ensemble is still pending or running, so the abandoned ensemble does not take the
    CPU from the new run. A finished ensemble is left as it is. 404 when the run is unknown
    or has no ensemble. Returns the ensemble status after the request.
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")
    run = runner.get(sim_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    cfg = run.config if isinstance(run.config, SimulationCreate) else None
    if cfg is None or cfg.ensemble is None:
        raise HTTPException(status_code=404, detail="No ensemble requested for this run")
    if run.ensemble_status not in ("completed", "failed"):
        run.cancel_ensemble()
    return {"simulation_id": sim_id, "status": run.ensemble_status or "pending",
            "cancel_requested": run.ensemble_status not in ("completed", "failed")}


_LIVE_STATUSES = (SimulationStatus.PENDING, SimulationStatus.RUNNING, SimulationStatus.PAUSED)

#: Seconds of silence on a running run's socket before the handler re-sends the run's status
#: (keeps the connection alive through proxies during long computations; round-3 field test R6)
HEARTBEAT_S = 15.0


def _frame_events(run: SimulationRun, sim_id: str, start: int) -> tuple[list[str], int]:
    """JSON texts of the run's frames from index ``start`` on, and the new frame count.
    Runs on a worker thread (large grid frames take a while to serialise)."""
    frames = run.get_frames()
    if start >= len(frames):
        return [], start
    offsets = _cell_offsets(run, frames)
    meta = run.frame_meta(len(frames))
    out = []
    for i in range(start, len(frames)):
        day, day_fwi = meta[i]
        frame = _frame_to_schema(frames[i], offset=offsets[i], day=day, day_fwi=day_fwi)
        out.append(json.dumps({"type": "simulation.frame", "simulation_id": sim_id,
                               "frame": frame.model_dump()}))
    return out, len(frames)


async def _read_controls(websocket: WebSocket, run: SimulationRun, queue: asyncio.Queue) -> None:
    """Pause / resume / cancel from the client; replies go through the handler's queue."""
    try:
        while True:
            text = await websocket.receive_text()
            try:
                action = json.loads(text).get("action")
            except (json.JSONDecodeError, AttributeError):
                continue
            if action == "pause":
                run.pause()
                queue.put_nowait({"type": "status", "state": "paused"})
            elif action == "resume":
                run.resume()
                queue.put_nowait({"type": "status", "state": "running"})
            elif action == "cancel":
                run.cancel()
                queue.put_nowait({"type": "status", "state": "cancelled"})
    except (WebSocketDisconnect, RuntimeError):
        queue.put_nowait(None)  # the client left: stop the handler


@router.websocket("/ws/{sim_id}")
async def simulation_websocket(websocket: WebSocket, sim_id: str) -> None:
    """WebSocket endpoint for streaming simulation frames.

    This handler is the socket's only writer (ws/manager.py). It sends the run's frames in
    order, each exactly once, read from the run itself; status messages and control replies
    come through its queue. The terminal event (``simulation.completed`` with ``n_frames``,
    or ``simulation.error``) is sent only after every frame, read after the status, so a
    finished run's last frames always arrive before it.
    """
    if ws_manager is None or runner is None:
        await websocket.close(code=1011)
        return

    run = runner.get(sim_id)
    if run is None:
        await websocket.close(code=4004, reason="Simulation not found")
        return

    queue = await ws_manager.connect(sim_id, websocket)
    reader = asyncio.create_task(_read_controls(websocket, run, queue))
    sent = 0
    try:
        # A client joining a run still computing gets its current phase first
        if run.status == SimulationStatus.RUNNING and run.phase is not None:
            await websocket.send_json(_run_status_event(run))
        last_sent = time.monotonic()
        while True:
            # Status before frames (see get_simulation): a terminal status read first means
            # every frame is already in the run
            status = run.status
            texts, sent = await run_in_threadpool(_frame_events, run, sim_id, sent)
            for text in texts:
                await websocket.send_text(text)
            if texts:
                last_sent = time.monotonic()
            if status not in _LIVE_STATUSES:
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # Heartbeat: a long computation (a multi-day run's day, a large grid run)
                # can leave the socket silent for minutes, and proxies close idle sockets.
                # The client keeps the last status; old clients ignore the repeat.
                if time.monotonic() - last_sent >= HEARTBEAT_S and status == SimulationStatus.RUNNING:
                    await websocket.send_json(_run_status_event(run))
                    last_sent = time.monotonic()
                continue
            last_sent = time.monotonic()
            if event is None:
                return  # client disconnected
            if event.get("type") != "_frames_added":
                await websocket.send_json(event)
            # anything else queued meanwhile: status and control replies in order
            while not queue.empty():
                event = queue.get_nowait()
                if event is None:
                    return
                if event.get("type") != "_frames_added":
                    await websocket.send_json(event)

        while not queue.empty():  # control replies (e.g. "cancelled") before the end event
            event = queue.get_nowait()
            if event is not None and event.get("type") not in ("_frames_added",
                                                                 "simulation.status"):
                await websocket.send_json(event)
        if status == SimulationStatus.COMPLETED:
            await websocket.send_json({
                "type": "simulation.completed",
                "simulation_id": sim_id,
                "n_frames": sent,
                "fuel_vintage_warning": _vintage_warning(run),
                "fuel_source": run.fuel_source,
                "buildings_source": getattr(run, "buildings_source", None),
                "terrain_source": getattr(run, "terrain_source", None),
                "active_segments": getattr(run, "active_segments", None),
            })
        elif status == SimulationStatus.FAILED:
            await websocket.send_json({
                "type": "simulation.error",
                "simulation_id": sim_id,
                "error": run.error,
            })

    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        await ws_manager.disconnect(sim_id, websocket)


# ---------------------------------------------------------------------------
# Multi-day scenario
# ---------------------------------------------------------------------------


@router.post("/multiday", response_model=SimulationResponse)
async def create_multiday_simulation(params: MultiDaySimulationCreate) -> SimulationResponse:
    """Start a multi-day fire scenario (24h/48h/72h).

    Chains one 24-hour Huygens spread simulation per day. FWI moisture codes
    (FFMC/DMC/DC) carry forward between days using CFFDRS daily equations.
    The fire perimeter from the end of each day seeds the next day's front.

    Returns a simulation_id immediately; connect to /ws/{sim_id} for streaming.
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    sim_id = runner.create_multiday(params, on_frame=_on_multiday_frame, on_status=_on_status)

    return SimulationResponse(
        simulation_id=sim_id,
        status=SimulationStatus.RUNNING,
        config=None,
    )


# ---------------------------------------------------------------------------
# Perimeter override (drone reconnaissance correction)
# ---------------------------------------------------------------------------


@router.post("/perimeter-override", response_model=SimulationResponse)
async def create_perimeter_override(req: PerimeterOverrideRequest) -> SimulationResponse:
    """Override simulated fire perimeter with drone reconnaissance data.

    Accepts a GeoJSON Polygon or MultiPolygon geometry representing the actual
    fire extent observed by drone, replaces the model-predicted front, and
    runs a new Huygens spread prediction from that corrected initial state.

    The new simulation inherits the original simulation's weather, fuel grid,
    terrain, WUI zones, and spotting configuration.  Connect to
    ``/ws/{simulation_id}`` for real-time frame streaming.

    Returns the new simulation_id immediately (status ``running``).
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    try:
        new_sim_id = runner.create_perimeter_override(req, on_frame=_on_frame, on_status=_on_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SimulationResponse(
        simulation_id=new_sim_id,
        status=SimulationStatus.RUNNING,
        config=None,
    )


@router.post("/observed-perimeter", response_model=SimulationResponse)
async def create_observed_perimeter(req: ObservedPerimeterRequest) -> SimulationResponse:
    """Standalone forecast from an observed perimeter at observation time T (A1, R13/R14).

    No source run: the body is a normal run request plus ``perimeter_geojson`` and
    ``observation_time`` (required), with the optional active-segment classification
    (manual ``active_edges``, RPAS ``hotspots_geojson`` or a FIRMS ``firms_csv``) and the
    held-line hold ``hold_hours``. Returns the new simulation_id at once (status running).
    """
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")
    try:
        sim_id = runner.create_observed_perimeter(req, on_frame=_on_frame, on_status=_on_status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run = runner.get(sim_id)
    return SimulationResponse(simulation_id=sim_id, status=SimulationStatus.RUNNING, config=None,
                              active_segments=run.active_segments if run else None)


@router.post("/classify-perimeter")
async def classify_perimeter(req: ClassifyPerimeterRequest) -> dict:
    """Active / held segments of an observed perimeter from RPAS hotspots or a FIRMS VIIRS CSV
    (no run). Returns ``summary``, ``active_edges`` (MultiLineString or null) and ``segments``
    (GeoJSON FeatureCollection, property ``status``). Rule: docs/active-segments.md."""
    from firesim.active_segments import classify_request

    try:
        c = await run_in_threadpool(
            classify_request, req.perimeter_geojson, req.observation_time, req.hotspots_geojson,
            req.firms_csv, req.classification_radius_m, req.classification_window_h,
            req.classification_segment_m)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"summary": {**c.summary(), "radius_m": req.classification_radius_m,
                        "window_h": req.classification_window_h,
                        "segment_m": req.classification_segment_m},
            "active_edges": c.active_edges(), "segments": c.feature_collection()}


# ---------------------------------------------------------------------------
# Monte Carlo burn probability
# ---------------------------------------------------------------------------


def _foliar_moisture(params: BurnProbabilityRequest) -> float:
    """FMC override, else ST-X-3 model from ignition point and date, else 100 %."""
    mods = params.fuel_modifiers
    if mods.fmc is not None:
        return mods.fmc
    if mods.day_of_year is not None:
        from firesim.fbp.calculator import calculate_foliar_moisture

        return calculate_foliar_moisture(
            params.ignition_lat, params.ignition_lng, mods.elevation_m, mods.day_of_year
        )
    return 100.0


# The route decorator sat on _foliar_moisture from 2026-10-05 (289a1d0) to 2026-10-11, so the
# endpoint returned a validation error; it belongs here.
@router.post("/burn-probability", response_model=BurnProbabilityResponse)
async def compute_burn_probability(params: BurnProbabilityRequest) -> BurnProbabilityResponse:
    """Run Monte Carlo burn probability analysis.

    Runs N iterations of the CA fire spread model with varied ignition points
    and weather inputs to produce a probabilistic burn probability map.
    Requires a fuel grid (from fuel_grid_path or FIRESIM_FUEL_GRID_PATH env var).

    Returns a 2D burn probability array where each cell value is
    P(burned) = iterations_burned / iterations_completed ∈ [0, 1].
    """
    import os

    from firesim.data.fuel_loader import load_fuel_grid
    from firesim.spread.huygens import SpreadConditions
    from firesim.spread.montecarlo import BurnProbabilityResult, MonteCarloConfig, run_monte_carlo
    from firesim_api.settings import settings

    # Resolve fuel grid path (explicit > env var)
    fuel_path = params.fuel_grid_path or settings.fuel_grid_path

    # Default fuel (City grid where mapped, Alberta Wildfire grid elsewhere; docs/fuel-grid-alberta.md)
    provincial_grid = None
    provincial_terrain = None  # (terrain grid, terrain_source) for a provincial window
    if runner is not None and fuel_path and runner.provincial_applies(
            params.model_copy(update={"fuel_grid_path": fuel_path})):
        from firesim.data.environment import load_environment_mask
        from firesim_api.services.runner import run_day_of_year

        def _load_provincial():
            nonlocal provincial_terrain
            grid, _, _, _, info = runner.resolve_provincial(
                params, run_day_of_year(params), None, None,
                params.water_path or settings.water_path)
            # Windows outside Edmonton: slope from the Alberta elevation file when the request
            # names the bundled DEM (docs/terrain-alberta.md)
            dem = params.dem_path or settings.dem_path
            if grid is not None and info["kind"] in ("provincial", "national") and dem:
                provincial_terrain = runner._window_terrain(grid, dem)
            bpath = runner.resolve_buildings_path(
                params.buildings_path or settings.buildings_path)
            if grid is not None and bpath:
                import dataclasses

                mask = load_environment_mask(
                    bounds=(grid.lat_min, grid.lat_max, grid.lng_min, grid.lng_max),
                    rows=grid.rows, cols=grid.cols, buildings_path=bpath)
                grid = dataclasses.replace(grid, fuel_types=[
                    [None if mask[r, c] else ft for c, ft in enumerate(row)]
                    for r, row in enumerate(grid.fuel_types)])
            return grid

        provincial_grid = await asyncio.get_event_loop().run_in_executor(None, _load_provincial)
        if provincial_grid is None:
            raise HTTPException(status_code=422, detail=(
                "Burn probability needs a fuel grid; no fuel grid covers this ignition "
                "(outside Alberta)"))

    if fuel_path and not os.path.exists(fuel_path) and provincial_grid is None:
        raise HTTPException(
            status_code=422,
            detail=f"Fuel grid file not found: {fuel_path!r}",
        )

    if provincial_grid is not None:
        fuel_grid = provincial_grid
    elif fuel_path:
        # Load real fuel grid from file
        try:
            water_path = params.water_path or settings.water_path
            buildings_path = params.buildings_path or settings.buildings_path
            fuel_grid = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: load_fuel_grid(
                    fuel_path,
                    water_path=water_path,
                    buildings_path=buildings_path,
                ),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load fuel grid: {exc}") from exc
    else:
        # No fuel grid available — generate a synthetic landscape centred on ignition
        from firesim.data.synthetic_grid import generate_synthetic_fuel_grid

        fuel_grid = generate_synthetic_fuel_grid(
            ignition_lat=params.ignition_lat,
            ignition_lng=params.ignition_lng,
            radius_km=5.0,
            cell_size_m=50.0,
        )
        logger.info(
            "Burn probability: synthetic fuel grid generated (%dx%d) around (%.4f, %.4f)",
            fuel_grid.rows, fuel_grid.cols,
            params.ignition_lat, params.ignition_lng,
        )

    # Load terrain grid for slope-adjusted spread (optional)
    terrain_grid = None
    dem_path = params.dem_path or settings.dem_path
    if provincial_terrain is not None:
        terrain_grid = provincial_terrain[0]
    elif dem_path:
        if not os.path.exists(dem_path):
            raise HTTPException(status_code=422, detail=f"DEM file not found: {dem_path!r}")
        try:
            from firesim.data.dem_loader import load_terrain_grid
            terrain_grid = await asyncio.get_event_loop().run_in_executor(
                None, lambda: load_terrain_grid(dem_path)
            )
            logger.info(
                "Burn probability DEM loaded: %dx%d terrain grid",
                terrain_grid.rows, terrain_grid.cols,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to load DEM: {exc}") from exc

    fwi = params.fwi_overrides
    conditions = SpreadConditions(
        wind_speed=params.weather.wind_speed,
        wind_direction=params.weather.wind_direction,
        ffmc=fwi.ffmc if fwi and fwi.ffmc is not None else 85.0,
        dmc=fwi.dmc if fwi and fwi.dmc is not None else 40.0,
        dc=fwi.dc if fwi and fwi.dc is not None else 200.0,
        pc=params.fuel_modifiers.percent_conifer,
        grass_cure=params.fuel_modifiers.grass_cure,
        pdf=params.fuel_modifiers.percent_dead_fir,
        gfl=params.fuel_modifiers.grass_fuel_load,
        fmc=_foliar_moisture(params),
    )

    mc_config = MonteCarloConfig(
        ignition_lat=params.ignition_lat,
        ignition_lng=params.ignition_lng,
        duration_hours=params.duration_hours,
        n_iterations=params.n_iterations,
        jitter_m=params.jitter_m,
        wind_speed_pct=params.wind_speed_pct,
        rh_abs=params.rh_abs,
        base_seed=params.base_seed,
    )

    # Run Monte Carlo in a thread (CPU-bound)
    result: BurnProbabilityResult = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: run_monte_carlo(mc_config, fuel_grid, conditions, terrain_grid=terrain_grid),
    )

    return BurnProbabilityResponse(
        burn_probability=result.burn_probability,
        rows=result.rows,
        cols=result.cols,
        lat_min=result.lat_min,
        lat_max=result.lat_max,
        lng_min=result.lng_min,
        lng_max=result.lng_max,
        n_iterations=result.n_iterations,
        iterations_completed=result.iterations_completed,
        cell_size_m=result.cell_size_m,
    )


# ---------------------------------------------------------------------------
# Fuel grid raster image (for map overlay)
# ---------------------------------------------------------------------------

# NOTE: The /fuel-grid-image GET route is registered above /{sim_id} to
# prevent FastAPI from matching "fuel-grid-image" as a sim_id path param.

# FBP fuel type → overlay colour. Greens for conifer, browns for deciduous and mixedwood,
# khaki for grass, greys for slash: kept away from the yellow-orange-red used for fire,
# intensity and evacuation zones so the fire stays readable on top of the fuel map.
_FUEL_COLOURS: dict[str, tuple[int, int, int]] = {
    "C1":  (0,   104,  55),   # Dark green
    "C2":  (0,   128,   0),   # Green
    "C3":  (34,  139,  34),   # Forest green
    "C4":  (85,  107,  47),   # Dark olive
    "C5":  (107, 142,  35),   # Olive
    "C6":  (154, 205,  50),   # Yellow-green
    "C7":  (173, 255,  47),   # Chartreuse
    "D1":  (139, 115,  85),   # Muted brown
    "D2":  (158, 142, 104),   # Muted tan
    "M1":  (210, 180, 140),   # Tan
    "M2":  (188, 143, 143),   # Rosy brown
    "M3":  (128,   0, 128),   # Purple
    "M4":  (186,  85, 211),   # Medium orchid
    "O1a": (189, 183, 107),   # Dark khaki
    "O1b": (143, 151,  89),   # Olive khaki
    "S1":  (112, 128, 144),   # Slate grey
    "S2":  (96,   96, 110),   # Grey
    "S3":  (70,   70,  80),   # Dark grey
}


# ---------------------------------------------------------------------------
# Run archive (services/archive.py): finished runs survive deploys and restarts
# ---------------------------------------------------------------------------


def _archive_meta(run: SimulationRun, resp: SimulationResponse) -> dict:
    """Index row fields for a finished run (request, sources, summary; no personal data)."""
    import os

    cfg = run.config
    request = cfg.model_dump(mode="json") if cfg is not None else None
    if run.override_request is not None:
        # The restart's own request (perimeter, weather, schedule), the request it ran as
        # (source run's + restart weather; restart_config) and the source run's request
        src = getattr(run, "source_config", None)
        request = {"source_config": src.model_dump(mode="json") if src is not None else None,
                   "perimeter_override": run.override_request,
                   "restart_config": request,
                   "weather_source": getattr(run, "weather_source", "request")}
    frames = resp.frames
    last = frames[-1] if frames else None
    days = sorted({f.day for f in frames if f.day is not None})
    fuel = run.fuel_source or {}
    bld = getattr(run, "buildings_source", None) or {}
    return {
        "kind": run.kind,
        "status": resp.status.value if hasattr(resp.status, "value") else str(resp.status),
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "request_json": json.dumps(request, separators=(",", ":")) if request is not None else None,
        "git_sha": os.environ.get("FIRESIM_GIT_SHA") or "unknown",
        "fuel_source_kind": fuel.get("kind"),
        "fuel_source_label": fuel.get("label"),
        "buildings_source_kind": bld.get("kind"),
        "buildings_source_label": bld.get("label"),
        "area_ha": last.area_ha if last is not None else None,
        "duration_hours": last.time_hours if last is not None else None,
        "frame_count": len(frames),
        "days": len(days) or None,
        "ensemble_status": run.ensemble_status,
    }


def _archive_payload(run: SimulationRun) -> dict:
    """Everything the archive stores for a run: built on the archive's worker thread."""
    resp = _response_for(run)
    return {
        "meta": _archive_meta(run, resp),
        "response": resp.model_dump(mode="json"),
        "arrival": _arrival_for(run),
        "ensemble": _ensemble_for(run),
    }


def archive_finished_run(run: SimulationRun) -> None:
    """``SimulationRunner.on_finished``: queue a finished run for the archive (returns at once;
    an archive failure is logged and never reaches the run)."""
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    if not run_archive.is_terminal(status):
        logger.warning("Run archive: %s ended with status %s; not archived", run.id, status)
        return
    run_archive.get_archive().submit(run.id, lambda: _archive_payload(run))
