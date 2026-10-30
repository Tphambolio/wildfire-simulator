# PREREG_SEALED_CONFIRM_H1H2: two confirmation tests on the sealed 2026 US `confirm` subset ([R67])

- **Registered:** 2026-10-25, branch `feat/sealed-confirm-h1h2` (from `master` `fc3b2ea`). This file, the
  input builder, the run script and the scoring script are committed and pushed **before the sealed list is
  opened and before any model is run on any sealed fire**. The commit SHA and this file's sha256 are recorded
  in the Result section, appended at the end. The text above the Result section is not edited after
  registration (check with `git diff <registration commit> HEAD -- docs/PREREG_SEALED_CONFIRM_H1H2.md`,
  which may only add lines).
- **Owner approval:** Travis Kennedy, 2026-10-25 (relayed by the coordinator): one pre-registration on the
  C4 sealed 2026 `confirm` subset covering H1 (the 1-burn-day active-edge rule, PR #101 [R63]) and H2 (the
  B2 minimum-CRPS ensemble post-processing setting P2, PR #106 [R65], coefficients fixed as fitted on
  Alberta calibration days).
- **Report:** [R67] `~/dev/wildfire/reports/Sealed 2026 confirmation H1 H2 2026-10-25.md`; data folder
  `~/dev/wildfire/reports/data/sealed-confirm-h1h2-2026-10-25/`.

**Code (committed with this file; must not change before the Result section):**

| File | sha256 |
|---|---|
| `scripts/validation/sealed2026/confirm_inputs.py` | `2315e7f29532afb1151c54a33c09306f2c1ff798c529fc05e4db712303a72243` |
| `scripts/validation/sealed2026/run_confirm.sh` | `de71f337385ec0b01a0b7bab13a09ad3d7f95434001c06421e4c3651ac7afedf` |
| `scripts/validation/sealed2026/score_confirm.py` | `059ef355f251843ad7382969c323cba7449109bdc8e44164ad82bfed1cc030e5` |
| `scripts/validation/sealed2026/check_ens_unchanged.py` | `f8310ac8508f562b0cc135421ad10e3c2ecfc6c15b9de727fa0ac9b0fe6b8cc9` |
| `scripts/validation/ens_postprocess.py` | `60a1ebf770c316ddc4f1e3166d0bc781bf8a28c6cafd45b1ea07a52d51ebe0fe` |
| `~/dev/wildfire/reports/data/active-rule-1v2-2026-10-24/scripts/score_1v2.py` ([R63], frozen) | `32bf05ffc3c5f8572ae45007b99d6379233771c96837e6c75428e1cc1128e127` |
| `~/dev/wildfire/reports/data/b2-ensemble-calibration-2026-10-24/fit.json` (B2, frozen) | `a7c393ead090c8202006b6347ed2fa2f7b77a7918935b511fc4527016335fdd3` |

## 0. What this test is, and what it is not

- **A transfer test as well as a confirmation.** H1 and H2 were developed and first tested on Alberta
  fires (CFSDS day-of-burning grids, Canadian FBP fuel grids, Alberta weather). The sealed fires are
  **western-US 2026 fires**. Fuels come from LANDFIRE through a coarse FBP crosswalk, day of burning from
  VIIRS fire objects instead of CFSDS, and weather from ERA5 at a US point. A FAIL can therefore mean
  "the method does not hold" **or** "the method does not transfer to US inputs"; this test cannot separate
  the two. A PASS supports the method under different fuels, data products and climate. The verdicts are
  stated as such.
- **Owner's sealed-set rules apply** (`docs/SEALED_2026.md` §7): the `confirm` orders 1-16 are used once,
  for deciding; afterwards they are spent for deciding. Nothing is run on the `scorecard` subset
  (orders 17-32). The sealed list is opened only by `scripts/validation/sealed2026/confirm_inputs.py`
  (sha256-checked against §6 of SEALED_2026 first), after this file is pushed. Fire-day selection is by
  the rules below, applied blind by that script; no person looks at a sealed fire's hotspots, growth or
  weather before the runs.
- **Perimeters:** the sealed snapshot (`us/sealed_us_2026_perimeters.geojson`, sha256 `7f0c62a2…ab74`), not a
  re-download; for a fire key with several polygons, the largest (`poly_GISAcres`) is used.

## 1. Hypotheses

- **H1 (active-edge rule).** On sealed 2026 US fire-days that start from a mature perimeter (start age
  ≥ 2 days, [R58]), with the held-edge barrier at H = 24 h, marking start cells active from the **last 1
  burn day** (A) instead of the last 2 (B) lowers the mean absolute log growth error |ln g| at 17 h,
  without losing one-day F1 and without adding under-prediction. H0: it does not.
