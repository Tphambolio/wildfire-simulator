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
    use_ca_mode: bool = Field(
        default=False,
        description=(
            "Force cellular automaton spread model. When True and no fuel_grid_path "
            "is supplied, a synthetic mixed-fuel landscape is generated around the "
            "ignition point for demo/testing purposes."
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


class SimulationResponse(BaseModel):
    """Response from simulation creation or status query."""

    simulation_id: str
    status: SimulationStatus
    config: SimulationCreate | None = None
    frames: list[SimulationFrame] = []
    error: str | None = None
