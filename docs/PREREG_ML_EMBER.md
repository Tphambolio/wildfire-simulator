# Pre-registration, stage 2: ML ember-cast confirmation on the v8 decision fires

**Status:** **approved by the owner and frozen, 2026-10-30 06:24 MDT** (drafted 2026-10-30 about 06:30 MDT and pushed as a draft
at `e3d3872` before the approval; see §11). **No decision fire has been scored by any model of this project.** The confirmation (§4) runs once, after the
approval is recorded in §11 and this file is pushed again; the registered text is never edited afterwards, and §12
(Result) is appended only.
**Constrained by:** `docs/PREREG_ML_EMBER_DEV.md` (stage 1, frozen `07fbf59` 2026-10-25, addenda A1–A4), whose unit,
labels, candidate cells, metric, feature contract, controls, CV design and stop rule this file completes and does not
change; `docs/PREREG_LR8.md` (v8, frozen `6452c1d`) whose scorer this file reuses unchanged. Where this file adds
(seeds, hashes, the exact models, a second RQ1 comparison) or deviates (§7), it says so.
**Branch:** `research/ml-ember-cast`, worktree `~/dev/wildfire/wt-ml-ember`. **Record:** `docs/ML_EMBER_RECORD.md`.
**Report number:** [R68]. **Claude for Science:** `proj_ac346e168289`.
**Development evidence:** *ML ember-cast development memo 2026-10-29* (`~/dev/wildfire/reports/`), training split only,
with the 2026-10-30 correction of §9 below.

---

## 1. Models (stage-1 §12 item 1)

Three models are fitted **once, on the whole training split** (the 165 included training fires; 86,334 sampled rows,
234 positives, 1,239 intervals, 143 fires carrying sampled rows under the §3 subsample) by
`scripts/ml_ember/confirm.py fit`, and stored as `reports/data/ml-ember-cast-2026-10-29/stage2/models.pkl`
(**sha256 `cf11ba9b7d6e2d5f2a65886a057a75319204d80db74f068a0c1b8f6405b65d43`**, fitted 2026-10-30 05:45 MDT, before this
draft, from the frozen tables of stage 1 §16 and the terrain table of A2). Nothing is refitted or re-tuned on or after
the confirmation; `confirm.py score` refuses a pickle whose hash differs.

| Name | What it is | Fixed choices |
|---|---|---|
| **RUNG2** (deciding, RQ1) | The carried rung of the ladder (stage 1 §7, A4): the additive within-interval conditional logistic of `ladder.fit_rung2` on the **clean arm's 13 features** `d_act_km, d_all_km, bear_off_deg, u10_ms, m_ff_pct, ffmc, I_head_kwm, ros_mmin, frp_prev_mw, curing_pct, fuel_group, slope_pct, elev_m` (cubic splines, 6 knots at training quantiles, `fuel_group` one-hot, log1p on `I_head_kwm` and `frp_prev_mw`, FBP-missing indicators, training-median imputation) | L2 penalty **C = 0.01** (the inner-fold choice in 5 of 5 outer folds of the full arm and 4 of 5 of the corrected clean arm, fold 2 choosing 0.1; the modal value is fixed); the stage-1 monotone rule applied once on the full fit: ALE Spearman `d_act_km` −1.0 (kept, non-increasing), `m_ff_pct` −1.0 (kept), `curing_pct` −0.36 (violates "non-decreasing" → entered linearly, coefficient −0.073) ; 83 coefficients, L-BFGS converged |
| **HYBRID** (deciding, RQ3) | Stage 1 §5 rung 4 as pre-registered: the RUNG2 design with **`m8_log` as a fixed offset** (coefficient 1, no scale) and the one pre-declared interaction `d_act_km × fuel_group` as the two product terms `d_act_km × 1{D-M}`, `d_act_km × 1{C-timber}` (entering through the same spline basis) | same C, same linearised column; the offset is M8's log expected count with the **frozen** `fit_beta.json` (β_w = 0; stage 1 §2) on the training rows (column `m8_log_frozen` of the frozen table) and on the decision fires (log of v8's own `score_M8`); 97 coefficients |
| **DIST** (deciding comparator, RQ1b; stage 1 §5 rung 0) | The distance-only within-interval logistic on `d_act_km` alone | C = 1 (as in `ladder.py`); standardised coefficient −2.675 |

