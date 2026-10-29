#!/usr/bin/env python3
"""C1 runner: FireSim and W.I.S.E. (black box) on the same fire-days, scored by the same code.

PREREG_COMPARATOR_C1. Per fire-day: FireSim's operational set-up (active edges 2 d, FFMC
spin-up, burning period 10-20 h; ``harness.simulate``) and a WISE job with the same inputs
(``wise_job.build_job``); both arrival grids are scored by ``harness.score_day`` (F1 etc.) and
``communities.arrival_errors``. With ``--ensemble K --seeds ...`` it also runs K members per seed
for both models with identical perturbations (``ensemble.perturb_config``, the shared-input
sizes of the pre-registration) and scores them with ``ensemble_scores.score_ensemble_day``.

    export FIRESIM_VALIDATION_DOMAINS=$FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains
    PYTHONPATH=engine/src python3 scripts/validation/c1/run_c1.py --days days.txt --out runs.jsonl

One JSON line per fire-day (and per ensemble seed); arrival grids are kept in
``<out dir>/arrivals/`` (npz, minutes) so any score can be recomputed. Fire-days already in
``--out`` are skipped (restartable). A WISE failure is recorded, never retried silently.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # scripts/ (validate.py)
sys.path.insert(0, str(HERE.parents[2] / "engine/src"))
sys.path.insert(0, str(HERE))

import communities as COM  # noqa: E402
import wise_job as WJ  # noqa: E402

WINDOWS = (4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 17.0, 24.0)
# Shared-input ensemble sizes (PREREG §6.3): the record's A15 sizes for the inputs both models
# take; ROS multiplier and foliar moisture not perturbed (WISE has no documented input for them)
SHARED_SIZES = dict(wind_dir_sd_deg=24.0, wind_speed_log_sd=0.405, ffmc_sd=7.2,
                    dmc_dc_log_sd=0.6, curing_sd=20.25, fmc_sd=0.0, ros_log_sd=0.0)
MIN_FREE_GB = 8.0
JOB_KEEP = ("job.fgmj", "status.json", "statistics.txt")  # kept from a scored job folder


def free_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 ** 2
    return math.nan


GATE = {"free_gb": MIN_FREE_GB, "exclusive": False, "stop_below_gb": None, "disk_free_gb": None,
        "disk_paths": ()}
HEAVY = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/heavyjobs.slice")
OTHER_JOB_BYTES = 1.5e9  # a heavyjobs.slice scope above this (not ours) counts as a heavy job


def other_heavy_jobs() -> list[str]:
    """Heavy jobs other than this process: heavyjobs.slice scopes using > 1.5 GB that do not
    contain this process, and docker containers other than ours and the MQTT broker."""
    me = Path(f"/proc/{os.getpid()}/cgroup").read_text().strip().split("/")[-1]
    out = []
    for sc in HEAVY.glob("*.scope") if HEAVY.exists() else []:
        try:
            if sc.name != me and int((sc / "memory.current").read_text()) > OTHER_JOB_BYTES:
                out.append(sc.name)
        except OSError:
            pass
    for pat in GATE.get("pgrep", ()):  # named jobs that must not be running (owner rule)
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        pids = [p for p in r.stdout.split() if p != str(os.getpid())]
        if pids:
            out.append(f"pgrep {pat!r}: {len(pids)}")
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
    out += [n for n in r.stdout.split() if n and not n.startswith("c1wise_") and n != "flighthub-emqx"]
    return out


def disk_low() -> list[str]:
    """Paths (jobs dir, --out dir) whose filesystem has less free disk than the disk gate."""
    lim = GATE["disk_free_gb"]
    return [] if lim is None else [f"{p}: {WJ.disk_free_gb(p):.1f} GB" for p in GATE["disk_paths"]
                                   if WJ.disk_free_gb(p) < lim]


def wait_for_disk(log: logging.Logger) -> None:
    while low := disk_low():
        log.info("paused: free disk below %g GB (%s)", GATE["disk_free_gb"], ", ".join(low))
        time.sleep(120)


def wait_for_memory(log: logging.Logger) -> None:
    while True:
        wait_for_disk(log)
        busy = other_heavy_jobs() if GATE["exclusive"] else []
        if free_gb() >= GATE["free_gb"] and not busy:
            return
        log.info("waiting: %.1f GB available (need %.0f); other heavy jobs: %s", free_gb(),
                 GATE["free_gb"], busy)
        time.sleep(120)


def opts_operational():
    from firesim.validation.harness import RunOptions

    return RunOptions(ignition="active", active_days=2, ffmc_spinup=True,
                      burning_period=(10.0, 20.0), windows_h=WINDOWS, oracle_max_h=17)


def start_masks(setup: dict, arrival: np.ndarray, dom, opts):
    """FireSim's start area, the cells it starts burning and those it burns out."""
    from firesim.spread.cellular import active_mask

    start = arrival <= 0.0
    if setup["active"] is None:
        return start, start.copy(), np.zeros_like(start)
    buf = max(dom.dx, dom.dy) if opts.active_buffer_m is None else opts.active_buffer_m
    act = active_mask(setup["active"], buf, dom.rows, dom.cols, dom.lat_max, dom.lng_min,
                      dom.cell_lat, dom.cell_lng, dom.dx, dom.dy)
    burning = start & act
    return start, burning, start & ~burning


