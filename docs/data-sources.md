# Bundled data sources

Every dataset the frontend ships in `frontend/public/edmonton/`, with its source, licence, date
and how to refresh it. Engine data (fuel grid, DEM, water, buildings) is listed in
`CLAUDE.md` (Data Files) and `docs/deployment.md`; the provincial fuel grid (Alberta Wildfire
FBP fuel types 2025, Open Government Licence – Alberta) in `docs/fuel-grid-alberta.md`; the
Alberta building footprints (Microsoft Canadian Building Footprints 2019, ODbL; municipal
footprints from Statistics Canada's Open Database of Buildings in nine municipalities, open
municipal licences, see "Buildings" below) in `docs/buildings-alberta.md`.

Attribution shown in the app (Critical assets card footer and map attribution):
"City of Edmonton Open Data (Open Government Licence – City of Edmonton) · Government of
Alberta continuing care list (Open Government Licence – Alberta) · Statistics Canada ODHF (Open
Government Licence – Canada) · © OpenStreetMap contributors (ODbL)".

## `assets.geojson`: critical assets

Built by `scripts/build_edmonton_assets.py` (fetch, clip to the Edmonton fuel grid, normalise,
geocode, merge across sources, de-duplicate). 623 features, about 330 KB (30 KB gzipped), built
2026-10-08. Each feature has `{name, category, source, source_id, licence, fetched_at,
sources}`, usually `detail`, and `verify` when a data-quality flag applies. `source` /
`source_id` / `licence` are the primary (most authoritative) record; `sources` lists every
record the feature was built from, primary first, as `{source, source_id}` (plus `data_date`,
and `role: "position"` for the City address point that positions a geocoded site). The four
plant sites from OSM keep their outline (with an `anchor` point), everything else is a point.

| Category | Source (dataset) | Licence | Data date | Count |
|----------|------------------|---------|-----------|-------|
| Hospitals and care facilities | Government of Alberta, Continuing Care Accommodation Standards compliance reporting (open.alberta.ca, extract "as of June 2026"), geocoded with City of Edmonton Parcel Addresses `ut27-nrpn`; Statistics Canada ODHF v1.1; OpenStreetMap (below) | OGL – Alberta; OGL – City of Edmonton; OGL – Canada; ODbL | 2026-07-13 (visits to 2026-07-06); 2026-10-05; 2020-04-20; 2026-10-08 | 127 |
| Fire stations | City of Edmonton Open Data, Fire Stations `b4y7-zhnz` | Open Government Licence – City of Edmonton | 2026-10-05 | 31 |
| Police stations | City Open Data, Police Stations `e7aq-scxv` | OGL – City of Edmonton | 2024-06-28 | 7 |
| Recreation centres (candidate reception centres) | City Open Data, Recreation Facilities `nz3t-vyg3`, facility type "Recreation Centre" | OGL – City of Edmonton | 2026-10-07 | 19 |
| Seniors centres | City Open Data, Seniors Centres `zmac-3mxq` | OGL – City of Edmonton | 2024-06-28 | 41 |
| Schools | City Open Data, Edmonton Catholic Schools (Current) `gfxq-u8uu` and EPSB School Locations `996c-239n` | OGL – City of Edmonton | 2026-05-04 / 2026-04-22 | 320 |
| Water and wastewater treatment | OpenStreetMap: `man_made=water_works`, `man_made=wastewater_plant`, and `landuse=industrial` named "… Water Treatment Plant" (Rossdale is mapped that way); storm basins dropped | ODbL 1.0, © OpenStreetMap contributors | 2026-05-31 (Overpass mirror's data date) | 4 |
| Power plants and substations | OpenStreetMap: `power=plant`, and `power=substation` with `voltage` of 69 kV or more (traction and minor distribution left out) | ODbL 1.0, © OpenStreetMap contributors | 2026-05-31 | 45 |
| LRT stations | City Open Data, LRT Stations and Stops `fhxi-cnhe` (platforms of one station merged) | OGL – City of Edmonton | 2026-09-28 | 29 |

The per-source dates, counts, match rates and every merge are also in the file's
`metadata.sources`.

### Hospitals and care facilities (2026-10-08 rebuild)

ODHF (2020) alone gave 11 sites; 43 Edmonton hospital and nursing/residential care records
had no coordinates. The layer now merges three sources, most authoritative first:

1. **Government of Alberta continuing care list** (Ministry of Assisted Living and Social
   Services; `continuing-care-accommodation-standards-compliance-reporting`, monthly XLSX,
   Open Government Licence – Alberta). Site name, civic address, postal code, type, units and
   inspection visits. Kept: Edmonton sites of every type except group homes (82 sites; small
   residences, often private houses), with 10 or more units (47 smaller sites left out), and
   inspected within two years of the extract (5 left out as possibly closed). 104 Edmonton
   sites kept: continuing care homes type A (long-term care) and B (designated supportive
   living), lodges, assisted living, hospices. Each civic address is **geocoded with the City
   of Edmonton address points** (`ut27-nrpn`, parcel and suite points averaged per civic
   address; exact house number and street, then the house number without a unit letter, then
   the street spelling or quadrant). 102 of 104 geocoded; not geocoded (no City address point
   for the listed address, so not in the layer): Shepherd's Care Eden House (2759 109 Street NW),
   McArthur Supportive Housing Site (14125 137 Avenue NW). Check against Alberta's own 2021
   geocoding (City Open Data `6g4m-3ynw`): median 15 m, 90th percentile 71 m, largest 184 m
   (large parcels).
2. **ODHF v1.1** (2020): the 11 sites with coordinates, and the **43 records without
   coordinates**, which have only a postal code (no civic address). 15 of the 43 are private
   day-surgery or outpatient clinics (oral surgery, laser eye, cosmetic and dental clinics that
   CIHI codes "active acute hospital"): left out as not inpatient or residential care. For the
   other 28, the civic address comes from the Alberta list (same postal code and agreeing name,
   else a unique name match) or from OSM `addr:*` tags, then the City address points.
   **Match rate 26 of 28 (93 %)**. Unmatched: Villa Caritas (no civic address in any source
   used; it is on the Misericordia site), Shepherd's Care Eden House (Alberta address not in
   the City address points).
