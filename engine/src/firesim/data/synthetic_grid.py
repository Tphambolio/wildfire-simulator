"""Generate synthetic fuel grids for demo/testing without real raster data.

Creates a plausible mixed-fuel landscape around a given ignition point.
Used when `use_ca_mode=True` is requested but no `fuel_grid_path` is supplied.
"""

from __future__ import annotations

import math
import random

from firesim.fbp.constants import FuelType
from firesim.spread.huygens import FuelGrid

# Fuel type distribution for a boreal-parkland mosaic (Edmonton surroundings)
# Weights roughly match an urban-wildland interface landscape
_FUEL_PALETTE: list[tuple[FuelType | None, float]] = [
    (FuelType.C2,  0.35),   # Boreal spruce — dominant
    (FuelType.C3,  0.15),   # Mature jack/lodgepole pine
    (FuelType.D1,  0.10),   # Leafless aspen
    (FuelType.M1,  0.08),   # Boreal mixedwood
    (FuelType.O1a, 0.07),   # Matted grass
    (FuelType.S1,  0.05),   # Jack pine slash
    (None,         0.20),   # Non-fuel (roads, water, cleared land)
]

_CUMULATIVE: list[tuple[float, FuelType | None]] = []
_running = 0.0
for _ft, _w in _FUEL_PALETTE:
    _running += _w
    _CUMULATIVE.append((_running, _ft))


def _random_fuel(rng: random.Random) -> FuelType | None:
    r = rng.random()
    for threshold, ft in _CUMULATIVE:
        if r < threshold:
            return ft
    return None


def generate_synthetic_fuel_grid(
    ignition_lat: float,
    ignition_lng: float,
    radius_km: float = 5.0,
    cell_size_m: float = 50.0,
    seed: int | None = None,
) -> FuelGrid:
    """Build a synthetic FuelGrid centred on the ignition point.

    The landscape is a spatially coherent random mosaic (patches of similar
    fuel rather than pure random noise) generated with a simple block-noise
    approach.

    Args:
        ignition_lat: Latitude of the fire ignition point.
        ignition_lng: Longitude of the fire ignition point.
        radius_km: Half-width of the square grid in km.
        cell_size_m: Grid cell size in metres.
        seed: Optional RNG seed for reproducibility.

    Returns:
        FuelGrid suitable for `Simulator`.
    """
    rng = random.Random(seed)

    # Grid extent
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(ignition_lat))

    half_deg_lat = (radius_km * 1000.0) / m_per_deg_lat
    half_deg_lng = (radius_km * 1000.0) / m_per_deg_lng

    lat_min = ignition_lat - half_deg_lat
    lat_max = ignition_lat + half_deg_lat
    lng_min = ignition_lng - half_deg_lng
    lng_max = ignition_lng + half_deg_lng

    rows = max(50, int(round((lat_max - lat_min) * m_per_deg_lat / cell_size_m)))
    cols = max(50, int(round((lng_max - lng_min) * m_per_deg_lng / cell_size_m)))

    # Patch-noise: assign a random fuel type per ~10-cell block, then
    # fill individual cells from that block's fuel (spatially coherent)
    BLOCK = 10  # cells per block side
    block_rows = math.ceil(rows / BLOCK)
    block_cols = math.ceil(cols / BLOCK)

    blocks: list[list[FuelType | None]] = [
        [_random_fuel(rng) for _ in range(block_cols)]
        for _ in range(block_rows)
    ]

    fuel_types: list[list[FuelType | None]] = []
    for r in range(rows):
        row_list: list[FuelType | None] = []
        br = r // BLOCK
        for c in range(cols):
            bc = c // BLOCK
            # Small per-cell perturbation (10% chance of overriding block fuel)
            if rng.random() < 0.10:
                row_list.append(_random_fuel(rng))
            else:
                row_list.append(blocks[br][bc])
        fuel_types.append(row_list)

    # Ensure ignition cell itself has fuel
    ign_row = int((lat_max - ignition_lat) / (lat_max - lat_min) * rows)
    ign_col = int((ignition_lng - lng_min) / (lng_max - lng_min) * cols)
    ign_row = max(0, min(rows - 1, ign_row))
    ign_col = max(0, min(cols - 1, ign_col))
    if fuel_types[ign_row][ign_col] is None:
        fuel_types[ign_row][ign_col] = FuelType.C2

    return FuelGrid(
        fuel_types=fuel_types,
        lat_min=lat_min,
        lat_max=lat_max,
        lng_min=lng_min,
        lng_max=lng_max,
        rows=rows,
        cols=cols,
    )
