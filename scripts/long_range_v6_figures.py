#!/usr/bin/env python3
"""Long-range embers v6: figures for the reframed validation report (PNG, 150 dpi; aggregate only).

Purpose: draw the target A and B figures of *Long-range embers v6 reframed validation 2026-10-13*:
  fig_spot_distance_<set>.png   spot-fire distance exceedance, V6 and A0 vs Storey et al. 2020 (p.7)
                                and the Cruz et al. 2012 maximum (33 km, p.269)
  fig_arrival_scatter_<set>.png  ensemble-median block arrival vs the observed interval, with and
                                without long-range embers
  fig_arrival_map_<fire>.png     block arrival error maps (540 m blocks), A0 vs V6
  fig_reliability_reach_<set>.png  P(block reached by hour) reliability, A0 vs V6
  fig_skill_A.png               MAE and Brier differences V6 - A0 with 95 % cluster-bootstrap CIs
  fig_skill_B.png               fire-averaged and per-fire AUC of the four loss models with CIs
  fig_reliability_B.png         reliability of the loss models (held-out)
Inputs: target A ensemble runs and score JSONs (``long_range_v6_arrival.py``), target B JSONs
(``long_range_v6_loss.py``). Run:
  PYTHONPATH=engine/src python3 scripts/long_range_v6_figures.py --arrival-dir D --eta E --fires ... --set heldout --out FIGDIR [--loss-json J]
Status: figures of a pre-registered test (PREREG_LR6) or of the training fit (labelled). No addresses.
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

import long_range_v6_arrival as A  # noqa: E402

C_A0 = "#7f7f7f"
C_V6 = "#d95f02"
C_OBS = "#1b9e77"
DPI = 150


def spot_fig(fires, adir, eta, out, label):
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    d = np.logspace(1.5, 4.7, 200)
    for model, key, col, name in (("V6", "lr_spots", C_V6, "V6 long-range spot fires"),
                                  ("A0", "albini_spots", C_A0, "A0 short-range (Albini) spot fires")):
        dist = []
        for f in fires:
            for m in A.load_members(adir / f, model, eta if model == "V6" else 0.0):
                ev = m.get(key)
                if ev is not None and len(ev):
                    dist.append(ev[:, 3])
        if not dist:
            continue
        dd = np.concatenate(dist)
        ax.plot(d / 1000, [np.mean(dd > x) for x in d], color=col, lw=2, label=f"{name} (n = {len(dd):,})")
    ax.scatter([0.1, 1.0, 3.0], [0.5, 0.10, 0.02], color="k", zorder=5, label="Storey et al. 2020: median, 10 % > 1 km, 2 % > 3 km")
    ax.axvline(13.9, color="k", ls=":", lw=1)
    ax.text(13.9, 2e-3, " Storey max 13.9 km", fontsize=7, rotation=90, va="bottom")
    ax.axvline(33.0, color="k", ls="--", lw=1)
    ax.text(33.0, 2e-3, " Cruz 2012: 33 km", fontsize=7, rotation=90, va="bottom")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(1e-4, 1.05)
    ax.set_xlabel("Distance from the spot fire to the fire at ignition (km)")
    ax.set_ylabel("Share of spot fires farther than x")
    ax.set_title(f"Spot-fire distances vs observations ({label})", fontsize=10)
    ax.legend(fontsize=7, loc="lower left")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)


def arrival_figs(fires, adir, eta, outdir, label):
    """Scatter of model median vs observed interval, arrival maps, reliability of reach."""
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.6), sharey=True)
    relP = {"A0": [], "V6": []}
    relO = {"A0": [], "V6": []}
    for f in fires:
        res, maps, ob, scored, g = A.score_fire(f, adir, eta, keep_maps=True)
        lo, hi = ob["t_lo"][scored] / 60, ob["t_hi"][scored] / 60
        mid = 0.5 * (lo + hi)
        for ax, model in zip(axs, ("A0", "V6")):
            if model not in maps:
                continue
            med = maps[model]["med"][scored] / 60
            ax.errorbar(mid, med, xerr=[mid - lo, hi - mid], fmt="o", ms=3, alpha=0.5, lw=0.6, label=f"{f} ({scored.sum()})")
            relP[model].append(maps[model]["P"])
            relO[model].append(maps[model]["O"])
        # map
        if "A0" in maps and "V6" in maps:
            figm, axm = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
            for a2, model in zip(axm, ("A0", "V6")):
                e = np.where(maps[model]["med"] < ob["t_lo"], -(ob["t_lo"] - maps[model]["med"]),
                             np.where(maps[model]["med"] > ob["t_hi"], maps[model]["med"] - ob["t_hi"], 0.0)) / 60
                e = np.where(scored, e, np.nan)
                ext = [g["lng_min"], g["lng_min"] + e.shape[1] * A.BLOCK_CELLS * g["cell_lng"],
                       g["lat_max"] - e.shape[0] * A.BLOCK_CELLS * g["cell_lat"], g["lat_max"]]
                comm = np.where(ob["n_bld"] >= A.MIN_BUILDINGS, 1.0, np.nan)
                a2.imshow(comm, extent=ext, cmap="Greys", vmin=0, vmax=3, interpolation="nearest")
                im = a2.imshow(e, extent=ext, cmap="RdBu_r", vmin=-12, vmax=12, interpolation="nearest")
                mae = res["models"][model]["mae_h"]
                a2.set_title(f"{f}: {model} (MAE {mae:.2f} h)" if mae is not None else f"{f}: {model}", fontsize=9)
                a2.set_xlabel("Longitude (°)")
                a2.set_ylabel("Latitude (°)")
                a2.tick_params(labelsize=7)
            cb = figm.colorbar(im, ax=axm, shrink=0.8)
            cb.set_label("Median arrival error (h): − early, + late, 0 = inside observed interval", fontsize=8)
            figm.suptitle(f"{f}: community-block arrival error, {label} (540 m blocks; grey = community blocks not scored)", fontsize=10)
            figm.savefig(outdir / f"fig_arrival_map_{f}.png", dpi=DPI)
            plt.close(figm)
    for ax, model in zip(axs, ("A0", "V6")):
        lim = [0, max(ax.get_xlim()[1], ax.get_ylim()[1])]
        ax.plot(lim, lim, "k--", lw=1)
        ax.set_xlabel("Observed arrival (h from run start; bar = observed interval)")
        ax.set_title({"A0": "A0: front + short-range spotting", "V6": "V6: + long-range ember spot fires"}[model], fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    axs[0].set_ylabel("Modelled arrival, ensemble median (h)")
    fig.suptitle(f"Fire arrival at community blocks, with vs without long-range embers ({label})", fontsize=10)
    fig.tight_layout()
    fig.savefig(outdir / f"fig_arrival_scatter_{label}.png", dpi=DPI)
    plt.close(fig)
    # reliability
    fig, ax = plt.subplots(figsize=(5.2, 5))
    e = np.linspace(0, 1, 11)
    for model, col in (("A0", C_A0), ("V6", C_V6)):
        if not relP[model]:
            continue
        P, O = np.concatenate(relP[model]), np.concatenate(relO[model])
        k = np.clip(np.digitize(P, e) - 1, 0, 9)
        xs = [P[k == i].mean() for i in range(10) if (k == i).sum() >= 10]
        ys = [O[k == i].mean() for i in range(10) if (k == i).sum() >= 10]
        ns = [(k == i).sum() for i in range(10) if (k == i).sum() >= 10]
        ax.plot(xs, ys, "o-", color=col, label=f"{model} (Brier {np.mean((P - O) ** 2):.3f}, n = {len(P):,})")
        for x, y, n in zip(xs, ys, ns):
            ax.annotate(str(n), (x, y), fontsize=6, color=col, xytext=(3, -8), textcoords="offset points")
    ax.plot([0, 1], [0, 1], "k--", lw=1)
    ax.set_xlabel("Forecast P(block reached by hour τ)")
    ax.set_ylabel("Observed share reached")
    ax.set_title(f"Reliability of fire-reach forecasts ({label})", fontsize=10)
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / f"fig_reliability_reach_{label}.png", dpi=DPI)
    plt.close(fig)


def skill_A(score_json, out):
    s = json.loads(Path(score_json).read_text())
    rows = []
    for f, r in s["fires"].items():
        if "mae_diff_V6_minus_A0" in r:
            rows.append((f, r["mae_diff_V6_minus_A0"], r.get("brier_diff_V6_minus_A0")))
    rows.append(("pooled", s["pooled"], s["pooled_brier"]))
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8))
    for ax, j, name in ((axs[0], 1, "MAE(V6) − MAE(A0), h"), (axs[1], 2, "Brier(V6) − Brier(A0)")):
        for k, row in enumerate(rows):
            d = row[j]
            col = "k" if row[0] == "pooled" else C_V6
            ax.errorbar(d["diff"], k, xerr=[[d["diff"] - d["p2.5"]], [d["p97.5"] - d["diff"]]], fmt="o", color=col, capsize=3)
        ax.axvline(0, color="k", lw=1)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r[0] for r in rows])
        ax.set_xlabel(name + " (negative = embers help)")
        ax.grid(alpha=0.3)
    fig.suptitle("Target A: skill of long-range embers vs FireSim without them (95 % cluster-bootstrap CI)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)


def skill_B(loss_json, out, out_rel):
    s = json.loads(Path(loss_json).read_text())
    models = ["B_dens", "B_dens_age", "B_comb_A0", "B_comb_V6"]
    fires = list(s["per_fire"])
    fig, ax = plt.subplots(figsize=(8, 4))
    w = 0.8 / len(models)
    cols = ["#bdbdbd", "#737373", "#1f78b4", C_V6]
    for i, m in enumerate(models):
        xs = np.arange(len(fires) + 1) + (i - 1.5) * w
        vals = [s["per_fire"][f]["models"][m]["auc"] for f in fires] + [s["fire_averaged"][m]["auc"]]
        ax.bar(xs, vals, w, color=cols[i], label=m)
        fa = s["fire_averaged"][m]
        ax.errorbar(xs[-1], fa["auc"], yerr=[[fa["auc"] - fa["p2.5"]], [fa["p97.5"] - fa["auc"]]], color="k", capsize=3)
    ax.axhspan(0.70, 0.80, color=C_OBS, alpha=0.12, label="0.70-0.80 (owner's target; Zamanialaei 2025: 0.64-0.84 in-fire)")
    ax.axhline(0.5, color="k", lw=1, ls=":")
    ax.set_xticks(np.arange(len(fires) + 1))
    ax.set_xticklabels(fires + ["fire-averaged"])
    ax.set_ylim(0.3, 1.0)
    ax.set_ylabel("AUC (destroyed vs survived)")
    ax.set_title(f"Target B: building-loss models, {s['_about']['status']}", fontsize=10)
    ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    fig, axs = plt.subplots(1, len(fires), figsize=(4 * len(fires), 4), squeeze=False)
    for ax, f in zip(axs[0], fires):
        for i, m in enumerate(models):
            rel = [b for b in s["per_fire"][f]["models"][m]["reliability"] if b["n"] >= 20]
            ax.plot([b["mean_p"] for b in rel], [b["observed"] for b in rel], "o-", color=cols[i], label=m, ms=3)
        ax.plot([0, 1], [0, 1], "k--", lw=1)
        ax.set_title(f"{f} (n = {s['per_fire'][f]['units']:,}, destroyed {s['per_fire'][f]['destroyed_rate']:.0%})", fontsize=9)
        ax.set_xlabel("Predicted P(destroyed)")
        ax.grid(alpha=0.3)
    axs[0][0].set_ylabel("Observed share destroyed")
    axs[0][0].legend(fontsize=7)
    fig.suptitle("Target B: reliability of building-loss models (bins with >= 20 units)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out_rel, dpi=DPI)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arrival-dir", type=Path)
    ap.add_argument("--eta", type=float)
    ap.add_argument("--fires", nargs="+")
    ap.add_argument("--set", default="heldout")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--score-json", type=Path)
    ap.add_argument("--loss-json", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.arrival_dir and a.fires:
        spot_fig(a.fires, a.arrival_dir, a.eta, a.out / f"fig_spot_distance_{a.set}.png", a.set)
        arrival_figs(a.fires, a.arrival_dir, a.eta, a.out, a.set)
    if a.score_json:
        skill_A(a.score_json, a.out / f"fig_skill_A_{a.set}.png")
    if a.loss_json:
        skill_B(a.loss_json, a.out / f"fig_skill_B_{a.set}.png", a.out / f"fig_reliability_B_{a.set}.png")


if __name__ == "__main__":
    main()
