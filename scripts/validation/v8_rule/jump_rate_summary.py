#!/usr/bin/env python3
"""jump_rate_summary.py -- measured VIIRS jump rate on the TRAINING third (pre-freeze check 3; OD9 test).

Addendum A1 (PREREG_LR8, 2026-10-24) version. Identical to the frozen pre-freeze script
(sha256 a2aa23f0...815b) except: (1) the rule masks are built by ``rule_masks`` (same expressions); (2) a
count rule ``count_secondary`` is added: the primary rule without the inside-final condition, keeping members
within 375 m of the final perimeter (the positives) AND clusters whose member is > 540 m outside it ("never
reached"), as PREREG_LR8 section 8a.2 says they "enter the count and Brier secondaries"; 375-540 m clusters are
neither (counted separately, never in K); per-fire columns K_never_reached and K_out_375_540 are written;
(3) paths: reads and writes FIRESIM_V8_RULE_OUT (default validation-data/v8-pool/rule_A1/), never the frozen
folder. The primary (ranked) rule and every sensitivity are unchanged.

Purpose
    Turn the training-mode jump catalogue (v8_viirs_jumps.py) into per-fire jump counts under the frozen
    rule and its sensitivities, fit a negative binomial (mean mu, dispersion k) by maximum likelihood, give a
    fire-bootstrap 95 % interval for mu, and state whether OD9 (< 0.25 detected jumps per fire) triggers.
Rules
    primary   distance 1-15 km (the candidate band; > 15 km reported separately), line check passed, inside the final perimeter (375 m tolerance), >= 2 km from a
              separate start, interval <= 12 h  (design section 8.4 as implemented)
    int14h    as primary with intervals <= 14 h
    int24h    as primary with intervals <= 24 h
    d2km      as primary with distance >= 2 km (the design's secondary threshold)
    no_sep    as primary without the separate-start mask
    strict    as primary, excluding jumps whose gap mid-point was burned but never detected (possible front runs)
Fire sets
    open_training: the seeded one-third (assignment 'training'); all_training adds the legacy fires.
    eligible: fires with >= 2 valid (<= 12 h) intervals (the design's VIIRS-side eligibility; the station and
    fuel checks are not applied here).
Outputs
    results/fires_training.csv, results/jump_rate_summary.json
"""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

RES = Path(os.environ.get("FIRESIM_V8_RULE_OUT", "/home/rpas/dev/wildfire/validation-data/v8-pool/rule_A1"))


def nb_fit(x):
    x = np.asarray(x, float)
    mu = x.mean()
    if mu == 0:
        return 0.0, float("nan")

    def nll(lk):
        k = np.exp(lk)
        return -np.sum(gammaln(x + k) - gammaln(k) - gammaln(x + 1) + k * np.log(k / (k + mu)) + x * np.log(mu / (k + mu)))
    r = minimize(lambda v: nll(v[0]), [0.0], method="Nelder-Mead")
    return float(mu), float(np.exp(r.x[0]))


