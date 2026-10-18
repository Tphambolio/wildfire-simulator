# Pre-registration PREREG_FRONT1: reducing over-growth at active edges (burning period 11–18 h; per-fuel-group ROS factor)

**Registered:** 2026-10-18 on branch `feat/front-overspread`. This file is committed and pushed **before any
held-out fire-day is run or scored with either arm**, and before the C1 baseline is re-run on this branch.

**Approval:** owner-approved backlog items A4, A7 and A8 (2026-10-18). The coordinator approved both arms
and the changes in §5 on 2026-10-18 ("pre-register BOTH arms").

**Basis:** [R60] *Front over-spread at active edges: phase 1 diagnostics* (calibration fire-days only) and
[R58] *Harness start-type strata baseline*.

**Analysis script:** `reports/data/front-overspread-2026-10-18/scripts/prereg_front1_score.py`. It is written
after this file and must implement exactly what is stated here.

## 1. Question

On mature-perimeter Alberta fire-days the C1 harness set-up grows about 2× the observed area at active
edges ([R58]: pooled ratio 1.82, mature-start median 2.22). [R60] located the excess on calibration
fire-days:
- **When:** in the late afternoon and evening. Modelled ÷ observed growth is 1.07 before the VIIRS day pass
  and 2.56 after it.
- **Where:** in conifer and mixedwood, and on days the model runs as crown fire.

**Question:** does either change below bring held-out growth toward observed without an F1 loss larger than
0.03 and without causing widespread under-prediction?
- **Arm A:** a shorter burning period, which is a set-up change in line with decision M3.
- **Arm B:** a rate-of-spread (ROS) factor by fuel group, which M3 allows only by fuel and fire type,
  never as a blanket cut.

## 2. Fire-days, data, split

- **Fire-days:** the 143 fire-days of 32 Alberta CFSDS fires in `$FIRESIM_VALIDATION_DATA/manifest.json`
  (sha256 `b4576b00f824cf59397334e72c26a0814470096e49c25fd157b77ad06b521b76`).
- **Fuel domains:** the pre-fire back-cast domains in
  `$FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains`.
  - sha256 of the sorted `<id>.npz:<sha256>` list: `bd422428d5c83191b3f24cccfa81e4afc5db53cde636ca895fd32fdaa21f2ce8`.
- **Weather:** ERA5 hourly as in [R45] and [R58].
- **Held-out set:** `firesim.validation.report.split_fires`, seed `firesim-skill-2026`.
  - 16 test fires, **79 fire-days**: 2014_350, 2014_461, 2015_452, 2016_146, 2017_204, 2019_148, 2019_177,
    2019_255, 2020_360, 2021_1227, 2021_1296, 2023_365, 2023_689, 2023_696, 2024_113, 2024_152.
  - Only these fire-days are scored for the decision.
- **What the arms were chosen on:** the 64 calibration fire-days (16 fires). They were used for every
  diagnostic and sweep in [R60].
  - No held-out outcome has been split, swept or looked at for either arm.
  - The pooled and per-stratum held-out C1 numbers of [R58] were already published. The arms were not
    chosen from them.

## 3. Configurations

All three runs use `scripts/validate.py run` on this branch with the same domains and these shared options:
`--ignition active --active-days 2 --ffmc-spinup --windows 4 6 8 10 12 14 17 24 --split test`, start hour
06:00, deterministic.

| Run | Name | Differs from C1 by |
|---|---|---|
| **C1 baseline (rerun)** | `c1_bp1020` | nothing: `--burning-period 10 20` |
| **Arm A** | `armA_bp1118` | `--burning-period 11 18` (spread only from 11:00 to 18:00 local clock) |
| **Arm B** | `armB_fuelfactor` | `--burning-period 10 20 --ros-factors C=0.55,M=0.55,O=0.5` |

**What arm B does.** The new option multiplies the head, flank and back FBP ROS of every cell by a factor
chosen by the cell's FBP fuel group:
- C = C-1..C-7: 0.55;
- M = M-1..M-4: 0.55;
- O = O-1a/b: 0.5;
- D and S unchanged.