def compact(scores: dict) -> dict:
    keep = ("tp", "fp", "fn", "area_pred", "area_obs", "precision", "recall", "f1", "iou",
            "area_diff_norm", "hausdorff_m", "spread_ratio", "bearing_err_deg", "growth_ratio")
    out = {}
    for k, v in scores.items():
        if isinstance(v, dict):
            out[k] = {kk: vv for kk, vv in v.items() if kk in keep or k == "oracle"}
        else:
            out[k] = v
    return out


def comm_summary(res: dict) -> dict:
    return {k: v for k, v in res.items() if k != "rows"} | {
        "rows": [{kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in r.items()}
                 for r in res["rows"]]}


def run_day(fid: str, day: int, args, log) -> list[dict]:
    from validate import _load_fire, domains_dir

    from firesim.validation.harness import Member, _setup, build_case, score_day, simulate

    opts = opts_operational()
    dom, groups, rec = _load_fire(fid)
    case = build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
    cd = case.domain
    setup = _setup(case, opts, Member())
    fs = simulate(case, opts, Member())
    start, burning, burned_out = start_masks(setup, fs["arrival"], cd, opts)
    jobs = Path(args.jobs_dir)
    adir = Path(args.out).parent / "arrivals"
    adir.mkdir(parents=True, exist_ok=True)
    key = f"{fid}_{day}"
    comms = COM.observed_arrivals(case, 17.0)
    base = {"fire_id": fid, "day": day, "date": case.start.date().isoformat(),
            "domains_dir": str(domains_dir()), "fuel_source": dom.meta.get("fuel_source", ""),
            "grid": [cd.rows, cd.cols], "res_m": round(min(cd.dx, cd.dy), 1),
            "n_start": int(start.sum()), "n_burning": int(burning.sum()),
            "n_burned_out": int(burned_out.sum())}
    out = []
    if not args.ensemble_only:
        wait_for_memory(log)
        d = jobs / key
        hff = WJ.hourly_ffmc_of(case, setup["config"], setup["fuel_grid"])
        meta = WJ.build_job(d, case, opts, setup, burning, burned_out, step_min=args.step_min,
                            cores=args.cores, hourly_ffmc=hff)
        meta["hourly_ffmc"] = {f"{k:%H}": round(v, 2) for k, v in sorted(hff.items())}
        wr = WJ.run_wise(d, timeout_s=args.timeout, stop_below_gb=GATE["stop_below_gb"],
                         disk_pause_gb=GATE["disk_free_gb"])
        wr["mem_cap"] = WJ.MEM
        row = dict(base, kind="det", firesim_run_s=fs["run_s"], wise=wr, wise_meta=meta)
        fs_scores = score_day(case, fs["arrival"], opts)
        row["obs_growth_ha"] = fs_scores["obs_growth_ha"]
        row["firesim"] = compact({k: v for k, v in fs_scores.items()
                                  if k in [f"{w:g}h" for w in WINDOWS] + ["oracle", "edge_hit"]})
        row["comm_obs"] = [{"name": c["name"], "reached": c["reached"],
                            "obs_min": c["obs_min"]} for c in comms]
        row["firesim_comm"] = comm_summary(COM.arrival_errors(comms, fs["arrival"], start))
        arrs = {"firesim": fs["arrival"].astype(np.float32)}
        if wr["ok"]:
            wa = WJ.arrival_from_perims(d, case, start, meta)
            ws = score_day(case, wa, opts)
            row["wise_scores"] = compact({k: v for k, v in ws.items()
                                          if k in [f"{w:g}h" for w in WINDOWS] + ["oracle",
                                                                                   "edge_hit"]})
            row["wise_comm"] = comm_summary(COM.arrival_errors(comms, wa, start))
            arrs["wise"] = wa.astype(np.float32)
            stats = d / "Outputs/stats.csv"
            if stats.exists():
                shutil.copy(stats, adir / f"{key}_wise_stats.csv")
        wait_for_disk(log)
        np.savez_compressed(adir / f"{key}_det.npz", **arrs)
        out.append(row)
        if not args.keep_jobs:  # scored: keep only the WISE input spec and run status
            for x in d.iterdir():
                if x.name not in JOB_KEEP:
                    shutil.rmtree(x, ignore_errors=True) if x.is_dir() else x.unlink()
        f_fs = row["firesim"]["17h"]["f1"]
        f_w = row.get("wise_scores", {}).get("17h", {}).get("f1")
        log.info("%s day %d det: FireSim F1 %.3f  WISE F1 %s  (WISE %.0f s, %.0f MB)", fid, day,
                 f_fs if f_fs == f_fs else float("nan"), f_w, wr["run_s"], wr["peak_mb"])
    if args.ensemble:
        out += run_ensemble(case, opts, setup, fs, start, burning, burned_out, comms, base,
                            key, args, log)
    return out


