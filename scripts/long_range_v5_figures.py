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
C = {"v3": "#2a78d6", "v4": "#eb6834", "v5": "#1baf7a", "extra": "#eda100", "v5 density": "#eda100",
     "obs": "#0b0b0b"}
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
        arrs = {k: z[k] for k in ("P_V5", "P_V3", "B_v3_R37", "P_V4") if k in z}
        if "units_lr5_" in path.name:  # held out: v4 from its own harness, v5 density variant
            v4 = _v4_heldout(res, f, len(z["P_V5"]))
            if v4 is not None:
                arrs["P_V4"] = v4
            dp = res / "heldout_density" / f"units_lr5_{f}_density.npz"
            if dp.exists():
                arrs["P_V5D"] = np.load(dp)["P_V5"]
        for key, model in (("P_V5", "v5"), ("P_V5D", "v5 density"), ("P_V3", "v3"), ("B_v3_R37", "v3"),
                           ("P_V4", "v4")):
            if key in arrs:
                pools.setdefault(model, ([], []))
                pools[model][0].append(arrs[key][sm])
                pools[model][1].append(z["obs"][sm])
    for model, (ps, os_) in pools.items():
        _reliability(ax, np.concatenate(ps), np.concatenate(os_).astype(float), C[model], model)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted P(destroyed) (bin mean)", color=INK)
    ax.set_ylabel("Observed share destroyed", color=INK)
    ax.set_title(f"Reliability, {tag.replace('_', ' ')} (pooled; bins with >= 30 buildings)", fontsize=10,
                 color=INK, loc="left")
    _style(ax)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out / f"fig_reliability_{tag}.png", dpi=DPI)
    plt.close(fig)


def _boot(blocks, fn, p, o, n=500, seed=20261013):
    nb = int(blocks.max()) + 1
    members = [np.nonzero(blocks == i)[0] for i in range(nb)]
    rng = np.random.default_rng(seed)
    v = []
    for _ in range(n):
        idx = np.concatenate([members[i] for i in rng.integers(0, nb, nb)])
        v.append(fn(p[idx], o[idx]))
    return float(fn(p, o)), float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))


def _auc(p, o):
    from scipy.stats import rankdata
    o = o.astype(bool)
    n1, n0 = o.sum(), (~o).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    r = rankdata(p)
    return (r[o].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def _bss(p, o):
    b = np.mean((p - o) ** 2)
    c = np.mean((o.mean() - o) ** 2)
    return 1 - b / c if c > 0 else np.nan


def _v4_heldout(res: Path, fire: str, n: int):
    p = Path(str(res).replace("v5-2026-10-12", "v4-2026-10-12")) / "heldout" / f"units_lr4_{fire}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    return z["P_V4"] if "P_V4" in z and len(z["P_V4"]) == n else None


def model_sets(res: Path):
    """(label, model -> P, obs, blocks) per fire from the LOFO and held-out unit files."""
    out = []
    for f in ("eaton", "palisades", "camp", "tubbs", "woolsey"):
        p = res / "lofo" / f"lofo_{f}.npz"
        if p.exists():
            z = np.load(p)
            sm = z["scored"]
            ms = {k: z[f"P_{k.upper()}"][sm] for k in ("v3", "v4", "v5") if f"P_{k.upper()}" in z}
            out.append((f"{f.capitalize()} (LOFO)", ms, z["obs"][sm].astype(float), z["x"][sm], z["y"][sm]))
    for f in ("glass", "czu", "northcomplex"):
        p = res / "heldout" / f"units_lr5_{f}.npz"
        if p.exists():
            z = np.load(p)
            sm = z["scored"]
            ms = {"v5": z["P_V5"][sm], "v3": z["B_v3_R37"][sm]}
            v4 = _v4_heldout(res, f, len(z["P_V5"]))
            if v4 is not None:
                ms["v4"] = v4[sm]
            out.append((f"{f.capitalize()} (held out)", ms, z["obs"][sm].astype(float), z["x"][sm], z["y"][sm]))
    return out


def fig_skill(res: Path, out: Path):
    """AUC and Brier skill vs base rate per fire and model, 95 % block-bootstrap CIs (250 m)."""
    sets = model_sets(res)
    if not sets:
        return []
    rows = []
    for lab, ms, o, x, y in sets:
        _, blk = np.unique(np.column_stack([np.floor(x / 250), np.floor(y / 250)]), axis=0, return_inverse=True)
        blk = blk.ravel()
        for m, p in ms.items():
            rows.append((lab, m, _boot(blk, _auc, p, o), _boot(blk, _bss, p, o)))
    groups = list(dict.fromkeys(r[0] for r in rows))
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 0.5 * len(groups) + 1.8), sharey=True)
    y0 = np.arange(len(groups))[::-1]
    off = {"v3": -0.22, "v4": 0.0, "v5": 0.22}
    for m in ("v3", "v4", "v5"):
        sel = [r for r in rows if r[1] == m]
        if not sel:
            continue
        yy = np.array([y0[groups.index(r[0])] + off[m] for r in sel])
        for ax, k, clip in ((axes[0], 2, None), (axes[1], 3, -1.5)):
            v = np.array([r[k] for r in sel])
            if clip is not None:
                v = np.maximum(v, clip)
            ax.errorbar(v[:, 0], yy, xerr=[v[:, 0] - v[:, 1], v[:, 2] - v[:, 0]], fmt="o", ms=4,
                        color=C[m], elinewidth=1.2, label=m if k == 2 else None)
    axes[0].axvline(0.5, color=INK2, lw=1)
    axes[1].axvline(0.0, color=INK2, lw=1)
    axes[0].set_yticks(y0)
    axes[0].set_yticklabels(groups, fontsize=8)
    axes[0].set_xlabel("AUC (0.5 = no ranking)", color=INK)
    axes[1].set_xlabel("Brier skill vs the fire's own base rate (clipped at -1.5)", color=INK)
    for a in axes:
        _style(a)
    axes[0].legend(fontsize=8, frameon=False, loc="lower left")
    fig.suptitle("v3, v4 and v5 per fire, 95 % block-bootstrap intervals (250 m blocks)", fontsize=10,
                 color=INK, x=0.02, ha="left")
    fig.tight_layout()
    fig.savefig(out / "fig_skill_vs_v3_v4.png", dpi=DPI)
    plt.close(fig)
    return [{"set": r[0], "model": r[1], "auc": r[2], "bss": r[3]} for r in rows]


