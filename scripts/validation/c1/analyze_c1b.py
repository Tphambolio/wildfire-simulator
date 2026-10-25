#!/usr/bin/env python3
"""C1b analysis: community arrival-time error, FireSim vs W.I.S.E. (PREREG_COMPARATOR_C1B §5-§7).

Reads the run rows (``run_c1.py`` output) and their arrival grids, rebuilds each fire-day's case
from the C1b data root (observations only), computes the interval-censored arrival error
(``communities_c1b``) for both models and the paired difference FireSim - WISE per fire-day,
95 % percentile bootstrap of whole fires (2,000 draws, default_rng(20261025)), margin 1 h.

    FIRESIM_VALIDATION_DATA=$FIRESIM_VALIDATION_DATA/c1b \\
    FIRESIM_VALIDATION_DOMAINS=$FIRESIM_VALIDATION_DATA/c1b/domains \\
    python3 scripts/validation/c1/analyze_c1b.py RUNS.jsonl --set sets/community_c1b_primary.txt \\
        [--calibration sets/community_c1b_calibration.txt] --json OUT.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1]), str(HERE.parents[2] / "engine/src")]

import analyze_c1 as A  # noqa: E402
import communities_c1b as CB  # noqa: E402

MARGIN_H = 1.0
RNG_SEED = 20261025


def read_set(p):
    return [(a, int(b)) for a, b in (x.split(":") for x in Path(p).read_text().split() if x)]


def score(rows_by_key, keys, arrivals_dir: Path, sensitivity: bool) -> dict:
    from validate import _load_fire

    from firesim.validation.harness import build_case

    per, out_rows, n_fail = {}, [], 0
    for fid, day in keys:
        r = rows_by_key.get((fid, day))
        if r is None:
            out_rows.append({"fire_day": f"{fid}:{day}", "status": "not run"})
            continue
        z = np.load(arrivals_dir / f"{fid}_{day}_det.npz")
        dom, groups, rec = _load_fire(fid)
        case = build_case(dom, day, groups, rec, start_hour=6, hours=24, margin_m=20000.0)
        reached, unreached = CB.observed_intervals(case)
        fs = z["firesim"]
        start = fs <= 0
        f = CB.model_errors(reached, unreached, fs, start)
        ok = "wise" in z.files and r["wise"]["ok"]
        if ok:
            w = CB.model_errors(reached, unreached, z["wise"], start)
        else:
            n_fail += 1
            if not sensitivity:
                out_rows.append({"fire_day": f"{fid}:{day}", "status": "WISE failed", "firesim": f})
                continue
            w = CB.model_errors(reached, unreached, np.where(start, 0.0, np.inf), start)
        if f["n"] == 0:
            continue
        per[(fid, day)] = f["mae_h"] - w["mae_h"]
        out_rows.append({"fire_day": f"{fid}:{day}", "status": "ok" if ok else "WISE failed (no growth)",
                         "firesim": f, "wise": w,
                         "f1_17h": {"firesim": A._nz(r["firesim"]["17h"]["f1"]),
                                    "wise": A._nz(r["wise_scores"]["17h"]["f1"]) if ok else 0.0}})
    d = A.boot_fire(per, seed=RNG_SEED)
    ok_rows = [x for x in out_rows if "wise" in x]
    res = {"diff": d, "wise_failures": n_fail,
           "firesim_mae_h": float(np.mean([x["firesim"]["mae_h"] for x in ok_rows])) if ok_rows else math.nan,
           "wise_mae_h": float(np.mean([x["wise"]["mae_h"] for x in ok_rows])) if ok_rows else math.nan,
           "n_communities": int(sum(x["firesim"]["n"] for x in ok_rows)),
           "hits": {"firesim": int(sum(x["firesim"]["hits"] for x in ok_rows)),
                    "wise": int(sum(x["wise"]["hits"] for x in ok_rows))},
           "false_arrivals": {"firesim": int(sum(x["firesim"]["false_arrivals"] for x in ok_rows)),
                              "wise": int(sum(x["wise"]["false_arrivals"] for x in ok_rows))},
           "f1_17h": {"firesim": float(np.mean([x["f1_17h"]["firesim"] for x in ok_rows])) if ok_rows else math.nan,
                      "wise": float(np.mean([x["f1_17h"]["wise"] for x in ok_rows])) if ok_rows else math.nan},
           "rows": out_rows}
    res["verdict"] = A.verdict(d, MARGIN_H, False)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs")
    ap.add_argument("--set", required=True)
    ap.add_argument("--calibration")
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [json.loads(x) for x in Path(a.runs).read_text().splitlines() if x.strip()]
    by = {(r["fire_id"], r["day"]): r for r in rows if r.get("kind") == "det"}
    adir = Path(a.runs).parent / "arrivals"
    out = {"margin_h": MARGIN_H, "rng_seed": RNG_SEED,
           "primary": score(by, read_set(a.set), adir, False),
           "sensitivity_wise_fail_as_no_growth": score(by, read_set(a.set), adir, True)}
    if a.calibration:
        out["calibration_descriptive"] = score(by, read_set(a.calibration), adir, False)
    txt = json.dumps(out, indent=1, default=float)
    if a.json:
        Path(a.json).write_text(txt)
    for k in ("primary", "sensitivity_wise_fail_as_no_growth", "calibration_descriptive"):
        if k in out:
            r = out[k]
            d = r["diff"]
            print(f"{k}: FireSim {r['firesim_mae_h']:.2f} h WISE {r['wise_mae_h']:.2f} h diff "
                  f"{d['mean']:+.2f} [{d['lo']:+.2f}, {d['hi']:+.2f}] (n={d['n_days']} days / "
                  f"{d['n_fires']} fires; {r['n_communities']} communities) -> {r['verdict']}")


if __name__ == "__main__":
    main()
