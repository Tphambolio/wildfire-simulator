#!/usr/bin/env python3
"""Pre-registered rescoring of the [R27] fire-days with the pre-fire (back-cast) Alberta grid.

The Alberta Wildfire 2025 grid maps each validation fire's own scar, and later scars, as
post-fire fuel. [R27] masked those scars and filled them from the nearest unmasked cell; the
back-cast (``firesim.data.backcast``, ``scripts/fuelgrid/backcast.py``) instead restores the
cells NBAC records as burned since the scenario date from a SCANFI v2 epoch typed with the CFS
national rules. ``PREREG`` below was committed and pushed before any back-cast run was scored.

    python scripts/validation/backcast_fire_test.py prereg
    python scripts/validation/backcast_fire_test.py domains ab2025_backcast     # and ab2025_backcast14
    python scripts/validation/backcast_fire_test.py run ab2025_backcast --workers 3
    python scripts/validation/backcast_fire_test.py analyse --out <dir>

Data stay outside the repository: domains and runs under
``$FIRESIM_VALIDATION_DATA/backcast``; comparator runs are the burn-scar sensitivity reruns
(``$FIRESIM_VALIDATION_DATA/burnscar-bias/runs``), which reproduce [R27] per fire-day exactly.
Needs PYTHONPATH=engine/src:. (repo root for ``scripts.*``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
VALDATA = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
OUT = VALDATA / "backcast"
COMPARATORS = VALDATA / "burnscar-bias" / "runs"
PREFIRE = Path(os.environ.get("PREFIRE_DATA", Path.home() / "dev/wildfire/prefire-data")) / "out"
NAT2014 = VALDATA / "fuels" / "nat2014b" / "nat_fbpfuels_2014b.tif"

PREREG = {
    "registered": "2026-10-12, committed and pushed before any back-cast run was scored "
                  "(the comparator runs were already known from [R27] and the burn-scar check)",
    "question": "Does the pre-fire back-cast of the Alberta Wildfire 2025 grid (NBAC burns since "
                "the scenario date restored from SCANFI v2, CFS national rules) predict observed "
                "one-day growth better than the same grid with [R27]'s scar mask and "
                "nearest-cell fill?",
    "fire_days": "The 143 fire-days of 32 Alberta fires of [R27] (manifest.json), unchanged: "
                 "ERA5 hourly weather, CFSDS codes, DOB, DEM, working areas.",
    "setup": "[R27]'s chosen set-up, unchanged: active edges 2 days, FFMC spin-up, burning "
             "period 10-20 h, start 06:00, deterministic, no spotting; windows 4-24 h. The "
             "harness code translation of the Alberta grid is the one [R27] used "
             "(opendata_fire_test.grid_codes 'ab2025': 6xx -> M-1/M-2 at the run's 50 %, "
             "31 -> O-1b, 105 non-fuel).",
    "scenario_date": "Per fire, the first day of its CFSDS day-of-burning (the fire's start); "
                     "one domain per fire, as in [R27].",
    "grids": {
        "ab2025_backcast": "(PRIMARY) Alberta 2025 grid back-cast to the fire's start: cells "
                           "with NBAC restore key >= the start date take the SCANFI v2 epoch "
                           "strictly before the fire year (2010 for 2014-2015 fires, 2015 for "
                           "2016-2020, 2020 for 2021-2024), typed by "
                           "scripts/fuelgrid/backcast.py as built; built on the 20 m fire-area "
                           "grid by nearest from 100 m, then mode to the domain, as [R27].",
        "ab2025_backcast14": "(secondary, source choice) the same back-cast with the restored "
                             "cells taken from CFS 2014b instead of SCANFI (2014b codes mapped "
                             "to Alberta codes). Validation only: 2014b is internal-use.",
        "comparators": "nat2014 (CFS 2014b), ab2025_masked ([R27] extra) and ab2025_unmasked "
                       "(burn-scar check), reused from burnscar-bias/runs (they reproduce "
                       "[R27] per fire-day exactly).",
    },
    "primary_endpoint": "Held-out (16 test fires, 79 fire-days) mean per-fire-day 17 h F1 "
                        "difference ab2025_backcast - ab2025_masked; 95 % CI from 2,000 "
                        "whole-fire bootstrap draws, seed 1 (opendata_fire_test.boot). "
                        "'better' / 'worse' only if the CI excludes 0, else 'no detectable "
                        "difference'.",
    "secondary": "Held-out: ab2025_backcast - nat2014, ab2025_backcast - ab2025_unmasked, "
                 "ab2025_backcast14 - ab2025_backcast (source choice: 2014b vs SCANFI fill); "
                 "all 32 fires; 8 h; area difference; fuel groups of the observed growth per "
                 "grid. Same bootstrap and decision words.",
    "decision_use": "If the back-cast is not worse than the masked grid, FireSim applies it to "
                    "dated historical scenarios (decision approved 2026-10-12, source chosen on "
                    "this evidence). A worse result is reported as such and blocks the default.",
}

GRIDS = ("ab2025_backcast", "ab2025_backcast14")
WINDOWS = (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 17.0, 24.0)

# CFS 2014b codes -> Alberta Wildfire codes (fuel_loader.CFS_NATIONAL_2014_CODES); M-3/M-4 have
# no Alberta code and become M-1/M-2 50 % (none under the Alberta fires' restored cells in
# practice; counted in the domain report); wetland 120 non-fuel as in the harness.
NAT2014_TO_AB = {101: 1, 102: 2, 103: 3, 104: 4, 105: 5, 106: 6, 107: 7, 108: 13, 109: 650,
                 110: 650, 111: 650, 112: 650, 113: 21, 114: 22, 115: 23, 116: 31, 117: 31,
                 118: 102, 119: 101, 120: 101, 121: 101, 122: 105}


def _odt():
    spec = importlib.util.spec_from_file_location("odt", REPO / "scripts/validation/opendata_fire_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ab_to_harness(raw: np.ndarray) -> np.ndarray:
    """Alberta codes -> the harness's CIFFC codes, exactly as [R27] (grid_codes 'ab2025')."""
    codes = np.zeros(raw.shape, np.int16)
    for c in (1, 2, 3, 4, 5, 6, 7, 13, 21, 22, 23, 101, 102, 104, 105):
        codes[raw == c] = c
    codes[raw == 31] = 32
    codes[(raw >= 610) & (raw <= 690)] = 60
    codes[codes == 0] = 101
    return codes