3. **OpenStreetMap** (Overpass, 2026-10-08): `amenity=hospital`, `healthcare=hospital`,
   `amenity=nursing_home`, `healthcare=nursing_home`, `social_facility=nursing_home` /
   `assisted_living`, `healthcare=hospice`, named. Names with clinic words (dental, clinic,
   surgery, laser, …) never create a site. Other `healthcare=*` values (clinics, pharmacies,
   dentists) are not care facilities and are not queried.

De-duplication across sources: records within 150 m whose names agree (equal, an acronym, a
shared distinctive word such as "Kipnes" or "Dickinsfield", or one name's words all in the
other's) are one site; when several agree, the most words in common, then the nearest. Two
Alberta records (or two ODHF sites) never merge: their source lists them as separate sites
(e.g. Benevolence Care Centre and Villa Marguerite share 9810 165 Street). Alberta sites in
St. Albert, Sherwood Park, Beaumont and Ardrossan are not geocoded (City address points cover
Edmonton only) but confirm OSM records of the same name there.

Result: 127 sites (primary source: Alberta 102, ODHF 11, OSM 14). Sources recorded: Alberta
list on 104 features, City address points 103, OSM 51, ODHF 37. Flags (`verify`, shown in the
card, the map popup and the EOC summary / ICS-209):

- "Possibly closed, verify: in ODHF (2020) only, in no current source": Boyle McCauley Health
  Centre (a community health centre, not in OSM as a hospital).
- "OpenStreetMap only: in no official list, verify": 12 sites (e.g. Strathcona Community
  Hospital in Sherwood Park, Pilgrims Hospice, Youville Home in St. Albert, community health
  centres tagged as hospitals).

### EOC location: not included

The City of Edmonton Emergency Operations Centre is not in the layer. No public source gives
its location (searched 2026-10-08: City web pages, council and audit reports, news releases,
OpenStreetMap, City Open Data; the one address found, 12825 185 Avenue NW, belongs to the
co-located dispatch/EOC project cancelled in 2020), and the point carried over from an earlier
hand-made layer could not be verified. Owner decision 2026-10-08: leave it out.

### De-duplication of same-name assets

Assets of one category with the same name within 300 m are one asset (generic rule,
`dedupe_same_name`): names are the same when their words match (case and punctuation aside)
or when an all-capitals word is the other name's initials ("RCMP K Division" / "Royal Canadian
Mounted Police"). The kept feature gets the others' sources, and their names in `detail`
("also listed as: …"); every merge is logged in `metadata.sources.dedupe_same_name.log`. The
rebuild merged 30: 25 LRT platform pairs, 3 seniors centres listed twice, the Grey Nuns
Community Hospital (ODHF and OSM 205 m apart), and the RCMP K Division (now four City rows,
two names at 11136 and 11140 109 Street NW: one asset). Same-site records of one dataset within
30 m are merged first, the same way.

### Refresh

Network needed, about a minute; Overpass is retried on busy servers:

```bash
python3 scripts/build_edmonton_assets.py
# keep the raw responses, and rebuild later without the network:
python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache
python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache --offline
```

The 2026-10-08 file was built with `--cache-dir` and then rebuilt `--offline` from the same
responses. The build prints every merge and every site it could not geocode.

Known gaps:

- Care facilities: group homes and sites under 10 units are left out by design; two Alberta
  sites and two ODHF records could not be positioned (above); sites outside Edmonton come from
  OSM and ODHF only.
- The EOC is not included (no verifiable public location; owner decision 2026-10-08).
- OSM completeness varies: substations without a `voltage` tag are not included; OSM-only care
  sites are flagged.

OSM-derived features are a derived database under the ODbL: keep the attribution and share
the derived data (this file) under the same licence.

## `roads.geojson`: major roads

Built by `scripts/build_edmonton_roads.py` from OpenStreetMap (Overpass API, data 2026-10-08):
ways inside the fuel-grid bounds with `highway` = `motorway`, `trunk`, `primary` or
`secondary`, and their `_link` roads (12,944 ways: motorway 693, motorway_link 1,253, trunk 145,
trunk_link 48, primary 1,253, primary_link 386, secondary 7,742, secondary_link 1,424). Ways of
one road (same name, ref and class) are joined end to end into one feature and simplified
(Douglas-Peucker, 5 m), coordinates rounded to 1e-5° (about 1 m): **816 features, 13,485
vertices, 419 KB** (the earlier motorway/trunk/primary file was 2,241 features, 440 KB).
Properties: `name`, `highway` (the class; ramps carry their parent class), `ref`, and `link: 1`
for ramps. Unnamed ramps are named after the named roads at their two ends ("184 Street NW /
Anthony Henday Drive NW ramp"); 5 ramps joined to no named road keep no name and are drawn
but not listed. ODbL 1.0, © OpenStreetMap contributors; the file's `metadata` repeats the
attribution, date and counts.

```bash
python3 scripts/build_edmonton_roads.py
python3 scripts/build_edmonton_roads.py --cache-dir /tmp/roads-cache            # keep the response
python3 scripts/build_edmonton_roads.py --cache-dir /tmp/roads-cache --offline  # rebuild offline
```

Road first-reach with the larger file (Vitest `assets.test.ts`, recorded Terwillegar fixture,
median of 5, this workstation): single run 23–25 ms (5 named roads reached), ensemble
P10 24–27 ms (8 roads); the old file took 12 / 3 ms (1 road). Both run once per
result (memoised in `App.tsx`), so the map and card stay responsive; the test fails above
500 ms.

## `neighbourhoods.geojson`: neighbourhoods

City of Edmonton Open Data, "City of Edmonton – Neighbourhoods" (`65fr-66s6`), 407 polygons
with `name`, `neighbourhood`, `ward`, `district`; bundled 2026-03-22 (the same layer as
`data/edmonton_neighbourhoods.geojson`). Open Government Licence – City of Edmonton.
Refresh from https://data.edmonton.ca/resource/65fr-66s6.geojson and keep the same property
names.

## `../alberta/census_subdivisions.geojson`: community units outside Edmonton

Statistics Canada, 2021 Census cartographic boundary file, census subdivisions
(`lcsd000b21a`), Alberta (PRUID 48), from the StatCan geography REST service
(`geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer/9`),
fetched 2026-10-11. Statistics Canada Open Licence ("Source: Statistics Canada, 2021 Census
boundary files"). 422 polygons (Alberta's 423 without Edmonton, 4811061, which keeps its
neighbourhoods), simplified to ~30 m (topology preserved), 5-decimal coordinates, 1.3 MB
(340 KB gzipped), properties `name` (CSD name; "Name (type)" where a name repeats), `csduid`,
`csdtype`, `source`. Refresh: `python scripts/build_ab_communities.py --out
frontend/public/alberta/census_subdivisions.geojson`. The app merges them with the Edmonton
neighbourhoods (`mergeCommunities`) and tags them "CSD" in the Neighbourhoods card.

## Alberta assets and roads (served by area, outside Edmonton)

Added 2026-10-11 (owner-approved task; report [R29] in `PROJECT_RECORD.md`). The bundled
Edmonton layers stay as they are. For the rest of the province the API serves two FlatGeobuf
files by box (`GET /api/v1/layers/assets|roads`, `docs/api-reference.md`); the app asks for
about 28 km around an ignition and drops Alberta features inside the Edmonton layers' box, so
nothing is counted twice and the province is never sent to the browser. Neither file holds
addresses, phone numbers, e-mails or any other personal data.

| File | Content | Sources and licences | Size, SHA-256 |
|---|---|---|---|
| `ab_assets_2026.fgb` (`scripts/build_ab_assets.py`) | 3,901 points (plant sites with their outline): hospitals and care facilities 330, schools 1,561, fire stations 270, police 134, EMS stations 25 (new category `ems`), recreation / community centres (candidate reception centres) 695, seniors centres 30, water and wastewater plants 109, power plants and substations ≥ 69 kV 661, LRT stations 86 | Statistics Canada ODHF v1.1 (2020-04; Open Government Licence – Canada): Alberta hospitals and nursing / residential care (outpatient clinics left out). OpenStreetMap via Overpass, data 2026-10-11 (ODbL 1.0, © OpenStreetMap contributors): everything else, and care facilities ODHF lacks | 1,459,376 B, `b61daf342bbf371010d786bbd99bd3119d932e8d4f70f175fa47cf5c7140028e` |
| `ab_major_roads_osm.fgb` (`scripts/build_ab_roads.py --pbf`) | 17,184 line pieces (≤ 5 km) of motorway, trunk, primary, secondary and their ramps (48,198 OSM ways), joined per road, Douglas-Peucker 5 m; `name`, `highway`, `ref`, `link` | OpenStreetMap, Geofabrik Alberta extract of 2026-10-08 (ODbL 1.0, © OpenStreetMap contributors) | 4,133,568 B, `40ee4de4af7c7180e649074f20a66c0da6362edd836e151cd570e4595142283b` |

Both: GitHub release `data-ab-infra-2026-10` (owner step), fetched by `api/Dockerfile` with the
SHA-256 checked; `FIRESIM_AB_ASSETS_PATH`, `FIRESIM_AB_ROADS_PATH`. Without them the endpoints
answer `available: false` and the app shows the Edmonton layers only.

**Care facilities.** ODHF records with coordinates (148 of 287 kept in Alberta; 4 with a positive, i.e. sign-flipped, longitude are corrected) are matched to
OSM records with an agreeing name within 300 m. ODHF has no coordinates for the rest; they are
placed only when exactly one OSM care record in the same census subdivision (2021, by name) has
an agreeing name (31 placed); 108 are left out (no address geocoding outside Edmonton: no open
province-wide address points). Single-source sites carry `verify` ("ODHF (2020) only, verify",
"OpenStreetMap only, verify"). The Alberta continuing care list used for Edmonton has civic
addresses only (no coordinates), so it is not used outside Edmonton.

**Schools.** Alberta Education's *Schools and authorities* list (open.alberta.ca
`alberta-education-schools-and-authorities`, Open Government Licence – Alberta, extract
2026-10-10; 2,688 school records, including special, colony and online schools) has addresses and
contact details but no coordinates: not used (it would need address geocoding, and its contact
columns are never wanted here). Schools come from OSM `amenity=school` (named): 1,561, so
coverage, rural above all, is incomplete.

**Roads.** Alberta Transportation's provincial highway network was not used: no province-wide
open download under OGL – Alberta could be confirmed, and OSM carries the numbered highways
with their `ref`. The Overpass route of `build_ab_roads.py` (2° tiles) was too slow on
2026-10-11 (9 of 30 tiles in 20 minutes); the release file was built from the Geofabrik extract
through GDAL's OSM driver (peak RSS 297 MB).

**Coverage.** OSM completeness varies by town: around Hinton (28 km) the file has 16 assets and
no fire station; around Canmore 49 (6 fire stations), Slave Lake 14. The card's tip says that
coverage depends on OSM mapping, and each Alberta asset is tagged with its source ("ODHF ·
OSM").

```bash
python3 scripts/build_ab_assets.py OUT_DIR --cache-dir CACHE          # Overpass + ODHF (~6 min)
python3 scripts/build_ab_roads.py OUT_DIR --pbf alberta-YYMMDD.osm.pbf  # Geofabrik extract
```

## Terrain outside Edmonton (engine data)

NRCan MRDEM-30 elevation averaged to ~100 m (`ab_terrain_mrdem30_100m.tif`, 91.7 MB, Open
Government Licence – Canada): `docs/terrain-alberta.md`.

## Buildings (engine data)

**Microsoft Canadian Building Footprints (2019), Alberta**, © Microsoft, Open Data Commons
Open Database License (ODbL) 1.0. The derived files `ab_buildings_ms2019.fgb` and
`ab_buildings_2026_10.fgb` (geometry, `id`, `area_m2`, and in the latter `source`; no
addresses) are databases under ODbL and are distributed under ODbL with this attribution:
"Building footprints: Microsoft Canadian Building Footprints (2019), Open Data Commons ODbL".
The API returns the same line in `buildings_source.attribution`.

**Municipal footprints (2026-10 file, `docs/buildings-alberta.md` §5)**, compiled by
Statistics Canada in the **Open Database of Buildings v3** (2025-04-15; the compilation is
released under the Open Government Licence – Canada and each record keeps its provider's
licence, `ODB_v3_data_providers.csv`). Only footprint geometry is used, inside each provider's
2021 census subdivision (minus the Edmonton extract box). Licences read on 2026-10-12; all
permit copying, adapting and redistribution for any lawful purpose with attribution, none is
share-alike:

| `source` | Provider, dataset (ODB download date) | Licence | Attribution |
|---|---|---|---|
| `odb:calgary` | City of Calgary, Buildings (2024-11-20) | [Open Government Licence – City of Calgary 2.1](https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa) | Contains information licensed under the Open Government Licence – City of Calgary. |
| `odb:lethbridge` | City of Lethbridge, Building Footprints (2024-12-03) | [City of Lethbridge – Open Data License](https://opendata.lethbridge.ca/pages/open-data-license) (v1 in ODB; v2.0 on the portal: no attribution clause, adds an indemnity) | Contains information licensed under the City of Lethbridge – Open Data License. |
| `odb:parkland-county` | Parkland County, Building Footprints (2025-01-14) | [Open Government License – Parkland County](https://www.parklandcounty.com/en/home-property-and-utilities/open-data-licence.aspx) | Contains information licensed under the Open Government License – Parkland County. |
| `odb:grande-prairie` | City of Grande Prairie, Building Footprint (2025-01-09) | Open Data License – City of Grande Prairie (text on the City's ArcGIS items, e.g. its parcel data; the portal's licence page no longer exists) | Contains information licensed under the Open Data License – City of Grande Prairie. |
| `odb:airdrie` | City of Airdrie, Airdrie 2018 Building Footprints (2024-11-20) | [Open Data Licence – City of Airdrie](https://data-airdrie.opendata.arcgis.com/pages/our-open-licence) | Contains information licensed under the Open Data Licence – City of Airdrie. |
| `odb:okotoks` | Town of Okotoks, Building Footprints (2024-12-16) | [Open Government Licence – Town of Okotoks](https://okotoksmaps-okotoks.hub.arcgis.com/pages/open-data-licence) | Contains information licensed under the Open Government Licence – Town of Okotoks. |
| `odb:cochrane` | Town of Cochrane, Buildings (2024-12-09) | [Open Data Licence – Town of Cochrane](https://geohub.cochrane.ca/pages/open-data-license-agreement) | Contains information licensed under the Open Data Licence – Town of Cochrane. |
| `odb:canmore` | Town of Canmore, Canmore Building Footprints (2024-12-03) | [Town of Canmore Open Data Licence](https://sttocgispublic.blob.core.windows.net/gis/Town%20of%20Canmore%20Open%20Data%20Licence.pdf) | Contains information licensed under the Town of Canmore Open Data Licence. |
| `odb:banff` | Town of Banff, Banff Open Data – Building Footprints (2024-11-20) | [Open Government Licence – Banff](https://maps.banff.ca/opendata/) | Contains information licensed under the Open Government Licence – Banff. |

Runs whose selection includes municipal footprints list those providers in
`buildings_source.label` and their lines in `buildings_source.attribution`; the About &
sources tab lists all nine. **Not used:** City of Edmonton (Edmonton stays on Microsoft
footprints), Strathcona County (Sherwood Park lies in the Edmonton box) and the Government of
Canada (NRCan) footprints (not municipal, partial coverage); their licences would have allowed
it. Statistics Canada 2021 census subdivisions (Statistics Canada Open Licence) define the
zones.

## How the app uses them

The Edmonton layers load automatically when the Edmonton fuel grid is on (no upload). After a
run, the Critical assets card reports, per asset, the first time the modelled fire is within
500 m and inside it (a burned cell on the asset: within half a cell diagonal of its centre),
for the single run and, with an ensemble, its P10 (early) arrival raster; for major
roads, the first time a burned cell lies on each named road. These are model outputs in clock
time (`frontend/src/utils/assets.ts`), never recommendations. Other jurisdictions add their
own GeoJSON under Setup, "Add your own layer" (points and polygons are assets, lines roads).