References scored alongside, never fitted here: **M8** (v8's `score_M8`, frozen β) and **B1** (v8's `score_B1`) from the
v8 candidate files; **ELMFIRE** on its seeded 150-fire subset (reference only, PREREG_LR8 §7).

No fitting step is stochastic (L-BFGS from zero; quantile knots), so no model seed is needed; the development seeds of
stage 1 §6 are unchanged. Environment: Python 3.13, numpy 2.4.2, scipy 1.17.1, scikit-learn 1.9.1, pandas as in the
`ml-ember` conda environment (stage 1 §14).

## 2. Confirmation set and power (stage-1 §12 item 2; §11 option B)

The v8 decision fires **as drawn and unsealed by v8** ([R70], PR #114): 400 fires drawn, **203 eligible** (CAN 63 /
US 140), K = 176 counted jumps (CAN 96 / US 80), **159 ranked jumps in 73 fires** (CAN 29 fires / 83 jumps; US 44 /
76), the transfer gate G1 passed on 200 of 203. Files: `validation-data/v8-pool/final/{candidates/*.npz, jumps.csv,
counts.csv, gate.json}` (sealed to this project until §4). Labels, candidate cells and the never-reached guard are
exactly PREREG_LR8 §4, §6 and A1 as `v8_score.py` applies them; this project adds nothing to them.

**Power recomputed from these counts** (`scripts/ml_ember/power_stage2.py`, seed `S_POWER = 2026103001`, 1,000 studies
per cell, the frozen v8 generator with the reference AUC set per region to M8's observed 0.710 / 0.642 for comparisons
against M8 and to 0.85 for the distance-only comparator; per-fire counts zero-truncated negative binomial with the
training dispersion; `stage2/power_stage2.json`, Fig. `stage2/fig_power_stage2.png`):

| true gain Δ | A: RUNG2 − M8 (ρ_s 0.6 / 0.8) | B: RUNG2 − DIST (ρ_s 0.8 / 0.9) | C: HYBRID − M8 (ρ_s 0.8 / 0.9) |
|---|---|---|---|
| 0 (false pass) | 0.028 / 0.027 | 0.035 / 0.018 | 0.021 / 0.024 |
| +0.02 | — | 0.35 / 0.48 | — |
| +0.03 | 0.22 / 0.38 | 0.64 / 0.85 | 0.37 / 0.51 |
| +0.04 | — | **0.90 / 0.98** | — |
| +0.05 | 0.56 / 0.75 | 0.97 / 1.00 | 0.76 / 0.94 |
| +0.08 | **0.92 / 0.99** | 1.00 | **0.99 / 1.00** |
| +0.10 | 0.99 / 1.00 | — | 1.00 |

SD of the S3 estimate: A 0.024 / 0.018, B 0.013 / 0.010, C 0.018 / 0.014. Brackets: A at +0.08 with the reference
AUC ± 0.05 gives 0.985 / 0.994; B at +0.04 with the reference at 0.80 gives 0.93, at 0.90 gives 1.00. **The sizing
stands:** RQ1a and RQ3 are sized at **Δ = +0.08** as stage 1 §1 fixed; RQ1b is sized at **Δ = +0.04**, the smallest
margin on the grid with power ≥ 0.90 at both agreement values. Minimum detectable gains at 80 % power: about +0.055
(A, ρ_s 0.8) to +0.07 (ρ_s 0.6); +0.03 to +0.035 (B); +0.04 to +0.05 (C).

## 3. Deciding comparisons and pass rules (stage-1 §12 items 3–4)