def fire_start(dom) -> int:
    """Scenario date of a fire: its first CFSDS day of burning, as YYYYDDD."""
    return dom.year * 1000 + int(dom.dob[dom.dob > 0].min())


def backcast_codes(fire_id: str, grid: str) -> tuple[np.ndarray, object, dict]:
    """20 m Alberta codes back-cast to the fire's start, the area Grid and stats."""
    from rasterio.enums import Resampling

    from firesim.data.backcast import BackcastData, compose
    from firesim.validation.cfsds import FireDomain
    from scripts.fuelgrid import fire_areas as FA

    odt = _odt()
    a = next(x for x in FA.load_areas() if x["fire_id"] == fire_id)
    g = FA.area_grid(a)
    dom = FireDomain.load(VALDATA / f"domains/{fire_id}.npz")
    skey = fire_start(dom)
    data = BackcastData.load(str(PREFIRE))
    epoch, epath = data.epoch_for(dom.year)
    base = FA._warp_remote(str(odt.AB2025), g, Resampling.nearest, dtype=np.int32)
    key = FA._warp_remote(data.burn_key, g, Resampling.nearest, dtype=np.int32)
    if grid == "ab2025_backcast":
        pre = FA._warp_remote(epath, g, Resampling.nearest, dtype=np.int32)
        src = f"SCANFI v2 {epoch}"
    else:
        raw14 = FA._warp_remote(str(NAT2014), g, Resampling.nearest, dtype=np.int32)
        pre = np.zeros(raw14.shape, np.int32)
        for k, v in NAT2014_TO_AB.items():
            pre[raw14 == k] = v
        src = "CFS 2014b"
    out, restored, unrestored = compose(base, key, pre, skey)
    st = {"scenario": skey, "epoch": epoch if grid == "ab2025_backcast" else None, "source": src,
          "restored_share": float(restored.mean()), "unrestored_share": float(unrestored.mean())}
    return ab_to_harness(out), g, st


def cmd_prereg(_a) -> None:
    print(json.dumps(PREREG, indent=1))


