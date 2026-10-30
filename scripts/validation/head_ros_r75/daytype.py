#!/usr/bin/env python3
"""[R75] pool plan, step 1: can forecast-time inputs separate "major-run" from "held" fire-days?

Purpose
    Before any pre-registration, check on SPENT data only whether a day-type rule built from inputs
    known at forecast time separates the days FireSim under-spreads (runs) from the days it
    over-spreads (held fires). Exploratory; the rules found here are proposals for the plan.

Data (all spent; nothing held out is opened)
    - the record's 143 fire-days (32 Alberta fires: 16 calibration + 16 H79 test), operational set-up on
      the back-cast grid: H79 from the [R45] rescore run `active2_spin_bp1020.jsonl`, calibration from
      [R72] `candidates_area.jsonl` (variant `base`; same code path, 64 fire-days);
    - the 20 C1b fire-days ([R72] `sensitivity_rows.jsonl`, variant `base`; scored in [R66]).
Predictors (known before the burn day; from CFSDS DOB of earlier days and CFSDS day-n codes, which are
ERA5-derived and stand in for a forecast): start age (days), start area (ha), previous-day growth
(ha, CFSDS `firearea` of day n-1) and its share of the start area (R71's `start_prev_day_share`),
day-n FFMC, ISI, BUI, FWI, noon wind (km/h) and the harness's mean 0-17 h ERA5 wind.
Outcome (for evaluation only): L17 = log10((modelled + 1 ha) / (observed + 1 ha)) day-n growth at 17 h;
H17 = log10((modelled + 90 m) / (observed + 90 m)) forward (head) spread distance at 17 h (harness
``head_spread``: farthest growth cell from the start area); observed growth (ha). The record's 143 fire-days
come from the [R45] rescore run file (all 143 days; split by ``report.split_fires``).

Outputs: OUT/daytype_rows.csv (one row per fire-day) and OUT/daytype_summary.json (rule tables).
Usage: python3 scripts/validation/head_ros_r75/daytype.py collect | summarise
"""
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VD = Path("/home/rpas/dev/wildfire/validation-data")
OUT = Path("/home/rpas/dev/wildfire/reports/data/head-ros-r75-pool-plan-2026-10-29")
R72 = Path("/home/rpas/dev/wildfire/reports/data/arrival-underspread-r72-2026-10-29")
H79_RUN = VD / "rescore-backcast/backcast/runs/active2_spin_bp1020.jsonl"


def predictors(root: str, domains: str, keys: list[tuple[str, int]]) -> list[dict]:
    """Forecast-time predictors for fire-days of one data root (runs in this process's env)."""
    os.environ["FIRESIM_VALIDATION_DATA"] = root
    os.environ["FIRESIM_VALIDATION_DOMAINS"] = domains
    sys.path[:0] = [str(REPO / "scripts"), str(REPO / "engine/src")]
    from validate import groups_csv, load_domain

    from firesim.validation.cfsds import read_groups
    from firesim.validation.start_strata import start_type

    out, cache = [], {}
    for fid, day in keys:
        if fid not in cache:
            dom = load_domain(fid)
            cache.clear()
            cache[fid] = (dom, read_groups(groups_csv(dom.year))[fid])
        dom, g = cache[fid]
        st = start_type(dom.dob, day, dom.cell_area_m2)
        prev = g.get(day - 1, {})
        today = g.get(day, {})
        out.append({"fire_day": f"{fid}:{day}", **st,
                    "prev_growth_ha": float(prev.get("firearea", math.nan)),
                    "ffmc": float(today.get("ffmc", math.nan)), "isi": float(today.get("isi", math.nan)),
                    "bui": float(today.get("bui", math.nan)), "fwi": float(today.get("fwi", math.nan)),
                    "ws_noon": float(today.get("ws", math.nan)), "rh_noon": float(today.get("rh", math.nan))})
    return out


def lratio(pred_m2, obs_m2):
    return math.log10((pred_m2 + 1e4) / (obs_m2 + 1e4))


