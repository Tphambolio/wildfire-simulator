"""Simulation runner service.

Manages simulation lifecycle: creation, execution, and result storage.
Simulations run in background threads and stream frames via callbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from firesim.fbp.constants import FuelType
from firesim.spread.simulator import Simulator
from firesim.types import HourlyWeather, SimulationConfig, SimulationFrame, WeatherInput

from firesim_api.schemas.simulation import (
    MultiDaySimulationCreate,
    PerimeterOverrideRequest,
    SimulationCreate,
    SimulationStatus,
    local_start_hour,
)
from firesim_api.services.run_ids import new_run_id

logger = logging.getLogger(__name__)


#: Runs kept in memory (frames, arrival grid, ensemble). Beyond this the oldest finished runs are
#: dropped, so a long uptime cannot fill the 2 GB machine (QA 2026-10-11: the store had no
#: bound). A dropped run answers 404, as after a restart; clients keep their own copy.
MAX_STORED_RUNS = int(os.environ.get("FIRESIM_MAX_STORED_RUNS", "40"))

_LIVE = (SimulationStatus.PENDING, SimulationStatus.RUNNING, SimulationStatus.PAUSED)

#: Provincial fuel windows kept in memory (2 GB API machine: keep this small)
_WINDOW_CACHE_SIZE = 2

#: Ensembles allowed to compute at once (``FIRESIM_MAX_CONCURRENT_ENSEMBLES``, default 1). The
#: API runs on one shared CPU: two ensembles at once stalled every other run, so later ones
#: wait their turn with status "pending" (cancellable while waiting).
_ENSEMBLE_SLOTS = threading.BoundedSemaphore(
    max(1, int(os.environ.get("FIRESIM_MAX_CONCURRENT_ENSEMBLES", "1") or 1)))


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
        # Multi-day runs: the day (1-based) of each frame, parallel to ``frames``, and each
        # day's FWI codes after the CFFDRS daily carry-over ({day: {ffmc, dmc, dc, isi, bui,
        # fwi}}), so GET and a late WebSocket join report them as the live stream does
        self.frame_days: list[int | None] = []
        self.day_fwi: dict[int, dict[str, float]] = {}
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
        # Multi-day runs: the day being computed (1-based) and the number of days, sent with
        # the status messages ("Day 2 of 3"; round-3 field test R12)
        self.day: int | None = None
        self.n_days: int | None = None
        self.on_status: Callable[[str, str, float | None], None] | None = None
        self._last_sent: tuple[str | None, float] = (None, -1.0)
        # Fuel data used (FuelSourceInfo fields), set when the grids are loaded
        self.fuel_source: dict | None = None
        # Buildings used (BuildingsSourceInfo fields), set when buildings are selected
        self.buildings_source: dict | None = None
        # Run archive (services/archive.py): kind of run, the perimeter-override request,
        # creation and end times (epoch seconds)
        self.kind: str = "single" if not isinstance(config, MultiDaySimulationCreate) else "multiday"
        self.override_request: dict | None = None
        self.created_at: float = time.time()
        self.finished_at: float | None = None
        #: Set once the run's thread has ended and its on_finished hook (the archive) has run
        self.done = threading.Event()
        # Terrain used (TerrainSourceInfo fields), set when the grids are loaded
        self.terrain_source: dict | None = None
        # Perimeter restarts: the source run's request, and where the weather came from
        # ("request": the restart's own; "source_run": the source's, none was sent)
        self.source_config: SimulationCreate | None = None
        self.weather_source: str = "request"

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

    def add_frame(self, frame: SimulationFrame, day: int | None = None) -> None:
        with self._lock:
            self.frames.append(frame)
            self.frame_days.append(day)

    def get_frames(self) -> list[SimulationFrame]:
        with self._lock:
            return list(self.frames)

    def frame_meta(self, n: int) -> list[tuple[int | None, dict[str, float] | None]]:
        """(day, day's FWI codes) for the first ``n`` frames (None, None for single-day runs)."""
        with self._lock:
            days = self.frame_days[:n]
        days += [None] * (n - len(days))
        return [(d, self.day_fwi.get(d) if d is not None else None) for d in days]

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


#: Municipal footprints in the 2026-10 Alberta file (``source`` = ``odb:<code>``): provider
#: and licence attribution (scripts/build_ab_buildings_odb.py, docs/data-sources.md).
ODB_PROVIDERS = {
    "calgary": ("City of Calgary", "Open Government Licence – City of Calgary"),
    "lethbridge": ("City of Lethbridge", "City of Lethbridge – Open Data License"),
    "parkland-county": ("Parkland County", "Open Government License – Parkland County"),
    "grande-prairie": ("City of Grande Prairie", "Open Data License – City of Grande Prairie"),
    "airdrie": ("City of Airdrie", "Open Data Licence – City of Airdrie"),
    "okotoks": ("Town of Okotoks", "Open Government Licence – Town of Okotoks"),
    "cochrane": ("Town of Cochrane", "Open Data Licence – Town of Cochrane"),
    "canmore": ("Town of Canmore", "Town of Canmore Open Data Licence"),
    "banff": ("Town of Banff", "Open Government Licence – Banff"),
}


def _buildings_info(kind: str, path: str, n: int, capped: bool, selection: str,
                    sources: dict | None = None) -> dict:
    """BuildingsSourceInfo fields for a run. ``sources``: selected buildings per ``source``
    code (2026-10 file); municipal sources add their provider to the label and their licence
    attribution."""
    from firesim_api.settings import settings

    name = os.path.basename(path)
    attribution = MS_BUILDINGS_ATTRIBUTION
    if path.lower().endswith(".fgb"):
        label = "Microsoft Canadian Building Footprints, Alberta (2019)"
        odb = sorted(c.split(":", 1)[1] for c in (sources or {}) if c.startswith("odb:"))
        known = [ODB_PROVIDERS[c] for c in odb if c in ODB_PROVIDERS]
        if known:
            names = ", ".join(p for p, _ in known)
            label = (f"{names} building footprints (Statistics Canada ODB v3)"
                     + (" and Microsoft (2019)" if (sources or {}).get("ms2019") else ""))
            attribution = "; ".join(
                ([MS_BUILDINGS_ATTRIBUTION] if (sources or {}).get("ms2019") else [])
                + [f"Contains information licensed under the {lic}" for _, lic in known])
    elif settings.city_buildings_path and name == os.path.basename(settings.city_buildings_path):
        label = "Microsoft Canadian Building Footprints, Edmonton extract (2019)"
    else:
        label, attribution = f"Building footprints {name}", None
    out = {"kind": kind, "label": label, "attribution": attribution,
           "selection": selection, "buildings": int(n), "capped": bool(capped)}
    if sources:
        out["sources"] = {str(k): int(v) for k, v in sorted(sources.items())}
    return out


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


def _rebase_schedule(base, changes, shift_h: float, duration_h: float):
    """A schedule re-based ``shift_h`` hours later: (weather in force at the new start, the
    later steps with their hours counted from it, dropping those at or after ``duration_h``)."""
    from firesim_api.schemas.simulation import WeatherParams

    weather = base
    later = []
    for c in sorted(changes or (), key=lambda c: c.hours_from_start):
        t = c.hours_from_start - shift_h
        if t <= 1e-9:
            weather = WeatherParams(
                wind_speed=c.wind_speed, wind_direction=c.wind_direction,
                temperature=c.temperature, relative_humidity=c.relative_humidity,
                precipitation_24h=base.precipitation_24h)
        elif t < duration_h:
            later.append(c.model_copy(update={"hours_from_start": t}))
    return weather, later


def restart_config(req: PerimeterOverrideRequest,
                   source: SimulationCreate) -> tuple[SimulationCreate, str]:
    """The request a perimeter restart runs as, and where its weather came from.

    The source run's request with the restart's duration, snapshot interval, start time,
    burning period and FFMC spin-up, no ensemble, and the restart's weather, FWI codes and
    schedule when it sends them (the app sends Setup's). Without ``weather`` the source's
    weather and its schedule, re-based to the restart time, apply ("source_run").
    """
    start = req.start_time or source.start_time
    shift = 0.0
    if start is not None and source.start_time is not None:
        shift = (start - source.start_time).total_seconds() / 3600.0
    if req.weather is not None:
        weather, schedule, from_source = req.weather, req.weather_schedule or None, "request"
    elif req.weather_schedule is not None:
        weather, schedule, from_source = source.weather, req.weather_schedule or None, "request"
    else:
        weather, later = _rebase_schedule(source.weather, source.weather_schedule, shift,
                                          req.duration_hours)
        schedule, from_source = later or None, "source_run"
    hourly = None
    if not schedule and source.hourly_weather:
        # The source's forecast stream, its hours counted from the restart time (records
        # outside the API's -24..72 h range dropped)
        hourly = [r.model_copy(update={"hours_from_start": r.hours_from_start - shift})
                  for r in source.hourly_weather if -24.0 <= r.hours_from_start - shift <= 72.0]
    params = source.model_copy(update={
        "weather": weather,
        "fwi_overrides": req.fwi_overrides if req.fwi_overrides is not None
        else source.fwi_overrides,
        "weather_schedule": schedule,
        "hourly_weather": hourly or None,
        "duration_hours": req.duration_hours,
        "snapshot_interval_minutes": req.snapshot_interval_minutes,
        "start_time": start,
        "burning_period": req.burning_period,
        "ffmc_spin_up": req.ffmc_spin_up,
        "ensemble": None,
    })
    return params, from_source


#: Year each fuel source represents (docs/PROJECT_RECORD.md §4.3 data sources)
FUEL_VINTAGE = {"city": 2025, "ab_wildfire": 2025, "cfs_national": 2025,
                "cfs_national_2014": 2011}


def fuel_vintage_year(info: dict | None, national_scheme: str = "cfs_national") -> int | None:
    """Year the run's fuel grid represents: the latest of the sources it used (a scenario
    before it may meet burn scars mapped as grass or non-fuel); None for uniform, synthetic
    or user-supplied grids."""
    if not info:
        return None
    kind = info.get("kind")
    national = FUEL_VINTAGE.get(national_scheme)
    if kind == "city":
        return FUEL_VINTAGE["city"]
    if kind == "national":
        return national
    if kind == "provincial":
        share = info.get("share") or {}
        years = [FUEL_VINTAGE["city"] if k == "city" else FUEL_VINTAGE["ab_wildfire"]
                 if k == "ab_wildfire" else national
                 for k, v in share.items() if v and v > 0]
        years = [y for y in years if y is not None]
        return max(years) if years else FUEL_VINTAGE["ab_wildfire"]
    return None


def fuel_vintage_warning(vintage_year: int | None, start_time) -> str | None:
    """Plain-language flag when the scenario starts before the fuel grid's year."""
    if vintage_year is None or start_time is None or start_time.year >= vintage_year:
        return None
    return (f"Fuel grid represents {vintage_year}. Areas burned since {start_time.year} may be "
            "mapped as grass or non-fuel, not the fuel that burned. Historical replays "
            "overstate spread in old burn scars.")


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
        # Called with the run once its thread ends (terminal status, ensemble done): the app
        # archives it from here (services/archive.py). Errors are logged, never raised.
        self.on_finished: Callable[[SimulationRun], None] | None = None
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
        # Provincial terrain windows (slope/aspect on a fuel window's lattice), same LRU size;
        # each ~10 MB (two 800 x 800 rows of doubles for a 40 km window at 50 m)
        self._terrain_cache: dict[tuple, object] = {}

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
        sim_id = new_run_id()
        run = SimulationRun(sim_id, params)
        run.on_status = on_status

        self._store(sim_id, run)

        # Start simulation in background thread
        self._start(self._execute, run, on_frame)

        return sim_id

    def _store(self, sim_id: str, run: SimulationRun) -> None:
        """Keep a new run; drop the oldest finished runs beyond ``MAX_STORED_RUNS`` (never a
        run still computing, nor one whose ensemble is)."""
        with self._lock:
            self._runs[sim_id] = run
            excess = len(self._runs) - MAX_STORED_RUNS
            for old_id, old in list(self._runs.items()):  # insertion order: oldest first
                if excess <= 0:
                    break
                if old_id == sim_id or old.status in _LIVE or old.ensemble_status == "running":
                    continue
                del self._runs[old_id]
                excess -= 1

    def get(self, sim_id: str) -> SimulationRun | None:
        with self._lock:
            return self._runs.get(sim_id)

    def _start(self, target: Callable[..., None], run: SimulationRun, *args) -> None:
        """Run ``target(run, *args)`` on a daemon thread; when it ends, record the end time
        and hand the run to ``on_finished`` (the run archive). The hook only queues work, and
        an error in it is logged: it never changes the run's result."""
        def body() -> None:
            try:
                target(run, *args)
            finally:
                run.finished_at = time.time()
                hook = self.on_finished
                if hook is not None:
                    try:
                        hook(run)
                    except Exception:  # noqa: BLE001 - the archive must never break a run
                        logger.exception("on_finished hook failed for %s", run.id)
                run.done.set()

        threading.Thread(target=body, daemon=True).start()

    def _get_building_index(self, buildings_path: str, neighbourhoods_path: str | None):
        """Return a BuildingIndex, opening and caching on first call.

        A FlatGeobuf (the Alberta file) is read by window; nothing per building is held. The
        Edmonton neighbourhood selection then applies to the former Edmonton extract's box
        only (``EDMONTON_EXTRACT_BOX``); a GeoJSON file keeps the former whole-file
        behaviour (docs/buildings-alberta.md). Buildings outside the box but within a City
        neighbourhood polygon are assigned to it too (decision 2026-10-12) unless
        ``FIRESIM_EDMONTON_BOX_LEGACY`` is set."""
        from firesim.data.building_index import EDMONTON_EXTRACT_BOX, BuildingIndex

        from firesim_api.settings import settings

        key = (buildings_path, neighbourhoods_path, settings.edmonton_box_legacy)
        with self._building_index_lock:
            if key in self._building_index_cache:
                return self._building_index_cache[key]

        logger.info(
            "BuildingIndex cache MISS: opening buildings=%s nbhd=%s",
            buildings_path, neighbourhoods_path,
        )
        fgb = buildings_path.lower().endswith(".fgb")
        bidx = BuildingIndex(buildings_path, neighbourhoods_path,
                             partition_box=EDMONTON_EXTRACT_BOX if fgb else None,
                             within_outside_box=fgb and not settings.edmonton_box_legacy)

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

    @staticmethod
    def ab_terrain_path() -> str | None:
        """The Alberta elevation file when installed, else None (Edmonton DEM only)."""
        from firesim_api.settings import settings

        p = settings.ab_terrain_path
        return p if p and os.path.exists(p) else None

    @staticmethod
    def _is_city_dem(path: str | None) -> bool:
        from firesim_api.settings import settings

        city = settings.city_dem_path
        if not path or not city:
            return False
        return os.path.realpath(path) == os.path.realpath(city)

    def _terrain_window(self, fuel_grid):
        """Slope/aspect from the Alberta elevation file on the fuel window's own lattice
        (cached, LRU of _WINDOW_CACHE_SIZE); None where the file has no elevation."""
        from firesim.data.provincial import Lattice
        from firesim.data.terrain import terrain_on_lattice

        path = self.ab_terrain_path()
        lat = Lattice.of_grid(fuel_grid)
        key = (path, round(lat.lat_max, 7), round(lat.lng_min, 7), round(lat.dlat, 9),
               round(lat.dlng, 9), lat.rows, lat.cols)
        with self._window_lock:
            if key in self._terrain_cache:
                hit = self._terrain_cache.pop(key)
                self._terrain_cache[key] = hit
                return hit
        terrain = terrain_on_lattice(path, lat)
        with self._window_lock:
            self._terrain_cache[key] = terrain
            while len(self._terrain_cache) > _WINDOW_CACHE_SIZE:
                self._terrain_cache.pop(next(iter(self._terrain_cache)))
        return terrain

    def _window_terrain(self, fuel_grid, dem_path: str | None):
        """(terrain grid, terrain_source dict) for a provincial / national window: the Alberta
        elevation file when installed and the request names the bundled Edmonton DEM (the app
        does when terrain is on); otherwise the request's DEM as before (the Edmonton DEM is
        flat outside Edmonton)."""
        from firesim.data.terrain import (AB_TERRAIN_ATTRIBUTION, AB_TERRAIN_LABEL,
                                          CITY_DEM_LABEL)

        if dem_path and self._is_city_dem(dem_path) and self.ab_terrain_path():
            terrain = self._terrain_window(fuel_grid)
            if terrain is not None:
                return terrain, {"kind": "provincial", "label": AB_TERRAIN_LABEL,
                                 "attribution": AB_TERRAIN_ATTRIBUTION}
        if not dem_path:
            return None, None
        terrain = self._load_grids(None, None, None, dem_path)[2]
        city = self._is_city_dem(dem_path)
        label = (f"{CITY_DEM_LABEL} (flat outside Edmonton)" if city
                 else f"DEM {os.path.basename(dem_path)}")
        return terrain, {"kind": "city_dem" if city else "user_dem", "label": label,
                         "attribution": None}

    @classmethod
    def _dem_terrain_info(cls, dem_path: str | None, terrain) -> dict | None:
        """terrain_source fields for a run that used ``dem_path`` as given."""
        from firesim.data.terrain import CITY_DEM_LABEL

        if not dem_path or terrain is None:
            return None
        if cls._is_city_dem(dem_path):
            return {"kind": "city_dem", "label": CITY_DEM_LABEL, "attribution": None}
        return {"kind": "user_dem", "label": f"DEM {os.path.basename(dem_path)}",
                "attribution": None}

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
        Terrain: the Edmonton DEM on the City lattice; a window of the Alberta elevation file
        for windows (``_window_terrain``). The returned dict carries the terrain_source fields
        under "terrain" (callers pop it).
        """
        from firesim.data.provincial import choose_fuel
        from firesim.fbp.season import apply_season, season_label

        sources = self.fuel_sources()
        choice = choose_fuel(sources, params.ignition_lat, params.ignition_lng,
                             getattr(params, "fuel_type", "C2"))
        info = {"kind": choice.kind, "label": choice.label, "attribution": choice.attribution,
                "season": None, "share": None}
        # Pre-fire back-cast for dated historical scenarios (docs/fuel-grid-backcast.md)
        from firesim_api.services.backcast import plan_backcast

        sources, info["backcast"] = plan_backcast(sources, choice.kind, params)
        fine = None
        if choice.kind == "city":
            fuel_grid, wui_grid, terrain = self._load_grids(
                sources.city_path, water_path, wui_path, dem_path, fill_path=sources.ab_path)
            fine = self._get_fine_source(sources.city_path, water_path, fuel_grid,
                                         fill_path=sources.ab_path)
            info["terrain"] = self._dem_terrain_info(dem_path, terrain)
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
            if stacked.backcast is not None:
                from firesim_api.services.backcast import describe_backcast

                info["backcast"] = describe_backcast(sources, stacked.backcast)
                info["label"] += f" ({info['backcast']['label']})"
            wui_grid = None
            if wui_path:
                from firesim.data.wui_loader import load_wui_modifiers

                wui_grid = load_wui_modifiers(
                    wui_path, bounds=(fuel_grid.lat_min, fuel_grid.lat_max, fuel_grid.lng_min,
                                      fuel_grid.lng_max), rows=fuel_grid.rows, cols=fuel_grid.cols)
            terrain, info["terrain"] = self._window_terrain(fuel_grid, dem_path)
        else:  # uniform: outside every grid
            fuel_grid, wui_grid = None, None
            terrain = self._load_grids(None, None, None, dem_path)[2] if dem_path else None
            info["terrain"] = self._dem_terrain_info(dem_path, terrain)
        if fuel_grid is not None:
            fuel_grid = apply_season(fuel_grid, day_of_year)
            info["season"] = season_label(day_of_year)
            if fine is not None:
                fine = fine.with_season(day_of_year)
        logger.info("Fuel source: %s (%s)", choice.kind, choice.label)
        return fuel_grid, wui_grid, terrain, fine, info

    def _set_terrain_source(self, run, dem_path: str | None, terrain_grid) -> None:
        """run.terrain_source: from resolve_provincial's "terrain" entry, else the DEM used."""
        info = run.fuel_source if isinstance(run.fuel_source, dict) else {}
        if "terrain" in info:
            run.terrain_source = info.pop("terrain")
        else:
            run.terrain_source = self._dem_terrain_info(dem_path, terrain_grid)

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

    def _engine_config(self, params: SimulationCreate, **over) -> SimulationConfig:
        """Engine config for a single run (``over``: fields that replace the request's)."""
        fwi = params.fwi_overrides
        kw = dict(
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
            ffmc=fwi.ffmc if fwi and fwi.ffmc is not None else 85.0,
            dmc=fwi.dmc if fwi and fwi.dmc is not None else 40.0,
            dc=fwi.dc if fwi and fwi.dc is not None else 200.0,
            **_fuel_kwargs(params),
            start_hour=params.start_hour(),
            burning_period=_burning_period(params),
            seed=getattr(params, "seed", None),
        )
        kw.update(over)
        if "hourly_weather" not in kw:
            kw["hourly_weather"] = params.engine_hourly_weather()
        return SimulationConfig(**kw)

    def _prepare_inputs(self, run: SimulationRun, params: SimulationCreate,
                        config: SimulationConfig) -> dict:
        """Fuel, terrain, WUI grids and buildings for a single run or a perimeter restart (one
        path, so a restart runs on exactly the grids, building mask, 20 m window source and
        structure inputs of a normal run with the same request).

        Returns a dict with ``fuel_grid`` (building-masked), ``spread_modifier_grid``,
        ``terrain_grid``, ``building_centroids``, ``building_geoms``, ``structure_geoms`` and
        ``fine_fuel``.
        """
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
        run.fuel_source["vintage_year"] = fuel_vintage_year(
            run.fuel_source, settings.national_fuel_scheme)
        self._set_terrain_source(run, dem_path, terrain_grid)

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
                # Structure spread reaches every building in the run area (the former
                # extract box is no longer a limit, decision 2026-10-12); the legacy
                # setting restores the clip for equivalence checks.
                source = (bidx.clipped(EDMONTON_EXTRACT_BOX)
                          if is_fgb and settings.edmonton_box_legacy else bidx)
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
                     "in the run area"), near.sources)
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

        # 20 m WUI window (mechanics decision M5): tried when buildings are near the fire,
        # kept only within the memory guards (firesim.spread.wui_window)
        fine_fuel = None
        if building_geoms:
            fine_fuel = (prov_fine if provincial
                         else self._get_fine_source(*fine_key, fuel_grid))
        return {
            "fuel_grid": masked_fuel_grid,
            "spread_modifier_grid": spread_modifier_grid,
            "terrain_grid": terrain_grid,
            "building_centroids": building_centroids,
            "building_geoms": building_geoms,
            "structure_geoms": structure_geoms,
            "fine_fuel": fine_fuel,
        }

    def _simulator(self, run: SimulationRun, config: SimulationConfig, params: SimulationCreate,
                   prep: dict, **extra) -> Simulator:
        """The Simulator for a single run or a restart, with the request's spotting and
        house-to-house options (``extra``: e.g. initial_front, active_edges)."""
        try:
            fuel_type = FuelType(params.fuel_type)
        except ValueError:
            fuel_type = FuelType.C2
        structure_geoms = prep["structure_geoms"]
        bridge = getattr(params, "structure_vegetation_bridge", False)
        return Simulator(
            config,
            fuel_grid=prep["fuel_grid"],
            terrain_grid=prep["terrain_grid"],
            default_fuel=fuel_type,
            spread_modifier_grid=prep["spread_modifier_grid"],
            enable_spotting=getattr(params, "enable_spotting", False),
            spotting_intensity=getattr(params, "spotting_intensity", 1.0),
            building_centroids=prep["building_centroids"],
            building_footprints=prep["building_geoms"] or None,
            structure_spread=getattr(params, "structure_spread", False),
            structure_footprints=structure_geoms,
            structure_embers=getattr(params, "structure_embers", False),
            structure_design_fire_kw_m2=getattr(params, "structure_design_fire_kw_m2", 150),
            structure_burnout=getattr(params, "structure_burnout", True),
            structure_vegetation=(self._get_building_vegetation()
                                  if structure_geoms is not None else None),
            structure_vegetation_bridge=bridge,
            structure_canopy=self._get_canopy() if bridge else None,
            structure_combustible_roof_share=getattr(
                params, "structure_combustible_roof_share", 0.0),
            progress=run.set_phase,
            fine_fuel=prep["fine_fuel"],
            fine_building_mask=prep["building_geoms"] or None,
            **extra,
        )

    def _execute(
        self,
        run: SimulationRun,
        on_frame: Callable[[str, SimulationFrame], None] | None,
    ) -> None:
        """Execute a simulation run."""
        run.status = SimulationStatus.RUNNING
        params = run.config

        try:
            config = self._engine_config(params)
            prep = self._prepare_inputs(run, params, config)
            simulator = self._simulator(run, config, params, prep)

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
                if ens is not None and prep["fuel_grid"] is not None:
                    self._run_ensemble(run, config, prep["fuel_grid"], prep["terrain_grid"],
                                       prep["spread_modifier_grid"], ens)
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
        # wait for a free ensemble slot (status stays "pending"); give up if cancelled
        while not _ENSEMBLE_SLOTS.acquire(timeout=0.5):
            if run._ensemble_cancel.is_set():
                run.ensemble_status = "cancelled"
                return
        try:
            SimulationRunner._run_ensemble_slot(run, config, fuel_grid, terrain_grid,
                                                spread_modifier_grid, ens)
        finally:
            _ENSEMBLE_SLOTS.release()

    @staticmethod
    def _run_ensemble_slot(run, config, fuel_grid, terrain_grid, spread_modifier_grid, ens) -> None:
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
        on_status: Callable[[str, str, float | None], None] | None = None,
    ) -> str:
        """Create and start a multi-day fire scenario.

        Chains one 24-hour Huygens simulation per day. The FWI system
        carries moisture codes (FFMC/DMC/DC) forward between days using
        CFFDRS daily equations. The final fire front from each day is used
        as the starting front for the next day.

        Args:
            params: Multi-day simulation parameters.
            on_frame: Callback (sim_id, frame, day_number) for each frame.
            on_status: Optional callback (sim_id, phase, progress) for progress display;
                progress is over the whole run, and ``run.day`` / ``run.n_days`` say which day.

        Returns:
            Simulation ID.
        """
        sim_id = new_run_id()
        run = SimulationRun(sim_id, params)
        run.on_status = on_status
        run.n_days = len(params.days)

        self._store(sim_id, run)

        self._start(self._execute_multiday, run, params, on_frame)

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
        n_days = len(params.days)
        run.n_days = n_days

        try:
            run.day = 1
            run.set_phase("loading")
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
            run.fuel_source["vintage_year"] = fuel_vintage_year(
                run.fuel_source, settings.national_fuel_scheme)
            self._set_terrain_source(run, dem_path, terrain_grid)

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
                run.day = day_num
                # Progress over the whole run: day k's spread fills [k-1, k] / n_days. The
                # grid model streams no frames until a day is computed, so without these
                # messages a 3-day run showed "Starting…" for its whole 35 min (R12), and the
                # socket carried nothing for minutes at a time.
                run.set_phase("spread", day_idx / n_days)

                def day_progress(phase: str, frac: float | None = None, _k: int = day_idx) -> None:
                    # The day's own phases (spread fraction, finishing) report as the run's
                    # spread; set_phase also stops the computation when the run is cancelled
                    if phase == "spread" and frac is not None:
                        run.set_phase("spread", (_k + frac) / n_days)
                    else:
                        run.set_phase("spread")

                # Advance FWI state using today's weather (CFFDRS daily equations)
                day_fwi = fwi_calc.calculate_daily(
                    temp=day_weather.temperature,
                    rh=day_weather.relative_humidity,
                    wind=day_weather.wind_speed,
                    rain=day_weather.precipitation_24h,
                    month=params.month,
                )
                run.day_fwi[day_num] = {
                    k: round(float(getattr(day_fwi, k)), 1)
                    for k in ("ffmc", "dmc", "dc", "isi", "bui", "fwi")
                }

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
                    progress=day_progress,
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

                    run.add_frame(adjusted, day=day_num)
                    run.progress = max(run.progress or 0.0,
                                       min(1.0, adjusted.time_hours / (24.0 * n_days)))
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

        except RunCancelled:
            run.status = SimulationStatus.CANCELLED
            logger.info("Multi-day simulation %s cancelled during computation", run.id)
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
        """Create a new simulation seeded from an observed (e.g. RPAS) fire perimeter.

        The restart is the source run's request with this request's weather, FWI codes,
        schedule, duration, start time and burning period (``restart_config``), run through
        the same path as a normal run (grids, building mask, exposure, house-to-house), from
        the observed perimeter instead of the ignition point.

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

        params, weather_source = restart_config(req, original.config)
        if params.start_time is None and (req.burning_period is not None or req.ffmc_spin_up):
            raise ValueError("burning_period / ffmc_spin_up need start_time (on the request or "
                             "the source simulation)")
        # Local start hour and hourly stream (the restart's schedule, or the source's
        # forecast re-based to the restart time); ValueError (422) if it cannot spin up
        clock = (params.start_hour(), params.engine_hourly_weather())

        logger.info(
            "Perimeter override: source=%s vertices=%d dur=%.1fh weather=%s",
            req.simulation_id, len(initial_front), req.duration_hours, weather_source,
        )

        sim_id = new_run_id()
        run = SimulationRun(sim_id, params)
        run.on_status = on_status
        run.kind = "perimeter_override"
        run.override_request = req.model_dump(mode="json")
        run.source_config = original.config
        run.weather_source = weather_source

        self._store(sim_id, run)

        self._start(self._execute_perimeter_override, run, params, initial_front, req,
                    on_frame, clock)

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
        """Execute a restart from an observed fire front (``params``: ``restart_config``).

        ``clock`` is (local start hour, engine hourly weather) of the restart.
        """
        run.status = SimulationStatus.RUNNING

        try:
            config = self._engine_config(params, start_hour=clock[0], hourly_weather=clock[1])
            prep = self._prepare_inputs(run, params, config)
            simulator = self._simulator(
                run, config, params, prep, initial_front=initial_front,
                active_edges=req.active_edges, active_edge_buffer_m=req.active_edge_buffer_m)

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
