# Pre-registration: W.I.S.E. as a black-box comparator on FireSim's held-out Alberta fire-days (C1)

**Registered:** 2026-10-24, committed and pushed on branch `feat/c1-comparator` (from `master`
`6dcc41d`, after PR #103) **before any held-out fire-day was run or scored with the comparator**.
Owner approval: Travis Kennedy, 2026-10-24 (backlog item C1, tool choice "WISE first, fall back to
Cell2Fire if WISE cannot run headless on this Linux workstation").

**Code:** `scripts/validation/c1/` (`run_c1.py` runner, `wise_job.py` job builder, `communities.py`
community arrival, `analyze_c1.py` paired differences). Committed with this file; the analysis
must implement exactly what is stated here. Any later change to the code that could change a
number is listed in the Result section with its reason.

## 1. Question

"Prometheus-level" is the owner's long-term goal for FireSim (strategy brief Q3). This
registration makes it measurable: on a fixed held-out set of Alberta fire-days, with the same fuel,
weather and start, scored by the same script, **how does FireSim's operational set-up compare with
W.I.S.E. (the Prometheus successor) on (1) 17 h F1, (2) arrival-time error at communities and
(3) ensemble coverage?** The set, inputs and scoring are frozen so the same comparison can be
re-run at each FireSim release.

## 2. Tool choice and feasibility (backlog searches I-1, I-2)

- **W.I.S.E. runs headless on this workstation.** W.I.S.E. 1.0.6-beta.6, official release package
  `wise-ubuntu2204-1.0.6-beta.6.deb` (sha256 `73fc9e16…6104382`, GitHub release of the
  WISE-Developers project), installed in an isolated Docker image `wise-local:1.0.6b6`
  (image `sha256:5cae5762…a5ba9652f`; `~/dev/wildfire/wise-reference/Dockerfile`: ubuntu:jammy +
  the .deb + OpenJDK 17 headless; no system-wide change, no sudo). It runs a JSON job file
  (`wise -t job.fgmj`) with no GUI, no MQTT and no Builder service. It was already used this way
  for the record's 9-fire-day comparison (2026-10-07) and the reference fixtures
  (`engine/tests/spread/data/wise_reference.json`).
- **So Cell2Fire is not needed** (fallback not triggered). Cell2Fire remains vendored, unrun.
- **Black box:** only documented FGMJ inputs are set (the demo job's structure, `vendor/wise_demo`,
  sha256 `f27b0a7d…aaf448`; LUT `405125f7…2aace`). No WISE source is read or changed; no WISE
  setting is tuned on any fire. The demo job's growth options are kept as shipped
  (`maxAccelTimestep` 2 min, `distRes` 1 m, `perimRes` 1 m, `minSpreadRos` 0.001 m/min,
  `dynamicSpatialThreshold` on, breaching on, growth percentile off); spotting off as in FireSim.

## 3. Fire-days (fixed sets)

| Set | File | sha256 | Use |
|---|---|---|---|
| **H79: held-out deterministic set** | `scripts/validation/c1/sets/heldout_79.txt` | `41f64a1ca901637d196d4a8e192081e7cd0e26fdc2a2dfaa9e650e98766dd36c` | metrics 1 and 2 |
| **H65: held-out ensemble set** | `scripts/validation/c1/sets/heldout_ens_65.txt` | `9367110ba8812b00bd2993bf0453cf969302305bb6eed0d219b728a319fffa18` | metric 3 |

- H79 = all 79 fire-days of the 16 held-out test fires of the record's split
  (`firesim.validation.report.split_fires`, seed `firesim-skill-2026`) in `manifest.json`
  (sha256 `b4576b00…6b521b76`, 143 fire-days, 32 fires).
- H65 = the record's E1 ensemble set (65 of the 79, all 16 fires; chosen in 2026-10-08 by FireSim's
  deterministic run time only, never by outcome; it drops 14 of the slowest, largest days).
- Fuel domains: the pre-fire back-cast grid (`$FIRESIM_VALIDATION_DATA/backcast/roots/ab2025_backcast/domains`,
  combined sha256 `bd422428…1f2ce8`, as in PREREG_RESCORE_BACKCAST), FireSim's own fuel for dated
  historical Alberta runs.
- **Prior exposure (disclosed):** 4 of H79 (2014_461:190, 2019_177:168, 2021_1227:191,
  2023_696:186) were run in WISE on 2026-10-07/15 under a different set-up (whole perimeter,
  06-23 h, daily FFMC as hourly). Nothing was chosen from those runs. **Communities:** before
  registering, the observed side alone (CFSDS DOB and the community layer, no model output) was
  checked: on H79, **no community's 500 m reach zone was reached by the observed fire on its burn
  day** (209 eligible community-fire-days; at 5 km, one: Peace Point, 2019_177:149). Metric 2 on
  H79 is therefore a false-arrival measure (see §6.2); this is known and accepted in advance.

## 4. What both models get (input mapping)

Per fire-day, FireSim's operational set-up (record's C1/`active2_spin_bp1020`): start 06:00 local
(UTC-6), active edges = cells burned in the 2 days before the burn day (1-cell buffer), the rest
of the start area burned out, FFMC spun up hourly from 17:00 the previous day, burning period
10:00-20:00, deterministic, spotting off, no suppression, windows 4-24 h. Harness defaults: D-1/M-1
→ D-2/M-2 for day of year 150-257, M-1/M-2 at the grid's percent conifer (50 % where the code
carries none), O-1 curing 60 % in the green season, 90 % otherwise.