def fig_map(npz: Path, out: Path, name: str, extra: dict | None = None):
    """Mean P per model and observed share destroyed per 250 m block (>= 3 scored buildings);
    km relative to the block-set centre, no basemap (aggregate only)."""
    if not npz.exists():
        return None
    z = np.load(npz)
    sm = z["scored"]
    x, y, o = z["x"][sm], z["y"][sm], z["obs"][sm].astype(float)
    panels = []
    for key, lab in (("P_V5", "v5"), ("P_V4", "v4"), ("P_V3", "v3"), ("B_v3_R37", "v3")):
        if key in z and lab not in [p[0] for p in panels]:
            panels.append((lab, z[key][sm]))
    for lab, v in (extra or {}).items():
        panels.append((lab, v[sm]))
    order = {"v5": 0, "v4": 1, "v3": 2}
    panels.sort(key=lambda t: order.get(t[0], 9))
    bx, by = np.floor(x / 250.0).astype(int), np.floor(y / 250.0).astype(int)
    keys, inv = np.unique(np.column_stack([bx, by]), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = np.bincount(inv)
    ok = n >= 3
    ob = np.bincount(inv, weights=o)[ok] / n[ok]
    kx, ky = (keys[ok, 0] + 0.5) * 0.25, (keys[ok, 1] + 0.5) * 0.25
    kx, ky = kx - kx.mean(), ky - ky.mean()
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQ)
    k = len(panels) + 1
    fig, axes = plt.subplots(1, k, figsize=(3.4 * k + 0.8, 3.9), sharex=True, sharey=True)
    stats = {}
    for ax, (lab, p) in zip(axes, panels):
        pb = np.bincount(inv, weights=p)[ok] / n[ok]
        r = float(np.corrcoef(pb, ob)[0, 1]) if len(pb) > 2 else float("nan")
        stats[lab] = {"block_r": r, "mean_p": float(p.mean())}
        sc = ax.scatter(kx, ky, c=pb, cmap=cmap, vmin=0, vmax=1, marker="s", s=10, linewidths=0)
        ax.set_title(f"{lab}: mean P (r = {r:.2f})", fontsize=9, color=INK, loc="left")
    axes[-1].scatter(kx, ky, c=ob, cmap=cmap, vmin=0, vmax=1, marker="s", s=10, linewidths=0)
    axes[-1].set_title(f"Observed share destroyed ({o.mean():.0%})", fontsize=9, color=INK, loc="left")
    for ax in axes:
        ax.set_aspect("equal")
        ax.set_xlabel("km (relative)", color=INK2, fontsize=8)
        _style(ax)
        ax.grid(False)
    fig.colorbar(sc, ax=axes, shrink=0.8, label="share of buildings (0-1)")
    fig.suptitle(f"{name}: predicted vs observed loss, 250 m blocks (n = {int(ok.sum())} blocks, "
                 f"{int(sm.sum())} buildings)", fontsize=10, color=INK, x=0.02, ha="left")
    fn = f"fig_map_{name.lower().replace(' ', '_')}.png"
    fig.savefig(out / fn, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return {"file": fn, "blocks": int(ok.sum()), "buildings": int(sm.sum()), "panels": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {"_about": {"purpose": "numbers shown in the v5 report figures", "status": "illustrative; "
                          "held-out values are PREREG_LR5 results, training values are LOFO",
                          "source": "scripts/long_range_v5_figures.py"}}
    fig_kernel_vs_storey(a.out)
    fig_diagnosis(a.results, a.out)
    fig_reliability(a.results, a.out, "training_LOFO",
                    {f: a.results / "lofo" / f"lofo_{f}.npz" for f in ("eaton", "palisades", "camp", "tubbs", "woolsey")})
    fig_reliability(a.results, a.out, "heldout",
                    {f: a.results / "heldout" / f"units_lr5_{f}.npz" for f in ("glass", "czu", "northcomplex")})
    summary["skill"] = fig_skill(a.results, a.out)
    summary["maps"] = []
    for f in ("eaton", "palisades", "camp", "tubbs", "woolsey"):
        summary["maps"].append(fig_map(a.results / "lofo" / f"lofo_{f}.npz", a.out, f"{f.capitalize()} LOFO"))
    for f in ("glass", "czu", "northcomplex"):
        p = a.results / "heldout" / f"units_lr5_{f}.npz"
        extra = None
        if p.exists():
            v4 = _v4_heldout(a.results, f, len(np.load(p)["P_V5"]))
            extra = {"v4": v4} if v4 is not None else None
        summary["maps"].append(fig_map(p, a.out, f"{f.capitalize()} held out", extra))
    (a.out / "figure_numbers.json").write_text(json.dumps(summary, indent=1, default=float))


if __name__ == "__main__":
    main()
