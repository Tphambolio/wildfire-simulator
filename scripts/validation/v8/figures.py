"""Figures for the v8 result report (reported only; reads the final/ outputs, changes nothing).

fig1_draw.png       cumulative counted jumps against eligible fires drawn, per region, with K_min / region floor
fig2_auc.png        S3-weighted AUC of M8, B1, B1all (and ELMFIRE on its subset) by region, with the 0.60 floor
fig3_af.png         per-fire a_f = mean(r_M8 - r_B1) against jumps per fire, with S3 and its bootstrap 95 % CI
fig4_secondary.png  AUC by gap class and distance band; predicted vs observed jumps per fire
Usage: python3 -m scripts.validation.v8.figures OUTDIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import common as C  # noqa: E402

FINAL = C.DATA / "final"
COL = {"M8": "#c0392b", "B1": "#2c7fb8", "B1all": "#7fb3d5", "ELMFIRE": "#6a3d9a"}


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    cnt = pd.read_csv(C.DATA / "draw" / "counts.csv")
    res = json.loads((FINAL / "score_out.json").read_text())
    sec = json.loads((FINAL / "secondary.json").read_text())
    R = pd.read_csv(FINAL / "ranks_per_jump.csv")

    # fig 1
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for r, c in (("CAN", "#d95f02"), ("US", "#1b9e77")):
        d = cnt[(cnt.region == r)].sort_values("rank")
        e = d[d.eligible.astype(bool)]
        ax.step(np.arange(1, len(e) + 1), e.K.cumsum(), where="post", color=c, label=f"{r}: {int(e.K.sum())} jumps, {len(e)} eligible of {len(d)} drawn")
    ax.axhline(60, ls=":", color="k", lw=1); ax.text(1, 62, "region floor 60", fontsize=8)
    ax.set_xlabel("eligible decision fires drawn (decision order)"); ax.set_ylabel("cumulative counted jumps (K)")
    ax.set_title(f"Sequential draw: total K = {int(cnt.K.sum())} (K_min 150)", fontsize=10)
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(out / "fig1_draw.png", dpi=150); plt.close(fig)

    # fig 2
    models = [m for m in ("M8", "B1", "B1all", "ELMFIRE") if m in R]
    regs = ["CAN", "US", "all"]
    fig, ax = plt.subplots(figsize=(6.5, 4))
    w = 0.8 / len(models)
    for i, m in enumerate(models):
        vals = []
        for g in regs:
            d = R if g == "all" else R[R.region == g]
            d = d.dropna(subset=[m])
            if not len(d):
                vals.append(np.nan); continue
            K = d.groupby("fire_key").size(); wf = K / (1 + 0.3 * (K - 1))
            per = d.groupby("fire_key")[m].mean()
            vals.append(float((wf * per).sum() / wf.sum()))
        ax.bar(np.arange(len(regs)) + i * w - 0.4 + w / 2, vals, w, color=COL[m], label=m)
        for x, v in zip(np.arange(len(regs)) + i * w - 0.4 + w / 2, vals):
            if np.isfinite(v):
                ax.text(x, v + 0.01, f"{v:.2f}", ha="center", fontsize=7)
    ax.axhline(0.60, ls="--", color="k", lw=1); ax.axhline(0.5, ls=":", color="grey", lw=1)
    ax.set_xticks(range(len(regs))); ax.set_xticklabels(regs); ax.set_ylim(0.3, 1.0)
    ax.set_ylabel("AUC (mean percentile rank of jump cells, S3 weights)")
    ax.legend(fontsize=8, ncol=len(models)); fig.tight_layout(); fig.savefig(out / "fig2_auc.png", dpi=150); plt.close(fig)

    # fig 3
    pf = pd.DataFrame(res["per_fire"])
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for r, c in (("CAN", "#d95f02"), ("US", "#1b9e77")):
        d = pf[pf.region == r]
        ax.scatter(d.K, d.a_f, s=18, color=c, alpha=0.75, label=r)
    s3 = res["S3"]
    ax.axhline(0, color="grey", lw=1)
    ax.axhline(s3["est"], color="k", lw=1.5, label=f"S3 = {s3['est']:+.3f} (95 % {s3['boot95'][0]:+.3f} to {s3['boot95'][1]:+.3f}); p = {s3['p_signflip']:.3f}")
    ax.axhspan(s3["boot95"][0], s3["boot95"][1], color="k", alpha=0.1)
    ax.set_xscale("log"); ax.set_xlabel("ranked jumps in the fire (K_f)"); ax.set_ylabel("a_f = mean(r_M8 − r_B1)")
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(out / "fig3_af.png", dpi=150); plt.close(fig)

    # fig 4
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    for ax, key, title in ((axs[0], "gap", "by gap class"), (axs[1], "dband", "by distance beyond the start perimeter")):
        g = sec["auc"].get(key, {})
        labs = [k for k in g if k not in ("nan", "None")]
        for i, m in enumerate(models):
            ax.bar(np.arange(len(labs)) + i * w - 0.4 + w / 2, [g[k].get(m) or np.nan for k in labs], w, color=COL[m], label=m)
        ax.set_xticks(range(len(labs))); ax.set_xticklabels([f"{k}\n(n={g[k]['n_jumps']})" for k in labs], fontsize=8)
        ax.axhline(0.5, ls=":", color="grey"); ax.set_ylim(0.3, 1.0); ax.set_title(title, fontsize=10)
    axs[0].set_ylabel("AUC (S3 weights)"); axs[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "fig4_secondary.png", dpi=150); plt.close(fig)
    print("figures written to", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
