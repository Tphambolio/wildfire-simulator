# Verification and limits

What has been checked, against what, and what has not. Last updated 2026-10-07.

FireSim is a **planning, training and what-if tool**. Verification here means the code solves
the FBP System's equations correctly and the spread models reproduce FBP's fire shape on
simple cases. It does not mean the predictions match real fires; the comparison with observed
fires is in [validation.md](validation.md).

## 1. FBP equations: verified against cffdrs

Reference: the CFS `cffdrs` package (Python port), run with the ST-X-3 FFMC coefficient 147.2.

| Quantity | Cases | Agreement |
|---|---|---|
| ROS, back and flank ROS, LB, net effective wind and direction, ISI, SFC, CFB, CFC, TFC, HFI | 18 fuels x 6 weather/slope cases (CI fixture); 12,960 cases (development run) | float precision (relative difference <= 3e-10) |
| Foliar moisture (eqs 1-8) | 9 locations x 122 days x 3 elevations | exact |
| Acceleration: ROS, distance and LB at time t (eqs 70-73, 81) | 8 fuels x 5 CFB x 7 times x 3 ROS x 3 LB | exact |

Tests: `engine/tests/fbp/test_cffdrs_reference.py`, `engine/tests/fbp/test_calculator.py`.

## 2. Spread models: reproduce the FBP ellipse on uniform fuel

Uniform fuel, 20 km/h wind, FFMC 92, BUI about 50. Burned area relative to FBP.

| Model | Case | 1 h | 2 h |
|---|---|---|---|
| Huygens | C-2, no acceleration | 0.99 | 0.99 |
| Huygens | O-1a 100 % cured, no acceleration | 0.96 | 0.96 |
| Huygens | C-2, accelerating (vs eqs 73, 81) | 1.07 | 1.02 |
| Huygens | O-1a, accelerating | 1.07 | 1.02 |
| Level set, 50 m cells | C-2 / M-1 / O-1a, no acceleration | 0.95 / 0.96 / 0.90 | |
| Level set, 50 m cells | C-2 / O-1a, accelerating | within 10 % / 12 % | |

At equilibrium Huygens runs slightly small because each wavelet is a 36-point polygon inside
the ellipse. With acceleration it runs slightly large because the envelope keeps width from
earlier, rounder wavelets that the instantaneous FBP ellipse (LB(t)) does not; the excess
shrinks with time. The level set runs slightly small on narrow fires (grass, LB 4.4)
because of numerical smoothing at 50 m cells. Tests: `engine/tests/spread/test_fbp_ellipse_agreement.py`,
`engine/tests/spread/test_cellular.py` (also: walls block spread, diagonal walls do not leak,
fire wraps through gaps, back cells are less intense than head cells, runs are deterministic)
and `engine/tests/spread/test_grid_continuation.py` (RPAS perimeter and multi-day starts,
outline encloses the burned cells).

## 3. Comparison with WISE (independent model)

WISE 1.0.6-beta.6 (successor to Prometheus; official Ubuntu build, run headless) on the same
uniform-fuel cases: C-2 and O-1a (100 % cured), wind 0, 20 and 30 km/h from the west, FFMC 92,
BUI 60, FMC 100, flat ground, point ignition, 1 h and 2 h. WISE applies FBP acceleration to
point ignitions; its 1 m polygon ignition gives the equilibrium case. FireSim / WISE:

| Model | Accelerating cases (12) | Equilibrium cases (4) |
|---|---|---|
| Huygens | area 0.97-1.06, head 0.985-1.025, flank 1.01-1.03 | area 0.96-0.99, head 0.98-0.99, flank 1.00 |
| Level set, 25 m cells | area 0.955-1.04, head 0.99-1.02, flank 0.89-1.01 | — |

On uniform slopes (C-2 30 % with no wind, wind aligned, and wind across the slope; O-1a 40 %
across a 10 km/h wind), FireSim / WISE at 1-2 h: Huygens area 1.02-1.09, head 1.01-1.05; level
set area 0.96-1.04, head 0.99-1.05; head direction within 1-2 degrees (cross-slope C-2: 59.0 vs
59.4). WISE stores slope as a truncated integer percent (`CWFGM_Grid.cpp`,
`(std::uint16_t)slope_factor`), so a 30 % DEM plane becomes 29 %; the WISE planes were built at
30.5 / 40.5 % to compare like with like. FireSim's slope calculation is identical to cffdrs; the
remaining 3-5 % on pure upslope runs is within WISE's own implementation differences.

