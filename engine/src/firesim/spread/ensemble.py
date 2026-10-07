"""Ensemble fire growth: arrival-time percentiles and burn probability.

Runs the grid (level-set) model N times with perturbed inputs and summarises, for every
cell, when the fire arrives across the members:

- P10 arrival: the time by which 10 % of members have reached the cell. This is the
  "worst-credible" early arrival used for warning-time margins.
- P50 arrival: the median member.
- P90 arrival: a late arrival; cells that fewer than 90 % of members reach have none.
- Burn probability: the fraction of members that reach the cell within the run.

Perturbations (independent, per member; defaults are placeholders until calibrated):
- wind direction: normal, sd ``wind_dir_sd_deg``. Every evaluation of fire growth models
  finds wind direction the dominant error source (Fox-Hughes et al. 2024; Bennett et al.
  2026), and the member's offset is applied to every hourly record (a systematic forecast
  error, not hour-to-hour noise).
- wind speed: log-normal multiplier, sd ``wind_speed_log_sd``.
- FFMC: normal, sd ``ffmc_sd`` (hourly FFMC starts from the perturbed value).
- DMC and DC: log-normal multipliers, sd ``dmc_dc_log_sd`` (so BUI varies).
- grass curing: normal, sd ``curing_sd`` (percentage points).
- foliar moisture: normal, sd ``fmc_sd``.
- rate of spread: log-normal multiplier, sd ``ros_log_sd``, median 1. Cruz & Alexander
  (2013) put typical FBP-type ROS errors at +-35-75 %, with under-prediction more common.
- ignition: optional jitter radius ``ignition_jitter_m`` (0 = the ignition is known).

The defaults should be replaced by values calibrated on observed fires (the validation
harness) and Environment and Climate Change Canada forecast-error statistics.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field, replace

import numpy as np

from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadModifierGrid, TerrainGrid
from firesim.spread.simulator import Simulator
from firesim.types import SimulationConfig

logger = logging.getLogger(__name__)

_UNBURNED = np.uint16(65535)


@dataclass
class EnsembleConfig:
    n_members: int = 50
    seed: int = 1
    wind_dir_sd_deg: float = 20.0
    wind_speed_log_sd: float = 0.2
    ffmc_sd: float = 1.5
    dmc_dc_log_sd: float = 0.1
    curing_sd: float = 10.0
    fmc_sd: float = 5.0
    ros_log_sd: float = 0.3
    ignition_jitter_m: float = 0.0
    quantiles: tuple[int, ...] = (10, 50, 90)


@dataclass
class EnsembleResult:
    """Arrival percentiles (whole minutes, -1 = not reached at that percentile)."""

    arrival: dict[int, np.ndarray]  # percentile -> int16 (rows, cols)
    burn_probability: np.ndarray  # float32 (rows, cols)
    members: list[dict] = field(default_factory=list)  # perturbations and area per member
    rows: int = 0
    cols: int = 0
    lat_min: float = 0.0
    lat_max: float = 0.0
    lng_min: float = 0.0
    lng_max: float = 0.0
    duration_minutes: float = 0.0


def perturb_config(config: SimulationConfig, ens: EnsembleConfig, rng: random.Random,
                   base_fmc: float) -> tuple[SimulationConfig, float, dict]:
    """One member's configuration, its ROS multiplier, and a record of the perturbation."""
    d_dir = rng.gauss(0.0, ens.wind_dir_sd_deg)
    k_ws = math.exp(rng.gauss(0.0, ens.wind_speed_log_sd))
    d_ffmc = rng.gauss(0.0, ens.ffmc_sd)
    k_dmc = math.exp(rng.gauss(0.0, ens.dmc_dc_log_sd))
    k_dc = math.exp(rng.gauss(0.0, ens.dmc_dc_log_sd))
    d_cure = rng.gauss(0.0, ens.curing_sd)
    d_fmc = rng.gauss(0.0, ens.fmc_sd)
    k_ros = math.exp(rng.gauss(0.0, ens.ros_log_sd))
    lat, lng = config.ignition_lat, config.ignition_lng
    if ens.ignition_jitter_m > 0:
        r = ens.ignition_jitter_m * math.sqrt(rng.random())
        th = rng.uniform(0.0, 2.0 * math.pi)
        lat += r * math.cos(th) / 111320.0
        lng += r * math.sin(th) / (111320.0 * math.cos(math.radians(lat)))

    weather = replace(
        config.weather,
        wind_direction=(config.weather.wind_direction + d_dir) % 360.0,
        wind_speed=max(0.0, config.weather.wind_speed * k_ws),
    )
    hourly = None
    if config.hourly_weather:
        hourly = tuple(
            replace(h, wind_direction=(h.wind_direction + d_dir) % 360.0,
                    wind_speed=max(0.0, h.wind_speed * k_ws))
            for h in config.hourly_weather
        )
    ffmc = config.ffmc if config.ffmc is not None else 85.0
    dmc = config.dmc if config.dmc is not None else 40.0
    dc = config.dc if config.dc is not None else 200.0
    member = replace(
        config,
        ignition_lat=lat, ignition_lng=lng, weather=weather, hourly_weather=hourly,
        ffmc=min(101.0, max(0.0, ffmc + d_ffmc)), dmc=max(0.0, dmc * k_dmc),
        dc=max(0.0, dc * k_dc),
        grass_cure=min(100.0, max(0.0, config.grass_cure + d_cure)),
        fmc=max(50.0, base_fmc + d_fmc),
    )
    record = {
        "wind_dir_offset_deg": round(d_dir, 2), "wind_speed_factor": round(k_ws, 4),
        "ffmc": round(member.ffmc, 2), "dmc": round(member.dmc, 2), "dc": round(member.dc, 1),
        "grass_cure": round(member.grass_cure, 1), "fmc": round(member.fmc, 1),
        "ros_multiplier": round(k_ros, 4),
    }
    return member, k_ros, record


