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
from firesim.types import HourlyWeather, SimulationConfig, SimulationFrame, WeatherInput

from firesim_api.schemas.simulation import (
    MultiDaySimulationCreate,
    PerimeterOverrideRequest,
    SimulationCreate,
    SimulationStatus,
    engine_hourly,
    local_start_hour,
)

logger = logging.getLogger(__name__)


#: Provincial fuel windows kept in memory (2 GB API machine: keep this small)
_WINDOW_CACHE_SIZE = 2


class RunCancelled(Exception):
    """Raised from the progress callback to stop a cancelled run mid-computation."""


#: Run phases reported to clients (WebSocket ``simulation.status``, GET ``phase``)
RUN_PHASES = ("loading", "buildings", "spread", "structures", "finishing")


class SimulationRun:
    """Tracks state of a single simulation run."""

    def __init__(self, sim_id: str, config: SimulationCreate | MultiDaySimulationCreate | None):
        self.id = sim_id
        self.config = config
        self.status: SimulationStatus = SimulationStatus.PENDING
        self.frames: list[SimulationFrame] = []
        self.error: str | None = None
        # Ensemble (optional, runs after the deterministic frames)
        self.ensemble = None
        self.ensemble_status: str | None = None  # running / completed / failed / cancelled
        self.ensemble_progress: tuple[int, int] = (0, 0)
        self.ensemble_error: str | None = None
        self._lock = threading.Lock()
        # Pause/cancel control
        self._pause_event = threading.Event()
        self._pause_event.set()  # Initially running (not paused)
        self._cancel_event = threading.Event()
        # Set by cancel_ensemble(): the client no longer wants this run's ensemble
        self._ensemble_cancel = threading.Event()
        # Progress for display: phase (RUN_PHASES) and fraction of the spread done (0-1)
        self.phase: str | None = None
        self.progress: float | None = None
        self.on_status: Callable[[str, str, float | None], None] | None = None
        self._last_sent: tuple[str | None, float] = (None, -1.0)
        # Fuel data used (FuelSourceInfo fields), set when the grids are loaded
        self.fuel_source: dict | None = None
        # Buildings used (BuildingsSourceInfo fields), set when buildings are selected
        self.buildings_source: dict | None = None

    def set_phase(self, phase: str, progress: float | None = None) -> None:
        """Record the run's phase and progress; notify ``on_status`` when the phase changes
        or progress moved at least 2 points (keeps WebSocket traffic small). Raises
        ``RunCancelled`` when the run was cancelled, so a long grid computation stops."""
        if self._cancel_event.is_set():
            raise RunCancelled()
        self.phase = phase
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        last_phase, last_p = self._last_sent
        p = self.progress if self.progress is not None else -1.0
        if phase == last_phase and (progress is None or p - last_p < 0.02):
            return
        self._last_sent = (phase, p)
        if self.on_status is not None:
            try:
                self.on_status(self.id, phase, self.progress if phase == "spread" else None)
            except Exception:  # display only: never fail a run over a status message
                logger.debug("status callback failed for %s", self.id, exc_info=True)

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

    def cancel_ensemble(self) -> None:
        """Stop the ensemble (before its next member) or skip it if it has not started.
        The client calls this when it starts a new run, so an abandoned ensemble does not
        keep the CPU from the new run (a 1-vCPU server)."""
        self._ensemble_cancel.set()
        if self.ensemble_status is None:
            self.ensemble_status = "cancelled"

    def cancel(self) -> None:
        """Cancel the simulation immediately."""
        self.status = SimulationStatus.CANCELLED
        self._cancel_event.set()
        self._pause_event.set()  # Unblock if paused


def _fuel_kwargs(params) -> dict:
    """Fuel modifiers for SimulationConfig (day of year from start_time when the request has one)."""
    if hasattr(params, "fuel_config_kwargs"):
        return params.fuel_config_kwargs()
    return params.fuel_modifiers.config_kwargs()


MS_BUILDINGS_ATTRIBUTION = ("Building footprints: Microsoft Canadian Building Footprints "
                            "(2019), Open Data Commons ODbL")


