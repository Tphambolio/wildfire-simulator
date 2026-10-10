"""Pydantic models for simulation endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class WeatherParams(BaseModel):
    """Weather input for simulation."""

    wind_speed: float = Field(..., ge=0, le=100, description="Wind speed in km/h")
    wind_direction: float = Field(
        ..., ge=0, lt=360, description="Wind direction (degrees, meteorological FROM)"
    )
    temperature: float = Field(
        default=25.0, ge=-40, le=50, description="Temperature in Celsius"
    )
    relative_humidity: float = Field(
        default=30.0, ge=0, le=100, description="Relative humidity (%)"
    )
    precipitation_24h: float = Field(
        default=0.0, ge=0, description="24-hour precipitation (mm)"
    )


class HourlyWeatherParams(BaseModel):
    """Weather for one hour of a simulation (applies until the next record)."""

    hours_from_start: float = Field(
        ..., ge=-24, le=72,
        description=(
            "Hours after the simulation start. Negative hours (back to -24) are before the "
            "start: used only for the FFMC spin-up (ffmc_spin_up), otherwise dropped."
        ),
    )
    temperature: float = Field(..., ge=-40, le=50, description="Temperature (C)")
    relative_humidity: float = Field(..., ge=0, le=100, description="Relative humidity (%)")
    wind_speed: float = Field(..., ge=0, le=100, description="10 m wind speed (km/h)")
    wind_direction: float = Field(..., ge=0, lt=360, description="Wind direction (degrees FROM)")
    precipitation: float = Field(default=0.0, ge=0, description="Rain in the hour (mm)")


class FWIOverrides(BaseModel):
    """Optional FWI component overrides."""

    ffmc: float | None = Field(default=None, ge=0, le=101, description="Fine Fuel Moisture Code")
    dmc: float | None = Field(default=None, ge=0, description="Duff Moisture Code")
    dc: float | None = Field(default=None, ge=0, description="Drought Code")


class FuelModifiers(BaseModel):
    """FBP fuel and foliage inputs (ST-X-3 / Wotton et al. 2009)."""

    grass_cure: float = Field(
        default=60.0, ge=0, le=100, description="Degree of grass curing (%) for O-1a/O-1b"
    )
    grass_fuel_load: float = Field(
        default=0.35, gt=0, le=5, description="Grass fuel load (kg/m2) for O-1a/O-1b"
    )
    percent_conifer: float = Field(default=50.0, ge=0, le=100, description="Percent conifer for M-1/M-2")
    percent_dead_fir: float = Field(
        default=35.0, ge=0, le=100, description="Percent dead balsam fir for M-3/M-4"
    )
    fmc: float | None = Field(
        default=None, gt=0, le=300,
        description="Foliar moisture content (%). If omitted, computed from location and day_of_year.",
    )
    day_of_year: int | None = Field(
        default=None, ge=1, le=366,
        description="Julian day for the ST-X-3 foliar moisture model (100 % used if omitted)",
    )
    elevation_m: float | None = Field(default=None, description="Elevation (m) for the foliar moisture model")

    def config_kwargs(self) -> dict:
        """Keyword arguments for firesim.types.SimulationConfig."""
        return self.model_dump()


class BurningPeriod(BaseModel):
    """Daily burning period: local clock hours between which fire spreads (none outside).

    Also accepted as a two-item list ``[start_hour, end_hour]``. Hours are on the local clock
    of the request's ``start_time`` (its own UTC offset). 10-20 h was chosen on calibration
    fires and tested on held-out Alberta fires (docs/validation.md).
    """

    start_hour: float = Field(..., ge=0, le=24, description="Local clock hour the period opens (0-24)")
    end_hour: float = Field(
        ..., ge=0, le=24, description="Local clock hour the period closes (0-24, after start_hour)"
    )

    @model_validator(mode="after")
    def _start_before_end(self) -> BurningPeriod:
        if not self.start_hour < self.end_hour:
            raise ValueError("burning_period start_hour must be before end_hour")
        return self

    def as_tuple(self) -> tuple[float, float]:
        return (self.start_hour, self.end_hour)


def burning_period_from_list(v):
    """Accept ``[start, end]`` as well as ``{"start_hour": ..., "end_hour": ...}``."""
    if isinstance(v, (list, tuple)):
        if len(v) != 2:
            raise ValueError("burning_period list must be [start_hour, end_hour]")
        return {"start_hour": v[0], "end_hour": v[1]}
    return v


def local_start_hour(start_time: datetime | None) -> float | None:
    """Clock hour of ``start_time`` in its own UTC offset (13.5 for 13:30-06:00)."""
    if start_time is None:
        return None
    return start_time.hour + start_time.minute / 60.0 + start_time.second / 3600.0


def engine_hourly(records, shift_hours: float, start_hour: float | None, ffmc_spin_up: bool):
    """Engine hourly records from API records re-based ``shift_hours`` later, with the spin-up
    hours kept or dropped (``firesim.spread.diurnal.hourly_for_run``; ValueError if unusable)."""
    from firesim.spread.diurnal import hourly_for_run
    from firesim.types import HourlyWeather

    recs = [
        HourlyWeather(
            hours_from_start=r.hours_from_start - shift_hours, temperature=r.temperature,
            relative_humidity=r.relative_humidity, wind_speed=r.wind_speed,
            wind_direction=r.wind_direction, precipitation=r.precipitation,
        )
        for r in records or ()
    ]
    return hourly_for_run(recs, start_hour, ffmc_spin_up)


BURNING_PERIOD_DOC = (
    'Optional daily burning period, e.g. {"start_hour": 10, "end_hour": 20} or [10, 20]: fire '
    "spreads only between these local clock hours (the clock of start_time, which is then "
    "required) and not at all outside them. Off by default. 10-20 h with ffmc_spin_up was "
    "chosen on calibration fires and raised one-day skill on held-out Alberta fires "
    "(docs/validation.md)."
)
SPIN_UP_DOC = (
    "Start the hourly FFMC at 17:00 local (start_time's clock) on or before the start, from the "
    "daily FFMC (fwi_overrides.ffmc, taken as the value at 17:00, about 16:00 LST; Lawson et "
    "al. 1996), and run it through the night on hourly_weather. Needs start_time and "
    "hourly_weather records back to that 17:00 (negative hours_from_start) and for the run. "
    "Off by default; when off, records before the start are dropped."
)


class EnsembleParams(BaseModel):
    """Ensemble run after the deterministic one (grid runs). Default perturbation sizes are
    the calibrated ones of firesim/spread/ensemble.py (DEFAULT_SIGMAS; sources and held-out
    scores in docs/validation.md "Ensemble calibration")."""

    n_members: int = Field(default=30, ge=5, le=200)
    seed: int = 1
    wind_dir_sd_deg: float = Field(default=24.0, ge=0, le=90)
    wind_speed_log_sd: float = Field(default=0.405, ge=0, le=1)
    ffmc_sd: float = Field(default=7.2, ge=0, le=10)
    dmc_dc_log_sd: float = Field(default=0.6, ge=0, le=1)
    curing_sd: float = Field(default=13.5, ge=0, le=50)
    fmc_sd: float = Field(default=15.0, ge=0, le=30)
    ros_log_sd: float = Field(default=0.825, ge=0, le=1)
    ignition_jitter_m: float = Field(default=0.0, ge=0, le=2000)


class SimulationCreate(BaseModel):
    """Request body for creating a new simulation."""

    ignition_lat: float = Field(..., ge=-90, le=90, description="Ignition latitude")
    ignition_lng: float = Field(..., ge=-180, le=180, description="Ignition longitude")
    weather: WeatherParams
    fwi_overrides: FWIOverrides | None = None
    fuel_modifiers: FuelModifiers = Field(default_factory=FuelModifiers)
    duration_hours: float = Field(default=4.0, gt=0, le=24, description="Simulation duration (hours)")
    snapshot_interval_minutes: float = Field(
        default=30.0, gt=0, le=120, description="Snapshot interval (minutes)"
    )
    hourly_weather: list[HourlyWeatherParams] | None = Field(
        default=None,
        description=(
            "Optional hourly weather stream. Each record sets wind, temperature, RH and rain from "
            "its hour until the next; FFMC starts from fwi_overrides.ffmc and follows the hourly "
            "FFMC model. DMC and DC stay fixed. Without it, `weather` applies throughout."
        ),
    )
    start_time: datetime | None = Field(
        default=None,
        description=(
            "Scenario start (ignition) time, ISO 8601 with a UTC offset, e.g. "
            "2026-04-28T13:40:00-06:00. Frame times are hours after it. When "
            "fuel_modifiers.day_of_year is not set, its local date sets the day of year for the "
            "foliar moisture model. hourly_weather records count hours from this time."
        ),
    )
    ensemble: EnsembleParams | None = Field(
        default=None,
        description=(
            "Grid runs: after the deterministic run completes, run an ensemble and serve "
            "P10/P50/P90 arrival and burn probability at GET /simulations/{id}/ensemble."
        ),
    )
    burning_period: BurningPeriod | None = Field(default=None, description=BURNING_PERIOD_DOC)
    ffmc_spin_up: bool = Field(default=False, description=SPIN_UP_DOC)
    cells_mode: Literal["cumulative", "incremental"] = Field(
        default="cumulative",
        description=(
            "Grid runs: 'cumulative' sends every burned cell so far in each frame; 'incremental' "
            "sends only the cells burned since the previous frame (frame.cells_offset says how "
            "many earlier cells were left out). Multi-day runs are always cumulative."
        ),
    )
    fuel_type: str = Field(default="C2", description="Default fuel type code")
    fuel_grid_path: str | None = Field(
        default=None,
        description="Path to FBP fuel type GeoTIFF raster (overrides uniform fuel_type)",
    )
    water_path: str | None = Field(
        default=None,
        description="Path to water body GeoJSON for non-fuel masking",
    )
    buildings_path: str | None = Field(
        default=None,
        description="Path to building footprint GeoJSON for non-fuel masking",
    )
    wui_zones_path: str | None = Field(
        default=None,
        description="Path to WUI zones GeoJSON with spread modifiers",
    )
    dem_path: str | None = Field(
        default=None,
        description=(
            "Path to Digital Elevation Model GeoTIFF for slope-adjusted spread. "
            "When provided, slope (%) and aspect (°) are derived per cell from the "
            "DEM and folded into the FBP net effective wind speed (ST-X-3 eqs 39-50). "
            "Overrides FIRESIM_DEM_PATH if set."
        ),
    )
    use_ca_mode: bool = Field(
        default=False,
        description=(
            "Force cellular automaton spread model. When True and no fuel_grid_path "
            "is supplied, loads the real fuel grid from the FIRESIM_FUEL_GRID_PATH "
            "environment variable if set; otherwise generates a synthetic mixed-fuel "
            "landscape around the ignition point for demo/testing purposes."
        ),
    )
    enable_spotting: bool = Field(
        default=False,
        description=(
            "Enable Albini (1979) ember spotting model. When True, wind-lofted embers "
            "from high-intensity crown fire cells seed new ignitions downwind, "
            "dramatically increasing spread under high-FWI conditions."
        ),
    )
    spotting_intensity: float = Field(
        default=1.0,
        ge=0.0,
        le=5.0,
        description=(
            "Multiplier on spot fire probability (1.0 = baseline, 0 = no spotting, "
            ">1 = increased ember density). Only used when enable_spotting is True."
        ),
    )
    seed: int | None = Field(
        default=None,
        description=(
            "Seed for the stochastic parts of the run (ember spotting). Omit to derive it "
            "from the other inputs, so identical requests give identical results; set it "
            "to rerun the same inputs with different random draws."
        ),
    )
    structure_spread: bool = Field(
        default=False,
        description=(
            "Opt-in, illustrative — not validated in Canada. Grid runs with building "
            "footprints: Hamada building-to-building spread between building units, started "
            "from the units the wildland front reaches (docs/structure-spread-spec.md). "
            "Frames then carry `structure_spread` counts of modelled involvement. Not a "
            "prediction of which buildings burn."
        ),
    )


    @field_validator("start_time")
    @classmethod
    def _start_time_has_offset(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("start_time needs a UTC offset (e.g. -06:00 or Z)")
        return v

    @field_validator("burning_period", mode="before")
    @classmethod
    def _burning_period_list(cls, v):
        return burning_period_from_list(v)

    @model_validator(mode="after")
    def _diurnal_options_need_clock(self) -> SimulationCreate:
        if self.burning_period is not None and self.start_time is None:
            raise ValueError("burning_period needs start_time (its local clock sets the hours)")
        if self.ffmc_spin_up:
            self.engine_hourly_weather()  # ValueError (422) if the stream cannot spin up
        return self

    def start_hour(self) -> float | None:
        """Local clock hour of the start (start_time's own offset)."""
        return local_start_hour(self.start_time)

    def engine_hourly_weather(self):
        """Engine hourly records: spin-up hours kept (ffmc_spin_up) or dropped."""
        return engine_hourly(self.hourly_weather, 0.0, self.start_hour(), self.ffmc_spin_up)

    def fuel_config_kwargs(self) -> dict:
        """Fuel modifiers for SimulationConfig, with the day of year from start_time if unset."""
        kw = self.fuel_modifiers.config_kwargs()
        if kw.get("day_of_year") is None and self.start_time is not None:
            kw["day_of_year"] = self.start_time.timetuple().tm_yday
        return kw

class SimulationStatus(str, Enum):
    """Simulation run status."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SimulationFrame(BaseModel):
    """A single simulation snapshot."""

    time_hours: float
    perimeter: list[list[float]]  # [[lat, lng], ...]
    area_ha: float
    head_ros_m_min: float
    max_hfi_kw_m: float
    fire_type: str
    flame_length_m: float
    fuel_breakdown: dict[str, float]
    spot_fires: list[dict] | None = None
    num_fronts: int = 1
    burned_cells: list[dict] | None = None
    day: int | None = None  # Multi-day scenario: which day (1-based)
    buildings_at_risk: int = 0  # building centroids inside the perimeter
    ignition_snapped_m: float = 0.0  # >0 if the ignition was moved to the nearest fuel cell
    # Building exposure (grid model with building footprints): counts reached by this frame.
    # Exposure, not ignition probability; see docs/building-exposure.md.
    building_exposure: dict[str, int] | None = None
    # Per-building exposure for buildings within 500 m of the fire (final frame only)
    building_exposure_detail: list[dict] | None = None
    # Grid model: fastest head cell reached since the previous frame
    # {lat, lng, ros, raz, hfi, cfb, fuel, t, max_spot_distance_m}
    head: dict | None = None
    # Incremental cells: number of earlier cells not repeated in burned_cells (0 = all cells)
    cells_offset: int = 0
    # Opt-in structure-to-structure spread (request `structure_spread`): counts of building
    # units with modelled involvement by this frame, with "label": "illustrative — not
    # validated in Canada". Not a prediction of which buildings burn.
    structure_spread: dict | None = None
    # Final frame only: involved units for the map (id, t_h, mechanism, source_id, polygon);
    # illustrative, display only, never exported (docs/api-reference.md)
    structure_spread_detail: list[dict] | None = None


class SimulationResponse(BaseModel):
    """Response from simulation creation or status query."""

    simulation_id: str
    status: SimulationStatus
    config: SimulationCreate | None = None
    frames: list[SimulationFrame] = []
    error: str | None = None
    #: Progress for display while running: "loading", "buildings", "spread", "structures",
    #: "finishing" (None before the run starts or for multi-day runs)
    phase: str | None = None
    #: Fraction of the spread computed, 0-1 (None until the spread starts)
    progress: float | None = None


class DayWeatherParams(BaseModel):
    """Weather conditions for one day of a multi-day scenario."""

    wind_speed: float = Field(..., ge=0, le=100, description="Wind speed in km/h")
    wind_direction: float = Field(..., ge=0, lt=360, description="Wind direction (degrees FROM)")
    temperature: float = Field(default=25.0, ge=-40, le=50, description="Noon temperature (°C)")
    relative_humidity: float = Field(default=30.0, ge=0, le=100, description="Noon RH (%)")
    precipitation_24h: float = Field(default=0.0, ge=0, description="24-hour precipitation (mm)")


class MultiDaySimulationCreate(BaseModel):
    """Request body for a multi-day fire scenario (24h/48h/72h).

    Each entry in `days` represents one 24-hour period. The FWI system
    carries FFMC/DMC/DC forward between days using CFFDRS daily equations.
    The fire perimeter from the end of each day is used as the starting
    front for the next day.
    """

    ignition_lat: float = Field(..., ge=-90, le=90, description="Ignition latitude")
    ignition_lng: float = Field(..., ge=-180, le=180, description="Ignition longitude")
    days: list[DayWeatherParams] = Field(
        ..., min_length=1, max_length=7, description="Weather for each 24-hour period"
    )
    fwi_overrides: FWIOverrides | None = None
    fuel_modifiers: FuelModifiers = Field(default_factory=FuelModifiers)
    """Starting FWI state (Day 0 carry-in). If None, uses spring startup defaults."""
    month: int = Field(default=6, ge=1, le=12, description="Month (1-12) for FWI day-length factors")
    snapshot_interval_minutes: float = Field(default=30.0, gt=0, le=120)
    fuel_type: str = Field(default="C2", description="Fuel type code")
    fuel_grid_path: str | None = Field(default=None, description="Path to fuel type GeoTIFF")
    water_path: str | None = Field(default=None, description="Path to water bodies GeoJSON")
    buildings_path: str | None = Field(default=None, description="Path to buildings GeoJSON")
    dem_path: str | None = Field(default=None, description="Path to DEM GeoTIFF for slope-adjusted spread")
    start_time: datetime | None = Field(
        default=None,
        description=(
            "Scenario start, ISO 8601 with a UTC offset. Each day runs 24 h from this clock "
            "time. Needed for burning_period."
        ),
    )
    burning_period: BurningPeriod | None = Field(
        default=None,
        description=BURNING_PERIOD_DOC + " Applied every day. (No FFMC spin-up for multi-day "
        "runs: they have daily weather only.)",
    )

    @field_validator("start_time")
    @classmethod
    def _start_time_has_offset(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("start_time needs a UTC offset (e.g. -06:00 or Z)")
        return v

    @field_validator("burning_period", mode="before")
    @classmethod
    def _burning_period_list(cls, v):
        return burning_period_from_list(v)

    @model_validator(mode="after")
    def _burning_period_needs_clock(self) -> MultiDaySimulationCreate:
        if self.burning_period is not None and self.start_time is None:
            raise ValueError("burning_period needs start_time (its local clock sets the hours)")
        return self


class PerimeterOverrideRequest(BaseModel):
    """Request to restart a simulation from an observed (e.g. drone) fire perimeter.

    The observed perimeter (GeoJSON geometry) replaces the model-predicted front and starts
    a new run as an established fire, with the grid model when the original run had a fuel
    grid (otherwise Huygens).
    """

    simulation_id: str = Field(
        ...,
        description="ID of a running or completed simulation whose config is reused",
    )
    perimeter_geojson: dict = Field(
        ...,
        description=(
            "GeoJSON Polygon or MultiPolygon *geometry* (not Feature) "
            "representing the drone-observed fire perimeter. "
            "Coordinates must be [lng, lat] per GeoJSON standard (RFC 7946)."
        ),
    )
    duration_hours: float = Field(
        default=4.0, gt=0, le=24,
        description="Spread prediction duration from corrected perimeter (hours)",
    )
    snapshot_interval_minutes: float = Field(
        default=30.0, gt=0, le=120,
        description="Snapshot interval for new frames (minutes)",
    )
    active_edges: dict | None = Field(
        default=None,
        description=(
            "Optional GeoJSON *geometry* (lng/lat) marking where the observed fire is still "
            "active, e.g. hot edges or heat from an RPAS thermal flight: LineString / "
            "MultiLineString along active edges, Polygon / MultiPolygon of active zones, or "
            "Point / MultiPoint hotspots. Parts of the perimeter within active_edge_buffer_m "
            "of it spread; the rest of the burned area is treated as burned out and does not "
            "spread (fire from active edges can still reach the fuel beyond it later). Omit "
            "to treat the whole perimeter as active. Grid model only (the source simulation "
            "needs a fuel grid)."
        ),
    )
    active_edge_buffer_m: float | None = Field(
        default=None, ge=0, le=5000,
        description="Distance (m) from active_edges within which burned cells are active "
                    "(default: one fuel-grid cell)",
    )
    start_time: datetime | None = Field(
        default=None,
        description=(
            "Time of the observed perimeter (the restart), ISO 8601 with a UTC offset. Default: "
            "the source simulation's start_time. The source's hourly_weather is re-based to it."
        ),
    )
    burning_period: BurningPeriod | None = Field(
        default=None,
        description=BURNING_PERIOD_DOC + " Not inherited from the source run; needs start_time "
        "here or on the source.",
    )
    ffmc_spin_up: bool = Field(
        default=False,
        description=SPIN_UP_DOC + " Uses the source run's hourly_weather, re-based to start_time.",
    )

    @field_validator("start_time")
    @classmethod
    def _start_time_has_offset(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("start_time needs a UTC offset (e.g. -06:00 or Z)")
        return v

    @field_validator("burning_period", mode="before")
    @classmethod
    def _burning_period_list(cls, v):
        return burning_period_from_list(v)

    @field_validator("active_edges")
    @classmethod
    def _active_edges_is_geometry(cls, v: dict | None) -> dict | None:
        allowed = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                   "MultiPolygon", "GeometryCollection"}
        if v is not None and v.get("type") not in allowed:
            raise ValueError("active_edges must be a GeoJSON geometry: "
                             + ", ".join(sorted(allowed)))
        return v


class BurnProbabilityRequest(BaseModel):
    """Request body for Monte Carlo burn probability analysis."""

    ignition_lat: float = Field(..., ge=-90, le=90, description="Ignition latitude")
    ignition_lng: float = Field(..., ge=-180, le=180, description="Ignition longitude")
    weather: WeatherParams
    fwi_overrides: FWIOverrides | None = None
    fuel_modifiers: FuelModifiers = Field(default_factory=FuelModifiers)
    duration_hours: float = Field(default=4.0, gt=0, le=24, description="Duration per iteration (hours)")
    n_iterations: int = Field(default=100, ge=1, le=500, description="Number of Monte Carlo iterations")
    jitter_m: float = Field(default=100.0, ge=0, le=1000, description="Ignition point jitter radius (metres)")
    wind_speed_pct: float = Field(default=10.0, ge=0, le=50, description="Wind speed variation (±%)")
    rh_abs: float = Field(default=5.0, ge=0, le=30, description="Relative humidity variation (±absolute %)")
    base_seed: int = Field(default=42, description="Base random seed for reproducibility")
    fuel_grid_path: str | None = Field(default=None, description="Path to GeoTIFF fuel raster")
    water_path: str | None = Field(default=None, description="Path to water bodies GeoJSON")
    buildings_path: str | None = Field(default=None, description="Path to buildings GeoJSON")
    dem_path: str | None = Field(
        default=None,
        description="Path to DEM GeoTIFF for slope-adjusted spread (ST-X-3 net effective wind)",
    )


class BurnProbabilityResponse(BaseModel):
    """Response from Monte Carlo burn probability analysis."""

    burn_probability: list[list[float]]  # 2D array [rows][cols], values [0, 1]
    rows: int
    cols: int
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    n_iterations: int
    iterations_completed: int
    cell_size_m: float
