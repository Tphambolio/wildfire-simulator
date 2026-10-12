# Alberta Wildfire FBP fuel grid: the default fuel outside Edmonton

**Since:** 2026-10-11 (owner decision 2026-10-10) · **Code:** `engine/src/firesim/data/provincial.py`,
`fuel_loader.py` (`ab_wildfire` scheme), `fbp/season.py`; API `services/runner.py`
(`resolve_provincial`) · **Tests:** `engine/tests/data/test_ab_wildfire_fuel.py`,
`api/tests/test_provincial_fuel.py` · **Report:** *Alberta Wildfire fuel grid default 2026-10-11*.

**Status:** an operational provincial fuel map used as FireSim's default where the City of
Edmonton LiDAR grid has no data. It is not field-validated here, and its own metadata states its
accuracy as "Unknown due to different data sources". The indicative fire-day check in §7 is
confounded by post-fire re-typing and says nothing about map accuracy.

## 1. The data

| Item | Value |
|---|---|
| Product | Alberta Wildfire – Fire Behavior Prediction (FBP) Fuel Type – Raster (`WildfireRasters.GDB.WF_FBP_FUEL_TYPE`) |
| Publisher | Government of Alberta, Alberta Wildfire (Wildfire Geospatial Intelligence Unit). © Government of Alberta |
| Grid | 100 m cells, EPSG:3400 (NAD83 / Alberta 10-TM Forest), int16, 7,856 × 15,002 cells, whole province including the White Area |
| Date | Dataset date 2025-07-16 (keyword "2025"); the image-service item was created 2026-03-20 and its cells already re-type the 2025 burns (~70 % of the area inside 2025 perimeters is code 105, [R35]) |
| Service | `https://geospatial.alberta.ca/umbriel/rest/services/wildfire/alberta_wildfire_fbp_fuel_type_raster/ImageServer` (public group "Wildfire geospatial data (public)") |
| Licence | **Open Government Licence – Alberta** (<https://open.alberta.ca/licence>), as stated on the FBP Fuel Type map-service item (`4fa5b04edebb4f6e883025e0add3b57e`). The image-service item (`689aac9591cb4f31a9178be663f22ee2`) carries only the Government of Alberta disclaimer: the information is provided "as is", without warranties, and the Crown is not liable for loss from its use. Both are recorded; written confirmation that OGL-Alberta covers the image service is an open item (§8) |
| Attribution (shown in the app) | "Contains information licensed under the Open Government Licence – Alberta." |
| Lineage | "Unknown due to different data sources" (metadata). Partly built from the Alberta Vegetation Inventory (AVI) and updated from the Alberta Satellite Land Cover where AVI is outdated (ASLC usage note), so it is not independent of AVI |
| Download | Province-wide `exportImage` on 2026-10-10; every class count equals the service's raster attribute table (complete). Provenance files beside the raster in `~/dev/wildfire/reference-layers/ab_wildfire_fbp_fueltype/` |

## 2. Code table (`ab_wildfire` scheme)

All 33 rows of the service's raster attribute table are mapped (`AB_WILDFIRE_CODES`; test
`TestCodeTable` checks every row and that no other code is mapped).

| Code | Label in the attribute table | FireSim fuel | Notes |
|---|---|---|---|
| 1-7 | C-1 … C-7 | C1 … C7 | |
| 13 | D-1/D-2 (Aspen) | D1, or D2 when leaves are on (§3) | |
| 21, 22, 23 | S-1, S-2, S-3 | S1, S2, S3 | |
| 31 | O-1 (Grass) | **O1b** (standing grass), all year | matted / standing not stated; decided 2026-10-12, §3, §9 |
| 610-690 (step 5) | M-1/M-2 (10-90 % conifer) | M1, or M2 when leaves are on; **percent conifer = code − 600, per cell** | e.g. 650 = 50 % |
| 101 | Non-Fuel | non-fuel | |
| 102 | Water | non-fuel | |
| 104 | Unclassified (91 cells) | non-fuel | |
| 105 | Vegetated Non-Fuel | **non-fuel** | mostly cropland and recent burns; decided 2026-10-12, §3, §9 |
| 0 | — | outside the service extent | no data (filled from another grid if any) |
| 32767 | — | no data inside the province (1.46 M cells) | non-fuel; still counts as "in Alberta" |
| −1 | not in the table | 29,544 cells, all west of 120 W near 59 N (British Columbia) | treated as outside the extent |

