#!/usr/bin/env python3
"""figures_stage2.py -- figures for the stage-2 confirmation result (PREREG_ML_EMBER section 12; report [R68]).

Reads only the confirmation outputs in reports/data/ml-ember-cast-2026-10-29/stage2/ (confirmation.json,
per_fire.csv, ranks_per_jump.csv) written once by confirm.py score; opens no pool file. Writes stage2/figures/:
  fig1_af.png        per-fire a_f for RUNG2 - M8 and RUNG2 - DIST against K_f, with S3 and its 95 % interval
  fig2_auc.png       within-interval AUC by model and region (M8, B1, ELMFIRE, HYBRID, DIST, RUNG2)
  fig3_strata.png    AUC by distance band and by gap class for M8, DIST, RUNG2
  figures.json       the plotted series (captions live in the report and the data folder README)
Usage: python -I scripts/ml_ember/figures_stage2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.ml_ember import paths as P  # noqa: E402

S2 = P.DATA_FOLDER / "stage2"
OUT = S2 / "figures"


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    OUT.mkdir(exist_ok=True)
    R = json.loads((S2 / "confirmation.json").read_text())
    pf = pd.read_csv(S2 / "per_fire.csv"); rj = pd.read_csv(S2 / "ranks_per_jump.csv")
    series = {}
    # fig 1: per-fire a_f
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.0), dpi=150, sharey=True)
    for ax, (a, b, key) in zip(axes, [("RUNG2", "M8", "RQ1a"), ("RUNG2", "DIST", "RQ1b")]):
        d = R["deciding"][key]
        af = pf[f"auc_{a}"] - pf[f"auc_{b}"]
        for reg, col in (("CAN", "#d62728"), ("US", "#1f77b4")):
            m = pf.region == reg
            ax.scatter(pf.K[m], af[m], s=18, color=col, alpha=0.75, label=reg)
        ax.axhline(0, color="k", lw=0.6)
        ax.axhline(d["S3"], color="k", lw=1.2)
        ax.axhspan(d["boot95"][0], d["boot95"][1], color="grey", alpha=0.25)
        ax.set_xscale("log"); ax.set_xlabel("ranked jumps in the fire, K_f (log)")
        ax.set_title(f"{key}: {a} − {b}: S3 {d['S3']:+.3f} [{d['boot95'][0]:+.3f}, {d['boot95'][1]:+.3f}], p {d['p_signflip']:.1e}", fontsize=8.5)
        ax.grid(alpha=0.3)
        series[key] = dict(K=pf.K.tolist(), a_f=af.round(4).tolist(), region=pf.region.tolist(), S3=d["S3"], ci=d["boot95"])
    axes[0].set_ylabel("a_f = mean within-interval rank difference"); axes[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "fig1_af.png"); plt.close(fig)
    # fig 2: AUC by model and region
    models = ["B1", "M8", "ELMFIRE", "HYBRID", "DIST", "RUNG2"]
    fig, ax = plt.subplots(figsize=(7.6, 4.0), dpi=150)
    x = np.arange(len(models)); w = 0.27
    for i, (grp, col) in enumerate((("CAN", "#d62728"), ("US", "#1f77b4"), ("All", "#444444"))):
        vals = [R["auc"][m]["by_region"].get(grp, np.nan) if grp != "All" else R["auc"][m]["AUC"] for m in models]
        ax.bar(x + (i - 1) * w, vals, w, color=col, label=grp)
        series.setdefault("auc", {})[grp] = dict(zip(models, [float(v) for v in vals]))
    ax.axhline(0.5, color="k", ls=":", lw=0.7); ax.axhline(0.6, color="k", ls="--", lw=0.7)
    ax.set_xticks(x); ax.set_xticklabels([m + ("\n(52 fires)" if m == "ELMFIRE" else "") for m in models], fontsize=8)
    ax.set_ylim(0.4, 1.0); ax.set_ylabel("within-interval AUC (S3-weighted)"); ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)
    ax.set_title("Confirmation set: 73 fires, 159 ranked jumps (ELMFIRE on its subset)", fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "fig2_auc.png"); plt.close(fig)
    # fig 3: strata
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.8), dpi=150, sharey=True)
    for ax, strat, order in zip(axes, ("dist_band_km", "gap"), (["1-3", "3-7", "7-15"], ["unburned", "later_detected", "never_detected"])):
        S = R["strata"][strat]; xs = np.arange(len(order))
        for i, (m, col) in enumerate((("M8", "#d62728"), ("DIST", "#2ca02c"), ("RUNG2", "#1f77b4"))):
            vals = [S[o][f"AUC_{m}"] for o in order]
            ax.bar(xs + (i - 1) * 0.27, vals, 0.27, color=col, label=m)
            series.setdefault("strata", {}).setdefault(strat, {})[m] = dict(zip(order, [float(v) for v in vals]))
        ax.set_xticks(xs); ax.set_xticklabels([f"{o}\n(n={S[o]['n']})" for o in order], fontsize=8)
        ax.axhline(0.5, color="k", ls=":", lw=0.7); ax.set_ylim(0.3, 1.0); ax.grid(axis="y", alpha=0.3)
        ax.set_title("by jump distance beyond the start perimeter (km)" if strat == "dist_band_km" else "by gap class", fontsize=9)
    axes[0].set_ylabel("within-interval AUC"); axes[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(OUT / "fig3_strata.png"); plt.close(fig)
    (OUT / "figures.json").write_text(json.dumps(series, indent=1))
    print("wrote", sorted(p.name for p in OUT.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
