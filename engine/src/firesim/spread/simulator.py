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
from dataclasses import replace
from typing import Generator

import numpy as np

from firesim.exposure import BuildingExposure, building_exposure, summarize
from firesim.fbp.calculator import calculate_flame_length, calculate_foliar_moisture
from firesim.fwi.calculator import hourly_ffmc
from firesim.fbp.constants import FuelType
from firesim.spread.huygens import (
    FireVertex,
    FuelGrid,
    SpreadConditions,
    SpreadModifierGrid,
    TerrainGrid,
    expand_fire_front,
    fbp_for_conditions,
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
        initial_burned: list[tuple[float, float]] | None = None,
        enable_spotting: bool = False,
        spotting_intensity: float = 1.0,
        building_centroids: list[tuple[float, float]] | None = None,
        acceleration: bool = True,
        building_footprints: list | None = None,
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
            building_centroids: List of (lat, lng) for buildings in nearby
                neighbourhoods. Used to count structures inside the perimeter per frame.
            building_footprints: shapely footprints (lng, lat) of those buildings. With the
                grid model, used for per-building exposure (distance, timing, radiant heat;
                ``firesim.exposure``); centroids are used when footprints are not given.
            initial_burned: (lat, lng) centres of already-burned grid cells, used by the
                grid model to continue a fire (e.g. the next day of a multi-day run).
            acceleration: Apply FBP point-ignition acceleration (ST-X-3 eqs 70-72)
                from each front's ignition. Fronts supplied via ``initial_front``
                (multi-day continuation, RPAS perimeter correction) are treated as
                established fires at equilibrium spread.
        """
        self.config = config
        self.fuel_grid = fuel_grid
        self.terrain_grid = terrain_grid
        self.default_fuel = default_fuel
        self.dt_minutes = dt_minutes
        self.num_rays = num_rays
        self.spread_modifier_grid = spread_modifier_grid
        self.initial_front = initial_front
        self.initial_burned = initial_burned
        self.enable_spotting = enable_spotting
        self.spotting_intensity = spotting_intensity
        if building_footprints and not building_centroids:
            building_centroids = [(g.centroid.y, g.centroid.x) for g in building_footprints]
        self.building_centroids = building_centroids
        self.building_footprints = building_footprints
        self.acceleration = acceleration

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
        if self.fuel_grid is not None:
            yield from self._run_cellular()
            return

        # Initialize fire front — use provided front (multi-day continuation) or
        # create a fresh ignition circle from the ignition point.
        if self.initial_front is not None and len(self.initial_front) >= 3:
            front = self.initial_front
            front_ignited = None  # established fire: no acceleration
        else:
            front = self._create_ignition_front(config.ignition_lat, config.ignition_lng)
            front_ignited = 0.0

        self._schedule = self.weather_schedule()

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
        # minutes at which each front was ignited (None = established, no acceleration)
        ignited_at: list[float | None] = [front_ignited if self.acceleration else None]
        all_spot_fires: list[SpotFire] = []

        # Yield initial frame (t=0)
        merged = self._merge_fronts(fronts)
        yield self._create_frame(merged, 0.0, spot_fires=[], num_fronts=1)

        # Main simulation loop
        while elapsed_minutes < total_minutes:
            dt = min(self.dt_minutes, total_minutes - elapsed_minutes)
            conditions = self.conditions_at(elapsed_minutes + 0.5 * dt)

            new_fronts: list[list[FireVertex]] = []
            timestep_spots: list[SpotFire] = []

            for f, t_ign in zip(fronts, ignited_at):
                window = (
                    None if t_ign is None
                    else (elapsed_minutes - t_ign, elapsed_minutes + dt - t_ign)
                )
                new_front = expand_fire_front(
                    front=f,
                    conditions=conditions,
                    fuel_grid=self.fuel_grid,
                    terrain_grid=self.terrain_grid,
                    dt_minutes=dt,
                    default_fuel=self.default_fuel,
                    num_rays=self.num_rays,
                    spread_modifier_grid=self.spread_modifier_grid,
                    accel_window=window,
                )
                new_fronts.append(simplify_front(new_front))

                # Ember spotting: opt-in, and only with a fuel grid (no barriers to jump otherwise)
                if self.enable_spotting and self.fuel_grid is not None:
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
            new_ignited = list(ignited_at)
            for spot in timestep_spots:
                if len(new_fronts) >= MAX_FRONTS:
                    break
                new_fronts.append(
                    self._create_ignition_front(spot.lat, spot.lng)
                )
                new_ignited.append(elapsed_minutes + dt if self.acceleration else None)
                all_spot_fires.append(spot)

            fronts = new_fronts
            ignited_at = new_ignited
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
        self._schedule = self.weather_schedule()

        ca_frames = run_cellular_simulation(
            config={
                "ignition_lat": config.ignition_lat,
                "ignition_lng": config.ignition_lng,
                "duration_hours": config.duration_hours,
            },
            fuel_grid=self.fuel_grid,
            conditions=self._schedule[0][1],
            default_fuel=self.default_fuel,
            spread_modifier_grid=self.spread_modifier_grid,
            terrain_grid=self.terrain_grid,
            dt_minutes=1.0,
            weather_schedule=self._schedule,
            snapshot_interval_minutes=config.snapshot_interval_minutes,
            enable_spotting=self.enable_spotting,
            spotting_intensity=self.spotting_intensity,
            acceleration=self.acceleration,
            initial_perimeter=(
                [(v.lat, v.lng) for v in self.initial_front]
                if self.initial_front and len(self.initial_front) >= 3 else None
            ),
            initial_burned=self.initial_burned,
        )

        exposure = self._building_exposure(ca_frames[-1].emitters if ca_frames else None,
                                           config.duration_hours * 60.0)

        # Each frame's cells are a prefix of the final frame's (ordered by arrival), so the
        # cell dicts are built once and each frame takes a slice
        all_cells = [
            {
                "lat": c.lat, "lng": c.lng,
                "intensity": c.intensity,
                "fuel": c.fuel_type,
                "fire_type": c.fire_type,
                "t": c.timestep,
                "ros": round(c.ros, 3),
                "part": c.part,
            }
            for c in (ca_frames[-1].burned_cells if ca_frames else [])
        ]
        arrival_raster = self._arrival_raster(ca_frames[-1].arrival if ca_frames else None)

        for i, cf in enumerate(ca_frames):
            # Outline of the largest burned area (polygon), for export, building counts
            # and multi-day carry-over
            perimeter = cf.perimeter or []
            burned_data = all_cells[:len(cf.burned_cells)]
            is_last = i == len(ca_frames) - 1

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
                flame_length_m=calculate_flame_length(
                    cf.max_intensity, 1.0 if frame_fire_type in (FireType.PASSIVE_CROWN, FireType.ACTIVE_CROWN) else 0.0
                ),
                fuel_breakdown=cf.fuel_breakdown,
                spot_fires=ca_spot_fires,
                num_fronts=1,
                burned_cells=burned_data,
                buildings_at_risk=(inside := self._buildings_inside(perimeter)),
                ignition_snapped_m=cf.ignition_snapped_m,
                building_exposure=(
                    summarize(exposure, cf.time_hours * 60.0 + 1e-9, inside) if exposure else None
                ),
                building_exposure_detail=(
                    _exposure_detail(exposure) if exposure and is_last else None
                ),
                head=self._head_summary(cf.head),
                arrival_raster=arrival_raster if is_last else None,
            )

    def _head_summary(self, head: dict | None) -> dict | None:
        """The grid frame's head cell, with the Albini maximum spotting distance there."""
        if head is None:
            return None
        from firesim.spread.albini import max_spot_distance

        wind = self.conditions_at(head["t"]).wind_speed  # 10 m wind when the head got there
        out = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in head.items()}
        out["max_spot_distance_m"] = round(
            max_spot_distance(head["fuel"], head["hfi"], head["cfb"], wind), 1
        )
        return out

    def _arrival_raster(self, arrival) -> dict | None:
        """Arrival minutes per fuel-grid cell (rounded; -1 = not burned), north row first."""
        if arrival is None or self.fuel_grid is None:
            return None
        g = self.fuel_grid
        minutes = np.where(np.isfinite(arrival), np.rint(arrival), -1).astype(np.int16)
        return {
            "rows": g.rows, "cols": g.cols,
            "lat_min": g.lat_min, "lat_max": g.lat_max, "lng_min": g.lng_min, "lng_max": g.lng_max,
            "minutes": minutes,
        }

    def _building_exposure(self, emitters, duration_min: float):
        """Per-building exposure records from the grid model's flame panels, or None."""
        if emitters is None or not (self.building_footprints or self.building_centroids):
            return None
        import shapely

        def local(coords):
            x, y = emitters.to_local(coords[:, 0], coords[:, 1])
            return np.column_stack([x, y])

        if self.building_footprints:
            geoms = shapely.transform(
                np.asarray(self.building_footprints, dtype=object), lambda xy: local(xy[:, ::-1])
            )
            cents = shapely.centroid(np.asarray(self.building_footprints, dtype=object))
            targets = [
                (float(shapely.get_y(c)), float(shapely.get_x(c)), g) for c, g in zip(cents, geoms)
            ]
            use_footprints = True
        else:
            lat = np.array([b[0] for b in self.building_centroids])
            lng = np.array([b[1] for b in self.building_centroids])
            x, y = emitters.to_local(lat, lng)
            targets = [(float(a), float(o), shapely.Point(px, py))
                       for a, o, px, py in zip(lat, lng, x, y)]
            use_footprints = False
        return building_exposure(targets, emitters, duration_min=duration_min,
                                 use_footprints=use_footprints)

    def _buildings_inside(self, perimeter: list[tuple[float, float]]) -> int:
        """Number of building centroids inside a (lat, lng) perimeter polygon."""
        if not self.building_centroids or len(perimeter) < 3:
            return 0
        import shapely

        poly = shapely.Polygon([(lng, lat) for lat, lng in perimeter])
        if not poly.is_valid:
            poly = poly.buffer(0)
        lats = np.array([b[0] for b in self.building_centroids])
        lngs = np.array([b[1] for b in self.building_centroids])
        return int(shapely.contains_xy(poly, lngs, lats).sum())

    def _foliar_moisture(self) -> float:
        """FMC: explicit override, else ST-X-3 eqs 1-8 from location and date, else 100 %."""
        config = self.config
        if config.fmc is not None:
            return config.fmc
        if config.day_of_year is not None:
            return calculate_foliar_moisture(
                config.ignition_lat, config.ignition_lng, config.elevation_m, config.day_of_year
            )
        return 100.0

    def weather_schedule(self) -> list[tuple[float, SpreadConditions]]:
        """(start minute, conditions) for each weather period, in time order.

        Without an hourly stream there is one period. With one, each record sets the wind,
        and FFMC is advanced through that hour with the hourly FFMC model from the previous
        period's value, starting from the configured FFMC; the hour uses the FFMC reached at
        its end (the moisture state its own weather produces).
        """
        base = self._spread_conditions()
        records = sorted(self.config.hourly_weather or (), key=lambda r: r.hours_from_start)
        if not records:
            return [(0.0, base)]
        schedule = [] if records[0].hours_from_start <= 0.0 else [(0.0, base)]
        ffmc = base.ffmc
        for i, rec in enumerate(records):
            end = records[i + 1].hours_from_start if i + 1 < len(records) else rec.hours_from_start + 1.0
            hours = max(end - rec.hours_from_start, 1e-6)
            ffmc = hourly_ffmc(rec.temperature, rec.relative_humidity, rec.wind_speed,
                               rec.precipitation, ffmc, hours)
            schedule.append((max(rec.hours_from_start, 0.0) * 60.0, replace(
                base, wind_speed=rec.wind_speed, wind_direction=rec.wind_direction, ffmc=ffmc,
            )))
        return schedule

    def conditions_at(self, minutes: float) -> SpreadConditions:
        """Conditions in force ``minutes`` after the start."""
        schedule = self._schedule if hasattr(self, "_schedule") else self.weather_schedule()
        current = schedule[0][1]
        for start, cond in schedule:
            if start <= minutes + 1e-9:
                current = cond
            else:
                break
        return current

    def _spread_conditions(self) -> SpreadConditions:
        """Weather, FWI codes and fuel modifiers for the FBP calculations."""
        config = self.config
        return SpreadConditions(
            wind_speed=config.weather.wind_speed,
            wind_direction=config.weather.wind_direction,
            ffmc=config.ffmc if config.ffmc is not None else 85.0,
            dmc=config.dmc if config.dmc is not None else 40.0,
            dc=config.dc if config.dc is not None else 200.0,
            pc=config.percent_conifer,
            grass_cure=config.grass_cure,
            pdf=config.percent_dead_fir,
            gfl=config.grass_fuel_load,
            fmc=self._foliar_moisture(),
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
        self, lat: float, lng: float, radius_m: float = 1.0, num_points: int = 12
    ) -> list[FireVertex]:
        """Create initial fire front as a small circle around ignition point.

        Starting with a single point causes degenerate geometry, so the front
        starts as a 1 m circle. A Huygens front grows the whole starting shape
        outward, so a larger circle adds roughly perimeter x radius to the area
        (a 30 m circle made a 30 min C-2 fire about 60 % too large).

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

        # Head-fire FBP metrics for the default fuel on flat ground, current weather
        fbp = fbp_for_conditions(self.conditions_at(time_hours * 60.0), self.default_fuel)

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

        # Count structures at risk within the fire perimeter
        buildings_at_risk = self._buildings_inside(perimeter)

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
            buildings_at_risk=buildings_at_risk,
        )


def _finite(v: float) -> float | None:
    return round(v, 2) if math.isfinite(v) else None


def _exposure_detail(records: list[BuildingExposure]) -> list[dict]:
    """Per-building exposure for buildings within 500 m of the fire (JSON-friendly)."""
    return [
        {
            "lat": r.lat, "lng": r.lng,
            "min_distance_m": round(r.min_distance_m, 1),
            "band": r.band,
            "first_within_30m_min": _finite(r.first_within_min.get(30.0, math.inf)),
            "first_within_100m_min": _finite(r.first_within_min.get(100.0, math.inf)),
            "peak_flux_kw_m2": round(r.peak_flux, 2),
            "minutes_over_12_5": round(r.minutes_over_12_5, 2),
            "ftp_index": round(r.ftp_index, 4),
            "ftp_index_high_emissive": round(r.ftp_index_high, 4),
        }
        for r in records
        if r.min_distance_m <= 500.0
    ]

