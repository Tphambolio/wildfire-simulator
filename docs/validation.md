# Validation against observed fires

How FireSim's fire growth compares with the observed daily growth of real Alberta wildfires,
measured the way Bennett et al. (2026) measured W.I.S.E. (the successor of Prometheus).
First results: 2026-10-07. This page reports skill scores, not a single "accuracy" figure.

**In short:** started from the observed perimeter and run for one burn day, FireSim
over-predicts growth on most days, much as W.I.S.E. does with default settings. Its one-day
overlap with the observed growth is low (F1 0.15-0.24 at the default 06-23 h window depending
on how the fire is started; 1 is perfect), in the same band as W.I.S.E. (0.26 nationally, and
0.19 vs FireSim's 0.24 when both were run on the same nine Alberta fire-days). The main error
sources are the burn window, treating the whole perimeter as active, and wind. It supports
preparedness, training and what-if planning now. It is not an operational growth forecast (see
"What this means for EOC use").

**Second round (same day):** marking only the recently active parts of the perimeter as
active, starting the hourly FFMC the previous afternoon and burning 10:00-20:00 raised the
one-day F1 on **held-out fires** (fires never used to choose any setting) from 0.12 to 0.21
(+0.09, 95 % CI +0.06 to +0.13) and cut the area over-prediction from +0.72 to +0.27. That
puts FireSim's operational set-up level with W.I.S.E. defaults (0.26 nationally; 0.19 on the
nine shared Alberta days), still well short of tuned W.I.S.E. (0.54). See "Skill
improvements and held-out results".

## Skill improvements and held-out results (second round, 2026-10-07)

Three changes were tested on the same 143 fire-days, each separately and combined, after
splitting the **fires** (not days) into a calibration half and a held-out test half. Every
choice (which active-edge rule, whether to spin up FFMC, which burning period) was made on
the calibration fires only; the test fires were scored once with the chosen set-up.

**Split.** Fires ranked by a seeded SHA-256 hash of their CFSDS id, first half calibration
(`firesim.validation.report.split_fires`, seed `firesim-skill-2026`, fixed before any run):
16 calibration fires / 64 fire-days (including the 2016 Horse River fire) and 16 test fires /
79 fire-days. Differences are paired by fire-day; 95 % confidence intervals come from a
bootstrap that resamples whole fires (2,000 draws), so days of one fire are not treated as
independent.

**What changed (all opt-in in the engine; defaults unchanged).**

1. **Active and inactive perimeter edges** (`active_edges` in the grid model and in the
   perimeter-override API; a GeoJSON line, polygon or point geometry with a buffer). Only
   starting cells within the buffer of the active geometry burn; the rest of the observed
   fire is burned out and cannot spread or burn again, so an inactive edge holds until fire
   from an active edge reaches the fuel beyond it. Operationally the geometry comes from an
   RPAS thermal flight. In the harness (`--ignition active`) it is derived **without the
   burn day's outcome**: the cells CFSDS shows burning on the previous day (or two days) are
   the active zone. Across the 143 fire-days that zone is a median 33 % of the starting
   perimeter's edge cells and contains a median 84 % of the edge cells that the next day's
   growth actually touches (precision 39 %, against 12 % if edge cells were picked at
   random). Bennett's ignition (edge cells touching the next day's growth) is kept as the
   oracle upper bound.
2. **Diurnal burning.** (a) *FFMC spin-up:* the daily FFMC describes mid-afternoon moisture
   (about 16:00 LST; Lawson et al. 1996, Van Wagner 1987), so the hourly FFMC model (Van
   Wagner 1977) now starts at 17:00 MDT the previous day and runs through the night on the
   ERA5 hourly weather instead of starting the 06:00 run at yesterday's afternoon value.
   Hourly FFMC and wind then give the diurnal rate of spread, as in Beck et al. (2002). No
   parameter is fitted. (b) *Burning period:* fire spreads only between set local hours each
   day (Prometheus / W.I.S.E. "burning conditions", Tymstra et al. 2010), ROS x 0 outside.
   Candidates 10-20, 12-20 and 10-18 h were compared on the calibration fires.
3. **Performance:** the grid model's per-cell FBP table is vectorised (below); results are
   unchanged.