def _buildings_info(kind: str, path: str, n: int, capped: bool, selection: str) -> dict:
    """BuildingsSourceInfo fields for a run."""
    from firesim_api.settings import settings

    name = os.path.basename(path)
    attribution = MS_BUILDINGS_ATTRIBUTION
    if path.lower().endswith(".fgb"):
        label = "Microsoft Canadian Building Footprints, Alberta (2019)"
    elif settings.city_buildings_path and name == os.path.basename(settings.city_buildings_path):
        label = "Microsoft Canadian Building Footprints, Edmonton extract (2019)"
    else:
        label, attribution = f"Building footprints {name}", None
    return {"kind": kind, "label": label, "attribution": attribution,
            "selection": selection, "buildings": int(n), "capped": bool(capped)}


def run_day_of_year(params) -> int | None:
    """Day of year of a request: fuel_modifiers.day_of_year, else start_time's local date."""
    doy = params.fuel_modifiers.day_of_year
    if doy is None and getattr(params, "start_time", None) is not None:
        doy = params.start_time.timetuple().tm_yday
    return doy


def _burning_period(params) -> tuple[float, float] | None:
    """Engine burning period (local clock hours) from an API request, if set."""
    bp = getattr(params, "burning_period", None)
    return bp.as_tuple() if bp is not None else None


def _override_clock(
    req: PerimeterOverrideRequest, source: SimulationCreate
) -> tuple[float | None, tuple[HourlyWeather, ...] | None]:
    """Local start hour and hourly weather for a perimeter restart.

    The restart starts at ``req.start_time`` (default: the source's start). The source's hourly
    stream is re-based to it, keeping the spin-up hours before it if ``req.ffmc_spin_up``.

    Raises:
        ValueError: a burning period or spin-up without any start time, or a stream that
            cannot spin up.
    """
    start = req.start_time or source.start_time
    if start is None and (req.burning_period is not None or req.ffmc_spin_up):
        raise ValueError("burning_period / ffmc_spin_up need start_time (on the request or the "
                         "source simulation)")
    shift = 0.0
    if start is not None and source.start_time is not None:
        shift = (start - source.start_time).total_seconds() / 3600.0
    start_hour = local_start_hour(start)
    hourly = engine_hourly(source.hourly_weather, shift, start_hour, req.ffmc_spin_up)
    return start_hour, hourly


