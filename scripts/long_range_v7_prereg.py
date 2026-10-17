#!/usr/bin/env python3
"""PREREG_LR7: pre-registration of the long-range embers v7 held-out test (calibrated front, then embers).

Purpose: fixes, before any held-out fire is simulated or scored, the fire split, the fitted front (θ) and
ember establishment (η0, κ), the models, metrics, pass criteria, bootstrap and disclosures for
``scripts/long_range_v7_arrival.py`` (target A) and ``scripts/long_range_v7_loss.py`` (target B).
Human-readable copy: ``docs/PREREG_LR7.md``. Its SHA-256 (``long_range_v7_arrival.prereg_hash()``) is
written into every held-out result file. Do not edit after the commit that adds it; add a dated block
instead and report both.

Run: python3 scripts/long_range_v7_prereg.py   (prints the block and its hash)
References: see docs/PREREG_LR7.md.
"""

import hashlib
import json

PREREG_LR7 = {
    "date_fixed": "2026-10-17",
    "decision": "Owner (Travis Kennedy, EOC Chief of Plans) approved round 7 ('yes start V7'): fix FireSim's front "
                "first (pre-fire fuels, observed hourly winds, spread-rate calibration on training fires), then refit "
                "long-range ember establishment on top with a variable eta; extend target B.",
    "fires": {
        "training": ["eaton", "palisades", "camp", "tubbs", "woolsey"],
        "primary_heldout_fresh": ["dixie", "caldor"],
        "secondary_heldout_seen": ["glass", "czu", "northcomplex"],
        "fresh_rejected": {"zogg": "only 3 community blocks inside the perimeter", "carr": "no 'No Damage' class; "
                           "99.3 % post-fire footprints", "kincade": "78.5 % post-fire footprints (v4 check)",
                           "thomas": "pre-2018 DINS protocol; not in GOFER"},
        "why_secondary": "Glass, CZU and North Complex were scored in v4-v6 (their arrival errors and loss AUCs are "
                         "known); they are reported with the same tests but never decide.",
    },
    "stage1_front": {
        "fuels": "LANDFIRE FBFM40 of the latest served vintage before each fire (LF2016 for 2017-2021 fires, LF2024 for "
                 "2025 fires), mode per 90 m cell, group per 180 m run cell; groups G grass/grass-shrub -> O-1b, S shrub "
                 "-> C-4, T timber understory/litter/slash -> C-7, U urban (NB1 or WorldCover built >= 25 %) -> O-1b x 0.5, "
                 "NB non-fuel (scripts/long_range_v7_fuels.py CROSSWALK)",
        "wind": "hourly observed: speed of the fire's v3/v4 wind-event station (fresh fires: the first valid station of "
                "the ranked list), direction = speed-weighted vector mean over the fire's stations (Camp: JBGC1 only); "
                "effective 10 m wind = f_w x RAWS 20 ft speed",
        "ffmc": "hourly FFMC (Van Wagner 1977 hourly model) spun up 24 h before the start from the v6 daily FFMC",
        "dmc_dc": "v4 convention 100 / 800 for the fresh fires (as for Glass, CZU, North Complex)",
        "grass_cure": 95.0,
        "parameters": ["m_G", "m_S", "m_T", "f_w"],
        "grid": {"m_G": [0.08, 0.15, 0.25, 0.4, 0.6], "m_S": [0.6, 1.0, 1.5, 2.2, 3.2], "m_T": [0.5, 1.0, 2.0, 4.0],
                 "f_w": [0.45, 0.6]},
        "objective": "J = (MAE/MAE_v6A0 + Brier/Brier_v6A0)/2 per training fire (deterministic observed-wind run); "
                     "theta minimising the mean J over the five training fires; LOFO reported",
        "theta_final": {"m_G": 0.25, "m_S": 1.5, "m_T": 2.0, "f_w": 0.45},
        "mean_J_final": 0.802,
        "per_fire_at_final": {"eaton": {"mae_h": 0.48, "bias_h": 0.28, "J": 0.428},
                              "palisades": {"mae_h": 2.43, "bias_h": 2.22, "J": 1.035},
                              "camp": {"mae_h": 4.62, "bias_h": 4.62, "J": 1.106, "note": "no scored block reached"},
                              "tubbs": {"mae_h": 0.65, "bias_h": 0.65, "J": 0.987},
                              "woolsey": {"mae_h": 2.12, "bias_h": 0.64, "J": 0.454}},
        "lofo_J": {"eaton": 0.428, "palisades": 1.035, "camp": 1.106, "tubbs": 2.998, "woolsey": 0.454},
        "lofo_note": "the LOFO theta equals theta_final on 4 folds; leaving Tubbs out picks m_T 0.5, which stalls Tubbs "
                     "(J 3.0); mean LOFO J 1.20 vs 1 = v6 A0 level",
        "history": "a 3-parameter grid (m_open shared by G and S, m_T, f_w {0.6, 0.85, 1.15}) ran first on the training "
                   "fires (fit_front_stage1.json, partial for Woolsey, superseded); it showed grass-dominated Woolsey "
                   "wanting m_open <= 0.15 and shrub-dominated Palisades >= 1.2 and the optimum at f_w's lower edge, so "
                   "the final grid split m_open into m_G and m_S and moved f_w to {0.45, 0.6}. Training fires only. "
                   "theta_final's f_w is again at the grid's lower edge.",
    },
    "stage2_eta": {
        "model": "eta(cell, t) = eta0 * w(group) * p_e(category(cell), FFMC_hourly(t)); w = 1 for G/U, kappa for S/T; "
                 "p_e Beverly & Wotton 2007 grass_summer (G, U) or pine_lichen (S, T); long mode Storey-located; "
                 "Albini short-range spotting kept",
        "grid": {"eta0": [1e-8, 1e-7, 3e-7, 1e-6, 3e-6, 1e-5], "kappa": [0.3, 1.0, 3.0]},
        "members": 6,
        "objective": "J = (MAE_V7/MAE_A0c + Brier_V7/Brier_A0c)/2 per training fire; mean over five fires; LOFO reported",
        "eta_final": {"eta0": 1e-8, "kappa": 3.0},
        "eta_tag": "e1e-08_k3",
        "mean_J_final": 0.918,
        "per_fire_at_final": {"eaton": {"mae_A0c_h": 0.81, "mae_V7_h": 0.53, "J": 0.863, "lr_spots_per_member": 36},
                              "palisades": {"mae_A0c_h": 2.68, "mae_V7_h": 2.37, "J": 0.873, "lr_spots_per_member": 69},
                              "camp": {"mae_A0c_h": 4.62, "mae_V7_h": 4.62, "J": 0.994, "lr_spots_per_member": 32},
                              "tubbs": {"mae_A0c_h": 0.81, "mae_V7_h": 0.34, "J": 0.830, "lr_spots_per_member": 134},
                              "woolsey": {"mae_A0c_h": 2.71, "mae_V7_h": 2.81, "J": 1.030, "lr_spots_per_member": 28}},
        "lofo_J": {"eaton": 0.863, "palisades": 0.873, "camp": 0.994, "tubbs": 1.081, "woolsey": 1.030},
        "lofo_note": "LOFO picks eta_final on 4 folds; leaving Tubbs out picks eta0 1e-7 kappa 3 (Tubbs J 1.08); mean "
                     "LOFO J 0.97. eta0 at the grid's lower edge again (as v6); flat near the optimum (1e-7/1: 0.931, "
                     "1e-7/3: 0.944); grid not extended. Source: train/stage2/fit_eta.json.",
    },
    "target_A": {
        "models": {"A0v6": "the v6 A0 baseline: secondary fires = v6's own 20 held-out A0 runs (PREREG_LR6); fresh fires "
                           "= the v6 rule (one proxy fuel C-7 if LANDFIRE timber >= 60 % of fuel cells else C-4, member "
                           "wind uniform in the station's [p25, p90] 10 m speed, direction vector mean +/- 20 deg, station "
                           "gain, constant mean hourly FFMC) re-run on the current engine (v6_baseline_cfg)",
                   "A0c": "calibrated front (theta_final) + Albini short-range spotting, no long-range embers",
                   "V7": "A0c + LongRangeSpotter with eta_final (variable eta)"},
        "members": 20,
        "ensemble": "member m: wind speed x exp(0.2 z1), direction + 10 z2 deg (default_rng([20261015, m])); A0c and V7 "
                    "paired (same perturbations)",
        "units": "540 m community blocks with >= 10 Microsoft footprints; observed interval [t_lo, t_hi] as v6 "
                 "(GOFER/FIRIS perimeters, VIIRS 375 m, NIST); scored if t_hi <= run end",
        "A1": {"metric": "pooled MAE(A0c) - MAE(A0v6), h, over the primary fresh fires' scored blocks",
               "pass": "upper 95 % CI < 0"},
        "A2": {"metric": "pooled MAE(V7) - MAE(A0c) and Brier(V7) - Brier(A0c) (P(block reached by hour), known statuses)",
               "pass": "MAE upper 95 % CI < 0 (CI excludes zero) AND Brier lower 95 % CI <= 0"},
        "A3": "transport check (reported): pooled V7 tail ratio P(>3 km)/P(>1 km) in [0.10, 0.40] (Storey 2020 0.20); "
              "max vs Storey 13.9 km / Cruz 2012 33 km",
        "secondary": "the same A1/A2 on Glass, CZU, North Complex and on all five held-out fires pooled; per-fire MAE, "
                     "bias; never decide",
    },
    "target_B": {
        "models": {"B_dens": ["log_dens100"], "B_dens_age": ["log_dens100", "age_dec", "age_missing"],
                   "B_comb_A0c": ["log_dens100", "age_dec", "age_missing", "log_sep_nn", "canopy_0_9",
                                  "canopy_0_9_missing", "canopy_0_30", "canopy_missing", "edge", "fire_wind",
                                  "p_reach_A0c"],
                   "B_comb_V7": ["log_dens100", "age_dec", "age_missing", "log_sep_nn", "canopy_0_9",
                                 "canopy_0_9_missing", "canopy_0_30", "canopy_missing", "edge", "fire_wind",
                                 "p_reach_V7", "log_landed_edge", "log_landed_int"]},
        "fit": "logistic, standardised, L2 C = 1, on the five training fires (Woolsey surveyed-only rule); LOFO reported",
        "labels": "Destroyed (>50%) = 1, other surveyed = 0; unsurveyed / Inaccessible not scored",
        "B1": {"metric": "fire-averaged AUC(B_comb_V7) - AUC(B_dens_age) on the primary fresh fires",
               "pass": "lower 95 % CI > 0"},
        "B2": {"metric": "fire-averaged AUC(B_comb_V7) - AUC(B_comb_A0c), fresh fires (do embers add?)",
               "pass": "lower 95 % CI > 0 (pre-registered secondary; reported pass/fail)"},
        "B3": "realism: fire-averaged AUC(B_comb_V7) vs the owner's 0.70-0.80 reference (reported)",
        "notes": "the fire-level wind term shifts a fire's intercept and cannot change within-fire AUC; it is there for "
                 "Brier/reliability. Canopy 0-9 m from pre-fire NAIP NDVI (v7 data agents); missing flags where absent.",
    },
    "bootstrap": {"clusters": "2 km spatial super-blocks resampled within each fire", "draws": 2000, "seed": 20261015,
                  "ci": "percentile 95 %", "A": "pooled over the set's blocks", "B": "per-fire AUC difference averaged "
                  "over the set's fires in each draw"},
    "rules": ["fit only on training fires", "no held-out simulation before this block is committed and pushed",
              "no configuration may be promoted after scoring", "aggregate outputs only; no addresses, no per-building "
              "outputs outside research files", "FireSim never suggests evacuation tiers"],
    "disclosures": [
        "ENGINE FIX 2ec1b17 (2026-10-15, before any held-out run): grid-model point ignitions stalled under strong wind "
        "(narrow starting ellipse); every v7 run uses the fix; v6's secondary A0 runs (start from GOFER perimeters except "
        "Glass) were made before it and are used as recorded.",
        "MEMORY FIX 2026-10-17 (training stage 2, before any held-out run): Tubbs stage-2 runs reached 14.6-16 GB "
        "because kernels extended tens of km beyond the run grid; LR.kernel(crop, return_total) keeps only the offsets a "
        "mode='same' convolution on the grid uses (exact) and normalises the short kernel by the full kernel's sum, "
        "summed only over the crosswind strip where the normal factor is not exactly 0.0 (a 2,394 km-cap Tubbs m2 "
        "kernel: 172 s -> 1.3 s). Equivalence vs 72b0a76: 300 random kernels bit-identical (sum within 3.4e-16 "
        "relative); 4 stored runs re-run (palisades V7 1e-5/1 m5, tubbs V7 1e-5/1 m1, tubbs V7 1e-7/1 m2, camp V7 "
        "3e-6/3 m2): arrival, lr_spots, albini_spots, emitted bit-identical; landed |diff| <= 4.6e-13 (FFT rounding; "
        "identical for cells >= 1e-3; target B's log10(1 + landed) changes <= 2e-13). Tolerance stated: arrival and "
        "spots exact, landed <= 1e-12 absolute. Peak RSS <= 0.43 GB (was 14.6-16 GB). Stage-2 V7 runs pooled across "
        "versions: 462/540 on 72b0a76, 65 on the fix's first form (kernel cells within ~1e-19; its 3-run check gave "
        "bit-identical arrival/spots), 13 (Tubbs m2) on the final form; every held-out run uses the final form.",
        "Camp wind direction: JBGC1 only, chosen on the training fire after the all-station mean sent every run south "
        "of Paradise (training-only decision).",
        "Stage-1 grid redesigned once on training results (see stage1_front.history).",
        "Fresh fire observation counts seen before this block (counts only): Dixie 275 community blocks, 60 scored "
        "(42 two-sided; 41 perimeter, 19 VIIRS upper bounds); Caldor 213, 12 scored (12 two-sided; 11 perimeter, 1 VIIRS).",
        "Dixie DINS aggregate class counts were seen in the 2026-10-10 profiling (CAL FIRE DINS test data report); "
        "nothing in v7 was set from them. Caldor narrative suppression left two fragment phrases visible (no numbers). "
        "The Grizzly Flat RAWS (STEELY FORK) froze at 0 mph from 17 Aug 10Z (timing at one point). The data agent "
        "knows from public reporting that Greenville and Grizzly Flats were heavily hit; not used.",
        "Run windows of the fresh fires were set by the data agent from wind records and incident narratives; boxes = "
        "the timed perimeter at the window end + 8 km within FRAP + 8 km (long_range_v7_data.py).",
        "Secondary fires: v4-v6 results known (v6 A0 MAE 10.64 h; B AUC 0.60).",
        "Target B training LOFO and the eta fit were run before this block (training fires only); results recorded below.",
        "LANDFIRE LF2019/LF2020 are no longer served: 2018-2021 fires use LF2016 (disturbances 2016 -> fire missing).",
    ],
    "training_results_seen": {
        "stage1_front": "fit_front.json: theta_final, mean J 0.802, LOFO J 1.20 (see stage1_front)",
        "stage2_eta": "train/stage2/fit_eta.json: 18 eta0 x kappa settings x 6 members per training fire; eta_final "
                      "1e-8 / 3, mean J 0.918, LOFO J 0.97 (see stage2_eta)",
        "target_B_lofo": {"file": "train/loss_lofo_training_eta_final.json (eta_final runs, 6 members)",
                          "fire_averaged_auc": {"B_dens": 0.645, "B_dens_age": 0.623, "B_comb_A0c": 0.647,
                                                "B_comb_V7": 0.648},
                          "B_comb_V7_minus_B_dens_age": [0.025, 0.002, 0.055],
                          "B_comb_V7_minus_B_comb_A0c": [0.002, -0.008, 0.013],
                          "per_fire_auc_B_comb_V7": {"eaton": 0.574, "palisades": 0.689, "camp": 0.727, "tubbs": 0.674,
                                                     "woolsey": 0.579},
                          "brier_fire_averaged": {"B_dens": 0.217, "B_dens_age": 0.227, "B_comb_A0c": 0.221,
                                                  "B_comb_V7": 0.231},
                          "note": "year built lowers training AUC (Tubbs year built for 0.3 % of units)"},
    },
}