WISE's equilibrium rates (head 22.96 m/min, back ~1.48, LB 2.565 for C-2) equal FireSim's FBP
layer. Back distances on the grid are only a few cells long and are not resolved at 25 m.
Fixture and tests: `engine/tests/spread/data/wise_reference.json`,
`engine/tests/spread/test_wise_reference.py`. Both models share the FBP System, so this checks
the spread implementation, not the FBP System against reality.

### Building exposure radiant model

The radiant model (`docs/building-exposure.md`) reproduces Cohen's (2004) worked SIAM values for
a 50 m x 20 m flame at 1200 K: 80.6 / 45.9 / 27.8 kW/m2 at 10 / 20 / 30 m against his
79 / 45 / 27, and his flux-time example (31 kW/m2 reaches the criterion in 59 s; Cohen: about
60 s). The same front built from 1 m panels gives the same flux, and a footprint wall 20 m away
the same as a point. Tests: `engine/tests/test_exposure.py`. This checks the implementation
against SIAM, which itself overestimates measured crown-fire flux (Cohen 2000).

### Head, flank and back (deployment data)

Each burned cell of a grid run carries the front's normal speed and a head / flank / back tag
(the angle between its spread direction, from the arrival-time gradient, and the cell's FBP
head direction RAZ: within 45° head, within 45° of the opposite back). On uniform fuel at
equilibrium (`engine/tests/spread/test_deployment_data.py`):

| Fuel | FBP ROS / BROS (m/min) | Fastest head cell | Rearmost back cell | Head direction vs RAZ |
|---|---|---|---|---|
| C-2, 20 km/h, FFMC 92 | 22.96 / 1.47 | 22.96 | 1.47 | 90° vs 90° |
| O-1a 100 % cured | 49.71 / 3.91 | 49.71 | 3.91 | 90° vs 90° |
| C-2 on a 40 % slope, wind across it | — | — | — | within 2° of the slope-adjusted RAZ |