def _synthetic_seed(lat: float, lng: float) -> int:
    """Stable RNG seed for the synthetic demo landscape at an ignition point."""
    return round(lat * 1e4) * 10_000_000 + round(lng * 1e4)


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
        # Native-resolution fuel grids for the 20 m WUI window, keyed by (fuel, water) path;
        # one byte per cell (~3 MB for Edmonton)
        self._fine_cache: dict[tuple, object] = {}
        self._fine_lock = threading.Lock()
        # Provincial fuel windows (runs outside the City grid), most recent last; at most
        # _WINDOW_CACHE_SIZE kept (each ~10-20 MB of Python lists for a 40 km window at 50 m)
        self._window_cache: dict[tuple, object] = {}
        self._window_lock = threading.Lock()

    def create(
        self,
        params: SimulationCreate,
        on_frame: Callable[[str, SimulationFrame], None] | None = None,
        on_status: Callable[[str, str, float | None], None] | None = None,
    ) -> str:
        """Create and start a new simulation.

        Args:
            params: Simulation parameters
            on_frame: Optional callback invoked for each frame (sim_id, frame)
            on_status: Optional callback (sim_id, phase, progress) for progress display

        Returns:
            Simulation ID
        """
        sim_id = str(uuid.uuid4())[:8]
        run = SimulationRun(sim_id, params)
        run.on_status = on_status

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

    def _get_building_index(self, buildings_path: str, neighbourhoods_path: str | None):
        """Return a BuildingIndex, opening and caching on first call.

        A FlatGeobuf (the Alberta file) is read by window; nothing per building is held. The
        Edmonton neighbourhood selection then applies to the former Edmonton extract's box
        only (``EDMONTON_EXTRACT_BOX``); a GeoJSON file keeps the former whole-file
        behaviour (docs/buildings-alberta.md)."""
        from firesim.data.building_index import EDMONTON_EXTRACT_BOX, BuildingIndex

        key = (buildings_path, neighbourhoods_path)
        with self._building_index_lock:
            if key in self._building_index_cache:
                return self._building_index_cache[key]

        logger.info(
            "BuildingIndex cache MISS: opening buildings=%s nbhd=%s",
            buildings_path, neighbourhoods_path,
        )
        fgb = buildings_path.lower().endswith(".fgb")
        bidx = BuildingIndex(buildings_path, neighbourhoods_path,
                             partition_box=EDMONTON_EXTRACT_BOX if fgb else None)

        with self._building_index_lock:
            self._building_index_cache[key] = bidx
        logger.info("BuildingIndex cached for key=%s", key)
        return bidx

    @staticmethod
    def resolve_buildings_path(path: str | None) -> str | None:
        """The buildings file a run reads: the Alberta FlatGeobuf when it is installed and the
        request (or the server default) names the bundled Edmonton file; otherwise the path as
        given (None = no buildings)."""
        from firesim_api.settings import settings

        ab = settings.ab_buildings_path
        if not ab or not os.path.exists(ab):
            return path
        city = settings.city_buildings_path
        if path and city and os.path.realpath(path) == os.path.realpath(city):
            return ab
        return path

    def _get_building_vegetation(self):
        """Per-building vegetation attributes (structure step 3), loaded once; None when the
        file is not configured or unreadable (attributes are context only)."""
        from firesim_api.settings import settings

        path = settings.building_vegetation_path
        if not path:
            return None
        cache = self.__dict__.setdefault("_veg_cache", {})
        if path not in cache:
            try:
                from firesim.structures.vegetation import BuildingVegetation

                cache[path] = BuildingVegetation.from_csv(path)
                logger.info("Building vegetation: %d rows from %s", len(cache[path]), path)
            except Exception:  # noqa: BLE001 - optional context data
                logger.exception("Building vegetation not loaded from %s", path)
                cache[path] = None
        return cache[path]

    def _get_canopy(self):
        """The gap-cover canopy raster (vegetation-bridged cutoff); None when absent."""
        from firesim_api.settings import settings

        path = settings.canopy_path
        if not path:
            return None
        cache = self.__dict__.setdefault("_canopy_cache", {})
        if path not in cache:
            try:
                from firesim.structures.vegetation import CanopyCover

                cache[path] = CanopyCover(path)
            except Exception:  # noqa: BLE001
                logger.exception("Canopy raster not opened from %s", path)
                cache[path] = None
        return cache[path]

    def _get_fine_source(self, fuel_path: str | None, water_path: str | None, run_grid,
                         fill_path: str | None = None):
        """The fuel raster at its native cell size for the 20 m WUI window, or None (disabled,
        no raster, or the run grid is not coarser than the raster). ``fill_path``: the Alberta
        grid filling the raster's no-data cells, as on the run grid."""
        from firesim_api.settings import settings

        if not fuel_path or run_grid is None or not settings.wui_fine_grid:
            return None
        key = (fuel_path, water_path, fill_path)
        with self._fine_lock:
            if key in self._fine_cache:
                return self._fine_cache[key]
        from firesim.data.fine_grid import FineFuelSource

        try:
            src = FineFuelSource.from_raster(fuel_path, water_path=water_path,
                                             fill_path=fill_path)
        except Exception:  # noqa: BLE001 - the 50 m run still works without it
            logger.exception("Native fuel grid for the 20 m window could not be loaded")
            src = None
        with self._fine_lock:
            self._fine_cache[key] = src
        return src

    def _load_grids(
        self,
        fuel_path: str | None,
        water_path: str | None,
        wui_path: str | None,
        dem_path: str | None = None,
        fill_path: str | None = None,
    ) -> tuple:
        """Load fuel grid, WUI modifiers, and terrain grid, caching by path combo.

        ``fill_path``: a raster (the Alberta Wildfire grid) filling the fuel grid's no-data
        cells (``load_fuel_grid``).

        Grids are purely spatial — independent of weather/FWI/ignition point,
        so they're safe to reuse across simulations. Buildings are NOT baked in
        here; they are applied per-simulation via BuildingIndex neighbourhood filter.

        Returns:
            (fuel_grid, spread_modifier_grid, terrain_grid) — any may be None.
        """
        if not fuel_path and not dem_path:
            return None, None, None

        cache_key = (fuel_path, water_path, wui_path, dem_path, fill_path)

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
                    fill_path=fill_path,
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

    # ── Provincial default fuel (docs/fuel-grid-alberta.md) ────────────────────────────────

    @staticmethod
    def fuel_sources():
        """``FuelSources`` when the Alberta grid is installed, else None (City grid only)."""
        from firesim.data.provincial import FuelSources
        from firesim_api.settings import settings

        ab = settings.ab_fuel_grid_path
        if not ab or not os.path.exists(ab):
            return None
        city = settings.city_fuel_grid_path
        national = settings.national_fuel_grid_path
        return FuelSources(
            ab_path=ab,
            city_path=city if city and os.path.exists(city) else None,
            national_path=national if national and os.path.exists(national) else None,
            national_scheme=settings.national_fuel_scheme,
            window_km=settings.provincial_window_km,
            city_margin_km=settings.city_margin_km,
        )

    @staticmethod
    def _is_city_grid(path: str | None) -> bool:
        from firesim_api.settings import settings

        city = settings.city_fuel_grid_path
        if not path or not city:
            return False
        return os.path.realpath(path) == os.path.realpath(city)

    def provincial_applies(self, params) -> bool:
        """The request asks for the default fuel: the City grid by path (or CA mode loading it),
        ``provincial_fuel`` on, and the Alberta grid installed."""
        from firesim_api.settings import settings

        if not getattr(params, "provincial_fuel", True) or self.fuel_sources() is None:
            return False
        path = params.fuel_grid_path
        if path is None and getattr(params, "use_ca_mode", False):
            path = settings.fuel_grid_path
        return self._is_city_grid(path)

    def _provincial_window(self, sources, lat: float, lng: float, water_path: str | None):
        """Cached ``provincial_window`` (keyed by the snapped window, LRU of _WINDOW_CACHE_SIZE)."""
        from firesim.data.provincial import Lattice, provincial_window

        lat_ = Lattice.around(lat, lng, sources.window_km, sources.cell_m)
        key = (round(lat_.lat_max, 7), round(lat_.lng_min, 7), lat_.rows, sources, water_path)
        with self._window_lock:
            hit = self._window_cache.pop(key, None)
            if hit is not None:
                self._window_cache[key] = hit
                return hit
        stacked = provincial_window(sources, lat, lng, water_path=water_path)
        with self._window_lock:
            self._window_cache[key] = stacked
            while len(self._window_cache) > _WINDOW_CACHE_SIZE:
                self._window_cache.pop(next(iter(self._window_cache)))
        return stacked

    def resolve_provincial(self, params, day_of_year: int | None, dem_path: str | None,
                           wui_path: str | None = None, water_path: str | None = None):
        """(fuel_grid, spread_modifier_grid, terrain_grid, fine_source_or_None, fuel_source dict)
        for a request that asks for the default fuel (``provincial_applies``).

        Inside the City grid (ignition at least ``city_margin_km`` inside its box): the City
        grid on its own 50 m lattice, the Alberta grid filling its no-data cells, and the 20 m
        window source filled the same way. Elsewhere in Alberta: a ``window_km`` window centred
        on the ignition (City cells where mapped, Alberta elsewhere, national outside Alberta if
        configured); no 20 m window. Outside every grid: no grid (the uniform ``fuel_type``).
        The season (D-1/D-2, M-1/M-2) is applied per run on a copy; cached grids stay unseasoned.
        """
        from firesim.data.provincial import choose_fuel
        from firesim.fbp.season import apply_season, season_label

        sources = self.fuel_sources()
        choice = choose_fuel(sources, params.ignition_lat, params.ignition_lng,
                             getattr(params, "fuel_type", "C2"))
        info = {"kind": choice.kind, "label": choice.label, "attribution": choice.attribution,
                "season": None, "share": None}
        fine = None
        if choice.kind == "city":
            fuel_grid, wui_grid, terrain = self._load_grids(
                sources.city_path, water_path, wui_path, dem_path, fill_path=sources.ab_path)
            fine = self._get_fine_source(sources.city_path, water_path, fuel_grid,
                                         fill_path=sources.ab_path)
        elif choice.kind in ("provincial", "national"):
            stacked = self._provincial_window(sources, params.ignition_lat, params.ignition_lng,
                                              water_path)
            fuel_grid = stacked.grid
            info["share"] = {k: round(v, 4) for k, v in stacked.share.items()}
            from firesim.data.provincial import AB_LABEL, CITY_LABEL, NATIONAL_LABEL

            parts = [(stacked.share.get("city", 0.0), f"{CITY_LABEL} where mapped"),
                     (stacked.share.get("ab_wildfire", 0.0), AB_LABEL),
                     (stacked.share.get("national", 0.0),
                      f"{NATIONAL_LABEL} ({sources.national_scheme}) outside Alberta")]
            info["label"] = "; ".join(text for share, text in parts if share > 0) or choice.label
            wui_grid = None
            if wui_path:
                from firesim.data.wui_loader import load_wui_modifiers

                wui_grid = load_wui_modifiers(
                    wui_path, bounds=(fuel_grid.lat_min, fuel_grid.lat_max, fuel_grid.lng_min,
                                      fuel_grid.lng_max), rows=fuel_grid.rows, cols=fuel_grid.cols)
            terrain = self._load_grids(None, None, None, dem_path)[2] if dem_path else None
        else:  # uniform: outside every grid
            fuel_grid, wui_grid = None, None
            terrain = self._load_grids(None, None, None, dem_path)[2] if dem_path else None
        if fuel_grid is not None:
            fuel_grid = apply_season(fuel_grid, day_of_year)
            info["season"] = season_label(day_of_year)
            if fine is not None:
                fine = fine.with_season(day_of_year)
        logger.info("Fuel source: %s (%s)", choice.kind, choice.label)
        return fuel_grid, wui_grid, terrain, fine, info

    def _legacy_fuel_source(self, params, fuel_grid, grid_path: str | None) -> dict:
        """FuelSourceInfo fields for a run that did not use the provincial default."""
        from firesim.data.provincial import CITY_LABEL

        if fuel_grid is None:
            kind, label = "uniform", f"Uniform {getattr(params, 'fuel_type', 'C2')}"
        elif grid_path is None:
            kind, label = "synthetic", "Synthetic demo fuel mosaic"
        elif self._is_city_grid(grid_path):
            kind, label = "city", f"{CITY_LABEL} (20 m, 2025)"
        else:
            kind, label = "user_grid", f"Fuel grid {os.path.basename(grid_path)}"
        return {"kind": kind, "label": label, "attribution": None, "season": None, "share": None}

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
                **_fuel_kwargs(params),
                hourly_weather=params.engine_hourly_weather(),
                start_hour=params.start_hour(),
                burning_period=_burning_period(params),
                seed=getattr(params, "seed", None),
            )

            from firesim_api.settings import settings

            # Resolve DEM path: per-request overrides env-var default
            dem_path = getattr(params, "dem_path", None) or settings.dem_path

            # Load spatial grids (cached — only loads once per unique path combo).
            # Buildings are NOT baked in here; applied per-simulation below.
            run.set_phase("loading")
            # Default fuel (City grid where mapped, Alberta Wildfire grid elsewhere) when the
            # request names the City grid and the Alberta grid is installed
            provincial = self.provincial_applies(params)
            prov_fine = None
            if provincial:
                water = params.water_path or (
                    settings.water_path if not params.fuel_grid_path else None)
                (fuel_grid, spread_modifier_grid, terrain_grid, prov_fine,
                 run.fuel_source) = self.resolve_provincial(
                    params, config.day_of_year, dem_path,
                    getattr(params, "wui_zones_path", None), water)
                fine_key = (None, None)
            else:
                fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                    params.fuel_grid_path,
                    params.water_path,
                    getattr(params, "wui_zones_path", None),
                    dem_path,
                )
                fine_key = (params.fuel_grid_path, params.water_path)
            # CA mode: load real grid from settings env var, fall back to synthetic
            if not provincial and fuel_grid is None and getattr(params, "use_ca_mode", False):
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
                    fine_key = (default_fuel_path, params.water_path or settings.water_path)
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

                    # Seeded from the ignition point (~11 m precision) so re-running a
                    # scenario reproduces the same demo landscape and result.
                    fine_key = (None, None)
                    fuel_grid = generate_synthetic_fuel_grid(
                        ignition_lat=params.ignition_lat,
                        ignition_lng=params.ignition_lng,
                        radius_km=5.0,
                        cell_size_m=50.0,
                        seed=_synthetic_seed(params.ignition_lat, params.ignition_lng),
                    )
                    logger.info(
                        "Synthetic CA grid generated: %dx%d around (%.4f, %.4f)",
                        fuel_grid.rows, fuel_grid.cols,
                        params.ignition_lat, params.ignition_lng,
                    )

            if run.fuel_source is None:
                run.fuel_source = self._legacy_fuel_source(params, fuel_grid, fine_key[0])

            # Buildings for the building mask, inside-perimeter counts and exposure
            # (docs/buildings-alberta.md). In Edmonton (ignition in the former extract's box):
            # the buildings of the 4 neighbourhoods nearest the ignition, exactly as before.
            # Elsewhere (Alberta file): the buildings in the run grid's box, the nearest
            # FIRESIM_BUILDINGS_MAX_NEAR to the ignition when there are more. Only a window of
            # the file is read either way.
            masked_fuel_grid = fuel_grid
            building_centroids = None
            building_geoms = None
            structure_geoms = None
            buildings_path = self.resolve_buildings_path(
                getattr(params, "buildings_path", None) or settings.buildings_path)
            is_fgb = bool(buildings_path) and buildings_path.lower().endswith(".fgb")
            if buildings_path and fuel_grid is not None and (
                    settings.neighbourhoods_path or is_fgb):
                import dataclasses
                from firesim.data.building_index import EDMONTON_EXTRACT_BOX
                from firesim.data.environment import load_environment_mask

                run.set_phase("buildings")
                bidx = self._get_building_index(buildings_path, settings.neighbourhoods_path)
                lat0, lng0 = params.ignition_lat, params.ignition_lng
                if bidx.in_partition(lat0, lng0):
                    nearest = bidx.nearest_neighbourhoods(lat0, lng0, n=4)
                    logger.info("Nearest neighbourhoods for ignition (%.4f, %.4f): %s",
                                lat0, lng0, nearest)
                    building_geoms = bidx.building_geoms_for(nearest)
                    building_centroids = bidx.building_centroids_for(nearest)
                    # Edmonton runs keep the former extract's buildings (same results)
                    source = bidx.clipped(EDMONTON_EXTRACT_BOX) if is_fgb else bidx
                    run.buildings_source = _buildings_info(
                        "edmonton_neighbourhoods", buildings_path, len(building_geoms), False,
                        f"{len(nearest)} City of Edmonton neighbourhoods nearest the ignition")
                else:
                    near = bidx.near(lat0, lng0, (fuel_grid.lat_min, fuel_grid.lat_max,
                                                  fuel_grid.lng_min, fuel_grid.lng_max),
                                     max_buildings=settings.buildings_max_near)
                    building_geoms, building_centroids = near.geoms, near.centroids
                    source = bidx
                    run.buildings_source = _buildings_info(
                        "near_ignition", buildings_path, len(building_geoms), near.capped,
                        ("run area" if not near.capped else
                         f"the {len(building_geoms):,} buildings nearest the ignition "
                         "in the run area"))
                logger.info("Building mask: %d geometries (%s)", len(building_geoms),
                            run.buildings_source["kind"])
                if getattr(params, "structure_spread", False):
                    # Structure spread may reach any building in the run area, not only the
                    # selection above (docs/structure-spread-spec.md §2). The index is passed
                    # as a footprint source: after the grid run, units are built only for the
                    # area the spread can reach, read by window from the file.
                    structure_geoms = source

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
                building_footprints=building_geoms or None,
                structure_spread=getattr(params, "structure_spread", False),
                structure_footprints=structure_geoms,
                structure_embers=getattr(params, "structure_embers", False),
                structure_design_fire_kw_m2=getattr(params, "structure_design_fire_kw_m2", 150),
                structure_burnout=getattr(params, "structure_burnout", True),
                structure_vegetation=(self._get_building_vegetation()
                                      if structure_geoms is not None else None),
                structure_vegetation_bridge=getattr(params, "structure_vegetation_bridge", False),
                structure_canopy=(self._get_canopy()
                                  if getattr(params, "structure_vegetation_bridge", False)
                                  else None),
                structure_combustible_roof_share=getattr(
                    params, "structure_combustible_roof_share", 0.0),
                progress=run.set_phase,
                # 20 m WUI window (mechanics decision M5): tried when buildings are near the
                # fire, kept only within the memory guards (firesim.spread.wui_window)
                fine_fuel=((prov_fine if provincial else self._get_fine_source(*fine_key, fuel_grid))
                           if building_geoms else None),
                fine_building_mask=building_geoms or None,
            )

            run.set_phase("spread", 0.0)
            for frame in simulator.run():
                run.add_frame(frame)
                run.progress = min(1.0, frame.time_hours / max(params.duration_hours, 1e-9))
                if on_frame is not None:
                    on_frame(run.id, frame)
                # Block here when paused; unblocks on resume() or cancel()
                run._pause_event.wait()
                if run._cancel_event.is_set():
                    break

            if not run._cancel_event.is_set():
                run.status = SimulationStatus.COMPLETED
                logger.info("Simulation %s completed: %d frames", run.id, len(run.frames))
                ens = getattr(params, "ensemble", None)
                if ens is not None and masked_fuel_grid is not None:
                    self._run_ensemble(run, config, masked_fuel_grid, terrain_grid,
                                       spread_modifier_grid, ens)
            else:
                logger.info("Simulation %s cancelled after %d frames", run.id, len(run.frames))

        except RunCancelled:
            run.status = SimulationStatus.CANCELLED
            logger.info("Simulation %s cancelled during computation", run.id)
        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Simulation %s failed: %s", run.id, e)

    @staticmethod
    def _run_ensemble(run, config, fuel_grid, terrain_grid, spread_modifier_grid, ens) -> None:
        """Run the ensemble for a completed grid run; results on ``run.ensemble``."""
        from firesim.spread.ensemble import EnsembleCancelledError, EnsembleConfig, run_ensemble

        if run._ensemble_cancel.is_set():
            run.ensemble_status = "cancelled"
            return
        run.ensemble_status = "running"
        run.ensemble_progress = (0, ens.n_members)
        try:
            run.ensemble = run_ensemble(
                config, fuel_grid, EnsembleConfig(**ens.model_dump()),
                terrain_grid=terrain_grid, spread_modifier_grid=spread_modifier_grid,
                progress=lambda done, total: setattr(run, "ensemble_progress", (done, total)),
                should_stop=run._ensemble_cancel.is_set,
            )
            run.ensemble_status = "completed"
        except EnsembleCancelledError as exc:
            run.ensemble_status = "cancelled"
            logger.info("Ensemble for %s cancelled by the client (%s)", run.id, exc)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the client
            run.ensemble_status = "failed"
            run.ensemble_error = str(exc)
            logger.exception("Ensemble for %s failed", run.id)

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
            if self.provincial_applies(params):
                (fuel_grid, spread_modifier_grid, terrain_grid, _,
                 run.fuel_source) = self.resolve_provincial(
                    params, run_day_of_year(params), dem_path,
                    None, params.water_path)
            else:
                fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                    params.fuel_grid_path,
                    params.water_path,
                    None,  # no WUI for multiday
                    dem_path,
                )
                run.fuel_source = self._legacy_fuel_source(params, fuel_grid, params.fuel_grid_path)

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
            initial_burned: list[tuple[float, float]] | None = None
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
                    **params.fuel_modifiers.config_kwargs(),
                    # each day runs 24 h from the same clock time
                    start_hour=local_start_hour(params.start_time),
                    burning_period=_burning_period(params),
                )

                simulator = Simulator(
                    config,
                    fuel_grid=fuel_grid,
                    terrain_grid=terrain_grid,
                    default_fuel=fuel_type,
                    spread_modifier_grid=spread_modifier_grid,
                    initial_front=initial_front,
                    initial_burned=initial_burned,
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

                # Carry the fire forward to the next day: the perimeter for the Huygens
                # model, and every burned cell for the grid model (which may hold
                # several separate burned areas, e.g. from spot fires)
                if last_frame is not None and len(last_frame.perimeter) >= 3:
                    initial_front = [
                        FireVertex(lat=lat, lng=lng) for lat, lng in last_frame.perimeter
                    ]
                else:
                    initial_front = None
                initial_burned = (
                    [(c["lat"], c["lng"]) for c in last_frame.burned_cells]
                    if last_frame is not None and last_frame.burned_cells else None
                )

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
        on_status: Callable[[str, str, float | None], None] | None = None,
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

        if req.active_edges is not None and not original.config.fuel_grid_path:
            raise ValueError(
                "active_edges needs the grid model: the source simulation has no fuel grid"
            )

        from firesim.spread.geojson_utils import geojson_to_fire_vertices

        try:
            initial_front = geojson_to_fire_vertices(req.perimeter_geojson)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ValueError(f"Invalid perimeter GeoJSON: {exc}") from exc

        clock = _override_clock(req, original.config)  # ValueError -> 422

        logger.info(
            "Perimeter override: source=%s vertices=%d dur=%.1fh",
            req.simulation_id, len(initial_front), req.duration_hours,
        )

        sim_id = str(uuid.uuid4())[:8]
        run = SimulationRun(sim_id, original.config)
        run.on_status = on_status

        with self._lock:
            self._runs[sim_id] = run

        thread = threading.Thread(
            target=self._execute_perimeter_override,
            args=(run, original.config, initial_front, req, on_frame, clock),
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
        clock: tuple[float | None, tuple[HourlyWeather, ...] | None] = (None, None),
    ) -> None:
        """Execute a simulation from a corrected drone-recon fire front.

        ``clock`` is (local start hour, hourly weather) from ``_override_clock``.
        """
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
                **_fuel_kwargs(params),
                start_hour=clock[0],
                hourly_weather=clock[1],
                burning_period=_burning_period(req),
                seed=getattr(params, "seed", None),
            )

            dem_path = params.dem_path or settings.dem_path
            run.set_phase("loading")
            if self.provincial_applies(params):  # same fuel as the source run
                (fuel_grid, spread_modifier_grid, terrain_grid, _,
                 run.fuel_source) = self.resolve_provincial(
                    params, config.day_of_year, dem_path, params.wui_zones_path,
                    params.water_path)
            else:
                fuel_grid, spread_modifier_grid, terrain_grid = self._load_grids(
                    params.fuel_grid_path,
                    params.water_path,
                    params.wui_zones_path,
                    dem_path,
                )
                run.fuel_source = self._legacy_fuel_source(params, fuel_grid, params.fuel_grid_path)

            simulator = Simulator(
                config,
                fuel_grid=fuel_grid,
                terrain_grid=terrain_grid,
                default_fuel=fuel_type,
                spread_modifier_grid=spread_modifier_grid,
                initial_front=initial_front,
                enable_spotting=params.enable_spotting,
                spotting_intensity=params.spotting_intensity,
                active_edges=req.active_edges,
                active_edge_buffer_m=req.active_edge_buffer_m,
                progress=run.set_phase,
            )

            run.set_phase("spread", 0.0)
            for frame in simulator.run():
                run.add_frame(frame)
                run.progress = min(1.0, frame.time_hours / max(req.duration_hours, 1e-9))
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

        except RunCancelled:
            run.status = SimulationStatus.CANCELLED
            logger.info("Perimeter override sim %s cancelled during computation", run.id)
        except Exception as e:
            run.status = SimulationStatus.FAILED
            run.error = str(e)
            logger.exception("Perimeter override sim %s failed: %s", run.id, e)
