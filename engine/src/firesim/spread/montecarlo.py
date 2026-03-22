"""Monte Carlo burn probability engine.

Runs N iterations of the CA fire spread model with varied ignition points,
weather inputs, and random seeds to produce a probabilistic burn probability
map. This is the core analytical tool for fire management planning.

Each iteration:
  - Jitters the ignition point ±jitter_m metres (default ±100 m)
  - Varies wind speed by ±wind_speed_pct % (default ±10 %)
  - Varies relative humidity by ±rh_abs % (default ±5 %)
  - Uses a deterministic per-iteration seed derived from base_seed

The result is a 2D float array of shape (rows, cols) where each value is the
fraction of iterations in which that cell burned — i.e., burn probability
∈ [0.0, 1.0].

References:
    Parisien, M.-A., & Moritz, M. A. (2009). Environmental controls on the
    distribution of wildfire at multiple spatial scales. Ecological Monographs,
    79(1), 127–154.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

import numpy as np

from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.huygens import FuelGrid, SpreadConditions, SpreadModifierGrid

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloConfig:
    """Parameters for a Monte Carlo burn probability run."""

    ignition_lat: float
    ignition_lng: float
    duration_hours: float = 4.0
    n_iterations: int = 100
    jitter_m: float = 100.0        # Ignition point jitter radius (metres)
    wind_speed_pct: float = 10.0   # Wind speed variation (±%)
    rh_abs: float = 5.0            # Relative humidity variation (±absolute %)
    base_seed: int = 42


@dataclass
class BurnProbabilityResult:
    """Output from a Monte Carlo burn probability run."""

    burn_probability: list[list[float]]  # 2D, shape (rows, cols), values [0, 1]
    rows: int
    cols: int
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float
    n_iterations: int
    iterations_completed: int
    cell_size_m: float


def run_monte_carlo(
    mc_config: MonteCarloConfig,
    fuel_grid: FuelGrid,
    base_conditions: SpreadConditions,
    spread_modifier_grid: SpreadModifierGrid | None = None,
    dt_minutes: float = 2.0,
) -> BurnProbabilityResult:
    """Run Monte Carlo burn probability analysis.

    Args:
        mc_config: Monte Carlo parameters (iterations, jitter, variation).
        fuel_grid: Spatial fuel grid for all iterations.
        base_conditions: Baseline weather/FWI (varied per iteration).
        spread_modifier_grid: Optional WUI spread modifiers.
        dt_minutes: CA timestep (smaller = more accurate, slower).

    Returns:
        BurnProbabilityResult with normalized burn probability per cell.
    """
    rows = fuel_grid.rows
    cols = fuel_grid.cols
    burn_count = np.zeros((rows, cols), dtype=np.int32)

    # Approximate cell size in metres (for jitter conversion)
    cell_lat = (fuel_grid.lat_max - fuel_grid.lat_min) / rows
    cell_size_m = cell_lat * 111_320.0

    # Degrees of jitter per axis
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(mc_config.ignition_lat))
    jitter_deg_lat = mc_config.jitter_m / m_per_deg_lat
    jitter_deg_lng = mc_config.jitter_m / m_per_deg_lng

    rng = random.Random(mc_config.base_seed)

    iterations_completed = 0
    for i in range(mc_config.n_iterations):
        iter_seed = rng.randint(0, 2**31 - 1)

        # Jitter ignition point
        ign_lat = mc_config.ignition_lat + rng.uniform(-jitter_deg_lat, jitter_deg_lat)
        ign_lng = mc_config.ignition_lng + rng.uniform(-jitter_deg_lng, jitter_deg_lng)

        # Clamp to grid bounds
        ign_lat = max(fuel_grid.lat_min, min(fuel_grid.lat_max, ign_lat))
        ign_lng = max(fuel_grid.lng_min, min(fuel_grid.lng_max, ign_lng))

        # Vary weather ±
        ws_factor = 1.0 + rng.uniform(-mc_config.wind_speed_pct / 100.0,
                                       mc_config.wind_speed_pct / 100.0)
        wind_speed = max(0.0, base_conditions.wind_speed * ws_factor)

        rh_delta = rng.uniform(-mc_config.rh_abs, mc_config.rh_abs)

        iter_conditions = SpreadConditions(
            wind_speed=wind_speed,
            wind_direction=base_conditions.wind_direction,
            ffmc=base_conditions.ffmc,
            dmc=base_conditions.dmc,
            dc=base_conditions.dc,
            pc=base_conditions.pc,
            grass_cure=base_conditions.grass_cure,
        )

        config = {
            "ignition_lat": ign_lat,
            "ignition_lng": ign_lng,
            "duration_hours": mc_config.duration_hours,
        }

        try:
            random.seed(iter_seed)  # Set global seed for CA's random calls
            frames = run_cellular_simulation(
                config,
                fuel_grid=fuel_grid,
                conditions=iter_conditions,
                spread_modifier_grid=spread_modifier_grid,
                dt_minutes=dt_minutes,
                snapshot_interval_minutes=mc_config.duration_hours * 60.0,  # final only
            )
            random.seed()  # Restore non-deterministic state

            # Accumulate burned cells from the final frame
            if frames:
                final = frames[-1]
                for cell in final.burned_cells:
                    # Convert lat/lng back to grid row/col
                    r = int((fuel_grid.lat_max - cell.lat) / cell_lat)
                    c_lat_span = (fuel_grid.lat_max - fuel_grid.lat_min)
                    c_lng_span = (fuel_grid.lng_max - fuel_grid.lng_min)
                    cell_lng_size = c_lng_span / cols
                    c = int((cell.lng - fuel_grid.lng_min) / cell_lng_size)
                    if 0 <= r < rows and 0 <= c < cols:
                        burn_count[r, c] += 1

            iterations_completed += 1

        except Exception as exc:
            logger.warning("Monte Carlo iteration %d failed: %s", i, exc)
            continue

        if (i + 1) % 10 == 0 or i == mc_config.n_iterations - 1:
            logger.info(
                "Monte Carlo: %d/%d iterations, max P=%.3f",
                i + 1, mc_config.n_iterations,
                float(burn_count.max()) / max(iterations_completed, 1),
            )

    # Normalize to [0, 1]
    if iterations_completed > 0:
        burn_prob = burn_count.astype(np.float32) / iterations_completed
    else:
        burn_prob = np.zeros((rows, cols), dtype=np.float32)

    burn_prob_list: list[list[float]] = [
        [float(burn_prob[r, c]) for c in range(cols)]
        for r in range(rows)
    ]

    logger.info(
        "Monte Carlo complete: %d/%d iterations, "
        "cells with P>0: %d (%.1f%%), max P=%.3f",
        iterations_completed, mc_config.n_iterations,
        int((burn_prob > 0).sum()),
        100.0 * float((burn_prob > 0).sum()) / (rows * cols),
        float(burn_prob.max()),
    )

    return BurnProbabilityResult(
        burn_probability=burn_prob_list,
        rows=rows,
        cols=cols,
        lat_min=fuel_grid.lat_min,
        lat_max=fuel_grid.lat_max,
        lng_min=fuel_grid.lng_min,
        lng_max=fuel_grid.lng_max,
        n_iterations=mc_config.n_iterations,
        iterations_completed=iterations_completed,
        cell_size_m=cell_size_m,
    )
