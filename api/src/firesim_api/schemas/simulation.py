"""Pydantic models for simulation endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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

    hours_from_start: float = Field(..., ge=0, le=72, description="Hours after the simulation start")
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


    @field_validator("start_time")
    @classmethod
    def _start_time_has_offset(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("start_time needs a UTC offset (e.g. -06:00 or Z)")
        return v

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


class SimulationResponse(BaseModel):
    """Response from simulation creation or status query."""

    simulation_id: str
    status: SimulationStatus
    config: SimulationCreate | None = None
    frames: list[SimulationFrame] = []
    error: str | None = None


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
