#!/usr/bin/env python3
"""Long-range embers v7: figures for the calibrated-front report (PNG, 150 dpi; aggregate only).

Purpose: draw the figures of *Long-range embers v7 calibrated front 2026-10-15*:
  fig_front_calibration_training.png  stage 1: per training fire, community-block arrival MAE and mean
                                      signed error of the v6 front (A0, proxy fuel) vs the calibrated
                                      front at the LOFO θ (fold left out) and at the final θ
  fig_eta_fit_training.png            stage 2: mean J (V7 vs A0c) over the η0 × κ grid, training fires
  fig_arrival_scatter_<set>.png       held-out: ensemble-median block arrival vs the observed interval,
                                      A0v6 / A0c / V7
  fig_skill_A_<set>.png               held-out: A1 and A2 differences with 95 % cluster-bootstrap CIs
  fig_spot_distance_<set>.png         V7 long-range spot-fire distance exceedance vs Storey et al. 2020
  fig_skill_B_<set>.png               held-out: fire-averaged and per-fire AUC of the loss models
Inputs: ``long_range_v7_arrival.py`` / ``long_range_v7_loss.py`` outputs. Run:
  PYTHONPATH=engine/src python3 scripts/long_range_v7_figures.py --train-dir T --heldout-dir H --eta-tag E --out FIG
Status: figures of a pre-registered test (PREREG_LR7) or of the training fit (labelled). No addresses.
References: Storey et al. (2020) Fire 3:10 p.7 (MS74WYUD); Cruz et al. (2012) FEM 284:269 (9PGICYK7).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import long_range_v7_arrival as V  # noqa: E402

C = {"A0v6": "#7f7f7f", "A0c": "#1f78b4", "V7": "#d95f02", "obs": "#1b9e77"}
NAMES = {"A0v6": "v6 front (proxy fuel)", "A0c": "calibrated front", "V7": "calibrated front + embers"}
DPI = 150


def fig_front(train_dir: Path, out: Path):
    fit = json.loads((train_dir / "fit_front.json").read_text())
    fires = list(V.TRAINING)
    x = np.arange(len(fires))
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    v6 = [fit["v6_reference"][f]["mae"] for f in fires]
    lo = [fit["lofo"][f]["mae_out_h"] for f in fires]
    fin = [fit["per_fire_at_final"][f]["mae_h"] for f in fires]
    w = 0.27
    axs[0].bar(x - w, v6, w, color=C["A0v6"], label="v6 A0 (ensemble, proxy fuel)")
    axs[0].bar(x, lo, w, color="#a6cee3", label="calibrated, fire left out (LOFO)")
    axs[0].bar(x + w, fin, w, color=C["A0c"], label="calibrated, final θ (in-sample)")
    axs[0].set_xticks(x, fires)
    axs[0].set_ylabel("block arrival MAE (h)")
    axs[0].legend(fontsize=7)
    axs[0].set_title("Arrival error, training fires")
    b_lo = [fit["lofo"][f]["bias_out_h"] for f in fires]
    b_fin = [fit["per_fire_at_final"][f]["bias_h"] for f in fires]
    axs[1].bar(x - w / 2, b_lo, w, color="#a6cee3", label="LOFO")
    axs[1].bar(x + w / 2, b_fin, w, color=C["A0c"], label="final θ")
    axs[1].axhline(0, color="k", lw=0.8)
    axs[1].set_xticks(x, fires)
    axs[1].set_ylabel("mean signed error (h; + late, − early)")
    axs[1].legend(fontsize=7)
    axs[1].set_title("Bias, training fires")
    fig.tight_layout()
    fig.savefig(out / "fig_front_calibration_training.png", dpi=DPI)
    plt.close(fig)


def fig_eta(train_dir: Path, out: Path):
    fit = json.loads((train_dir / "fit_eta.json").read_text())
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    for k, col in zip(V.GRID_KAPPA, ("#66c2a5", "#fc8d62", "#8da0cb")):
        xs, ys = [], []
        for e0 in V.GRID_ETA0:
            t = f"V7_e{e0:g}_k{k:g}"
            if t in fit["mean_J"]:
                xs.append(e0)
                ys.append(fit["mean_J"][t])
        ax.plot(xs, ys, "o-", color=col, label=f"κ = {k:g} (shrub/timber receptivity)")
    ax.axhline(1.0, color="k", ls="--", lw=0.8, label="J = 1: no change vs calibrated front")
    ax.set_xscale("log")
    ax.set_xlabel("η0 (per-ember establishment efficiency)")
    ax.set_ylabel("mean J over 5 training fires")
    ax.legend(fontsize=7)
    ax.set_title("Stage 2: ember establishment fit (training)")
    fig.tight_layout()
    fig.savefig(out / "fig_eta_fit_training.png", dpi=DPI)
    plt.close(fig)


def fig_scatter(fires, heldout_dir, eta_tag, setname, out, v6_dir):
    class Args:  # noqa: D401 - small namespace
        pass

    a = Args()
    a.out, a.eta_tag, a.members, a.v6_dir = heldout_dir, eta_tag, None, v6_dir
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.4), sharex=True, sharey=True)
    for ax, model in zip(axs, ("A0v6", "A0c", "V7")):
        mk = ("o", "s", "^", "D", "v")
        allv = []
        for f, m in zip(fires, mk):
            runs = V._model_runs(f, model, a)
            if not runs:
                continue
            s = V.score_arrivals(f, runs)
            ob, sc = s["ob"], s["scored"]
            lo, hi, med = ob["t_lo"][sc] / 60, ob["t_hi"][sc] / 60, s["med"][sc] / 60
            mid = 0.5 * (lo + hi)
            ax.errorbar(mid, med, xerr=[mid - lo, hi - mid], fmt=m, ms=3, alpha=0.6, lw=0.5,
                        label=f"{f} (MAE {s['mae_h']:.1f} h)")
            allv += list(hi) + list(med)
        mx = max(allv) if allv else 40
        ax.plot([0, mx], [0, mx], "k--", lw=0.8)
        ax.set_title(NAMES[model])
        ax.set_xlabel("observed arrival interval (h from run start)")
        ax.legend(fontsize=6)
    axs[0].set_ylabel("ensemble-median modelled arrival (h)")
    fig.suptitle(f"Community-block arrival, {setname} held-out fires (540 m blocks)")
    fig.tight_layout()
    fig.savefig(out / f"fig_arrival_scatter_{setname}.png", dpi=DPI)
    plt.close(fig)


def fig_skill_a(scores: dict, setname: str, out: Path):
    rows = []
    for f, r in list(scores["fires"].items()) + [("pooled", scores["pooled"])]:
        for key, lab in (("A1_A0c_minus_A0v6_mae", "A1 MAE: calibrated − v6 (h)"),
                         ("A2_V7_minus_A0c_mae", "A2 MAE: embers − calibrated (h)")):
            if key in r:
                rows.append((f, lab, r[key]["diff"], r[key]["p2.5"], r[key]["p97.5"]))
    labs = sorted({r[1] for r in rows})
    fig, axs = plt.subplots(1, len(labs), figsize=(5.2 * len(labs), 3.8))
    axs = np.atleast_1d(axs)
    for ax, lab in zip(axs, labs):
        rr = [r for r in rows if r[1] == lab]
        y = np.arange(len(rr))
        ax.errorbar([r[2] for r in rr], y, xerr=[[r[2] - r[3] for r in rr], [r[4] - r[2] for r in rr]], fmt="o",
                    color="k", capsize=3)
        ax.axvline(0, color="r", lw=0.8)
        ax.set_yticks(y, [r[0] for r in rr])
        ax.set_title(lab, fontsize=9)
    fig.suptitle(f"Target A differences with 95 % cluster-bootstrap CIs ({setname}; < 0 = better)")
    fig.tight_layout()
    fig.savefig(out / f"fig_skill_A_{setname}.png", dpi=DPI)
    plt.close(fig)


def fig_spot(fires, heldout_dir, eta_tag, setname, out):
    d = []
    for f in fires:
        for m in V.load_runs(heldout_dir / f, f"V7_{eta_tag}"):
            if m.get("lr_spots") is not None and len(m["lr_spots"]):
                d.append(m["lr_spots"][:, 3])
    if not d:
        return
    d = np.concatenate(d)
    xs = np.logspace(2, 4.6, 200)
    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    ax.loglog(xs / 1000, [np.mean(d > x) for x in xs], color=C["V7"], label=f"V7 long-range spot fires (n = {len(d):,})")
    ax.plot([0.1, 1, 3], [0.5, 0.10, 0.02], "ko", label="Storey et al. 2020 (median, 10 % > 1 km, 2 % > 3 km)")
    ax.axvline(13.9, color="k", ls=":", lw=0.8, label="Storey max 13.9 km")
    ax.axvline(33, color="k", ls="--", lw=0.8, label="Cruz et al. 2012: 33 km")
    ax.set_xlabel("distance to the fire at ignition (km)")
    ax.set_ylabel("share of spot fires farther than x")
    ax.legend(fontsize=7)
    ax.set_title(f"Spot-fire distances, {setname} held-out fires")
    fig.tight_layout()
    fig.savefig(out / f"fig_spot_distance_{setname}.png", dpi=DPI)
    plt.close(fig)


def fig_skill_b(loss: dict, setname: str, out: Path):
    models = list(loss["fire_averaged"])
    fires = list(loss["per_fire"])
    fig, ax = plt.subplots(figsize=(7.5, 4))
    x = np.arange(len(models))
    fa = loss["fire_averaged"]
    ax.errorbar(x, [fa[m]["auc"] for m in models], yerr=[[fa[m]["auc"] - fa[m]["p2.5"] for m in models],
                                                         [fa[m]["p97.5"] - fa[m]["auc"] for m in models]],
                fmt="s", color="k", capsize=4, label="fire-averaged (95 % CI)")
    for f, mk in zip(fires, ("o", "^", "v", "D", "x")):
        ax.plot(x + 0.12, [loss["per_fire"][f]["models"][m]["auc"] for m in models], mk, alpha=0.6, label=f)
    ax.axhspan(0.70, 0.80, color="#d9f0d3", alpha=0.6, label="owner's realism reference 0.70-0.80")
    ax.axhline(0.5, color="r", lw=0.8)
    ax.set_xticks(x, models, fontsize=8)
    ax.set_ylabel("AUC (destroyed vs not)")
    ax.legend(fontsize=7)
    ax.set_title(f"Target B building-loss discrimination ({setname})")
    fig.tight_layout()
    fig.savefig(out / f"fig_skill_B_{setname}.png", dpi=DPI)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--train-dir", type=Path, required=True)
    ap.add_argument("--heldout-dir", type=Path, default=None)
    ap.add_argument("--eta-tag", default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fresh", nargs="+", default=["dixie", "caldor"])
    ap.add_argument("--v6-dir", type=Path, default=V.A.V6DATA / "results-2026-10-13" / "heldout")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if (a.train_dir / "fit_front.json").exists():
        fig_front(a.train_dir, a.out)
    if (a.train_dir / "fit_eta.json").exists():
        fig_eta(a.train_dir, a.out)
    if a.heldout_dir:
        for setname, fires in (("fresh", a.fresh), ("secondary", list(V.SECONDARY))):
            fig_scatter(fires, a.heldout_dir, a.eta_tag, setname, a.out, a.v6_dir)
            fig_spot(fires, a.heldout_dir, a.eta_tag, setname, a.out)
            p = a.heldout_dir / f"scores_PREREG_LR7_{setname}.json"
            if p.exists():
                fig_skill_a(json.loads(p.read_text()), setname, a.out)
            q = a.heldout_dir / f"loss_PREREG_LR7_{setname}.json"
            if q.exists():
                fig_skill_b(json.loads(q.read_text()), setname, a.out)


if __name__ == "__main__":
    main()
