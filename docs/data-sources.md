# Bundled data sources

Every dataset the frontend ships in `frontend/public/edmonton/`, with its source, licence, date
and how to refresh it. Engine data (fuel grid, DEM, water, buildings) is listed in
`CLAUDE.md` (Data Files) and `docs/deployment.md`.

Attribution shown in the app (Critical assets card footer and map attribution):
"City of Edmonton Open Data (Open Government Licence – City of Edmonton) · Government of
Alberta continuing care list (Open Government Licence – Alberta) · Statistics Canada ODHF (Open
Government Licence – Canada) · © OpenStreetMap contributors (ODbL)".

## `assets.geojson`: critical assets

Built by `scripts/build_edmonton_assets.py` (fetch, clip to the Edmonton fuel grid, normalise,
geocode, merge across sources, de-duplicate). 624 features, about 330 KB (30 KB gzipped), built
2026-10-08. Each feature has `{name, category, source, source_id, licence, fetched_at,
sources}`, usually `detail`, and `verify` when a data-quality flag applies. `source` /
`source_id` / `licence` are the primary (most authoritative) record; `sources` lists every
record the feature was built from, primary first, as `{source, source_id}` (plus `data_date`,
and `role: "position"` for the City address point that positions a geocoded site). The four
plant sites from OSM keep their outline (with an `anchor` point), everything else is a point.

| Category | Source (dataset) | Licence | Data date | Count |
|----------|------------------|---------|-----------|-------|
| Emergency operations centre | Manual point, "City of Edmonton public information"; **unverified manual point** (see below) | Public information | n/a | 1 |
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

### EOC location: unverified manual point

The EOC point (-113.4808, 53.5460) is carried over from an earlier hand-made layer. On
2026-10-08 these public sources were searched for the City of Edmonton Emergency Operations
Centre's address: City of Edmonton web pages (Office of Emergency Management), council and
audit reports (Emergency Management Governance and Risk Assessment Audit), news releases and
coverage (2020 activation, 2023 reception centres), OpenStreetMap (`emergency=*` and names in
the grid), and City Open Data facility datasets. None gives the EOC's address; the one address
found (12825 185 Avenue NW) belongs to the co-located dispatch and EOC project cancelled in
2020. The point is kept and marked **"Unverified manual point"** in the data (`verify`), the
card (a data note and the row), the map popup, the EOC summary and the ICS-209. The point lies
in a parking lot east of downtown (Boyle Street), which is a further reason not to rely on it.
To fix it, put the EOC's public address in `EOC_MANUAL` (`scripts/build_edmonton_assets.py`),
geocode it with the City address points and cite the source.

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
- The EOC point is an unverified manual point.
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
median of 5, this workstation): single run 23–25 ms (5 named roads reached), worst-credible
ensemble P10 24–27 ms (8 roads); the old file took 12 / 3 ms (1 road). Both run once per
result (memoised in `App.tsx`), so the map and card stay responsive; the test fails above
500 ms.

## `neighbourhoods.geojson`: neighbourhoods

City of Edmonton Open Data, "City of Edmonton – Neighbourhoods" (`65fr-66s6`), 407 polygons
with `name`, `neighbourhood`, `ward`, `district`; bundled 2026-03-22 (the same layer as
`data/edmonton_neighbourhoods.geojson`). Open Government Licence – City of Edmonton.
Refresh from https://data.edmonton.ca/resource/65fr-66s6.geojson and keep the same property
names.

## How the app uses them

The Edmonton layers load automatically when the Edmonton fuel grid is on (no upload). After a
run, the Critical assets card reports, per asset, the first time the modelled fire is within
500 m and inside it (a burned cell on the asset: within half a cell diagonal of its centre),
for the single run and, with an ensemble, its worst-credible (P10) arrival raster; for major
roads, the first time a burned cell lies on each named road. These are model outputs in clock
time (`frontend/src/utils/assets.ts`), never recommendations. Other jurisdictions add their
own GeoJSON under Setup, "Add your own layer" (points and polygons are assets, lines roads).