- **H2 (ensemble post-processing P2).** Applying the B2 setting **P2** (member-by-member shift −0.26 log10
  and stretch 1.55 of each fire-day's 20 member log areas, fitted on 61 Alberta calibration fire-days and
  frozen in `fit.json`, sha256 `a7c393ea…35fdd3`) to today's raw 20-member A15 ensemble improves the
  ensemble's 17 h area forecast on sealed 2026 US fire-days, judged as in B2 (coverage of the P10-P90
  range, with CRPS non-inferiority). H0: it does not. **No coefficient is re-fitted.**

## 2. Inputs (built blind by `confirm_inputs.py confirm`; every rule fixed here)

| Input | Source and rule |
|---|---|
| Fires | `confirm` orders 1-16 of `us/sealed_us_2026.csv` (sha256 `b9c7a303…48e80`, checked before reading). Anonymous IDs `2026_C01` … `2026_C16` (= order); names stay in the sealed list |
| Day of burning (DOB) | NASA FEDS NRT "large fire" new-fire pixels (VIIRS 375 m, S-NPP/NOAA-20/NOAA-21; Chen et al. 2022, *Sci. Data* 9:249), open NASA VEDA OGC API collection `public.eis_fire_lf_newfirepix_nrt` (no key). A FEDS step `t` is a local-solar date + 00:00 (night overpass) or 12:00 (day overpass); a pixel's burn day is the date of `t − 12 h` (day pass of day D and the following night pass both → D). Pixels inside the sealed final perimeter buffered 375 m (EPSG:5070), with burn day from discovery − 1 d to end + 1 d (local dates; end = earliest containment/control/out, from the sealed list), any FEDS fire ID. Every grid cell whose centre is inside the final perimeter takes the burn day of the nearest kept pixel (nearest neighbour; Parks 2014, *IJWF* 23:215); other cells 0 |
| Grid | Regular lat/lng, perimeter bounds + 15 km; 90 m, or the first of 180/270/360 m keeping ≤ 1.2 M cells, else 450 m (the `validate.choose_res` rule) |
| Fuel | LANDFIRE **LF2025** FBFM40 CONUS (30 m; public domain; disturbances through 2025 = pre-fire for 2026), LFPS image service, nearest onto the grid; FBP crosswalk of the embers v7/v8 US inputs: grass → O-1b, shrub → C-4, timber/timber-litter/slash-blowdown → C-7, urban (NB1) → O-1b, water → water, other non-burnable → non-fuel; scheme `cfs_national` |
| Terrain | LANDFIRE LF2020 Elev CONUS (30 m), bilinear onto the grid; slope/aspect by the harness |
| Hourly weather | ERA5 at the grid centre (Open-Meteo archive, as the Alberta harness), in the point's local clock time (Open-Meteo `timezone=auto`, IANA zone recorded); burn-day hours and the burning period 10-20 h are local clock hours, as for Alberta |
| Daily FWI codes (start-of-day FFMC/DMC/DC; CFSDS-row stand-in) | ERA5 hourly at the grid centre, local **standard** noon, 24 h rain to noon, `FWICalculator` (Van Wagner & Pickett 1985) from 1 April 2026 (or 30 d before discovery if earlier), start values FFMC 85, DMC 6, DC 15 (standard start-up, v8 addendum A2); DMC/DC day-length factors as in the engine (no latitude adjustment) |
| Grass curing | **Unchanged harness defaults** (O-1 60 % between day 150 and 258, 90 % otherwise): the method as developed in Alberta. A known transfer weakness (western-US summer grass is usually near fully cured); not adjusted, so the methods are tested as they stand |
| Fire-days | `firesim.validation.cfsds.fire_day_pairs` with the harness defaults (burning on day − 1, growth touching the earlier area, ≥ 1 growth cell); **at most 8 per fire**, a seeded sample `random.Random(f"2026102501:{fire_id}")` when a fire has more. A fire with no FEDS pixel inside its perimeter, or no eligible pair, contributes nothing and is listed |

