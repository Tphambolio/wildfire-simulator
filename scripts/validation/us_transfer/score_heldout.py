"""Score PREREG_US_TRANSFER1 (docs/PREREG_US_TRANSFER1.md §6-§7): FIX1 vs base on the held-out US fires.

Purpose: the registered decision, computed only from the four run files of ``sens_run.py`` on the held-out root
(``base``, ``fix1`` deterministic; ``ens_base``, ``ens_fix1`` 20-member ensembles) and, for the Canadian guard, the
``base`` / ``fix1`` files on the Canadian development fire-days. Nothing here is tuned; thresholds are §7's.

Metrics (17 h, deterministic member unless stated; L = log10(pred + 10 ha) - log10(obs + 10 ha)):
  P1 paired mean |L| (fix - base), 95 % whole-fire bootstrap CI (2,000 draws, seed 1): upper bound < -0.10;
  P2 log-area bias of FIX1: |mean L_fix| <= 0.50 (point);
  P3 17 h F1 non-inferiority: paired mean F1 (fix - base), lower bound >= -0.02 (NaN F1 = 0);
  P4 ensemble P10-P90 coverage of the observed log10(area + 10 ha), FIX1 ensemble: >= 0.60 (point) and paired
     coverage gain (fix - base) lower bound > 0;
  U  under-prediction safety guard (deciding): on U = fire-days whose observed 17 h growth is in the top tercile
     of the held-out deterministic set (>= its 66.7th percentile) or >= 2,000 ha: (U1) share with L_fix < -0.5
     <= share with L_base < -0.5 + 0.05 (points); (U2) mean L_fix on U >= -0.25 (point). Reported: both shares,
     mean L on U, mean L on the L < -0.5 days, CIs.
  G  Canadian guard: on every Canadian development fire-day run, FIX1's 17 h modelled area equals base's (FIX1 is
     US-only by construction); any difference = FAIL of the guard.
PASS iff P1-P4, U and G all hold (U fails FIX1 whatever |L| does) and the minimum data rule holds (>= 15 fires and >= 30 fire-days with both base
and fix1 for P1-P3; >= 10 fires and >= 25 fire-days for P4); otherwise FAIL (or INCONCLUSIVE when data are short).

Usage: ``python3 -m scripts.validation.us_transfer.score_heldout <heldout runs dir> <canada runs dir> <out dir>``
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

NB, SEED = 2000, 1


def L(p, o):
    return math.log10(p + 10.0) - math.log10(o + 10.0)


def rd(p: Path) -> dict:
    out = {}
    if p.exists():
        for x in p.read_text().splitlines():
            r = json.loads(x)
            if "error" not in r:
                out[(r["fire_id"], r["day"])] = r
    return out


def boot(v, f, stat=np.mean):
    v, f = np.asarray(v, float), np.asarray(f)
    uf = np.unique(f)
    idx = {k: np.nonzero(f == k)[0] for k in uf}
    rng = np.random.default_rng(SEED)
    bs = [stat(v[np.concatenate([idx[k] for k in rng.choice(uf, len(uf))])]) for _ in range(NB)]
    return [float(stat(v)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)), int(len(v)), int(len(uf))]


def inside(members, obs):
    lm = np.log10(np.asarray(members) + 10.0)
    lo, hi = np.percentile(lm, 10), np.percentile(lm, 90)
    y = math.log10(obs + 10.0)
    return float(lo <= y <= hi)


def main():
    ho, ca, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    b, f = rd(ho / "base.jsonl"), rd(ho / "fix1.jsonl")
    keys = sorted(set(b) & set(f))
    fz = [k[0] for k in keys]
    Lb = np.array([L(b[k]["17h"]["pred_ha"], b[k]["17h"]["obs_ha"]) for k in keys])
    Lf = np.array([L(f[k]["17h"]["pred_ha"], f[k]["17h"]["obs_ha"]) for k in keys])
    f1 = lambda r: r["17h"]["f1"] if r["17h"]["f1"] == r["17h"]["f1"] else 0.0
    Fb = np.array([f1(b[k]) for k in keys]); Ff = np.array([f1(f[k]) for k in keys])
    res = {"n_fire_days": len(keys), "n_fires": len(set(fz))}
    res["P1_dabsL"] = boot(np.abs(Lf) - np.abs(Lb), fz)
    res["mean_absL"] = {"base": boot(np.abs(Lb), fz), "fix1": boot(np.abs(Lf), fz)}
    res["P2_bias"] = {"base": boot(Lb, fz), "fix1": boot(Lf, fz)}
    res["P3_dF1"] = boot(Ff - Fb, fz)
    res["F1"] = {"base": boot(Fb, fz), "fix1": boot(Ff, fz)}
    eb, ef = rd(ho / "ens_base.jsonl"), rd(ho / "ens_fix1.jsonl")
    ek = sorted(set(eb) & set(ef))
    efz = [k[0] for k in ek]
    cb = np.array([inside(eb[k]["member_ha"], eb[k]["obs_ha"]) for k in ek])
    cf = np.array([inside(ef[k]["member_ha"], ef[k]["obs_ha"]) for k in ek])
    res["P4_coverage"] = {"n_fire_days": len(ek), "n_fires": len(set(efz)),
                          "base": boot(cb, efz) if ek else None, "fix1": boot(cf, efz) if ek else None,
                          "gain": boot(cf - cb, efz) if ek else None}
    cb_, cf_ = rd(ca / "base.jsonl"), rd(ca / "fix1.jsonl")
    ck = sorted(set(cb_) & set(cf_))
    diff = [k for k in ck if abs(cb_[k]["17h"]["pred_ha"] - cf_[k]["17h"]["pred_ha"]) > 1e-9]
    res["G_canada"] = {"n_fire_days": len(ck), "n_different": len(diff), "pass": len(ck) > 0 and not diff}
    enough = res["n_fires"] >= 15 and res["n_fire_days"] >= 30
    enough4 = len(set(efz)) >= 10 and len(ek) >= 25
    obs = np.array([b[k]["17h"]["obs_ha"] for k in keys])
    u = (obs >= np.percentile(obs, 200.0 / 3.0)) | (obs >= 2000.0) if len(obs) else np.zeros(0, bool)
    uf = [x for x, m in zip(fz, u) if m]
    sb, sf = float(np.mean(Lb[u] < -0.5)) if u.any() else math.nan, float(np.mean(Lf[u] < -0.5)) if u.any() else math.nan
    res["U_underprediction"] = {
        "n_fire_days": int(u.sum()), "n_fires": len(set(uf)), "obs_threshold_ha": float(min(np.percentile(obs, 200.0 / 3.0), 2000.0)) if len(obs) else None,
        "share_L_lt_-0.5": {"base": sb, "fix1": sf,
                            "fix1_ci": boot((Lf[u] < -0.5).astype(float), uf) if u.any() else None},
        "mean_L_on_U": {"base": boot(Lb[u], uf) if u.any() else None, "fix1": boot(Lf[u], uf) if u.any() else None},
        "mean_L_fix1_on_days_L_lt_-0.5": float(Lf[u][Lf[u] < -0.5].mean()) if (Lf[u] < -0.5).any() else None,
        "U1": bool(u.any() and sf <= sb + 0.05), "U2": bool(u.any() and float(Lf[u].mean()) >= -0.25)}
    # descriptive subsets (decide nothing): early-stage / little held edge / non-mature
    def sub(mask):
        q = [i for i, m in enumerate(mask) if m]
        if not q:
            return None
        return {"n": len(q), "mean_L_base": float(Lb[q].mean()), "mean_L_fix1": float(Lf[q].mean()),
                "share_fix1_L_lt_-0.5": float(np.mean(Lf[q] < -0.5)), "dF1": float((Ff[q] - Fb[q]).mean())}
    st_ = [b[k].get("start_type") for k in keys]
    so = [((b[k].get("start_perimeter_obs") or {}).get("static_share")) for k in keys]
    res["descriptive_subsets"] = {
        "non_mature_start": sub([x != "mature" for x in st_]),
        "little_held_edge_obs_static_lt_0.9": sub([x is not None and x < 0.9 for x in so]),
        "early_start_age_le_3d": sub([(b[k].get("start_age_days") or 0) <= 3 for k in keys]),
        "free_running_prev_day_share_ge_0.3": sub([(b[k].get("start_prev_day_share") or 0) >= 0.3 for k in keys])}
    c = {"P1": res["P1_dabsL"][2] < -0.10, "P2": abs(res["P2_bias"]["fix1"][0]) <= 0.50,
         "P3": res["P3_dF1"][1] >= -0.02,
         "P4": bool(ek) and res["P4_coverage"]["fix1"][0] >= 0.60 and res["P4_coverage"]["gain"][1] > 0,
         "U": res["U_underprediction"]["U1"] and res["U_underprediction"]["U2"],
         "G": res["G_canada"]["pass"]}
    res["conditions"] = c
    res["verdict"] = "INCONCLUSIVE" if not (enough and enough4) else ("PASS" if all(c.values()) else "FAIL")
    res["_about"] = {"script": "scripts/validation/us_transfer/score_heldout.py", "prereg": "docs/PREREG_US_TRANSFER1.md"}
    (out / "result_us_transfer1.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("n_fire_days", "n_fires", "conditions", "verdict")}))


if __name__ == "__main__":
    main()
