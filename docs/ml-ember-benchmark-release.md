# ML ember-cast open benchmark: proposed release contents and licence check (owner decision D4)

**Status:** proposal for the owner, 2026-10-30 (professor session). Nothing is released by this document. Owner
decision D4 (2026-10-25) was "planned in principle, timing and contents decided later"; this is the "later".
**Record:** `docs/ML_EMBER_RECORD.md`. **Source tables:** `reports/data/ml-ember-cast-2026-10-29/` (stage 1 §13).

## 1. Recommendation

Release **the training-split benchmark only**, now that [R68] is decided: the frozen feature tables, the split and
fold assignment, the labels, the baseline scores (B1, M8 per fold, DIST), the scoring code and the two
pre-registrations. **Hold back the decision-fire features and ranks** (`stage2/decision_cells.parquet`,
`ranks_per_jump.csv`, `per_fire.csv`) until the corrected-M8 test (PREREG_M8_C1) has run, because they are the one
held-out set a reader could tune on; release them with that result. Hold back nothing else for licence reasons: every
input is open, and every table is a derived product (no raw hotspot rows, no raw station series, no raster tiles).

## 2. Proposed contents

| Item | Files | Size | Note |
|---|---|---|---|
| Feature tables | `table/{cells_eval,cells_fit,intervals,fires}.parquet` (+ `terrain.parquet`, `m8_suffstats.parquet`) | ~0.3 GB | float32; §4 columns only; `coords_control` and `seam_control` **excluded** (box coordinates and texture are forbidden features and leak the footprint) |
| Labels and split | `jumps_training.csv` (fire key, interval end, cell, distance, gap, year), `split.csv`, the fold assignment | small | from the v8 pre-freeze checks; no coordinates beyond the 375 m cell index within the fire's run box |
| Baselines | per-fold `betas.json`, `ladder/{clean,full}/ladder.json`, `rung2_rerun.json`, `stage2/models.json` | small | B1, M8, DIST, rung 1–4 out-of-fold scores |
| Code | `scripts/ml_ember/` (loader guard, features, cv, ladder, confirm, power), `scripts/validation/v8_score.py` | — | MIT or the repository's licence (owner's choice) |
| Documents | PREREG_ML_EMBER_DEV, PREREG_ML_EMBER (with §12), [R68], the development memo, this file | — | |
| Held back | `stage2/decision_cells.parquet`, `ranks_per_jump.csv`, `per_fire.csv`, any v8 `final/` file | — | until PREREG_M8_C1 reports |

## 3. Licence check by source (what the released tables derive from)

| Source | Licence / terms as recorded | Obligation for a derived-table release | Verified? |
|---|---|---|---|
| NASA FIRMS VIIRS 375 m (VNP14IMG, VJ114IMG) | NASA open data; FIRMS acknowledgement text and product DOIs (PREREG_LR8 §2) | Acknowledge FIRMS/LANCE and cite the product DOIs in the README; no redistribution restriction on derived products | recorded in PREREG_LR8; acknowledgement text to be copied verbatim at release |
| NBAC 2026-05-13 | Open Government Licence – Canada | Attribution statement ("Contains information licensed under the Open Government Licence – Canada") | recorded |
| MTBS; NIFC WFIGS | Public domain (USGS/USFS; NIFC) | Citation courtesy | recorded |
| IEM ASOS/METAR hourly (Iowa Environmental Mesonet) | Open; IEM asks for acknowledgement | Acknowledge IEM and the originating networks (NWS ASOS, NAV CANADA/ECCC METAR) | **to verify** the current IEM terms page wording before release |
| ERA5 precipitation at the station via Open-Meteo | Open-Meteo CC BY 4.0; ERA5 under the Copernicus C3S licence | Attribute Open-Meteo (CC BY 4.0) and Copernicus/ECMWF ERA5 | recorded; C3S attribution sentence to be copied at release |
| CFS FBP fuel 2024 (100 m) + SCANFI v2 back-cast | Open Government Licence – Canada | OGL-Canada attribution | recorded |
| LANDFIRE FBFM40 | Public domain | Citation courtesy | recorded |
| MODIS MOD13Q1 v061 NDVI (Planetary Computer) | NASA open data | Acknowledge LP DAAC | recorded |
| Copernicus DEM GLO-90 | Copernicus DEM licence (free use, attribution required: "produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved") | The attribution sentence verbatim in the README | **to verify** the exact current wording at release |
| Alberta Wildfire FBP raster, City of Edmonton data | not used in these tables | — | — |

No source forbids redistribution of derived, aggregated or re-gridded products; two attribution wordings (IEM,
Copernicus DEM) are to be copied from the current terms pages at release time. **No addresses, buildings, owner data,
SFOC material or per-building results are in any table** (checked: the §4 contract has none).

## 4. What the owner decides

- **D4a** release the training-split benchmark now (recommended) or after PREREG_M8_C1.
- **D4b** code licence for `scripts/ml_ember/` (the repository's licence applies by default).
- **D4c** venue: a GitHub release on `Tphambolio/wildfire-simulator` (recommended; the existing data-release pattern)
  or a Zenodo deposit with a DOI (better for citation; needs an account).
- **D4d** whether the held-back decision-fire files are released with the M8-C1 result (recommended) or kept sealed
  for a further test.