Every test is the v8 scorer's **one-sided fire-level sign-flip randomisation test on S3** (PREREG_LR8 §7.3; 20,000
flips within regions, **seed `S_TEST = 2026101803`**, the v8 seed, so that this project's numbers sit on v8's scale),
**p < 0.025**, with S3 = Σ w_f a_f / Σ w_f, w_f = K_f / (1 + 0.3 (K_f − 1)), a_f = the fire's mean within-interval
percentile-rank difference. Three deciding tests are declared; no further multiplicity correction (stage 1 §1 declared
two; the third, RQ1b, is an addition under §7 and makes RQ1 harder to over-read, not easier to pass).

| Test | Comparison | Passes if | Sized for |
|---|---|---|---|
| **RQ1a** skill beyond M8 | S3(RUNG2 − M8) | > 0 and p < 0.025 | +0.08 |
| **RQ1b** skill beyond a distance decay | S3(RUNG2 − DIST) | > 0 and p < 0.025 | +0.04 |
| **RQ3** constrained hybrid beyond M8 | S3(HYBRID − M8) | > 0 and p < 0.025 | +0.08 |

**Readings, fixed now.**
- RQ1a pass + RQ1b pass: the learned ranker carries skill beyond both M8 and a bare distance decay; the physics memo
  (§8) addresses distance decay *and* the second-order terms (bearing offset, fine-fuel moisture).
- RQ1a pass + RQ1b fail: ML beats M8 **through the distance decay alone**; the single instruction to the physics is the
  decay shape, and no correction layer beyond a re-specified decay is proposed for the product.
- RQ1a fail: the development gain (+0.27) did not transfer to unseen fires drawn under the v8 rule; the report states
  the minimum detectable gain (§2) and says so plainly. The physics memo still stands on the training-split
  diagnostics, labelled as such.
- RQ3 pass: the offset hybrid improves on M8 on unseen fires; the descriptive ratio (HYBRID − M8)/(RUNG2 − M8) ≥ 0.5
  reads "closes most of the gap" (stage 1 §1). RQ3 fail (expected from development, +0.02 in-sample): M8's physics as a
  fixed offset does not help the ranking; the hybrid in this form is not proposed for the product.
- No pass or fail is ever read as "M8 is near the information limit" (stage 1 §1).

**Not deciding, reported:** the full-arm model is **not scored** (stage 1 §7: at most two fitted ladder models on the
confirmation fires; the arms differ by ≤ 0.01 in development); rung 3, the scaled hybrid, the ERA5 arm and RQ5 are not
scored on decision fires (§8).

## 4. Procedure (one shot)

1. **Approval and freeze.** Travis approves §11; the approval line is added to §11, the file is committed and pushed;
   that commit's sha is the **confirmation commit**. The loader's only override is `--confirmation-commit <sha>` with
   the sha present on `origin` (stage 1 §14, A1); every overridden open is logged in `refusals.log`.
2. **Decision-fire features** (`confirm.py features --confirmation-commit <sha> --workers 2`, under `capjob`,
   ≤ 5 GB): for every decision fire that holds a ranked candidate jump in `final/jumps.csv` (rows with a candidate
   `cell_id` and a numeric interval), `features.load_fire(assignment="decision")` and `iter_intervals` build the stage-1
   §4 features of its jump intervals with the **same code path and frozen β** as the training tables (the recomputed
   geometry is checked against the v8 interval files, max abs difference reported), terrain from Copernicus GLO-90
   (A2), and write `stage2/decision_cells.parquet` + `decision_build.json`. A fire that fails to build is listed; its
   jumps are counted as "interval excluded" by the scorer (PREREG_LR8 §8, A1), never scored. Build failures are fixed
   and this stage re-run **before** step 3; no score exists at this stage. If more than 10 % of the 159 ranked jumps
   cannot be built, the confirmation is reported as **not decidable (procedural)** and the owner is asked.
