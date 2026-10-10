"""Sanity check: each open-data grid loads in the FireSim engine and spreads (local engine only).

For each AOI and grid variant, ignite one O-1b cell and one forest cell (C-2 on the leaf-on grid,
D-1 on the leaf-off grid) and run 2 h of spread with fixed, moderately severe weather.
Needs ``PYTHONPATH=engine/src``.
"""

from __future__ import annotations

import json
import logging

import numpy as np
from pyproj import Transformer
from scipy import ndimage as ndi

try:
    from . import config as C
    from .build import WORK, file_stem
    from .sources import aoi_grid
except ImportError:  # pragma: no cover
    import config as C  # type: ignore[no-redef]
    from build import WORK, file_stem  # type: ignore[no-redef]
    from sources import aoi_grid  # type: ignore[no-redef]

log = logging.getLogger("fuelgrid.firesim")


def _site(cls: np.ndarray, value: int, grid) -> tuple[float, float]:
    """Centre cell (lat, lng) of the largest patch of `value`, eroded so it is interior."""
    m = ndi.binary_erosion(cls == value, iterations=3)
    if not m.any():
        m = cls == value
    lab, n = ndi.label(m)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    rr, cc = np.nonzero(lab == sizes.argmax())
    i = np.argmin((rr - rr.mean()) ** 2 + (cc - cc.mean()) ** 2)
    x = grid.x0 + (cc[i] + 0.5) * grid.cell
    y = grid.y0 - (rr[i] + 0.5) * grid.cell
    lng, lat = Transformer.from_crs(C.CRS, "EPSG:4326", always_xy=True).transform(x, y)
    return lat, lng


def run() -> dict:
    from firesim.data.fuel_loader import load_fuel_grid
    from firesim.spread.simulator import Simulator
    from firesim.types import SimulationConfig, WeatherInput

    out = {}
    for key in C.AOIS:
        grid = aoi_grid(key)
        cls = np.load(WORK / f"{key}.npz")["cls"]
        sites = {"O-1b": _site(cls, C.O1B, grid), "C-2": _site(cls, C.C2, grid), "D-2": _site(cls, C.D2, grid)}
        for variant, scheme, doy in (("fbp_opendata_leafon", "auto", 200),
                                     ("fbp_opendata_leafon_ciffc", "cfs_national", 200),
                                     ("fbp_opendata_leafoff_ciffc", "cfs_national", 120)):
            path = C.OUT_DIR / f"{file_stem(key, variant)}.tif"
            fg = load_fuel_grid(str(path), target_resolution_m=C.CELL, code_scheme=scheme)
            for name in ("O-1b", "C-2", "D-2"):
                lat, lng = sites[name]
                fuel = fg.get_fuel_at(lat, lng)
                cfg = SimulationConfig(ignition_lat=lat, ignition_lng=lng,
                                       weather=WeatherInput(25.0, 25.0, 20.0, 270.0, 0.0),
                                       duration_hours=2.0, snapshot_interval_minutes=60.0,
                                       ffmc=92.0, dmc=40.0, dc=300.0, grass_cure=90.0,
                                       day_of_year=doy)
                frames = list(Simulator(cfg, fuel_grid=fg).run())
                last = frames[-1]
                rec = {"aoi": key, "grid": path.name, "scheme": scheme, "grid_cells": [fg.rows, fg.cols],
                       "ignition": name, "lat": round(lat, 5), "lng": round(lng, 5),
                       "fuel_at_ignition": getattr(fuel, "value", None), "area_ha_2h": round(last.area_ha, 2),
                       "head_ros_m_min": round(float(last.head_ros_m_min or 0), 2)}
                out[f"{key}/{variant}/{name}"] = rec
                log.info("%s", rec)
    (C.VAL_DIR / "firesim_check.json").write_text(json.dumps(out, indent=2))
    return out
