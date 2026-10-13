#!/usr/bin/env python3
"""Version comparison figure for the long-range ember models v2 / v3 / v4 (illustrative — not
validated): building-level AUC and Brier skill vs the fire's base rate, per fire, from the saved
result files of each version's validation run (no new model runs).

Inputs (outside the repo, ``~/dev/wildfire/validation-data/calfire-dins/``):
- v2: ``results-long-range-v2-2026-10-11/results_lr2_<fire>.json`` (PRIMARY; PREREG_LR2, [R33])
- v3: ``results-long-range-v3-2026-10-12/train_A/results_lr3_<fire>.json`` (in-sample, 20
  members) and ``.../heldout/results_lr3_<fire>.json`` (PREREG_LR3, [R37])
- v4: ``results-long-range-v4-2026-10-12/training_final/results_lr4_<fire>.json`` (in-sample,
  20 members) and ``.../heldout/results_lr4_<fire>.json`` (PREREG_LR4, [R39])
Output: ``fig_version_comparison.png`` and ``version_comparison.csv`` in ``--out``.

Usage: python3 scripts/long_range_version_comparison.py --out <report data dir>
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

B = Path.home() / "dev/wildfire/validation-data/calfire-dins"
SRC = {
    "v2": {f: (B / f"results-long-range-v2-2026-10-11/results_lr2_{f}.json", "PRIMARY")
           for f in ("eaton", "palisades", "camp")},
    "v3": {**{f: (B / f"results-long-range-v3-2026-10-12/train_A/results_lr3_{f}.json", "V3")
              for f in ("eaton", "palisades", "camp")},
           **{f: (B / f"results-long-range-v3-2026-10-12/heldout/results_lr3_{f}.json", "V3")
              for f in ("tubbs", "woolsey")}},
    "v4": {**{f: (B / f"results-long-range-v4-2026-10-12/training_final/results_lr4_{f}.json", "V4")
              for f in ("eaton", "palisades", "camp", "tubbs", "woolsey")},
           **{f: (B / f"results-long-range-v4-2026-10-12/heldout/results_lr4_{f}.json", "V4")
              for f in ("glass", "czu", "northcomplex")}},
}
HELD = {"v2": {"palisades"}, "v3": {"tubbs", "woolsey"}, "v4": {"glass", "czu", "northcomplex"}}
FIRES = ["eaton", "palisades", "camp", "tubbs", "woolsey", "glass", "czu", "northcomplex"]
COL = {"v2": "#94a3b8", "v3": "#64748b", "v4": "#c2410c"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = []
    for ver, fires in SRC.items():
        for f, (p, key) in fires.items():
            if not p.exists():
                continue
            s = json.loads(p.read_text())["scores"][key]
            rows.append({"version": ver, "fire": f, "role": "held-out" if f in HELD[ver] else "training/in-sample",
                         "auc": s["auc"], "brier": s["brier"], "bss_vs_base_rate": s["bss_vs_climatology"]})
    with open(a.out / "version_comparison.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.4))
    width = 0.27
    for j, ver in enumerate(("v2", "v3", "v4")):
        for ax, key in zip(axs, ("auc", "bss_vs_base_rate")):
            xs, ys, hatch = [], [], []
            for i, f in enumerate(FIRES):
                r = [x for x in rows if x["version"] == ver and x["fire"] == f]
                if r:
                    xs.append(i + (j - 1) * width)
                    ys.append(max(r[0][key], -1.5) if key != "auc" else r[0][key])
                    hatch.append("//" if r[0]["role"] == "held-out" else "")
            bars = ax.bar(xs, ys, width * 0.95, color=COL[ver], label=ver)
            for b_, h in zip(bars, hatch):
                b_.set_hatch(h)
    axs[0].axhline(0.5, color="k", lw=0.8, ls="--")
    axs[0].set_ylim(0.3, 0.75)
    axs[0].set_ylabel("AUC (building level; 0.5 = no discrimination)")
    axs[1].axhline(0.0, color="k", lw=0.8)
    axs[1].set_ylabel("Brier skill vs the fire's base rate (clipped at −1.5)")
    for ax in axs:
        ax.set_xticks(range(len(FIRES)))
        ax.set_xticklabels(FIRES, rotation=30, fontsize=8)
    axs[0].legend(title="hatched = held-out", fontsize=8)
    fig.suptitle("Long-range ember versions v2 / v3 / v4 per fire (training in-sample vs held-out)", fontsize=11)
    fig.text(0.995, 0.002, "Illustrative — not validated", ha="right", va="bottom", fontsize=8, color="#b91c1c")
    fig.savefig(a.out / "fig_version_comparison.png", dpi=150, bbox_inches="tight")
    print(len(rows), "rows")


if __name__ == "__main__":
    main()
