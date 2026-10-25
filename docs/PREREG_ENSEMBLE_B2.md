# Pre-registration: ensemble re-calibration with a bias term (backlog B2)

**Registered:** 2026-10-24, committed and pushed on branch `feat/b2-ensemble-calibration` (from
`master` `6dcc41d`, after PR #103) **before the calibration fit was run and before any held-out
fire-day was scored with any post-processing arm**. Owner approval: Travis Kennedy, 2026-10-24
(backlog B2; statistical post-processing of the member cloud's log area, EMOS or quantile-mapping
type, explicitly allowed; no machine learning beyond that).

**Code (committed with this file; must not change before the Result section):**

| File | sha256 |
|---|---|
| `engine/src/firesim/validation/ensemble_calibration.py` (transform, fit rules) | `f53983baa0efc8baf80052a00380b32414f138802e10c801a871a39e13374281` |
| `scripts/validation/ens_postprocess.py` (fit, score, decision) | `60a1ebf770c316ddc4f1e3166d0bc781bf8a28c6cafd45b1ea07a52d51ebe0fe` |
| `engine/src/firesim/validation/ensemble_scores.py` (CRPS, coverage, spread/skill; unchanged from `master`) | `73d7f515503dc567448d1f432130929a7543e90704b22c4652afa213fe554183` |

## 1. Question and hypothesis

[R49] scored the record's 20-member ensemble (A15 sizes, seed 1) on the pre-fire back-cast grid on
65 held-out fire-days: the observed 17 h growth area falls inside the members' P10-P90 range on
**49 %** of days (calibrated: ~80 %), CRPS of log10(area + 10 ha) **0.407**, spread/skill **1.34**,
ensemble-mean bias **+0.22 log10** (a factor of ~1.7 too large), and the rank histogram piles at
rank 0 (every member larger than the observed area on 18 of 65 days). A wider symmetric spread
alone cannot remove a shared bias.

**Hypothesis.** A two-parameter member-by-member (MBM) post-processing of each fire-day's member
log areas, an additive shift (central-tendency / bias correction) plus a constant stretch about
the ensemble mean, fitted on the **calibration** fire-days only, raises held-out P10-P90 coverage
to **≥ 70 %** without making the CRPS worse.

Transform (per fire-day, members i = 1..20, `y_i = log10(area_i + 10 ha)`, `m = mean(y)`):
`y'_i = max(1, m + shift + scale × (y_i − m))`; corrected area `10^y'_i − 10 ha`. Shift and
stretch are the "perturbation size" and "bias" of B2 expressed in the scored variable: the
physical perturbation sizes (A15) and the engine are unchanged. Basis: Van Schaeybroeck &
Vannitsem (2015) MBM (shift and stretch fitted by minimum CRPS); Gneiting et al. (2005) EMOS
(minimum-CRPS estimation); Gneiting & Raftery (2007) CRPS.

Why not a physical correction as the deciding arm: the per-fuel-group ROS factor (A4, [R60],
PREREG_FRONT1) already failed its held-out single-run test and is on an unmerged branch (#96);
an inflation of x 2 on the physical sizes was rejected in the 2026-10-08 calibration (run time,
ROS log-sd 1.1 not physically defensible). A post-processing arm needs no new held-out runs and
leaves every existing output unchanged unless a caller applies it.

## 2. Data

- **Grid:** the Alberta Wildfire FBP 2025 grid back-cast to each fire's start ([R44]),
  `$FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains` (domain set sha256
  `bd422428d5c83191b3f24cccfa81e4afc5db53cde636ca895fd32fdaa21f2ce8`, as in
  PREREG_RESCORE_BACKCAST §3). This is the grid FireSim uses for historical Alberta replays and
  the grid of the current record statement ([R49]).
- **Manifest:** `manifest.json` sha256 `b4576b00…b521b76`; split `report.split_fires` (seed
  `firesim-skill-2026`), 16 calibration and 16 test fires, unchanged since 2026-10-07.
- **Ensemble:** C1 set-up (`--ignition active --active-days 2 --ffmc-spinup --burning-period 10
  20`), 20 members, A15 sizes `{"wind_dir_sd_deg":24,"wind_speed_log_sd":0.405,"ffmc_sd":7.2,
  "dmc_dc_log_sd":0.6,"curing_sd":20.25,"fmc_sd":15,"ros_log_sd":0.825}`, seed 1, windows 8 and 17 h.
  Scored at **17 h** only.

### 2.1 Calibration set (fitting only)

The 61 calibration fire-days of 16 fires whose deterministic 2014b run in
`skill/runs/active2_spin_bp1020.jsonl` (sha256 `b3becf98…a9f6`) took ≤ 20 s: the record's
2026-10-08 calibration selection (run time only, never outcome; the three dropped are 2016_255
days 126-128). Run on the back-cast grid on this branch's code by
`reports/data/b2-ensemble-calibration-2026-10-24/scripts/run_cal.sh` →
`$FIRESIM_VALIDATION_DATA/b2-ensemble/backcast/runs/phys_a1.5_cal.jsonl`. This run was started
on 2026-10-24 13:02 MDT, before this file was pushed; it is calibration data only. Its sha256 is
recorded in `fit.json` and in the Result.

### 2.2 Held-out set (fixed)

The **65 held-out fire-days of 16 test fires** of [R49]: the test fires restricted to fire-days
whose deterministic 2014b run took ≤ 40 s (run time only). Member areas come from the existing
[R49] back-cast run file `$FIRESIM_VALIDATION_DATA/rescore-backcast/backcast/runs/phys_a1.5.jsonl`,
**sha256 `b716bd19dd04fec9021b0aa0a55e97db850408f39ac4367b2457655b55e8f351`**, unchanged. Fires
(days): 2014_350 (1), 2014_461 (2), 2015_452 (4), 2016_146 (6), 2017_204 (4), 2019_148 (5),
2019_177 (1), 2019_255 (5), 2020_360 (6), 2021_1227 (5), 2021_1296 (2), 2023_365 (5), 2023_689
(6), 2023_696 (6), 2024_113 (1), 2024_152 (6). No new held-out simulation is run.

**Code consistency.** The held-out file was run at `61dafdd`; the calibration run uses `6dcc41d`.
The only engine change between them that touches the run is E10 (#103, [R64]), reported
bit-identical. Check (calibration data only): each calibration fire-day's 17 h deterministic score
inside the new ensemble rows is compared with the back-cast C1 run
`rescore-backcast/backcast/runs/active2_spin_bp1020.jsonl` (code `a5da766`, identical engine to
`61dafdd`); any difference is reported.

### 2.3 Prior use of the held-out set (disclosure)

- The 65 held-out fire-days and this exact member file were scored in [R49] (2026-10-17):
  coverage, CRPS, spread/skill, bias, rank histogram, P10 recall. The same days on 2014b were
  scored in 2026-10-08 (record third round) and [R49]. The 79-day held-out set (single run) was
  also used by [R45], [R58] and [R60] (PREREG_FRONT1).
- **The B2 design (a shift plus a stretch) was chosen knowing [R49]'s held-out diagnosis** (bias
  to large areas and too little spread). That diagnosis was also visible on the calibration
  fires in 2026-10-08 (2014b: coverage 54 %, below P10 33 %). No held-out value enters the fit;
  the grids, the fit rules and the decision rule below are fixed here.
- Before this file was written, the fit code was smoke-tested on the **calibration** rows of the
  record's 2014b run (`enscal/runs/phys_a1.5.jsonl`, old code, 61 days, in-sample only). No
  held-out row was read by the new code.

## 3. Arms

| Arm | Shift | Stretch (scale) | Role |
|---|---|---|---|
| **B** baseline | 0 | 1 | today's ensemble (identity; [R49] numbers) |
| **P1** coverage-targeted MBM | CRPS-optimal at the chosen scale | smallest scale on the grid reaching **≥ 80 %** calibration coverage | **deciding** |
| P2 minimum-CRPS MBM | joint minimum of mean calibration CRPS | joint minimum | descriptive |
| P0 bias only | CRPS-optimal at scale 1 | 1 | descriptive (how much the bias alone does) |

## 4. Fit (calibration fire-days only; `ens_postprocess.py fit`)

- Grids: shift −1.50 to +0.50 step 0.01 (log10 units); scale 0.50 to 4.00 step 0.05.
- For a given scale, the shift is the grid value with the lowest mean calibration CRPS (first
  minimum on ties).
- P1: scales are tried in increasing order; the first whose calibration coverage (observation
  within `np.percentile(y', 10)` and `np.percentile(y', 90)`, inclusive, as `summarize_ensemble`)
  is ≥ 0.80 is taken, with its shift. If no scale reaches 0.80, the largest scale is used and the
  fit is reported as not reaching the target (the held-out test still runs).
- P2: the (shift, scale) pair with the lowest mean calibration CRPS over the whole grid.
- Leave-one-fire-out on the calibration fires is reported (descriptive).
- `fit.json` is written and **frozen** before `score` reads any held-out row.

## 5. Scores (held-out; `ens_postprocess.py score`)

All at 17 h on log10(area + 10 ha), per fire-day, identical to
`firesim.validation.ensemble_scores.summarize_ensemble` (verified by unit test): coverage of the
P10-P90 range; CRPS of the 20 corrected members (empirical, all N² pairs); rank; below P10 / above
P90; spread/skill (Fortin et al. 2014 eq. 15) and ensemble-mean bias. **CIs:** 2,000 whole-fire
bootstrap draws, seed 1, percentile 95 % (the [R49] convention); paired differences arm − B per
fire-day.

## 6. Single deciding metric and pass rule

**Deciding metric: held-out P10-P90 coverage of arm P1.**

**PASS** if and only if all three hold:

1. P1 held-out coverage (point estimate) **≥ 0.70**;
2. the 95 % CI of the paired coverage difference P1 − B has its **lower bound > 0**;
3. **CRPS guard (non-inferiority):** the mean paired ΔCRPS (P1 − B) is **≤ 0** and its 95 %
   upper bound is **≤ +0.050** log10 units (about 12 % of today's 0.407; about the half-width of
   [R49]'s paired CRPS CI, so a truly unchanged arm is not failed by noise alone).

Otherwise **FAIL**. P2 and P0 decide nothing; their scores are reported with CIs.

## 7. What each outcome means

- **PASS:** a calibrated area range can be shown with coverage near the target on unseen fires
  without losing CRPS. The correction stays **opt-in**: `firesim.validation.ensemble_calibration`
  changes nothing unless called. Wiring it into the API / ensemble card / 209 (e.g. a
  "calibrated area range" beside the raw members, and the member-count threshold whose footprint
  matches the corrected P10 area) and making it the default are **owner decisions**, proposed in
  the report, not made here. The record's ensemble statement for historical replays gains the
  corrected numbers beside the raw ones.
- **FAIL:** the correction stays opt-in and the PR stays a draft, unmerged; the record keeps [R49]'s
  statement. The report says which condition failed.
- Either way: the parameters are valid only for this set-up (back-cast grid, ERA5, active-edge
  start, burning period 10-20 h, 17 h window, A15 sizes). A forecast-driven or different-window
  run needs its own fit.

## 8. Descriptive extras (decide nothing)

Rank histograms; below/above split; spread/skill with whole-fire CI (B, P1); leave-one-fire-out
of the deciding numbers; P1 coverage by observed-size tercile; and a footprint bridge: for each
arm, the member-count threshold whose burn-probability footprint area is closest to the
corrected 90th-percentile area (FireSim's "P10" end), with the share of days that footprint holds
≥ 90 % of the observed growth (compare [R49]: 28 %).

## 9. Limits fixed in advance

- 65 held-out fire-days of 16 fires, already used ([R49]); fire-days are correlated within a fire.
- The 14 largest held-out fire-days are excluded (run-time cut), so the largest runs are untested.
- A shift and stretch of the area distribution do not move any simulated perimeter; footprints
  are the simulated ones, only their labels or threshold change.

## Result

*(appended after the held-out scoring; the text above is unchanged)*

**Registration:** commit `b6662c82e05ddb19c8a9aec4fd40a7f71cf56628` (tag `prereg-ensemble-b2-2026-10-24`),
pushed 2026-10-24 13:13 MDT; sha256 of this file as registered (everything above this section)
`683713daf4934236cf8320277e9394cd5e967d8607dfe6c766b6a88cce839f7b`. Code at scoring time identical
to the registered commit (`git diff b6662c8 -- engine/src scripts` empty). Report: [R65].

**Calibration run** (`phys_a1.5_cal.jsonl`, sha256 `8c4f020c…bfc75b`): 61 of 61 fire-days, 0
errors, 20 members each, A15 config in every row; member perturbations identical to the record's
2014b calibration run (61 of 61); deterministic 17 h scores bit-identical to the back-cast C1 run
at `a5da766` (61 of 61), so the E10 engine change did not alter results.

**Fit (calibration only, `fit.json` sha256 `a7c393ea…35fdd3`, frozen 14:00 MDT before scoring):**

| Arm | Shift (log10) | Scale | Cal. coverage | Cal. CRPS | LOFO coverage / CRPS |
|---|---|---|---|---|---|
| B | 0 | 1 | 0.574 | 0.422 | 0.574 / 0.422 |
| **P1** | **−0.25** (areas × 0.56) | **2.25** | 0.803 | 0.377 | 0.803 / 0.386 |
| P2 | −0.26 | 1.55 | 0.754 | 0.370 | 0.738 / 0.380 |
| P0 | −0.25 | 1 | 0.574 | 0.381 | 0.574 / 0.392 |

**Held-out (65 fire-days, 16 fires; 2,000 whole-fire draws, seed 1):**

| Arm | Coverage [95 % CI] | CRPS [CI] | ΔCRPS vs B [CI] | Δcoverage vs B [CI] | Spread/skill |
|---|---|---|---|---|---|
| B | 0.492 [0.380, 0.597] | 0.407 [0.329, 0.506] | – | – | 1.34 [1.13, 1.59] |
| **P1** | **0.846 [0.741, 0.937]** | 0.376 [0.310, 0.448] | **−0.031 [−0.092, +0.032]** | **+0.354 [+0.231, +0.483]** | 0.55 [0.46, 0.66] |
| P2 | 0.662 [0.556, 0.769] | 0.368 [0.294, 0.445] | −0.039 [−0.092, +0.017] | +0.169 [+0.085, +0.270] | 0.80 |
| P0 | 0.508 [0.388, 0.623] | 0.379 [0.298, 0.464] | −0.029 [−0.070, +0.020] | +0.015 [−0.079, +0.117] | 1.23 |

B reproduces [R49] exactly (0.492, 0.407, 1.34).

**Decision (§6): PASS.** (1) P1 coverage 0.846 ≥ 0.70; (2) Δcoverage lower bound +0.231 > 0;
(3) mean ΔCRPS −0.031 ≤ 0 and upper bound +0.032 ≤ +0.050.

**Reading (descriptive, decides nothing):** the bias term removes the shared over-prediction
(ensemble-mean bias +0.22 → +0.02 log10; rank-0 days 18 → 1), but P1 is now **over-dispersed**
(spread/skill 0.55; held-out coverage 85 % above the 80 % target; median P10-P90 range a factor
of ~60 in area vs ~6.5 today). The CRPS gain is not significant on its own (CI includes 0); P1 is
worse than B on 38 of 65 days and on the largest-growth tercile (+0.10). The P10 footprint does
not improve: mapped to the corrected area it holds ≥ 90 % of growth on 29 % of days (B 25 %,
native 28 %). Outcome per §7: the correction stays **opt-in**; wiring and any default are owner
decisions ([R65] §6).