It acts through the per-cell ROS multiplier path that the WUI modifiers already use, set by
`run_cellular_simulation(..., ros_factor_by_fuel_group=...)` and `RunOptions.ros_factor_by_fuel_group`. The
option is **opt-in and off by default**, so with no factors every result is bit-identical to `master`.

**How the arms were selected (frozen):** from the 12 calibration settings in [R60] §4, take those with
calibration median growth ratio in [0.8, 1.25], then pick the one with the highest calibration mean F1.
- Arm A, burning period 11–18: F1 0.268, median ratio 1.24.
- Arm B, the best ROS-factor setting by the same rule: F1 0.266, median ratio 1.01.
- No other setting is tried before scoring.

**Status after the test.** Arm A is a run setting; the burning period is already a harness, API and UI
option. Arm B stays an opt-in option, off by default, whatever the result.

**Baseline reproduction check.** The rerun C1 baseline must reproduce the held-out records of the [R58] run
file `reports/data/harness-start-strata-2026-10-18/baseline_run_active2_spin_bp1020.jsonl` (sha256
`64a2c3054505e6331464505fe5a2df696b181574bf3c95e026f7892265ac342e`).
- Tolerance: identical F1 and growth ratio at 17 h on all 79 fire-days, within 1e-9.
- If it does not reproduce, the test stops and the discrepancy is reported before any arm is scored.

## 4. Metrics (17 h window, deterministic run)

- **Growth ratio** `g`: modelled ÷ observed growth area beyond the start area (`growth_ratio`, A2).
  - A fire-day with no observed growth beyond the start area has an undefined `g`. It is left out of
    conditions 1, 2 and 4, and the count is reported.
  - A fire-day with zero modelled growth has `g = 0`. It enters logarithms as 0.01.
- **F1:** the growth F1 at 17 h, with NaN counted as 0. All 79 fire-days are used.
- **Bootstrap:** 2,000 resamples of whole held-out fires (16), numpy `default_rng(1)`. All statistics for
  both arms are computed on the same resamples.
  - Paired statistics are fire-day means of the per-fire-day differences, with fire-days weighted equally,
    as in `validate.py compare`.

## 5. Pass rule (per arm; Holm over the two arms)

An arm **passes** only if all four conditions hold.

1. **Growth moves toward observed.**
   - Statistic: d̄ = mean over fire-days of |ln g_arm| − |ln g_C1|.
   - Condition: the upper bound of the bootstrap CI of d̄ is below 0.
2. **Growth on target.** The held-out median of g_arm, a point value, lies in [0.67, 1.5].
3. **F1 non-inferiority.**
   - Statistic: ΔF1 = mean over fire-days of F1_arm − F1_C1.
   - Condition: the lower bound of its bootstrap CI is above **−0.03**.
   - *Why the margin is −0.03:* on the calibration fire-days the paired ΔF1 CIs of the candidate settings
     had a half-width of about 0.02. Arm A: −0.006 [−0.024, +0.009]. Arm B: −0.008 [−0.030, +0.011].
   - So a −0.02 margin would fail most changes on sampling noise alone, even with no true loss. −0.03 still
     rules out losing more than about a tenth of the baseline F1 (0.25).
   - The margin was set before any held-out look.
4. **No widespread under-prediction.**
   - Statistic: Δs = s_arm − s_C1, where s is the share of fire-days with g < 0.5.
   - Condition: the upper bound of the bootstrap CI of Δs is below **+0.10** (10 percentage points).

**Multiplicity (Holm, family α = 0.05 over arms A and B).**
- **CI level for conditions 1, 3 and 4 at Holm step k:** a two-sided (1 − α_k) percentile CI.
  - Step 1: α_1 = 0.025, so a 97.5 % CI.
  - Step 2: α_2 = 0.05, so a 95 % CI.
