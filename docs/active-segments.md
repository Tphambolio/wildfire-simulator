# Observed-perimeter tools: observation time, standalone forecast, active-segment classification (backlog A1)

What shipped from backlog A1 without the held-edge barrier (owner decision 2026-10-24). The
barrier (engine `hold_hours` / `hold_band_m`) **failed its pre-registered test** (held-out
ΔF1 at 17 h −0.002 [−0.005, +0.001]; `PREREG_ACTIVE_SEGMENT`, [R62]) and stays opt-in on
draft PR #100, **not merged** (owner rule: failed tests stay opt-in and unmerged). Nothing in
this document sends or applies a hold. Report: [R62]
(`~/dev/wildfire/reports/Active-segment restart 2026-10-24.md`).

## 1. What a classified restart does

Exactly what a restart with hand-marked active edges already does (`active_edges`,
`docs/validation.md`, set-up C1 in [R62]): starting cells within `active_edge_buffer_m` (one
grid cell by default) of the active edges spread; the rest of the starting fire is treated as
**burned out** for the whole run. A classification from detections only chooses the active
edges. In the app the other segments are labelled **"Not active (burned out)"**, the meaning
unmarked edges already have.

Active edges act only in the grid model (a fuel grid, or the synthetic demo grid). A request
that asks for a classification without a fuel grid gets a 422. A classification in which **no
segment is active** is refused (422 in the API; a message and a disabled button in the app):
nothing would spread, and a silent zero-growth forecast would mislead. Use the whole perimeter
or mark edges by hand instead.

## 2. Classifying segments: the rule (not validated)

Sources, in priority order; the first one given is used on its own.

1. **Manual:** edges drawn on the map or perimeter sides picked in the panel (`active_edges`, as
   before).
2. **RPAS thermal hotspots:** GeoJSON Point / MultiPoint features with a time property
   (`time`, ISO 8601; also `datetime`, `observed_at`, `timestamp`; naive times are read as
   UTC). Points without a time are dropped.
3. **VIIRS 375 m (NASA FIRMS):** an uploaded FIRMS CSV (`latitude`, `longitude`, `acq_date`,
   `acq_time` HHMM UTC, `confidence`). Low-confidence (`l`) rows are dropped; nominal (`n`) and
   high (`h`) rows are kept; MODIS rows (numeric confidence) are kept at confidence ≥ 30. There
   is no live FIRMS fetch: the user uploads the file.

**Rule** (`engine/src/firesim/active_segments.py`): each outer ring is cut into equal pieces of
about 250 m along the boundary (`classification_segment_m`); a piece is **active** if a
qualifying detection lies within **1 km** of it (`classification_radius_m`) and was observed in
the **12 h up to and including T** (`classification_window_h`); every other piece is
**inactive**. Distances use a local equirectangular projection about the perimeter centroid.
The 1 km and 12 h come from the [R53] §6 item 1 proposal; they are fixed defaults, not fitted.

**Not validated.** The rule has not been checked against real RPAS thermal flights, or against
VIIRS on fires with mapped held lines. The app says so in the classification tooltip and in the
readout ("rule not validated"). Hand-marked active edges remain the validated route (C1; the
harness proxy for "active" is CFSDS day of burning, not RPAS heat).

References: Schroeder, W., Oliva, P., Giglio, L., Csiszar, I.A. (2014). The New VIIRS 375 m
active fire detection data product. *Remote Sensing of Environment* 143:85–96; NASA FIRMS,
*VIIRS I-Band 375 m Active Fire Data* attribute descriptions (firms.modaps.eosdis.nasa.gov);
[R53] Dixie early-arrival diagnostic §6 items 1–3.

## 3. API (additive; existing payloads unchanged)

- `POST /api/v1/simulations/perimeter-override` gains optional `hotspots_geojson`, `firms_csv`,
  `classification_radius_m` (1000), `classification_window_h` (12) and
  `classification_segment_m` (250). Used only when `active_edges` is not given; T is the
  restart's `start_time` (or the source run's). Without them a request resolves to the same
  engine options and archives the same request record as before.
- **`POST /api/v1/simulations/observed-perimeter`** (owner items R13/R14): standalone forecast
  from an observed perimeter, no source run. Body: a normal run request plus
  `perimeter_geojson` and **`observation_time`** T (required, ISO 8601 with a UTC offset; the run
  starts at T; a `start_time` that differs is refused), optional `active_edges` /
  `active_edge_buffer_m` and the classification fields. Ignition defaults to the perimeter's
  centroid. No ensemble.
- **`POST /api/v1/simulations/classify-perimeter`**: classification without a run;
  `{summary, active_edges, segments}` (`status` `active` / `inactive`).
- `active_segments` in responses of classified runs: source, `n_segments`, `n_active`,
  `n_inactive`, lengths, segments; after the run `static_share_model` (modelled share of the
  start perimeter, 500 m samples, that advanced ≤ 1 km; the [R58] method on the arrival raster)
  and `growth_ha`. Other runs: `null`. An uploaded FIRMS CSV is archived as its row count only.

## 4. App

Setup → **Observed perimeter (RPAS)**: **Observed at (T)** (defaults to the timeline's selected
time); **Classify from RPAS hotspots** / **Classify from VIIRS file**; a **Classified from
detections** mode whose active segments are drawn as active edges (orange-red), with the panel
reading "Active edge · Not active (burned out) · Segments active M of N · … · rule not
validated"; after a classified run, "This run: modelled static share X % · Segments active M of
N" (the observed static share cannot be known during an incident). Before any run, the panel
offers **Forecast from observed at T** with Setup's weather, fuel and options (constant
weather, no spin-up, no ensemble). No evacuation suggestions, addresses, owner data or
per-building output.

## 5. Harness

`scripts/validate.py run --start-types point|fresh|mature` keeps only fire-days of those start
types ([R58] strata; CFSDS DOB before the burn day only). `--hold-hours` / `--hold-band-m`
stay on #100.
