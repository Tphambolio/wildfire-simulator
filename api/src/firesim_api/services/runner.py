"""Simulation runner service.

Manages simulation lifecycle: creation, execution, and result storage.
Simulations run in background threads and stream frames via callbacks.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Callable

from firesim.fbp.constants import FuelType
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, SimulationFrame, WeatherInput

from firesim_api.schemas.simulation import (
    SimulationCreate,
    SimulationStatus,
)

logger = logging.getLogger(__name__)


class SimulationRun:
    """Tracks state of a single simulation run."""

    def __init__(self, sim_id: str, config: SimulationCreate):
        self.id = sim_id
        self.config = config
        self.status: SimulationStatus = SimulationStatus.PENDING
        self.frames: list[SimulationFrame] = []
        self.error: str | None = None
        self._lock = threading.Lock()

    def add_frame(self, frame: SimulationFrame) -> None:
        with self._lock:
            self.frames.append(frame)

    def get_frames(self) -> list[SimulationFrame]:
        with self._lock:
            return list(self.frames)


class SimulationRunner:
    """Manages simulation runs.

    Stores active and completed simulations in memory.
    In a production system this would use a database.
    """

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}
        self._lock = threading.Lock()
        # Cache loaded grids keyed by (fuel_path, water_path, buildings_path, wui_path)
        # Grids are spatial only — independent of weather/FWI, so safe to reuse
        self._grid_cache: dict[tuple, tuple] = {}
        self._grid_cache_lock = threading.Lock()

    def create(
        self,
        params: SimulationCreate,
        on_frame: Callable[[str, SimulationFrame], None] | None = None,
    ) -> str:
        """Create and start a new simulation.

        Args:
            params: Simulation parameters
            on_frame: Optional callback invoked for each frame (sim_id, frame)

        Returns:
            Simulation ID
        """
        sim_id = str(uuid.uuid4())[:8]
        run = SimulationRun(sim_id, params)

        with self._lock:
            self._runs[sim_id] = run

        # Start simulation in background thread
        thread = threading.Thread(
            target=self._execute,
            args=(run, on_frame),
            daemon=True,
        )
        thread.start()

        return sim_id

    def get(self, sim_id: str) -> SimulationRun | None:
        with self._lock:
            return self._runs.get(sim_id)

    def _load_grids(
        self,
        fuel_path: str | None,
        water_path: str | None,
        buildings_path: str | None,
        wui_path: str | None,
    ) -> tuple:
        """Load fuel grid and WUI modifiers, caching by path combo.

        Grids are purely spatial — independent of weather/FWI/ignition point,
        so they're safe to reuse across simulations.
        """
        if not fuel_path:
            return None, None

        cache_key = (fuel_path, water_path, buildings_path, wui_path)

        with self._grid_cache_lock:
            if cache_key in self._grid_cache:
                cached_fuel, _ = self._grid_cache[cache_key]
                if cached_fuel is not None:
                    logger.info(
                        "Grid cache HIT: %s — serving %dx%d grid from cache",
                        fuel_path, cached_fuel.rows, cached_fuel.cols,
                    )
                else:
                    logger.info("Grid cache HIT: %s (no grid loaded)", fuel_path)
                return self._grid_cache[cache_key]

        logger.info("Grid cache MISS: loading %s", fuel_path)

        # Load outside lock (slow operation, don't block other threads)
        from firesim.data.fuel_loader import load_fuel_grid

        try:
            fuel_grid = load_fuel_grid(
                fuel_path,
                water_path=water_path,
                buildings_path=buildings_path,
            )
        except FileNotFoundError:
            logger.error("Fuel grid file not found: %s", fuel_path)
            raise
        except ValueError as exc:
            logger.error("Fuel grid rejected (%s): %s", fuel_path, exc)
            raise
        except Exception as exc:
            logger.error(
                "Failed to load fuel grid from %s: %s — "
                "file may be corrupt or in an unsupported format",
                fuel_path, exc,
            )
            raise

        spread_modifier_grid = None
        if wui_path:
            from firesim.data.wui_loader import load_wui_modifiers

            spread_modifier_grid = load_wui_modifiers(
                wui_path,
                bounds=(fuel_grid.lat_min, fuel_grid.lat_max,
                        fuel_grid.lng_min, fuel_grid.lng_max),
                rows=fuel_grid.rows,
                cols=fuel_grid.cols,
            )

        result = (fuel_grid, spread_modifier_grid)

        with self._grid_cache_lock:
            self._grid_cache[cache_key] = result
            logger.info(
                "Grid cache STORE: %s — %dx%d grid, wui=%s",
                fuel_path, fuel_grid.rows, fuel_grid.cols, wui_path is not None,
            )

        return result

    def _execute(
        self,
        run: SimulationRun,
        on_frame: Callable[[str, SimulationFrame], None] | None,
    ) -> None:
        """Execute a simulation run."""
        run.status = SimulationStatus.RUNNING
        params = run.config

        try:
            # Resolve fuel type
            try:
                fuel_type = FuelType(params.fuel_type)
            except ValueError:
                fuel_type = FuelType.C2

            # Build engine config
            fwi = params.fwi_overrides
            config = SimulationConfig(
                ignition_lat=params.ignition_lat,
                ignition_lng=params.ignition_lng,
                weather=WeatherInput(
                    temperature=params.weather.temperature,
                    relative_humidity=params.weather.relative_humidity,
                    wind_speed=params.weather.wind_speed,
                    wind_direction=params.weather.wind_direction,
                    precipitation_24h=params.weather.precipitation_24h,
                ),
                duration_hours=params.duration_hours,
                snapshot_interval_minutes=params.snapshot_interval_minutes,
                ffmc=fwi.ffmc if fwi else 85.0,
                dmc=fwi.dmc if fwi else 40.0,
                dc=fwi.dc if fwi else 200.0,
            )

            # Load spatial grids (cached — only loads once per unique path combo)
            fuel_grid, spread_modifier_grid = self._load_grids(
                params.fuel_grid_path,
                params.water_path,
                params.buildings_path,
                getattr(params, "wui_zones_path", None),
            )

            simulator = Simulator(
                config,
                fuel_grid=fuel_grid,
                default_fuel=fuel_type,
                spread_modifier_grid=spread_modifier_grid,
            )

            for frame in simulator.run():
                run.add_frame(frame)
                if on_frame is not None:
                    on_frame(run.id, frame)

            run.status = SimulationStatus.COMPLETED
            logger.info("Simulation %s completed: %d frames", run.id, len(run.frames))

        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Simulation %s failed: %s", run.id, e)
