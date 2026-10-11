# Terrain for all of Alberta

**Since:** 2026-10-11 (owner-approved task) · **Code:** `engine/src/firesim/data/terrain.py`,
`dem_loader.slope_aspect`; API `services/runner.py` (`_window_terrain`), burn probability in
`routers/simulations.py` · **Build:** `scripts/build_ab_terrain.py` · **Tests:**
`engine/tests/data/test_alberta_layers_terrain.py`, `api/tests/test_alberta_layers_terrain.py`
· **Report:** *Alberta assets roads terrain 2026-10-11* (PROJECT_RECORD [R28]).

Before this change the only terrain was the bundled Edmonton DEM, so every run outside it was
flat (open item of `docs/fuel-grid-alberta.md` §8). Mountain and foothill towns (Canmore,
Hinton, Grande Cache, Crowsnest Pass) spread upslope much faster than on the flat, so that gap
mattered most where it was largest.

## 1. Data

| Item | Value |
|---|---|
| Source | Natural Resources Canada, **Medium Resolution Digital Elevation Model (MRDEM-30)**, digital terrain model (bare earth), 30 m, EPSG:3979; CanElevation cloud-optimised GeoTIFF `https://canelevation-dem.s3.ca-central-1.amazonaws.com/mrdem-30/mrdem-30-dtm.tif` (file date 2026-06-22) |
| Licence | **Open Government Licence – Canada**. Attribution shown with runs: "Contains information licensed under the Open Government Licence – Canada (NRCan Medium Resolution Digital Elevation Model, MRDEM-30)." |
| Why MRDEM, not CDEM | MRDEM-30 is NRCan's current national DEM (it supersedes CDEM, ~20 m nominal but built from older, mixed sources); it is published as one COG that can be read by window over HTTP, so the province could be built without downloading Canada |
| File | `ab_terrain_mrdem30_100m.tif`: elevation in **decimetres, uint16** (0 = no data; `SCALE=0.1` tag), EPSG:4326, cells 0.0009° N-S (~100 m) × 0.0015° E-W (~97 m at 54.5 N), 12,333 × 6,800 cells, box 48.95-60.05 N, 120.10-109.90 W (Alberta plus a margin into BC, SK, NWT; every cell has a value), 512 px tiles, DEFLATE + predictor 2, no overviews. **91,670,525 bytes**, SHA-256 `90ba464a8e73d410ee8b0103208ad13624c1c8bc5ae5b150c0309e937ac9aeb9`. Elevation 157-3,879 m |
| Build | `python3 scripts/build_ab_terrain.py OUT_DIR` reads the source's 60 m overview by window (91 blocks of 1024 × 1024 output cells), averages it onto the grid (`Resampling.average`), writes a tiled GeoTIFF, then `gdal_translate -of COG`. 273 s, peak RSS 226 MB, ~0.5 GB downloaded. Sidecar `<file>.json` (extent, counts, SHA-256, provenance) |
| Shipping | GitHub release **`data-ab-infra-2026-10`** (owner step), fetched by `api/Dockerfile` with the SHA-256 checked (`scripts/fetch_ab_fuel_grid.py`, label "Alberta terrain"); `FIRESIM_AB_TERRAIN_PATH=/app/data/ab_terrain_mrdem30_100m.tif`. If the download fails the image still builds and runs behave as before |

**Why elevation and not precomputed slope/aspect.** Aspect is noise on flat ground (most of
the province) and compresses poorly; elevation with a horizontal predictor compresses well. Slope
and aspect are then derived on the run's own lat/lng lattice with the same central finite
differences as the Edmonton DEM (`dem_loader.slope_aspect`), so aspect is relative to true north
on both (a grid stored in a projected CRS would need a grid-convergence correction of up to ~4°
at the province's edges). Decimetres keep the elevation quantisation at 0.1 m (0.1 % slope over
100 m).

**Why ~100 m.** It matches the Alberta Wildfire fuel grid that drives runs outside Edmonton
(100 m cells read onto a 50 m lattice). Averaging 60 m cells to ~100 m smooths short steep
pitches: slopes on cliffs and narrow gullies are lower than a 30 m DEM gives (Canmore window:
mean 42 %, 90th percentile 76 %, max 238 %). A 50 m file would be ~4× larger (~350 MB); not
justified while the fuel grid is 100 m.

## 2. Selection (like the fuel grid)

| Run | Terrain |
|---|---|
| Fuel on the City grid's lattice (ignition ≥ 5 km inside the City grid's box) | **Edmonton 30 m DEM**, unchanged (`terrain_source.kind` "city_dem") |
| Provincial / national 40 km fuel window (elsewhere), request names the bundled DEM (`FIRESIM_CITY_DEM_PATH`, default `/app/data/edmonton_dem.tif`; the app sends it whenever terrain is on) and the Alberta file is installed | **Window of the Alberta file** on the fuel window's lattice ("provincial") |
| Same, without the Alberta file | the Edmonton DEM as before (flat outside Edmonton; label says so) |
| `dem_path: null` (terrain off in the app) | none (flat) |
| Any other `dem_path`, or a run that does not use the provincial default fuel | the DEM as given, unchanged |

Perimeter override, multi-day and burn probability use the same rule. Every run reports
`terrain_source` (`kind`, `label`, `attribution`) on `GET /simulations/{id}` and the
`simulation.completed` WebSocket event.

## 3. Reading

`terrain_on_lattice(path, lattice)` transforms the lattice (grown by one cell for the
finite differences) to the file's CRS, reads that window plus two cells, interpolates elevation
bilinearly onto the lattice, fills cells without data with the window mean, and computes
slope and aspect. A 40 km window at 50 m reads about 450 × 270 uint16 cells (0.25 MB) and holds
the slope and aspect rows as `array('d')` (2 × 800 × 800 × 8 B ≈ 10 MB). Windows are cached
beside the fuel windows (LRU of 2). The Edmonton DEM (≈ 76 MB as compact rows) is no longer
loaded for runs outside Edmonton when the Alberta file is installed.

Measured on the workstation: 0.1-0.2 s per window; peak RSS of a script reading four windows
(Canmore, Hinton, Slave Lake, Edmonton) 164 MB in total. Live-server figures are in the report
(§ memory) and `PROJECT_RECORD.md` §5.

## 4. Effect

Slope enters FBP as a slope-equivalent wind speed added to the wind vector (ST-X-3 eqs 39-50),
which is how the Edmonton DEM has always been used. The Canmore and Hinton before/after runs are
in the report. The check is behavioural (upslope runs faster, burned area shifts upslope), not a
validation: no observed fire in mountain terrain has been scored with the provincial terrain.

## 5. Limitations and open items

- ~100 m cells under-represent steep short slopes and ridgelines. The FBP slope factor is capped
  at 10 for slopes of 70 % or more (ST-X-3 eq 39 as implemented in `spread/slope.py`), so the
  smoothing matters most between about 20 % and 70 %.
- Elevation only: no terrain shading, valley winds or diurnal slope winds (FireSim has no wind
  model; the request wind applies everywhere).
- MRDEM is a 2026 compilation of mixed sources and dates (its own metadata); not checked against
  field survey here.
- The Edmonton DEM's exact product remains unverified (`docs/data-sources.md`); it is kept on the
  City lattice so Edmonton results do not change.
