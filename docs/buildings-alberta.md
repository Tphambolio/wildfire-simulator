# Buildings for all of Alberta

Added 2026-10-11 (owner-approved task). Report: `~/dev/wildfire/reports/Alberta buildings 2026-10-11.md` (PROJECT_RECORD [R25]).

## 1. Data

| Item | Value |
|---|---|
| Source | Microsoft Canadian Building Footprints, Alberta (`Alberta.zip`, 2019 release, 1,777,439 footprints), https://github.com/microsoft/CanadianBuildingFootprints |
| Licence | Open Data Commons Open Database License (ODbL) 1.0. Attribution: "Building footprints: Microsoft Canadian Building Footprints (2019), Open Data Commons ODbL". The FlatGeobuf is a derived database, so it stays under ODbL. |
| File | `ab_buildings_ms2019.fgb`: FlatGeobuf, EPSG:4326, packed Hilbert R-tree; 372,228,480 bytes; SHA-256 `14f0d6760665d332d74917798071bef893dd372b2b6b9f217a82c907da4405f8` |
| Fields | `id` (int64), `area_m2` (geodesic, WGS84, holes removed). **No address or other attributes.** Geometry is the source geometry unchanged (6-decimal coordinates). |
| Stable ids | The footprints of the former Edmonton extract (`data/edmonton_buildings.geojson.gz`: the source footprints whose ring-average centroid is in 53.39-53.71 N, 113.75-113.25 W; verified: all 346,238, identical coordinates, ids in source order) keep that file's `id` (1-346,238). These ids key `edmonton_building_vegetation.csv.gz` and earlier results. Every other footprint gets `1,000,000 + its 1-based position in the source file`. |
| Build | `python scripts/build_ab_buildings.py --ms-zip <Alberta.zip> --edmonton data/edmonton_buildings.geojson.gz --out ab_buildings_ms2019.fgb` (streams the source one feature per line; 125 s, peak RSS 765 MB; writes `<out>.json` with counts, extent, hashes). Raw data: `~/dev/wildfire/buildings-ab/` (outside the repo). |
| Shipping | GitHub release `data-ab-buildings-ms2019`, fetched by `api/Dockerfile` with the SHA-256 checked (`scripts/fetch_ab_fuel_grid.py`, label "Alberta buildings"); `FIRESIM_AB_BUILDINGS_PATH=/app/data/ab_buildings_ms2019.fgb`. If the asset is missing the image still builds and the API uses the Edmonton file only. |

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

ODB is **not clearly better** for this use: mixing it in would change the building unit
(sheds and garages as separate units) in some municipalities only, create seams at municipal
boundaries, and break Edmonton equivalence. Microsoft footprints are kept province-wide. A
municipality-by-municipality switch (Lethbridge 64,267 ODB vs 34,749 Microsoft; Grande Prairie
35,840 vs 20,809) is an owner decision (open item).

**Known gaps.** Microsoft 2019 coverage is uneven: e.g. ~640 footprints within 5 km of Hinton,
a town of about 10,000 people. Counts outside the major cities can be low; say so with results.

## 2. Engine and API

`firesim.data.building_index.BuildingIndex` reads buildings **by window for the run area** and
holds nothing per building between reads (`.fgb`); a GeoJSON file is read once into compact
WKB arrays (the former in-memory index kept raw GeoJSON dicts).

- **Edmonton** (ignition inside the former extract's box, `EDMONTON_EXTRACT_BOX`, which also
  covers St. Albert, Sherwood Park and the airport fringe): unchanged. The building mask,
  inside-perimeter counts and exposure use the buildings of the 4 City neighbourhoods nearest
  the ignition, each building assigned as before (ring-average centroid within a neighbourhood
  polygon, else the nearest neighbourhood centroid), over the buildings of the former extract
  only. Only a window is read: the 4 polygons plus their Voronoi cells (the only places a
  nearest-centroid fallback building can be) clipped to the box. Structure spread reads the
  reachable box, clipped to buildings whose centroid is in the extract's box, so the same
  buildings enter as before.
- **Elsewhere in Alberta**: the buildings whose centroid is in the run grid's box; when there
  are more than `FIRESIM_BUILDINGS_MAX_NEAR` (default 30,000), the nearest the ignition (by
  footprint-bounds centre). Structure spread reads its reachable box from the whole file.
- The request still names `/app/data/edmonton_buildings.geojson.gz` (the app does);
  `SimulationRunner.resolve_buildings_path` substitutes the Alberta file when it is installed.
  `buildings_path: null` still means no buildings; any other path is used as given.
- Every run reports `buildings_source` (kind, label, attribution, selection, count, capped).
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