The scheme is never auto-detected (codes 1-7, 13, 31 mean other things in local schemes).

## 3. Rules and why

**Leaf-on / leaf-off (D-1/D-2, M-1/M-2).** The grid carries one code per seasonal pair. The
loader maps them to the leafless type and the run swaps in the green type between **green-up,
day of year 150 (30 May), and leaf-off, day 258 (15 September)** (`firesim.fbp.season`). These
are the dates the validation harness has used for Alberta's boreal since 2026-10-06
(`RunOptions.greenup_doy / leafoff_doy`, now imported from `fbp.season`), and day 150 is also the
end of FireSim's pre-green-up grass-curing window (`fbp/curing.py`). They are fixed regional
defaults, not observations; early or late springs move green-up by weeks (Pickell et al. 2017).
The day of year comes from `fuel_modifiers.day_of_year`, else `start_time`. **Without a date the
leafless type is kept**: D-1 and M-1 spread faster than D-2 and M-2 (D-2 does not carry fire
below BUI 80), so an undated run errs toward more spread. Only the leafless labels are swapped, so
the City grid's D-2 / M-2 cells (leaf-on all year, an open item since 2026-10-07) are untouched.

**Percent conifer.** The 6xx codes carry it in 5 % steps. `FuelGrid` has a new optional per-cell
`pc` layer; the grid model uses it for M-1/M-2 cells in place of the request's
`percent_conifer` (one FBP evaluation per distinct value, `cellular._cell_keys`). The same layer
is filled for CIFFC `cfs_national` grids (4xx/5xx/6xx). Percent dead fir (M-3/M-4) stays the
request value. The ember-spotting model still uses the request value.

**O-1 → O-1b all year (decided 2026-10-12, [R35]).** The provincial grid has one grass code.
ST-X-3 describes two subtypes, "one for the matted grass condition common after snowmelt or in
the spring (O-1a), and the other for standing dead grass common in late summer to early fall
(O-1b)" (Forestry Canada Fire Danger Group 1992, p.16; Table 3, p.13; the O-1a curve was fitted
to cut grass taken to represent "early spring grass fuel matted down by snow loads", p.28).
FireSim keeps **O-1b in every season**, for these reasons:
(1) the subtype describes an observed fuel state, which neither this grid nor the CWFIS curing
grids record ("Standing and matted cured grass are not differentiated", CWFIS percent-curing
metadata); (2) the two curves cross at **ISI 8**: below it O-1a is up to ~10 % faster in
absolute terms of about 1 m/min (ISI 5 at 95 % cured: 11.3 vs 10.0 m/min), above it O-1b is
faster by 5 % at ISI 10, 21 % at ISI 20, 31 % at ISI 40 and 33 % at ISI 60; the ratio does not
depend on curing (same curing factor, Wotton et al. 2009 eq 35), so O-1b avoids the larger,
high-ISI under-prediction that matters for planning; (3) grass does not reliably mat in
Alberta: in the snow-free Chinook belt, O-1b at 100 % curing reproduced the 27 November 2011
Lethbridge grass/cropland fire run (predicted 145 m/min, observed 138 m/min; Alexander,
Heathcott & Schwanke 2013, p.26); (4) a correct date rule would need to know which cells came
from the Alberta grid, because the City grid's own O-1a / O-1b cells share the labels — not a
small change. "O-1b is the faster type" in earlier text was true only from ISI 8 up and is
corrected here. Grass curing is unchanged (95 % default in the pre-green-up window, required
outside it; `fbp/curing.py`). Untested on observed fires: the CFSDS fire-days have little
grass (`validation-data/curing`). Reopen if Alberta grass fire-days with observed spread show
O-1b over-predicting in spring, or if a "matted grass" scenario switch is wanted.

