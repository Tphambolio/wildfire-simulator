"""Run the grid model on fixed cases and save each final arrival/ROS grid, so two engine
versions can be compared cell by cell (backlog E10 equivalence check).

Cases (synthetic 50 m grid, no data files needed): point ignition 6 h; the same with ember
spotting on; hourly weather changes with a 10-20 burning period over 30 h; a starting burned
area (multi-day day 2 style) for 24 h. Usage:
    PYTHONPATH=<engine src> python scripts/perf/grid_equivalence.py OUT.npz
"""
import sys
import time

import numpy as np

from firesim.data.synthetic_grid import generate_synthetic_fuel_grid
from firesim.spread.cellular import run_cellular_simulation
from firesim.spread.diurnal import burning_period_schedule
from firesim.spread.huygens import SpreadConditions


def cond(ws, wd, ffmc=90.0):
    return SpreadConditions(wind_speed=ws, wind_direction=wd, ffmc=ffmc, dmc=45.0, dc=300.0)


def main(out):
    grid = generate_synthetic_fuel_grid(53.5, -113.5, radius_km=4.0, cell_size_m=50.0, seed=11)
    base = {"ignition_lat": 53.5, "ignition_lng": -113.5}
    sched = [(0.0, cond(20, 270)), (120.0, cond(30, 250)), (300.0, cond(15, 300, 88.0)),
             (600.0, cond(25, 270))]
    cases = {
        "point_6h": dict(config={**base, "duration_hours": 6}, conditions=cond(20, 270)),
        "spotting_6h": dict(config={**base, "duration_hours": 6}, conditions=cond(30, 250),
                            enable_spotting=True, spotting_intensity=2.0, seed=5),
        "schedule_bp_30h": dict(config={**base, "duration_hours": 30}, conditions=sched[0][1],
                                weather_schedule=burning_period_schedule(sched, 8.0, (10, 20), 1800.0)),
    }
    res = {}
    for name, kw in cases.items():
        t0 = time.perf_counter()
        frames = run_cellular_simulation(fuel_grid=grid, **kw)
        res[name + "_arrival"] = frames[-1].arrival
        res[name + "_area"] = np.array([f.area_ha for f in frames]) if hasattr(frames[-1], "area_ha") else np.array([len(f.burned_cells) for f in frames])
        print(name, f"{time.perf_counter() - t0:.2f} s", int(np.isfinite(frames[-1].arrival).sum()), "cells")
    # Day-2 style: start from the burned cells of point_6h
    arr = res["point_6h_arrival"]
    rows, cols = arr.shape
    cl = (grid.lat_max - grid.lat_min) / rows
    cg = (grid.lng_max - grid.lng_min) / cols
    pts = [(grid.lat_max - (r + 0.5) * cl, grid.lng_min + (c + 0.5) * cg) for r, c in np.argwhere(np.isfinite(arr))]
    t0 = time.perf_counter()
    frames = run_cellular_simulation(config={**base, "duration_hours": 24}, fuel_grid=grid,
                                     conditions=cond(25, 260), initial_burned=pts)
    res["day2_arrival"] = frames[-1].arrival
    print("day2", f"{time.perf_counter() - t0:.2f} s", int(np.isfinite(frames[-1].arrival).sum()), "cells")
    np.savez_compressed(out, **res)


if __name__ == "__main__":
    main(sys.argv[1])
