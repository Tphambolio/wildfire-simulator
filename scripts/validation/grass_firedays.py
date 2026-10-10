"""How much O-1 grass is in play on each CFSDS validation fire-day (for the curing check).

For every fire-day in the manifest: the share of the observed day's growth on O-1a/O-1b fuel,
the share of O-1 among burnable cells within ``--near-m`` of the fire's starting area (fuel the
day's run can reach), and the season (FireSim's pre-green-up window, green season, other).
Writes a CSV; prints a summary. Uses the prepared domains only (no runs, no outcome tuning).

    PYTHONPATH=engine/src python scripts/validation/grass_firedays.py --out grass_firedays.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import numpy as np
from scipy import ndimage

from firesim.data.fuel_loader import CODE_SCHEMES
from firesim.fbp.curing import in_spring_curing_window
from firesim.fbp.constants import FuelType
from firesim.validation.cfsds import FireDomain

ROOT = Path(os.environ.get("FIRESIM_VALIDATION_DATA", "/home/rpas/dev/wildfire/validation-data"))
GREENUP_DOY, LEAFOFF_DOY = 150, 258  # RunOptions defaults


def season(doy: int) -> str:
    if GREENUP_DOY <= doy < LEAFOFF_DOY:
        return "green"
    return "spring window" if in_spring_curing_window(doy) else "other dormant"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--near-m", type=float, default=2000.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    rows = []
    for m in manifest:
        dom = FireDomain.load(ROOT / f"domains/{m['fire_id']}.npz")
        lut = CODE_SCHEMES[dom.fuel_scheme]
        codes = np.unique(dom.fuel)
        o1 = np.isin(dom.fuel, [c for c in codes if lut.get(int(c)) in (FuelType.O1a, FuelType.O1b)])
        fuel = np.isin(dom.fuel, [c for c in codes if lut.get(int(c)) is not None])
        n_cells = max(1, int(round(args.near_m / min(dom.dx, dom.dy))))
        for day in m["days"]:
            growth = dom.dob == day
            before = (dom.dob > 0) & (dom.dob < day)
            near = ndimage.binary_dilation(before, iterations=n_cells) & ~before & fuel
            rows.append({
                "fire_id": m["fire_id"], "day": day, "season": season(day),
                "growth_cells": int(growth.sum()),
                "o1_growth_cells": int((growth & o1).sum()),
                "o1_growth_share": float((growth & o1).sum() / max(1, growth.sum())),
                "o1_near_share": float((near & o1).sum() / max(1, near.sum())),
            })
    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    n = len(rows)
    any_growth = [r for r in rows if r["o1_growth_cells"] > 0]
    tot = sum(r["growth_cells"] for r in rows)
    tot_o1 = sum(r["o1_growth_cells"] for r in rows)
    print(f"{n} fire-days; O-1 in observed growth on {len(any_growth)} "
          f"({100 * len(any_growth) / n:.0f} %); O-1 share of all observed growth "
          f"{100 * tot_o1 / max(1, tot):.2f} %")
    for thr in (0.01, 0.05, 0.10):
        k = sum(r["o1_growth_share"] >= thr for r in rows)
        print(f"  O-1 >= {100 * thr:.0f} % of the day's growth: {k} fire-days")
    for s in ("spring window", "green", "other dormant"):
        rs = [r for r in rows if r["season"] == s]
        if rs:
            print(f"  {s}: {len(rs)} fire-days, {sum(r['o1_growth_cells'] > 0 for r in rs)} with "
                  f"O-1 growth, median O-1 share within {args.near_m:g} m "
                  f"{100 * float(np.median([r['o1_near_share'] for r in rs])):.1f} %")


if __name__ == "__main__":
    main()