3. **Score** (`confirm.py score --confirmation-commit <sha> --expect-models-sha256 cf11ba9b…b65d43`), once:
   (a) the pickle hash is checked; (b) DIST, RUNG2 and HYBRID are scored on every built cell; (c) the scores are
   aligned to v8's candidate files: for every interval the candidate cell set must be **identical** to v8's and the
   frozen-β M8 log-score must equal log(`score_M8`) within 10⁻⁴; **any mismatch halts the run before any S3 is
   computed** (`alignment.json` is written and the owner is asked; the fix is disclosed and the stage re-run); (d) the
   v8 scorer's `per_fire` ranks every ranked jump under M8, B1, DIST, RUNG2, HYBRID and ELMFIRE where present;
   (e) the three tests of §3 are evaluated; (f) `confirmation.json`, `ranks_per_jump.csv`, `per_fire.csv` are written.
   The script refuses to run a second time while `confirmation.json` exists; `--force-rescore` is recorded in the
   output if ever used and would be disclosed in §12.
4. **Report.** §12 is appended with the numbers of step 3; the record, the data folder and Claude for Science are
   updated; [R68] is written. Nothing about any model, feature, rule or reading changes after step 3 starts.

**Integrity check built into step 3:** S3(M8 − B1) on the same ranks must reproduce [R70]'s **+0.147** (95 % +0.098 to
+0.194, 159 jumps / 73 fires); a different value means the ranking machinery differs from v8's and the run is halted.

## 5. Reported with each comparison (stage-1 §12 item 5)

