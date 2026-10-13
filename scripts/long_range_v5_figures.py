#!/usr/bin/env python3
"""Figures for long-range embers v5 (PNG, 150 dpi). Aggregates only: maps are 250 m blocks in
km from the block-set centre, no basemap, no addresses.

    PYTHONPATH=engine/src python3 scripts/long_range_v5_figures.py --results <dir> --out <report data dir>

``<dir>`` holds ``diagnosis/``, ``fit/``, ``lofo/`` and ``heldout/`` (whatever exists is drawn).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine" / "src"))
from firesim.structures import long_range as LR  # noqa: E402

# reference palette (fixed categorical order; text in ink, not series colour)
C = {"v3": "#2a78d6", "v4": "#eb6834", "v5": "#1baf7a", "extra": "#eda100", "obs": "#0b0b0b"}
INK, INK2, GRID_C = "#0b0b0b", "#52514e", "#e4e3df"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DPI = 150


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(color=GRID_C, lw=0.6)
    ax.set_axisbelow(True)


def fig_kernel_vs_storey(out: Path):
    """Exceedance P(spot distance > d) of the v5 two-mode kernel against Storey et al. 2020 p.7."""
    d = np.logspace(1, np.log10(20_000), 400)
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    cases = [((10.0, 6.70), "10 MW/m, 6.7 m/s", C["v3"]), ((30.0, 11.17), "30 MW/m, 11.2 m/s", C["v5"]),
             ((40.0, 17.88), "40 MW/m, 17.9 m/s", C["v4"])]
    for (i, u), lab, col in cases:
        ax.plot(d / 1000, LR.two_mode_exceedance(d, i, u), color=col, lw=2, label=f"v5 two-mode, {lab}")
    from firesim.structures.embers import sardoy_lognormal, u10_to_u6
    from scipy.special import ndtr
    mu, sg = (float(v) for v in sardoy_lognormal(30.0, float(u10_to_u6(11.17))))
    ax.plot(d / 1000, 1 - ndtr((np.log(d) - mu) / sg), color=INK2, lw=1.5, ls="--",
            label="v1-v4 lognormal tail (6.1 m wind, untruncated), 30 MW/m")
    sx = [0.1, 1.0, 3.0]
    sy = [0.5, 0.10, 0.02]
    ax.scatter(sx, sy, s=60, color=C["obs"], zorder=5, label="Storey et al. 2020 p.7 (4,219 spot fires)")
    for x, y, t in zip(sx, sy, ["median 0.1 km", "10 % > 1 km", "2 % > 3 km"]):
        ax.annotate(t, (x, y), xytext=(6, 4), textcoords="offset points", fontsize=8, color=INK)
    ax.axvspan(4.5, 10.5, color=GRID_C, alpha=0.6, lw=0)
    ax.text(6.5, 0.6, "Sardoy long\n(charring) mode\n4.5-10.5 km", fontsize=8, color=INK2, ha="center")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(1e-4, 1.0)
    ax.set_xlim(0.01, 20)
    ax.set_xlabel("Downwind distance d (km)", color=INK)
    ax.set_ylabel("Share of spot fires beyond d", color=INK)
    ax.set_title("v5 ember kernel against observed spot-fire distances", fontsize=10, color=INK, loc="left")
    _style(ax)
    ax.legend(fontsize=7, frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(out / "fig_kernel_vs_storey.png", dpi=DPI)
    plt.close(fig)


def fig_diagnosis(res: Path, out: Path):
    p = res / "diagnosis" / "diagnosis.json"
    if not p.exists():
        return
    dg = json.loads(p.read_text())
    keys = ["minus_d_edge", "v3_wildland_embers", "v5_short_embers", "v5_long_embers", "front_contact",
            "density_30m", "density_100m", "minus_year_built", "minus_canopy_0_30",
            "P_v3", "P_v3_no_survival", "P_v5_wildland_only", "P_v5_urban_only", "P_v5_links_only", "P_v5"]
    labels = ["nearer wildland edge", "v3 wildland embers", "v5 short-mode embers", "v5 long-mode embers",
              "front contact", "buildings within 30 m", "buildings within 100 m", "older (year built)",
              "less canopy 0-30 m", "P v3 [R37]", "P v3, no survival", "P v5, wildland embers only",
              "P v5, urban embers only", "P v5, links only", "P v5 (24 g, λ 6 m, s 0.3)"]
    fires = list(dg)
    fig, ax = plt.subplots(figsize=(7.6, 6.2))
    y = np.arange(len(keys))[::-1]
    cols = [C["v3"], C["v4"], C["v5"], C["extra"], "#e87ba4"]
    off = np.linspace(-0.3, 0.3, len(fires))
    for f, o, col in zip(fires, off, cols):
        a, lo, hi = [], [], []
        for k in keys:
            r = dg[f]["auc"].get(k)
            a.append(r["auc"] if r else np.nan)
            ci = r.get("ci95") if r else None
            lo.append(ci[0] if ci else np.nan)
            hi.append(ci[1] if ci else np.nan)
        a, lo, hi = map(np.array, (a, lo, hi))
        ax.errorbar(a, y + o, xerr=[np.where(np.isfinite(lo), a - lo, 0), np.where(np.isfinite(hi), hi - a, 0)],
                    fmt="o", ms=4, color=col, elinewidth=1, label=f.capitalize())
    ax.axvline(0.5, color=INK2, lw=1)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlim(0.2, 0.85)
    ax.set_xlabel("AUC, destroyed vs not (0.5 = no information; 95 % block bootstrap)", color=INK)
    ax.set_title("Each component alone on the training fires", fontsize=10, color=INK, loc="left")
    _style(ax)
    ax.legend(fontsize=7, frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "fig_diagnosis_component_auc.png", dpi=DPI)
    plt.close(fig)


def _reliability(ax, p, o, col, lab):
    bins = np.linspace(0, 1, 11)
    i = np.clip(np.digitize(p, bins) - 1, 0, 9)
    xs, ys, ns = [], [], []
    for b in range(10):
        m = i == b
        if m.sum() >= 30:
            xs.append(p[m].mean())
            ys.append(o[m].mean())
            ns.append(m.sum())
    ax.plot(xs, ys, "-o", color=col, lw=2, ms=5, label=lab)


def fig_reliability(res: Path, out: Path, tag: str, files: dict):
    have = {k: v for k, v in files.items() if v.exists()}
    if not have:
        return
    fig, ax = plt.subplots(figsize=(5.2, 5.0))
    ax.plot([0, 1], [0, 1], color=INK2, lw=1, ls="--")
    pools = {}
    for f, path in have.items():
        z = np.load(path)
        sm = z["scored"]
        for key, model in (("P_V5", "v5"), ("P_V3", "v3"), ("B_v3_R37", "v3"), ("P_V4", "v4")):
            if key in z:
                pools.setdefault(model, ([], []))
                pools[model][0].append(z[key][sm])
                pools[model][1].append(z["obs"][sm])
    for model, (ps, os_) in pools.items():
        _reliability(ax, np.concatenate(ps), np.concatenate(os_).astype(float), C[model], model)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted P(destroyed) (bin mean)", color=INK)
    ax.set_ylabel("Observed share destroyed", color=INK)
    ax.set_title(f"Reliability, {tag} (bins with >= 30 buildings)", fontsize=10, color=INK, loc="left")
    _style(ax)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out / f"fig_reliability_{tag}.png", dpi=DPI)
    plt.close(fig)


def fig_skill(res: Path, out: Path):
    """AUC and Brier skill vs base rate with 95 % CIs: LOFO training folds and held-out fires."""
    rows = []
    lp = res / "lofo" / "lofo_v5.json"
    if lp.exists():
        lo = json.loads(lp.read_text())
        for f, r in lo.items():
            for model in ("v3", "v4", "v5"):
                s = r["scores"].get(model)
                if s:
                    rows.append((f"{f} (LOFO)", model, s["auc"], s["bss_vs_climatology"]))
    for p in sorted((res / "heldout").glob("results_lr5_*.json")) if (res / "heldout").exists() else []:
        if "pooled" in p.name or "DRYRUN" in p.name:
            continue
        r = json.loads(p.read_text())
        f = r["fire"]
        rows.append((f"{f} (held out)", "v5", r["scores"]["V5"]["auc"], r["scores"]["V5"]["bss_vs_climatology"]))
        b = r["baselines"].get("v3_R37")
        if b:
            rows.append((f"{f} (held out)", "v3", b["auc"], b["bss_vs_climatology"]))
        v4p = Path(str(res).replace("v5", "v4")) / "heldout" / f"results_lr4_{f}.json"
        if v4p.exists():
            r4 = json.loads(v4p.read_text())
            rows.append((f"{f} (held out)", "v4", r4["scores"]["V4"]["auc"], r4["scores"]["V4"]["bss_vs_climatology"]))
    if not rows:
        return
    groups = list(dict.fromkeys(r[0] for r in rows))
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 0.45 * len(groups) + 1.6), sharey=True)
    y = np.arange(len(groups))[::-1]
    off = {"v3": -0.22, "v4": 0.0, "v5": 0.22}
    for model in ("v3", "v4", "v5"):
        sel = [r for r in rows if r[1] == model]
        if not sel:
            continue
        yy = [y[groups.index(r[0])] + off[model] for r in sel]
        axes[0].scatter([r[2] for r in sel], yy, color=C[model], s=28, label=model, zorder=3)
        axes[1].scatter([max(r[3], -1.0) for r in sel], yy, color=C[model], s=28, zorder=3)
    axes[0].axvline(0.5, color=INK2, lw=1)
    axes[1].axvline(0.0, color=INK2, lw=1)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels(groups, fontsize=8)
    axes[0].set_xlabel("AUC", color=INK)
    axes[1].set_xlabel("Brier skill vs the fire's base rate (clipped at -1)", color=INK)
    for a in axes:
        _style(a)
    axes[0].legend(fontsize=8, frameon=False, loc="lower right")
    axes[0].set_title("v3, v4, v5 per fire (CIs: see report tables)", fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(out / "fig_skill_vs_v3_v4.png", dpi=DPI)
    plt.close(fig)


def fig_map(npz: Path, out: Path, name: str, key: str = "P_V5"):
    """Mean P and observed share per 250 m block (>= 3 scored buildings), side by side."""
    if not npz.exists():
        return
    z = np.load(npz)
    sm = z["scored"]
    if key not in z:
        return
    x, y, o, p = z["x"][sm], z["y"][sm], z["obs"][sm].astype(float), z[key][sm]
    bx, by = np.floor(x / 250.0).astype(int), np.floor(y / 250.0).astype(int)
    keys, inv = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = np.bincount(inv)
    ok = n >= 3
    ob = np.bincount(inv, weights=o)[ok] / n[ok]
    pb = np.bincount(inv, weights=p)[ok] / n[ok]
    kx, ky = (keys[ok, 0] + 0.5) * 0.25, (keys[ok, 1] + 0.5) * 0.25
    kx, ky = kx - kx.mean(), ky - ky.mean()
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.6), sharex=True, sharey=True)
    for ax, v, t in ((axes[0], pb, f"Mean modelled P ({key[2:].lower()})"), (axes[1], ob, "Observed share destroyed (DINS)")):
        sc = ax.scatter(kx, ky, c=v, cmap=cmap, vmin=0, vmax=1, marker="s", s=14, linewidths=0)
        ax.set_aspect("equal")
        ax.set_title(t, fontsize=9, color=INK, loc="left")
        ax.set_xlabel("km (relative)", color=INK2, fontsize=8)
        _style(ax)
        ax.grid(False)
    fig.colorbar(sc, ax=axes, shrink=0.8, label="share")
    r = float(np.corrcoef(pb, ob)[0, 1]) if len(pb) > 2 else float("nan")
    fig.suptitle(f"{name}: 250 m blocks (n = {ok.sum()}), block correlation r = {r:.2f}", fontsize=10,
                 color=INK, x=0.02, ha="left")
    fig.savefig(out / f"fig_map_{name.lower().replace(' ', '_')}.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    fig_kernel_vs_storey(a.out)
    fig_diagnosis(a.results, a.out)
    fig_reliability(a.results, a.out, "training_LOFO",
                    {f: a.results / "lofo" / f"lofo_{f}.npz" for f in ("eaton", "palisades", "camp", "tubbs", "woolsey")})
    fig_reliability(a.results, a.out, "heldout",
                    {f: a.results / "heldout" / f"units_lr5_{f}.npz" for f in ("glass", "czu", "northcomplex")})
    fig_skill(a.results, a.out)
    for f in ("eaton", "palisades", "camp", "tubbs", "woolsey"):
        fig_map(a.results / "lofo" / f"lofo_{f}.npz", a.out, f"{f.capitalize()} LOFO")
    for f in ("glass", "czu", "northcomplex"):
        fig_map(a.results / "heldout" / f"units_lr5_{f}.npz", a.out, f"{f.capitalize()} held out")


if __name__ == "__main__":
    main()
