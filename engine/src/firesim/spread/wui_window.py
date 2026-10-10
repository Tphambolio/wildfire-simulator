"""Run the grid model at the fuel grid's native 20 m in a window around the fire near buildings.

Mechanics decision M5 (2026-10-10): run at the fuel grid's native 20 m within the
wildland-urban interface and keep 50 m elsewhere, within the 2 GB live server's memory.

Design (docs/verification.md "20 m WUI window"): a nested level set (20 m cells inside, 50 m
outside, coupled at the boundary) would need two-way coupling, since a front can leave the
window and come back, and a mixed-resolution output for every consumer (frames, perimeter,
arrival raster, flame panels, structure contact). Instead:

1. The run is made at 50 m on the whole grid, as before. Its burned area sizes the window.
2. If buildings lie in the window (the box of the 50 m burned cells grown by a margin), the
   whole run is repeated at the native cell size on a crop of that box, with the same
   physics, weather, seed and building mask. Every output then comes from one grid at one
   resolution, and the FBP level set is unchanged (its verification holds at any cell size).
3. The 20 m result is kept only if it is complete: no burned cell within ``edge_cells`` of a
   crop side that is not the grid's own edge, and no spot fire landing outside the crop.
   Otherwise the margin is doubled and the crop repeated (``max_attempts`` in all).
4. Guards: the crop must hold at most ``max_cells`` cells and the predicted (and actual)
   number of 20 m burned cells must stay under ``max_burned_cells``. When a guard fails, or the
   fire keeps reaching the crop edge, the 50 m run is returned, labelled with the reason.

The window is therefore "20 m where the fire and the buildings are, when the run area is
small enough", and the 50 m grid everywhere else. A run without buildings in its window stays
at 50 m (M5 limits 20 m to the WUI).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from firesim.data.raster_grid import M_PER_DEG_LAT

logger = logging.getLogger(__name__)

# Notes carried on frames (``SimulationFrame.grid``); short, shown in a tooltip
NOTE_USED = "20 m cells (native fuel grid) in a window around the fire near buildings"
NOTE_NO_BUILDINGS = "50 m cells: no buildings near the fire"
NOTE_TOO_LARGE = "50 m cells: the fire is too large for the 20 m window"
NOTE_EDGE = "50 m cells: the fire kept reaching the edge of the 20 m window"
NOTE_COARSE = "50 m cells"


@dataclass(frozen=True)
class WindowOptions:
    """Limits of the 20 m window (memory and run-time guards; see the module docstring).

    ``max_cells``: 20 m cells in the crop. The grid run's peak traced memory is about 265
    bytes per cell (whole-grid float arrays; ``engine/tests/spread/test_wui_window.py`` bounds
    it at 320), so 600,000 cells (240 km², a 15.5 km square) peak at about 160 MB, close to the
    50 m run on the whole Edmonton grid (502,090 cells, ~133 MB), which is freed before the crop
    is run. The window therefore adds little to the run's memory peak.
    ``max_burned_cells``: 20 m burned cells (60,000 = 2,400 ha); bounds the frame payload
    and the run time (the level set's work grows with the burned area).
    """

    max_cells: int = 600_000
    max_burned_cells: int = 60_000
    min_margin_m: float = 500.0
    margin_fraction: float = 0.5  # of the 50 m burned box's larger side
    max_attempts: int = 3
    edge_cells: int = 6  # the level set's computational band (cellular.BAND_CELLS)


@dataclass
class WindowResult:
    """What the run used: the grid, its frames and a summary for the frames."""

    grid: object  # FuelGrid the frames are on
    frames: list
    used: bool
    cell_m: float
    reason: str  # "used", "no_buildings", "too_large", "edge", "disabled"
    note: str
    attempts: int = 0
    window_cells: int = 0
    bounds: tuple | None = None
    detail: dict = field(default_factory=dict)

    def summary(self) -> dict:
        """JSON-friendly summary carried on every frame (``SimulationFrame.grid``)."""
        out = {"cell_m": round(self.cell_m, 1), "wui_window": self.used, "reason": self.reason,
               "note": self.note}
        if self.bounds is not None and self.used:
            out["window"] = [round(float(v), 6) for v in self.bounds]
            out["window_cells"] = self.window_cells
        return out


def _cell_sizes(grid) -> tuple[float, float, float, float]:
    cell_lat = (grid.lat_max - grid.lat_min) / grid.rows
    cell_lng = (grid.lng_max - grid.lng_min) / grid.cols
    mid = math.radians((grid.lat_max + grid.lat_min) / 2.0)
    return cell_lat, cell_lng, cell_lat * M_PER_DEG_LAT, cell_lng * M_PER_DEG_LAT * math.cos(mid)


def burned_box(arrival, grid, duration_min, ignition) -> tuple[float, float, float, float]:
    """(lat_min, lat_max, lng_min, lng_max) of the cells burned by ``duration_min`` (the
    ignition point when none burned)."""
    cell_lat, cell_lng, _, _ = _cell_sizes(grid)
    burned = None if arrival is None else np.isfinite(arrival) & (arrival <= duration_min)
    if burned is None or not burned.any():
        lat, lng = ignition
        return lat, lat, lng, lng
    r = np.nonzero(burned.any(axis=1))[0]
    c = np.nonzero(burned.any(axis=0))[0]
    return (grid.lat_max - (r[-1] + 1) * cell_lat, grid.lat_max - r[0] * cell_lat,
            grid.lng_min + c[0] * cell_lng, grid.lng_min + (c[-1] + 1) * cell_lng)


def grow_box(box, margin_m: float, clip) -> tuple[float, float, float, float]:
    """``box`` grown by ``margin_m`` on every side, clipped to ``clip``."""
    lat_min, lat_max, lng_min, lng_max = box
    mid = math.radians((lat_min + lat_max) / 2.0)
    dlat = margin_m / M_PER_DEG_LAT
    dlng = margin_m / (M_PER_DEG_LAT * math.cos(mid))
    return (max(clip[0], lat_min - dlat), min(clip[1], lat_max + dlat),
            max(clip[2], lng_min - dlng), min(clip[3], lng_max + dlng))


def reaches_edge(arrival, duration_min, crop, outer, edge_cells: int) -> bool:
    """True when a cell burned by ``duration_min`` lies within ``edge_cells`` of a side of
    ``crop`` (a FuelGrid) that is not on the edge of ``outer`` (lat_min, lat_max, lng_min,
    lng_max)."""
    if arrival is None:
        return False
    burned = np.isfinite(arrival) & (arrival <= duration_min)
    if not burned.any():
        return False
    cell_lat, cell_lng, _, _ = _cell_sizes(crop)
    rows, cols = burned.shape
    r = np.nonzero(burned.any(axis=1))[0]
    c = np.nonzero(burned.any(axis=0))[0]
    k = edge_cells
    north_open = crop.lat_max < outer[1] - 0.5 * cell_lat
    south_open = crop.lat_min > outer[0] + 0.5 * cell_lat
    west_open = crop.lng_min > outer[2] + 0.5 * cell_lng
    east_open = crop.lng_max < outer[3] - 0.5 * cell_lng
    return bool((north_open and r[0] < k) or (south_open and r[-1] >= rows - k)
                or (west_open and c[0] < k) or (east_open and c[-1] >= cols - k))


def run_with_window(run_fn, coarse_grid, coarse_frames, duration_min, fine_source, *,
                    ignition, has_buildings, building_geoms=None,
                    options: WindowOptions | None = None) -> WindowResult:
    """The 20 m window run, or the 50 m run with the reason it was kept.

    Args:
        run_fn: ``run_fn(grid) -> list[CellularFrame]``: the grid model with the run's
            settings on another grid (same weather, seed, options).
        coarse_grid, coarse_frames: the 50 m run (its last frame carries ``arrival``).
        duration_min: run length (minutes).
        fine_source: ``FineFuelSource`` (native cells; ``cells_in``, ``crop``, ``cell_m``).
        ignition: (lat, lng) of the ignition.
        has_buildings: ``has_buildings(bounds) -> bool``: buildings intersect the box.
        building_geoms: footprints (lng/lat) masked as non-fuel on the crop (the run grid's
            building mask).
    """
    opts = options or WindowOptions()
    _, _, ch, cw = _cell_sizes(coarse_grid)
    coarse_cell = min(ch, cw)

    def keep(reason, note, **kw):
        return WindowResult(grid=coarse_grid, frames=coarse_frames, used=False,
                            cell_m=coarse_cell, reason=reason, note=note, **kw)

    arrival = coarse_frames[-1].arrival if coarse_frames else None
    outer = (coarse_grid.lat_min, coarse_grid.lat_max, coarse_grid.lng_min, coarse_grid.lng_max)
    sb = fine_source.bounds
    outer = (max(outer[0], sb[0]), min(outer[1], sb[1]), max(outer[2], sb[2]), min(outer[3], sb[3]))
    box = burned_box(arrival, coarse_grid, duration_min, ignition)
    extent_m = max((box[1] - box[0]) * M_PER_DEG_LAT,
                   (box[3] - box[2]) * M_PER_DEG_LAT * math.cos(math.radians(box[0])))
    margin = max(opts.min_margin_m, opts.margin_fraction * extent_m)
    n_coarse = int(np.sum(np.isfinite(arrival) & (arrival <= duration_min))) if arrival is not None else 0
    predicted = n_coarse * (coarse_cell / fine_source.cell_m) ** 2
    detail = {"coarse_burned_cells": n_coarse, "predicted_fine_burned_cells": int(predicted)}

    bounds = grow_box(box, margin, outer)
    if not has_buildings(bounds):
        return keep("no_buildings", NOTE_NO_BUILDINGS, detail=detail)
    if predicted > opts.max_burned_cells:
        return keep("too_large", NOTE_TOO_LARGE, detail=detail)
    for attempt in range(1, opts.max_attempts + 1):
        bounds = grow_box(box, margin, outer)
        cells = fine_source.cells_in(bounds)
        if cells > opts.max_cells:
            return keep("too_large", NOTE_TOO_LARGE, attempts=attempt - 1, window_cells=cells,
                        detail=detail)
        crop = fine_source.crop(bounds, building_geoms)
        frames = run_fn(crop)
        last = frames[-1] if frames else None
        fine_arrival = last.arrival if last is not None else None
        n_fine = (int(np.sum(np.isfinite(fine_arrival) & (fine_arrival <= duration_min)))
                  if fine_arrival is not None else 0)
        detail.update({"fine_burned_cells": n_fine, "margin_m": round(margin, 0)})
        if n_fine > opts.max_burned_cells:
            return keep("too_large", NOTE_TOO_LARGE, attempts=attempt, window_cells=cells,
                        detail=detail)
        edge = reaches_edge(fine_arrival, duration_min, crop, outer, opts.edge_cells)
        spots_out = getattr(last, "spots_off_grid", 0) if last is not None else 0
        if not edge and not spots_out:
            logger.info("20 m window: %dx%d cells, %d burned, attempt %d", crop.rows, crop.cols,
                        n_fine, attempt)
            return WindowResult(grid=crop, frames=frames, used=True, cell_m=fine_source.cell_m,
                                reason="used", note=NOTE_USED, attempts=attempt,
                                window_cells=cells, bounds=bounds, detail=detail)
        logger.info("20 m window attempt %d: fire reached the window edge (edge %s, %d spots "
                    "off the crop); growing the margin", attempt, edge, spots_out)
        margin *= 2.0
    return keep("edge", NOTE_EDGE, attempts=opts.max_attempts, detail=detail)