**105 "Vegetated Non-Fuel" → non-fuel (decided 2026-10-12, [R35]).** Alberta does not publish
the class definition (the metadata and attribute table give only the label). Measured on a
systematic 200 m sample of the 100 m grid (`scripts/validation/ab_fuel_105_extent.py`,
`ab_fuel_105_landcover.py`), 105 is two things: **cropland** (ABMI Wall-to-Wall 2010 land cover:
46 % agriculture overall, 57 % where no fire is mapped since 1984, 64 % in the WUI proxy) and
**recently burned land** (63 % of 2024 burns and 30 % of 2019-2023 burns are 105, against 12 % of
unburned land and 3-7 % of burns older than 2014; ~70 % of the area inside 2025 fires ≥ 1,000
ha). About 22 % of all 105 lies in 2019-2024 burns and roughly 6 % more in 2025 burns. It is
12.6 % of the province's mapped cells and 24 % of a community WUI proxy (cells within 2.4 km
of a 2021 population centre; 5,790 km²). It stays **non-fuel**: it is Alberta Wildfire's own
coding for these lands, the Prometheus/CFS lookup table maps 105 to non-fuel, recent burns
have little surface fuel for a few years, and FBP has no crop fuel type; mapping it to O-1b
would turn every recent burn into grass and mapping cropland to a fuel needs a crop-type
layer. A switch was not added: it would need the per-cell source code after stacking (not a
small change). **Limitation:** cured stubble and burn regrowth can carry fire in spring and
fall (the 2011 Lethbridge fire ran through cropland at 138 m/min), so modelled fire can stop at
farmland and young burns where real fire continues — notably around Edmonton. The
pre-registered 2025 burn test (`PREREG_105`) was **void** because the grid already re-types
2025 burns (§9); the barrier behaviour of 105 is untested.

## 4. Which grid a run uses

The default applies when a request names the bundled City grid as `fuel_grid_path`
(`FIRESIM_CITY_FUEL_GRID_PATH`, default `/app/data/Edmonton_FBP_FuelLayer_20251105_10m.tif`; the
app sends it whenever "Use Edmonton fuel grid" is on) or `use_ca_mode` loads it, `provincial_fuel`
is true (default) and the server has the Alberta grid (`FIRESIM_AB_FUEL_GRID_PATH`). Then
(`firesim.data.provincial.choose_fuel`):

| Ignition | Grid | Cells |
|---|---|---|
| Inside the City grid's box, at least 5 km from its edge (`FIRESIM_CITY_MARGIN_KM`) | **"city"**: the City grid on its own 50 m lattice (the same cells as before), its no-data cells (outside the City's mapped area) filled from the Alberta grid. The 20 m WUI window uses the City 20 m cells with the same fill | City majority class (20 m → 50 m, as before); Alberta nearest neighbour |
| Elsewhere in Alberta, including near the City grid's edge | **"provincial"**: a 40 km square window (`FIRESIM_PROVINCIAL_WINDOW_KM`) centred on the ignition (snapped to a 5 km lattice so nearby runs share a cached window; the ignition is ≥ 15 km from the edge), 50 m cells: City grid where it has data, Alberta grid elsewhere, a national grid outside Alberta if configured. No 20 m window | City 20 m → 50 m majority; Alberta 100 m → 50 m nearest neighbour (each 50 m cell takes the 100 m cell under its centre: no class is invented) |
| Outside Alberta, national grid configured (`FIRESIM_NATIONAL_FUEL_GRID_PATH`, `FIRESIM_NATIONAL_FUEL_SCHEME`) | **"national"** window | as above |
| Outside every grid | **"uniform"**: the request's `fuel_type`, Huygens model, labelled "no fuel grid covers the ignition (outside Alberta)" | — |

