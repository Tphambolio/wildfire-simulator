"""Fire spread simulation orchestrator.

The Simulator class accepts configuration (ignition point, weather, fuel grid,
terrain) and runs the Huygens wavelet spread algorithm. It yields SimulationFrame
snapshots at configurable intervals, enabling real-time streaming.

Usage:
    sim = Simulator(config, fuel_grid, terrain_grid)
    for frame in sim.run():
        print(f"Time: {frame.time_hours}h, Area: {frame.area_ha} ha")
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Generator

from firesim.fbp.calculator import calculate_fbp
from firesim.fbp.constants import FuelType
from firesim.spread.ellipse import calculate_length_to_breadth_ratio
from firesim.spread.huygens import (
    FireVertex,
    FuelGrid,
    SpreadConditions,
    SpreadModifierGrid,
    TerrainGrid,
    expand_fire_front,
    simplify_front,
)
from firesim.spread.perimeter import calculate_polygon_area_ha, vertices_to_polygon
from firesim.spread.cellular import CellularFrame, run_cellular_simulation
from firesim.spread.spotting import SpotFire, check_ember_spotting
from firesim.types import FBPResult, FireType, SimulationConfig, SimulationFrame

logger = logging.getLogger(__name__)


class Simulator:
    """Fire spread simulation using Huygens wavelet expansion.

    The simulator runs as a generator, yielding SimulationFrame objects
    at each snapshot interval. This enables streaming to clients.

    Attributes:
        config: Simulation configuration (ignition, weather, duration)
        fuel_grid: Spatial fuel type grid (or None for uniform fuel)
        terrain_grid: Slope/aspect grid (or None for flat terrain)
        default_fuel: Fuel type when grid is None or lookup returns None
    """

    def __init__(
        self,
        config: SimulationConfig,
        fuel_grid: FuelGrid | None = None,
        terrain_grid: TerrainGrid | None = None,
        default_fuel: FuelType = FuelType.C2,
        dt_minutes: float = 5.0,
        num_rays: int = 36,
        spread_modifier_grid: SpreadModifierGrid | None = None,
        initial_front: list[FireVertex] | None = None,
        enable_spotting: bool = False,
        spotting_intensity: float = 1.0,
    ):
        """Initialize simulator.

        Args:
            config: Simulation configuration
            fuel_grid: Spatial fuel types (None = uniform default_fuel)
            terrain_grid: Slope/aspect (None = flat terrain)
            default_fuel: Default fuel type when no grid available
            dt_minutes: Internal timestep (minutes). Controls simulation
                accuracy. Smaller = more accurate but slower.
            num_rays: Number of directional rays per Huygens wavelet.
                More rays = smoother perimeters but slower.
            spread_modifier_grid: Per-cell WUI zone modifiers (None = no modification)
        """
        self.config = config
        self.fuel_grid = fuel_grid
        self.terrain_grid = terrain_grid
        self.default_fuel = default_fuel
        self.dt_minutes = dt_minutes
        self.num_rays = num_rays
        self.spread_modifier_grid = spread_modifier_grid
        self.initial_front = initial_front
        self.enable_spotting = enable_spotting
        self.spotting_intensity = spotting_intensity

    def run(self) -> Generator[SimulationFrame, None, None]:
        """Run the simulation, yielding frames at snapshot intervals.

        Auto-selects spread model:
        - Fuel grid present → cellular automaton (wraps around buildings)
        - No fuel grid → Huygens wavelet (open wildland)

        Yields:
            SimulationFrame at each snapshot_interval_minutes
        """
        config = self.config

        # Auto-select: CA for large spatial grids (real-world data),
        # Huygens for uniform fuel or small test grids
        if self.fuel_grid is not None and self.fuel_grid.rows >= 50 and self.fuel_grid.cols >= 50:
            yield from self._run_cellular()
            return

        # Initialize fire front — use provided front (multi-day continuation) or
        # create a fresh ignition circle from the ignition point.
        if self.initial_front is not None and len(self.initial_front) >= 3:
            front = self.initial_front
        else:
            front = self._create_ignition_front(config.ignition_lat, config.ignition_lng)

        # Build spread conditions from config
        conditions = SpreadConditions(
            wind_speed=config.weather.wind_speed,
            wind_direction=config.weather.wind_direction,
            ffmc=config.ffmc if config.ffmc is not None else 85.0,
            dmc=config.dmc if config.dmc is not None else 40.0,
            dc=config.dc if config.dc is not None else 200.0,
        )

        # Time tracking
        total_minutes = config.duration_hours * 60.0
        snapshot_interval = config.snapshot_interval_minutes
        elapsed_minutes = 0.0
        next_snapshot = snapshot_interval

        logger.info(
            "Starting simulation: ignition=(%.4f, %.4f), duration=%.1fh, fuel=%s",
            config.ignition_lat,
            config.ignition_lng,
            config.duration_hours,
            self.default_fuel.value,
        )

        # Multi-front support: list of independent fire fronts
        fronts: list[list[FireVertex]] = [front]
        all_spot_fires: list[SpotFire] = []

        # Yield initial frame (t=0)
        merged = self._merge_fronts(fronts)
        yield self._create_frame(merged, 0.0, spot_fires=[], num_fronts=1)

        # Main simulation loop
        while elapsed_minutes < total_minutes:
            dt = min(self.dt_minutes, total_minutes - elapsed_minutes)

            new_fronts: list[list[FireVertex]] = []
            timestep_spots: list[SpotFire] = []

            for f in fronts:
                new_front = expand_fire_front(
                    front=f,
                    conditions=conditions,
                    fuel_grid=self.fuel_grid,
                    terrain_grid=self.terrain_grid,
                    dt_minutes=dt,
                    default_fuel=self.default_fuel,
                    num_rays=self.num_rays,
                    spread_modifier_grid=self.spread_modifier_grid,
                )
                new_fronts.append(simplify_front(new_front))

                # Check ember spotting (only when fuel grid present — no barriers to jump otherwise)
                if self.fuel_grid is not None:
                    spots = check_ember_spotting(
                        front=f,
                        conditions=conditions,
                        fuel_grid=self.fuel_grid,
                        spread_modifier_grid=self.spread_modifier_grid,
                        default_fuel=self.default_fuel,
                        dt_minutes=dt,
                    )
                    timestep_spots.extend(spots)

            # Create new fronts from spot fires (cap at 10 active fronts)
            MAX_FRONTS = 10
            for spot in timestep_spots:
                if len(new_fronts) >= MAX_FRONTS:
                    break
                new_fronts.append(
                    self._create_ignition_front(spot.lat, spot.lng, radius_m=15.0)
                )
                all_spot_fires.append(spot)

            fronts = new_fronts
            elapsed_minutes += dt

            # Yield snapshot if we've reached the interval
            if elapsed_minutes >= next_snapshot or elapsed_minutes >= total_minutes:
                time_hours = elapsed_minutes / 60.0
                merged = self._merge_fronts(fronts)
                frame = self._create_frame(
                    merged, time_hours,
                    spot_fires=all_spot_fires,
                    num_fronts=len(fronts),
                )
                yield frame
                next_snapshot += snapshot_interval

        merged_final = self._merge_fronts(fronts)
        logger.info(
            "Simulation complete: %.1fh, final area=%.1f ha, %d fronts, %d spot fires",
            config.duration_hours,
            calculate_polygon_area_ha(merged_final),
            len(fronts),
            len(all_spot_fires),
        )

    def _run_cellular(self) -> Generator[SimulationFrame, None, None]:
        """Run cellular automaton spread model.

        Used when fuel_grid is present (urban/WUI scenarios).
        Produces per-cell burned data instead of perimeter polygons.
        """
        config = self.config
        conditions = SpreadConditions(
            wind_speed=config.weather.wind_speed,
            wind_direction=config.weather.wind_direction,
            ffmc=config.ffmc if config.ffmc is not None else 85.0,
            dmc=config.dmc if config.dmc is not None else 40.0,
            dc=config.dc if config.dc is not None else 200.0,
        )

        ca_frames = run_cellular_simulation(
            config={
                "ignition_lat": config.ignition_lat,
                "ignition_lng": config.ignition_lng,
                "duration_hours": config.duration_hours,
            },
            fuel_grid=self.fuel_grid,
            conditions=conditions,
            default_fuel=self.default_fuel,
            spread_modifier_grid=self.spread_modifier_grid,
            dt_minutes=1.0,
            snapshot_interval_minutes=config.snapshot_interval_minutes,
            enable_spotting=self.enable_spotting,
            spotting_intensity=self.spotting_intensity,
        )

        for cf in ca_frames:
            # Convert CellularFrame to SimulationFrame
            # Perimeter = convex boundary of burned cells (for area display)
            if cf.burned_cells:
                perimeter = [
                    (c.lat, c.lng) for c in cf.burned_cells[::max(1, len(cf.burned_cells) // 100)]
                ]
            else:
                perimeter = []

            # Build burned_cells list for heatmap rendering (with fire_type for color coding)
            burned_data = [
                {
                    "lat": c.lat, "lng": c.lng,
                    "intensity": c.intensity,
                    "fuel": c.fuel_type,
                    "fire_type": c.fire_type,
                    "t": c.timestep,
                }
                for c in cf.burned_cells
            ]

            ca_spot_fires = None
            if cf.spot_fires:
                ca_spot_fires = [
                    {
                        "lat": s.lat, "lng": s.lng,
                        "distance_m": s.distance_m, "hfi_kw_m": s.hfi_kw_m,
                        "source_lat": s.source_lat, "source_lng": s.source_lng,
                    }
                    for s in cf.spot_fires
                ]

            # Derive worst (most severe) fire type from all burned cells
            _TYPE_PRIORITY = {
                "active_crown": 3,
                "passive_crown": 2,
                "surface_with_torching": 1,
                "surface": 0,
            }
            if cf.burned_cells:
                worst_type_str = max(
                    (c.fire_type for c in cf.burned_cells),
                    key=lambda ft: _TYPE_PRIORITY.get(ft, 0),
                )
                frame_fire_type = FireType(worst_type_str)
            else:
                frame_fire_type = FireType.SURFACE

            yield SimulationFrame(
                time_hours=cf.time_hours,
                perimeter=perimeter,
                area_ha=cf.area_ha,
                head_ros_m_min=cf.mean_ros,
                max_hfi_kw_m=cf.max_intensity,
                fire_type=frame_fire_type,
                flame_length_m=0.0,
                fuel_breakdown=cf.fuel_breakdown,
                spot_fires=ca_spot_fires,
                num_fronts=1,
                burned_cells=burned_data,
            )

    @staticmethod
    def _merge_fronts(fronts: list[list[FireVertex]]) -> list[FireVertex]:
        """Merge multiple fire fronts into a single vertex list.

        For simplicity, concatenates all vertices. The convex hull in
        simplify_front will handle the outer boundary.
        """
        merged: list[FireVertex] = []
        for f in fronts:
            merged.extend(f)
        return merged

    def _create_ignition_front(
        self, lat: float, lng: float, radius_m: float = 30.0, num_points: int = 12
    ) -> list[FireVertex]:
        """Create initial fire front as a small circle around ignition point.

        Starting with a single point causes degenerate geometry. Instead,
        we initialize with a small circle representing the initial fire.

        Args:
            lat: Ignition latitude
            lng: Ignition longitude
            radius_m: Initial fire radius (meters)
            num_points: Number of vertices in the initial circle

        Returns:
            List of FireVertex forming a small circle
        """
        m_per_deg_lat = 111320.0
        m_per_deg_lng = 111320.0 * math.cos(math.radians(lat))

        vertices = []
        for i in range(num_points):
            angle = 2.0 * math.pi * i / num_points
            dlat = radius_m * math.cos(angle) / m_per_deg_lat
            dlng = radius_m * math.sin(angle) / m_per_deg_lng
            vertices.append(FireVertex(lat=lat + dlat, lng=lng + dlng))

        return vertices

    def _create_frame(
        self, front: list[FireVertex], time_hours: float,
        spot_fires: list[SpotFire] | None = None,
        num_fronts: int = 1,
    ) -> SimulationFrame:
        """Create a SimulationFrame from the current fire front.

        Calculates area, ROS, intensity, and other metrics from the
        current fire front state.
        """
        # Calculate area
        area_ha = calculate_polygon_area_ha(front)

        # Calculate FBP for the head fire direction to get metrics
        fbp = calculate_fbp(
            fuel_type=self.default_fuel,
            wind_speed=self.config.weather.wind_speed,
            ffmc=self.config.ffmc if self.config.ffmc is not None else 85.0,
            dmc=self.config.dmc if self.config.dmc is not None else 40.0,
            dc=self.config.dc if self.config.dc is not None else 200.0,
        )

        # Build fuel breakdown
        fuel_breakdown: dict[str, float] = {}
        if self.fuel_grid is not None:
            counts: dict[str, int] = defaultdict(int)
            total = 0
            for v in front:
                ft = self.fuel_grid.get_fuel_at(v.lat, v.lng)
                if ft is not None:
                    counts[ft.value] += 1
                    total += 1
            if total > 0:
                fuel_breakdown = {k: v / total for k, v in counts.items()}
        else:
            fuel_breakdown = {self.default_fuel.value: 1.0}

        # Perimeter as list of (lat, lng)
        perimeter = vertices_to_polygon(front)

        return SimulationFrame(
            time_hours=time_hours,
            perimeter=perimeter,
            area_ha=area_ha,
            head_ros_m_min=fbp.ros_final,
            max_hfi_kw_m=fbp.hfi,
            fire_type=fbp.fire_type,
            flame_length_m=fbp.flame_length,
            fuel_breakdown=fuel_breakdown,
            spot_fires=[
                {
                    "lat": s.lat, "lng": s.lng,
                    "distance_m": s.distance_m, "hfi_kw_m": s.hfi_kw_m,
                    "source_lat": s.source_lat, "source_lng": s.source_lng,
                }
                for s in (spot_fires or [])
            ] or None,
            num_fronts=num_fronts,
        )