def cmd_domains(a) -> None:
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.warp import reproject

    from firesim.validation.cfsds import FireDomain
    from firesim.validation.harness import crop_for_day

    man = json.loads((VALDATA / "manifest.json").read_text())
    root = OUT / "roots" / a.grid
    (root / "domains").mkdir(parents=True, exist_ok=True)
    for name in ("cfsds", "weather"):
        if not (root / name).exists():
            (root / name).symlink_to(VALDATA / name)
    (root / "manifest.json").write_text(json.dumps(man, indent=1))
    rep = {}
    for m in man:
        fid = m["fire_id"]
        p = root / f"domains/{fid}.npz"
        if p.exists() and not a.force:
            continue
        dom = FireDomain.load(VALDATA / f"domains/{fid}.npz")
        codes, g, st = backcast_codes(fid, a.grid)
        dst = np.zeros(dom.dob.shape, np.int32)
        reproject(codes.astype(np.int32), dst, src_transform=g.transform, src_crs=g.crs,
                  dst_transform=from_origin(dom.lng_min, dom.lat_max, dom.cell_lng, dom.cell_lat),
                  dst_crs="EPSG:4326", resampling=Resampling.mode, src_nodata=0, dst_nodata=0)
        meta = dict(dom.meta, fuel_source=f"back-cast test grid '{a.grid}' "
                                          "(scripts/validation/backcast_fire_test.py)",
                    fuel_resampling="mode from 20 m EPSG:3400", backcast=st)
        nd = FireDomain(fire_id=dom.fire_id, year=dom.year, dob=dom.dob, fuel=dst,
                        elevation=dom.elevation, lat_max=dom.lat_max, lng_min=dom.lng_min,
                        cell_lat=dom.cell_lat, cell_lng=dom.cell_lng, fuel_scheme="cfs_national",
                        meta=meta)
        holes = sum(int((crop_for_day(nd, d).fuel == 0).sum()) for d in m["days"])
        nd.save(p)
        rep[fid] = {**st, "cells_without_fuel_code_in_working_areas": holes}
        print(fid, rep[fid], flush=True)
    rp = root / "domains_report.json"
    old = json.loads(rp.read_text()) if rp.exists() else {}
    old.update(rep)
    rp.write_text(json.dumps(old, indent=1))


# ── runs (the [R27] set-up, in process, as the burn-scar check's rerun_lb.py) ───────────────
V = None
GRID = None


def _task(t: dict) -> dict:
    import logging

    from firesim.validation.harness import RunOptions, _fuel_mix, build_case, score_day, simulate
    logging.getLogger("firesim").setLevel(logging.WARNING)
    fid, day = t["fire_id"], t["day"]
    t0 = time.time()
    try:
        dom, groups, rec = V._load_fire(fid)
        case = build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
        opts = RunOptions(ignition="active", enable_spotting=False, windows_h=WINDOWS,
                          active_days=2, active_buffer_m=None, ffmc_spinup=True,
                          burning_period=(10.0, 20.0))
        arr = simulate(case, opts)["arrival"]
        sc = score_day(case, arr, opts)
        cd = case.domain
        excluded = ((cd.dob > 0) & (cd.dob < day)) | (arr <= 0.0)
        obs = (cd.dob == day) & ~excluded
        pred17 = (arr <= 17 * 60.0) & ~excluded
        out = {"fire_id": fid, "day": day, "year": dom.year, "grid": GRID,
               "harness_f1_17h": sc["17h"]["f1"], "harness_f1_8h": sc["8h"]["f1"],
               "harness_area_diff_17h": sc["17h"]["area_diff_norm"],
               "mix_obs": _fuel_mix(cd, obs, day, opts) if obs.any() else {},
               "mix_pred17": _fuel_mix(cd, pred17, day, opts) if pred17.any() else {}}
    except Exception as exc:  # recorded, not fatal
        out = {"fire_id": fid, "day": day, "grid": GRID, "error": repr(exc)}
    out["wall_s"] = time.time() - t0
    return out


def cmd_run(a) -> None:
    global V, GRID
    GRID = a.grid
    os.environ["FIRESIM_VALIDATION_DATA"] = str(OUT / "roots" / a.grid)
    spec = importlib.util.spec_from_file_location("validate_cli", REPO / "scripts/validate.py")
    V = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(V)
    man = json.loads((OUT / "roots" / a.grid / "manifest.json").read_text())
    tasks = sorted(({"fire_id": m["fire_id"], "day": d} for m in man for d in m["days"]),
                   key=lambda t: (t["fire_id"], t["day"]))
    (OUT / "runs").mkdir(parents=True, exist_ok=True)
    out = OUT / "runs" / f"{a.grid}.jsonl"
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["fire_id"], r["day"]))
    tasks = [t for t in tasks if (t["fire_id"], t["day"]) not in done]
    print(f"{a.grid}: {len(tasks)} fire-days", flush=True)
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=8) as pool, open(out, "a") as fh:
        for r in pool.imap_unordered(_task, tasks):
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            if "error" in r:
                print("ERR", r, flush=True)
    print(f"{a.grid} done in {(time.time() - t0) / 60:.1f} min", flush=True)