"Has data" means a mapped class, including non-fuel and water; only "outside extent" and "no
data" cells take the next source. Any other `fuel_grid_path` is used exactly as given
("user_grid"), `provincial_fuel: false` restores the City-only behaviour, and a server without the
Alberta file behaves as before 2026-10-11. No national grid is bundled, so on the live API runs
outside Alberta are uniform.

Every run reports what it used: `fuel_source` on `GET /simulations/{id}` and on the
`simulation.completed` WebSocket event (`kind`, `label`, `attribution`, `season`, `share` of cells
per source for windows). Perimeter override (same fuel as the source run), multi-day runs and
burn probability use the same rule; burn probability returns 422 outside every grid. Slope: the
bundled DEM covers Edmonton only; since 2026-10-11 windows elsewhere read the Alberta MRDEM-30 terrain (`docs/terrain-alberta.md`). Buildings, exposure
and structure spread use the bundled Edmonton footprints, so they act only near Edmonton.

**Changes inside Edmonton.** Every cell the City grid maps is unchanged (test
`test_fill_keeps_every_city_cell`; measured on the real grids: 0 changed cells). What changes is
the City grid's no-data area: 77 % of its 851 × 590 box at 50 m was non-fuel, and the fill turns
73,430 of those cells (18,400 ha) into Alberta fuel (57,554 O-1b, 14,936 D-1/D-2, 904 C-2, 36
M-1/M-2), mostly around the city, so fires that reach the city limits keep spreading instead
of stopping, and ignitions near the box edge get a window.

## 5. Reading the large raster

The grid is never read whole. `read_codes_on_lattice` transforms the requested lat/lng box to
EPSG:3400, reads only that source window (+2 cells) with `rasterio` and reprojects it onto the run
lattice: a 40 km window reads about 450 × 450 int16 cells (0.4 MB) from at most four 512 px
tiles of the COG. Measured on the workstation: 0.25 s per window; a 40 km window at 50 m is 800 × 800
cells.

## 6. Deployment

The API (Fly.io, 2 GB) ships the grid inside the image, like the Edmonton grid, but not in git:

1. `scripts/build_ab_fuel_cog.py SRC.tif OUT_DIR` converts the download to a cloud-optimised
   GeoTIFF: same cells and codes, 512 px tiles, DEFLATE + predictor 2, mode overviews, 0 declared
   no data, provenance and licence in the GeoTIFF tags, JSON sidecar with the SHA-256.
   **31.8 MB** (`e9555f401c4932f348db09a54231bb8123b3598794f15e9346c002fc934a991f`), from a
   155 MB uncompressed source; class counts re-checked against the attribute table.
2. The COG is attached to the GitHub release **`data-ab-fbp-2025`** of this repository (owner
   step; not done by the PR): `gh release create data-ab-fbp-2025 OUT_DIR/ab_wildfire_fbp_fueltype_2025_100m_3400_cog.tif --title "Alberta Wildfire FBP fuel grid 2025 (COG)" --notes "…OGL-Alberta attribution…"`.
3. `api/Dockerfile` downloads it at build time (`scripts/fetch_ab_fuel_grid.py`, standard
   library, SHA-256 checked) to `/app/data/` and sets `FIRESIM_AB_FUEL_GRID_PATH`. If the
   download fails the build continues and the API keeps the City grid only (logged); runs then
   report `fuel_source.kind` "city" with no Alberta attribution.

