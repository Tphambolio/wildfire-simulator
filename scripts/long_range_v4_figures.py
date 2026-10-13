#!/usr/bin/env python3
"""Figures and small CSV/JSON summaries for the long-range embers v4 report (illustrative —
not validated). Maps are aggregated to 250 m blocks (no addresses, no building-level output).

Usage:
    python3 scripts/long_range_v4_figures.py --fit <fit dir> --results <score dir> --out <fig dir>
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

CAPTION = "Illustrative — not validated"
COL = {"v4": "#c2410c", "c": "#2563eb", "obs": "#111827", "e": "#6b7280"}


def _stamp(fig):
    fig.text(0.995, 0.002, CAPTION, ha="right", va="bottom", fontsize=8, color="#b91c1c")


def calibration_curves(fit, out: Path):
    """Brier vs each fitted parameter (others at their final value), per training fire."""
    grid, fin, table = fit["grid"], fit["final"]["theta"], fit["table"]
    keys = list(grid)
    fig, axs = plt.subplots(1, len(keys), figsize=(4.3 * len(keys), 3.8), sharey=True)
    for ax, k in zip(axs, keys):
        for fire, c in zip(table, ("#c2410c", "#2563eb", "#059669")):
            rows = [r for r in table[fire]["runs"] if all(r[o] == fin[o] for o in keys if o != k)]
            rows.sort(key=lambda r: r[k])
            ax.plot([r[k] for r in rows], [r["brier"] for r in rows], "o-", color=c, label=fire)
            ax.axhline(table[fire]["climatology_brier"], color=c, ls=":", lw=1)
        ax.axvline(fin[k], color="k", lw=0.8, ls="--")
        ax.set_xlabel(k)
        if k in ("link_scale_m", "ember_factor"):
            ax.set_xscale("log")
    axs[0].set_ylabel("Brier (8 members; dotted = climatology)")
    axs[0].legend(fontsize=8)
    fig.suptitle("v4 calibration on the training fires (others at the fitted value, dashed)", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_calibration_curves.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    with open(out / "fit_grid_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fire"] + keys + ["brier", "auc", "kappa", "sum_p", "ring_involved", "block_skill"])
        for fire in table:
            for r in table[fire]["runs"]:
                w.writerow([fire] + [r[k] for k in keys] + [round(r["brier"], 5), round(r["auc"], 4), round(r["kappa"], 4),
                                                            round(r["sum_p"], 1), round(r["ring_involved"], 1),
                                                            r["block"]["skill_vs_climatology"]])
    (out / "fit_cv_summary.json").write_text(json.dumps({"lofo_cv": fit["lofo_cv"], "final": fit["final"],
                                                         "grid": grid, "members": fit["members"]}, indent=1))


def _blocks(d, key, block=250.0):
    sm = d["scored"]
    bx = np.floor(d["x"][sm] / block).astype(int)
    by = np.floor(d["y"][sm] / block).astype(int)
    keys, inv = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = np.bincount(inv)
    return keys, n, np.bincount(inv, weights=d["obs"][sm].astype(float)) / n, np.bincount(inv, weights=d[key][sm]) / n


def fire_map(fire, d, out: Path):
    keys, n, obs, p = _blocks(d, "P_V4")
    ok = n >= 3
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.6), sharex=True, sharey=True)
    for ax, val, ttl in zip(axs, (obs, p), ("Observed destroyed share (DINS)", "Mean modelled P, v4")):
        sc = ax.scatter(keys[ok, 0] * 0.25, keys[ok, 1] * 0.25, c=val[ok], s=10, marker="s",
                        cmap="inferno_r", vmin=0, vmax=1)
        ax.set_title(ttl, fontsize=10)
        ax.set_aspect("equal")
        ax.set_xlabel("km east (local frame)")
    axs[0].set_ylabel("km north (local frame)")
    fig.colorbar(sc, ax=axs, shrink=0.8, label="share / probability")
    fig.suptitle(f"{fire}: 250 m blocks with ≥ 3 scored buildings", fontsize=11)
    _stamp(fig)
    fig.savefig(out / f"fig_map_{fire}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def reliability(results, out: Path, name="fig_reliability.png", title="Reliability (10 bins)"):
    fig, axs = plt.subplots(1, len(results), figsize=(4.3 * len(results), 4.0))
    axs = np.atleast_1d(axs)
    for ax, (fire, r) in zip(axs, results):
        ax.plot([0, 1], [0, 1], color="#9ca3af", ls="--", lw=1)
        rel = [b for b in r["scores"]["V4"]["reliability"] if b["n"]]
        ax.plot([b["mean_p"] for b in rel], [b["observed"] for b in rel], "o-", color=COL["v4"], label="v4")
        for b in rel:
            ax.annotate(str(b["n"]), (b["mean_p"], b["observed"]), fontsize=6, xytext=(2, 2), textcoords="offset points")
        rel = [b for b in r["baselines"]["c_chen_mcaneney"]["reliability"] if b["n"]]
        ax.plot([b["mean_p"] for b in rel], [b["observed"] for b in rel], "s-", color=COL["c"], label="(c)", ms=3)
        ax.set_title(fire, fontsize=9)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("modelled probability")
    axs[0].set_ylabel("observed destroyed share")
    axs[0].legend(fontsize=8)
    fig.suptitle(title, fontsize=11)
    _stamp(fig)
    fig.savefig(out / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


def skill_bars(groups, out: Path):
    tests = [("Brier_c_minus_v4", "Brier(c) − Brier(v4)"), ("kappa_v4_minus_b", "κ(v4) − κ(b)"),
             ("kappa_v4_minus_d", "κ(v4) − κ(d)"), ("Brier_e_minus_v4", "Brier(e) − Brier(v4)\nbase rate (test fire)"),
             ]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    w = 0.8 / len(groups)
    cols = ["#c2410c", "#64748b", "#94a3b8", "#0f766e"]
    for j, (label, r) in enumerate(groups):
        for i, (k, _) in enumerate(tests):
            t = r["tests"].get(k)
            if t is None:
                continue
            x = i + (j - (len(groups) - 1) / 2) * w
            ax.bar(x, t["point"], w * 0.9, color=cols[j % 4], label=label if i == 0 else None)
            ax.errorbar(x, t["point"], yerr=[[t["point"] - t["p2.5"]], [t["p97.5"] - t["point"]]], color="k", capsize=3, lw=1)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(range(len(tests)))
    ax.set_xticklabels([t[1] for t in tests], fontsize=8)
    ax.set_ylabel("difference (> 0: v4 better), 95 % block bootstrap")
    ax.set_title("PREREG_LR4 comparisons (held-out fires)", fontsize=11)
    ax.legend(fontsize=8)
    _stamp(fig)
    fig.savefig(out / "fig_skill_vs_baselines.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def effects(results, out: Path):
    """Survival / run-end / links / urban / ember sensitivities: Brier and ΣP vs observed."""
    names = ["V4", "NO_COVARIATES", "NO_STOP", "NO_SURVIVAL"]
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    w = 0.8 / len(results)
    for j, (fire, r) in enumerate(results):
        br = [r["scores"]["V4"]["brier"]] + [r["sensitivity"][n]["brier"] for n in names[1:]]
        sp = [r["scores"]["V4"]["expected_count"]] + [r["sensitivity"][n]["expected_count"] for n in names[1:]]
        x = np.arange(len(names)) + (j - (len(results) - 1) / 2) * w
        axs[0].bar(x, br, w * 0.9, label=fire)
        axs[1].bar(x, np.array(sp) / max(r["observed_destroyed"], 1), w * 0.9, label=fire)
    for ax, yl in zip(axs, ("Brier (lower is better)", "Σ P / observed destroyed")):
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=30, fontsize=8)
        ax.set_ylabel(yl)
    axs[1].axhline(1, color="k", lw=0.8, ls="--")
    axs[0].legend(fontsize=8)
    fig.suptitle("Effect of survival, run end, links, urban embers (sensitivities, 20 members)", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_survival_duration_effects.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def distance(results, out: Path):
    fig, axs = plt.subplots(1, len(results), figsize=(4.3 * len(results), 3.8), sharey=True)
    axs = np.atleast_1d(axs)
    for ax, (fire, r) in zip(axs, results):
        b = [x for x in r["distance_bands"] if x["units"]]
        xs = np.arange(len(b))
        ax.plot(xs, [x["observed_rate"] for x in b], "o-", color=COL["obs"], label="observed")
        ax.plot(xs, [x["mean_P_v4"] for x in b], "s-", color=COL["v4"], label="v4")
        ax.plot(xs, [x["mean_P_c"] for x in b], "d-", color=COL["c"], label="(c)")
        ax.set_xticks(xs)
        ax.set_xticklabels([x["band_m"].replace("-1e+09", "+") for x in b], rotation=35, fontsize=8)
        ax.set_title(fire, fontsize=9)
        ax.set_ylim(0, 1.02)
    axs[0].set_ylabel("loss rate / mean P")
    axs[0].legend(fontsize=8)
    fig.suptitle("Loss rate by distance from the wildland edge", fontsize=11)
    _stamp(fig)
    fig.savefig(out / "fig_distance.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def summaries(results, pooled, out: Path):
    small = {}
    for fire, r in results:
        small[fire] = {k: r[k] for k in ("units_scored", "observed_destroyed", "units_ring", "front_contact_units",
                                         "stop_min", "window", "theta", "tests", "distance_bands",
                                         "false_alarms_outside", "prereg_sha256")}
        small[fire]["scores"] = {k: {kk: v[kk] for kk in ("brier", "bss_vs_climatology", "log_score", "auc",
                                                          "expected_count", "at_0.5", "reliability")}
                                 for k, v in {**r["scores"], **r["sensitivity"], **r["baselines"]}.items()}
    (out / "scores_by_fire.json").write_text(json.dumps(small, indent=1, default=float))
    if pooled:
        (out / "prereg_lr3_pooled.json").write_text(json.dumps(
            {k: pooled[k] for k in ("fires", "theta", "units_scored", "observed_destroyed", "tests", "decision",
                                    "prereg_sha256")}, indent=1, default=float))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--heldout", nargs="+", default=["glass", "czu", "northcomplex"])
    ap.add_argument("--training", nargs="+", default=["eaton", "palisades", "camp", "tubbs", "woolsey"])
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    fit = json.loads((a.fit / "fit_v4.json").read_text())
    calibration_curves(fit, a.out)
    held, train = [], []
    for fire in a.heldout + a.training:
        j = a.results / f"results_lr4_{fire}.json"
        if not j.exists():
            continue
        r = json.loads(j.read_text())
        (held if fire in a.heldout else train).append((fire, r))
        z = a.results / f"units_lr4_{fire}.npz"
        if z.exists():
            fire_map(fire, dict(np.load(z)), a.out)
    pj = a.results / f"results_lr4_pooled_{'_'.join(a.heldout)}.json"
    pooled = json.loads(pj.read_text()) if pj.exists() else None
    if held:
        reliability(held, a.out, "fig_reliability_heldout.png", "Reliability, held-out fires (v4, fitted values)")
        effects(held, a.out)
        distance(held + train, a.out)
        groups = ([("pooled held-out (decides)", pooled)] if pooled else []) + [(f, r) for f, r in held]
        skill_bars(groups, a.out)
    if train:
        reliability(train, a.out, "fig_reliability_training.png", "Reliability, training fires (in-sample, fitted)")
    summaries(held + train, pooled, a.out)
    print(sorted(p.name for p in a.out.iterdir()))


if __name__ == "__main__":
    main()
