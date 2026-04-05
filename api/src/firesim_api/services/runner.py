"""Simulation runner service.

Manages simulation lifecycle: creation, execution, and result storage.
Simulations run in background threads and stream frames via callbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Callable

from firesim.fbp.constants import FuelType
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig, SimulationFrame, WeatherInput

from firesim_api.schemas.simulation import (
    MultiDaySimulationCreate,
    PerimeterOverrideRequest,
    SimulationCreate,
    SimulationStatus,
)

logger = logging.getLogger(__name__)


class SimulationRun:
    """Tracks state of a single simulation run."""

    def __init__(self, sim_id: str, config: SimulationCreate | MultiDaySimulationCreate | None):
        self.id = sim_id
        self.config = config
        self.status: SimulationStatus = SimulationStatus.PENDING
        self.frames: list[SimulationFrame] = []
        self.error: str | None = None
        self._lock = threading.Lock()
        # Pause/cancel control
        self._pause_event = threading.Event()
        self._pause_event.set()  # Initially running (not paused)
        self._cancel_event = threading.Event()

    def add_frame(self, frame: SimulationFrame) -> None:
        with self._lock:
            self.frames.append(frame)

    def get_frames(self) -> list[SimulationFrame]:
        with self._lock:
            return list(self.frames)

    def pause(self) -> None:
        """Pause the simulation after the current frame."""
        if self.status == SimulationStatus.RUNNING:
            self.status = SimulationStatus.PAUSED
            self._pause_event.clear()

    def resume(self) -> None:
        """Resume a paused simulation."""
        if self.status == SimulationStatus.PAUSED:
            self.status = SimulationStatus.RUNNING
            self._pause_event.set()

    def cancel(self) -> None:
        """Cancel the simulation immediately."""
        self.status = SimulationStatus.CANCELLED
        self._cancel_event.set()
        self._pause_event.set()  # Unblock if paused


class SimulationRunner:
    """Manages simulation runs.

    Stores active and completed simulations in memory.
    In a production system this would use a database.
    """

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}
        self._lock = threading.Lock()
        # Cache loaded grids keyed by (fuel_path, water_path, wui_path, dem_path).
        # Buildings are intentionally excluded — they are applied per-simulation
        # using the neighbourhood-filtered BuildingIndex, not baked into the grid.
        self._grid_cache: dict[tuple, tuple] = {}
        self._grid_cache_lock = threading.Lock()
        # BuildingIndex cache keyed by (buildings_path, neighbourhoods_path).
        # One expensive load+join per unique pair; reused across all simulations.
        self._building_index_cache: dict[tuple, object] = {}
        self._building_index_lock = threading.Lock()

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

    def _get_building_index(self, buildings_path: str, neighbourhoods_path: str):
        """Return a BuildingIndex, loading and caching on first call."""
        from firesim.data.building_index import BuildingIndex

        key = (buildings_path, neighbourhoods_path)
        with self._building_index_lock:
            if key in self._building_index_cache:
                return self._building_index_cache[key]

        # Load outside the lock — can be slow (30-60s), but only happens once.
        logger.info(
            "BuildingIndex cache MISS: loading buildings=%s nbhd=%s",
            buildings_path, neighbourhoods_path,
        )
        bidx = BuildingIndex(buildings_path, neighbourhoods_path)

        with self._building_index_lock:
            self._building_index_cache[key] = bidx
        logger.info("BuildingIndex cached for key=%s", key)
        return bidx

    def _load_grids(
        self,
        fuel_path: str | None,
        water_path: str | None,
        wui_path: str | None,
        dem_path: str | None = None,
    ) -> tuple:
        """Load fuel grid, WUI modifiers, and terrain grid, caching by path combo.

        Grids are purely spatial — independent of weather/FWI/ignition point,
        so they're safe to reuse across simulations. Buildings are NOT baked in
        here; they are applied per-simulation via BuildingIndex neighbourhood filter.

        Returns:
            (fuel_grid, spread_modifier_grid, terrain_grid) — any may be None.
        """
        if not fuel_path and not dem_path:
            return None, None, None

        cache_key = (fuel_path, water_path, wui_path, dem_path)

        with self._grid_cache_lock:
            if cache_key in self._grid_cache:
                cached = self._grid_cache[cache_key]
                cached_fuel = cached[0]
                if cached_fuel is not None:
                    logger.info(
                        "Grid cache HIT: %s — serving %dx%d grid from cache",
                        fuel_path, cached_fuel.rows, cached_fuel.cols,
                    )
                else:
                    logger.info("Grid cache HIT (dem-only or empty)")
                return cached

        logger.info("Grid cache MISS: loading fuel=%s dem=%s", fuel_path, dem_path)

        fuel_grid = None
        spread_modifier_grid = None
        terrain_grid = None

        if fuel_path:
            from firesim.data.fuel_loader import load_fuel_grid

            try:
                fuel_grid = load_fuel_grid(
                    fuel_path,
                    water_path=water_path,
                    # buildings_path intentionally omitted — applied per-simulation
                    # via BuildingIndex neighbourhood filter in _execute.
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

            if wui_path:
                from firesim.data.wui_loader import load_wui_modifiers

                spread_modifier_grid = load_wui_modifiers(
                    wui_path,
                    bounds=(fuel_grid.lat_min, fuel_grid.lat_max,
                            fuel_grid.lng_min, fuel_grid.lng_max),
                    rows=fuel_grid.rows,
                    cols=fuel_grid.cols,
                )

        if dem_path:
            from firesim.data.dem_loader import load_terrain_grid

            try:
                terrain_grid = load_terrain_grid(dem_path)
                logger.info(
                    "DEM loaded: %dx%d terrain grid for slope-adjusted spread",
                    terrain_grid.rows, terrain_grid.cols,
                )
            except FileNotFoundError:
                logger.error("DEM file not found: %s", dem_path)
                raise
            except Exception as exc:
                logger.error("Failed to load DEM from %s: %s", dem_path, exc)
                raise

        result = (fuel_grid, spread_modifier_grid, terrain_grid)

        with self._grid_cache_lock:
            self._grid_cache[cache_key] = result
            logger.info(
                "Grid cache STORE: fuel=%s dem=%s wui=%s",
                fuel_path is not None, dem_path is not None, wui_path is not None,
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

            from firesim_api.settings import settings

            # Resolve DEM path: per-request overrides env-var default
            dem_path = getattr(params, "dem_path", None) or settings.dem_path

            # Load spatial grids (cached — only loads once per unique path combo).
            # Buildings are NOT baked in here; applied per-simulation below.
            fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                params.fuel_grid_path,
                params.water_path,
                getattr(params, "wui_zones_path", None),
                dem_path,
            )

            # CA mode: load real grid from settings env var, fall back to synthetic
            if fuel_grid is None and getattr(params, "use_ca_mode", False):
                default_fuel_path = settings.fuel_grid_path
                if default_fuel_path and os.path.exists(default_fuel_path):
                    logger.info("CA mode: loading real fuel grid from %s", default_fuel_path)
                    real_grid, real_wui, real_terrain = self._load_grids(
                        default_fuel_path,
                        params.water_path or settings.water_path,
                        getattr(params, "wui_zones_path", None),
                        dem_path,
                    )
                    fuel_grid = real_grid
                    if real_wui is not None and spread_modifier_grid is None:
                        spread_modifier_grid = real_wui
                    if real_terrain is not None and terrain_grid is None:
                        terrain_grid = real_terrain
                    logger.info(
                        "Real CA grid loaded: %dx%d (%.4f-%.4fN, %.4f-%.4fE)",
                        fuel_grid.rows, fuel_grid.cols,
                        fuel_grid.lat_min, fuel_grid.lat_max,
                        fuel_grid.lng_min, fuel_grid.lng_max,
                    )
                else:
                    from firesim.data.synthetic_grid import generate_synthetic_fuel_grid

                    fuel_grid = generate_synthetic_fuel_grid(
                        ignition_lat=params.ignition_lat,
                        ignition_lng=params.ignition_lng,
                        radius_km=5.0,
                        cell_size_m=50.0,
                    )
                    logger.info(
                        "Synthetic CA grid generated: %dx%d around (%.4f, %.4f)",
                        fuel_grid.rows, fuel_grid.cols,
                        params.ignition_lat, params.ignition_lng,
                    )

            # Apply neighbourhood-filtered building mask per simulation.
            # Uses BuildingIndex (loaded once, cached) to find the 4 nearest
            # neighbourhoods to the ignition point, then rasterizes only those
            # ~5-15K buildings instead of all 334K citywide.
            masked_fuel_grid = fuel_grid
            building_centroids = None
            buildings_path = getattr(params, "buildings_path", None) or settings.buildings_path
            if buildings_path and fuel_grid is not None and settings.neighbourhoods_path:
                import dataclasses
                from firesim.data.environment import load_environment_mask

                bidx = self._get_building_index(buildings_path, settings.neighbourhoods_path)
                nearest = bidx.nearest_neighbourhoods(
                    params.ignition_lat, params.ignition_lng, n=4
                )
                logger.info("Nearest neighbourhoods for ignition (%.4f, %.4f): %s",
                            params.ignition_lat, params.ignition_lng, nearest)
                building_geoms = bidx.building_geoms_for(nearest)
                building_centroids = bidx.building_centroids_for(nearest)
                logger.info("Building mask: %d geometries from %d neighbourhoods",
                            len(building_geoms), len(nearest))

                if building_geoms:
                    bldg_mask = load_environment_mask(
                        bounds=(fuel_grid.lat_min, fuel_grid.lat_max,
                                fuel_grid.lng_min, fuel_grid.lng_max),
                        rows=fuel_grid.rows,
                        cols=fuel_grid.cols,
                        building_geoms=building_geoms,
                    )
                    # Apply mask to a copy of fuel_grid — never mutate the cached grid
                    new_fuel_types = [list(row) for row in fuel_grid.fuel_types]
                    masked = 0
                    for r in range(fuel_grid.rows):
                        for c in range(fuel_grid.cols):
                            if bldg_mask[r, c] and new_fuel_types[r][c] is not None:
                                new_fuel_types[r][c] = None
                                masked += 1
                    masked_fuel_grid = dataclasses.replace(fuel_grid, fuel_types=new_fuel_types)
                    logger.info("Building mask applied: %d cells masked", masked)

            simulator = Simulator(
                config,
                fuel_grid=masked_fuel_grid,
                terrain_grid=terrain_grid,
                default_fuel=fuel_type,
                spread_modifier_grid=spread_modifier_grid,
                enable_spotting=getattr(params, "enable_spotting", False),
                spotting_intensity=getattr(params, "spotting_intensity", 1.0),
                building_centroids=building_centroids,
            )

            for frame in simulator.run():
                run.add_frame(frame)
                if on_frame is not None:
                    on_frame(run.id, frame)
                # Block here when paused; unblocks on resume() or cancel()
                run._pause_event.wait()
                if run._cancel_event.is_set():
                    break

            if not run._cancel_event.is_set():
                run.status = SimulationStatus.COMPLETED
                logger.info("Simulation %s completed: %d frames", run.id, len(run.frames))
            else:
                logger.info("Simulation %s cancelled after %d frames", run.id, len(run.frames))

        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Simulation %s failed: %s", run.id, e)

    # ── Multi-day scenario ──────────────────────────────────────────────────

    def create_multiday(
        self,
        params: MultiDaySimulationCreate,
        on_frame: Callable[[str, SimulationFrame, int], None] | None = None,
    ) -> str:
        """Create and start a multi-day fire scenario.

        Chains one 24-hour Huygens simulation per day. The FWI system
        carries moisture codes (FFMC/DMC/DC) forward between days using
        CFFDRS daily equations. The final fire front from each day is used
        as the starting front for the next day.

        Args:
            params: Multi-day simulation parameters.
            on_frame: Callback (sim_id, frame, day_number) for each frame.

        Returns:
            Simulation ID.
        """
        sim_id = str(uuid.uuid4())[:8]
        run = SimulationRun(sim_id, params)

        with self._lock:
            self._runs[sim_id] = run

        thread = threading.Thread(
            target=self._execute_multiday,
            args=(run, params, on_frame),
            daemon=True,
        )
        thread.start()

        return sim_id

    def _execute_multiday(
        self,
        run: SimulationRun,
        params: MultiDaySimulationCreate,
        on_frame: Callable[[str, SimulationFrame, int], None] | None,
    ) -> None:
        """Execute a multi-day scenario, chaining per-day Huygens simulations."""
        import dataclasses

        from firesim.fwi.calculator import FWICalculator
        from firesim.spread.huygens import FireVertex

        run.status = SimulationStatus.RUNNING

        try:
            try:
                fuel_type = FuelType(params.fuel_type)
            except ValueError:
                fuel_type = FuelType.C2

            from firesim_api.settings import settings

            dem_path = getattr(params, "dem_path", None) or settings.dem_path
            fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                params.fuel_grid_path,
                params.water_path,
                None,  # no WUI for multiday
                dem_path,
            )

            # Initialise FWI carry-in state from user overrides or spring defaults
            fwi_ov = params.fwi_overrides
            ffmc_prev = fwi_ov.ffmc if fwi_ov and fwi_ov.ffmc is not None else 85.0
            dmc_prev = fwi_ov.dmc if fwi_ov and fwi_ov.dmc is not None else 6.0
            dc_prev = fwi_ov.dc if fwi_ov and fwi_ov.dc is not None else 15.0

            fwi_calc = FWICalculator(
                ffmc_prev=ffmc_prev,
                dmc_prev=dmc_prev,
                dc_prev=dc_prev,
            )

            initial_front: list[FireVertex] | None = None
            time_offset = 0.0  # cumulative hours added to frame timestamps

            for day_idx, day_weather in enumerate(params.days):
                day_num = day_idx + 1

                # Advance FWI state using today's weather (CFFDRS daily equations)
                day_fwi = fwi_calc.calculate_daily(
                    temp=day_weather.temperature,
                    rh=day_weather.relative_humidity,
                    wind=day_weather.wind_speed,
                    rain=day_weather.precipitation_24h,
                    month=params.month,
                )

                config = SimulationConfig(
                    ignition_lat=params.ignition_lat,
                    ignition_lng=params.ignition_lng,
                    weather=WeatherInput(
                        temperature=day_weather.temperature,
                        relative_humidity=day_weather.relative_humidity,
                        wind_speed=day_weather.wind_speed,
                        wind_direction=day_weather.wind_direction,
                        precipitation_24h=day_weather.precipitation_24h,
                    ),
                    duration_hours=24.0,
                    snapshot_interval_minutes=params.snapshot_interval_minutes,
                    ffmc=day_fwi.ffmc,
                    dmc=day_fwi.dmc,
                    dc=day_fwi.dc,
                )

                simulator = Simulator(
                    config,
                    fuel_grid=fuel_grid,
                    terrain_grid=terrain_grid,
                    default_fuel=fuel_type,
                    spread_modifier_grid=spread_modifier_grid,
                    initial_front=initial_front,
                )

                last_frame: SimulationFrame | None = None

                for frame in simulator.run():
                    # Skip the t=0 frame on day 2+ (duplicates end of previous day)
                    if day_idx > 0 and frame.time_hours == 0.0:
                        last_frame = frame
                        continue

                    adjusted = dataclasses.replace(
                        frame, time_hours=round(frame.time_hours + time_offset, 3)
                    )

                    run.add_frame(adjusted)
                    if on_frame is not None:
                        on_frame(run.id, adjusted, day_num)

                    run._pause_event.wait()
                    if run._cancel_event.is_set():
                        break

                    last_frame = frame

                if run._cancel_event.is_set():
                    break

                # Carry fire front forward to next day
                if last_frame is not None and len(last_frame.perimeter) >= 3:
                    initial_front = [
                        FireVertex(lat=lat, lng=lng) for lat, lng in last_frame.perimeter
                    ]
                else:
                    initial_front = None

                time_offset += 24.0
                logger.info(
                    "Multi-day sim %s — Day %d complete. FWI: FFMC=%.1f DMC=%.1f DC=%.1f FWI=%.1f",
                    run.id, day_num,
                    day_fwi.ffmc, day_fwi.dmc, day_fwi.dc, day_fwi.fwi,
                )

            if not run._cancel_event.is_set():
                run.status = SimulationStatus.COMPLETED
                logger.info(
                    "Multi-day simulation %s completed: %d frames across %d days",
                    run.id, len(run.frames), len(params.days),
                )
            else:
                logger.info("Multi-day simulation %s cancelled", run.id)

        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Multi-day simulation %s failed: %s", run.id, e)

    # ── Perimeter override (drone recon correction) ──────────────────────────

    def create_perimeter_override(
        self,
        req: PerimeterOverrideRequest,
        on_frame: Callable[[str, SimulationFrame], None] | None = None,
    ) -> str:
        """Create a new simulation seeded from a drone-observed fire perimeter.

        Looks up the original simulation, reuses its spatial/weather config,
        converts the supplied GeoJSON geometry to a FireVertex list, and runs
        a fresh Huygens spread from that corrected initial front.

        Args:
            req: Perimeter override parameters including original simulation ID
                 and GeoJSON geometry.
            on_frame: Optional callback invoked per frame (sim_id, frame).

        Returns:
            New simulation ID.

        Raises:
            ValueError: Original simulation not found, or invalid perimeter.
        """
        original = self.get(req.simulation_id)
        if original is None:
            raise ValueError(f"Simulation '{req.simulation_id}' not found")

        if not isinstance(original.config, SimulationCreate):
            raise ValueError(
                "Perimeter override requires a single-day simulation as the source. "
                "Multi-day source simulations are not supported."
            )

        from firesim.spread.geojson_utils import geojson_to_fire_vertices

        try:
            initial_front = geojson_to_fire_vertices(req.perimeter_geojson)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ValueError(f"Invalid perimeter GeoJSON: {exc}") from exc

        logger.info(
            "Perimeter override: source=%s vertices=%d dur=%.1fh",
            req.simulation_id, len(initial_front), req.duration_hours,
        )

        sim_id = str(uuid.uuid4())[:8]
        run = SimulationRun(sim_id, original.config)

        with self._lock:
            self._runs[sim_id] = run

        thread = threading.Thread(
            target=self._execute_perimeter_override,
            args=(run, original.config, initial_front, req, on_frame),
            daemon=True,
        )
        thread.start()

        return sim_id

    def _execute_perimeter_override(
        self,
        run: SimulationRun,
        params: SimulationCreate,
        initial_front: list,
        req: PerimeterOverrideRequest,
        on_frame: Callable[[str, SimulationFrame], None] | None,
    ) -> None:
        """Execute a simulation from a corrected drone-recon fire front."""
        run.status = SimulationStatus.RUNNING

        try:
            try:
                fuel_type = FuelType(params.fuel_type)
            except ValueError:
                fuel_type = FuelType.C2

            from firesim_api.settings import settings

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
                duration_hours=req.duration_hours,
                snapshot_interval_minutes=req.snapshot_interval_minutes,
                ffmc=fwi.ffmc if fwi and fwi.ffmc is not None else 85.0,
                dmc=fwi.dmc if fwi and fwi.dmc is not None else 40.0,
                dc=fwi.dc if fwi and fwi.dc is not None else 200.0,
            )

            dem_path = params.dem_path or settings.dem_path
            fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                params.fuel_grid_path,
                params.water_path,
                params.wui_zones_path,
                dem_path,
            )

            simulator = Simulator(
                config,
                fuel_grid=fuel_grid,
                terrain_grid=terrain_grid,
                default_fuel=fuel_type,
                spread_modifier_grid=spread_modifier_grid,
                initial_front=initial_front,
                enable_spotting=params.enable_spotting,
                spotting_intensity=params.spotting_intensity,
            )

            for frame in simulator.run():
                run.add_frame(frame)
                if on_frame is not None:
                    on_frame(run.id, frame)
                run._pause_event.wait()
                if run._cancel_event.is_set():
                    break

            if not run._cancel_event.is_set():
                run.status = SimulationStatus.COMPLETED
                logger.info(
                    "Perimeter override sim %s completed: %d frames",
                    run.id, len(run.frames),
                )
            else:
                logger.info("Perimeter override sim %s cancelled", run.id)

        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Perimeter override sim %s failed: %s", run.id, e)
