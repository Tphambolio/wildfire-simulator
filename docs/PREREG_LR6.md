# PREREG_LR6: long-range embers v6, reframed validation (pre-registration)

- **Purpose:** to fix the v6 held-out test before any held-out fire is scored. That covers the models, fitted values, metrics, pass criteria, held-out fires, bootstrap and disclosures.
- **Audience:** the FireSim owner (EOC Chief of Plans), reviewers and later sessions.
- **Status:** pre-registered, fixed on 2026-10-13, and committed and pushed before scoring.
- **Machine-readable copy:** `scripts/long_range_v6_prereg.py`. Its SHA-256, `ad293486f4e745b72cbd5bdb1e4161bbb989f166d11ae70e2a9b35bd65a0dfdf`, is carried in every held-out result file.
- **Related:** spec §6.8; PROJECT_RECORD decision of 2026-10-13; [R37] (v3); the v4/v5 work (`PREREG_LR4`/`LR5`, separate branches, results not read for this).

## 1. Decision being tested

On 2026-10-13 the owner split ember validation into two targets.

- **(A) Ember transport and community arrival.** When could fire, from the front or from embers, reach a neighbourhood?
- **(B) Building loss with a combined model.** Ember arrival starts the loss. House-to-house spread, governed by building density, building age/code and near-building vegetation, then decides which buildings are lost.

This follows the v5 diagnosis on the training fires (`calfire-dins/results-long-range-v5-2026-10-12/diagnosis/diagnosis.json`):

- v3 saturates.
- Wildland ember load and closeness to the edge rank destruction backwards (AUC 0.31-0.39).
- Density within 100 m ranks well (0.68-0.73), and year built 0.57-0.66.
- Eaton is near 0.5 on everything.
- Woolsey's directions reverse.

## 2. Questions

