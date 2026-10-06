"""Ember spotting: new ignitions downwind of the fire front.

Maximum spotting distance comes from Albini's models (``firesim.spread.albini``): the
wind-driven surface-fire model (Albini 1983, Morris 1987) for surface fires and the
torching-tree model (Albini 1979, Chase 1981) when crowns burn.

The rest is heuristic, carried over from v2 and not from Albini, who gives no landing
distribution, ignition probability or number of spots:
- embers are emitted only where head fire intensity is at least 4000 kW/m;
- the chance of a spot per front vertex scales with intensity (capped at 0.15 per 5 min)
  and the WUI ember multiplier;
- the landing distance is uniform between 30 % and 100 % of the maximum;
- the direction is von Mises around the downwind direction, tighter in stronger wind.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from firesim.fbp.constants import FuelType
from firesim.spread.albini import max_spot_distance
from firesim.spread.huygens import (
    fbp_for_conditions,
    FireVertex,
    FuelGrid,
    SpreadConditions,
    SpreadModifierGrid,
    _M_PER_DEG_LAT,
    _m_per_deg_lng,
)

# Head fire intensity below which no embers are emitted (heuristic, not from Albini)
CROWN_FIRE_THRESHOLD_KW_M = 4000.0

# Maximum spotting probability at extreme intensity
MAX_SPOT_PROB = 0.15

# Intensity reference for probability scaling
INTENSITY_REF_KW_M = 30000.0


@dataclass
class SpotFire:
    """A spot fire ignited by wind-lofted embers."""

    lat: float
    lng: float
    source_lat: float
    source_lng: float
    distance_m: float
    hfi_kw_m: float


def check_ember_spotting(
    front: list[FireVertex],
    conditions: SpreadConditions,
    fuel_grid: FuelGrid | None,
    spread_modifier_grid: SpreadModifierGrid | None,
    default_fuel: FuelType,
    dt_minutes: float,
    check_interval: int = 4,
    intensity_multiplier: float = 1.0,
) -> list[SpotFire]:
    """Check for ember spotting from the fire front.

    Samples vertices along the fire front and checks if conditions
    support ember lofting. Uses stochastic model for spotting probability,
    distance, and direction.

    Args:
        front: Current fire front vertices.
        conditions: Weather and FWI conditions.
        fuel_grid: Spatial fuel type grid.
        spread_modifier_grid: WUI modifier grid (for ember_multiplier).
        default_fuel: Fallback fuel type.
        dt_minutes: Current timestep (affects probability scaling).
        check_interval: Check every Nth vertex (performance tuning).

    Returns:
        List of SpotFire objects for new ignition points.
    """
    spot_fires: list[SpotFire] = []

    for i in range(0, len(front), check_interval):
        vertex = front[i]

        # Determine local fuel type
        fuel = default_fuel
        if fuel_grid is not None:
            local_fuel = fuel_grid.get_fuel_at(vertex.lat, vertex.lng)
            if local_fuel is not None:
                fuel = local_fuel
            else:
                continue  # Non-fuel vertex can't generate embers

        # Calculate FBP to get HFI
        cbh, cfl = fuel_grid.get_canopy_at(vertex.lat, vertex.lng) if fuel_grid else (None, None)
        fbp = fbp_for_conditions(conditions, fuel, cbh=cbh, cfl=cfl)

        hfi = fbp.hfi  # Head fire intensity (kW/m)

        # Only intense fires emit embers (heuristic threshold)
        if hfi < CROWN_FIRE_THRESHOLD_KW_M:
            continue

        # Get ember multiplier from WUI zones
        ember_mult = 1.0
        if spread_modifier_grid is not None:
            _, _, ember_mult = spread_modifier_grid.get_modifiers_at(
                vertex.lat, vertex.lng
            )

        # Spotting probability (scaled by timestep and UI intensity multiplier)
        base_prob = min(MAX_SPOT_PROB, hfi / INTENSITY_REF_KW_M) * ember_mult
        # Scale probability by timestep (calibrated for 5-min steps)
        spot_prob = base_prob * (dt_minutes / 5.0) * max(0.0, intensity_multiplier)

        if random.random() > spot_prob:
            continue  # No spot fire this timestep

        # Maximum distance from Albini's models; landing point sampled below it (heuristic)
        max_distance = max_spot_distance(fuel.value, hfi, fbp.cfb, conditions.wind_speed)
        spot_distance = max_distance * random.uniform(0.3, 1.0)

        if spot_distance < 10.0:
            continue  # Too short to matter

        # Spotting direction (von Mises distribution)
        # Fire spreads opposite to wind FROM direction
        spread_dir_rad = math.radians((conditions.wind_direction + 180.0) % 360.0)
        # Concentration parameter: higher wind = more focused ember shower
        kappa = max(1.0, conditions.wind_speed / 10.0)
        # Sample from von Mises
        spot_angle_rad = _von_mises_sample(spread_dir_rad, kappa)

        # Convert to lat/lng displacement
        m_per_lng = _m_per_deg_lng(vertex.lat)
        dlat = spot_distance * math.cos(spot_angle_rad) / _M_PER_DEG_LAT
        dlng = spot_distance * math.sin(spot_angle_rad) / m_per_lng

        new_lat = vertex.lat + dlat
        new_lng = vertex.lng + dlng

        # Check if landing point has fuel
        if fuel_grid is not None:
            landing_fuel = fuel_grid.get_fuel_at(new_lat, new_lng)
            if landing_fuel is None:
                continue  # Landed on non-fuel (water, building)

        spot_fires.append(SpotFire(
            lat=new_lat,
            lng=new_lng,
            source_lat=vertex.lat,
            source_lng=vertex.lng,
            distance_m=spot_distance,
            hfi_kw_m=hfi,
        ))

    return spot_fires


def _von_mises_sample(mu: float, kappa: float) -> float:
    """Sample from von Mises distribution using rejection method.

    Simple implementation — for small kappa, falls back to uniform.
    """
    if kappa < 0.01:
        return random.uniform(0, 2 * math.pi)

    # Best-Fisher algorithm for von Mises sampling
    a = 1.0 + math.sqrt(1.0 + 4.0 * kappa * kappa)
    b = (a - math.sqrt(2.0 * a)) / (2.0 * kappa)
    r = (1.0 + b * b) / (2.0 * b)

    while True:
        u1 = random.random()
        z = math.cos(math.pi * u1)
        f = (1.0 + r * z) / (r + z)
        c = kappa * (r - f)
        u2 = random.random()

        if c * (2.0 - c) > u2 or math.log(c / u2) + 1.0 >= c:
            u3 = random.random()
            theta = mu + math.copysign(math.acos(f), u3 - 0.5)
            return theta