def summarise(K, rng):
    mu, k = nb_fit(K)
    bs = [rng.choice(K, len(K)).mean() for _ in range(4000)]
    return dict(n_fires=int(len(K)), jumps=int(K.sum()), mu_hat=mu, k_hat=k, mu_lo95=float(np.percentile(bs, 2.5)),
                mu_hi95=float(np.percentile(bs, 97.5)), share_zero=float(np.mean(K == 0)), max=int(K.max()) if len(K) else 0,
                share_jumps_top10pct_fires=float(np.sort(K)[::-1][:max(1, len(K) // 10)].sum() / max(K.sum(), 1)))


def rule_masks(J: pd.DataFrame) -> dict:
    """Row masks of the catalogue per rule (frozen expressions) plus the addendum-A1 rule-2 classes."""
    base = J.line_ok & J.inside_final & (J.dist_m <= 15000)  # counted jumps lie in the 1-15 km candidate band
    rules = {
        "primary": base & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000),
        "int14h": base & J.sep_ok & (J.interval_h <= 14) & (J.dist_m >= 1000),
        "int24h": base & J.sep_ok & (J.interval_h <= 24) & (J.dist_m >= 1000),
        "d2km": base & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 2000),
        "no_sep": base & (J.interval_h <= 12) & (J.dist_m >= 1000),
        "strict": base & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.gap != "never_detected"),
    }
    # addendum A1 (section 8a.2): same filters as primary except the final-perimeter condition
    outer = J.line_ok & J.sep_ok & (J.interval_h <= 12) & (J.dist_m >= 1000) & (J.dist_m <= 15000) & ~J.inside_final
    never = outer & (J.out_final_m > 540)
    band = outer & (J.out_final_m <= 540)
    rules["count_secondary"] = rules["primary"] | never
    return dict(rules=rules, never_reached=never, out_375_540=band)


def main():
    J = pd.read_csv(RES / "jumps_training.csv")
    F = pd.read_csv(RES / "fires_training_raw.csv")
    F["n_int_12h"] = F.n_int_12h.fillna(0)
    M = rule_masks(J)
    rules = M["rules"]
    for name in ("never_reached", "out_375_540"):
        F["K_" + name] = F.fire_key.map(J[M[name]].groupby("fire_key").size()).fillna(0).astype(int)
    rng = np.random.default_rng(20261022)
    out = dict(rules={}, notes={})
    for name, m in rules.items():
        c = J[m].groupby("fire_key").size()
        F["K_" + name] = F.fire_key.map(c).fillna(0).astype(int)
        R = {}
        for setname, sel in [("open_training", F.assignment == "training"), ("all_training", F.assignment.notna())]:
            R[setname] = summarise(F.loc[sel, "K_" + name].values, rng)
            R[setname + "_eligible"] = summarise(F.loc[sel & (F.n_int_12h >= 2), "K_" + name].values, rng)
        R["by_region_open_training"] = {r: summarise(F.loc[(F.assignment == "training") & (F.region == r), "K_" + name].values, rng)
                                        for r in ("CAN", "US")}
        R["by_region_open_training_eligible"] = {r: summarise(F.loc[(F.assignment == "training") & (F.region == r) & (F.n_int_12h >= 2),
                                                                    "K_" + name].values, rng) for r in ("CAN", "US")}
        out["rules"][name] = R
    P = J[rules["primary"]]
    out["primary_jump_details"] = dict(
        gap_class_share=P.gap.value_counts(normalize=True).round(3).to_dict(),
        daynight_share=P.daynight.value_counts(normalize=True).round(3).to_dict(),
        satellite_share=P.satellite.value_counts(normalize=True).round(3).to_dict(),
        dist_m_quantiles={q: float(P.dist_m.quantile(q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)},
        share_beyond_3km_of_beyond_1km=float((P.dist_m >= 3000).mean()) if len(P) else None,
        by_year=P.groupby("year").size().to_dict(),
        candidate_clusters_ge1km=int((J.dist_m >= 1000).sum()),
        beyond_15km_otherwise_qualifying=int((J.line_ok & J.inside_final & J.sep_ok & (J.interval_h <= 12) & (J.dist_m > 15000)).sum()),
        removed_by=dict(line_check=int((~J.line_ok & (J.dist_m >= 1000)).sum()),
                        outside_final=int((~J.inside_final & (J.dist_m >= 1000)).sum()),
                        separate_start=int((~J.sep_ok & J.line_ok & J.inside_final & (J.dist_m >= 1000)).sum()),
                        interval_gt12h=int((J.interval_h > 12).sum())),
        # review rule 2 (PREREG_LR8 section 8a): clusters that would be jumps but whose member is > 540 m outside
        # the final perimeter are 'never reached' (count/Brier only); 375-540 m are neither positive nor negative
        never_reached_gt540m=int((J.line_ok & J.sep_ok & ~J.inside_final & (J.out_final_m > 540) & (J.interval_h <= 12)
                                  & (J.dist_m >= 1000)).sum()),
        outside_375_540m=int((J.line_ok & J.sep_ok & ~J.inside_final & (J.out_final_m <= 540) & (J.interval_h <= 12)
                              & (J.dist_m >= 1000)).sum()))
    out["intervals"] = dict(
        median_valid_12h_per_fire=float(F.loc[F.assignment == "training", "n_int_12h"].median()),
        share_fires_eligible=float((F.loc[F.assignment == "training", "n_int_12h"] >= 2).mean()),
        total_valid_12h_open=int(F.loc[F.assignment == "training", "n_int_12h"].sum()),
        total_valid_14h_open=int(F.loc[F.assignment == "training", "n_int_14h"].fillna(0).sum()),
        total_valid_24h_open=int(F.loc[F.assignment == "training", "n_int_24h"].fillna(0).sum()))
    mu = out["rules"]["primary"]["open_training"]["mu_hat"]
    out["OD9"] = dict(threshold=0.25, measured_mu_open_training=mu, triggers=bool(mu < 0.25))
    F.to_csv(RES / "fires_training.csv", index=False)
    json.dump(out, open(RES / "jump_rate_summary.json", "w"), indent=1, default=float)
    print(json.dumps({k: out["rules"][k]["open_training"] for k in out["rules"]}, indent=1))
    print(json.dumps(out["rules"]["primary"]["by_region_open_training"], indent=1))
    print(json.dumps(out["primary_jump_details"], indent=1, default=str), json.dumps(out["intervals"]), out["OD9"])


if __name__ == "__main__":
    main()