def run_ensemble(case, opts, setup, fs, start, burning, burned_out, comms, base, key, args, log):
    from firesim.spread.ensemble import EnsembleConfig, perturb_config
    from firesim.spread.simulator import Simulator
    from firesim.validation.ensemble_scores import score_ensemble_day
    from firesim.validation.harness import _run

    cd = case.domain
    out = []
    base_fmc = Simulator(setup["config"], fuel_grid=setup["fuel_grid"])._foliar_moisture()
    for seed in args.seeds:
        ens = EnsembleConfig(n_members=args.ensemble, seed=seed, **SHARED_SIZES)
        rng = random.Random(ens.seed)
        fs_stack = np.full((ens.n_members, cd.rows, cd.cols), np.inf, dtype=np.float32)
        w_stack = np.full((ens.n_members, cd.rows, cd.cols), np.inf, dtype=np.float32)
        recs, wise_runs, fs_s = [], [], 0.0
        w_ok = True
        for i in range(ens.n_members):
            cfg, k_ros, r = perturb_config(setup["config"], ens, rng, base_fmc)
            r = dict(r, wind_dir_offset_deg=float((cfg.weather.wind_direction
                                                   - setup["config"].weather.wind_direction)
                                                  % 360.0))
            # exact (unrounded) member values for WISE
            hw0 = setup["config"].hourly_weather
            pos = [j for j, h in enumerate(hw0 or ()) if h.wind_speed > 0]
            k_ws = (cfg.hourly_weather[pos[0]].wind_speed / hw0[pos[0]].wind_speed
                    if pos else r["wind_speed_factor"])
            member = {"wind_dir_offset_deg": r["wind_dir_offset_deg"], "wind_speed_factor": k_ws,
                      "ffmc": cfg.ffmc, "dmc": cfg.dmc, "dc": cfg.dc, "grass_cure": cfg.grass_cure}
            recs.append(r)
            a, s, _ = _run(setup, cfg, opts, k_ros, seed=f"{case.fire_id}:{case.day}:ens{seed}:{i}")
            fs_stack[i] = a
            fs_s += s
            if not w_ok:  # a failed member already makes this day-seed a WISE failure
                wise_runs.append({"ok": False, "rc": None, "run_s": 0.0, "peak_mb": 0.0,
                                  "err": "skipped: an earlier member failed"})
                continue
            wait_for_memory(log)
            d = Path(args.jobs_dir) / f"{key}_s{seed}_m{i:02d}"
            hff = WJ.hourly_ffmc_of(case, cfg, setup["fuel_grid"])
            meta = WJ.build_job(d, case, opts, setup, burning, burned_out, member=member,
                                step_min=args.step_min, cores=args.cores, hourly_ffmc=hff)
            wr = WJ.run_wise(d, timeout_s=args.timeout, stop_below_gb=GATE["stop_below_gb"],
                         disk_pause_gb=GATE["disk_free_gb"])
            wise_runs.append({k: wr[k] for k in ("ok", "rc", "run_s", "peak_mb", "err")})
            if wr["ok"]:
                w_stack[i] = WJ.arrival_from_perims(d, case, start, meta)
            else:
                w_ok = False
            shutil.rmtree(d, ignore_errors=True)
        win = 17.0
        row = dict(base, kind="ens", seed=seed, n_members=ens.n_members, sizes=SHARED_SIZES,
                   members=recs, firesim_run_s=fs_s, wise_runs=wise_runs, wise_all_ok=w_ok)
        row["firesim_ens"] = score_ensemble_day(cd.dob, case.day, fs_stack, fs["arrival"],
                                                cd.cell_area_m2, win)
        if w_ok:
            det_w = np.load(Path(args.out).parent / "arrivals" / f"{key}_det.npz")
            wdet = det_w["wise"] if "wise" in det_w else w_stack[0]
            row["wise_ens"] = score_ensemble_day(cd.dob, case.day, w_stack, wdet,
                                                 cd.cell_area_m2, win)
        np.savez_compressed(Path(args.out).parent / "arrivals" / f"{key}_ens_s{seed}.npz",
                            firesim=fs_stack, wise=w_stack)
        out.append(row)
        log.info("%s day %d ens seed %d: %d members, WISE ok=%s", case.fire_id, case.day, seed,
                 ens.n_members, w_ok)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", required=True, help="file of FIRE_ID:DAY lines (or comma list)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs-dir", default=str(Path.home() / "dev/wildfire/wise-reference/c1_jobs"))
    ap.add_argument("--step-min", type=int, default=30)
    ap.add_argument("--cores", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=10800)
    ap.add_argument("--ensemble", type=int, default=0, help="members per seed (0 = none)")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--ensemble-only", action="store_true")
    ap.add_argument("--keep-jobs", action="store_true")
    ap.add_argument("--mem", default=None, help="WISE container memory cap (default 5g)")
    ap.add_argument("--gate-free-gb", type=float, default=MIN_FREE_GB,
                    help="start a WISE run only with this much memory available")
    ap.add_argument("--gate-exclusive", action="store_true",
                    help="also wait until no other heavy job runs (other_heavy_jobs)")
    ap.add_argument("--gate-pgrep", nargs="*", default=[],
                    help="with --gate-exclusive: also wait while `pgrep -f PATTERN` finds a process")
    ap.add_argument("--stop-below-gb", type=float, default=None,
                    help="kill the run and stop the batch if available memory falls below this")
    ap.add_argument("--disk-pause-gb", type=float, default=None,
                    help="pause (no new WISE job; docker pause a running one) while free disk on the "
                         "jobs/--out filesystems is below this")
    a = ap.parse_args()
    if a.mem:
        WJ.MEM = a.mem
    GATE.update(free_gb=a.gate_free_gb, exclusive=a.gate_exclusive, stop_below_gb=a.stop_below_gb,
                pgrep=tuple(a.gate_pgrep), disk_free_gb=a.disk_pause_gb)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("firesim").setLevel(logging.WARNING)
    log = logging.getLogger("c1")
    p = Path(a.days)
    items = [x.strip() for x in (p.read_text().split() if p.exists() else a.days.split(","))
             if x.strip() and not x.startswith("#")]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(a.jobs_dir).mkdir(parents=True, exist_ok=True)
    GATE["disk_paths"] = (Path(a.jobs_dir), out.parent)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done.add((r["fire_id"], r["day"], r["kind"], r.get("seed")))
    for it in items:
        fid, day = it.split(":")
        day = int(day)
        want_det = not a.ensemble_only and (fid, day, "det", None) not in done
        want_ens = a.ensemble and any((fid, day, "ens", s) not in done for s in a.seeds)
        if not want_det and not want_ens:
            continue
        sub = argparse.Namespace(**vars(a))
        sub.ensemble_only = not want_det
        sub.ensemble = a.ensemble if want_ens else 0
        sub.seeds = [s for s in a.seeds if (fid, day, "ens", s) not in done]
        t0 = time.time()
        try:
            rows = run_day(fid, day, sub, log)
        except WJ.LowMemoryStop as exc:
            log.error("stopping the batch: %s", exc)
            break
        except Exception as exc:  # noqa: BLE001 (recorded, the batch continues)
            log.exception("%s:%d failed", fid, day)
            rows = [{"fire_id": fid, "day": day, "kind": "error", "error": repr(exc)}]
        wait_for_disk(log)
        with open(out, "a") as fh:
            for r in rows:
                r["wall_s"] = time.time() - t0
                r["git"] = subprocess.run(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"],
                                          capture_output=True, text=True).stdout.strip()
                fh.write(json.dumps(r, default=float) + "\n")


if __name__ == "__main__":
    main()