- **p-value of each arm:** p = 2 × max over conditions 1, 3 and 4 of the one-sided bootstrap tail beyond the
  boundary. The tails are:
  - condition 1: the share of resamples with d̄ ≥ 0;
  - condition 3: the share with ΔF1 ≤ −0.03;
  - condition 4: the share with Δs ≥ +0.10.

  This is an intersection–union test: the arm's p is the largest of its conditions' p-values.
- **Step 1:** the arm with the smaller p is tested at α_1 = 0.025. It passes if p ≤ 0.025 and condition 2
  holds.
- **Step 2:** only if step 1 passed, the other arm is tested at α_2 = 0.05 in the same way.
- **Stopping:** if step 1 fails, both arms fail.
- **Ties:** ties in p are broken in favour of arm A.

**Outcomes.**
- **Fail:** the arm stays opt-in and unmerged. Arm A's burning period is not changed in any default. The
  result is recorded in [R60], PROJECT_RECORD §4.4 and the decision log.
- **Pass:** the arm is offered to the owner, as a new harness default and UI default burning period (A), or
  as a documented model-discrepancy term per fuel group (B). Merging happens only on the owner's decision.

## 6. Reported, not deciding

- Per start-type stratum (point, fresh, mature): ΔF1, median g, share g > 1. Mature is the only stratum
  large enough to read.
- Precision and recall.
- Static shares, observed and modelled.
- ΔF1 and median g at 8 h and 24 h.
- The share of fire-days with g < 0.67.
- **Crown-dominated fire-days.** These are fire-days where more than 50 % of the C1 baseline's modelled
  growth burned as intermittent or continuous crown fire, by the [R60] diagnostic definition. Classification
  uses a `front_overspread_diag.py --split test` run of the baseline only.
  - For these days: median g and ΔF1 per arm.
- The AHWD status split (held vs not held) of the held-out results.
- The pooled growth ratio.
- **Leave-the-largest-fire-out:** the results with the held-out fire with the largest observed growth left
  out. Horse River dominates the calibration growth (80 %); this is the matching check on the held-out side.
- **VIIRS timing for the arms** (`viirs_diurnal_split.py`): the before-pass and after-pass ratios, using
  the arms' diagnostic runs.

## 7. Caveats (stated in advance)

- **Grass.** The 17× grass over-growth on calibration fire-days may come from the grass curing default
  (60 % in the green season) or from mapping rather than front speed. Arm B's grass factor tests it only
  bluntly. A curing check belongs in a separate later test (backlog A9) and is not part of this one.
- **Literature.** The literature digest in [R60] (Prometheus/W.I.S.E. burning conditions, hourly FFMC) came
  from a research sub-agent with page citations. It was not line-checked. It motivates arm A but does not
  enter the decision.
- **Horse River and pooled numbers.** Horse River (2016_255) carries 80 % of the calibration observed growth,
  so calibration pooled numbers are dominated by one fire. The selection rule used per-fire-day medians and
  means to limit this. Held-out results without the largest held-out fire are reported, not deciding.
- **Data limits.**
  - CFSDS growth is daily and kriged.
  - There is one deterministic run per arm.
  - Suppression is not modelled, and the AHWD status is not a held-line measure ([R60] §2).
- **Scope.** The test cannot tell a late-day ROS excess from a weather (ERA5 point) excess. Arm A cuts both;
  arm B cuts only the ROS.

## 8. Compute and order of work

1. Commit and push this file.
2. Implement the arm B option with unit tests (engine and API suites run one at a time under
   `flock /tmp/claude-1000/firesim-tests.lock`).
3. Re-run C1 on the held-out fire-days and run the reproduction check.
4. Run arms A and B on the held-out fire-days once each, under `capjob` with 3 workers (≤ 5 GB).
5. Run the baseline diagnostic for the crown classification.
6. Score with `prereg_front1_score.py`.
7. Report in [R60] with pass or fail as computed. Nothing is re-run or re-tuned after scoring.
