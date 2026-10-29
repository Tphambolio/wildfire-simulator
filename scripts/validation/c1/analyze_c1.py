#!/usr/bin/env python3
"""C1 analysis: paired FireSim - W.I.S.E. differences with whole-fire bootstrap CIs.

Implements PREREG_COMPARATOR_C1 §6-§8 exactly:

1. 17 h F1 on the day's growth (``harness.score_day``; NaN -> 0, as ``validate.py compare``);
   paired difference FireSim - WISE per fire-day; higher is better.
2. Community arrival error (``communities.arrival_errors``), hours, per fire-day with at least
   one eligible timed community; FireSim - WISE; lower is better.
3. Ensemble coverage: per fire-day and seed, 1 if the observed growth area lies inside the
   members' 10th-90th percentile of log10(area + 10 ha) (``ensemble_scores``), else 0;
   FireSim - WISE; higher is better (ideal 80 %).

CIs: 95 % percentile bootstrap, 2,000 draws, numpy default_rng(20261024). Resampling unit:
whole fires (all their fire-days); for coverage a second stage resamples seeds within each
drawn fire-day. Primary analysis: fire-days where both models completed; sensitivity: WISE
failures scored as no growth (F1 0, no arrival, coverage 0).

    python3 scripts/validation/c1/analyze_c1.py RUNS.jsonl [--set SET.txt] [--json OUT.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "engine/src"))

N_BOOT = 2000
RNG_SEED = 20261024
MARGINS = {"f1_17h": 0.03, "comm_mae_h": 1.0, "coverage": 0.10}  # non-inferiority (PREREG §7)
AREA_FLOOR_HA = 10.0


def _nz(v) -> float:
    return 0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def boot_fire(diffs: dict[tuple, float], seed: int = RNG_SEED, n: int = N_BOOT) -> dict:
    """Mean paired difference over fire-days; 95 % CI resampling whole fires."""
    by_fire: dict[str, list[float]] = defaultdict(list)
    for (fid, _), d in diffs.items():
        by_fire[fid].append(d)
    fires = sorted(by_fire)
    if not fires:
        return {"n_days": 0, "n_fires": 0, "mean": math.nan, "lo": math.nan, "hi": math.nan}
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        pick = rng.integers(0, len(fires), len(fires))
        vals = [v for k in pick for v in by_fire[fires[k]]]
        means.append(np.mean(vals))
    allv = [v for f in fires for v in by_fire[f]]
    return {"n_days": len(allv), "n_fires": len(fires), "mean": float(np.mean(allv)),
            "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5))}


def boot_fire_seed(vals: dict[tuple, dict[int, float]], seed: int = RNG_SEED,
                   n: int = N_BOOT) -> dict:
    """Two-stage bootstrap: fires, then seeds within each drawn fire-day."""
    by_fire: dict[str, list[dict[int, float]]] = defaultdict(list)
    for (fid, _), s in vals.items():
        by_fire[fid].append(s)
    fires = sorted(by_fire)
    if not fires:
        return {"n_days": 0, "n_fires": 0, "mean": math.nan, "lo": math.nan, "hi": math.nan}
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(n):
        out = []
        for k in rng.integers(0, len(fires), len(fires)):
            for s in by_fire[fires[k]]:
                ks = list(s)
                out += [s[ks[j]] for j in rng.integers(0, len(ks), len(ks))]
        means.append(np.mean(out))
    allv = [np.mean(list(s.values())) for f in fires for s in by_fire[f]]
    return {"n_days": len(allv), "n_fires": len(fires), "mean": float(np.mean(allv)),
            "lo": float(np.percentile(means, 2.5)), "hi": float(np.percentile(means, 97.5))}


def inside_p10_p90(member_ha: list[float], obs_ha: float) -> float:
    m = np.log10(np.asarray(member_ha, dtype=float) + AREA_FLOOR_HA)
    o = math.log10(obs_ha + AREA_FLOOR_HA)
    return float(np.percentile(m, 10) <= o <= np.percentile(m, 90))


def verdict(diff: dict, margin: float, higher_better: bool) -> str:
    lo, hi = diff["lo"], diff["hi"]
    if not np.isfinite(lo):
        return "not estimable"
    if not higher_better:  # turn into "FireSim better = positive"
        lo, hi = -hi, -lo
    if lo > 0:
        return "FireSim better (CI excludes 0)"
    if lo > -margin:
        return "FireSim not worse than WISE by the margin (non-inferior)"
    if hi < 0:
        return "FireSim worse (CI excludes 0)"
    return "inconclusive (CI includes 0 and crosses the margin)"


def analyse(rows: list[dict], fire_days: set | None, sensitivity: bool) -> dict:
    det = {(r["fire_id"], r["day"]): r for r in rows if r.get("kind") == "det"}
    ens = defaultdict(dict)
    for r in rows:
        if r.get("kind") == "ens":
            ens[(r["fire_id"], r["day"])][r["seed"]] = r
    keys = sorted(det) if fire_days is None else sorted(k for k in det if k in fire_days)
    res: dict = {"set_size": len(fire_days) if fire_days else None, "n_det_rows": len(keys)}
    missing = sorted(fire_days - set(det)) if fire_days else []
    res["missing_det"] = [f"{a}:{b}" for a, b in missing]

    f1d, f1_fs, f1_w, cmd, cfs, cw, n_fail = {}, [], [], {}, [], [], 0
    comm_counts = defaultdict(int)
    for k in keys:
        r = det[k]
        ok = r.get("wise", {}).get("ok") and "wise_scores" in r
        if not ok:
            n_fail += 1
            if not sensitivity:
                continue
        a = _nz(r["firesim"]["17h"]["f1"])
        b = _nz(r["wise_scores"]["17h"]["f1"]) if ok else 0.0
        f1d[k] = a - b
        f1_fs.append(a)
        f1_w.append(b)
        fc = r["firesim_comm"]
        if ok:
            wc = r["wise_comm"]
        else:  # no growth: the community is never reached
            wc = {"mae_h": float(np.mean([abs(min(x["obs_h"], 17.0) - 17.0)
                                          for x in fc["rows"] if not x["untimed"]]))
                  if fc["n_timed"] else math.nan}
        if fc["n_timed"] and np.isfinite(fc["mae_h"]) and np.isfinite(wc["mae_h"]):
            cmd[k] = fc["mae_h"] - wc["mae_h"]
            cfs.append(fc["mae_h"])
            cw.append(wc["mae_h"])
        comm_counts["eligible"] += fc["n_eligible"]
        comm_counts["obs_reached"] += fc["n_obs_reached"]
        comm_counts["untimed"] += fc["n_untimed"]
        comm_counts["firesim_reached"] += fc["n_model_reached"]
        comm_counts["firesim_false"] += fc["n_false_arrival"]
        if ok:
            comm_counts["wise_reached"] += r["wise_comm"]["n_model_reached"]
            comm_counts["wise_false"] += r["wise_comm"]["n_false_arrival"]
    res["wise_failures"] = n_fail
    res["f1_17h"] = {"firesim": float(np.mean(f1_fs)) if f1_fs else math.nan,
                     "wise": float(np.mean(f1_w)) if f1_w else math.nan,
                     "diff": boot_fire(f1d)}
    res["f1_17h"]["verdict"] = verdict(res["f1_17h"]["diff"], MARGINS["f1_17h"], True)
    res["comm_mae_h"] = {"firesim": float(np.mean(cfs)) if cfs else math.nan,
                         "wise": float(np.mean(cw)) if cw else math.nan,
                         "diff": boot_fire(cmd), "counts": dict(comm_counts)}
    res["comm_mae_h"]["verdict"] = verdict(res["comm_mae_h"]["diff"], MARGINS["comm_mae_h"], False)

    cov, cfs_, cw_ = {}, [], []
    n_ens_fail = 0
    for k, seeds in ens.items():
        if fire_days is not None and k not in fire_days:
            continue
        per = {}
        for s, r in seeds.items():
            a = inside_p10_p90(r["firesim_ens"]["member_ha"], r["firesim_ens"]["obs_ha"])
            if r.get("wise_all_ok") and "wise_ens" in r:
                b = inside_p10_p90(r["wise_ens"]["member_ha"], r["wise_ens"]["obs_ha"])
            else:
                n_ens_fail += 1
                if not sensitivity:
                    continue
                b = 0.0
            per[s] = a - b
            cfs_.append(a)
            cw_.append(b)
        if per:
            cov[k] = per
    res["coverage"] = {"firesim": float(np.mean(cfs_)) if cfs_ else math.nan,
                       "wise": float(np.mean(cw_)) if cw_ else math.nan,
                       "n_day_seeds": len(cfs_), "wise_failures": n_ens_fail,
                       "diff": boot_fire_seed(cov)}
    res["coverage"]["verdict"] = verdict(res["coverage"]["diff"], MARGINS["coverage"], True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs")
    ap.add_argument("--set", help="the registered fire-day set file (FIRE_ID:DAY per line)")
    ap.add_argument("--ens-set", help="the registered ensemble fire-day set file")
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [json.loads(x) for x in Path(a.runs).read_text().splitlines() if x.strip()]

    def read_set(p):
        if not p:
            return None
        return {(x.split(":")[0], int(x.split(":")[1])) for x in Path(p).read_text().split()
                if x and not x.startswith("#")}

    s = read_set(a.set)
    es = read_set(a.ens_set)
    out = {}
    for label, sens in (("primary", False), ("sensitivity_wise_fail_as_no_growth", True)):
        r = analyse(rows, s, sens)
        if es is not None:
            r["coverage"] = analyse(rows, es, sens)["coverage"]
        out[label] = r
    out["margins"] = MARGINS
    out["n_boot"], out["rng_seed"] = N_BOOT, RNG_SEED
    txt = json.dumps(out, indent=1, default=float)
    if a.json:
        Path(a.json).write_text(txt)
    for label in ("primary", "sensitivity_wise_fail_as_no_growth"):
        r = out[label]
        print(f"== {label}: {r['n_det_rows']} fire-days, WISE failures {r['wise_failures']}")
        for m in ("f1_17h", "comm_mae_h", "coverage"):
            d = r[m]["diff"]
            print(f"  {m}: FireSim {r[m]['firesim']:.3f}  WISE {r[m]['wise']:.3f}  "
                  f"diff {d['mean']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}] "
                  f"(n={d['n_days']} days / {d['n_fires']} fires)  -> {r[m]['verdict']}")


if __name__ == "__main__":
    main()
