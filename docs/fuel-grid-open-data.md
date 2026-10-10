# Open-data FBP fuel grid (no LiDAR, no municipal inventory)

**Version:** 1.0.0 (grids `v20261010`) and **2.0.0** (full FBP key + disturbance, stand-level
typing, northern Alberta test areas; §9) · **Code:** `scripts/fuelgrid/` · **Tests:** `engine/tests/fuelgrid/`
**Status:** research product. It has been compared cell by cell with the City of Edmonton LiDAR
fuel grid, which has itself never been field-checked. Nothing here is a field-validated accuracy.

This document follows the structure of the BC Wildfire Service fuel-type layer report (BCWS
2017): inputs, decision key, percent-conifer table, seasonal rule, validation, and uses and
limitations.

## 1. What it is

A reproducible pipeline that builds a 20 m FBP fuel-type grid in EPSG:3776 from open data only:
satellite canopy height, Sentinel-2 seasonal composites, national forest attributes, crop
inventory, building footprints and OpenStreetMap. It needs no LiDAR and no municipal vegetation
inventory, so it can be run for any Alberta municipality. The City LiDAR crowns are used **only in
Edmonton**, as training labels for the conifer-fraction model and as the comparison grid.

It also fixes, in its own design, the Edmonton grid issues listed in the 2026-10-07 fuel-grid
audit: the inventory-polygon gate (replaced by a contiguous-stand rule), no accuracy statement
(spatial-block hold-out against the LiDAR grid and the CFS baseline), leaf-on all year (leaf-on
and leaf-off grids with a documented switch), no embedded metadata (GeoTIFF tags and a JSON
sidecar), the wrong filename (names carry AOI, product, season, resolution, CRS and version), the
unread percent-conifer raster (percent conifer is written per cell and carried inside the CIFFC
mixedwood codes), and nearest-neighbour coarsening (outputs are native 20 m; nothing is resampled).

## 2. Products

Written to `$FUELGRID_DATA/out/` (default `~/dev/wildfire/fuelgrid-data/out/`), never to the repo.

| File | Content | Load in FireSim |
|---|---|---|
| `{aoi}_fbp_opendata_leafon_20m_3776_v{date}.tif` | Leaf-on grid, `canopy_lidar` codes 2 C-2, 12 D-2, 14 M-2, 31 O-1a, 32 O-1b, 98 water, 99 non-fuel, 0 no data | `load_fuel_grid(path)` (auto-detected, same codes as the City grid) |
| `{aoi}_fbp_opendata_leafon_ciffc_20m_3776_v{date}.tif` | Leaf-on grid, CIFFC codes: 2 C-2, 12 D-2, 5xx M-2 with xx % conifer, 31, 32, 101 non-fuel, 102 water | `load_fuel_grid(path, code_scheme="cfs_national")` |
| `{aoi}_fbp_opendata_leafoff_ciffc_20m_3776_v{date}.tif` | Leaf-off grid: 11 D-1, 4xx M-1 with xx % conifer, rest as above | `load_fuel_grid(path, code_scheme="cfs_national")` |
| `{aoi}_percent_conifer_20m_3776_v{date}.tif` | Predicted percent conifer (0-100) in C-2 / D-2 / M-2 cells, −1 elsewhere | (not read by the engine yet) |
| `{aoi}_fbp_opendata_rule_20m_3776_v{date}.tif` | Decision-key rule that set each cell (§4), the per-cell provenance band | — |
| `{aoi}_fbp_opendata_20m_3776_v{date}.json` | Sidecar: all tags, parameters, rules, sources with licences and retrieval records | — |

Every GeoTIFF carries tags: `product`, `aoi`, `season`, `code_scheme`, `codes`, `resolution_m`,
`crs`, `version`, `date_built`, `generator` (FireSim commit), `sources` (title and licence of
every input), `decision_key`, `params`, `seasonal_rule`.

AOIs: **Edmonton** on exactly the City LiDAR grid (1461 × 2115 cells, origin 18940 E / 5953940 N),
cells inside the OSM city boundary; **St. Albert** inside its OSM boundary plus a 3 km buffer
(796 × 790 cells, origin 15820 E / 5953600 N).

The `canopy_lidar` scheme has no leafless codes, so the leaf-off grid exists only in CIFFC codes.
The engine does not yet read percent conifer per cell (it uses the request value), so the PC carried
in 4xx/5xx codes is preserved but not used until the engine is extended.

## 3. Inputs

All open; no account, key or e-mail needed. Downloads go to `$FUELGRID_DATA/raw/`; every retrieval
is logged with date in `$FUELGRID_DATA/provenance.json`.

