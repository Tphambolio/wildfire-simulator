"""Shared dataclasses and type definitions for firesim."""

from __future__ import annotations

from dataclasses import dataclass, fields
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
class HourlyWeather:
    """Weather for one hour of a simulation, applied from ``hours_from_start`` to the next record."""

    hours_from_start: float
    temperature: float  # Celsius
    relative_humidity: float  # percent
    wind_speed: float  # km/h at 10 m
    wind_direction: float  # degrees, meteorological (FROM)
    precipitation: float = 0.0  # mm in the hour


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
    # Hourly weather stream. When set, wind and FFMC change hour by hour: FFMC starts from
    # ``ffmc`` and follows the hourly FFMC model (Van Wagner 1977); DMC and DC stay fixed.
    hourly_weather: tuple[HourlyWeather, ...] | None = None
    # Records with negative ``hours_from_start`` (e.g. from 17:00 the previous afternoon, when
    # the daily FFMC applies) only advance the hourly FFMC to the start ("spin-up").
    # Opt-in burning period (grid and Huygens models): fire spreads at full rate only between
    # these local clock hours each day, and at ``burning_period_off_factor`` x ROS outside them
    # (firesim.spread.diurnal). Needs ``start_hour``, the local clock hour at t = 0.
    start_hour: float | None = None
    burning_period: tuple[float, float] | None = None
    burning_period_off_factor: float = 0.0
    # Seed for the stochastic parts of a run (ember spotting). None = derived from all the
    # other fields (firesim.spread.spotting.derive_seed), so identical inputs give identical
    # runs; set it to rerun the same inputs with different random draws.
    seed: int | None = None

    def resolved_seed(self) -> int:
        """The seed actually used: ``seed``, or a hash of every other field."""
        if self.seed is not None:
            return self.seed
        from firesim.spread.spotting import derive_seed

        return derive_seed(*(getattr(self, f.name) for f in fields(self) if f.name != "seed"))


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
    buildings_at_risk: int = 0  # building centroids inside the perimeter
    ignition_snapped_m: float = 0.0  # >0 if ignition was moved to nearest fuel cell
    # Exposure counts reached by this frame (grid model with buildings; see firesim.exposure)
    building_exposure: dict | None = None
    # Per-building exposure, final frame only, buildings within 500 m of the fire
    building_exposure_detail: list[dict] | None = None
    # Grid model: fastest head cell reached since the previous frame
    # {lat, lng, ros (m/min), raz (deg, spread direction), hfi, cfb, fuel, max_spot_distance_m}
    head: dict | None = None
    # Grid model, final frame only: arrival minutes per cell (-1 = not burned) and grid bounds
    arrival_raster: dict | None = None
    # Opt-in structure-to-structure spread (Hamada; illustrative, not validated in Canada):
    # counts of building units with modelled involvement by this frame. Not a prediction of
    # which buildings burn. See docs/structure-spread-spec.md.
    structure_spread: dict | None = None
    # Final frame only, with structure_spread: the involved units (footprint, time, mechanism)
    # for the map; illustrative, display only (docs/structure-spread-spec.md §9)
    structure_spread_detail: list[dict] | None = None
