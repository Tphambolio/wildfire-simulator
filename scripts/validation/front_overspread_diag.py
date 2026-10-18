"""Front over-spread diagnostics on harness fire-days (backlog A4/A7; descriptive).

For each fire-day of one split (default: calibration fires only) this runs the [R45] C1 set-up
(active edges 2 days, hourly FFMC spin-up, burning period 10-20 h) exactly as
``scripts/validate.py run`` does, optionally with a ROS factor per FBP fuel group, and records
what the standard run file does not:

- modelled growth (cells beyond the start area, arrival <= window) by FBP fuel group and by
  FBP fire type (surface CFB < 0.1, intermittent crown 0.1-0.9, continuous crown >= 0.9; CFB
  from the rate at which the front crossed the cell, as the frames compute it);
- observed CFSDS growth by fuel group (same fuel grid, same seasonal fuel rule);
- modelled growth by clock hour (arrival-time histogram), for the diurnal profile;
- F1 and growth ratio at 17 h, as the harness scores them (``score_day``).

ROS factors (``--factors C=0.5,M=0.7``) enter through a per-cell ROS multiplier keyed on the
cell's fuel group, the same multiplier path the WUI modifiers use; with no factors the run is the
C1 baseline. Fuel groups: C (C-1..C-7), M (M-1..M-4), D (D-1, D-2), O (O-1a, O-1b), S (S-1..S-3).

Descriptive only: no score, threshold or default changes. Needs PYTHONPATH=engine/src and
``--domains-dir`` as for validate.py.

    PYTHONPATH=engine/src CAPJOB_MEM=5G capjob python3 scripts/validation/front_overspread_diag.py \
        --domains-dir $FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains \
        --out OUT.jsonl --split calibration --workers 3 [--factors C=0.5]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GROUPS = ("C", "M", "D", "O", "S")
FIRE_TYPES = ("surface", "intermittent", "crown")
C1 = dict(ignition="active", active_days=2, ffmc_spinup=True, burning_period=(10.0, 20.0),
          windows_h=(17.0,))


def fuel_group(code: str | None) -> str:
    return "NF" if not code else code[0]


class FuelGroupFactors:
    """Duck-typed SpreadModifierGrid: ROS multiplier by the fuel group of the cell."""

    def __init__(self, fuel_grid, factors: dict[str, float]):
        self.fg = fuel_grid
        self.factors = factors
        self.rows, self.cols = fuel_grid.rows, fuel_grid.cols
        self.cell_lat = (fuel_grid.lat_max - fuel_grid.lat_min) / fuel_grid.rows
        self.cell_lng = (fuel_grid.lng_max - fuel_grid.lng_min) / fuel_grid.cols

    def get_modifiers_at(self, lat, lng):
        r = min(max(int((self.fg.lat_max - lat) / self.cell_lat), 0), self.rows - 1)
        c = min(max(int((lng - self.fg.lng_min) / self.cell_lng), 0), self.cols - 1)
        ft = self.fg.fuel_types[r][c]
        return self.factors.get(fuel_group(ft.value if ft else None), 1.0), 1.0, None


def _task(task: dict) -> dict:
    import validate as V

    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.huygens import FireVertex  # noqa: F401  (import check)
    from firesim.validation.harness import (
        Member, RunOptions, _setup, build_case, score_day,
    )

    logging.getLogger("firesim").setLevel(logging.WARNING)
    t0 = time.time()
    fid, day = task["fire_id"], task["day"]
    try:
        dom, groups, rec = V._load_fire(fid)
        case = build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
        opts = RunOptions(**{**C1, **({"burning_period": tuple(task["burning_period"])}
                                      if task.get("burning_period") else {})})
        setup = _setup(case, opts, Member())
        cfg = setup["config"]
        from firesim.spread.simulator import Simulator

        sim = Simulator(cfg, setup["fuel_grid"], setup["terrain"],
                        initial_burned=setup["burned_pts"])
        schedule = sim.weather_schedule()
        mods = FuelGroupFactors(setup["fuel_grid"], task["factors"]) if task["factors"] else None
        dur = cfg.duration_hours
        frames = run_cellular_simulation(
            config={"ignition_lat": cfg.ignition_lat, "ignition_lng": cfg.ignition_lng,
                    "duration_hours": dur},
            fuel_grid=setup["fuel_grid"], conditions=schedule[0][1],
            spread_modifier_grid=mods, terrain_grid=setup["terrain"], dt_minutes=1.0,
            weather_schedule=schedule, snapshot_interval_minutes=dur * 60.0,
            initial_perimeter=setup["perimeter"], initial_burned=setup["burned_pts"],
            compute_perimeter=False, active_edges=setup["active"],
            active_edge_buffer_m=opts.active_buffer_m, seed=f"{fid}:{day}:det")
        last = frames[-1]
        arrival = last.arrival if last.arrival is not None else np.full((dom.rows, dom.cols), np.inf)
        d = case.domain
        scores = score_day(case, arrival, opts)
        w = scores["17h"]
        prior = (d.dob > 0) & (d.dob < day)
        excluded = (arrival <= 0.0) | prior
        cell_ha = d.cell_area_m2 / 1e4
        # modelled growth by fuel group x fire type, and by clock hour
        mg = {g: {ft: 0.0 for ft in FIRE_TYPES} for g in GROUPS + ("NF",)}
        hours = np.zeros(25)
        fg = setup["fuel_grid"]
        cl = (fg.lat_max - fg.lat_min) / fg.rows
        cn = (fg.lng_max - fg.lng_min) / fg.cols
        ros_by_group: dict[str, list] = {g: [] for g in GROUPS}
        for cell in last.burned_cells:
            if not (0.0 < cell.arrival_min <= 17 * 60.0):
                continue
            r = int((fg.lat_max - cell.lat) / cl)
            c = int((cell.lng - fg.lng_min) / cn)
            if excluded[r, c]:
                continue
            g = fuel_group(cell.fuel_type)
            ft = cell.fire_type
            k = "surface" if ft in ("surface", "surface_with_torching") else (
                "crown" if ft == "active_crown" else "intermittent")
            mg[g][k] += cell_ha
            hours[int(6 + cell.arrival_min / 60.0)] += cell_ha
            if g in ros_by_group:
                ros_by_group[g].append(cell.ros)
        obs_growth = (d.dob == day) & ~excluded
        from firesim.data.fuel_loader import CODE_SCHEMES
        from firesim.validation.harness import _seasonal

        cmap = CODE_SCHEMES[d.fuel_scheme]
        og = {g: 0.0 for g in GROUPS + ("NF",)}
        codes, counts = np.unique(d.fuel[obs_growth], return_counts=True)
        for code, n in zip(codes, counts):
            ft = _seasonal(cmap.get(int(code)), day, opts)
            og[fuel_group(ft.value if ft else None)] += float(n) * cell_ha
        out = {
            "fire_id": fid, "day": day, "factors": task["factors"],
            "burning_period": list(opts.burning_period),
            "f1_17h": w["f1"], "growth_ratio_17h": w["growth_ratio"],
            "area_pred_ha": w["area_pred"] / 1e4, "area_obs_ha": w["area_obs"] / 1e4,
            "precision": w["precision"], "recall": w["recall"],
            "model_growth_ha": mg, "obs_growth_ha_by_group": og,
            "model_growth_by_clock_hour_ha": hours.tolist(),
            "median_cross_ros_m_min": {g: float(np.median(v)) if v else None
                                       for g, v in ros_by_group.items()},
            "ffmc": case.ffmc, "bui": case.bui, "fwi": case.fwi,
            "mean_ws_kmh": float(np.mean([h.wind_speed for h in case.hourly[:17]])),
        }
    except Exception as exc:  # recorded, not fatal
        out = {"fire_id": fid, "day": day, "error": repr(exc)}
    out["wall_s"] = time.time() - t0
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--domains-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", default="calibration", choices=("calibration", "test", "all"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--factors", default="", help="e.g. C=0.5,M=0.7 (fuel group -> ROS factor)")
    ap.add_argument("--burning-period", type=float, nargs=2, default=None,
                    help="local clock hours (default C1: 10 20)")
    a = ap.parse_args()
    os.environ["FIRESIM_VALIDATION_DOMAINS"] = a.domains_dir
    import validate as V
    from firesim.validation.report import split_fires

    factors = {k: float(v) for k, v in (p.split("=") for p in a.factors.split(",") if p)}
    manifest = json.loads(V.manifest_path().read_text())
    split = split_fires({m["fire_id"] for m in manifest})
    tasks = [{"fire_id": m["fire_id"], "day": d, "factors": factors,
              "burning_period": a.burning_period} for m in manifest
             for d in m["days"] if a.split == "all" or split[m["fire_id"]] == a.split]
    tasks.sort(key=lambda t: (t["fire_id"], t["day"]))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {(r["fire_id"], r["day"]) for r in map(json.loads, out.read_text().splitlines())
                if "error" not in r}
    tasks = [t for t in tasks if (t["fire_id"], t["day"]) not in done]
    print(f"{len(tasks)} fire-days -> {out}", flush=True)
    with Pool(a.workers, maxtasksperchild=8) as pool, open(out, "a") as fh:
        for k, r in enumerate(pool.imap_unordered(_task, tasks), 1):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(k, r["fire_id"], r["day"], r.get("f1_17h", r.get("error")),
                  round(r["wall_s"]), flush=True)


if __name__ == "__main__":
    main()