| Input | Version / date | Licence | Use | URL |
|---|---|---|---|---|
| Meta / WRI 1 m Global Canopy Height (Tolan et al. 2024), tile 021211312 | v1 (`alsgedi_global_v6_float`); imagery 2015-2020, mostly 2020 | CC BY 4.0 | Canopy cover (share of 1 m pixels ≥ 5 m) and mean tree height per 20 m cell; stand delineation | `s3://dataforgood-fb-data/forests/v1/alsgedi_global_v6_float/` (HTTPS) |
| ESA WorldCover 10 m (Zanaga et al. 2022), tile N51W114 | v200, 2021 imagery | CC BY 4.0 | Water, built-up, grass/shrub/wetland fractions per cell | `esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/` |
| Sentinel-2 L2A (Copernicus), Element84 Earth Search STAC | 98 scenes (49 dates): spring leaf-off 12 dates (Apr 13 - May 3, 2023-26), autumn leaf-off 8 (Oct 18 - Nov 6, 2023-25), summer 17 (Jul-Aug 2024-25), snow-on winter 12 (Feb 17 - Mar 20, 2023-26); cloud < 20 %; scene IDs in provenance | Copernicus Sentinel data terms (free, full, open) | Per-season median reflectance (7 bands) and indices; the evergreen signal | `earth-search.aws.element84.com/v1` |
| SCANFI v2 (Guindon et al. 2026) | `v2_20260119`, year 2025 | OGL – Canada | Crown closure, height, broadleaf and conifer species crown closure (features; untrained baseline) | `ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/` |
| SCANFI v3 fractional land cover | `v3_20260528`, year 2025 | OGL – Canada | Treed coniferous / broadleaf and shrub fractions (features) | `…/SCANFI/v3/` |
| AAFC Annual Crop Inventory | 2025, 30 m | OGL – Canada | Crop and pasture → O-1a; grassland, shrubland, wetland → O-1b; urban | `agriculture.canada.ca/imagery-images/rest/services/annual_crop_inventory/2025/ImageServer` |
| Microsoft Global ML Building Footprints, Canada, quadkey 021211312 | release 2026-02-03 | ODbL 1.0 | Building footprint fraction per cell (built context) | `minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv` |
| OpenStreetMap Alberta extract (Geofabrik) | 2026-10-09 extract | ODbL 1.0 | landuse / natural / leisure polygons: maintained urban land vs natural open land | `download.geofabrik.de/north-america/canada/alberta-latest.osm.pbf` |
| OpenStreetMap boundaries via Nominatim lookup | Edmonton R2564500, St. Albert R11890349, retrieved 2026-10-10 | ODbL 1.0 | AOI masks | `nominatim.openstreetmap.org/lookup` |
| CFS National FBP Fuel Type Layer – Current Year | 2026, 30 m | OGL – Canada | **Comparison only** (baseline), not an input | `open.canada.ca` record `851e6a27-…` |
| City of Edmonton LiDAR crowns (2025 leaf-on LiDAR, v2 conifer classifier) | 5.16 M crowns | City internal data — **not redistributed**; read locally from `$FUELGRID_LIDAR_DIR` | Edmonton training labels and comparison grid only; aggregate statistics only | — |

Choice of canopy source. The Meta 1 m map resolves single crowns and gaps, which a 20 m stand rule
needs, and is CC BY 4.0. ETH's 10 m global canopy height (Lang et al. 2023) is coarser than the cell
and WorldCover's tree class has no height; both were rejected for stand delineation. Meta's map
under-reads cover outside stands (mean ≥ 5 m cover 0.05 in City non-fuel cells against 0.18 from
the LiDAR crowns) and its imagery is up to five years older than the 2025 LiDAR, but inside
stands it reads higher (0.58-0.70 against 0.44-0.48), so a 40 % stand threshold is met where the
LiDAR has stands. Correlation with LiDAR crown cover per cell: r = 0.52.

## 4. Decision key

Inputs are first put on the 20 m grid: area-weighted fractions for WorldCover, footprints (4 m
sub-cells) and the 1 m canopy model; bilinear for SCANFI; nearest for the crop inventory and
OSM categories (rasterised by priority: farmland < urban < maintained < natural open < wood < water).
The key is ordered; the first rule that matches sets the class. Rule numbers are written to the
rule raster.

| # | Rule | Class |
|---|---|---|
| 1 | WorldCover water ≥ 50 % of the cell | Water (non-fuel) |
| 2 | Cell in a **treed stand** and its own canopy ≥ 10 % | C-2 / M-2 / D-2 by conifer fraction |
| 3 | Cell in a treed stand, canopy < 10 % (gap) | O-1b |
| 4 | OSM natural=scrub/heath/grassland/wetland or landuse=meadow, not built | O-1b |
| 5 | Built (below), OSM urban landuse (residential, commercial, industrial, …), OSM maintained land (parks, sports fields, golf, cemeteries, landuse=grass, …), or ACI urban | Non-fuel |
| 6 | AAFC annual crop, or pasture/forage (122), or OSM farmland | O-1a |
| 7 | AAFC grassland/shrubland/wetland/peatland/forest, or WorldCover tree+shrub+grass+wetland+moss ≥ 50 %, or OSM wood | O-1b |
| 8 | Anything else (bare, unclassified) | Non-fuel |

**Treed stand (replaces the uPLVI polygon gate).** A stand is an 8-connected patch of cells whose
3 × 3 mean canopy cover (CHM ≥ 5 m) is ≥ 40 % and that are not built, with area ≥ 1 ha. The 40 %
cut sits inside the VRI "open" crown-closure class and well above its 10 % "non-treed" limit
(BCWS 2017); 10 % is used inside stands to separate treed from open cells. The 1 ha minimum is
the audit's stand size. A cell is **built** if its own building-footprint share is ≥ 2 %, the
footprint share of its 3 × 3 (60 m) neighbourhood is ≥ 10 %, or WorldCover built-up is ≥ 50 %.
Canopy over houses, yards and pavement therefore never forms or joins a stand: urban trees stay
non-fuel (standard practice, BCWS 2017; NRC 2021), while ravine, riparian and park woodlands are
typed by their vegetation whether or not an inventory polygon covers them. All parameters were
fixed before the Edmonton comparison (`config.PARAMS`).

