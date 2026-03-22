"""Shared dataclasses and type definitions for firesim."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FireType(str, Enum):
    """Classification of fire behavior type."""

    SURFACE = "surface"
    SURFACE_WITH_TORCHING = "surface_with_torching"
    PASSIVE_CROWN = "passive_crown"
    ACTIVE_CROWN = "active_crown"


@dataclass(frozen=True)
class FBPResult:
    """Complete output from FBP calculation."""

    fuel_type: str
    isi: float
    bui: float
    ros_surface: float  # m/min
    ros_final: float  # m/min (includes crown fire adjustment)
    sfc: float  # surface fuel consumption (kg/m2)
    cfc: float  # crown fuel consumption (kg/m2)
    tfc: float  # total fuel consumption (kg/m2)
    sfi: float  # surface fire intensity (kW/m)
    hfi: float  # head fire intensity (kW/m)
    cfb: float  # crown fraction burned (0-1)
    fire_type: FireType
    flame_length: float  # m (Byram 1959)


@dataclass(frozen=True)
class FWIResult:
    """Complete output from FWI calculation."""

    ffmc: float
    dmc: float
    dc: float
    isi: float
    bui: float
    fwi: float


@dataclass(frozen=True)
class WeatherInput:
    """Weather conditions for fire simulation."""

    temperature: float  # Celsius
    relative_humidity: float  # percent (0-100)
    wind_speed: float  # km/h at 10m
    wind_direction: float  # degrees, meteorological (direction wind blows FROM)
    precipitation_24h: float  # mm in last 24 hours


@dataclass(frozen=True)
class SimulationConfig:
    """Configuration for a fire spread simulation."""

    ignition_lat: float
    ignition_lng: float
    weather: WeatherInput
    duration_hours: float
    snapshot_interval_minutes: float = 30.0
    ffmc: float | None = None  # override; if None, calculated from weather
    dmc: float | None = None
    dc: float | None = None


@dataclass(frozen=True)
class SimulationFrame:
    """A single snapshot of the fire at a point in time."""

    time_hours: float
    perimeter: list[tuple[float, float]]  # [(lat, lng), ...] polygon vertices
    area_ha: float
    head_ros_m_min: float
    max_hfi_kw_m: float
    fire_type: FireType
    flame_length_m: float
    fuel_breakdown: dict[str, float]  # {fuel_code: fraction_of_burned_area}
    spot_fires: list[dict] | None = None  # [{lat, lng, distance_m, hfi_kw_m}, ...]
    num_fronts: int = 1
    burned_cells: list[dict] | None = None  # [{lat, lng, intensity, fuel}, ...] for CA mode