**Results on the held-out test fires** (16 fires, 79 fire-days). "End of day" scores all
growth simulated from 06:00 to 23:00 (Bennett's 17 h window; with a burning period the fire
stops at 20:00). ΔF1 is against the first-round operational set-up.

| Set-up (operational: no burn-day information) | F1 mean (median) | Precision | Recall | IoU | Area diff | Over-predicted days | ΔF1 [95 % CI] |
|---|---|---|---|---|---|---|---|
| Whole perimeter active, 06-23 h (first round) | 0.118 (0.076) | 0.09 | 0.70 | 0.07 | +0.72 | 91 % | – |
| + FFMC spin-up only | 0.125 (0.065) | 0.10 | 0.64 | 0.07 | +0.66 | 89 % | +0.007 [-0.001, +0.014] |
| + burning period 10-20 h only (with spin-up) | 0.146 (0.091) | 0.14 | 0.56 | 0.09 | +0.54 | 84 % | +0.028 [+0.013, +0.042] |
| + active edges only (previous day's growth) | 0.154 (0.118) | 0.13 | 0.58 | 0.09 | +0.52 | 85 % | +0.036 [+0.018, +0.057] |
| + active edges (previous 2 days) and spin-up | 0.164 (0.113) | 0.13 | 0.55 | 0.10 | +0.47 | 84 % | +0.046 [+0.026, +0.067] |
| **Chosen: active edges (2 days) + spin-up + burning period 10-20 h** | **0.208 (0.184)** | 0.21 | 0.47 | 0.13 | **+0.27** | 75 % | **+0.091 [+0.056, +0.125]** |
| Oracle start (Bennett ignition) + spin-up, 06-23 h | 0.245 (0.215) | 0.28 | 0.51 | 0.15 | +0.22 | 70 % | +0.127 [+0.089, +0.166] |

On the calibration fires the same rows score 0.191, 0.202, 0.216, 0.213, 0.219, **0.224** and
0.258. Averaged per fire first (Bennett's Table 2 style) the chosen set-up scores 0.224 on the
test fires (first round 0.137). With the chosen set-up the median predicted/observed forward
spread distance is 1.14 (first round 2.74), 24 % of days are within ±35 % (10 %), and the
median absolute head-bearing error is 44° (53°). The best-hour ("oracle duration") F1 on the
test fires rises from 0.191 to 0.265; with Bennett's ignition it is 0.368.

**How the choice was made.** Calibration F1 at the end of the day: active edges 1 day 0.213,
2 days 0.219, 1 day with a 1 km buffer 0.210 (so the default one-cell buffer); spin-up made
no difference to calibration F1 (0.213 with and without, on active edges) but reduced the
area over-prediction, and it is physically required, so it was kept. Burning periods with
active edges (2 days) and spin-up: 10-20 h 0.224, 12-20 h 0.222, 10-18 h 0.219; 10-20 h was
chosen. These calibration differences (0.219-0.224 across six active-edge / burning-period
variants) are far smaller than their uncertainty, and the held-out F1 of the same six
variants ranges 0.208-0.229, so the gain comes from the three ideas, not from the exact
setting; the chosen one happens to be the lowest of the six on the test fires.

**What did not help.** FFMC spin-up alone changes F1 by less than 0.01 (it mostly trims
morning spread and the area bias). Widening the active zone to 1 km lowered calibration skill.
A shorter window from 06:00 without the other changes helps less: the best fixed window on the
calibration fires is 10 h (F1 0.219 vs 0.191 at 17 h), and on the test fires the first-round
set-up scores 0.148 at 10 h. The burning period makes the **under-prediction of the biggest
runs worse**: on the Horse River fire's 4-5 May runs the predicted head reaches 16-22 % of the
observed distance (first round 27-37 %); F1 on those two days goes from 0.33 / 0.59 to 0.34 / 0.53.
By fuel (all 143 days, chosen set-up vs first round) M-2 days improve most (F1 0.06 to 0.16,
area difference +0.92 to +0.60) and D-2 days least (0.08 to 0.11); small days (< 100 ha of
growth) remain near zero (0.02 to 0.08).

**Run time.** Vectorising the per-cell FBP evaluation made the first-round runs about three
times faster with **identical scores**: re-running the 143 perimeter-start fire-days gave the
same true/false positive and negative areas in every fire-day and window (maximum difference
0), with model run time falling from 4.0 to 2.05 CPU-hours (median 37 s to 11 s per fire-day,
largest Horse River day 12 to 9 min; same machine, not otherwise controlled for load). What
remains is mostly the level-set update on the largest fires. The chosen set-up takes 0.57
CPU-hours (median 4 s per fire-day) because fire spreads for 10 h instead of 17.

**Caveats.** 16 test fires is few: the confidence intervals above are wide and treat fires,
not days, as the unit. The active zone comes from CFSDS's interpolated day of burning, which
is cleaner than a real thermal flight in some ways (whole perimeter, no smoke or canopy
occlusion) and coarser in others (MODIS/VIIRS timing, 90 m); real RPAS edges should be
validated separately. The burning period is a fixed clock window, so it cannot represent
overnight runs or days that burn late; it is set per run in the engine and is not a default.
The deterministic skill above is reported alone; ensemble and RPAS-corrected (mid-day
re-start) skill are not yet measured.

## Results (first run, 2026-10-07)

143 fire-days from 32 Alberta fires (2014-2024), including six days of the 2016 Horse River
fire. Scores are fire-day means (medians in brackets) on the day's growth. W.I.S.E. figures are
Bennett et al.'s Table 1 (2,210 fires, 19,848 fire-days, Canada-wide).

| Run | F1 | Precision | Recall | IoU | Area diff | Hausdorff (m) |
|---|---|---|---|---|---|---|
| FireSim, perimeter start, 06-14 h (8 h) | 0.18 (0.15) | 0.18 | 0.49 | 0.11 | +0.40 | 9,980 (6,436) |
| **FireSim, perimeter start, 06-23 h (17 h)** | **0.15 (0.10)** | 0.11 | 0.67 | 0.09 | **+0.67** | 11,437 (7,831) |
| FireSim, perimeter start, 24 h | 0.13 (0.08) | 0.09 | 0.71 | 0.08 | +0.73 | 12,266 (8,657) |
| FireSim, perimeter start, best hour | 0.22 (0.20) | 0.19 | 0.44 | 0.14 | +0.40 | 9,267 (6,200) |
| FireSim, Bennett ignition, 8 h | 0.28 (0.28) | 0.41 | 0.38 | 0.18 | -0.11 | 5,578 (3,280) |
| **FireSim, Bennett ignition, 17 h** | **0.24 (0.24)** | 0.25 | 0.55 | 0.15 | **+0.29** | 6,487 (4,278) |
| FireSim, Bennett ignition, best hour | 0.38 (0.40) | 0.46 | 0.40 | 0.25 | -0.13 | 4,916 (2,893) |
| FireSim, Bennett ignition, best hour and best of 12 wind directions (20 fire-days) | 0.43 (0.46) | 0.56 | 0.45 | 0.30 | -0.12 | 5,483 |
| W.I.S.E. S1 default, 06-23 h | 0.26 | 0.20 | 0.86 | 0.19 | +0.54 | 2,828 |
| W.I.S.E. S2 best hour | 0.50 | 0.45 | 0.64 | 0.28 | -0.11 | 918 |
| W.I.S.E. S3 best hour and wind | 0.54 | 0.48 | 0.70 | 0.31 | -0.06 | 891 |

Averaged per fire first (Bennett's Table 2 style), the 17 h F1 is 0.17 with a perimeter start
and 0.26 with Bennett's ignition (W.I.S.E.: 0.28).

**W.I.S.E. on identical inputs.** Nine fire-days were also run in W.I.S.E. 1.0.6-beta.6
(headless Docker) with exactly FireSim's fuel, DEM, starting perimeter, hourly weather and
codes (`scripts/validation/wise_fireday.py`). Mean 17 h F1: W.I.S.E. 0.19, FireSim 0.24; area
difference +0.63 vs +0.58; best hour 0.27 vs 0.31. The two models track each other day by day
(both near zero on the same bad days, both about 0.5 on the good ones), so on these inputs the
error comes mostly from what the two models share (FBP rates, the inputs and the set-up), not
from FireSim's spread algorithm. Nine days are too few to rank the models.

**Biases.**

- **Area is over-predicted.** With a perimeter start, growth is over-predicted on 93 % of
  fire-days at 17 h (77 % at 8 h). Treating the whole observed perimeter as active roughly
  triples the predicted area (median 2.9 times the Bennett-ignition prediction): real fires
  grow from part of their edge, while FireSim spreads every flank and back. Bennett's ignition
  removes much of that (over-predicted on 73 % of days) but cannot be used in a forecast,
  because it is chosen from the next day's growth.
- **Burn window.** Shorter is better: the best hour has a median of 5 h (perimeter start) and
  7 h (Bennett ignition) after 06:00. W.I.S.E. showed the same (Bennett: 59 % of days peak
  within 6 h). Running 06-23 h at full FBP rates over-burns.
- **Head runs on big days are under-predicted.** For days with >= 1,000 ha of growth the
  median forward spread ratio is about 1.1-1.5, but on the Horse River fire's 4-5 May runs it
  was 0.1-0.4 (predicted head 10-40 % of the observed distance) while total area was still
  roughly right or over. Small days (< 100 ha) are over-predicted several-fold (median spread
  ratio 2-6).
- **Direction.** Median absolute head-bearing error is about 50-60°. Picking the best of 12
  constant wind directions raises the best-hour F1 from 0.39 to 0.43 on the 20-day subset, a
  smaller gain than burn duration, as in Bennett.
- **Rate of spread.** Forward spread distance is within ±35 % of the observed (Cruz &
  Alexander's band) on only about 20 % of fire-days.
- **By fuel**, conifer days (C-1, C-2: 108 of 143) score near the average; the green aspen and
  mixedwood days (D-2, M-2) score worst (17 h F1 about 0.06-0.08 with a perimeter start, with
  large over-prediction).
- **By FWI and size**, skill rises with the size of the day's growth (17 h F1 0.02 for < 100 ha,
  0.25 for 1,000-10,000 ha, 0.36 for > 10,000 ha) and with fire weather (Extreme FWI days
  best, n = 4). This matches Bennett: small days are hard for every model.
- **Hausdorff distances** are 2-4 times Bennett's. With a perimeter start the farthest
  disagreement is usually a flank of a large old fire that the model spreads and the
  observation does not; Hausdorff distance is dominated by the single worst point, so it
  penalises this heavily.
- **Spotting** (opt-in Albini model) made no material difference on the 50-day subset (17 h
  F1 0.154 with and without; area difference +0.69 both).

Run time on a shared 12-core workstation: median 37 s per fire-day (90th percentile 5 min,
largest Horse River day 12 min), about 4 CPU-hours for the 143 fire-days, mostly FBP
re-evaluation at each weather hour (see "Known engine issues"; now vectorised, about three
times faster with identical results). W.I.S.E. took a median of about 3 min on the same days
with 4 cores.

## What this means for EOC use

- **Preparedness and training now.** One-day growth from FireSim is in the same low-skill band
  as the operational Canadian model with default settings. That is useful for exercises,
  what-if planning and showing how a fire could behave under given weather, and to show how
  sensitive the outcome is to wind and burn window.
- **Not an operational forecast.** On a typical day less than a quarter of the predicted and
  observed growth overlap. Default 17 h runs over-state the area that will burn by several
  times; they should be read as an upper envelope (where the fire could get), not as tomorrow's
  perimeter. The biggest wind-driven runs can still go farther than predicted.
- **Use the RPAS active edges and a burning period.** Marking the active edges from a thermal
  flight (`active_edges` in the perimeter override), starting FFMC from the previous afternoon
  and burning 10:00-20:00 roughly halves the area over-prediction and raises one-day F1 to
  about 0.21 on held-out fires (second round). The biggest wind-driven runs are then
  under-predicted further, so keep a full 06-23 h run as the upper envelope alongside it.
- **What would improve it next:** run wind-direction and wind-speed ensembles and present burn
  probability rather than one perimeter, use the current fuel grid with per-cell percent
  conifer and burn scars, calibrate per fuel type (D-2 and small days remain poor), and
  measure RPAS-corrected mid-day restarts. Each change can be measured with
  `scripts/validate.py` against the same fire-days.

## Data

| Input | Source | Licence | Use here |
|---|---|---|---|
| Observed daily progression | Canadian Fire Spread Dataset (CFSDS) v1.1, Barber et al. 2024, *Sci. Data* 11:764; [OSF f48ry](https://osf.io/f48ry/) (doi:10.17605/OSF.IO/F48RY) | CC BY 4.0 | Per-fire day-of-burning (DOB) rasters, 90 m, Lambert conformal conic NAD83; fires >= 500 ha, 2002-2024 |
| Fire-day weather and FWI | CFSDS daily summaries (`Firegrowth_groups_v1_1_<year>.csv`: `ffmc`, `dmc`, `dc`, `bui`, `isi`, `fwi`, `ws`, `rh`, `tmax`, ... per fire-day, ERA5-derived) | CC BY 4.0 | Starting FFMC/DMC/DC (previous day's row); FWI class of the burn day |
| Hourly weather | ERA5 (Hersbach et al. 2020) via the [Open-Meteo archive API](https://open-meteo.com/en/docs/historical-weather-api), point at the fire centre, local time | CC BY 4.0 (Open-Meteo); Copernicus licence (ERA5) | Hourly temperature, RH, 10 m wind speed and direction, precipitation |
| Fuel | CFS National FBP fuel types, version 2014b (Beaudoin et al. 2014 kNN forest attributes + LCC 2011), 250 m, [CWFIS downloads](https://cwfis.cfs.nrcan.gc.ca/downloads/fuels/archive/) | NRCan end-user agreement: internal use, not redistributed | Fuel per cell (`cfs_national_2014` code scheme) |
| Terrain | Canadian Medium Resolution DEM (MRDEM) 30 m DTM, [cloud-optimised GeoTIFF](https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif) | Open Government Licence - Canada | Slope and aspect |

CFSDS has no wind direction and one row per day, so hourly weather comes from ERA5. Bennett
used ERA5-Land (9 km); Open-Meteo's ERA5-Land feed has no 10 m wind, so ERA5 (0.25°) is used
for all hourly variables. The CFSDS DOB is the truncated kriged arrival day from MODIS/VIIRS
hotspots inside the NBAC final perimeter, shifted to local daylight time (CFSDS R example), so a
"day" is a local calendar day. Its timing within a day is interpolated from a few satellite
passes, so the split between consecutive days is uncertain by hours.

The current national grids (2024, 100 m and 30 m, CIFFC codes with percent conifer per
cell; `cfs_national` code scheme) post-date most of these fires and map their burn scars, so
the 2014b grid is used for every fire, as Bennett did. Unlike Bennett, no burn-scar succession
rules are applied, so areas burned between 2014 and the fire are mapped as their 2014 fuel.

The data live outside the repository (`$FIRESIM_VALIDATION_DATA`, default
`/home/rpas/dev/wildfire/validation-data`; about 0.6 GB):

```bash
python scripts/validation/fetch_cfsds.py                    # CFSDS rasters + daily summaries (~100 MB)
curl -O https://cwfis.cfs.nrcan.gc.ca/downloads/fuels/archive/National_FBP_Fueltypes_version2014b.zip
unzip National_FBP_Fueltypes_version2014b.zip -d $FIRESIM_VALIDATION_DATA/fuels/nat2014b
export PYTHONPATH=engine/src
python scripts/validate.py prepare --n-fires 32 --days-per-fire 6   # domains, fire-days, ERA5
python scripts/validate.py run --ignition perimeter --workers 8     # one JSON line per fire-day
python scripts/validate.py run --ignition bennett --workers 8
python scripts/validate.py report                                   # report.md + fire_days.csv
# second round: active edges, FFMC spin-up, burning period; calibration vs held-out test fires
R=--runs-dir=$FIRESIM_VALIDATION_DATA/skill/runs
python scripts/validate.py $R run --name perimeter_base --windows 4 6 8 10 12 14 17 24
python scripts/validate.py $R run --name active2_spin_bp1020 --ignition active --active-days 2 \
    --ffmc-spinup --burning-period 10 20 --windows 4 6 8 10 12 14 17 24
python scripts/validate.py $R compare perimeter_base active2_spin_bp1020 --ref perimeter_base
python3 scripts/validation/wise_fireday.py 2019_177:168 ...         # optional WISE runs (Docker)
```

## Protocol

For each fire-day (Bennett's day *n*):

1. **Grid.** DOB, fuel and DEM are reprojected with `rasterio.warp.reproject` onto one regular
   latitude/longitude grid per fire (FireSim's `FuelGrid` convention): nearest neighbour for
   DOB and fuel, bilinear for elevation. Cells are 90 m (CFSDS resolution); fires whose domain
   would exceed 1.2 million cells use 180 m (the 2016 Horse River fire). The domain is the fire
   plus 15 km; each fire-day is cropped to the area burned so far plus 20 km. No run reached the
   edge of its working area.
2. **Start.** Everything CFSDS shows burned before day *n* is the starting fire. In the default
   **perimeter** mode it enters through FireSim's RPAS-correction path (`initial_perimeter`
   for the largest burned patch, `initial_burned` for the others): this uses no information
   from day *n*. The **bennett** mode copies Bennett's set-up: only day *n*-1 cells that touch
   day *n*'s observed growth are ignited, and older burned cells are non-fuel. That uses the
   outcome to decide where the fire is active, so it flatters the model; it is there for a
   like-for-like comparison with the W.I.S.E. numbers.
3. **Weather.** Hourly ERA5 from 06:00 local on day *n*; FFMC, DMC, DC from the CFSDS row of
   day *n*-1 (Bennett used the previous day's codes from a national daily FWI database). FFMC
   then follows FireSim's hourly FFMC model; DMC and DC stay fixed. Foliar moisture from date,
   location and elevation (ST-X-3).
4. **Fuel options.** National-grid D-1 and M-1 labels become D-2 / M-2 between day of year 150
   and 258 (green-up to leaf-off; an assumption for Alberta's boreal); M-1/M-2 at 50 %
   conifer; O-1 curing 90 % outside that period, 60 % inside; wetland (120), vegetated
   non-fuel (122), urban, water and non-fuel classes are non-fuel.
5. **Run.** The grid (level-set) model, deterministic, no suppression, spotting off unless
   stated, 24 h.
6. **Score.** Bennett's metrics on the day's **growth**: cells in the model's starting area are
   removed from both prediction and observation. Scored at 8 h (06-14), 17 h (06-23, Bennett's
   default "Scenario 1") and 24 h, plus the best of hours 1-17 ("oracle", Bennett's Scenario 2,
   which needs the observation and therefore measures the model's potential, not its forecast
   skill). Cumulative (whole-fire) scores are also stored; they are higher simply because old
   burned area counts as agreement, and are not reported as skill.

Metrics (`engine/src/firesim/validation/metrics.py`, unit-tested on shapes with known
answers): precision TP/(TP+FP); recall TP/(TP+FN); F1 = 2PR/(P+R) (Sørensen/Dice); IoU =
TP/(TP+FP+FN) (Jaccard); normalised area difference (A_pred - A_obs)/(A_pred + A_obs), > 0 is
over-prediction; Hausdorff distance between the predicted and observed growth cells (exact for
the rasters via the Euclidean distance transform; Bennett computed it on polygons). Head
spread: the growth cell farthest from the starting area gives the forward spread distance and
its bearing (Fox-Hughes et al. 2024); with equal durations, predicted/observed spread distance
is the rate-of-spread ratio, and the share within ±35 % is the Cruz & Alexander (2013) band.

**Fire-day selection.** Alberta fires (CFSDS centroid inside Alberta) from 2014-2024 with
>= 1,000 ha final area; 32 fires sampled round-robin across years with a fixed seed, the 2016
Horse River fire always included. Fire-days follow Bennett's filters: day *n*-1 and day *n*
both burned, and day *n*'s growth touches the earlier burned area. Up to 6 fire-days per fire
are sampled (Horse River: 2-7 May 2016, its main runs, including the day it entered Fort
McMurray). Bennett used 2,210 fires and 19,848 fire-days nationally; this is a first, small
Alberta sample, so the numbers carry wide uncertainty.

## Assumptions and differences from Bennett et al. (2026)

- No suppression in either model. Bennett removed suppressed fires; CFSDS does not flag
  suppression, so suppressed days (e.g. Horse River at Fort McMurray) are included.
- Burn window: 06:00 local start; scored at 8, 17 and 24 h. Burning between midnight and 06:00
  on day *n* is in the observation but not simulated (usually small).
- Fuel 250 m resampled to 90 m; no burn-scar succession; M-1/M-2 at one percent conifer.
- One weather point per fire (ERA5 0.25°), no spatial weather.
- Spotting off by default (as Bennett); a spotting subset is reported separately.
- Ignition: FireSim's operational path ignites the whole observed perimeter; Bennett ignited
  only the edge that the next day's growth touches (see Protocol 2).

## Known engine issues found while building this

- **Fuel and DEM loaders do not reproject projected rasters** (found by a separate audit):
  `load_fuel_grid` / `load_terrain_grid` index cells linearly inside the raster's lat/lng
  bounding box, so cells in a projected CRS are misplaced (Edmonton fuel grid: median 121 m;
  `edmonton_dem.tif`: median 1.4 km). The harness does not use the loaders for projected
  rasters: it reprojects everything itself (Protocol 1). A separate PR fixes the loaders.
- `load_fuel_grid` treats a geographic raster's resolution (degrees) as metres and would shrink
  an EPSG:4326 raster to almost nothing. Not fixed here (the harness builds `FuelGrid` directly).
- `normalize_fuel_codes` crashed on 8-bit rasters with a no-data value (the national 2014b grid
  is uint8). Fixed in this PR (cast to int32 before writing the -9999 fill).
- The spotting model draws from Python's unseeded global `random`, so spotting runs are not
  repeatable. The harness seeds it per fire-day; the engine itself is unchanged.
- Run time: about 80 % of a fire-day run was re-evaluating FBP for every distinct cell type
  (fuel x slope x aspect) at each hourly weather change (`_CellParams.evaluate`, about 18,000
  `calculate_fbp` calls per hour on a real fire-day). Fixed in the second round: each fuel /
  canopy group is evaluated at once with `fbp_ellipse_arrays` (same equations and operation
  order, slope-equivalent wind once per distinct slope). Results equal the scalar path to
  floating-point rounding (`engine/tests/fbp/test_vectorised.py`,
  `engine/tests/spread/test_cellular_fbp_table.py`) and the 143 fire-day scores were identical.
- A weather period in which nothing can spread (e.g. outside a burning period) used to end the
  grid run; it now waits for the next weather change. FBP floors ROS above zero, so default
  runs were not affected.

No default engine spread behaviour was changed for these measurements: active edges, FFMC
spin-up and the burning period are opt-in.

## References

- Barber, Q.E., et al. (2024). The Canadian Fire Spread Dataset. *Scientific Data* 11, 764.
  doi:10.1038/s41597-024-03436-4.
- Bennett, L., Jain, P., Moore, B., Boisvert, J. (2026). Assessment of fire spread predictions
  from the Wildfire Intelligence and Simulation Engine (W.I.S.E.) using a large set of
  satellite-derived wildfire perimeters. *Int. J. Wildland Fire* 35(8), WF26072.
  doi:10.1071/WF26072.
- Beaudoin, A., et al. (2014). Mapping attributes of Canada's forests at moderate resolution
  through kNN and MODIS imagery. *Can. J. For. Res.* 44, 521-532.
- Cruz, M.G., Alexander, M.E. (2013). Uncertainty associated with model predictions of surface
  and crown fire rates of spread. *Environmental Modelling & Software* 47, 16-28.
- Fox-Hughes, P., et al. (2024). *Int. J. Wildland Fire*, doi:10.1071/WF23028 (four
  simulators on ten Australian fires; threat score, bearing and forward-spread error).
- Beck, J.A., Alexander, M.E., Harvey, S.D., Beaver, A.K. (2002). Forecasting diurnal
  variations in fire intensity to enhance wildland firefighter safety. *Int. J. Wildland Fire*
  11, 173-182.
- Hersbach, H., et al. (2020). The ERA5 global reanalysis. *Q. J. R. Meteorol. Soc.* 146,
  1999-2049.
- Lawson, B.D., Armitage, O.B., Hoskins, W.D. (1996). *Diurnal variation in the Fine Fuel
  Moisture Code: tables and computer source code.* FRDA Report 245.
- Tymstra, C., Bryce, R.W., Wotton, B.M., Taylor, S.W., Armitage, O.B. (2010). *Development and
  structure of Prometheus: the Canadian wildland fire growth simulation model.* Inf. Rep.
  NOR-X-417.
- Van Wagner, C.E. (1977). *A method of computing fine fuel moisture content throughout the
  diurnal cycle.* Inf. Rep. PS-X-69.
- Van Wagner, C.E. (1987). *Development and structure of the Canadian Forest Fire Weather Index
  System.* Forestry Tech. Rep. 35.