**Conifer fraction.** A LightGBM regression predicts the share of conifer crowns in a cell from
88 open-data features: per-season Sentinel-2 bands and indices (NDVI, NDMI, NDRE, NBR, NDSI, raw
and 3 × 3 means; summer minus spring, autumn and winter differences), SCANFI v2 crown closure,
height and species crown closure, SCANFI v3 fractions, and the canopy model's cover and height.
The strongest features are leaf-off contrasts: summer − autumn NDVI and summer − snow-on winter
NDVI, then tree height. Labels: the City LiDAR crowns aggregated to the 20 m cell, conifer share by
count (crowns without a classifier label count as deciduous), exactly the City grid's definition,
on 136,233 Edmonton cells with ≥ 2 crowns inside City forest cells or open-data stands.
A regression toward the mean almost never predicts ≥ 0.75, so its output is **quantile-mapped**
onto the training-label distribution (both CDFs from the training cells only) before the fixed
cuts are applied. This was chosen by spatial-block CV on the LiDAR labels (C/M/D κ 0.24 with
plain thresholds, 0.28 mapped, 0.28 for a three-class classifier that finds few C-2), not on the
grid comparison.

**Typing.** Conifer fraction ≥ 0.75 → C-2, ≤ 0.25 → D-2, otherwise M-2, the City grid's cuts.
C-2 rather than C-3 because white spruce is the local conifer (SCANFI v2 pine crown closure
averages under 0.2 % over the region). Rough pasture and forage (ACI 122) are O-1a with crops; natural grass and shrub
are O-1b.

**Spatial hold-out.** Edmonton is cut into 2 km blocks dealt at random (seed 20261010) to five folds.
Every Edmonton cell's conifer fraction comes from the model that did not see its block, so every
Edmonton number below is held out. St. Albert uses one model fitted on all Edmonton labels.

### Percent conifer (BCWS-style frequency table, M-2 cells, area in ha)

| PC | 26-34 | 35-44 | 45-54 | 55-64 | 65-74 | median |
|---|---:|---:|---:|---:|---:|---:|
| Edmonton | 407 | 368 | 291 | 258 | 188 | 44 |
| St. Albert + 3 km | 128 | 101 | 72 | 53 | 29 | 41 |

## 5. Seasonal switch

| Season | Day of year (default) | Grid |
|---|---|---|
| Snow-melt to green-up | < 150 | leaf-off (D-1, M-1 with the same percent conifer) |
| Green-up to leaf fall | 150 - 257 | leaf-on (D-2, M-2) |
| Leaf fall to snow | ≥ 258 | leaf-off |

FBP software switches "between the 'leafless' and the 'green'… fuel type on the estimated date of
deciduous bud-flush" (BCWS 2017); D-1/M-1 are the leafless benchmark types (ST-X-3;
GLC-X-10). The defaults are FireSim's existing `greenup_doy` / `leafoff_doy` (validation harness)
so that the engine has one rule. The interface is
`scripts.fuelgrid.rules.season_for_doy(doy, greenup_doy=150, leafoff_doy=258)` → `"leafon"` or
`"leafoff"`, then load the matching file. **Not wired into the engine in this PR** (follow-up,
PROJECT_RECORD §6). Near the switch date, or when green-up is uncertain, run both grids and average
the predictions, never the fuels (mechanics decision M5; Bennett et al. 2024). A local green-up
date from Sentinel-2 phenology could later replace the fixed day.

## 6. Validation

Cells compared: 1,961,051 (78,442 ha) inside both the OSM boundary and the City grid. Agreement
with the LiDAR grid is **not accuracy**: that grid has never been field-checked, its forest types
disagree with uPLVI stand calls (audit), and part of the disagreement is by design (§6.3).

### 6.1 Edmonton, open-data vs City LiDAR grid (held-out conifer fractions)

| Comparison | Open-data OA | κ | CFS 2026 OA | κ |
|---|---:|---:|---:|---:|
| Six classes (C-2, D-2, M-2, O-1a, O-1b, NF) | **0.767** | **0.561** | 0.681 | 0.445 |
| Five groups (conifer, deciduous, mixed, grass, NF) | **0.801** | **0.599** | 0.695 | 0.450 |
| Fuel vs non-fuel | **0.836** | **0.646** | 0.797 | 0.572 |
| C/D/M where both call forest | **0.586** | **0.288** | 0.460 | 0.080 |

