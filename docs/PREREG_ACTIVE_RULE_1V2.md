# PREREG_ACTIVE_RULE_1V2: 1-burn-day vs 2-burn-day active-edge rule on mature-perimeter fire-days ([R63])

- **Registered:** 2026-10-24, on branch `feat/active-rule-1v2` (from `feat/active-segment-restart`
  `87e9989`, draft PR #100). This file is committed and pushed **before any held-out fire-day is run
  with the 1-burn-day rule**. Its commit SHA and sha256 are recorded in the Result section, which is
  appended at the end. The text above the Result section is not edited after registration.
- **Owner approval:** Travis Kennedy, 2026-10-24 (relayed by the coordinator): run a fresh
  pre-registered test of the 1-day vs 2-day active-edge rule from [R62]. The owner also kept the
  app's default hold H = 24 h (recorded in `docs/active-segments.md` and `docs/PROJECT_RECORD.md`).
- **Report:** [R63] `~/dev/wildfire/reports/Active-edge rule 1 vs 2 burn days 2026-10-24.md`;
  data folder `~/dev/wildfire/reports/data/active-rule-1v2-2026-10-24/`.

## 0. Reuse of the held-out set (disclosed)

- **There is no fresh held-out set.** The test set below is the same 70 held-out mature fire-days
  (15 fires) that were already scored once, for [R62] `PREREG_ACTIVE_SEGMENT`, under the **2-burn-day
  rule only** (arms R = C1 and N = H 24 h).
- **What has been seen:** the 2-day arm's held-out results (F1, growth ratio, static share) are
  published in [R62] §8 and its data folder (`runs_heldout/N_hold24.jsonl`). The 2-day arm of this
  test is that same configuration.
- **What has not been seen:** the **1-day rule has never been run or scored on any held-out
  fire-day**. No held-out 1-day run file exists at registration. The [R62] item (c) note that this
  comparison "would need ... a fresh held-out set" is overridden by the owner's approval, with this
  disclosure.
- **Consequence:** knowledge of the 2-day arm could in principle have shaped the metric choice.
  To limit that, the primary metric is the one suggested in the owner-approved brief, and the
  calibration expectation (§6) was computed and written down before this file was pushed.

## 1. Question and hypothesis

On CFSDS Alberta fire-days that start from a **mature perimeter** ([R58] stratum, start age
≥ 2 days), with the held-edge barrier at H = 24 h, does marking start cells active from the
**last 1 burn day** of CFSDS growth (instead of the last 2) bring modelled growth closer to observed
growth, without losing one-day F1 or adding under-prediction?

- **H1:** the mean absolute log growth error, |ln g|, is lower with the 1-day rule.
- **H0:** it is not lower (equal or higher).

## 2. Data, split, set-up (fixed; as [R62] §8)

- **Data:** the 143 record fire-days on the pre-fire back-cast domains
  (`validation-data/backcast/roots/ab2025_backcast/domains`), ERA5 hourly weather.
- **Split:** the fire split of `firesim.validation.report.split_fires`.
- **Test set:** held-out (`--split test`) fire-days with `--start-types mature`: 70 fire-days on
  15 fires ([R58], [R62]). The tag uses CFSDS DOB before the burn day only.
- **Common set-up:** `--ignition active --hold-hours 24 --ffmc-spinup --burning-period 10 20
  --windows 4 6 8 10 12 14 17 24`, barrier one cell wide (default `--hold-band-m`), 3 workers under
  `capjob` (5 G cap), with the code at this file's commit (engine and harness unchanged since
  `5b586d4`, the code of the [R62] runs).
- **Deciding window:** 17 h (06:00–23:00 local scored day; identical to 24 h because spread stops at
  20:00).

## 3. Arms (everything else fixed)

- **A (1-burn-day rule):** `--active-days 1 --hold-hours 24`. A start cell is active if CFSDS shows it
  burning on the burn day before (`day − 1 ≤ DOB < day`), within one cell. Every other start cell is
  a held edge with the 24 h barrier.
- **B (2-burn-day rule, reference):** `--active-days 2 --hold-hours 24` (`day − 2 ≤ DOB < day`).
  This is [R62] arm N.
- Both arms are run fresh. B must reproduce [R62] N (§7 integrity).

## 4. Deciding score (single primary)

- **Growth ratio g:** modelled ÷ observed growth area beyond the start area, at 17 h (the harness
  field `growth_ratio`).
- **Clipped:** gc = min(max(g, 0.05), 20). This keeps fire-days with zero modelled growth (g = 0)
  in the score at a bounded penalty (|ln 0.05| = 3.0) instead of dropping them. Fire-days with no
  observed growth (g NaN; it depends on the observation only, so it is the same in both arms) are
  dropped from both arms.
- **Statistic:** D = mean over fire-days of ( |ln gc_A| − |ln gc_B| ). Negative = the 1-day rule is
  closer to the observed growth.
- **CI:** 95 % percentile bootstrap resampling **whole fires** with replacement, 2,000 resamples,
  `numpy.random.default_rng(seed=1)`, the method of `validate.py _fire_bootstrap` and [R62]
  `score_heldout.py`.
