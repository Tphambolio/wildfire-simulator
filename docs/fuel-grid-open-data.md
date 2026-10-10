# Open-data FBP fuel grid (no LiDAR, no municipal inventory)

**Version:** 1.0.0 (grids `v20261010`) · **Code:** `scripts/fuelgrid/` · **Tests:** `engine/tests/fuelgrid/`
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

## References

- BCWS (2017). *BC Wildfire Service Fuel Type Layer: Overview and Limitations*.
- Bennett, Da Silva & Boisvert (2024): average predictions, not inputs (mechanics decision M5).
- Forbes & Beverly (2024): exposure varies with the fuel map used.
- Forestry Canada (1992). ST-X-3; Wotton, Alexander & Taylor (2009). GLC-X-10.
- Guindon et al. (2026). SCANFI v2. doi:10.23687/07653869-f303-46c2-a04e-9ab479b73cbf.
- Lang et al. (2023). Nature Ecology & Evolution 7:1778 (ETH 10 m canopy height; not used).
- NRC (2021). *National Guide for Wildland-Urban Interface Fires*.
- Taneja et al. (2021). Int. J. Wildland Fire 30:776 (fuel-grid resolution).
- Tolan et al. (2024). Remote Sensing of Environment 300:113888 (Meta / WRI canopy height).
- Zanaga et al. (2022). ESA WorldCover 10 m 2021 v200. doi:10.5281/zenodo.7254221.
- Fuel-grid audit: *Fuel type grids Canada global* (2026-10-07), PROJECT_RECORD [R2].