CFS five groups with code 13 excluded: OA 0.720, κ 0.477 (the audit's 0.72 / 0.48, reproduced);
counting code 13 as disagreement, OA 0.694. Code 13 covers 2,767 ha of the city.

Per class (open-data, six classes): C-2 precision 0.29 / recall 0.22; D-2 0.50 / 0.46; M-2 0.39 /
0.34; O-1a 0.66 / 0.79; O-1b 0.37 / 0.18; non-fuel 0.86 / 0.88. CFS: C-2 0.07 / 0.27; D-2
0.12 / 0.62; M-2 0.14 / 0.18; O-1a 0.69 / 0.68; O-1b (none mapped); non-fuel 0.86 / 0.81.

**Spatial-block folds** (five-group κ, open-data / CFS): 0.621 / 0.496, 0.628 / 0.488, 0.677 / 0.490,
0.512 / 0.382, 0.552 / 0.383. The open-data grid beats CFS in every fold (six-class κ range 0.49-0.61
against 0.38-0.49). In-sample conifer fractions change the five-group κ only from 0.599 to 0.602
(forest-type κ 0.288 → 0.347): the land-cover gate drives the grid agreement; the trained part
matters for C/D/M.

**Conifer fraction at the cell (136,233 labelled cells, held out):** C/M/D κ 0.276 (folds 0.259-0.293),
OA 0.573, recall C/M/D 0.37 / 0.52 / 0.67, precision 0.39 / 0.51 / 0.67, MAE 0.21. Unmapped
regression: R² 0.27, MAE 0.20, but C-2 recall 0.04. Untrained SCANFI v2 conifer share: κ 0.085,
C-2 recall 0.11. Label noise bounds this: a 3 × 3 mean of the LiDAR labels reproduces the cells'
own C/M/D class with only κ 0.48.

**Ablation without Edmonton training** (SCANFI conifer share for typing, same gate): five groups
0.798 / 0.593, forest type where both forest κ 0.099. The gate transfers without training;
conifer typing needs the leaf-off Sentinel-2 signal and local labels.

### 6.2 Areas (ha, Edmonton)

| Class | LiDAR grid | Open-data leaf-on | Open-data leaf-off | CFS 2026 |
|---|---:|---:|---:|---:|
| C-2 / conifer | 506 | 391 | 391 | 2,081 |
| D-2 (D-1) / deciduous | 2,129 | 1,934 | 1,934 (D-1) | 7,945 (+2,767 code 13) |
| M-2 (M-1) / mixedwood | 1,746 | 1,514 | 1,514 (M-1) | 2,263 |
| O-1a | 16,984 | 20,329 | 20,329 | 16,722 (O-1, all classes) |
| O-1b | 7,644 | 3,615 | 3,615 | — |
| Non-fuel + water | 49,434 | 50,658 (1,663 water) | 50,658 | 46,665 (1,678 water) |

### 6.3 Contiguous-stand effect

The audit found 1,331 ha of LiDAR stands (≥ 1 ha at ≥ 40 % cover) that the City grid maps as
non-fuel; reproduced here exactly (1,331 ha of 4,080 ha). The open-data stand rule types 172 ha of
them as forest and 23 ha as stand gaps (O-1b). The rest is **built context** (1,060 ha within 60 m
of buildings, i.e. canopy over residential lots, which the rule keeps non-fuel by design) and 79 ha
where the satellite canopy model reads < 40 %.
Overall the open-data grid maps 3,839 ha of forest, of which 443 ha is non-fuel and 524 ha grass on
the LiDAR grid; 467 ha of LiDAR forest is non-fuel and 1,042 ha grass on the open-data grid.

**Leaf-off variant:** D-1 1,934 ha, M-1 1,514 ha in Edmonton; C-2 unchanged.

### 6.4 A gap in the LiDAR grid

South of northing 5,914,940 m (EPSG:3776; about 41 Ave SW, the 2019 annexation) the LiDAR grid is
99.6 % non-fuel over 3,173 ha, while the 2025 crop inventory maps 86 % crop and CFS 73 % grass;
the open-data grid has 82 % O-1a there. This looks like an inventory-coverage gap in the City grid
(inferred; uPLVI vintage not recorded). Excluding the zone (found on the map, so post hoc): open-data
five groups 0.830 / κ 0.657, CFS 0.718 / 0.492.

### 6.5 St. Albert (no reference grid)

Areas inside the boundary (6,524 ha): C-2 7, D-2 219, M-2 107, O-1a 1,843, O-1b 377, non-fuel
3,943, water 29 ha (leaf-off: D-1 219, M-1 107). With the 3 km buffer (19,413 ha): C-2 52, D-2 637,
M-2 384, O-1a 8,766, O-1b 1,290, non-fuel 7,804, water 480 ha. CFS 2026 inside the boundary:
conifer 195, deciduous 784 (+283 code 13), mixedwood 195, grass 1,824, non-fuel 3,243 ha.
Agreement with CFS (five groups, code 13 as deciduous): inside 0.713 / κ 0.519; with buffer 0.697 /
0.525. This is agreement between two maps, not accuracy. The only error estimate that transfers is
Edmonton's held-out one, and St. Albert's stands, wetlands (Big Lake) and greenfield land differ
from Edmonton's training domain. A list of 26 field-check sites (largest disagreements with CFS,
stands near a C/M/D cut, largest patch of each forest type) is written to
`validation/st_albert_ground_truth_sites.csv`.

### 6.6 FireSim engine check

Every grid loads (`canopy_lidar` auto-detected; CIFFC with `code_scheme="cfs_national"`) at the
native 20 m and spreads (2 h, FFMC 92, BUI ≈ 60, 20 km/h): O-1b 29-53 ha, C-2 20-46 ha; D-2 does not
spread at this BUI (FBP buildup effect, as the audit found) while D-1 on the leaf-off grid burns
9-11 ha. Results in `validation/firesim_check.json`.

## 7. Uses and limitations

**Use it for:** wildland spread in municipalities with no LiDAR fuel grid (St. Albert first); a
second, independent fuel map for Edmonton ensembles (average predictions across maps, M5);
seasonal (leaf-off) runs; locating where field checks matter most.

**Do not use it for:** stand-level C-2 / M-2 / D-2 calls without field checking (cell-level
conifer typing κ ≈ 0.28 against noisy LiDAR labels); structure-to-structure spread (urban canopy
and lawns are non-fuel by assumption; buildings belong to the separate structure layer); any claim
of accuracy beyond the Edmonton held-out agreement; areas outside the region grid without re-running
the fetch; C-3/C-1/slash or post-fire fuels (not mapped); claims about curing (O-1a/O-1b curing is a
run input).

**Known limitations:**
- No field validation; the comparison grid is itself unvalidated.
- Grass versus non-fuel is the largest disagreement (O-1b recall 0.18): maintained versus
  unmaintained grass is not visible to these sensors, and OSM park and greenfield tags decide much
  of it. Greenfield land awaiting development is non-fuel here (St. Albert site 1, 164 ha) and
  should be checked.
- Isolated canopy near buildings is non-fuel by design (the 1,060 ha above); ember and torching
  sources from urban conifers belong to the planned tree layer (M4/M6), not to this grid.
- The canopy model's imagery is 2015-2020; recent clearing or growth is missed. Leaf-off Sentinel-2
  composites mix 2023-2026.
- Water from WorldCover misses narrow channels; OSM water is deliberately not used (audit).
- St. Albert conifer typing is a transfer from Edmonton; no local labels.
- Only one comparison resolution (20 m); outputs inherit the 20 m cell (Taneja et al. 2021).