- **Scoring script:** `reports/data/active-rule-1v2-2026-10-24/scripts/score_1v2.py`, frozen at
  registration, sha256 `32bf05ffc3c5f8572ae45007b99d6379233771c96837e6c75428e1cc1128e127`.
- **PASS requires all three:**
  1. **Primary:** the 95 % CI of D lies entirely below 0 (upper bound < 0).
  2. **Guard 1 (F1):** the paired ΔF1 (A − B) at 17 h, mean over fire-days with NaN F1 = 0, has a
     95 % fire-bootstrap **lower** bound ≥ −0.01.
  3. **Guard 2 (under-prediction):** the rise in the share of fire-days with g < 0.5 (A − B; g = 0
     counts as < 0.5) is at most **+10 percentage points** (point estimate, as PREREG_FRONT1 and
     PREREG_ACTIVE_SEGMENT).
- **FAIL:** anything else. If the CI of D lies entirely above 0, the result is reported as
  **worse** (the 1-day rule moves growth further from observed).

## 5. Secondary (reported, not deciding)

- ΔF1 (A − B) at 8 h, 17 h and 24 h with CIs; F1 per arm.
- Mean |ln gc| per arm; median g per arm with CIs; mean Δlog10 g (pairs with g > 0 in both arms).
- Sensitivity of the primary to the zero handling: (i) the [R62] convention (unclipped, pairs with
  g = 0 in either arm dropped); (ii) clip (0.01, 100). These cannot change the verdict.
- Share of fire-days with g < 0.5 and with g > 2 per arm; counts of g = 0.
- Static share of the start perimeter: observed median, modelled median per arm.
- Per-fire mean of |ln gc_A| − |ln gc_B|; number of fire-days whose F1 differs.

## 6. Expectation, stated before the run (opinion, labelled)

The calibration runs of [R62] (56 calibration mature fire-days, 14 fires; `a1_h24` vs `a2_h24`),
scored with the frozen script before registration (`calibration_1v2_descriptive.json` in the data
folder; descriptive, decides nothing):

| 17 h, calibration | A (1 d) | B (2 d) | A − B [95 % CI] |
|---|---|---|---|
| Mean \|ln gc\| (**primary statistic**) | 1.330 | 1.317 | **+0.013 [−0.081, +0.088]** |
| F1 mean | 0.271 | 0.272 | −0.001 [−0.015, +0.011] (guard 1 lower bound −0.015 < −0.01) |
| Median g | 1.55 | 2.14 | Δlog10 −0.120 [−0.175, −0.063] |
| Share g < 0.5 | 17.9 % | 10.7 % | +7.1 pts (guard 2 ≤ +10 met) |
| Share g > 2 | 42.9 % | 51.8 % | |

- The 1-day rule cuts **over**-spread (lower median g, fewer days with g > 2) but adds about as much
  **under**-spread (more days with g < 0.5), so the absolute log error barely moves. On calibration
  the primary would **fail** (CI includes 0) and guard 1 would also fail.
- **A FAIL is therefore the most likely outcome.** The "less over-spread" seen in [R62] is a shift of
  the whole growth distribution down, not a gain in accuracy, on calibration.

## 7. Order and integrity

1. Commit and push this file (step 1). Nothing held-out is run with `--active-days 1` before then.
2. Start each run only when `free -g` shows ≥ 8 GB available; each under `capjob` with a 5 G cap,
   3 workers, one at a time. Run B (2 d) then A (1 d) on the held-out mature fire-days.
3. **Integrity check:** B must reproduce [R62] `runs_heldout/N_hold24.jsonl` (sha256
   `c8aca62d4e61424b0f337ccff7d7677db43c23fb9f25334f365147361fc3cb62`) on all 70 fire-days with
   **0 mismatches**, using [R58] `check_unchanged.py` (every key of the reference record except
   wall-clock timings). If it does not, the test is **void** and A is not scored.
4. Score with the frozen script and append the Result below. Report every arm run, and any rerun or
   interruption.

## 8. What each outcome means

- **PASS:** on held-out mature perimeters (with the disclosed reuse) the 1-burn-day proxy predicts
  growth more accurately without an F1 loss or a large rise in under-prediction. The owner may then
  choose to make 1 day the harness's active-edge proxy for mature restarts (a change to the C1
  reference, which would need its own record entry). Nothing changes automatically, and nothing is
  merged without the owner.
- **FAIL (no detectable difference):** the 2-burn-day rule stays the harness reference for A1;
  the 1-day rule stays an opt-in harness flag (`--active-days 1`) and is not adopted. Less over-spread
  alone is not counted as a gain.
- **FAIL (worse):** as above, and the 1-day rule is recorded as moving growth away from observed.
- **FAIL on a guard only:** as FAIL; the guard is reported as the reason.
- In every case: no rerun with another H, clip, window or set; no re-decision on held-out data.
  The product classification rule (1 km / 12 h from hotspots or VIIRS) is not tested here, because
  CFSDS DOB is daily.

## Result
