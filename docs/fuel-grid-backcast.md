# Pre-fire (back-cast) fuel for historical scenarios

**Since:** 2026-10-12 (owner approval 2026-10-12) · **Code:** `engine/src/firesim/data/backcast.py`,
`provincial.py` (`FuelSources.backcast`); API `services/backcast.py`, one hook in
`services/runner.py` (`resolve_provincial`); offline build `scripts/fuelgrid/backcast.py` ·
**Tests:** `engine/tests/data/test_backcast.py`, `engine/tests/fuelgrid/test_fuelgrid_backcast.py`,
`api/tests/test_backcast_fuel.py` · **Reports:** *Pre-fire fuel grids for historical replays
2026-10-12* (design, [R39-fuel]), *Burn-scar fuel bias in validation 2026-10-12* ([R43]), *Pre-fire fuel
grid build and Slave Lake replay 2026-10-12* (build, validation, [R44]).

**Status:** heuristic. The restored fuel is typed from 30 m forest-inventory estimates
(SCANFI v2), not observed fuel. It replaces post-fire typing (grass O-1, vegetated non-fuel)
that is known to be wrong for the scenario date; it is not a reconstruction of the agency's
fuel map of that year (none is published).

## 1. The problem

FireSim's fuel outside Edmonton is the Alberta Wildfire FBP grid of 2025
(`docs/fuel-grid-alberta.md`). It re-types burns: fresh burns as vegetated non-fuel (105),
older ones largely as O-1 grass for a decade or more (31 % of 2011-2015 burns and 43 % of
2016-2018 burns are O-1, [R39-fuel] §6.2). A replay of a fire before 2025 therefore meets its
own scar, and every later scar, as post-fire fuel. The Slave Lake SWF-065 replay (2011) ran
on 80 % O-1 and overshot the documented spread (owner field tests 2026-10-12).

## 2. What a run gets