The WISE job (`wise_job.build_job`) gets:

| Input | FireSim | WISE |
|---|---|---|
| Fuel | harness `FuelGrid` (lat/lng grid) | the same cell types → WISE LUT codes (C-1…C-7, D-1/D-2, S-1…S-3, O-1a/O-1b; M-1/M-2 by percent conifer to the nearest 5 % LUT class, 405-495 / 505-595; M-3/M-4 at 35 % dead fir, 735/835); reprojected nearest-neighbour to EPSG:3400 at the harness resolution; cells FireSim burns out at the start are non-fuel |
| Terrain | domain DEM | the same DEM, bilinear to EPSG:3400; WISE derives slope/aspect itself |
| Start | `burning` = start cells within the active zone | the same cells as polygons (`POLYGON_OUT`, one per patch, simplified to 0.25 cell) |
| Weather | ERA5 hourly from 17:00 the day before | the same hours, one station at the domain centre; wind direction converted from true to grid north (EPSG:3400 grid convergence at the centre) |
| FFMC | hourly, Van Wagner (1977) from the previous day's CFSDS FFMC at 17:00 | **FireSim's own hourly FFMC values**, in the hourly file's FFMC column (WISE reads that column as the hourly FFMC) |
| DMC, DC | previous day's CFSDS codes, fixed | the same, fixed in every hour |
| Foliar moisture | ST-X-3 from date, location, elevation | WISE's own ST-X-3 calculation (terrain on) |
| Burning period | ROS × 0 outside 10-20 h | the scenario runs 10:00-20:00; outputs to 23:00 hold the 20:00 perimeter |
| Curing | harness rule | the same degree for O-1a and O-1b (fuel definitions with ST-X-3 O-1 parameters) |
| Output | arrival minute per cell | perimeters every 30 min → arrival = first perimeter containing the cell centre; FireSim's start area set to 0 in both, so both exclude the same start |

**Smoke test (calibration fires only; disclosed).** Five calibration fire-days (2022_441:188,
2018_441:191, 2024_126:191, 2017_241:159, 2015_230:180) and a 2-member, 2-seed ensemble on
2022_441:188 were run to validate the mapping. FireSim's 17 h F1 reproduced the record on all
five. One mapping error was found and fixed: the first job gave WISE the daily FFMC in every
hour, which WISE uses as the hourly FFMC (93.6 all day vs FireSim's spun-up 86.5-90.5), and WISE
burned 2,152 ha against 778 ha with FireSim's hourly FFMC. The mapping above (FireSim's hourly
FFMC) was fixed on that basis, **before any held-out run**. Also fixed from reading the old
`wise_fireday.py`: O-1b curing was not set (now set as O-1a), per-cell percent conifer and M-3/M-4
dead fir were not mapped. On the smoke days the two models' growth directions agree within 7°
and WISE's growth contains most of FireSim's (overlap 76-100 % of FireSim's cells). Smoke data:
`$FIRESIM_VALIDATION_DATA/c1-comparator/smoke/` (not part of any result).

## 5. Fair run, failures, resources

- **A fair run:** both models on the same fire-day with the inputs of §4, same code commit for the
  whole batch, WISE settings as in §2 (never changed per fire), no reruns to improve a score.
- **WISE failure:** non-zero exit, no `perim.shp`, the container's 5 GB memory cap exceeded, or
  3 h wall time (10,800 s). Recorded with its reason, never retried — except a workstation
  failure that is not WISE's (docker daemon down, disk full, host reboot): rerun once, disclosed.
  A FireSim error on a fire-day is recorded the same way.
- **Resources:** every WISE container `--memory 5g`; the runner under `capjob` (5 GB); a WISE run
  starts only when ≥ 8 GB is available (`/proc/meminfo`); at most two runners at once, 4 WISE
  threads each.

## 6. Metrics (all from the same scoring code for both models)

### 6.1 Metric 1 (primary): 17 h F1 on H79

`firesim.validation.harness.score_day` on each model's arrival grid: F1 (Dice) of the day's
growth beyond the start area at 17 h (06:00-23:00; growth stops at 20:00 for both), NaN → 0
(no predicted growth), as `validate.py compare`. Per fire-day difference d = F1_FireSim −
F1_WISE; higher is better.

### 6.2 Metric 2: community arrival-time error on H79

`communities.py`. Community units = FireSim's own outside Edmonton: `places.geojson` (1,388
hamlets, urban service areas, designated places, CGNDB points as 250 m discs; sha256 `0e8e76dc…`)
plus StatCan 2021 census subdivisions of settlement type (CY, T, VL, SV, IRI, S-É;
`census_subdivisions.geojson`, sha256 `3e60a2af…`). Reach zone = unit + 500 m (FireSim's
"fire within 500 m" rule), rasterised all-touched. A community is eligible on a fire-day if its
zone is on the working grid and does not touch the start area.

