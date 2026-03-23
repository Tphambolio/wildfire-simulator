"""Simulation REST endpoints."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from firesim_api.schemas.simulation import (
    BurnProbabilityRequest,
    BurnProbabilityResponse,
    MultiDaySimulationCreate,
    PerimeterOverrideRequest,
    SimulationCreate,
    SimulationFrame as FrameSchema,
    SimulationResponse,
    SimulationStatus,
)
from firesim_api.services.runner import SimulationRunner
from firesim_api.ws.manager import ConnectionManager
from firesim.types import SimulationFrame

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])

# Shared state — injected from main app
runner: SimulationRunner | None = None
ws_manager: ConnectionManager | None = None


def _frame_to_schema(frame: SimulationFrame, day: int | None = None) -> FrameSchema:
    """Convert engine SimulationFrame to API schema."""
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
        burned_cells=frame.burned_cells,
        day=day,
    )


def _on_frame(sim_id: str, frame: SimulationFrame) -> None:
    """Callback from simulation thread — broadcast frame via WebSocket."""
    if ws_manager is None:
        return
    event = {
        "type": "simulation.frame",
        "simulation_id": sim_id,
        "frame": _frame_to_schema(frame).model_dump(),
    }
    ws_manager.broadcast_from_thread(sim_id, event)


def _on_multiday_frame(sim_id: str, frame: SimulationFrame, day: int) -> None:
    """Callback from multi-day simulation thread — broadcast frame with day tag."""
    if ws_manager is None:
        return
    event = {
        "type": "simulation.frame",
        "simulation_id": sim_id,
        "frame": _frame_to_schema(frame, day=day).model_dump(),
    }
    ws_manager.broadcast_from_thread(sim_id, event)


@router.post("", response_model=SimulationResponse)
async def create_simulation(params: SimulationCreate) -> SimulationResponse:
    """Start a new fire spread simulation."""
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    sim_id = runner.create(params, on_frame=_on_frame)

    return SimulationResponse(
        simulation_id=sim_id,
        status=SimulationStatus.RUNNING,
        config=params,
    )


@router.get("/{sim_id}", response_model=SimulationResponse)
async def get_simulation(sim_id: str) -> SimulationResponse:
    """Get simulation status and results."""
    if runner is None:
        raise HTTPException(status_code=500, detail="Runner not initialized")

    run = runner.get(sim_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation not found")

    frames = [_frame_to_schema(f) for f in run.get_frames()]

    # Only include config in response for single-day simulations
    config_out = run.config if isinstance(run.config, SimulationCreate) else None

    return SimulationResponse(
        simulation_id=run.id,
        status=run.status,
        config=config_out,
        frames=frames,
        error=run.error,
    )


@router.websocket("/ws/{sim_id}")
async def simulation_websocket(websocket: WebSocket, sim_id: str) -> None:
    """WebSocket endpoint for streaming simulation frames."""
    if ws_manager is None or runner is None:
        await websocket.close(code=1011)
        return

    run = runner.get(sim_id)
    if run is None:
        await websocket.close(code=4004, reason="Simulation not found")
        return

    await ws_manager.connect(sim_id, websocket)

    try:
        # Send any frames that already exist
        for frame in run.get_frames():
            await websocket.send_json({
                "type": "simulation.frame",
                "simulation_id": sim_id,
                "frame": _frame_to_schema(frame).model_dump(),
            })

        # If already done, send completion
        if run.status == SimulationStatus.COMPLETED:
            await websocket.send_json({
                "type": "simulation.completed",
                "simulation_id": sim_id,
            })
        elif run.status == SimulationStatus.FAILED:
            await websocket.send_json({
                "type": "simulation.error",
                "simulation_id": sim_id,
                "error": run.error,
            })

        # Keep connection alive; process control messages and wait for terminal state
        while run.status in (SimulationStatus.RUNNING, SimulationStatus.PAUSED):
            try:
                msg_text = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                try:
                    msg = json.loads(msg_text)
                    action = msg.get("action")
                    if action == "pause":
                        run.pause()
                        await websocket.send_json({"type": "status", "state": "paused"})
                    elif action == "resume":
                        run.resume()
                        await websocket.send_json({"type": "status", "state": "running"})
                    elif action == "cancel":
                        run.cancel()
                        await websocket.send_json({"type": "status", "state": "cancelled"})
                except (json.JSONDecodeError, AttributeError):
                    pass
            except asyncio.TimeoutError:
                continue

        # Send final event based on terminal state
        if run.status == SimulationStatus.COMPLETED:
            await websocket.send_json({
                "type": "simulation.completed",
                "simulation_id": sim_id,
            })
        elif run.status == SimulationStatus.CANCELLED:
            # Status already sent on cancel action; send final frame if available
            frames = run.get_frames()
            if frames:
                await websocket.send_json({
                    "type": "simulation.frame",
                    "simulation_id": sim_id,
                    "frame": _frame_to_schema(frames[-1]).model_dump(),
                })

    except WebSocketDisconnect:
        pass
    finally:
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

    sim_id = runner.create_multiday(params, on_frame=_on_multiday_frame)

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
        new_sim_id = runner.create_perimeter_override(req, on_frame=_on_frame)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SimulationResponse(
        simulation_id=new_sim_id,
        status=SimulationStatus.RUNNING,
        config=None,
    )


# ---------------------------------------------------------------------------
# Monte Carlo burn probability
# ---------------------------------------------------------------------------


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
    if not fuel_path:
        raise HTTPException(
            status_code=422,
            detail=(
                "Burn probability analysis requires a fuel grid. "
                "Supply fuel_grid_path in the request or set the "
                "FIRESIM_FUEL_GRID_PATH environment variable."
            ),
        )
    if not os.path.exists(fuel_path):
        raise HTTPException(
            status_code=422,
            detail=f"Fuel grid file not found: {fuel_path!r}",
        )

    # Load fuel grid (runs synchronously — large files may take a moment)
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

    # Load terrain grid for slope-adjusted spread (optional)
    terrain_grid = None
    dem_path = params.dem_path or settings.dem_path
    if dem_path:
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