The estimate; the stratified fire bootstrap 95 % interval (2,000 draws, **seed `S_BOOT = 2026101804`**, v8's); S1 and
S2 weightings; the share of fires with a_f > 0; by region; by gap class (unburned / later detected / never detected);
by distance band of the jump (1–3, 3–7, 7–15 km); by active-share tercile of the interval; by year; the within-interval
AUC of every model overall and per region; the per-fire table (K_f, AUC per model); the ELMFIRE comparison on the fires
whose ranked jumps all carry an ELMFIRE score (reference only); the jump accounting of A1 (ranked / never reached /
375–540 m / interval excluded / not a candidate). Every figure carries the full caption (purpose, what is shown, how
to read, key result, caveats, references).

## 6. Blindness statement (stage-1 §12 item 6)

- **Read by the professor, in aggregate only:** the v8 result report [R70] in full on 2026-10-29 02:12–02:20 MDT
  (A1: K, per-region counts, S3(M8 − B1), region AUCs, ELMFIRE at M8's level, skill falling with distance, count
  miscalibration, Brier); and on **2026-10-30 about 05:15 MDT, while extracting per-region fire counts for §2 by a
  text search of the report**, the headline of its post-hoc fuel-seam addendum (Canada, hazard-only fuel: S3(M8 − B1)
  **+0.154** [+0.091, +0.216] against +0.139 registered; 29 Canadian fires with ranked jumps holding 83 jumps). The
  standing colleague rule (record §3) says these decision-fire numbers were not to reach this project before this file
  was pushed. **They did, inadvertently, and are disclosed here.** They were read **after** the deciding models, the
  clean arm and both RQ1 comparisons had been fixed in the development memo of 2026-10-29 (§5 there), and no choice in
  this file changed after that reading; they concern M8 versus B1 under an altered fuel layer, not any model of this
  project. The FireSim main session and the owner were told on 2026-10-30.
- **Not opened by anyone on this project, at this draft:** `final/candidates/`, `final/jumps.csv`, `ranks_per_jump.csv`,
  `sealed_manifest.csv`, any decision fire's `intervals/`, `viirs_decision/`, `elmfire_runs/`, `m8_parity/`, any sealed
  2026 record. The loader's `refusals.log` holds 0 overrides.
- **Known to the designer before stage 2, from [R70] (A1):** M8's skill falls with distance; B1 is near chance in the
  US; the counts are miscalibrated in opposite directions by region. None added a feature (stage 1 §4 is closed).
- **This file's draft commit** and the stage-1 freeze `07fbf59` (2026-10-25, before anyone read [R70]) date the
  feature contract and the ladder before any decision-fire information.

## 7. Deviations from stage 1 and disclosures (stage-1 §12 item 6; "add and disclose, never remove")

1. **GLM form.** Rungs 1–2 are the within-interval **conditional** logistic (A3), because the pooled form attenuated a
   planted within-interval effect threefold.
2. **`sec_act` dropped** from rungs 1–4 as the exact 45° threshold of `bear_off_deg` (A3). It stays in the rung-0
   reference "distance + sector", which is not scored here.
3. **Monotone rule.** `curing_pct` enters linearly with a **negative** coefficient; stage 1's "non-decreasing in curing"
   is not supported by the training data (A3, memo §4) and is not imposed on the deciding model.
4. **Terrain** from Copernicus GLO-90 (A2) because the v8 inputs carry zero elevation.
5. **Fuel, option (b)** (A3, control 6): the clean arm (no `fuel_code`, `hazshare`, `d_nonfuel_km`) decides; the full
   arm is not scored on the confirmation fires (§3; owner decision D6).
6. **Correction of the development numbers (2026-10-30).** `ladder.fit_rung2` ignored the arm until 2026-10-30, so
   the "clean arm" rung 2 and rung 4 of the 2026-10-29 ladder runs carried `hazshare` (the two arms' rung-2 values were
   identical, 0.913, which is how it was found). The function was fixed, and rung 2 and rung 4 of the clean arm were
   re-run under the identical folds, seeds, inner grid and rule (`scripts/ml_ember/rung2_clean_rerun.py`,
   `ladder/clean/rung2_rerun.json`): see §9. The deciding RUNG2 of §1 is the corrected clean model. Rungs 1 and 3 and
   every control were unaffected.
7. **RQ1b added** as a deciding comparison against the distance-only logistic, on the development finding that a
   single distance decay already beats M8 by +0.21 (memo §4). RQ1a is unchanged. Stage 1 §7's "at most two models
   scored" is read as two **ladder** models; DIST is the stage 1 §5 rung-0 reference with one coefficient.
8. **Hybrid offset.** Stage 1 §4 refits β per fold in development and uses the frozen β on the confirmation fires; the
   full-training HYBRID uses the frozen β on **both** sides (the full-split refit reproduces it to 0.03 in every
   coefficient, memo §3). The two product terms of the pre-declared interaction enter through the spline basis.
9. **C fixed** at 0.01 for RUNG2 and HYBRID (the modal inner-fold choice, 9 of 10 fold choices across the arms), not re-chosen on the full split.
10. **Stop rule** applied as written: rung 2 carried although the climb test failed (A4); rung 3's objective chosen on
    inner folds (A4); the scaled hybrid added as descriptive (A4). None is scored here except RUNG2 and HYBRID.
11. **Features built only for jump intervals** of decision fires (every interval of a fire is iterated, only those with
    a ranked jump are kept): the within-interval metric needs no other interval. Consequently **RQ5** (Brier of
    P(any jump), CRPS of distance) is **not scored on the confirmation set**; no calibrated occurrence model was
    carried from development (control 2 showed weather acting at fire level). RQ5 stays development-only (memo §2).
12. **ERA5 arm** run in development (memo §4), never deciding; reported in [R68] as a sensitivity if D7 is approved.
13. **Training frame.** 143 of the 165 included fires carry sampled rows (22 fires have no jump and drew no empty
    interval under the §3 rule); every included fire's intervals are in the evaluation table.
14. **Sealed path added** (A4): `engine/tests/spread/data/m8_parity/`.
15. The `assignment="decision"` parameter of `features.load_fire` (added 2026-10-30) reads the sealed decision
    detections only through the guard; the unit tests of stage 1 still pass (37 + 12 + 6 + 5 new).

## 8. Not scored on decision fires, and what comes after

Not scored: the full arm; rung 1; rung 3 (either objective); the scaled hybrid; the ERA5 arm; RQ5; any product
correction layer. **After §12 is appended:** the physics-translation memo (M8 corrections, each its own future
pre-registered test: wind entered once; a receptor-group establishment factor; expected counts not scaled by
interval length; the distance decay re-specified against the development curve); the final report [R68] with figures;
the draft PR (never merged by an agent); the owner's decisions on product use (opt-in, labelled; option C of stage 1
§11 before any product use) and on the open benchmark (D4).

## 9. Development results carried into this file (training split, never deciding)