- Model arrival = earliest arrival minute in the zone, censored at T = 17 h.
- Observed: *reached* if any zone cell has CFSDS DOB = burn day; its time = the first VIIRS 375 m
  active-fire detection (NASA FIRMS archive, S-NPP and NOAA-20, any confidence; files of
  `validation-data/viirs/raw`, `SHA256SUMS` sha256 `15165623…c6c4d`) in the zone between 06:00
  and 23:00 local; reached but not detected in that window = *untimed* (excluded, counted); not
  reached = censored at T.
- Error = |min(a_model, T) − min(a_obs, T)| hours; fire-day value = mean over its eligible timed
  communities; fire-days with none are not in this metric. d = MAE_FireSim − MAE_WISE; lower is
  better. Also reported: communities reached by each model and false arrivals.
- On H79 every eligible community is unreached (§3), so the metric measures hours of false early
  arrival; VIIRS timing matters only on any future set with reached communities. VIIRS detections
  lag true arrival (about four overpasses a day), so an observed time is an upper bound.

### 6.3 Metric 3: ensemble coverage on H65

Both models run the **same K = 10 members per seed, seeds 1 and 2**, with identical perturbations
from `firesim.spread.ensemble.perturb_config` (common random numbers): the record's A15 sizes for
the inputs both models accept: wind direction sd 24°, log wind speed sd 0.405, FFMC sd 7.2,
log DMC/DC sd 0.6, curing sd 20.25; **ROS multiplier and foliar moisture not perturbed** (sd 0)
because WISE has no documented input for them. This is a "shared-input" ensemble, not FireSim's
operational ensemble (which also perturbs ROS and FMC); its coverage is compared between models,
not with the record's 49 %. FireSim's hourly FFMC of each member goes to WISE as in §4.

Per fire-day and seed: `ensemble_scores.score_ensemble_day` at 17 h; covered = 1 if the
observed growth area lies within the members' 10th-90th percentile of log10(area + 10 ha), else 0.
d = covered_FireSim − covered_WISE. Higher is better while both are below the ideal 80 %; if
either exceeds 80 %, |coverage − 0.8| is reported and used for the verdict instead. Also reported:
CRPS of log10 area, P10/P50/P90 footprint F1.

### 6.4 Uncertainty

Mean paired difference with a 95 % percentile bootstrap CI, 2,000 draws,
`numpy.random.default_rng(20261024)`, **resampling whole fires** (all their fire-days together);
for coverage a second stage resamples the seeds within each drawn fire-day ("by fire and seed").
FireSim and WISE are both deterministic for one input set, so metrics 1-2 have no seed stage.

## 7. Target ("Prometheus-level") and verdicts

FireSim is **at WISE level** on a metric if the lower 95 % bound of the difference, oriented so
that positive favours FireSim, is above −margin (non-inferiority); **better** if it is above 0;
**worse** if the upper bound is below 0; otherwise inconclusive. Margins (owner judgement calls,
fixed now):

| Metric | Margin | Reason |
|---|---|---|
| 17 h F1 | 0.03 | about the smallest F1 change the record treats as real (back-cast +0.034) |
| Community arrival MAE | 1.0 h | finer than an EOC planning cycle; VIIRS timing is coarser |
| Coverage | 10 percentage points | one member in ten |

The three-part target is met when all three are at least "at WISE level". The same sets, inputs,
WISE build and script are re-run at each FireSim release (`run_c1.py` + `analyze_c1.py`).

## 8. Analysis populations and order

- **Primary:** fire-days (and seeds) where both models completed.
- **Sensitivity:** WISE failures scored as no growth (F1 0, no arrival, not covered).
- **Descriptive only (no verdict):** 8 h and best-hour F1, precision, recall, area difference,
  over-predicted days, Hausdorff, CRPS, start-type strata (record A2), run time and memory; the
  calibration fire-day 2016_255:124 (Horse River into Fort McMurray, the only community arrival
  with a VIIRS time in the 143 fire-days) for metric 2.
- **Run order and cuts:** H79 deterministic first, then H65 seed 1, then seed 2. If the batch
  cannot finish in 36 h of wall time, seed 2 is dropped and coverage is reported with seed 1 only
  (seed stage then degenerate); anything not completed is reported as not completed.

## 9. Licences and use

WISE: AGPL-3.0 (the WISE-Developers repositories; the .deb itself carries no licence file); run unmodified in its own container; only its output perimeters
are read; nothing from it enters FireSim. Data: CFSDS (CC BY 4.0), ERA5 via Open-Meteo (CC BY 4.0),
NASA FIRMS VIIRS (open, NASA data policy), community layers (OGL – Alberta, OGL – Canada,
Statistics Canada Open Licence). No ML. Nothing requested from any agency.

## Result

(Appended after the runs; this section only grows.)
