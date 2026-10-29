# ML Ember-Cast (FireSim) — Project Record

Living record of the work, decisions, references and testing behind the **ML ember-cast research programme**: learning where embers from a large wildfire start new spot fires 1–15 km ahead in the next 12 hours, from FireSim's open satellite spot-jump dataset, to measure the achievable skill, diagnose FireSim's physics ember model (M8) and, if justified, propose a constrained physics-plus-statistics correction. Lives at `docs/ML_EMBER_RECORD.md` on branch `research/ml-ember-cast` (moved here 2026-10-25 from `~/dev/wildfire/reports/ML ember-cast project record 2026-10-25.md`, which now points here) and is re-attached to the Claude for Science project after every change. Append; never rewrite history.

- **Deliverables:** a professor's review (done), a development pre-registration, a confirmation pre-registration, a final report with figures, a physics-translation memo, a draft PR (never merged by an agent). No product change without the owner's decision.
- **Claude for Science:** this programme is `proj_ac346e168289` (http://localhost:8000/projects/proj_ac346e168289), created 2026-10-25 04:05 MDT; artifacts are also mirrored to the FireSim project.
- **Parent project:** FireSim (`~/dev/wildfire/wildfire-simulator-v3`, GitHub `Tphambolio/wildfire-simulator`; Claude for Science `proj_4c36553a0c0c`). The ember data and the v8 test live there.
- **Owner:** Travis Kennedy, P.Ag. **Supervisor:** the professor agent. **Analyst:** the postdoc agent (proposal author).
- **Status at this revision (2026-10-25, 11:00 MDT):** Phase 1 (review) complete, verdict "proceed with amendments"; **owner approved D1–D4 as recommended (2026-10-25 ~10:25 MDT)**; stage-1 development pre-registration `docs/PREREG_ML_EMBER_DEV.md` written and pushed before anyone read a v8 result; D2 sealing actions on branch `feat/c4-sealed-us-supplement`; waiting for v8 to decide before any infrastructure or fitting (brief Phase 2 gate). **No ML model fitted. No sealed path opened.**

---

## 1. Purpose and audience

**Question.** Given a fire's observed perimeter, its active edges, fuels, hourly station weather, terrain and the previous interval's spread, where in the next ≤ 12 h will a new VIIRS-detected spot fire appear 1–15 km beyond the perimeter? Three deliverables: (1) the skill achievable from these inputs on unseen fires; (2) a diagnosis of what M8 mis-weights (above all its fitted zero bed-wind coefficient); (3) if justified, a constrained, explainable correction layer on M8.

**Audience.** The owner (EOC Chief of Plans, ICS-trained, not an academic); fire agencies and EOC planners who must be able to follow any model that reaches the product; later reviewers.

**What it is not.** Not a black-box replacement for FireSim's physics. Not an operational forecast (interval weather is observed, "perfect prognosis"). Nothing from it enters the product without its own pre-registered test and an owner decision, and the product never shows evacuation-tier suggestions, addresses, owner data, per-building results, or SFOC numbers or condition IDs.

## 2. Architecture (pre-registration done; no code built yet)

| File / module | Role | Status |
|---|---|---|
| `reports/ML ember-cast research proposal 2026-10-25.md` | The postdoc's proposal (questions, data, methods, hold-out options, decisions requested) | done |
| `reports/ML ember-cast professor role brief 2026-10-25.md` | The professor's mandate, binding rules, phases | done |
| `reports/ML ember-cast professor review 2026-10-25.md` | Verdict, RQ audit, leakage audit, hold-out decision with measured power, β_w check design, ladder, weather, 17 amendments | done |
| `reports/data/ml-ember-cast-professor-review-2026-10-25/` | Power simulation (script, results, Fig. 1), README, `_about.json` | done |
| `docs/PREREG_ML_EMBER_DEV.md` (stage 1) | Feature contract (25 named features, 8 forbidden classes), negative subsampling, ladder with hyperparameter rules, stop rule, nested grouped CV with seeds, 8 controls, β_w check, weather arms, hold-out plan B/C, stage-2 template, benchmark plan, disclosures, frozen sha256s | **done 2026-10-25**, pushed before anyone read a v8 result |
| `scripts/ml_ember/{__init__,paths}.py` | Package set-up (frozen v8 code imported, never copied); roots, the sealed list of §2 + A1, seeds, frozen sha256s | **done 2026-10-29** |
| `scripts/ml_ember/loader.py` | `Guard`: the single file-open for pool data; refuses and logs (`refusals.log`) `v8-pool/{viirs_decision,final,elmfire_runs}/`, `draw/sealed*`, `draw/sealed_manifest.csv`, `validation-data/sealed2026/` (its own README/_about excepted), the v8 per-jump result files, and `intervals/` of every non-`training` key; only override `--confirmation-commit <sha>` AND the sha on `origin`; count files pass; 37 unit tests (`engine/tests/validation/test_ml_ember_loader.py`) | **done 2026-10-29** |
| `scripts/ml_ember/features.py` | Deterministic builder on the frozen v8 primitives (`detections`, `epochs`, `zone_weather`, `tfc_intensity`, `block_of`, `m8_score`, `b1_score`; sibling functions for the start perimeter / active segments / FBP-with-CFB verified bit-identical to the npz); §4 columns only; control columns kept apart; 12 unit tests (`test_ml_ember_features.py`, synthetic fire + pool check) | **done 2026-10-29** |
| `scripts/ml_ember/build_table.py` | CLI: label identity check (sha256 `5da778ae…`), matching to candidate cells, §3 negative subsample (`S_NEG`), six Parquet tables, `_about.json` with sha256s | **done 2026-10-29** |
| `scripts/ml_ember/ladder.py` | GLM → additive → monotone GBM (LightGBM 4.6) → hybrid with log(M8) offset; nested whole-fire grouped CV; controls | pending |
| `reports/data/ml-ember-cast-2026-10-29/` | Frozen training-split tables (`table/{cells_eval,cells_fit,intervals,fires,coords_control,seam_control}.parquet`, float32, sha256s in `_about.json`), README | **built 2026-10-29** (counts in §7) |
| `docs/PREREG_ML_EMBER.md` (stage 2) | Deciding comparisons, pass rules, seeds, sha256s, blindness statement; owner approval; pushed before any decision fire is scored | pending |

**Data flow.** v8 pool (`~/dev/wildfire/validation-data/v8-pool/`, built by `scripts/validation/v8/{inputs,intervals}.py` on branch `feat/long-range-embers-v8`) → feature builder (training split only until stage 2) → model ladder under nested grouped CV → stage-2 pre-registration → one-shot scoring of the v8 decision fires with PREREG_LR8's metric (within-interval percentile rank, per-fire mean, S3 weights, fire-level sign-flip test) → report, physics memo, draft PR.

**Data sources (all open; nothing requested from any agency).** NASA FIRMS VIIRS 375 m S-NPP + NOAA-20 yearly archives (NASA open data); NBAC 2026-05-13 (OGL-Canada); MTBS 2018–2024 and NIFC WFIGS 2025 (public domain); IEM ASOS/METAR hourly station weather with ERA5 precipitation at the station via Open-Meteo (open; CC BY 4.0 Open-Meteo); CFS 2024 FBP fuel 100 m + SCANFI v2 back-cast (OGL-Canada) in Canada, LANDFIRE FBFM40 pre-fire vintage (public domain) in the US; MODIS MOD13Q1 NDVI via Planetary Computer (open). Sensitivity arm (owner decision D3 pending): ERA5 hourly at the active segments (Open-Meteo archive).

**Compute.** Shared workstation (Ryzen 5 5600GT, 31 GB, GTX 1070 Ti, ~61 GB free disk): every heavy job via `capjob` ≤ 5 GB, started only with ≥ 8 GB available; full test suites one at a time under `flock /tmp/claude-1000/firesim-tests.lock`; at most two heavy agents. Python 3.13 (miniforge), scikit-learn 1.8, XGBoost 3.2, LightGBM 4.6; `pyarrow`, `pygam`, `shap`, `interpret` not installed (use a dedicated conda env; no system installs).

## 3. Decisions log

| Date | Decision | Why |
|---|---|---|
| 2026-10-15 | Owner: ML order = bias-correction, building-loss GBM, fuel classification, **VIIRS spotting statistics last**; pass/fail verdicts stay on simple pre-registered models; "no ML without the owner" | ML is secondary until the fire count grows |
| 2026-10-17 | Owner: open data only; never request anything from Alberta Wildfire or any agency; ELMFIRE is a black box (never read its source) | Independence and licence |
| 2026-10-18 | Owner: PREREG_LR8 frozen (`6452c1d`); OD1–OD12 (Δ +0.08, K_min 150, western Canada + western US 2018–2025, seeded one-third training split, B1 from active segments) | v8 design [R54] §10a |
| 2026-10-24 | Owner: PREREG_LR8 addenda A1–A3; sealed 2026 set C4 (US block now, Canadian NBAC-2026 block later); "scorecard" fires never decide; confirm fires spent once used | [R61] §5; `SEALED_2026.md` |
| 2026-10-25 | Postdoc: proposal written while v8 drawing; nothing run; hold-out B recommended, C before product | Proposal §7.1, §14 |
| 2026-10-25 | Professor: **verdict "proceed with amendments"**; hold-out **B with a two-stage pre-registration, C sealed now for product use**; deciding tests sized at Δ = +0.08 (measured power: B 0.97 / 0.85 at ρ_s 0.8 / 0.6; +0.05 only 0.72 / 0.49); RQ2 split into within-interval (RQ2a) and occurrence (RQ2b); **no CNN rung**; station-only primary weather arm with a disclosed ERA5 sensitivity arm; 17 amendments | Review §0, §3–§9 |
| 2026-10-25 | Professor: two leakage channels flagged and gated by new negative controls: the Canadian fuel composite (SCANFI-typed cells = the fire's final footprint) and M8's β fitted on all training fires (refit per outer fold) | Review §4; `inputs.py` `canada_fuel`, `fit_beta.json` |
| 2026-10-25 ~10:25 | **Owner: D1–D4 approved as recommended** ("Proceed with D1 to D4 according to the plan"): D1 Phase 1 approved, stage-1 pre-registration written now; D2 hold-out B now, C sealed now for product use, Canadian 2026 `confirm` subset earmarked for the ember target L, the 24 unsealed US 2026 records sealed as a scale-only block (seed 2026102403); D3 disclosed ERA5 sensitivity arm; D4 open benchmark planned in principle, product hybrid decided after RQ3 | Review §0 recommendations |
| 2026-10-25 | Professor: stage-1 seeds fixed (`S_NEG 2026102501`, `S_FOLD 2026102502`, `S_MODEL 2026102503`, `S_BOOT_DEV 2026102504`, `S_TEST_DEV 2026102505`, `S_CTRL 2026102506`); negative subsampling 200 per positive interval, 25 % of empty intervals × 50; fuel-seam control pass threshold AUC ≤ 0.55; report number [R68] claimed | PREREG_ML_EMBER_DEV §3, §6, §8, §14 |
| 2026-10-25 | Professor: infrastructure (loader, feature builder, frozen table) waits for v8 to decide, per the brief's Phase 2 gate; only documents are committed before then | Role brief Phase 2 item 4 |

## 4. Methods and references

| Component | Implementation (planned) | Source |
|---|---|---|
| Target and metric | Within-interval percentile rank of the jump cell among candidate cells (ties half), per-fire mean, S3 weights w = K/(1 + 0.3(K − 1)), one-sided fire-level sign-flip test p < 0.025, stratified fire bootstrap 95 % CI | PREREG_LR8 §6–§7 (frozen `6452c1d`); Hanley & McNeil 1982; Obuchowski 1997 |
| Jump rule, intervals, candidate cells, active segments | As frozen in v8; never re-tuned here | PREREG_LR8 §4, §6; A1 |
| Reference models | B1 (Beverly et al. 2021 hazard share × Storey-scaled decay × ±45° downwind); M8 (two-mode kernel: Sardoy 2008 short mode clamped, lognormal long mode median 733 m σ 0.99 cut 15.6 km; establishment logit with β_m fixed from Beverly & Wotton 2007, κ_g = 0, β_w ≥ 0, β_L ≤ 0 fitted by penalised Poisson) | PREREG_LR8 §5, A2.3, A2.6; [R54] §3–§4; [R52] §4 |
| GLM / additive rungs | Penalised logistic or Poisson on physics-named features; spline features with monotone shape where physics is unambiguous | scikit-learn 1.8; Hastie & Tibshirani GAM (standard) |
| GBM rung | LightGBM monotone constraints, depth ≤ 4, `min_data_in_leaf` ≥ 200, ≤ 25 features, seeded within-interval negative subsampling; LambdaRank grouped by interval as alternative | LightGBM 4.6 docs; Farajpoor & Narimani 2026 (GBM no better than logistic once neighbourhoods held out) |
| Hybrid | Poisson boosting or additive model with log(M8 score) as offset (`init_score` / `base_margin`), ≤ 3 interactions | Residual learning on a physics offset (standard); B2 precedent [R65] |
| Validation | Nested whole-fire grouped CV (outer 5 folds stratified region × year), leave-region-out, leave-year-out; negative controls (within-interval label shuffle, weather ±1 day, other-fire features, fold-level permutation, box-coordinate-only model, fuel-seam detector); positive control (planted wind effect, recovered through `fit.py`) | Review §4, §6.3, §7; Southwell 2026 (LOFO 0.42–0.94) |
| Interpretation | ALE, grouped permutation importance; SHAP secondary; findings only when stable across ≥ 4 of 5 folds | Apley & Zhu 2020 (ALE); Lundberg & Lee 2017 (SHAP) |
| Power | Frozen v8 generator (`power_sim.py`) with M8 at AUC 0.63, ρ_s 0.6/0.8, measured rates | Review §5; data folder |

**Deliberate deviations / heuristics / disclosures:**
- Interval weather is observed, not forecast (perfect prognosis), as in M8 and the v8 target.
- Station weather = IEM ASOS/METAR (airports); only 25 % of Canadian training fires are eligible, so the Canadian population is "fires near airports" (PREREG A2.1).
- 400 of 650 training jumps lie on fires without an eligible station (selection; the ERA5 arm tests it).
- Canadian fuel is a composite; the fuel-seam control gates its use.
- M8's κ_g = 0 confounds the wind term with the receptor fuel group (review §6.1).
- No cloud mask; 12 h interval limit; VIIRS-only start perimeters (PREREG A2.7, §4).

### References
- Beverly JL, McLoughlin N, Chapman E (2021) *Landsc. Ecol.* 36:785 (B1 hazard share).
- Beverly JL, Wotton BM (2007) *IJWF* 16:161 (p_e moisture curve; coefficients from FireSim code, paper not in corpus).
- Farajpoor, Narimani (2026) *Front. Sustain.* doi:10.3389/frsus.2026.1975724 (spatial-block vs random CV) [V via R52].
- Hanley JA, McNeil BJ (1982) *Radiology* 143:29; Obuchowski NA (1997) *Stat. Med.* 16:1529.
- Sardoy N et al. (2008) *Combust. Flame* 154:478 (short-mode kernel).
- Schroeder W et al. (2014) *RSE* 143:85 (VIIRS 375 m).
- Southwell C (2026) preprint doi:10.21203/rs.3.rs-10771666/v1 (leave-one-fire-out) [V via R52].
- Storey MA et al. (2020) *Fire* 3:10; Storey MA et al. (2020) *IJWF* 29 doi:10.1071/WF19124 (spot distances; the one held-out empirical spotting model, a GLM).
- Yang G et al. (2021) *J. For. Res.* doi:10.1007/s11676-020-01282-9 (wind needed for firebrand ignition) [V via R52].
- Apley DW, Zhu J (2020) *JRSS-B* 82:1059 (ALE); Lundberg SM, Lee S-I (2017) NeurIPS (SHAP) — standard, unverified here.
- FireSim record: PREREG_LR8 + A1–A3; [R52] *Ember literature ML comparison 2026-10-17*; [R54] *v8 ember study design 2026-10-18*; [R61]; [R65]; `docs/SEALED_2026.md`; `docs/validation.md`.

### Data sources
| Source | Use | Licence |
|---|---|---|
| NASA FIRMS VIIRS 375 m (VNP14IMG, VJ114IMG), yearly archives 2018–2025 | Jumps, perimeters, active segments (via v8 pool) | NASA open data; FIRMS acknowledgement |
| NBAC 2026-05-13; MTBS; NIFC WFIGS 2025 | Pools, final perimeters | OGL-Canada; public domain |
| IEM ASOS/METAR; ERA5 via Open-Meteo | Hourly weather; precipitation (and the sensitivity arm) | Open; CC BY 4.0 (Open-Meteo), Copernicus licence (ERA5) |
| CFS FBP 2024 100 m + SCANFI v2; LANDFIRE FBFM40 | Fuels | OGL-Canada; public domain |
| MODIS MOD13Q1 v061 (Planetary Computer) | Grass curing | Open |

## 5. Testing

| Suite | Command | What it checks | Status |
|---|---|---|---|
| Power simulation | `CAPJOB_MEM=2G capjob python3 -I scripts/power_options.py --sims 1000 --flips 2000` in `reports/data/ml-ember-cast-professor-review-2026-10-25/` | Power of the deciding comparison by hold-out option; false-pass rate at Δ = 0 (0.030, nominal 0.025) | run 2026-10-25, 63 s |
| Loader guard | `flock /tmp/claude-1000/firesim-tests.lock python3 -m pytest engine/tests/validation/test_ml_ember_loader.py -q` (ml-ember env, `PYTHONPATH=engine/src:.`) | Refuses and logs every sealed path of §2 + A1 on a synthetic tree; count files, training paths and sealed2026 README/_about pass; override needs the flag AND a remote sha (mocked); the loader never writes | 37 passed 2026-10-29 |
| Feature contract | `… test_ml_ember_features.py -q` | Every column in the §4 list (hard-coded); features bit-identical when epoch i is perturbed (synthetic fire); FRP from epoch i−1 only; final perimeter enters only `in_final`; `bi`/`bj`/texture absent; sibling FBP and weather equal the frozen primitives; §3 subsample counts, weights and seed; recomputed geometry equals the npz on 5 pool fires | 11 passed, 1 skipped until the tables exist (then 12) 2026-10-29 |
| Negative controls | ladder run with `--control {shuffle,weather_shift,other_fire,fold_perm,box_xy,fuel_seam}` | Skill collapses to 0.50 ± 0.03; fuel-seam detector AUC ≤ 0.55 | pending |
| Positive control | planted β_w = 1 through `fit.py` | Recovery of a known wind effect | pending |

**Oracle / reference provenance:** PREREG_LR8's scorer (`scripts/validation/v8_score.py`, tested against `sklearn.metrics.roc_auc_score` and a sign-flip null calibration) is the metric oracle; the frozen training jump catalogue (`jumps_training.csv`, sha256 `5da778ae…bf54e`) is the label source for development.

**Latest results:** 2026-10-25 — power only (table in the review §5.1); no model results exist.

## 6. Open items
- [x] Owner decisions D1–D4 (review §0): approved 2026-10-25 as recommended.
- [x] v8 reported 2026-10-29 ([R69], PR #114): **PASS** on all four criteria; K 176 (CAN 96 / US 80), 159 ranked jumps in 73 fires; S3(M8 − B1) +0.147 (95 % +0.098 to +0.194); M8 AUC 0.710 / 0.642; B1 0.571 / 0.489; ELMFIRE ≈ M8; M8 skill falls with distance; counts miscalibrated ×0.4 CAN / ×6 US. Read by the professor 02:12 MDT 2026-10-29, after the stage-1 freeze (PREREG_ML_EMBER_DEV A1).
- [x] Stage-1 development pre-registration written and pushed before anyone on the project reads the v8 result (`docs/PREREG_ML_EMBER_DEV.md`, 2026-10-25).
- [x] Phase 2 infrastructure (2026-10-29, data-engineer subagent): `scripts/ml_ember/{paths,loader,features,build_table}.py` with tests, frozen Parquet tables, data folder `reports/data/ml-ember-cast-2026-10-29/`. **Open question for the professor:** `inputs.npz` `elev` is all zero in every v8 fire, so the §4 terrain features (`elev_m`, `slope_pct`, `aspect_deg`, `tpi_1km`) were omitted rather than written as constants; a DEM source would be an addition under §4's rule.
- [x] Parquet writer: dedicated conda env `ml-ember` (conda-forge: python 3.13, pyarrow 25, fastparquet, scipy 1.17.1 and numpy 2.4.2 pinned to the base versions so the FFT hazard share reproduces bit-for-bit).
- [ ] Terrain fill (PREREG A2): `elev_m`, `slope_pct`, `aspect_deg`, `tpi_1km` from Copernicus GLO-90 for every included training fire box; written as `table/terrain.parquet` joined on `row_id`; never carries `bi`/`bj`.
- [ ] Fuel-seam control: if it fails, a box-wide pre-fire SCANFI typing for Canadian fires (feasibility: SCANFI server speed; local Alberta mirror only).
- [ ] Choose the additive-model library (spline logistic in scikit-learn vs an EBM in a dedicated env).
- [x] Report number [R68] claimed in `docs/REPORT_NUMBERS.md` (2026-10-25).
- [x] Seal C done 2026-10-25 on branch `feat/c4-sealed-us-supplement` (**PR #108 merged by owner instruction 12:18 MDT, master `34cb689`**): SEALED_2026 §6a scale-only US block, **18 fires** sealed (seed `2026102403`, list sha256 `ed7cc5110f4c325d1072ae12979161e637b745e3828285f509698034ab465dc1`); §9 claims ledger records the ember-L claim on the Canadian 2026 `confirm` subset.
- [ ] Back up every artifact to this Claude for Science project after each phase; verify the file list.

## 7. Work log

| Date | Commit / file | Summary |
|---|---|---|
| 2026-10-25 02:03 | `reports/ML ember-cast research proposal 2026-10-25.md` | Postdoc proposal (analyst session) |
| 2026-10-25 02:04 | `reports/ML ember-cast professor role brief 2026-10-25.md` | Role brief (analyst session) |
| 2026-10-25 02:15 | `reports/data/ml-ember-cast-professor-review-2026-10-25/scripts/power_options.py` | Power simulation written and run under capjob (seed 20261025) |
| 2026-10-25 02:20 | `reports/ML ember-cast professor review 2026-10-25.md` | Professor's review: verdict, audits, decisions, 17 amendments |
| 2026-10-25 03:51 | data folder `README.md`, `_about.json` | Descriptors with sha256s (delayed by the quota-guard pause 02:16–03:20) |
| 2026-10-25 04:00 | this record; Claude for Science project created | Record and project set up at the owner's request |
| 2026-10-25 10:20 | `v8-pool/draw/{counts.csv,state.json,draw_log.txt}` (counts only) | Phase 1 report delivered to owner in chat; v8 draw re-checked: 150/150 drawn, 116 jumps, stop rule not met; no sealed path opened; awaiting D1–D4 |
| 2026-10-25 ~10:25 | owner message | D1–D4 approved as recommended |
| 2026-10-25 10:45 | `docs/PREREG_ML_EMBER_DEV.md` (this branch) | Stage-1 development pre-registration frozen; v8 undecided and unread |
| 2026-10-25 11:00 | `docs/ML_EMBER_RECORD.md`, `docs/REPORT_NUMBERS.md` | Record moved into the repo; [R68] claimed |
| 2026-10-25 11:30 | `feat/c4-sealed-us-supplement` `df9443f`, `159eeed`, result commit; `validation-data/sealed2026/us/sealed_us_2026_supp.csv` | D2 executed: supplementary scale-only US 2026 block sealed (18 fires), ember claim on the Canadian 2026 `confirm` subset recorded; sealed folder README/_about updated; nothing opened |
| 2026-10-25 12:18 | master `34cb689` (PR #108) | Owner instructed the merge of the sealing PR; registry next-free fixed to [R69]; master record, SEALED_2026 and registry re-attached to Claude for Science |
| 2026-10-29 02:12 | `reports/Long-range embers v8 result 2026-10-29.md` [R69] | v8 PASS read (first reading on this project); PREREG_ML_EMBER_DEV addendum A1 (blindness statement, sealed paths, v8 code merged in from `593e2d1`); Phase 2 gate open |
| 2026-10-29 07:10-07:44 | `scripts/ml_ember/{paths,loader,features,build_table}.py`, `engine/tests/validation/test_ml_ember_{loader,features}.py`, `reports/data/ml-ember-cast-2026-10-29/` | Phase 2 data layer built by the data-engineer subagent (interrupted by the quota guard 02:25-06:50 and again 07:47-12:00 MDT; no report delivered, work verified directly by the professor at 12:15): 49 tests pass; label file sha256 equals the frozen `5da778ae…ff54e`; `fit_beta.json` equals `fda5706c…`; 0 refusals; 165 training fires (CAN 60 / US 105), 55 with jumps, 234 positives (CAN 176 / US 58), 4,656 intervals, 4.19 M evaluation cells, 86,334 fitting rows; recomputed geometry bit-identical to the npz except the ±45° sector flag in one fire (NBAC:2025_430; the tables store the npz values, which §4 names as the source); no NaN beyond the declared FBP-at-no-active-cell cases; the `cfb` overflow warning clips to 1, no Inf. Finding: `inputs.npz` `elev` is all zero in every v8 fire (v8 `inputs.py` writes `np.zeros`), so the four §4 terrain features have no data behind them → PREREG addendum A2 (open DEM fill) |