# Dated block after the PREREG_LR7 commit (a5fa8ba, pushed 2026-10-17 12:01); PREREG_LR7 above is unchanged.
PREREG_LR7_ADDENDUM_1 = {
    "date": "2026-10-17",
    "when": "after the first held-out runs had started (Dixie A0v6 x 7, Caldor A0v6 x 1 and A0c x 1 written; no V7 "
            "held-out run finished, nothing scored); all held-out processes stopped at 12:58 before this change",
    "why": "the Caldor V7 member-0 run built a Sardoy short-range kernel with a 5.9e8 m median and a 2.6e9 m cap "
           "(extreme intensity); the exact full-kernel sum used for normalisation (a5fa8ba) takes many minutes per "
           "kernel at ~1.4e7 rows, so the held-out V7 runs could not finish; 72b0a76 could not allocate it at all",
    "change": "engine LR.kernel: for a cropped 'cap' kernel whose half-extent exceeds KERNEL_EXACT_CELLS = 100,000 cells "
              "(an 18,000 km cap at 180 m), the normalising sum is the analytic lognormal mass Phi((ln cap - mu)/sigma) "
              "and only the cropped rows are built; below that the exact cell sum is kept",
    "evidence": "analytic vs exact cell sum within 1.2e-7 relative for medians >= 80 km (6 kernels x 5 directions); "
                "the Caldor kernel's mass on the run grid is 9e-76 (nothing lands); 300 random kernels still "
                "bit-identical to 72b0a76; 5 training stage-2 re-runs (2 Tubbs member 2, Tubbs m1, Palisades, Camp) "
                "give bit-identical arrival / spots / emitted (landed <= 4.6e-13 vs 72b0a76; identical to the stored "
                "final-form Tubbs m2 run); no training kernel exceeded the threshold (the fix's first form would have "
                "taken ~9,700 s for one such kernel; no training setting took more than 1,473 s)",
    "effect_on_prereg": "none intended: models, eta_final, metrics, pass criteria unchanged; every held-out V7 run uses "
                        "this engine; held-out A0v6 / A0c runs do not use the ember kernel",
}