Image size +31.8 MB. Memory (local API process, same requests with and without the grid, 6 h grid
runs, current RSS after each run): City only 241-260 MB (peak 290 MB); with the grid 284 MB after
an Edmonton run (City fill + 20 m fill source, +43 MB), 322-328 MB after three 40 km window runs
(+38 MB after the first window, +3 MB for each further one; the window cache is capped at 2),
peak 417 MB, far below the 1.09 GB peak of a
structure-spread run measured on 2026-10-10. Rebuilding the COG for a newer provincial grid:
re-download, re-run step 1, upload under a new release tag, update `AB_FUEL_URL` /
`AB_FUEL_SHA256`.

Alternative considered: reading the COG by HTTP range requests (`/vsicurl/`) from the release at
run time. Rejected for now: GitHub release downloads redirect to short-lived signed URLs, every
cold start would need network, and 32 MB in the image costs nothing at run time.

## 7. Indicative check on observed fires (not a skill estimate)

Seven fire-days from the **calibration** half of the CFSDS validation split (never the held-out
test fires), the validation's chosen set-up (active edges, FFMC spin-up, burning period 10-20 h,
17 h score), once with the national 2014b grid the harness uses and once with the Alberta grid
read onto the same 90 m domain (`scripts/validation/ab_fuel_compare.py`): mean F1 0.287 → 0.295,
median 0.269 → 0.226, mean area bias +0.28 → +0.08, mean |bias| 0.38 → 0.28. Per fire-day F1 went
up on three, down on three and stayed level on one.

**This comparison is confounded and must not be read as map accuracy.** The Alberta grid is a
2025 map: inside every validation fire's burned area it shows the post-fire state, not the fuel
that burned — 2024 scars 84-96 % non-fuel, 2021-2023 scars mostly D-1/D-2 (aspen
regeneration), 2014-2016 scars largely O-1 (e.g. 96 % O-1 in the growth of 2014_799, which the
2014 grid typed 86 % C-2). A fair test needs fires that burn after the map's content date — **2026 and later**, because
the published grid already re-types the 2025 burns ([R35]: 25 of 29 fires of 2025 ≥ 1,000 ha
failed the pre-registered map-timing gate) — or the pre-registered northern test. Operationally the post-fire re-typing is a
strength: recent burns do not carry fire as if they were mature spruce.

## 8. Limitations and open items

- **Maps little vegetation inside Edmonton**: around the city centre the provincial grid is mostly
  101 non-fuel / 102 water with some D-1/D-2 and C-2, and no M-type cells over uPLVI forest
  (reference-layers report, 2026-10-10). That is why the City grid stays the source wherever it
  has data. The same will hold in other towns: urban parks and ravines may be non-fuel.
- **Partly AVI-derived**, with AVI photo years back to the 1990s in places; conifer-deciduous
  agreement with pure AVI stands is mixed (reference-layers report §2: pure deciduous stands are
  rarely mapped D in two of three northern test areas).
