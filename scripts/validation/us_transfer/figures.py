"""Figures for the R71 report from ``analyse.py`` output (results.json, per_fireday.csv).

Purpose: five figures, captions in the report: (1) baseline log-area bias, US vs Canada development fires
through the same pipeline, with the spent R67 sealed fires described; (2) paired one-factor effects with
fire-bootstrap CIs; (3) where the US modelled growth lands, by LANDFIRE FBFM40 group; (4) suppression /
fire-stage stratification; (5) scoring-geometry checks.

Usage: ``python3 -m scripts.validation.us_transfer.figures <out_dir>``
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .analyse import LABELS, SEALED_DET, L  # noqa: E402

US, CAN, SEAL = "#c0392b", "#2471a3", "#7d3c98"


def main():
    out = Path(sys.argv[1])
    res = json.loads((out / "results.json").read_text())
    rows = list(csv.DictReader(open(out / "per_fireday.csv")))
    # fig 1
    fig, ax = plt.subplots(figsize=(7.5, 4))
    bins = np.linspace(-2, 3.5, 34)
    for reg, col in (("US", US), ("CAN", CAN)):
        v = [float(r["L"]) for r in rows if r["region"] == reg]
        ax.hist(v, bins=bins, histtype="step", lw=2, color=col, density=True,
                label=f"{reg} development (n={len(v)}), mean {np.mean(v):+.2f}")
    if SEALED_DET.exists():
        sv = []
        for x in SEALED_DET.read_text().splitlines():
            r = json.loads(x)
            if "error" not in r:
                h = r["members"]["det"]["17h"]
                sv.append(L(h["area_pred"] / 1e4, h["area_obs"] / 1e4))
        ax.hist(sv, bins=bins, histtype="step", lw=2, ls="--", color=SEAL, density=True,
                label=f"R67 sealed 2026 (described only, n={len(sv)}), mean {np.mean(sv):+.2f}")
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("L = log10(modelled + 10 ha) - log10(observed + 10 ha), 17 h growth")
    ax.set_ylabel("density of fire-days")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(out / "fig1_baseline_bias.png", dpi=150); plt.close(fig)
    # fig 2
    var = res["variants"]
    names = sorted(var, key=lambda v: var[v].get("US", var[v].get("CAN"))["dL"][0])
    fig, ax = plt.subplots(figsize=(8, 0.38 * len(names) + 1.5))
    for i, v in enumerate(names):
        for reg, col, off in (("US", US, -0.15), ("CAN", CAN, 0.15)):
            if reg in var[v]:
                p, lo, hi = var[v][reg]["dL"][:3]
                ax.errorbar(p, i + off, xerr=[[p - lo], [hi - p]], fmt="o", color=col, ms=4, capsize=2,
                            label=reg if i == 0 else None)
    b = res["baseline"]["US"]["mean_L"][0]
    ax.axvline(0, color="k", lw=0.8)
    ax.axvline(-b, color=US, ls=":", lw=1, label=f"removes all US bias ({-b:+.2f})")
    ax.set_yticks(range(len(names)), [LABELS[v] for v in names], fontsize=8)
    ax.set_xlabel("paired change in L (variant - base), mean over fire-days, 95 % fire-bootstrap CI")
    h, lab = ax.get_legend_handles_labels()
    ax.legend(h, lab, fontsize=8, loc="lower right")
    fig.tight_layout(); fig.savefig(out / "fig2_paired_effects.png", dpi=150); plt.close(fig)
    # fig 3
    fg = res["fuel_groups_US"]
    gs = [g for g in ("GR", "GS", "SH", "TU", "TL", "SB", "NB1", "NB8", "NB") if g in fg["pred_ha"] or g in fg["obs_ha"]]
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    x = np.arange(len(gs))
    ax.bar(x - 0.2, [fg["obs_ha"].get(g, 0) for g in gs], 0.4, color="#7f8c8d", label="observed 17 h growth")
    ax.bar(x + 0.2, [fg["pred_ha"].get(g, 0) for g in gs], 0.4, color=US, label="modelled 17 h growth (base)")
    for i, g in enumerate(gs):
        r = fg["pred_over_obs"].get(g)
        if r:
            ax.text(i, max(fg["obs_ha"].get(g, 0), fg["pred_ha"].get(g, 0)) * 1.15, f"x{r:.1f}", ha="center", fontsize=8)
    ax.set_yscale("log"); ax.set_xticks(x, gs); ax.set_ylabel("area summed over US fire-days (ha)")
    ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig3_fuel_groups.png", dpi=150); plt.close(fig)
    # fig 4
    sup = res["suppression"]
    keys = ["stage<0.25", "0.25-0.6", "stage>=0.6", "before_containment", "on_or_after_containment"]
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    for reg, col, off in (("US", US, -0.12), ("CAN", CAN, 0.12)):
        for i, k in enumerate(keys):
            if reg in sup and k in sup[reg] and sup[reg][k][3] > 0:
                p, lo, hi, n, nf = sup[reg][k]
                ax.errorbar(i + off, p, yerr=[[p - lo], [hi - p]], fmt="o", color=col, capsize=2,
                            label=reg if i == 0 else None)
                ax.text(i + off, hi + 0.05, f"{n}", fontsize=7, ha="center", color=col)
    ax.set_xticks(range(len(keys)), ["< 25 % burned\nbefore day", "25-60 %", ">= 60 %", "before WFIGS\ncontainment",
                                     "on/after\ncontainment"], fontsize=8)
    ax.axhline(0, color="k", lw=0.8); ax.set_ylabel("mean L (17 h)"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig4_suppression_stage.png", dpi=150); plt.close(fig)
    # fig 5
    geo = res["geometry"]
    gk = [("L_base", "base (NN fill in final perimeter)"), ("L_pred_clipped_to_final_footprint", "model clipped to final footprint"),
          ("L_vs_detected_only_obs", "obs = detected cells only")]
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    for reg, col, off in (("US", US, -0.12), ("CAN", CAN, 0.12)):
        for i, (k, _) in enumerate(gk):
            if reg in geo and k in geo[reg]:
                p, lo, hi = geo[reg][k][:3]
                ax.errorbar(i + off, p, yerr=[[p - lo], [hi - p]], fmt="o", color=col, capsize=2, label=reg if i == 0 else None)
    if "feds" in var and "US" in var["feds"]:
        p, lo, hi = var["feds"]["US"]["dL"][:3]
        b0 = res["baseline"]["US"]["mean_L"][0]
        ax.errorbar(len(gk) - 0.12, b0 + p, yerr=[[p - lo], [hi - p]], fmt="s", color=US, capsize=2)
        gk = gk + [("feds", "DOB from FEDS (base + paired change; US 2018-21)")]
    ax.set_xticks(range(len(gk)), [t for _, t in gk], fontsize=7)
    ax.axhline(0, color="k", lw=0.8); ax.set_ylabel("mean L (17 h)"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig5_scoring_geometry.png", dpi=150); plt.close(fig)
    print("figures written to", out)


if __name__ == "__main__":
    main()
