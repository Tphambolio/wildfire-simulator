"""Paired comparison of validation runs by fire-day stratum (season, O-1 grass share).

Reads run files (``scripts/validate.py run``) and the per-fire-day grass table
(``scripts/validation/grass_firedays.py``); for each stratum and fire split reports n, mean F1,
mean normalised area difference and the paired ΔF1 against a reference run with a 95 % CI
from a bootstrap over fires (as ``validate.py compare``). Used for the grass curing check
(decision M1, 2026-10-10).

    PYTHONPATH=engine/src python scripts/validation/compare_by_stratum.py \\
        --runs-dir $FIRESIM_VALIDATION_DATA/curing/runs --grass grass_firedays.csv \\
        --ref harness_90_60 old_default_60 m1_spring95 m1_spring95_summer90
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from firesim.validation.report import split_fires


def load(runs_dir: Path, name: str, window: str) -> dict:
    out = {}
    for line in (runs_dir / f"{name}.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "error" in r:
            continue
        m = r["members"].get("det") or next(iter(r["members"].values()))
        if window in m:
            out[(r["fire_id"], r["day"])] = m[window]
    return out


def boot(diffs: dict, n_boot: int = 2000, seed: int = 1) -> tuple[float, float, float]:
    by_fire: dict[str, list[float]] = {}
    for (fid, _), d in diffs.items():
        by_fire.setdefault(fid, []).append(d)
    fires = sorted(by_fire)
    rng = np.random.default_rng(seed)
    means = [np.mean([v for k in rng.choice(len(fires), len(fires)) for v in by_fire[fires[k]]])
             for _ in range(n_boot)]
    allv = [v for f in fires for v in by_fire[f]]
    return float(np.mean(allv)), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--grass", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--window", default="17h")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rd = Path(args.runs_dir)
    grass = {(r["fire_id"], int(r["day"])): r for r in csv.DictReader(open(args.grass))}
    runs = {n: load(rd, n, args.window) for n in [args.ref, *args.runs]}
    common = set.intersection(*(set(v) for v in runs.values()))
    split = split_fires({k[0] for k in common})
    strata = {
        "all": lambda g: True,
        "spring window (DOY 60-149)": lambda g: g["season"] == "spring window",
        "green season (DOY 150-257)": lambda g: g["season"] == "green",
        "other dormant": lambda g: g["season"] == "other dormant",
        "O-1 in observed growth (any)": lambda g: int(g["o1_growth_cells"]) > 0,
        "O-1 >= 5 % of observed growth": lambda g: float(g["o1_growth_share"]) >= 0.05,
        "no O-1 in observed growth": lambda g: int(g["o1_growth_cells"]) == 0,
    }
    lines = [f"Window {args.window}; reference {args.ref}; {len(common)} common fire-days.", "",
             "| stratum | set | n | run | F1 mean | area diff | ΔF1 vs ref [95 % CI, by fire] |",
             "|---|---|---|---|---|---|---|"]
    for sname, pred in strata.items():
        for side in ("test", "calibration", "all"):
            keys = [k for k in common if pred(grass[k]) and (side == "all" or split[k[0]] == side)]
            if not keys:
                continue
            for n in [args.ref, *args.runs]:
                f1 = np.array([np.nan_to_num(runs[n][k]["f1"], nan=0.0) for k in keys])
                ad = np.array([runs[n][k]["area_diff_norm"] for k in keys], dtype=float)
                delta = "–"
                if n != args.ref:
                    d = {k: np.nan_to_num(runs[n][k]["f1"], nan=0.0)
                         - np.nan_to_num(runs[args.ref][k]["f1"], nan=0.0) for k in keys}
                    m, lo, hi = boot(d)
                    delta = f"{m:+.4f} [{lo:+.4f}, {hi:+.4f}]"
                lines.append(f"| {sname} | {side} | {len(keys)} | {n} | {f1.mean():.4f} | "
                             f"{np.nanmean(ad):+.3f} | {delta} |")
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