Data root (outside the repository): `$FIRESIM_VALIDATION_DATA/sealed2026-confirm/` (`domains/`,
`manifest.json`, `cfsds/groups/Firegrowth_groups_v1_1_2026.csv` [the FWI rows; the file name is the
harness's], `weather/`, `inputs/<fire_id>.json` with every source, count and hash, `build_summary.json`).

## 3. Runs (`scripts/validation/sealed2026/run_confirm.sh`; each under `capjob` 5 G, 3 workers, one at a time, started only with ≥ 8 GB available)

**Common set-up (as [R63] and B2):** `--ignition active --ffmc-spinup --burning-period 10 20`, start
06:00 local, harness margin 20 km, deterministic member.

| Step | Code | What |
|---|---|---|
| 0a (done before registration, Alberta only) | `12cf308` (the [R63] registered code; engine unchanged to `d0fb5af`), detached worktree `wt-sealed-confirm-h1code` | H1 integrity: arm B on Alberta fire 2019_148's mature days reproduces [R63] `B_2d_h24.jsonl` |
| 0b (done before registration, Alberta only) | this branch (= `master` `fc3b2ea` engine) | H2 integrity: the ensemble on Alberta calibration fire 2022_481 reproduces B2 `phys_a1.5_cal.jsonl` |
| 1 | this branch | build the sealed inputs (§2): opens the sealed list |
| 2 | `12cf308` | **H1:** B = `--active-days 2 --hold-hours 24`, then A = `--active-days 1 --hold-hours 24`; `--start-types mature`; `--windows 4 6 8 10 12 14 17 24` |
| 3 | this branch | **H2:** deterministic C1 `--active-days 2 --windows 8 17` on every selected fire-day (`det_C1.jsonl`); then `--ensemble 20 --ens-seed 1 --ens-params A15` on the fire-days whose deterministic run took **≤ 120 s** (`--max-det-run-s 120 --det-run det_C1.jsonl`; run time only, never outcome; B2 used 40 s on the smaller Alberta domains; arm-B dev fire-days took 4-225 s, under machine load). This cut keeps the ensemble affordable (≤ 21 × 120 s per fire-day) and, as in B2, drops the slowest (mostly largest-growth) fire-days, a disclosed selection |
| 4 | this branch | score (§4, §5) |

A15 = `{"wind_dir_sd_deg":24,"wind_speed_log_sd":0.405,"ffmc_sd":7.2,"dmc_dc_log_sd":0.6,"curing_sd":20.25,"fmc_sd":15,"ros_log_sd":0.825}`.
H1 uses a detached checkout of `12cf308` because the held-edge barrier (`--hold-hours`) is in PRs
#100/#101, not on `master`; H2 needs no barrier (B2's set-up has none). Any interruption is resumed (the
harness skips finished fire-days) and reported; no fire-day is re-run for any other reason.

## 4. H1: deciding score and pass rule (as [R63] §4, unchanged)

- **Scorer:** the frozen [R63] script `~/dev/wildfire/reports/data/active-rule-1v2-2026-10-24/scripts/score_1v2.py`
  (sha256 `32bf05ffc3c5f8572ae45007b99d6379233771c96837e6c75428e1cc1128e127`), run unchanged on A and B by
  `score_confirm.py`.
- **Primary:** growth ratio g (modelled ÷ observed growth beyond the start area, 17 h), clipped
  gc = min(max(g, 0.05), 20); fire-days with no observed growth dropped; D = mean over fire-days of
  |ln gc_A| − |ln gc_B|.
- **PASS requires all three:** (1) the 95 % CI of D lies entirely below 0; (2) guard 1: paired ΔF1 (A − B)
  at 17 h (NaN F1 = 0) has a 95 % lower bound ≥ −0.01; (3) guard 2: the rise in the share of fire-days
  with g < 0.5 (A − B; point estimate) is ≤ +10 percentage points. Otherwise FAIL; a CI of D entirely
  above 0 is reported as **worse**.
- **Secondaries (not deciding):** as [R63] §5 (ΔF1 at 8/17/24 h, medians, Δlog10 g, zero-handling
  sensitivities, shares g < 0.5 and g > 2, static share, per-fire D).

## 5. H2: deciding score and pass rule (B2 §6 with P2 in place of P1)

- **Scorer:** `scripts/validation/ens_postprocess.py score` (sha256 `60a1ebf7…bfe0fe`, unchanged since B2)
  with the frozen B2 `fit.json` (sha256 `a7c393ead090c8202006b6347ed2fa2f7b77a7918935b511fc4527016335fdd3`)
  copied into the output folder and re-hashed; scores at 17 h on log10(area + 10 ha), as
  `summarize_ensemble`.
- **Deciding metric:** held-out P10-P90 coverage of arm **P2**.
- **PASS iff all three:** (1) P2 coverage (point) ≥ 0.70; (2) the 95 % CI of the paired coverage
  difference P2 − B has lower bound > 0; (3) CRPS non-inferiority: mean paired ΔCRPS (P2 − B) ≤ 0 and its
  95 % upper bound ≤ +0.050 log10. Otherwise FAIL. P1 and P0 are reported (they decide nothing; B2's own
  printed "decision" line, which is about P1, is ignored).
- **Secondaries:** spread/skill, ensemble-mean bias, rank histograms, below-P10/above-P90 shares, footprint
  bridge, leave-one-fire-out, CRPS by observed-size tercile.

## 6. Statistics common to both

- **CIs:** 95 % percentile bootstrap resampling **whole fires** with replacement, 2,000 draws,
  `numpy.random.default_rng(1)` (both frozen scorers already do this).
- **Minimum data:** each hypothesis needs **≥ 8 fires and ≥ 30 scored fire-days**; otherwise its verdict
  is **INCONCLUSIVE** (FEDS coverage or fire-day eligibility too thin), and the subset is still spent.
- **Multiplicity:** two separate tests of two different methods feeding two separate owner decisions;
  each is judged at its own 95 % level (one-sided use of a 95 % CI, so ≤ 2.5 % false-PASS rate each
  for the primary condition). No correction and **no joint claim** ("both pass") is made; the chance that
  at least one false PASS occurs is at most about 5 %.

## 7. What was looked at before registration (disclosure)

- **H1:** [R62]/[R63] Alberta results (calibration and held-out, including the fragile PASS and its
  zero-handling sensitivity). **H2:** B2 [R65] Alberta calibration and held-out results. On Alberta
  held-out, **P2 had coverage 0.662 [0.556, 0.769] (below the 0.70 floor), Δcoverage +0.169 [+0.085,
  +0.270], ΔCRPS −0.039 [−0.092, +0.017]**: under the H2 rule P2 would have **failed** condition 1 on
  Alberta. The rule is kept as B2's (owner: "coverage and CRPS as in B2") rather than relaxed after seeing
  that.
- **FEDS time convention:** checked on the 2021 Dixie fire (spent for deciding; not sealed) against FIRMS
  VIIRS acquisition times: FEDS `t` = date + 00:00 matched the ~09-11 UTC night passes of that date and
  date + 12:00 the ~20-22 UTC day passes.
- **Pipeline development on non-sealed 2026 fires** (records removed from the sealing pool by rule 5
  "not complete" or rule 6 "initial point > 2 km", which `docs/SEALED_2026.md` allows to be read; never
  the 24 `rule5_no_end_time` records): 9 fires built by `confirm_inputs.py dev`. Looked at: FEDS pixel
  counts, nearest-pixel distances, fire-day counts, start types, run errors and **run times** of arm B
  (H1 code) on 15 mature fire-days of 2 dev fires (the run was stopped there; 0 errors). Two rule-6 fires had no FEDS pixels inside the perimeter
  (both rule-6 records, i.e. miscoded location or start), so a sealed fire can drop out; this is why §6 has a minimum-data rule.
  The 120 s ensemble cut was set from those run times and the compute budget. **No score (F1, g, CRPS,
  coverage) of any dev fire was computed or looked at**, and no dev fire-day was run with arm A or with
  the ensemble.
- **Sealed fires:** nothing. The list, perimeters, FEDS pixels, fuel, weather and growth of the sealed fires
  have not been opened; `confirm_inputs.py` has never been run in `confirm` mode.
- **Integrity runs 0a/0b** were run before registration on Alberta data only: 0a, code `12cf308`
  reproduces [R63] arm B on 2019_148's 4 mature held-out fire-days, **0 mismatches in 752 window values**;
  0b, this branch's ensemble reproduces B2 `phys_a1.5_cal.jsonl` on 2022_481's 3 calibration fire-days,
  **0 mismatches** (all ensemble scores and member perturbations).

## 8. Expectation, stated before the run (opinion, labelled)

- **H1:** a FAIL is more likely than a PASS. The Alberta held-out PASS was narrow and depended on the clip;
  the Alberta calibration fires pointed the other way; and US inputs (C-4 for every shrub type, cured grass
  at 60 %, FEDS day of burning) add noise that can hide a small effect. If the US fires over-spread a lot
  (likely with C-4 shrub), the 1-day rule's lower growth may help |ln g| on over-spread days, so a PASS is
  possible for the wrong reason; the secondaries (median g, share g > 2) will show that.
- **H2:** a FAIL on condition 1 is the most likely outcome (P2 missed 0.70 on Alberta), and a US bias of a
  different size than Alberta's (the −0.26 shift was fitted to Alberta's over-prediction) could make P2
  worse or better than on Alberta.