## 8. Reproduce

```bash
# from the repo root; ~12 min once the ~3.8 GB of downloads are cached (3.1 GB of it is the CFS
# comparison layer); 4.0 GB in $FUELGRID_DATA. A rerun from an empty folder with cached downloads
# reproduced every grid and validation number bit for bit (2026-10-10).
python -m scripts.fuelgrid.run all          # fetch -> labels -> build -> validate
PYTHONPATH=engine/src python -m scripts.fuelgrid.run firesim   # optional engine check
```

`labels` needs the City crown tables (`FUELGRID_LIDAR_DIR`); without them only St. Albert-type
runs with a saved model are possible (not implemented in v1). Stages are idempotent; `--force`
refetches. Dependencies beyond the engine: geopandas, pyogrio, lightgbm, matplotlib, pyproj.

**Shipping.** Do not commit the rasters. Proposed: attach the six files per AOI (~1 MB total) and
the JSON sidecar to a GitHub release `fuelgrid-v20261010`, and point `FIRESIM_FUEL_GRID_PATH` at
the downloaded file. Keep the City LiDAR grid as the Edmonton default until a field check says
which grid is better.

## 9. Version 2: full FBP decision key, disturbance, stand-level typing (2.0.0)

v2 extends the six-class key to the FBP types that can be mapped from open data, adds burn scars
and cut blocks, types forest at the stand scale, and runs outside Edmonton. v1 files, code tables
and parameters are unchanged (a test pins them), so `v20261010` still rebuilds bit for bit.
Code: `key2.py` (decision key, pure numpy), `sources2.py` (inputs on any region grid),
`build2.py`, `validate2.py`; `python -m scripts.fuelgrid.run all2` (needs the v1 build), then
`PYTHONPATH=engine/src python -m scripts.fuelgrid.run firesim2`.

### 9.1 Products

