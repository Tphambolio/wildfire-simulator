# Verification and limits

What has been checked, against what, and what has not. Last updated 2026-10-06.

FireSim is a **planning, training and what-if tool**. Verification here means the code solves
the FBP System's equations correctly and the spread models reproduce FBP's fire shape on
simple cases. It does not mean the predictions match real fires; that has not been tested.

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

WISE's equilibrium rates (head 22.96 m/min, back ~1.48, LB 2.565 for C-2) equal FireSim's FBP
layer. Back distances on the grid are only a few cells long and are not resolved at 25 m.
Fixture and tests: `engine/tests/spread/data/wise_reference.json`,
`engine/tests/spread/test_wise_reference.py`. Both models share the FBP System, so this checks
the spread implementation, not the FBP System against reality.

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

Most of these came from the v2 code base, whose fire science was assembled from summaries
rather than the source reports, and the old tests re-implemented the same formulas, so they
could not catch the errors.

## 5. Not verified, and known limits

- **No comparison with observed fires.** No historical fire, experimental burn or perimeter
  dataset has been run.
- **Compared with WISE only on uniform fuel.** Heterogeneous fuel, slope, barriers and spotting
  have not been compared with WISE, Burn-P3 or Cell2Fire.
- **Spotting**: maximum distance follows Albini/Chase/Morris and reproduces their published
  worked examples (Chase 1981 torching 0.34 mi; Albini 1983 surface fire 0.45 km;
  `engine/tests/spread/test_albini.py`). Emission, probability and landing distance are
  heuristic, stand sizes per fuel type are assumed, terrain is ignored and active crown fires
  are underestimated, so treat spot fire output as illustrative.
- **Burn probability** varies only ignition point, wind speed and RH; it is not a Burn-P3-style
  ensemble over historical weather and ignitions.
- **Weather** is constant through a simulation (no hourly stream, no diurnal FFMC).
- **Huygens perimeters are convex**; heterogeneous landscapes should use the grid model.
- **Fuel grids**: the Edmonton grid is the City canopy-LiDAR product at 20 m (simulated at 50 m).
  Outside Edmonton only a uniform fuel type or a synthetic demo landscape is available.
- Urban trees in the Edmonton grid are non-fuel; structure-to-structure spread is not modelled.

## How to reproduce

```bash
PYTHONPATH=engine/src:api/src python -m pytest engine/tests api/tests
# regenerate the cffdrs fixture (needs a cffdrs_py checkout):
CFFDRS_PY_PATH=/path/to/cffdrs_py python engine/tests/fbp/data/generate_cffdrs_reference.py
```