Pooled out-of-fold within-interval AUC under the stage-1 §6 folds (memo §4; `ladder/{clean,full}/ladder.json`,
`ladder/clean/rung2_rerun.json`):

| Model | Clean arm (corrected 2026-10-30) | Full arm | Stored 2026-10-29 "clean" (= full) |
|---|---|---|---|
| B1 | 0.563 | 0.563 | — |
| M8 (β per fold) | 0.641 [0.587, 0.694] | 0.641 | — |
| DIST | 0.854 [0.816, 0.893] | 0.854 | — |
| rung 1 | 0.896 | 0.898 | — |
| **rung 2 (RUNG2)** | **0.909 [0.884, 0.933]** | 0.913 | 0.913 [0.889, 0.936] |
| rung 3 (LambdaRank) | 0.855 | 0.845 | — |
| **rung 4 (HYBRID)** | **0.675 [0.617, 0.729]** | 0.662 | 0.662 [0.603, 0.718] |

Differences, clean arm corrected (S3, fire-bootstrap 95 %, sign-flip p, development seeds): rung 2 − M8
**+0.267 [+0.210, +0.325], p < 10⁻⁴, positive in 5/5 folds**; rung 2 − DIST **+0.055 [+0.028, +0.082], p < 10⁻⁴, positive in 4/5 folds (fold 4 −0.009)**; rung 4 − M8 **+0.033 [−0.013, +0.081], p 0.094**; corrected rung 2 − stored rung 2
**−0.004 [−0.007, −0.001]. Stop rule re-checked on the corrected numbers: rung 2 − rung 1 = +0.013 (climb test fails), rung 1 is 0.013 below the best, outside the +0.01 band, so rung 2 stays carried (A4)**. Transfer (RQ4, descriptive, stored run): CAN → US 0.904, US → CAN 0.880, leave-one-year-out
0.868–0.949 for the carried rung against 0.56–0.68 for M8. β_w (RQ2b): ≤ 0 under every variant and wind source,
planted β_w = 1 recovered 5/5 (memo §3). Weather acts at fire level (control 2).

## 10. Seeds and frozen files

