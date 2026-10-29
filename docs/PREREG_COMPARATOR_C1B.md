# Pre-registration: community arrival, FireSim vs W.I.S.E., on Alberta fire-days with observed community arrival (C1b, sibling of PREREG_COMPARATOR_C1)

**Registered:** 2026-10-25, committed and pushed on branch `feat/c1-comparator` (PR #109) **before
any model (FireSim or WISE) was run on any fire-day of this set except the one disclosed in §3**.
Owner approval: Travis Kennedy, 2026-10-25 ~11:15 MDT (C1 follow-up 2: "a community-arrival set,
open data only, selection rule, metric and margin fixed before any model output").

**Why:** in PREREG_COMPARATOR_C1 metric 2 was degenerate: no held-out community was reached on
its burn day by anyone. This set is chosen so that every fire-day has at least one observed
community arrival.

**Code (frozen with this file):** `scripts/validation/c1/prepare_c1b.py` (selection and domains;
observations only), `communities_c1b.py` (metric), `analyze_c1b.py` (paired difference),
`run_c1.py` + `wise_job.py` (the C1 runner and WISE mapping, unchanged from C1 except the memory
options of commit `cfc7ef4`).

## 1. Question

On Alberta fire-days where the observed fire reached a community, how far off is each model's
arrival time at that community, and is FireSim's error within 1 h of W.I.S.E.'s?

## 2. Selection rule (fixed; observations only)

1. **Fires:** every CFSDS v1.1 fire 2014-2024 with its centroid in Alberta
   (`validate.alberta_fires`, any size) and a DOB raster (the back-cast fuel needs SCANFI epochs
   from 2010 on).
2. **Fire-days:** Bennett's filters (day n-1 burned; day n growth touches the earlier area).
3. **Community reached:** the day-n growth touches a C1 community reach zone (FireSim's
   `places.geojson` + settlement CSDs, unit + 500 m, all-touched) that no earlier day touched.
4. Steps 1-3 are screened on the native DOB raster (`prepare_c1b.py screen`: 22 fire-days,
   13 fires), then re-applied on the harness domain grid (`finalise`: a fire-day stays if it
   passes Bennett's filters on the domain and `communities.observed_arrivals` finds a reached
   community on its working area): **20 fire-days** (2 dropped: 2016_255:128 and 2023_366:160,
   no reached community on the harness grid).
5. All qualifying fire-days are kept (no sampling).

| File (`scripts/validation/c1/sets/`) | sha256 | Content |
|---|---|---|
| `community_c1b.txt` | `1f4816c33f4f396bbd79bc542f23706a903fe631c328ce7b3546c0a33f464088` | all 20 |
| **`community_c1b_primary.txt`** | `fd4272899d57513faf34d4fb71ec006d1c272b5707aa13e28262c277f8a903b7` | **14 fire-days, 11 fires, none in the 32-fire record** |
| `community_c1b_calibration.txt` | `5a45993fb2bfa90c73e3a88c838dbb5fba47821d16e012461dff19b8b996d445` | 6 Horse River (2016_255) days 124-127, 135, 139 |
| `community_c1b_screen.json`, `_finalise.json`, `_manifest.json` | `67cd2203…`, `38960d2e…`, `c8719af9…` | screen, per-day decision, run manifest |

Primary fire-days: 2019_133:148 (Steen River), 2019_172:143/147/148 (High Level, Metis, Paddle
Prairie), 2022_483:246 (Snaring), 2023_317:234 (Fort Fitzgerald, Thebathi 196), 2023_364:125 (East
Prairie), 2023_681:125/138 (Fox Lake, Fifth Meridian), 2023_704:138 (Decrene), 2023_708:125
(Entwistle, Evansburg, Lobstick, Styal), 2023_709:125 (Poplar Ridge, Drayton Valley),
2023_712:137 (Fox Creek), 2024_451:205 (Jasper, Jasper Park Lodge).

## 3. Calibration days and prior exposure

- **Calibration fire (Horse River, 2016_255):** FireSim's operational set-up was chosen on the 16
  calibration fires, which include it. Its 6 days are **descriptive only**: reported separately,
  never in the primary, the CI or the verdict.
- **New fires (the primary 14):** not in the 32-fire record, so never used for any FireSim or
  C1 choice; held out for both models.
- **Disclosed prior run:** 2016_255:124 (calibration) was run in both models on 2026-10-25 as C1's
  descriptive check (both reached Fort McMurray's zone ~1.8 h before the first VIIRS detection).
  No primary fire-day has been run by any model.

## 4. Inputs and runs

- **Domains:** built for this set by `prepare_c1b.py domains` exactly as the record's
  (`validate.py prepare`: 90 m, 180 m where needed to stay ≤ 1.2 M cells, fire + 15 km; MRDEM-30),
  with fuel = the pre-fire back-cast of the Alberta Wildfire 2025 grid to the fire's start
  (`backcast_fire_test.py` logic, SCANFI v2 epoch; restored share 2-47 %). Horse River reuses its
  record back-cast domain. Combined sha256 of the 13 domain files
  (`<id>.npz:<sha256>\n`, sorted): `31679712fa6e2bdd5ce4ca353ebd5e56063ea18f8fad26f79d4960187ed734c9`.
  Root: `$FIRESIM_VALIDATION_DATA/c1b/`.
- **Weather:** ERA5 hourly via Open-Meteo (the record's cache), CFSDS previous-day codes.
- **Models:** exactly C1's: FireSim operational set-up (active edges 2 d, FFMC spin-up, burning
  period 10-20 h); WISE 1.0.6-beta.6 black box with the C1 mapping (PREREG_COMPARATOR_C1 §4).
  Deterministic only.
- **Resources (owner rule of 2026-10-25):** WISE container cap 10 GB; one WISE container at a
  time; a run starts only with ≥ 14 GB available and no other heavy job (no other heavyjobs.slice
  scope above 1.5 GB, no other docker container); a run is killed and the batch stopped if
  available memory falls below 4 GB (then resumed later on the same rule; a stopped run is a
  workstation failure, rerun once).
- **Failure (WISE):** non-zero exit, no output, the 10 GB cap or 3 h wall time; recorded, not
  retried (except workstation failures, rerun once and disclosed).

## 5. Metric: interval-censored community arrival error

Times are hours from the run start (06:00 local, UTC-6, day n). For each reached community of a
fire-day (`communities_c1b.py`):

- **Observed interval [lo, hi].** CFSDS bounds arrival to the local day n: [−6, 18]. hi = min(18,
  first VIIRS 375 m detection in the zone at or after −6 h) (no detection, or only the night pass
  after 18 h: hi = 18). lo = max(−6, the last VIIRS overpass before hi; an overpass is a cluster,
  gaps > 20 min, of all VIIRS detection times in the working area from −6 h on), i.e. the last
  look at the fire before the first detection in the zone.
- **Model arrival a:** the earliest arrival in the zone (h); not reached in the 24 h run → 24.
- **Error:** 0 if lo ≤ a ≤ hi, else the distance from a to the interval.
- **Fire-day value:** mean error over its reached communities (communities touching the start
  area are not scored). **Paired difference** d = error_FireSim − error_WISE per fire-day; lower
  is better.
- **CI:** 95 % percentile bootstrap of whole fires, 2,000 draws, `default_rng(20261025)`.
- **Margin and verdict (the owner-confirmed C1 rule):** non-inferior if the CI's upper bound is
  below +1.0 h; better if below 0; worse if the lower bound is above 0; else inconclusive.
- **Populations:** primary = the 14 fire-days where both models completed; sensitivity = WISE
  failures scored as no arrival (a = 24).
- **Descriptive:** hits (a ≤ 18 h), false arrivals at eligible unreached communities, 17 h F1,
  the 6 calibration days.
- **Expected power (stated now):** 14 fire-days on 11 fires; the CI will be wide and an
  "inconclusive" verdict is likely. VIIRS sees each place about four times a day, so intervals of
  several hours are expected.

## 6. Order

C1 follow-up 1 (the 10 GB rerun of C1's 9 failed fire-days) first, then the 14 primary fire-days,
then the 6 calibration days, all under §4's resource rule. The results are reported in a
separate PR if they cannot finish before PR #109 is merged (owner, 2026-10-25), appended to this
file's Result section.

## Result

(Appended after the runs; this section only grows.)

### Run note (appended 2026-10-25)

- A first launch at 11:31-11:39 MDT (workstation clock) broke the owner's resource rule: the runner's own gate let it start while the sealed-confirm ensemble and the v8 draw were still running. The coordinator stopped the C1 rerun, and I killed the chained C1b runner. That runner had started the primary fire-day 2019_133:148: FireSim computed it in memory and a WISE container had started. **No C1b row was written and no C1b output was seen.**
- All runs now wait until `pgrep -f run_confirm.sh` and `pgrep -f "v8.draw|elmfire"` find nothing and ≥ 14 GB is available, in an outer loop and before every WISE run (commit `81e9950`).

### Result (appended 2026-10-29)

- **Integrity:** set sha256s verified (primary `fd427289…`, calibration `5a45993f…`).
  - `analyze_c1b.py`, `communities_c1b.py`, `analyze_c1.py` and `sets/` are byte-identical to this file's commit `eb5a116`.
  - Run rows carry `e6cc152`. Every change to `run_c1.py`/`wise_job.py` since `eb5a116` is run control only: named-job gate `81e9950`, docker stop `60eb5a7`, disk pause and job-folder trimming `e6cc152`. Inputs, the WISE mapping and scoring are untouched.
- **Primary** (11 of 14 fire-days on 9 fires where both ran; 16 reached communities):
  - Mean error FireSim 8.46 h, WISE 8.39 h. Difference FireSim − WISE **+0.08 h [+0.01, +0.17]**.
  - **Verdict: non-inferior.** The upper bound is below the +1 h margin.
  - The CI also lies wholly above 0. The §5 "worse" condition (lower bound above 0) therefore also holds: FireSim is statistically later, by about 5 min, well inside the margin.
- **Sensitivity** (3 WISE failures as no arrival; 14 fire-days / 11 fires, 20 communities): FireSim 8.71 h, WISE 9.08 h, −0.37 h [−1.48, +0.13], non-inferior.
- **Descriptive (primary, 16 communities):**
  - Hits (≤ 18 h): 6 and 6, the same communities. False arrivals: 0 and 0.
  - Inside the observed interval: FireSim 1, WISE 2.
  - **Neither model reached 10 of the 16 within 24 h.** These shared misses (≥ 6 h each) are most of the error.
  - 3 communities were reached before the 06:00 run start, which no model can match.
  - The two models agree within 2 h on all 16.
  - 17 h F1 on the 11 days: FireSim 0.318, WISE 0.349.
- **Calibration** (Horse River, descriptive, 4 of 6 days where both ran): 2.73 h vs 1.57 h, +1.17 h (one fire, no CI). WISE times Draper and Saprae Creek, which FireSim does not reach. Fort McMurray: both within 0.3 h.
- **WISE failures:** 10 GB cap (exit 137) on 3 of 14 primary days (2019_172:143, 2023_317:234, 2023_712:137) and 2 of 6 calibration days (2016_255:135, 139). Recorded, not retried (§4). FireSim had no failures.
- **Deviations and disclosures:**
  1. **Resource gate.** Runs started with ≥ 12 GB available and ran alongside other jobs (launch decision of 12:12 MDT, 2026-10-25), instead of §4's ≥ 14 GB with no other heavy job. The container cap (10 GB) was as registered, and no run was stopped for host memory.
  2. **Disk full.** At 13:08 MDT on 2026-10-25 the first two C1b fire-days (2019_133:148, 2016_255:124) failed on a full disk while writing inputs. No row was written and WISE never ran. Both were rerun on resume, a workstation failure rerun once (§4).
  3. **Run times.** Runs took 2026-10-26 00:48–05:39 MDT.
- **Report:** [R66] §12 and Fig. 4. Data: `c1b/` in the [R66] data folder.
