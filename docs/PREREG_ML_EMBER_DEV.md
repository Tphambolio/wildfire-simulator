# Pre-registration, stage 1: ML ember-cast development (feature contract, ladder, CV, controls)

**Status:** frozen 2026-10-25 (about 10:45 MDT), committed and pushed **before anyone on this project read the
v8 result report, and before any model was fitted.** The v8 ember test (PREREG_LR8) had not reported when
this file was written: its draw stood at 150 fires drawn per region, 116 jumps in all (CAN 62, US 54; the
count-only files `v8-pool/draw/{counts.csv,state.json,draw_log.txt}` at 10:20 MDT), short of its stop rule.
**Branch:** `research/ml-ember-cast`, from `origin/master` `fc3b2ea` (after PR #106).
**Owner approval:** Travis Kennedy, 2026-10-25 (about 10:25 MDT): "Proceed with D1 to D4 according to the
plan", that is every recommendation of the professor's review (*ML ember-cast professor review 2026-10-25*,
§0): D1 approve Phase 1 and write this stage-1 document now; D2 hold-out B now, C sealed now for product
use, the Canadian 2026 `confirm` subset of C4 earmarked for the ember target, the 24 unsealed US 2026 records
sealed as a scale-only block; D3 a disclosed ERA5 sensitivity arm; D4 an open benchmark release planned in
principle. **Nothing is fitted until v8 has decided** (the brief's Phase 2 gate, owner rule "no ML without
the owner"); this file fits nothing and opens nothing.
**Supersedes nothing; constrains:** stage 2 (`docs/PREREG_ML_EMBER.md`, written after development on the
training split, approved by the owner, committed and pushed before any decision fire is scored). Stage 2
may **add** (seeds, sha256s, the exact chosen rung, hyperparameters) and must **disclose** every deviation
from this file; it may not remove a control, widen the feature list, or change a deciding comparison.
**Record:** `docs/ML_EMBER_RECORD.md` (this branch). Reports: `~/dev/wildfire/reports/` (review, proposal,
role brief; sha256s in §16). Claude for Science project `proj_ac346e168289`.

Nothing in this file was changed after any decision-fire jump location, VIIRS hotspot attributed to a
decision fire, decision-fire interval product, model score on a decision fire, or sealed 2026 record was
seen. None has been opened (§15).

---

## 1. Questions (amended from the proposal §5 by the review §3)

Unit, labels, candidate cells, ranks and the S3 statistic are **exactly PREREG_LR8 §4 and §6**, so that
every ML number sits on the same scale as the v8 result. All tests are one-sided fire-level sign-flip
randomisation tests on S3 (PREREG_LR8 §7.3), p < 0.025, 20,000 flips; two deciding tests are declared, no
further correction.

| RQ | Deciding or descriptive | Comparison (stage 2 fixes the model identities) | Sized for |
|---|---|---|---|
| **RQ1 skill beyond M8** | **Decides** | S3(best unconstrained rung − M8) > 0 on the confirmation fires | Δ = +0.08 (review §5: power 0.97 at ρ_s 0.8, 0.85 at 0.6; 0.72 / 0.49 at +0.05). Minimum detectable gain at 80 % power about +0.06 to +0.075 |
| **RQ2a within-interval diagnosis** | Descriptive | ALE shapes of distance to the active edge, downwind offset, receptor fuel and curing, terrain; reported only if sign and rough shape agree in ≥ 4 of 5 outer folds | — |
| **RQ2b between-interval occurrence** | Descriptive | Occurrence model (jumps per interval) with wind speed, m_ff, intensity, interval length; **the β_w check of §9** | — |
| **RQ3 constrained hybrid** | **Decides** | S3(hybrid − M8) > 0. Descriptive: (hybrid − M8)/(best rung − M8) ≥ 0.5 "closes most of the gap" | Δ = +0.08 |
| **RQ4 transfer** | Descriptive | train-CAN → test-US and the reverse; leave-one-year-out for 2023, 2024, 2025 (earlier years pooled); S3 by direction with fire-bootstrap CIs | — |
| **RQ5 probabilistic** | Descriptive | Brier skill of P(any jump in the interval) against the training-split regional base rate; CRPS of jump distance against the training empirical distance distribution; reliability per region | — |

The proposal's bands ("≥ +0.05 means the physics misses structure; ≤ +0.02 means M8 is near the information
limit") are **descriptive statements with CIs**, never verdicts. "M8 is near the information limit" is not
claimed from a null result; the report states the minimum detectable gain instead.

**Weather is observed ("perfect prognosis")** for the interval, as in M8 and the v8 target. Every report says
so. Skill measured here is conditional on the interval's observed weather; an operational 12 h forecast
would use forecast weather and is out of scope.

## 2. Data (open only; nothing requested from any agency)

Sources, pool, split, exclusions and jump rule are **PREREG_LR8 §2–§4** (frozen `6452c1d`, addenda A1–A3 on
`feat/long-range-embers-v8`). Development uses **only the training split**:

| Item | Path (outside the repository) | sha256 |
|---|---|---|
| Training split membership | `reports/data/v8-prefreeze-checks-2026-10-18/results/split.csv` | `dee6eaac66cf6ddccc89e5992009904e74dc099aaac08402d3a6f7a72163b5c1` |
| Training fires with run status | `.../results/fires_training.csv` | `5749f1f36eab6817e53f954e443ac5bea08e1ca2412d047823b1fe8bb42620a3` |
| Training jump catalogue (labels) | `.../results/jumps_training.csv` | `5da778ae194c09d3d6d05f03cd5c15b3e8334786c159f846453bfbc7b23ff54e` |
| Per-fire inputs | `validation-data/v8-pool/fires/<key>/{inputs.npz, meta.json, wx_<station>.csv, intervals/*.npz}` (training keys only) | per-file sha256s in the frozen table's `_about.json` (stage 2) |
| Training hotspots | `validation-data/v8-pool/viirs_training/viirs_training_<year>.pkl.gz` | stage 2 |
| M8 frozen coefficients (confirmation only; refit per fold in development, §4) | `validation-data/v8-pool/fit/fit_beta.json` | `fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425` |
| G1 gate ranges (reported, never a feature) | `validation-data/v8-pool/fit/gate_ranges.json` | `3dd02d3a9c86b9fa28accbfae239160e5351eb0d72b0d7b5aa381e54e92c5a22` |

Training fires actually run by v8 at the freeze of this file: 62 Canadian (194 catalogue jumps, 27 fires with
jumps) and 112 US (64 jumps, 33 fires), of which 239 jumps (178 CAN, 61 US) on 167 fires were matched to a
valid interval and used by the M8 fit; 400 catalogue jumps lie on fires without an eligible station. These are
the development sample; the station-arm models never see a fire without an eligible station.

**Sealed and forbidden in development** (the loader of §14 refuses them): `v8-pool/viirs_decision/`,
`v8-pool/draw/sealed*`, `v8-pool/draw/sealed_manifest.csv`, any decision fire's `intervals/`, any model
score or run output on a decision fire, `validation-data/sealed2026/` (every file; the `_about.json` and
`README.md` of that folder excepted), and ELMFIRE outputs (`v8-pool/elmfire_runs/`, decision fires only).
Only `draw/counts.csv`, `draw/state.json` and `draw/draw_log.txt` (per-block counts) may be read.

## 3. Unit, labels, candidate cells, negatives

- **Unit:** a candidate cell (375 m) in a fire-interval; **candidate cells, intervals, jumps and gap classes
  exactly as PREREG_LR8 §4 and §6** (burnable, centres 1–15 km from the start perimeter, unburned at
  t_start, inside the run box; valid intervals ≤ 12 h; a jump counts once at the 375 m cell of its nearest
  qualifying member; "never reached" clusters enter counts and Brier only, never ranks).
- **Positive:** the candidate cell holding a counted jump. Everything else in the candidate set is a negative.
- **Scoring always uses the full candidate set** of every interval (ranks as PREREG_LR8 §6). Subsampling is
  a fitting device only.
- **Negative subsampling for fitting (declared, review amendment 13):** per interval with ≥ 1 jump, all
  positives plus a uniform sample of **200 negatives** (all if fewer); per interval with no jump, intervals
  are sampled with probability **0.25** and each sampled interval contributes **50 negatives**; seed
  `S_NEG = 2026102501`, stream `default_rng(S_NEG + fire_index)` with fires in the sorted order of
  `split.csv`. Weights 1/sampling-fraction restore the interval's candidate count in Poisson and logistic
  objectives. The occurrence model (RQ2b) uses **all** intervals at interval level, no subsampling.
- **Whole fires are the unit of independence.** No split, fold, bootstrap or permutation ever divides a fire.

## 4. Feature contract (the only features any rung may use)

Every feature is computable at the interval start t_start from inputs that do not depend on what the fire did
after t_start or on where it ended, **except** the interval's observed weather, which is allowed with
disclosure (§1) because M8 and the v8 target use it. Features marked **P** may enter a product correction
layer; **D** (diagnostic) may not. Names are the frozen column names of the feature table.

| Family | Feature | Definition | Source | Flag |
|---|---|---|---|---|
| geometry | `d_act_km` | distance from the cell centre to the nearest active-segment cell | `intervals/*.npz` `d_act` | P |
| geometry | `d_all_km` | distance to the nearest start-perimeter edge cell | `d_all` | P |
| geometry | `sec_act`, `sec_all` | cell within ±45° of the interval mean downwind direction from the nearest active / any edge cell | `sec_act`, `sec_all` | P |
| geometry | `bear_off_deg` | absolute angular offset (0–180°) between the bearing from the nearest active cell to the candidate and the interval mean downwind direction | recomputed from the start perimeter and `u10` direction by the frozen `intervals.py` path | P |
| geometry | `act_len_km`, `act_share` | active-edge length; active cells / all edge cells of the start perimeter | start-perimeter products of epoch i−1 | P |
| geometry | `fire_area_km2`, `fire_age_h` | start-perimeter area; hours since the first in-perimeter detection | epoch products | P |
| geometry | `gap_h` | interval length t_end − t_start (hours) | epoch times | P (disclosed, low risk) |
| geometry | `hour_local` | local solar hour of t_start | t_start, grid longitude | P |
| weather | `u10_ms`, `wd_deg` | per-zone interval mean 10 m wind speed and direction (station arm) | `u10`, `wx_*.csv` via the zone-station map | P (disclosed) |
| weather | `m_ff_pct`, `ffmc`, `temp_c`, `rh_pct` | per-zone interval means | `m_ff`, `wx_*.csv` | P (disclosed) |
| weather | `stn_dist_km` | distance from the cell's zone station to the fire | `meta.stations` | D |
| fire behaviour | `I_head_kwm`, `ros_mmin`, `cfb` | FBP head intensity, spread rate, crown fraction at the nearest active segment, at the observed spread rate over the **previous** interval | M8's source arrays (epoch i−1) | P |
| fire behaviour | `spread_prev_km` | observed advance of the perimeter over the previous interval at the nearest active cell | epochs i−2, i−1 | P |
| fire behaviour | `frp_prev_mw` | summed VIIRS FRP of active-segment detections at **epoch i−1 only** | training hotspots | P |
| receptor | `fuel_code`, `fuel_group` | FBP code at the cell (Canada; US crosswalk); group grass / D-M / C-timber as M8's WAF groups | `inputs.npz` `code` | P (gated by the fuel-seam control, §8) |
| receptor | `hazard`, `hazshare` | B1 hazard class at the cell; hazard share within 500 m | `hazard`, `hazshare` | P |
| receptor | `curing_pct` | grass curing | `curing` | P |
| receptor | `elev_m`, `slope_pct`, `aspect_deg`, `tpi_1km` | elevation; slope; aspect; topographic position index (cell minus the 1 km mean elevation; terrain-relative, never box-relative) | `elev` | P |
| receptor | `d_nonfuel_km`, `d_water_km` | distance to the nearest non-fuel / water cell | `code` | P |
| receptor | `d_act_x_fuel_group` | the only pre-declared interaction (≤ 3 pairwise interactions allowed in the hybrid; the other two are named in stage 2 from the ladder's ALE read-out, disclosed) | derived | P |
| physics | `m8_log` | log of the M8 expected established spots for the cell, **β refitted inside every outer fold** on that fold's training fires with `fit.py`'s sufficient statistics (amendment 3); the frozen `fit_beta.json` is used only on the confirmation fires | `A`, `u10`, `m_ff`, `fit.py` | P |
| physics | `b1` | B1 score (no fitting) | as PREREG_LR8 §5 | P |
| context | `region`, `year`, `sat_era` | CAN / US; calendar year; S-NPP-only (before 2018-04) vs S-NPP + NOAA-20 | `meta.json` | D (RQ4 only; never in a product) |
| quality | `fuel_restored_share`, `perim_agree_flag`, `gap_class` | share of the box re-typed from SCANFI; v8 perimeter-agreement flag; gap class of the jump | `meta.json`, v8 products | D (strata for reporting; never a feature) |

**Forbidden, in every rung and every arm (review §4, amendments 1, 2, 4, 5):**
- any coordinate or position relative to the run box (row, column, normalised position, distance to the
  box edge), and any "relative position" that is not purely terrain-derived;
- every neighbourhood-texture statistic of the fuel grid (code entropy, patch size, edge density,
  code-change counts, autocorrelation), at any radius;
- the final perimeter, the end-of-interval perimeter, any detection at epoch i or later, FRP at t_end, the
  gap class, the jump count of the interval or fire, and anything derived from them;
- the ELMFIRE score (no runs exist on training fires; it stays a reported reference on its decision-fire
  subset in stage 2);
- any weather after t_end; any forecast weather; any weather at a decision fire before v8 reports;
- station and ERA5 weather in one model (§10);
- fire identity, fire key, or any per-fire constant not listed above.

**Rule for additions.** A feature not in this table may be added only in stage 2, named and defined there,
flagged "added after stage 1", and excluded from the two deciding models unless the owner approves it.

## 5. Model ladder (review §7; each rung under the CV of §6)

| Rung | Model | Constraints and hyperparameter rule | Max features |
|---|---|---|---|
| 0 | References: B1; M8 (β per fold); distance-only logistic (`d_act_km`); distance + `sec_act` logistic | none fitted beyond M8's β₀, β_w, β_L | — |
| 1 | Penalised logistic (within-interval, cell level) and Poisson (interval level) GLM on physics-named features | L2 penalty chosen on the inner folds from {0.01, 0.1, 1, 10} | 15 |
| 2 | Additive model: `sklearn` spline features (≤ 6 knots per feature) with penalised logistic regression; monotone where physics is unambiguous: non-increasing in `d_act_km` beyond 1 km, non-decreasing in `curing_pct`, non-increasing in `m_ff_pct` | penalty on inner folds; knots fixed at training quantiles | 15 |
| 3 | Gradient-boosted trees, LightGBM 4.6: `max_depth` ≤ 4, `num_leaves` ≤ 15, `min_data_in_leaf` ≥ 200, `learning_rate` 0.05, early stopping on the inner folds (patience 50, ≤ 2,000 rounds), `feature_fraction` 0.8, monotone constraints as rung 2; alternative objective LambdaRank grouped by interval | rounds and `min_data_in_leaf` ∈ {200, 500} on inner folds | 25 |
| 4 | Hybrid: rung 2 or rung 3 (whichever rung 3's stop-rule verdict selects) with `m8_log` as an **offset** (`init_score` / `base_margin`), ≤ 3 pairwise interactions (§4), same constraints | as its base rung | 25 |
| — | **CNN / U-Net: not permitted in this project** (review §7 item 6) | — | — |

Seeds: `S_MODEL = 2026102503` for every stochastic fitting step (stream per outer fold:
`S_MODEL + fold_index`). Class weights: none beyond the subsampling weights of §3.

**Interpretation:** accumulated local effects and grouped permutation importance first; SHAP only as a
secondary view with the correlated-feature caveat; nothing is reported as a finding unless its sign and rough
shape hold in ≥ 4 of 5 outer folds.

## 6. Cross-validation design (nested, whole-fire grouped)

- **Outer folds:** 5, grouped by fire, stratified by region × year, assigned by sorting fire keys within each
  stratum, permuting with `default_rng(S_FOLD + stratum_index)`, `S_FOLD = 2026102502`, and dealing in
  round robin. Fires with no jump are folded the same way (they contribute negatives and occurrence zeros).
- **Inner folds:** 3, grouped by fire, within each outer training set, for every hyperparameter choice in §5.
  Nothing is chosen on an outer test fold.
- **Per fold:** β₀, β_w, β_L of M8 refitted on the outer training fires (`fit.py fit()` on their sufficient
  statistics, same priors and bounds as v8); `m8_log` of the test fold uses that fold's β.
- **Transfer folds (RQ4, descriptive):** train CAN → test US; train US → test CAN; leave-one-year-out for
  2023, 2024 and 2025 (all other years pooled as training).
- **Statistics:** S3 and its fire-bootstrap 95 % interval (2,000 draws, `S_BOOT_DEV = 2026102504`) on the
  pooled out-of-fold predictions; per-fold S3 reported; the fire-level sign-flip test (20,000 flips,
  `S_TEST_DEV = 2026102505`) for every rung-to-rung difference. Out-of-fold predictions are the only
  development numbers ever reported.

## 7. Stop rule (climbing the ladder)

Climb from rung r to r+1 only if, on the pooled out-of-fold predictions, **all three** hold:
1. S3(r+1 − r) ≥ **+0.02**;
2. the fire-bootstrap 95 % interval of that difference has a lower bound > **−0.01**;
3. the main effects of rung r+1 keep their sign in **≥ 4 of 5** outer folds.

Otherwise stop, and carry forward the **simplest rung within +0.01 of the best**. Rung 4 is fitted
regardless of where rungs 1–3 stop (RQ3 needs it), on the base rung the stop rule selected. Stage 2 scores
at most **two** models on the confirmation fires: the carried rung (RQ1) and the hybrid (RQ3).

## 8. Controls (every control is run before any result of §5 is reported; review §4, §6.3, amendment 12)

**Negative controls (each must give S3-AUC within 0.50 ± 0.03 on held-out fires; a failure stops reporting
until the leak is found, review §10):**
1. labels shuffled within each interval;
2. interval weather shifted by ±1 day (the station series of the same fire);
3. features of another fire (fires paired by a seeded permutation, `S_CTRL = 2026102506`);
4. **fold-level permutation:** fire labels permuted across fires (catches per-fire constants);
5. **box-leak control:** rung 3 on box-relative (row, column) coordinates only;
6. **fuel-seam control:** rung 3 on fuel-derived features only (`fuel_code`, `hazard`, `curing_pct` and,
   deliberately, the forbidden texture features) trained to predict "candidate cell lies inside the final
   perimeter" on training fires, fire-grouped CV. **Pass: AUC ≤ 0.55.** Fail: the fuel features leak the
   footprint. Then either (a) the whole Canadian box is re-typed from one pre-fire SCANFI epoch (one source;
   feasibility: SCANFI server speed, local Alberta mirror only), or (b) fuel is restricted to `hazard` and the
   ladder is run twice, with and without fuel, both reported. The choice is recorded in stage 2.

**Positive controls (each must recover the planted effect):**
7. **planted wind effect through the physics:** synthetic counts generated from the training intervals' own
   `A` arrays with β_w = 1 (other β at the fitted values), `fit.py` run unchanged, must return β_w ≈ 1
   (within its profile 95 % interval). If it cannot, the M8 structure cannot see wind regardless of the data,
   and §9's reading changes accordingly;
8. **planted ML signal:** a synthetic label set with a known monotone effect of `bear_off_deg` and `u10_ms`
   planted at the training positives' rate; rungs 1–3 must recover the sign and the ALE shape in ≥ 4 of 5
   folds.

## 9. The β_w check (RQ2b; fits only M8's own coefficients; runs once v8 has decided)

As the review §6.3, in this order, training split only:
1. unbounded refit, β_w free in (−5, 5), profile-likelihood 95 % interval; then β_w with κ_g free per
   receptor fuel group; both reported;
2. between-interval count model: jumps per interval ~ Poisson, offset log Σ_c λ_c at β_w = 0, free
   log-linear term in ln(1 + U10); by gap class, by interval length (< 8 h vs 8–12 h), by region;
3. label sensitivities: strict rule (no `never_detected`); "possible jumps" (line-check-removed clusters
   within 1–15 km re-included as positives); 2 km minimum distance;
4. wind source: ERA5 10 m wind over the active segments (§10) in place of the station, same models;
5. positive control 7 of §8;
6. ML read-out: ALE of `u10_ms` in the occurrence model, ALE of `fuel_group`, their interaction.

Readings (fixed now): receptor confound → a physics change to M8 (κ_g free or a receptor detectability
factor; its own pre-registered test); censoring → shorter intervals and the 14 h sensitivity, and the report
says the target under-counts wind-driven spotting; double counting → restructure the emission–establishment
split; station → the ERA5 arm becomes the recommended input for a future M8 fit. None is a reason to park
embers.

## 10. Weather arms (owner decision D3)

- **Primary arm: station-only, exactly M8's inputs** (IEM ASOS/METAR hourly, PREREG_LR8 A2.1; per-zone
  station map from `meta.json`). Every deciding comparison uses this arm.
- **Sensitivity arm: ERA5 hourly 10 m wind, temperature, RH at the active segments** (Open-Meteo archive,
  CC BY 4.0; the source the pipeline already uses for station precipitation), interval means per zone.
  Three uses, all disclosed, never deciding: (a) §9 item 4; (b) the whole ladder re-run with ERA5 features;
  (c) the training fires without an eligible station (the 400 lost catalogue jumps) added, to test the
  station-selection effect. ERA5-vs-station errors measured 2026-10-08 (wind-direction sd 18°, log-speed
  sd 0.24 on fire-weather days) are quoted with every ERA5 result.
- **Never:** the two sources in one model; forecast winds; ERA5 or any weather at a decision fire before v8
  reports; an ERA5 result used to choose the primary-arm model.

## 11. Hold-out plan (owner decision D2)

- **Option B (confirmation, stage 2):** the v8 decision fires drawn under PREREG_LR8's frozen stop rule,
  scored once with PREREG_LR8's candidate cells, labels and metric, after the owner approves stage 2.
  Disclosure carried into stage 2 (review §5.3): the decision fires' labels have been seen by the v8
  analysis, the owner and the professor in aggregate and per stratum, but by no ML model, and this file's
  commit hash and date show that the feature contract predates that reading. **If v8 ends "not
  decidable"** (caps or pools reached before K ≥ 150), stage 2 recomputes power from the counts; below 0.8 at
  Δ = +0.08 the confirmation is reported as descriptive and the decision moves to option C (amendment 17).
- **Option C (any product use):** no correction layer enters FireSim without a pass on fires that did not
  exist when the models were designed. Two blocks, both governed by `docs/SEALED_2026.md`:
  - the **Canadian 2026 `confirm` subset** of C4 (NBAC 2026, expected about May 2027, seed
    `S_SEAL_CA = 2026102402`, rules fixed in SEALED_2026 §5): **earmarked for the ember target L** by the
    owner (D2). The claim is recorded in SEALED_2026 (branch `feat/c4-sealed-us-supplement`) and spends those
    `confirm` fires for deciding when the test is run;
  - the **24 unsealed 100 %-contained US 2026 records** (SEALED_2026 §6): sealed as a **scale-only** block
    (seed `2026102403`, SEALED_2026 §6a), used to add fires to C, never to decide (the US block alone has
    power below 0.15, review §5).
- **Option A** (training split, nested CV) is development only; its numbers are never called a confirmation.

## 12. Stage-2 pass-rule template (to be completed, not changed, in `docs/PREREG_ML_EMBER.md`)

1. Models: the carried rung (named) and the hybrid (named), with hyperparameters, seeds and code sha256s.
2. Confirmation set: the v8 decision fires as drawn; K and per-region counts; power recomputed from the
   counts at Δ = +0.08 (§11).
3. **RQ1 passes** if S3(carried rung − M8) > 0, one-sided sign-flip p < 0.025 (20,000 flips, seed named).
4. **RQ3 passes** if S3(hybrid − M8) > 0, same test.
5. Reported with each: the estimate, fire-bootstrap 95 % interval (2,000 draws, seed named), by region, by
   gap class, by active-share tercile; the ELMFIRE comparison on its subset (reference only).
6. Blindness statement: who read what of the v8 result report, when; the hash of this file; the list of
   stage-1 deviations.
7. Result section appended only; registered text never edited; no model, feature or rule changes after any
   confirmation score is seen.

## 13. Open benchmark release (owner decision D4: planned in principle)

Planned contents: the frozen training-split jump catalogue, the feature table of §4 (float32 Parquet) with
its `_about.json`, the fire-level split and fold assignment, the baseline scores (B1, M8 per fold), the
scoring code, and this file. **Before any release:** a licence check per source (NASA FIRMS acknowledgement
and DOIs; NBAC and CFS OGL-Canada; MTBS and LANDFIRE public domain; WFIGS public domain; IEM station data
terms; Open-Meteo CC BY 4.0; MODIS NDVI); no decision-fire or sealed-2026 content until v8 and stage 2 have
reported; the owner's decision on timing. The product-hybrid question is decided after RQ3's result, not
before.

## 14. Infrastructure, guards, compute and records

- **Branch and tree:** `research/ml-ember-cast` in `~/dev/wildfire/wt-ml-ember`; one worktree per agent.
  Never merged by an agent; auto-merge never enabled.
- **Loader guard (amendment 16, unit-tested):** `scripts/ml_ember/loader.py` refuses every path of §2
  "sealed and forbidden" unless called with `--confirmation-commit <sha>` naming the stage-2 commit **and**
  that commit exists on the remote. A refused open is logged.
- **Feature builder:** `scripts/ml_ember/features.py`, deterministic, with unit tests that (a) every column
  is in §4, (b) no column depends on epoch i detections (built from a synthetic fire where epoch i is
  perturbed; features must be bit-identical), (c) box-relative coordinates and texture statistics are absent,
  (d) FRP uses epoch i−1 only.
- **Frozen table:** Parquet float32 (`fastparquet` or a dedicated conda env; no system installs), one row
  per sampled candidate cell-interval (§3) plus a full-candidate index for scoring; sha256s in stage 2 and in
  the data folder `reports/data/ml-ember-cast-<date>/{README.md,_about.json}` from day one.
- **Compute:** every heavy job `capjob <cmd>` with `CAPJOB_MEM` ≤ 5G and ≥ 8 GB available (`free -g`); at
  most two heavy agents at once; full test suites one at a time under `flock
  /tmp/claude-1000/firesim-tests.lock`; the v8 draw, C1 and the sealed H1/H2 tests share the workstation.
- **Records:** every figure with the full caption (purpose, what is shown, how to read, key result, caveats,
  references); `docs/ML_EMBER_RECORD.md` appended after every step; report number **[R68]** claimed in
  `docs/REPORT_NUMBERS.md` for the programme's report; everything backed up to Claude for Science
  `proj_ac346e168289` (`tools/c4s_upload.sh -p proj_ac346e168289 -x <slug>__<subdir>`), verified after each
  upload.
- **Never in the product:** evacuation-tier suggestions, addresses, owner data, per-building results, SFOC
  numbers or condition IDs.

## 15. Disclosures (what was looked at before this freeze)

- Read: the proposal, the role brief, `PREREG_LR8.md` and addenda A1–A3, [R52], [R54] (all of it, including
  §10a owner decisions and §14), `SEALED_2026.md`, `validation.md`, the v8 pre-freeze README, the v8 runner
  code (`scripts/validation/v8/{fit,intervals,inputs,common}.py`).
- Opened, training split only: `v8-pool/fit/{fit_beta,gate_ranges,training_diagnostic}.json`;
  `v8-prefreeze-checks-2026-10-18/results/{split,fires_training,jumps_training}.csv` and
  `jump_rate_summary.json`; one training fire's `meta.json`, `inputs.npz` and one `intervals/*.npz`
  (NBAC:2018_1414; field names and shapes only, used for §4); `v8-pool/fires/NBAC_2018_1001/meta.json`
  (a training fire without an eligible station); `runs.csv`.
- Opened, counts only (allowed): `v8-pool/draw/{counts.csv,state.json,draw_log.txt}` on 2026-10-25 at
  02:10 and 10:20 MDT.
- Opened, sealed-2026 folder: `us/draw_summary.json` (counts and hashes, no fire names) and the **rule
  column only** of `us/eligibility_log.csv` (24 records removed as `rule5_no_end_time`), to write SEALED_2026
  §6a; no fire name, state or geometry of any 2026 record was displayed.
- Simulated: the power study of the review §5 (`power_options.py`, seed 20261025).
- **Not opened, by anyone on this project:** the v8 result report (none exists at this freeze), any
  decision-fire hotspot attribution, interval product, jump location or model score; any sealed 2026 fire
  record; `training_diagnostic.json`'s FAIL line is a `gate=None` artefact (review §11) and is not a result.
- No model of any kind has been fitted to any ember data by this project.

## 16. Frozen files

| File | sha256 |
|---|---|
| `reports/ML ember-cast professor review 2026-10-25.md` | `55030671b4a73236d841b0355ff493678598e3d8fee63ea5390a98dbb5ac5237` |
| `reports/ML ember-cast research proposal 2026-10-25.md` | `ec01271175de420e8be06ec81d7517de4728c74739865df451c7384489fb0ac9` |
| `reports/ML ember-cast professor role brief 2026-10-25.md` | `6156a15db71c056ad66382f2454e1ff4add3def7d01d8a4de5786717bbff345b` |
| `reports/data/ml-ember-cast-professor-review-2026-10-25/scripts/power_options.py` | `6213c0d8ad12ee17be71491cc18105e282dda044425253561c9eb19ae336edcd` |
| `reports/data/ml-ember-cast-professor-review-2026-10-25/results/power_options.json` | `367d42b2f1ec592ccda24cadd130ff810442dbaa38dc01d2c2b4a49fd0687a96` |
| `validation-data/v8-pool/fit/fit_beta.json` | `fda5706c400cd74b07b7243c110031d09aa9160445cdcd2996a63ac8d18ec425` |
| `v8-prefreeze-checks-2026-10-18/results/jumps_training.csv` | `5da778ae194c09d3d6d05f03cd5c15b3e8334786c159f846453bfbc7b23ff54e` |
| `v8-prefreeze-checks-2026-10-18/results/split.csv` | `dee6eaac66cf6ddccc89e5992009904e74dc099aaac08402d3a6f7a72163b5c1` |
| PREREG_LR8 freeze commit | `6452c1d`; branch head at this freeze `1c67168` (addendum A3) |

Seeds declared in this file: `S_NEG 2026102501`, `S_FOLD 2026102502`, `S_MODEL 2026102503`,
`S_BOOT_DEV 2026102504`, `S_TEST_DEV 2026102505`, `S_CTRL 2026102506`. Stage 2 adds its own.

## 17. Addenda (dated; appended only)

### A1 (2026-10-29, 02:15 MDT): v8 has reported; blindness statement; sealed paths named

- **v8 result read.** The professor read *Long-range embers v8 result 2026-10-29* ([R69], PR #114) in full at
  02:12-02:20 MDT on 2026-10-29, the first reading of any v8 result by anyone on this project. This file's
  stage-1 freeze (`07fbf59`, 2026-10-25 10:45 MDT) predates it by four days. What was read: aggregate and
  per-stratum results only (K 176, 159 ranked jumps in 73 fires; M8 AUC 0.710 CAN / 0.642 US; B1 0.571 / 0.489;
  S3(M8 − B1) +0.147; ELMFIRE at M8's level; M8 skill falling with distance; counts ×0.4 CAN / ×6 US; Brier
  worse than climatology). **Not opened:** the per-jump files `results/{jumps,ranks_per_jump,sealed_manifest}.csv`
  of the v8 data folder, `v8-pool/final/`, and any decision fire's `intervals/`.
- **Consequence for the plan (no rule change).** The confirmation set (§11, option B) is now fixed: the 203
  eligible decision fires, 159 ranked jumps in 73 fires. Stage 2 recomputes power from these counts (the §1 sizing
  used a simulated median K of 162, so the +0.08 sizing is expected to stand). The v8 finding that M8's skill
  falls with distance and that B1 is near chance in the US are **disclosed as known to the designer** before
  stage 2; they do not add features (§4 is closed) but will be named in stage 2's blindness statement.
- **Sealed paths, added to the loader's refusal list (§2, §14):** `validation-data/v8-pool/final/` (every file),
  `reports/data/long-range-embers-v8-2026-10-29/results/{jumps.csv,ranks_per_jump.csv,sealed_manifest.csv,
  score_out.json,secondary.json,sensitivities.json,unseal_exclusions.json}`, `v8-pool/elmfire_runs/` (decision
  fires), and every `v8-pool/fires/<key>/intervals/` whose key is not in the training split. The guard is tested
  against each.
- **Code dependency.** `origin/feat/long-range-embers-v8` (head `593e2d1`, the v8 result commit) is merged into
  this branch so that `scripts/ml_ember/` imports the frozen v8 code (`scripts/validation/v8/{intervals,inputs,
  fit,common}.py`, `v8_score.py`) rather than copying it. No v8 file is modified on this branch.
- **Labels.** Development labels are the training catalogue as re-run under PREREG_LR8 A1 (the v8 report states
  it is identical to the pre-freeze catalogue, sha256 `5da778ae…ff54e`); the loader verifies that identity and
  records both hashes in the data folder's `_about.json`.