The frame's `head_ros_m_min` is the fastest head cell's speed (before 2026-10-06 it was the mean
FBP head rate of newly burned cells, which is not the front's speed).

### Classes shown in the UI

- **Head fire intensity classes 1-6** (10 / 500 / 2,000 / 4,000 / 10,000 kW/m): limits as on the
  CWFIS head fire intensity map (GeoServer layer `public:hfi`, read 2026-10-06); meanings and
  equipment paraphrased from Cole & Alexander (1995), written for C-2 on level ground (class 6 is
  their "explosive" upper portion of class 5). `frontend/src/utils/fireClasses.ts`.
- **FWI classes** (0-5 / 6-15 / 16-22 / 23-29 / 30+): CWFIS national FWI map intervals
  (GeoServer `public:fwi` legend). An FWI map class, not an official fire danger rating.
  `engine/src/firesim/fwi/classes.py`, `frontend/src/utils/fwiClass.ts`.
- Removed as unsourced (2026-10-06): an I-V intensity scheme with US resource typing, an RPAS
  stand-off distance rule and a 2 km crown-fire personnel rule.

## 4. History: what was wrong before 2026-10

An audit on 2026-10-05 (NRES 799 thesis work) found the shipped engine departed from FBP in
many places. All are fixed and each has a regression test:

| Defect | Effect |
|---|---|
| Grass curing used the 1992 form, capped near 0.71 | grass spread about 27-29 % too slow at full cure |
| No buildup effect for D-1 (and only part of M-1/M-2) | aspen/mixedwood spread off by 3-10 % |
| Surface fuel consumption a constant equal to CFL | intensity off by up to 76 % |
| Crown fraction burned `1 - sqrt(CSI/SFI)` instead of eq 58 | CFB off by up to 0.76 |
| Invented crown-density multiplier on ROS (up to 3x) | C-2 crown fires about 1.8x too fast |
| Back = head/LB², flank = head/LB | fire shape wrong; grass fire area about 3.9x too large |
| Forest LB equation used for grass | grass fires too narrow |
| Slope as a capped (2x) multiplier, not net effective wind | steep-slope spread too slow, direction wrong |
| FMC fixed at 100 %, C-4 q 0.75 | crown initiation thresholds off |
| Stochastic cellular automaton with a "heat accumulation" ignition rule | grid-mode fires up to ~16-47x too large, random die-outs |
| Huygens fires started as a 30 m circle | 30 min fires about 60 % too large |
| Fuel loader read the drone-pipeline codes 41/42 (M-1/M-2) as grass | wrong fuels on pipeline rasters |
| Grid model ignored the starting perimeter | on fuel grids, RPAS perimeter corrections and multi-day days restarted from the ignition point |
| Grid-mode "perimeter" was an unordered sample of cell centres | invalid GeoJSON export; buildings at risk always 0 |
| Synthetic demo landscape unseeded | identical scenarios gave different results (26 vs 35 ha) |
| Level-set cells joining the computational window kept stale phi | grid-mode head 6-9 % slow, worse on finer grids (found by the WISE comparison) |
| Fuel and DEM loaders stretched projected rasters over their lat/lng bounding box instead of reprojecting (fixed 2026-10-07) | Edmonton fuel cells misplaced by a median 121 m (max 332 m); the UTM DEM by a median 1.4 km (max 3.6 km); slope aspect taken from grid north. Found by the fuel-grid provenance audit |

Most of these came from the v2 code base, whose fire science was assembled from summaries
rather than the source reports, and the old tests re-implemented the same formulas, so they
could not catch the errors.

## 5. Not verified, and known limits

- **Observed fires: first results only.** [docs/validation.md](validation.md) compares one-day
  growth with 143 observed fire-days from 32 Alberta fires (Canadian Fire Spread Dataset,
  Bennett et al. 2026 protocol). Skill is modest (F1 0.15-0.24 at the default 17 h window,
  depending on initialisation) and growth is over-predicted on most days, in the same band as
  WISE. FireSim remains a preparedness, training and what-if tool (see `docs/model-card.md`).
- **Compared with WISE only on uniform fuel**, flat and on uniform slopes. Heterogeneous fuel,
  real terrain, barriers and spotting have not been compared with WISE, Burn-P3 or Cell2Fire.
- **Spotting**: maximum distance follows Albini/Chase/Morris and reproduces their published
  worked examples (Chase 1981 torching 0.34 mi; Albini 1983 surface fire 0.45 km;
  `engine/tests/spread/test_albini.py`). Emission, probability and landing distance are
  heuristic, stand sizes per fuel type are assumed, terrain is ignored and active crown fires
  are underestimated, so treat spot fire output as illustrative.
- **Burn probability** varies only ignition point, wind speed and RH; it is not a Burn-P3-style
  ensemble over historical weather and ignitions.
- **Weather**: an hourly stream is supported; hourly FFMC matches cffdrs `hffmc` (5,760 cases,
  max difference 3e-11). DMC and DC are held fixed within a run, and the forecast option depends
  on Open-Meteo's forecast quality. Without a stream, weather is constant.
- **Huygens perimeters are convex**; heterogeneous landscapes should use the grid model.
- **Fuel grids**: the Edmonton grid is the City canopy-LiDAR product at 20 m (simulated at 50 m).
  Outside Edmonton only a uniform fuel type or a synthetic demo landscape is available.
- Urban trees in the Edmonton grid are non-fuel; structure-to-structure spread is not modelled.
- **Time zone**: times are America/Edmonton. Alberta moved to permanent UTC-6 on 2026-06-18
  (IANA tzdata 2026c); software with older time zone data shows MST (UTC-7) after 2026-11-01.
  `frontend/src/utils/time.ts` handles both.
- **Water mask**: the optional OpenStreetMap water layer (`data/edmonton_water_bodies.geojson.gz`,
  2,275 polygons, 429 invalid) covers about 13,900 ha of the city, including about 3,700 ha
  that the LiDAR fuel grid maps as vegetation, and masked 17-21 % of C-2, D-2 and M-2 cells.
  It is off by default (2026-10-07); the LiDAR grid already maps water as non-fuel. A
  replacement from authoritative hydrography (City, CanVec) is pending.
- **WUI zone modifiers** (`data/wui_zones.geojson.gz`: 425 park buffers with spread x0.7,
  intensity x1.2, embers x3.0) have no documented source or generating script. They are off by
  default in the UI and should not be used for results presented as measured.

## How to reproduce

```bash
PYTHONPATH=engine/src:api/src python -m pytest engine/tests api/tests
# regenerate the cffdrs fixture (needs a cffdrs_py checkout):
CFFDRS_PY_PATH=/path/to/cffdrs_py python engine/tests/fbp/data/generate_cffdrs_reference.py
```
