#!/usr/bin/env python3
"""power_stage2.py -- power of the stage-2 deciding comparisons on the v8 confirmation set (PREREG_ML_EMBER section 2).

Purpose
    PREREG_ML_EMBER_DEV section 11 / 12 item 2 require the power of the deciding comparisons to be recomputed from the
    confirmation set's observed counts once v8 has reported. This script does that with the frozen v8 power generator
    (reports/data/v8-ember-study-design-2026-10-18/scripts/power_sim.py, imported by path, unchanged; binormal scores,
    fire-level heterogeneity tau_b / tau_d, intra-fire clustering rho_w, between-model score correlation rho_s) and the
    fire-level one-sided sign-flip test on S3 (p < 0.025, 20,000 flips in the real test; 2,000 here per simulated study).

What is conditioned on (aggregate counts only, from the v8 result report [R70] and its figure captions; no per-jump
file, no rank, no location was read):
    * 73 fires with ranked jumps: Canada 29 fires / 83 jumps, US 44 fires / 76 jumps (159 ranked jumps in all);
    * per-fire jump counts drawn zero-truncated negative binomial with the training dispersion (k 0.21 CAN, 0.39 US),
      the untruncated mean solved so that the truncated mean equals the observed jumps per fire with jumps;
    * the reference model's AUC per region: M8 0.710 (CAN) / 0.642 (US) for comparisons against M8 ([R70] criterion 4);
      0.85 for the distance-only comparator (development value, PREREG A-memo section 4; the confirmation value is
      unknown by design), both bracketed by +/- 0.05.

Comparisons sized
    A  rung 2 (clean arm) - M8          rho_s 0.6 / 0.8   Delta 0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.27
    B  rung 2 (clean arm) - distance-only logistic   rho_s 0.8 / 0.9   Delta 0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08
    C  offset hybrid - M8               rho_s 0.8 / 0.9   Delta as A (the hybrid contains M8 as its offset)

Outputs (reports/data/ml-ember-cast-2026-10-29/stage2/)
    power_stage2.json    power, false-pass rate and SD of the S3 estimate per comparison, rho_s, reference AUC and Delta
    fig_power_stage2.png power curves (caption in the data folder README)

Usage
    CAPJOB_MEM=2G capjob python3 -I scripts/ml_ember/power_stage2.py [--sims 1000] [--flips 2000] [--seed 2026103001]

Caveats
    Binormal scores and tau_b 0.35, tau_d 0.25, rho_w 0.30 are the v8 design's hypotheses [H]. Conditioning on the
    observed fires-with-jumps and jump totals removes the draw variance; the power is for this confirmation set.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.ml_ember import paths as P  # noqa: E402

GEN = Path("/home/rpas/dev/wildfire/reports/data/v8-ember-study-design-2026-10-18/scripts/power_sim.py")
spec = importlib.util.spec_from_file_location("power_sim", GEN)
PS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PS)

OUT = P.DATA_FOLDER / "stage2"
RHO_PRE = 0.30
S_POWER = 2026103001

# aggregate counts of the confirmation set ([R70] section "jumps" table and Fig. 1 / fuel-seam addendum: 29 CAN fires with
# ranked jumps holding 83 jumps; 73 fires and 159 ranked jumps in all)
CONF = [dict(region="CAN", n_fires=29, jumps=83, k=0.21, auc_m8=0.710),
        dict(region="US", n_fires=44, jumps=76, k=0.39, auc_m8=0.642)]


def mu_truncated(mean_pos: float, k: float) -> float:
    """Untruncated NB mean whose zero-truncated mean equals mean_pos."""
    f = lambda mu: mu / (1.0 - (k / (k + mu)) ** k) - mean_pos  # noqa: E731
    return brentq(f, 1e-6, 10 * mean_pos)


def s3_signflip(regions, rng, flips, alpha=0.025):
    a_all, w_all = [], []
    for K, sm, sb in regions:
        has = K > 0
        Kc = np.maximum(K, 1)
        a = (sm - sb) / Kc
        w = K / (1.0 + (Kc - 1.0) * RHO_PRE) * has
        a_all.append(a[has]); w_all.append(w[has])
    a = np.concatenate(a_all); w = np.concatenate(w_all)
    est = float((w * a).sum() / w.sum())
    signs = rng.choice([-1.0, 1.0], size=(flips, len(a)))
    null = (signs * (w * a)).sum(axis=1) / w.sum()
    p = float((np.sum(null >= est) + 1) / (flips + 1))
    return est, p < alpha


def sim_region_truncated(rng, n, mu, delta, p):
    """n fires that all hold >= 1 jump: draw with the generator, keep fires with K > 0 until n are in hand."""
    Ks, sms, sbs = [], [], []
    got = 0
    while got < n:
        K, sm, sb = PS.sim_region(rng, 4 * n, mu, delta, p)
        keep = K > 0
        Ks.append(K[keep]); sms.append(sm[keep]); sbs.append(sb[keep]); got += int(keep.sum())
    K = np.concatenate(Ks)[:n]; sm = np.concatenate(sms)[:n]; sb = np.concatenate(sbs)[:n]
    return K, sm, sb


def run(rng, comparison, auc_ref_by_region, rho, delta, sims, flips):
    passes, ests, Ks = 0, [], []
    for _ in range(sims):
        regs = []
        for c in CONF:
            p = PS.base_params(); p.update(auc_b=auc_ref_by_region[c["region"]], rho_s=rho, k=c["k"])
            mu = mu_truncated(c["jumps"] / c["n_fires"], c["k"])
            regs.append(sim_region_truncated(rng, c["n_fires"], mu, delta, p))
        est, ok = s3_signflip(regs, rng, flips)
        passes += ok; ests.append(est); Ks.append(sum(r[0].sum() for r in regs))
    e = np.array(ests)
    return dict(comparison=comparison, rho_s=rho, delta=delta, power=passes / sims, est_mean=float(e.mean()),
                est_sd=float(e.std()), K_mean=float(np.mean(Ks)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--flips", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=S_POWER)
    a = ap.parse_args(argv)
    rng = np.random.default_rng(a.seed)
    t0 = time.time()
    m8 = {c["region"]: c["auc_m8"] for c in CONF}
    dist = {c["region"]: 0.85 for c in CONF}
    plan = {
        "A_rung2_minus_M8": dict(ref=m8, rhos=(0.6, 0.8), deltas=(0.0, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.27)),
        "B_rung2_minus_distance": dict(ref=dist, rhos=(0.8, 0.9), deltas=(0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08)),
        "C_hybrid_minus_M8": dict(ref=m8, rhos=(0.8, 0.9), deltas=(0.0, 0.03, 0.05, 0.08, 0.10)),
        # brackets of the reference AUC for the two sized margins (sensitivity)
        "A_rung2_minus_M8_ref-0.05": dict(ref={k: v - 0.05 for k, v in m8.items()}, rhos=(0.8,), deltas=(0.08,)),
        "A_rung2_minus_M8_ref+0.05": dict(ref={k: v + 0.05 for k, v in m8.items()}, rhos=(0.8,), deltas=(0.08,)),
        "B_rung2_minus_distance_ref0.80": dict(ref={k: 0.80 for k in dist}, rhos=(0.9,), deltas=(0.04, 0.05)),
        "B_rung2_minus_distance_ref0.90": dict(ref={k: 0.90 for k in dist}, rhos=(0.9,), deltas=(0.04, 0.05)),
    }
    out = dict(settings=dict(sims=a.sims, flips=a.flips, seed=a.seed, generator=str(GEN), base=PS.base_params(),
                             confirmation_counts=CONF, test="one-sided fire-level sign-flip on S3, p < 0.025",
                             truncation="zero-truncated NB per fire with jumps; untruncated mean solved so the truncated mean "
                                        "equals jumps / fires-with-jumps"),
               results=[])
    for name, pl in plan.items():
        for rho in pl["rhos"]:
            for d in pl["deltas"]:
                r = run(rng, name, pl["ref"], rho, d, a.sims, a.flips); r["ref_auc"] = pl["ref"]; out["results"].append(r)
                print(f"{name} rho={rho} d={d:+.2f}: power {r['power']:.3f} sd {r['est_sd']:.3f} K~{r['K_mean']:.0f} ({time.time() - t0:.0f}s)", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "power_stage2.json").write_text(json.dumps(out, indent=1))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=150)
    cols = {"A_rung2_minus_M8": "#d62728", "B_rung2_minus_distance": "#1f77b4", "C_hybrid_minus_M8": "#2ca02c"}
    labels = {"A_rung2_minus_M8": "A: rung 2 - M8 (ref AUC 0.71 CAN / 0.64 US)",
              "B_rung2_minus_distance": "B: rung 2 - distance-only (ref AUC 0.85)",
              "C_hybrid_minus_M8": "C: offset hybrid - M8"}
    for name, col in cols.items():
        rows = [r for r in out["results"] if r["comparison"] == name]
        for rho in sorted({r["rho_s"] for r in rows}):
            rr = sorted([r for r in rows if r["rho_s"] == rho], key=lambda r: r["delta"])
            ax.plot([r["delta"] for r in rr], [r["power"] for r in rr], "-" if rho == max(x["rho_s"] for x in rows) else "--",
                    color=col, marker="o", ms=3.5, label=f"{labels[name]}, rho_s {rho}")
    ax.axhline(0.80, color="k", lw=0.8, ls=":"); ax.axhline(0.025, color="k", lw=0.5, ls=":")
    ax.set_xlabel("true S3 gain (Delta)"); ax.set_ylabel("probability the sign-flip test passes (p < 0.025)")
    ax.set_ylim(0, 1); ax.set_xlim(-0.003, 0.28); ax.grid(alpha=0.3); ax.legend(fontsize=6.5, loc="lower right")
    ax.set_title("Stage-2 power on the v8 confirmation set (73 fires, 159 ranked jumps)", fontsize=9)
    fig.tight_layout(); fig.savefig(OUT / "fig_power_stage2.png")
    print(f"done in {time.time() - t0:.0f}s -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
