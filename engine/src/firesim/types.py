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
    back_ros: float = 0.0  # m/min, ST-X-3 eq 89 (back ISI)
    flank_ros: float = 0.0  # m/min, ST-X-3 eq 89 (FROS = (ROS + BROS) / (2 LB))
    lb: float = 1.0  # length-to-breadth ratio (ST-X-3 eq 79/80)
    wsv: float = 0.0  # net effective wind speed incl. slope (km/h)
    raz: float = 0.0  # direction of head fire spread (degrees, toward, 0=N)
    csi: float = 0.0  # critical surface intensity for crowning (kW/m)
    rso: float = 0.0  # critical surface ROS for crowning (m/min)
    fmc: float = 0.0  # foliar moisture content used (%)
    cfl: float = 0.0  # crown fuel load available to burn (kg/m2; M types scaled by PC/PDF)


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
    # FBP fuel modifiers (ST-X-3 / Wotton et al. 2009)
    grass_cure: float = 60.0  # degree of curing (%) for O-1a/O-1b
    grass_fuel_load: float = 0.35  # kg/m2, O-1a/O-1b surface fuel consumption
    percent_conifer: float = 50.0  # M-1/M-2
    percent_dead_fir: float = 35.0  # M-3/M-4
    # Foliar moisture: explicit override, else ST-X-3 eqs 1-6 from ignition
    # lat/lng and day_of_year, else 100 %.
    fmc: float | None = None
    day_of_year: int | None = None
    elevation_m: float | None = None


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
    buildings_at_risk: int = 0
    ignition_snapped_m: float = 0.0  # >0 if ignition was moved to nearest fuel cell