`$FUELGRID_DATA/out/v2/{aoi}_fbp_opendata2_{kind}_20m_{epsg}_v{date}.tif`, kind =
`leafon_ciffc`, `leafoff_ciffc` (CIFFC codes, load with `code_scheme="cfs_national"`: 1 C-1, 2 C-2,
3 C-3, 4 C-4, 7 C-7, 11 D-1, 12 D-2, 21 S-1, 22 S-2, 31 O-1a, 32 O-1b, 4xx M-1 / 5xx M-2 with
xx % conifer, 101 non-fuel, 102 water), `rule` (v2 rule per cell), `percent_conifer` (band 1:
20 m continuous percent conifer; band 2: decision-unit percent conifer used for typing), and a
`meta` JSON sidecar. The `canopy_lidar` scheme has no codes for the new types, so v2 is CIFFC only.
AOIs: Edmonton and St. Albert (EPSG:3776, map year 2026) and three northern Alberta test areas
(EPSG:3400, Alberta 10-TM Forest; §9.4). Provenance of every v2 retrieval:
`$FUELGRID_DATA/provenance_v2.json` (v1's `provenance.json` is not rewritten).

### 9.2 Decision key (ordered; first match wins; rule numbers in the rule raster)

Sources: **CFS 2018** = Swystun, Taylor & Simpson, *FBP Fuel Layer Decision Rules* (14 Nov 2018,
the rules behind CanFG 2019); **NRCan 2026** = LaCarte et al., *FBP Forest Fuel Type Mapping:
Overview of the Methodology, Data Sources, and Uses & Limitations* (Cat. Fo4-268/2026E-PDF; the
CFS 30 m layer), printed page numbers; **Perrakis 2015** = Perrakis & Eade, *BC Wildfire Fuel
Typing and Fuel Type Layer Description, 2015 Version* (BCWS), printed page numbers. **[H]** = a
FireSim choice where no source fixes the value. No published Alberta Wildfire typing key was found
in open sources; Alberta practice enters only through the AVI comparison (§9.5).

| # | Rule | Class | Source |
|---|---|---|---|
| 1, 3-8 | v1 land-cover gate (water, built/urban, crop/pasture, open land, stand gaps; §4) | as v1 | §4 |
| — | **Forest extent at 20 m:** v1 contiguous stands (Meta CHM). Test areas only: also SCANFI v3 treed cover through the same stand rule (≥ 40 % 3×3, ≥ 1 ha, not built) on cells the v1 gate left open | — | [H] (§9.3) |
| — | **Decision unit:** every evidence layer is averaged over the forest cells of each 100 m block (5 × 5 cells); typing uses the means; the 20 m extent is kept | — | conifer-mapping report 2026-10-10 §2, §4; provinces map at ~100 m (SCANFI, Guindon et al. 2024 p.26) |
| — | Conifer share: tamarack counted as deciduous | — | CFS 2018 p.1 |
| 25 / 65 | Unit conifer ≤ 25 % | D-2 (leaf-off D-1) | CFS 2018 p.1 (> 75 % broadleaf = deciduous); Perrakis 2015 App. A4 p.59 (%C ≤ 20 → D-1/2). **[H] kept as D-2**, not low-PC M-2 (below) |
| 26 / 66 | Unit conifer 25-75 % | M-2 (leaf-off M-1) with unit PC | CFS 2018 p.1; percent conifer carried (CIFFC 5xx) |
| 24 / 64 | Conifer ≥ 75 % and (ponderosa ≥ 30 %, or ponderosa ≥ 20 % with Douglas-fir ≥ 25 %, or Douglas-fir ≥ 20 % with closure ≤ 40 %) | C-7 | NRCan 2026 rules 4.h, 4.i (p.8); CFS 2018 rule 36 (p.3) |
| 22 / 62 | Conifer ≥ 75 %, jack + lodgepole pine ≥ 25 % of the unit, height < 12 m, 2 m+ cover ≥ 60 % | C-4 | NRCan 2026 rules 4.e/4.f (p.8: > 25 % pine, < 12 m); density cut **[H]** from Perrakis 2015 p.19 ("dense (> 60 % crown closure)"; C-4 behaviour uncommon) |
| 23 / 63 | Conifer ≥ 75 %, pine ≥ 25 %, otherwise | C-3 | NRCan 2026 rules 4.e/4.f (p.8); Perrakis 2015 p.18 |
| 21 / 61 | Conifer ≥ 75 %, black spruce ≥ 75 %, lichen ≥ 1 % (SCANFI v3 non-treed lichen), closure < 60 % | C-1 | NRCan 2026 rule 2.d (p.7) |
| 20 / 60 | Conifer ≥ 75 %, otherwise (white / black spruce, fir) | C-2 | NRCan 2026 rule 2.b (p.7); Perrakis 2015 p.18 |
| 30 | Outside stands (v1 rules 4/7, not built): 2 m+ cover ≥ 10 %, SCANFI treed-coniferous ≥ 25 % and ≥ 3 × broadleaf, black spruce ≥ 75 % with lichen ≥ 1 % | C-1 | NRCan 2026 rule 2.d (p.7); thresholds **[H]** |
| 31 | Same open-conifer cells otherwise (sparse conifer, treed peatland) | M-1/M-2, 25 % conifer | NRCan 2026 rule 1.e (p.7: coniferous, closure < 30 % → M1-25 %) |
| 32 | v1 rule-7 cells with WorldCover herbaceous wetland + moss ≥ 50 %, or AAFC wetland / peatland | O-1b (relabel only) | **[H]**: open fen/bog as standing grass. Alternatives in the sources: non-fuel/water for saturated bogs (Perrakis 2015 §5.4.4 p.25), "wetland, FBP type unknown" (CFS 2014b code 120), O-1a for herb/bryoid (NRCan 2026 rule 1.a p.7) |
| 40 | Forest cell burned 1-2 years before the map year | non-fuel | NRCan 2026 Table 3 (p.17) |
| 41 | Burned 3-5 years before | D-2 (leaf-off D-1) | idem |
| 42 | Burned 6-21 years before | M-2 25 % conifer | idem; > 21 years: the vegetation inputs decide **[H]** (NRCan 2026 applies Table 3 only to post-2020 fires, p.16-17; FireSim applies it to every burn ≤ 21 years because the canopy imagery predates many of them). O-1, non-fuel and water are kept (p.17) |
| 50 | Forest loss (Hansen) not inside a burn of ±1 year, 1-5 years before, pre-harvest pine ≥ 50 % of conifer | S-1 | slash for the first 5 years: Perrakis 2015 §5.4.3 p.24 (App. A4 p.56: pine logged ≤ 7 yr → S-1); fire attribution and pine share **[H]** |
| 51 | Same, spruce / fir / mixed | S-2 | Perrakis 2015 App. A4 p.58 (Sb/Sw/Se logged ≤ 10 yr → S-2) |
| 52 | Same, pre-harvest conifer ≤ 25 % (aspen block) | D-2 | **[H]** (no FBP aspen slash; aspen suckers within 2-3 years) |
| 53 | Harvest 6-24 years before, cell not treed again (v1 rules 3/7/8) | O-1b | Perrakis 2015 App. A4 p.55 (non-vegetated, 7-24 yr, dry zones incl. BWBS → O-1a/b); treed regrowth is typed by the inputs |

Harvest rules need a treed block before the cut (SCANFI closure ≥ 25 % in the epoch before the
harvest year) and never apply in urban (rule 5) or crop (rule 6) cells **[H]**; the more recent of
fire and harvest wins. Where the canopy image (Meta CHM acquisition year, per metadata polygon)
predates a later fire or forest loss, cover and height come from SCANFI's epoch instead **[H]**.

**Stand-level typing (adopted from the conifer-mapping report, 2026-10-10).** At 20 m the City
crown labels agree with themselves (split-half) at only κ 0.33, against 0.72 at 100 m; averaging the
20 m prediction to stands or 100 m blocks and re-mapping there roughly doubles agreement, and at
stand scale C-2 is only 0.6-2 % of Edmonton's forest. v2 therefore: (1) keeps the continuous 20 m
percent conifer (band 1 of the percent-conifer raster); (2) averages it over the forest cells of
each 100 m block and, in Edmonton, re-maps the block means to the block label distribution
**out of fold** (the map for each 2 km spatial fold is fitted on blocks with ≥ 50 crowns in the
other folds; St. Albert uses the table fitted on all Edmonton blocks); (3) carries the block
percent conifer through M-1/M-2 and calls C-2 only where the block mean is ≥ 0.75; the pine/spruce
split and the C-1/C-3/C-4/C-7 calls use block means of species shares, height, cover, closure and
lichen. (4) **D-2 below 25 % conifer is kept** [H]: it is the FBP/CFS/BC practice (above), and the
engine's D-2 does not spread below BUI ≈ 80 while M-2 at low PC would burn every aspen stand —
the report measured that hedge as +0.8-1.2 m/min stand-mean ROS bias. Users who want the hedge can
read the unit PC band and re-type. In the test areas there are no labels: the SCANFI composition
is averaged over blocks without re-mapping. Units are blocks anchored at the AOI origin, not SLIC
objects (the report's 4 ha objects scored κ 0.53 vs 0.49 for 100 m blocks).

**Not mappable from open data (stated, not guessed):** M-3/M-4 (no open dead-balsam-fir or
budworm / beetle mortality layer at this scale; NRCan 2026 p.18 also leaves them out), C-5 (not in
Alberta), C-6 plantations (no open plantation layer), S-3 (coastal), C-7 only where SCANFI shows
ponderosa / Douglas-fir (montane Alberta; none in the test areas). Mature vs immature pine is by
canopy height and density, not age or stem density.

### 9.3 Why a second treed gate in the test areas

In the boreal test areas the Meta 1 m canopy model reads almost no cover ≥ 5 m where WorldCover
maps trees: in v2's first run 37,186 ha of the 86,624 ha 2023 test area fell to rule 7 (O-1b) with
median Meta cover 0 while WorldCover tree averaged 54 % and SCANFI treed-coniferous 33 % there.
The canopy model under-reads short, open black spruce and peatland forest. The v1 stand rule
therefore also runs on SCANFI v3 treed cover (same 40 % / 1 ha / not-built thresholds) in the test
areas only; Edmonton and St. Albert do not use it (their stands come from the same rule as v1).

### 9.4 Inputs (all open; licences)

| Input | Use | Licence |
|---|---|---|
| v1 inputs (§3) | gate, canopy, Edmonton conifer fraction | as §3 |
| SCANFI v2 epochs (`v2_20260119`; 2015, 2020, 2025): closure, height, 10 species crown closures | species shares, pre-harvest composition, stale-canopy fallback | OGL – Canada |
| SCANFI v3 annual (`v3_20260528`, map year − 1): treed coniferous / broadleaf, non-treed lichen, herbaceous, shrubs, water, burn scars | C-1 lichen, open conifer, second treed gate | OGL – Canada |
| Meta CHM tile metadata (`acq_date` polygons) | canopy image year (stale-canopy rule) | CC BY 4.0 |
| Hansen et al. Global Forest Change v1.12 `lossyear` (tiles 60N_120W, 60N_110W) | forest loss year 2001-2024 → harvest | CC BY 4.0 |
| Alberta Wildfire historical wildfire perimeters 1931-2025 (`fp-historical-wildfire-perimeter-data.zip`; BURNCODE B*, PB* excluded — small polygons, median 4 ha, taken to be prescribed/partial burns [H]) | years since fire; fire attribution of forest loss | OGL – Alberta (open.alberta.ca listing) |
| ESA WorldCover 2021 tiles N54W111, N57W117; Microsoft footprints; OSM; AAFC ACI (map year − 1; zero in the northern areas, outside the ACI extent) | gate in the test areas | as §3 |
| **Comparison only:** CFS 2026 30 m; CFS 2024 100 m (`FBP_fueltypes_Canada_100m_EPSG3978_20240527`); AVI Crown (Alberta Vegetation Inventory, `AVI_Crown`) | §9.5 | OGL – Canada; OGL – Alberta |

NBAC was not downloaded (the per-year files are 190 MB each); the Alberta perimeters, already
downloaded by the reference-layer survey, were used instead. NTEMS harvest (1985-2020) ends before
the slash window of the test years.

**Pre-fire inputs.** Each test area is mapped for its fire year with SCANFI v2 of the last epoch
before it (2020), SCANFI v3 and ACI of the previous year, burns and forest loss before the year,
Meta canopy imagery ≤ 2020 (checked per cell; the build refuses imagery from the fire year or
later) and WorldCover 2021. Not pre-fire: OSM (2026 extract) and the Microsoft footprints (2026),
which carry little in these wildland areas.

### 9.5 Results

**Test areas** (CFSDS burned extent + 5 km; fire `2024_126` lies against the 2023 burn of
`2023_689`, so its pre-fire map carries that scar as non-fuel). Leaf-on areas, ha:

| Class | 2023_689 (86,624 ha) | 2024_126 (67,356 ha) | 2024_152 (83,482 ha) |
|---|---:|---:|---:|
| C-1 | 2,636 | 1,680 | 306 |
| C-2 | 22,035 | 10,949 | 12,287 |
| C-3 | 114 | 176 | 903 |
| C-4 | 343 | 387 | 3,374 |
| D-2 (D-1 leaf-off) | 8,300 | 7,542 | 5,840 |
| M-2 (M-1) | 37,839 | 29,029 | 28,944 |
| S-1 / S-2 | 7 / 497 | 1 / 57 | 17 / 162 |
| O-1b | 14,309 | 9,309 | 17,896 |
| Non-fuel / water | 114 / 431 | 7,834 / 391 | 478 / 13,276 |

M-2 includes 3,076 / 347 / 3,957 ha of 6-21-year-old burns at 25 % conifer (rule 42).

**Comparison with the CFS layers and AVI** (groups: spruce C-1/C-2, pine C-3/C-4, other conifer,
deciduous, mixedwood, open, slash, non-fuel, water; OA / κ). CFS 2026 includes the test fires
and later burns, so those are excluded; CFS 2024 (May 2024) is pre-fire for the 2024 fires only.
AVI (photo 1996 in the two western areas, 2003-2010 at 2024_152) is compared only on cells with no
burn since 1990 and no Hansen loss, through a group crosswalk [H] (`validate2.avi_groups`).

| Area | v2 vs CFS 2026 | v2 vs CFS 2024 | v2 vs AVI | CFS 2026 vs AVI | CFS 2024 vs AVI |
|---|---|---|---|---|---|
| 2023_689 | 0.43 / 0.21 | (not pre-fire) | **0.47 / 0.30** | 0.39 / 0.17 | 0.43 / 0.23 |
| 2024_126 | 0.38 / 0.19 | 0.53 / 0.37 | **0.50 / 0.35** | 0.42 / 0.19 | 0.48 / 0.28 |
| 2024_152 | 0.48 / 0.37 | 0.48 / 0.35 | **0.57 / 0.48** | 0.54 / 0.43 | 0.56 / 0.44 |

Where both maps call conifer, pine vs spruce agrees with CFS 2026 on 95-96 % of cells in the
western areas (κ 0.48-0.63) and 76 % at 2024_152 (κ 0.39). The largest systematic differences:
v2 maps 8-17 k ha O-1b per area where CFS maps forest (CFS has almost no open class), and half
CFS's C-2 area (CFS types much more as C-2, v2 as M-2). Against AVI, v2 has the highest agreement
of the three maps in every area, but pine recall is low (0.06 in the western areas, where AVI has
little pine; 0.42 at 2024_152) and mixedwood precision is 0.11-0.14 (AVI calls most of v2's M-2
spruce or deciduous). None of these references is field truth.

**Edmonton conifer typing at the decision unit** (all out of fold, City crown labels; label
ceiling = split-half κ 0.33 at 20 m, 0.72 at 100 m, from the report): 20 m cells κ 0.278 (OA 0.58,
MAE 0.205); **100 m blocks κ 0.423** (OA 0.714, folds 0.39-0.47, PC MAE 0.119, 5,610 blocks
≥ 50 crowns; unit means without re-mapping κ 0.399); 200 m blocks κ 0.438. The labels put 2.0 % of
the 100 m forest blocks in C-2 (60 % M-2, 38 % D-2); v2 calls C-2 with precision and recall 0.31.
This is below the report's 0.49-0.53 because v2 uses the v1 (baseline) features and blocks, not
the report's c5 features and SLIC objects.

**Change from v1** (map year 2026): Edmonton 1,214 ha of 78,442 ha (1.6 %) — C-2 391 → 54 ha,
M-2 1,514 → 2,193 ha, D-2 1,934 → 1,557 ha (stand-level typing), C-4 11 ha and C-3 0.4 ha
(SCANFI pine ≥ 25 %; plantations, unverified), 37 ha O-1b → M-2 25 % (open conifer), 1.6 ha slash,
and 54 ha of forest lost since the canopy image now typed from SCANFI (cleared land → crop /
non-fuel). St. Albert 310 ha (1.6 %), the same pattern. Agreement with CFS 2026 is unchanged
(Edmonton groups 0.769 / κ 0.597).

**Reproducibility:** rebuilding v2 gave bit-identical grids and work arrays (25 of 25).

**Engine check** (`firesim2`; `validation/v2/firesim_check_v2.json`): all ten v2 grids (5 AOIs ×
leaf-on/off) load at 20 m with `code_scheme="cfs_national"`, and an ignition in the largest patch
of every class present reads back the intended engine type (C1, C2, C3, C4, D1, D2, M1, M2, O1a,
O1b, S1, S2) and spreads for 1 h at FFMC 92 / BUI ≈ 60 / 20 km/h (e.g. 2024_152 leaf-on: C-1
18 ha, C-4 42 ha, S-1 18 ha, S-2 21 ha), except D-2, which does not spread at this BUI (FBP
buildup effect, as in v1). All these FBP types already had equations in the engine
(`fbp/constants.py`); no engine code was changed.

### 9.6 Uses and limitations of v2

Use v2 for runs outside Edmonton where no agency grid is at hand, for pre-fire hindcasts of the
validation fires, and as a second Edmonton map. Do not read a 20 m cell's type as a stand call:
the type is a 100 m block call painted on the 20 m extent. In the north the conifer fraction is
SCANFI's imputation, not trained locally; pine is under-called against AVI in the western areas;
C-4 versus C-3 rests on canopy height and cover only; slash rests on Hansen loss minus Alberta
burn perimeters (losses from unmapped fires, insects or wind count as harvest); the post-fire
table is a national boreal rule; open wetlands are O-1b by choice. The engine reads the fuel type
from the CIFFC code but not yet the per-cell percent conifer (§5 follow-up).

## References

- BCWS (2017). *BC Wildfire Service Fuel Type Layer: Overview and Limitations*.
- Bennett, Da Silva & Boisvert (2024): average predictions, not inputs (mechanics decision M5).
- Forbes & Beverly (2024): exposure varies with the fuel map used.
- Forestry Canada (1992). ST-X-3; Wotton, Alexander & Taylor (2009). GLC-X-10.
- Hansen et al. (2013). Science 342:850 (Global Forest Change; v1.12 used).
- LaCarte et al. (2026). *FBP Forest Fuel Type Mapping: Overview of the Methodology, Data Sources, and Uses & Limitations*. NRCan, Cat. Fo4-268/2026E-PDF.
- Perrakis, D.D.B. & Eade, G. (2015). *British Columbia Wildfire Fuel Typing and Fuel Type Layer Description, 2015 Version*. BC Wildfire Service.
- Swystun, T., Taylor, S. & Simpson, B. (2018). *FBP Fuel Layer Decision Rules* (CanFG; Swystun et al. 2019).
- Conifer-mapping report (2026-10-10): *Conifer vs deciduous mapping: literature and experiments*, `~/dev/wildfire/reports/` (split-half label ceiling, stand-level typing).
- Guindon et al. (2026). SCANFI v2. doi:10.23687/07653869-f303-46c2-a04e-9ab479b73cbf.
- Lang et al. (2023). Nature Ecology & Evolution 7:1778 (ETH 10 m canopy height; not used).
- NRC (2021). *National Guide for Wildland-Urban Interface Fires*.
- Taneja et al. (2021). Int. J. Wildland Fire 30:776 (fuel-grid resolution).
- Tolan et al. (2024). Remote Sensing of Environment 300:113888 (Meta / WRI canopy height).
- Zanaga et al. (2022). ESA WorldCover 10 m 2021 v200. doi:10.5281/zenodo.7254221.
- Fuel-grid audit: *Fuel type grids Canada global* (2026-10-07), PROJECT_RECORD [R2].
