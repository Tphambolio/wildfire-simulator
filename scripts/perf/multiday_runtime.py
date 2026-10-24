"""Time (and optionally profile) the app's default multi-day run through the API runner.

Purpose: backlog E10 (slow live 72 h runs). Reproduces the request the frontend sends for the
default Setup in multi-day mode (Edmonton grid, terrain on, buildings on but unused by
multi-day, 3 days of W 20/25/30 km/h, FFMC 90 / DMC 45 / DC 300, 30 min snapshots, burning
period 10-20 local) and runs it synchronously through ``SimulationRunner._execute_multiday``,
the code path the live server uses. Writes the per-day wall time, the final arrival grid of
each day (``.npz``) and a summary JSON, so two code versions can be compared cell by cell.

Usage (from the repository root, data files as in ``data/`` plus the Alberta grids):
    PYTHONPATH=engine/src:api/src python scripts/perf/multiday_runtime.py OUT_DIR \
        [--lat 53.4605 --lng -113.6599] [--start 2026-10-24T00:32:00-06:00] [--profile]
Environment: FIRESIM_AB_FUEL_GRID_PATH, FIRESIM_AB_TERRAIN_PATH (optional; the live server
has both), FIRESIM_CITY_FUEL_GRID_PATH / FIRESIM_CITY_DEM_PATH (set to the local data/ copies).
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
os.environ.setdefault("FIRESIM_CITY_FUEL_GRID_PATH",
                      os.path.join(DATA, "Edmonton_FBP_FuelLayer_20251105_10m.tif"))
os.environ.setdefault("FIRESIM_CITY_DEM_PATH", os.path.join(DATA, "edmonton_dem.tif"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lat", type=float, default=53.4605)
    ap.add_argument("--lng", type=float, default=-113.6599)
    ap.add_argument("--start", default="2026-10-24T00:32:00-06:00")
    ap.add_argument("--days", default="20/270/25/30,25/270/28/25,30/260/30/20",
                    help="wind/dir/temp/rh per day, comma separated")
    ap.add_argument("--grass-cure", type=float, default=95.0)
    ap.add_argument("--doy", type=int, default=297)
    ap.add_argument("--month", type=int, default=10)
    ap.add_argument("--no-burning-period", action="store_true")
    ap.add_argument("--profile", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    from firesim_api.schemas.simulation import MultiDaySimulationCreate
    from firesim_api.services.runner import SimulationRun, SimulationRunner

    days = []
    for d in a.days.split(","):
        ws, wd, t, rh = (float(x) for x in d.split("/"))
        days.append({"wind_speed": ws, "wind_direction": wd, "temperature": t,
                     "relative_humidity": rh, "precipitation_24h": 0})
    params = MultiDaySimulationCreate(
        ignition_lat=a.lat, ignition_lng=a.lng, days=days,
        fwi_overrides={"ffmc": 90, "dmc": 45, "dc": 300},
        fuel_modifiers={"grass_cure": a.grass_cure, "percent_conifer": 50, "day_of_year": a.doy},
        month=a.month, snapshot_interval_minutes=30, fuel_type="C2",
        fuel_grid_path=os.environ["FIRESIM_CITY_FUEL_GRID_PATH"],
        buildings_path=os.path.join(DATA, "edmonton_buildings.geojson.gz"),
        dem_path=os.environ["FIRESIM_CITY_DEM_PATH"], start_time=a.start,
        burning_period=None if a.no_burning_period else {"start_hour": 10, "end_hour": 20},
    )
    runner = SimulationRunner()
    run = SimulationRun("perf", params)
    marks: list[tuple[float, int, float]] = []  # (wall s, day, sim hours)
    t0 = time.perf_counter()

    def on_frame(_id, frame, day):
        marks.append((time.perf_counter() - t0, day, frame.time_hours))

    prof = cProfile.Profile() if a.profile else None
    if prof:
        prof.enable()
    runner._execute_multiday(run, params, on_frame)
    if prof:
        prof.disable()
        prof.dump_stats(os.path.join(a.out, "profile.pstats"))
        with open(os.path.join(a.out, "profile.txt"), "w") as fh:
            pstats.Stats(prof, stream=fh).sort_stats("cumulative").print_stats(45)
            pstats.Stats(prof, stream=fh).sort_stats("tottime").print_stats(30)
    total = time.perf_counter() - t0

    day_end = {}
    for wall, day, _h in marks:
        day_end[day] = wall
    frames = run.frames
    final = frames[-1] if frames else None
    summary = {
        "status": str(run.status), "error": run.error, "wall_s": round(total, 2),
        "day_end_wall_s": {str(k): round(v, 2) for k, v in day_end.items()},
        "n_frames": len(frames),
        "area_ha_by_day_end": {str(d): next((f.area_ha for f, dd in zip(frames, run.frame_days)
                                             if dd == d and abs(f.time_hours - 24 * d) < 1e-6),
                                            None) for d in range(1, len(days) + 1)}
        if hasattr(run, "frame_days") else None,
        "final_area_ha": final.area_ha if final else None,
        "fuel_source": run.fuel_source,
        "args": vars(a),
    }
    # Cell set of every frame (lat, lng, t) for equivalence checks
    cells = []
    for f in frames:
        for c in f.burned_cells or []:
            cells.append((f.time_hours, c["lat"], c["lng"], c.get("t", np.nan), c.get("ros", np.nan),
                          c.get("intensity", np.nan)))
    np.savez_compressed(os.path.join(a.out, "frames.npz"),
                        time_hours=np.array([f.time_hours for f in frames]),
                        area_ha=np.array([f.area_ha for f in frames]),
                        cells=np.array(cells, dtype=float).reshape(-1, 6))
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    print(json.dumps(summary, default=str)[:1500])


if __name__ == "__main__":
    sys.exit(main())
