#!/usr/bin/env python3
"""Figures for the long-range embers v2 report (illustrative — not validated).

Reads the aggregate JSON and the per-unit archive written by ``long_range_v2_validation.py``
(outside the repository) and writes PNG figures (150 dpi). Maps are aggregated to 250 m
blocks in a local metric frame (no addresses, no building-level output).

Usage:
    python3 scripts/long_range_v2_figures.py --results <results dir> --out <figure dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

CAPTION = "Illustrative — not validated"
FIRES = [("palisades", "Palisades 2025 (independent, primary)"),
         ("eaton", "Eaton 2025 (disclosed check, not independent)"),
         ("camp", "Camp 2018 (disclosed check, not independent)")]
COL = {"v2": "#c2410c", "v1": "#6b7280", "c": "#2563eb", "obs": "#111827"}


def _stamp(fig):
    fig.text(0.995, 0.005, CAPTION, ha="right", va="bottom", fontsize=8, color="#b91c1c")


def chain_diagram(out: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    boxes = {
        "wild": (0.2, 2.6, "Wildland source\ntorching/crowning cells\n250-640 embers/kg (drawn)"),
        "tw": (2.7, 2.6, "Transport\nSardoy lognormal,\nAlbini cap"),
        "ign": (5.2, 2.6, "Ignition per building\n1.44 m² receptive bed, q = 4·p_bed·p_b\ninvolved 5.7 min later"),
        "stage": (7.7, 2.6, "Structure stage\nHamada 30 m, burn-out\n(front contact too)"),
        "urb": (7.7, 0.4, "Urban source\nburning buildings emit\nGR' = 10 per MJ × HRR"),
        "tu": (5.2, 0.4, "Transport\nHimoto lognormal\nfrom the downwind edge"),
    }
    for k, (x, y, txt) in boxes.items():
        ax.add_patch(FancyBboxPatch((x, y), 2.1, 1.2, boxstyle="round,pad=0.05",
                                    fc="#fff7ed" if k in ("urb", "tu") else "#f1f5f9", ec="#334155"))
        ax.text(x + 1.05, y + 0.6, txt, ha="center", va="center", fontsize=8.5)

    def arrow(a, b, txt=None, rad=0.0):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=14, color="#334155",
                                     connectionstyle=f"arc3,rad={rad}"))
        if txt:
            ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.15, txt, ha="center", fontsize=7.5)

    arrow((2.35, 3.2), (2.65, 3.2))
    arrow((4.85, 3.2), (5.15, 3.2))
    arrow((7.35, 3.2), (7.65, 3.2))
    arrow((8.75, 2.55), (8.75, 1.65), "involved units")
    arrow((7.65, 1.0), (7.35, 1.0))
    arrow((6.25, 1.65), (6.25, 2.55), "every 10 min")
    ax.text(0.2, 0.9, "Per member: wind, p_b ~ U(0.1, 1),\nembers/kg drawn; per building: "
            "p_bed\n(log-uniform 2e-4–7e-3). 50 members →\nP(involved) = share of members.",
            fontsize=8, va="center")
    ax.set_title("Long-range embers v2: wildland → town → town (opt-in, illustrative)", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_chain.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def _blocks(d, key, block=250.0):
    sm = d["scored"]
    x, y = d["x"][sm], d["y"][sm]
    bx = np.floor(x / block).astype(int)
    by = np.floor(y / block).astype(int)
    keys, inv = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = np.bincount(inv)
    obs = np.bincount(inv, weights=d["obs"][sm].astype(float)) / n
    p = np.bincount(inv, weights=d[key][sm]) / n
    return keys, n, obs, p


def maps(fire, label, d, out: Path) -> str:
    keys, n, obs, p = _blocks(d, "P_PRIMARY")
    _, _, _, p1 = _blocks(d, "P_V1")
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6), sharex=True, sharey=True)
    ok = n >= 3
    for ax, val, ttl in zip(axs, (obs, p, p1), ("Observed destroyed share (DINS)",
                                                 "Mean modelled P, v2", "Mean modelled P, v1")):
        sc = ax.scatter(keys[ok, 0] * 0.25, keys[ok, 1] * 0.25, c=val[ok], s=9, marker="s",
                        cmap="inferno_r", vmin=0, vmax=1)
        ax.set_title(ttl, fontsize=10)
        ax.set_aspect("equal")
        ax.set_xlabel("km east (local frame)")
    axs[0].set_ylabel("km north (local frame)")
    fig.colorbar(sc, ax=axs, shrink=0.8, label="share / probability")
    fig.suptitle(f"{label}: 250 m blocks with ≥ 3 scored buildings", fontsize=11)
    _stamp(fig)
    path = out / f"fig_map_{fire}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path.name


def reliability(results, out: Path) -> None:
    fig, axs = plt.subplots(1, len(results), figsize=(4.4 * len(results), 4.2))
    axs = np.atleast_1d(axs)
    for ax, (fire, label, r) in zip(axs, results):
        ax.plot([0, 1], [0, 1], color="#9ca3af", lw=1, ls="--")
        for k, c, nm in (("PRIMARY", COL["v2"], "v2"), ("V1", COL["v1"], "v1")):
            rel = [b for b in r["scores"][k]["reliability"] if b["n"]]
            ax.plot([b["mean_p"] for b in rel], [b["observed"] for b in rel], "o-", color=c,
                    label=nm, ms=4)
        rel = [b for b in r["baselines"]["c_chen_mcaneney"]["reliability"] if b["n"]]
        ax.plot([b["mean_p"] for b in rel], [b["observed"] for b in rel], "s-", color=COL["c"],
                label="(c) Chen & McAneney", ms=3)
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("modelled probability (bin mean)")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
    axs[0].set_ylabel("observed destroyed share")
    axs[0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Reliability (10 equal-width bins)", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_reliability.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def skill_bars(results, out: Path) -> None:
    tests = [("BSS_vs_c (Brier_c - Brier_v2)", "Brier(c) − Brier(v2)\n(> 0: v2 better)"),
             ("kappa_v2_minus_b", "κ(v2) − κ(b)\nwindward seeding"),
             ("kappa_v2_minus_d", "κ(v2) − κ(d)\ndistance band"),
             ("kappa_v2_minus_a", "κ(v2) − κ(a)\nHamada seeds"),
             ("kappa_v2_minus_V1", "κ(v2) − κ(v1)"),
             ("BSS_vs_e (Brier_e - Brier_v2)", "Brier(e) − Brier(v2)\nclimatology")]
    fig, ax = plt.subplots(figsize=(11, 4.6))
    w = 0.8 / len(results)
    cols = ["#c2410c", "#64748b", "#94a3b8"]
    for j, (fire, label, r) in enumerate(results):
        for i, (k, _) in enumerate(tests):
            t = r["tests"].get(k)
            if t is None:
                continue
            xpos = i + (j - (len(results) - 1) / 2) * w
            ax.bar(xpos, t["point"], w * 0.9, color=cols[j % 3], label=label if i == 0 else None)
            ax.errorbar(xpos, t["point"], yerr=[[t["point"] - t["p2.5"]], [t["p97.5"] - t["point"]]],
                        color="k", capsize=3, lw=1)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(range(len(tests)))
    ax.set_xticklabels([t[1] for t in tests], fontsize=8)
    ax.set_ylabel("difference (95 % block-bootstrap interval)")
    ax.set_title("Pre-registered comparisons: v2 against each baseline (κ at P ≥ 0.5; Brier)", fontsize=11)
    ax.legend(fontsize=8)
    _stamp(fig)
    fig.savefig(out / "fig_skill_vs_baselines.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def distance(results, out: Path) -> None:
    fig, axs = plt.subplots(1, len(results), figsize=(4.4 * len(results), 4.0), sharey=True)
    axs = np.atleast_1d(axs)
    for ax, (fire, label, r) in zip(axs, results):
        bands = [b for b in r["distance_bands"] if b["units"]]
        xs = np.arange(len(bands))
        for key, c, nm, mk in (("observed_rate", COL["obs"], "observed", "o"),
                               ("mean_P_v2", COL["v2"], "v2", "s"), ("mean_P_v1", COL["v1"], "v1", "^"),
                               ("mean_P_c", COL["c"], "(c)", "d")):
            ax.plot(xs, [b[key] for b in bands], marker=mk, color=c, label=nm)
        ax.set_xticks(xs)
        ax.set_xticklabels([b["band_m"].replace("-1e+09", "+") for b in bands], rotation=35, fontsize=8)
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("distance from the wildland edge (m)")
        ax.set_ylim(0, 1.02)
    axs[0].set_ylabel("loss rate / mean probability")
    axs[0].legend(fontsize=8)
    fig.suptitle("Loss rate by distance from the imposed wildland source", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_distance.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    chain_diagram(a.out)
    results = []
    for fire, label in FIRES:
        j = a.results / f"results_lr2_{fire}.json"
        z = a.results / f"units_lr2_{fire}.npz"
        if not j.exists():
            continue
        r = json.loads(j.read_text())
        results.append((fire, label, r))
        if z.exists() and fire in ("palisades", "eaton", "camp"):
            maps(fire, label, dict(np.load(z)), a.out)
    if results:
        reliability(results, a.out)
        skill_bars(results, a.out)
        distance(results, a.out)
    print(sorted(p.name for p in a.out.glob("fig_*.png")))


if __name__ == "__main__":
    main()