- **Build rules, per-pixel source and year are not published.** Recommended 2026-10-12 ([R35],
  §9): ask the Wildfire Geospatial Intelligence Unit (`WF.AWHQ-GIS@gov.ab.ca`, the contact in
  the image service's metadata) for them and for written confirmation that OGL-Alberta covers
  the image service; the draft request is in [R35]. Sending it is the owner's step.
- 105 → non-fuel and O-1 → O-1b all year are decisions (§3, §9), with the limitations stated
  there; fixed green-up / leaf-off dates (§3).
- 100 m cells: fine fuel breaks (roads, narrow wetlands) below 100 m are not represented.
- No national grid on the live API. (Terrain outside Edmonton: added 2026-10-11, `docs/terrain-alberta.md`.)
- No field accuracy assessment; see the stratified sampling plan in the reference-layers report.

## 9. Decisions 2026-10-12 ([R35])

| Question | Decision | Key evidence |
|---|---|---|
| O-1 grass: O-1b all year or O-1a by date | **O-1b all year** (no code change) | ST-X-3 p.16 gives matted grass as "common" after snowmelt, a fuel state, not a date rule; grid and CWFIS curing do not record state; O-1b ≥ O-1a from ISI 8 (×1.21 at ISI 20, ×1.33 at ISI 60), O-1a ≤ ~1 m/min faster below it; O-1b reproduced a snow-free Alberta grass/cropland run (Alexander et al. 2013, p.26); a date swap needs per-source labels |
| 105 "Vegetated Non-Fuel" | **Keep non-fuel** (no code change; not configurable for now) | 105 is cropland (ABMI 2010: 46 % agriculture; 64 % in the WUI proxy) and recent burns (63 % of 2024 burns, ~70 % of 2025 burns); Alberta's and Prometheus' tables make it non-fuel; FBP has no crop type. Pre-registered 2025 burn test (`PREREG_105`, commit `a61ac7e`) void: the grid already re-types 2025 burns |
| Ask Alberta Wildfire GIS for licence confirmation and build rules | **Yes, recommended** (owner sends; no message sent by FireSim work) | The full grid is redistributed in a public GitHub release; the item it came from (image service `689aac95…`) carries only the GoA disclaimer, the OGL-Alberta statement is on the viewing map-service item (`4fa5b04e…`); the 105 definition, re-typing rules and update cadence are unpublished and matter for the decisions above |

Scripts: `scripts/validation/ab_fuel_105_extent.py`, `ab_fuel_105_landcover.py`,
`ab_fuel_105_burn_test.py` (PREREG_105), `grass_o1a_o1b.py`; tests
`engine/tests/data/test_ab_wildfire_fuel.py::TestGrassAndVegetatedNonFuelDecisions`.

## References

- Government of Alberta. Alberta Wildfire – Fire Behavior Prediction (FBP) Fuel Type – Raster,
  dataset date 2025-07-16; item `689aac9591cb4f31a9178be663f22ee2`; viewing service item
  `4fa5b04edebb4f6e883025e0add3b57e` (OGL-Alberta). Retrieved 2026-10-10.
- Forestry Canada Fire Danger Group (1992). *Development and structure of the Canadian Forest Fire
  Behavior Prediction System.* ST-X-3. (O-1a / O-1b: p.13 Table 3, p.16, p.28.)
- Alexander, M.E., Heathcott, M.J., Schwanke, R.L. (2013). *Fire behaviour case study of two
  early winter grass fires in southern Alberta, 27 November 2011.* Partners in Protection
  Association, Edmonton. <https://www.frames.gov/documents/catalog/alexander_heathcott_schwanke_2013.pdf>
- Wotton, B.M., Alexander, M.E., Taylor, S.W. (2009). *Updates and revisions to the 2009 Canadian
  Forest Fire Behavior Prediction System.* Information Report GLC-X-10.
- Natural Resources Canada, CWFIS. *Percent grass curing across Canada - daily grids*, metadata.
  <https://cwfis.cfs.nrcan.gc.ca/downloads/pcuring/Percent_curing_metadata_NAP_ISO_19115_2003.pdf>
- ABMI (2013). *Wall-to-Wall Land Cover Map 2010, v1.0.* Alberta Biodiversity Monitoring Institute.
- Hermosilla, T., Wulder, M.A., White, J.C., Coops, N.C. (2022). Land cover classification in an
  era of big and open data. *Remote Sensing of Environment* 268, 112780 (NTEMS VLCE2).
- Johnston, L.M., Flannigan, M.D. (2018). Mapping Canadian wildland fire interface areas.
  *International Journal of Wildland Fire* 27(1), 1-14. doi:10.1071/WF16221.
- Government of Alberta. *Open Government Licence – Alberta*, version 2.1. <https://open.alberta.ca/licence>
- Pickell, P.D., Coops, N.C., Ferster, C.J., et al. (2017). An early warning system to forecast
  the close of the spring burning window from satellite-observed greenness. *Scientific Reports*
  7, 14190.
- Internal reports: *Alberta reference fuel layers and accuracy plan* (2026-10-10); *Alberta
  Wildfire fuel grid default* (2026-10-11).