# Second dated block (after addendum 1, commit c77a96b); PREREG_LR7 and addendum 1 unchanged.
PREREG_LR7_ADDENDUM_2 = {
    "date": "2026-10-17",
    "when": "during the held-out runs, before any scoring (nothing scored)",
    "why": "held-out Caldor V7 member 15 was killed at the 5 GB cap (peak 5.29 GB): for an east/west wind the "
           "kernel's crosswind strip spans every column of its box, so one 64-row block of an extreme-cap kernel "
           "allocated whole rows (2.8e7 columns in the analytic case)",
    "change": "engine LR.kernel: in the analytic case columns are limited to the crop window (nothing outside it is "
              "kept or summed); in the exact case a row block wider than 4e6 elements is evaluated in column chunks",
    "evidence": "kept cells unchanged: 300 random kernels bit-identical to 72b0a76; 5 training re-runs give the same "
                "result as for addendum 1 (arrival / spots / emitted bit-identical, landed <= 4.6e-13); the re-run of "
                "Caldor V7 m15 completes at 0.42 GB peak; unit test test_kernel_east_west_extreme_cap_stays_small",
    "effect_on_prereg": "none intended; held-out runs that were killed are re-run with this engine; held-out V7 runs "
                        "that finished on c77a96b are kept (kept kernel cells unchanged; a normalising sum changes only by "
                        "summation order, at rounding level, in row blocks wider than 4e6 elements)",
}


def sha256_addendum_2() -> str:
    return hashlib.sha256(json.dumps(PREREG_LR7_ADDENDUM_2, sort_keys=True, default=float).encode()).hexdigest()


def sha256() -> str:
    return hashlib.sha256(json.dumps(PREREG_LR7, sort_keys=True, default=float).encode()).hexdigest()


def sha256_addendum_1() -> str:
    return hashlib.sha256(json.dumps(PREREG_LR7_ADDENDUM_1, sort_keys=True, default=float).encode()).hexdigest()


if __name__ == "__main__":
    print(json.dumps(PREREG_LR7, indent=1, default=float))
    print("sha256", sha256())
    print(json.dumps(PREREG_LR7_ADDENDUM_1, indent=1))
    print("sha256 addendum 1", sha256_addendum_1())
    print(json.dumps(PREREG_LR7_ADDENDUM_2, indent=1))
    print("sha256 addendum 2", sha256_addendum_2())