- **A:** Do long-range ember spot fires (V6) make FireSim's predicted fire-arrival times at community blocks closer to the observed arrival than FireSim without long-range embers (A0: front + short-range spotting), on held-out fires, without worse probabilistic reach forecasts?
- **B:** Does a combined building-loss model (density, year built, separation, canopy 0-30 m, and target A's fire reach and landed embers) discriminate destroyed from surviving buildings better than density + year built on held-out fires?

## 3. Fires

| Role | Fires |
|---|---|
| Training: everything fitted here | Eaton 2025, Palisades 2025, Camp 2018, Tubbs 2017, Woolsey 2018 |
| Held-out: decides | Glass 2020, CZU 2020, North Complex 2020 (the v4/v5 held-out set) |

All three held-out fires have timing data: GOFER hourly GOES perimeters and VIIRS.

**Not added:**
- **Lahaina:** no open timed-arrival data is prepared or verified on disk; there is no blind set-up, and it is outside the CAL FIRE DINS frame. Writing a new set-up after seeing v6's training behaviour would risk tuning.
- **Marshall:** swapped out in the v4 selection, because the public Boulder County views have no "No Damage" class. No timing data is prepared.

## 4. Target A

### Models (paired ensembles, the same wind draws)

Both models use FireSim's grid fire model, a level set on a 180 m grid.

- **A0:** front plus FireSim's existing short-range spotting (Albini maximum distance, heuristic landing), with the seeding fix (commit `64e6ac7`).
- **V6:** A0 plus long-range ember spot fires (`LongRangeSpotter`, spec §6.8).
  - Storey-located long mode.
  - **η = 1e-8**, fitted on the training fires (§6).

### Held-out set-up

| Item | Value |
|---|---|
| Members per model | 20 |
| Fuel, weather, ignition | v4 `FIRES4` values unchanged: proxy fuel, FFMC, DMC/DC, wind ranges, origins |
| Non-fuel | WorldCover 2021 water or bare ≥ 50 % |
| Town cells | built-up ≥ 25 %, ROS × 0.5 [H] |
| Terrain | 3DEP slope |
| Wind | hourly: member speed × station gain |
| Starting fire | Glass: point ignition at 03:48 PDT. CZU and North Complex: the GOFER perimeter at the run start |

### Scoring units and observations

**Units:** 540 m community blocks with at least 10 Microsoft footprints.

The observed arrival is an interval [t_lo, t_hi]:
- **t_hi (upper bound):** the earliest of the first VIIRS detection within 375 m, the first GOFER/FIRIS perimeter containing the block centre, or the first NIST point.
- **t_lo (lower bound):** the latest of the last earlier perimeter not containing the block, the previous VIIRS overpass [H], or the NIST time − 30 min.
- **Scored blocks:** those with t_hi at or before the end of the run.

### Metrics and pass criteria

| | Metric | Test | Condition |
|---|---|---|---|
| **A1 (primary)** | MAE (h) of the ensemble-median block arrival from the observed interval. Blocks the fire never reaches take the run end. | MAE(V6) − MAE(A0), pooled over held-out blocks | upper 95 % CI < 0 |
| **A2 (co-primary)** | Brier of P(block reached by hour τ), using known statuses only. Not reached = t_lo ≥ τ, or the block is outside the final perimeter + 1 block. | Brier(V6) − Brier(A0) | lower 95 % CI ≤ 0 (V6 not significantly worse) |

**Embers add skill on target A only if A1 and A2 both hold.**

**A3 (transport check; reported, not a pass condition):**
- The pooled V6 tail ratio P(spot > 3 km)/P(spot > 1 km) is compared with Storey et al. 2020 *Fire* 3:10 p.7 (0.02/0.10 = 0.20). It counts as consistent if it lies in [0.10, 0.40].
- The maximum spot distance is compared with Cruz et al. 2012 p.269 (33 km).
- Distance is measured to the nearest cell burned before the spot fire ignited, as Storey measured it (p.6).
- Distances under one cell (180 m) cannot be resolved, so the median is not compared.

**Secondary measures:**
- MAE per fire;
- share of blocks within 1 h;
- late-warning share (observed upper bound before the ensemble's 10th percentile);
- ahead-ignition hit rate (VIIRS jumps > 1 km);
- spot fires outside the final perimeter;
- aggregate error maps.

## 5. Target B

### Models

All are logistic regressions, standardised, L2 penalty with C = 1. They are fitted on all five training fires; leave-one-fire-out CV is reported.

| Model | Features |
|---|---|
| B_dens | log(1 + buildings within 100 m) |
| B_dens_age | B_dens + year built (DINS `YEARBUILT`, decades from 1950, with a missing flag) |
| B_comb_A0 | B_dens_age + log nearest-neighbour separation + canopy cover 0-30 m (the closest available ring to 0-9 m) + P(reach) from A0 |
| B_comb_V6 | B_comb_A0, with P(reach) from V6 and log10 landed embers from V6 |

### Labels

- Destroyed (> 50 %) = 1. Every other surveyed class = 0.
- Unsurveyed and Inaccessible buildings are not scored.
- **Woolsey fix:** only surveyed units are scored, because Woolsey DINS records only damaged structures. This replaces the v3-v5 rule that counted unsurveyed footprints as undamaged. Woolsey's label therefore becomes destroyed vs damaged-not-destroyed.
- **Label check on the training data (seen before this prereg):**
  - Woolsey density AUC is 0.405 under the old rule (14,539 units, 11 % destroyed) and 0.477 under the surveyed rule (2,002 units, 82 % destroyed).
  - Year built, scored on the units where it is recorded, is 0.617 under both rules.
  - P(reach) from A0 is 0.594 under the old rule and 0.543 under the surveyed rule.
  - Source: `results-2026-10-13/train/woolsey_label_check.json`.

### Metrics and pass criteria

| | Metric | Condition |
|---|---|---|
| **B1 (primary)** | Fire-averaged held-out AUC of B_comb_V6 − B_dens_age | lower 95 % CI > 0 |
| **B2 (realism; reported, not a pass condition)** | Fire-averaged AUC(B_comb_V6), against the 0.70-0.80 target | — |

The 0.70-0.80 target is only partly verified. Zamanialaei et al. 2025 *Nat. Commun.* 16:8041, Table 2 p.7, reports XGBoost AUC of 0.635-0.841 per fire. Those tests used random splits within each fire and DINS construction features. Table 1 reports logistic regression at 0.65 for the five fires combined. No published held-out-fire AUC was found.

**Secondary measures:**
- B_comb_V6 − B_comb_A0: do long-range embers add anything beyond fire reach?
- Brier score and reliability;
- AUC per fire;
- B_dens_age − B_dens.

## 6. Training fit (target A; done before this prereg, training fires only)

| Item | Value |
|---|---|
| Grid | η ∈ {1e-8, 1e-7, 1e-6, 3e-6, 1e-5} |
| Ensemble | A0 and V6, 10 members each, 180 m grid |
| Objective | J = (MAE_V6/MAE_A0 + Brier_V6/Brier_A0)/2 per training fire; eta minimising the mean J over the five training fires (LOFO CV reported) |
| Result | **η = 1e-8**, at the lower edge of the grid |

**Objective changed before scoring.** Changed on 2026-10-13 BEFORE any held-out score, from MAE alone. Reason: the first training scores showed MAE falling monotonically with eta on the fires whose observations are one-sided (Eaton 3/62 and Tubbs 0/113 two-sided blocks: an early model is never penalised) while the Brier of P(reached) rose sharply (Tubbs 0.050 -> 0.330, Eaton 0.130 -> 0.384 at eta 1e-5). MAE alone would pick the largest eta; J weights both pre-registered conditions equally.

**Mean J by η:**

| η | 1e-8 | 1e-7 | 1e-6 | 3e-6 | 1e-5 |
|---|---|---|---|---|---|
| Mean J | 0.966 | 1.041 | 1.452 | 1.658 | 1.996 |

**LOFO J at the LOFO-chosen η (1e-8 every time):**

| Fire | Eaton | Palisades | Camp | Tubbs | Woolsey |
|---|---|---|---|---|---|
| J | 1.029 | 0.947 | 0.997 | 0.801 | 1.055 |

**Per-fire picture.** the optimum is at the lower edge of the grid: one eta for all fires is pulled down by Woolsey, Eaton and Tubbs (embers make arrival too early / add false alarms) while Camp and Palisades improve up to 1e-5 and 1e-7 respectively (training table in fit_v6_arrival.json)

**Secondary model V6_2s (reported, never decides).** η = 1e-7. eta minimising mean J on the training fires with mostly two-sided observations (Camp 154/204, Palisades 115/127): J 0.883 at 1e-7 vs 0.888 at 1e-6; reported as a secondary model, never decides; also S2's eta x 10.

**Training observations.** Two-sided blocks out of scored blocks:

| Fire | Two-sided / scored |
|---|---|
| Eaton | 3/62 |
| Palisades | 115/127 |
| Camp | 154/204 |
| Tubbs | 0/113 |
| Woolsey | 286/364 |

**Target B LOFO, training.** Fire-averaged AUC:

| Model | AUC |
|---|---|
| B_dens_age | 0.623 |
| B_comb_A0 | 0.649 |
| B_comb_V6 | 0.645 |

B_comb_V6 − B_dens_age = +0.022 (95 % CI −0.010 to +0.050).

Source files: `results-2026-10-13/train/fit_v6_arrival.json` and `loss_lofo_eta1e-08.json`.

## 7. Bootstrap

- Clusters are 2 km spatial super-blocks, resampled with replacement within each fire.
- 2,000 resamples, seed 20261013, percentile 95 % intervals.
- **A:** clusters are pooled across the held-out fires.
- **B:** the statistic is the per-fire AUC difference, averaged over the fires in each resample.

## 8. Sensitivities (reported, never promoted)

S2 values: η × 10 = 1e-7 (the same runs as V6_2s) and η / 10 = 1e-9.

- **S1:** target A without the VIIRS previous-overpass lower bound (scoring only).
- **S2:** η × 10 and η / 10 (10 members).
- **S3:** target B fitted without Woolsey.
- **S4:** target B with Woolsey under the old rule in training.

## 9. Disclosures (seen before fixing this block)

- GLASS BLINDING BREACH (2026-10-13, first v6 session): while listing files the v6 agent printed derived/glass_OUTCOMES_SEALED.json, which showed Glass aggregate unit counts by damage class (e.g. No Damage 1,868; Destroyed 790; UNSURVEYED 909 in the footprint set) and DINS record counts by class. No spatial or per-building outcome was seen. No other sealed file (CZU, North Complex) was opened. Nothing in v6 was set from these numbers.
- SPOTTING FIX (commit 64e6ac7, 2026-10-13): FireSim's short-range spot fires that landed outside the burned area's box never spread before the fix. A0 (the target-A baseline) uses the fixed behaviour; an A0 with the old behaviour would be a weaker baseline.
- Held-out observation structure seen before fixing this block (counts only, no times): scored community blocks Glass 188 (all two-sided; 125 perimeter, 63 VIIRS upper bounds), CZU 47 (all two-sided), North Complex 44 (all two-sided) (observation_counts_180m.json).
- Held-out A0 simulations (20 members) were run before this block was fixed (no observation or outcome read by the run code); they are used as is.
- Training results seen: the v6 eta fit (fit_v6_arrival.json), the target-B LOFO (loss_lofo_eta1e-08.json: fire-averaged LOFO AUC B_dens_age 0.623, B_comb_A0 0.649, B_comb_V6 0.645; B_comb_V6 - B_dens_age +0.022, 95 % CI -0.010 to +0.050), the Woolsey label check, the v5 diagnosis and the v3 report. v4/v5 held-out results (PREREG_LR4/LR5) were not read.
- Grid moved from 90 m to 180 m during the training runs for compute (a 90 m Woolsey member took > 15 min); the 90 m training runs are superseded and were not used for the fit.
- Aggregate loss totals for the held-out fires may appear in the AAR/incident documents on disk (refs/<fire>/); they were not used.

## 10. Rules

- Fit only on training fires.
- No configuration may be promoted to primary after scoring.
- Outputs are aggregate only (blocks, fires): no addresses and no per-building outputs.
- FireSim never suggests evacuation tiers.

## References

| Reference | Pages used | Zotero key |
|---|---|---|
| Storey MA, Price OF, Bradstock RA, Sharples JJ (2020). Analysis of variation in distance, number, and distribution of spotting in southeast Australian wildfires. *Fire* 3(2):10. doi:10.3390/fire3020010 | pp.6-7 | `MS74WYUD` |
| Cruz MG et al. (2012). Anatomy of a catastrophic wildfire: the Black Saturday Kilmore East fire. *For. Ecol. Manage.* 284:269-285. doi:10.1016/j.foreco.2012.02.035 | pp.269, 276, 281 | `9PGICYK7` |
| Sardoy N et al. (2008). *Combust. Flame* 154:478-488. doi:10.1016/j.combustflame.2008.05.006 | pp.483-488 | `AGFRNV4F` |
| Zamanialaei M et al. (2025). *Nat. Commun.* 16:8041 | Tables 1-2, p.7 | key not recorded |
| Knapp EE, Valachovic YS, Quarles SL, Johnson NG (2021). Research Square preprint. doi:10.21203/rs.3.rs-580864/v1 | p.10 | key not recorded |
| Masoudvaziri N et al. (2021). SWUIFT. *Environ. Model. Softw.* 143:105097 | Table 1, p.2 | `S8HRF8VS` |
| Maranghides A et al. (2021). NIST TN 2135 (Camp Fire timeline). doi:10.6028/NIST.TN.2135 | Appendix F | — |
| Beverly JL, Wotton BM (2007). AMS preprint | eq 2.02, p.5 | — |
| Filkov AI et al. (2017). *Proc. Combust. Inst.* 36:3263-3270 | Table 1, p.3267 | `56QTE56J` |
| Qin Y (2025). Dissertation | eq 3.7, pp.59-61 | as PROJECT_RECORD |
