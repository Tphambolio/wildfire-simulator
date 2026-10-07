#!/usr/bin/env python3
"""FireSim validation against observed Alberta fires (CFSDS; Bennett et al. 2026 protocol).

Stages (run in order; each is resumable):

    # 0. data: CFSDS (scripts/validation/fetch_cfsds.py) and the national FBP fuel grid
    #    (see docs/validation.md "Data"), in $FIRESIM_VALIDATION_DATA
    python scripts/validate.py prepare --n-fires 32 --days-per-fire 6
    python scripts/validate.py run --ignition perimeter --workers 8
    python scripts/validate.py run --ignition bennett --workers 8
    python scripts/validate.py run --ignition perimeter --spotting --max-cases 60
    python scripts/validate.py run --ignition perimeter --wind-members --max-cases 40
    python scripts/validate.py report

``prepare`` picks Alberta fires (2014-2024, >= 1,000 ha; the 2016 Horse River fire always
included), resamples DOB / fuel / DEM onto a lat/lng grid per fire, picks fire-days and fetches
hourly ERA5 weather. ``run`` simulates each fire-day and appends one JSON line per fire-day to
``runs/<run-name>.jsonl``. ``report`` writes markdown tables and a per-fire-day CSV.

Large data stay under $FIRESIM_VALIDATION_DATA (default /home/rpas/dev/wildfire/validation-data),
never in the repository. Needs PYTHONPATH=engine/src.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import random
import sys
import time
from datetime import timedelta
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
FUEL_2014B = ROOT / "fuels/nat2014b/nat_fbpfuels_2014b.tif"
MRDEM = "/vsicurl/https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif"
HORSE_RIVER = "2016_255"
HORSE_RIVER_DAYS = (123, 124, 125, 126, 127, 128)  # 2 May (Fort McMurray entry 3 May) to 7 May
MAX_CELLS = 1_200_000

# Rough Alberta outline (lng, lat): the 60th parallel, 110 W, 49 N, and the continental
# divide approximated from Waterton to 53.8 N / 120 W.
ALBERTA = [(-120, 60), (-110, 60), (-110, 49), (-114.06, 49), (-114.7, 49.6), (-115.6, 50.6),
           (-116.6, 51.6), (-118.2, 52.6), (-120, 53.8)]

log = logging.getLogger("validate")


def groups_csv(year: int) -> Path:
    return ROOT / f"cfsds/groups/Firegrowth_groups_v1_1_{year}.csv"


def manifest_path() -> Path:
    return ROOT / "manifest.json"


# --------------------------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------------------------

def alberta_fires(years: range, min_area_ha: float) -> list[dict]:
    import shapely

    from firesim.validation.cfsds import read_groups

    ab = shapely.Polygon(ALBERTA)
    out = []
    for y in years:
        if not groups_csv(y).exists():
            continue
        for fid, days in read_groups(groups_csv(y)).items():
            rows = list(days.values())
            area = sum(r["firearea"] for r in rows)
            lat = float(np.mean([r["lat"] for r in rows]))
            lng = float(np.mean([r["lon"] for r in rows]))
            if area >= min_area_ha and ab.contains(shapely.Point(lng, lat)):
                out.append({"fire_id": fid, "year": y, "area_ha": area, "lat": lat, "lng": lng,
                            "ndays": len(rows)})
    return out


def choose_res(dob_path: Path, buffer_m: float) -> float:
    import rasterio

    from firesim.validation.cfsds import DOB_NODATA

    with rasterio.open(dob_path) as src:
        a = src.read(1)
        rr, cc = np.nonzero((a != DOB_NODATA) & (a > 0))
        h = (rr.max() - rr.min() + 1) * abs(src.res[1]) + 2 * buffer_m
        w = (cc.max() - cc.min() + 1) * abs(src.res[0]) + 2 * buffer_m
    for res in (90.0, 180.0, 270.0, 360.0):
        if (h / res) * (w / res) <= MAX_CELLS:
            return res
    return 450.0


def cmd_prepare(args) -> None:
    from firesim.validation.cfsds import FireDomain, fire_day_pairs, prepare_domain
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    rng = random.Random(args.seed)
    fires = alberta_fires(range(args.year_min, args.year_max + 1), args.min_area)
    log.info("%d Alberta fires >= %.0f ha in %d-%d", len(fires), args.min_area, args.year_min,
             args.year_max)
    by_year: dict[int, list[dict]] = {}
    for f in fires:
        by_year.setdefault(f["year"], []).append(f)
    chosen = [f for f in fires if f["fire_id"] == HORSE_RIVER]
    years = sorted(by_year)
    # Round-robin over years so the sample spans years (and therefore fuels and regions)
    pools = {y: rng.sample(by_year[y], len(by_year[y])) for y in years}
    while len(chosen) < args.n_fires and any(pools.values()):
        for y in years:
            if pools[y] and len(chosen) < args.n_fires:
                f = pools[y].pop()
                if f["fire_id"] != HORSE_RIVER:
                    chosen.append(f)

    (ROOT / "domains").mkdir(exist_ok=True)
    manifest = []
    for f in chosen:
        fid = f["fire_id"]
        dob_path = ROOT / f"cfsds/doy/{fid}_krig.tif"
        if not dob_path.exists():
            log.warning("%s: no DOB raster", fid)
            continue
        dpath = ROOT / f"domains/{fid}.npz"
        if dpath.exists():
            dom = FireDomain.load(dpath)
        else:
            res = choose_res(dob_path, args.buffer_m)
            t = time.time()
            dom = prepare_domain(fid, dob_path, FUEL_2014B, MRDEM, res_m=res,
                                 buffer_m=args.buffer_m,
                                 dem_overview_level=0 if res < 120 else 1)
            dom.save(dpath)
            log.info("%s: domain %dx%d at %.0f m (%.0fs)", fid, dom.rows, dom.cols, res,
                     time.time() - t)
        pairs = fire_day_pairs(dom)
        if not pairs:
            log.info("%s: no eligible fire-days", fid)
            continue
        if fid == HORSE_RIVER:
            days = [p.day for p in pairs if p.day in HORSE_RIVER_DAYS]
        else:
            days = sorted(rng.sample([p.day for p in pairs], min(args.days_per_fire, len(pairs))))
        burn_days = dom.burn_days()
        lat = dom.lat_max - 0.5 * dom.rows * dom.cell_lat
        lng = dom.lng_min + 0.5 * dom.cols * dom.cell_lng
        fetch_era5_hourly(lat, lng, doy_to_date(dom.year, burn_days[0]) - timedelta(days=1),
                          doy_to_date(dom.year, burn_days[-1]) + timedelta(days=2),
                          ROOT / "weather")
        manifest.append({**f, "res_m": dom.meta.get("res_m"), "grid": [dom.rows, dom.cols],
                         "eligible_days": len(pairs), "days": days,
                         "weather_point": [lat, lng],
                         "weather_range": [burn_days[0], burn_days[-1]]})
        log.info("%s (%d, %.0f ha): %d eligible fire-days, chose %s", fid, f["year"],
                 f["area_ha"], len(pairs), days)
        manifest_path().write_text(json.dumps(manifest, indent=1))
    log.info("%d fires, %d fire-days in %s", len(manifest), sum(len(m["days"]) for m in manifest),
             manifest_path())


# --------------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _load_fire(fid: str):
    from firesim.validation.cfsds import FireDomain, read_groups
    from firesim.validation.weather import doy_to_date, fetch_era5_hourly

    m = next(x for x in json.loads(manifest_path().read_text()) if x["fire_id"] == fid)
    dom = FireDomain.load(ROOT / f"domains/{fid}.npz")
    groups = read_groups(groups_csv(dom.year))[fid]
    lat, lng = m["weather_point"]
    d0, d1 = m["weather_range"]
    rec = fetch_era5_hourly(lat, lng, doy_to_date(dom.year, d0) - timedelta(days=1),
                            doy_to_date(dom.year, d1) + timedelta(days=2), ROOT / "weather")
    return dom, groups, rec


def _run_task(task: dict) -> dict:
    from firesim.validation.harness import (
        DETERMINISTIC,
        RunOptions,
        build_case,
        run_fire_day,
        wind_direction_members,
    )

    logging.getLogger("firesim").setLevel(logging.WARNING)
    t = time.time()
    try:
        dom, groups, rec = _load_fire(task["fire_id"])
        case = build_case(dom, task["day"], groups, rec, start_hour=task["start_hour"],
                          hours=24, margin_m=task["margin_m"])
        opts = RunOptions(ignition=task["ignition"], enable_spotting=task["spotting"],
                          windows_h=tuple(task["windows"]))
        members = wind_direction_members() if task["wind_members"] else DETERMINISTIC
        out = run_fire_day(case, opts, members)
    except Exception as exc:  # recorded, not fatal: one bad fire-day must not stop the batch
        out = {"fire_id": task["fire_id"], "day": task["day"], "error": repr(exc)}
    out["wall_s"] = time.time() - t
    return out


def run_name(args) -> str:
    if args.name:
        return args.name
    name = f"{args.ignition}_start{args.start_hour:02d}"
    if args.spotting:
        name += "_spotting"
    if args.wind_members:
        name += "_wind12"
    return name


def cmd_run(args) -> None:
    manifest = json.loads(manifest_path().read_text())
    tasks = [{"fire_id": m["fire_id"], "day": d} for m in manifest for d in m["days"]]
    if args.max_cases:
        tasks = random.Random(args.seed).sample(tasks, min(args.max_cases, len(tasks)))
    out = ROOT / "runs" / f"{run_name(args)}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if "error" not in r:
                done.add((r["fire_id"], r["day"]))
    tasks = [dict(t, ignition=args.ignition, spotting=args.spotting, start_hour=args.start_hour,
                  windows=args.windows, margin_m=args.margin_m, wind_members=args.wind_members)
             for t in tasks if (t["fire_id"], t["day"]) not in done]
    # Group by fire so each worker's cached domain is reused
    tasks.sort(key=lambda t: (t["fire_id"], t["day"]))
    log.info("%d fire-days to run -> %s", len(tasks), out)
    t0 = time.time()
    with Pool(args.workers, maxtasksperchild=8) as pool, open(out, "a") as fh:
        for k, r in enumerate(pool.imap_unordered(_run_task, tasks), 1):
            fh.write(json.dumps(r, default=_json_default) + "\n")
            fh.flush()
            det = r.get("members", {}).get("det") or next(iter(r.get("members", {}).values()), {})
            log.info("[%d/%d] %s day %s: %s F1(17h)=%s %.0fs", k, len(tasks), r["fire_id"],
                     r["day"], r.get("error", ""),
                     _f(det.get("17h", {}).get("f1")) if det else "-", r["wall_s"])
    log.info("done in %.1f min", (time.time() - t0) / 60)


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def _f(v) -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.3f}"


# --------------------------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------------------------

def _read_runs() -> dict[str, list[dict]]:
    runs = {}
    for p in sorted((ROOT / "runs").glob("*.jsonl")):
        recs = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        runs[p.stem] = recs
    return runs


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def cmd_report(args) -> None:
    from firesim.validation.report import (
        BENNETT_WISE,
        METRICS,
        fmt,
        flatten,
        group_by,
        per_fire_means,
        spread_summary,
        summarize,
    )

    runs = _read_runs()
    lines = ["# FireSim validation results (generated by scripts/validate.py report)", ""]
    csv_rows = []
    for name, recs in runs.items():
        ok = [r for r in recs if "error" not in r]
        errors = [r for r in recs if "error" in r]
        if not ok:
            continue
        members = sorted({m for r in ok for m in r["members"]})
        n_fires = len({r["fire_id"] for r in ok})
        run_s = [m["run_s"] for r in ok for m in r["members"].values()]
        lines += [f"## Run `{name}`", "",
                  f"{len(ok)} fire-days from {n_fires} fires ({len(errors)} errors); "
                  f"members: {', '.join(members)}; model run time per member median "
                  f"{np.median(run_s):.0f} s, max {np.max(run_s):.0f} s; "
                  f"edge of working area reached on "
                  f"{sum(r['members'][members[0]].get('edge_hit', False) for r in ok)} fire-days; "
                  f"observed growth on cells the fuel grid calls non-fuel: mean "
                  f"{100 * np.mean([r['obs_growth_fuel'].get('NF', 0.0) for r in ok]):.0f} %; "
                  f"observed growth per fire-day median "
                  f"{np.median([r['obs_growth_ha'] for r in ok]):,.0f} ha.", ""]
        member = "det" if "det" in members else None
        if member is None:  # wind ensemble: best member per fire-day (Bennett scenario 3)
            best = []
            for r in ok:
                b = r["best_member"]
                best.append({**r, "members": {"best": r["members"][b]}})
            rows = flatten(best, "best")
            lines.append("Scores use, per fire-day, the constant wind direction with the best "
                         "oracle-duration F1 (Bennett scenario 3).")
            lines.append("")
        else:
            rows = flatten(ok, member)
        for r in rows:
            csv_rows.append({"run": name, **r})
        hdr = ["window", "n", *METRICS]
        trows = []
        for w in ("8h", "17h", "24h", "oracle"):
            rs = [r for r in rows if r["window"] == w]
            if not rs:
                continue
            s = summarize(rs)
            trows.append([w, str(len(rs))] + [
                f"{fmt(s[k]['mean'], k)} ({fmt(s[k]['median'], k)})" for k in METRICS])
        lines += ["Fire-day mean (median) of each score:", "", _table(hdr, trows), ""]
        # per-fire means (Bennett Table 2)
        frows = []
        for w in ("17h", "oracle"):
            pf = per_fire_means([r for r in rows if r["window"] == w])
            if pf:
                s = summarize(pf)
                frows.append([w, str(len(pf))] + [fmt(s[k]["mean"], k) for k in METRICS])
        if frows:
            lines += ["Mean over fires of per-fire means:", "", _table(hdr, frows), ""]
        # spread diagnostics
        srows = []
        for w in ("8h", "17h", "24h"):
            rs = [r for r in rows if r["window"] == w]
            if rs:
                s = spread_summary(rs)
                srows.append([w, str(s["n"]), _f(s["spread_ratio_median"]),
                              f"{100 * s['spread_over_share']:.0f} %",
                              f"{100 * s['spread_under_share']:.0f} %",
                              f"{100 * s['within_35pct']:.0f} %",
                              f"{s['bearing_err_median']:.0f}", f"{s['abs_bearing_err_median']:.0f}"])
        if srows:
            lines += ["Head spread (farthest growth from the starting area):", "",
                      _table(["window", "n", "median pred/obs", "> +35 %", "< -35 %",
                              "within ±35 %", "median bearing err °", "median abs bearing err °"],
                             srows), ""]
        for key, title in (("fuel", "dominant fuel of observed growth"),
                           ("fwi_class", "burn-day FWI class (CFSDS)"),
                           ("growth_class", "observed growth")):
            for w in ("17h", "oracle"):
                g = group_by([r for r in rows if r["window"] == w], key)
                if not g:
                    continue
                grows = [[k, str(v["n"]), fmt(v["f1"]["mean"], "f1"), fmt(v["f1"]["median"], "f1"),
                          fmt(v["iou"]["mean"], "iou"), fmt(v["area_diff_norm"]["mean"], "a")]
                         for k, v in g.items()]
                lines += [f"By {title}, window {w}:", "",
                          _table([key, "n", "F1 mean", "F1 median", "IoU mean", "area diff mean"],
                                 grows), ""]
    wise_path = ROOT / "runs_wise" / "wise_vs_firesim.jsonl"
    if wise_path.exists():
        wrows = []
        acc: dict[str, list[float]] = {}
        for line in wise_path.read_text().splitlines():
            r = json.loads(line)
            w = r["wise"] if isinstance(r["wise"], dict) else {}
            if "17h" not in w:
                continue
            vals = {"wise_f1_17": w["17h"]["f1"], "fs_f1_17": r["firesim"]["17h"]["f1"],
                    "wise_ad_17": w["17h"]["area_diff_norm"],
                    "fs_ad_17": r["firesim"]["17h"]["area_diff_norm"],
                    "wise_f1_best": (w.get("oracle") or {}).get("f1", math.nan),
                    "fs_f1_best": r["firesim"]["oracle"]["f1"],
                    "wise_s": r["wise_run_s"], "fs_s": r["firesim_run_s"]}
            for k, v in vals.items():
                acc.setdefault(k, []).append(v)
            wrows.append([f"{r['fire_id']} day {r['day']}", f"{r['obs_growth_ha']:,.0f}"]
                         + [_f(vals[k]) for k in ("wise_f1_17", "fs_f1_17", "wise_ad_17",
                                                  "fs_ad_17", "wise_f1_best", "fs_f1_best")]
                         + [f"{vals['wise_s']:.0f}", f"{vals['fs_s']:.0f}"])
        if wrows:
            wrows.append(["mean", ""] + [_f(float(np.nanmean(acc[k]))) for k in
                                         ("wise_f1_17", "fs_f1_17", "wise_ad_17", "fs_ad_17",
                                          "wise_f1_best", "fs_f1_best")]
                         + [f"{np.mean(acc['wise_s']):.0f}", f"{np.mean(acc['fs_s']):.0f}"])
            lines += ["## WISE vs FireSim on identical inputs (perimeter start, 06-23 h, no spotting)",
                      "", _table(["fire-day", "obs growth ha", "WISE F1 17h", "FireSim F1 17h",
                                  "WISE area diff", "FireSim area diff", "WISE F1 best h",
                                  "FireSim F1 best h", "WISE s", "FireSim s"], wrows), ""]
    lines += ["## Bennett et al. (2026) W.I.S.E. benchmark (fire-day means)", "",
              _table(["scenario", *METRICS],
                     [[k] + [fmt(v[m], m) for m in METRICS] for k, v in BENNETT_WISE.items()]), ""]
    out = ROOT / "report.md"
    out.write_text("\n".join(lines))
    with open(ROOT / "fire_days.csv", "w", newline="") as fh:
        if csv_rows:
            w = csv.DictWriter(fh, fieldnames=list(csv_rows[0]))
            w.writeheader()
            w.writerows(csv_rows)
    print("\n".join(lines))
    log.info("wrote %s and %s", out, ROOT / "fire_days.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--n-fires", type=int, default=32)
    p.add_argument("--days-per-fire", type=int, default=6)
    p.add_argument("--year-min", type=int, default=2014)
    p.add_argument("--year-max", type=int, default=2024)
    p.add_argument("--min-area", type=float, default=1000.0)
    p.add_argument("--buffer-m", type=float, default=15000.0)
    p.add_argument("--seed", type=int, default=2026)
    r = sub.add_parser("run")
    r.add_argument("--ignition", choices=("perimeter", "bennett"), default="perimeter")
    r.add_argument("--spotting", action="store_true")
    r.add_argument("--wind-members", action="store_true",
                   help="12 constant wind directions (Bennett scenario 3)")
    r.add_argument("--start-hour", type=int, default=6)
    r.add_argument("--windows", type=float, nargs="+", default=[8.0, 17.0, 24.0])
    r.add_argument("--margin-m", type=float, default=20000.0)
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    r.add_argument("--max-cases", type=int, default=0)
    r.add_argument("--seed", type=int, default=2026)
    r.add_argument("--name", default="")
    sub.add_parser("report")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    {"prepare": cmd_prepare, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