## 9. What each outcome means

- **H1 PASS:** the 1-burn-day rule's gain holds on fresh fires under transfer; the owner may make it the
  harness active-edge proxy for mature restarts (separate record entry). **H1 FAIL:** the 2-burn-day rule
  stays; the [R63] PASS is not confirmed (this may be method or transfer; no re-test on these fires).
- **H2 PASS:** P2 applied with Alberta coefficients improves a fresh, out-of-region ensemble; the owner may
  show a P2-calibrated area range (opt-in, wiring is an owner decision). **H2 FAIL:** P2 is not confirmed;
  B2 stays opt-in and unshown by default.
- **INCONCLUSIVE:** too few usable fires/fire-days; the subset is still spent; the record says so.
- In every case: no re-run with other settings, crosswalk, curing, DOB method, cut or window on these
  fires; no re-decision on them; nothing merges without the owner.

## Result

*(appended after the runs; the text above is unchanged)*

- **Registration:** commit `5dfa0a35b33759606dc32680ede7bf2e59989495` (2026-10-25 01:16:53 MDT), pushed
  as the branch's creation at 07:16:55 UTC (01:16:55 MDT, GitHub branch activity); this file's sha256 at
  registration `11ce8189b6263beb73934c8cf739fc696123d85531c0be6d7be4d2ac97b8060c`. The sealed list was first
  opened by step 1 at 07:21 UTC (`build_summary.json`). All seven code/fit sha256s in the table above were
  re-checked unchanged before scoring (2026-10-29 01:58 MDT).