A run whose request names the City grid (the app's fuel-grid toggle) and has a `start_time`:

| Scenario date | Fuel |
|---|---|
| Undated, or after 2025 (the grid's last burn year) | unchanged; no `fuel_source.backcast` |
| 1986-2025, ignition in a provincial window (outside the City grid's box) | **back-cast**: Alberta cells with an NBAC restore key on or after the date take the pre-fire fuel of the SCANFI v2 epoch strictly before the scenario's year; `fuel_source.backcast.applied = true` |
| Same, ignition on the City lattice (Edmonton) | not back-cast (City LiDAR grid 2025; few burns); `applied = false` with the reason |
| Before the first installed epoch + 1 year, or data not installed | unchanged; `applied = false` with the reason |

**Restore key** (per 100 m cell, `ab_burn_key_100m.tif`): the latest of the NBAC burns
(1985-2025, release 2026-05-13) covering the cell centre, as `YYYYDDD` of the burn's recorded end
date (latest of agency and hotspot end dates), or `YYYY366` when no end date is recorded (most
Alberta polygons: agency end dates are 99 % missing, hotspot end dates about half). A scenario
dated `S` restores cells with key ≥ `S`: every burn of a later year, and burns of its own year
not recorded as finished before it, **including the fire being replayed** (its own scar is the
large part of the problem: every cell of its growth is in it).

**Epoch**: the newest SCANFI v2 epoch strictly before the scenario's year (2010 for 2011-2015,
2015 for 2016-2020, 2020 for 2021-2025). Strictly before, because a same-year epoch can contain
that year's burns (SCANFI uses LandTrendr-smoothed imagery). Epoch rasters hold values only for
cells burned after the epoch.

**Typing** (`scripts/fuelgrid/backcast.py type_scanfi`, rule table `RULE_NAMES`): the CFS
national FBP decision rules (Swystun, Taylor & Simpson 2018, `FBP_Fuel_Layer_Decision_Rules.pdf`)
applied to SCANFI v2 averaged to the 100 m Alberta cell (closure, height, species crown closure
by mean; land cover by mode): forest (closure ≥ 10 %) split by needleleaf share with tamarack
counted as broadleaf (> 75 % broadleaf D-1/D-2; > 75 % needleleaf conifer; else mixedwood:
closure ≤ 30 % D-1/D-2, else M-1/M-2 35 / 50 / 65 % conifer), conifer typed by leading species,
closure and height (black spruce C-1 / C-2 / C-4, with jack pine C-4 / C-2 / C-3; white spruce
and other conifers D-1/D-2 below 30 % closure, else C-2; balsam fir M-1/M-2 25 % / C-2 / C-4;
jack and lodgepole pine C-7 / M-1/M-2 50 % / C-3 / C-4; Douglas-fir, ponderosa and white pine
C-7 / C-3 / C-4 / C-5); open land: shrub M-1/M-2 25 %, other open land O-1 unless black spruce or
tamarack is among the three leading species (then D-1/D-2); water and rock non-fuel. Deviations
[H]: no ecozone layer, so the taiga rules that give C-1 (rules 7, 46, 51) are not applied and
the non-taiga branch (C-2) is; SCANFI land cover stands in for the kNN "vegetated" and NALCMS
layers. Codes are Alberta Wildfire codes, so the season swap (D-1/D-2, M-1/M-2) and per-cell
percent conifer work as for the 2025 grid.

## 3. Why this source (evidence)

- **No Alberta grid for an earlier year is published**; the national releases are 250 m or
  post-date most fires ([R39-fuel] §1-2).
- **SCANFI v2 carried the most pre-fire information** in the one pilot (Horse River 2016 vs
  pre-fire AVI: C/D/M κ 0.34 vs 0.18 for CFS 2014b and 0.15 for the 2025 grid; [R39-fuel] §6.3).
- **Licence**: SCANFI and NBAC are Open Government Licence – Canada and can ship in the image;
  CFS 2014b is an NRCan internal-use agreement and cannot. 2014b was tested only as a validation
  comparator.
- **Re-scoring of the [R27] fire-days** (pre-registered, `PREREG` in
  `scripts/validation/backcast_fire_test.py`, commit `3582102`, tag `prereg-backcast-2026-10-12`;
  [R44] §3). Held-out (16 fires, 79 fire-days), 17 h F1:
  - back-cast 0.246 vs the scar-masked 2025 grid 0.160: **ΔF1 +0.086 [+0.046, +0.128], better**
    (primary);
  - vs CFS 2014b 0.212: +0.034 [+0.007, +0.058], better;
  - the same back-cast filled from 2014b instead of SCANFI: −0.026 [−0.053, +0.001], no
    detectable difference (all 32 fires: −0.031 [−0.051, −0.012], worse).

  SCANFI was kept as the source on this evidence and on its licence.
- **Group agreement with 2014b** on cells burned 2012-2025: C/D/M 0.59, κ 0.28. The back-cast is
  more mixedwood (36 % vs 21 %) and less conifer (50 % vs 61 %) ([R44] Figure 4).

## 4. Data and deployment

Built offline (`python -m scripts.fuelgrid.backcast burns | epoch E… | manifest | package`),
outputs in `$PREFIRE_DATA/out` (default `~/dev/wildfire/prefire-data/out`), shipped as one tar in
GitHub release `data-ab-prefire-v1`, fetched and SHA-256-checked by `api/Dockerfile` into
`/app/data/backcast` (`FIRESIM_AB_BACKCAST_DIR`). It is part of the image, not the Fly volume,
so it adds no storage cost. Reads are windowed (40 km window: two extra 100 m reads, well under
1 MB); the window cache key includes the scenario date.

SCANFI is read from NRCan's server once into a local sparse mirror
(`python -m scripts.fuelgrid.backcast mirror E`): only the 256 × 256 blocks under Alberta cells
burned after the epoch (2010: 5,115 blocks; 2015: 4,105; 2020: 3,166; under 1 MB/s from the
server). Typing then reads the mirror: 9-17 min per epoch, peak 1.7 GB.

Installed: the burn key (5.1 MB) and epochs 2010, 2015, 2020 (5.6, 4.5, 3.6 MB); release tar
18.7 MB. Epochs 1985-2005 are not built yet, so scenarios before 2011 are not back-cast and say so.

## 5. Limits

- Typing is untested against observed pre-fire fuel beyond the group-level pilot; SCANFI's
  mixedwood bias in Alberta ([R26]) carries over.
- Burns **before** the scenario stay at their 2025 state (no young-burn rule); harvest since the
  scenario is not restored (CanLaD / AVI cut blocks: open item).
- A fire burning on the scenario date is restored in full, including what had already burned;
  a restart from an observed perimeter covers that ground.
- 5-year epochs: a cell that burned between the epoch and the scenario and again after it is
  restored to the forest before both burns.
- City LiDAR cells and grids outside Alberta are not back-cast.
- Narrow features at 100 m: at Slave Lake the modelled head runs along a 100 m band of O-1 grass
  beside Highway 2, outside the NBAC burn, so it is not back-cast ([R44] §4).
- Town and building fuels are unchanged (the Flat Top report attributes part of the 2011
  Prometheus under-prediction to residential areas mapped as non-fuel).