Seeds: `S_TEST 2026101803`, `S_BOOT 2026101804` (v8's scorer, reused); `S_POWER 2026103001`; stage-1 seeds unchanged
(`S_NEG 2026102501`, `S_FOLD 2026102502`, `S_MODEL 2026102503`, `S_BOOT_DEV 2026102504`, `S_TEST_DEV 2026102505`,
`S_CTRL 2026102506`).

| File | sha256 |
|---|---|
| `docs/PREREG_ML_EMBER_DEV.md (stage 1 + A1–A4)` | `01bd22d40e168fb22d3f79813c53fd34548fcf3ada926d3995fba42d61f71e58` |
| `scripts/ml_ember/confirm.py` | `757a42f8ac03468bfbc4881d1c04438c3fb952ce7b0c24362b1c93c0d1c4d3cf` |
| `scripts/ml_ember/ladder.py (corrected)` | `c34accbc6cb740135427c651e8aac195fbb3b2182385f12947b7b3a1126f14b3` |
| `scripts/ml_ember/features.py` | `a6d18bdf72b3f3bae179ce6b33a6ce64fc6e706681015fa840bf9025be38d1ea` |
| `scripts/ml_ember/cv.py` | `fedaed6c81c11ebb8ca08248f0e84471185b84f91b71faa1754810a682cd7bb5` |
| `scripts/ml_ember/loader.py` | `797a5d3609c02b38b63907331a0925b625b8a1cb9ce1e8f873d630f60f270035` |
| `scripts/ml_ember/paths.py` | `46900c6f8aa18559c97411cac63216b1571533e9c9193864740a26f5a5785380` |
| `scripts/ml_ember/build_table.py` | `1db0567a5684dc90f1e0b1b93baa978e7fc9f4b71a20be6336133d5534fa46b9` |
| `scripts/ml_ember/terrain.py` | `eda510a074136703bc25e8e8539be118c6f1f65154a7756fdbb429dc4e7c68ec` |
| `scripts/ml_ember/power_stage2.py` | `a28ae93398aa9e343a51c07948d3ffcc5b719473c4f8d91781b3ace39b7e2d7f` |
| `scripts/ml_ember/rung2_clean_rerun.py` | `c0524a2bbcdbfcaac427d17c4c9eb1b9e6a86a0cf4a6be233c54d60cbd9023bc` |
| `scripts/validation/v8_score.py (v8's scorer, unchanged)` | `623233f687eb97d83a5cd3d81806accb28f78a6ef699f941249247c38ebad880` |
| `engine/tests/validation/test_ml_ember_confirm.py` | `82a84c93db529f38ac321a75b4949c474db28d87410fde915a5dc5aaf6cc51d5` |
| `stage2/models.pkl` | `cf11ba9b7d6e2d5f2a65886a057a75319204d80db74f068a0c1b8f6405b65d43` |
| `stage2/models.json` | `304db7f9b07314f0555aae05c51ed56d7e154a0b008da4d6504cc75ca867e054` |
| `stage2/power_stage2.json` | `707008f3113bc9ab5ccf33584f33f43989bafc8f4d17b38d6f879492119b4f7e` |
| `ladder/clean/rung2_rerun.json` | `4e6689f5bb9c3c49176035555da6eba48d273f0a2a36e2d5934fa4ffff4d8837` |
| `ladder/clean/ladder.json (stored run)` | `e70ad67434cf992e8bf7d457790d62dbebee544a1d34ddae0344c1d4cf158436` |
| `ladder/full/ladder.json` | `229c52f6b8af99eca5094bca129249a0144a2346e8a8b8784181c27b1dc2e40d` |
| `beta_w/beta_w_check.json` | `5c061a335c6837ff407e1c7ac35d9a13c6bfe99a8f8918b27cbf990fe7cd48f5` |
| `era5/era5_arm.json` | `5d575967a5e65b4bb33ced2ff20e67ecafde3536f5eadf538ebc008132f7c7b6` |
| `table/cells_fit.parquet` | `34e113725773ba1705ab336657ba02489e658077e47076917d7383fd709b2805` |
| `table/cells_eval.parquet` | `fb09a1869f4f1674e597087cb7a95f8a763d52803843a454e639f7c150dadc6c` |
| `table/terrain.parquet` | `fa7a326c6c29a5c66486db3eca3971ed061fe519f6d97bbd33fddaecb5b17084` |
| `table/m8_suffstats.parquet` | `1acc127582a2d23511255c63db296a79883779012d717e3d0eef92e7ec1bff32` |
| `table/fires.parquet` | `7f31bca97110cb705a0adbba509e6165fd78e4f713b16f44c85f2b559e7ccef9` |
| `ML ember-cast development memo 2026-10-29.md (with addendum A)` | `af120c98ef076b61caae378164f6085648e9a5e23bce03388daf3d01a6b043aa` |
| `validation-data/v8-pool/fit/fit_beta.json (frozen, stage 1)` | `fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425` |

## 11. Owner decisions requested (with the professor's recommendation)

- **D5 — approve this stage-2 pre-registration** (models §1, pass rules and margins §3, procedure §4). *Recommend:
  approve.* Until the approval line below exists, nothing is scored.
- **D6 — the clean arm decides; the full arm is not scored on the decision fires.** *Recommend: yes.* (The alternative,
  scoring the full arm as a disclosed third fitted model, would breach stage 1 §7's two-model rule for ≤ 0.01 of
  development difference.)
- **D7 — the ERA5 arm is reported in [R68] as a disclosed sensitivity** (development only). *Recommend: yes.*

**Approval:** Travis Kennedy, 2026-10-30 06:24 MDT, in the professor session, in reply to the brief listing D5–D7 with the
recommendations above: **"Approve, yes and yes."** (D5 approved; D6 yes: the clean arm decides, the full arm is not
scored; D7 yes: the ERA5 arm is reported as a sensitivity). The commit that carries this line is the confirmation
commit; the registered text above is frozen from this commit on.

## 12. Result (appended after the one-shot run; registered text above never edited)

_(empty at the draft)_
