"""Cellular automaton fire spread model.

8-neighbor grid-based spread that naturally wraps around non-fuel obstacles.
Ported from V2's fire_spread.py. Used when a spatial fuel grid is provided
(urban/WUI scenarios). The Huygens wavelet model is used for uniform fuel
(open wildland).
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

import numpy as np

from firesim.fbp.calculator import calculate_fbp
from firesim.fbp.constants import FuelType
from firesim.spread.ellipse import (
    calculate_back_ros,
    calculate_flank_ros,
    calculate_length_to_breadth_ratio,
)
from firesim.spread.huygens import FireVertex, FuelGrid, SpreadConditions, SpreadModifierGrid, TerrainGrid
from firesim.spread.slope import calculate_directional_slope_factor
from firesim.spread.spotting import SpotFire, check_ember_spotting

logger = logging.getLogger(__name__)

# 8-neighbor offsets: (drow, dcol, angle_degrees)
NEIGHBORS = [
    (-1, 0, 0),      # N
    (-1, 1, 45),     # NE
    (0, 1, 90),      # E
    (1, 1, 135),     # SE
    (1, 0, 180),     # S
    (1, -1, 225),    # SW
    (0, -1, 270),    # W
    (-1, -1, 315),   # NW
]

# Heat accumulation threshold for ignition override
HEAT_IGNITION_THRESHOLD = 5.0


@dataclass
class BurnedCell:
    """A single burned cell with location and intensity."""
    lat: float
    lng: float
    intensity: float  # kW/m
    fuel_type: str
    timestep: int
    fire_type: str = "surface"  # FireType value: surface, surface_with_torching, passive_crown, active_crown


@dataclass
class CellularFrame:
    """Output frame from cellular automaton simulation."""
    time_hours: float
    burned_cells: list[BurnedCell]
    total_burned: int
    new_cells: int
    area_ha: float
    max_intensity: float
    mean_ros: float
    fuel_breakdown: dict[str, float]
    spot_fires: list[SpotFire] | None = None
    num_fronts: int = 1
    ignition_snapped_m: float = 0.0  # >0 if ignition was moved to nearest fuel cell


def run_cellular_simulation(
    config: dict,
    fuel_grid: FuelGrid,
    conditions: SpreadConditions,
    default_fuel: FuelType = FuelType.C2,
    spread_modifier_grid: SpreadModifierGrid | None = None,
    terrain_grid: TerrainGrid | None = None,
    dt_minutes: float = 1.0,
    snapshot_interval_minutes: float = 30.0,
    enable_spotting: bool = False,
    spotting_intensity: float = 1.0,
) -> list[CellularFrame]:
    """Run fire spread using cellular automaton on the fuel grid.

    Args:
        config: Dict with ignition_lat, ignition_lng, duration_hours.
        fuel_grid: Spatial fuel grid (required).
        conditions: Weather/FWI conditions.
        default_fuel: Fallback fuel type.
        spread_modifier_grid: Optional WUI modifiers.
        terrain_grid: Optional slope/aspect grid for directional slope correction
            (CFFDRS ST-X-3). When provided, spread probability to each 8-neighbor
            is scaled by the directional slope factor.
        dt_minutes: Timestep in minutes.
        snapshot_interval_minutes: How often to yield frames.
        enable_spotting: When True, apply Albini (1979) ember spotting model to seed
            new ignitions from the active fire front at each timestep.
        spotting_intensity: Multiplier on spot fire probability (1.0 = baseline;
            0 = disabled). Has no effect when enable_spotting is False.

    Returns:
        List of CellularFrame snapshots.
    """
    rows = fuel_grid.rows
    cols = fuel_grid.cols
    lat_min = fuel_grid.lat_min
    lat_max = fuel_grid.lat_max
    lng_min = fuel_grid.lng_min
    lng_max = fuel_grid.lng_max

    cell_lat = (lat_max - lat_min) / rows
    cell_lng = (lng_max - lng_min) / cols
    cell_size_m = cell_lat * 111320.0  # approximate meters per cell

    # Convert ignition point to grid coordinates
    ign_lat = config["ignition_lat"]
    ign_lng = config["ignition_lng"]
    ign_row = int((lat_max - ign_lat) / cell_lat)
    ign_col = int((ign_lng - lng_min) / cell_lng)
    ign_row = max(0, min(rows - 1, ign_row))
    ign_col = max(0, min(cols - 1, ign_col))

    # Check ignition point has fuel; BFS outward to nearest fuel cell if not
    ign_fuel = fuel_grid.fuel_types[ign_row][ign_col]
    ignition_snapped_m = 0.0
    if ign_fuel is None:
        from collections import deque
        MAX_SNAP_CELLS = 100  # ~5 km at 50 m cell size
        visited: set[tuple[int, int]] = {(ign_row, ign_col)}
        q: deque[tuple[int, int, int]] = deque([(ign_row, ign_col, 0)])
        snapped_row: int | None = None
        snapped_col: int | None = None
        snap_cells = 0

        while q:
            r, c, dist = q.popleft()
            if dist >= MAX_SNAP_CELLS:
                break
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if (nr, nc) in visited:
                    continue
                visited.add((nr, nc))
                if fuel_grid.fuel_types[nr][nc] is not None:
                    snapped_row, snapped_col, snap_cells = nr, nc, dist + 1
                    break
                q.append((nr, nc, dist + 1))
            if snapped_row is not None:
                break

        if snapped_row is not None:
            ign_row, ign_col = snapped_row, snapped_col
            ign_fuel = fuel_grid.fuel_types[ign_row][ign_col]
            ignition_snapped_m = snap_cells * cell_size_m
            logger.warning(
                "Ignition in non-fuel zone — snapped %.0fm to nearest fuel cell (%d,%d) fuel=%s",
                ignition_snapped_m, ign_row, ign_col, ign_fuel.value if ign_fuel else "none",
            )
        else:
            logger.warning(
                "No fuel within %.0fm of ignition point — simulation will be empty",
                MAX_SNAP_CELLS * cell_size_m,
            )

    # Initialize grids
    burned = np.zeros((rows, cols), dtype=bool)
    burning = np.zeros((rows, cols), dtype=bool)
    heat_accumulated = np.zeros((rows, cols), dtype=np.float32)
    intensity_map = np.zeros((rows, cols), dtype=np.float32)
    # Burn timer: how many minutes a cell has been burning (0 = not burning)
    burn_timer = np.zeros((rows, cols), dtype=np.float32)
    # Burn duration: how long a cell burns before exhausting (cell_size / ROS)
    burn_duration = np.full((rows, cols), 10.0, dtype=np.float32)  # default 10 min

    # Ignite starting cell
    burning[ign_row, ign_col] = True

    # Pre-compute FBP for each fuel type (cache to avoid redundant calculations)
    fbp_cache: dict[str, tuple] = {}

    def get_fbp(fuel: FuelType) -> tuple:
        key = fuel.value
        if key not in fbp_cache:
            fbp = calculate_fbp(
                fuel_type=fuel,
                wind_speed=conditions.wind_speed,
                ffmc=conditions.ffmc,
                dmc=conditions.dmc,
                dc=conditions.dc,
            )
            lbr = calculate_length_to_breadth_ratio(conditions.wind_speed)
            fbp_cache[key] = (fbp.ros_final, fbp.hfi, lbr, fbp.fire_type)
        return fbp_cache[key]

    # Simulation loop
    duration_minutes = config["duration_hours"] * 60.0
    elapsed = 0.0
    next_snapshot = 0.0
    frames: list[CellularFrame] = []
    all_burned_cells: list[BurnedCell] = []
    snapshot_burned_cells: list[BurnedCell] = []  # cells since last snapshot
    iteration = 0
    last_mean_ros = 0.0  # mean ROS of active burning cells at last timestep
    all_spot_fires: list[SpotFire] = []
    snapshot_spot_fires: list[SpotFire] = []

    # Seed ignition cell so the t=0 snapshot is non-empty (ignition cell is burning,
    # not yet spread-to, so it is never added by the spread loop).
    if ign_fuel is not None:
        ign_ros, ign_hfi, _ign_lbr, ign_fire_type = get_fbp(ign_fuel)
        ign_lat_pos = lat_max - (ign_row + 0.5) * cell_lat
        ign_lng_pos = lng_min + (ign_col + 0.5) * cell_lng
        _ign_cell = BurnedCell(
            lat=ign_lat_pos,
            lng=ign_lng_pos,
            intensity=ign_hfi,
            fuel_type=ign_fuel.value,
            timestep=0,
            fire_type=ign_fire_type.value,
        )
        all_burned_cells.append(_ign_cell)
        snapshot_burned_cells.append(_ign_cell)

    # Fire spread direction (opposite of wind FROM)
    spread_dir = (conditions.wind_direction + 180.0) % 360.0

    logger.info(
        "CA simulation: %dx%d grid, cell=%.0fm, ignition=(%d,%d), duration=%.1fh",
        rows, cols, cell_size_m, ign_row, ign_col, config["duration_hours"],
    )

    while elapsed <= duration_minutes:
        # Snapshot
        if elapsed >= next_snapshot:
            frame = _make_frame(
                elapsed, all_burned_cells, snapshot_burned_cells,
                rows, cols, cell_size_m, fuel_grid, burned,
                mean_ros=last_mean_ros,
                spot_fires=list(snapshot_spot_fires) if snapshot_spot_fires else None,
                ignition_snapped_m=ignition_snapped_m if not frames else 0.0,
            )
            frames.append(frame)
            snapshot_burned_cells = []  # reset for next interval
            snapshot_spot_fires = []
            next_snapshot += snapshot_interval_minutes

        if not np.any(burning):
            break

        new_burning = np.zeros((rows, cols), dtype=bool)

        # Get all currently burning cell coordinates
        burn_rows, burn_cols = np.where(burning)

        ros_sum = 0.0
        ros_count = 0

        for idx in range(len(burn_rows)):
            row, col = int(burn_rows[idx]), int(burn_cols[idx])

            # Get fuel type at this cell
            fuel = fuel_grid.fuel_types[row][col]
            if fuel is None:
                continue

            # Get FBP output (ros_base, head fire intensity, LBR, fire_type enum)
            ros_base, fi, lbr, cell_fire_type = get_fbp(fuel)
            ros_sum += ros_base
            ros_count += 1

            # Apply WUI modifiers
            ros_mod = 1.0
            cell_center_lat = lat_max - (row + 0.5) * cell_lat
            cell_center_lng = lng_min + (col + 0.5) * cell_lng
            if spread_modifier_grid is not None:
                rm, im, _ = spread_modifier_grid.get_modifiers_at(
                    cell_center_lat, cell_center_lng,
                )
                ros_base *= rm
                fi *= im

            if ros_base <= 0.001:
                continue

            # Terrain: look up slope/aspect once per burning cell
            if terrain_grid is not None:
                slope_pct, aspect_deg = terrain_grid.get_slope_aspect(cell_center_lat, cell_center_lng)
            else:
                slope_pct, aspect_deg = 0.0, 0.0

            # Store intensity and set burn duration for this cell
            intensity_map[row, col] = fi
            if burn_duration[row, col] == 10.0:  # not yet set
                burn_duration[row, col] = max(2.0, cell_size_m / max(ros_base, 0.1))

            # Try to spread to each of 8 neighbors
            for dir_idx, (dr, dc_off, angle) in enumerate(NEIGHBORS):
                nr, nc = row + dr, col + dc_off

                # Boundary check
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue

                # Skip already burned, burning, or newly ignited
                if burned[nr, nc] or burning[nr, nc] or new_burning[nr, nc]:
                    continue

                # Check neighbor fuel
                neighbor_fuel = fuel_grid.fuel_types[nr][nc]
                if neighbor_fuel is None:
                    continue  # Non-fuel — fire wraps around

                # Apply directional slope factor (ST-X-3 §3.3)
                if slope_pct > 1.0:
                    sf = calculate_directional_slope_factor(slope_pct, aspect_deg, angle)
                    ros_spread = ros_base * sf
                else:
                    ros_spread = ros_base

                # Elliptical spread probability
                spread_prob = _elliptical_spread_prob(
                    angle, spread_dir, ros_spread, lbr, cell_size_m, dt_minutes,
                )

                # Heat accumulation for failed ignitions
                heat_transfer = spread_prob * fi / 1000.0
                heat_accumulated[nr, nc] += heat_transfer

                # Ignition check
                if random.random() < spread_prob or heat_accumulated[nr, nc] > HEAT_IGNITION_THRESHOLD:
                    new_burning[nr, nc] = True
                    heat_accumulated[nr, nc] = 0.0

                    # Record burned cell
                    cell_lat_pos = lat_max - (nr + 0.5) * cell_lat
                    cell_lng_pos = lng_min + (nc + 0.5) * cell_lng
                    # Determine fire type for the neighbor cell using its own FBP
                    neighbor_fire_type = get_fbp(neighbor_fuel)[3]
                    cell = BurnedCell(
                        lat=cell_lat_pos,
                        lng=cell_lng_pos,
                        intensity=fi,
                        fuel_type=neighbor_fuel.value,
                        timestep=iteration,
                        fire_type=neighbor_fire_type.value,
                    )
                    all_burned_cells.append(cell)
                    snapshot_burned_cells.append(cell)

        # Update mean ROS for this timestep
        if ros_count > 0:
            last_mean_ros = ros_sum / ros_count

        # Update burn timers — cells burn for duration then become burned-out
        burn_timer[burning] += dt_minutes
        exhausted = burning & (burn_timer >= burn_duration)
        burned |= exhausted
        burning[exhausted] = False

        # Ember spotting: sample front cells and seed new ignitions
        if enable_spotting and np.any(burning):
            _apply_spotting(
                burning=burning,
                burned=burned,
                new_burning=new_burning,
                fuel_grid=fuel_grid,
                conditions=conditions,
                spread_modifier_grid=spread_modifier_grid,
                default_fuel=default_fuel,
                lat_max=lat_max,
                lat_min=lat_min,
                lng_min=lng_min,
                cell_lat=cell_lat,
                cell_lng=cell_lng,
                rows=rows,
                cols=cols,
                dt_minutes=dt_minutes,
                spotting_intensity=spotting_intensity,
                all_spot_fires=all_spot_fires,
                snapshot_spot_fires=snapshot_spot_fires,
                all_burned_cells=all_burned_cells,
                snapshot_burned_cells=snapshot_burned_cells,
                iteration=iteration,
            )

        # Add newly ignited cells to burning
        burning |= new_burning
        elapsed += dt_minutes
        iteration += 1

    # Final snapshot
    if elapsed > frames[-1].time_hours * 60.0 if frames else True:
        frame = _make_frame(
            min(elapsed, duration_minutes), all_burned_cells, snapshot_burned_cells,
            rows, cols, cell_size_m, fuel_grid, burned,
            mean_ros=last_mean_ros,
            spot_fires=list(snapshot_spot_fires) if snapshot_spot_fires else None,
        )
        frames.append(frame)

    total_burned = int(np.sum(burned))
    logger.info(
        "CA simulation complete: %.1fh, %d cells burned (%.1f ha), %d spot fires, %d iterations",
        config["duration_hours"], total_burned,
        total_burned * (cell_size_m ** 2) / 10000.0, len(all_spot_fires), iteration,
    )

    return frames


def _apply_spotting(
    burning: np.ndarray,
    burned: np.ndarray,
    new_burning: np.ndarray,
    fuel_grid: FuelGrid,
    conditions: SpreadConditions,
    spread_modifier_grid: SpreadModifierGrid | None,
    default_fuel: FuelType,
    lat_max: float,
    lat_min: float,
    lng_min: float,
    cell_lat: float,
    cell_lng: float,
    rows: int,
    cols: int,
    dt_minutes: float,
    spotting_intensity: float,
    all_spot_fires: list[SpotFire],
    snapshot_spot_fires: list[SpotFire],
    all_burned_cells: list[BurnedCell],
    snapshot_burned_cells: list[BurnedCell],
    iteration: int,
) -> None:
    """Apply ember spotting from the CA fire front.

    Identifies cells on the fire perimeter (burning cells adjacent to unburned
    fuel), converts them to FireVertex objects, and calls check_ember_spotting.
    Spot fire landing locations are seeded as new ignitions in new_burning.
    """
    # Extract fire front: burning cells that border unburned fuel
    front_vertices: list[FireVertex] = []
    burn_rows, burn_cols = np.where(burning)

    # Cap front sample for performance — every 3rd burning cell
    FRONT_SAMPLE_INTERVAL = 3
    for idx in range(0, len(burn_rows), FRONT_SAMPLE_INTERVAL):
        row, col = int(burn_rows[idx]), int(burn_cols[idx])
        # Only include cells with at least one unburned fuel neighbor (true front)
        is_front = False
        for dr, dc, _ in [(-1, 0, 0), (0, 1, 0), (1, 0, 0), (0, -1, 0)]:
            nr, nc = row + dr, col + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if not burned[nr, nc] and not burning[nr, nc]:
                    neighbor_fuel = fuel_grid.fuel_types[nr][nc]
                    if neighbor_fuel is not None:
                        is_front = True
                        break
        if is_front:
            cell_center_lat = lat_max - (row + 0.5) * cell_lat
            cell_center_lng = lng_min + (col + 0.5) * cell_lng
            front_vertices.append(FireVertex(lat=cell_center_lat, lng=cell_center_lng))

    if not front_vertices:
        return

    spots = check_ember_spotting(
        front=front_vertices,
        conditions=conditions,
        fuel_grid=fuel_grid,
        spread_modifier_grid=spread_modifier_grid,
        default_fuel=default_fuel,
        dt_minutes=dt_minutes,
        check_interval=1,
        intensity_multiplier=spotting_intensity,
    )

    for spot in spots:
        # Convert spot landing lat/lng to grid coordinates
        spot_row = int((lat_max - spot.lat) / cell_lat)
        spot_col = int((spot.lng - lng_min) / cell_lng)

        if not (0 <= spot_row < rows and 0 <= spot_col < cols):
            continue  # Outside grid bounds
        if burned[spot_row, spot_col] or burning[spot_row, spot_col] or new_burning[spot_row, spot_col]:
            continue  # Already burning

        spot_fuel = fuel_grid.fuel_types[spot_row][spot_col]
        if spot_fuel is None:
            continue  # Non-fuel landing cell

        new_burning[spot_row, spot_col] = True
        cell_lat_pos = lat_max - (spot_row + 0.5) * cell_lat
        cell_lng_pos = lng_min + (spot_col + 0.5) * cell_lng
        cell = BurnedCell(
            lat=cell_lat_pos,
            lng=cell_lng_pos,
            intensity=spot.hfi_kw_m,
            fuel_type=spot_fuel.value,
            timestep=iteration,
            fire_type="surface",  # Spot fire ignitions begin as surface fires
        )
        all_burned_cells.append(cell)
        snapshot_burned_cells.append(cell)
        all_spot_fires.append(spot)
        snapshot_spot_fires.append(spot)


def _elliptical_spread_prob(
    neighbor_angle: float,
    spread_dir: float,
    ros: float,
    lbr: float,
    cell_size: float,
    dt: float,
) -> float:
    """Calculate spread probability to a neighbor using the FBP directional ROS.

    Uses geometric interpolation between the three FBP reference points:
      θ = 0°   → head_ros   (maximum, downwind)
      θ = 90°  → flank_ros  = head_ros / LBR  (perpendicular to wind)
      θ = 180° → back_ros   = head_ros / LBR² (upwind)

    The formula is:
      dir_ros(θ) = head_ros × (back_ros / head_ros)^(θ / π)
                 = head_ros × LBR^(−2θ / π)

    This satisfies all three FBP reference conditions exactly:
      At θ=0:    head_ros × 1            = head_ros  ✓
      At θ=π/2:  head_ros × (1/LBR²)^½  = head_ros/LBR = flank_ros  ✓
      At θ=π:    head_ros × (1/LBR²)^1  = head_ros/LBR² = back_ros  ✓

    The polar-conic focus formula (a(1−e²)/(1−e·cosθ)) was tried but gives
    flank_ros/LBR at θ=90° instead of flank_ros — under high LBR this makes
    flank probability so low that the fire stalls when the head direction is
    blocked by non-fuel (roads, water bodies).

    Reference: FBP System ST-X-3; Alexander & de Groot (1988).
    """
    head_ros = ros
    if head_ros < 1e-10:
        return 0.0  # no spread

    back_ros = calculate_back_ros(head_ros, lbr)  # head_ros / LBR²

    # Angular separation from head fire direction, normalised to [0°, 180°]
    raw_diff = (neighbor_angle - spread_dir) % 360.0
    if raw_diff > 180.0:
        raw_diff = 360.0 - raw_diff
    theta_rad = math.radians(raw_diff)  # in [0, π]

    # Geometric interpolation: dir_ros = head_ros × (back_ros/head_ros)^(θ/π)
    ratio = back_ros / head_ros  # = 1/LBR² ∈ (0, 1]
    dir_ros = head_ros * (ratio ** (theta_rad / math.pi))

    # Distance fire can travel in this timestep
    dist = dir_ros * dt  # metres

    # Probability = fraction of cell size covered
    return min(1.0, dist / cell_size)


def _make_frame(
    elapsed_minutes: float,
    all_burned_cells: list[BurnedCell],
    new_burned_cells: list[BurnedCell],
    rows: int,
    cols: int,
    cell_size_m: float,
    fuel_grid: FuelGrid,
    burned: np.ndarray,
    mean_ros: float = 0.0,
    spot_fires: list[SpotFire] | None = None,
    ignition_snapped_m: float = 0.0,
) -> CellularFrame:
    """Create a frame snapshot with all cumulative cells + timestamps."""
    total = int(np.sum(burned))
    area_ha = total * (cell_size_m ** 2) / 10000.0

    # Fuel breakdown from all cells
    fuel_counts: dict[str, int] = {}
    for cell in all_burned_cells:
        fuel_counts[cell.fuel_type] = fuel_counts.get(cell.fuel_type, 0) + 1
    total_fuel = sum(fuel_counts.values()) or 1
    fuel_breakdown = {k: v / total_fuel for k, v in fuel_counts.items()}

    max_intensity = max((c.intensity for c in all_burned_cells), default=0.0)

    return CellularFrame(
        time_hours=elapsed_minutes / 60.0,
        burned_cells=list(all_burned_cells),  # snapshot copy — list grows after this call
        total_burned=total,
        new_cells=len(new_burned_cells),
        area_ha=area_ha,
        max_intensity=max_intensity,
        mean_ros=mean_ros,
        fuel_breakdown=fuel_breakdown,
        spot_fires=spot_fires,
        ignition_snapped_m=ignition_snapped_m,
    )
