# Buildings for all of Alberta

Added 2026-10-11 (owner-approved task). Report: `~/dev/wildfire/reports/Alberta buildings 2026-10-11.md` (PROJECT_RECORD [R25]).
Municipal footprints (ODB v3) and the Edmonton box decision added 2026-10-12: §5-6, report
`~/dev/wildfire/reports/ODB municipal buildings and Edmonton box 2026-10-12.md` ([R34]).

## 1. Data

| Item | Value |
|---|---|
| Source | Microsoft Canadian Building Footprints, Alberta (`Alberta.zip`, 2019 release, 1,777,439 footprints), https://github.com/microsoft/CanadianBuildingFootprints |
| Licence | Open Data Commons Open Database License (ODbL) 1.0. Attribution: "Building footprints: Microsoft Canadian Building Footprints (2019), Open Data Commons ODbL". The FlatGeobuf is a derived database, so it stays under ODbL. |
| File | `ab_buildings_ms2019.fgb`: FlatGeobuf, EPSG:4326, packed Hilbert R-tree; 372,228,480 bytes; SHA-256 `14f0d6760665d332d74917798071bef893dd372b2b6b9f217a82c907da4405f8` |
| Fields | `id` (int64), `area_m2` (geodesic, WGS84, holes removed). **No address or other attributes.** Geometry is the source geometry unchanged (6-decimal coordinates). |
| Stable ids | The footprints of the former Edmonton extract (`data/edmonton_buildings.geojson.gz`: the source footprints whose ring-average centroid is in 53.39-53.71 N, 113.75-113.25 W; verified: all 346,238, identical coordinates, ids in source order) keep that file's `id` (1-346,238). These ids key `edmonton_building_vegetation.csv.gz` and earlier results. Every other footprint gets `1,000,000 + its 1-based position in the source file`. |
| Build | `python scripts/build_ab_buildings.py --ms-zip <Alberta.zip> --edmonton data/edmonton_buildings.geojson.gz --out ab_buildings_ms2019.fgb` (streams the source one feature per line; 125 s, peak RSS 765 MB; writes `<out>.json` with counts, extent, hashes). Raw data: `~/dev/wildfire/buildings-ab/` (outside the repo). |
| Shipping | Superseded by the 2026-10 file (§5) as the preferred asset. GitHub release `data-ab-buildings-ms2019` stays the fallback: `api/Dockerfile` fetches `data-ab-buildings-2026-10` first and, if it is missing or fails its SHA-256, this file (with a warning), both to `/app/data/ab_buildings.fgb` (`FIRESIM_AB_BUILDINGS_PATH`). If both fail the image still builds and the API uses the Edmonton file only. |

**Why FlatGeobuf.** The API needs a few thousand footprints in a box out of 1.78 million,
from a file on local disk or, later, over HTTP, with little memory. FlatGeobuf's packed Hilbert
R-tree sits at the head of the file: a box query reads the index nodes and then only the
matching features (by byte range), with no per-file state kept in memory; pyogrio (GDAL's
FlatGeobuf driver, bundled in the pyogrio wheel) reads it by box and by feature id, and GDAL
can read it remotely through `/vsicurl/`. GeoParquet with bbox covering columns and spatially
sorted row groups would be ~3× smaller (compressed) but needs pyarrow (not in the image) and
filters only at row-group granularity, so a small box still decodes whole row groups.

**Sources evaluated (ODB).** Statistics Canada's Open Database of Buildings v3 (2025-04-15;
Alberta: 1,334,404 footprints from 11 municipal sources plus 208,576 "Government of Canada"
footprints) was compared with the Microsoft file (`odb_vs_ms.json` in the report data):

- Where both exist, Microsoft footprints are almost all present in ODB (Microsoft centroid
  inside an ODB footprint: Calgary NW 99.4 %, Sherwood Park 98.8 %, Edmonton Riverbend 98.1 %,
  Okotoks 96.5 %, Canmore 93.7 %). ODB has 5-37 % more footprints, mostly small accessory
  buildings (under 20 m²: Canmore 763 vs 22, Riverbend 397 vs 1) and newer construction.
- ODB covers only those municipalities (Calgary, Edmonton, Lethbridge, Parkland County,
  Strathcona County, Grande Prairie, Airdrie, Okotoks, Cochrane, Canmore, Banff) and, from
  federal footprints, some other communities (e.g. Red Deer, Medicine Hat, St. Albert, Wood
  Buffalo); most of the province (e.g. Hinton and most rural counties) has no ODB
  footprints.
