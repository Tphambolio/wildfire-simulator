"""Pydantic models for simulation endpoints."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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


class FWIOverrides(BaseModel):
    """Optional FWI component overrides."""

    ffmc: float | None = Field(default=None, ge=0, le=101, description="Fine Fuel Moisture Code")
    dmc: float | None = Field(default=None, ge=0, description="Duff Moisture Code")
    dc: float | None = Field(default=None, ge=0, description="Drought Code")


class SimulationCreate(BaseModel):
    """Request body for creating a new simulation."""

    ignition_lat: float = Field(..., ge=-90, le=90, description="Ignition latitude")
    ignition_lng: float = Field(..., ge=-180, le=180, description="Ignition longitude")
    weather: WeatherParams
    fwi_overrides: FWIOverrides | None = None
    duration_hours: float = Field(default=4.0, gt=0, le=24, description="Simulation duration (hours)")
    snapshot_interval_minutes: float = Field(
        default=30.0, gt=0, le=120, description="Snapshot interval (minutes)"
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
            "DEM and used in the CFFDRS ISF → RSF slope correction (ST-X-3 §3.3). "
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
    """Starting FWI state (Day 0 carry-in). If None, uses spring startup defaults."""
    month: int = Field(default=6, ge=1, le=12, description="Month (1-12) for FWI day-length factors")
    snapshot_interval_minutes: float = Field(default=30.0, gt=0, le=120)
    fuel_type: str = Field(default="C2", description="Fuel type code")
    fuel_grid_path: str | None = Field(default=None, description="Path to fuel type GeoTIFF")
    water_path: str | None = Field(default=None, description="Path to water bodies GeoJSON")
    buildings_path: str | None = Field(default=None, description="Path to buildings GeoJSON")
    dem_path: str | None = Field(default=None, description="Path to DEM GeoTIFF for slope-adjusted spread")


class PerimeterOverrideRequest(BaseModel):
    """Request to override simulated fire perimeter with drone reconnaissance data.

    The corrected perimeter (GeoJSON geometry) replaces the model-predicted
    front and seeds a fresh Huygens spread run, closing the loop between
    simulation and ground truth during an active incident.
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
        description="Path to DEM GeoTIFF for slope-adjusted spread (CFFDRS ISF → RSF)",
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
