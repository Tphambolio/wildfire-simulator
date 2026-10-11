#!/usr/bin/env python3
"""Indicative check: Alberta Wildfire FBP 2025 grid vs the national 2014b grid on a few CFSDS
fire-days (same cases, same weather, same set-up; only the fuel grid differs).

Small on purpose: a handful of fire-days from the CALIBRATION half of the validation split
(``firesim.validation.report.split_fires``), never the held-out test fires, so it cannot leak
into the held-out results or the separate pre-registered northern test. Not a skill estimate.

    PYTHONPATH=engine/src python scripts/validation/ab_fuel_compare.py AB_COG.tif [--n 5]

Set-up: the validation's chosen operational set-up (docs/validation.md, second round): active
edges from the previous day's growth, FFMC spin-up from 17:00 the day before, burning period
10-20 h, start 06:00, scored at 17 h. The Alberta codes are read onto each domain's own lattice
(nearest neighbour, 100 m -> the domain's 90 m or coarser cells) by windowed reads.
Writes JSON lines to $FIRESIM_VALIDATION_DATA/ab_fuel/compare.jsonl.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate as V  # noqa: E402  (scripts/validate.py: manifest, weather, domains)

from firesim.data.provincial import Lattice, read_codes_on_lattice  # noqa: E402
from firesim.validation.harness import RunOptions, build_case, run_fire_day  # noqa: E402
from firesim.validation.report import split_fires  # noqa: E402

OPTS = RunOptions(ignition="active", ffmc_spinup=True, burning_period=(10.0, 20.0),
                  windows_h=(17.0,))


def ab_domain(dom, ab_path: str):
    lat = Lattice(dom.lat_max, dom.lng_min, dom.cell_lat, dom.cell_lng, dom.rows, dom.cols)
    codes = read_codes_on_lattice(ab_path, lat)
    return dataclasses.replace(dom, fuel=codes.astype(np.int32), fuel_scheme="ab_wildfire",
                               meta={**dom.meta, "fuel_source": Path(ab_path).name})


def fuel_shares(dom, mask) -> dict:
    from firesim.data.fuel_loader import CODE_SCHEMES

    cmap = CODE_SCHEMES[dom.fuel_scheme]
    vals, n = np.unique(dom.fuel[mask], return_counts=True)
    out: dict[str, float] = {}
    for v, k in zip(vals.tolist(), n.tolist()):
        ft = cmap.get(int(v))
        key = ft.value if ft is not None else "non-fuel"
        out[key] = out.get(key, 0) + k
    tot = max(1, int(mask.sum()))
    return {k: round(v / tot, 3) for k, v in sorted(out.items(), key=lambda x: -x[1])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ab_path")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--max-cells", type=int, default=400_000)
    ap.add_argument("--fires", nargs="*", help="calibration fire ids to use instead of the "
                    "first --n by id (e.g. scars the 2025 grid has not re-typed)")
    ap.add_argument("--out", default="compare.jsonl")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    manifest = json.loads(V.manifest_path().read_text())
    side = split_fires([m["fire_id"] for m in manifest])
    picks = [m for m in sorted(manifest, key=lambda m: m["fire_id"])
             if side[m["fire_id"]] == "calibration" and m["grid"][0] * m["grid"][1] <= args.max_cells
             and m["days"] and (not args.fires or m["fire_id"] in args.fires)][: args.n]
    out_dir = V.ROOT / "ab_fuel"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / args.out, "w") as f:
        for m in picks:
            dom, groups, rec = V._load_fire(m["fire_id"])
            day = m["days"][0]
            row = {"fire_id": m["fire_id"], "day": day, "res_m": m["res_m"], "grid": m["grid"]}
            for name, d in (("national_2014b", dom), ("ab_wildfire_2025", ab_domain(dom, args.ab_path))):
                t = time.time()
                case = build_case(d, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
                r = run_fire_day(case, OPTS)
                s = r["members"]["det"]["17h"]
                growth = (case.domain.dob == day)
                row["obs_growth_ha"] = r["obs_growth_ha"]
                row[name] = {"f1": s["f1"], "area_diff_norm": s["area_diff_norm"],
                             "precision": s["precision"], "recall": s["recall"],
                             "pred_ha": s["area_pred"] / 1e4, "obs_ha": s["area_obs"] / 1e4,
                             "fuel_in_obs_growth": fuel_shares(case.domain, growth),
                             "run_s": round(time.time() - t, 1)}
            print(json.dumps(row))
            f.write(json.dumps(row) + "\n")
            f.flush()


if __name__ == "__main__":
    main()
