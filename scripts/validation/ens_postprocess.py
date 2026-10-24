"""B2: fit and score the log-area post-processing of the ensemble (docs/PREREG_ENSEMBLE_B2.md).

Two steps, run in this order and never the other way round:

    # 1. fit on the CALIBRATION fire-days only (no held-out file is read)
    python scripts/validation/ens_postprocess.py fit --cal CAL.jsonl --out DIR
    # 2. apply the frozen fit to the HELD-OUT fire-days and score (reads DIR/fit.json)
    python scripts/validation/ens_postprocess.py score --test TEST.jsonl --out DIR

Arms (PREREG §4): B = today's members unchanged (identity); P1 = MBM shift + stretch, stretch
chosen to reach 80 % calibration coverage, shift CRPS-optimal at that stretch (deciding arm);
P2 = MBM with shift and stretch both CRPS-optimal (descriptive); P0 = shift only, stretch 1
(descriptive). Scores at the 17 h window on log10(area + 10 ha), exactly as
``firesim.validation.ensemble_scores.summarize_ensemble``. CIs: 2,000 whole-fire bootstrap
draws, seed 1, percentile 95 % (the [R49] convention).

Outputs in DIR: fit.json (step 1); results.json and per_fireday.csv (step 2).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from firesim.validation.ensemble_calibration import (
    COVERAGE_TARGET,
    SCALE_GRID,
    SHIFT_GRID,
    best_shift,
    calibrate_member_areas,
    day_logs,
    fit_coverage_target,
    fit_min_crps,
    footprint_for_area,
    mean_scores,
    score_day,
    transform_log_area,
)
from firesim.validation.ensemble_scores import spread_skill

WINDOW = "17h"
N_BOOT = 2000
SEED = 1
NI_MARGIN = 0.050  # CRPS non-inferiority margin, log10 units (PREREG §6)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_rows(p: Path) -> list[dict]:
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if "error" not in r and WINDOW in r.get("ens", {})]
    keys = [(r["fire_id"], r["day"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise SystemExit(f"duplicate fire-days in {p}")
    return sorted(rows, key=lambda r: (r["fire_id"], r["day"]))


def fit_arms(days) -> dict:
    p1 = fit_coverage_target(days)
    p2 = fit_min_crps(days)
    p0_shift = best_shift(days, 1.0)
    return {
        "B": {"shift": 0.0, "scale": 1.0, **mean_scores(days, 0.0, 1.0)},
        "P1": {k: v for k, v in p1.items() if k != "path"},
        "P2": p2,
        "P0": {"shift": p0_shift, "scale": 1.0, **mean_scores(days, p0_shift, 1.0)},
        "_p1_path": p1["path"],
    }


def cmd_fit(args) -> None:
    cal = Path(args.cal)
    rows = read_rows(cal)
    days = day_logs(rows, WINDOW)
    arms = fit_arms(days)
    # leave-one-fire-out on the calibration fires (descriptive): refit without the fire,
    # score the fire's days
    fires = sorted({r["fire_id"] for r in rows})
    lofo = {a: {"crps": [], "inside": []} for a in ("B", "P1", "P2", "P0")}
    lofo_params = {}
    for f in fires:
        tr = [d for d, r in zip(days, rows) if r["fire_id"] != f]
        te = [d for d, r in zip(days, rows) if r["fire_id"] == f]
        a = fit_arms(tr)
        lofo_params[f] = {k: (a[k]["shift"], a[k]["scale"]) for k in ("P1", "P2", "P0")}
        for k in lofo:
            for y, o in te:
                s = score_day(transform_log_area(y, a[k]["shift"], a[k]["scale"]), o)
                lofo[k]["crps"].append(s.crps)
                lofo[k]["inside"].append(s.inside)
    out = {
        "step": "fit (calibration fire-days only)",
        "cal_file": str(cal), "cal_sha256": sha256(cal),
        "n_fire_days": len(rows), "n_fires": len(fires), "fires": fires,
        "window": WINDOW, "coverage_target": COVERAGE_TARGET,
        "shift_grid": [float(SHIFT_GRID[0]), float(SHIFT_GRID[-1]), 0.01],
        "scale_grid": [float(SCALE_GRID[0]), float(SCALE_GRID[-1]), 0.05],
        "arms": {k: v for k, v in arms.items() if not k.startswith("_")},
        "p1_path": arms["_p1_path"],
        "calibration_spread_skill": {
            k: spread_skill([transform_log_area(y, arms[k]["shift"], arms[k]["scale"])
                             for y, _ in days], np.array([o for _, o in days]))
            for k in ("B", "P1", "P2", "P0")},
        "leave_one_fire_out": {
            "note": "descriptive: each calibration fire scored with a fit on the other fires",
            "params": lofo_params,
            **{k: {"crps": float(np.mean(v["crps"])), "coverage": float(np.mean(v["inside"]))}
               for k, v in lofo.items()}},
    }
    d = Path(args.out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "fit.json").write_text(json.dumps(out, indent=1))
    a = out["arms"]
    for k in ("B", "P1", "P2", "P0"):
        print(f"{k}: shift {a[k]['shift']:+.2f} scale {a[k]['scale']:.2f}  "
              f"CRPS {a[k]['crps']:.3f} coverage {a[k]['coverage']:.3f} "
              f"below/above {a[k]['below_p10']:.2f}/{a[k]['above_p90']:.2f}")
    print("LOFO:", {k: out["leave_one_fire_out"][k] for k in ("B", "P1", "P2", "P0")})


def boot(by_fire: dict[str, list[float]], stat=np.mean) -> tuple[float, float, float]:
    fires = sorted(by_fire)
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(N_BOOT):
        pick = rng.choice(len(fires), len(fires), replace=True)
        vals.append(stat([v for k in pick for v in by_fire[fires[k]]]))
    allv = [v for f in fires for v in by_fire[f]]
    return (float(stat(allv)), float(np.percentile(vals, 2.5)),
            float(np.percentile(vals, 97.5)))


def _cell_ha(e: dict) -> float:
    n = sum(e["k_obs"])
    if n:
        return e["obs_ha"] / n
    c = sum(e["k_cells"][2:])  # the P10 footprint: k >= 2 of 20
    return e["p10"]["area_pred"] / 1e4 / c if c else math.nan


def cmd_score(args) -> None:
    d = Path(args.out)
    fit = json.loads((d / "fit.json").read_text())
    test = Path(args.test)
    rows = read_rows(test)
    days = day_logs(rows, WINDOW)
    arms = {k: (v["shift"], v["scale"]) for k, v in fit["arms"].items()}
    per = []
    for r, (y, o) in zip(rows, days):
        e = r["ens"][WINDOW]
        row = {"fire_id": r["fire_id"], "day": r["day"], "obs_ha": e["obs_ha"],
               "obs_log10": o, "native_p10_recall": e["p10"]["recall"]}
        ch = _cell_ha(e)
        for k, (sh, sc) in arms.items():
            t = transform_log_area(y, sh, sc)
            s = score_day(t, o)
            ha = calibrate_member_areas(e["member_ha"], sh, sc)
            fp = (footprint_for_area(e["k_cells"], e["k_obs"], ch, float(np.percentile(ha, 90)))
                  if np.isfinite(ch) else {"k": None, "area_ha": math.nan, "recall": math.nan})
            row.update({f"{k}_crps": s.crps, f"{k}_inside": int(s.inside),
                        f"{k}_below": int(s.below), f"{k}_above": int(s.above),
                        f"{k}_rank": s.rank, f"{k}_lo": float(np.percentile(t, 10)),
                        f"{k}_hi": float(np.percentile(t, 90)), f"{k}_mean": float(t.mean()),
                        f"{k}_fp_k": fp["k"], f"{k}_fp_area_ha": fp["area_ha"],
                        f"{k}_fp_recall": fp["recall"]})
        per.append(row)

    def by_fire(key, base=None):
        out: dict[str, list[float]] = {}
        for p in per:
            v = p[key] - (p[base] if base else 0.0)
            out.setdefault(p["fire_id"], []).append(float(v))
        return out

    res = {"step": "score (held-out fire-days, frozen calibration fit)",
           "fit_sha256": sha256(d / "fit.json"), "test_file": str(test),
           "test_sha256": sha256(test), "n_fire_days": len(per),
           "n_fires": len({p["fire_id"] for p in per}), "window": WINDOW,
           "bootstrap": f"{N_BOOT} whole-fire draws, seed {SEED}, percentile 95 % CI",
           "arms": {}}
    obs = np.array([o for _, o in days])
    for k, (sh, sc) in arms.items():
        t_all = [transform_log_area(y, sh, sc) for y, _ in days]
        ss = spread_skill(t_all, obs)
        rec = [np.nan_to_num(p[f"{k}_fp_recall"], nan=0.0) >= 0.9 for p in per]
        res["arms"][k] = {
            "shift": sh, "scale": sc,
            "coverage": boot(by_fire(f"{k}_inside")),
            "crps": boot(by_fire(f"{k}_crps")),
            "below_p10": float(np.mean([p[f"{k}_below"] for p in per])),
            "above_p90": float(np.mean([p[f"{k}_above"] for p in per])),
            "rank_hist": np.bincount([p[f"{k}_rank"] for p in per], minlength=21).tolist(),
            "spread_skill": ss["ratio"], "spread_skill_debiased": ss["ratio_debiased"],
            "ens_mean_bias_log10": ss["bias"], "rmse": ss["rmse"], "rms_spread": ss["rms_spread"],
            "mapped_p10_footprint_recall_ge_0.9": float(np.mean(rec)),
        }
        if k != "B":
            res["arms"][k]["d_coverage_vs_B"] = boot(by_fire(f"{k}_inside", "B_inside"))
            res["arms"][k]["d_crps_vs_B"] = boot(by_fire(f"{k}_crps", "B_crps"))
    res["native_p10_recall_ge_0.9"] = float(np.mean(
        [np.nan_to_num(p["native_p10_recall"], nan=0.0) >= 0.9 for p in per]))
    # spread/skill CI for B and P1 (descriptive)
    for k in ("B", "P1"):
        sh, sc = arms[k]
        idx: dict[str, list[int]] = {}
        for i, p in enumerate(per):
            idx.setdefault(p["fire_id"], []).append(i)
        fires = sorted(idx)
        rng = np.random.default_rng(SEED)
        t_all = [transform_log_area(y, sh, sc) for y, _ in days]
        vals = []
        for _ in range(N_BOOT):
            pick = [i for j in rng.choice(len(fires), len(fires), replace=True)
                    for i in idx[fires[j]]]
            vals.append(spread_skill([t_all[i] for i in pick], obs[pick])["ratio"])
        res["arms"][k]["spread_skill_ci"] = [float(np.nanpercentile(vals, 2.5)),
                                             float(np.nanpercentile(vals, 97.5))]
    # leave-one-fire-out sensitivity of the deciding numbers (descriptive)
    lofo = {}
    for f in sorted({p["fire_id"] for p in per}):
        q = [p for p in per if p["fire_id"] != f]
        lofo[f] = {"P1_coverage": float(np.mean([p["P1_inside"] for p in q])),
                   "d_crps": float(np.mean([p["P1_crps"] - p["B_crps"] for p in q]))}
    res["leave_one_fire_out_P1"] = lofo
    # pre-registered decision (PREREG §6)
    p1 = res["arms"]["P1"]
    c1 = p1["coverage"][0] >= 0.70
    c2 = p1["d_coverage_vs_B"][1] > 0.0
    c3 = p1["d_crps_vs_B"][0] <= 0.0 and p1["d_crps_vs_B"][2] <= NI_MARGIN
    res["decision"] = {
        "rule": "PASS iff P1 held-out coverage >= 0.70 AND lower 95 % CI of coverage(P1) - "
                "coverage(B) > 0 AND (mean dCRPS(P1 - B) <= 0 AND its upper 95 % CI <= +0.050)",
        "coverage_ge_0.70": c1, "d_coverage_ci_lo_gt_0": c2, "crps_non_inferior": c3,
        "verdict": "PASS" if (c1 and c2 and c3) else "FAIL"}
    (d / "results.json").write_text(json.dumps(res, indent=1))
    with open(d / "per_fireday.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(per[0]))
        w.writeheader()
        w.writerows(per)
    for k, v in res["arms"].items():
        print(f"{k}: cov {v['coverage'][0]:.3f} [{v['coverage'][1]:.3f}, {v['coverage'][2]:.3f}]"
              f" CRPS {v['crps'][0]:.3f}  ss {v['spread_skill']:.2f}"
              + (f"  dCRPS {v['d_crps_vs_B'][0]:+.3f} [{v['d_crps_vs_B'][1]:+.3f}, "
                 f"{v['d_crps_vs_B'][2]:+.3f}]" if k != "B" else ""))
    print("decision:", res["decision"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--cal", required=True)
    f.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--test", required=True)
    s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"fit": cmd_fit, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
