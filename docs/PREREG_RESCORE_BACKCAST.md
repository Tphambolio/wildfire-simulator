# Pre-registration: headline spread validation rescored on the pre-fire (back-cast) fuel grid

**Registered:** 2026-10-15, committed and pushed on branch `feat/rescore-backcast` (from `master`
`f4cdd17`, after PRs #79 and #80) **before any back-cast run of the configurations below was
scored**, except the primary configuration, which PR #79 already scored (disclosed in §8).
Owner approval: Travis Kennedy, 2026-10-15 ("rescore validation after 79").

**Analysis script:** `rescore.py` in
`~/dev/wildfire/reports/data/validation-rescore-backcast-2026-10-15/scripts/` (written after this
file; it must implement exactly what is stated here). Runs use `scripts/validate.py` with the new
`--domains-dir` option (this branch) and `scripts/validation/wise_fireday.py`.

## 1. Question

The validation record (`docs/validation.md`, `docs/PROJECT_RECORD.md` §4.4, `docs/model-card.md`)
reports FireSim's spread skill on the CFS national FBP grid 2014b (250 m, internal-use licence,
not shipped). For a dated historical run in Alberta, FireSim itself now uses the Alberta Wildfire
2025 grid back-cast to the scenario date (PR #79: NBAC burns on or after the date restored from
SCANFI v2, CFS 2018 national rules). **How do the record's headline skill numbers and
conclusions change when the same fire-days are scored on the back-cast grid instead of 2014b?**

## 2. Fire-days and split

- The 143 fire-days of 32 Alberta CFSDS fires (2014-2024) in `manifest.json` of
  `$FIRESIM_VALIDATION_DATA` (sha256 `b4576b00f824cf59397334e72c26a0814470096e49c25fd157b77ad06b521b76`),
  unchanged: same DOB, DEM, ERA5 hourly weather, CFSDS codes, working areas.
- Held-out split as in the record: `firesim.validation.report.split_fires`, seed
  `firesim-skill-2026`: 16 test fires, 79 fire-days (2014_350, 2014_461, 2015_452, 2016_146,
  2017_204, 2019_148, 2019_177, 2019_255, 2020_360, 2021_1227, 2021_1296, 2023_365, 2023_689,
  2023_696, 2024_113, 2024_152); 16 calibration fires, 64 fire-days.
- **Pre-2011 fire-days:** none in this set (all fires 2014-2024); every fire has an epoch
  (2010 for 2014-2015, 2015 for 2016-2020, 2020 for 2021-2024). Any fire-day the back-cast could
  not be built for would be reported separately as "not back-cast" and kept in the 2014b-only
  numbers, never dropped silently. Expected count: 0 of 143.

## 3. Grids (the only thing that differs between the two arms)