def cmd_collect():
    sys.path[:0] = [str(REPO / "engine/src")]
    from firesim.validation.report import split_fires

    recs = [json.loads(x) for x in H79_RUN.read_text().splitlines()]
    sp = split_fires({r["fire_id"] for r in recs})
    rows = []
    for r in recs:  # all 143 record fire-days (calibration + H79 test), operational, back-cast grid
        m = r["members"]["det"]["17h"]
        ps, os_ = m.get("pred_spread_m"), m.get("obs_spread_m")
        head = (math.log10((ps + 90.0) / (os_ + 90.0)) if ps is not None and os_ is not None
                and not (isinstance(os_, float) and math.isnan(os_)) else math.nan)
        rows.append({"set": "H79" if sp[r["fire_id"]] == "test" else "calibration",
                     "fire_day": f"{r['fire_id']}:{r['day']}", "L17": lratio(m["area_pred"], m["area_obs"]),
                     "H17": head, "obs_ha": m["area_obs"] / 1e4, "obs_head_m": os_, "mean_ws_0_17": r["mean_ws_kmh"]})
    c1b = []
    for line in (R72 / "sensitivity_rows.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("variant") == "base":
            c1b.append(r["fire_day"])
    c1b_rows = {json.loads(x)["fire_day"]: json.loads(x) for x in (R72 / "candidates_arrival.jsonl").read_text().splitlines()
                if json.loads(x).get("variant") == "base"}
    for fd in c1b:
        r = c1b_rows[fd]
        rows.append({"set": "C1b", "fire_day": fd, "L17": r["log10_area_ratio"], "H17": math.nan,
                     "obs_ha": r["area_obs_m2"] / 1e4, "obs_head_m": math.nan, "mean_ws_0_17": math.nan})
    # predictors, one subprocess per data root (the harness reads its root at import)
    rec = []
    for root, dom, sets in ((str(VD), str(VD / "backcast/roots/ab2025_backcast/domains"), ("H79", "calibration")),
                            (str(VD / "c1b"), str(VD / "c1b/domains"), ("C1b",))):
        keys = [tuple(r["fire_day"].split(":")) for r in rows if r["set"] in sets]
        code = (f"import json,sys; sys.path.insert(0,{str(HERE)!r}); import daytype as D; "
                f"print(json.dumps(D.predictors({root!r},{dom!r},{[(a, int(b)) for a, b in keys]!r})))")
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        rec += json.loads(res.stdout.strip().splitlines()[-1])
    pr = {r["fire_day"]: r for r in rec}
    out = [{**r, **pr[r["fire_day"]]} for r in rows]
    with open(OUT / "daytype_rows.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(len(out), "fire-days")


RULES = {
    "held_R71": lambda r: r["start_type"] == "mature" and r["start_prev_day_share"] < 0.05,
    "run_prev_share_ge_0.10": lambda r: r["start_prev_day_share"] >= 0.10,
    "run_prev_share_ge_0.20": lambda r: r["start_prev_day_share"] >= 0.20,
    "run_isi_ge_10": lambda r: r["isi"] >= 10,
    "run_isi_ge_10_and_prev_ge_0.05": lambda r: r["isi"] >= 10 and r["start_prev_day_share"] >= 0.05,
    "run_fwi_ge_23_and_prev_ge_0.05": lambda r: r["fwi"] >= 23 and r["start_prev_day_share"] >= 0.05,
    "run_prev_ge_1000ha_and_share_ge_0.10": lambda r: r["prev_growth_ha"] >= 1000 and r["start_prev_day_share"] >= 0.10,
    "run_not_held_and_isi_ge_8": lambda r: not (r["start_type"] == "mature" and r["start_prev_day_share"] < 0.05) and r["isi"] >= 8,
}


def boot_fire(vals: dict, n=2000, seed=20261029):
    keys = list(vals)
    if not keys:
        return {"mean": math.nan, "lo": math.nan, "hi": math.nan, "n": 0}
    fires = sorted({k.split(":")[0] for k in keys})
    by = {f: [vals[k] for k in keys if k.split(":")[0] == f] for f in fires}
    rng = np.random.default_rng(seed)
    m = [float(np.mean([v for i in rng.choice(len(fires), len(fires)) for v in by[fires[i]]])) for _ in range(n)]
    return {"mean": float(np.mean(list(vals.values()))), "lo": float(np.percentile(m, 2.5)),
            "hi": float(np.percentile(m, 97.5)), "n": len(keys), "n_fires": len(fires)}


def cmd_summarise():
    rows = list(csv.DictReader(open(OUT / "daytype_rows.csv")))
    for r in rows:
        for k, v in r.items():
            if k not in ("set", "fire_day", "start_type"):
                try:
                    r[k] = float(v)
                except ValueError:
                    pass
    obs = np.array([r["obs_ha"] for r in rows])
    top = np.percentile(obs, 100 * 2 / 3)
    res = {"n": len(rows), "obs_top_tercile_ha": float(top), "rules": {}}
    for name, f in RULES.items():
        sel = [r for r in rows if f(r)]
        rest = [r for r in rows if not f(r)]
        res["rules"][name] = {
            "n_in": len(sel), "n_out": len(rest),
            "L17_in": boot_fire({r["fire_day"]: r["L17"] for r in sel}),
            "L17_out": boot_fire({r["fire_day"]: r["L17"] for r in rest}),
            "H17_in": boot_fire({r["fire_day"]: r["H17"] for r in sel if r["H17"] == r["H17"]}),
            "H17_out": boot_fire({r["fire_day"]: r["H17"] for r in rest if r["H17"] == r["H17"]}),
            "share_in_obs_top_tercile": float(np.mean([r["obs_ha"] >= top for r in sel])) if sel else math.nan,
            "share_in_underspread_L_lt_-0.3": float(np.mean([r["L17"] < -0.3 for r in sel])) if sel else math.nan,
            "by_set": {s: sum(r["set"] == s for r in sel) for s in ("H79", "calibration", "C1b")}}
    res["all"] = boot_fire({r["fire_day"]: r["L17"] for r in rows})
    res["top_tercile_L17"] = boot_fire({r["fire_day"]: r["L17"] for r in rows if r["obs_ha"] >= top})
    res["all_H17"] = boot_fire({r["fire_day"]: r["H17"] for r in rows if r["H17"] == r["H17"]})
    res["top_tercile_H17"] = boot_fire({r["fire_day"]: r["H17"] for r in rows if r["obs_ha"] >= top and r["H17"] == r["H17"]})
    print("H17 all", res["all_H17"], "top tercile", res["top_tercile_H17"])
    # power inputs: between-fire SD of the per-fire mean H17 / L17 on candidate major-run days
    for name in ("run_prev_ge_1000ha_and_share_ge_0.10", "run_prev_share_ge_0.10"):
        sel = [r for r in rows if RULES[name](r)]
        for key in ("H17", "L17"):
            by = {}
            for r in sel:
                if r[key] == r[key]:
                    by.setdefault(r["fire_day"].split(":")[0], []).append(r[key])
            fm = np.array([np.mean(v) for v in by.values()])
            dpf = np.mean([len(v) for v in by.values()]) if by else math.nan
            sd = float(np.std(fm, ddof=1)) if len(fm) > 1 else math.nan
            # fires needed for a 95 % CI half-width of 0.10 log10 on a paired difference, assuming the paired
            # (fix - base) SD is half the between-fire SD of the level (stated assumption)
            need = {f"halfwidth_{h}": int(math.ceil((1.96 * 0.5 * sd / h) ** 2)) for h in (0.05, 0.10)}
            res.setdefault("power_inputs", {})[f"{name}:{key}"] = {"n_fires": len(fm), "days_per_fire": dpf,
                                                                    "sd_fire_mean": sd, "fires_needed": need}
    print(json.dumps(res.get("power_inputs"), indent=1))
    (OUT / "daytype_summary.json").write_text(json.dumps(res, indent=1))
    print("all L17", {k: round(v, 3) if isinstance(v, float) else v for k, v in res["all"].items()})
    print("top tercile obs growth L17", {k: round(v, 3) if isinstance(v, float) else v for k, v in res["top_tercile_L17"].items()})
    for k, v in res["rules"].items():
        a, b = v["L17_in"], v["L17_out"]
        print(f"{k:34s} in {v['n_in']:3d} L {a['mean']:+.2f} [{a['lo']:+.2f},{a['hi']:+.2f}] | out {v['n_out']:3d} "
              f"L {b['mean']:+.2f} [{b['lo']:+.2f},{b['hi']:+.2f}] | top-tercile share {v['share_in_obs_top_tercile']:.2f} "
              f"| L<-0.3 share {v['share_in_underspread_L_lt_-0.3']:.2f} {v['by_set']}")
        h, hh = v["H17_in"], v["H17_out"]
        print(f"{'':34s} head H17 in {h['mean']:+.2f} [{h['lo']:+.2f},{h['hi']:+.2f}] (n {h['n']}) | out {hh['mean']:+.2f} [{hh['lo']:+.2f},{hh['hi']:+.2f}]")


if __name__ == "__main__":
    {"collect": cmd_collect, "summarise": cmd_summarise}[sys.argv[1]]()
