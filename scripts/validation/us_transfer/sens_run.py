"""Paired one-factor-at-a-time sensitivity runs for the US transfer diagnosis ([R71]).

Purpose: run each development fire-day (``build_dev.py``) under the R67 set-up (``base``) and under variants
that change exactly one input or rule, so the change in modelled 17 h growth can be attributed to that factor.
Every variant uses the same fire-days, the same start, the same scoring code as the harness
(``firesim.validation.harness.simulate`` / ``score_day``); no engine code is changed.

Base set-up (R67 det_C1 / H1 arm B without the barrier): active-edge ignition, last 2 burn days active,
hourly FFMC spin-up from 17:00 the day before, burning period 10-20 h local, start 06:00 local, 20 km
margin, deterministic member, ERA5 hourly at the grid centre, daily codes from ERA5 noon, O-1 curing
60 % (day 150-258) / 90 % (harness defaults), LANDFIRE -> FBP crosswalk v7.

Variants (``VARIANTS``):
  (a) fuels   ``xw_<name>``: one FBFM40 group re-crosswalked (``common.CROSSWALKS``; US only);
              ``cure95``: O-1 curing 95 % all season; ``cure_ndvi``: the fire's NDVI curing (median, v8).
  (b) weather ``stn``: station hourly T/RH/wind and station daily codes; ``stn_wind``: station wind only;
              ``stn_trh``: station temperature and RH only; ``stn_fwi``: station daily codes only;
              ``nospin``: no hourly FFMC spin-up (start-of-day FFMC held); ``wind075``: every hour's
              wind speed x 0.75 (elasticity, not a correction).
  (c) damping ``rm050`` / ``rm030`` / ``rm020``: every cell's ROS x 0.5 / 0.3 / 0.2 (the harness's ensemble
              ``ros_multiplier`` hook), the dose-response used to size a suppression-persistence damping rule.
              ``shd1_rmNNN``: shrub -> D-1/D-2 crosswalk together with ROS x NNN/100 (combined candidate fix);
              ``rm015``: ROS x 0.15.
  fix         ``fix1``: the PREREG_US_TRANSFER1 candidate (shrub -> D-1/D-2, and ROS x 0.20 on held-fire days =
              mature start with < 5 % of the start area burned the day before; US only); ``ens_base`` / ``ens_fix1``: 20-member A15 ensembles (B2/R67 perturbations) of base / fix1,
              recording each member's 17 h growth area (coverage check).
  (d) start   ``act1``: last 1 burn day active; ``perim``: whole start perimeter active (no edge rule).
  (e) scoring ``feds``: DOB from the NASA FEDS archive (the R67 day-of-burning source; US 2018-2021).
The base record also carries scoring-geometry extras: growth inside the eventual final footprint, growth on
cells that burned on a later day, and scores against detected-only observed growth (``dob_det``).

Output: ``<runs-dir>/<variant>.jsonl``, one JSON line per fire-day (resumable: done fire-days are skipped).
A variant whose inputs equal the base for a fire-day (no cell of the changed fuel group, no grass for a curing
variant, no station, not a FEDS year) is recorded with ``same_as_base: true`` and not re-run.

Usage (repository root): ``PYTHONPATH=engine/src python3 -m scripts.validation.us_transfer.sens_run
--variants base act1 ... --workers 3 [--region US] [--max-base-s 120]``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import timedelta
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from . import common as C

WINDOWS = (8.0, 17.0, 24.0)
XW = [k for k in C.CROSSWALKS if k != "base"]
VARIANTS = (["base"] + [f"xw_{k}" for k in XW] +
            ["cure95", "cure_ndvi", "stn", "stn_wind", "stn_trh", "stn_fwi", "nospin", "wind075",
             "act1", "perim", "feds", "rm050", "rm030", "rm020", "rm010",
             "shd1_rm100", "shd1_rm050", "shd1_rm030", "shd1_rm020", "shd1_rm015", "rm015",
             "fix1", "ens_base", "ens_fix1"])

# PREREG_US_TRANSFER1 candidate fix (frozen there): US (LANDFIRE) fire-days only
FIX1_SHRUB_CODE = C.D1          # FBFM40 SH 141-149 -> D-1 (D-2 green between day 150 and 258)
FIX1_K_HELD = 0.20              # ROS multiplier on held-fire days (below)
FIX1_HELD_SHARE = 0.05          # held fire: mature start (age >= 2 days, [R58]) and < 5 % of the start area
                                # burned on the previous day (start_prev_day_share; known at forecast time)
A15 = {"wind_dir_sd_deg": 24, "wind_speed_log_sd": 0.405, "ffmc_sd": 7.2, "dmc_dc_log_sd": 0.6,
       "curing_sd": 20.25, "fmc_sd": 15, "ros_log_sd": 0.825}


def fix1(dom, side, day):
    """(domain, ros multiplier) under FIX1; non-US domains (no FBFM40 sidecar) are returned unchanged."""
    from dataclasses import replace as dreplace
    from firesim.validation.start_strata import start_type

    if "fbfm" not in side:
        return dom, 1.0
    fuel = dom.fuel.copy()
    fuel[np.isin(side["fbfm"], C.GROUPS["SH"])] = FIX1_SHRUB_CODE
    st = start_type(dom.dob, day, dom.cell_area_m2)
    held = st["start_type"] == "mature" and (st.get("start_prev_day_share") or 0.0) < FIX1_HELD_SHARE
    k = FIX1_K_HELD if held else 1.0
    return dreplace(dom, fuel=fuel), k


@lru_cache(maxsize=2)
def load_fire(fid: str):
    from firesim.validation.cfsds import FireDomain
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly, parse_open_meteo

    man = {m["fire_id"]: m for m in json.loads((C.DATA / "manifest.json").read_text())}[fid]
    dom = FireDomain.load(C.DATA / "domains" / f"{fid}.npz")
    side = dict(np.load(C.DATA / "sidecar" / f"{fid}.npz"))
    lat, lng = man["weather_point"]
    d0, d1 = man["weather_range"]
    era5 = fetch_era5_hourly(lat, lng, doy_to_date(dom.year, d0) - timedelta(days=1),
                             doy_to_date(dom.year, d1) + timedelta(days=2), C.DATA / "weather",
                             timezone=man["timezone"])
    sp = C.DATA / "station" / f"{fid}.json"
    stn = parse_open_meteo(json.loads(sp.read_text())) if sp.exists() else None
    groups = {}
    for kind in ("era5", "stn"):
        gp = C.DATA / "groups" / f"{fid}_{kind}.json"
        if gp.exists():
            groups[kind] = {int(r["DOB"]): r for r in json.loads(gp.read_text())}
    return man, dom, side, era5, stn, groups


def swap(era5, stn, fields: tuple[str, ...]):
    """ERA5 hour records with ``fields`` taken from the station record of the same local hour."""
    from dataclasses import replace

    by = {r.time: r for r in stn}
    return [replace(r, **{f: getattr(by[r.time], f) for f in fields}) if r.time in by else r for r in era5]


def crop_bounds(dob: np.ndarray, day: int, dom, margin_m: float = 20000.0):
    burned = (dob > 0) & (dob <= day)
    rr, cc = np.nonzero(burned)
    mr, mc = int(math.ceil(margin_m / dom.dy)), int(math.ceil(margin_m / dom.dx))
    return (max(rr.min() - mr, 0), min(rr.max() + mr + 1, dom.rows),
            max(cc.min() - mc, 0), min(cc.max() + mc + 1, dom.cols))


def area_scores(pred: np.ndarray, obs: np.ndarray, cell: float) -> dict:
    tp = float((pred & obs).sum()) * cell / 1e4
    ap, ao = float(pred.sum()) * cell / 1e4, float(obs.sum()) * cell / 1e4
    return {"pred_ha": ap, "obs_ha": ao, "f1": 2 * tp / (ap + ao) if ap + ao > 0 else math.nan,
            "tp_ha": tp}


def run_task(task: dict) -> dict:
    import logging
    from dataclasses import replace as dreplace

    from firesim.validation.cfsds import fire_day_pairs
    from firesim.validation.harness import Member, RunOptions, build_case, score_day, simulate

    logging.getLogger("firesim").setLevel(logging.WARNING)
    fid, day, v = task["fire_id"], task["day"], task["variant"]
    t = time.time()
    out = {"fire_id": fid, "day": day, "variant": v}
    try:
        man, dom, side, era5, stn, groups = load_fire(fid)
        records, grp = era5, groups["era5"]
        opts = dict(ignition="active", active_days=2, ffmc_spinup=True, burning_period=(10.0, 20.0),
                    windows_h=tuple(task.get("windows", WINDOWS)))
        member = Member()
        same = False
        if v.startswith("xw_"):
            if "fbfm" not in side:
                same = True
            else:
                fuel = dom.fuel.copy()
                for g, code in C.CROSSWALKS[v[3:]].items():
                    fuel[np.isin(side["fbfm"], C.GROUPS[g])] = code
                same = bool((fuel == dom.fuel).all())
                dom = dreplace(dom, fuel=fuel)
        elif v in ("cure95", "cure_ndvi"):
            cure = 95.0 if v == "cure95" else man.get("ndvi_curing")
            has_grass = bool(np.isin(dom.fuel, [C.O1A, C.O1B]).any())
            if cure is None or not has_grass:
                same = True
            else:
                opts.update(grass_cure_green=float(cure), grass_cure_dormant=float(cure))
        elif v.startswith("stn"):
            if stn is None or "stn" not in groups:
                same = True
            elif v == "stn":
                records, grp = stn, groups["stn"]
            elif v == "stn_wind":
                records = swap(era5, stn, ("wind_speed", "wind_direction"))
            elif v == "stn_trh":
                records = swap(era5, stn, ("temperature", "relative_humidity"))
            elif v == "stn_fwi":
                grp = groups["stn"]
        elif v == "nospin":
            opts["ffmc_spinup"] = False
        elif v == "wind075":
            member = Member(name="w075", wind_speed_factor=0.75)
        elif v == "act1":
            opts["active_days"] = 1
        elif v == "perim":
            opts["ignition"] = "perimeter"
        elif v == "feds":
            fd = side.get("dob_feds")
            if fd is None or not (fd > 0).any():
                out["error"] = "no FEDS DOB"
                return out
            dom = dreplace(dom, dob=fd)
            if day not in {p.day for p in fire_day_pairs(dom)}:
                out["error"] = "fire-day not eligible under FEDS DOB"
                return out
        elif v.startswith("rm") or v.startswith("shd1_rm"):
            opts["_rm"] = int(v[-3:]) / 100.0
            if v.startswith("shd1_"):  # combined: shrub -> D-1/D-2 crosswalk plus the ROS factor
                if "fbfm" not in side:
                    same = True
                else:
                    fuel = dom.fuel.copy()
                    fuel[np.isin(side["fbfm"], C.GROUPS["SH"])] = C.D1
                    dom = dreplace(dom, fuel=fuel)
        elif v in ("fix1", "ens_fix1"):
            dom, k = fix1(dom, side, day)
            opts["_rm"] = k
        elif v not in ("base", "ens_base"):
            raise ValueError(v)
        if same:
            out["same_as_base"] = True
            return out
        r0, r1, c0, c1 = crop_bounds(dom.dob, day, dom)
        case = build_case(dom, day, grp, records, start_hour=6, hours=24, margin_m=20000.0)
        rm = opts.pop("_rm", 1.0)
        ro = RunOptions(**opts)
        if v.startswith("ens_"):
            return ens_task(out, case, ro, rm, fid, day, t)
        if rm != 1.0:
            from firesim.validation.harness import _run, _setup
            st_ = _setup(case, ro, member)
            arr, rs_, fmc_ = _run(st_, st_["config"], ro, ros_multiplier=rm, seed=f"{fid}:{day}:{member.name}")
            res = {"arrival": arr, "run_s": rs_, "fmc": fmc_}
        else:
            res = simulate(case, ro, member)
        sc = score_day(case, res["arrival"], ro)
        cd = case.domain
        cell = cd.cell_area_m2
        prior = (cd.dob > 0) & (cd.dob < day)
        excluded = (res["arrival"] <= 0.0) | prior
        obs = (cd.dob == day) & ~excluded
        out.update({k: sc[k] for k in ("obs_growth_ha", "initial_ha", "obs_growth_fuel", "edge_hit")})
        out.update(run_s=res["run_s"], start_type=sc.get("start_type"),
                   ffmc=case.ffmc, dmc=case.dmc, dc=case.dc, bui=case.bui,
                   mean_ws_17=float(np.mean([h.wind_speed for h in case.hourly[:17]])) * member.wind_speed_factor,
                   max_ws_17=float(np.max([h.wind_speed for h in case.hourly[:17]])) * member.wind_speed_factor,
                   min_rh_17=float(np.min([h.relative_humidity for h in case.hourly[:17]])),
                   max_t_17=float(np.max([h.temperature for h in case.hourly[:17]])),
                   grass_cure=opts.get("grass_cure_green"),
                   start_perimeter_obs=sc.get("start_perimeter_obs"))
        from firesim.validation.start_strata import start_type
        out.update(start_type(cd.dob, day, cell))
        for w in ro.windows_h:
            k = f"{w:g}h"
            pred = (res["arrival"] <= w * 60.0) & ~excluded
            s = area_scores(pred, obs, cell)
            s.update(growth_ratio=sc[k]["growth_ratio"], static_share_model=sc[k].get("static_share_model"),
                     spread_ratio=sc[k].get("spread_ratio"))
            if v == "base":
                foot = cd.dob > 0
                s["pred_in_final_ha"] = float((pred & foot).sum()) * cell / 1e4
                s["pred_later_ha"] = float((pred & (cd.dob > day)).sum()) * cell / 1e4
                if "dob_det" in side:
                    det = side["dob_det"][r0:r1, c0:c1]
                    if det.shape == cd.dob.shape:
                        od = (det == day) & ~excluded
                        sd = area_scores(pred, od, cell)
                        s["det_obs_ha"], s["det_f1"] = sd["obs_ha"], sd["f1"]
                if "fbfm" in side and w == 17.0:
                    fb = side["fbfm"][r0:r1, c0:c1]
                    if fb.shape == cd.dob.shape:
                        grp_lab = C.fbfm_group(fb)
                        s["pred_by_group_ha"] = {g: float((pred & (grp_lab == g)).sum()) * cell / 1e4
                                                 for g in np.unique(grp_lab[pred])} if pred.any() else {}
                        s["obs_by_group_ha"] = {g: float((obs & (grp_lab == g)).sum()) * cell / 1e4
                                                for g in np.unique(grp_lab[obs])} if obs.any() else {}
                if w == 17.0:
                    from firesim.validation.harness import _fuel_mix
                    s["pred_fuel_mix"] = _fuel_mix(cd, pred, day, ro) if pred.any() else {}
            out[k] = s
    except Exception as exc:  # recorded, not fatal
        out["error"] = repr(exc)[:400]
    out["wall_s"] = time.time() - t
    return out


def ens_task(out, case, ro, rm, fid, day, t):
    """20-member A15 ensemble (same draws as the harness for seed 1), every member's ROS also x ``rm``."""
    import random
    from firesim.spread.ensemble import EnsembleConfig, perturb_config
    from firesim.spread.simulator import Simulator
    from firesim.validation.harness import Member, _run, _setup

    ens = EnsembleConfig(n_members=20, seed=1, **A15)
    st_ = _setup(case, ro, Member())
    det, run_s, _ = _run(st_, st_["config"], ro, ros_multiplier=rm, seed=f"{fid}:{day}:det")
    base_fmc = Simulator(st_["config"], fuel_grid=st_["fuel_grid"])._foliar_moisture()
    rng = random.Random(ens.seed)
    cd = case.domain
    cell = cd.cell_area_m2
    excluded = (det <= 0.0) | ((cd.dob > 0) & (cd.dob < day))
    obs = (cd.dob == day) & ~excluded
    areas = []
    for i in range(ens.n_members):
        cfg, k_ros, _ = perturb_config(st_["config"], ens, rng, base_fmc)
        arr, s_, _ = _run(st_, cfg, ro, k_ros * rm, seed=f"{fid}:{day}:ens{ens.seed}:{i}")
        run_s += s_
        areas.append(float(((arr <= 17 * 60.0) & ~excluded & (arr > 0)).sum()) * cell / 1e4)
    out.update({"obs_ha": float(obs.sum()) * cell / 1e4,
                "det_ha": float(((det <= 17 * 60.0) & ~excluded).sum()) * cell / 1e4,
                "member_ha": areas, "rm": rm, "run_s": run_s, "wall_s": time.time() - t})
    from firesim.validation.start_strata import start_type
    out.update(start_type(cd.dob, day, cell))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variants", nargs="+", default=["base"])
    ap.add_argument("--region", nargs="+", default=["US", "CAN"])
    ap.add_argument("--windows", type=float, nargs="+", default=list(WINDOWS))
    ap.add_argument("--days-per-fire", type=int, default=0,
                    help="at most this many fire-days per fire (seeded random.Random(f'2026102902:{fid}'))")
    ap.add_argument("--fires", nargs="+", default=None, help="only these fire IDs")
    ap.add_argument("--runs-dir", default=str(C.DATA / "runs"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-base-s", type=float, default=0.0,
                    help="only fire-days whose base run took <= this (run time only, never outcome)")
    a = ap.parse_args()
    for v in a.variants:
        if v not in VARIANTS:
            raise SystemExit(f"unknown variant {v}")
    rd = Path(a.runs_dir)
    rd.mkdir(parents=True, exist_ok=True)
    man = [m for m in json.loads((C.DATA / "manifest.json").read_text()) if m["region"] in a.region
           and (a.fires is None or m["fire_id"] in a.fires)]
    keep = None
    if a.max_base_s:
        base = [json.loads(x) for x in (rd / "base.jsonl").read_text().splitlines()]
        keep = {(r["fire_id"], r["day"]) for r in base if "error" not in r and r["run_s"] <= a.max_base_s}
    if a.days_per_fire:
        import random
        for m in man:
            ds = [d for d in m["days"] if keep is None or (m["fire_id"], d) in keep]
            if len(ds) > a.days_per_fire:
                ds = sorted(random.Random(f"2026102902:{m['fire_id']}").sample(ds, a.days_per_fire))
            m["days"] = ds
    tasks = []
    for v in a.variants:
        p = rd / f"{v}.jsonl"
        done = set()
        if p.exists():
            done = {(r["fire_id"], r["day"]) for r in map(json.loads, p.read_text().splitlines())
                    if "error" not in r or r["error"].startswith(("no FEDS", "fire-day not"))}
        for m in man:
            for d in m["days"]:
                if (m["fire_id"], d) in done or (keep is not None and (m["fire_id"], d) not in keep):
                    continue
                tasks.append({"fire_id": m["fire_id"], "day": d, "variant": v, "windows": a.windows})
    tasks.sort(key=lambda t: (t["fire_id"], t["day"], t["variant"]))
    print(f"{len(tasks)} runs", flush=True)
    fhs = {v: open(rd / f"{v}.jsonl", "a") for v in a.variants}
    t0 = time.time()
    with Pool(a.workers, maxtasksperchild=6) as pool:
        for k, r in enumerate(pool.imap_unordered(run_task, tasks), 1):
            fhs[r["variant"]].write(json.dumps(r, default=float) + "\n")
            fhs[r["variant"]].flush()
            if k % 10 == 0 or "error" in r:
                print(f"[{k}/{len(tasks)}] {r['fire_id']} {r['day']} {r['variant']} "
                      f"{r.get('error', '')} {r.get('run_s', 0):.0f}s {(time.time() - t0) / 60:.1f} min", flush=True)
    for fh in fhs.values():
        fh.close()


if __name__ == "__main__":
    sys.exit(main())