| Arm | Domains | sha256 of the 32 domain files (`<id>.npz:<sha256>\n`, sorted by id) |
|---|---|---|
| **2014b** (current record) | `$FIRESIM_VALIDATION_DATA/domains` (built by `validate.py prepare`) | `ad8e039ed1bce9489c08bdacf36d0aaa4545964784a9fe556340266c284e2e16` |
| **Back-cast** | `$FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains` (built for #79 by `backcast_fire_test.py domains ab2025_backcast`; scenario date = the fire's first CFSDS burn day) | `bd422428d5c83191b3f24cccfa81e4afc5db53cde636ca895fd32fdaa21f2ce8` |

Both arms are **rerun on this branch's code** (same commit for both), so code changes since the
recorded numbers (grid diagonal fix PR #35, etc.) cannot appear as a grid effect. The record's
published values are shown beside them for reference only.

Harness conventions unchanged: D-1/M-1 → D-2/M-2 for day of year 150-257, M-1/M-2 at 50 %
conifer, harness default curing (90 % outside the green season, 60 % inside; this reproduces the
recorded 0.212), start 06:00, deterministic, spotting off, no suppression.

## 4. Configurations

| ID | `validate.py run` options | Fire-days | Record value on 2014b |
|---|---|---|---|
| **C1 (PRIMARY) `active2_spin_bp1020`** chosen set-up | `--ignition active --active-days 2 --ffmc-spinup --burning-period 10 20 --windows 4 6 8 10 12 14 17 24` | all 143 | held-out 17 h F1 0.212 (after the diagonal fix); area diff +0.27; over-predicted 75 % |
| C2 `perimeter_base` first round, whole perimeter, 06-23 h | `--ignition perimeter --windows 4 6 8 10 12 14 17 24` | all 143 | all: F1 0.15, over-predicted 93 %, area diff +0.67; held-out 0.118 |
| C3 `bennett_spin` oracle start + spin-up | `--ignition bennett --ffmc-spinup --windows 4 6 8 10 12 14 17 24` | all 143 | held-out 0.250 (after the fix) |
| C4 `bennett_start06` Bennett start, first round (the WISE-comparable 0.24) | `--ignition bennett --windows 8 17 24` | all 143 | all: 0.24 |
| A1 `perimeter_spin` | `--ignition perimeter --ffmc-spinup` | held-out 79 | 0.125 |
| A2 `perimeter_spin_bp1020` | `--ignition perimeter --ffmc-spinup --burning-period 10 20` | held-out 79 | 0.146 |
| A3 `active1` | `--ignition active --active-days 1` | held-out 79 | 0.154 |
| A4 `active2_spin` | `--ignition active --active-days 2 --ffmc-spinup` | held-out 79 | 0.164 |
| E1 ensemble `phys_a1.5` | C1 + `--ensemble 20 --ens-seed 1` with the record's A15 sizes (`wind_dir_sd_deg` 24, `wind_speed_log_sd` 0.405, `ffmc_sd` 7.2, `dmc_dc_log_sd` 0.6, `curing_sd` 20.25, `fmc_sd` 15, `ros_log_sd` 0.825), `--split test --max-det-run-s 40 --det-run skill/runs/active2_spin_bp1020.jsonl` (sha256 `b3becf98…a9f6`) | the record's 65 held-out fire-days (selected by the 2014b deterministic run time only, the same 65 for both arms) | coverage 49 %, CRPS 0.471, CRPSS +0.24, spread/skill 1.58, P10 ≥ 90 % on 29 % (pre-fix code) |
| W1 W.I.S.E. on identical inputs | `wise_fireday.py` (perimeter start, 06-23 h, no spotting) on the back-cast domains | the record's 9 fire-days: 2014_461:190, 2019_177:168, 2015_403:186, 2023_696:186, 2024_126:192, 2018_1077:221, 2021_1227:191, 2022_433:155, 2016_255:125 | WISE 0.19 vs FireSim 0.24 (17 h F1 means) |

A1-A4 cover the held-out ablation table (`docs/validation.md`, "Skill improvements"). Window
list differences between configurations do not affect the 17 h scores. For W1 the 2014b WISE
side is the record's existing `runs_wise/wise_vs_firesim.jsonl` (WISE 1.0.6-beta.6 is
deterministic and unchanged); the FireSim side of both arms is C2 at 17 h.

If run time forces a cut, the order of priority is C1, C2, C3, A1-A4, C4, W1, E1; anything not
completed is reported as not completed, never estimated.

## 5. Metrics

Per fire-day, at 17 h (06-23 h, Bennett's Scenario 1) unless stated, with the harness's metrics
(`firesim.validation.metrics`) on the day's growth: F1 (no predicted growth → F1 = 0, as in
`validate.py compare`), precision, recall, IoU, normalised area difference
(A_pred − A_obs)/(A_pred + A_obs), over-predicted day (area difference > 0), forward spread ratio
within ±35 %, absolute head-bearing error; also 8 h and best-hour F1. Ensemble (E1): observed
area inside the members' P10-P90, CRPS of log10 area, CRPS skill vs the deterministic run,
spread/skill, P10 footprint holding ≥ 90 % of the observed growth. Per-fuel strata: the
dominant fuel group (C / M / D / O-1 / other) of the day's observed growth **on the 2014b grid**,
so both arms are scored on identical day sets (the back-cast's own dominant group is shown as a
descriptive second view).

## 6. Comparisons and bootstrap

- **Primary:** C1, held-out 79 fire-days, mean per-fire-day 17 h F1 difference
  **back-cast − 2014b (both rerun)**, paired by fire-day.
- **Secondary (paired, back-cast − 2014b):** C1-C4 and A1-A4 F1 at 17 h, 8 h and best hour;
  area difference; over-predicted share (per-day 0/1); within ±35 % share; held-out and all
  fire-days. Skill gains: gain = F1(C1) − F1(C2) per fire-day on each grid (and A1-A4 − C2),
  and the difference in gains (back-cast gain − 2014b gain), paired by fire-day. E1: per-day
  coverage (0/1), CRPS and P10 ≥ 90 % (0/1).
- **Bootstrap:** 2,000 draws resampling **whole fires** with replacement, seed 1, percentile 95 %
  CI of the mean of fire-day differences (as #79's `PREREG`, `opendata_fire_test.boot`).
  Decision words: "better" / "worse" only if the CI excludes 0, else "no detectable
  difference".
- **Reproduction checks (reported, not verdicts):** the 2014b rerun of C1 against the record's
  0.212 and the diagonal-fix runs; the back-cast rerun of C1 against #79's per-fire-day 17 h F1
  (`backcast/runs/ab2025_backcast.jsonl`); maximum per-fire-day |Δ|.

## 7. What counts as "the headline changes"

1. **Held-out skill (C1):** the record's headline held-out 17 h F1 (0.212, CFS 2014b) is replaced
   by the back-cast value **if the primary CI excludes 0** (whether better or worse); the 2014b
   value stays in the record as the former comparator. If the CI includes 0, the headline stays
   0.212 and the back-cast value is added beside it as "no detectable difference".
2. **Over-prediction ("over-predicted on 93 % of days", C2 all 143; "75 %", C1 held-out):** the
   share is updated if the paired difference in share has a CI excluding 0; the wording
   "over-predicts on most days" changes only if the back-cast share is ≤ 50 %.
3. **Area bias (C2 all, C1 held-out):** updated if the paired CI excludes 0.
4. **Skill gain of active edges + spin-up + burning period (+0.091 [+0.056, +0.125] on 2014b
   before the fix):** the statement changes if the difference in gains has a CI excluding 0; the
   gain is also reported per grid with its CI. Component gains (A1-A4) are descriptive.
5. **Oracle / Bennett starts (C3 held-out 0.250; C4 all 0.24):** updated if the paired CI
   excludes 0.
6. **Per-fuel-group skill:** descriptive only (few days per group); no verdict.
7. **W.I.S.E. comparison (W1, 9 days):** descriptive only; too few days to rank models (as the
   record says). Bennett et al.'s national W.I.S.E. figures (0.26 / 0.54) are not rescored.
8. **Ensemble (E1):** the record's ensemble statement changes if the paired CI of coverage or
   of CRPS excludes 0; the other ensemble scores are descriptive.

Changes under 1-5 and 8 update `docs/PROJECT_RECORD.md` §4.4, `docs/validation.md` and, for 1-4,
`docs/model-card.md`. Everything else is reported in the report only.

## 8. Disclosures: what was seen before registration

- **The primary comparison has already been seen.** PR #79's pre-registered rescoring
  (`PREREG` `3582102`; [R44] §3) scored the identical back-cast domain files (same sha256) on
  the identical 143 fire-days with C1's options (its own runner, `backcast_fire_test.py run`,
  calling the same harness functions), against a 2014b rerun from the burn-scar check [R43].
  Held-out 17 h F1: back-cast 0.246 vs 2014b 0.212, **+0.034 [+0.007, +0.058], better**; all 143:
  +0.039 [+0.019, +0.059]; 8 h held-out +0.014 [−0.022, +0.043]; mean area difference +0.26 vs
  +0.25; fuel groups of the observed growth (back-cast: C 60 %, M 30 %, D 8 %, O-1 0.6 %). The
  primary here therefore **fully overlaps** #79 (same 16 held-out fires, 79 fire-days) and is
  expected to reproduce it; it is a confirmation on this branch's code, not new evidence.
- **Not seen for the back-cast before registration:** C2-C4, A1-A4, E1 and W1 on any fire-day;
  C1's precision, recall, over-predicted share, spread ratio, bearing error, best-hour F1 and
  per-fuel-group scores; any calibration-fire result other than #79's "all 143" means.
- **Seen on 2014b:** every record number in the table of §4 (`docs/validation.md`,
  `PROJECT_RECORD.md` §4.4), the [R27] / [R43] / [R44] comparator results (AB 2025 masked and
  unmasked, CFS 2026, open-data grids) and their per-fire-day F1 at 17 h and 8 h.
- **Prior expectation (judgement):** the back-cast's area bias was no smaller than 2014b's in
  #79 (+0.26 vs +0.25), so the over-prediction conclusions are not expected to change much.
- **Not part of this test:** suppression. A separate agent is compiling suppression history per
  fire and fire-day; if `reports/data/suppression-history-2026-10-15/suppression_by_fireday.csv`
  exists when scoring finishes, a stratified table is added as **exploratory**, outside these
  verdicts. Outputs are keyed by `fire_id` and ISO date for that join.
