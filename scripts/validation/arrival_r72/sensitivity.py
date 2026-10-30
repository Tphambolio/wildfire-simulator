#!/usr/bin/env python3
"""[R72] stage 1: paired one-factor sensitivity runs of FireSim on the C1b fire-days.

Purpose
    Test, one factor at a time, how much of the C1b community-arrival error each candidate cause
    explains: the 24 h run of each C1b fire-day (14 primary + 6 Horse River calibration) is
    repeated with one input or option changed, everything else as the operational C1b run
    (``common.opts_operational``). Each variant's arrival grid is scored with the registered C1b
    metric against the same start mask as the baseline (``common.score_with_start``), and with the
    harness's 17 h F1 (``harness.score_day``) for the F1 guard. Exploratory, not registered; the
    C1b days are already scored, so this diagnoses and does not tune or claim.

Variants (name: what changes)
    base            none (reproduces the stored C1b FireSim grid; checked cell by cell)
    bp_off          no burning period (spread 24 h; the hourly FFMC still gives a diurnal ROS)
    bp_10_24        burning period 10-24 h local (evening extended to midnight)
    bp_06_24        burning period 06-24 h local (morning and evening extended)
    perimeter       whole start perimeter active (no active-edge rule)
    active3         active edges = growth of the last 3 days (operational: 2)
    ros1.5..ros4    every period's ROS x 1.5 / 2 / 3 / 4 (ROS-bias ladder)
    ws1.5           ERA5 wind speed x 1.5 every hour
    wdir_toward:<c> constant wind direction blowing from the start area toward community <c>
                    (oracle direction; speeds unchanged), one run per reached community
    cured_leafless  grass curing 90 % and leafless D-1/M-1 on every day (no green-up)
    fuel2014b       CFS 2014b national FBP grid on the same lattice instead of the back-cast grid
    no_spinup       FFMC starts at the daily (afternoon) value at 06:00 (no evening spin-up)
    spotting        FireSim's opt-in ember spotting (Albini maximum distance, heuristic landing)
    bp_off+perimeter, bp_off+ros1.5   two combinations (interaction check)
    station_wind / station_speed / station_dir  the nearest open station's hourly 10 m wind
                    (weather_obs.py series; speed and direction, speed only, direction only)
                    replaces ERA5 for hours 0-23; missing station hours keep ERA5

Outputs
    OUT/sensitivity_rows.jsonl  one line per (fire-day, variant): community rows, 17 h F1, run s
    BIG/arrivals/<fire>_<day>_<variant>.npz  arrival minutes (float32)
Usage
    CAPJOB_MEM=5G capjob python3 scripts/validation/arrival_r72/sensitivity.py [--workers 3]
        [--variants a,b,...] [--days fire:day,...]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as K  # noqa: E402

VARIANTS = ["base", "bp_off", "bp_10_24", "bp_06_24", "perimeter", "active3", "ros1.5", "ros2",
            "ros3", "ros4", "ws1.5", "wdir_toward", "cured_leafless", "fuel2014b", "no_spinup",
            "spotting", "bp_off+perimeter", "bp_off+ros1.5", "station_wind", "station_speed",
            "station_dir"]


def f1_17(case, arrival, opts):
    from firesim.validation.harness import score_day

    s = score_day(case, arrival, opts)
    f = s["17h"]["f1"]
    return 0.0 if (f is None or (isinstance(f, float) and math.isnan(f))) else float(f)


def domain_2014b(fid: str):
    from firesim.validation.cfsds import FireDomain

    p = K.VD / "c1b/domains2014b" / f"{fid}.npz"
    if not p.exists():
        p = K.VD / "domains" / f"{fid}.npz"  # the record's CFS 2014b domain (Horse River)
    d = FireDomain.load(p)
    return d if d.dob.shape == K.load_fire(fid)[0].dob.shape else None


def station_hourly(fid, day, case, variant):
    """The case's hourly stream with the nearest station's wind (None if no usable station)."""
    w = json.loads((K.OUT / "weather_obs.json").read_text())[f"{fid}:{day}"]
    sid = w["series_station"]
    if sid is None:
        return None
    st = next(s for s in w["stations"] if s["sid"] == sid)
    by_h = {o["h"]: o for o in st["hourly"]}
    out = []
    for h in case.hourly:
        o = by_h.get(int(round(h.hours_from_start)), {})
        ws, wd = h.wind_speed, h.wind_direction
        if variant in ("station_wind", "station_speed") and o.get("ws") is not None:
            ws = float(o["ws"])
        if variant in ("station_wind", "station_dir") and o.get("wd") is not None and (o.get("ws") or 0) > 0:
            wd = float(o["wd"])
        out.append(replace(h, wind_speed=ws, wind_direction=wd))
    return tuple(out)


def runs_for(fid: str, day: int, variant: str, case, base_start):
    """Yield (variant label, arrival, opts) for one requested variant."""
    from firesim.validation.harness import Member, crop_for_day

    op = K.opts_operational
    if variant == "base":
        yield variant, *K.run(case, op())[:1], op()
    elif variant == "bp_off":
        yield variant, K.run(case, op(burning_period=None))[0], op(burning_period=None)
    elif variant == "bp_10_24":
        yield variant, K.run(case, op(burning_period=(10.0, 24.0)))[0], op()
    elif variant == "bp_06_24":
        yield variant, K.run(case, op(burning_period=(6.0, 24.0)))[0], op()
    elif variant == "perimeter":
        yield variant, K.run(case, op(ignition="perimeter"))[0], op()
    elif variant == "active3":
        yield variant, K.run(case, op(active_days=3))[0], op()
    elif variant.startswith("ros"):
        yield variant, K.run(case, op(), ros_multiplier=float(variant[3:]))[0], op()
    elif variant == "ws1.5":
        yield variant, K.run(case, op(), member=Member(name="ws1.5", wind_speed_factor=1.5))[0], op()
    elif variant == "wdir_toward":
        import communities_c1b as CB
        from scipy import ndimage

        reached, _ = CB.observed_intervals(case)
        dom = case.domain
        for c in reached:
            m = c["mask"]
            if (m & base_start).any():
                continue
            Dz = ndimage.distance_transform_edt(~m, sampling=(dom.dy, dom.dx))
            p = np.unravel_index(int(np.argmin(np.where(base_start, Dz, np.inf))), Dz.shape)
            zr, zc = np.nonzero(m)
            k = int(np.argmin(np.hypot((zr - p[0]) * dom.dy, (zc - p[1]) * dom.dx)))
            brg = K.bearing_deg((zc[k] - p[1]) * dom.dx, (p[0] - zr[k]) * dom.dy)
            frm = (brg + 180.0) % 360.0
            mem = Member(name=f"wdir{frm:.0f}", constant_wind_direction=frm)
            yield f"wdir_toward:{c['name']}", K.run(case, op(), member=mem)[0], op()
    elif variant == "cured_leafless":
        o = op(greenup_doy=999, grass_cure_green=90.0)
        yield variant, K.run(case, o)[0], op()
    elif variant == "fuel2014b":
        d14 = domain_2014b(fid)
        if d14 is None:
            return
        c2 = replace(case, domain=crop_for_day(d14, day, 20000.0))
        assert c2.domain.dob.shape == case.domain.dob.shape
        yield variant, K.run(c2, op())[0], op()
    elif variant == "no_spinup":
        yield variant, K.run(case, op(ffmc_spinup=False))[0], op()
    elif variant == "spotting":
        yield variant, K.run(case, op(enable_spotting=True))[0], op()
    elif variant.startswith("station_"):
        hourly = station_hourly(fid, day, case, variant)
        if hourly is None:
            return
        yield variant, K.run(case, op(), hourly=hourly)[0], op()
    elif variant == "bp_off+perimeter":
        yield variant, K.run(case, op(burning_period=None, ignition="perimeter"))[0], op()
    elif variant == "bp_off+ros1.5":
        yield variant, K.run(case, op(burning_period=None), ros_multiplier=1.5)[0], op()
    else:
        raise ValueError(variant)


def task(args):
    fid, day, pop, variants = args
    import logging

    logging.getLogger("firesim").setLevel(logging.WARNING)
    case = K.load_case(fid, day)
    stored = K.stored_baseline(fid, day)
    base_start = stored <= 0
    out = []
    adir = K.BIG / "arrivals"
    adir.mkdir(parents=True, exist_ok=True)
    for v in variants:
        try:
            for label, arr, opts in runs_for(fid, day, v, case, base_start):
                res = K.score_with_start(case, arr, base_start)
                row = {"fire_day": f"{fid}:{day}", "population": pop, "variant": label,
                       "family": v, "mae_h": res["mae_h"], "hits": res["hits"],
                       "false_arrivals": res["false_arrivals"], "n": res["n"],
                       "rows": [{k: (round(x, 4) if isinstance(x, float) else x) for k, x in r.items()}
                                for r in res["rows"]],
                       "f1_17h": f1_17(case, arr, opts),
                       "burned_ha_24h": float((arr <= 1440).sum() * case.domain.cell_area_m2 / 1e4)}
                if label == "base":
                    row["identical_to_stored"] = bool(np.array_equal(np.isinf(arr), np.isinf(stored)) and
                                                      np.allclose(arr[np.isfinite(arr)], stored[np.isfinite(stored)], atol=1e-3))
                safe = label.replace(":", "_").replace(" ", "_").replace("/", "_")[:60]
                np.savez_compressed(adir / f"{fid}_{day}_{safe}.npz", arrival=arr.astype(np.float32))
                out.append(row)
        except Exception as exc:  # noqa: BLE001 (recorded)
            out.append({"fire_day": f"{fid}:{day}", "population": pop, "variant": v, "family": v,
                        "error": repr(exc)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--days", default="")
    ap.add_argument("--out", default=str(K.OUT / "sensitivity_rows.jsonl"))
    a = ap.parse_args()
    variants = [v for v in a.variants.split(",") if v]
    days = K.all_days()
    if a.days:
        want = set(a.days.split(","))
        days = [d for d in days if f"{d[0]}:{d[1]}" in want]
    out = Path(a.out)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["fire_day"], r["family"]))
    tasks = []
    for fid, day, pop in days:
        todo = [v for v in variants if (f"{fid}:{day}", v) not in done]
        if todo:
            tasks.append((fid, day, pop, todo))
    with Pool(a.workers, maxtasksperchild=1) as pool:
        for rows in pool.imap_unordered(task, tasks):
            with open(out, "a") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=float) + "\n")
            print(rows[0]["fire_day"] if rows else "(no run: variant not applicable)", [(r["variant"][:22], round(r.get("mae_h", float("nan")), 2)) for r in rows], flush=True)


if __name__ == "__main__":
    main()
