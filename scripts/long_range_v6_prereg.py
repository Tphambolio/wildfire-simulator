#!/usr/bin/env python3
"""PREREG_LR6: pre-registration of the long-range embers v6 held-out test (targets A and B).

Purpose: fixes, before any held-out fire is scored, the models, fitted values, metrics, pass
criteria, held-out fires, bootstrap and disclosures for ``scripts/long_range_v6_arrival.py`` (target A)
and ``scripts/long_range_v6_loss.py`` (target B). Human-readable copy: ``docs/PREREG_LR6.md``.
Its SHA-256 (``long_range_v6_arrival.prereg_hash()``) is written into every held-out result file.
Do not edit after the commit that adds it; add a dated block instead and report both.

Run: python3 scripts/long_range_v6_prereg.py   (prints the block and its hash)
References: see docs/PREREG_LR6.md.
"""

import hashlib
import json

PREREG_LR6 = {
    "date_fixed": "2026-10-13",
    "decision": "Owner (Travis Kennedy, EOC Chief of Plans), 2026-10-13: ember validation is split into "
                "(A) ember transport and community arrival and (B) building loss with a combined model.",
    "questions": {
        "A": "Do long-range ember spot fires (V6) make FireSim's predicted fire-arrival times at community "
             "blocks closer to the observed arrival than FireSim without long-range embers (A0: front + "
             "short-range spotting), on held-out fires, without worse probabilistic reach forecasts?",
        "B": "Does a combined building-loss model (density, year built, separation, canopy 0-30 m, and "
             "target A's fire reach and landed embers) discriminate destroyed from surviving buildings "
             "better than density + year built on held-out fires?",
    },
    "heldout_fires": ["glass", "czu", "northcomplex"],
    "training_fires": ["eaton", "palisades", "camp", "tubbs", "woolsey"],
    "not_added": {
        "lahaina": "no open timed-arrival data prepared or verified on disk; no blind set-up; outside the "
                   "CAL FIRE DINS frame; adding a new set-up after seeing v6 training behaviour risks tuning",
        "marshall": "swapped out in the v4 selection (no 'No Damage' class in the public Boulder County views); "
                    "no prepared timing data",
    },
    "target_A": {
        "models": {
            "A0": "run_cellular_simulation(enable_spotting=True, spot_sample_spacing_m=150, dt 10 min), fixed "
                  "spotting seeding (commit 64e6ac7)",
            "V6": "A0 + LongRangeSpotter(long='storey', eta=ETA_FINAL)",
        },
        "eta_final": 1e-8,
        "eta_fit": {
            "grid": [1e-8, 1e-7, 1e-6, 3e-6, 1e-5], "members": 10, "grid_m": 180,
            "objective": "J = (MAE_V6/MAE_A0 + Brier_V6/Brier_A0)/2 per training fire; eta minimising the mean "
                         "J over the five training fires (LOFO CV reported)",
            "objective_change": "Changed on 2026-10-13 BEFORE any held-out score, from MAE alone. Reason: the "
                                "first training scores showed MAE falling monotonically with eta on the fires "
                                "whose observations are one-sided (Eaton 3/62 and Tubbs 0/113 two-sided blocks: "
                                "an early model is never penalised) while the Brier of P(reached) rose sharply "
                                "(Tubbs 0.050 -> 0.330, Eaton 0.130 -> 0.384 at eta 1e-5). MAE alone would pick "
                                "the largest eta; J weights both pre-registered conditions equally.",
            "mean_J": {"1e-08": 0.966, "1e-07": 1.041, "1e-06": 1.452, "3e-06": 1.658, "1e-05": 1.996},
            "lofo_J": {"eaton": 1.029, "palisades": 0.947, "camp": 0.997, "tubbs": 0.801, "woolsey": 1.055},
            "note": "the optimum is at the lower edge of the grid: one eta for all fires is pulled down by Woolsey, "
                    "Eaton and Tubbs (embers make arrival too early / add false alarms) while Camp and Palisades "
                    "improve up to 1e-5 and 1e-7 respectively (training table in fit_v6_arrival.json)",
        },
        "secondary_V6_2s": {"eta": 1e-7, "why": "eta minimising mean J on the training fires with mostly two-sided "
                            "observations (Camp 154/204, Palisades 115/127): J 0.883 at 1e-7 vs 0.888 at 1e-6; "
                            "reported as a secondary model, never decides; also S2's eta x 10"},
        "members_heldout": 20,
        "seeds": "member draw default_rng([20261013, m]); spotting seed 20261013*100+m (A0 Albini), "
                 "202610130+m (V6 Poisson)",
        "setup": "90 m grid (perimeter box + 4 km), proxy fuel/FFMC/DMC/DC/wind ranges/origins of FIRES4 "
                 "(v4, unchanged), WorldCover 2021 water/bare >= 0.5 non-fuel, built-up >= 0.25 town ROS x 0.5, "
                 "3DEP slope, hourly wind = member speed x station gain (station / its 75th pct, clipped 0.3-1.5), "
                 "CZU and North Complex start from the GOFER perimeter at the run start",
        "units": "community blocks 540 m (6 x 6 cells) with >= 10 buildings (Microsoft US v2 footprint centroids)",
        "observation": "interval [t_lo, t_hi]: t_hi = min(first VIIRS 375 m detection within 375 m of the block "
                       "centre, first GOFER/FIRIS perimeter containing it, first NIST point); t_lo = max(last "
                       "earlier perimeter not containing it, previous VIIRS overpass, NIST - 30 min); only "
                       "observations before t_hi; blocks with t_hi <= run end are scored",
        "primary_A1": "pooled over held-out blocks: MAE (h) of the distance of the ensemble-median block arrival "
                      "(not reached = run end) from the observed interval; test MAE(V6) - MAE(A0)",
        "co_primary_A2": "Brier of P(block reached by hour tau), tau = every hour of the run, statuses known only "
                         "(reached: t_hi <= tau; not reached: t_lo >= tau or block outside final perimeter + 1 "
                         "block); per-block mean; test Brier(V6) - Brier(A0)",
        "pass_A": "embers add skill on target A iff A1 upper 95 % CI < 0 AND A2 lower 95 % CI <= 0 "
                  "(V6 not significantly worse in Brier)",
        "transport_check_A3": "reported, not a pass condition: pooled V6 long-range spot-fire distances to the "
                              "fire at ignition, tail ratio P(>3 km)/P(>1 km) vs Storey et al. 2020 Fire 3:10 p.7 "
                              "(0.02/0.10 = 0.20), consistent if in [0.10, 0.40]; maximum distance vs Cruz et al. "
                              "2012 p.269 (33 km)",
        "secondary": ["MAE per fire", "share of blocks within 1 h", "share of blocks where the observed upper "
                      "bound precedes the ensemble 10th percentile (late warning)", "ahead-ignition hit rate "
                      "(VIIRS jumps > 1 km from earlier detections and outside earlier perimeters; model fire "
                      "within one block by t + 30 min)", "share of spot fires outside the final perimeter + 1 block",
                      "spot fires per member", "maps of block arrival error (aggregate)"],
    },
    "target_B": {
        "models": {
            "B_dens": "log(1 + buildings within 100 m)",
            "B_dens_age": "+ (year built - 1950)/10, missing flag (DINS YEARBUILT)",
            "B_comb_A0": "+ log nearest-neighbour separation, canopy 0-30 m (+ missing flag), P(reach) from A0",
            "B_comb_V6": "B_comb_A0 with P(reach) from V6 + log10(1 + landed embers per m2) from V6",
        },
        "fit": "logistic regression, standardised, L2 C=1, fitted on all five training fires (LOFO CV reported)",
        "labels": "Destroyed (>50%) = 1, other surveyed = 0; unsurveyed and Inaccessible not scored; Woolsey: "
                  "surveyed units only (fix of the v3-v5 unsurveyed-as-undamaged rule)",
        "primary_B1": "fire-averaged held-out AUC: B_comb_V6 - B_dens_age",
        "pass_B": "B1 lower 95 % CI > 0",
        "realism_B2": "fire-averaged AUC(B_comb_V6) >= 0.70 reported against Zamanialaei et al. 2025 Table 2 "
                      "(0.635-0.841 per fire, in-fire splits); not a pass condition",
        "secondary": ["B_comb_V6 - B_comb_A0 (do long-range embers add to loss beyond front reach)",
                      "Brier and reliability", "AUC per fire", "B_dens_age - B_dens"],
    },
    "bootstrap": "2 km spatial clusters (super-blocks) resampled with replacement within each fire, 2,000 "
                 "resamples, seed 20261013, percentile 95 % intervals; pooled tests resample clusters of all "
                 "held-out fires together (A) or average the per-fire statistic (B)",
    "sensitivities": ["S1 A without the VIIRS previous-overpass lower bound (scoring only)",
                      "S2 A with eta x 10 and eta / 10 (10 members)",
                      "S3 B fitted without Woolsey", "S4 B with Woolsey under the old rule in training"],
    "disclosures": [
        "GLASS BLINDING BREACH (2026-10-13, first v6 session): while listing files the v6 agent printed "
        "derived/glass_OUTCOMES_SEALED.json, which showed Glass aggregate unit counts by damage class (e.g. "
        "No Damage 1,868; Destroyed 790; UNSURVEYED 909 in the footprint set) and DINS record counts by class. "
        "No spatial or per-building outcome was seen. No other sealed file (CZU, North Complex) was opened. "
        "Nothing in v6 was set from these numbers.",
        "SPOTTING FIX (commit 64e6ac7, 2026-10-13): FireSim's short-range spot fires that landed outside the "
        "burned area's box never spread before the fix. A0 (the target-A baseline) uses the fixed behaviour; "
        "an A0 with the old behaviour would be a weaker baseline.",
        "Held-out observation structure seen before fixing this block (counts only, no times): scored community "
        "blocks Glass 188 (all two-sided; 125 perimeter, 63 VIIRS upper bounds), CZU 47 (all two-sided), North "
        "Complex 44 (all two-sided) (observation_counts_180m.json).",
        "Held-out A0 simulations (20 members) were run before this block was fixed (no observation or outcome "
        "read by the run code); they are used as is.",
        "Training results seen: the v6 eta fit (fit_v6_arrival.json), the target-B LOFO (loss_lofo_eta1e-08.json: "
        "fire-averaged LOFO AUC B_dens_age 0.623, B_comb_A0 0.649, B_comb_V6 0.645; B_comb_V6 - B_dens_age "
        "+0.022, 95 % CI -0.010 to +0.050), the Woolsey label check, the v5 diagnosis and the v3 report. "
        "v4/v5 held-out results (PREREG_LR4/LR5) were not read.",
        "Grid moved from 90 m to 180 m during the training runs for compute (a 90 m Woolsey member took > 15 min); "
        "the 90 m training runs are superseded and were not used for the fit.",
        "Aggregate loss totals for the held-out fires may appear in the AAR/incident documents on disk "
        "(refs/<fire>/); they were not used.",
    ],
    "rules": "Fit only on training fires. No configuration may be promoted to primary after scoring. "
             "Aggregate outputs only (blocks, fires); no addresses or per-building outputs.",
}


def prereg_hash() -> str:
    return hashlib.sha256(json.dumps(PREREG_LR6, sort_keys=True, default=float).encode()).hexdigest()


if __name__ == "__main__":
    print(json.dumps(PREREG_LR6, indent=1))
    print(prereg_hash())
