# PREREG_ACTIVE_SEGMENT: held-edge barrier on mature-perimeter fire-days (A1, [R62])

- **Registered:** 2026-10-24, on branch `feat/active-segment-restart`. This file was pushed
  **before any held-out fire-day was run with a hold**. Its sha256 is recorded in [R62] and in
  the commit that follows it. Results are appended at the end; the text above them is not edited.
- **Owner decisions** (Travis Kennedy, 2026-10-24, relayed by the coordinator: "go with your
  recommendations"): the active rule, how H is chosen, the deciding comparison and the UI labels,
  as set out below.
- **What already exists** (descriptive only, decides nothing): the calibration runs in [R62] §4
  (56 calibration mature fire-days, 14 fires). They are the only runs used to choose H.

## 1. Question

On CFSDS Alberta fire-days that start from a **mature perimeter** (the [R58] stratum, start age
≥ 2 days), does adding the held-edge barrier (backlog A1, `docs/active-segments.md`) raise
one-day F1 over today's active-edge set-up?

## 2. Data, split, set-up (fixed; as [R45] / [R58])

- **Data:** the 143 record fire-days on the pre-fire back-cast domains
  (`validation-data/backcast/roots/ab2025_backcast/domains`), with ERA5 hourly weather.
- **Split:** the fire split of `firesim.validation.report.split_fires`.
- **Test set:** the **held-out** fire-days with `start_type == "mature"`. [R58] counts 70 fire-days
  on 15 fires. The tag uses CFSDS DOB before the burn day only (`--split test --start-types
  mature`).
- **Common set-up:** `--ignition active --active-days 2 --ffmc-spinup --burning-period 10 20
  --windows 4 6 8 10 12 14 17 24`, run with the code at this file's commit.

## 3. Arms

- **Active rule (owner decision 1):** a start cell is active if CFSDS shows it burning on the
  **2 burn days before the burn day** (`day − 2 ≤ DOB < day`), within one cell. Every other start
  cell is a held edge.
- **Reference R:** today's active-edge set-up, C1 (`--active-days 2`, hold unset). Held cells
  are burned out for the run, with no barrier.
- **New rule N:** active edges plus the held-edge barrier, `--active-days 2 --hold-hours 24`,
  barrier one cell wide (default). Held cells and the ring of fuel next to them, except next to an
  active cell, cannot burn for H hours, and are then released.
- **Choice of H (owner decision 2), on calibration fire-days only.**
  - Candidates were {0, 12, 24} h.
  - The criterion was the best paired mean ΔF1 at 17 h vs C1. The tie-breaks, in order, were the
    PREREG_FRONT1 guards: a lower mean Δ|ln g| (growth ratio g), then a smaller rise in the share
    of days with g < 0.5.
  - Calibration numbers (56 fire-days, 14 fires; 95 % fire bootstrap, 2,000 resamples, seed 1;
    `choose_h.json` in the [R62] data folder):

    | H | ΔF1 17 h vs C1 | mean Δ\|ln g\| | Δ share g < 0.5 |
    |---|---|---|---|
    | 0 (all active) | −0.026 [−0.054, −0.007] | +0.417 [+0.175, +0.716] | −3.6 pts [−8.6, 0.0] |
    | 12 | −0.016 [−0.043, +0.001] | +0.233 [+0.084, +0.416] | −1.8 pts [−6.0, 0.0] |
    | **24** | **−0.001 [−0.003, +0.001]** | −0.028 [−0.047, −0.013] | 0.0 pts [0.0, 0.0] |

  - **Chosen: H = 24 h**, which has the best ΔF1, so no tie-break was needed. Within the
    06:00–23:00 scored day, H = 24 is never released, so N differs from R only by the barrier ring.
  - The product default stays 24 h, with a range of 0–72 h.
- **Expectation, stated before the run (opinion):** calibration showed no detectable F1 change
  (−0.001 [−0.003, +0.001]), so a **fail (CI including 0)** is the most likely outcome.

## 4. Deciding score (owner decision 3)

- **Statistic:** paired ΔF1 (N − R) at the **17 h** window, the mean over the held-out mature
  fire-days. A NaN F1 counts as 0.
- **CI:** 95 % fire bootstrap, 2,000 resamples of whole fires, seed 1 (the method of
  `validate.py compare --ref` and `_fire_bootstrap`).
- **PASS** requires both:
  1. the CI excludes 0 and the mean is above 0; and
  2. the under-prediction guard holds: the rise in the share of fire-days with growth ratio
     g < 0.5 (N minus R) is at most **+10 percentage points** (the point estimate, as in
     PREREG_FRONT1 condition 4).
- **FAIL:** anything else. If the ΔF1 CI is entirely below 0, the result is reported as **worse**.

## 5. Secondary (reported, not deciding)

- Growth ratio: the median with its CI, and the mean paired Δlog10 and Δ|ln g|.
- Static share of the start perimeter: observed against modelled (medians), and the mean of
  observed minus modelled with its CI.
- ΔF1 at 8 h and at 24 h.
- The share of days with g < 0.5, with its CI.
- Calibration fire-days and the point and fresh strata, reported descriptively.

## 6. If it fails

- The held-edge barrier stays **opt-in and labelled "not validated"** in the product.
- The API default is unchanged: an unset `hold_hours` means no barrier.
- The app's 24 h default (with marked or classified edges) is the owner's to keep or drop.
- Nothing is merged without the owner. A failure is not re-run with another H, and is not
  re-decided on held-out data.

## 7. Order and integrity

1. Commit and push this file, then record its sha256 in [R62].
2. Run R and N once each on the held-out mature fire-days, under `capjob` with 3 workers
   (≤ 5 GB).
3. Integrity check: R must reproduce the [R58] C1 run file on those fire-days with 0 mismatches.
   If it does not, the test is **void**.
4. Score the result and append it below. Report every arm that was run.

## Result

**Appended 2026-10-24, after the held-out run.** The text above is unchanged from the registered file
(sha256 `5e9c50df78699dfa95835a7aec6989243178c5461c0477a00f5d87a7e27bcd35`, commit `2668550`).

- **Runs:** R (C1) and N (hold 24 h) on the held-out mature fire-days: 70 fire-days, 15 fires,
  0 errors. Run under `capjob` with 3 workers. N was interrupted once at 59 of 70 and resumed
  (the harness is resumable per fire-day).
- **Integrity (§7.3):** R reproduces the [R58] C1 run file on these 70 fire-days with 0 mismatches
  in 13,160 window values. **The test is valid.**
- **Primary (17 h):** ΔF1 (N − R) = **−0.002 [−0.005, +0.001]** (F1 0.231 → 0.229). The CI includes
  0, so condition 1 is not met.
- **Under-prediction guard:** Δ share of fire-days with g < 0.5 = +1.4 points [0.0, +4.5]
  (14.3 % → 15.7 %). The guard is met.
- **Verdict: FAIL.** No detectable difference: the CI includes 0, and the result is not "worse"
  because the CI does not lie entirely below 0. As registered, the held-edge barrier **stays
  opt-in and labelled "not validated"**. The API default (an unset hold) is unchanged.
- **Secondary (17 h):**
  - Growth ratio median 2.20 → 2.17; mean Δlog10 g −0.028 [−0.040, −0.018]: about 6 % less
    growth.
  - Mean Δ|ln g| −0.033 [−0.068, +0.001].
  - Static share: observed median 0.993; modelled median 0.979 in both arms. Mean observed −
    modelled for N is +0.027 [+0.001, +0.058].
  - ΔF1 at 8 h −0.002 [−0.005, +0.000]. 24 h is the same as 17 h, because spread stops at 20:00.
  - F1 differs from R on 55 of the 70 fire-days, by small amounts.
- **Reading (opinion, labelled):** as the calibration runs predicted, the barrier changes almost
  nothing on CFSDS mature perimeters. C1 already holds the old perimeter for the run, so the only
  extra effect is a one-cell ring, which trims growth slightly without changing F1.
- **Files:** `result_prereg_active_segment.json`, `integrity_R_vs_R58_C1.json` and
  `runs_heldout/` in the [R62] data folder.
