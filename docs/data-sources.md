# Bundled data sources

Every dataset the frontend ships in `frontend/public/edmonton/`, with its source, licence, date
and how to refresh it. Engine data (fuel grid, DEM, water, buildings) is listed in
`CLAUDE.md` (Data Files) and `docs/deployment.md`.

Attribution shown in the app (Critical assets card footer and map attribution):
"City of Edmonton Open Data (Open Government Licence – City of Edmonton) · Statistics Canada
ODHF (Open Government Licence – Canada) · © OpenStreetMap contributors (ODbL)".

## `assets.geojson`: critical assets

Built by `scripts/build_edmonton_assets.py` (fetch, clip to the Edmonton fuel grid, normalise,
de-duplicate). 509 features, about 180 KB, built 2026-10-08. Each feature has
`{name, category, source, source_id, licence, fetched_at}` and usually `detail`; the four plant
sites from OSM keep their outline (with an `anchor` point), everything else is a point.

| Category | Source (dataset) | Licence | Data date | Count |
|----------|------------------|---------|-----------|-------|
| Emergency operations centre | Manual point, "City of Edmonton public information" (not in an open dataset; position carried over from the earlier hand-made layer, not independently verified) | Public information | n/a | 1 |
| Hospitals and care facilities | Statistics Canada, Open Database of Healthcare Facilities v1.1 ([open.canada.ca 543fe07a-…](https://open.canada.ca/data/en/dataset/543fe07a-fd79-40e9-a829-ccd697526765)); hospitals and nursing/residential care with coordinates, one record per site | Open Government Licence – Canada | 2020-04-20 | 11 |
| Fire stations | City of Edmonton Open Data, Fire Stations `b4y7-zhnz` | Open Government Licence – City of Edmonton | 2026-10-05 | 31 |
| Police stations | City Open Data, Police Stations `e7aq-scxv` | OGL – City of Edmonton | 2024-06-28 | 8 |
| Recreation centres (candidate reception centres) | City Open Data, Recreation Facilities `nz3t-vyg3`, facility type "Recreation Centre" | OGL – City of Edmonton | 2026-10-07 | 19 |
| Seniors centres | City Open Data, Seniors Centres `zmac-3mxq` | OGL – City of Edmonton | 2024-06-28 | 41 |
| Schools | City Open Data, Edmonton Catholic Schools (Current) `gfxq-u8uu` and EPSB School Locations `996c-239n` | OGL – City of Edmonton | 2026-05-04 / 2026-04-22 | 320 |
| Water and wastewater treatment | OpenStreetMap: `man_made=water_works`, `man_made=wastewater_plant`, and `landuse=industrial` named "… Water Treatment Plant" (Rossdale is mapped that way); storm basins dropped | ODbL 1.0, © OpenStreetMap contributors | 2026-10-08 | 4 |
| Power plants and substations | OpenStreetMap: `power=plant`, and `power=substation` with `voltage` of 69 kV or more (traction and minor distribution left out) | ODbL 1.0, © OpenStreetMap contributors | 2026-10-08 | 45 |
| LRT stations | City Open Data, LRT Stations and Stops `fhxi-cnhe` (platforms of one station merged) | OGL – City of Edmonton | 2026-09-28 | 29 |

The per-source dates and counts are also in the file's `metadata.sources`.

Refresh (network needed, about a minute; Overpass is retried on busy servers):

```bash
python3 scripts/build_edmonton_assets.py
# keep the raw responses, and rebuild later without the network:
python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache
python3 scripts/build_edmonton_assets.py --cache-dir /tmp/assets-cache --offline
```

Known gaps:

- ODHF has no coordinates for 43 Edmonton records (mostly continuing-care and nursing homes,
  and private surgical clinics), so they are not in the layer; ODHF itself dates from 2020.
- The EOC point is manual. Check it against City information before relying on it.
- OSM completeness varies: substations without a `voltage` tag are not included.
- The RCMP K Division appears twice (two neighbouring addresses with different names in the
  City dataset).

OSM-derived features are a derived database under the ODbL: keep the attribution and share
the derived data (this file) under the same licence.

## `roads.geojson`: major roads

OpenStreetMap ways with `highway=motorway`, `trunk` or `primary` (2,241 lines), bundled
2026-03-22 (TRA-201; the extraction script was not kept). ODbL 1.0, © OpenStreetMap
contributors. `secondary` roads are not in this file: the app reads them if a refreshed file
(or a user layer) has them. To refresh, export the same classes for the fuel-grid bounds
(`scripts/build_edmonton_assets.py`, `BOUNDS`) from Overpass with `out geom` and keep `name`,
`highway`, `ref`.

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
