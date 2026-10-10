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

### 1b. Daily FWI System against cffdrs (2026-10-10)

`engine/tests/fwi/test_cffdrs_fwi_reference.py`, fixture from
`engine/tests/fwi/data/generate_cffdrs_fwi_reference.py`: the cffdrs 48-day test sequence
(start 85 / 6 / 15) and 21 single-day edge cases (cold days, heavy and threshold rain, zero wind,
RH 0 / 100, codes 0 / 101), each at the FFMC coefficient 147.2 and at cffdrs's 147.27723.

| Quantity | Agreement with cffdrs at the same coefficient |
|---|---|
| FFMC, ISI, DC | float precision (≤ 1e-13) |
| DMC | exact without DMC rain; ≤ 0.064 on rain days (FireSim uses Van Wagner 1987 eq 16 as printed, cffdrs the program form `20 + 280/exp(0.023 P)`); BUI ≤ 0.066, FWI ≤ 0.02 |
| One-decimal CSV values (cffdrs R at 147.27723) | after rounding: FFMC/DMC/BUI ≤ 0.1, ISI ≤ 0.2, FWI ≤ 0.3, DC exact |

The edge cases caught the cold-day Drought Code rule (15 failures before the fix); the 48-day
sequence has no day at or below -2.8 °C.

## 2. Spread models: reproduce the FBP ellipse on uniform fuel

Uniform fuel, 20 km/h wind, FFMC 92, BUI about 50. Burned area relative to FBP.

| Model | Case | 1 h | 2 h |
|---|---|---|---|
| Huygens | C-2, no acceleration | 0.99 | 0.99 |
| Huygens | O-1a 100 % cured, no acceleration | 0.96 | 0.96 |
| Huygens | C-2, accelerating (vs eqs 73, 81) | 1.07 | 1.02 |
| Huygens | O-1a, accelerating | 1.07 | 1.02 |
| Level set, 50 m cells | C-2 / M-1 / O-1a, no acceleration (wind on a grid axis; any direction since 2026-10-09, below) | 0.96 / 0.97 / 0.91 | |
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

### Point ignitions with the wind off the grid axes (fixed 2026-10-09)

**Defect** (found 2026-10-08). A point ignition in a narrow FBP ellipse barely spread in the
grid model when the wind was off the grid axes: O-1a (grass cure 60 %, LB 4.9), 25 km/h,
FFMC 88, 50 m cells, 2 h, no acceleration burned 43 cells with the wind from 180° and 3 with
it from 225°. Fires started from a perimeter were not affected.

**Root cause.** Two numerical faults, found by printing phi after the starting ellipse and by
switching each part of the fix on and off (cells burned at 2 h, wind from 180° / 225° / 200°;
FBP ellipse 47.6 cells):

| Starting-ellipse connectivity | Starting phi | Advection | 180° | 225° | 200° |
|---|---|---|---|---|---|
| 4-connected (before) | head × (t_arrival − t0) | axis by axis | 43 | 3 | 28 |
| 4-connected | signed distance | axis by axis | 42 | 1 | 23 |
| corner links | head × (t_arrival − t0) | axis by axis | 43 | 26 | 36 |
| corner links | head × (t_arrival − t0) | axis + diagonal | 43 | 40 | 40 |
| corner links | signed distance | axis by axis | 42 | 25 | 33 |
| **corner links (after)** | **signed distance** | **axis + diagonal** | **42** | **43** | **42** |