- **Inputs (step 1):** 16 fires opened; 14 contribute fire-days (69). Not contributing: `2026_C11` (no FEDS
  pixel inside the perimeter) and `2026_C09` (pixels but no eligible fire-day pair).
- **Runs:** step 2, B and A, 58 mature fire-days of 10 fires each, 0 errors. Step 3, `det_C1` 69 fire-days,
  0 errors; `ens20_A15` on the 62 fire-days of 14 fires whose deterministic run took ≤ 120 s (7 fire-days
  of `2026_C05`, 160-459 s, dropped by the cut), 0 errors.
- **Interruption (disclosed, §3):** `ens20_A15` stopped after 36 of 62 fire-days with a half-written 37th
  line (`2026_C12` day 207), most likely because the root disk filled (ENOSPC inferred: disk 99 % used,
  traceback truncated). The 36 complete lines were kept, the partial line was removed (copy and sha256
  kept), and `run_confirm.sh 3` resumed at 2026-10-25 22:53 MDT. The harness skipped the 69 finished
  `det_C1` fire-days and ran the 26 remaining ensemble fire-days; it finished at 2026-10-26 06:12. Nothing
  else was re-run. Integrity: the first 36 lines are byte-identical after the resume, and the re-run
  `2026_C12` day 207 record equals the dropped partial line in every byte apart from the timing fields.
  The final file has 62 rows, all unique (fire, day); there was nothing to de-duplicate. A disk guard
  (`scripts/validation/sealed2026/disk_guard.sh`, committed after the runs; it pauses the runs below 10 G
  free and changes no result) ran during the resume and never triggered. Side effect: the resume
  overwrote the original `det_C1.log`; `det_C1.jsonl` is unchanged.
- **H1: FAIL** (10 fires, 49 scored fire-days; minimum data met). D = **−0.127 [−0.349, +0.053]**: the CI
  does not lie below 0, so condition 1 fails. Guard 1 ΔF1 +0.009 [−0.006, +0.023] passes; guard 2 share
  g < 0.5 +4.1 pts passes. Not "worse" (the CI is not above 0).
- **H2: FAIL** (14 fires, 62 fire-days; minimum data met). P2 coverage **0.597 [0.494, 0.717]** < 0.70, so
  condition 1 fails. Δcoverage P2 − B +0.242 [+0.140, +0.387] passes; ΔCRPS −0.207 [−0.257, −0.149] passes
  (non-inferior and better). Raw B coverage 0.355, ensemble-mean bias +1.17 log10.
- Report [R67]: `~/dev/wildfire/reports/Sealed 2026 confirmation H1 H2 2026-10-25.md`; results
  `~/dev/wildfire/reports/data/sealed-confirm-h1h2-2026-10-25/result_confirm_h1h2.json`. Orders 1-16 are
  now spent for deciding.