- Each municipal layer has its own open licence (e.g. Open Government Licence – City of
  Calgary), ODB records carry address fields, and vintages differ (2018-2024).

ODB was **not adopted on 2026-10-11**: mixing it in changes the building unit (sheds and
garages as separate units) in some municipalities only, creates seams at municipal
boundaries, and must not change Edmonton. The owner then approved a municipality-by-municipality
switch with those effects documented: **§5** (2026-10-12).

**Known gaps.** Microsoft 2019 coverage is uneven: e.g. ~640 footprints within 5 km of Hinton,
a town of about 10,000 people. Counts outside the major cities can be low; say so with results.

## 2. Engine and API

`firesim.data.building_index.BuildingIndex` reads buildings **by window for the run area** and
holds nothing per building between reads (`.fgb`); a GeoJSON file is read once into compact
WKB arrays (the former in-memory index kept raw GeoJSON dicts).

- **Edmonton** (ignition inside the former extract's box, `EDMONTON_EXTRACT_BOX`, which also
  covers St. Albert, Sherwood Park and the airport fringe): the building mask,
  inside-perimeter counts and exposure use the buildings of the 4 City neighbourhoods nearest
  the ignition, each building assigned as before (ring-average centroid within a neighbourhood
  polygon, else the nearest neighbourhood centroid) over the buildings of the former extract,
  **plus** (since 2026-10-12, §6) buildings outside the box that lie within a City
  neighbourhood polygon (by the polygon only, never by the fallback). Only a window is read:
  the 4 polygons plus their Voronoi cells (the only places a nearest-centroid fallback
  building can be) clipped to the box. Structure spread reads every building in the reachable
  box (since 2026-10-12; before, only those with centroid in the extract's box).
  `FIRESIM_EDMONTON_BOX_LEGACY=1` restores the former behaviour exactly.
- **Elsewhere in Alberta**: the buildings whose centroid is in the run grid's box; when there
  are more than `FIRESIM_BUILDINGS_MAX_NEAR` (default 30,000), the nearest the ignition (by
  footprint-bounds centre). Structure spread reads its reachable box from the whole file.
- The request still names `/app/data/edmonton_buildings.geojson.gz` (the app does);
  `SimulationRunner.resolve_buildings_path` substitutes the Alberta file when it is installed.
  `buildings_path: null` still means no buildings; any other path is used as given.
- Every run reports `buildings_source` (kind, label, attribution, selection, count, capped;
  outside Edmonton also `sources`, the selected buildings per source code, with the municipal
  providers in the label and their licence lines in the attribution).
- Burn probability on a provincial window rasterises the buildings tile by tile from the file.

**Equivalence (Edmonton, vs `master` 1b377b9).** Neighbourhood selection identical
(footprints, centroids, ids, order) for 15 ignitions; footprint-source counts and ids identical
for 4 boxes including the whole City grid (333,950 centroids, 334,018 intersecting); five full
runs (live request and 4 more ignitions, structure spread on) give identical exposure, counts
per frame and structure results. Memory on the live-request reproduction: peak RSS 1,097 →
347 MB (§ report).

## 3. Community units

Edmonton keeps its 407 neighbourhoods. Elsewhere the Neighbourhoods card uses Statistics Canada
2021 census subdivisions (municipalities, counties, reserves; 422 in Alberta without Edmonton's
own), `frontend/public/alberta/census_subdivisions.geojson` (1.3 MB, simplified ~30 m; built by
`scripts/build_ab_communities.py`; Statistics Canada Open Licence). Every unit carries a `source`
and the card tags census subdivisions "CSD". Dissemination areas (~5,800 in Alberta) were not
used: they are unnamed codes, not places an EOC can act on. Since 2026-10-12 the hamlets and
localities inside the census subdivisions are units too (`frontend/public/alberta/places.geojson`,
"Calahoo (hamlet, Sturgeon County)"; `docs/data-sources.md`, [R36]).

## 4. Vegetation around buildings outside Edmonton (on demand)

The per-building vegetation table (`edmonton_building_vegetation.csv.gz`, context only) covers
Edmonton. For another area, compute it offline with `scripts/build_building_vegetation.py`
(never province-wide; a Meta CHM tile is ~0.5 GB and ring metrics take minutes per town):

```bash
# 1. footprints of the area, with their Alberta-file ids (GeoJSON, as the script expects)
ogr2ogr -f GeoJSON -spat -117.62 53.38 -117.50 53.44 hinton_fp.geojson \
    ~/dev/wildfire/buildings-ab/ab_buildings_ms2019.fgb
# 2. the Meta/WRI 1 m canopy height tile(s) covering it (CC BY 4.0), then
python3 scripts/build_building_vegetation.py ring-metrics --chm <meta_chm_tile.tif> \
    --footprints hinton_fp.geojson --id-field id --out hinton_veg.csv.gz
# 3. optional gap-cover raster for the vegetation bridge
python3 scripts/build_building_vegetation.py canopy-raster --chm <meta_chm_tile.tif> \
    --footprints hinton_fp.geojson --out hinton_canopy_5m.tif
```

`ring-metrics` writes the same table the API loads (id + cc_0_5, cc_5_10, cc_10_30, cc_0_10,
overhang_frac, dist_stand_1ha_m). Ids are unique across the Alberta file, so tables for several
areas can be concatenated (one header) and served with `FIRESIM_BUILDING_VEGETATION_PATH`; the
canopy raster is one file per area (`FIRESIM_CANOPY_PATH`). Never use City of Edmonton LiDAR
products for this.

## 5. Municipal footprints where licensed (2026-10-12, owner-approved)

**File** `ab_buildings_2026_10.fgb` (GitHub release `data-ab-buildings-2026-10`, owner step;
built by `scripts/build_ab_buildings_odb.py`, 70 s, peak RSS 1,224 MB): FlatGeobuf, EPSG:4326,
packed Hilbert R-tree, geometry type mixed (Polygon; 16 multi-part ODB footprints stay
MultiPolygon), **1,946,672 footprints**, 514,087,424 bytes, SHA-256
`f0a305562863839f7aa6bb0fe2db360c6bfa59b8bb6ebe1bfb652718e2214256`. Inputs: the Microsoft file
above (SHA-256 `14f0d676…05f8`), Statistics Canada **Open Database of Buildings v3** for Alberta
(`ODB_v3_AB.gpkg`, 2025-04-15, SHA-256 `8a914218…463d`) and the 2021 census subdivisions
(`ab_csd_2021_lcsd000b21.geojson`, SHA-256 `6a4d936b…124`).

**Fields:** geometry, `id` (int64), `area_m2` (geodesic, WGS84) and `source`: `ms2019`, or
`odb:<provider>` (`calgary`, `lethbridge`, `grande-prairie`, `airdrie`, `okotoks`, `cochrane`,
`canmore`, `banff`, `parkland-county`). **No ODB attribute is read** (ODB records carry
addresses, names, types, heights; none is copied).

**Ids (stable):** Edmonton extract 1-346,238 unchanged; other Microsoft footprints keep
1,000,000 + source position (max 2,777,439) where they are kept; ODB footprints
**10,000,000 + their feature id (fid) in `ODB_v3_AB.gpkg`**: range 10,000,001-11,334,404
(used: up to 11,279,753). The ranges do not overlap.

**Rules.**

1. **Zone** of a provider = its census subdivision **minus the Edmonton extract box** (so every
   footprint an Edmonton run read before stays the Microsoft one).
2. **Membership by ring-average centroid** (as the engine computes it): an ODB footprint of the
   provider is kept when its centroid is in the zone (ODB footprints of the provider outside
   its CSD are dropped: e.g. Calgary's 11,257 in Rocky View, Foothills, Chestermere and Tsuu
   T'ina; Okotoks's 3,073); a Microsoft footprint is dropped when its centroid is in any zone.
   Each centroid lies in at most one zone, so no building is counted twice.
3. **Seams:** an ODB footprint overlapping a kept Microsoft footprint (centroid on the other
   side) by ≥ 50 % of the smaller is the same building twice; the ODB one is dropped (9:
   Calgary 1, Parkland County 5, Banff 3). Check on the built file (`seams.json`): along every
   zone edge (0.005° band) 0 pairs overlap ≥ 50 %; 5 pairs overlap 13-35 % of the smaller
   footprint (different outlines of neighbouring buildings, kept).

**Providers and licences** (read 2026-10-12 from each licence page; `docs/data-sources.md`):

| Provider (CSD) | Licence | ODB kept | Microsoft replaced | ratio |
|---|---|---|---|---|
| City of Calgary (4806016) | Open Government Licence – City of Calgary 2.1 | 476,770 | 394,200 | 1.21 |
| City of Lethbridge (4802012) | City of Lethbridge – Open Data License | 64,266 | 34,749 | 1.85 |
| Parkland County (4811034, minus the box) | Open Government Licence – Parkland County | 52,191 | 22,574 | 2.31 |
| City of Grande Prairie (4819012) | Open Data License – City of Grande Prairie | 35,841 | 20,809 | 1.72 |
| City of Airdrie (4806021) | Open Data Licence – City of Airdrie | 23,145 | 20,262 | 1.14 |
| Town of Okotoks (4806012) | Open Government Licence – Town of Okotoks | 11,440 | 8,736 | 1.31 |
| Town of Cochrane (4806019) | Open Data Licence – Town of Cochrane | 10,618 | 6,385 | 1.66 |
| Town of Canmore (4815023) | Town of Canmore Open Data Licence | 6,358 | 4,500 | 1.41 |
| Town of Banff (4815035) | Open Government Licence – Banff | 1,956 | 1,137 | 1.72 |

All nine grant a worldwide, royalty-free licence to copy, modify, publish, adapt and
distribute for any lawful purpose (commercial included) with attribution only (Lethbridge's
v2.0 has no attribution clause but an indemnity and "may change" clause; Grande Prairie's
licence text is published on its ArcGIS items, its portal licence page no longer exists). None
is share-alike, so the combined file can be distributed under ODbL (required by the Microsoft
part) with each provider's attribution.

**Not used:** City of Edmonton (Edmonton stays Microsoft; licence would allow it); Strathcona
County (59 % of its ODB footprints, Sherwood Park, lie in the Edmonton box, which stays
Microsoft; switching the rest would put a seam through Sherwood Park's east side, where 1,644
ODB footprints lie within 0.02° east of the box edge; licence would allow it); Government of
Canada (NRCan Automatically Extracted Buildings: machine-extracted like Microsoft's and
covering parts of some CSDs only, so no CSD-wide switch; OGL – Canada would allow it).
Parkland County is used outside the box only: 2,784 of its ODB footprints (Acheson, east of
113.75 W) lie in the box, where 1,592 Microsoft footprints stay; the seam there runs through
the Acheson industrial area.

**Unit change (read before comparing towns).** Municipal layers map accessory buildings
(sheds, detached garages) as separate footprints, so the structure-spread unit and every
building count differ by municipality. Footprints under 20 m² (Microsoft → ODB): Lethbridge
504 → 19,194 (30 % of footprints), Grande Prairie 151 → 9,964 (28 %), Parkland County 562 →
10,558 (20 %), Canmore 30 → 1,043 (16 %), Banff 13 → 235, Calgary 3,201 → 4,598, Airdrie 148 → 225,
Okotoks 30 → 147, Cochrane 38 → 246. Median area: Lethbridge 129 → 98 m², Grande Prairie 133 →
102 m², Parkland County 128 → 63 m²; Calgary, Airdrie, Okotoks, Cochrane within ±16 %. Counts
of "buildings within 500 m" and "units involved" in these towns are therefore not comparable
with Microsoft-based towns (or with Edmonton): a house with a shed and a garage is three
units. The D4 size filter (< 40 m², [H]) remains the way to test this. Coverage: Microsoft
footprints with no ODB footprint within 5 m: 0.3-6 % per town (Parkland County 6.3 %, rural;
Calgary 0.5 %), no cluster of more than 61 in a 500 m cell (`coverage.json`); they are not
back-filled (that would mix units again).

## 6. Edmonton extract box: decision (2026-10-12)

**Dropped for structure spread and for the City's own neighbourhoods; kept as the
Microsoft-only zone, the Edmonton-ignition test and the fallback-assignment partition.**

- Structure spread in Edmonton runs now reads every building in the reachable box (as
  everywhere else in Alberta), so buildings south of 53.39 N (Nisku, the airport, Leduc
  County, Edmonton's southern annexed neighbourhoods), west of 113.75 W (Acheson), north of
  53.71 N and east of 113.25 W count when the modelled fire reaches them.
- Buildings outside the box that lie within an Edmonton neighbourhood polygon (Edmonton South
  West / Central / Central East / South East, Crossroads, part of Energy and Technology Park)
  are assigned to that neighbourhood, by the polygon only. Buildings outside the box in no
  neighbourhood polygon (Nisku, Leduc) are never assigned by the nearest-centroid fallback
  (that would put all of Alberta into the edge neighbourhoods).
- The box still decides which ignitions use the 4-nearest-neighbourhood selection, and is the
  zone where the Microsoft footprints are never replaced (§5).
- `FIRESIM_EDMONTON_BOX_LEGACY=1` restores the former behaviour exactly (equivalence checks).

Reasons and evidence: report [R34] and `PROJECT_RECORD.md` decisions log.
