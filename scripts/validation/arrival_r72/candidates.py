#!/usr/bin/env python3
"""[R72] choose the PREREG_ARRIVAL1 candidate on non-held-out data only.

Purpose
    Stage 1 shows the shared under-spread is not ember-driven, but the two simple set-up changes
    that recover arrivals (no burning period; a blanket ROS factor) cost 17 h F1 and add area,
    which the F1 and US over-prediction guards would catch. This script compares a small family
    of ISI-conditioned burning periods (burning_conditions.py; ISI_MIN in ISI_GRID) with the
    operational run and with no burning period, on two non-held-out sets:
      arrival  the 20 C1b fire-days (14 primary + 6 Horse River; already scored in [R66]),
               C1b metric (communities_c1b) with the stored baseline's start mask;
      area     the record's 16 calibration fires (validate.py manifest, report.split_fires
               'calibration'; the back-cast domains of [R45]), 17 h F1 (harness.score_day) and
               log10 (modelled + 1 ha) / (observed + 1 ha) growth-area ratio.
    The record's 79 held-out (H79) fire-days and every fresh fire are not used.

Usage
    CAPJOB_MEM=5G capjob python3 scripts/validation/arrival_r72/candidates.py arrival|area [--workers 3]
Outputs
    OUT/candidates_arrival.jsonl, OUT/candidates_area.jsonl (one line per fire-day x variant)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
ISI_GRID = (6.0, 10.0, 14.0, 20.0)
VARIANTS = ["base", "bp_off"] + [f"isi{int(x)}" for x in ISI_GRID]


def run_variant(case, v):
    """Arrival grid of variant ``v`` (operational options otherwise)."""
    import numpy as np

    import common as K
    from burning_conditions import conditioned_schedule
    from firesim.spread.cellular import run_cellular_simulation
    from firesim.spread.huygens import FireVertex
    from firesim.spread.simulator import Simulator
    from firesim.validation.harness import Member, _setup

    if v == "base":
        return K.run(case, K.opts_operational())[0], K.opts_operational()
    if v == "bp_off":
        o = K.opts_operational(burning_period=None)
        return K.run(case, o)[0], K.opts_operational()
    thr = float(v[3:])
    o = K.opts_operational(burning_period=None)
    setup = _setup(case, o, Member())
    cfg = setup["config"]
    sim = Simulator(cfg, setup["fuel_grid"], setup["terrain"],
                    initial_front=[FireVertex(lat=a, lng=b) for a, b in setup["perimeter"]] if setup["perimeter"] else None,
                    initial_burned=setup["burned_pts"])
    sched = conditioned_schedule(sim.weather_schedule(), cfg.start_hour, K.BP, thr)
    dur = cfg.duration_hours
    frames = run_cellular_simulation(
        config={"ignition_lat": cfg.ignition_lat, "ignition_lng": cfg.ignition_lng, "duration_hours": dur},
        fuel_grid=setup["fuel_grid"], conditions=sched[0][1], terrain_grid=setup["terrain"], dt_minutes=1.0,
        weather_schedule=sched, snapshot_interval_minutes=dur * 60.0, enable_spotting=False,
        initial_perimeter=setup["perimeter"], initial_burned=setup["burned_pts"], compute_perimeter=False,
        active_edges=setup["active"], active_edge_buffer_m=o.active_buffer_m, seed=f"{case.fire_id}:{case.day}:det")
    arr = frames[-1].arrival if frames and frames[-1].arrival is not None else np.full((case.domain.rows, case.domain.cols), np.inf)
    return arr.astype("float32"), K.opts_operational()


def task(args):
    mode, fid, day, pop = args
    import logging

    import numpy as np

    import common as K
    from firesim.validation.harness import score_day

    logging.getLogger("firesim").setLevel(logging.WARNING)
    case = K.load_case(fid, day)
    out = []
    base_start = K.stored_baseline(fid, day) <= 0 if mode == "arrival" else None
    for v in VARIANTS:
        try:
            arr, opts = run_variant(case, v)
            s = score_day(case, arr, opts)["17h"]
            f1 = 0.0 if s["f1"] is None or (isinstance(s["f1"], float) and math.isnan(s["f1"])) else float(s["f1"])
            row = {"fire_day": f"{fid}:{day}", "population": pop, "variant": v, "f1_17h": f1,
                   "area_pred_m2": s["area_pred"], "area_obs_m2": s["area_obs"],
                   "log10_area_ratio": math.log10((s["area_pred"] + 1e4) / (s["area_obs"] + 1e4))}
            if mode == "arrival":
                res = K.score_with_start(case, arr, base_start)
                row.update({"mae_h": res["mae_h"], "hits": res["hits"], "n": res["n"],
                            "rows": [{"name": r["name"], "model_h": round(r["model_h"], 3), "err_h": round(r["err_h"], 3)}
                                     for r in res["rows"]]})
            out.append(row)
        except Exception as exc:  # noqa: BLE001
            out.append({"fire_day": f"{fid}:{day}", "variant": v, "error": repr(exc)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["arrival", "area"])
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if a.mode == "area":
        vd = "/home/rpas/dev/wildfire/validation-data"
        os.environ["FIRESIM_VALIDATION_DATA"] = vd
        os.environ["FIRESIM_VALIDATION_DOMAINS"] = f"{vd}/backcast/roots/ab2025_backcast/domains"
    sys.path.insert(0, str(HERE))
    import common as K

    if a.mode == "arrival":
        days = [(a.mode, f, d, p) for f, d, p in K.all_days()]
    else:
        from firesim.validation.report import split_fires

        man = json.loads(Path(os.environ["FIRESIM_VALIDATION_DATA"], "manifest.json").read_text())
        sp = split_fires({m["fire_id"] for m in man})
        days = [(a.mode, m["fire_id"], int(d), "calibration") for m in man if sp[m["fire_id"]] == "calibration"
                for d in m["days"]]
    out = K.OUT / f"candidates_{a.mode}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(x)["fire_day"] for x in out.read_text().splitlines() if x.strip()}
    todo = [t for t in days if f"{t[1]}:{t[2]}" not in done]
    print(len(days), "fire-days,", len(todo), "to run", flush=True)
    with Pool(a.workers, maxtasksperchild=1) as pool:
        for rows in pool.imap_unordered(task, todo):
            with open(out, "a") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=float) + "\n")
            print(rows[0]["fire_day"], [(r["variant"], round(r.get("mae_h", r.get("f1_17h", float("nan"))), 3)) for r in rows], flush=True)


if __name__ == "__main__":
    main()