def run_ensemble(
    config: SimulationConfig,
    fuel_grid: FuelGrid,
    ens: EnsembleConfig | None = None,
    terrain_grid: TerrainGrid | None = None,
    spread_modifier_grid: SpreadModifierGrid | None = None,
    initial_perimeter: list[tuple[float, float]] | None = None,
    initial_burned: list[tuple[float, float]] | None = None,
    progress=None,
) -> EnsembleResult:
    """Run ``ens.n_members`` perturbed grid-model fires and summarise arrival times.

    ``initial_perimeter`` / ``initial_burned`` start every member from an observed fire
    (RPAS perimeter or a previous day), as in the deterministic model.
    ``progress(done, total)`` is called after each member.
    """
    ens = ens or EnsembleConfig()
    rng = random.Random(ens.seed)
    duration = config.duration_hours * 60.0
    base_fmc = Simulator(config, fuel_grid=fuel_grid)._foliar_moisture()
    stack = np.full((ens.n_members, fuel_grid.rows, fuel_grid.cols), _UNBURNED, dtype=np.uint16)
    members: list[dict] = []
    cell_lat = (fuel_grid.lat_max - fuel_grid.lat_min) / fuel_grid.rows
    cell_area_ha = (cell_lat * 111320.0) * (
        (fuel_grid.lng_max - fuel_grid.lng_min) / fuel_grid.cols
        * 111320.0 * math.cos(math.radians((fuel_grid.lat_max + fuel_grid.lat_min) / 2))
    ) / 1e4
    for i in range(ens.n_members):
        member_cfg, k_ros, record = perturb_config(config, ens, rng, base_fmc)
        sim = Simulator(member_cfg, fuel_grid=fuel_grid)
        schedule = [(t, replace(c, ros_multiplier=k_ros)) for t, c in sim.weather_schedule()]
        frames = run_cellular_simulation(
            {"ignition_lat": member_cfg.ignition_lat, "ignition_lng": member_cfg.ignition_lng,
             "duration_hours": member_cfg.duration_hours},
            fuel_grid=fuel_grid, conditions=schedule[0][1],
            spread_modifier_grid=spread_modifier_grid, terrain_grid=terrain_grid,
            snapshot_interval_minutes=duration, weather_schedule=schedule,
            acceleration=initial_perimeter is None and initial_burned is None,
            initial_perimeter=initial_perimeter, initial_burned=initial_burned,
            compute_perimeter=False,
        )
        arrival = frames[-1].arrival if frames and frames[-1].arrival is not None else None
        if arrival is not None:
            reached = np.isfinite(arrival)
            stack[i][reached] = np.clip(np.rint(arrival[reached]), 0, 65534).astype(np.uint16)
            record["area_ha"] = round(float(reached.sum()) * cell_area_ha, 2)
        else:
            record["area_ha"] = 0.0
        members.append(record)
        if progress:
            progress(i + 1, ens.n_members)

    n = ens.n_members
    stack.sort(axis=0)
    arrival_q: dict[int, np.ndarray] = {}
    for q in ens.quantiles:
        k = max(0, math.ceil(q / 100.0 * n) - 1)  # the k-th earliest member arrival
        v = stack[k]
        arrival_q[q] = np.where(v == _UNBURNED, -1, v.astype(np.int32)).astype(np.int16)
    burn_probability = (stack != _UNBURNED).sum(axis=0).astype(np.float32) / n
    logger.info("Ensemble: %d members, P50 area %.1f ha", n,
                float(np.median([m["area_ha"] for m in members])) if members else 0.0)
    return EnsembleResult(
        arrival=arrival_q, burn_probability=burn_probability, members=members,
        rows=fuel_grid.rows, cols=fuel_grid.cols, lat_min=fuel_grid.lat_min,
        lat_max=fuel_grid.lat_max, lng_min=fuel_grid.lng_min, lng_max=fuel_grid.lng_max,
        duration_minutes=duration,
    )