# ── analysis ─────────────────────────────────────────────────────────────────────────────────

def _read(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if "error" not in r:
            out[(r["fire_id"], r["day"])] = r
    return out


def cmd_analyse(a) -> None:
    from firesim.validation.report import split_fires

    odt = _odt()
    runs = {g: _read(OUT / "runs" / f"{g}.jsonl") for g in GRIDS if (OUT / "runs" / f"{g}.jsonl").exists()}
    for g in ("nat2014", "ab2025_masked", "ab2025_unmasked"):
        runs[g] = _read(COMPARATORS / f"{g}.jsonl")
    keys_all = sorted(set.intersection(*(set(r) for r in runs.values())))
    split = split_fires({k[0] for k in keys_all})
    sets = {"held_out": [k for k in keys_all if split[k[0]] == "test"], "all": keys_all}
    res = {"prereg": PREREG, "n": {s: len(v) for s, v in sets.items()},
           "fires": {s: len({k[0] for k in v}) for s, v in sets.items()}, "levels": {},
           "paired": {}}

    def val(r, w):
        v = r[f"harness_f1_{w}"] if w != "area" else r["harness_area_diff_17h"]
        return math.nan if v is None else float(v)

    for s, keys in sets.items():
        res["levels"][s] = {g: {w: float(np.nanmean([val(runs[g][k], w) for k in keys]))
                                for w in ("17h", "8h", "area")} for g in runs}
        res["paired"][s] = {}
        pairs = [("ab2025_backcast", "ab2025_masked"), ("ab2025_backcast", "nat2014"),
                 ("ab2025_backcast", "ab2025_unmasked"), ("ab2025_backcast14", "ab2025_backcast"),
                 ("ab2025_backcast14", "nat2014"), ("ab2025_backcast14", "ab2025_masked"),
                 ("ab2025_masked", "nat2014")]
        for ga, gb in pairs:
            if ga not in runs or gb not in runs:
                continue
            row = {}
            for w in ("17h", "8h", "area"):
                d = {k: val(runs[ga][k], w) - val(runs[gb][k], w) for k in keys}
                ci = odt.boot(d)
                row[w] = {"mean": ci[0], "lo": ci[1], "hi": ci[2], "verdict": odt.verdict(ci)}
            res["paired"][s][f"{ga} - {gb}"] = row
    # fuel groups of the observed growth (area-weighted by cells) per grid, held-out and all
    res["mix_obs"] = {}
    for s, keys in sets.items():
        res["mix_obs"][s] = {}
        for g in GRIDS:
            if g not in runs:
                continue
            tot = defaultdict(float)
            for k in keys:
                for ft, sh in (runs[g][k].get("mix_obs") or {}).items():
                    tot[odt.fuel_group(ft)] += sh
            n = sum(tot.values()) or 1.0
            res["mix_obs"][s][g] = {k: v / n for k, v in sorted(tot.items())}
    res["per_fireday"] = [{"fire_id": k[0], "day": k[1], "set": split[k[0]],
                           **{g: runs[g][k]["harness_f1_17h"] for g in runs}} for k in keys_all]
    dest = Path(a.out)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results_backcast_rescoring.json").write_text(json.dumps(res, indent=1, default=float))
    for s in sets:
        print(s, res["n"][s], json.dumps(res["paired"][s], default=float)[:2000])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prereg")
    d = sub.add_parser("domains")
    d.add_argument("grid", choices=GRIDS)
    d.add_argument("--force", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("grid", choices=GRIDS)
    r.add_argument("--workers", type=int, default=3)
    an = sub.add_parser("analyse")
    an.add_argument("--out", required=True)
    a = ap.parse_args()
    {"prereg": cmd_prereg, "domains": cmd_domains, "run": cmd_run, "analyse": cmd_analyse}[a.cmd](a)


if __name__ == "__main__":
    main()