1. *Connectivity of the starting ellipse (the gate).* The cells inside the exact starting
   ellipse were kept only if 4-connected to the ignition cell (`ndimage.label` default), so
   a needle-shaped ellipse lying along a diagonal was cut into single cells, all but the
   ignition reset to "unburned". Corner-joined cells now count as connected when a fuel cell
   that carries fire sits beside both: a narrow ellipse on a diagonal stays one piece, a
   diagonal line of non-fuel still separates, and fuel with zero spread still cannot carry
   (PR #24; D-2 below BUI 80 still does not spread, tested).
2. *Cross-wind numerical diffusion.* The level set took U·∇phi axis by axis (u_x times the
   upwind x difference plus u_y times the upwind y difference). With U on a diagonal this adds
   diffusion across the wind, which fills the valley of phi across a front only a few cells
   wide, so a grass fire thinned and its head slowed (with connectivity fixed, 4.1 m/min
   between 1 and 3 h at 225° against FBP 6.9 and 6.7 at 180°). U is now split into non-negative parts along the grid axis and grid diagonal that
   bracket it, each with its own one-sided ENO2 difference (a rotated upwind stencil); along an
   axis this is the old scheme. The diagonal neighbour is used only when it is fuel and a cell
   beside both is fuel (no leaks through diagonal walls, tested).
3. *Starting phi.* phi used to start as head × (t_arrival − t0): it has the right zero contour,
   but its other level sets are narrower copies of the ellipse, LB times steeper across the
   head axis than along it, which adds to the kink the upwind differences see on a diagonal. It
   is now the signed distance to the starting ellipse (exact, from a dense boundary sample).

Unchanged: the FBP rates and ellipse (cffdrs-verified layer), the Huygens velocity, the
starting ellipse itself, START_CELLS, the CFL number and the zero-ROS rule.

**After the fix** (`engine/tests/spread/test_cellular.py::TestRotationalInvariance`,
`::test_point_ignition_grass_diagonal_wind`, `::test_starting_ellipse_connects_through_corners_but_not_diagonal_walls`):

| Case (50 m cells) | Before | After |
|---|---|---|
| O-1a 2 h, cells for wind from 0/45/…/315° | 43 / 3 / 43 / 3 / … | 42 / 43 / 42 / 43 / … (min/max 0.98) |
| O-1a 3 h, 180° vs 225° | 97 vs 14 | 99 vs 95 |
| Area / FBP ellipse, 1 h, 20 km/h, FFMC 92, wind 270° → 225°: C-2 | 0.95 → 0.88 | 0.96 → 0.94 |
| same, M-1 | 0.97 → 0.87 | 0.97 → 0.97 |
| same, O-1a (100 % cured) | 0.89 → 0.56 | 0.91 → 0.92 |
| same with acceleration, C-2 / O-1a at 225° | 0.92 / 0.60 | 0.97 / 1.00 |
| API, uniform O-1a raster (20 m EPSG:3776, run at 50 m), 25 km/h, 2 h, wind 180° vs 225° | 235 ha vs 166 ha | 241 ha vs 248 ha |

So the old scheme was also 10-13 % short for C-2 and M-1 point ignitions on a diagonal, not
only grass. The tests require each of six wind directions (0-225° in 45° steps) to be within
12 % (grass, about 45 cells) or 5 % (C-2, about 800 cells) of their mean, and the burned
centroid within 10° of downwind; the tolerance is the cell-count quantisation of the turned
ellipse (see the test docstring). Remaining: on a pure grid axis the level set still runs about
10 % small for grass at 50 m (narrow front, numerical smoothing), and the error shrinks with
the cell size; the new stencil makes grid runs about 1.3-1.5× slower. Effect on the CFSDS validation (all 143
fire-days): held-out F1 at 17 h 0.208 → 0.212 (active edges + spin-up + 10-20 h) and
0.245 → 0.250 (Bennett start), within noise ([validation.md](validation.md)).

### Level set vs Lautenberger (2013)

FireSim's grid model is often described as "ELMFIRE-style". Checked against the published paper
only (Lautenberger 2013, *Fire Safety J.* 62: 289-298; ELMFIRE source code was not opened), the
shared part is the Eulerian level-set framework; the numerics and the spread-rate rule differ.

| Item | Lautenberger (2013) | FireSim `spread/cellular.py` | |
|---|---|---|---|
| Front representation | Scalar phi on a regular grid, front at phi = 0, advection `dphi/dt + Ux dphi/dx + Uy dphi/dy = 0` (eq 1, p.290) | Same | agree |
| Front normal | Node-centred central differences (eqs 2-4, p.290) | Mean of the two one-sided differences (= central) | agree |
| Spatial scheme | Superbee flux limiter on face values (eqs 6-9, pp.290-291) | ENO2 (min-mod of second differences), first order next to non-fuel, rotated (axis + diagonal) upwinding | differ |
| Time scheme | Second-order Runge-Kutta (eqs 10a-b, p.291); CFL-limited step | Forward Euler, Courant number 0.2 | differ |
| Initial phi | -1 burning, +1 elsewhere (p.291) | Signed distance; exact FBP point-ignition ellipse for the first 5 cells of head run | differ |
| Domain edge | Zero-gradient (p.291) | Zero-gradient at the domain and across non-fuel cells | agree |
| Normal spread rate | Rothermel head rate projected by cosines: `Vs/Vs0 = max(1, 1 + phi_s cos(theta_a - pi - theta) + phi_w cos(theta_w - pi - theta))` (eqs 13-14, p.292), i.e. not an ellipse; flanks never slower than the no-wind rate | FBP ellipse support function (Richards 1990): head ROS, flank FROS, back BROS from ST-X-3 | differ (FireSim keeps the FBP shape) |
| Slope projection | Map-plane rate reduced by `1 - |cos(theta_a - theta)|(1 - cos gamma)` (eq 15, p.292) | Slope enters only through FBP net effective wind (ST-X-3); no map-plane projection | differ |
| Acceleration | `1 - exp(-(t - t_ign)/tau)`, tau ~ 600 s (eq 12, p.291) | FBP point-ignition acceleration (ST-X-3 eqs 70-71), mean over each step | differ (FireSim follows FBP) |
| Crown fire | Van Wagner (1977) I0 (eq 17), RAC = 0.05/CBD (eq 19), crown ROS = 3.34 x FM10 (p.292) | FBP crown fraction burned (ST-X-3 eqs 56-58) | differ |
| Verification | No-wind circle, wind, slope and wind + slope vs BehavePlus on a 5 m grid (Sec. 3, pp.294-295) | Area and shape vs the exact FBP ellipse at 25-50 m (section 2) and vs WISE (section 3) | analogous tests |

So the level set reproduces the FBP ellipse (section 2) rather than ELMFIRE's cosine-projected
shape; results should not be described as ELMFIRE results. The Superbee / RK2 combination was not
adopted and is not needed for the ellipse tests above, but it is the published alternative if
ENO2 shows oscillation or front-speed problems on finer (20 m) grids.

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

Behaviour changes after the first observed-fire validation (2026-10-07, second round):

| Change | Effect |
|---|---|
| Grid model's per-cell FBP table vectorised (`fbp_ellipse_arrays`) | None on results (equal to floating-point rounding; identical scores on 143 fire-days); about 3x faster |
| A zero-rate weather period ended the grid run | Now waits for the next weather change (needed for burning periods; FBP floors ROS above zero, so default runs were unaffected) |
| Hourly records before t = 0 were all applied at t = 0 | They now only advance FFMC (spin-up); the first spread period is the record covering t = 0 |
| Ensemble ROS factor replaced the period's `ros_multiplier` | It now multiplies it (identical when no burning period is set) |
| New opt-in features | `active_edges` (observed perimeter with inactive edges burned out), `burning_period`; defaults unchanged, so the FBP-ellipse and WISE comparisons above are unchanged |

Most of these came from the v2 code base, whose fire science was assembled from summaries
rather than the source reports, and the old tests re-implemented the same formulas, so they
could not catch the errors.

## 5. Not verified, and known limits

- **Observed fires: first results only.** [docs/validation.md](validation.md) compares one-day
  growth with 143 observed fire-days from 32 Alberta fires (Canadian Fire Spread Dataset,
  Bennett et al. 2026 protocol). Skill is modest (F1 0.15-0.24 at the default 17 h window,
  depending on initialisation) and growth is over-predicted on most days, in the same band as
  WISE. With active edges, FFMC spin-up and a 10-20 h burning period the held-out F1 is 0.21
  (second round), still short of tuned WISE (0.54). FireSim remains a preparedness, training and what-if tool (see `docs/model-card.md`).
- **Compared with WISE only on uniform fuel**, flat and on uniform slopes. Heterogeneous fuel,
  real terrain, barriers and spotting have not been compared with WISE, Burn-P3 or Cell2Fire.
- **Spotting**: maximum distance follows Albini/Chase/Morris and reproduces their published
  worked examples (Chase 1981 torching 0.34 mi; Albini 1983 surface fire 0.45 km;
  `engine/tests/spread/test_albini.py`). Emission, probability and landing distance are
  heuristic, stand sizes per fuel type are assumed, terrain is ignored and active crown fires
  are underestimated, so treat spot fire output as illustrative.
- **Spotting is repeatable** (2026-10-08). Every spotting draw comes from a private
  `random.Random`, never the global `random` module. Its seed is `SimulationConfig.seed`
  (API field `seed`) or, when that is unset, a SHA-256 hash of all the other configuration
  fields (`SimulationConfig.resolved_seed`, `firesim.spread.spotting.derive_seed`), so the
  same inputs give the same spot fires in any process; setting a different seed reruns the
  same inputs with different draws. `run_cellular_simulation(seed=...)` takes the seed directly
  (default: a hash of its config and first weather period); the burn-probability and
  validation runs pass per-member seeds. Before this, spotting used the unseeded global
  generator and only the validation harness seeded it. Tests:
  `engine/tests/spread/test_cellular.py::TestSpottingRepeatability` (identical runs give
  identical spot fires; different seeds differ; global state untouched). Ensemble members get
  distinct derived seeds because their perturbed inputs differ; an explicit `seed` is shared
  by all members.
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
